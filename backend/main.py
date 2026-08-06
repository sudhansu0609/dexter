import os
import sys
import json
import urllib.parse

# Ensure backend directory is in python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from hybrid_llm import HybridLLMRouter
from spilled_coffee_controller import SpilledCoffeeAIController
from skill_engine import DexterSkillEngine
from dexter_tools import DayPlannerTool, YouTubeMusicTool, AppLauncherTool, PlaywrightBrowserTool

app = FastAPI(title="Dexter AI Super Assistant Engine", version="1.0.0")

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

class CurateRequest(BaseModel):
    niche: Optional[str] = "AI Tech & Viral Trends"
    count: Optional[int] = 3

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
    
    # 1. Intent Detection for App Launching
    if any(k in msg for k in ["open ", "launch ", "start "]):
        app_target = msg.replace("open ", "").replace("launch ", "").replace("start ", "").replace("app", "").strip()
        launch_res = AppLauncherTool.launch_app(app_target)
        reply = f"I have launched {launch_res.get('appName', app_target)} for you on your desktop!"
        return {
            "reply": reply,
            "action": "launch_app",
            "details": launch_res,
            "speechUrl": f"https://translate.google.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(reply[:100])}&tl=en&client=tw-ob"
        }

    # 2. Intent Detection for YouTube & Music Playback
    if any(k in msg for k in ["play ", "youtube", "music", "song"]):
        song_query = msg.replace("play ", "").replace("on youtube", "").replace("song", "").strip()
        yt_res = youtube_tool.search_and_play(song_query)
        reply = f"Searching and playing '{song_query}' on YouTube now!"
        return {
            "reply": reply,
            "action": "play_youtube",
            "details": yt_res,
            "speechUrl": f"https://translate.google.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(reply[:100])}&tl=en&client=tw-ob"
        }

    # 3. Intent Detection for SpilledCoffeeAI Overnight Video Curation
    if any(k in msg for k in ["curate", "video title", "spilledcoffee", "overnight content"]):
        curate_res = spilled_coffee.run_overnight_curation(count=3)
        reply = f"I have curated {curate_res['count']} viral video titles and script outlines for SpilledCoffeeAI! They are ready in your dashboard."
        return {
            "reply": reply,
            "action": "curate_content",
            "details": curate_res,
            "speechUrl": f"https://translate.google.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(reply[:100])}&tl=en&client=tw-ob"
        }

    # 4. Intent Detection for Day Planning & Agenda
    if any(k in msg for k in ["plan my day", "agenda", "schedule", "my tasks"]):
        agenda = day_planner.get_agenda()
        task_list = [f"{t['time']} - {t['title']}" for t in agenda.get("tasks", [])]
        reply = "Here is your agenda for today: " + ", ".join(task_list[:3]) + "."
        return {
            "reply": reply,
            "action": "get_agenda",
            "details": agenda,
            "speechUrl": f"https://translate.google.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(reply[:100])}&tl=en&client=tw-ob"
        }

    # 5. Hybrid LLM Reasoning Engine
    system_prompt = (
        "You are Dexter, an intelligent, ultra-smart personal AI super assistant. "
        "You assist the user with planning their day, curating viral YouTube video scripts, launching apps, and answering questions. "
        "Speak warmly, with high intelligence and enthusiasm."
    )
    llm_reply = llm_router.generate(req.message, system_prompt=system_prompt, provider=req.provider, model=req.model)
    
    # Auto-learn skill if multi-step task
    if len(msg.split()) > 6:
        skill_engine.learn_skill_from_task("User Command Reasoning", req.message, [req.message, llm_reply[:80]])

    return {
        "reply": llm_reply,
        "action": "chat_response",
        "speechUrl": f"https://translate.google.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(llm_reply[:100])}&tl=en&client=tw-ob"
    }

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

@app.get("/api/dexter/playlists")
def get_playlists():
    return youtube_tool.get_playlists()
