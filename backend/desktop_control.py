"""
Hands for the desktop (roadmap v11, P2).

Dexter could open an app and read its title, and that was the whole repertoire:
no way to press a key, type a sentence, scroll, click a button by its name, or
put a window where you want it. So "press control s", "type hello world",
"scroll down", "click save", "switch to code", "snap chrome to the left" and
"close notepad" all did nothing. This module is the missing hand.

Three rules shape every function here, and they come from D4 in the plan:

  * Keys go only to the window that was *named*, or to the one in focus. Focus
    is taken deliberately and then **verified**; if something else grabbed the
    foreground in between, the keystroke is refused rather than sprayed at
    whatever happens to be there.
  * Dexter never force-closes anything on "close". WM_CLOSE lets the app show
    its own unsaved-work prompt. Only `kill_app` terminates, and the tool that
    calls it asks first.
  * Dexter's own window is never the target of "this window". You are looking
    at Dexter when you type to Dexter, so "close this" would close Dexter.

Everything that touches Windows goes through a one-line helper (`_enum_windows`,
`_send_keys`, `_post_close`, `_uia_window`, ...). That is what lets the tests
drive the whole module with fakes: no test in this repo may move a real mouse,
send a real keystroke or close a real window.
"""

import dataclasses
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# ───────────────────────── optional packages ─────────────────────────


def _try(module: str):
    """Import or None. Following windows_tools: a partial install still works."""
    try:
        return __import__(module)
    except Exception:
        return None


_gw = _try("pygetwindow")
_psutil = _try("psutil")
_keyboard = _try("keyboard")

try:
    import win32con as _win32con  # type: ignore
    import win32gui as _win32gui  # type: ignore
    import win32process as _win32process  # type: ignore
except Exception:  # pragma: no cover - pywin32 missing
    _win32con = _win32gui = _win32process = None  # type: ignore

_pywinauto_desktop: Any = None
_pywinauto_failed = False


def _uia_desktop():
    """pywinauto's UIA backend costs about a second to import, so it is paid for
    on the first *read* rather than on every `import desktop_control`."""
    global _pywinauto_desktop, _pywinauto_failed
    if _pywinauto_desktop is not None or _pywinauto_failed:
        return _pywinauto_desktop
    try:
        from pywinauto import Desktop  # type: ignore

        _pywinauto_desktop = Desktop(backend="uia")
    except Exception:
        _pywinauto_failed = True
        _pywinauto_desktop = None
    return _pywinauto_desktop


class DesktopError(RuntimeError):
    """Something Dexter should say out loud, not a stack trace."""


# ───────────────────────── windows ─────────────────────────

SW_MAXIMIZE = 3
SW_SHOWNOACTIVATE = 4
SW_MINIMIZE = 6
SW_RESTORE = 9

#: Spoken names for processes, so "switch to code" and "close the browser" work.
APP_ALIASES: Dict[str, str] = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "browser": "chrome",
    "the browser": "chrome",
    "edge": "edge",
    "msedge": "edge",
    "microsoft edge": "edge",
    "firefox": "firefox",
    "code": "code",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "editor": "code",
    "explorer": "explorer",
    "file explorer": "explorer",
    "files": "explorer",
    "spotify": "spotify",
    "notepad": "notepad",
    "discord": "discord",
    "premiere": "premiere",
    "premiere pro": "premiere",
    "adobe premiere pro": "premiere",
    "photoshop": "photoshop",
    "adobe photoshop": "photoshop",
    "terminal": "terminal",
    "powershell": "powershell",
    "obs": "obs",
}

#: Which image name each alias lives in. Several apps answer to more than one.
APP_PROCESSES: Dict[str, Tuple[str, ...]] = {
    "chrome": ("chrome.exe",),
    "edge": ("msedge.exe",),
    "firefox": ("firefox.exe",),
    "code": ("code.exe",),
    "explorer": ("explorer.exe",),
    "spotify": ("spotify.exe",),
    "notepad": ("notepad.exe",),
    "discord": ("discord.exe",),
    "premiere": ("adobe premiere pro.exe", "premiere.exe"),
    "photoshop": ("photoshop.exe",),
    "terminal": ("windowsterminal.exe", "wt.exe"),
    "powershell": ("powershell.exe", "pwsh.exe"),
    "obs": ("obs64.exe", "obs.exe"),
}

