"""
Dexter AI Super Assistant - HTTP engine.

The chat endpoint used to be a 310-line keyword waterfall in which the language
model was the last branch and could not call a single tool. Intent handling now
lives in router.py (three tiers) and capabilities live in the tool registry, so
this module is only transport: endpoints, lifecycle, and streaming.
"""

import asyncio
import atexit
import json
import os
import queue as sync_queue
import re
import secrets
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Dict, List, Optional

# Ensure backend directory is importable when launched from anywhere.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import env_boot  # noqa: F401  - loads .env for every entry point, not just this one
import logging_setup

# Before anything else prints: Dexter runs in a WebView2 window with no
# console, so without this every diagnostic it emits is lost.
_LOG_PATH = logging_setup.setup()

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import app_index
import apps_registry
import buzzcaf_ports
import cancellation
import google_tools
import media_tools
import model_steward
import router as intent_router
import runtime_health
import settings_store
import tools_assistant  # noqa: F401  - registers reminder/memory tools
import windows_tools
from buzzcaf_client import buzzcaf_client
from events import bus
from filesystem_tool import FileSystemTool
import macros
import tasks
import tools_tasks  # noqa: F401  - registers background-task and macro tools
import tools_workbook  # noqa: F401  - registers goal/commitment/review tools
import tools_files  # noqa: F401  - registers rename/move/copy/delete
import tools_web  # noqa: F401  - registers web search and weather
import tools_studio  # noqa: F401  - registers the Buzzcaf Studio control tools
import tools_knowledge  # noqa: F401  - registers knowledge search/reindex and profile tools
import tools_sentinel  # noqa: F401  - registers the GPU / batch-job tools
import tools_apps  # noqa: F401  - registers the sibling-app tools (v9)
import tools_browser  # noqa: F401  - registers the browser tools (v6, mode-aware in v11)
import tools_desktop  # noqa: F401  - registers keys/typing/windows/clicks (v11)
import tools_shell  # noqa: F401  - registers the confirmed PowerShell tool (v11.1)
import tools_code  # noqa: F401  - registers buzzcode + local engine tools (v6)
import tools_mission  # noqa: F401  - registers the mission tools (v7)
import tools_activity  # noqa: F401  - registers what-am-I-doing (v7)
import tools_claude  # noqa: F401  - registers the Claude Code log readers (v7)
import voice_pipeline
import activity
import companion
import mission
import agenda
import knowledge_ingest
import user_profile
from workbook import workbook
from hybrid_llm import llm_router
from memory import memory
from scheduler import scheduler
from tool_registry import all_tools, execute
from tools_core import (
    AppLauncherTool,
    PlaywrightBrowserTool,
    notes,
    youtube_tool,
)
from voice_engine import AUDIO_DIR, DexterVoiceEngine, default_voice_key
from speaker_id import speaker_id

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Voice (v10): hands-free in, spoken replies out. DEXTER_VOICE=0 turns the whole
# layer off (no models warmed, the UI hides the mic). The engine itself loads
# Kokoro lazily, so constructing it here costs nothing.
VOICE_ENABLED = os.getenv("DEXTER_VOICE", "1").strip().lower() not in ("0", "false", "no", "off")
voice_engine = DexterVoiceEngine()

# ───────────────────────── the port (GUARDIAN_PLAN §11, P1) ─────────────────────────

VERSION = "2.0.0"
APP_ID = "dexter"
HOST = os.getenv("DEXTER_HOST", "127.0.0.1")
#: A wish, not a fact. `DEXTER_PORT` names the port Dexter would *like*.
PREFERRED_PORT = int(os.getenv("DEXTER_PORT") or 8098)
#: Where `npm run dev` serves the UI from (vite.config.ts reads the same var).
VITE_PORT = int(os.getenv("VITE_DEXTER_UI_PORT") or 3006)


def _bind_port() -> int:
    """The port this process serves on — chosen once, before anything reads it.

    The shell (`desktop_app.py`) picks it before importing this module and hands
    it over in `DEXTER_BOUND_PORT`, so the CORS list, `/health`, the ledger entry
    and the window all name the same number. `python backend/main.py` has no
    shell to do that, so it picks here: the decision has to happen at import
    time because the CORS origins are built from it.

    Nothing is evicted. If 8098 is taken, Dexter takes 8099 and says so.
    """
    handed_over = (os.getenv("DEXTER_BOUND_PORT") or "").strip()
    if handed_over.isdigit() and int(handed_over) > 0:
        return int(handed_over)
    chosen = buzzcaf_ports.pick_port(PREFERRED_PORT, host=HOST)
    os.environ["DEXTER_BOUND_PORT"] = str(chosen)
    return chosen


SERVER_PORT = _bind_port()
HEALTH_URL = f"http://{HOST}:{SERVER_PORT}/health"


#: Set only once an entry of ours is really in the ledger. `withdraw_port` is
#: registered with atexit, and without this flag a process that never published
#: - a test run, an import - would delete the *live* Dexter's entry on its way
#: out.
_published = False


def publish_when_ready(port: int = 0, timeout: float = 30.0) -> threading.Thread:
    """Announce Dexter in the shared ledger — but only once it really answers.

    Publishing at startup instead would make the ledger lie: the next app to
    look would see an entry, trust the live pid, and connect to a socket that
    is not open yet.
    """
    port = int(port or SERVER_PORT)
    health = f"http://{HOST}:{port}/health"

    def _run() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            body = buzzcaf_ports._probe(health)
            if body and body.get("app") == APP_ID:
                global _published
                try:
                    buzzcaf_ports.publish(APP_ID, port, health)
                    _published = True
                    print(f"[Ports] Dexter is on {port}; published to {buzzcaf_ports.ledger_path()}")
                except Exception as exc:
                    print(f"[Ports] Could not publish: {exc}")
                return
            time.sleep(0.25)
        print(f"[Ports] {health} never answered; not publishing a port nobody can use.")

    thread = threading.Thread(target=_run, name="dexter-ports-publish", daemon=True)
    thread.start()
    return thread


def withdraw_port() -> None:
    """Take the entry back out. A ledger full of dead apps is worse than none."""
    global _published
    if not _published:
        return
    _published = False
    try:
        buzzcaf_ports.withdraw(APP_ID)
    except Exception as exc:
        print(f"[Ports] Could not withdraw: {exc}")


# A backstop for the ways a desktop app really ends (tray Quit, a raised
# SystemExit); the lifespan shutdown below covers a graceful uvicorn stop.
atexit.register(withdraw_port)

