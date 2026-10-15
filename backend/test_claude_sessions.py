"""
Reading Claude Code's logs (v7, K8): transcripts fold into sessions, noise is
ignored, the day narrative and session story read right, the live session is
detected, and the phrasings route without a model.
"""

import json
import datetime
import os
import time

import pytest

import claude_sessions as cs
import tools_claude  # noqa: F401
from router import fast_path


def _write_session(folder, session_id, cwd, prompts, files, commands, title=None, age_seconds=60, branch="main"):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{session_id}.jsonl")
    records = []
    if title:
        records.append({"type": "ai-title", "aiTitle": title, "sessionId": session_id})
    records.append({"type": "user", "cwd": cwd, "gitBranch": branch, "sessionId": session_id, "timestamp": "2026-09-06T08:05:23.863Z",
                    "message": {"role": "user", "content": "<local-command-caveat>Caveat: ignore</local-command-caveat>"}})
    for p in prompts:
        records.append({"type": "user", "cwd": cwd, "sessionId": session_id, "message": {"role": "user", "content": p}})
        content = [{"type": "tool_use", "name": "Write", "input": {"file_path": f}} for f in files] + \
                  [{"type": "tool_use", "name": "Bash", "input": {"command": c}} for c in commands] + \
                  [{"type": "text", "text": f"Done with: {p}"}]
        records.append({"type": "assistant", "cwd": cwd, "sessionId": session_id, "message": {"role": "assistant", "content": content}})
        records.append({"type": "user", "cwd": cwd, "sessionId": session_id, "isSidechain": True,
                        "message": {"role": "user", "content": "subagent chatter"}})
        records.append({"type": "user", "cwd": cwd, "sessionId": session_id,
                        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}})
    with open(path, "w", encoding="utf-8") as handle:
        for r in records:
            handle.write(json.dumps(r) + "\n")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def _seconds_since_midnight() -> int:
    """How far into the day we are - the budget for "earlier today"."""
    now = datetime.datetime.now()
    return max(120, now.hour * 3600 + now.minute * 60 + now.second)


@pytest.fixture
def logs(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    monkeypatch.setattr(cs, "CLAUDE_PROJECTS", str(root))
    monkeypatch.setattr(cs, "ENABLED", True)
    cs._cache.clear()
    _write_session(root / "B--p-BuzzAnimate", "aaa", r"B:\p\BuzzAnimate", ["make the director move", "now add sound"],
                   [r"B:\p\BuzzAnimate\rig.py", r"B:\p\BuzzAnimate\sound.py"], ["python -m pytest -q"],
                   title="Director or scenery not responding", age_seconds=60, branch="rigging")
    _write_session(root / "B--p-dexter", "bbb", r"B:\p\dexter", ["fix the tray"], [r"B:\p\dexter\backend\desktop_app.py"], [],
                   # Old enough to sort second, new enough to still count as "today"
                   # whatever time the suite runs: three hours before midnight is
                   # yesterday, and this is the kind of test that fails once at 00:24
                   # and gets blamed on whatever was last changed.
                   age_seconds=min(3 * 3600, _seconds_since_midnight() - 60))
    _write_session(root / "B--p-old", "ccc", r"B:\p\old", ["ancient"], [], [], age_seconds=40 * 86400)
    return root


def test_transcripts_fold_into_sessions_and_skip_noise(logs):
    sessions = cs.list_sessions(days=7)
    assert [s["id"] for s in sessions] == ["aaa", "bbb"]            # newest first, the old one out of the window
    a = sessions[0]
    assert a["project"] == "BuzzAnimate" and a["branch"] == "rigging" and a["title"] == "Director or scenery not responding"
    assert a["prompts"] == ["make the director move", "now add sound"] and a["turns"] == 2   # caveat, sidechain, tool_result skipped
    assert [os.path.basename(f) for f in a["files"]] == ["rig.py", "sound.py"]
    assert a["commands"] == ["python -m pytest -q", "python -m pytest -q"] and a["tools"] == {"Write": 4, "Bash": 2}
    assert a["last_reply"] == "Done with: now add sound"
    b = sessions[1]
    assert b["title"] == "fix the tray"                              # no AI title: the first prompt stands in
    assert cs.list_sessions(days=7, project="dexter")[0]["id"] == "bbb"


def test_live_session_and_context(logs):
    live = cs.active_session()
    assert live and live["id"] == "aaa"
    lines = cs.context_lines()
    assert lines[0].startswith("Claude Code is active on BuzzAnimate: 'Director or scenery not responding'; last prompt: now add sound")
    assert lines[1].startswith("Claude Code today: dexter (main): 'fix the tray' - 1 prompt(s)")
    assert cs.now_fragment() == "Claude Code on BuzzAnimate: Director or scenery not responding"


def test_narratives(logs):
    today = cs.narrative("today")
    assert today.startswith("Today in Claude Code: 2 session(s).")
    assert "BuzzAnimate (rigging): 'Director or scenery not responding' - 2 prompt(s)" in today and "wrote rig.py, sound.py" in today
    assert "3 file(s) written and 2 command(s) run" in today
    assert cs.narrative("yesterday") == "Yesterday: no Claude Code sessions."
    assert cs.narrative("today", project="nothing") == "Today: no Claude Code sessions on nothing."
    story = cs.session_story(cs.find_session("buzzanimate"))
    assert story.startswith("Claude Code session on BuzzAnimate (rigging)") and "You asked: make the director move; now add sound." in story
    assert "Files written: rig.py, sound.py." in story and "Claude's last words: Done with: now add sound" in story
    assert cs.find_session("last")["id"] == "aaa" and cs.find_session("tray")["id"] == "bbb" and cs.find_session("zzz") is None


def test_claude_phrases_route_without_the_model():
    assert fast_path("what was I doing in claude")[0] == "claude_activity"
    tool, args = fast_path("what did claude do yesterday")
    assert tool == "claude_activity" and args == {"when": "yesterday", "project": None}
    tool, args = fast_path("what did claude change in dexter")
    assert tool == "claude_activity" and args["project"] == "dexter"
    assert fast_path("show me the last claude session")[0] == "claude_session"
    tool, args = fast_path("what happened in the claude session on buzzanimate")
    assert tool == "claude_session" and args == {"which": "buzzanimate"}
