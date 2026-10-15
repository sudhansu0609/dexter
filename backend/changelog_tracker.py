"""
Dexter Changelog & Self-Awareness Tracker
========================================

Tracks what changes, updates, and feature additions were made to Dexter
since the last time it ran.

Provides:
- `get_changes_since_last_run()`: Detailed dictionary of recent changes, commits, and summary.
- `context_block()`: Formatted bullet-point block injected into companion/system prompt.
- `record_session()`: Persists the current commit and run timestamp on startup.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "data")
STATE_FILE = os.path.join(DATA_DIR, "changelog_state.json")
PROGRESS_FILE = os.path.join(ROOT_DIR, "PROGRESS.md")

# Notable recent features to always include if no new git commit diff is available
LANDMARK_UPDATES = [
    "Mid-Way Interruption: You can interrupt Dexter anytime mid-speech or mid-thought by saying 'stop', 'wait', 'hold on', pressing the red UI Stop button, or hitting the Esc key.",
    "Logitech Brio Default Microphone: Dexter now automatically detects and selects the Logitech Brio microphone by default whenever it is plugged in.",
    "App & Tab Context Routing: Dexter understands context when switching between desktop apps and their specific tabs (e.g. browser vs native app window controls).",
]

_cached_changes: Optional[Dict[str, Any]] = None
_cached_at: float = 0.0
_CACHE_TTL = 30.0  # seconds


def _run_git(args: List[str]) -> Optional[str]:
    """Run a git command safely without flashing windows on Windows."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=flags,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def get_current_commit() -> Dict[str, str]:
    """Get current HEAD commit hash, date, and subject message."""
    out = _run_git(["log", "-1", "--format=%H|%ad|%s", "--date=iso"])
    if out and "|" in out:
        parts = out.split("|", 2)
        return {
            "hash": parts[0],
            "short_hash": parts[0][:7],
            "date": parts[1] if len(parts) > 1 else "",
            "message": parts[2] if len(parts) > 2 else "",
        }
    return {"hash": "", "short_hash": "", "date": "", "message": ""}


def _read_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(data: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    temp = STATE_FILE + f".tmp.{os.getpid()}"
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp, STATE_FILE)
    except Exception:
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except Exception:
            pass


def _extract_recent_progress_entries(max_entries: int = 3) -> List[str]:
    """Extract recent version headings and highlights from PROGRESS.md."""
    if not os.path.exists(PROGRESS_FILE):
        return []
    entries = []
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        current_header = ""
        current_blurb = ""
        for line in lines:
            line_str = line.strip()
            if line_str.startswith("## v"):
                if current_header:
                    desc = f"{current_header}: {current_blurb}" if current_blurb else current_header
                    desc = desc.replace("—", "-").replace("–", "-").replace('*"', '"').replace('"*', '"')
                    entries.append(desc)
                    if len(entries) >= max_entries:
                        break
                current_header = line_str.lstrip("#").strip()
                current_blurb = ""
            elif current_header and not current_blurb and line_str and not line_str.startswith("#"):
                current_blurb = line_str
        if current_header and len(entries) < max_entries:
            desc = f"{current_header}: {current_blurb}" if current_blurb else current_header
            desc = desc.replace("—", "-").replace("–", "-").replace('*"', '"').replace('"*', '"')
            entries.append(desc)
    except Exception:
        pass
    return entries