#: How an app should be *called* in a reply, keyed by image name.
PRETTY_PROCESS: Dict[str, str] = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "code.exe": "VS Code",
    "explorer.exe": "File Explorer",
    "spotify.exe": "Spotify",
    "notepad.exe": "Notepad",
    "discord.exe": "Discord",
    "adobe premiere pro.exe": "Premiere Pro",
    "photoshop.exe": "Photoshop",
    "windowsterminal.exe": "Terminal",
    "pythonw.exe": "Dexter",
}


@dataclasses.dataclass
class Win:
    """One visible top-level window."""

    hwnd: int
    title: str
    pid: int = 0
    process: str = ""

    @property
    def app(self) -> str:
        pretty = PRETTY_PROCESS.get((self.process or "").lower())
        if pretty:
            return pretty
        if self.process:
            return os.path.splitext(self.process)[0].title()
        # No process information (pygetwindow fallback): the tail of a Windows
        # title is almost always the application - "report.md - Notepad++".
        tail = (self.title or "").split(" - ")[-1].strip()
        return tail or "Unknown"

    def describe(self) -> str:
        if self.title and self.app and self.app.lower() not in self.title.lower():
            return f"{self.title} ({self.app})"
        return self.title or self.app


def _process_name(pid: int) -> str:
    if not pid or not _psutil:
        return ""
    try:
        return (_psutil.Process(pid).name() or "").lower()
    except Exception:
        return ""


def _enum_windows() -> List[Win]:
    """Every visible titled window, front of the z-order first.

    EnumWindows hands them back in z-order, which is how "the window you were
    just in" is found without keeping a hook installed.
    """
    found: List[Win] = []
    if _win32gui is not None:
        def _collect(hwnd, _unused):
            try:
                if not _win32gui.IsWindowVisible(hwnd):
                    return True
                title = _win32gui.GetWindowText(hwnd) or ""
                if not title.strip():
                    return True
                pid = 0
                if _win32process is not None:
                    try:
                        pid = _win32process.GetWindowThreadProcessId(hwnd)[1]
                    except Exception:
                        pid = 0
                found.append(Win(hwnd=hwnd, title=title, pid=pid, process=_process_name(pid)))
            except Exception:
                pass
            return True

        try:
            _win32gui.EnumWindows(_collect, None)
            return found
        except Exception:
            found = []
    if _gw is not None:
        try:
            for handle in _gw.getAllWindows():
                title = (getattr(handle, "title", "") or "").strip()
                if title:
                    found.append(Win(hwnd=int(getattr(handle, "_hWnd", 0) or 0), title=title))
        except Exception:
            pass
    return found


def _foreground_hwnd() -> int:
    if _win32gui is None:
        return 0
    try:
        return int(_win32gui.GetForegroundWindow())
    except Exception:
        return 0


def _set_foreground(hwnd: int) -> None:
    if _win32gui is None:
        raise DesktopError("Window control needs pywin32. Run: pip install pywin32")
    try:
        if _win32gui.IsIconic(hwnd):
            _show_window(hwnd, SW_RESTORE)
    except Exception:
        pass
    _win32gui.SetForegroundWindow(hwnd)


def _show_window(hwnd: int, command: int) -> None:
    if _win32gui is None:
        raise DesktopError("Window control needs pywin32. Run: pip install pywin32")
    _win32gui.ShowWindow(hwnd, command)


def _post_close(hwnd: int) -> None:
    """WM_CLOSE: the app decides what to do about unsaved work, as it should."""
    if _win32gui is None:
        raise DesktopError("Window control needs pywin32. Run: pip install pywin32")
    wm_close = getattr(_win32con, "WM_CLOSE", 0x0010) if _win32con else 0x0010
    _win32gui.PostMessage(hwnd, wm_close, 0, 0)


def _terminate(pid: int) -> bool:
    if not _psutil:
        return False
    try:
        proc = _psutil.Process(pid)
        proc.terminate()
        return True
    except Exception:
        return False


def _send_keys(combo: str) -> None:
    """Send one hotkey to whatever has focus. Sequences are space-separated, so
    VS Code's "ctrl+k s" is two presses and not an impossible single chord."""
    if not _keyboard:
        raise DesktopError("Keyboard control needs the keyboard package. Run: pip install keyboard")
    for part in str(combo).split():
        _keyboard.send(part)


def _write_text(text: str) -> None:
    if not _keyboard:
        raise DesktopError("Typing needs the keyboard package. Run: pip install keyboard")
    _keyboard.write(text, delay=0.01)


def _uia_window(hwnd: int):
    desktop = _uia_desktop()
    if desktop is None:
        return None
    try:
        return desktop.window(handle=hwnd)
    except Exception:
        return None


