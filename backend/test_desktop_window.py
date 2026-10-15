"""
The window has to open somewhere a person can see it (v13.4).

"The maximised version of this app is not opening" turned out not to be a show()
that failed. data/window.json held:

    {"width": 380, "height": 520, "x": -32000, "y": -32000}

(-32000, -32000) is the position Windows reports for a **minimized** window;
`_save_window` stored it on a close-while-minimized, and every launch afterwards
created the full window 32000 pixels off the desktop. It opened perfectly, where
nobody could see it - which from the owner's chair is indistinguishable from a
maximise button that does nothing.

So three things are pinned down here: the sentinel is never believed, it is never
written, and a window that somehow ends up off-screen is walked back at show time.
"""

import pytest

import desktop_app as shell_module
from desktop_app import DexterShell


SCREEN = {"left": 0, "top": 0, "width": 1920, "height": 1080}


@pytest.fixture(autouse=True)
def one_monitor(monkeypatch, tmp_path):
    """A single 1920x1080 monitor at the origin, and a throwaway window.json."""
    monkeypatch.setattr(shell_module, "virtual_screen", lambda: dict(SCREEN))
    monkeypatch.setattr(shell_module, "WINDOW_FILE", str(tmp_path / "window.json"))


class FakeWindow:
    """A pywebview Window as far as the geometry code is concerned."""

    def __init__(self, x=200, y=100, width=520, height=760, shows=True):
        self.x, self.y = x, y
        self.width, self.height = width, height
        self.on_top = False
        self._shows = shows
        self.calls = []

    def show(self):
        self.calls.append(("show",))
        if not self._shows:
            raise RuntimeError("the window never came up")

    def restore(self):
        self.calls.append(("restore",))

    def hide(self):
        self.calls.append(("hide",))

    def maximize(self):
        self.calls.append(("maximize",))

    def move(self, x, y):
        self.calls.append(("move", x, y))
        self.x, self.y = x, y

    def resize(self, width, height):
        self.calls.append(("resize", width, height))
        self.width, self.height = width, height


# ───────────────────────── is it visible at all ─────────────────────────


def test_the_minimized_sentinel_is_never_a_real_position():
    assert shell_module.on_screen(-32000, -32000, 380, 520) is False


def test_a_window_in_the_middle_of_the_screen_is_visible():
    assert shell_module.on_screen(700, 160, 520, 760) is True


def test_a_position_left_of_every_monitor_is_not_visible():
    """Remembered from a second screen that has since been unplugged."""
    assert shell_module.on_screen(-1900, 100, 520, 760) is False


def test_a_position_past_the_right_edge_is_not_visible():
    assert shell_module.on_screen(1910, 100, 520, 760) is False


def test_a_sliver_still_counts_as_visible():
    """Partly off the left edge is untidy, not unreachable."""
    assert shell_module.on_screen(-300, 40, 520, 760) is True


def test_a_missing_position_is_not_a_visible_one():
    assert shell_module.on_screen(None, None, 520, 760) is False


# ───────────────────────── what gets loaded ─────────────────────────


def _write(tmp_path, geometry):
    from jsonstore import save_json

    save_json(shell_module.WINDOW_FILE, geometry)


def test_the_off_screen_position_is_dropped_so_pywebview_centres_the_window(tmp_path):
    _write(tmp_path, {"width": 380, "height": 520, "x": -32000, "y": -32000})
    geometry = shell_module._load_window()
    assert "x" not in geometry and "y" not in geometry
    assert geometry["width"] == 380 and geometry["height"] == 520


def test_a_good_position_is_remembered(tmp_path):
    _write(tmp_path, {"width": 600, "height": 800, "x": 120, "y": 60})
    assert shell_module._load_window() == {"width": 600, "height": 800, "x": 120, "y": 60}


def test_a_size_bigger_than_the_screen_is_clamped_to_it(tmp_path):
    _write(tmp_path, {"width": 9000, "height": 9000, "x": 0, "y": 0})
    geometry = shell_module._load_window()
    assert geometry["width"] == SCREEN["width"]
    assert geometry["height"] == SCREEN["height"]


def test_nothing_saved_means_the_default_size_and_no_position():
    geometry = shell_module._load_window()
    assert geometry["width"] == shell_module.DEFAULT_WINDOW["width"]
    assert "x" not in geometry


# ───────────────────────── what gets saved ─────────────────────────


def test_a_minimized_window_does_not_overwrite_the_last_good_position(tmp_path):
    from jsonstore import load_json

    _write(tmp_path, {"width": 520, "height": 760, "x": 300, "y": 120})
    shell_module._save_window(FakeWindow(x=-32000, y=-32000, width=520, height=760))
    saved = load_json(shell_module.WINDOW_FILE, {})
    assert (saved["x"], saved["y"]) == (300, 120), "the bug that started all of this"
    assert saved["width"] == 520


def test_a_normal_close_remembers_where_the_window_was(tmp_path):
    from jsonstore import load_json

    shell_module._save_window(FakeWindow(x=410, y=90, width=640, height=900))
    saved = load_json(shell_module.WINDOW_FILE, {})
    assert (saved["x"], saved["y"], saved["width"], saved["height"]) == (410, 90, 640, 900)


def test_the_size_is_kept_even_when_the_position_is_not(tmp_path):
    from jsonstore import load_json

    shell_module._save_window(FakeWindow(x=-32000, y=-32000, width=700, height=900))
    saved = load_json(shell_module.WINDOW_FILE, {})
    assert (saved["width"], saved["height"]) == (700, 900)
    assert "x" not in saved, "no position was believable, so none is remembered"


# ───────────────────────── opening it ─────────────────────────


