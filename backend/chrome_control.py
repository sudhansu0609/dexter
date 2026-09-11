"""
Your Chrome, not a second one (roadmap v11, P1).

The complaint this module answers: "dexter opens chrome but not what i say in
it". Dexter had a browser, but it was *its own* - a Playwright window on a
separate profile under data/browser_profile. So "open youtube" either failed or
opened a second Chrome that knew none of your logins, and "close this tab"
acted in a window you were not looking at.

Chrome 136 and later refuse `--remote-debugging-port` on the default profile,
and a copy of the profile is not your Chrome. So control here is by the three
honest means that work on the window you are actually using:

  (a) `chrome.exe <url>` - the running instance takes the URL as a new tab;
  (b) keystrokes to the **focused** Chrome window for tab and window actions;
  (c) Windows UI Automation to *read* the active tab's URL, title, tab strip
      and page text.

Closing is always WM_CLOSE, one per window: Chrome ends its session cleanly and
can offer to restore it. `kill()` exists for a wedged browser and the tool that
calls it asks first.

Window finding, focus verification and keystroke delivery are shared with
`desktop_control`; this module is the Chrome-shaped specialisation of it.
"""

import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

import desktop_control as dc
from browser import known_site, normalize_target

CHROME_IMAGE = "chrome.exe"
CHROME_SUFFIX = " - Google Chrome"

#: Default is your Chrome. `playwright` keeps the v6 behaviour (Dexter's own
#: window) for anyone who wants a browser that is not theirs to lose.
MODE_ENV = "DEXTER_BROWSER_MODE"

_STANDARD_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
)


class ChromeError(RuntimeError):
    """A sentence Dexter can say, not a traceback."""


def mode() -> str:
    """'own' (your Chrome) or 'playwright' (Dexter's own window)."""
    value = (os.environ.get(MODE_ENV) or "own").strip().lower()
    return "playwright" if value.startswith("play") else "own"


def own_mode() -> bool:
    return mode() == "own"


# ───────────────────────── finding Chrome ─────────────────────────


