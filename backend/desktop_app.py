"""
Dexter desktop shell: FastAPI in-process plus two native WebView2 windows.

v8: Dexter is a small pop-up by default - one message and one line to answer
it, bottom-right, always on top, hidden until it has something to say or the
owner calls it (tray click / Ctrl+Alt+D / Esc to dismiss). The full app (the
conversation, mission, planner, Studio) is a second window opened from the
tray menu or the pop-up's expand button. Set DEXTER_START_MODE=full to open
the full window at launch instead.

Launched by Dexter.vbs (hidden, via pythonw - no console) or start_dexter.bat
(console, for development). Everything that used to be a silent failure under
pythonw now ends in a message box: a failed preflight, a backend that never
answers, or an exception on the way up (roadmap v5, 1.6).
"""

import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from typing import Any, Callable, Dict, Optional

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import env_boot  # noqa: E402,F401  - loads .env for every entry point

try:
    import logging_setup  # noqa: E402,F401  - route prints into data/dexter.log
    logging_setup.setup()
except Exception:  # pragma: no cover - logging is a convenience, not a gate
    pass

import buzzcaf_ports  # noqa: E402  - the shared port ledger (GUARDIAN_PLAN section 11)

HOST = "127.0.0.1"
#: The address uvicorn binds to. Loopback identity stays `HOST`; `DEXTER_BIND`
#: opens the socket up (0.0.0.0) so the phone can reach Dexter over Tailscale.
BIND_HOST = os.getenv("DEXTER_BIND", HOST).strip() or HOST
#: The port Dexter would *like*.
PREFERRED_PORT = int(os.getenv("DEXTER_PORT") or 8098)

MUTEX_NAME = "Local\\DexterDesktopAppSingleInstanceMutex"
_instance_mutex = None


def _is_already_running() -> bool:
    """Return True if another instance of Dexter Desktop is already running."""
    global _instance_mutex
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        last_error = kernel32.GetLastError()
        if last_error == ERROR_ALREADY_EXISTS:
            return True
        _instance_mutex = mutex
        return False
    except Exception as exc:
        print(f"[Desktop] Single-instance mutex check: {exc}")
        return False


