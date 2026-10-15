"""
Activity awareness (v7, K7): samples become sessions and totals, titles and
paths map to projects, recent files are found within a budget, and the
answers read like a colleague who was looking over your shoulder.
"""

import os
import re
import time
from datetime import datetime

import pytest

import activity
import tools_activity  # noqa: F401
from router import fast_path

PROJECTS = [("Buzzcaf_Media", r"X:\p\Buzzcaf_Media"), ("dexter", r"X:\p\Buzzcaf_Media\dexter"), ("beyond3baje", r"X:\p\beyond3baje")]


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "known_projects", lambda: PROJECTS)
    store = activity.ActivityLog(path=str(tmp_path / "activity.jsonl"))
    monkeypatch.setattr(activity, "activity_log", store)
    monkeypatch.setattr(store, "recent_files", lambda hours=6, limit=12, force=False: [
        {"path": r"X:\p\Buzzcaf_Media\dexter\backend\router.py", "name": "router.py", "project": "dexter", "minutes_ago": 3, "ext": "py"},
    ][:limit])
    return store


def _fill(store, start, minutes, app, title, step=30):
    for i in range(int(minutes * 60 / step)):
        store.add_sample(start + i * step, app, title, persist=False)


def test_samples_fold_into_sessions_and_totals(log):
    t0 = time.time() - 3600
    _fill(log, t0, 40, "VS Code", "router.py - dexter - Visual Studio Code")
    _fill(log, t0 + 40 * 60, 10, "Chrome", "Channel dashboard - YouTube Studio - Google Chrome")
    _fill(log, t0 + 50 * 60, 5, "", "", )                                  # away
    _fill(log, t0 + 55 * 60, 5, "VS Code", "agenda.py - dexter - Visual Studio Code")
    sessions = log.sessions(t0 - 1)
    assert [(s["app"], s["project"]) for s in sessions] == [("VS Code", "dexter"), ("Chrome", ""), ("VS Code", "dexter")]
    assert sessions[0]["minutes"] == 40 and sessions[0]["title"] == "router.py - dexter"
    totals = log.totals(t0 - 1)
    assert totals["apps"][0] == {"app": "VS Code", "minutes": 45, "top": [("router.py - dexter", 40), ("agenda.py - dexter", 5)]}
    assert totals["projects"] == [{"project": "dexter", "minutes": 45}]
    current = log.current()
    assert current and current["app"] == "VS Code" and current["project"] == "dexter" and current["minutes"] == 5


def test_projects_from_titles_and_paths():
    assert activity.project_for_title("edit_v3.prproj - Adobe Premiere Pro - beyond3baje", PROJECTS) == "beyond3baje"
    assert activity.project_for_title("New Tab - Google Chrome", PROJECTS) == ""
    assert activity.project_for_path(r"X:\p\Buzzcaf_Media\dexter\backend\router.py", PROJECTS) == "dexter"
    assert activity.project_for_path(r"X:\p\beyond3baje\ep4\script.md", PROJECTS) == "beyond3baje"
    assert activity.friendly_app("Code.exe") == "VS Code" and activity.friendly_app("weird.exe") == "weird"


def test_recent_files_scan_finds_the_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "known_projects", lambda: [("proj", str(tmp_path / "proj"))])
    (tmp_path / "proj" / "node_modules").mkdir(parents=True)
    (tmp_path / "proj" / "node_modules" / "x.js").write_text("x")
    old = tmp_path / "proj" / "old.md"
    old.write_text("old")
    os.utime(old, (time.time() - 48 * 3600, time.time() - 48 * 3600))
    fresh = tmp_path / "proj" / "script.md"
    fresh.write_text("fresh")
    (tmp_path / "proj" / "notes.log").write_text("log")
    files = activity.scan_recent_files([str(tmp_path)], hours=6)
    names = [f["name"] for f in files]
    assert names[0] == "script.md" and "old.md" not in names and "x.js" not in names and "notes.log" not in names
    assert files[0]["project"] == "proj"


def test_narratives_and_context(log):
    t0 = time.time() - 30 * 60
    _fill(log, t0, 30, "Premiere Pro", "ep4_cut.prproj - Adobe Premiere Pro - beyond3baje")
    now = activity.narrative("now")
    assert now.startswith("You're in Premiere Pro on beyond3baje") and "router.py in dexter (3 min ago)" in now
    today = activity.narrative("today")
    # 29 or 30: the samples are a minute apart and the clock can tick mid-test.
    assert today.startswith("Today:")
    assert re.search(r"Premiere Pro (29|30) min", today), today
    assert re.search(r"beyond3baje (29|30) min", today), today
    assert activity.narrative("yesterday").startswith("Yesterday: I don't have any activity")
    lines = activity.context_lines()
    assert lines[0].startswith("In focus now: Premiere Pro on beyond3baje") and "Recently saved: router.py" in lines[1]
    assert re.search(r"Premiere Pro on beyond3baje · (29|30) min", activity.now_line())


def test_activity_phrases_route_without_the_model():
    assert fast_path("what am I working on")[0] == "what_am_i_doing"
    assert fast_path("what was I doing")[0] == "what_am_i_doing"
    tool, args = fast_path("what did I do yesterday")
    assert tool == "activity_summary" and args == {"when": "yesterday"}
    tool, args = fast_path("how did I spend my time today")
    assert tool == "activity_summary" and args == {"when": "today"}
