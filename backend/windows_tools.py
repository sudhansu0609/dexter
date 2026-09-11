"""
Windows system control.

Volume, brightness, clipboard, screenshots, window management and lock/sleep,
plus active-window awareness so Dexter has a referent for "summarise this" or
"what am I looking at".

Every optional package is probed once at import and degrades to a clear message
rather than an exception, so a partial install still leaves the rest working.
"""

import ctypes
import os
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from tool_registry import ToolResult, tool

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SCREENSHOT_DIR = os.path.join(DATA_DIR, "screenshots")


def _try(module: str):
    try:
        return __import__(module)
    except Exception:
        return None


_pyperclip = _try("pyperclip")
_mss = _try("mss")
_sbc = _try("screen_brightness_control")

try:
    import pygetwindow as _gw  # type: ignore
except Exception:
    _gw = None

try:
    from comtypes import CLSCTX_ALL  # type: ignore
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore

    _HAS_PYCAW = True
except Exception:
    _HAS_PYCAW = False


# ───────────────────────── volume ─────────────────────────


_com_ready = threading.local()


def _ensure_com() -> None:
    """
    COM must be initialised per thread.

    Endpoints run inside FastAPI's threadpool, so each worker thread hits the
    audio API on a fresh thread that has never called CoInitialize - which
    failed with WinError -2147221008 until this was added.
    """
    if getattr(_com_ready, "done", False):
        return
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        pass
    _com_ready.done = True


