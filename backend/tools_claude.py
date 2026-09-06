"""
What happened in Claude Code, as Dexter tools (roadmap v7, K8). Read-only:
Dexter never runs Claude Code; buzzcode is the coder.
"""

from typing import Optional

import claude_sessions
from tool_registry import ToolResult, tool


@tool(
    name="claude_activity",
    description="What the user did in Claude Code today, yesterday or this week: sessions per project, what was asked, files Claude wrote, commands run.",
    params={
        "when": {"type": "string", "description": "today, yesterday, this week", "required": False},
        "project": {"type": "string", "description": "Only sessions in this repository", "required": False},
    },
    examples=[
        "what was I doing in claude",
        "what did claude do today",
        "what did claude change in dexter",
        "what did I ask claude yesterday",
        "claude sessions this week",
        "what has claude been working on",
    ],
    category="claude",
)
def claude_activity(when: Optional[str] = "today", project: Optional[str] = None) -> ToolResult:
    text = claude_sessions.narrative(when or "today", project)
    return ToolResult(ok=True, summary=text, data={"sessions": claude_sessions.list_sessions(days=8, project=project, limit=12)})


@tool(
    name="claude_session",
    description="The story of one Claude Code session (the last one by default, or by project or title): what was asked, files written, commands, Claude's last words.",
    params={"which": {"type": "string", "description": "'last', a project name, or words from the session title", "required": False}},
    examples=[
        "show me the last claude session",
        "what happened in the claude session on buzzanimate",
        "what was claude's last message",
        "tell me about the claude session about rigging",
    ],
    category="claude",
)
def claude_session(which: Optional[str] = None) -> ToolResult:
    info = claude_sessions.find_session(which)
    if not info:
        return ToolResult.fail("I can't find a Claude Code session like that in the last 30 days.")
    return ToolResult(ok=True, summary=claude_sessions.session_story(info), data={k: v for k, v in info.items() if k != "path"})