def _activate_existing_instance() -> None:
    """Signal an existing instance of Dexter to unhide and focus its window."""
    # 1. First notify the active Dexter backend via POST /api/dexter/show
    for port in (PREFERRED_PORT, 8098, 8099):
        try:
            req = urllib.request.Request(
                f"http://{HOST}:{port}/api/dexter/show",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status in (200, 204):
                    print(f"[Desktop] Existing instance on port {port} signaled to show.")
                    break
        except Exception:
            pass

    # 2. Also bring the native window forward via Win32 FindWindow
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            SW_RESTORE = 9
            hwnd = user32.FindWindowW(None, "Dexter")
            if hwnd:
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                print(f"[Desktop] Brought existing Dexter window (HWND {hwnd}) to front.")
        except Exception as exc:
            print(f"[Desktop] FindWindow activation warning: {exc}")


PORT = buzzcaf_ports.pick_port(PREFERRED_PORT, host=HOST)
# Hand the decision to main.py before it is imported: its CORS list, its
# /health body and this window all have to name the same number.
os.environ["DEXTER_BOUND_PORT"] = str(PORT)
if PORT != PREFERRED_PORT:
    print(f"[Ports] {PREFERRED_PORT} is taken; Dexter is stepping forward to {PORT}.")
#: The identity route (rule 4), not /api/dexter/health: this is the one that
#: says *who* is on the port, and it is what the ledger entry points at.
HEALTH_URL = f"http://{HOST}:{PORT}/health"
DEV_URL = os.getenv("DEXTER_DEV_URL", "http://localhost:3006")
DIST_INDEX = os.path.join(ROOT_DIR, "dist", "index.html")
SRC_DIR = os.path.join(ROOT_DIR, "src")
WINDOW_FILE = os.path.join(ROOT_DIR, "data", "window.json")

DEFAULT_WINDOW = {"width": 520, "height": 760}
MIN_WIDTH, MIN_HEIGHT = 380, 520
POPUP_SIZE = (380, 250)
POPUP_MARGIN = (16, 72)   # from the right edge, and above the taskbar

RUN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "DexterTray"


def is_autostart_enabled() -> bool:
    """Check if Dexter is registered to auto-start in Windows CurrentVersion\\Run."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_READ) as key:
            try:
                val, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
                return bool(val)
            except FileNotFoundError:
                return False
    except Exception as exc:
        print(f"[Desktop] Could not check autostart: {exc}")
        return False


def set_autostart(enabled: bool) -> bool:
    """Register or unregister Dexter in Windows CurrentVersion\\Run."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                exe_path = os.path.join(ROOT_DIR, "Dexter.exe")
                if os.path.exists(exe_path):
                    cmd = f'"{exe_path}" --tray'
                else:
                    vbs_path = os.path.join(ROOT_DIR, "Dexter.vbs")
                    wscript_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
                    cmd = f'"{wscript_path}" "{vbs_path}" --tray'
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
                print(f"[Desktop] Autostart enabled: {cmd}")
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                    print("[Desktop] Autostart disabled.")
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        print(f"[Desktop] Could not set autostart: {exc}")
        return False


def get_start_mode() -> str:
    """
    Resolve startup mode: 'tray' | 'popup' | 'full'.
    Supports CLI arguments (--tray, --hidden, --minimized, --full, --popup)
    and environment variable DEXTER_START_MODE.
    """
    args = [a.lower() for a in sys.argv[1:]]
    if any(a in args for a in ("--tray", "--hidden", "--minimized")):
        return "tray"
    if "--full" in args:
        return "full"
    if "--popup" in args:
        return "popup"

    mode = os.getenv("DEXTER_START_MODE", "popup").strip().lower()
    if mode in ("tray", "hidden", "minimized"):
        return "tray"
    if mode == "full":
        return "full"
    return "popup"


START_MODE = get_start_mode()

MB_ERROR, MB_WARNING = 0x10, 0x30

# Closing the window used to end the process, and with it the scheduler: no
# reminder could fire once Dexter was out of the way. With the tray on, close
# hides the window and everything keeps running; Quit lives in the tray menu.
TRAY_ENABLED = os.getenv("DEXTER_TRAY", "1") != "0"
HOTKEY = os.getenv("DEXTER_HOTKEY", "ctrl+alt+d")



def _message_box(title: str, text: str, icon: int = MB_ERROR) -> None:
    """A dialog the user can actually see; pythonw has no console to print to."""
    print(f"[Desktop] {title}: {text}")
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, title, icon)
        except Exception:
            pass


# ───────────────────────── preflight ─────────────────────────


def _preflight_or_exit() -> None:
    """Run the same checks start_dexter.bat runs, and refuse to open on a real failure."""
    try:
        import preflight
    except Exception as exc:
        print(f"[Desktop] Preflight unavailable, continuing: {exc}")
        return

    report = preflight.run_fast_checks()
    for line in report.problem_lines():
        print(f"[Preflight] {line}")
    if report.failed:
        _message_box(
            "Dexter cannot start",
            "Preflight found problems that stop Dexter from working:\n\n"
            + "\n".join(report.problem_lines())
            + "\n\nFix them and launch again (start_dexter.bat shows the full report).",
        )
        sys.exit(1)

    # The slow probes (model servers, GPU, Windows integrations) took ~7 s and
    # only ever warn; run them after the window is up and log what they find.
    def _slow_checks() -> None:
        try:
            for line in preflight.run_slow_checks().problem_lines():
                print(f"[Preflight] {line}")
        except Exception as exc:
            print(f"[Preflight] slow checks skipped: {exc}")

    threading.Thread(target=_slow_checks, name="dexter-preflight-slow", daemon=True).start()


# ───────────────────────── UI bundle ─────────────────────────


def _newest_source_mtime() -> float:
    newest = 0.0
    for base, dirs, files in os.walk(SRC_DIR):
        dirs[:] = [d for d in dirs if d not in ("test", "__tests__")]
        for name in files:
            if name.endswith((".ts", ".tsx", ".css", ".html")):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(base, name)))
                except OSError:
                    pass
    return newest


def _dist_state() -> str:
    """'missing' | 'stale' | 'fresh' - what the window would actually show."""
    if not os.path.exists(DIST_INDEX):
        return "missing"
    try:
        built = os.path.getmtime(DIST_INDEX)
    except OSError:
        return "missing"
    return "stale" if _newest_source_mtime() > built + 1 else "fresh"