# ───────────────────────── who is Dexter ─────────────────────────


def is_self(win: Optional[Win]) -> bool:
    """Dexter's own window. "Close this" must never mean "close me"."""
    if win is None:
        return False
    if win.pid and win.pid == os.getpid():
        return True
    title = (win.title or "").strip().lower()
    return title == "dexter" or title.startswith("dexter - ")


def windows(include_self: bool = False) -> List[Win]:
    found = _enum_windows()
    return found if include_self else [w for w in found if not is_self(w)]


def active_window(ignore_self: bool = True) -> Optional[Win]:
    """The window the user is working in.

    When Dexter itself is in front (which it is, the moment you type to it) the
    answer is the next window down the z-order instead - the one you were in.
    """
    found = _enum_windows()
    hwnd = _foreground_hwnd()
    front = next((w for w in found if w.hwnd == hwnd), None)
    if front is not None and not (ignore_self and is_self(front)):
        return front
    for win in found:
        if not (ignore_self and is_self(win)):
            return win
    return None


_WORDY = re.compile(r"[^\w]+")


def _alias_of(name: str) -> Optional[str]:
    key = _WORDY.sub(" ", (name or "").strip().lower()).strip()
    key = re.sub(r"\s+", " ", key)
    if not key:
        return None
    if key in APP_ALIASES:
        return APP_ALIASES[key]
    stripped = re.sub(r"^(the|my)\s+", "", key)
    return APP_ALIASES.get(stripped)


def find_windows(name: str) -> List[Win]:
    """Windows belonging to a spoken app or matching a title, best first.

    Process matches win over title matches: "close chrome" means the browser,
    not the file called chrome.png that happens to be open in Photoshop.
    """
    wanted = (name or "").strip().lower().strip(" .!?,'\"")
    if not wanted:
        return []
    alias = _alias_of(wanted)
    images = APP_PROCESSES.get(alias or "", ())
    by_process: List[Win] = []
    exact_title: List[Win] = []
    loose_title: List[Win] = []
    word = re.compile(r"(?<![\w+#])" + re.escape(wanted) + r"(?![\w+#])", re.I)
    for win in windows():
        process = (win.process or "").lower()
        if images and process in images:
            by_process.append(win)
            continue
        if not images and process and os.path.splitext(process)[0] == wanted:
            by_process.append(win)
            continue
        title = win.title or ""
        if word.search(title):
            exact_title.append(win)
        elif wanted in title.lower():
            loose_title.append(win)
    # A known app that has no window is *closed*, not "whatever title contains
    # the word". "close notepad" with only Notepad++ open closed Notepad++
    # (v11.1, live) - the same lesson close_window learned in v8.
    if images:
        return by_process or exact_title
    return by_process or exact_title or loose_title


def resolve_window(name: Optional[str]) -> Win:
    """A named window, or the one in focus. Raises with something sayable."""
    if name:
        matches = find_windows(name)
        if not matches:
            raise DesktopError(f"I can't see a window for '{name}'. Is it open?")
        return matches[0]
    win = active_window()
    if win is None:
        raise DesktopError("I couldn't tell which window you're in.")
    return win


FOCUS_TIMEOUT = float(os.environ.get("DEXTER_FOCUS_TIMEOUT", "1.5") or 1.5)


def focus(win: Win, timeout: float = FOCUS_TIMEOUT) -> bool:
    """Bring a window forward and confirm it actually came forward.

    Windows refuses SetForegroundWindow in several situations (a modal dialog
    elsewhere, a full-screen game, the foreground lock). Believing it worked is
    how keystrokes end up in the wrong application, so the answer is measured.
    """
    try:
        _set_foreground(win.hwnd)
    except DesktopError:
        raise
    except Exception:
        pass
    deadline = time.time() + max(0.0, timeout)
    while True:
        if _foreground_hwnd() == win.hwnd:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.05)


def _focused_target(name: Optional[str]) -> Win:
    """The window a keystroke is allowed to go to, focused and verified."""
    win = resolve_window(name)
    if is_self(win):
        raise DesktopError(
            "That's Dexter's own window. Tell me which app you mean and I'll act there."
        )
    if _foreground_hwnd() == win.hwnd:
        return win
    if not focus(win, FOCUS_TIMEOUT):
        front = next((w for w in _enum_windows() if w.hwnd == _foreground_hwnd()), None)
        took = f" - {front.describe()} has it" if front else ""
        raise DesktopError(
            f"I couldn't put {win.describe()} in focus{took}, so I sent nothing."
        )
    return win


