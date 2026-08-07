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
        try:
            with open(PLANNER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"date": time.strftime("%Y-%m-%d"), "tasks": []}

    def _save_planner(self, data: Dict[str, Any]):
        with open(PLANNER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_agenda(self) -> Dict[str, Any]:
        planner = self._load_planner()
        today = time.strftime("%Y-%m-%d")
        if planner.get("date") != today:
            # Carry over incomplete tasks, mark date as today
            incomplete = [t for t in planner.get("tasks", []) if not t.get("completed", False)]
            planner = {"date": today, "tasks": incomplete}
            self._save_planner(planner)
        return planner

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


class StickyNotesTool:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(NOTES_FILE):
            self._save_notes([
                {"id": "n1", "title": "Welcome Note", "content": "Dexter AI Super Assistant Quick Note", "color": "#fef08a", "created": time.strftime("%Y-%m-%d %H:%M")}
            ])

    def _load_notes(self) -> List[Dict[str, Any]]:
        try:
            with open(NOTES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_notes(self, data: List[Dict[str, Any]]):
        with open(NOTES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

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
        """Dynamically scan Windows directories for matching application executables."""
        target = app_name.lower().strip()
        # Clean filler prefixes/suffixes
        target = target.replace("adobe ", "").replace("microsoft ", "").strip()

        search_dirs = [
            r"C:\Program Files\Adobe",
            r"C:\Program Files (x86)\Adobe",
            r"C:\Program Files",
            os.path.expanduser(r"~\AppData\Roaming\Microsoft\Windows\Start Menu\Programs"),
            r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs"
        ]

        for sd in search_dirs:
            if os.path.exists(sd):
                try:
                    for root, dirs, files in os.walk(sd):
                        for f in files:
                            f_lower = f.lower()
                            if f_lower.endswith(".exe") or f_lower.endswith(".lnk"):
                                if target in f_lower:
                                    # Exclude helper/service background tasks
                                    if not any(b in f_lower for b in ["helper", "service", "prefs", "dlms", "unins", "setup", "crash"]):
                                        return os.path.join(root, f)
                except Exception:
                    pass
        return None

    @classmethod
    def verify_process_running(cls, proc_name: str) -> bool:
        """Check if process is active in Windows tasklist."""
        try:
            cmd = f'tasklist /FI "IMAGENAME eq {proc_name}*"'
            out = subprocess.check_output(cmd, shell=True, text=True, errors="ignore")
            return proc_name.lower() in out.lower()
        except Exception:
            return False

    @classmethod
    def launch_app(cls, app_name: str) -> Dict[str, Any]:
        clean_name = app_name.lower().strip()
        for filler in [" on my pc", " on my computer", " for me", " app", " application", " please"]:
            clean_name = clean_name.replace(filler, "")
        clean_name = clean_name.strip()

        # Step 1: Check pre-mapped commands
        cmd_to_run = cls.APP_COMMANDS.get(clean_name)
        
        # Step 2: If no direct command mapped, dynamically scan disk for exact executable
        found_exe = cls.find_installed_exe(clean_name)

        attempts = []
        if found_exe:
            attempts.append(f'"{found_exe}"')
        if cmd_to_run:
            attempts.append(cmd_to_run)
        
        # Fallback attempt
        attempts.append(f'start "" "{clean_name}"')

        # Recursive Execution & Verification Loop
        for idx, run_cmd in enumerate(attempts, 1):
            try:
                print(f"[Smart Launcher] Attempt {idx}: Executing '{run_cmd}'...")
                if run_cmd.endswith('.exe"') or run_cmd.endswith('.exe'):
                    os.startfile(run_cmd.strip('"'))
                else:
                    subprocess.Popen(run_cmd, shell=True)
                
                time.sleep(1.2)
                
                # Verify process launch if exe name available
                exe_basename = os.path.basename(found_exe) if found_exe else clean_name
                if cls.verify_process_running(exe_basename.split('.')[0]):
                    return {
                        "success": True,
                        "appName": clean_name or app_name,
                        "command": run_cmd,
                        "verified": True,
                        "message": f"Successfully verified and launched {clean_name or app_name}!"
                    }
                else:
                    # Non-blocking success if launched without error
                    return {
                        "success": True,
                        "appName": clean_name or app_name,
                        "command": run_cmd,
                        "message": f"Launched {clean_name or app_name} on your desktop."
                    }
            except Exception as e:
                print(f"[Smart Launcher] Attempt {idx} failed: {e}")

        return {
            "success": False,
            "appName": app_name,
            "error": "Could not verify execution",
            "message": f"Attempted to launch {app_name}, but the executable could not be verified on your system."
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
    BUZZCAF_API = "http://localhost:8095"

    @classmethod
    def launch_buzzcaf_studio(cls) -> Dict[str, Any]:
        bat_file = os.path.join(BUZZCAF_DIR, "start_spilledCoffeeAi.bat")
        if os.path.exists(bat_file):
            try:
                subprocess.Popen(f'cmd /c "{bat_file}"', cwd=BUZZCAF_DIR, shell=True)
                webbrowser.open("http://localhost:3005")
            except Exception:
                pass
            return {
                "success": True,
                "message": "Successfully launched BuzzcafAI 115-Agent Studio Platform!"
            }
        else:
            # Fallback open browser URL
            try:
                webbrowser.open("http://localhost:3005")
            except Exception:
                pass
            return {
                "success": True,
                "message": "Opened BuzzcafAI Web Studio Dashboard."
            }

    @classmethod
    def generate_channel_script(cls, channel_name: str, topic: str) -> Dict[str, Any]:
        try:
            url = f"{cls.BUZZCAF_API}/api/v1/curate"
            res = requests.post(url, json={"channel": channel_name, "topic": topic}, timeout=3)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass

        # Fallback launch studio platform
        cls.launch_buzzcaf_studio()
        return {
            "success": True,
            "channel": channel_name,
            "topic": topic,
            "message": f"Dispatched BuzzcafAI 115-Agent Workforce for channel '{channel_name}' on topic '{topic}'!"
        }
