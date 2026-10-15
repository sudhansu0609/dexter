"""
One espeak library per process, instead of two copies per spoken sentence (v13.7d).

`phonemizer` copies the **entire espeak-ng DLL** into a fresh temporary directory
every time a backend is constructed, and loads the copy:

    # phonemizer/backend/espeak/api.py
    self._tempdir = tempfile.mkdtemp()
    library_copy = self._tempdir / f'{espeak.stem}_{uuid4()}{espeak.suffix}'
    shutil.copy(espeak, library_copy)

It does that for a real reason - espeak-ng keeps its state in globals, so two live
wrappers would tread on each other, and `dlopen` hands back the same handle for the
same path. But `EspeakBackend.__init__` builds **two** wrappers (one inside
`is_available()`, one for itself), `phonemizer.phonemize()` builds a backend per
call, and Kokoro phonemizes every chunk. That is two DLL copies per sentence Dexter
speaks. This machine had **1,172 of them** sitting in `%TEMP%`, and the copy is where
the synthesis thread happened to be parked in the py-spy dump of a frozen Dexter.

So: one shared wrapper, created once, for the life of the process. The reason
phonemizer avoids sharing is concurrency, and the answer to that is a lock rather
than a thousand file copies - `synthesis_lock` below, which `voice_engine` holds
around its Kokoro calls.

Nothing here is required: if the patch does not apply (a new phonemizer layout, say),
everything keeps working exactly as before, one copy per sentence.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

#: Held around anything that phonemizes. espeak-ng is not thread-safe, and sharing
#: one wrapper is only sound if calls do not overlap.
synthesis_lock = threading.RLock()

_installed = False
_shared: Any = None
_created = 0
#: The modules whose `EspeakWrapper` name now points at the shared factory. Kept so
#: `warm()` need not re-import phonemizer, which pulls in its whole package.
_patched: List[Any] = []
_lock = threading.Lock()

#: A temp directory left behind by phonemizer is a `tempfile.mkdtemp()` name - `tmp`
#: plus eight random characters - holding **exactly one** file, which is the copied
#: library. Depending on the phonemizer version that is `espeak-ng.dll` or the older
#: `libespeak-ng_<uuid>.dll`. All three conditions together are what makes deleting
#: them safe: on this machine 1,172 matched and 211 similar-looking `ports.json`
#: directories did not.
_COPY_NAME = re.compile(
    r"^(?:lib)?espeak[-_]?ng?(?:_[0-9a-f-]{8,})?\.(?:dll|so[.0-9]*|dylib)$",
    re.IGNORECASE,
)
_TEMP_DIR_NAME = re.compile(r"^tmp[a-z0-9_]{6,12}$", re.IGNORECASE)

#: Every phonemizer module that constructs a wrapper of its own. Missing one costs a
#: DLL copy per sentence: patching only `base` took it from four copies per chunk to
#: one, and `espeak.available_voices` was the fourth.
_PATCH_MODULES = (
    "phonemizer.backend.espeak.base",
    "phonemizer.backend.espeak.espeak",
    "phonemizer.backend.espeak.mbrola",
)


def install() -> bool:
    """Make every `EspeakWrapper()` return one shared instance. Idempotent."""
    global _installed
    with _lock:
        if _installed:
            return True
        try:
            import importlib

            from phonemizer.backend.espeak.wrapper import EspeakWrapper
        except Exception as exc:
            print(f"[Voice] espeak caching unavailable ({exc}); "
                  "phonemizer will copy its library per sentence.")
            return False

        def shared_wrapper(*args: Any, **kwargs: Any) -> Any:
            global _shared, _created
            # Never under `_lock`: constructing the wrapper copies a file, and the
            # callers of this are already serialised by `synthesis_lock`.
            if _shared is None:
                _shared = EspeakWrapper(*args, **kwargs)
                _created += 1
            return _shared

        patched = []
        for name in _PATCH_MODULES:
            try:
                module = importlib.import_module(name)
            except Exception:
                continue   # mbrola is optional
            if getattr(module, "EspeakWrapper", None) is not None:
                module.EspeakWrapper = shared_wrapper
                patched.append(name.rsplit(".", 1)[-1])
                _patched.append(module)
        if not patched:
            print("[Voice] espeak caching found nothing to patch; leaving it alone.")
            return False
        _installed = True
        print(f"[Voice] espeak library will be loaded once, not copied per sentence "
              f"({', '.join(patched)}).")
        return True


def warm() -> bool:
    """Create the shared wrapper now, so the first reply does not pay for it."""
    if not install() or not _patched:
        return False
    try:
        _patched[0].EspeakWrapper()
        return _shared is not None
    except Exception as exc:
        print(f"[Voice] Could not warm the espeak library: {exc}")
        return False


def stats() -> Dict[str, Any]:
    return {"installed": _installed, "wrappersCreated": _created,
            "shared": _shared is not None, "leftoverCopies": len(find_leftovers())}


# ───────────────────── clearing up what has already leaked ─────────────────────


def find_leftovers(temp_dir: Optional[str] = None,
                   older_than_seconds: float = 600.0) -> List[str]:
    """Temp directories that contain nothing but one copied espeak library.

    Deliberately strict: one entry, and its name has to match the copy phonemizer
    makes. Anything else in the directory and it is somebody else's, left alone.
    """
    root = temp_dir or tempfile.gettempdir()
    found: List[str] = []
    cutoff = time.time() - max(0.0, older_than_seconds)
    try:
        entries = os.scandir(root)
    except OSError:
        return found
    with entries:
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if not _TEMP_DIR_NAME.match(entry.name):
                    continue
                if entry.stat().st_mtime > cutoff:
                    continue  # might belong to a synthesis happening right now
                names = os.listdir(entry.path)
                if len(names) == 1 and _COPY_NAME.match(names[0]):
                    found.append(entry.path)
            except OSError:
                continue
    return found


def sweep(temp_dir: Optional[str] = None, older_than_seconds: float = 600.0,
          limit: int = 5000) -> Dict[str, Any]:
    """Delete those directories. Returns what went and what was freed."""
    removed, failed, freed = [], [], 0
    for path in find_leftovers(temp_dir, older_than_seconds)[:limit]:
        try:
            for name in os.listdir(path):
                target = os.path.join(path, name)
                freed += os.path.getsize(target)
            shutil.rmtree(path)
            removed.append(path)
        except OSError:
            failed.append(path)
    return {"removed": len(removed), "failed": len(failed),
            "freedMb": round(freed / (1024 * 1024), 1)}