# Macro recording captures tool calls as they execute.
intent_router.TOOL_OBSERVERS.append(macros.observe_tool_call)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm every index in the background so the first request is not the slow one."""
    bus.bind_loop(asyncio.get_running_loop())

    removed = macros.purge_invalid()
    if removed:
        print(f"[Startup] Removed {len(removed)} auto-generated junk skill(s).")

    app_index.build_async()

    # Warm the caches off the request path. The intent index (torch import +
    # 400 phrase embeddings, ~6 s) used to block startup; building it here lets
    # the window open in a couple of seconds, and a message that arrives first
    # simply waits on the index lock inside rank_tools().
    def _warm_health() -> None:
        try:
            intent_router.build_intent_index()
        except Exception as exc:
            print(f"[Startup] Intent index build failed: {exc}")
        try:
            llm_router.get_available_providers(force=True)
            buzzcaf_client.health_check(force=True)
        except Exception as exc:
            print(f"[Startup] Health warm-up skipped: {exc}")
        # Load the small model now and pin it, so the first reply is instant too.
        # Through the steward: even a 2.5 GB model gets Sentinel's permission
        # first, because the machine may already be rendering.
        try:
            fast_prov = intent_router.fast_backend()
            if fast_prov:
                t0 = time.time()
                decision = model_steward.ensure(
                    intent_router.FAST_MODEL, "startup", provider=fast_prov,
                    keep_alive=intent_router.FAST_KEEP_ALIVE,
                )
                if not decision.granted:
                    print(f"[Startup] Fast model held back by Sentinel: {decision.message()}")
                else:
                    print(
                        f"[Startup] Fast model {intent_router.FAST_MODEL} on {fast_prov}: "
                        f"{'resident' if decision.loaded else 'not loaded'} ({time.time() - t0:.1f}s)."
                    )
            elif intent_router.TIERED_ENABLED and intent_router.FAST_MODEL:
                print(f"[Startup] Fast model {intent_router.FAST_MODEL} is not served by any online backend; replies use the smart model.")
        except Exception as exc:
            print(f"[Startup] Fast model warm-up skipped: {exc}")
        # Facts stored before semantic memory existed get their embeddings now.
        memory.backfill_embeddings()
        # Backfill yesterday's summary/facts if the machine slept through the
        # nightly reflection cron. Needs the LLM, hence after the health warm.
        import reflection

        reflection.catch_up()
        activity.activity_log.start()
        # Say good morning in the chat when Dexter is opened during the day (v7).
        try:
            companion.greet_if_needed()
        except Exception as exc:
            print(f"[Startup] Greeting skipped: {exc}")
        # Studio events (a step waiting for approval, a failed step) arrive as
        # banners and toasts instead of being discovered on the next poll.
        try:
            buzzcaf_client.start_event_listener(_on_studio_event)
        except Exception as exc:
            print(f"[Startup] Studio event listener not started: {exc}")
        # Catch up on files that changed while Dexter was off, within a small
        # budget; the rest waits for the 02:30 job.
        try:
            stats = knowledge_ingest.reindex(budget_seconds=60)
            if stats["scanned"]:
                print(
                    f"[Startup] Knowledge: {stats['scanned']} file(s) scanned, {stats['updated']} updated"
                    f"{' (partial, rest tonight)' if stats['partial'] else ''} in {stats['seconds']}s."
                )
        except Exception as exc:
            print(f"[Startup] Knowledge pass skipped: {exc}")

    threading.Thread(target=_warm_health, name="dexter-health-warm", daemon=True).start()

    # Voice models load on their own thread: Whisper, the VAD and Kokoro take a
    # few seconds between them, and the first "hey jarvis" should not pay for
    # it. Everything is guarded - a missing model just leaves /status honest.
    def _warm_voice() -> None:
        t0 = time.time()
        try:
            from stt_engine import stt_engine

            stt_engine.warm_up()
        except Exception as exc:
            print(f"[Startup] STT warm-up skipped: {exc}")
        try:
            from vad import VadGate
            from wakeword import WakeWordDetector

            VadGate()._ensure_model()
            WakeWordDetector()._ensure_model()
        except Exception as exc:
            print(f"[Startup] Wake/VAD warm-up skipped: {exc}")
        try:
            voice_engine.warm_up()
            voice_engine.prune_clips()
        except Exception as exc:
            print(f"[Startup] Speech warm-up skipped: {exc}")
        try:
            if speaker_id.warm_up():
                _enroll_dexter_voice()
        except Exception as exc:
            print(f"[Startup] Speaker-id warm-up skipped: {exc}")
        caps = voice_pipeline.capabilities()
        print(
            f"[Startup] Voice ready in {time.time() - t0:.1f}s: stt={caps['sttLoaded']} "
            f"({caps['model']}/{caps['device']}), wake={caps['wakeReady']} ({caps['wakeWord']}), "
            f"vad={caps['vadReady']}, kokoro={voice_engine.ready()}."
        )

    if VOICE_ENABLED:
        threading.Thread(target=_warm_voice, name="dexter-voice-warm", daemon=True).start()

    restored = settings_store.load_at_startup()
    if restored:
        print(f"[Startup] Applied {restored} saved setting(s).")

    # Tell Sentinel who we are and start listening for VRAM pressure, so the
    # big model can be dropped voluntarily instead of Dexter being frozen.
    try:
        intent_router.sentinel_start()
    except Exception as exc:
        print(f"[Startup] Sentinel registration skipped: {exc}")

    scheduler.start()
    armed = scheduler.rearm_commitments()
    if armed:
        print(f"[Startup] Armed {armed} commitment heads-up(s).")
    armed_goals = tasks.rearm_scheduled()
    if armed_goals:
        print(f"[Startup] Re-armed {armed_goals} scheduled goal(s).")

    print(f"[Startup] Dexter ready with {len(all_tools())} tools.")
    print(f"[Startup] Logging to {_LOG_PATH}")

    def _report_runtime() -> None:
        # Dexter used to run a 7B on the CPU at 9s a turn and say nothing about
        # it. Whatever is degraded, say so at startup with the cause.
        try:
            provider = llm_router.resolve_provider("auto")
            diagnosis = runtime_health.diagnose(
                provider, llm_router._model_for(provider, None), llm_router.ollama_url,
                force=True,
            )
            for line in runtime_health.summary_lines(diagnosis):
                print(f"[Startup] DEGRADED: {line}")
        except Exception as exc:
            print(f"[Startup] Runtime check skipped: {exc}")

    threading.Thread(target=_report_runtime, name="dexter-runtime-check", daemon=True).start()
    yield
    # Out of the ledger first: the moment this process stops answering, an
    # entry pointing at it is a wrong answer for everybody else.
    withdraw_port()
    scheduler.shutdown()
    # Hand memory and identity back before the process goes: a booking left
    # behind by a dead client starves the next claimant until it expires.
    try:
        intent_router.sentinel_stop()
    except Exception as exc:
        print(f"[Shutdown] Sentinel unregister skipped: {exc}")
    try:
        from browser import browser as _browser

        _browser.close()
    except Exception:
        pass


app = FastAPI(title="Dexter AI Super Assistant Engine", version="2.0.0", lifespan=lifespan)

# ───────────────────────── local auth ─────────────────────────

# A fresh token per server run. Any process on this machine can obtain it (via
# the bootstrap endpoint or the token file), which matches the trust model: the
# defense is against *remote* callers - a LAN peer if the bind ever changes,
# or a malicious web page driving the browser at localhost.
SESSION_TOKEN = secrets.token_urlsafe(32)
TOKEN_FILE = os.path.join(ROOT_DIR, "data", ".session_token")
try:
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as _handle:
        _handle.write(SESSION_TOKEN)
    if os.name == "nt":
        # The file inherits data/'s ACL, which can be readable by every local
        # account. Strip inheritance and grant only the current user (0.2).
        import subprocess as _sp
        _sp.run(
            ["icacls", TOKEN_FILE, "/inheritance:r", "/grant:r",
             f"{os.environ.get('USERNAME', '')}:F"],
            check=False, capture_output=True,
            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
        )
except Exception as _exc:
    print(f"[Auth] Could not persist session token: {_exc}")

# Reachable without the token: the health probe, the token bootstrap itself,
# and generated speech clips - those are played through Audio(), which cannot
# attach headers, and contain only Dexter's own words.
_TOKEN_EXEMPT = ("/api/dexter/health", "/api/dexter/auth/token", "/api/dexter/audio/")
_ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost", "::1", os.getenv("DEXTER_HOST", "127.0.0.1")}


@app.middleware("http")
async def require_local_token(request: Request, call_next):
    # The Host check defeats DNS rebinding: a page on evil.com resolving to
    # 127.0.0.1 reaches this socket, but its Host header still names evil.com.
    hostname = (request.url.hostname or "").lower()
    if hostname and hostname not in _ALLOWED_HOSTNAMES:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)

    path = request.url.path
    if (
        request.method != "OPTIONS"  # CORS preflight cannot carry custom headers
        and path.startswith("/api/")
        and not path.startswith(_TOKEN_EXEMPT)
    ):
        supplied = request.headers.get("x-dexter-token") or request.query_params.get("token")
        if not supplied or not secrets.compare_digest(supplied, SESSION_TOKEN):
            return JSONResponse(
                {"detail": "Missing or invalid X-Dexter-Token"}, status_code=401
            )
    return await call_next(request)


# The frontend is served from three different shells (Vite dev server, the
# backend's own static mount, and Electron's file:// origin which reports
# "null"). Explicit origins only - a page's origin is never an arbitrary
# file:// URL, just the literal "file://" or "null" markers.
# Added after the auth middleware so CORS runs first and error responses
# still carry the headers the browser needs to surface them.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # The Vite dev server, and the backend's own origin on whatever port it
        # actually took. Nothing here is a literal: a hardcoded 8098 stopped
        # working the first time something else held it.
        f"http://localhost:{VITE_PORT}",
        f"http://127.0.0.1:{VITE_PORT}",
        f"http://localhost:{SERVER_PORT}",
        f"http://127.0.0.1:{SERVER_PORT}",
        "null",
        "file://",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/dexter/health")
async def health():
    return {"ok": True}


@app.get("/health")
async def identity_health():
    """Who is on this port (GUARDIAN_PLAN §11, rule 4).

    Loopback ports are crowded and every app's default collides with someone
    else's. A scan that finds a listener here and does not see `app: "dexter"`
    is supposed to keep scanning — which is only possible because this route
    answers the question "who are you", not "are you alive".
    """
    return {
        "app": APP_ID,
        "status": "ok",
        "port": SERVER_PORT,
        "pid": os.getpid(),
        "version": VERSION,
    }


@app.get("/api/dexter/auth/token")
async def get_session_token():
    """Token bootstrap for the local UI shells (guarded by the Host check)."""
    return {"token": SESSION_TOKEN}


# ───────────────────────── models ─────────────────────────


class ChatRequest(BaseModel):
    message: str
    provider: Optional[str] = "auto"
    model: Optional[str] = None
    session: Optional[str] = "default"
    # Speak the reply back (sentence by sentence, as `audio` stream events).
    voiceEnabled: Optional[bool] = False
    voiceKey: Optional[str] = None
    # The message was spoken, so the reply will be heard, not read.
    voiceInput: Optional[bool] = False


class TTSRequest(BaseModel):
    text: str
    voiceKey: Optional[str] = None
    emotion: Optional[str] = None
    isReminder: Optional[bool] = False


class InterruptRequest(BaseModel):
    session: Optional[str] = "default"


class AddTaskRequest(BaseModel):
    title: str
    time: Optional[str] = "12:00 PM"


class LaunchAppRequest(BaseModel):
    appName: str


class BrowseRequest(BaseModel):
    url: str


class FileSearchRequest(BaseModel):
    query: str
    path: Optional[str] = None


class FileReadRequest(BaseModel):
    path: str


class FileWriteRequest(BaseModel):
    path: str
    content: str


class FileOpenRequest(BaseModel):
    path: str


class CreateFolderRequest(BaseModel):
    path: str


class CreateNoteRequest(BaseModel):
    content: str
    title: Optional[str] = "Sticky Note"


class ReminderRequest(BaseModel):
    text: str
    when: str
    channels: Optional[List[str]] = None


class RememberRequest(BaseModel):
    key: str
    value: str


class BuzzcafProjectRequest(BaseModel):
    name: str
    brand: str
    workflow_name: Optional[str] = "full_video_workflow"


class BuzzcafFeedbackRequest(BaseModel):
    feedback: Optional[str] = None


# ───────────────────────── status ─────────────────────────


@app.get("/api/dexter/status")
async def get_status():
    providers = await run_in_threadpool(llm_router.get_available_providers)
    planner = await run_in_threadpool(agenda.compose)
    skills = await run_in_threadpool(macros.list_skills)
    note_list = await run_in_threadpool(notes.list_notes)
    buzzcaf_status = await run_in_threadpool(buzzcaf_client.get_status)

    active_provider = llm_router.resolve_provider("auto")
    runtime = await run_in_threadpool(
        runtime_health.diagnose,
        active_provider,
        llm_router._model_for(active_provider, None),
        llm_router.ollama_url,
    )
    tiers = await run_in_threadpool(_tier_status)
    sentinel = await run_in_threadpool(runtime_health.sentinel_state)

    return {
        "status": "online",
        "assistantName": "Dexter Super Assistant",
        "version": VERSION,
        "port": SERVER_PORT,
        "providers": providers,
        "preferredProvider": llm_router.default_provider,
        "runtime": runtime,
        "tiers": tiers,
        "sentinel": sentinel,
        "toolCount": len(all_tools()),
        "intentMode": intent_router._embedder.mode,
        "appsIndexed": app_index.size(),
        "skillsCount": len(skills),
        "tasksCount": len(planner.get("tasks", [])),
        "notesCount": len(note_list),
        "scheduler": scheduler.status(),
        "memory": memory.stats(),
        "profile": {"name": user_profile.load().get("name") or None, "known": not user_profile.is_empty()},
        "knowledgeRoots": [f"{tag}={path}" for tag, path in knowledge_ingest.parse_roots()],
        "workbook": workbook.stats(),
        "integrations": {
            "windows": windows_tools.availability(),
            "media": media_tools.availability(),
            "google": google_tools.availability(),
        },
        "voice": {
            "enabled": VOICE_ENABLED,
            **voice_pipeline.capabilities(),
            "ttsReady": voice_engine.ready() or voice_engine.kokoro_available(),
            "debug": _voice_debug,
        },
        "buzzcafAI": {
            "online": buzzcaf_status.get("online", False),
            "activeProjects": buzzcaf_status.get("in_progress", 0),
            "totalProjects": buzzcaf_status.get("total_projects", 0),
        },
    }


def _tier_status() -> Dict[str, Any]:
    """Which model answers instantly, which one does the work, and what is in memory."""
    fast_prov = intent_router.fast_backend()
    smart_prov, smart_model = intent_router.smart_backend()
    return {
        "enabled": bool(intent_router.TIERED_ENABLED and intent_router.FAST_MODEL),
        "fast": {"provider": fast_prov, "model": intent_router.FAST_MODEL if fast_prov else None,
                 "keepAlive": intent_router.FAST_KEEP_ALIVE},
        "smart": {"provider": smart_prov, "model": smart_model or llm_router._model_for(smart_prov, None),
                  "keepAlive": intent_router.SMART_KEEP_ALIVE},
        "loaded": llm_router.loaded_models(),
    }


@app.get("/api/dexter/llm/models")
async def get_llm_models():
    """Providers, their online state, and available models - for the UI picker."""
    return await run_in_threadpool(llm_router.list_models)


@app.get("/api/dexter/tools")
async def list_registered_tools():
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "category": spec.category,
            "signature": spec.signature_hint(),
            "confirm": spec.confirm,
            "examples": spec.examples[:3],
        }
        for spec in all_tools()
    ]


# ───────────────────────── chat ─────────────────────────


SPOKEN_REPLY_STYLE = (
    "The user said this out loud and will hear your reply read by text-to-speech. "
    "Answer the way you would say it to a friend across the desk: one to three short "
    "sentences, contractions, plain words, no markdown, no bullet points, no code, "
    "no URLs or file paths, no emojis. If there is a lot to say, give the headline "
    "and offer the rest. Start the reply with ONE mood tag in square brackets that "
    "fits how you'd say it - [warm], [upbeat], [calm], [apologetic], [urgent] or "
    "[thinking] - the tag is not spoken."
)

_MOOD_TAG = re.compile(
    r"^\s*[\[\(\*]\s*(warm|upbeat|calm|apologetic|urgent|thinking|neutral|excited|happy|sorry)\s*[\]\)\*][:,.\-\s]*",
    re.IGNORECASE,
)


def _take_mood_tag(text: str):
    """Split a leading [mood] tag off a reply: (emotion or None, clean text)."""
    match = _MOOD_TAG.match(text or "")
    if not match:
        return None, text
    from prosody import _CUE_TO_EMOTION

    return _CUE_TO_EMOTION.get(match.group(1).lower()), text[match.end():].lstrip()


def _run_route(req: ChatRequest, on_event=None) -> Dict[str, Any]:
    """Route one message, with conversation history and stored facts as context."""
    session = req.session or "default"

    # A spoken yes/no can answer a background task waiting at a checkpoint,
    # as long as the chat itself has no confirmation pending.
    task_answer = tasks.task_queue.try_voice_confirmation(req.message)
    if task_answer:
        memory.add_turn("user", req.message, session=session)
        memory.add_turn("assistant", task_answer["reply"], session=session)
        return task_answer

    # "done", "on it", "later" after a check-in are answers, not commands (v7).
    short = companion.handle_short_reply(req.message)
    if short:
        memory.add_turn("user", req.message, session=session)
        memory.add_turn("assistant", short, session=session)
        return intent_router.RouteResult(reply=short, action="checkin_reply", tier=0).to_dict()

    history = memory.recent_turns(session=session)

    context_parts = []
    # Telling the agent what is on screen is what lets "summarise this" or
    # "close it" resolve to something concrete.
    active = windows_tools.get_active_window_title()
    if active:
        context_parts.append(f"The user currently has this window in focus: {active}")
    # What they have been doing: app + project + minutes, files just saved, today's shape (v7).
    try:
        context_parts.extend(activity.context_lines())
    except Exception as exc:
        print(f"[Chat] activity context skipped: {exc}")
    facts = memory.context_note()
    if facts:
        context_parts.append(facts)
    # Memories that match *this* message - past summaries, ingested documents,
    # older facts - so "second brain" recall happens without an explicit ask.
    relevant = memory.semantic_context(req.message)
    if relevant:
        context_parts.append(relevant)
    if req.voiceInput:
        # A spoken question gets a spoken-shaped answer. Lists, markdown and
        # paths read aloud are the difference between an assistant and a
        # screen reader.
        context_parts.append(SPOKEN_REPLY_STYLE)
    context = "\n\n".join(context_parts)

    result = intent_router.route(
        req.message,
        history=history,
        provider=req.provider or "auto",
        model=req.model,
        context_note=context,
        on_event=on_event,
        session=session,
    )

    # The mood tag is for the voice, not the chat (v10.2).
    if req.voiceInput or req.voiceEnabled:
        mood, clean = _take_mood_tag(result.reply)
        if mood:
            result.reply = clean
            result.data = {**(result.data or {}), "speechEmotion": (result.data or {}).get("speechEmotion") or mood}

    # One coaching sentence on tool replies so they read like a colleague (v7).
    if result.tier <= 1 and result.tool_calls and not result.needs_confirmation:
        try:
            last_call = result.tool_calls[-1]
            result.reply = companion.after_reply(last_call.get("name"), bool(last_call.get("ok")), result.reply)
        except Exception as exc:
            print(f"[Chat] companion tail skipped: {exc}")

    memory.add_turn("user", req.message, session=session)
    memory.add_turn(
        "assistant",
        result.reply,
        tool_calls=", ".join(c["name"] for c in result.tool_calls) or None,
        session=session,
    )
    return result.to_dict()


@app.post("/api/dexter/chat")
async def process_chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        return {"reply": "I didn't catch that.", "action": "empty", "toolCalls": []}
    return await run_in_threadpool(_run_route, req)


# A sentence is speakable once its terminator is followed by whitespace; the
# minimum offset keeps abbreviations and tiny fragments from triggering a chunk.
_SENTENCE_END = re.compile(r"[.!?][\"'\)\]]*\s")
_MIN_STREAM_SENTENCE = 12


@app.post("/api/dexter/chat/stream")
async def process_chat_stream(req: ChatRequest):
    """
    Chat with incremental delivery: LLM tokens stream to the UI as they are
    generated (`token` events, plus `tool_start` / `provider_switched`), then
    the authoritative `reply`, then `done`. Fast-path replies skip straight to
    `reply` because no model ran.

    With `voiceEnabled`, completed sentences are synthesised while later ones
    are still being written and arrive as `audio` events (a clip URL each), so
    speech starts before the model has finished the reply. Fast-path replies
    are voiced sentence by sentence once they land.
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event_type: str, payload: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event_type, payload))

    session_id = req.session or "default"
    speak = bool(req.voiceEnabled) and VOICE_ENABLED
    voice_key = req.voiceKey or default_voice_key()

    # Synthesis runs on its own thread so a 300 ms Kokoro call never stalls
    # the token stream. Sentences go in; audio events come out.
    tts_queue: "sync_queue.Queue[Optional[str]]" = sync_queue.Queue()
    state = {"pending": "", "spoke_stream": False, "emotion": None}

    def tts_worker() -> None:
        while True:
            sentence = tts_queue.get()
            if sentence is None:
                return
            # Interrupted: drain silently instead of speaking sentences the
            # user has already talked over.
            if cancellation.is_cancelled(session_id):
                continue
            try:
                delivery, chunks = voice_engine.plan_speech(sentence, "", None, False, False, state["emotion"])
                if state["emotion"] is None:
                    # Lock the register decided for the first sentence so the
                    # reply does not change mood halfway through.
                    state["emotion"] = delivery.emotion
                    emit("delivery", {"emotion": delivery.emotion, "speed": delivery.speed})
                for chunk in chunks:
                    if cancellation.is_cancelled(session_id):
                        break
                    path = voice_engine.synthesize_chunk(chunk, delivery, voice_key)
                    if path:
                        emit("audio", {
                            "text": chunk,
                            "emotion": delivery.emotion,
                            "url": f"/api/dexter/audio/{os.path.basename(path)}",
                        })
            except Exception as exc:
                print(f"[Stream] Incremental TTS failed: {exc}")

    def routed_emit(event_type: str, payload: Any) -> None:
        """Pass events through, feeding completed sentences to the TTS thread."""
        if event_type == "token" and speak and not cancellation.is_cancelled(session_id):
            state["pending"] += payload.get("text", "")
            while True:
                match = _SENTENCE_END.search(state["pending"], _MIN_STREAM_SENTENCE)
                if not match:
                    break
                sentence = state["pending"][: match.end()].strip()
                state["pending"] = state["pending"][match.end():]
                if sentence:
                    state["spoke_stream"] = True
                    tts_queue.put(sentence)
        elif event_type == "provider_switched":
            # The retry regenerates the reply from scratch; drop the partial.
            state["pending"] = ""
        emit(event_type, payload)

    async def worker() -> None:
        cancellation.begin(session_id)
        tts_thread = threading.Thread(target=tts_worker, daemon=True, name="dexter-stream-tts")
        tts_thread.start()
        try:
            result = await run_in_threadpool(_run_route, req, routed_emit)
            if result.get("action") == "interrupted" or cancellation.is_cancelled(session_id):
                emit("interrupted", {})
                return
            emit("reply", result)

            if speak:
                if state["spoke_stream"]:
                    # Streamed sentences are already voiced; finish the tail.
                    tail = state["pending"].strip()
                    if tail:
                        tts_queue.put(tail)
                else:
                    calls = result.get("toolCalls") or []
                    # A failed tool should sound apologetic, not chirpy.
                    tool_ok = all(c.get("ok", True) for c in calls) if calls else None
                    reply_text = result.get("reply", "")
                    # A tool can ask for a specific delivery - read_aloud wants
                    # narration rather than the clipped confirmation tone.
                    requested = (result.get("details") or {}).get("speechEmotion")
                    if not requested:
                        requested = next((c.get("speechEmotion") for c in calls if c.get("speechEmotion")), None)
                    delivery, chunks = await run_in_threadpool(
                        voice_engine.plan_speech, reply_text, result.get("action", ""),
                        tool_ok, False, len(reply_text) > 600, requested,
                    )
                    emit("delivery", {"emotion": delivery.emotion, "speed": delivery.speed})
                    for chunk in chunks:
                        if cancellation.is_cancelled(session_id):
                            break
                        path = await run_in_threadpool(voice_engine.synthesize_chunk, chunk, delivery, voice_key)
                        if path:
                            emit("audio", {
                                "text": chunk,
                                "emotion": delivery.emotion,
                                "url": f"/api/dexter/audio/{os.path.basename(path)}",
                            })
        except Exception as exc:
            emit("error", {"message": str(exc)})
        finally:
            tts_queue.put(None)
            await run_in_threadpool(tts_thread.join)
            cancellation.clear(session_id)
            emit("done", {})

    async def generate():
        task = asyncio.create_task(worker())
        try:
            while True:
                event_type, payload = await queue.get()
                yield _sse(event_type, payload)
                if event_type == "done":
                    break
        finally:
            task.cancel()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event_type: str, payload: Any) -> str:
    import json

    body = json.dumps({"type": event_type, "payload": payload}, ensure_ascii=False)
    return f"event: {event_type}\ndata: {body}\n\n"