def _spawn(args: List[str]) -> None:
    """The one place a process is started. Tests replace this."""
    subprocess.Popen(
        args,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def find_chrome() -> Optional[str]:
    """chrome.exe, from the env override, the standard paths, a running Chrome,
    or the app index - in that order of certainty."""
    override = (os.environ.get("DEXTER_CHROME_EXE") or "").strip().strip('"')
    candidates: List[str] = [override] if override else []
    candidates.extend(_STANDARD_PATHS)
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    # A running Chrome knows where it lives, which beats guessing.
    if dc._psutil is not None:
        try:
            for proc in dc._psutil.process_iter(["name", "exe"]):
                if (proc.info.get("name") or "").lower() == CHROME_IMAGE:
                    exe = proc.info.get("exe")
                    if exe and os.path.isfile(exe):
                        return exe
        except Exception:
            pass
    try:
        import app_index

        found = app_index.lookup("chrome")
        if found and found.lower().endswith(".exe") and os.path.isfile(found):
            return found
    except Exception:
        pass
    return None


def chrome_windows() -> List[dc.Win]:
    """Every Chrome window, front-most first.

    Matched on the *process*, because a window called "chrome logo.png - Paint"
    is not a browser, and a Chrome app window has no " - Google Chrome" suffix.
    """
    found = [w for w in dc.windows() if (w.process or "").lower() == CHROME_IMAGE]
    if found:
        return found
    # No process information available (no pywin32/psutil): fall back to the
    # title suffix, which is right for ordinary browser windows.
    return [w for w in dc.windows() if (w.title or "").endswith(CHROME_SUFFIX)]


def is_running() -> bool:
    if chrome_windows():
        return True
    if dc._psutil is None:
        return False
    try:
        return any(
            (proc.info.get("name") or "").lower() == CHROME_IMAGE
            for proc in dc._psutil.process_iter(["name"])
        )
    except Exception:
        return False


def active_chrome() -> Optional[dc.Win]:
    """The Chrome window the user is looking at, else the front-most one."""
    found = chrome_windows()
    if not found:
        return None
    front = dc._foreground_hwnd()
    for win in found:
        if win.hwnd == front:
            return win
    return found[0]


def tab_title(win: Optional[dc.Win] = None) -> str:
    win = win or active_chrome()
    if win is None:
        return ""
    title = win.title or ""
    return title[: -len(CHROME_SUFFIX)] if title.endswith(CHROME_SUFFIX) else title


def _require_window() -> dc.Win:
    win = active_chrome()
    if win is None:
        raise ChromeError("Chrome isn't open. Say 'open chrome' (or name a site) and I'll start it.")
    return win


# ───────────────────────── opening ─────────────────────────

LAUNCH_WAIT_SECONDS = float(os.environ.get("DEXTER_CHROME_WAIT", "6") or 6)


def open_url(target: str, new_window: bool = False) -> Dict[str, Any]:
    """Open a URL, a known site or a search in the Chrome you use.

    `chrome.exe <url>` is handed to the already-running instance by Chrome
    itself, which is why this is a new tab in *your* window rather than a second
    browser. With Chrome closed, the same command starts it. There is no command
    line that replaces the *current* tab, so this is always a new one - which is
    also what "open youtube" means to a person.
    """
    url = normalize_target(target)
    exe = find_chrome()
    if not exe:
        raise ChromeError(
            "I couldn't find chrome.exe. Install Chrome, or set DEXTER_CHROME_EXE "
            "to its full path."
        )
    was_running = is_running()
    args = [exe]
    if new_window:
        args.append("--new-window")
    args.append(url)
    try:
        _spawn(args)
    except Exception as exc:
        raise ChromeError(f"Chrome would not start: {exc}") from exc

    win = None
    deadline = time.time() + (1.5 if was_running else LAUNCH_WAIT_SECONDS)
    while time.time() < deadline:
        win = active_chrome()
        if win is not None:
            break
        time.sleep(0.25)
    if win is not None:
        try:
            dc.focus(win, timeout=0.8)
        except Exception:
            pass
        win = active_chrome() or win
    return {
        "url": url,
        "launched": not was_running,
        "title": tab_title(win),
        "window": win.describe() if win else "",
        "target": target,
    }


def search(query: str, site: Optional[str] = None) -> Dict[str, Any]:
    """'search youtube for lo-fi' / 'google best mic' - one tab, right site."""
    text = (query or "").strip()
    if site:
        text = f"{site.strip()} {text}".strip()
    if not text:
        raise ChromeError("What should I search for?")
    return open_url(text)


# ───────────────────────── keystroke actions ─────────────────────────

#: Chrome's own shortcuts. Sent only to a Chrome window that is verified to hold
#: the foreground, so a mistimed Ctrl+W can never close someone else's tab.
ACTIONS: Dict[str, str] = {
    "close_tab": "ctrl+w",
    "new_tab": "ctrl+t",
    "next_tab": "ctrl+tab",
    "previous_tab": "ctrl+shift+tab",
    "reopen_tab": "ctrl+shift+t",
    "back": "alt+left",
    "forward": "alt+right",
    "reload": "f5",
    "hard_reload": "ctrl+shift+r",
    "find": "ctrl+f",
    "zoom_in": "ctrl+plus",
    "zoom_out": "ctrl+-",
    "zoom_reset": "ctrl+0",
    "fullscreen": "f11",
    "new_window": "ctrl+n",
    "history": "ctrl+h",
    "downloads": "ctrl+j",
    "address_bar": "ctrl+l",
    "print": "ctrl+p",
}


def perform(action: str, repeat: int = 1) -> Dict[str, Any]:
    """Run one of ACTIONS in the Chrome window in front."""
    key = re.sub(r"[\s-]+", "_", (action or "").strip().lower())
    combo = ACTIONS.get(key)
    if not combo:
        raise ChromeError(f"I don't have a Chrome shortcut for '{action}'.")
    win = _require_window()
    before = tab_title(win)
    try:
        target = dc._focused_target(win.title)
    except dc.DesktopError as exc:
        raise ChromeError(str(exc)) from exc
    for _ in range(max(1, min(int(repeat or 1), 10))):
        dc._send_keys(combo)
    time.sleep(0.15)
    return {"action": key, "keys": combo, "window": target.describe(),
            "was": before, "title": tab_title()}


def close_tab() -> Dict[str, Any]:
    return perform("close_tab")


def new_tab() -> Dict[str, Any]:
    return perform("new_tab")


def next_tab() -> Dict[str, Any]:
    return perform("next_tab")


def previous_tab() -> Dict[str, Any]:
    return perform("previous_tab")


def reopen_tab() -> Dict[str, Any]:
    return perform("reopen_tab")


def back() -> Dict[str, Any]:
    return perform("back")


def forward() -> Dict[str, Any]:
    return perform("forward")


def reload(hard: bool = False) -> Dict[str, Any]:
    return perform("hard_reload" if hard else "reload")


def zoom(direction: str = "in") -> Dict[str, Any]:
    where = (direction or "in").strip().lower()
    if where in ("reset", "normal", "100", "100%"):
        return perform("zoom_reset")
    return perform("zoom_out" if where.startswith("out") or where == "smaller" else "zoom_in")


def fullscreen() -> Dict[str, Any]:
    return perform("fullscreen")


def find_in_page(text: str) -> Dict[str, Any]:
    """Ctrl+F, then the words. Chrome's own find bar, highlighting and all."""
    needle = (text or "").strip()
    if not needle:
        raise ChromeError("What should I look for on the page?")
    result = perform("find")
    dc._write_text(needle)
    result["searched"] = needle
    return result


def scroll(direction: str = "down", amount: int = 1) -> Dict[str, Any]:
    win = _require_window()
    try:
        return dc.scroll(direction, amount=amount, window=win.title)
    except dc.DesktopError as exc:
        raise ChromeError(str(exc)) from exc


def click(name: str) -> Dict[str, Any]:
    """Click something in the page by its accessible name (UIA), or fail saying
    what is there. Chrome exposes link and button names to UIA, which is enough
    for "click sign in" without a screenshot or a vision model."""
    win = _require_window()
    try:
        return dc.click_text(name, window=win.title)
    except dc.DesktopError as exc:
        raise ChromeError(str(exc)) from exc


def type_text(text: str, submit: bool = False, allow_secret: bool = False) -> Dict[str, Any]:
    win = _require_window()
    try:
        return dc.type_text(text, submit=submit, window=win.title, allow_secret=allow_secret)
    except dc.DesktopError as exc:
        raise ChromeError(str(exc)) from exc


# ───────────────────────── closing ─────────────────────────


def close(wait: float = dc.CLOSE_WAIT_SECONDS) -> Dict[str, Any]:
    """Close every Chrome window, gracefully. Never TerminateProcess."""
    try:
        return dc.close_app("chrome", wait=wait)
    except dc.DesktopError as exc:
        raise ChromeError(str(exc)) from exc


def kill() -> Dict[str, Any]:
    """Terminate Chrome. For a wedged browser only, and always confirmed first."""
    try:
        return dc.kill_app("chrome")
    except dc.DesktopError as exc:
        raise ChromeError(str(exc)) from exc


# ───────────────────────── reading (UI Automation) ─────────────────────────

#: Chrome's omnibox. The name is stable across versions and locales that follow
#: the English accessibility strings; the second form appears while it is empty.
OMNIBOX_NAMES = ("Address and search bar", "Address and search bar, Search Google or type a URL")
PAGE_TEXT_LIMIT = 6000


def _uia(win: dc.Win):
    spec = dc._uia_window(win.hwnd)
    if spec is None:
        raise ChromeError(
            "Reading the page needs pywinauto (UI Automation). Run: pip install pywinauto"
        )
    return spec


def _value_of(element) -> str:
    for attempt in ("get_value", "window_text"):
        getter = getattr(element, attempt, None)
        if not callable(getter):
            continue
        try:
            value = getter()
            if value:
                return str(value).strip()
        except Exception:
            continue
    try:
        legacy = element.legacy_properties()
        value = legacy.get("Value") or legacy.get("Name")
        if value:
            return str(value).strip()
    except Exception:
        pass
    return ""


def active_url(win: Optional[dc.Win] = None) -> str:
    """What the omnibox says. Chrome hides the scheme, so it is put back."""
    win = win or _require_window()
    spec = _uia(win)
    for name in OMNIBOX_NAMES:
        try:
            element = spec.child_window(title=name, control_type="Edit")
        except Exception:
            continue
        value = _value_of(element)
        if value:
            if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I) and not value.startswith("about:"):
                value = f"https://{value}"
            return value
    # Some builds name it differently; the only Edit in the toolbar is the bar.
    for element in dc._descendants(spec, "Edit"):
        value = _value_of(element)
        if value and ("." in value or value.startswith("about:")):
            return value if "://" in value else f"https://{value}"
    return ""


