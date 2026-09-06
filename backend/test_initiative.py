"""
Initiative (v7, K6): Dexter proposes the next move, once per thing, a few a
day, and the "You lead" switch silences check-ins, nudges, tails and
initiatives alike.
"""

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
