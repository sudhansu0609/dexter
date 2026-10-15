"""
Browser control as Dexter tools (roadmap v6 B1, rebuilt for v11 P1.4).

These tools used to drive one thing: Dexter's own Playwright window on its own
profile. That is why "open youtube" put a second browser on screen, logged into
nothing, and "close this tab" closed a tab you could not see.

From v11 every tool here is **mode-aware**:

    DEXTER_BROWSER_MODE=own         (default) act in the Chrome you use, via
                                    chrome_control: a URL handed to the running
                                    instance, keystrokes to the focused window,
                                    UI Automation to read it
    DEXTER_BROWSER_MODE=playwright   the v6 behaviour, kept for anything that
                                    needs a scriptable page (selectors, full
                                    DOM text, a real page screenshot)

The tool names, arguments and example phrasings are unchanged, so the router and
the model do not have to know which mode is in force.
"""

import re
from typing import Any, Callable, Dict, Optional

import chrome_control
from browser import BrowserError, browser
from desktop_control import DesktopError
from tool_registry import ToolResult, tool


def _own() -> bool:
    return chrome_control.own_mode()


def _extract_section(text: str, section: str, window: int = 1500) -> str:
    """The slice of the page around the first mention of a heading/label.

    "read the ingredients section" is a whole-page read plus a find: locate where
    the section is named and hand back the text from there. Falls back to matching
    any single word of the request so "the summary" still lands on "Summary".
    """
    if not text:
        return ""
    low = text.lower()
    idx = low.find(section.lower())
    if idx < 0:
        for word in (w for w in re.split(r"\s+", section.lower()) if len(w) > 2):
            idx = low.find(word)
            if idx >= 0:
                break
    if idx < 0:
        return ""
    return text[idx: idx + max(200, int(window))].strip()


def _guard(fn: Callable[[], ToolResult]) -> ToolResult:
    try:
        return fn()
    except (BrowserError, chrome_control.ChromeError, DesktopError) as exc:
        return ToolResult.fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool never raises into the turn
        return ToolResult.fail(f"Browser error: {str(exc).splitlines()[0][:200]}")


def _where(info: Dict[str, Any]) -> str:
    """'in Chrome' / 'in the window I acted on' - every result names its window."""
    window = info.get("window") or ""
    return f" in {window}" if window else ""


@tool(
    name="browser_open",
    description=(
        "Open a URL, a well-known site (youtube, gmail, github...), a 'youtube <query>' search, "
        "or a Google search as a new tab in the user's Chrome, so later commands can read and act on it."
    ),
    params={
        "target": {"type": "string", "description": "URL, site name, or search words"},
        "new_tab": {"type": "boolean", "description": "Kept for compatibility; a URL always lands in a new tab", "required": False},
    },
    examples=[
        "go to github.com",
        "open youtube",
        "open gmail",
        "search youtube for bhangarh fort documentary",
        "browse to the wikipedia page for chandragupta maurya",
        "open my youtube studio",
        "google best budget microphone for voiceover",
        "open a new tab with reddit",
    ],
    category="browser",
)
def browser_open(target: str, new_tab: bool = False) -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.open_url(target)
            name = info.get("title") or info.get("url")
            lead = "Opened Chrome on" if info.get("launched") else "Opened"
            return ToolResult(ok=True, summary=f"{lead} {name}.", data=info)
        info = browser.open(target, new_tab=bool(new_tab))
        return ToolResult(ok=True, summary=f"Opened {info.get('title') or info.get('url')}.", data=info)

    return _guard(_do)