# ───────────────────────── keys, text, scrolling ─────────────────────────

_KEY_WORDS = {
    "control": "ctrl",
    "ctrl": "ctrl",
    "cmd": "ctrl",
    "command": "ctrl",
    "alt": "alt",
    "option": "alt",
    "shift": "shift",
    "win": "windows",
    "windows": "windows",
    "super": "windows",
    "escape": "esc",
    "esc": "esc",
    "return": "enter",
    "enter": "enter",
    "del": "delete",
    "delete": "delete",
    "backspace": "backspace",
    "space": "space",
    "spacebar": "space",
    "tab": "tab",
    "plus": "plus",
    "minus": "-",
    "dash": "-",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "page up": "page up",
    "page down": "page down",
    "pageup": "page up",
    "pagedown": "page down",
    "print screen": "print screen",
}


def normalize_combo(spoken: str) -> str:
    """"control shift s" / "ctrl+s" / "the f5 key" -> a combo keyboard accepts."""
    text = (spoken or "").strip().lower()
    text = re.sub(r"\b(?:the|key|keys|button)\b", " ", text)
    text = re.sub(r"\s*\+\s*", " + ", text)
    parts = [p for p in re.split(r"[\s+]+", text) if p]
    out: List[str] = []
    for part in parts:
        out.append(_KEY_WORDS.get(part, part))
    combo = "+".join(out)
    return combo


def press_keys(keys: str, window: Optional[str] = None) -> Dict[str, Any]:
    combo = normalize_combo(keys)
    if not combo:
        raise DesktopError("Which keys should I press?")
    win = _focused_target(window)
    _send_keys(combo)
    return {"keys": combo, "window": win.describe(), "app": win.app}


# A secret typed into an address bar goes into history, into sync, and into the
# suggestion list for everyone who borrows the machine. Refusing is cheap.
_SECRET_WORDS = re.compile(
    r"\b(?:password|passwd|passphrase|secret|api[\s_-]?key|access[\s_-]?token|"
    r"bearer|otp|one[\s-]?time[\s-]?code|2fa|cvv|card number|pin)\b",
    re.IGNORECASE,
)
_SECRET_PREFIX = re.compile(r"^(?:sk-|pk-|ghp_|gho_|github_pat_|xox[baprs]-|AIza|AKIA|ya29\.)", re.I)
_URLISH = re.compile(r"^(?:https?://|www\.|[\w.-]+@[\w.-]+|[A-Za-z]:[\\/]|[\w-]+(?:\.[\w-]+)+/?$)")
BROWSER_PROCESSES = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe")


def looks_like_a_secret(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if _SECRET_WORDS.search(value):
        return True
    if _SECRET_PREFIX.match(value):
        return True
    if " " in value or _URLISH.match(value):
        return False
    if not 16 <= len(value) <= 200:
        return False
    # A single opaque token: long, no spaces, and mixing the character classes
    # people never mix in a sentence.
    classes = sum(
        bool(pattern.search(value))
        for pattern in (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"\d"),
                        re.compile(r"[^A-Za-z0-9]"))
    )
    return classes >= 3


def type_text(
    text: str,
    submit: bool = False,
    window: Optional[str] = None,
    allow_secret: bool = False,
) -> Dict[str, Any]:
    if text is None or not str(text):
        raise DesktopError("What should I type?")
    win = _focused_target(window)
    if (
        not allow_secret
        and (win.process or "").lower() in BROWSER_PROCESSES
        and looks_like_a_secret(text)
    ):
        raise DesktopError(
            f"That looks like a password or a key, and {win.app}'s address bar "
            "remembers everything typed into it. Click the field you mean and "
            "say 'type it anyway' if you want me to."
        )
    _write_text(str(text))
    if submit:
        _send_keys("enter")
    return {"typed": str(text), "submitted": bool(submit),
            "window": win.describe(), "app": win.app}


_SCROLL_KEYS = {
    "down": "page down",
    "up": "page up",
    "top": "ctrl+home",
    "bottom": "ctrl+end",
}


def scroll(direction: str = "down", amount: int = 1, window: Optional[str] = None) -> Dict[str, Any]:
    """Page keys, not a mouse wheel: no pointer is moved and nothing under the
    cursor gets scrolled by accident."""
    where = (direction or "down").strip().lower()
    where = re.sub(r"^(?:to\s+)?(?:the\s+)?", "", where)
    if where in ("end",):
        where = "bottom"
    if where in ("start", "beginning"):
        where = "top"
    combo = _SCROLL_KEYS.get(where)
    if not combo:
        raise DesktopError("I can scroll up, down, to the top or to the bottom.")
    win = _focused_target(window)
    repeats = 1 if where in ("top", "bottom") else max(1, min(int(amount or 1), 10))
    for _ in range(repeats):
        _send_keys(combo)
    return {"scrolled": where, "times": repeats, "window": win.describe(), "app": win.app}


