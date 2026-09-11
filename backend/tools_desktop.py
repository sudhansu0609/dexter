"""
The minute stuff, as tools (roadmap v11, P2).

"press control s", "type hello world", "scroll down", "click save", "switch to
code", "minimize this", "snap chrome to the left", "close notepad" - the small
physical things you do a hundred times a day and could not ask Dexter for.

Each tool is a thin honest wrapper over `desktop_control`: it names the window it
acted on, it refuses rather than guesses when focus cannot be confirmed, and only
`kill_app` is destructive - which is why it is the one tool here that asks first.
"""

from typing import Any, Callable, Dict, Optional

import desktop_control as dc
from tool_registry import ToolResult, tool


def _guard(fn: Callable[[], ToolResult]) -> ToolResult:
    try:
        return fn()
    except dc.DesktopError as exc:
        return ToolResult.fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool reports, never raises
        return ToolResult.fail(f"That didn't work: {str(exc).splitlines()[0][:200]}")


def _in(info: Dict[str, Any]) -> str:
    window = info.get("window") or ""
    return f" in {window}" if window else ""


@tool(
    name="press_keys",
    description=(
        "Press a keyboard shortcut in the window the user is working in, e.g. 'ctrl+s', "
        "'alt+tab', 'f5'. Say the keys, not what you hope they do."
    ),
    params={
        "keys": {"type": "string", "description": "The combination, e.g. 'ctrl+s' or 'control shift p'"},
        "window": {"type": "string", "description": "Optional app or window title to send them to", "required": False},
    },
    examples=[
        "press control s",
        "hit ctrl shift p",
        "press escape",
        "press f5",
        "send control z",
        "press enter for me",
        "press alt f4 in notepad",
    ],
    category="desktop",
)
def press_keys(keys: str, window: Optional[str] = None) -> ToolResult:
    def _do():
        info = dc.press_keys(keys, window=window)
        return ToolResult(ok=True, summary=f"Pressed {info['keys']}{_in(info)}.", data=info)

    return _guard(_do)


@tool(
    name="type_text",
    description=(
        "Type a sentence into the window the user is working in, as if typed on the keyboard. "
        "Set submit to press Enter afterwards."
    ),
    params={
        "text": {"type": "string", "description": "The text to type"},
        "submit": {"type": "boolean", "description": "Press Enter after typing", "required": False},
        "window": {"type": "string", "description": "Optional app or window title to type into", "required": False},
        "allow_secret": {"type": "boolean", "description": "Only when the user insists: type something that looks like a password", "required": False},
    },
    examples=[
        "type hello world",
        "type out 'thanks for watching' for me",
        "write my email address here",
        "type this into the document: the render finished at 2am",
        "type lo-fi beats and press enter",
    ],
    category="desktop",
)
def type_text(
    text: str,
    submit: bool = False,
    window: Optional[str] = None,
    allow_secret: bool = False,
) -> ToolResult:
    def _do():
        info = dc.type_text(text, submit=bool(submit), window=window, allow_secret=bool(allow_secret))
        tail = " and pressed Enter" if info.get("submitted") else ""
        return ToolResult(ok=True, summary=f"Typed it{tail}{_in(info)}.", data=info)

    return _guard(_do)


@tool(
    name="scroll_window",
    description="Scroll the window the user is working in (or a named one) up, down, to the top or to the bottom.",
    params={
        "direction": {"type": "string", "description": "down, up, top or bottom", "required": False,
                      "enum": ["down", "up", "top", "bottom"]},
        "amount": {"type": "integer", "description": "How many screens (default 1)", "required": False},
        "window": {"type": "string", "description": "Optional app or window title", "required": False},
    },
    examples=[
        "scroll down in this window",
        "scroll up in notepad",
        "jump to the bottom of this window",
        "scroll down three screens",
        "go to the top of this document",
    ],
    category="desktop",
)
def scroll_window(direction: str = "down", amount: int = 1, window: Optional[str] = None) -> ToolResult:
    def _do():
        info = dc.scroll(direction, amount=int(amount or 1), window=window)
        times = f" x{info['times']}" if info.get("times", 1) > 1 else ""
        return ToolResult(ok=True, summary=f"Scrolled {info['scrolled']}{times}{_in(info)}.", data=info)

    return _guard(_do)