@tool(
    name="browser_read_page",
    description=(
        "Read the page in the front Chrome tab: its title, URL and text. "
        "Use it to answer questions about what the user is looking at. Give 'section' "
        "to read just the part around a heading or label the user names."
    ),
    params={
        "max_chars": {"type": "integer", "description": "How much text to read (default 6000)", "required": False},
        "section": {"type": "string",
                    "description": "A heading/label to read around, e.g. 'ingredients', 'summary', 'reviews'",
                    "required": False},
    },
    examples=[
        "read this page",
        "what does this page say",
        "what's on this page",
        "summarize the page that's open",
        "what's on the screen in the browser",
        "read the article",
        "read the ingredients section",
        "what does the summary section say",
    ],
    category="browser",
)
def browser_read_page(max_chars: int = 6000, section: Optional[str] = None) -> ToolResult:
    def _do():
        limit = int(max_chars or 6000)
        info = chrome_control.read_active_tab(limit) if _own() else browser.read(max_chars=limit)
        text = info.get("text") or ""
        title = info.get("title") or info.get("url") or "this tab"

        if section and section.strip():
            excerpt = _extract_section(text, section.strip())
            if not excerpt:
                return ToolResult(
                    ok=False,
                    summary=f"I couldn't find a '{section}' section on {title}.",
                    data={**info, "section": section},
                )
            return ToolResult(
                ok=True,
                summary=f"{title} — '{section}': {excerpt[:700]}" + ("…" if len(excerpt) > 700 else ""),
                data={**info, "section": section, "excerpt": excerpt},
            )

        preview = text[:600].replace("\n", " ")
        if not preview:
            return ToolResult(
                ok=True,
                summary=f"You're on {title} ({info.get('url') or 'no URL read'}). "
                        "I couldn't get any text out of the page.",
                data=info,
            )
        return ToolResult(ok=True, summary=f"{title}: {preview}", data=info)

    return _guard(_do)


@tool(
    name="browser_click",
    description="Click a button, link or control in the front browser tab by its visible text.",
    params={"target": {"type": "string", "description": "The visible text or label of what to click"}},
    examples=[
        "click sign in",
        "click on the first result",
        "press the subscribe button",
        "click the videos tab",
        "hit next",
        "click accept all",
    ],
    category="browser",
)
def browser_click(target: str) -> ToolResult:
    def _do():
        try:
            import browser_cdp
            if browser_cdp.active_mode():
                info = browser_cdp.session.click_text(target)
                return ToolResult(ok=True, summary=f"Clicked '{info['clicked']}'. Now on {info.get('title') or info.get('url')}.", data=info)
        except Exception:
            pass

        if _own():
            info = chrome_control.click(target)
            return ToolResult(ok=True, summary=f"Clicked '{info['clicked']}'{_where(info)}.", data=info)
        info = browser.click(target)
        return ToolResult(
            ok=True,
            summary=f"Clicked '{info['clicked']}'. Now on {info.get('title') or info.get('url')}.",
            data=info,
        )

    return _guard(_do)


@tool(
    name="browser_type",
    description="Type text into the front browser tab (into the field that has focus) and optionally press Enter.",
    params={
        "text": {"type": "string", "description": "What to type"},
        "into": {"type": "string", "description": "Field label or placeholder, e.g. 'search', 'email'", "required": False},
        "submit": {"type": "boolean", "description": "Press Enter afterwards", "required": False},
    },
    examples=[
        "type bhangarh fort in the search box and press enter",
        "enter my email address in the email field",
        "type hello world into the comment box",
        "write 'thanks for watching' in the reply field",
    ],
    category="browser",
)
def browser_type(text: str, into: Optional[str] = None, submit: bool = False) -> ToolResult:
    def _do():
        try:
            import browser_cdp
            if browser_cdp.active_mode():
                info = browser_cdp.session.fill(into or "", text, submit=bool(submit))
                tail = " and pressed Enter" if submit else ""
                target = into or "the focused field"
                return ToolResult(ok=True, summary=f"Typed into {target}{tail}.", data=info)
        except Exception:
            pass

        if _own():
            # A named field is clicked first (UIA), then typed into; without a
            # name the keystrokes go wherever the page already has focus, which
            # is what a person gets after clicking.
            if into:
                chrome_control.click(into)
            info = chrome_control.type_text(text, submit=bool(submit))
            tail = " and pressed Enter" if submit else ""
            target = into or "the focused field"
            info["into"] = target
            return ToolResult(ok=True, summary=f"Typed into {target}{tail}{_where(info)}.", data=info)
        info = browser.type_text(text, into=into, submit=bool(submit))
        tail = " and pressed Enter" if submit else ""
        return ToolResult(ok=True, summary=f"Typed into {info['into']}{tail}.", data=info)

    return _guard(_do)