def tab_names(win: Optional[dc.Win] = None) -> List[str]:
    win = win or _require_window()
    spec = _uia(win)
    names: List[str] = []
    for element in dc._descendants(spec, "TabItem"):
        label = dc._element_name(element)
        if label and label not in names:
            names.append(label)
    return names


def page_text(max_chars: int = PAGE_TEXT_LIMIT, win: Optional[dc.Win] = None) -> str:
    """The document's text through UIA, budgeted.

    A full UIA walk of a long page is slow and most of it is chrome (menus,
    toolbars), so the walk starts at the Document control and stops at the
    character budget or the clock, whichever comes first.
    """
    win = win or _require_window()
    spec = _uia(win)
    limit = max(200, min(int(max_chars or PAGE_TEXT_LIMIT), PAGE_TEXT_LIMIT))
    root = spec
    try:
        document = spec.child_window(control_type="Document")
        if document is not None:
            root = document
    except Exception:
        pass
    started = time.time()
    chunks: List[str] = []
    total = 0
    for element in dc._descendants(root):
        if time.time() - started > dc.UIA_BUDGET_SECONDS:
            break
        label = dc._element_name(element)
        if not label or len(label) > 600:
            continue
        if chunks and label == chunks[-1]:
            continue
        chunks.append(label)
        total += len(label) + 1
        if total >= limit:
            break
    text = re.sub(r"[ \t]+", " ", "\n".join(chunks))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def read_active_tab(max_chars: int = PAGE_TEXT_LIMIT, with_text: bool = True) -> Dict[str, Any]:
    """Everything Dexter can honestly say about what you are looking at."""
    win = _require_window()
    info: Dict[str, Any] = {
        "title": tab_title(win),
        "window": win.describe(),
        "url": "",
        "tabs": [],
        "text": "",
    }
    try:
        info["url"] = active_url(win)
    except ChromeError:
        raise
    except Exception:
        info["url"] = ""
    try:
        info["tabs"] = tab_names(win)
    except Exception:
        info["tabs"] = []
    if with_text:
        try:
            info["text"] = page_text(max_chars, win=win)
        except Exception:
            info["text"] = ""
    return info


