"""
Scheduler tests (roadmap 7.2): re-arm on restart, missed-reminder catch-up,
and the maintenance jobs that fix bug 7 (prune fired reminders, dedup
overnight content on write).

APScheduler is optional in production and absent here on purpose: the tests
inject a fake scheduler + fake triggers, so what is exercised is Dexter's own
logic - which reminders get armed, fired, delivered and pruned - not APS.

Run:  pytest backend/test_scheduler.py -v
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scheduler as scheduler_module
from scheduler import REMINDER_RETENTION_DAYS, DexterScheduler


class FakeAPS:
    """Records job registrations instead of running a thread pool."""

    def __init__(self):
        self.jobs = {}
        self.removed = []

    def add_job(self, fn, trigger=None, args=None, id=None, **kwargs):
        self.jobs[id] = {"fn": fn, "trigger": trigger, "args": args or []}

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self.jobs.pop(job_id, None)

    def get_jobs(self):
        return list(self.jobs.values())


def fake_date_trigger(run_date=None):
    return ("date", run_date)


def fake_cron_trigger(**kwargs):
    return ("cron", kwargs)


@pytest.fixture()
def sched(tmp_path, monkeypatch):
    """A started scheduler wired to fakes and an isolated reminders file."""
    monkeypatch.setattr(scheduler_module, "REMINDERS_FILE", str(tmp_path / "reminders.json"))
    monkeypatch.setattr(scheduler_module, "DateTrigger", fake_date_trigger)
    monkeypatch.setattr(scheduler_module, "CronTrigger", fake_cron_trigger)
    # Toasts would hit the Windows shell; deliveries are asserted via _deliver.
    monkeypatch.setattr(scheduler_module.notify, "toast_async", lambda *a, **k: None)

    instance = DexterScheduler()
    instance._scheduler = FakeAPS()
    instance._started = True
    return instance


def iso(dt):
    return dt.isoformat(timespec="seconds")


# ───────────────────────── create / cancel ─────────────────────────


def test_create_reminder_arms_a_job(sched):
    reminder = sched.create_reminder("check the render", "in 2 hours")
    assert reminder["ok"]
    assert reminder["scheduled"]
    assert reminder["id"] in sched._scheduler.jobs
    stored = sched.list_reminders()
    assert len(stored) == 1 and stored[0]["text"] == "check the render"


def test_unparseable_time_is_reported_not_swallowed(sched):
    result = sched.create_reminder("do the thing", "whenever you fancy")
    assert result["ok"] is False
    assert "whenever you fancy" in result["error"]
    assert sched.list_reminders() == []


def test_recurring_reminder_uses_cron(sched):
    reminder = sched.create_reminder("stand up", "every day at 9am")
    assert reminder["ok"] and reminder["cron"] == {"hour": 9, "minute": 0}
    trigger = sched._scheduler.jobs[reminder["id"]]["trigger"]
    assert trigger == ("cron", {"hour": 9, "minute": 0})


def test_cancel_by_text_removes_job_and_record(sched):
    reminder = sched.create_reminder("water the plants", "in 3 hours")
    hit = sched.cancel_by_text("plants")
    assert hit and hit["id"] == reminder["id"]
    assert sched.list_reminders(include_fired=False) == []
    assert reminder["id"] in sched._scheduler.removed


# ───────────────────────── restart: re-arm + missed catch-up ─────────────────────────


def test_restore_rearms_future_and_delivers_missed(sched, monkeypatch):
    now = datetime.now()
    future = {
        "id": "r-future", "text": "future one", "at": iso(now + timedelta(hours=1)),
        "cron": None, "fired": False,
    }
    missed = {
        "id": "r-missed", "text": "missed one", "at": iso(now - timedelta(hours=2)),
        "cron": None, "fired": False,
    }
    already_fired = {
        "id": "r-done", "text": "old one", "at": iso(now - timedelta(days=1)),
        "cron": None, "fired": True,
    }
    sched._save([future, missed, already_fired])

    delivered = []
    monkeypatch.setattr(
        sched, "_deliver", lambda reminder, missed=False: delivered.append((reminder["id"], missed))
    )

    restored = sched._restore_reminders()

    # The future reminder is re-armed; the missed one is surfaced once, now.
    assert restored == 1
    assert "r-future" in sched._scheduler.jobs
    assert delivered == [("r-missed", True)]

    # And the missed one is marked fired on disk so a second restart is silent.
    stored = {r["id"]: r for r in sched.list_reminders()}
    assert stored["r-missed"]["fired"] is True
    assert stored["r-future"]["fired"] is False


def test_restore_rearms_recurring_even_after_firing(sched):
    cron = {"id": "r-cron", "text": "daily", "at": None,
            "cron": {"hour": 9, "minute": 0}, "fired": True}
    sched._save([cron])
    restored = sched._restore_reminders()
    assert restored == 1
    assert "r-cron" in sched._scheduler.jobs


# ───────────────────────── firing ─────────────────────────


def test_fire_marks_one_shot_fired(sched, monkeypatch):
    reminder = sched.create_reminder("tea", "in 30 minutes")
    delivered = []
    monkeypatch.setattr(
        sched, "_deliver", lambda r, missed=False: delivered.append(r["id"])
    )

    sched._fire(reminder["id"])

    assert delivered == [reminder["id"]]
    stored = sched.list_reminders()[0]
    assert stored["fired"] is True and stored.get("firedAt")


def test_fire_keeps_recurring_unfired(sched, monkeypatch):
    reminder = sched.create_reminder("stand up", "every day at 9am")
    monkeypatch.setattr(sched, "_deliver", lambda r, missed=False: None)

    sched._fire(reminder["id"])

    stored = sched.list_reminders()[0]
    assert stored["fired"] is False  # cron reminders fire again tomorrow


# ───────────────────────── maintenance: prune (bug 7) ─────────────────────────


def test_prune_removes_only_old_fired_one_shots(sched):
    now = datetime.now()
    old_fired = {
        "id": "r-old", "text": "ancient", "cron": None, "fired": True,
        "firedAt": iso(now - timedelta(days=REMINDER_RETENTION_DAYS + 1)),
    }
    fresh_fired = {
        "id": "r-fresh", "text": "recent", "cron": None, "fired": True,
        "firedAt": iso(now - timedelta(days=1)),
    }
    pending = {
        "id": "r-pending", "text": "upcoming", "cron": None, "fired": False,
        "at": iso(now + timedelta(days=30)),
    }
    old_cron = {
        "id": "r-cron", "text": "daily", "cron": {"hour": 9, "minute": 0}, "fired": True,
        "firedAt": iso(now - timedelta(days=30)),
    }
    sched._save([old_fired, fresh_fired, pending, old_cron])

    removed = sched._prune_reminders()

    assert removed == 1
    kept = {r["id"] for r in sched.list_reminders()}
    assert kept == {"r-fresh", "r-pending", "r-cron"}


# ───────────────────────── maintenance: overnight dedup (bug 7) ─────────────────────────


def test_overnight_curation_dedups_on_write(tmp_path, monkeypatch):
    import tools_core

    monkeypatch.setattr(tools_core, "OVERNIGHT_FILE", str(tmp_path / "overnight.json"))

    class FakeLLM:
        def generate(self, *a, **k):
            return (
                '[{"title": "Same Idea", "hook": "h", "scriptOutline": [],'
                ' "targetAudience": "t", "ctrScore": "9%"}]'
            )

    controller = tools_core.SpilledCoffeeAIController(FakeLLM())
    first = controller.run_overnight_curation("test niche")
    second = controller.run_overnight_curation("test niche")

    assert first["count"] == 1
    assert second["count"] == 0  # identical title skipped on re-run
    titles = [i["title"] for i in controller.get_overnight_content()]
    assert titles == ["Same Idea"]