@tool(
    name="browser_scroll",
    description="Scroll the front browser tab down, up, to the top or to the bottom.",
    params={
        "direction": {"type": "string", "description": "down, up, top, bottom", "required": False},
        "amount": {"type": "integer", "description": "How many screens (default 1)", "required": False},
    },
    examples=["scroll down", "scroll up a bit", "go to the top of the page", "scroll to the bottom", "page down"],
    category="browser",
)
def browser_scroll(direction: str = "down", amount: int = 1) -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.scroll(direction, amount=int(amount or 1))
            return ToolResult(ok=True, summary=f"Scrolled {info['scrolled']}{_where(info)}.", data=info)
        info = browser.scroll(direction)
        return ToolResult(ok=True, summary=f"Scrolled {info['scrolled']}.", data=info)

    return _guard(_do)


@tool(
    name="browser_back",
    description="Go back to the previous page in the front browser tab.",
    examples=["go back", "back to the previous page", "browser back", "take me back a page"],
    category="browser",
)
def browser_back() -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.back()
            return ToolResult(ok=True, summary=f"Went back{_where(info)}.", data=info)
        info = browser.back()
        return ToolResult(ok=True, summary=f"Back on {info.get('title') or info.get('url')}.", data=info)

    return _guard(_do)


@tool(
    name="browser_forward",
    description="Go forward again after going back, in the front browser tab.",
    examples=["go forward", "forward a page", "undo that back", "browser forward"],
    category="browser",
)
def browser_forward() -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.forward()
            return ToolResult(ok=True, summary=f"Went forward{_where(info)}.", data=info)
        return ToolResult.fail("Going forward needs your own Chrome (DEXTER_BROWSER_MODE=own).")

    return _guard(_do)


@tool(
    name="browser_reload",
    description="Reload the page in the front browser tab.",
    params={"hard": {"type": "boolean", "description": "Bypass the cache", "required": False}},
    examples=["reload the page", "refresh this tab", "refresh the page", "reload it", "hard refresh this page"],
    category="browser",
)
def browser_reload(hard: bool = False) -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.reload(hard=bool(hard))
            return ToolResult(ok=True, summary=f"Reloaded {info.get('title') or 'the page'}{_where(info)}.", data=info)
        info = browser.open(browser.tabs()[0]["url"]) if browser.tabs() else {}
        return ToolResult(ok=True, summary="Reloaded the page.", data=info)

    return _guard(_do)


@tool(
    name="browser_tabs",
    description="List the tabs open in the front Chrome window.",
    examples=["what tabs are open", "list my browser tabs", "show the open tabs", "which tabs do I have"],
    category="browser",
)
def browser_tabs() -> ToolResult:
    def _do():
        if _own():
            names = chrome_control.tab_names()
            if not names:
                return ToolResult(
                    ok=True,
                    summary="I couldn't read the tab strip. Is Chrome open?",
                    data={"tabs": []},
                )
            current = chrome_control.tab_title()
            lines = [
                f"{i}. {name}{' (current)' if current and name.startswith(current[:40]) else ''}"
                for i, name in enumerate(names, 1)
            ]
            return ToolResult(ok=True, summary=f"{len(names)} tab(s): " + "; ".join(lines),
                              data={"tabs": names, "current": current})
        tabs = browser.tabs()
        if not tabs:
            return ToolResult(ok=True, summary="No tabs are open.", data={"tabs": []})
        lines = [f"{t['index']}. {t['title'] or t['url']}{' (current)' if t['current'] else ''}" for t in tabs]
        return ToolResult(ok=True, summary=f"{len(tabs)} tab(s): " + "; ".join(lines), data={"tabs": tabs})

    return _guard(_do)


@tool(
    name="browser_switch_tab",
    description="Switch to another tab by number, by part of its title, or 'next'/'previous'.",
    params={"which": {"type": "string", "description": "Tab number, title words, 'next' or 'previous'"}},
    examples=["switch to the youtube tab", "next tab", "go to tab 2", "previous tab", "jump to the gmail tab"],
    category="browser",
)
def browser_switch_tab(which: str) -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.switch_tab(which)
            name = info.get("title") or info.get("which")
            return ToolResult(ok=True, summary=f"Switched to {name}{_where(info)}.", data=info)
        info = browser.switch_tab(which)
        return ToolResult(ok=True, summary=f"Switched to tab {info['index']}: {info.get('title') or info.get('url')}.", data=info)

    return _guard(_do)


