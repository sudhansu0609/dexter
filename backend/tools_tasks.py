"""
Autonomy tools: background tasks, scheduled goals, and recorded macros.

Kept separate from tools_core/tools_assistant because these import the task
queue and macro recorder, which import the router - grouping them here keeps
the import graph acyclic.
"""

from typing import Optional

import macros
import tasks
from tool_registry import ToolResult, tool

# ───────────────────────── background tasks ─────────────────────────


@tool(
    name="run_in_background",
    description=(
        "Hand Dexter a goal to work on in the background while the user does "
        "something else. Dexter plans the steps, executes them, reports "
        "progress, and speaks up when finished."
    ),
    params={"goal": {"type": "string", "description": "What to accomplish"}},
    examples=[
        "in the background, check my email and calendar and brief me",
        "work on curating video ideas while I edit",
        "handle this in the background",
        "do that as a background task",
    ],
    category="tasks",
)
def run_in_background(goal: str) -> ToolResult:
    submitted = tasks.task_queue.submit(goal)
    if not submitted.get("ok"):
        return ToolResult.fail(submitted.get("error", "Could not start the task."))
    return ToolResult(
        ok=True,
        summary=f"On it - working on '{submitted['title']}' in the background. "
                "I'll tell you when it's done.",
        data=submitted,
    )


@tool(
    name="check_background_tasks",
    description="Report the status of Dexter's background tasks.",
    params={},
    examples=[
        "how are your background tasks going",
        "status of the background task",
        "what are you working on right now",
    ],
    category="tasks",
)
def check_background_tasks() -> ToolResult:
    records = tasks.task_queue.list(limit=5)
    if not records:
        return ToolResult(ok=True, summary="No background tasks yet.", data={"tasks": []})
    active = [t for t in records if t["status"] in ("queued", "running", "waiting_confirmation")]
    lines = [f"{t['title']}: {t['status'].replace('_', ' ')}" for t in records[:3]]
    lead = f"{len(active)} active. " if active else "Nothing running. "
    return ToolResult(ok=True, summary=lead + "; ".join(lines), data={"tasks": records})


# ───────────────────────── scheduled goals ─────────────────────────


@tool(
    name="schedule_task",
    description=(
        "Schedule a goal for Dexter to run automatically at a time or on a "
        "recurring pattern, e.g. every Friday at 5 curate video ideas."
    ),
    params={
        "goal": {"type": "string", "description": "What to do when it fires"},
        "when": {"type": "string", "description": "When, e.g. 'every friday at 5pm', 'tomorrow at 9'"},
    },
    examples=[
        "every friday at 5 curate video ideas and email me the list",
        "every morning at 8 check my calendar and brief me",
        "schedule a task for tomorrow at noon to clean up my downloads",
        "automate checking my email every day at 9",
    ],
    category="tasks",
)
def schedule_task(goal: str, when: str) -> ToolResult:
    result = tasks.schedule_goal(goal, when)
    if not result.get("ok"):
        return ToolResult.fail(result.get("error", "Could not schedule that."))
    return ToolResult(
        ok=True,
        summary=f"Scheduled: '{goal[:80]}' - {result['when']}.",
        data=result,
    )


@tool(
    name="list_scheduled_tasks",
    description="List the goals Dexter is scheduled to run automatically.",
    params={},
    examples=[
        "what tasks do you have scheduled",
        "list my automations",
        "show scheduled background jobs",
    ],
    category="tasks",
)
def list_scheduled_tasks() -> ToolResult:
    entries = tasks.list_scheduled()
    if not entries:
        return ToolResult(ok=True, summary="No scheduled tasks.", data={"scheduled": []})
    lines = [f"'{e['goal'][:60]}' ({e['whenText']})" for e in entries[:5]]
    return ToolResult(
        ok=True,
        summary=f"{len(entries)} scheduled: " + "; ".join(lines),
        data={"scheduled": entries},
    )


@tool(
    name="cancel_scheduled_task",
    description="Cancel a scheduled automation by describing it.",
    params={"what": {"type": "string", "description": "Words from the scheduled goal"}},
    examples=[
        "cancel the friday curation task",
        "stop the scheduled email check",
        "remove that automation",
    ],
    confirm=True,
    category="tasks",
)
def cancel_scheduled_task(what: str) -> ToolResult:
    removed = tasks.cancel_scheduled(what)
    if not removed:
        return ToolResult.fail(f"No scheduled task matches '{what}'.")
    return ToolResult(
        ok=True,
        summary=f"Cancelled the scheduled task: '{removed['goal'][:80]}'.",
        data={"removed": removed},
    )


# ───────────────────────── recorded macros ─────────────────────────


@tool(
    name="record_skill",
    description=(
        "Start recording the user's next actions as a named, replayable skill. "
        "Every tool Dexter runs is captured until they say stop recording."
    ),
    params={"name": {"type": "string", "description": "Name for the new skill"}},
    examples=[
        "record a skill called publish routine",
        "learn a new skill named morning setup",
        "start recording a macro called render prep",
    ],
    category="skills",
)
def record_skill(name: str) -> ToolResult:
    return macros.start_recording(name)


@tool(
    name="stop_recording",
    description="Stop recording and save the skill being captured.",
    params={},
    examples=[
        "stop recording",
        "that's the whole skill",
        "finish the macro",
        "end recording",
    ],
    category="skills",
)
def stop_recording() -> ToolResult:
    return macros.stop_recording()


@tool(
    name="run_skill",
    description=(
        "Replay a recorded skill by name, optionally substituting its subject "
        "with a new parameter."
    ),
    params={
        "name": {"type": "string", "description": "Which skill to run"},
        "parameter": {
            "type": "string",
            "description": "Optional new subject to substitute into the steps",
            "required": False,
        },
    },
    examples=[
        "run my publish routine",
        "do the morning setup skill",
        "run the render prep macro for the outro video",
        "replay publish routine",
    ],
    confirm=True,
    category="skills",
)
def run_skill(name: str, parameter: Optional[str] = None) -> ToolResult:
    return macros.replay(name, parameter)
