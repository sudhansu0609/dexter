"""
Dictation as tools (v13).

"Take notes from me continuously if I say so, into Notion or Notepad or sticky
notes or any tool I say." These three tools are the switch; `dictation.py` is the
machine, and `voice_pipeline` routes what the mic hears into it while it is on.

The session matters: dictation belongs to the conversation it was started in, so
a note dictated in the pop-up is not interleaved with one in the full window. The
router binds it around the call (`tool_registry.session_now()`) rather than
passing it as a parameter - a session id in the schema is an invitation for the
model to invent one.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from dictation import dictation
from tool_registry import ToolResult, session_now, tool


@tool(
    name="start_dictation",
    description=(
        "Start taking notes continuously: everything the user says next is written "
        "down instead of answered, until they say stop. Give 'into' to choose where "
        "- notes (default), notepad, sticky notes, notion, the clipboard, a file "
        "path, or any open app by name."
    ),
    params={
        "into": {"type": "string",
                 "description": "Where to write: 'notepad', 'notion', 'sticky notes', "
                                "'the clipboard', a file path, or an app's name",
                 "required": False},
        "title": {"type": "string", "description": "What the note is about", "required": False},
    },
    examples=[
        "take notes",
        "start taking notes",
        "take notes from me continuously",
        "take notes in notepad",
        "write this down in notion",
        "start dictation into sticky notes",
        "take notes about the video plan",
        "keep writing down what I say",
    ],
    category="notes",
)
def start_dictation(into: Optional[str] = None, title: Optional[str] = None) -> ToolResult:
    _session = session_now()
    if dictation.active(_session):
        state = dictation.status(_session)
        return ToolResult(
            ok=True,
            summary=f"I'm already taking notes into {state['where']} "
                    f"({state['lines']} line(s)). Say \"stop taking notes\" to finish.",
            data=state,
        )
    result = dictation.start(_session, target=into, title=title)
    if not result.get("ok"):
        return ToolResult.fail(str(result.get("error") or "I couldn't start taking notes."))
    return ToolResult(
        ok=True,
        summary=(
            f"Taking notes into {result['where']}. Everything you say goes in as-is - "
            "say \"new paragraph\", \"scratch that\", or \"stop taking notes\" when you're done."
        ),
        data=result,
    )


@tool(
    name="stop_dictation",
    description="Stop taking notes and say where they were saved and how much there is.",
    params={},
    examples=[
        "stop taking notes",
        "stop dictation",
        "that's the end of the note",
        "finish the note",
        "stop writing this down",
    ],
    category="notes",
)
def stop_dictation() -> ToolResult:
    _session = session_now()
    if not dictation.active(_session):
        return ToolResult(ok=True, summary="I wasn't taking notes.", data={"active": False})
    result = dictation.stop(_session)
    where = result["where"]
    return ToolResult(
        ok=True,
        summary=(
            f"Stopped. {result['words']} word(s) over {result['lines']} line(s) in {where}"
            + (f" ({result['path']})" if result.get("path") else "") + "."
        ),
        data=result,
    )


@tool(
    name="dictation_status",
    description="Say whether notes are being taken right now, where they are going, and how much.",
    params={},
    examples=[
        "are you still taking notes",
        "where are my notes going",
        "how much have you written down",
        "dictation status",
    ],
    category="notes",
)
def dictation_status() -> ToolResult:
    state = dictation.status(session_now())
    if not state.get("active"):
        return ToolResult(ok=True, summary="I'm not taking notes. Say \"take notes\" to start.",
                          data=state)
    paused = " (paused)" if state.get("paused") else ""
    return ToolResult(
        ok=True,
        summary=(
            f"Taking notes into {state['where']}{paused}: {state['words']} word(s), "
            f"{state['lines']} line(s), {state['minutes']} min so far."
        ),
        data=state,
    )
