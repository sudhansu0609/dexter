"""
Page-level tools: search here, pick the nth thing, move text around (v13.2).

The owner's list, each line a tool:

  "search a video by its name in current tab"      -> search_here
  "select the first video / profile / link"         -> open_result / click_nth
  "search anything on youtube or google and click
   the links"                                       -> search_and_open
  "open comment, like, subscribe any video"         -> youtube_page_action
  "copy a section of text as I say and paste it in
   any other tab at any other field I say"          -> copy_text / paste_text

Each one names what it did or, on a miss, what it could see - because "it says it
is doing it but it does nothing" is the failure these replace, and the cure for
that is a reply that can only be written when something actually happened.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import page_control as page
import youtube_control as yt
from tool_registry import ToolResult, tool


def _guard(fn) -> ToolResult:
    try:
        return fn()
    except (page.PageError, yt.YouTubeError) as exc:
        return ToolResult.fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool reports, never raises
        return ToolResult.fail(f"That didn't work: {str(exc).splitlines()[0][:200]}")


@tool(
    name="search_here",
    description=(
        "Search in the browser tab that is already open, instead of opening a new "
        "one. Use for 'search for this in the current tab'. Names a site "
        "(youtube, google, images, maps, wikipedia, github) or searches the site "
        "already open."
    ),
    params={
        "query": {"type": "string", "description": "What to search for"},
        "site": {"type": "string", "description": "Where: youtube, google, images, maps…",
                 "required": False},
    },
    examples=[
        "search for lofi beats in this tab",
        "search this tab for rust tutorials",
        "search youtube for that song in the current tab",
        "look up the weather here",
        "search google for the best mic in this tab",
    ],
    category="browser",
)
def search_here(query: str, site: Optional[str] = None) -> ToolResult:
    def _do():
        info = page.search_here(query, site=site)
        return ToolResult(
            ok=True,
            summary=f"Searched {info['site']} for '{info['searched']}' in the tab you were in"
                    + (f" - now showing {info['title']}." if info.get("title") else "."),
            data=info,
        )

    return _guard(_do)


@tool(
    name="open_result",
    description=(
        "Click a result on the page the user is looking at: the nth one, or the one "
        "whose title they said. Works on a Google results page, YouTube, or any "
        "page of links."
    ),
    params={
        "position": {"type": "integer", "description": "1 for the first result, 2 for the second",
                     "required": False},
        "title": {"type": "string", "description": "Words from the result's title",
                  "required": False},
        "kind": {"type": "string", "description": "result, link, video, item",
                 "required": False},
    },
    examples=[
        "open the first result",
        "click the first link",
        "select the second result",
        "open the third link on this page",
        "click the result about wgpu",
        "open the one called lofi hip hop radio",
    ],
    category="browser",
)
def open_result(position: Optional[int] = None, title: Optional[str] = None,
                kind: str = "result") -> ToolResult:
    def _do():
        info = page.open_result(position=position, title=title, kind=kind or "result")
        return ToolResult(
            ok=True,
            summary=f"Opened '{str(info['opened'])[:80]}' - {info.get('route', 'clicked it')}.",
            data={k: v for k, v in info.items() if k != "element"},
        )

    return _guard(_do)


@tool(
    name="select_nth",
    description=(
        "Select the nth thing of a kind in the window the user is looking at - a "
        "profile card, an option, a row, a list item, a suggestion. Use when there "
        "is no label to click, only a position ('select the first profile')."
    ),
    params={
        "what": {"type": "string", "description": "profile, option, item, row, link, card…"},
        "position": {"type": "integer", "description": "1 for the first", "required": False},
        "window": {"type": "string", "description": "App or window title", "required": False},
    },
    examples=[
        "select the first profile",
        "pick the first profile in chrome",
        "choose the second option",
        "select the first item in the list",
        "click the first row",
        "select the third suggestion",
    ],
    category="desktop",
)
def select_nth(what: str, position: int = 1, window: Optional[str] = None) -> ToolResult:
    def _do():
        info = page.click_nth(what, position=int(position or 1), window=window)
        return ToolResult(
            ok=True,
            summary=f"Selected '{str(info['clicked'])[:70]}' "
                    f"(the {page._ordinal(info['position'])} {what}) in {info['window']}.",
            data=info,
        )

    return _guard(_do)


@tool(
    name="search_and_open",
    description=(
        "Search the web (or YouTube) and open one of the results in one go - the "
        "first by default. Use for 'google X and open the first link'."
    ),
    params={
        "query": {"type": "string", "description": "What to search for"},
        "site": {"type": "string", "description": "google (default), youtube, images…",
                 "required": False},
        "position": {"type": "integer", "description": "Which result to open (default 1)",
                     "required": False},
    },
    examples=[
        "google best mic for voiceover and open the first link",
        "search youtube for lofi and play the first one",
        "look up wgpu tutorials and open the top result",
        "find the weather in Delhi and open the first link",
    ],
    category="browser",
)
def search_and_open(query: str, site: Optional[str] = None, position: int = 1) -> ToolResult:
    def _do():
        where = (site or "").strip().lower()
        index = max(1, int(position or 1))
        if where == "youtube" or yt.on_youtube() and not where:
            info = yt.open_video(query=query, index=index, prefer_page=False)
            return ToolResult(ok=True,
                              summary=f"Playing '{str(info['opened'])[:80]}' - {info['route']}.",
                              data=info)
        searched = page.search_here(query, site=where or "google")
        opened = page.open_result(position=index)
        return ToolResult(
            ok=True,
            summary=(f"Searched {searched['site']} for '{searched['searched']}' and opened "
                     f"'{str(opened['opened'])[:70]}'."),
            data={"searched": searched, "opened": {k: v for k, v in opened.items()
                                                   if k != "element"}},
        )

    return _guard(_do)


@tool(
    name="youtube_page_action",
    description=(
        "Press one of the buttons around a YouTube video: like, dislike, subscribe, "
        "unsubscribe, comments, share, save to a playlist, show the description."
    ),
    params={
        "action": {"type": "string",
                   "description": "like, dislike, subscribe, unsubscribe, comments, share, "
                                  "save, description"},
    },
    examples=[
        "like this video",
        "subscribe to this channel",
        "open the comments",
        "show me the comments",
        "save this to a playlist",
        "share this video",
        "show the description",
        "unsubscribe from this channel",
    ],
    category="media",
)
def youtube_page_action(action: str) -> ToolResult:
    def _do():
        info = yt.page_action(action)
        return ToolResult(ok=True, summary=f"{info['action'].capitalize()}: {info['how']}.",
                          data=info)

    return _guard(_do)


@tool(
    name="copy_text",
    description=(
        "Copy text to the clipboard from what the user is looking at: the current "
        "selection, everything on the page, or a named field's contents."
    ),
    params={
        "what": {"type": "string",
                 "description": "'selection' (default), 'all', or the name of a field",
                 "required": False},
        "window": {"type": "string", "description": "App or window title", "required": False},
    },
    examples=[
        "copy this",
        "copy that paragraph",
        "copy the selected text",
        "copy everything on this page",
        "copy what's in the prompt box",
        "copy this section of text",
    ],
    category="desktop",
)
def copy_text(what: str = "selection", window: Optional[str] = None) -> ToolResult:
    def _do():
        info = page.copy_here(what or "selection", window=window)
        text = str(info.get("text") or "")
        preview = text[:200].replace("\n", " ") + ("…" if len(text) > 200 else "")
        return ToolResult(
            ok=True,
            summary=f"Copied {info.get('length', len(text))} characters: {preview}",
            data=info,
        )

    return _guard(_do)


@tool(
    name="paste_text",
    description=(
        "Paste the clipboard into a field, optionally switching to another browser "
        "tab first. Use for 'paste it into the Gemini prompt in the other tab'. "
        "Give 'text' to type something specific instead of the clipboard."
    ),
    params={
        "field": {"type": "string", "description": "The field's name or placeholder, e.g. "
                                                   "'Search', 'Ask Gemini', 'Message'",
                  "required": False},
        "tab": {"type": "string", "description": "Words from the tab's title to switch to first",
                "required": False},
        "text": {"type": "string", "description": "Text to type instead of the clipboard",
                 "required": False},
        "submit": {"type": "boolean", "description": "Press Enter afterwards", "required": False},
    },
    examples=[
        "paste it in the gemini tab",
        "paste that into the search box",
        "paste it into the prompt in the other tab and send it",
        "paste this in the message field",
        "paste it here",
    ],
    category="desktop",
)
def paste_text(field: Optional[str] = None, tab: Optional[str] = None,
               text: Optional[str] = None, submit: bool = False) -> ToolResult:
    def _do():
        info = page.paste_there(field=field, tab=tab, text=text, submit=bool(submit))
        where = f" in the {info['tab']} tab" if info.get("tab") else ""
        tail = " and pressed Enter" if info.get("submitted") else ""
        return ToolResult(ok=True,
                          summary=f"Pasted into {info['field']}{where}{tail}.", data=info)

    return _guard(_do)


@tool(
    name="page_results",
    description=(
        "List what the page in front is offering - its results, links or videos - "
        "numbered, so one can be chosen by position."
    ),
    params={
        "kind": {"type": "string", "description": "result, link, video, item, profile",
                 "required": False},
        "limit": {"type": "integer", "description": "How many (default 10)", "required": False},
    },
    examples=[
        "what's on this page",
        "list the results",
        "what links are on this page",
        "read me the options",
        "what profiles are there",
    ],
    category="browser",
)
def page_results(kind: str = "result", limit: int = 10) -> ToolResult:
    def _do():
        found = page.results(limit=max(1, min(int(limit or 10), 25)), kind=kind or "result")
        if not found:
            return ToolResult.fail(
                f"I can't see any {kind}s on this page. Some pages publish nothing to "
                "Windows; tell me the exact text and I'll click that."
            )
        lines = [f"{row['position']}. {row['title'][:70]}" for row in found]
        return ToolResult(
            ok=True,
            summary=f"{len(found)} {kind}(s) on this page: " + "; ".join(lines),
            data={"results": [{"position": r["position"], "title": r["title"],
                               "kind": r["kind"]} for r in found]},
        )

    return _guard(_do)


# ───────────────────────── exact control, when it is wanted (v13.4) ─────────────────────────


@tool(
    name="precise_browser",
    description=(
        "Turn on exact browser control (Playwright over Chrome's debug port) or "
        "report whether it is on. With it, clicking the nth result, filling a named "
        "field and reading a selection are exact rather than read off Windows "
        "accessibility names - and Dexter can act without stealing focus."
    ),
    params={
        "action": {"type": "string", "description": "'status' (default), 'start' or 'stop'",
                   "required": False, "enum": ["status", "start", "stop"]},
    },
    examples=[
        "turn on precise browser control",
        "is precise browser control on",
        "start the debuggable chrome",
        "use playwright for the browser",
        "switch to the debug browser",
        "switch to the debug profile",
        "stop precise browser control",
        "go back to my own chrome",
        "stop using the debug browser",
    ],
    category="browser",
)
def precise_browser(action: str = "status") -> ToolResult:
    import browser_cdp

    wanted = (action or "status").strip().lower()

    def _do():
        if wanted == "start":
            info = browser_cdp.launch()
            browser_cdp.set_mode(True)
            where = info.get("profile") or browser_cdp.PROFILE_DIR
            if info.get("already"):
                return ToolResult(
                    ok=True,
                    summary=f"Exact control was already on ({info.get('Browser', 'Chrome')} "
                            f"on {browser_cdp.endpoint()}).",
                    data=info,
                )
            return ToolResult(
                ok=True,
                summary=(
                    f"Exact control is on: {info.get('Browser', 'Chrome')} on "
                    f"{browser_cdp.endpoint()}. It uses its own profile ({where}), so sign "
                    "into Google in that window once and it stays signed in. From now on "
                    "clicking a result, filling a field and reading a selection are exact, "
                    "and I can act without taking your focus. I'm now driving that debug "
                    "Chrome for browser/YouTube actions, not your own window."
                ),
                data=info,
            )
        if wanted == "stop":
            browser_cdp.set_mode(False)
            browser_cdp.session.close()
            return ToolResult(
                ok=True,
                summary="Detached from the debuggable Chrome - I'll go back to driving the "
                        "browser through Windows accessibility. The window itself is still "
                        "open; close it yourself if you want it gone.",
                data=browser_cdp.status(),
            )
        state = browser_cdp.status()
        if not state["enabled"]:
            return ToolResult(ok=True, summary="Exact browser control is switched off "
                                               "(DEXTER_CHROME_CDP=0).", data=state)
        if not state["available"]:
            return ToolResult(
                ok=True,
                summary=(
                    "Exact control is off - I'm driving the browser through Windows "
                    "accessibility, which works on your own Chrome but picks things by "
                    "name. Say \"turn on precise browser control\" and I'll start a "
                    "debuggable Chrome (its own profile; sign in once)."
                ),
                data=state,
            )
        return ToolResult(
            ok=True,
            summary=(
                f"Exact control is on: {state.get('browser', 'Chrome')} on "
                f"{state['endpoint']}, {state.get('tabs', '?')} tab(s), currently "
                f"{state.get('title', 'nothing')}."
            ),
            data=state,
        )

    try:
        return _do()
    except browser_cdp.CdpError as exc:
        return ToolResult.fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"That didn't work: {str(exc).splitlines()[0][:200]}")