def _rebuild_dist() -> bool:
    """`npm run build`, hidden. Only when DEXTER_AUTOBUILD=1; it needs Node."""
    import shutil

    npm = shutil.which("npm")
    if not npm:
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        done = subprocess.run(
            [npm, "run", "build"], cwd=ROOT_DIR, capture_output=True, text=True,
            timeout=300, creationflags=flags,
        )
        return done.returncode == 0
    except Exception as exc:
        print(f"[Desktop] Rebuild failed: {exc}")
        return False


def _resolve_target_url() -> str:
    state = _dist_state()
    if state == "missing":
        # No bundle at all: assume a Vite dev server (start_dexter.bat / npm run dev).
        print(f"[Desktop] dist/ missing; opening the dev server at {DEV_URL}")
        return DEV_URL
    if state == "stale":
        if os.getenv("DEXTER_AUTOBUILD", "0") == "1" and _rebuild_dist():
            print("[Desktop] UI bundle was stale; rebuilt.")
        else:
            # Serving yesterday's UI silently is how "my change did nothing"
            # happens. Say so once, then open what exists.
            _message_box(
                "Dexter UI bundle is out of date",
                "Files under src/ are newer than dist/. The window will show the "
                "previous build.\n\nRun `npm run build` (or set DEXTER_AUTOBUILD=1 "
                "in .env) to pick up the changes.",
                MB_WARNING,
            )
    return f"http://{HOST}:{PORT}/"


# ───────────────────────── window geometry ─────────────────────────


# ── raising a window without deadlocking the interpreter (v13.7c) ──
#
# See `DexterShell._raise` for the whole story. In short: pywebview's `on_top`
# becomes a synchronous SetWindowPos inside .NET, pythonnet holds the GIL across
# it, and the GUI thread needs the GIL to answer - so the process stops dead.
# These do the same job through ctypes, which releases the GIL, and ask Windows to
# *post* the change rather than wait for it.
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
SWP_NOACTIVATE, SWP_ASYNCWINDOWPOS = 0x0010, 0x4000
SW_RESTORE = 9


