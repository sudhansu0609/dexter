"""
Browser control as Dexter tools (roadmap v6, B1).

`open_website` still hands a URL to whatever browser Windows prefers and
forgets about it. These tools drive Dexter's own Chrome window so that a
conversation can continue on the page: open, read, click, type, scroll,
tabs, screenshot.
"""

from typing import Any, Dict, Optional

from browser import BrowserError, browser
from tool_registry import ToolResult, tool


def _guard(fn, *args, **kwargs) -> ToolResult:
    try:
        return fn(*args, **kwargs)
    except BrowserError as exc:
        return ToolResult.fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"Browser error: {str(exc).splitlines()[0][:200]}")


@tool(
    name="browser_open",
    description=(
        "Open a URL, a well-known site (youtube, gmail, github...), a 'youtube <query>' search, "
        "or a Google search in Dexter's own browser window, so later commands can read and click on it."
    ),
    params={
        "target": {"type": "string", "description": "URL, site name, or search words"},
        "new_tab": {"type": "boolean", "description": "Open in a new tab instead of the current one", "required": False},
    },
    examples=[
        "go to github.com",
        "open youtube in the browser",
        "search youtube for bhangarh fort documentary",
        "browse to the wikipedia page for chandragupta maurya",
        "open my youtube studio",
        "google best budget microphone for voiceover in the browser",
        "open a new tab with reddit",
    ],
    category="browser",
)
def browser_open(target: str, new_tab: bool = False) -> ToolResult:
    def _do():
        info = browser.open(target, new_tab=bool(new_tab))
        return ToolResult(ok=True, summary=f"Opened {info.get('title') or info.get('url')}.", data=info)

    return _guard(_do)


@tool(
    name="browser_read_page",
    description="Read the current page in Dexter's browser: title, main text, headings, links and form fields. Use it to answer questions about what is on screen.",
    params={"max_chars": {"type": "integer", "description": "How much text to read (default 6000)", "required": False}},
    examples=[
        "read this page",
        "what does this page say",
        "summarize the page that's open in the browser",
        "what's on the screen in the browser",
        "what are the headings on this page",
        "read the article",
    ],
    category="browser",
)
def browser_read_page(max_chars: int = 6000) -> ToolResult:
    def _do():
        info = browser.read(max_chars=int(max_chars or 6000))
        text = info.get("text", "")
        preview = text[:600].replace("\n", " ")
        return ToolResult(ok=True, summary=f"{info.get('title')}: {preview}", data=info)

    return _guard(_do)


@tool(
    name="browser_click",
    description="Click a button, link, tab or element on the current page by its visible text or label.",
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
        info = browser.click(target)
        return ToolResult(ok=True, summary=f"Clicked '{info['clicked']}'. Now on {info.get('title') or info.get('url')}.", data=info)

    return _guard(_do)


@tool(
    name="browser_type",
    description="Type text into a field on the current page (by its label or placeholder, or the first text field) and optionally press Enter.",
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
        "search this page for chandragupta",
    ],
    category="browser",
)
def browser_type(text: str, into: Optional[str] = None, submit: bool = False) -> ToolResult:
    def _do():
        info = browser.type_text(text, into=into, submit=bool(submit))
        tail = " and pressed Enter" if submit else ""
        return ToolResult(ok=True, summary=f"Typed into {info['into']}{tail}.", data=info)

    return _guard(_do)


@tool(
    name="browser_scroll",
    description="Scroll the current page down, up, to the top or to the bottom.",
    params={"direction": {"type": "string", "description": "down, up, top, bottom", "required": False}},
    examples=["scroll down", "scroll up a bit", "go to the top of the page", "scroll to the bottom", "page down"],
    category="browser",
)
def browser_scroll(direction: str = "down") -> ToolResult:
    def _do():
        info = browser.scroll(direction)
        return ToolResult(ok=True, summary=f"Scrolled {info['scrolled']}.", data=info)

    return _guard(_do)


@tool(
    name="browser_back",
    description="Go back to the previous page in Dexter's browser.",
    examples=["go back", "back to the previous page", "browser back", "take me back a page"],
    category="browser",
)
def browser_back() -> ToolResult:
    def _do():
        info = browser.back()
        return ToolResult(ok=True, summary=f"Back on {info.get('title') or info.get('url')}.", data=info)

    return _guard(_do)


@tool(
    name="browser_tabs",
    description="List the tabs open in Dexter's browser.",
    examples=["what tabs are open", "list my browser tabs", "show the open tabs", "which tabs do I have"],
    category="browser",
)
def browser_tabs() -> ToolResult:
    def _do():
        tabs = browser.tabs()
        if not tabs:
            return ToolResult(ok=True, summary="No tabs are open.", data={"tabs": []})
        lines = [f"{t['index']}. {t['title'] or t['url']}{' (current)' if t['current'] else ''}" for t in tabs]
        return ToolResult(ok=True, summary=f"{len(tabs)} tab(s): " + "; ".join(lines), data={"tabs": tabs})

    return _guard(_do)


@tool(
    name="browser_switch_tab",
    description="Switch to another tab by number, by part of its title or URL, or 'next'/'previous'.",
    params={"which": {"type": "string", "description": "Tab number, title words, 'next' or 'previous'"}},
    examples=["switch to the youtube tab", "next tab", "go to tab 2", "previous tab", "jump to the gmail tab"],
    category="browser",
)
def browser_switch_tab(which: str) -> ToolResult:
    def _do():
        info = browser.switch_tab(which)
        return ToolResult(ok=True, summary=f"Switched to tab {info['index']}: {info.get('title') or info.get('url')}.", data=info)

    return _guard(_do)


@tool(
    name="browser_close_tab",
    description="Close the current tab in Dexter's browser.",
    examples=["close this tab", "close the tab", "shut the current tab"],
    category="browser",
)
def browser_close_tab() -> ToolResult:
    def _do():
        info = browser.close_tab()
        return ToolResult(ok=True, summary=f"Closed '{info['closed']}'; {info['remaining']} tab(s) left.", data=info)

    return _guard(_do)


@tool(
    name="browser_screenshot",
    description="Save a screenshot of the current page in Dexter's browser and return its path.",
    params={"full_page": {"type": "boolean", "description": "Capture the whole scrollable page", "required": False}},
    examples=["screenshot this page", "take a screenshot of the browser", "capture the whole page", "grab a picture of this tab"],
    category="browser",
)
def browser_screenshot(full_page: bool = False) -> ToolResult:
    def _do():
        info = browser.screenshot(full_page=bool(full_page))
        return ToolResult(ok=True, summary=f"Saved a screenshot of {info.get('title')} to {info['path']}.", data=info)

    return _guard(_do)