def get_changes_since_last_run(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Calculate and return what changed in Dexter since the last run.

    Returns:
        {
            "has_changes": bool,
            "summary": str,
            "items": [str, ...],
            "commits": [{"hash": ..., "message": ..., "date": ...}],
            "last_run": str,
            "current_run": str,
            "last_commit": str,
            "current_commit": str
        }
    """
    global _cached_changes, _cached_at
    now = time.time()
    if not force_refresh and _cached_changes is not None and (now - _cached_at < _CACHE_TTL):
        return _cached_changes

    state = _read_state()
    last_commit = state.get("last_commit_hash", "")
    last_run_time = state.get("last_run_timestamp", "")

    current = get_current_commit()
    curr_commit = current.get("hash", "")

    commits: List[Dict[str, str]] = []
    items: List[str] = []

    # Check git commits between last_commit and HEAD
    if curr_commit:
        if last_commit and last_commit != curr_commit:
            git_range = f"{last_commit}..HEAD"
            out = _run_git(["log", git_range, "--format=%h|%ad|%s", "--date=short"])
            if out:
                for line in out.splitlines():
                    if "|" in line:
                        h, d, m = line.split("|", 2)
                        commits.append({"hash": h, "date": d, "message": m})
                        items.append(f"{m} ({h})")

        if not commits:
            # If no range diff, grab the latest 3 git commits for general visibility
            out = _run_git(["log", "-3", "--format=%h|%ad|%s", "--date=short"])
            if out:
                for line in out.splitlines():
                    if "|" in line:
                        h, d, m = line.split("|", 2)
                        commits.append({"hash": h, "date": d, "message": m})

    # Add landmark feature updates so Dexter always explains concrete user-facing capabilities
    for update in LANDMARK_UPDATES:
        if not any(update[:25].lower() in item.lower() for item in items):
            items.append(update)

    # Supplement with PROGRESS.md recent highlights
    progress_highlights = _extract_recent_progress_entries(2)
    for p in progress_highlights:
        if not any(p[:20].lower() in item.lower() for item in items):
            items.append(p)

    has_changes = bool(last_commit != curr_commit or commits or items)

    # Build human-friendly prose summary
    if items:
        clean_items = [re.sub(r"^[*\-•]\s*", "", i).strip() for i in items[:4]]
        summary = "Since last time, here are the key updates to Dexter:\n" + "\n".join(f"- {it}" for it in clean_items)
    else:
        summary = "Dexter is up to date with the latest features and configuration."

    result = {
        "has_changes": has_changes,
        "summary": summary,
        "items": items,
        "commits": commits,
        "last_run": last_run_time,
        "current_run": datetime.now().isoformat(),
        "last_commit": last_commit,
        "current_commit": curr_commit,
    }

    _cached_changes = result
    _cached_at = now
    return result


def record_session() -> Dict[str, Any]:
    """
    Record current startup session into changelog_state.json.
    Should be called during application startup (FastAPI lifespan).
    """
    changes = get_changes_since_last_run(force_refresh=True)
    current = get_current_commit()

    state = _read_state()
    # Save previous run as history before overwriting
    prev_runs = state.get("history", [])
    if state.get("last_run_timestamp"):
        prev_runs.append({
            "timestamp": state["last_run_timestamp"],
            "commit": state.get("last_commit_hash", ""),
        })
        # Keep last 10 historical runs
        prev_runs = prev_runs[-10:]

    new_state = {
        "last_run_timestamp": datetime.now().isoformat(),
        "last_commit_hash": current.get("hash", state.get("last_commit_hash", "")),
        "last_commit_short": current.get("short_hash", state.get("last_commit_short", "")),
        "history": prev_runs,
        "landmark_updates": LANDMARK_UPDATES,
    }
    _write_state(new_state)
    return changes


def context_block() -> str:
    """
    Produce a concise section for companion.context_block() so Dexter's LLM
    natively knows what changed in its codebase and capabilities since last time.
    """
    changes = get_changes_since_last_run()
    items = changes.get("items", [])[:4]
    if not items:
        return ""

    lines = ["## Recent updates to Dexter (what changed since last time):"]
    for it in items:
        # Keep each bullet point brief and readable
        clean = re.sub(r"^[*\-•]\s*", "", it).strip()
        lines.append(f"- {clean}")
    lines.append("If the user asks what changed in you or what's new, answer directly with these facts.")
    return "\n".join(lines)