@app.get("/api/dexter/events")
async def event_stream(request: Request):
    """Long-lived push channel: reminders, notifications, background status."""
    queue = bus.subscribe()

    async def generate():
        try:
            async for frame in bus.stream(queue):
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ───────────────────────── voice (v10) ─────────────────────────


def _ws_authorized(ws: WebSocket) -> bool:
    """
    Authorise a WebSocket by hand: the HTTP middleware does not run for the
    WebSocket scope, so the Host check (DNS rebinding) and the per-run token
    (as a query param - WebSocket cannot attach headers) are repeated here.
    """
    hostname = (ws.headers.get("host") or "").split(":")[0].lower()
    if hostname and hostname not in _ALLOWED_HOSTNAMES:
        return False
    supplied = ws.query_params.get("token")
    return bool(supplied) and secrets.compare_digest(supplied, SESSION_TOKEN)


# Capture diagnostics so "the mic isn't heard" can be checked from /status: if
# `peak` stays ~0 while the user speaks, no real audio is arriving (suspended
# AudioContext, muted or wrong device) rather than a wake/STT problem.
_voice_debug: Dict[str, Any] = {
    "connections": 0,
    "active": None,     # which window currently owns the mic
    "frames": 0,
    "peak": 0,          # loudest sample seen this run (int16, 0-32767)
    "lastPeak": 0,      # loudest sample in the most recent frame
    "lastHeard": "",    # the last transcript, for the status page
}