def switch_tab(which: str) -> Dict[str, Any]:
    """'next', 'previous', a number, or words from a tab's title.

    By name it is Ctrl+1..8 when the tab strip says where it is; otherwise
    Ctrl+Tab as many times as needed, which is what a person would do.
    """
    wanted = (which or "").strip().lower()
    if not wanted:
        raise ChromeError("Which tab?")
    if wanted in ("next", "right", "forward"):
        return {**perform("next_tab"), "which": wanted}
    if wanted in ("previous", "prev", "left", "back", "last"):
        return {**perform("previous_tab"), "which": wanted}
    win = _require_window()
    names = []
    try:
        names = tab_names(win)
    except Exception:
        names = []
    index: Optional[int] = None
    if wanted.isdigit():
        index = int(wanted)
    else:
        for position, label in enumerate(names, 1):
            if wanted in label.lower():
                index = position
                break
    if index is None:
        options = "; ".join(names[:8])
        raise ChromeError(
            f"No tab matching '{which}'." + (f" Open tabs: {options}" if options else "")
        )
    if 1 <= index <= 8:
        try:
            target = dc._focused_target(win.title)
        except dc.DesktopError as exc:
            raise ChromeError(str(exc)) from exc
        dc._send_keys(f"ctrl+{index}")
        time.sleep(0.15)
        return {"which": which, "index": index, "window": target.describe(),
                "title": tab_title(), "keys": f"ctrl+{index}"}
    result = perform("next_tab", repeat=1)
    result["which"] = which
    result["index"] = index
    return result


# ───────────────────────── site names are sites (P1.3) ─────────────────────────


def site_for(name: str) -> Optional[str]:
    """'youtube' -> the URL; 'chrome' / 'notepad' -> None (those are apps)."""
    return known_site(name)


def availability() -> Dict[str, Any]:
    return {
        "mode": mode(),
        "chrome": find_chrome(),
        "running": is_running(),
        **dc.availability(),
    }
