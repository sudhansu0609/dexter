"""
Activity awareness as Dexter tools (roadmap v7, K7): what am I working on,
what did I do today / yesterday / this morning.
"""

from typing import Optional

import activity
from tool_registry import ToolResult, tool


@tool(
    name="what_am_i_doing",
    description="What the user is working on right now: the app and window in focus, how long, files just saved, where today's time went.",
    examples=[
        "what am I working on",
        "what am I doing right now",
        "what was I doing",
        "where was I",
        "what did I have open",
        "what have I been working on",
    ],
    category="activity",
)
def what_am_i_doing() -> ToolResult:
    if not activity.ENABLED:
        return ToolResult.fail("Activity awareness is off (Settings → Activity awareness).")
    return ToolResult(ok=True, summary=activity.narrative("now"), data=activity.brief())


@tool(
    name="activity_summary",
    description="How the user's time went: today, yesterday, this morning or this week, by app and by project.",
    params={"when": {"type": "string", "description": "today, yesterday, this morning, this week", "required": False}},
    examples=[
        "what did I do today",
        "how did I spend my time today",
        "what did I work on yesterday",
        "what did I do this morning",
        "how did my day go",
        "time by app today",
    ],
    category="activity",
)
def activity_summary(when: Optional[str] = "today") -> ToolResult:
    if not activity.ENABLED:
        return ToolResult.fail("Activity awareness is off (Settings → Activity awareness).")
    return ToolResult(ok=True, summary=activity.narrative(when or "today"), data=activity.brief())
