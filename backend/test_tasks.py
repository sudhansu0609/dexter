"""
Autonomy tests: task queue lifecycle, checkpoint confirmations, guardrails,
voice approval, recorded macros, scheduled goals, and proactive rate limits.
The LLM and the router are mocked - these test the machinery, not the model.

Run:  pytest backend/test_tasks.py -v
"""

import os
import sys
import time
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import macros
import proactive
import router
import tasks
from router import RouteResult
from tool_registry import ToolResult


class FakeLLM:
    """Plans every goal as two fixed steps."""

    def generate(self, prompt, **kwargs):
        return '{"steps": ["step one", "step two"]}'


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "TASKS_FILE", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(tasks, "SCHEDULED_FILE", str(tmp_path / "scheduled.json"))
    monkeypatch.setattr(tasks, "llm_router", FakeLLM())
    return tasks.TaskQueue()


# ───────────────────────── task lifecycle ─────────────────────────


def test_task_runs_to_done(queue, monkeypatch):
    calls = []

    def fake_route(message, **kwargs):
        calls.append(message)
        return RouteResult(reply=f"did: {message}", action="agent", tier=2,
                           tool_calls=[{"name": "x", "args": {}, "ok": True, "summary": "ok"}])

    monkeypatch.setattr(router, "route", fake_route)
    submitted = queue.submit("test goal")
    assert submitted["ok"]
    assert wait_for(lambda: (queue.get(submitted["id"]) or {}).get("status") == "done")
    record = queue.get(submitted["id"])
    assert record["plan"] == ["step one", "step two"]
    assert calls == ["step one", "step two"]
    assert record["result"] == "did: step two"
    assert record["toolCallsUsed"] == 2
    assert any("Step 2/2" in p["text"] for p in record["progress"])


def test_checkpoint_approve_resumes(queue, monkeypatch):
    sequence = []

    def fake_route(message, **kwargs):
        sequence.append(message)
        if message == "step one" and sequence.count("step one") == 1:
            return RouteResult(reply="Just to confirm - send the email?",
                               action="confirm", tier=2, needs_confirmation=True)
        return RouteResult(reply=f"done: {message}", action="agent", tier=2)

    monkeypatch.setattr(router, "route", fake_route)
    submitted = queue.submit("emailish goal")
    task_id = submitted["id"]
    assert wait_for(lambda: (queue.get(task_id) or {}).get("status") == "waiting_confirmation")
    assert queue.get(task_id)["question"].startswith("Just to confirm")

    assert queue.confirm(task_id, True)
    assert wait_for(lambda: (queue.get(task_id) or {}).get("status") == "done")
    # After approval the queue answers "yes" into the task session.
    assert "yes" in sequence


def test_checkpoint_deny_cancels(queue, monkeypatch):
    def fake_route(message, **kwargs):
        if message == "step one":
            return RouteResult(reply="Confirm?", action="confirm", tier=2,
                               needs_confirmation=True)
        return RouteResult(reply=f"done: {message}", action="agent", tier=2)

    monkeypatch.setattr(router, "route", fake_route)
    task_id = queue.submit("goal")["id"]
    assert wait_for(lambda: (queue.get(task_id) or {}).get("status") == "waiting_confirmation")
    queue.confirm(task_id, False)
    assert wait_for(lambda: (queue.get(task_id) or {}).get("status") == "cancelled")
    assert "denied" in queue.get(task_id)["result"]


def test_tool_call_guardrail(queue, monkeypatch):
    monkeypatch.setattr(tasks, "MAX_TOOL_CALLS", 3)

    def fake_route(message, **kwargs):
        many = [{"name": "x", "args": {}, "ok": True, "summary": "ok"}] * 5
        return RouteResult(reply="worked", action="agent", tier=2, tool_calls=many)

    monkeypatch.setattr(router, "route", fake_route)
    task_id = queue.submit("expensive goal")["id"]
    assert wait_for(lambda: (queue.get(task_id) or {}).get("status") == "failed")
    assert "limit" in queue.get(task_id)["result"]


