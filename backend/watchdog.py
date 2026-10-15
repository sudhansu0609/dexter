"""
Finding out where Dexter stopped (v13.7).

Dexter froze twice, and both times the log simply **ended** - mid-turn, with no
exception, no traceback, and nothing from any other thread. A log that stops is the
least useful evidence there is: it rules nothing out. Three separate explanations
survived a whole round of investigation (a wedged console window, the foreground
window's title, the text-to-speech), one of them was tested and disproved, and none
of them could be confirmed, because the one question that matters had no answer:

    **which thread was stuck, and in what call?**

So this module exists to answer that the next time, without being asked:

* a **heartbeat** every 10 s into `data/heartbeat.txt`, written with `os.write` to a
  raw file descriptor - deliberately *not* through `logging`, because a deadlocked
  logging handler would silence every other thread and is itself a candidate;
* the **age of the turn in flight**, so a turn that never finished is visible;
* when a turn passes `STUCK_AFTER` seconds, **every thread's stack** into
  `data/stacks.log` via `faulthandler`, which also writes to a raw file and works
  from a thread other than the stuck one;
* the **time of the last HTTP request**, which settles the question the logs could
  not: the microphone lives in the WebView2 page, so a frozen *UI* and a frozen
  *backend* look identical from outside - Dexter stops answering either way. If
  requests stop while heartbeats continue, the front end froze. If heartbeats stop
  too, the process did.

None of it is on a hot path: one dict update per request, one file append per ten
seconds.
"""

from __future__ import annotations

import faulthandler
import os
import threading
import time
from typing import Any, Dict, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat.txt")
STACKS_FILE = os.path.join(DATA_DIR, "stacks.log")

#: How often to write a heartbeat.
INTERVAL_SECONDS = float(os.getenv("DEXTER_WATCHDOG_INTERVAL", "10") or 10)
#: A turn still running after this long is stuck; dump the stacks.
STUCK_AFTER = float(os.getenv("DEXTER_WATCHDOG_STUCK_AFTER", "25") or 25)
#: Keep the heartbeat file small - it is a ring of recent lines, not history.
MAX_HEARTBEAT_BYTES = 256 * 1024
ENABLED = (os.getenv("DEXTER_WATCHDOG", "1") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)

_lock = threading.Lock()
#: Every turn in flight, by token. A dict rather than one slot because turns *do*
#: overlap - the pop-up and the full window both route, and a background task can
#: run its own - and the first version of this kept only the latest, so two
#: overlapping turns shared a token and finishing the older one hid the live one.
_turns: Dict[int, Dict[str, Any]] = {}
_next_token = 0
_turns_done = 0
_last_request: Dict[str, Any] = {"at": 0.0, "path": ""}
_dumped: set = set()
_thread: Optional[threading.Thread] = None
_stacks_handle = None


def note_request(path: str) -> None:
    """Called by the HTTP middleware. Deliberately tiny."""
    _last_request["at"] = time.time()
    _last_request["path"] = path


def turn_started(text: str, source: str = "chat") -> int:
    """A turn is in flight. Returns a token to pass back to `turn_finished`."""
    global _next_token
    with _lock:
        _next_token += 1
        token = _next_token
        _turns[token] = {"token": token, "text": (text or "")[:120], "source": source,
                         "at": time.time(), "thread": threading.current_thread().name}
    return token


def turn_finished(token: int) -> None:
    global _turns_done
    with _lock:
        if _turns.pop(token, None) is not None:
            _turns_done += 1
        _dumped.discard(token)


def _oldest_turn() -> Optional[Dict[str, Any]]:
    """The turn that has been running longest - the one that is stuck, if any is."""
    with _lock:
        if not _turns:
            return None
        return dict(min(_turns.values(), key=lambda turn: turn["at"]))


