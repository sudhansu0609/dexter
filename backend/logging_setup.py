"""
Give Dexter a log file.

There was no logging configuration anywhere - no `logging.basicConfig`, no
handler, no file. Roughly ninety `print()` calls went to a stdout that nobody
sees, because Dexter normally runs inside a WebView2 window with no console
attached. When it misbehaved there was nothing to read afterwards.

Two things happen here:

1. `logging` is configured against a rotating file, so new code can just call
   `logging.getLogger(__name__)` and be captured.
2. stdout and stderr are *tee'd* into the same file. The existing `print()`
   diagnostics are well-tagged (`[Router]`, `[Startup]`, `[Scheduler]`) and
   perfectly useful - they were only ever missing a destination. Teeing keeps
   every one of them without a ninety-site rewrite that could only introduce
   bugs. New code should still use `logging`.

Uncaught exceptions in the main thread and in worker threads are logged too;
previously a crashed background thread died in silence.
"""

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional, TextIO

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
_LOG_PATH = os.path.join(_LOG_DIR, "dexter.log")

_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3

_configured = False
_lock = threading.Lock()


class _Tee:
    """Write to the original stream and to the log file.

    Never let a logging failure break the program it is observing: every write
    to the mirror is best-effort, and the real stream is written first.
    """

    def __init__(self, original: Optional[TextIO], mirror: TextIO, tag: str) -> None:
        self._original = original
        self._mirror = mirror
        self._tag = tag

    def write(self, text: str) -> int:
        written = 0
        if self._original is not None:
            try:
                written = self._original.write(text)
            except Exception:
                pass
        try:
            # Mirror verbatim. print() emits the text and its newline as two
            # separate writes, so filtering out whitespace-only writes would
            # run every logged line into the next one.
            if text:
                self._mirror.write(text)
                self._mirror.flush()
        except Exception:
            pass
        return written

    def flush(self) -> None:
        for stream in (self._original, self._mirror):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return bool(self._original and self._original.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        # Some libraries probe for a real fd; give them the console's, never the
        # log file's, so redirection of the mirror can't confuse them.
        if self._original is None:
            raise OSError("no file descriptor")
        return self._original.fileno()


def log_path() -> str:
    return _LOG_PATH


def setup(level: int = logging.INFO) -> str:
    """Configure logging and start mirroring stdout/stderr. Idempotent."""
    global _configured

    with _lock:
        if _configured:
            return _LOG_PATH

        os.makedirs(_LOG_DIR, exist_ok=True)

        handler = RotatingFileHandler(
            _LOG_PATH, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
            encoding="utf-8", errors="replace",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )

        root = logging.getLogger()
        root.setLevel(level)
        root.addHandler(handler)

        # A second, plain handle on the same file for the print mirror - going
        # through `logging` would wrap every print in a second timestamp.
        mirror = open(_LOG_PATH, "a", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = _Tee(sys.stdout, mirror, "out")  # type: ignore[assignment]
        sys.stderr = _Tee(sys.stderr, mirror, "err")  # type: ignore[assignment]

        def _log_uncaught(exc_type, exc_value, exc_tb):
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_tb)
                return
            logging.getLogger("dexter").critical(
                "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
            )

        sys.excepthook = _log_uncaught

        def _log_thread_exception(args) -> None:
            logging.getLogger("dexter.thread").error(
                "Uncaught exception in thread %s",
                getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = _log_thread_exception

        _configured = True
        return _LOG_PATH