def _nudge_to_front(handle: int) -> None:
    """Lift a window above the others in this process, without waiting for it."""
    if not handle or sys.platform != "win32":
        return
    try:
        import ctypes

        flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
        user32 = ctypes.windll.user32
        # Topmost, then back: the pop-up is permanently on top, so a full window
        # that merely rises to the front of the normal band still ends up behind
        # it. Asking twice is what puts it above everything for the moment it
        # needs, without making it sticky.
        user32.SetWindowPos(handle, HWND_TOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(handle, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    except Exception as exc:
        print(f"[Desktop] Could not raise the window: {exc}")


def _place(handle: int, x: int, y: int, width: int, height: int) -> bool:
    """Move and size a window, posted rather than sent.

    `window.move()` and `window.resize()` are the same hazard as `on_top`: .NET
    property sets that end in a synchronous SetWindowPos while pythonnet holds the
    GIL. This is called to rescue a window that opened off-screen, which is not a
    moment to risk freezing the process.
    """
    if not handle or sys.platform != "win32":
        return False
    try:
        import ctypes

        flags = SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
        return bool(ctypes.windll.user32.SetWindowPos(
            handle, 0, int(x), int(y), int(width), int(height), flags))
    except Exception as exc:
        print(f"[Desktop] Could not place the window: {exc}")
        return False


def _restore(handle: int) -> None:
    """Un-minimize, through Win32 rather than pywebview's `restore()`.

    Same reasoning: `WindowState = Normal` is another marshalled .NET property
    that moves the window, and this path has to be safe to call from any thread.
    """
    if not handle or sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, SW_RESTORE)
    except Exception:
        pass


def virtual_screen() -> Dict[str, int]:
    """The rectangle every monitor together covers, for judging a saved position."""
    try:
        import ctypes

        metrics = ctypes.windll.user32.GetSystemMetrics
        return {"left": int(metrics(76)), "top": int(metrics(77)),
                "width": int(metrics(78)) or 1920, "height": int(metrics(79)) or 1080}
    except Exception:
        return {"left": 0, "top": 0, "width": 1920, "height": 1080}


def on_screen(x: Any, y: Any, width: int, height: int) -> bool:
    """Would a window at this position actually be visible to somebody?

    This is the check whose absence hid the main window (v13.4). Windows reports a
    **minimized** window's position as (-32000, -32000); `_save_window` stored that
    on a close-while-minimized, and every launch afterwards created the window
    32000 pixels off-screen. It opened perfectly, where nobody could see it - which
    from the outside is exactly "the maximised version of this app is not opening".
    """
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return False
    x, y = int(x), int(y)
    if x <= -30000 or y <= -30000:
        return False  # the minimized sentinel, or nonsense
    screen = virtual_screen()
    # At least this much of the title bar has to be inside the desktop for the
    # window to be reachable with a mouse.
    margin = 120
    if x + width < screen["left"] + margin or x > screen["left"] + screen["width"] - margin:
        return False
    if y + height < screen["top"] + margin or y > screen["top"] + screen["height"] - margin:
        return False
    return True


def _load_window() -> Dict[str, Any]:
    try:
        from jsonstore import load_json

        saved = load_json(WINDOW_FILE, {}) or {}
    except Exception:
        saved = {}
    geometry = dict(DEFAULT_WINDOW)
    for key in ("width", "height", "x", "y"):
        value = saved.get(key)
        if isinstance(value, (int, float)):
            geometry[key] = int(value)
    screen = virtual_screen()
    geometry["width"] = max(MIN_WIDTH, min(int(geometry["width"]), screen["width"]))
    geometry["height"] = max(MIN_HEIGHT, min(int(geometry["height"]), screen["height"]))
    # A remembered position that is off every monitor (an unplugged second screen,
    # or the minimized sentinel) is dropped rather than honoured: pywebview centres
    # the window when x and y are absent.
    if not on_screen(geometry.get("x"), geometry.get("y"),
                     geometry["width"], geometry["height"]):
        if geometry.get("x") is not None or geometry.get("y") is not None:
            print(f"[Desktop] Ignoring a remembered window position that is off-screen "
                  f"({geometry.get('x')},{geometry.get('y')}); centring instead.")
        geometry.pop("x", None)
        geometry.pop("y", None)
    return geometry


def _save_window(window: Any) -> None:
    """Remember the size and position - unless the window is minimized.

    A minimized window reports (-32000, -32000), and saving that is what made the
    main window invisible on every subsequent launch. The size is kept either way;
    only a believable position is written.
    """
    try:
        from jsonstore import load_json, save_json

        width, height = int(window.width), int(window.height)
        geometry: Dict[str, Any] = {"width": max(MIN_WIDTH, width),
                                    "height": max(MIN_HEIGHT, height)}
        try:
            x, y = int(window.x), int(window.y)
        except Exception:
            x = y = None
        if on_screen(x, y, width, height):
            geometry["x"], geometry["y"] = x, y
        else:
            previous = load_json(WINDOW_FILE, {}) or {}
            for key in ("x", "y"):
                value = previous.get(key)
                if isinstance(value, (int, float)) and on_screen(
                    previous.get("x"), previous.get("y"), width, height
                ):
                    geometry[key] = int(value)
            print("[Desktop] The window was minimized or off-screen; keeping the last "
                  "good position instead of remembering this one.")
        save_json(WINDOW_FILE, geometry)
    except Exception as exc:
        print(f"[Desktop] Could not remember window size: {exc}")


# ───────────────────────── tray + hotkey (roadmap v5, 5.3) ─────────────────────────


def _tray_icon_image():
    for name in ("icon.png", "icon.ico"):
        candidate = os.path.join(ROOT_DIR, "assets", name)
        if os.path.exists(candidate):
            try:
                from PIL import Image

                img = Image.open(candidate)
                img.load()
                return img
            except Exception as exc:
                print(f"[Desktop] Could not load icon from {candidate}: {exc}")

    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=16, fill=(79, 70, 229, 255))
    draw.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 255))
    return image


