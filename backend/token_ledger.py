"""
Token ledger: how many tokens every model call has used, all-time and since
this process started, per provider and per model.

Every LLM reply that reports usage lands here from `HybridLLMRouter.chat`.
OpenAI-compatible servers report `prompt_tokens` / `completion_tokens`
(streams only when asked with `stream_options.include_usage`); Ollama's native
path reports `prompt_eval_count` / `eval_count`. Both are counted the same way.

Persisted to data/token_usage.json through jsonstore so a restart keeps the
all-time numbers; the session bucket is memory only.
"""

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from jsonstore import load_json, save_json

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PATH = os.path.join(DATA_DIR, "token_usage.json")

_lock = threading.Lock()
_state: Optional[Dict[str, Any]] = None
_session: Dict[str, Any] = {}
_session_since = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bucket() -> Dict[str, int]:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _empty() -> Dict[str, Any]:
    return {
        "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": _bucket(),
        "providers": {},
        "models": {},
    }


def _load() -> Dict[str, Any]:
    global _state, _session
    if _state is None:
        data = load_json(PATH, None)
        _state = data if isinstance(data, dict) and "totals" in data else _empty()
        if not _session:
            _session = _empty()
            _session["since"] = _session_since
    return _state


def tokens_of(usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Prompt / completion counts from whatever shape the backend reported."""
    usage = usage or {}

    def num(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        return 0

    prompt = num("prompt_tokens", "prompt_eval_count", "input_tokens")
    completion = num("completion_tokens", "eval_count", "output_tokens")
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def _add(target: Dict[str, Any], provider: str, model: str, counts: Dict[str, int]) -> None:
    for bucket in (
        target["totals"],
        target["providers"].setdefault(provider, _bucket()),
        target["models"].setdefault(f"{provider}/{model}", _bucket()),
    ):
        bucket["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            bucket[key] += counts[key]


def record(provider: str, model: str, usage: Optional[Dict[str, Any]]) -> None:
    """Count one completed model call. Never raises: accounting must not break a reply."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return  # fake sessions in the test-suite must not land in the owner's ledger
    try:
        counts = tokens_of(usage)
        provider = (provider or "unknown").strip() or "unknown"
        model = (model or "default").strip() or "default"
        with _lock:
            state = _load()
            _add(state, provider, model, counts)
            _add(_session, provider, model, counts)
            state["updated"] = time.time()
            save_json(PATH, state)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[TokenLedger] could not record usage: {exc}")


def _rows(table: Dict[str, Dict[str, int]]) -> list:
    rows = []
    for name, bucket in table.items():
        row = {"name": name, **bucket}
        if "/" in name:
            row["provider"], row["model"] = name.split("/", 1)
        rows.append(row)
    rows.sort(key=lambda r: (-r["total_tokens"], -r["calls"], r["name"]))
    return rows


def summary() -> Dict[str, Any]:
    with _lock:
        state = _load()
        return {
            "since": state["since"],
            "sessionSince": _session["since"],
            "allTime": dict(state["totals"]),
            "session": dict(_session["totals"]),
            "providers": _rows(state["providers"]),
            "models": _rows(state["models"]),
            "sessionProviders": _rows(_session["providers"]),
            "sessionModels": _rows(_session["models"]),
        }


def reset() -> Dict[str, Any]:
    """Zero the all-time counters (the session bucket keeps counting)."""
    global _state
    with _lock:
        _state = _empty()
        save_json(PATH, _state)
    return summary()
