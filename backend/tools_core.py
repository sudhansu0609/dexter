"""
Core tool implementations and registrations.

The planner, notes, playlist, launcher and browser implementations live here
directly (folded in from the retired dexter_tools.py - roadmap 0.3; the
overnight-curation controller was removed in v5 because the Buzzcaf Studio
owns video ideation), alongside the
@tool registrations that give each one a uniform signature, a description the
model can reason about, and the example phrasings that power Tier 1 semantic
matching. filesystem_tool.py and buzzcaf_client.py remain separate modules.

Owning the tool singletons here (rather than in main.py) keeps a single instance
per process and lets the scheduler and router import them without cycles.
"""

import difflib
import os
import shutil
import subprocess
import time
import urllib.parse
import webbrowser
from datetime import datetime
from typing import Any, Dict, List, Optional

import app_index
from buzzcaf_client import buzzcaf_client
from filesystem_tool import FileSystemTool
from jsonstore import load_json, save_json
from timeparse import format_display, parse_when
from tool_registry import ToolResult, tool

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PLANNER_FILE = os.path.join(DATA_DIR, "day_planner.json")
PLAYLIST_FILE = os.path.join(DATA_DIR, "playlists.json")
NOTES_FILE = os.path.join(DATA_DIR, "sticky_notes.json")


# ───────────────────────── implementations ─────────────────────────


class DayPlannerTool:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(PLANNER_FILE):
            # An empty day, not a demo one: seeded sample tasks used to show up
            # as if the owner had planned them.
            self._save_planner({"date": time.strftime("%Y-%m-%d"), "tasks": []})

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
            os.startfile("ms-stk:")
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
    def _launch_target(cls, target: str) -> None:
        """
        Launch without a shell. os.startfile drives ShellExecute, which already
        resolves protocol URIs (ms-stk:), App Paths names (chrome), and PATH
        executables - everything `start` did, minus the injection surface of
        interpolating a user-derived name into a command line.
        """
        target = target.strip().strip('"')
        if target.lower().startswith("start "):
            target = target[6:].strip().strip('"')
        if not target:
            raise OSError("empty launch target")
        try:
            os.startfile(target)
        except OSError:
            # ShellExecute misses PATHEXT scripts like code.cmd; which() finds them.
            resolved = shutil.which(target)
            if not resolved:
                raise
            subprocess.Popen(
                [resolved], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )

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
            attempts.append(found_exe)
        attempts.append(clean_name)

        errors: List[str] = []
        for idx, run_cmd in enumerate(attempts, 1):
            try:
                print(f"[Smart Launcher] Attempt {idx}: '{run_cmd}'")
                cls._launch_target(run_cmd)
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


# ───────────────────────── shared singletons ─────────────────────────

day_planner = DayPlannerTool()
youtube_tool = YouTubeMusicTool()
sticky_notes = StickyNotesTool()


# ───────────────────────── applications ─────────────────────────


@tool(
    name="launch_app",
    description=(
        "Open, launch or start an application on the user's Windows PC. "
        "Use for any installed program: browsers, editors, Adobe apps, games, utilities."
    ),
    params={
        "app_name": {
            "type": "string",
            "description": "Name of the application, e.g. 'chrome', 'vscode', 'photoshop'",
        }
    },
    examples=[
        "open chrome", "launch vscode", "start spotify", "fire up photoshop",
        "open notepad", "can you open the calculator", "launch premiere pro",
        "start file explorer", "open task manager", "boot up discord",
    ],
    category="system",
)
def launch_app(app_name: str) -> ToolResult:
    return ToolResult.from_legacy(AppLauncherTool.launch_app(app_name))


@tool(
    name="open_website",
    description="Open a website or URL in the default web browser.",
    params={"url": {"type": "string", "description": "URL or domain, e.g. 'github.com'"}},
    examples=[
        "go to github.com", "open youtube.com", "navigate to reddit",
        "visit stackoverflow", "browse to twitter.com", "pull up gmail in the browser",
    ],
    category="system",
)
def open_website(url: str) -> ToolResult:
    return ToolResult.from_legacy(PlaywrightBrowserTool.browse_url(url))


