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
from typing import Any, Dict, Optional

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import env_boot  # noqa: E402,F401  - loads .env for every entry point

try:
    import logging_setup  # noqa: E402,F401  - route prints into data/dexter.log
except Exception:  # pragma: no cover - logging is a convenience, not a gate
    pass

import buzzcaf_ports  # noqa: E402  - the shared port ledger (GUARDIAN_PLAN section 11)

HOST = "127.0.0.1"
#: The port Dexter would *like*. It is a wish: if something already holds it,
#: Dexter steps forward rather than killing whatever is there, and publishes
#: where it landed so the Studio, BuzzBrain and the suite can still find it.
PREFERRED_PORT = int(os.getenv("DEXTER_PORT") or 8098)
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
START_MODE = os.getenv("DEXTER_START_MODE", "popup").strip().lower()

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
    geometry["width"] = max(MIN_WIDTH, geometry["width"])
    geometry["height"] = max(MIN_HEIGHT, geometry["height"])
    return geometry


def _save_window(window: Any) -> None:
    try:
        from jsonstore import save_json

        save_json(WINDOW_FILE, {
            "width": int(window.width), "height": int(window.height),
            "x": int(window.x), "y": int(window.y),
        })
    except Exception as exc:
        print(f"[Desktop] Could not remember window size: {exc}")


# ───────────────────────── tray + hotkey (roadmap v5, 5.3) ─────────────────────────


def _tray_icon_image():
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

    @property
    def _windows(self):
        return self._full, self._popup

    # ── pop-up ──
    def show_popup(self) -> bool:
        if self._popup is None:
            return False
        try:
            self._popup.show()
            try:
                self._popup.restore()
            except Exception:
                pass
            self._popup_visible = True
            return True
        except Exception as exc:
            print(f"[Desktop] Could not show the pop-up: {exc}")
            return False

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
    def open_full(self) -> bool:
        if self._full is None:
            return False
        try:
            self._full.show()
            try:
                self._full.restore()
            except Exception:
                pass
            return True
        except Exception as exc:
            print(f"[Desktop] Could not show the window: {exc}")
            return False

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
            try:
                window.restore()
            except Exception:
                pass
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
        try:
            if state["icon"] is not None:
                state["icon"].stop()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Ask Dexter", ask, default=True),
        pystray.MenuItem("Open Dexter", show),
        pystray.MenuItem("Reindex knowledge", reindex),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Dexter", quit_dexter),
    )
    try:
        icon = pystray.Icon("dexter", _tray_icon_image(), "Dexter", menu)
    except Exception as exc:
        print(f"[Desktop] Tray icon failed ({exc}); close will quit.")
        return None
    state["icon"] = icon
    threading.Thread(target=icon.run, name="dexter-tray", daemon=True).start()

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

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


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


def wait_for_server(url: str, timeout: float = 20.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
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

    threading.Thread(target=start_backend, name="dexter-uvicorn", daemon=True).start()

    target_url = _resolve_target_url()
    # Wait for the backend even when the window points at the Vite dev server:
    # the API it talks to is this one, and nothing may be published before the
    # socket really answers.
    backend_up = wait_for_server(HEALTH_URL)
    if not backend_up and target_url.startswith(f"http://{HOST}:{PORT}"):
        _message_box(
            "Dexter backend did not start",
            f"Nothing answered at {HEALTH_URL} within 20 seconds.\n\n"
            "See data\\dexter.log for the error.",
        )
        sys.exit(1)
    if backend_up:
        _publish_port()

    geometry = _load_window()
    shell = DexterShell()
    popup_first = START_MODE != "full"
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
        hidden=popup_first,
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
            hidden=not popup_first,
            background_color="#ffffff",
            js_api=shell,
        )
        shell._popup = popup
        shell._popup_visible = popup_first

        def popup_closing():
            shell._popup_visible = False
            if tray_state.get("state") and not tray_state["state"]["quit"]:
                popup.hide()
                return False
            return True

        popup.events.closing += popup_closing

    def on_closing():
        _save_window(window)
        if tray_state.get("state") and not tray_state["state"]["quit"]:
            # Hide instead of closing: reminders, the scheduler and the Studio
            # event bridge keep running; the tray icon brings the window back.
            window.hide()
            return False
        return True

    window.events.closing += on_closing

    def after_start() -> None:
        tray_state["state"] = _install_tray(window, shell if popup is not None else None)
        if popup is not None and tray_state["state"] is None:
            # No tray means no way back to a hidden window: show the full app.
            shell.open_full()

    print(f"[Desktop] Window opening after {time.perf_counter() - started:.1f}s.")
    webview.start(after_start, debug=False)


def main() -> int:
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
