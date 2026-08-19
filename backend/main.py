"""
Dexter AI Super Assistant - HTTP engine.

The chat endpoint used to be a 310-line keyword waterfall in which the language
model was the last branch and could not call a single tool. Intent handling now
lives in router.py (three tiers) and capabilities live in the tool registry, so
this module is only transport: endpoints, lifecycle, and streaming.
"""

import asyncio
import os
import queue as sync_queue
import re
import secrets
import sys
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

# Ensure backend directory is importable when launched from anywhere.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv

    load_dotenv(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    )
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import app_index
import google_tools
import media_tools
import router as intent_router
import tools_assistant  # noqa: F401  - registers reminder/memory tools
import windows_tools
from buzzcaf_client import buzzcaf_client
from dexter_tools import AppLauncherTool, PlaywrightBrowserTool
from events import bus
from filesystem_tool import FileSystemTool
import macros
import tasks
import tools_tasks  # noqa: F401  - registers background-task and macro tools
from hybrid_llm import llm_router
from memory import memory
from scheduler import scheduler
from tool_registry import all_tools
from tools_core import day_planner, spilled_coffee, sticky_notes, youtube_tool
from voice_engine import DexterVoiceEngine

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(ROOT_DIR, "data", "audio")

voice_engine = DexterVoiceEngine()

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
    await run_in_threadpool(intent_router.build_intent_index)

    # Warm the health caches off the request path. Otherwise the very first
    # /status pays for every provider probe at once (~6s) while the UI waits.
    def _warm_health() -> None:
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

    threading.Thread(target=_warm_health, name="dexter-health-warm", daemon=True).start()

    scheduler.start()
    armed = scheduler.rearm_planner()
    if armed:
        print(f"[Startup] Armed {armed} planner reminder(s).")
    armed_goals = tasks.rearm_scheduled()
    if armed_goals:
        print(f"[Startup] Re-armed {armed_goals} scheduled goal(s).")

    print(f"[Startup] Dexter ready with {len(all_tools())} tools.")
    yield
    scheduler.shutdown()


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
except Exception as _exc:
    print(f"[Auth] Could not persist session token: {_exc}")

# Reachable without the token: the health probe, the token bootstrap itself,
# and generated TTS clips - those are played through Audio()/EventSource-style
# APIs that cannot attach headers, and contain only Dexter's own speech.
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
    voiceEnabled: Optional[bool] = True
    voiceKey: Optional[str] = "michael"
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


class CurateRequest(BaseModel):
    niche: Optional[str] = "AI Tech & Viral Trends"
    count: Optional[int] = 3


class TTSRequest(BaseModel):
    text: str
    voiceKey: Optional[str] = "michael"
    emotion: Optional[str] = None
    isReminder: Optional[bool] = False


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
    planner = await run_in_threadpool(day_planner.get_agenda)
    skills = await run_in_threadpool(macros.list_skills)
    overnight = await run_in_threadpool(spilled_coffee.get_overnight_content)
    notes = await run_in_threadpool(sticky_notes.list_notes)
    buzzcaf_status = await run_in_threadpool(buzzcaf_client.get_status)

    return {
        "status": "online",
        "assistantName": "Dexter Super Assistant",
        "version": "2.0.0",
        "providers": providers,
        "toolCount": len(all_tools()),
        "intentMode": intent_router._embedder.mode,
        "appsIndexed": app_index.size(),
        "skillsCount": len(skills),
        "tasksCount": len(planner.get("tasks", [])),
        "overnightQueueCount": len(overnight),
        "notesCount": len(notes),
        "scheduler": scheduler.status(),
        "memory": memory.stats(),
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

    history = memory.recent_turns(session=session)

    context_parts = []
    # Telling the agent what is on screen is what lets "summarise this" or
    # "close it" resolve to something concrete.
    active = windows_tools.get_active_window_title()
    if active:
        context_parts.append(f"The user currently has this window in focus: {active}")
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
    Chat with incremental delivery.

    LLM tokens stream to the UI as they are generated (`token` events), and
    completed sentences are synthesised while later ones are still being
    written - so speech starts before the model has finished the reply, not
    after. Fast-path replies (no LLM, so no tokens) fall back to synthesising
    the finished reply sentence by sentence as before.
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event_type: str, payload: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event_type, payload))

    # Synthesis runs on its own thread so a 300ms Kokoro call never stalls the
    # token stream. Sentences go in; audio events come out.
    tts_queue: "sync_queue.Queue[Optional[str]]" = sync_queue.Queue()
    state = {"pending": "", "spoke_stream": False, "emotion": None}

    def tts_worker() -> None:
        while True:
            sentence = tts_queue.get()
            if sentence is None:
                return
            try:
                delivery, chunks = voice_engine.plan_speech(
                    sentence, "", None, False, False, state["emotion"]
                )
                if state["emotion"] is None:
                    # Lock the register decided for the first sentence so the
                    # reply does not change mood halfway through.
                    state["emotion"] = delivery.emotion
                    emit("delivery", {"emotion": delivery.emotion, "speed": delivery.speed})
                for chunk in chunks:
                    path = voice_engine.synthesize_chunk(chunk, delivery, req.voiceKey or "michael")
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
        if event_type == "token" and req.voiceEnabled:
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
        tts_thread = threading.Thread(target=tts_worker, daemon=True, name="dexter-stream-tts")
        tts_thread.start()
        try:
            result = await run_in_threadpool(_run_route, req, routed_emit)
            emit("reply", result)

            if req.voiceEnabled:
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
                        requested = next(
                            (c.get("speechEmotion") for c in calls if c.get("speechEmotion")), None
                        )

                    delivery, chunks = await run_in_threadpool(
                        voice_engine.plan_speech,
                        reply_text,
                        result.get("action", ""),
                        tool_ok,
                        False,
                        len(reply_text) > 600,
                        requested,
                    )
                    emit("delivery", {"emotion": delivery.emotion, "speed": delivery.speed})

                    for chunk in chunks:
                        path = await run_in_threadpool(
                            voice_engine.synthesize_chunk, chunk, delivery, req.voiceKey or "michael"
                        )
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
    return await run_in_threadpool(day_planner.get_agenda)


