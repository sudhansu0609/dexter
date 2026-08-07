import os
import sys
import json
import time
import urllib.parse

# Ensure backend directory is in python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load .env if python-dotenv available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from hybrid_llm import HybridLLMRouter
from spilled_coffee_controller import SpilledCoffeeAIController
from skill_engine import DexterSkillEngine
from dexter_tools import DayPlannerTool, YouTubeMusicTool, AppLauncherTool, PlaywrightBrowserTool, StickyNotesTool
from fastapi.responses import FileResponse
from filesystem_tool import FileSystemTool
from voice_engine import DexterVoiceEngine

app = FastAPI(title="Dexter AI Super Assistant Engine", version="1.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

llm_router = HybridLLMRouter()
spilled_coffee = SpilledCoffeeAIController(llm_router)
skill_engine = DexterSkillEngine(llm_router)
day_planner = DayPlannerTool()
youtube_tool = YouTubeMusicTool()
voice_engine = DexterVoiceEngine()
sticky_notes = StickyNotesTool()

class ChatRequest(BaseModel):
    message: str
    provider: Optional[str] = "auto"
    model: Optional[str] = None
    voiceEnabled: Optional[bool] = True
    voiceKey: Optional[str] = "christopher"

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

class CurateRequest(BaseModel):
    niche: Optional[str] = "AI Tech & Viral Trends"
    count: Optional[int] = 3

class TTSRequest(BaseModel):
    text: str
    voiceKey: Optional[str] = "christopher"

def _speech_url(text: str) -> str:
    """Build a safe speech URL for continuous audio output."""
    clean = text[:200].rsplit(" ", 1)[0] if len(text) > 200 else text
    return f"https://translate.google.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(clean)}&tl=en&client=tw-ob"

# ───────── Intent keywords (ordered specific → broad) ─────────
FOLDER_CREATE_KEYWORDS = ["create folder", "make folder", "new folder", "create directory", "make directory", "new directory"]
STICKY_NOTE_KEYWORDS   = ["sticky note", "add note", "create note", "take a note", "write note", "my notes", "notes"]
CALENDAR_KEYWORDS    = ["calendar", "schedule my day", "add task", "schedule event", "add event", "book time", "schedule"]
PLANNER_KEYWORDS     = ["plan my day", "agenda", "schedule", "my tasks", "today's plan", "daily plan"]
CURATE_KEYWORDS      = ["curate", "video title", "spilledcoffee", "overnight content", "generate titles"]
YOUTUBE_KEYWORDS     = ["play ", "youtube", "search for", "play song", "play music", "play video"]
BROWSE_KEYWORDS      = ["go to ", "browse ", "navigate ", "open website", "open url", "visit "]
FILESYS_KEYWORDS     = ["find file", "search file", "read file", "open folder", "search folder"]
LAUNCH_KEYWORDS      = ["open ", "launch ", "start app"]

@app.get("/api/dexter/status")
def get_status():
    providers = llm_router.get_available_providers()
    skills = skill_engine.list_skills()
    planner = day_planner.get_agenda()
    overnight = spilled_coffee.get_overnight_content()
    notes = sticky_notes.list_notes()
    
    return {
        "status": "online",
        "assistantName": "Dexter Super Assistant",
        "providers": providers,
        "skillsCount": len(skills),
        "tasksCount": len(planner.get("tasks", [])),
        "overnightQueueCount": len(overnight),
        "notesCount": len(notes)
    }

@app.post("/api/dexter/chat")
def process_chat(req: ChatRequest):
    msg = req.message.strip().lower()

    # ── 0. Folder & Directory Creation ──
    if any(k in msg for k in FOLDER_CREATE_KEYWORDS):
        import re
        folder_name = re.sub(r'^(dexter\s+)?(create folder|make folder|new folder|create directory|make directory|new directory)\s*(named|called|for)?\s*', '', msg, flags=re.IGNORECASE).strip()
        if not folder_name:
            folder_name = "New_Dexter_Folder"
        
        folder_res = FileSystemTool.create_folder(folder_name)
        reply = f"Created folder '{os.path.basename(folder_res['folderPath'])}' and opened it in File Explorer!"
        return {"reply": reply, "action": "create_folder", "details": folder_res, "speechUrl": _speech_url(reply)}

    # ── 1. Sticky Notes Integration ──
    if any(k in msg for k in STICKY_NOTE_KEYWORDS):
        import re
        note_content = re.sub(r'^(dexter\s+)?(add note|create note|take a note|write note|sticky note)\s*(to|that|saying)?\s*', '', msg, flags=re.IGNORECASE).strip()
        if not note_content or note_content in ["my notes", "sticky notes", "show notes", "open notes"]:
            notes = sticky_notes.list_notes()
            AppLauncherTool.launch_app("sticky notes")
            reply = f"You have {len(notes)} sticky notes. Opened Windows Sticky Notes for you." if notes else "Opened Windows Sticky Notes."
            return {"reply": reply, "action": "list_notes", "details": notes, "speechUrl": _speech_url(reply)}
        else:
            new_note = sticky_notes.create_note(note_content)
            reply = f"Created sticky note: '{note_content}' and launched Sticky Notes!"
            return {"reply": reply, "action": "create_note", "details": new_note, "speechUrl": _speech_url(reply)}

    # ── 2. Calendar & Day Scheduling ──
    if any(k in msg for k in CALENDAR_KEYWORDS):
        import re
        if "open calendar" in msg or "show calendar" in msg:
            AppLauncherTool.launch_app("calendar")
            reply = "Opened Windows Calendar for you."
            return {"reply": reply, "action": "open_calendar", "speechUrl": _speech_url(reply)}
        
        task_title = re.sub(r'^(dexter\s+)?(schedule|add task|add event|book time|calendar)\s*(to|that|saying)?\s*', '', msg, flags=re.IGNORECASE).strip()
        if task_title:
            new_task = day_planner.add_task(task_title)
            AppLauncherTool.launch_app("calendar")
            reply = f"Scheduled '{task_title}' in your day planner and opened Windows Calendar!"
            return {"reply": reply, "action": "schedule_event", "details": new_task, "speechUrl": _speech_url(reply)}

    # ── 3. Day Planning & Agenda ──
    if any(k in msg for k in PLANNER_KEYWORDS):
        agenda = day_planner.get_agenda()
        task_list = [f"{t['time']} - {t['title']}" for t in agenda.get("tasks", [])]
        reply = "Here is your agenda for today: " + ", ".join(task_list[:4]) + "." if task_list else "Your agenda is empty. Add some tasks!"
        return {"reply": reply, "action": "get_agenda", "details": agenda, "speechUrl": _speech_url(reply)}

    # ── 2. SpilledCoffeeAI Overnight Content Curation ──
    if any(k in msg for k in CURATE_KEYWORDS):
        curate_res = spilled_coffee.run_overnight_curation(count=3)
        reply = f"I have curated {curate_res['count']} viral video titles and script outlines for SpilledCoffeeAI! They are ready in your dashboard."
        return {"reply": reply, "action": "curate_content", "details": curate_res, "speechUrl": _speech_url(reply)}

    # ── 3. YouTube & Music Playback ──
    if any(k in msg for k in YOUTUBE_KEYWORDS):
        import re
        song_query = msg
        # Clean leading action words
        song_query = re.sub(r'^(i asked it to |please |can you |could you |search for |search |play |find |show me |look up |open |watch )+(the )?(channel )?(video )?(song )?', '', song_query, flags=re.IGNORECASE).strip()
        # Clean trailing YouTube / platform phrases
        song_query = re.sub(r'\s+(on youtube|youtube channel|youtube video|youtube|channel|video|song|music|please).*$', '', song_query, flags=re.IGNORECASE).strip()
        
        # Phonetic auto-correction for Hinglish / STT mishearings (e.g. 'beyond teen bajaj' / 'beyond 3 baje' -> 'beyond3baje')
        clean_check = song_query.lower()
        if "beyond" in clean_check and ("teen" in clean_check or "3" in clean_check or "bajaj" in clean_check or "baje" in clean_check):
            song_query = "beyond3baje"

        if song_query:
            yt_res = youtube_tool.search_and_play(song_query)
            reply = f"Searching and playing '{song_query}' on YouTube now!"
            return {"reply": reply, "action": "play_youtube", "details": yt_res, "speechUrl": _speech_url(reply)}

    # ── 4. Web Browsing via Playwright ──
    if any(k in msg for k in BROWSE_KEYWORDS):
        url_target = msg
        for strip_word in ["go to ", "browse ", "navigate ", "open website ", "open url ", "visit "]:
            url_target = url_target.replace(strip_word, "")
        url_target = url_target.strip()
        browse_res = PlaywrightBrowserTool.browse_url(url_target)
        reply = f"Opening {url_target} in your browser now."
        return {"reply": reply, "action": "browse_url", "details": browse_res, "speechUrl": _speech_url(reply)}

    # ── 5. File System Commands ──
    if any(k in msg for k in FILESYS_KEYWORDS):
        file_query = msg
        for strip_word in ["find file ", "search file ", "read file ", "open folder ", "search folder "]:
            file_query = file_query.replace(strip_word, "")
        file_query = file_query.strip()
        fs_res = FileSystemTool.search_files(file_query)
        count = fs_res.get("count", 0)
        reply = f"Found {count} files matching '{file_query}' on your system." if count > 0 else f"No files matching '{file_query}' were found."
        return {"reply": reply, "action": "search_files", "details": fs_res, "speechUrl": _speech_url(reply)}

    # ── 6. App Launching ──
    if any(k in msg for k in LAUNCH_KEYWORDS):
        import re
        # Clean leading action words
        app_target = re.sub(r'^(i asked it to |please |can you |could you )?(open|launch|start|run)\s+(the\s+)?(app\s+)?', '', msg, flags=re.IGNORECASE).strip()
        # Clean trailing filler words
        app_target = re.sub(r'\s+(on my pc|on my computer|for me|app|application|please).*$', '', app_target, flags=re.IGNORECASE).strip()
        
        if app_target:
            launch_res = AppLauncherTool.launch_app(app_target)
            if launch_res.get("success"):
                reply = f"I have launched {launch_res.get('appName', app_target)} for you on your desktop!"
            else:
                reply = f"I tried to launch {app_target}, but ran into an issue: {launch_res.get('error', 'App not found')}."
            return {"reply": reply, "action": "launch_app", "details": launch_res, "speechUrl": _speech_url(reply)}

    # ── 7. Check learned skills for a matching trigger ──
    matched_skill = skill_engine.match_skill(msg)
    if matched_skill:
        skill_reply = f"I found a learned skill '{matched_skill['name']}' that matches your request. Executing steps: {', '.join(matched_skill.get('steps', [])[:3])}"
        return {"reply": skill_reply, "action": "skill_match", "details": matched_skill, "speechUrl": _speech_url(skill_reply)}

    # ── 8. Hybrid LLM Reasoning Engine (fallback) ──
    system_prompt = (
        "You are Dexter, an intelligent, ultra-smart personal AI super assistant. "
        "Be extremely concise, direct, and precise. "
        "Keep answers brief (1 to 2 sentences max) unless detailed information is explicitly requested. "
        "Never use conversational fluff or filler text."
    )
    llm_reply = llm_router.generate(req.message, system_prompt=system_prompt, provider=req.provider, model=req.model)

    # Auto-learn skill only if substantive multi-step task — use a unique name based on timestamp
    if len(msg.split()) > 8:
        skill_name = f"task_{int(time.time())}"
        skill_engine.learn_skill_from_task(skill_name, req.message, [req.message, llm_reply[:80]])

    return {"reply": llm_reply, "action": "chat_response", "speechUrl": _speech_url(llm_reply)}

@app.get("/api/dexter/planner")
def get_planner():
    return day_planner.get_agenda()

@app.post("/api/dexter/planner/add")
def add_planner_task(req: AddTaskRequest):
    return day_planner.add_task(req.title, req.time)

@app.post("/api/dexter/planner/toggle/{task_id}")
def toggle_planner_task(task_id: str):
    success = day_planner.toggle_task(task_id)
    return {"success": success}

@app.get("/api/dexter/spilledcoffee/overnight-queue")
def get_spilled_coffee_queue():
    return spilled_coffee.get_overnight_content()

@app.post("/api/dexter/spilledcoffee/curate")
def curate_spilled_coffee(req: CurateRequest):
    return spilled_coffee.run_overnight_curation(niche_topic=req.niche or "AI Tech & Viral Trends", count=req.count or 3)

@app.get("/api/dexter/skills")
def get_skills():
    return skill_engine.list_skills()

@app.post("/api/dexter/system/launch")
def launch_system_app(req: LaunchAppRequest):
    return AppLauncherTool.launch_app(req.appName)

@app.post("/api/dexter/browser/navigate")
def browse_url(req: BrowseRequest):
    return PlaywrightBrowserTool.browse_url(req.url)

@app.post("/api/dexter/filesystem/search")
def search_files(req: FileSearchRequest):
    return FileSystemTool.search_files(req.query, req.path)

@app.post("/api/dexter/filesystem/read")
def read_file(req: FileReadRequest):
    return FileSystemTool.read_file(req.path)

@app.post("/api/dexter/filesystem/write")
def write_file(req: FileWriteRequest):
    return FileSystemTool.write_file(req.path, req.content)

class CreateFolderRequest(BaseModel):
    path: str

@app.post("/api/dexter/filesystem/create-folder")
def create_folder(req: CreateFolderRequest):
    return FileSystemTool.create_folder(req.path)

@app.post("/api/dexter/filesystem/open")
def open_file_path(req: FileOpenRequest):
    return FileSystemTool.open_local_path(req.path)

@app.post("/api/dexter/tts/generate")
def generate_expressive_tts(req: TTSRequest):
    file_path = voice_engine.generate_speech(req.text, req.voiceKey or "christopher")
    return FileResponse(file_path, media_type="audio/mpeg")

class CreateNoteRequest(BaseModel):
    content: str
    title: Optional[str] = "Sticky Note"

@app.get("/api/dexter/notes")
def get_sticky_notes():
    return sticky_notes.list_notes()

@app.post("/api/dexter/notes/add")
def create_sticky_note(req: CreateNoteRequest):
    return sticky_notes.create_note(req.content, req.title or "Sticky Note")

@app.post("/api/dexter/notes/delete/{note_id}")
def delete_sticky_note(note_id: str):
    return {"success": sticky_notes.delete_note(note_id)}

@app.get("/api/dexter/playlists")
def get_playlists():
    return youtube_tool.get_playlists()

dist_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist")
if os.path.exists(dist_dir):
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    print("Starting Dexter Engine on port 8098...")
    uvicorn.run(app, host="0.0.0.0", port=8098)