@tool(
    name="click_text",
    description=(
        "Click a button, menu item or control by the text on it, in the window the user is working in. "
        "Uses Windows accessibility names, so no mouse hunting."
    ),
    params={
        "text": {"type": "string", "description": "The visible text of the control, e.g. 'Save'"},
        "window": {"type": "string", "description": "Optional app or window title", "required": False},
    },
    examples=[
        "click save in this app",
        "click the ok button",
        "press cancel in that dialog",
        "click don't save",
        "click the export button in premiere",
    ],
    category="desktop",
)
def click_text(text: str, window: Optional[str] = None) -> ToolResult:
    def _do():
        info = dc.click_text(text, window=window)
        return ToolResult(ok=True, summary=f"Clicked '{info['clicked']}'{_in(info)}.", data=info)

    return _guard(_do)


@tool(
    name="read_screen_text",
    description=(
        "Read the text of the window the user is working in (or a named one) through Windows "
        "accessibility. Use it to answer 'what does this window say'."
    ),
    params={
        "max_chars": {"type": "integer", "description": "Character budget (default 4000)", "required": False},
        "window": {"type": "string", "description": "Optional app or window title", "required": False},
    },
    examples=[
        "read this window",
        "what does this window say",
        "read what's on screen",
        "what text is in the active window",
        "read the dialog that just appeared",
    ],
    category="desktop",
)
def read_screen_text(max_chars: int = 4000, window: Optional[str] = None) -> ToolResult:
    def _do():
        info = dc.read_window_text(max_chars=int(max_chars or 4000), window=window)
        text = info.get("text") or ""
        if not text:
            return ToolResult(ok=True, summary=f"{info['window']} exposes no readable text.", data=info)
        preview = text[:600].replace("\n", " ")
        return ToolResult(ok=True, summary=f"{info['window']}: {preview}", data=info)

    return _guard(_do)


@tool(
    name="switch_to",
    description="Bring an already-open app or window to the front, and confirm it actually came forward.",
    params={"app": {"type": "string", "description": "App or part of a window title, e.g. 'code', 'chrome'"}},
    examples=[
        "switch to code",
        "flip to chrome",
        "jump to premiere",
        "bring explorer to the front",
        "switch back to notepad",
    ],
    category="desktop",
)
def switch_to(app: str) -> ToolResult:
    def _do():
        win = dc.resolve_window(app)
        if dc.is_self(win):
            return ToolResult.fail("That's Dexter's own window - name another app.")
        if not dc.focus(win):
            return ToolResult.fail(
                f"I asked for {win.describe()} but Windows kept the focus elsewhere, "
                "so I've not sent anything to it."
            )
        return ToolResult(ok=True, summary=f"Switched to {win.describe()}.",
                          data={"window": win.describe(), "app": win.app, "title": win.title})

    return _guard(_do)


_ARRANGED = {
    "minimize": "Minimized",
    "maximize": "Maximized",
    "restore": "Restored",
    "snap_left": "Snapped to the left:",
    "snap_right": "Snapped to the right:",
    "close": "Asked to close:",
}


@tool(
    name="arrange_window",
    description=(
        "Minimize, maximize, restore, snap left, snap right or close a window. "
        "With no window named it acts on the one the user is working in."
    ),
    params={
        "action": {"type": "string", "description": "minimize, maximize, restore, snap_left, snap_right or close",
                   "enum": list(dc.WINDOW_ACTIONS)},
        "window": {"type": "string", "description": "Optional app or window title", "required": False},
    },
    examples=[
        "minimize this",
        "maximize this window",
        "snap chrome to the left",
        "put code on the right half",
        "restore that window",
        "minimize everything except chrome",
    ],
    category="desktop",
)
def arrange_window(action: str, window: Optional[str] = None) -> ToolResult:
    def _do():
        info = dc.arrange(action, window=window)
        said = _ARRANGED.get(info["action"], info["action"])
        return ToolResult(ok=True, summary=f"{said} {info['window']}.", data=info)

    return _guard(_do)