# ───────────────────────── files ─────────────────────────


@tool(
    name="search_files",
    description=(
        "Search the user's local PC for files whose name matches a query. "
        "Returns matching paths. Use this before reading or opening a file "
        "when you only know part of its name."
    ),
    params={
        "query": {"type": "string", "description": "Part of the filename to look for"},
        "path": {
            "type": "string",
            "description": "Optional folder to search in. Defaults to the Dexter project folder.",
            "required": False,
        },
    },
    examples=[
        "find my invoice file", "search for a file called report",
        "look for python files", "where is my resume", "find files named budget",
    ],
    category="files",
)
def search_files(query: str, path: Optional[str] = None) -> ToolResult:
    raw = FileSystemTool.search_files(query, path)
    results = raw.get("results", [])
    if not raw.get("success"):
        return ToolResult.fail(raw.get("error", "File search failed."), **raw)
    if not results:
        return ToolResult(ok=True, summary=f"No files matching '{query}' were found.", data=raw)

    names = ", ".join(r["fileName"] for r in results[:5])
    more = f" (+{len(results) - 5} more)" if len(results) > 5 else ""
    return ToolResult(
        ok=True,
        summary=f"Found {len(results)} file(s) matching '{query}': {names}{more}",
        data=raw,
    )


@tool(
    name="read_file",
    description="Read the contents of a file on the user's PC. Requires a full path.",
    params={"path": {"type": "string", "description": "Absolute path to the file"}},
    examples=["read that file", "show me the contents of this file", "open and read the document"],
    category="files",
)
def read_file(path: str) -> ToolResult:
    raw = FileSystemTool.read_file(path)
    if not raw.get("success"):
        return ToolResult.fail(raw.get("error", "Could not read file."), **raw)
    content = raw.get("content", "")
    preview = content[:600] + ("..." if raw.get("truncated") else "")
    return ToolResult(
        ok=True,
        summary=f"Contents of {os.path.basename(path)}:\n{preview}",
        data=raw,
    )


@tool(
    name="write_file",
    description="Write text content to a file on disk, creating or overwriting it.",
    params={
        "path": {"type": "string", "description": "Absolute path of the file to write"},
        "content": {"type": "string", "description": "Text to write into the file"},
    },
    examples=["save this to a file", "write that into a text file", "create a file with this content"],
    confirm=True,
    category="files",
)
def write_file(path: str, content: str) -> ToolResult:
    return ToolResult.from_legacy(FileSystemTool.write_file(path, content))


@tool(
    name="create_folder",
    description=(
        "Create a new folder and open it in File Explorer. "
        "A relative name is created on the Desktop."
    ),
    params={"path": {"type": "string", "description": "Folder name or absolute path"}},
    examples=[
        "create a folder called projects", "make a new folder on my desktop",
        "make a directory named invoices", "new folder for the video assets",
    ],
    category="files",
)
def create_folder(path: str) -> ToolResult:
    return ToolResult.from_legacy(FileSystemTool.create_folder(path))


@tool(
    name="open_path",
    description="Open an existing file or folder in the default Windows viewer.",
    params={"path": {"type": "string", "description": "Absolute path to open"}},
    examples=["open that folder", "open this file", "show me that in explorer"],
    category="files",
)
def open_path(path: str) -> ToolResult:
    return ToolResult.from_legacy(FileSystemTool.open_local_path(path))


# ───────────────────────── day planner ─────────────────────────


