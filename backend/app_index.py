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
#: Raised from 45s in v13.7: the scan now reads each candidate's PE header to tell a
#: console tool from an application, which on a cold cache took 34s here - close
#: enough to the old cap that a slower disk would have silently truncated the index.
#: It runs in a background thread, weekly, so the budget is cheap and a partial
#: index is not.
_MAX_SCAN_SECONDS = 120

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

#: Directories full of command-line utilities that are not applications anybody
#: asks Dexter to open. Git for Windows alone contributed `tr`, `rm`, `dd`, `ls`,
#: `sh` and about a hundred more to a list of "installed apps", and one of them was
#: launched when the owner asked for the hand tracking. A bundled JRE's `bin` is the
#: same story (`keytool`).
_SKIP_PATHS = (
    r"\git\usr\bin", r"\git\mingw64\bin", r"\git\mingw32\bin", r"\git\usr\libexec",
    r"\jre\bin", r"\jdk\bin", r"\jre\lib", r"\python3\scripts", r"\nodejs\node_modules",
    r"\cmake\bin", r"\llvm\bin", r"\ffmpeg\bin",
)

def _skip_dir(path: str) -> bool:
    lowered = path.lower()
    return any(part in lowered for part in _SKIP_PATHS)


def is_console_exe(path: str) -> bool:
    """Is this a command-line program rather than a windowed application?

    Read from the PE header (`IMAGE_SUBSYSTEM_WINDOWS_CUI` = 3). This is the honest
    way to tell `tr.exe` from `vlc.exe`: the first attempt at the fix used the
    *length* of the name, which threw out VLC along with the coreutils - a
    three-letter app is still an app. What a person means by "an app" is the thing
    with a window.

    Also what decides how the launcher starts something (see `tools_core`): a
    console tool begun with no arguments tends to sit on stdin forever.
    """
    if not path.lower().endswith(".exe"):
        return False
    try:
        with open(path, "rb") as handle:
            if handle.read(2) != b"MZ":
                return False
            handle.seek(0x3C)
            pe_offset = int.from_bytes(handle.read(4), "little")
            if not 0 < pe_offset < 0x10000000:
                return False
            handle.seek(pe_offset)
            if handle.read(4) != b"PE\0\0":
                return False
            handle.seek(pe_offset + 0x5C)        # OptionalHeader.Subsystem
            return int.from_bytes(handle.read(2), "little") == 3
    except Exception:
        return False

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

                if _skip_dir(root):
                    dirs[:] = []
                    continue

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
                    # An index of *applications*. A console program is a command-line
                    # tool, and having a hundred of them in here is what let "open the
                    # hand tracking" resolve to GNU `tr`. A shortcut is exempt:
                    # somebody made it on purpose.
                    if lowered.endswith(".exe") and is_console_exe(full):
                        continue
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


#: Below this many characters, a name only ever matches **exactly**.
#:
#: The reason is worth keeping: matching was a plain substring test in both
#: directions, preferring the shortest key. `"tr" in "hand tracking"` is true, and
#: `tr` is two characters, so "can you open the hand tracking?" launched GNU `tr`
#: from Git's usr\bin - which sits waiting on stdin forever. The same bug sent
#: "open Person to Profile" to `pr.exe` and "open the key" to `keytool.exe`, three
#: times in one day before anyone worked out what they were looking at.
_MIN_FUZZY = 4


def _has_words(haystack: str, needle: str) -> bool:
    """Does `needle` appear in `haystack` as whole words?

    Both sides are already normalised to lowercase words, so this is the
    difference between "tracking" containing the *app* `tr` and merely containing
    those two letters.
    """
    return re.search(
        r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack
    ) is not None


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
    if len(target) < _MIN_FUZZY:
        # Too short to guess with. "the key" becoming Java's keytool is not a
        # helpful guess, it is a different program.
        return None

    # What was asked for is a whole-word part of a longer app name: "code" ->
    # "code insiders". The *shortest* such key wins, so the plainest name is
    # preferred over a variant.
    in_name = sorted((len(key), key) for key in apps if _has_words(key, target))
    if in_name:
        return apps[in_name[0][1]]

    # Or the app's whole name appears inside what was said. Here the *longest*
    # match wins: the most specific app named, not the shortest one that happens
    # to be mentioned.
    named = sorted((len(key), key) for key in apps
                   if len(key) >= _MIN_FUZZY and _has_words(target, key))
    if named:
        return apps[named[-1][1]]

    close = difflib.get_close_matches(
        target, [key for key in apps if len(key) >= _MIN_FUZZY], n=1, cutoff=0.75
    )
    if close:
        return apps[close[0]]

    return None


def size() -> int:
    return len(_ensure_loaded())