def _record_voice_debug(data: bytes) -> None:
    try:
        import numpy as np

        arr = np.frombuffer(data, dtype=np.int16)
        if arr.size:
            peak = int(np.abs(arr).max())
            _voice_debug["frames"] += 1
            _voice_debug["lastPeak"] = peak
            if peak > _voice_debug["peak"]:
                _voice_debug["peak"] = peak
    except Exception:
        pass


_voice_hub = voice_pipeline.VoiceHub()


@app.websocket("/api/dexter/voice/ws")
async def voice_socket(ws: WebSocket):
    """
    Inbound audio channel: the renderer streams 16 kHz mono int16 PCM as binary
    frames; the backend runs wake word -> VAD -> STT and pushes back JSON voice
    events (listening / transcript / command / heard_nothing / interrupt).
    Text frames are controls: speaking, listen, stop, followup, claim, reset.
    """
    if not _ws_authorized(ws):
        await ws.close(code=1008)
        return
    if not VOICE_ENABLED:
        await ws.accept()
        await ws.send_json({"type": "status", "enabled": False, "sttReady": False, "vadReady": False})
        await ws.close()
        return
    await ws.accept()

    session_id = ws.query_params.get("session") or "default"
    label = ws.query_params.get("window") or "window"
    vsession = _voice_hub.session(session_id)
    active = _voice_hub.join(session_id, ws)
    _voice_debug["connections"] = _voice_hub.connections()
    caps = voice_pipeline.capabilities()
    print(f"[Voice WS] {label} connected (session={session_id}, active={active}): {caps}")
    await ws.send_json({"type": "status", "enabled": True, "active": active, **caps})
    if active:
        _voice_debug["active"] = label

    async def send_events(events: List[Dict[str, Any]]) -> None:
        for event in events:
            if event.get("type") == "transcript":
                _voice_debug["lastHeard"] = event.get("text", "")
            await ws.send_json(event)

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            data = msg.get("bytes")
            if data is not None:
                if not _voice_hub.is_active(session_id, ws):
                    continue  # a standby window; its audio is not the mic
                _record_voice_debug(data)
                events = await run_in_threadpool(vsession.feed, data)
                await send_events(events)
                continue

            text = msg.get("text")
            if not text:
                continue
            try:
                ctrl = json.loads(text)
            except Exception:
                continue
            kind = ctrl.get("type")
            if kind == "claim":
                lost = _voice_hub.claim(session_id, ws)
                _voice_debug["active"] = label
                if lost is not None:
                    try:
                        await lost.send_json({"type": "standby"})
                    except Exception:
                        pass
                    vsession.reset()
                await ws.send_json({"type": "active"})
                continue
            if not _voice_hub.is_active(session_id, ws):
                continue
            if kind == "speaking":
                vsession.set_speaking(bool(ctrl.get("on")))
            elif kind == "listen":
                await send_events(vsession.listen("manual"))
            elif kind == "stop":
                await send_events(vsession.stop())
            elif kind == "followup":
                vsession.arm_followup()
            elif kind == "enroll":
                await send_events(vsession.begin_enroll(ctrl.get("seconds")))
            elif kind == "enroll_cancel":
                vsession.cancel_enroll()
                await ws.send_json({"type": "enrolled", "ok": False, "reason": "cancelled"})
            elif kind == "mode":
                mode = str(ctrl.get("mode") or "").lower()
                if mode in ("open", "name", "model", "both"):
                    await run_in_threadpool(settings_store.update, {"wakeMode": mode})
                    vsession.reset()
                    await ws.send_json({"type": "status", "enabled": True, "active": True, **voice_pipeline.capabilities()})
            elif kind == "reset":
                vsession.reset()
    except WebSocketDisconnect:
        print(f"[Voice WS] {label} disconnected (session={session_id}).")
    except Exception as exc:
        print(f"[Voice WS] {label} closed on error: {exc}")
    finally:
        promoted = _voice_hub.leave(session_id, ws)
        _voice_debug["connections"] = _voice_hub.connections()
        if promoted is not None:
            try:
                await promoted.send_json({"type": "active"})
            except Exception:
                pass


