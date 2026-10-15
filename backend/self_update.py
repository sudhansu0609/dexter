"""
Rebuilding and relaunching Dexter itself (self-reinstall, v13.11).

The owner asked for the whole loop: have buzzcode edit Dexter's *own* code
(that is `code_task(project="dexter")`, which opens buzzcode in its own terminal
against this repo) and then **reinstall Dexter** so those edits take.

A "reinstall" here is exactly the README's install steps, no more: reinstall the
Python dependencies and rebuild the front end. It runs in its own visible console
on purpose -

* pip and npm are slow and chatty, and a terminal is where that belongs;
* the build must not fight the *running* Dexter for the same files mid-turn, so it
  is a separate process the owner can watch;
* a window that shows every command is its own confirmation - nothing about the
  app the owner is running gets rebuilt behind their back.

Restarting is opt-in (`restart=True`): the freshly built Dexter is launched only
when asked, and even then the old instance is left for the owner to close rather
than killed out from under them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, Optional

#: dexter/ - this file lives in dexter/backend/.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dexter_root() -> str:
    return os.environ.get("DEXTER_HOME") or ROOT


def _python_exe() -> str:
    """A real python.exe (not pythonw) so pip's output shows in the console."""
    exe = sys.executable or ""
    base = os.path.basename(exe).lower()
    if base.startswith("pythonw"):
        candidate = os.path.join(os.path.dirname(exe), base.replace("pythonw", "python", 1))
        if os.path.exists(candidate):
            return candidate
    if exe and os.path.exists(exe) and not base.startswith("pythonw"):
        return exe
    return shutil.which("python") or shutil.which("python3") or "python"


def _has_npm() -> bool:
    return bool(shutil.which("npm") or shutil.which("npm.cmd"))


def preflight() -> Dict[str, Any]:
    """What a reinstall would use, and whether it is all here."""
    root = dexter_root()
    return {
        "root": root,
        "requirements": os.path.exists(os.path.join(root, "requirements.txt")),
        "packageJson": os.path.exists(os.path.join(root, "package.json")),
        "launcher": os.path.exists(os.path.join(root, "start_dexter.bat")),
        "python": _python_exe(),
        "npm": _has_npm(),
    }


def _script(root: str, py: str, deps: bool, frontend: bool, restart: bool) -> str:
    """The batch a reinstall runs. Each step stops the run on failure and the
    window stays open so the owner can read why."""
    lines = [
        "@echo off",
        "setlocal",
        "title Dexter - reinstall",
        f'cd /d "{root}"',
        "echo ===================================================",
        "echo   Reinstalling Dexter",
        "echo ===================================================",
        "echo.",
    ]
    if deps:
        lines += [
            "echo [deps] Python dependencies (pip install -r requirements.txt)...",
            f'"{py}" -m pip install -r requirements.txt',
            "if errorlevel 1 goto :fail",
            "echo.",
        ]
    if frontend:
        lines += [
            "echo [frontend] npm install...",
            "call npm install",
            "if errorlevel 1 goto :fail",
            "echo [frontend] npm run build...",
            "call npm run build",
            "if errorlevel 1 goto :fail",
            "echo.",
        ]
    lines += [
        "echo ===================================================",
        "echo   Reinstall complete.",
        "echo ===================================================",
    ]
    if restart:
        lines += [
            "echo Restarting Dexter...",
            'start "" "%CD%\\start_dexter.bat"',
        ]
    lines += [
        "echo.",
        "echo You can close this window.",
        "timeout /t 8 >nul",
        "exit /b 0",
        ":fail",
        "echo.",
        "echo [ERROR] Reinstall failed - see the messages above. Nothing was restarted.",
        "pause",
        "exit /b 1",
    ]
    return "\r\n".join(lines) + "\r\n"


def reinstall_dexter(restart: bool = False, deps: bool = True,
                     frontend: bool = True) -> Dict[str, Any]:
    """Rebuild Dexter in a visible console; optionally relaunch it when done.

    Returns at once - the build runs in its own window. `restart=True` launches a
    fresh Dexter after a clean build; the running instance is left for the owner
    to close.
    """
    info = preflight()
    root = info["root"]
    if frontend and not info["packageJson"]:
        return {"ok": False, "error": f"No package.json in {root}; is this the Dexter repo?"}
    if frontend and not info["npm"]:
        return {"ok": False, "error": "npm isn't on PATH, so the front end can't be rebuilt. "
                                      "Install Node.js, or say 'reinstall dexter dependencies only'."}
    if deps and not info["requirements"]:
        deps = False  # nothing to install; skip rather than fail
    if restart and not info["launcher"]:
        restart = False  # build anyway, just don't try to relaunch

    script_dir = os.path.join(root, "data")
    try:
        os.makedirs(script_dir, exist_ok=True)
    except Exception:
        script_dir = root
    script_path = os.path.join(script_dir, "_reinstall_dexter.bat")
    try:
        with open(script_path, "w", encoding="ascii", errors="replace") as fh:
            fh.write(_script(root, info["python"], deps, frontend, restart))
    except Exception as exc:
        return {"ok": False, "error": f"Couldn't write the reinstall script: {exc}"}

    try:
        # `cmd /c start` gives the build its own console and detaches it from
        # Dexter, so a Dexter restart does not take the build window with it.
        subprocess.Popen(
            ["cmd", "/c", "start", "Dexter reinstall", "/D", root, "cmd", "/k",
             script_path],
            cwd=root, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as exc:
        return {"ok": False, "error": f"The reinstall console would not start: {exc}"}

    steps = []
    if deps:
        steps.append("Python deps")
    if frontend:
        steps.append("front-end build")
    return {
        "ok": True,
        "root": root,
        "steps": steps,
        "restart": restart,
        "script": script_path,
        "startedAt": time.time(),
    }
