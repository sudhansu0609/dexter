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

from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from hybrid_llm import HybridLLMRouter
from spilled_coffee_controller import SpilledCoffeeAIController
from skill_engine import DexterSkillEngine
from dexter_tools import DayPlannerTool, YouTubeMusicTool, AppLauncherTool, PlaywrightBrowserTool

app = FastAPI(title="Dexter AI Super Assistant Engine", version="1.1.0")

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

class ChatRequest(BaseModel):
    message: str
    provider: Optional[str] = "auto"
    model: Optional[str] = None
    voiceEnabled: Optional[bool] = True

class AddTaskRequest(BaseModel):
    title: str
    time: Optional[str] = "12:00 PM"

class LaunchAppRequest(BaseModel):
    appName: str

class BrowseRequest(BaseModel):
    url: str

class CurateRequest(BaseModel):
    niche: Optional[str] = "AI Tech & Viral Trends"
    count: Optional[int] = 3

def _speech_url(text: str) -> str:
    """Build a safe speech URL — truncate on word boundary, max 180 chars."""
    clean = text[:180].rsplit(" ", 1)[0] if len(text) > 180 else text
    return f"https://translate.google.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(clean)}&tl=en&client=tw-ob"

# ───────── Intent keywords (ordered specific → broad) ─────────
PLANNER_KEYWORDS = ["plan my day", "agenda", "schedule", "my tasks", "today's plan", "daily plan"]
CURATE_KEYWORDS  = ["curate", "video title", "spilledcoffee", "overnight content", "generate titles"]
YOUTUBE_KEYWORDS = ["play ", "youtube ", "play song", "play music", "play video"]
BROWSE_KEYWORDS  = ["go to ", "browse ", "navigate ", "open website", "open url", "visit "]
LAUNCH_KEYWORDS  = ["open ", "launch ", "start app"]

@app.get("/api/dexter/status")
def get_status():
    providers = llm_router.get_available_providers()
    skills = skill_engine.list_skills()
    planner = day_planner.get_agenda()
    overnight = spilled_coffee.get_overnight_content()
    
    return {
        "status": "online",
        "assistantName": "Dexter Super Assistant",
        "providers": providers,
        "skillsCount": len(skills),
        "tasksCount": len(planner.get("tasks", [])),
        "overnightQueueCount": len(overnight)
    }

@app.post("/api/dexter/chat")
def process_chat(req: ChatRequest):
    msg = req.message.strip().lower()

    # ── 1. Day Planning & Agenda (check FIRST — avoids "start my day" hitting app launcher) ──
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
        song_query = msg
        for strip_word in ["play ", "on youtube", "song ", "music ", "video "]:
            song_query = song_query.replace(strip_word, "")
        song_query = song_query.strip()
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

    # ── 5. App Launching (checked AFTER planner & YouTube to avoid false positives) ──
    if any(k in msg for k in LAUNCH_KEYWORDS):
        app_target = msg
        for strip_word in ["open ", "launch ", "start ", "app"]:
            app_target = app_target.replace(strip_word, "")
        app_target = app_target.strip()
        if app_target:
            launch_res = AppLauncherTool.launch_app(app_target)
            reply = f"I have launched {launch_res.get('appName', app_target)} for you on your desktop!"
            return {"reply": reply, "action": "launch_app", "details": launch_res, "speechUrl": _speech_url(reply)}

    # ── 6. Check learned skills for a matching trigger ──
    matched_skill = skill_engine.match_skill(msg)
    if matched_skill:
        skill_reply = f"I found a learned skill '{matched_skill['name']}' that matches your request. Executing steps: {', '.join(matched_skill.get('steps', [])[:3])}"
        return {"reply": skill_reply, "action": "skill_match", "details": matched_skill, "speechUrl": _speech_url(skill_reply)}

    # ── 7. Hybrid LLM Reasoning Engine (fallback) ──
    system_prompt = (
        "You are Dexter, an intelligent, ultra-smart personal AI super assistant. "
        "You assist the user with planning their day, curating viral YouTube video scripts, launching apps, and answering questions. "
        "Speak warmly, with high intelligence and enthusiasm."
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

@app.get("/api/dexter/playlists")
def get_playlists():
    return youtube_tool.get_playlists()