def _find_task(title_fragment: str) -> Optional[Dict[str, Any]]:
    """Match a task by fuzzy title, because the model never knows task ids."""
    tasks = day_planner.get_agenda().get("tasks", [])
    if not tasks:
        return None
    fragment = title_fragment.lower().strip()

    for task in tasks:
        if fragment == task.get("title", "").lower():
            return task
    for task in tasks:
        if fragment and fragment in task.get("title", "").lower():
            return task

    titles = [t.get("title", "") for t in tasks]
    close = difflib.get_close_matches(title_fragment, titles, n=1, cutoff=0.55)
    if close:
        return next((t for t in tasks if t.get("title") == close[0]), None)
    return None


@tool(
    name="get_agenda",
    description="Get the user's schedule and task list for today.",
    params={},
    examples=[
        "what's on my agenda", "plan my day", "what do I have today",
        "show my tasks", "what's my schedule", "read me today's plan",
        "what am I doing today", "daily plan",
    ],
    category="planner",
)
def get_agenda() -> ToolResult:
    agenda = day_planner.get_agenda()
    tasks = agenda.get("tasks", [])
    if not tasks:
        return ToolResult(ok=True, summary="Your agenda is empty for today.", data=agenda)

    pending = [t for t in tasks if not t.get("completed")]
    done_count = len(tasks) - len(pending)
    if not pending:
        return ToolResult(
            ok=True,
            summary=f"All {len(tasks)} tasks for today are complete.",
            data=agenda,
        )

    lines = [f"{t.get('time', '')} - {t.get('title', '')}" for t in pending[:6]]
    summary = f"{len(pending)} task(s) remaining today ({done_count} done): " + "; ".join(lines)
    return ToolResult(ok=True, summary=summary, data=agenda)


@tool(
    name="add_task",
    description=(
        "Add a task to the user's day planner at a given time. "
        "Use this for scheduling something, not for a one-off reminder alert."
    ),
    params={
        "title": {"type": "string", "description": "What the task is"},
        "when": {
            "type": "string",
            "description": "Natural language time, e.g. '3pm', 'tomorrow at 9', 'in 2 hours'",
            "required": False,
        },
    },
    examples=[
        "add a task to review the script at 3pm", "schedule gym session tomorrow at 6",
        "put a meeting on my calendar at 2", "add to my plan: edit the video",
        "book time for deep work at 10am",
    ],
    category="planner",
)
def add_task(title: str, when: Optional[str] = None) -> ToolResult:
    parsed = parse_when(when) if when else None
    display = format_display(parsed) if parsed else (when or "12:00 PM")

    task = day_planner.add_task(title, display)
    if parsed:
        task["at"] = parsed.isoformat(timespec="seconds")
        day_planner.update_task(task["id"], {"at": task["at"]})

    return ToolResult(
        ok=True,
        summary=f"Added '{title}' to your plan at {display}.",
        data=task,
    )


@tool(
    name="complete_task",
    description="Mark a task in the day planner as done, matched by its title.",
    params={"title": {"type": "string", "description": "Title or part of the task title"}},
    examples=[
        "mark the gym task as done", "I finished reviewing the script",
        "check off my morning routine", "complete the editing task",
    ],
    category="planner",
)
def complete_task(title: str) -> ToolResult:
    task = _find_task(title)
    if not task:
        return ToolResult.fail(f"I couldn't find a task matching '{title}' on today's plan.")
    day_planner.toggle_task(task["id"])
    state = "not done" if task.get("completed") else "done"
    return ToolResult(ok=True, summary=f"Marked '{task['title']}' as {state}.", data=task)


# ───────────────────────── notes ─────────────────────────


@tool(
    name="create_note",
    description="Write a sticky note to capture an idea or piece of information.",
    params={
        "content": {"type": "string", "description": "The text of the note"},
        "title": {"type": "string", "description": "Short title", "required": False},
    },
    examples=[
        "take a note", "write this down", "make a sticky note about the meeting",
        "note that I need to call the client", "jot this down", "capture this idea",
    ],
    category="notes",
)
def create_note(content: str, title: Optional[str] = None) -> ToolResult:
    note = sticky_notes.create_note(content, title or "Sticky Note")
    return ToolResult(ok=True, summary=f"Noted: {content[:120]}", data=note)