@tool(
    name="browser_new_tab",
    description="Open a new, empty tab in the front Chrome window.",
    examples=["open a new tab", "new tab please", "give me a blank tab", "new browser tab"],
    category="browser",
)
def browser_new_tab() -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.new_tab()
            return ToolResult(ok=True, summary=f"New tab{_where(info)}.", data=info)
        info = browser.open("about:blank", new_tab=True)
        return ToolResult(ok=True, summary="New tab.", data=info)

    return _guard(_do)


@tool(
    name="browser_close_tab",
    description="Close the tab that is currently in front.",
    examples=["close this tab", "close the tab", "shut the current tab", "close that tab"],
    category="browser",
)
def browser_close_tab() -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.close_tab()
            closed = info.get("was") or "the tab"
            return ToolResult(ok=True, summary=f"Closed '{closed}'{_where(info)}.", data=info)
        info = browser.close_tab()
        return ToolResult(ok=True, summary=f"Closed '{info['closed']}'; {info['remaining']} tab(s) left.", data=info)

    return _guard(_do)


@tool(
    name="browser_reopen_tab",
    description="Reopen the tab that was just closed.",
    examples=["reopen that tab", "bring back the tab I closed", "undo closing that tab", "reopen the last tab"],
    category="browser",
)
def browser_reopen_tab() -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.reopen_tab()
            return ToolResult(ok=True, summary=f"Reopened the last closed tab{_where(info)}.", data=info)
        return ToolResult.fail("Reopening a closed tab needs your own Chrome (DEXTER_BROWSER_MODE=own).")

    return _guard(_do)


@tool(
    name="browser_find_in_page",
    description="Open Chrome's find bar and search the current page for some words.",
    params={"text": {"type": "string", "description": "What to find on the page"}},
    examples=[
        "find chandragupta on this page",
        "search this page for pricing",
        "look for the word refund on the page",
        "ctrl f for bhangarh",
    ],
    category="browser",
)
def browser_find_in_page(text: str) -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.find_in_page(text)
            return ToolResult(ok=True, summary=f"Searching the page for '{info['searched']}'{_where(info)}.", data=info)
        return ToolResult.fail("Find-in-page needs your own Chrome (DEXTER_BROWSER_MODE=own).")

    return _guard(_do)


@tool(
    name="browser_zoom",
    description="Zoom the current page in, out, or back to 100%.",
    params={"direction": {"type": "string", "description": "in, out or reset", "required": False}},
    examples=["zoom in on this page", "zoom out a bit", "make the text bigger in the browser", "reset the zoom"],
    category="browser",
)
def browser_zoom(direction: str = "in") -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.zoom(direction)
            return ToolResult(ok=True, summary=f"Zoomed {direction}{_where(info)}.", data=info)
        return ToolResult.fail("Zoom needs your own Chrome (DEXTER_BROWSER_MODE=own).")

    return _guard(_do)


@tool(
    name="browser_fullscreen",
    description="Toggle full screen in the browser (F11).",
    examples=["make the browser full screen", "full screen this", "exit full screen", "toggle fullscreen in chrome"],
    category="browser",
)
def browser_fullscreen() -> ToolResult:
    def _do():
        if _own():
            info = chrome_control.fullscreen()
            return ToolResult(ok=True, summary=f"Toggled full screen{_where(info)}.", data=info)
        return ToolResult.fail("Full screen needs your own Chrome (DEXTER_BROWSER_MODE=own).")

    return _guard(_do)


@tool(
    name="browser_screenshot",
    description="Save a picture of the browser window and return its path.",
    params={"full_page": {"type": "boolean", "description": "Capture the whole scrollable page (Dexter's own browser only)", "required": False}},
    examples=["screenshot this page", "take a screenshot of the browser", "capture the whole page", "grab a picture of this tab"],
    category="browser",
)
def browser_screenshot(full_page: bool = False) -> ToolResult:
    def _do():
        if _own():
            import desktop_control as dc

            win = chrome_control.active_chrome()
            if win is None:
                return ToolResult.fail("Chrome isn't open.")
            path = dc.capture_window(win)
            return ToolResult(ok=True, summary=f"Saved a picture of {win.describe()} to {path}.",
                              data={"path": path, "window": win.describe(), "title": chrome_control.tab_title(win)})
        info = browser.screenshot(full_page=bool(full_page))
        return ToolResult(ok=True, summary=f"Saved a screenshot of {info.get('title')} to {info['path']}.", data=info)

    return _guard(_do)
