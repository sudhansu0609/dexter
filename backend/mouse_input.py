"""
The mouse, driven by Windows' own input queue (v13).

One module, one job: move the pointer, press its buttons, turn its wheel. It
exists so the hand tracker (and anything after it) never talks to ctypes
directly, and so a test can replace the whole thing with a recorder - no test in
this repo may move the real pointer.

`SendInput` rather than `SetCursorPos`: SetCursorPos teleports the cursor without
telling anyone, and applications that track movement (games, drag handles,
canvases) do not see it. SendInput goes through the same path a real mouse does,
so a drag started by a pinch behaves like a drag started by a hand.

Absolute coordinates in Windows' input API are 0..65535 across the *virtual*
desktop, which is what makes multi-monitor work; pixels are converted here and
nowhere else.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any, Dict, List, Optional, Tuple

# ───────────────────────── Win32 plumbing ─────────────────────────

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

WHEEL_DELTA = 120

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _INPUT(ctypes.Structure):
    class _VALUE(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", _VALUE)]


def _user32():
    try:
        return ctypes.windll.user32
    except Exception:  # pragma: no cover - not Windows
        return None


class MouseError(RuntimeError):
    """Something Dexter should say, not a traceback."""


def available() -> bool:
    return _user32() is not None


def virtual_screen() -> Tuple[int, int, int, int]:
    """(left, top, width, height) across every monitor."""
    user = _user32()
    if user is None:
        raise MouseError("Mouse control needs Windows.")
    metrics = user.GetSystemMetrics
    return (
        int(metrics(SM_XVIRTUALSCREEN)), int(metrics(SM_YVIRTUALSCREEN)),
        int(metrics(SM_CXVIRTUALSCREEN)) or 1, int(metrics(SM_CYVIRTUALSCREEN)) or 1,
    )


def position() -> Tuple[int, int]:
    user = _user32()
    if user is None:
        raise MouseError("Mouse control needs Windows.")
    point = wintypes.POINT()
    user.GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y)


def _send(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
    user = _user32()
    if user is None:
        raise MouseError("Mouse control needs Windows.")
    event = _INPUT(type=INPUT_MOUSE,
                   mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags,
                                  time=0, dwExtraInfo=None))
    sent = user.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
    if sent != 1:
        raise MouseError("Windows refused the mouse event (SendInput returned 0).")


# ───────────────────────── the API the rest of Dexter uses ─────────────────────────


def move_to(x: int, y: int) -> Tuple[int, int]:
    """Absolute move, in screen pixels, clamped to the virtual desktop."""
    left, top, width, height = virtual_screen()
    px = max(left, min(int(x), left + width - 1))
    py = max(top, min(int(y), top + height - 1))
    # 0..65535 across the virtual desktop, per SendInput's contract. Rounded, not
    # truncated: across a 3840-wide desktop truncation lands a pixel short, and a
    # pointer that is always one pixel left of where it was asked to be is the
    # kind of thing that looks like drift.
    nx = round((px - left) * 65535 / max(1, width - 1))
    ny = round((py - top) * 65535 / max(1, height - 1))
    _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)
    return px, py


def move_by(dx: int, dy: int) -> None:
    _send(MOUSEEVENTF_MOVE, int(dx), int(dy))


def down(button: str = "left") -> None:
    flags = _BUTTONS.get((button or "left").lower())
    if not flags:
        raise MouseError(f"There is no '{button}' mouse button.")
    _send(flags[0])


def up(button: str = "left") -> None:
    flags = _BUTTONS.get((button or "left").lower())
    if not flags:
        raise MouseError(f"There is no '{button}' mouse button.")
    _send(flags[1])


def click(button: str = "left", double: bool = False) -> None:
    down(button)
    up(button)
    if double:
        time.sleep(0.06)
        down(button)
        up(button)


def scroll(notches: float = -1.0, horizontal: bool = False) -> None:
    """Wheel. Positive is up / right, as Windows counts it."""
    amount = int(round(float(notches) * WHEEL_DELTA))
    if amount == 0:
        return
    _send(MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, data=amount & 0xFFFFFFFF)


class Recorder:
    """A stand-in that records instead of acting. Used by the tests and by
    `hand_mouse(dry_run=True)`, which is how the gestures can be tried out
    without the pointer moving."""

    def __init__(self) -> None:
        self.events: List[Tuple[str, Any]] = []
        self.cursor: Tuple[int, int] = (0, 0)

    def move_to(self, x: int, y: int) -> Tuple[int, int]:
        self.cursor = (int(x), int(y))
        self.events.append(("move_to", self.cursor))
        return self.cursor

    def move_by(self, dx: int, dy: int) -> None:
        self.cursor = (self.cursor[0] + int(dx), self.cursor[1] + int(dy))
        self.events.append(("move_by", (int(dx), int(dy))))

    def down(self, button: str = "left") -> None:
        self.events.append(("down", button))

    def up(self, button: str = "left") -> None:
        self.events.append(("up", button))

    def click(self, button: str = "left", double: bool = False) -> None:
        self.events.append(("click", (button, bool(double))))

    def scroll(self, notches: float = -1.0, horizontal: bool = False) -> None:
        self.events.append(("scroll", (float(notches), bool(horizontal))))

    def position(self) -> Tuple[int, int]:
        return self.cursor

    def virtual_screen(self) -> Tuple[int, int, int, int]:
        return (0, 0, 1920, 1080)

    def kinds(self) -> List[str]:
        return [name for name, _ in self.events]


class System:
    """The real mouse, behind the same interface as `Recorder`."""

    move_to = staticmethod(move_to)
    move_by = staticmethod(move_by)
    down = staticmethod(down)
    up = staticmethod(up)
    click = staticmethod(click)
    scroll = staticmethod(scroll)
    position = staticmethod(position)
    virtual_screen = staticmethod(virtual_screen)