# ───────────────────────── UI Automation: click and read ─────────────────────────

UIA_BUDGET_SECONDS = float(os.environ.get("DEXTER_UIA_BUDGET", "5") or 5)
CLICKABLE_TYPES = ("Button", "Hyperlink", "MenuItem", "ListItem", "TabItem", "CheckBox",
                   "RadioButton", "TreeItem", "Text")


def _element_name(element) -> str:
    for getter in ("window_text", "name"):
        try:
            value = getattr(element, getter)
            value = value() if callable(value) else value
            if value:
                return str(value).strip()
        except Exception:
            continue
    return ""


def _descendants(spec, control_type: Optional[str] = None) -> List[Any]:
    try:
        if control_type:
            return list(spec.descendants(control_type=control_type))
        return list(spec.descendants())
    except Exception:
        return []


def click_text(name: str, window: Optional[str] = None) -> Dict[str, Any]:
    """Click the thing called `name` in a window, by UI Automation name.

    No screen coordinates and no vision model: Windows already knows the name
    of every control, and `invoke()` presses it without moving the pointer.
    """
    needle = (name or "").strip().strip("\"'")
    if not needle:
        raise DesktopError("What should I click?")
    win = _focused_target(window)
    spec = _uia_window(win.hwnd)
    if spec is None:
        raise DesktopError(
            "Clicking by name needs pywinauto. Run: pip install pywinauto"
        )
    pattern = re.compile(re.escape(needle), re.I)
    started = time.time()
    seen: List[str] = []
    best = None
    for control_type in CLICKABLE_TYPES:
        for element in _descendants(spec, control_type):
            if time.time() - started > UIA_BUDGET_SECONDS:
                break
            label = _element_name(element)
            if not label:
                continue
            if len(seen) < 40:
                seen.append(label)
            if pattern.search(label):
                best = (element, label, control_type)
                break
        if best:
            break
    if not best:
        options = ", ".join(dict.fromkeys(s for s in seen if len(s) < 40))
        raise DesktopError(
            f"Nothing called '{needle}' in {win.describe()}."
            + (f" I can see: {options[:300]}" if options else "")
        )
    element, label, control_type = best
    for attempt in ("invoke", "select", "click_input"):
        action = getattr(element, attempt, None)
        if not callable(action):
            continue
        try:
            action()
            return {"clicked": label, "kind": control_type.lower(), "how": attempt,
                    "window": win.describe(), "app": win.app}
        except Exception:
            continue
    raise DesktopError(f"I found '{label}' in {win.describe()} but it would not take a click.")


def read_window_text(max_chars: int = 4000, window: Optional[str] = None) -> Dict[str, Any]:
    """The readable text of a window, through UIA, with a budget.

    A deep UIA walk on a busy window can take many seconds, so both the clock
    and the character count are capped and the result says it was trimmed.
    """
    win = resolve_window(window)
    spec = _uia_window(win.hwnd)
    if spec is None:
        raise DesktopError("Reading a window needs pywinauto. Run: pip install pywinauto")
    limit = max(200, int(max_chars or 4000))
    started = time.time()
    chunks: List[str] = []
    total = 0
    truncated = False
    for element in _descendants(spec):
        if time.time() - started > UIA_BUDGET_SECONDS:
            truncated = True
            break
        label = _element_name(element)
        if not label or len(label) > 400:
            continue
        if chunks and label == chunks[-1]:
            continue
        chunks.append(label)
        total += len(label) + 1
        if total >= limit:
            truncated = True
            break
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(chunks)).strip()[:limit]
    return {"window": win.describe(), "app": win.app, "text": text,
            "truncated": truncated, "elements": len(chunks)}


# ───────────────────────── window arrangement ─────────────────────────

WINDOW_ACTIONS = ("minimize", "maximize", "restore", "snap_left", "snap_right", "close")
_ACTION_WORDS = {
    "minimise": "minimize",
    "minimize": "minimize",
    "hide": "minimize",
    "maximise": "maximize",
    "maximize": "maximize",
    "fullsize": "maximize",
    "restore": "restore",
    "unmaximize": "restore",
    "snap left": "snap_left",
    "snap_left": "snap_left",
    "left": "snap_left",
    "snap right": "snap_right",
    "snap_right": "snap_right",
    "right": "snap_right",
    "close": "close",
}