@app.post("/api/dexter/planner/add")
async def add_planner_task(req: AddTaskRequest):
    task = await run_in_threadpool(day_planner.add_task, req.title, req.time or "12:00 PM")
    from timeparse import parse_when

    parsed = parse_when(req.time or "")
    if parsed:
        task = day_planner.update_task(task["id"], {"at": parsed.isoformat(timespec="seconds")}) or task
        scheduler.sync_planner_task(task)
    return task


@app.post("/api/dexter/planner/toggle/{task_id}")
async def toggle_planner_task(task_id: str):
    success = await run_in_threadpool(day_planner.toggle_task, task_id)
    return {"success": success}


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


# ───────────────────────── voice ─────────────────────────


@app.post("/api/dexter/tts/generate")
async def generate_expressive_tts(req: TTSRequest):
    # Reminders arrive here from the SSE handler and should sound urgent, so
    # this endpoint runs the same prosody pipeline as streaming chat rather
    # than the old flat synthesis.
    paths, _delivery = await run_in_threadpool(
        voice_engine.speak_expressive,
        req.text,
        "",
        None,
        bool(req.isReminder),
        len(req.text or "") > 600,
        req.emotion,
        req.voiceKey or "michael",
    )
    file_path = paths[0] if paths else None
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=503, detail="Speech synthesis unavailable")
    # Kokoro emits WAV and edge-tts emits MP3; the old handler hardcoded mpeg
    # for both, which made some players reject the Kokoro output.
    media_type = "audio/wav" if file_path.lower().endswith(".wav") else "audio/mpeg"
    return FileResponse(file_path, media_type=media_type)


@app.get("/api/dexter/audio/{filename}")
async def get_audio(filename: str):
    """Serve a generated clip for the streaming chat endpoint."""
    safe = os.path.basename(filename)
    path = os.path.join(AUDIO_DIR, safe)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Audio not found")
    media_type = "audio/wav" if safe.lower().endswith(".wav") else "audio/mpeg"
    return FileResponse(path, media_type=media_type)


# ───────────────────────── notes / media / content ─────────────────────────


@app.get("/api/dexter/notes")
async def get_sticky_notes():
    return await run_in_threadpool(sticky_notes.list_notes)


@app.post("/api/dexter/notes/add")
async def create_sticky_note(req: CreateNoteRequest):
    return await run_in_threadpool(
        sticky_notes.create_note, req.content, req.title or "Sticky Note"
    )


@app.post("/api/dexter/notes/delete/{note_id}")
async def delete_sticky_note(note_id: str):
    return {"success": await run_in_threadpool(sticky_notes.delete_note, note_id)}


@app.get("/api/dexter/playlists")
async def get_playlists():
    return youtube_tool.get_playlists()


@app.get("/api/dexter/spilledcoffee/overnight-queue")
async def get_spilled_coffee_queue():
    return await run_in_threadpool(spilled_coffee.get_overnight_content)


@app.post("/api/dexter/spilledcoffee/curate")
async def curate_spilled_coffee(req: CurateRequest):
    return await run_in_threadpool(
        spilled_coffee.run_overnight_curation,
        req.niche or "AI Tech & Viral Trends",
        req.count or 3,
    )


# ───────────────────────── BuzzcafAI ─────────────────────────


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