@tool(
    name="close_app",
    description=(
        "Close an application: every window of it, politely, so the app can prompt about unsaved work. "
        "Use this for 'close chrome' or 'quit notepad'."
    ),
    params={"name": {"type": "string", "description": "App name or part of a window title"}},
    examples=[
        "close notepad",
        "close chrome",
        "quit spotify",
        "shut down discord",
        "close the browser",
        "close explorer",
    ],
    category="desktop",
)
def close_app(name: str) -> ToolResult:
    def _do():
        info = dc.close_app(name)
        if info["remaining"]:
            return ToolResult(
                ok=True,
                summary=(
                    f"Asked {info['app']} to close {info['asked']} window(s); "
                    f"{info['remaining']} still open - it's probably asking about unsaved work."
                ),
                data=info,
            )
        word = "window" if info["asked"] == 1 else "windows"
        return ToolResult(ok=True, summary=f"Closed {info['app']} ({info['asked']} {word}).", data=info)

    return _guard(_do)


def _kill_prompt(args: Dict[str, Any]) -> str:
    name = args.get("name") or "that app"
    return (
        f"Force-stopping {name} loses anything unsaved - it gets no chance to ask. "
        "Say yes and I'll do it."
    )


@tool(
    name="kill_app",
    description=(
        "Force-stop an application that will not close. Destructive: unsaved work is lost. "
        "Only use when the user says kill, force quit, or it is frozen."
    ),
    params={"name": {"type": "string", "description": "App name"}},
    examples=[
        "kill chrome",
        "force quit premiere",
        "premiere is frozen, kill it",
        "force stop discord",
    ],
    confirm=True,
    category="desktop",
)
def kill_app(name: str) -> ToolResult:
    def _do():
        info = dc.kill_app(name)
        return ToolResult(ok=True, summary=f"Force-stopped {info['app']} ({info['killed']} process(es)).", data=info)

    return _guard(_do)


kill_app._dexter_confirm_prompt = _kill_prompt


@tool(
    name="list_windows",
    description="List the open windows grouped by the application they belong to.",
    examples=[
        "which apps have windows open",
        "how many windows do I have per app",
        "list my windows by application",
        "how many chrome windows are open",
        "show every window with its app",
    ],
    category="desktop",
)
def list_windows() -> ToolResult:
    def _do():
        rows = dc.grouped_windows()
        if not rows:
            return ToolResult(ok=True, summary="Nothing is open that I can see.", data={"apps": []})
        parts = [f"{row['app']} ({row['count']})" for row in rows]
        return ToolResult(ok=True, summary=f"{len(rows)} app(s) with windows: " + ", ".join(parts),
                          data={"apps": rows})

    return _guard(_do)


@tool(
    name="app_shortcut",
    description=(
        "Do a named thing the way the app in front expects it: 'new tab', 'new file', 'find', "
        "'toggle sidebar', 'command palette', 'next track'. Dexter knows the per-app shortcuts."
    ),
    params={
        "action": {"type": "string", "description": "What to do, e.g. 'new tab', 'save all', 'toggle sidebar'"},
        "window": {"type": "string", "description": "Optional app or window title", "required": False},
    },
    examples=[
        "new tab",
        "open a new file",
        "toggle the sidebar",
        "open the command palette",
        "save all",
        "find in this app",
    ],
    category="desktop",
)
def app_shortcut(action: str, window: Optional[str] = None) -> ToolResult:
    def _do():
        info = dc.run_recipe(action, window=window)
        how = "" if info["source"] == "app" else " (the usual Windows shortcut)"
        return ToolResult(ok=True, summary=f"{info['action'].capitalize()}: {info['keys']}{_in(info)}{how}.", data=info)

    return _guard(_do)


@tool(
    name="desktop_status",
    description="What Dexter can and cannot do on this desktop right now: which helper packages are present.",
    examples=[
        "can you control my windows",
        "is desktop control working",
        "check your desktop hands",
        "do you have keyboard control",
    ],
    category="desktop",
)
def desktop_status() -> ToolResult:
    import chrome_control

    state = dc.availability()
    chrome = chrome_control.availability()
    missing = [name for name, ok in state.items() if not ok]
    summary = (
        f"Browser mode: {chrome['mode']}. Chrome: "
        f"{'found' if chrome.get('chrome') else 'not found'}"
        f"{', running' if chrome.get('running') else ''}. "
        + ("Everything's available." if not missing else f"Missing: {', '.join(missing)}.")
    )
    return ToolResult(ok=True, summary=summary, data={"desktop": state, "chrome": chrome})