def arrange(action: str, window: Optional[str] = None) -> Dict[str, Any]:
    wanted = _ACTION_WORDS.get(re.sub(r"\s+", " ", (action or "").strip().lower()))
    if wanted is None:
        raise DesktopError("I can minimize, maximize, restore, snap left, snap right or close a window.")
    win = resolve_window(window)
    if is_self(win):
        raise DesktopError("That's Dexter's own window - name the app you mean.")
    if wanted == "minimize":
        _show_window(win.hwnd, SW_MINIMIZE)
    elif wanted == "maximize":
        _show_window(win.hwnd, SW_MAXIMIZE)
    elif wanted == "restore":
        _show_window(win.hwnd, SW_RESTORE)
    elif wanted == "close":
        _post_close(win.hwnd)
    else:
        # Snapping is a shell gesture, not a window style: Win+Left/Right is the
        # only way to get the same result the user gets by hand, and it needs the
        # window in focus first.
        target = _focused_target(win.title)
        _send_keys("windows+left" if wanted == "snap_left" else "windows+right")
        win = target
    return {"action": wanted, "window": win.describe(), "app": win.app}


# ───────────────────────── apps ─────────────────────────

CLOSE_WAIT_SECONDS = float(os.environ.get("DEXTER_CLOSE_WAIT", "6") or 6)


def close_app(name: str, wait: float = CLOSE_WAIT_SECONDS) -> Dict[str, Any]:
    """Close every window of an app, politely, and report what is left.

    One WM_CLOSE per window is what clicking every X would do: Chrome ends its
    session cleanly, an editor with unsaved work shows its own prompt. Nothing
    is terminated here - that is `kill_app`, and it is asked about first.
    """
    targets = find_windows(name)
    targets = [w for w in targets if not is_self(w)]
    if not targets:
        raise DesktopError(f"{name} doesn't have a window open.")
    label = targets[0].app
    for win in targets:
        try:
            _post_close(win.hwnd)
        except DesktopError:
            raise
        except Exception:
            continue
    deadline = time.time() + max(0.0, wait)
    remaining = list(targets)
    while True:
        alive = {w.hwnd for w in windows()}
        remaining = [w for w in targets if w.hwnd in alive]
        if not remaining or time.time() >= deadline:
            break
        time.sleep(0.25)
    return {"app": label, "closed": len(targets) - len(remaining),
            "asked": len(targets), "remaining": len(remaining),
            "titles": [w.title for w in targets]}


def kill_app(name: str) -> Dict[str, Any]:
    """Terminate an app. Only ever reached through a confirmed `kill`."""
    targets = find_windows(name)
    pids = {w.pid for w in targets if w.pid and not is_self(w)}
    if not pids and _psutil:
        alias = _alias_of(name) or (name or "").strip().lower()
        images = APP_PROCESSES.get(alias, (f"{alias}.exe",))
        try:
            for proc in _psutil.process_iter(["pid", "name"]):
                if (proc.info.get("name") or "").lower() in images:
                    pids.add(proc.info["pid"])
        except Exception:
            pass
    if not pids:
        raise DesktopError(f"I can't find a running {name} to stop.")
    killed = sum(1 for pid in pids if _terminate(pid))
    return {"app": targets[0].app if targets else name, "killed": killed, "found": len(pids)}


def grouped_windows() -> List[Dict[str, Any]]:
    """Open windows, one row per app, front-most app first."""
    order: List[str] = []
    groups: Dict[str, Dict[str, Any]] = {}
    for win in windows():
        key = win.app
        if key not in groups:
            groups[key] = {"app": key, "process": win.process, "titles": []}
            order.append(key)
        groups[key]["titles"].append(win.title)
    rows = [groups[key] for key in order]
    for row in rows:
        row["count"] = len(row["titles"])
    return rows


# ───────────────────────── per-app hotkey recipes (P2.2) ─────────────────────────

