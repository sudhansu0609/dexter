"""
Start the local model server Dexter is configured for (v11.1).

Dexter's tiers run on Ollama (`DEXTER_LLM_PROVIDER=ollama`, qwen2.5 3B + 7B),
and Ollama is a program that has to be running. Nothing started it: not
Windows (it is not in the Run key), not `start_dexter.bat`, not Dexter. The
morning it was up, everything worked; the afternoon it was not, every sentence
came back "My local reasoning engine is offline. Please make sure LM Studio or
Ollama is running." - a message that asks the owner to do the one thing the
assistant could have done itself.

So: at preflight, at startup, and whenever a request finds the preferred
backend gone, Dexter starts it. `ollama serve` runs detached and windowless
(its output goes to data/ollama.log); LM Studio's server is started with
`lms server start` when that is the preference. Attempts are rate-limited so a
backend that refuses to come up is retried once a minute, not once a token.

What this never does: pull a model, kill anything, or start a backend the
owner did not choose. `DEXTER_AUTOSTART_LLM=0` turns it off.
"""

import os
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import env_boot  # noqa: F401  - loads .env for every entry point
import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT_DIR, "data")

ENABLED = os.getenv("DEXTER_AUTOSTART_LLM", "1").strip().lower() not in ("0", "false", "no", "off")
#: How long to wait for a freshly started server to answer.
START_TIMEOUT = float(os.getenv("DEXTER_AUTOSTART_TIMEOUT", "25") or 25)
#: Do not hammer a backend that will not come up.
RETRY_COOLDOWN = float(os.getenv("DEXTER_AUTOSTART_COOLDOWN", "60") or 60)

_lock = threading.Lock()
_last_attempt: Dict[str, float] = {}

# Test seams: the tests replace these so nothing real is probed or spawned.
_probe: Callable[[str], bool]
_spawn: Callable[[list, Dict[str, str]], Optional[int]]


def _http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        return requests.get(url, timeout=timeout).status_code == 200
    except Exception:
        return False


def _detached(args: list, env: Dict[str, str], log_name: str) -> Optional[int]:
    """Start a server that outlives this process, with no console window."""
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, name, 0)
    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(os.path.join(LOG_DIR, log_name), "ab")
    proc = subprocess.Popen(
        args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        env={**os.environ, **env}, creationflags=flags, close_fds=True,
    )
    return proc.pid


_probe = _http_ok
_spawn = lambda args, env: _detached(args, env, "ollama.log" if "ollama" in str(args[0]).lower() else "lmstudio.log")  # noqa: E731


# ───────────────────────── where the programs are ─────────────────────────


def ollama_exe() -> Optional[str]:
    found = shutil.which("ollama")
    if found:
        return found
    local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    return local if os.path.isfile(local) else None


def lms_exe() -> Optional[str]:
    found = shutil.which("lms")
    if found:
        return found
    home = os.path.join(os.path.expanduser("~"), ".lmstudio", "bin", "lms.exe")
    return home if os.path.isfile(home) else None


def _host_port(url: str, default: str) -> str:
    """'http://127.0.0.1:11434/v1' -> '127.0.0.1:11434' (what OLLAMA_HOST wants)."""
    try:
        parsed = urlparse(url)
        if parsed.hostname:
            return f"{parsed.hostname}:{parsed.port or 80}"
    except Exception:
        pass
    return default


def _ollama_probe_url() -> str:
    base = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/v1").rstrip("/")
    return base[:-3] + "/api/tags" if base.endswith("/v1") else base + "/api/tags"


def _lmstudio_probe_url() -> str:
    return os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234/v1").rstrip("/") + "/models"


# ───────────────────────── starting them ─────────────────────────


