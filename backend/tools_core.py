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
import re
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
PLAYLIST_FILE = os.path.join(DATA_DIR, "playlists.json")


# ───────────────────────── implementations ─────────────────────────


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


def _ingest_note(path: str) -> None:
    """Make a fresh note recallable right away instead of waiting for the nightly pass."""
    try:
        import knowledge_ingest

        knowledge_ingest.ingest_file(path, "notes", force=True)
    except Exception as exc:
        print(f"[Notes] Could not index {os.path.basename(path)}: {exc}")


class NotesTool:
    """
    Notes are Markdown files in the notes folder (the `notes` knowledge root,
    or data/notes when none is configured), so they are readable in any
    editor, indexed by knowledge_ingest, and never trapped in a JSON blob.
    The old sticky-notes store also popped open Windows Sticky Notes on every
    note; that is gone.
    """

    @staticmethod
    def notes_dir() -> str:
        try:
            import knowledge_ingest

            for tag, path in knowledge_ingest.parse_roots():
                if tag == "notes":
                    return path
        except Exception:
            pass
        path = os.path.join(DATA_DIR, "notes")
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def _slug(text: str) -> str:
        words = re.findall(r"[a-z0-9]+", (text or "").lower())
        return "-".join(words[:6]) or "note"

    def create_note(self, content: str, title: Optional[str] = None) -> Dict[str, Any]:
        content = (content or "").strip()
        title = (title or "").strip() or (content.split("\n")[0][:60] if content else "Note")
        folder = self.notes_dir()
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d-%H%M")
        name = f"{stamp}-{self._slug(title)}.md"
        path = os.path.join(folder, name)
        counter = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{stamp}-{self._slug(title)}-{counter}.md")
            counter += 1
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"# {title}\n\n{content}\n")
        _ingest_note(path)
        return {
            "id": os.path.basename(path), "title": title, "content": content,
            "path": path, "created": time.strftime("%Y-%m-%d %H:%M"),
        }

    def list_notes(self, limit: int = 20) -> List[Dict[str, Any]]:
        folder = self.notes_dir()
        try:
            names = [n for n in os.listdir(folder) if n.lower().endswith(".md")]
        except OSError:
            return []
        entries = []
        for name in names:
            path = os.path.join(folder, name)
            try:
                mtime = os.path.getmtime(path)
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    text = handle.read(4000)
            except OSError:
                continue
            lines = [l for l in text.splitlines() if l.strip()]
            title = lines[0].lstrip("# ").strip() if lines else name
            body = "\n".join(lines[1:]).strip()
            entries.append({
                "id": name, "title": title, "content": body[:400], "path": path,
                "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)), "_mtime": mtime,
            })
        entries.sort(key=lambda e: e["_mtime"], reverse=True)
        for entry in entries:
            entry.pop("_mtime", None)
        return entries[:limit]

    def delete_note(self, note_id: str) -> bool:
        name = os.path.basename(note_id or "")
        if not name.lower().endswith(".md"):
            return False
        path = os.path.join(self.notes_dir(), name)
        if not os.path.exists(path):
            return False
        os.remove(path)
        try:
            from memory import memory

            memory.remove_document(path)
            memory.remove_ingest_source(path)
        except Exception:
            pass
        return True


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

youtube_tool = YouTubeMusicTool()
notes = NotesTool()


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


@tool(
    name="get_agenda",
    description="Today's agenda: commitments due or overdue, reminders, and calendar events, in one list.",
    params={},
    examples=[
        "what's on my agenda", "plan my day", "what do I have today",
        "show my tasks", "what's my schedule", "read me today's plan",
        "what am I doing today", "daily plan",
    ],
    category="planner",
)
def get_agenda() -> ToolResult:
    import agenda as agenda_module

    snapshot = agenda_module.compose()
    tasks = snapshot.get("tasks", [])
    pending = [t for t in tasks if not t.get("completed")]
    done_count = snapshot["counts"].get("done", 0)
    if not tasks:
        return ToolResult(ok=True, summary="Nothing on your agenda today.", data=snapshot)
    if not pending:
        return ToolResult(ok=True, summary=f"Everything is done: {done_count} commitment(s) kept today.", data=snapshot)

    def line(t):
        marker = "overdue: " if t.get("overdue") else ""
        kind = " (reminder)" if t["kind"] == "reminder" else " (calendar)" if t["kind"] == "event" else ""
        rolled = f" [moved {t['rollCount']}x]" if t.get("rollCount") else ""
        return f"{marker}{t.get('time') or 'anytime'} - {t.get('title', '')}{kind}{rolled}"

    summary = f"{len(pending)} thing(s) today ({done_count} done): " + "; ".join(line(t) for t in pending[:8])
    return ToolResult(ok=True, summary=summary, data=snapshot)


@tool(
    name="add_task",
    description=(
        "Commit to a task today (or on a given day/time). It goes into the workbook; "
        "with a time, a reminder is set too."
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
    import agenda as agenda_module

    try:
        item = agenda_module.add(title, when)
    except ValueError as exc:
        return ToolResult.fail(str(exc))
    reminder = " I'll remind you before it." if item.get("reminder") else ""
    return ToolResult(
        ok=True,
        summary=f"Committed to '{title}' {item.get('display', 'today')}.{reminder}",
        data=item,
    )


@tool(
    name="complete_task",
    description="Mark an open commitment as done, matched by its title.",
    params={"title": {"type": "string", "description": "Title or part of the task title"}},
    examples=[
        "mark the gym task as done", "I finished reviewing the script",
        "check off my morning routine", "complete the editing task",
    ],
    category="planner",
)
def complete_task(title: str) -> ToolResult:
    import agenda as agenda_module

    done = agenda_module.complete(title)
    if not done:
        return ToolResult.fail(f"I couldn't find an open commitment matching '{title}'.")
    return ToolResult(ok=True, summary=f"Marked '{done.get('text', title)}' as done.", data=done)


# ───────────────────────── notes ─────────────────────────


@tool(
    name="create_note",
    description="Write a note (a Markdown file in your notes folder) to capture an idea or piece of information.",
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
    note = notes.create_note(content, title)
    return ToolResult(ok=True, summary=f"Noted in {os.path.basename(note['path'])}: {content[:120]}", data=note)


@tool(
    name="list_notes",
    description="List the user's notes (Markdown files in the notes folder), newest first.",
    params={},
    examples=["show my notes", "what notes do I have", "read my sticky notes", "list my notes"],
    category="notes",
)
def list_notes() -> ToolResult:
    entries = notes.list_notes()
    if not entries:
        return ToolResult(ok=True, summary="You have no notes yet.", data={"notes": [], "folder": notes.notes_dir()})
    preview = "; ".join(f"{n['title']}" for n in entries[:6])
    return ToolResult(
        ok=True,
        summary=f"You have {len(entries)} note(s) in {notes.notes_dir()}: {preview}",
        data={"notes": entries, "folder": notes.notes_dir()},
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


def registered_categories() -> List[str]:
    from tool_registry import all_tools

    return sorted({spec.category for spec in all_tools()})
