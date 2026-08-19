import os
import json
import time
import subprocess
import webbrowser
import urllib.parse
from datetime import datetime
from typing import List, Dict, Any, Optional

import requests

import app_index
from jsonstore import load_json, save_json
from timeparse import parse_when

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PLANNER_FILE = os.path.join(DATA_DIR, "day_planner.json")
PLAYLIST_FILE = os.path.join(DATA_DIR, "playlists.json")
NOTES_FILE = os.path.join(DATA_DIR, "sticky_notes.json")

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
        planner = load_json(PLANNER_FILE, {"date": time.strftime("%Y-%m-%d"), "tasks": []})
        return self._migrate(planner)

    def _migrate(self, planner: Dict[str, Any]) -> Dict[str, Any]:
        """
        Backfill the ISO 'at' timestamp on legacy rows.

        Tasks used to store only a display string like "09:00 AM", which nothing
        could ever schedule against. Existing data is preserved - we just derive
        a real datetime alongside it so the scheduler has something to fire on.
        """
        changed = False
        date_str = planner.get("date") or time.strftime("%Y-%m-%d")
        for task in planner.get("tasks", []):
            if task.get("at"):
                continue
            display = task.get("time", "")
            parsed = None
            try:
                base = datetime.strptime(date_str, "%Y-%m-%d")
                parsed = datetime.combine(
                    base.date(), datetime.strptime(display.strip(), "%I:%M %p").time()
                )
            except Exception:
                parsed = parse_when(display) if display else None
            if parsed:
                task["at"] = parsed.isoformat(timespec="seconds")
                changed = True
        if changed:
            save_json(PLANNER_FILE, planner)
        return planner

    def _save_planner(self, data: Dict[str, Any]):
        save_json(PLANNER_FILE, data)

    def get_agenda(self) -> Dict[str, Any]:
        planner = self._load_planner()
        today = time.strftime("%Y-%m-%d")
        if planner.get("date") != today:
            # Carry over incomplete tasks, dropping their stale 'at' datetimes so
            # _migrate re-derives them against today - otherwise the scheduler
            # sees times days in the past and never fires them again.
            incomplete = []
            for t in planner.get("tasks", []):
                if t.get("completed", False):
                    continue
                t.pop("at", None)
                incomplete.append(t)
            planner = {"date": today, "tasks": incomplete}
            self._save_planner(planner)
            planner = self._migrate(planner)
        return planner

    def add_task(self, title: str, task_time: str = "12:00 PM") -> Dict[str, Any]:
        planner = self._load_planner()
        new_task = {
            "id": f"t-{int(time.time() * 1000)}",
            "time": task_time,
            "title": title,
            "completed": False
        }
        planner.setdefault("tasks", []).append(new_task)
        self._save_planner(planner)
        return new_task

    def update_task(self, task_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Merge fields into an existing task. Used to attach ISO 'at' timestamps."""
        planner = self._load_planner()
        for t in planner.get("tasks", []):
            if t.get("id") == task_id:
                t.update(patch)
                self._save_planner(planner)
                return t
        return None

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        for t in self._load_planner().get("tasks", []):
            if t.get("id") == task_id:
                return t
        return None

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
        return load_json(PLAYLIST_FILE, [])

    def _save_playlists(self, data: List[Dict[str, Any]]):
        save_json(PLAYLIST_FILE, data)

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


class StickyNotesTool:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(NOTES_FILE):
            self._save_notes([
                {"id": "n1", "title": "Welcome Note", "content": "Dexter AI Super Assistant Quick Note", "color": "#fef08a", "created": time.strftime("%Y-%m-%d %H:%M")}
            ])

    def _load_notes(self) -> List[Dict[str, Any]]:
        return load_json(NOTES_FILE, [])

    def _save_notes(self, data: List[Dict[str, Any]]):
        save_json(NOTES_FILE, data)

    def create_note(self, content: str, title: str = "Sticky Note", color: str = "#fef08a") -> Dict[str, Any]:
        notes = self._load_notes()
        note = {
            "id": f"n-{int(time.time())}",
            "title": title,
            "content": content,
            "color": color,
            "created": time.strftime("%Y-%m-%d %H:%M")
        }
        notes.insert(0, note)
        self._save_notes(notes)
        # Open Windows Sticky Notes app
        try:
            subprocess.Popen("start ms-stk:", shell=True)
        except Exception:
            pass
        return note

    def list_notes(self) -> List[Dict[str, Any]]:
        return self._load_notes()

    def delete_note(self, note_id: str) -> bool:
        notes = self._load_notes()
        new_notes = [n for n in notes if n["id"] != note_id]
        if len(new_notes) < len(notes):
            self._save_notes(new_notes)
            return True
        return False


class AppLauncherTool:
    APP_COMMANDS = {
        "edge": "start msedge:",
        "ms edge": "start msedge:",
        "msedge": "start msedge:",
        "microsoft edge": "start msedge:",
        "browser": "start msedge:",
        "calendar": "start outlookcal:",
        "windows calendar": "start outlookcal:",
        "sticky notes": "start ms-stk:",
        "sticky note": "start ms-stk:",
        "notes": "start ms-stk:",
        "chrome": "start chrome",
        "google chrome": "start chrome",
        "notepad": "notepad.exe",
        "calc": "calc.exe",
        "calculator": "calc.exe",
        "vscode": "code",
        "vs code": "code",
        "code": "code",
        "spotify": "spotify.exe",
        "cmd": "cmd.exe",
        "powershell": "powershell.exe",
        "explorer": "explorer.exe",
        "file explorer": "explorer.exe",
        "task manager": "taskmgr.exe",
        "settings": "start ms-settings:"
    }

    @classmethod
    def find_installed_exe(cls, app_name: str) -> Optional[str]:
        """
        Resolve an app name to a path using the prebuilt index.

        This used to os.walk all of Program Files on every miss, which blocked
        the request thread for seconds. app_index does that scan once, in the
        background, and caches it to disk.
        """
        return app_index.lookup(app_name)

    @classmethod
    def verify_process_running(cls, proc_name: str) -> bool:
        """Check if process is active in Windows tasklist."""
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {proc_name}*"],
                text=True, errors="ignore",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return proc_name.lower() in out.lower()
        except Exception:
            return False

    @classmethod
    def launch_app(cls, app_name: str) -> Dict[str, Any]:
        clean_name = app_name.lower().strip()
        for filler in [" on my pc", " on my computer", " for me", " app", " application", " please"]:
            clean_name = clean_name.replace(filler, "")
        clean_name = clean_name.strip()

        # Mapped protocol/shell commands win: they handle UWP apps (ms-stk:,
        # outlookcal:) that have no plain executable on disk.
        cmd_to_run = cls.APP_COMMANDS.get(clean_name)
        found_exe = None if cmd_to_run else cls.find_installed_exe(clean_name)

        attempts: List[str] = []
        if cmd_to_run:
            attempts.append(cmd_to_run)
        if found_exe:
            attempts.append(f'"{found_exe}"')
        attempts.append(f'start "" "{clean_name}"')

        errors: List[str] = []
        for idx, run_cmd in enumerate(attempts, 1):
            try:
                print(f"[Smart Launcher] Attempt {idx}: '{run_cmd}'")
                target = run_cmd.strip('"')
                if target.lower().endswith((".exe", ".lnk")) and os.path.exists(target):
                    os.startfile(target)
                else:
                    subprocess.Popen(
                        run_cmd, shell=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                # No sleep + tasklist poll here: that cost 1.2s on the request
                # thread and the result was discarded anyway. Popen raising is
                # the real failure signal; a silent no-op is rare and cheap.
                return {
                    "success": True,
                    "appName": clean_name or app_name,
                    "command": run_cmd,
                    "resolvedPath": found_exe,
                    "message": f"Opening {clean_name or app_name}.",
                }
            except Exception as e:
                errors.append(f"{run_cmd}: {e}")
                print(f"[Smart Launcher] Attempt {idx} failed: {e}")

        return {
            "success": False,
            "appName": app_name,
            "error": "; ".join(errors) or "Could not execute",
            "message": (
                f"I couldn't find '{app_name}' on this PC. "
                f"It may not be installed, or try the exact program name."
            ),
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


BUZZCAF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "BuzzcafAI")

class BuzzcafAITool:
    """
    Legacy tool for launching BuzzcafAI Studio.
    For full API integration, use buzzcaf_client.py instead.
    """
    BUZZCAF_API = "http://localhost:8095"

    @classmethod
    def launch_buzzcaf_studio(cls) -> Dict[str, Any]:
        """Launch the BuzzcafAI Studio application and web dashboard."""
        bat_file = os.path.join(BUZZCAF_DIR, "start_spilledCoffeeAi.bat")
        launched_backend = False

        if os.path.exists(bat_file):
            try:
                subprocess.Popen(f'cmd /c "{bat_file}"', cwd=BUZZCAF_DIR, shell=True)
                launched_backend = True
            except Exception:
                pass

        # Open the web dashboard
        try:
            webbrowser.open("http://localhost:3005")
        except Exception:
            pass

        return {
            "success": True,
            "launched_backend": launched_backend,
            "dashboard_url": "http://localhost:3005",
            "api_url": cls.BUZZCAF_API,
            "message": "Launched BuzzcafAI 115-Agent Studio Platform!"
        }

    @classmethod
    def check_online(cls) -> bool:
        """Check if BuzzcafAI API is online."""
        try:
            res = requests.get(f"{cls.BUZZCAF_API}/health", timeout=2)
            return res.status_code == 200
        except Exception:
            return False