def _wait_online(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _probe(url):
            return True
        time.sleep(0.5)
    return False


def _cooling(name: str) -> bool:
    return time.time() - _last_attempt.get(name, 0.0) < RETRY_COOLDOWN


def ensure_ollama(wait: float = START_TIMEOUT) -> Dict[str, Any]:
    """Make sure Ollama's server answers; start it if it does not.

    Returns {"online": bool, "started": bool, "seconds": float, "reason": str}.
    """
    url = _ollama_probe_url()
    if _probe(url):
        return {"online": True, "started": False, "seconds": 0.0, "reason": "already running"}
    if not ENABLED:
        return {"online": False, "started": False, "seconds": 0.0, "reason": "autostart is off (DEXTER_AUTOSTART_LLM=0)"}
    exe = ollama_exe()
    if not exe:
        return {"online": False, "started": False, "seconds": 0.0, "reason": "ollama.exe is not installed"}
    with _lock:
        if _cooling("ollama"):
            return {"online": False, "started": False, "seconds": 0.0,
                    "reason": f"tried less than {int(RETRY_COOLDOWN)} s ago"}
        _last_attempt["ollama"] = time.time()
    t0 = time.time()
    try:
        pid = _spawn([exe, "serve"], {"OLLAMA_HOST": _host_port(os.getenv("OLLAMA_URL", ""), "127.0.0.1:11434")})
    except Exception as exc:
        return {"online": False, "started": False, "seconds": 0.0, "reason": f"could not start ollama serve: {exc}"}
    online = _wait_online(url, wait)
    seconds = round(time.time() - t0, 1)
    if online:
        print(f"[LLM] Ollama was not running; started it (pid {pid}, ready in {seconds}s).")
        return {"online": True, "started": True, "seconds": seconds, "reason": "started"}
    print(f"[LLM] Started ollama serve (pid {pid}) but it did not answer within {int(wait)}s; see data/ollama.log.")
    return {"online": False, "started": True, "seconds": seconds, "reason": f"started but not answering after {int(wait)}s"}


def ensure_lmstudio(wait: float = START_TIMEOUT) -> Dict[str, Any]:
    """Make sure LM Studio's local server answers; `lms server start` if not."""
    url = _lmstudio_probe_url()
    if _probe(url):
        return {"online": True, "started": False, "seconds": 0.0, "reason": "already running"}
    if not ENABLED:
        return {"online": False, "started": False, "seconds": 0.0, "reason": "autostart is off (DEXTER_AUTOSTART_LLM=0)"}
    exe = lms_exe()
    if not exe:
        return {"online": False, "started": False, "seconds": 0.0, "reason": "lms.exe is not installed"}
    with _lock:
        if _cooling("lmstudio"):
            return {"online": False, "started": False, "seconds": 0.0,
                    "reason": f"tried less than {int(RETRY_COOLDOWN)} s ago"}
        _last_attempt["lmstudio"] = time.time()
    t0 = time.time()
    try:
        port = _host_port(os.getenv("LM_STUDIO_URL", ""), "127.0.0.1:1234").rsplit(":", 1)[-1]
        pid = _spawn([exe, "server", "start", "--port", port], {})
    except Exception as exc:
        return {"online": False, "started": False, "seconds": 0.0, "reason": f"could not run lms server start: {exc}"}
    online = _wait_online(url, wait)
    seconds = round(time.time() - t0, 1)
    if online:
        print(f"[LLM] LM Studio's server was not running; started it (ready in {seconds}s).")
        return {"online": True, "started": True, "seconds": seconds, "reason": "started"}
    print(f"[LLM] Ran lms server start (pid {pid}) but it did not answer within {int(wait)}s.")
    return {"online": False, "started": True, "seconds": seconds, "reason": f"started but not answering after {int(wait)}s"}


def ensure_preferred(preferred: Optional[str] = None, wait: float = START_TIMEOUT) -> Dict[str, Any]:
    """Start whichever backend Dexter is configured to prefer.

    'ollama' / 'lmstudio': that one. 'auto': nothing if any local server is
    up; otherwise Ollama first (the tiered models live there), then LM Studio.
    Returns {"provider": name, **ensure_result} for the backend that was acted
    on, or {"provider": None, "online": True} when nothing needed doing.
    """
    pref = (preferred or os.getenv("DEXTER_LLM_PROVIDER", "auto") or "auto").strip().lower()
    if pref == "ollama":
        return {"provider": "ollama", **ensure_ollama(wait)}
    if pref == "lmstudio":
        return {"provider": "lmstudio", **ensure_lmstudio(wait)}
    if _probe(_ollama_probe_url()) or _probe(_lmstudio_probe_url()):
        return {"provider": None, "online": True, "started": False, "seconds": 0.0, "reason": "a local server is up"}
    result = ensure_ollama(wait)
    if result["online"]:
        return {"provider": "ollama", **result}
    return {"provider": "lmstudio", **ensure_lmstudio(wait)}


def ensure_provider(provider: str, wait: float = START_TIMEOUT) -> Dict[str, Any]:
    """`ensure_ollama` / `ensure_lmstudio` by name; anything else is 'not ours to start'."""
    if provider == "ollama":
        return ensure_ollama(wait)
    if provider == "lmstudio":
        return ensure_lmstudio(wait)
    return {"online": False, "started": False, "seconds": 0.0, "reason": f"{provider} is not something Dexter starts"}


def _reset_for_tests() -> None:
    _last_attempt.clear()