class DexterShell:
    """What the pages can ask of the shell (window.pywebview.api.*), and what
    the tray and hotkey do. Both windows share one instance.

    Everything that is not a method is underscore-prefixed on purpose: pywebview
    walks every public attribute of a js_api object to mirror it into
    JavaScript, and a Window held publicly here sent it into an endless
    recursion that froze the whole app at startup (v8).
    """

    def __init__(self) -> None:
        self._full = None
        self._popup = None
        self._popup_visible = False
        self._quitting = False

    @property
    def _windows(self):
        return self._full, self._popup

    # ── pop-up ──
    def show_popup(self) -> bool:
        if self._popup is None:
            return False
        # Same treatment as the full window: shown is not the same as in front.
        if not self._raise(self._popup):
            return False
        self._popup_visible = True
        return True

    def hide_popup(self) -> bool:
        if self._popup is None:
            return False
        try:
            self._popup.hide()
            self._popup_visible = False
            return True
        except Exception as exc:
            print(f"[Desktop] Could not hide the pop-up: {exc}")
            return False

    def toggle_popup(self) -> bool:
        return self.hide_popup() if self._popup_visible else self.show_popup()

    # ── full window ──
    def _raise(self, window) -> bool:
        """Show a window *and* put it in front."""
        try:
            window.show()
        except Exception as exc:
            print(f"[Desktop] Could not show the window: {exc}")
            return False
        handle = self._handle(window)
        if handle:
            _restore(handle)
            _nudge_to_front(handle)
            self._foreground(window)
        return True

    @staticmethod
    def _handle(window) -> int:
        """The window's own HWND, so Win32 can be used directly."""
        cached = getattr(window, "_dexter_hwnd", 0)
        if cached:
            return int(cached)
        native = getattr(window, "native", None)
        if native is None:
            return 0
        for attribute in ("Handle", "handle", "winId"):
            try:
                value = getattr(native, attribute, None)
                if value is None:
                    continue
                handle = int(value.ToInt64()) if hasattr(value, "ToInt64") else int(value)
                if handle:
                    try:
                        window._dexter_hwnd = handle
                    except Exception:
                        pass
                    return handle
            except Exception:
                continue
        return 0

    @staticmethod
    def _foreground(window) -> None:
        """SetForegroundWindow on the window's own handle, when we can find it."""
        handle = DexterShell._handle(window)
        if not handle:
            return
        try:
            import win32con  # type: ignore
            import win32gui  # type: ignore

            if win32gui.IsIconic(handle):
                win32gui.ShowWindow(handle, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(handle)
        except Exception as exc:
            print(f"[Desktop] Could not bring the window forward: {exc}")

    def open_full(self, maximize: bool = False) -> bool:
        """Bring the main window up. The pop-up's maximise button calls this, so
        it also tucks the pop-up away: one window at a time is the gesture."""
        if self._full is None:
            return False
        if not self._raise(self._full):
            return False
        # It may have been *created* off-screen from a stale saved position (the
        # window.json (-32000, -32000) bug), in which case it is now shown where
        # nobody can see it. Put it back on the desktop before claiming success.
        self._bring_on_screen(self._full)
        if maximize:
            try:
                self._full.maximize()
            except Exception as exc:
                print(f"[Desktop] Could not maximize the window: {exc}")
        # The small window has been replaced by the big one; leaving it floating
        # on top of what the owner just asked for is the wrong answer. Only once
        # the big one is genuinely up, though - hiding the pop-up and showing
        # nothing is the worst outcome of the two.
        if self._popup is not None and self._popup_visible and self._visible(self._full):
            self.hide_popup()
        return True

    @staticmethod
    def _visible(window) -> bool:
        """Is this window actually somewhere a person could see it?"""
        try:
            return on_screen(window.x, window.y, int(window.width), int(window.height))
        except Exception:
            # No geometry available: assume the show worked rather than refusing.
            return True

    @staticmethod
    def _bring_on_screen(window) -> None:
        if DexterShell._visible(window):
            return
        screen = virtual_screen()
        try:
            width = max(MIN_WIDTH, min(int(window.width), screen["width"]))
            height = max(MIN_HEIGHT, min(int(window.height), screen["height"]))
        except Exception:
            width, height = DEFAULT_WINDOW["width"], DEFAULT_WINDOW["height"]
        x = screen["left"] + max(0, (screen["width"] - width) // 2)
        y = screen["top"] + max(0, (screen["height"] - height) // 3)
        if _place(DexterShell._handle(window), x, y, width, height):
            print(f"[Desktop] The window was off-screen; moved it to {x},{y}.")
        else:
            # No handle (not yet created): pywebview's own setters are the only
            # option left, and at that point nothing is marshalled cross-thread.
            try:
                window.resize(width, height)
                window.move(x, y)
            except Exception as exc:
                print(f"[Desktop] Could not move the window back on-screen: {exc}")

    def maximize_full(self) -> bool:
        """"Maximise Dexter": the full window, filling the screen."""
        return self.open_full(maximize=True)

    def hide_full(self) -> bool:
        if self._full is None:
            return False
        try:
            self._full.hide()
            return True
        except Exception as exc:
            print(f"[Desktop] Could not hide the window: {exc}")
            return False


def _enable_webview2_mic() -> None:
    """
    Grant microphone access to the WebView2 (edgechromium) desktop shell.

    pywebview registers a media-permission handler only in its Qt backend, not
    in the WebView2 backend this app runs on, so getUserMedia is never granted
    and the mic stays dead. We wrap pywebview's own CoreWebView2-ready hook to
    auto-allow microphone requests for both windows. Fully guarded: a failure
    leaves the shell as-is and never blocks startup (v10).
    """
    try:
        from webview.platforms import edgechromium as ec
        from Microsoft.Web.WebView2.Core import (
            CoreWebView2PermissionKind,
            CoreWebView2PermissionState,
        )
    except Exception as exc:
        print(f"[Desktop] Mic-permission patch unavailable (non-WebView2?): {exc}")
        return

    original_ready = ec.EdgeChrome.on_webview_ready
    if getattr(original_ready, "_dexter_mic_patch", False):
        return

    def patched_ready(self, sender, args):
        original_ready(self, sender, args)
        try:
            core = self.webview.CoreWebView2

            def on_permission(_s, evt):
                if evt.PermissionKind == CoreWebView2PermissionKind.Microphone:
                    evt.State = CoreWebView2PermissionState.Allow

            core.PermissionRequested += on_permission
            print("[Desktop] WebView2 microphone permission handler attached.")
        except Exception as exc:
            print(f"[Desktop] Could not attach mic-permission handler: {exc}")

    patched_ready._dexter_mic_patch = True  # type: ignore[attr-defined]
    ec.EdgeChrome.on_webview_ready = patched_ready


def _popup_position(width: int, height: int):
    """Bottom-right of the primary screen, clear of the taskbar."""
    try:
        import webview

        screen = webview.screens[0]
        return int(screen.width - width - POPUP_MARGIN[0]), int(screen.height - height - POPUP_MARGIN[1])
    except Exception:
        return None, None


def _install_tray(window, shell: Optional["DexterShell"] = None) -> Optional[Dict[str, Any]]:
    """
    A tray icon (click: the pop-up; menu: Ask Dexter / Open Dexter / Reindex /
    Quit) and a global hotkey that brings the pop-up up. Returns the shared
    state dict, or None when the optional packages are missing (the window
    then closes normally).
    """
    if not TRAY_ENABLED:
        return None
    try:
        import pystray
    except Exception as exc:
        print(f"[Desktop] Tray unavailable ({exc}); close will quit.")
        return None

    state: Dict[str, Any] = {"quit": False, "icon": None}

    def show(icon=None, item=None) -> None:
        if shell is not None:
            shell.open_full()
            return
        try:
            window.show()
            # Win32, not `window.restore()` - the tray runs on its own thread too.
            _restore(DexterShell._handle(window))
        except Exception as exc:
            print(f"[Desktop] Could not show the window: {exc}")

    def ask(icon=None, item=None) -> None:
        if shell is not None:
            shell.toggle_popup()
        else:
            show()

    def reindex(icon=None, item=None) -> None:
        def _run() -> None:
            try:
                import knowledge_ingest

                stats = knowledge_ingest.reindex()
                print(f"[Desktop] Reindex from tray: {stats['updated']} updated, {stats['removed']} removed.")
            except Exception as exc:
                print(f"[Desktop] Reindex failed: {exc}")

        threading.Thread(target=_run, name="dexter-tray-reindex", daemon=True).start()

    def quit_dexter(icon=None, item=None) -> None:
        state["quit"] = True
        if shell is not None:
            shell._quitting = True
        try:
            if state["icon"] is not None:
                state["icon"].stop()
        except Exception:
            pass
        try:
            if shell is not None and shell._popup is not None:
                shell._popup.destroy()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            os._exit(0)

    def open_logs(icon=None, item=None) -> None:
        log_file = os.path.join(ROOT_DIR, "data", "dexter.log")
        if not os.path.exists(log_file):
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "a", encoding="utf-8"):
                pass
        try:
            if sys.platform == "win32":
                os.startfile(log_file)
        except Exception as exc:
            print(f"[Desktop] Could not open log file: {exc}")

    def toggle_autostart(icon=None, item=None) -> None:
        new_state = not is_autostart_enabled()
        set_autostart(new_state)

    menu = pystray.Menu(
        pystray.MenuItem("Ask Dexter", ask, default=True),
        pystray.MenuItem("Open Dexter", show),
        pystray.MenuItem("Reindex knowledge", reindex),
        pystray.MenuItem("Open logs", open_logs),
        pystray.MenuItem("Autostart with Windows", toggle_autostart, checked=lambda item: is_autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Dexter", quit_dexter),
    )
    try:
        icon_title = f"Dexter AI Assistant (Port {PORT})"
        icon = pystray.Icon("dexter", _tray_icon_image(), icon_title, menu)
    except Exception as exc:
        print(f"[Desktop] Tray icon failed ({exc}); close will quit.")
        return None
    state["icon"] = icon
    threading.Thread(target=icon.run, name="dexter-tray", daemon=True).start()

    # Inform the user via tray notification where Dexter is located
    def _notify_ready():
        time.sleep(1.0)
        try:
            if getattr(icon, "HAS_NOTIFICATION", False):
                icon.notify(
                    "Dexter is running in the system tray. Click ^ in taskbar if hidden.",
                    "Dexter AI Assistant",
                )
        except Exception:
            pass

    threading.Thread(target=_notify_ready, daemon=True).start()

    try:
        import keyboard

        keyboard.add_hotkey(HOTKEY, ask)
        print(f"[Desktop] Hotkey {HOTKEY} brings up the pop-up.")
    except Exception as exc:
        print(f"[Desktop] Global hotkey unavailable: {exc}")
    return state


# ───────────────────────── backend ─────────────────────────


def start_backend() -> None:
    import uvicorn
    from main import app

    uvicorn.run(app, host=BIND_HOST, port=PORT, log_level="warning")


def _publish_port() -> None:
    """Announce the port, now that the server has actually answered on it."""
    try:
        import main as engine

        engine.publish_when_ready(PORT)
    except Exception as exc:
        print(f"[Ports] Could not publish: {exc}")


def _withdraw_port() -> None:
    try:
        import main as engine

        engine.withdraw_port()
    except Exception as exc:
        print(f"[Ports] Could not withdraw: {exc}")


#: How long the window waits for /health. It was 20 s, which a warm machine
#: meets (~5 s) but a Windows logon does not: with the disk cold and Docker,
#: LM Studio and Chrome starting alongside, importing torch/speechbrain took
#: longer, the "did not start" dialog fired, and OK on it killed a backend that
#: was a few seconds from ready. A backend that really fails is caught sooner
#: by its thread dying (see `alive`), so this can afford to be generous.
STARTUP_TIMEOUT = float(os.getenv("DEXTER_STARTUP_TIMEOUT", "180") or 180)


def wait_for_server(url: str, timeout: float = STARTUP_TIMEOUT,
                    alive: Optional[Callable[[], bool]] = None) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if alive is not None and not alive():
            return False  # import error, port bind failure: no point waiting
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Dexter/desktop"})
            with urllib.request.urlopen(req, timeout=2) as res:
                if res.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


# ───────────────────────── entry ─────────────────────────


def launch_desktop() -> None:
    started = time.perf_counter()
    _preflight_or_exit()

    import webview  # checked by preflight; imported late so the dialog above can explain its absence

    # Let the WebView2 shell grant microphone access (voice input needs it).
    if os.getenv("DEXTER_VOICE", "1").strip().lower() not in ("0", "false", "no", "off"):
        _enable_webview2_mic()

    backend = threading.Thread(target=start_backend, name="dexter-uvicorn", daemon=True)
    backend.start()

    target_url = _resolve_target_url()
    # Wait for the backend even when the window points at the Vite dev server:
    # the API it talks to is this one, and nothing may be published before the
    # socket really answers.
    backend_up = wait_for_server(HEALTH_URL, alive=backend.is_alive)
    if not backend_up and target_url.startswith(f"http://{HOST}:{PORT}"):
        reason = (
            "The backend stopped during startup."
            if not backend.is_alive()
            else f"Nothing answered at {HEALTH_URL} within {STARTUP_TIMEOUT:.0f} seconds."
        )
        _message_box(
            "Dexter backend did not start",
            f"{reason}\n\nSee data\\dexter.log for the error.",
        )
        sys.exit(1)
    if backend_up:
        _publish_port()

    geometry = _load_window()
    shell = DexterShell()
    full_hidden = START_MODE != "full"
    popup_hidden = START_MODE != "popup"
    window = webview.create_window(
        title="Dexter",
        url=target_url,
        width=geometry["width"],
        height=geometry["height"],
        x=geometry.get("x"),
        y=geometry.get("y"),
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        resizable=True,
        frameless=False,
        easy_drag=True,
        on_top=False,
        hidden=full_hidden,
        background_color="#f4f5f7",
        js_api=shell,
    )
    shell._full = window
    tray_state: Dict[str, Any] = {}

    popup = None
    if TRAY_ENABLED:
        # The pop-up: one message, one line, bottom-right, on top. Hidden until
        # Dexter has something to say or the owner asks (tray / hotkey).
        px, py = _popup_position(*POPUP_SIZE)
        sep = "&" if "?" in target_url else "?"
        popup = webview.create_window(
            title="Dexter",
            url=f"{target_url}{sep}mode=popup",
            width=POPUP_SIZE[0],
            height=POPUP_SIZE[1],
            x=px,
            y=py,
            resizable=False,
            frameless=True,
            easy_drag=True,
            on_top=True,
            hidden=popup_hidden,
            background_color="#ffffff",
            js_api=shell,
        )
        shell._popup = popup
        shell._popup_visible = not popup_hidden

        def popup_closing():
            shell._popup_visible = False
            if not getattr(shell, "_quitting", False):
                popup.hide()
                return False
            return True

        popup.events.closing += popup_closing

    def on_closing():
        _save_window(window)
        if not getattr(shell, "_quitting", False):
            # Hide instead of closing: reminders, the scheduler and the Studio
            # event bridge keep running; the tray icon brings the window back.
            window.hide()
            return False
        return True

    window.events.closing += on_closing

    # Connect remote activation from duplicate launches directly to shell
    try:
        import main as engine
        engine.register_show_window_callback(shell.open_full)
    except Exception as exc:
        print(f"[Desktop] Could not register show callback: {exc}")

    def after_start() -> None:
        tray_state["state"] = _install_tray(window, shell if popup is not None else None)
        if popup is not None and tray_state["state"] is None:
            # No tray means no way back to a hidden window: show the full app.
            shell.open_full()

    print(f"[Desktop] Window opening after {time.perf_counter() - started:.1f}s.")
    webview.start(after_start, debug=False)


def main() -> int:
    args = [a.lower() for a in sys.argv[1:]]
    if "--register-autostart" in args or "--install-autostart" in args:
        ok = set_autostart(True)
        print("Dexter registered for autostart." if ok else "Failed to register autostart.")
        return 0 if ok else 1
    if "--unregister-autostart" in args or "--uninstall-autostart" in args:
        ok = set_autostart(False)
        print("Dexter unregistered from autostart." if ok else "Failed to unregister autostart.")
        return 0 if ok else 1
    if "--status-autostart" in args:
        enabled = is_autostart_enabled()
        print(f"Dexter autostart enabled: {enabled}")
        return 0

    if _is_already_running():
        print("[Desktop] Dexter is already running. Bringing existing window to front.")
        _activate_existing_instance()
        return 0

    try:
        launch_desktop()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception:
        text = traceback.format_exc()
        print(text)
        _message_box("Dexter crashed on startup", text[-1500:])
        return 1
    finally:
        # The tray's Quit calls os._exit, which runs no atexit hook; this is the
        # path that actually removes the entry on a normal close.
        _withdraw_port()


if __name__ == "__main__":
    sys.exit(main())