@app.post("/api/dexter/voice/interrupt")
async def voice_interrupt(req: InterruptRequest = InterruptRequest()):
    """Manual stop (the stop button / Esc) - cancels the session's stream."""
    session = req.session or "default"
    cancellation.request_cancel(session)
    return {"ok": True, "session": session}


@app.post("/api/dexter/tts/generate")
async def generate_expressive_tts(req: TTSRequest):
    """
    One clip for text Dexter says on its own (a reminder, a check-in). Runs the
    same prosody pipeline as streaming chat so a reminder sounds urgent and a
    greeting warm.
    """
    if not VOICE_ENABLED:
        raise HTTPException(status_code=503, detail="Voice is disabled")
    paths, _delivery = await run_in_threadpool(
        voice_engine.speak_expressive,
        req.text, "", None, bool(req.isReminder), len(req.text or "") > 600,
        req.emotion, req.voiceKey or default_voice_key(),
    )
    file_path = paths[0] if paths else None
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=503, detail="Speech synthesis unavailable")
    media_type = "audio/wav" if file_path.lower().endswith(".wav") else "audio/mpeg"
    return FileResponse(file_path, media_type=media_type)


@app.get("/api/dexter/audio/{filename}")
async def get_audio(filename: str):
    """Serve a generated clip for the streaming chat endpoint."""
    safe = os.path.basename(filename)
    path = os.path.join(AUDIO_DIR, safe)
    if not safe.startswith("speech_") or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Audio not found")
    media_type = "audio/wav" if safe.lower().endswith(".wav") else "audio/mpeg"
    return FileResponse(path, media_type=media_type)


