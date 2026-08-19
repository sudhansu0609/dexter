"""
Core tool registrations.

These wrap the existing, working implementations in dexter_tools.py,
filesystem_tool.py, buzzcaf_client.py and spilled_coffee_controller.py. The
implementations are not rewritten here - this module only gives them a uniform
signature, a description the model can reason about, and the example phrasings
that power Tier 1 semantic matching.

Owning the tool singletons here (rather than in main.py) keeps a single instance
per process and lets the scheduler and router import them without cycles.
"""

import difflib
import os
from typing import Any, Dict, List, Optional

from buzzcaf_client import buzzcaf_client
from dexter_tools import (
    AppLauncherTool,
    DayPlannerTool,
    PlaywrightBrowserTool,
    StickyNotesTool,
    YouTubeMusicTool,
)
from filesystem_tool import FileSystemTool
from hybrid_llm import llm_router
from spilled_coffee_controller import SpilledCoffeeAIController
from timeparse import format_display, parse_when
from tool_registry import ToolResult, tool

# ───────────────────────── shared singletons ─────────────────────────

day_planner = DayPlannerTool()
youtube_tool = YouTubeMusicTool()
sticky_notes = StickyNotesTool()
spilled_coffee = SpilledCoffeeAIController(llm_router)


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
    ],
    category="media",
)
def play_on_youtube(query: str) -> ToolResult:
    raw = youtube_tool.search_and_play(query)
    return ToolResult(ok=True, summary=f"Playing '{query}' on YouTube.", data=raw)


# ───────────────────────── content studio ─────────────────────────


@tool(
    name="curate_video_ideas",
    description=(
        "Generate fresh YouTube video concepts, titles and hooks for a niche. "
        "Used for the SpilledCoffee content pipeline."
    ),
    params={
        "niche": {"type": "string", "description": "Topic area", "required": False},
        "count": {"type": "integer", "description": "How many ideas", "required": False},
    },
    examples=[
        "curate some video ideas", "generate video titles",
        "give me content ideas for my channel", "brainstorm youtube videos",
        "run the overnight curation",
    ],
    category="content",
)
def curate_video_ideas(niche: Optional[str] = None, count: Optional[int] = None) -> ToolResult:
    items = spilled_coffee.run_overnight_curation(
        niche_topic=niche or "AI Tech & Viral Trends",
        count=count or 3,
    )
    titles = "; ".join(i.get("title", "") for i in (items or [])[:3])
    return ToolResult(
        ok=True,
        summary=f"Generated {len(items or [])} video concept(s): {titles}",
        data={"items": items},
    )


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
            "BuzzcafAI studio is starting up - the dashboard will be at localhost:3005."
            if online else "I tried to start BuzzcafAI but it did not come online in time."
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

    brands = buzzcaf_client.list_brands() or ["Beyond3Baje"]
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
    step = result.get("step") or result.get("current_step") or "next step"
    return ToolResult(
        ok=True,
        summary=f"Ran {step} on the active project.",
        data=result,
    )


def registered_categories() -> List[str]:
    from tool_registry import all_tools

    return sorted({spec.category for spec in all_tools()})