def _volume_interface():
    """
    Get the endpoint volume control.

    pycaw changed shape: newer releases return an AudioDevice wrapper exposing
    `.EndpointVolume` directly, while older ones return a raw IMMDevice that has
    to be Activate()d. Support both so a pycaw upgrade cannot break volume.
    """
    _ensure_com()
    device = AudioUtilities.GetSpeakers()

    endpoint = getattr(device, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint

    raw = getattr(device, "_dev", device)
    interface = raw.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return interface.QueryInterface(IAudioEndpointVolume)


@tool(
    name="set_volume",
    description=(
        "Set, raise, lower or mute the system audio volume. "
        "Use `level` for an absolute percentage, or `change` for a relative step."
    ),
    params={
        "level": {
            "type": "integer",
            "description": "Absolute volume 0-100",
            "required": False,
        },
        "change": {
            "type": "integer",
            "description": "Relative change, e.g. 10 or -10",
            "required": False,
        },
        "mute": {
            "type": "boolean",
            "description": "True to mute, False to unmute",
            "required": False,
        },
    },
    examples=[
        "turn the volume up", "turn it down", "set volume to 30",
        "mute the sound", "unmute", "make it louder", "volume down a bit",
        "max volume", "turn the sound off",
    ],
    category="system",
)
def set_volume(
    level: Optional[int] = None,
    change: Optional[int] = None,
    mute: Optional[bool] = None,
) -> ToolResult:
    if not _HAS_PYCAW:
        return ToolResult.fail("Volume control needs pycaw. Run: pip install pycaw comtypes")

    try:
        volume = _volume_interface()

        if mute is not None:
            volume.SetMute(bool(mute), None)
            return ToolResult(ok=True, summary="Muted." if mute else "Unmuted.",
                              data={"muted": bool(mute)})

        current = round(volume.GetMasterVolumeLevelScalar() * 100)
        if level is None and change is None:
            return ToolResult(ok=True, summary=f"Volume is at {current}%.",
                              data={"level": current})

        target = level if level is not None else current + change
        target = max(0, min(100, int(target)))

        volume.SetMute(False, None)
        volume.SetMasterVolumeLevelScalar(target / 100.0, None)
        return ToolResult(ok=True, summary=f"Volume set to {target}%.", data={"level": target})
    except Exception as exc:
        return ToolResult.fail(f"Could not change the volume: {exc}")


# ───────────────────────── brightness ─────────────────────────


@tool(
    name="set_brightness",
    description="Set or adjust the display brightness.",
    params={
        "level": {"type": "integer", "description": "Absolute brightness 0-100", "required": False},
        "change": {"type": "integer", "description": "Relative change", "required": False},
    },
    examples=[
        "make the screen brighter", "dim the screen", "set brightness to 40",
        "turn the brightness down", "screen is too dark",
    ],
    category="system",
)
def set_brightness(level: Optional[int] = None, change: Optional[int] = None) -> ToolResult:
    if not _sbc:
        return ToolResult.fail(
            "Brightness control needs screen-brightness-control. "
            "Run: pip install screen-brightness-control"
        )
    try:
        current = _sbc.get_brightness()[0]
        if level is None and change is None:
            return ToolResult(ok=True, summary=f"Brightness is at {current}%.",
                              data={"level": current})
        target = max(0, min(100, int(level if level is not None else current + change)))
        _sbc.set_brightness(target)
        return ToolResult(ok=True, summary=f"Brightness set to {target}%.", data={"level": target})
    except Exception as exc:
        return ToolResult.fail(f"Could not change brightness: {exc}")


# ───────────────────────── clipboard ─────────────────────────


@tool(
    name="read_clipboard",
    description="Read whatever text is currently on the clipboard.",
    params={},
    examples=["what's on my clipboard", "read the clipboard", "what did I just copy"],
    category="system",
)
def read_clipboard() -> ToolResult:
    if not _pyperclip:
        return ToolResult.fail("Clipboard access needs pyperclip. Run: pip install pyperclip")
    try:
        content = _pyperclip.paste() or ""
        if not content.strip():
            return ToolResult(ok=True, summary="The clipboard is empty.", data={"content": ""})
        preview = content[:500] + ("..." if len(content) > 500 else "")
        return ToolResult(ok=True, summary=f"Clipboard: {preview}",
                          data={"content": content, "length": len(content)})
    except Exception as exc:
        return ToolResult.fail(f"Could not read the clipboard: {exc}")


@tool(
    name="write_clipboard",
    description="Copy text onto the clipboard so the user can paste it.",
    params={"text": {"type": "string", "description": "Text to copy"}},
    examples=["copy that to my clipboard", "put this on the clipboard", "copy this text"],
    category="system",
)
def write_clipboard(text: str) -> ToolResult:
    if not _pyperclip:
        return ToolResult.fail("Clipboard access needs pyperclip. Run: pip install pyperclip")
    try:
        _pyperclip.copy(text)
        return ToolResult(ok=True, summary="Copied to your clipboard.", data={"length": len(text)})
    except Exception as exc:
        return ToolResult.fail(f"Could not write to the clipboard: {exc}")


# ───────────────────────── screenshots ─────────────────────────


@tool(
    name="take_screenshot",
    description="Capture the screen to an image file and open it.",
    params={},
    examples=["take a screenshot", "capture my screen", "grab a screenshot", "screenshot this"],
    category="system",
)
def take_screenshot() -> ToolResult:
    if not _mss:
        return ToolResult.fail("Screenshots need mss. Run: pip install mss")
    try:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, f"screenshot_{int(time.time())}.png")
        with _mss.mss() as sct:
            sct.shot(mon=-1, output=path)
        try:
            os.startfile(path)
        except Exception:
            pass
        return ToolResult(ok=True, summary=f"Screenshot saved to {os.path.basename(path)}.",
                          data={"path": path})
    except Exception as exc:
        return ToolResult.fail(f"Could not take a screenshot: {exc}")


# ───────────────────────── windows ─────────────────────────


