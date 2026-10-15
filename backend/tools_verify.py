"""
Tools that answer a question instead of listing things (v13.8).

Asked for as: "Dexter is not able to check if Chrome is open or not, neither can it
confirm if a tab or a website is opened or not, it won't be able to verify if a video
is present with a certain title in an opened YouTube."

The capability was all there. What was missing was a tool whose *answer* is yes or no.
Dexter had `browser_tabs`, `list_windows` and `page_results` - lists - and the model
had to turn a list into a verdict, which is where a confident wrong answer comes from.
Each of these replies with the verdict **and how it knows**, and says "I cannot tell
from here" when that is the truth, because an honest maybe is worth more than a
coin-flip yes.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import verify
from tool_registry import ToolResult, tool


def _and_how(state: Dict[str, Any]) -> str:
    """'(exactly, over Chrome's debug port)' - or the honest alternative."""
    how = state.get("how")
    if how == "cdp":
        return "exactly, reading Chrome's own tabs"
    if how == "windows":
        return "from the open windows"
    if how == "processes":
        return "from the process list"
    if how == "accessibility":
        return "from what the page publishes to Windows"
    return "as best I could"


@tool(
    name="is_app_open",
    description=(
        "Check whether an application is actually open right now - Chrome, a browser, "
        "Notepad, any program. Answers yes or no with the windows it can see, and "
        "says when something is running without a visible window. Use this to verify "
        "before acting, or when the user asks whether something is open."
    ),
    params={
        "app": {"type": "string", "required": True,
                "description": "The application, e.g. 'chrome', 'notepad', 'a browser'."},
    },
    examples=[
        "is chrome open",
        "do I have chrome open",
        "is notepad running",
        "check if my browser is open",
        "is there a chrome window open",
    ],
    category="desktop",
)
def is_app_open(app: str) -> ToolResult:
    state = verify.app_open(app)
    name = (app or "").strip()
    if state["verdict"] and state["windows"]:
        titles = "; ".join(w["title"][:60] for w in state["windows"][:3] if w["title"])
        count = len(state["windows"])
        return ToolResult(
            ok=True,
            summary=(f"Yes - {name} is open with {count} window"
                     f"{'s' if count != 1 else ''} ({_and_how(state)})"
                     + (f": {titles}" if titles else "") + "."),
            data=state,
        )
    if state["verdict"]:
        names = ", ".join(str(p["process"]) for p in state["processes"][:3])
        return ToolResult(
            ok=True,
            summary=(f"{name} is running ({names}) but has no window on screen - "
                     "minimised to the tray, or still starting."),
            data=state,
        )
    return ToolResult(ok=True, summary=f"No - {name} is not open.", data=state)


@tool(
    name="is_site_open",
    description=(
        "Check whether a website is open in a browser tab right now - YouTube, Gmail, "
        "a URL, anything. With exact browser control on it reads every tab's address; "
        "without it, it can only see the front tab of each window and says so rather "
        "than guessing."
    ),
    params={
        "site": {"type": "string", "required": True,
                 "description": "The site or URL, e.g. 'youtube', 'gmail', 'github.com'."},
    },
    examples=[
        "is youtube open",
        "do I have youtube open in a tab",
        "is gmail open somewhere",
        "check if github is open",
        "have I got a tab with youtube",
    ],
    category="browser",
)
def is_site_open(site: str) -> ToolResult:
    state = verify.site_open(site)
    name = (site or "").strip()
    if state["verdict"] is True:
        tab = (state["tabs"] or [{}])[0]
        where = tab.get("title") or name
        position = tab.get("position")
        at = f" (tab {position})" if position else ""
        return ToolResult(
            ok=True,
            summary=f"Yes - {name} is open{at}: {str(where)[:80]} ({_and_how(state)}).",
            data=state,
        )
    if state["verdict"] is False:
        tail = (f" I checked {state['checked']} tab(s)." if state.get("how") == "cdp"
                else f" {state.get('why', '')}".rstrip())
        return ToolResult(ok=True, summary=f"No - {name} is not open.{tail}", data=state)
    # The honest maybe, with the way to turn it into a yes or a no.
    return ToolResult(
        ok=True,
        summary=(f"I can't tell whether {name} is open: {state.get('why', '')}. "
                 "Say \"start precise browser\" and I can read every tab."),
        data=state,
    )


@tool(
    name="is_on_page",
    description=(
        "Check whether something is on the page the user is looking at - a video with "
        "a certain title, a link, a button, a result. Answers yes or no, and when it "
        "is there, which numbered item it is, so it can then be opened. Use this to "
        "verify a video exists on an open YouTube before playing it."
    ),
    params={
        "text": {"type": "string", "required": True,
                 "description": "Words from the title to look for."},
        "kind": {"type": "string", "required": False,
                 "description": "What sort of thing: video, link, result, button."},
    },
    examples=[
        "is there a video called lofi hip hop on this page",
        "can you see a video about rust tutorials",
        "check if there's a link to the docs on this page",
        "is the subscribe button on this page",
        "do you see anything about microphones here",
    ],
    category="browser",
)
def is_on_page(text: str, kind: str = "") -> ToolResult:
    state = verify.on_page(text, kind=kind or "")
    what = (text or "").strip()
    if state["verdict"]:
        first = state["matches"][0]
        position = first.get("position")
        others = len(state["matches"]) - 1
        extra = f" (and {others} more like it)" if others > 0 else ""
        return ToolResult(
            ok=True,
            summary=(f"Yes - {'number ' + str(position) + ' is ' if position else ''}"
                     f"\"{str(first.get('title'))[:80]}\"{extra}. "
                     f"Checked {state['checked']} item(s), {_and_how(state)}."),
            data=state,
        )
    if state["verdict"] is None:
        return ToolResult(
            ok=False,
            summary=f"I couldn't read the page to check: {state.get('why', 'nothing to read')}.",
            data=state,
        )
    seen = "; ".join(t[:40] for t in state.get("titles", [])[:4] if t)
    return ToolResult(
        ok=True,
        summary=(f"No - nothing matching \"{what}\" in the {state['checked']} item(s) "
                 f"I can see{' (' + seen + '…)' if seen else ''}."),
        data=state,
    )


@tool(
    name="what_page_is_this",
    description=(
        "Say which page the user is looking at: its title, its address and which site "
        "it is. Use to confirm a navigation actually landed, or to resolve 'this page'."
    ),
    params={},
    examples=[
        "what page am I on",
        "which site is this",
        "what's open in front of me",
        "did that page open",
    ],
    category="browser",
)
def what_page_is_this() -> ToolResult:
    state = verify.where()
    if not (state.get("title") or state.get("url")):
        return ToolResult(
            ok=False,
            summary="I can't see a browser page in front - no Chrome window is active.",
            data=state,
        )
    url = state.get("url") or ""
    return ToolResult(
        ok=True,
        summary=(f"{state.get('title') or 'Untitled'}"
                 + (f" - {url[:90]}" if url else "")
                 + f" ({_and_how(state)})."),
        data=state,
    )
