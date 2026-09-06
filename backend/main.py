"""
Dexter AI Super Assistant - HTTP engine.

The chat endpoint used to be a 310-line keyword waterfall in which the language
model was the last branch and could not call a single tool. Intent handling now
lives in router.py (three tiers) and capabilities live in the tool registry, so
this module is only transport: endpoints, lifecycle, and streaming.
"""

import asyncio
import json
import os
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

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import app_index
import cancellation
import google_tools
import media_tools
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
import tools_browser  # noqa: F401  - registers Dexter's browser tools (v6)
import tools_code  # noqa: F401  - registers buzzcode + local engine tools (v6)
import tools_mission  # noqa: F401  - registers the mission tools (v7)
import tools_activity  # noqa: F401  - registers what-am-I-doing (v7)
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

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

    restored = settings_store.load_at_startup()
    if restored:
        print(f"[Startup] Applied {restored} saved setting(s).")

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
    scheduler.shutdown()
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

# Reachable without the token: the health probe and the token bootstrap itself.
_TOKEN_EXEMPT = ("/api/dexter/health", "/api/dexter/auth/token")
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
        "http://localhost:3006",
        "http://127.0.0.1:3006",
        "http://localhost:8098",
        "http://127.0.0.1:8098",
        "null",
        "file://",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/dexter/health")
async def health():
    return {"ok": True}


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

    return {
        "status": "online",
        "assistantName": "Dexter Super Assistant",
        "version": "2.0.0",
        "providers": providers,
        "preferredProvider": llm_router.default_provider,
        "runtime": runtime,
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
        "buzzcafAI": {
            "online": buzzcaf_status.get("online", False),
            "activeProjects": buzzcaf_status.get("in_progress", 0),
            "totalProjects": buzzcaf_status.get("total_projects", 0),
        },
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


@app.post("/api/dexter/chat/stream")
async def process_chat_stream(req: ChatRequest):
    """
    Chat with incremental delivery: LLM tokens stream to the UI as they are
    generated (`token` events, plus `tool_start` / `provider_switched`), then
    the authoritative `reply`, then `done`. Fast-path replies skip straight to
    `reply` because no model ran.
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event_type: str, payload: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event_type, payload))

    session_id = req.session or "default"

    async def worker() -> None:
        cancellation.begin(session_id)
        try:
            result = await run_in_threadpool(_run_route, req, emit)
            if result.get("action") == "interrupted" or cancellation.is_cancelled(session_id):
                emit("interrupted", {})
                return
            emit("reply", result)
        except Exception as exc:
            emit("error", {"message": str(exc)})
        finally:
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
    return await run_in_threadpool(settings_store.update, req.changes)


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


# ───────────────────────── static frontend ─────────────────────────

dist_dir = os.path.join(ROOT_DIR, "dist")
if os.path.exists(dist_dir):
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    # Loopback by default. Endpoints require the per-run session token and file
    # paths pass through pathguard, but there is still no TLS or per-user auth -
    # keep it off untrusted networks.
    host = os.getenv("DEXTER_HOST", "127.0.0.1")
    print(f"Starting Dexter Engine on http://{host}:8098")
    uvicorn.run(app, host=host, port=8098)