def get_active_window_title() -> str:
    """Cheap enough to call on every turn - used to give the agent context."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value
    except Exception:
        return ""


@tool(
    name="get_active_window",
    description=(
        "Find out which application and window the user currently has in focus. "
        "Use this to resolve vague references like 'this' or 'what I'm looking at'."
    ),
    params={},
    examples=[
        "what am I looking at", "what window is open", "what am I working on",
        "what's in focus right now",
    ],
    category="system",
)
def get_active_window() -> ToolResult:
    title = get_active_window_title()
    if not title:
        return ToolResult(ok=True, summary="I couldn't tell which window is active.", data={})
    return ToolResult(ok=True, summary=f"You're in: {title}", data={"title": title})


@tool(
    name="list_open_windows",
    description="List the titles of all open application windows.",
    params={},
    examples=["what apps are open", "list my open windows", "what's running"],
    category="system",
)
def list_open_windows() -> ToolResult:
    if not _gw:
        return ToolResult.fail("Window listing needs pygetwindow. Run: pip install pygetwindow")
    try:
        titles: List[str] = [t for t in _gw.getAllTitles() if t and t.strip()]
        if not titles:
            return ToolResult(ok=True, summary="No open windows found.", data={"windows": []})
        preview = "; ".join(titles[:8])
        return ToolResult(ok=True, summary=f"{len(titles)} open window(s): {preview}",
                          data={"windows": titles})
    except Exception as exc:
        return ToolResult.fail(f"Could not list windows: {exc}")


@tool(
    name="focus_window",
    description="Bring an already-open window to the front by part of its title.",
    params={"title": {"type": "string", "description": "Part of the window title"}},
    examples=["switch to chrome", "bring up my editor window", "focus the browser",
              "go back to vscode"],
    category="system",
)
def focus_window(title: str) -> ToolResult:
    if not _gw:
        return ToolResult.fail("Window control needs pygetwindow. Run: pip install pygetwindow")
    try:
        matches = _rank_windows(_gw.getAllWindows(), title)
        if not matches:
            return ToolResult.fail(f"No open window matching '{title}'.")
        window = matches[0]
        if window.isMinimized:
            window.restore()
        window.activate()
        return ToolResult(ok=True, summary=f"Switched to {window.title}.",
                          data={"title": window.title})
    except Exception as exc:
        return ToolResult.fail(f"Could not focus that window: {exc}")


def _rank_windows(windows, title: str):
    """Whole-word matches first ("notepad" is Notepad, not Notepad++; "code" is
    VS Code, not "Decode.png"), then plain substring matches. Returns [] when
    nothing matches at all."""
    import re as _re

    wanted = (title or "").strip().lower()
    if not wanted:
        return []
    word = _re.compile(r"(?<![\w+#])" + _re.escape(wanted) + r"(?![\w+#])", _re.I)
    exact, loose = [], []
    for w in windows:
        text = (w.title or "").strip()
        if not text:
            continue
        if word.search(text):
            exact.append(w)
        elif wanted in text.lower():
            loose.append(w)
    return exact or loose


@tool(
    name="close_window",
    description=(
        "Close one open window by part of its title. "
        "To close a whole application (all of its windows) use close_app instead."
    ),
    params={"title": {"type": "string", "description": "Part of the window title"}},
    examples=["close that window", "close this window", "shut the window called report",
              "close the window titled invoice"],
    # No confirmation (v8): applications prompt for unsaved work themselves, and
    # "close notepad" -> "are you sure?" made a one-word job a two-step one.
    confirm=False,
    category="system",
)
def close_window(title: str) -> ToolResult:
    if not _gw:
        return ToolResult.fail("Window control needs pygetwindow. Run: pip install pygetwindow")
    try:
        matches = _rank_windows(_gw.getAllWindows(), title)
        if not matches:
            return ToolResult.fail(f"No open window matching '{title}'.")
        closed = matches[0].title
        matches[0].close()
        return ToolResult(ok=True, summary=f"Closed {closed}.", data={"title": closed})
    except Exception as exc:
        return ToolResult.fail(f"Could not close that window: {exc}")


# ───────────────────────── power ─────────────────────────


@tool(
    name="lock_screen",
    description="Lock the Windows session immediately.",
    params={},
    examples=["lock my pc", "lock the screen", "lock the computer", "I'm stepping away"],
    category="system",
)
def lock_screen() -> ToolResult:
    try:
        ctypes.windll.user32.LockWorkStation()
        return ToolResult(ok=True, summary="Locking your PC.")
    except Exception as exc:
        return ToolResult.fail(f"Could not lock the screen: {exc}")


@tool(
    name="sleep_pc",
    description="Put the computer to sleep.",
    params={},
    examples=["put the pc to sleep", "sleep the computer", "go to sleep"],
    confirm=True,
    category="system",
)
def sleep_pc() -> ToolResult:
    try:
        subprocess.Popen(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return ToolResult(ok=True, summary="Going to sleep.")
    except Exception as exc:
        return ToolResult.fail(f"Could not sleep the PC: {exc}")


# Restart / shut down / sign out go through shutdown.exe with a short grace so
# a "never mind" within it still works (`cancel_shutdown`). All three ask first:
# they end every app on the machine, and a small model that hears "restart my
# pc" in the middle of a sentence about a render must not be able to act alone.


def _shutdown_exe(*flags: str) -> None:
    subprocess.Popen(
        ["shutdown.exe", *flags],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


POWER_GRACE_SECONDS = int(os.getenv("DEXTER_POWER_GRACE_SECONDS", "10") or 10)


def _power_prompt(verb: str) -> Callable[[Dict[str, Any]], str]:
    def _build(_args: Dict[str, Any]) -> str:
        return (
            f"{verb} closes everything that's open - anything unsaved is lost. "
            "Say yes and I'll do it."
        )

    return _build


@tool(
    name="restart_pc",
    description="Restart this computer. Destructive: every app closes. Ask first; only on a clear request.",
    params={},
    examples=["restart my pc", "reboot the computer", "restart windows", "reboot this machine"],
    confirm=True,
    category="system",
)
def restart_pc() -> ToolResult:
    try:
        _shutdown_exe("/r", "/t", str(POWER_GRACE_SECONDS), "/c", "Dexter is restarting the PC.")
        return ToolResult(
            ok=True,
            summary=f"Restarting in {POWER_GRACE_SECONDS} seconds. Say 'cancel the restart' to stop it.",
            data={"grace": POWER_GRACE_SECONDS},
        )
    except Exception as exc:
        return ToolResult.fail(f"Could not restart: {exc}")


restart_pc._dexter_confirm_prompt = _power_prompt("Restarting")


@tool(
    name="shutdown_pc",
    description="Shut this computer down (power off). Destructive: every app closes. Ask first.",
    params={},
    examples=["shut down my pc", "power off the computer", "turn off the pc", "shutdown windows"],
    confirm=True,
    category="system",
)
def shutdown_pc() -> ToolResult:
    try:
        _shutdown_exe("/s", "/t", str(POWER_GRACE_SECONDS), "/c", "Dexter is shutting the PC down.")
        return ToolResult(
            ok=True,
            summary=f"Shutting down in {POWER_GRACE_SECONDS} seconds. Say 'cancel the shutdown' to stop it.",
            data={"grace": POWER_GRACE_SECONDS},
        )
    except Exception as exc:
        return ToolResult.fail(f"Could not shut down: {exc}")


shutdown_pc._dexter_confirm_prompt = _power_prompt("Shutting down")


@tool(
    name="sign_out",
    description="Sign out of Windows (log off). Every app closes. Ask first.",
    params={},
    examples=["sign me out", "log off windows", "log out of my account", "sign out of windows"],
    confirm=True,
    category="system",
)
def sign_out() -> ToolResult:
    try:
        _shutdown_exe("/l")
        return ToolResult(ok=True, summary="Signing you out.")
    except Exception as exc:
        return ToolResult.fail(f"Could not sign out: {exc}")


sign_out._dexter_confirm_prompt = _power_prompt("Signing out")


@tool(
    name="cancel_shutdown",
    description="Cancel a restart or shutdown that is counting down.",
    params={},
    examples=["cancel the restart", "cancel the shutdown", "don't restart", "abort the shutdown"],
    category="system",
)
def cancel_shutdown() -> ToolResult:
    try:
        proc = subprocess.run(
            ["shutdown.exe", "/a"], capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            return ToolResult(ok=True, summary="Cancelled - the PC stays on.")
        return ToolResult(ok=True, summary="Nothing was counting down.", data={"stderr": (proc.stderr or "")[:200]})
    except Exception as exc:
        return ToolResult.fail(f"Could not cancel: {exc}")


@tool(
    name="empty_recycle_bin",
    description="Empty the Recycle Bin. Destructive: what is in it is gone for good. Ask first.",
    params={},
    examples=["empty the recycle bin", "clear the recycle bin", "empty my trash", "purge the recycle bin"],
    confirm=True,
    category="system",
)
def empty_recycle_bin() -> ToolResult:
    try:
        # SHEmptyRecycleBinW(hwnd, root, flags): no confirmation dialog, no
        # progress window, no sound. Dexter has already asked.
        flags = 0x0001 | 0x0002 | 0x0004
        rc = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        if rc in (0, -2147418113):  # S_OK, or E_UNEXPECTED when it was already empty
            return ToolResult(ok=True, summary="Recycle Bin emptied.")
        return ToolResult.fail(f"Windows refused to empty the Recycle Bin (code {rc}).")
    except Exception as exc:
        return ToolResult.fail(f"Could not empty the Recycle Bin: {exc}")


def _recycle_prompt(_args: Dict[str, Any]) -> str:
    return "Emptying the Recycle Bin can't be undone. Say yes and I'll do it."


empty_recycle_bin._dexter_confirm_prompt = _recycle_prompt


def availability() -> Dict[str, Any]:
    return {
        "volume": _HAS_PYCAW,
        "brightness": bool(_sbc),
        "clipboard": bool(_pyperclip),
        "screenshot": bool(_mss),
        "windows": bool(_gw),
    }
