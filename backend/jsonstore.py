"""
Atomic JSON persistence.

The previous pattern - open(path, "w") then json.dump - truncates the file the
instant it opens. A crash or a concurrent read mid-write left an empty file, and
because every loader swallowed the parse error and returned [], the next save
then persisted that empty state permanently. Writing to a temp file and renaming
makes the swap atomic on Windows via os.replace.
"""

import json
import os
import shutil
import tempfile
import threading
import time
from typing import Any

# os.replace is atomic on Windows but not immune to sharing violations: an
# antivirus scanner, the search indexer, or a concurrent reader holding the
# destination open makes it raise PermissionError (WinError 5/32). That surfaced
# as a silently dropped save - the planner, reminders, and task state just did
# not persist. The handles are held for milliseconds, so a short bounded retry
# turns a lost write into a slightly delayed one.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SECONDS = 0.05

_locks: dict = {}
_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    key = os.path.abspath(path)
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def _replace_with_retry(tmp_path: str, path: str) -> None:
    """os.replace, retried through transient Windows sharing violations."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))


def load_json(path: str, default: Any) -> Any:
    """
    Read JSON, falling back to `default`. A corrupt file is preserved as
    `<name>.corrupt` rather than silently overwritten, so data loss is
    recoverable instead of invisible.
    """
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
        if not content:
            return default
        return json.loads(content)
    except json.JSONDecodeError as exc:
        backup = f"{path}.corrupt"
        try:
            shutil.copy2(path, backup)
            print(f"[jsonstore] {os.path.basename(path)} is corrupt ({exc}); saved copy to {backup}")
        except Exception:
            pass
        return default
    except Exception as exc:
        print(f"[jsonstore] Could not read {path}: {exc}")
        return default


def save_json(path: str, data: Any) -> bool:
    """Write JSON atomically. Returns False instead of raising on failure."""
    lock = _lock_for(path)
    with lock:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_with_retry(tmp_path, path)
            return True
        except Exception as exc:
            print(f"[jsonstore] Could not write {path}: {exc}")
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            return False