def dump_stacks(why: str) -> bool:
    """Every thread's stack, appended to data/stacks.log.

    `faulthandler` writes to the file descriptor directly and does not allocate or
    take the logging lock, which is exactly what is needed when the reason for
    calling it might be that something else is holding one.
    """
    global _stacks_handle
    try:
        if _stacks_handle is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            _stacks_handle = open(STACKS_FILE, "a", buffering=1, encoding="utf-8")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        _stacks_handle.write(f"\n{'=' * 72}\n{stamp}  {why}\n{'=' * 72}\n")
        _stacks_handle.flush()
        faulthandler.dump_traceback(file=_stacks_handle, all_threads=True)
        _stacks_handle.flush()
        return True
    except Exception:
        return False


def _heartbeat_line() -> str:
    turn = _oldest_turn()
    with _lock:
        done, running = _turns_done, len(_turns)
    now = time.time()
    parts = [time.strftime("%H:%M:%S"), f"threads={threading.active_count()}",
             f"turns={done}"]
    last = _last_request["at"]
    parts.append(f"last_request={now - last:.0f}s_ago({_last_request['path']})"
                 if last else "last_request=never")
    if turn:
        parts.append(f"IN_TURN={now - turn['at']:.0f}s on {turn['thread']}"
                     + (f" (+{running - 1} more)" if running > 1 else "")
                     + f": {turn['text']!r}")
    else:
        parts.append("idle")
    return "  ".join(parts) + "\n"


def _loop() -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(HEARTBEAT_FILE, flags)
    except Exception:
        return
    while True:
        time.sleep(INTERVAL_SECONDS)
        try:
            # A raw write, so this survives a jammed logging handler.
            os.write(fd, _heartbeat_line().encode("utf-8", "replace"))
            if os.lseek(fd, 0, os.SEEK_END) > MAX_HEARTBEAT_BYTES:
                os.close(fd)
                os.replace(HEARTBEAT_FILE, HEARTBEAT_FILE + ".1")
                fd = os.open(HEARTBEAT_FILE, flags)
        except Exception:
            pass
        try:
            turn = _oldest_turn()
            if turn and time.time() - turn["at"] > STUCK_AFTER:
                # Once per stuck turn, not once per tick: a hang would otherwise
                # fill the disk with the same stacks.
                if turn["token"] not in _dumped:
                    _dumped.add(turn["token"])
                    dump_stacks(f"a turn has been running {time.time() - turn['at']:.0f}s: "
                                f"{turn['text']!r} (started on {turn['thread']})")
                    print(f"[Watchdog] A turn has been running for "
                          f"{time.time() - turn['at']:.0f}s; thread stacks written to "
                          f"data/stacks.log.")
        except Exception:
            pass


def start() -> bool:
    """Begin watching. Safe to call twice; does nothing when switched off."""
    global _thread
    if not ENABLED:
        return False
    with _lock:
        if _thread and _thread.is_alive():
            return True
        # Also catch a hard crash (access violation in a native library), which
        # otherwise leaves no trace at all under pythonw.
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            faulthandler.enable(file=open(STACKS_FILE, "a", buffering=1, encoding="utf-8"))
        except Exception:
            pass
        _thread = threading.Thread(target=_loop, name="dexter-watchdog", daemon=True)
        _thread.start()
    print(f"[Watchdog] Heartbeat every {INTERVAL_SECONDS:.0f}s to data/heartbeat.txt; "
          f"stacks after {STUCK_AFTER:.0f}s in one turn.")
    return True


def status() -> Dict[str, Any]:
    turn = _oldest_turn()
    with _lock:
        done, running = _turns_done, len(_turns)
    return {
        "enabled": ENABLED,
        "running": bool(_thread and _thread.is_alive()),
        "turns": done,
        "inFlight": running,
        "inTurn": turn,
        "stuckAfter": STUCK_AFTER,
        "lastRequest": dict(_last_request),
        "heartbeat": HEARTBEAT_FILE,
        "stacks": STACKS_FILE,
    }