def test_voice_confirmation_targets_waiting_task(queue, monkeypatch):
    def fake_route(message, **kwargs):
        if message == "step one":
            return RouteResult(reply="Confirm?", action="confirm", tier=2,
                               needs_confirmation=True)
        return RouteResult(reply="done", action="agent", tier=2)

    monkeypatch.setattr(router, "route", fake_route)
    task_id = queue.submit("goal")["id"]
    assert wait_for(lambda: (queue.get(task_id) or {}).get("status") == "waiting_confirmation")

    assert queue.try_voice_confirmation("open chrome") is None  # not a yes/no
    answer = queue.try_voice_confirmation("yes go ahead")
    assert answer and answer["action"] == "task_confirmed"
    assert wait_for(lambda: (queue.get(task_id) or {}).get("status") == "done")


def test_cancel_queued_task(queue, monkeypatch):
    monkeypatch.setattr(router, "route",
                        lambda *a, **k: RouteResult(reply="x", action="agent", tier=2))
    # Cancel before the worker can pick it up by cancelling a second submission
    first = queue.submit("one")["id"]
    second = queue.submit("two")["id"]
    queue.cancel(second)
    assert wait_for(lambda: (queue.get(first) or {}).get("status") == "done")
    assert queue.get(second)["status"] == "cancelled"


# ───────────────────────── macros ─────────────────────────


@pytest.fixture()
def skills_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(macros, "SKILLS_DIR", str(tmp_path / "skills"))
    macros._recording.clear()
    return tmp_path / "skills"


def test_record_and_replay_with_parameter(skills_dir, monkeypatch):
    assert macros.start_recording("publish routine").ok
    # Control tools and failed calls must not be captured.
    macros.observe_tool_call("default", "record_skill", {"name": "x"}, True)
    macros.observe_tool_call("default", "search_files", {"query": "intro"}, True)
    macros.observe_tool_call("default", "broken_tool", {}, False)
    macros.observe_tool_call("default", "create_note", {"content": "render the intro today"}, True)
    macros.observe_tool_call("default", "launch_app", {"app_name": "notepad"}, True)
    saved = macros.stop_recording()
    assert saved.ok and "3 step(s)" in saved.summary

    skill = macros.get_skill("publish routine")
    assert skill["slot"] == "intro"
    assert [s["tool"] for s in skill["steps"]] == ["search_files", "create_note", "launch_app"]

    executed = []

    def fake_execute(name, args):
        executed.append((name, args))
        return ToolResult(ok=True, summary=f"{name} ok")

    monkeypatch.setattr(macros, "execute", fake_execute)
    result = macros.replay("publish routine", parameter="outro")
    assert result.ok
    assert executed[0] == ("search_files", {"query": "outro"})
    assert executed[1][1]["content"] == "render the outro today"
    assert executed[2] == ("launch_app", {"app_name": "notepad"})


def test_replay_stops_on_failed_step(skills_dir, monkeypatch):
    macros.start_recording("fragile")
    macros.observe_tool_call("default", "a_tool", {"v": "x"}, True)
    macros.observe_tool_call("default", "b_tool", {}, True)
    macros.stop_recording()

    monkeypatch.setattr(
        macros, "execute",
        lambda name, args: ToolResult(ok=(name != "a_tool"), summary=f"{name}"),
    )
    result = macros.replay("fragile")
    assert not result.ok and "step 1" in result.summary


def test_legacy_skill_not_replayable(skills_dir):
    os.makedirs(skills_dir, exist_ok=True)
    import json
    with open(skills_dir / "old.json", "w", encoding="utf-8") as fh:
        json.dump({"name": "old prose skill", "steps": ["do a thing", "do another"]}, fh)
    listed = macros.list_skills()
    assert listed and listed[0]["executable"] is False
    assert not macros.replay("old prose skill").ok


def test_stop_without_steps_fails(skills_dir):
    macros.start_recording("empty one")
    assert not macros.stop_recording().ok
    assert macros.get_skill("empty one") is None


# ───────────────────────── scheduled goals ─────────────────────────


def test_schedule_recurring_and_cancel(queue, monkeypatch):
    result = tasks.schedule_goal("curate video ideas and email me", "every friday at 5pm")
    assert result["ok"]
    entries = tasks.list_scheduled()
    assert len(entries) == 1 and entries[0]["cron"]

    removed = tasks.cancel_scheduled("curate")
    assert removed and tasks.list_scheduled() == []


