"""
Installed-application index.

Replaces the unbounded os.walk of C:\\Program Files that previously ran on every
unmapped app launch - a scan that could take seconds to minutes while the user
waited. The scan now happens once in a background thread, is capped in depth and
time, and is cached to data/app_index.json. Lookups become a dict hit plus a
fuzzy fallback, so launching an app is effectively instant.
"""

import difflib
import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

from jsonstore import load_json, save_json

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
INDEX_FILE = os.path.join(DATA_DIR, "app_index.json")

INDEX_TTL_SECONDS = 7 * 24 * 3600
_MAX_DEPTH = 4
_MAX_SCAN_SECONDS = 45

_SCAN_DIRS = [
    os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
    os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
]

# Background helpers, updaters and uninstallers pollute results badly.
_NOISE = (
    "helper", "service", "prefs", "dlms", "unins", "setup", "crash", "update",
    "installer", "repair", "report", "diagnos", "watchdog", "launcher_", "redist",
    "vcredist", "dotnet", "readme", "license", "eula", "manual", "documentation",
)

_index: Dict[str, str] = {}
_index_lock = threading.Lock()
_building = False


def _is_noise(filename: str) -> bool:
    lowered = filename.lower()
    return any(token in lowered for token in _NOISE)


def _normalize(name: str) -> str:
    """'Adobe Photoshop 2024.lnk' -> 'photoshop'."""
    stem = os.path.splitext(name)[0].lower()
    stem = re.sub(r"\b(19|20)\d{2}\b", " ", stem)          # version years
    stem = re.sub(r"\bv?\d+(\.\d+)*\b", " ", stem)          # version numbers
    stem = re.sub(r"\b(adobe|microsoft|windows|apache|google|the)\b", " ", stem)
    stem = re.sub(r"\b(x64|x86|32.?bit|64.?bit|cc|beta|preview)\b", " ", stem)
    stem = re.sub(r"[^a-z0-9 ]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def _scan() -> Dict[str, str]:
    """Walk the known install locations once, depth- and time-capped."""
    found: Dict[str, str] = {}
    started = time.time()

    for base in _SCAN_DIRS:
        if not base or not os.path.isdir(base):
            continue
        base_depth = base.rstrip(os.sep).count(os.sep)

        try:
            for root, dirs, files in os.walk(base):
                if time.time() - started > _MAX_SCAN_SECONDS:
                    print("[AppIndex] Scan time limit reached; using partial index.")
                    return found

                if root.rstrip(os.sep).count(os.sep) - base_depth >= _MAX_DEPTH:
                    dirs[:] = []

                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".")
                    and d.lower() not in ("node_modules", "cache", "temp", "logs", "locales")
                ]

                for filename in files:
                    lowered = filename.lower()
                    if not (lowered.endswith(".exe") or lowered.endswith(".lnk")):
                        continue
                    if _is_noise(lowered):
                        continue

                    key = _normalize(filename)
                    if not key or len(key) < 2:
                        continue

                    full = os.path.join(root, filename)
                    # Prefer .lnk from the Start Menu: they carry working dirs
                    # and launch args that a bare .exe path loses.
                    if key in found:
                        existing_is_lnk = found[key].lower().endswith(".lnk")
                        new_is_lnk = lowered.endswith(".lnk")
                        if existing_is_lnk or not new_is_lnk:
                            continue
                    found[key] = full
        except Exception as exc:
            print(f"[AppIndex] Skipping {base}: {exc}")

    return found


def build(force: bool = False) -> Dict[str, str]:
    """Build (or reuse) the index. Safe to call repeatedly."""
    global _index

    if not force:
        cached = load_json(INDEX_FILE, None)
        if isinstance(cached, dict) and cached.get("apps"):
            age = time.time() - cached.get("builtAt", 0)
            if age < INDEX_TTL_SECONDS:
                with _index_lock:
                    _index = cached["apps"]
                print(f"[AppIndex] Loaded {len(_index)} apps from cache.")
                return _index

    print("[AppIndex] Scanning for installed applications...")
    started = time.time()
    apps = _scan()
    elapsed = time.time() - started

    with _index_lock:
        _index = apps
    save_json(INDEX_FILE, {"builtAt": time.time(), "apps": apps})
    print(f"[AppIndex] Indexed {len(apps)} applications in {elapsed:.1f}s.")
    return apps


def build_async(force: bool = False) -> None:
    """Kick off a build without blocking startup or a request."""
    global _building
    with _index_lock:
        if _building:
            return
        _building = True

    def _run() -> None:
        global _building
        try:
            build(force=force)
        finally:
            with _index_lock:
                _building = False

    threading.Thread(target=_run, name="dexter-app-index", daemon=True).start()


def _ensure_loaded() -> Dict[str, str]:
    with _index_lock:
        if _index:
            return _index
    cached = load_json(INDEX_FILE, None)
    if isinstance(cached, dict) and cached.get("apps"):
        with _index_lock:
            globals()["_index"] = cached["apps"]
        return cached["apps"]
    return {}


def lookup(app_name: str) -> Optional[str]:
    """Resolve a spoken app name to an executable or shortcut path."""
    apps = _ensure_loaded()
    if not apps:
        return None

    target = _normalize(app_name)
    if not target:
        return None

    if target in apps:
        return apps[target]

    # Substring: prefer the shortest key so "code" beats "code insiders".
    candidates: List[Tuple[int, str]] = [
        (len(key), key) for key in apps if target in key or key in target
    ]
    if candidates:
        return apps[min(candidates)[1]]

    close = difflib.get_close_matches(target, list(apps.keys()), n=1, cutoff=0.75)
    if close:
        return apps[close[0]]

    return None


def size() -> int:
    return len(_ensure_loaded())
