import os
import json
import time
import subprocess
import webbrowser
import urllib.parse
from typing import List, Dict, Any, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PLANNER_FILE = os.path.join(DATA_DIR, "day_planner.json")
PLAYLIST_FILE = os.path.join(DATA_DIR, "playlists.json")

class DayPlannerTool:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(PLANNER_FILE):
            self._save_planner({
                "date": time.strftime("%Y-%m-%d"),
                "tasks": [
                    {"id": "t1", "time": "09:00 AM", "title": "Morning Routine & Coffee", "completed": True},
                    {"id": "t2", "time": "10:30 AM", "title": "Review SpilledCoffeeAI overnight videos", "completed": False},
                    {"id": "t3", "time": "02:00 PM", "title": "Content Strategy & Scripting Session", "completed": False},
                    {"id": "t4", "time": "06:00 PM", "title": "Evening Walk & Music Playlist", "completed": False}
                ]
            })

    def _load_planner(self) -> Dict[str, Any]:
        try:
            with open(PLANNER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"date": time.strftime("%Y-%m-%d"), "tasks": []}

    def _save_planner(self, data: Dict[str, Any]):
        with open(PLANNER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_agenda(self) -> Dict[str, Any]:
        return self._load_planner()

    def add_task(self, title: str, task_time: str = "12:00 PM") -> Dict[str, Any]:
        planner = self._load_planner()
        new_task = {
            "id": f"t-{int(time.time())}",
            "time": task_time,
            "title": title,
            "completed": False
        }
        planner["tasks"].append(new_task)
        self._save_planner(planner)
        return new_task

    def toggle_task(self, task_id: str) -> bool:
        planner = self._load_planner()
        for t in planner.get("tasks", []):
            if t["id"] == task_id:
                t["completed"] = not t.get("completed", False)
                self._save_planner(planner)
                return True
        return False


class YouTubeMusicTool:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(PLAYLIST_FILE):
            self._save_playlists([
                {
                    "name": "Chill Coding & Focus",
                    "songs": [
                        {"title": "Lofi Hip Hop Radio 24/7", "url": "https://www.youtube.com/watch?v=jfKfPfyJRdk"},
                        {"title": "Deep Focus Ambient Tech", "url": "https://www.youtube.com/results?search_query=deep+focus+ambient"}
                    ]
                }
            ])

    def _load_playlists(self) -> List[Dict[str, Any]]:
        try:
            with open(PLAYLIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_playlists(self, data: List[Dict[str, Any]]):
        with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def search_and_play(self, query: str) -> Dict[str, Any]:
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.youtube.com/results?search_query={encoded_query}"
        
        # Open in default web browser
        try:
            webbrowser.open(search_url)
        except Exception:
            pass

        return {
            "query": query,
            "url": search_url,
            "status": f"Searching and playing '{query}' on YouTube"
        }

    def get_playlists(self) -> List[Dict[str, Any]]:
        return self._load_playlists()


class AppLauncherTool:
    APP_COMMANDS = {
        "chrome": "chrome.exe",
        "google chrome": "chrome.exe",
        "notepad": "notepad.exe",
        "calc": "calc.exe",
        "calculator": "calc.exe",
        "vscode": "code",
        "vs code": "code",
        "code": "code",
        "spotify": "spotify.exe",
        "cmd": "cmd.exe",
        "powershell": "powershell.exe"
    }

    @classmethod
    def launch_app(cls, app_name: str) -> Dict[str, Any]:
        clean_name = app_name.lower().strip()
        cmd_to_run = cls.APP_COMMANDS.get(clean_name, clean_name)
        
        try:
            subprocess.Popen(cmd_to_run, shell=True)
            return {
                "success": True,
                "appName": app_name,
                "command": cmd_to_run,
                "message": f"Successfully launched {app_name}"
            }
        except Exception as e:
            return {
                "success": False,
                "appName": app_name,
                "error": str(e),
                "message": f"Failed to launch {app_name}: {str(e)}"
            }


class PlaywrightBrowserTool:
    @classmethod
    def browse_url(cls, url: str) -> Dict[str, Any]:
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
            
        try:
            webbrowser.open(url)
            return {
                "success": True,
                "url": url,
                "message": f"Opened {url} in browser"
            }
        except Exception as e:
            return {
                "success": False,
                "url": url,
                "error": str(e)
            }