def test_schedule_one_shot_and_stale_drop(queue):
    result = tasks.schedule_goal("clean downloads", "tomorrow at 9am")
    assert result["ok"]
    entries = tasks.list_scheduled()
    assert entries[0]["at"] and not entries[0]["cron"]

    # A one-shot whose time already passed is dropped on re-arm.
    entries[0]["at"] = "2020-01-01T09:00:00"
    tasks._save_scheduled(entries)
    tasks.rearm_scheduled()
    assert tasks.list_scheduled() == []


def test_schedule_bad_time_rejected(queue):
    assert not tasks.schedule_goal("goal", "whenever you feel like it")["ok"]


def test_scheduled_command_action_dispatches(queue, monkeypatch):
    """A scheduled command fires the shell, not the goal planner."""
    import subprocess

    import notify

    seen = {}
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: seen.setdefault("argv", argv))
    monkeypatch.setattr(notify, "toast_async", lambda *a, **k: None)

    res = tasks.create_scheduled({"type": "command", "command": "echo hi", "shell": "cmd"}, "tomorrow at 9am")
    assert res["ok"]
    entry = tasks.list_scheduled()[0]
    assert entry["action"]["type"] == "command"

    tasks._run_action(entry)
    assert "argv" in seen and "echo hi" in " ".join(seen["argv"])


def test_scheduled_app_action_dispatches(queue, monkeypatch):
    import notify
    import tools_core

    monkeypatch.setattr(notify, "toast_async", lambda *a, **k: None)
    seen = {}

    def fake_launch(cls, name):
        seen["app"] = name
        return {"success": True}

    monkeypatch.setattr(tools_core.AppLauncherTool, "launch_app", classmethod(fake_launch))

    res = tasks.create_scheduled({"type": "app", "app": "Notepad"}, "tomorrow at 9am")
    assert res["ok"]
    tasks._run_action(tasks.list_scheduled()[0])
    assert seen.get("app") == "Notepad"


def test_run_now_fires_without_removing(queue, monkeypatch):
    import notify

    monkeypatch.setattr(notify, "toast_async", lambda *a, **k: None)
    res = tasks.create_scheduled({"type": "goal", "goal": "brief me"}, "every friday at 5pm")

    fired = {}
    monkeypatch.setattr(tasks.task_queue, "submit", lambda goal, **k: fired.setdefault("goal", goal))

    assert tasks.run_now(res["id"]) is True
    assert fired["goal"] == "brief me"
    assert len(tasks.list_scheduled()) == 1, "run-now must not retire a recurring entry"


def test_cancel_scheduled_by_id(queue):
    res = tasks.create_scheduled({"type": "app", "app": "Notepad"}, "every friday at 5pm")
    assert tasks.cancel_scheduled_by_id(res["id"]) is True
    assert tasks.list_scheduled() == []
    assert tasks.cancel_scheduled_by_id(res["id"]) is False


# ───────────────────────── proactive ─────────────────────────


def test_quiet_hours_window(monkeypatch):
    monkeypatch.setenv("DEXTER_QUIET_HOURS", "22-8")
    assert proactive.in_quiet_hours(datetime(2026, 8, 19, 23, 0))
    assert proactive.in_quiet_hours(datetime(2026, 8, 19, 3, 0))
    assert not proactive.in_quiet_hours(datetime(2026, 8, 19, 12, 0))
    monkeypatch.setenv("DEXTER_QUIET_HOURS", "0-0")
    assert not proactive.in_quiet_hours(datetime(2026, 8, 19, 3, 0))


def test_cooldown_and_daily_dedup(monkeypatch):
    proactive._last_sent.clear()
    proactive._sent_today.clear()
    assert proactive._should_send("calendar", "meeting soon")
    proactive._send("calendar", "meeting soon")
    # Identical suggestion is deduped for the day...
    assert not proactive._should_send("calendar", "meeting soon")
    # ...and a different one in the same category is held by the cooldown.
    assert not proactive._should_send("calendar", "other meeting")
    proactive._last_sent["calendar"] = time.time() - 100 * 60
    assert proactive._should_send("calendar", "other meeting")


def test_disabled_scan_sends_nothing(monkeypatch):
    monkeypatch.setenv("DEXTER_PROACTIVE", "0")
    assert proactive.run_scan() == 0
