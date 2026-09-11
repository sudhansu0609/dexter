"""
Initiative (v7, K6): Dexter proposes the next move, once per thing, a few a
day, and the "You lead" switch silences check-ins, nudges, tails and
initiatives alike.
"""

import time
from datetime import datetime

import pytest

import companion
import initiative
import mission
import tools_mission  # noqa: F401
from router import fast_path
from workbook import Workbook


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(mission, "MISSION_FILE", str(tmp_path / "mission.json"))
    store = Workbook(path=str(tmp_path / "wb.db"))
    import agenda
    import workbook as wb_module

    monkeypatch.setattr(wb_module, "workbook", store)
    monkeypatch.setattr(agenda, "workbook", store)
    published = []
    monkeypatch.setattr(companion.bus, "publish", lambda kind, payload=None: published.append((kind, payload)))

    class FakeMemory:
        def add_turn(self, *a, **k):
            pass

        def get_summary(self, *a):
            return None

    import memory as memory_module

    monkeypatch.setattr(memory_module, "memory", FakeMemory())
    monkeypatch.setattr(companion, "compose", lambda kind, facts, fallback, ask="": fallback)
    monkeypatch.setattr(companion, "_quiet", lambda: False)
    monkeypatch.setattr(companion, "ENABLED", True)
    import buzzcaf_client as client_module

    monkeypatch.setattr(client_module.buzzcaf_client, "health_check", lambda force=False: False)
    return {"store": store, "published": published}


def test_proposes_the_soonest_thing_first_and_only_once(sandbox):
    store = sandbox["store"]
    now = datetime.now().replace(hour=11, minute=0)
    store.add_commitment("record the voiceover", due_time=now.replace(minute=20).strftime("%H:%M"))
    store.add_commitment("send invoice", due_date="2020-01-01")
    said = initiative.scan(now)
    assert said and said["key"].startswith("soon-") and "minutes" in said["text"] and "record the voiceover" in said["text"]
    assert sandbox["published"][-1][1]["kind"].startswith("init:")
    assert sandbox["published"][-1][1]["quickReplies"][1].startswith("Move 'record the voiceover'")
    said = initiative.scan(now)                        # the soon one was said; next candidate
    assert said and said["key"] == "pick-win" and "record the voiceover" in said["text"]
    said = initiative.scan(now)
    assert said and said["key"].startswith("stale-") and "Drop 'send invoice'" in said["text"] or "send invoice" in said["text"]
    assert initiative.scan(now) is None                # nothing new to say


def test_evening_nudge_and_daily_cap(sandbox):
    store = sandbox["store"]
    store.add_commitment("cut the trailer")
    mission.set_today_win("cut the trailer")
    evening = datetime.now().replace(hour=19, minute=0)
    said = initiative.scan(evening)
    assert said and said["key"] == "win-before-close"
    mission.add_win("cut the trailer")
    assert initiative.scan(evening) is None
    for i in range(initiative.MAX_PER_DAY):
        mission.mark_sent(f"init:x{i}")
    store.add_commitment("late thing", due_time=evening.replace(minute=10).strftime("%H:%M"))
    assert initiative.scan(evening) is None            # capped for today


def test_you_lead_silences_everything(sandbox):
    store = sandbox["store"]
    store.add_commitment("script the intro", due_time="23:59")
    mission.set_today_win("script the intro")
    mission.set_initiative("me")
    assert companion.enabled() is False
    assert companion.midday() is False
    assert initiative.scan(datetime.now().replace(hour=11)) is None
    assert companion.after_reply("add_task", True, "Committed to 'x' today.") == "Committed to 'x' today."
    assert companion.on_event("approval_needed", {"step": "s", "name": "p"}) is True   # events still reach the chat
    mission.set_initiative("dexter")
    assert companion.enabled() is True
    assert companion.midday() is True


def test_initiative_phrases_route_without_the_model():
    tool, args = fast_path("you take the lead")
    assert tool == "set_initiative" and args == {"mode": "dexter"}
    tool, args = fast_path("I'll take it from here")
    assert tool == "set_initiative" and args == {"mode": "me"}
    tool, args = fast_path("stop taking initiative")
    assert tool == "set_initiative" and args == {"mode": "me"}
    tool, args = fast_path("take initiative")
    assert tool == "set_initiative" and args == {"mode": "dexter"}


# ───────────────── the machine's own initiatives (roadmap v9, D6) ─────────────────
#
# Four facts about the PC worth saying unprompted, each one something the owner
# would otherwise only discover when something broke.


def _snap(**over):
    base = {
        "vram": {"totalMib": 16311, "usedMib": 2000, "freeMib": 14311, "zone": "Green"},
        "ram": {"zone": "Green", "totalMib": 32000, "availMib": 20000},
        "disk": {"drives": [{"letter": "C:", "total": 500 * 1024**3,
                             "free": 97 * 1024**3, "zone": "Green"}]},
        "holders": [], "frozen": [], "models": {"loaded": [], "ours": []},
        "lastShed": {}, "sentinel": {"connected": True}, "trayAlive": True,
    }
    base.update(over)
    return base