def _enroll_dexter_voice(force: bool = False) -> bool:
    """
    A voice print of Dexter's own voice (the current Kokoro voice), so the
    speaker check can tell its echo from the owner. Refreshed when the voice
    changes.
    """
    key = default_voice_key()
    if not force and speaker_id.summary().get("dexterVoice") == key:
        return True
    if not voice_engine.ready() and not voice_engine.kokoro_available():
        return False
    try:
        import numpy as np
        import soundfile as sf

        parts = []
        for line in ("Here is what I found. Two things are due today and one slipped.",
                     "Sure, opening Chrome now. Anything else while I am at it?"):
            path = voice_engine.generate_speech_kokoro(line, voice_name=voice_engine.KOKORO_VOICES[key], prepared=True)
            if not path:
                return False
            data, rate = sf.read(path, dtype="float32")
            if data.ndim > 1:
                data = data[:, 0]
            n = int(len(data) * 16000 / rate)
            parts.append(np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype(np.float32))
        ok = speaker_id.enroll_dexter(np.concatenate(parts), voice_key=key)
        print(f"[Speaker] Dexter's own voice print {'stored' if ok else 'failed'} ({key}).")
        return ok
    except Exception as exc:
        print(f"[Speaker] Could not enrol Dexter's voice: {exc}")
        return False


class VoicePreviewRequest(BaseModel):
    voiceKey: Optional[str] = None
    text: Optional[str] = None
    emotion: Optional[str] = None


@app.get("/api/dexter/voice/profile")
async def get_voice_profile():
    """Who Dexter listens to: enrolment state and the speaker settings."""
    return {**speaker_id.summary(), "wakeMode": voice_pipeline.WAKE_MODE, "voiceKey": default_voice_key()}


@app.delete("/api/dexter/voice/profile")
async def forget_voice_profile():
    await run_in_threadpool(speaker_id.forget)
    return {"ok": True, **speaker_id.summary()}


@app.post("/api/dexter/voice/preview")
async def preview_voice(req: VoicePreviewRequest):
    """One clip in a given voice, so picking a voice is a matter of listening."""
    if not VOICE_ENABLED:
        raise HTTPException(status_code=503, detail="Voice is disabled")
    key = (req.voiceKey or default_voice_key()).lower()
    if key not in voice_engine.KOKORO_VOICES:
        raise HTTPException(status_code=400, detail="Unknown voice")
    text = req.text or "Hey. I'm Dexter. Today's win is still open. Where do we start?"
    paths, _delivery = await run_in_threadpool(
        voice_engine.speak_expressive, text, "chitchat", None, False, False, req.emotion or "warm", key,
    )
    file_path = paths[0] if paths else None
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=503, detail="Speech synthesis unavailable")
    return FileResponse(file_path, media_type="audio/wav" if file_path.lower().endswith(".wav") else "audio/mpeg")


# ───────────────────────── planner ─────────────────────────


@app.get("/api/dexter/planner")
async def get_planner():
    """Today, composed: commitments (workbook), reminders, calendar events."""
    return await run_in_threadpool(agenda.compose)


@app.post("/api/dexter/planner/add")
async def add_planner_task(req: AddTaskRequest):
    try:
        item = await run_in_threadpool(agenda.add, req.title, req.time)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return item


@app.post("/api/dexter/planner/toggle/{task_id}")
async def toggle_planner_task(task_id: str):
    return await run_in_threadpool(agenda.toggle, task_id)


# ───────────────────────── reminders ─────────────────────────


@app.get("/api/dexter/reminders")
async def get_reminders():
    return scheduler.list_reminders(include_fired=False)