def test_opening_the_full_window_walks_an_off_screen_one_back(tmp_path):
    """Even with a poisoned window.json already obeyed at creation time, the
    maximise button has to put the window where it can be seen."""
    full = FakeWindow(x=-32000, y=-32000)
    shell = DexterShell()
    shell._full = full
    assert shell.open_full() is True
    assert any(call[0] == "move" for call in full.calls)
    assert shell_module.on_screen(full.x, full.y, full.width, full.height)


def test_an_on_screen_window_is_left_where_the_owner_put_it():
    full = FakeWindow(x=240, y=80)
    shell = DexterShell()
    shell._full = full
    shell.open_full()
    assert [call for call in full.calls if call[0] in ("move", "resize")] == []


def test_maximising_happens_after_the_window_is_back_on_screen():
    full = FakeWindow(x=-32000, y=-32000)
    shell = DexterShell()
    shell._full = full
    assert shell.maximize_full() is True
    names = [call[0] for call in full.calls]
    assert names.index("move") < names.index("maximize")


def test_the_popup_is_tucked_away_once_the_big_window_is_up():
    full, popup = FakeWindow(), FakeWindow()
    shell = DexterShell()
    shell._full, shell._popup, shell._popup_visible = full, popup, True
    shell.open_full(maximize=True)
    assert ("hide",) in popup.calls
    assert shell._popup_visible is False


def test_the_popup_stays_if_the_full_window_refuses_to_show():
    """Hiding the one window that works and showing nothing is the worst outcome."""
    full, popup = FakeWindow(shows=False), FakeWindow()
    shell = DexterShell()
    shell._full, shell._popup, shell._popup_visible = full, popup, True
    assert shell.open_full() is False
    assert ("hide",) not in popup.calls
    assert shell._popup_visible is True


# ───────────── the call that froze the interpreter (v13.7c) ─────────────
#
# `window.on_top = True` is one line, reads as harmless, and deadlocked the whole
# of Dexter twice. Caught live with py-spy:
#
#   Thread-14 (_call):  NtUserSetWindowPos (win32u.dll)   <- holds the GIL
#                       set_on_top (pywebview winforms)
#                       _raise (desktop_app.py)
#   Dummy-9 (GUI):      PyGILState_Ensure                 <- waits for the GIL
#   every other thread: PyEval_RestoreThread              <- waits for the GIL
#
# pywebview sets .NET's TopMost, that becomes a *synchronous* SetWindowPos, and
# pythonnet holds the GIL across the call - so the GUI thread could not acquire the
# GIL to answer. Windows marked both windows "not responding" while Python did
# nothing at all: no logging, no heartbeat, no scheduler, no voice.


class WindowThatNoticesTopMost(FakeWindow):
    """Records every deadlock-prone pywebview call instead of performing it."""

    def __init__(self, hwnd=0, **kwargs):
        # Before super(): FakeWindow sets `on_top`, which is a property here.
        self.marshalled = []
        super().__init__(**kwargs)
        # FakeWindow's own constructor touched it; only what `_raise` does counts.
        self.marshalled.clear()
        self.native = type("Native", (), {"Handle": hwnd})() if hwnd else None

    @property
    def on_top(self):
        self.marshalled.append("on_top-get")
        return False

    @on_top.setter
    def on_top(self, value):
        self.marshalled.append(f"on_top={value}")

    def restore(self):
        self.marshalled.append("restore")

    def move(self, x, y):
        self.marshalled.append("move")
        super().move(x, y)

    def resize(self, width, height):
        self.marshalled.append("resize")
        super().resize(width, height)


def test_raising_a_window_never_touches_on_top(monkeypatch):
    """The regression test for the freeze. Win32 only, from here on."""
    window = WindowThatNoticesTopMost(hwnd=4242)
    nudged, restored = [], []
    monkeypatch.setattr(shell_module, "_nudge_to_front", lambda h: nudged.append(h))
    monkeypatch.setattr(shell_module, "_restore", lambda h: restored.append(h))

    shell = DexterShell()
    shell._full = window
    assert shell.open_full() is True
    assert window.marshalled == [], f"marshalled .NET calls on a worker thread: {window.marshalled}"
    assert nudged == [4242], "raised through Win32 instead"
    assert restored == [4242]


def test_the_handle_is_found_once_and_remembered(monkeypatch):
    window = WindowThatNoticesTopMost(hwnd=99)
    assert DexterShell._handle(window) == 99
    window.native = None          # as if pywebview had let go of it
    assert DexterShell._handle(window) == 99, "cached, so a raise never re-reads .NET"


def test_a_window_with_no_handle_yet_still_works(monkeypatch):
    """Before the native window exists there is nothing to call Win32 on, and the
    pywebview setters are then the only option - and safe, because nothing is
    being marshalled across threads yet."""
    window = WindowThatNoticesTopMost(hwnd=0, x=-32000, y=-32000)
    shell = DexterShell()
    shell._full = window
    assert shell.open_full() is True
    assert "move" in window.marshalled, "fell back, rather than leaving it off-screen"


def test_moving_a_real_window_goes_through_win32(monkeypatch):
    placed = {}
    monkeypatch.setattr(shell_module, "_place",
                        lambda h, x, y, w, ht: placed.update(hwnd=h, x=x, y=y) or True)
    window = WindowThatNoticesTopMost(hwnd=7, x=-32000, y=-32000)
    DexterShell._bring_on_screen(window)
    assert placed["hwnd"] == 7
    assert "move" not in window.marshalled and "resize" not in window.marshalled


def test_the_win32_helpers_are_harmless_without_a_handle():
    """They are called on paths that run before a window exists."""
    assert shell_module._place(0, 1, 2, 3, 4) is False
    shell_module._nudge_to_front(0)
    shell_module._restore(0)