#: "new tab" is Ctrl+T in Chrome and Ctrl+N in Notepad; "find" is Ctrl+F nearly
#: everywhere but Ctrl+L in Spotify. Without this table Dexter would send a
#: plausible key to the wrong app, which is worse than saying it doesn't know.
RECIPES: Dict[str, Dict[str, str]] = {
    "chrome": {
        "new tab": "ctrl+t", "close tab": "ctrl+w", "reopen tab": "ctrl+shift+t",
        "next tab": "ctrl+tab", "previous tab": "ctrl+shift+tab", "new window": "ctrl+n",
        "incognito": "ctrl+shift+n", "find": "ctrl+f", "reload": "f5", "hard reload": "ctrl+shift+r",
        "back": "alt+left", "forward": "alt+right", "history": "ctrl+h", "downloads": "ctrl+j",
        "bookmark": "ctrl+d", "print": "ctrl+p", "zoom in": "ctrl+plus", "zoom out": "ctrl+-",
        "reset zoom": "ctrl+0", "fullscreen": "f11", "devtools": "f12", "address bar": "ctrl+l",
        "toggle sidebar": "ctrl+shift+b",
    },
    "edge": {
        "new tab": "ctrl+t", "close tab": "ctrl+w", "reopen tab": "ctrl+shift+t",
        "next tab": "ctrl+tab", "previous tab": "ctrl+shift+tab", "new window": "ctrl+n",
        "incognito": "ctrl+shift+n", "find": "ctrl+f", "reload": "f5", "back": "alt+left",
        "forward": "alt+right", "print": "ctrl+p", "zoom in": "ctrl+plus", "zoom out": "ctrl+-",
        "fullscreen": "f11", "devtools": "f12", "address bar": "ctrl+l",
        "toggle sidebar": "ctrl+shift+b", "split screen": "ctrl+shift+2",
    },
    "code": {
        "new file": "ctrl+n", "open file": "ctrl+o", "save": "ctrl+s", "save all": "ctrl+k s",
        "close tab": "ctrl+w", "find": "ctrl+f", "find in files": "ctrl+shift+f",
        "replace": "ctrl+h", "go to file": "ctrl+p", "command palette": "ctrl+shift+p",
        "toggle sidebar": "ctrl+b", "toggle panel": "ctrl+j", "terminal": "ctrl+grave",
        "comment": "ctrl+/", "format": "shift+alt+f", "split editor": "ctrl+\\",
        "zen mode": "ctrl+k z", "go to line": "ctrl+g", "next tab": "ctrl+page down",
        "previous tab": "ctrl+page up", "source control": "ctrl+shift+g",
    },
    "explorer": {
        "new folder": "ctrl+shift+n", "new window": "ctrl+n", "find": "ctrl+f",
        "rename": "f2", "properties": "alt+enter", "up": "alt+up", "back": "alt+left",
        "forward": "alt+right", "refresh": "f5", "select all": "ctrl+a",
        "toggle preview": "alt+p", "new tab": "ctrl+t",
    },
    "spotify": {
        "next track": "ctrl+right", "previous track": "ctrl+left", "play": "space",
        "pause": "space", "find": "ctrl+l", "search": "ctrl+l", "volume up": "ctrl+up",
        "volume down": "ctrl+down", "like": "alt+shift+b", "new playlist": "ctrl+n",
        "repeat": "ctrl+r", "shuffle": "ctrl+s", "queue": "ctrl+shift+q",
    },
    "notepad": {
        "new file": "ctrl+n", "new tab": "ctrl+t", "new window": "ctrl+shift+n",
        "save": "ctrl+s", "save as": "ctrl+shift+s", "find": "ctrl+f", "replace": "ctrl+h",
        "print": "ctrl+p", "go to line": "ctrl+g", "select all": "ctrl+a", "zoom in": "ctrl+plus",
        "zoom out": "ctrl+-",
    },
    "discord": {
        "find": "ctrl+f", "search": "ctrl+f", "quick switcher": "ctrl+k",
        "mute": "ctrl+shift+m", "deafen": "ctrl+shift+d", "next unread": "alt+shift+down",
        "mark read": "escape", "emoji": "ctrl+e", "toggle sidebar": "ctrl+shift+u",
    },
    "premiere": {
        "save": "ctrl+s", "save as": "ctrl+shift+s", "import": "ctrl+i", "export": "ctrl+m",
        "new sequence": "ctrl+n", "cut": "ctrl+k", "razor": "c", "selection tool": "v",
        "play": "space", "mark in": "i", "mark out": "o", "zoom in": "plus", "zoom out": "-",
        "undo": "ctrl+z", "redo": "ctrl+shift+z", "find": "ctrl+f", "render": "shift+e",
    },
}

