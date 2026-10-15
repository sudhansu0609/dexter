"""
Mission Reminder as tools (v13).

Dexter's own mission (north star, this week's focus, today's win) is
`tools_mission.py` and lives in Dexter's data. This file is about the *app* -
the blocks, the goals, the tree - which has its own model and its own window.
They stay separate on purpose; these tools are the bridge, and they say which
side they acted on.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import mission_bridge as bridge
from tool_registry import ToolResult, tool


def _guard(fn) -> ToolResult:
    try:
        return fn()
    except bridge.MissionError as exc:
        return ToolResult.fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool reports, never raises
        return ToolResult.fail(f"That didn't work: {str(exc).splitlines()[0][:200]}")


@tool(
    name="mission_review",
    description=(
        "Review the missions and time blocks in the Mission Reminder app: the "
        "mission statement, today's blocks and which were kept, the running "
        "session and the next nudge."
    ),
    params={},
    examples=[
        "review my missions",
        "what are my blocks today",
        "how am I doing on my blocks",
        "what does mission reminder say",
        "read me my mission",
        "am I on track with my time blocks",
        "what's my next block",
    ],
    category="mission",
)
def mission_review() -> ToolResult:
    def _do():
        state = bridge.review()
        return ToolResult(ok=bool(state.get("running")), summary=bridge.describe(state), data=state)

    return _guard(_do)


@tool(
    name="mission_go_to_tab",
    description=(
        "Switch to a specific tab in the Mission Reminder app: 'today', 'mission', "
        "'goals', 'blocks', 'forest', 'review', or 'settings'. Brings the app forward "
        "and switches to the tab."
    ),
    params={
        "tab": {
            "type": "string",
            "description": "The tab name: 'today', 'mission', 'goals', 'blocks', 'forest', 'review', or 'settings'",
            "enum": ["today", "mission", "goals", "blocks", "forest", "review", "settings"],
        }
    },
    examples=[
        "open goals tab in mission reminder",
        "go to goals tab in mission reminder",
        "switch to goals tab",
        "show goals in mission reminder",
        "open blocks tab in mission reminder",
        "switch to review tab in mission reminder",
        "open settings in mission reminder",
        "open today tab in mission reminder",
    ],
    category="mission",
)
def mission_go_to_tab(tab: str) -> ToolResult:
    def _do():
        info = bridge.go_to(tab)
        tab_title = str(info.get("tab", tab)).capitalize()
        return ToolResult(
            ok=True,
            summary=f"Switched to the {tab_title} tab in Mission Reminder.",
            data=info,
        )

    return _guard(_do)


@tool(
    name="mission_save_block",
    description=(
        "Add a time block to the Mission Reminder app - a promise to show up at a "
        "time. Drives the app's own UI, so the block is saved where the app keeps it."
    ),
    params={
        "name": {"type": "string", "description": "What the block is, e.g. 'Deep work'"},
        "start": {"type": "string", "description": "When it starts, e.g. '9am', '14:30'",
                  "required": False},
        "minutes": {"type": "integer", "description": "How long, in minutes", "required": False},
    },
    examples=[
        "add a block called deep work at 9am for 50 minutes",
        "schedule a mission block for editing at 2pm",
        "put a block in mission reminder for the gym at 6",
        "save a time block called writing at 7am",
    ],
    category="mission",
)
def mission_save_block(name: str, start: Optional[str] = None,
                       minutes: Optional[int] = None) -> ToolResult:
    def _do():
        info = bridge.add_block(name, start=start, minutes=minutes)
        when = f" at {info['start']}" if info.get("start") else ""
        span = f" for {info['minutes']} min" if info.get("minutes") else ""
        return ToolResult(
            ok=True,
            summary=f"Saved '{info['name']}'{when}{span} as a block in Mission Reminder.",
            data=info,
        )

    return _guard(_do)


@tool(
    name="mission_save_goal",
    description="Add a goal to the Mission Reminder app, with why it serves the mission.",
    params={
        "title": {"type": "string", "description": "The goal"},
        "why": {"type": "string", "description": "Why it serves the mission", "required": False},
    },
    examples=[
        "add a goal in mission reminder to ship version two",
        "save a mission goal: finish the course",
        "add the goal launch the channel to mission reminder",
    ],
    category="mission",
)
def mission_save_goal(title: str, why: Optional[str] = None) -> ToolResult:
    def _do():
        info = bridge.add_goal(title, why=why)
        return ToolResult(ok=True, summary=f"Saved the goal '{info['title']}' in Mission Reminder.",
                          data=info)

    return _guard(_do)


@tool(
    name="mission_save_statement",
    description=(
        "Write the mission statement in the Mission Reminder app - the one thing the "
        "owner's days are supposed to add up to."
    ),
    params={"statement": {"type": "string", "description": "The mission, in one line"}},
    examples=[
        "set my mission in mission reminder to build a studio that runs itself",
        "save this as my mission: one video a week, forever",
        "update the mission statement in the app",
    ],
    category="mission",
)
def mission_save_statement(statement: str) -> ToolResult:
    def _do():
        info = bridge.save_mission(statement)
        return ToolResult(ok=True, summary=f"Mission saved in the app: {info['statement']}",
                          data=info)

    return _guard(_do)


@tool(
    name="mission_start_block",
    description=(
        "Start today's next time block in the Mission Reminder app (or pause/resume "
        "the one that is running)."
    ),
    params={
        "action": {"type": "string", "description": "'start' or 'pause'", "required": False,
                   "enum": ["start", "pause"]},
    },
    examples=[
        "start my next block",
        "start the next mission block",
        "pause my block",
        "resume my block",
        "begin my focus session",
    ],
    category="mission",
)
def mission_start_block(action: str = "start") -> ToolResult:
    def _do():
        if str(action).strip().lower().startswith("pause") or str(action).strip().lower().startswith("resume"):
            info = bridge.toggle_pause()
            session = info.get("session")
            if not session:
                return ToolResult(ok=True, summary="Nothing is running in Mission Reminder.",
                                  data=info)
            word = "paused" if session["paused"] else "running again"
            return ToolResult(ok=True, summary=f"'{session['name']}' is {word}.", data=info)
        info = bridge.start_next_block()
        session = info.get("session")
        if session:
            return ToolResult(ok=True,
                              summary=f"Started '{session['name']}'. I'll stay quiet while it runs.",
                              data=info)
        return ToolResult(
            ok=False,
            summary=("I pressed start in Mission Reminder but it hasn't reported a running "
                     "session - there may be no block left today."),
            data=info,
        )

    return _guard(_do)


@tool(
    name="mission_outbox",
    description=(
        "What Dexter has tried to save into Mission Reminder, and whether it landed. "
        "Use when a save was reported as failed."
    ),
    params={},
    examples=[
        "did my mission block save",
        "what's in the mission outbox",
        "show me the mission saves that failed",
    ],
    category="mission",
)
def mission_outbox() -> ToolResult:
    rows = bridge.history(10)
    if not rows:
        return ToolResult(ok=True, summary="I haven't saved anything into Mission Reminder yet.",
                          data={"items": []})
    failed = [r for r in rows if r.get("state") == "failed"]
    lines = [f"{r['at']} {r['kind']}: {r.get('state')}"
             + (f" - {r.get('note', '')[:80]}" if r.get("note") else "") for r in rows[-6:]]
    head = f"{len(failed)} of the last {len(rows)} did not land. " if failed else ""
    return ToolResult(ok=True, summary=head + "; ".join(lines),
                      data={"items": rows, "failed": len(failed)})
