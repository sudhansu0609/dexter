"""
Reading a window's title without betting the process on it (v13.7).

`GetWindowTextW` looks like a getter. For a window owned by **another process** it
is not: Windows implements it as a `SendMessage(WM_GETTEXT)`, which waits for that
window's thread to pump messages, **with no timeout**. If the other process is hung,
the caller hangs too - forever, with no exception and nothing logged.

Dexter did this on every chat turn (to tell the model "the user currently has this
window in focus") and every 30 seconds in the activity sampler. So one hung window
in the foreground was a total, silent freeze of the assistant, and that is what
happened: a misresolved "open the hand tracking" launched a console utility that
waits on stdin forever, its console came to the foreground, and the next turn went
to read its title and never came back.

Two ways not to do that, in order of preference:

1. `InternalGetWindowText` reads the caption Windows already has stored for the
   window. It asks the owning thread for nothing, so it cannot block. This is what
   Task Manager and Spy++ use to list windows that may be hung.
2. `SendMessageTimeoutW` with `SMTO_ABORTIFHUNG` - ask, but with a deadline, and
   give up immediately if the window is already known to be unresponsive.

An empty string is a perfectly good answer. A title is context for a model; no
context is a worse answer, and waiting forever is not an answer at all.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys
from typing import Any

#: Long enough for any real title bar; titles are context, not content.
MAX_CHARS = 512

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
SMTO_ABORTIFHUNG = 0x0002
SMTO_BLOCK = 0x0001

#: How long to wait for a window that will not answer. A quarter of a second is
#: already far longer than a healthy window needs.
DEFAULT_TIMEOUT_MS = 250

_user32: Any = None


def _load() -> Any:
    global _user32
    if _user32 is not None:
        return _user32
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    try:
        user32.InternalGetWindowText.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int,
        ]
        user32.InternalGetWindowText.restype = ctypes.c_int
    except Exception:
        pass
    try:
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_void_p,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
        ]
        user32.SendMessageTimeoutW.restype = wintypes.LPARAM
    except Exception:
        pass
    _user32 = user32
    return user32


def window_text(hwnd: int, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    """The window's title, or "" - never a wait longer than `timeout_ms`."""
    if not hwnd:
        return ""
    user32 = _load()
    if user32 is None:
        return ""
    buffer = ctypes.create_unicode_buffer(MAX_CHARS)

    # 1. The stored caption. No message, so no way to be blocked.
    try:
        written = int(user32.InternalGetWindowText(hwnd, buffer, MAX_CHARS))
        if written > 0:
            return buffer.value
    except Exception:
        pass

    # 2. Ask, with a deadline.
    try:
        result = ctypes.c_size_t(0)
        ok = user32.SendMessageTimeoutW(
            hwnd, WM_GETTEXT, MAX_CHARS, ctypes.cast(buffer, ctypes.c_void_p),
            SMTO_ABORTIFHUNG | SMTO_BLOCK, int(max(1, timeout_ms)), ctypes.byref(result),
        )
        if ok:
            return buffer.value
    except Exception:
        pass
    return ""


def foreground_window() -> int:
    user32 = _load()
    if user32 is None:
        return 0
    try:
        return int(user32.GetForegroundWindow())
    except Exception:
        return 0


def foreground_text(timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    return window_text(foreground_window(), timeout_ms=timeout_ms)


def window_pid(hwnd: int) -> int:
    """The process behind a window. Purely local - never blocks."""
    user32 = _load()
    if user32 is None or not hwnd:
        return 0
    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0
