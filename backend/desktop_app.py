"""
Dexter desktop shell: FastAPI in-process plus a native WebView2 window.

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

PORT = int(os.getenv("DEXTER_PORT", "8098"))
HOST = "127.0.0.1"
HEALTH_URL = f"http://{HOST}:{PORT}/api/dexter/health"
DEV_URL = os.getenv("DEXTER_DEV_URL", "http://localhost:3006")
DIST_INDEX = os.path.join(ROOT_DIR, "dist", "index.html")
SRC_DIR = os.path.join(ROOT_DIR, "src")
WINDOW_FILE = os.path.join(ROOT_DIR, "data", "window.json")

DEFAULT_WINDOW = {"width": 520, "height": 760}
MIN_WIDTH, MIN_HEIGHT = 380, 520

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

    image = Image.new("RGBA", (64, 64), (9, 13, 22, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, 58, 58), fill=(56, 189, 248, 255))
    draw.ellipse((22, 22, 42, 42), fill=(9, 13, 22, 255))
    return image


def _install_tray(window) -> Optional[Dict[str, Any]]:
    """
    A tray icon with Show / Reindex knowledge / Quit, and a global hotkey that
    brings the window back. Returns the shared state dict, or None when the
    optional packages are missing (the window then closes normally).
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
        try:
            window.show()
            try:
                window.restore()
            except Exception:
                pass
        except Exception as exc:
            print(f"[Desktop] Could not show the window: {exc}")

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
        pystray.MenuItem("Show Dexter", show, default=True),
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

        keyboard.add_hotkey(HOTKEY, show)
        print(f"[Desktop] Hotkey {HOTKEY} shows the window.")
    except Exception as exc:
        print(f"[Desktop] Global hotkey unavailable: {exc}")
    return state


# ───────────────────────── backend ─────────────────────────


def start_backend() -> None:
    import uvicorn
    from main import app

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


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

    threading.Thread(target=start_backend, name="dexter-uvicorn", daemon=True).start()

    target_url = _resolve_target_url()
    if target_url.startswith(f"http://{HOST}:{PORT}") and not wait_for_server(HEALTH_URL):
        _message_box(
            "Dexter backend did not start",
            f"Nothing answered at {HEALTH_URL} within 20 seconds.\n\n"
            "See data\\dexter.log for the error.",
        )
        sys.exit(1)

    geometry = _load_window()
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
        background_color="#090d16",
    )
    tray_state: Dict[str, Any] = {}

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
        tray_state["state"] = _install_tray(window)

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


if __name__ == "__main__":
    sys.exit(main())