@tool(
    name="list_notes",
    description="List the user's saved sticky notes.",
    params={},
    examples=["show my notes", "what notes do I have", "read my sticky notes", "list my notes"],
    category="notes",
)
def list_notes() -> ToolResult:
    notes = sticky_notes.list_notes()
    if not notes:
        return ToolResult(ok=True, summary="You have no notes saved.", data={"notes": []})
    preview = "; ".join(n.get("content", "")[:60] for n in notes[:5])
    return ToolResult(
        ok=True,
        summary=f"You have {len(notes)} note(s): {preview}",
        data={"notes": notes},
    )


# ───────────────────────── media ─────────────────────────


@tool(
    name="play_on_youtube",
    description=(
        "Search YouTube for a song, artist or video and open it in the browser. "
        "Use when the user names specific content to watch or listen to."
    ),
    params={"query": {"type": "string", "description": "What to search for on YouTube"}},
    examples=[
        "play some lofi music", "put on jazz", "play the latest video from that channel",
        "search youtube for python tutorials", "play relaxing music",
        # The README advertises this exact phrasing as a Tier-1 example, but it
        # scored below the confidence floor and fell through to the agent loop.
        "put on some music", "put some music on",
    ],
    category="media",
)
def play_on_youtube(query: str) -> ToolResult:
    raw = youtube_tool.search_and_play(query)
    return ToolResult(ok=True, summary=f"Playing '{query}' on YouTube.", data=raw)


# ───────────────────────── BuzzcafAI studio ─────────────────────────


@tool(
    name="buzzcaf_status",
    description="Check the status of the BuzzcafAI content studio and its projects.",
    params={},
    examples=[
        "what's the status of buzzcaf", "buzzcaf project status",
        "how are my video projects doing", "is the studio running",
    ],
    category="buzzcaf",
)
def buzzcaf_status() -> ToolResult:
    status = buzzcaf_client.get_status()
    if not status.get("online"):
        return ToolResult(
            ok=True,
            summary="BuzzcafAI studio is offline. Say 'start buzzcaf studio' to bring it up.",
            data=status,
        )
    return ToolResult(
        ok=True,
        summary=(
            f"BuzzcafAI is online with {status.get('total_projects', 0)} project(s), "
            f"{status.get('in_progress', 0)} in progress."
        ),
        data=status,
    )


@tool(
    name="buzzcaf_start_studio",
    description="Start the BuzzcafAI 115-agent content studio (backend and dashboard).",
    params={},
    examples=[
        "start buzzcaf studio", "launch buzzcafai", "boot up the agent studio",
        "open spilled coffee studio", "start the 115 agents",
    ],
    category="buzzcaf",
)
def buzzcaf_start_studio() -> ToolResult:
    result = buzzcaf_client.start_studio(wait_for_ready=True, timeout=20)
    online = result.get("online") or result.get("success")
    return ToolResult(
        ok=bool(online),
        summary=(
            "The Buzzcaf Studio is up." if online
            else "I tried to start the Buzzcaf Studio but it did not come online in time."
        ),
        data=result,
    )


@tool(
    name="buzzcaf_list_projects",
    description="List the video projects in the BuzzcafAI studio.",
    params={},
    examples=["show my video projects", "list buzzcaf projects", "what projects do I have"],
    category="buzzcaf",
)
def buzzcaf_list_projects() -> ToolResult:
    projects = buzzcaf_client.list_projects() or []
    if not projects:
        return ToolResult(ok=True, summary="You have no BuzzcafAI projects yet.", data={"projects": []})
    names = "; ".join(
        f"{p.get('name', 'untitled')} ({p.get('status', 'unknown')})" for p in projects[:5]
    )
    return ToolResult(
        ok=True,
        summary=f"{len(projects)} project(s): {names}",
        data={"projects": projects},
    )