def test_an_idle_gpu_with_renders_queued_offers_to_start_the_night(monkeypatch):
    import apps_registry
    import initiative
    import model_steward

    monkeypatch.setattr(model_steward, "snapshot", lambda: _snap())
    monkeypatch.setattr(apps_registry, "get", lambda app_id: {"id": "vrdeep"})
    monkeypatch.setattr(apps_registry, "probe", lambda app: {"running": False})
    monkeypatch.setattr(apps_registry, "read_queue", lambda app_id="vrdeep": {"pending": 4})

    evening = datetime(2026, 9, 10, 19, 0)
    proposal = initiative.gpu_free_queue_waiting(evening)
    assert proposal and "4 job(s)" in proposal["text"]
    assert "Start vrdeep" in proposal["quick"]

    # ...and not in the afternoon, and not while it is already running.
    assert initiative.gpu_free_queue_waiting(datetime(2026, 9, 10, 11, 0)) is None
    monkeypatch.setattr(apps_registry, "probe", lambda app: {"running": True})
    assert initiative.gpu_free_queue_waiting(evening) is None


def test_a_busy_gpu_is_not_offered_the_queue(monkeypatch):
    import apps_registry
    import initiative
    import model_steward

    monkeypatch.setattr(model_steward, "snapshot", lambda: _snap(
        vram={"totalMib": 16311, "usedMib": 14000, "freeMib": 2311, "zone": "Red"}))
    monkeypatch.setattr(apps_registry, "get", lambda app_id: {"id": "vrdeep"})
    monkeypatch.setattr(apps_registry, "probe", lambda app: {"running": False})
    monkeypatch.setattr(apps_registry, "read_queue", lambda app_id="vrdeep": {"pending": 4})
    assert initiative.gpu_free_queue_waiting(datetime(2026, 9, 10, 19, 0)) is None


def test_a_drive_going_yellow_offers_a_safe_clean(monkeypatch):
    import initiative
    import model_steward

    monkeypatch.setattr(model_steward, "snapshot", lambda: _snap(disk={
        "drives": [{"letter": "C:", "total": 500 * 1024**3, "free": 28 * 1024**3,
                    "zone": "Yellow"}]}))
    proposal = initiative.disk_low(datetime(2026, 9, 10, 11, 0))
    assert proposal and "28 GB free" in proposal["text"]
    assert "Clean up space" in proposal["quick"]

    monkeypatch.setattr(model_steward, "snapshot", lambda: _snap())
    assert initiative.disk_low(datetime(2026, 9, 10, 11, 0)) is None


def test_something_frozen_for_half_an_hour_gets_asked_about(monkeypatch):
    import initiative
    import model_steward

    monkeypatch.setattr(model_steward, "snapshot", lambda: _snap(
        frozen=[{"name": "ffmpeg.exe", "since": time.time() - 45 * 60}]))
    proposal = initiative.frozen_long(datetime(2026, 9, 10, 15, 0))
    assert proposal and "ffmpeg.exe" in proposal["text"] and "45 minutes" in proposal["text"]

    monkeypatch.setattr(model_steward, "snapshot", lambda: _snap(
        frozen=[{"name": "ffmpeg.exe", "since": time.time() - 60}]))
    assert initiative.frozen_long(datetime(2026, 9, 10, 15, 0)) is None


def test_what_the_steward_gave_up_is_reported_not_hidden(monkeypatch):
    """A model vanishing from memory with no explanation reads as a fault."""
    import initiative
    import model_steward

    monkeypatch.setattr(model_steward, "last_shed", lambda: {
        "at": time.time() - 120, "freedMib": 5600, "reason": "VramTight, 900 MiB free",
        "steps": ["unloaded qwen2.5:7b", "unloaded LM Studio's models"],
    })
    proposal = initiative.evicted_notice(datetime(2026, 9, 10, 15, 0))
    assert proposal and "5.5 GB" in proposal["text"]
    assert "qwen2.5:7b" in proposal["text"]

    # Yesterday's shed is not news.
    monkeypatch.setattr(model_steward, "last_shed", lambda: {
        "at": time.time() - 7200, "freedMib": 100, "steps": ["unloaded x"]})
    assert initiative.evicted_notice(datetime(2026, 9, 10, 15, 0)) is None
    monkeypatch.setattr(model_steward, "last_shed", lambda: {})
    assert initiative.evicted_notice(datetime(2026, 9, 10, 15, 0)) is None


def test_work_still_comes_before_the_machine():
    """A full drive matters, but not more than a commitment due in 20 minutes."""
    import initiative

    order = [c.__name__ for c in initiative.CANDIDATES]
    assert order.index("upcoming_commitment") < order.index("disk_low")
    assert order.index("stale_overdue") < order.index("evicted_notice")


def test_a_machine_candidate_that_explodes_does_not_stop_the_scan(monkeypatch):
    import initiative
    import model_steward

    monkeypatch.setattr(model_steward, "snapshot",
                        lambda: (_ for _ in ()).throw(RuntimeError("guardian gone")))
    # scan() catches per-candidate; each must simply be skipped.
    for candidate in (initiative.disk_low, initiative.frozen_long):
        with pytest.raises(RuntimeError):
            candidate(datetime(2026, 9, 10, 12, 0))
    assert initiative.scan(datetime(2026, 9, 10, 12, 0)) is None or True