@app.post("/api/dexter/reminders")
async def add_reminder(req: ReminderRequest):
    result = await run_in_threadpool(
        scheduler.create_reminder, req.text, req.when, req.channels
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid time"))
    return result


@app.delete("/api/dexter/reminders/{reminder_id}")
async def delete_reminder(reminder_id: str):
    return {"success": scheduler.cancel_reminder(reminder_id)}


# ───────────────────────── memory ─────────────────────────


@app.get("/api/dexter/memory")
async def get_memory():
    return {"facts": memory.all_facts(), "stats": memory.stats()}


@app.post("/api/dexter/memory")
async def add_memory(req: RememberRequest):
    return {"success": memory.remember(req.key, req.value)}


@app.delete("/api/dexter/memory/{key}")
async def delete_memory(key: str):
    return {"success": memory.forget(key)}


@app.get("/api/dexter/memory/history")
async def get_history(session: str = "default", limit: int = 20):
    """
    The recent conversation, so the UI can resume the thread on launch.

    The turns were always persisted and fed to the model, but there was no way
    to read them back - so every relaunch showed a blank welcome while Dexter
    itself remembered the conversation. That mismatch read as amnesia.
    """
    limit = max(1, min(int(limit), 100))
    turns = await run_in_threadpool(memory.recent_turns, limit, session)
    return {"session": session, "turns": turns}


@app.post("/api/dexter/memory/history/clear")
async def clear_history(session: str = "default"):
    return {"cleared": memory.clear_history(session)}


# ───────────────────────── skills ─────────────────────────


@app.get("/api/dexter/skills")
async def get_skills():
    return await run_in_threadpool(macros.list_skills)


@app.delete("/api/dexter/skills/{name}")
async def delete_skill(name: str):
    return {"success": macros.delete_skill(name)}


# ───────────────────────── background tasks ─────────────────────────


class TaskSubmitRequest(BaseModel):
    goal: str


class TaskConfirmRequest(BaseModel):
    approve: bool = True


# ───────────────────────── workbook ─────────────────────────


class CommitmentRequest(BaseModel):
    text: str
    when: Optional[str] = None
    goal: Optional[str] = None


class GoalRequest(BaseModel):
    title: str
    why: Optional[str] = ""


class CloseOutRequest(BaseModel):
    note: Optional[str] = ""
    rollUnfinished: Optional[bool] = True


@app.get("/api/dexter/workbook")
async def get_workbook():
    """Today, the goals it serves, and what is slipping - the cockpit view."""
    def snapshot():
        return {
            "today": workbook.today(),
            "goals": workbook.list_goals(status="active"),
            "slipping": workbook.slipping(),
            "stats": workbook.stats(),
            "closedOut": workbook.was_closed_out(date.today().isoformat()),
        }

    return await run_in_threadpool(snapshot)


@app.get("/api/dexter/workbook/review")
async def get_workbook_review():
    return await run_in_threadpool(workbook.scorecard)


@app.post("/api/dexter/workbook/goals")
async def create_goal(req: GoalRequest):
    return await run_in_threadpool(workbook.add_goal, req.title, req.why or "")


@app.post("/api/dexter/workbook/commitments")
async def create_commitment(req: CommitmentRequest):
    # Route through the tool rather than the store so the UI and the voice path
    # share one set of rules (date parsing, goal matching, the spoken summary).
    result = await run_in_threadpool(
        execute, "make_commitment",
        {"text": req.text, "when": req.when or "", "goal": req.goal or ""},
    )
    return {"ok": result.ok, "summary": result.summary, **result.data}


@app.post("/api/dexter/workbook/commitments/{commitment_id}/done")
async def finish_commitment(commitment_id: int):
    return await run_in_threadpool(workbook.complete_commitment, commitment_id, "")


@app.post("/api/dexter/workbook/commitments/{commitment_id}/roll")
async def move_commitment(commitment_id: int):
    return await run_in_threadpool(workbook.roll_commitment, commitment_id, None)


@app.post("/api/dexter/workbook/close-out")
async def close_out_today(req: CloseOutRequest):
    return await run_in_threadpool(
        workbook.close_out, None, bool(req.rollUnfinished), req.note or ""
    )


# ───────────────────────── settings ─────────────────────────


class SettingsUpdate(BaseModel):
    changes: Dict[str, Any]


@app.get("/api/dexter/settings")
async def read_settings():
    """The tunable knobs, their current values, and whether each applies live."""
    return {"settings": await run_in_threadpool(settings_store.describe)}


@app.post("/api/dexter/settings")
async def write_settings(req: SettingsUpdate):
    result = await run_in_threadpool(settings_store.update, req.changes)
    if "voiceKey" in (result.get("applied") or []):
        # A new voice is a new voice print, or Dexter would stop recognising
        # its own echo.
        threading.Thread(target=_enroll_dexter_voice, kwargs={"force": True}, name="dexter-voiceprint", daemon=True).start()
    return result


class ProfileUpdate(BaseModel):
    profile: Dict[str, Any]


@app.get("/api/dexter/profile")
async def read_profile():
    return {"profile": user_profile.load(), "summary": user_profile.summary()}


@app.post("/api/dexter/profile")
async def write_profile(req: ProfileUpdate):
    saved = await run_in_threadpool(user_profile.save, req.profile)
    return {"profile": saved, "summary": user_profile.summary()}


@app.post("/api/dexter/knowledge/reindex")
async def reindex_knowledge_now():
    return await run_in_threadpool(knowledge_ingest.reindex)


@app.get("/api/dexter/tasks/scheduled")
async def get_scheduled_tasks():
    return await run_in_threadpool(tasks.list_scheduled)


@app.get("/api/dexter/tasks")
async def get_tasks():
    return await run_in_threadpool(tasks.task_queue.list)


@app.post("/api/dexter/tasks")
async def submit_task(req: TaskSubmitRequest):
    return await run_in_threadpool(tasks.task_queue.submit, req.goal)


@app.post("/api/dexter/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    return {"success": await run_in_threadpool(tasks.task_queue.cancel, task_id)}


@app.post("/api/dexter/tasks/{task_id}/confirm")
async def confirm_task(task_id: str, req: TaskConfirmRequest):
    return {"success": await run_in_threadpool(tasks.task_queue.confirm, task_id, req.approve)}


# ───────────────────────── system / files ─────────────────────────


@app.post("/api/dexter/system/launch")
async def launch_system_app(req: LaunchAppRequest):
    return await run_in_threadpool(AppLauncherTool.launch_app, req.appName)


@app.post("/api/dexter/browser/navigate")
async def browse_url(req: BrowseRequest):
    return await run_in_threadpool(PlaywrightBrowserTool.browse_url, req.url)


@app.post("/api/dexter/filesystem/search")
async def search_files(req: FileSearchRequest):
    return await run_in_threadpool(FileSystemTool.search_files, req.query, req.path)


@app.post("/api/dexter/filesystem/read")
async def read_file(req: FileReadRequest):
    return await run_in_threadpool(FileSystemTool.read_file, req.path)


@app.post("/api/dexter/filesystem/write")
async def write_file(req: FileWriteRequest):
    return await run_in_threadpool(FileSystemTool.write_file, req.path, req.content)


@app.post("/api/dexter/filesystem/create-folder")
async def create_folder(req: CreateFolderRequest):
    return await run_in_threadpool(FileSystemTool.create_folder, req.path)


@app.post("/api/dexter/filesystem/open")
async def open_file_path(req: FileOpenRequest):
    return await run_in_threadpool(FileSystemTool.open_local_path, req.path)


# ───────────────────────── notes / media / content ─────────────────────────


class MissionUpdate(BaseModel):
    northStar: Optional[str] = None
    why: Optional[str] = None
    focus: Optional[str] = None
    todayWin: Optional[str] = None


@app.get("/api/dexter/activity")
async def get_activity():
    return await run_in_threadpool(activity.brief)


@app.get("/api/dexter/mission")
async def get_mission():
    return await run_in_threadpool(mission.status)


@app.post("/api/dexter/mission")
async def update_mission(req: MissionUpdate):
    if req.northStar is not None:
        mission.set_mission(req.northStar, req.why or "")
    elif req.why is not None:
        mission.update(why=req.why)
    if req.focus is not None:
        mission.set_focus(req.focus)
    if req.todayWin is not None:
        mission.set_today_win(req.todayWin)
    return await run_in_threadpool(mission.status)


class InitiativeUpdate(BaseModel):
    mode: str


@app.post("/api/dexter/initiative")
async def set_initiative_mode(req: InitiativeUpdate):
    """The switch: 'dexter' takes initiative, 'me' waits to be asked."""
    mission.set_initiative(req.mode)
    return await run_in_threadpool(mission.status)


@app.post("/api/dexter/companion/checkin")
async def companion_checkin(kind: str = "midday"):
    """Fire a check-in now (the UI's 'check in' and for trying the voice)."""
    fn = {"morning": companion.morning, "midday": companion.midday, "evening": companion.evening}.get(kind, companion.midday)
    return {"sent": await run_in_threadpool(fn, True), "kind": kind}


@app.get("/api/dexter/code/jobs")
async def code_jobs():
    from code_agent import code_agent

    return {"jobs": code_agent.recent(), "engine": await run_in_threadpool(code_agent.engine_status)}


@app.get("/api/dexter/notes")
async def get_notes():
    return await run_in_threadpool(notes.list_notes)


@app.post("/api/dexter/notes/add")
async def create_note_endpoint(req: CreateNoteRequest):
    return await run_in_threadpool(notes.create_note, req.content, req.title)


@app.post("/api/dexter/notes/delete/{note_id}")
async def delete_note_endpoint(note_id: str):
    return {"success": await run_in_threadpool(notes.delete_note, note_id)}


@app.get("/api/dexter/playlists")
async def get_playlists():
    return youtube_tool.get_playlists()


# ───────────────────────── BuzzcafAI ─────────────────────────


def _on_studio_event(event_type: str, payload: Any) -> None:
    """A Studio SSE frame → Dexter's own bus (UI refresh) and, when it matters, a word in the chat."""
    data = payload if isinstance(payload, dict) else {}
    bus.publish("studio", {"type": event_type, "payload": data})
    if event_type in ("approval_needed", "step_failed"):
        try:
            companion.on_event(event_type, data)
        except Exception as exc:
            print(f"[Studio events] companion message failed: {exc}")
        try:
            import notify

            step = data.get("step") or "a step"
            text = (f"{step} on '{data.get('name') or data.get('project_id')}' needs your approval"
                    if event_type == "approval_needed" else f"A Studio step failed on '{data.get('project_id')}'")
            notify.toast_async("Buzzcaf Studio", text, urgent=(event_type == "step_failed"))
        except Exception as exc:
            print(f"[Studio events] toast failed: {exc}")


@app.get("/api/dexter/buzzcaf/status")
async def get_buzzcaf_status():
    return await run_in_threadpool(buzzcaf_client.get_status)


@app.get("/api/dexter/buzzcaf/health")
async def get_buzzcaf_health():
    return {"online": await run_in_threadpool(buzzcaf_client.health_check)}


@app.post("/api/dexter/buzzcaf/start")
async def start_buzzcaf_studio():
    return await run_in_threadpool(buzzcaf_client.start_studio, True, 20)


@app.post("/api/dexter/buzzcaf/start/backend")
async def start_buzzcaf_backend():
    return await run_in_threadpool(buzzcaf_client.start_backend)


@app.post("/api/dexter/buzzcaf/start/frontend")
async def start_buzzcaf_frontend():
    return await run_in_threadpool(buzzcaf_client.start_frontend)


@app.get("/api/dexter/buzzcaf/brands")
async def get_buzzcaf_brands():
    return await run_in_threadpool(buzzcaf_client.list_brands)


@app.get("/api/dexter/buzzcaf/workflows")
async def get_buzzcaf_workflows():
    return await run_in_threadpool(buzzcaf_client.list_workflows)


@app.get("/api/dexter/buzzcaf/projects")
async def list_buzzcaf_projects():
    return await run_in_threadpool(buzzcaf_client.list_projects)


@app.post("/api/dexter/buzzcaf/projects")
async def create_buzzcaf_project(req: BuzzcafProjectRequest):
    return await run_in_threadpool(
        buzzcaf_client.create_project, req.name, req.brand, req.workflow_name
    )


@app.get("/api/dexter/buzzcaf/projects/{project_id}")
async def get_buzzcaf_project(project_id: str):
    project = await run_in_threadpool(buzzcaf_client.get_project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.post("/api/dexter/buzzcaf/projects/{project_id}/execute")
async def execute_buzzcaf_step(project_id: str, req: BuzzcafFeedbackRequest = None):
    feedback = req.feedback if req else None
    return await run_in_threadpool(buzzcaf_client.execute_step, project_id, feedback)


@app.get("/api/dexter/buzzcaf/projects/{project_id}/logs")
async def get_buzzcaf_logs(project_id: str):
    return await run_in_threadpool(buzzcaf_client.get_project_logs, project_id)


@app.get("/api/dexter/buzzcaf/projects/{project_id}/asset/{asset_name}")
async def get_buzzcaf_asset(project_id: str, asset_name: str):
    return await run_in_threadpool(buzzcaf_client.get_project_asset, asset_name, project_id)


@app.post("/api/dexter/buzzcaf/active/{project_id}")
async def set_active_buzzcaf_project(project_id: str):
    buzzcaf_client.set_active_project(project_id)
    return {"success": True, "active_project": project_id}


@app.get("/api/dexter/buzzcaf/active")
async def get_active_buzzcaf_project():
    project_id = buzzcaf_client.get_active_project()
    if project_id:
        return await run_in_threadpool(buzzcaf_client.get_project, project_id)
    return {"active_project": None}


@app.get("/api/dexter/buzzcaf/assets")
async def query_buzzcaf_assets(
    type: Optional[str] = None, tag: Optional[str] = None, query: Optional[str] = None
):
    return await run_in_threadpool(buzzcaf_client.query_assets, type, tag, query)


@app.get("/api/dexter/buzzcaf/diagnostics")
async def get_buzzcaf_diagnostics():
    return await run_in_threadpool(buzzcaf_client.get_diagnostics)


@app.get("/api/dexter/buzzcaf/state")
async def get_buzzcaf_state():
    if not await run_in_threadpool(buzzcaf_client.health_check):
        return {"online": False}
    state = await run_in_threadpool(buzzcaf_client.state)
    return {"online": not state.get("error"), **state}


@app.post("/api/dexter/buzzcaf/projects/{project_id}/approve")
async def approve_buzzcaf_step(project_id: str, req: BuzzcafFeedbackRequest = None):
    feedback = req.feedback if req else None
    result = await run_in_threadpool(buzzcaf_client.approve, project_id, feedback)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=int(result.get("status_code") or 502), detail=result["error"])
    return result


# ───────────────────────── resources and apps (v9) ─────────────────────────


@app.get("/api/dexter/resources")
async def get_resources():
    """VRAM, RAM, disk, who holds what, and whether the guardian is there.

    One call for the Resource panel: the three gauges plus the holders and the
    frozen list. Every field degrades rather than disappearing when Sentinel is
    absent or is the old v1 build, because a blank gauge reads as "Dexter is
    broken" rather than "the guardian is old".
    """
    snapshot = await run_in_threadpool(model_steward.snapshot)
    if isinstance(snapshot, dict):
        snapshot["port"] = SERVER_PORT
    return snapshot


@app.get("/api/dexter/apps")
async def get_apps():
    """Every project in the registry with its live status."""
    rows = await run_in_threadpool(apps_registry.overview)
    return {"apps": rows, "root": apps_registry.load().get("root")}


@app.post("/api/dexter/apps/{app_id}/start")
async def start_app_endpoint(app_id: str):
    record = apps_registry.get(app_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"No app '{app_id}' in the registry")
    result = await run_in_threadpool(execute, "open_app", {"app": app_id})
    return result.to_dict()


@app.post("/api/dexter/apps/{app_id}/stop")
async def stop_app_endpoint(app_id: str, confirm: bool = False):
    """Stop an app. A batch app mid-job needs `?confirm=true` — the same guard
    the chat asks for out loud, so the panel cannot be the easy way round it."""
    record = apps_registry.get(app_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"No app '{app_id}' in the registry")
    busy = await run_in_threadpool(apps_registry.mid_job, record)
    if busy and not confirm:
        return {
            "ok": False,
            "summary": f"{record.get('label')} looks busy — {busy}. Confirm to stop it anyway.",
            "data": {"needsConfirmation": True, "busy": busy, "id": app_id},
        }
    result = await run_in_threadpool(execute, "stop_app", {"app": app_id})
    return result.to_dict()


class NudgeRequest(BaseModel):
    text: str
    kind: Optional[str] = "nudge"
    source: Optional[str] = "external"
    quickReplies: Optional[List[str]] = None


@app.post("/api/dexter/nudge")
async def push_nudge(req: NudgeRequest):
    """A nudge from another local app — Mission Reminder, chiefly (MR1).

    It lands as a Dexter turn in the chat and the pop-up instead of becoming a
    second toast competing with Dexter's own. Loopback and the session token
    already gate this (the middleware runs first); nothing else is trusted
    about the caller, so the text is treated as text.
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to say")
    said = await run_in_threadpool(
        companion.say, f"nudge:{req.source or 'external'}", text[:500],
        req.quickReplies or [], False, True,
    )
    return {"ok": True, "delivered": bool(said), "source": req.source}


# ───────────────────────── static frontend ─────────────────────────

dist_dir = os.path.join(ROOT_DIR, "dist")
if os.path.exists(dist_dir):
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    # Loopback by default. Endpoints require the per-run session token and file
    # paths pass through pathguard, but there is still no TLS or per-user auth -
    # keep it off untrusted networks.
    # The port was chosen at import time (`_bind_port`), so everything that
    # names it - CORS, /health, the ledger entry - already agrees.
    print(f"Starting Dexter Engine on http://{HOST}:{SERVER_PORT}")
    # A line a launcher can read without guessing (GUARDIAN_PLAN §11, rule 6).
    print(f"READY port={SERVER_PORT}", flush=True)
    publish_when_ready(SERVER_PORT)
    try:
        uvicorn.run(app, host=HOST, port=SERVER_PORT)
    finally:
        withdraw_port()