#: What almost every Windows app agrees on. The fallback, never the first answer.
GENERIC_RECIPE: Dict[str, str] = {
    "save": "ctrl+s", "copy": "ctrl+c", "paste": "ctrl+v", "cut": "ctrl+x",
    "select all": "ctrl+a", "undo": "ctrl+z", "redo": "ctrl+y", "find": "ctrl+f",
    "print": "ctrl+p", "new": "ctrl+n", "new file": "ctrl+n", "new window": "ctrl+n",
    "close tab": "ctrl+w", "refresh": "f5", "delete": "delete", "rename": "f2",
}

_ACTION_SYNONYMS = {
    "open a new tab": "new tab", "another tab": "new tab", "tab": "new tab",
    "new document": "new file", "blank file": "new file",
    "search": "find", "search this": "find", "find in page": "find",
    "sidebar": "toggle sidebar", "toggle the sidebar": "toggle sidebar",
    "explorer": "toggle sidebar", "palette": "command palette",
    "skip": "next track", "next song": "next track", "previous song": "previous track",
    "reload the page": "reload", "refresh the page": "reload",
    "save the file": "save", "save it": "save", "save this": "save",
    "undo that": "undo", "reopen the last tab": "reopen tab",
    "reopen closed tab": "reopen tab", "restore tab": "reopen tab",
}


def recipe_key(app_or_process: str) -> Optional[str]:
    """'chrome.exe', 'Chrome', 'vs code' -> the recipe table key."""
    raw = (app_or_process or "").strip().lower()
    if not raw:
        return None
    if raw in RECIPES:
        return raw
    stem = os.path.splitext(raw)[0]
    if stem in RECIPES:
        return stem
    alias = _alias_of(raw) or _alias_of(stem)
    return alias if alias in RECIPES else None


def normalize_action(action: str) -> str:
    text = re.sub(r"\s+", " ", (action or "").strip().lower()).strip(" .!?")
    text = re.sub(r"^(?:please\s+)?(?:do\s+)?", "", text)
    text = re.sub(r"^(?:the|a|an)\s+", "", text)
    return _ACTION_SYNONYMS.get(text, text)


def recipe(action: str, app: Optional[str]) -> Tuple[Optional[str], str]:
    """(keys, where it came from): 'app', 'generic', or '' when unknown."""
    wanted = normalize_action(action)
    key = recipe_key(app or "")
    if key:
        keys = RECIPES[key].get(wanted)
        if keys:
            return keys, "app"
    keys = GENERIC_RECIPE.get(wanted)
    if keys:
        return keys, "generic"
    return None, ""


def run_recipe(action: str, window: Optional[str] = None) -> Dict[str, Any]:
    """Do `action` the way the app in front expects it to be done."""
    win = resolve_window(window)
    keys, source = recipe(action, win.process or win.app)
    if not keys:
        raise DesktopError(
            f"In {win.app} I don't know how to '{normalize_action(action)}'. "
            "Tell me the shortcut and I'll use it."
        )
    target = _focused_target(win.title if window else None)
    _send_keys(keys)
    return {"action": normalize_action(action), "keys": keys, "source": source,
            "window": target.describe(), "app": target.app}


SHOTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "screenshots"
)


def window_rect(win: Win) -> Optional[Tuple[int, int, int, int]]:
    if _win32gui is None:
        return None
    try:
        left, top, right, bottom = _win32gui.GetWindowRect(win.hwnd)
        if right > left and bottom > top:
            return int(left), int(top), int(right), int(bottom)
    except Exception:
        pass
    return None


def capture_window(win: Win, path: Optional[str] = None) -> str:
    """A picture of one window, not the whole desktop.

    Used for "screenshot this tab": the window is brought forward first, because
    a covered window captures whatever is on top of it.
    """
    try:
        import mss  # type: ignore
        from mss.tools import to_png  # type: ignore
    except Exception as exc:  # pragma: no cover - mss missing
        raise DesktopError("Screenshots need mss. Run: pip install mss") from exc
    focus(win, 0.8)
    rect = window_rect(win)
    if rect is None:
        raise DesktopError("I couldn't measure that window.")
    left, top, right, bottom = rect
    os.makedirs(SHOTS_DIR, exist_ok=True)
    target = path or os.path.join(SHOTS_DIR, f"window-{time.strftime('%Y%m%d-%H%M%S')}.png")
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        to_png(shot.rgb, shot.size, output=target)
    return target


def availability() -> Dict[str, Any]:
    return {
        "windows": _win32gui is not None or _gw is not None,
        "win32": _win32gui is not None,
        "keyboard": bool(_keyboard),
        "processes": bool(_psutil),
        "uia": _uia_desktop() is not None,
    }