@tool(
    name="buzzcaf_create_project",
    description="Create a new video production project in the BuzzcafAI studio.",
    params={
        "name": {"type": "string", "description": "Project or video topic name"},
        "brand": {"type": "string", "description": "Channel brand", "required": False},
        "workflow_name": {"type": "string", "description": "Workflow template", "required": False},
    },
    examples=[
        "start a new video about AI agents", "create a video project on productivity",
        "make a video about the new iphone", "produce a video on crypto trends",
    ],
    category="buzzcaf",
)
def buzzcaf_create_project(
    name: str, brand: Optional[str] = None, workflow_name: Optional[str] = None
) -> ToolResult:
    if not buzzcaf_client.health_check():
        buzzcaf_client.start_studio(wait_for_ready=True, timeout=20)

    brands = buzzcaf_client.list_brands()
    if not brands:
        return ToolResult.fail(
            "The Studio returned no channel brands - is it running? Say 'open the studio' first."
        )
    chosen = brand if brand in brands else brands[0]

    project = buzzcaf_client.create_project(
        name, chosen, workflow_name or "full_video_workflow"
    )
    if not project or not project.get("id"):
        return ToolResult.fail("Could not create the project in BuzzcafAI.", raw=project)

    buzzcaf_client.set_active_project(project["id"])
    return ToolResult(
        ok=True,
        summary=f"Created video project '{name}' on {chosen}. Say 'run the next step' to begin.",
        data=project,
    )


@tool(
    name="buzzcaf_execute_step",
    description=(
        "Run the next workflow step on the active BuzzcafAI video project, "
        "optionally with revision feedback."
    ),
    params={
        "feedback": {
            "type": "string",
            "description": "Optional revision notes for the current step",
            "required": False,
        }
    },
    examples=[
        "run the next step", "continue the project", "advance the workflow",
        "make the script funnier", "revise that and continue",
    ],
    category="buzzcaf",
)
def buzzcaf_execute_step(feedback: Optional[str] = None) -> ToolResult:
    project_id = buzzcaf_client.get_active_project()
    if not project_id:
        projects = buzzcaf_client.list_projects() or []
        active = next((p for p in projects if p.get("status") == "in_progress"), None)
        if not active:
            return ToolResult.fail(
                "There is no active video project. Say 'start a video about ...' first."
            )
        project_id = active.get("id")
        buzzcaf_client.set_active_project(project_id)

    result = buzzcaf_client.execute_step(project_id, feedback)
    if not isinstance(result, dict):
        return ToolResult.fail("The Studio returned an unexpected reply for that step.", raw=result)
    if result.get("error") or result.get("status") == "error":
        detail = result.get("error") or result.get("message") or "unknown error"
        return ToolResult.fail(f"The Studio could not run the step: {detail}", raw=result)

    project = result.get("project") if isinstance(result.get("project"), dict) else result
    history = project.get("history") if isinstance(project, dict) else None
    last = history[-1] if isinstance(history, list) and history else {}
    step = (
        (last.get("step_name") if isinstance(last, dict) else None)
        or result.get("step") or result.get("current_step")
        or (project.get("current_step") if isinstance(project, dict) else None)
        or "the next step"
    )
    step_status = (last.get("status") if isinstance(last, dict) else None) or result.get("status") or ""
    if step_status == "paused_for_approval":
        summary = (
            f"{step} finished and is waiting for your approval. "
            "Say 'approve' to continue or give revision notes."
        )
    elif step_status in ("failed", "error"):
        return ToolResult.fail(f"{step} failed in the Studio.", raw=result)
    elif isinstance(project, dict) and project.get("status") == "completed":
        summary = f"{step} finished; the project is complete."
    else:
        summary = f"Ran {step} on the active project."
    return ToolResult(ok=True, summary=summary, data=result)


def registered_categories() -> List[str]:
    from tool_registry import all_tools

    return sorted({spec.category for spec in all_tools()})
