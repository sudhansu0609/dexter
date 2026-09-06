"""
Dexter as a coworker (v7): the mission store, the stance in every prompt,
check-ins that land in the chat (not just toasts), short replies understood
as answers, coaching tails, and the mission tools' routing.
"""

import time

import pytest

import companion
import mission
import tools_mission  # noqa: F401
from router import fast_path
from workbook import Workbook


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(mission, "MISSION_FILE", str(tmp_path / "mission.json"))
    store = Workbook(path=str(tmp_path / "wb.db"))
    import workbook as wb_module

    monkeypatch.setattr(wb_module, "workbook", store)
    import agenda

    monkeypatch.setattr(agenda, "workbook", store)
    published = []
    monkeypatch.setattr(companion.bus, "publish", lambda kind, payload=None: published.append((kind, payload)))
    turns = []

    class FakeMemory:
        def add_turn(self, role, content, tool_calls=None, session="default"):
            turns.append((role, content, tool_calls))

        def get_summary(self, scope, period):
            return "Wrote the Bhangarh outline and fixed the audio" if scope == "day" else None

    import memory as memory_module

    monkeypatch.setattr(memory_module, "memory", FakeMemory())
    monkeypatch.setattr(companion, "compose", lambda kind, facts, fallback, ask="": fallback)
    monkeypatch.setattr(companion, "_quiet", lambda: False)
    monkeypatch.setattr(companion, "ENABLED", True)
    return {"store": store, "published": published, "turns": turns}


def test_mission_store_and_prompt_block(sandbox):
    mission.set_mission("get Beyond3Baje to 100k subs", "so the channels pay the bills")
    mission.set_focus("finish the Bhangarh script")
    mission.set_today_win("record the voiceover")
    mission.add_blocker("render keeps crashing")
    mission.add_win("published the short")
    block = companion.context_block()
    assert "coworkers on one mission" in block
    assert "100k subs" in block and "because so the channels pay the bills" in block
    assert "finish the Bhangarh script" in block and "record the voiceover" in block
    assert "render keeps crashing" in block and "published the short" in block
    assert "## Today:" in block
    assert mission.clear_blocker("render") == "render keeps crashing"
    assert "100k subs" in companion.fast_stance()


def test_morning_checkin_lands_in_the_chat_once(sandbox):
    store = sandbox["store"]
    store.add_commitment("edit episode two", due_time="10:00")
    store.add_commitment("send invoice", due_date="2020-01-01")
    assert companion.morning() is True
    kind, payload = sandbox["published"][-1]
    assert kind == "companion" and payload["kind"] == "morning"
    assert "Yesterday: Wrote the Bhangarh outline" in payload["text"]
    assert "edit episode two" in payload["text"] and "send invoice" in payload["text"]
    assert payload["text"].endswith("What's the one thing that makes today a win?")
    assert payload["quickReplies"] == ["edit episode two"]
    assert sandbox["turns"][-1][0] == "assistant" and sandbox["turns"][-1][2] == "checkin:morning"
    assert companion.morning() is False            # already said today
    assert companion.greet_if_needed() is False


def test_checkins_respect_snooze_and_quiet_hours(sandbox, monkeypatch):
    sandbox["store"].add_commitment("script the intro")
    mission.set_today_win("script the intro")
    mission.snooze(1)
    assert companion.midday() is False
    mission.update(snoozed_until=0)
    monkeypatch.setattr(companion, "_quiet", lambda: True)
    assert companion.midday() is False
    monkeypatch.setattr(companion, "_quiet", lambda: False)
    assert companion.midday() is True
    assert sandbox["published"][-1][1]["quickReplies"] == ["On it", "Done", "Stuck", "Later"]


def test_short_replies_answer_the_last_checkin(sandbox):
    store = sandbox["store"]
    store.add_commitment("record the voiceover")
    mission.set_today_win("record the voiceover")
    assert companion.handle_short_reply("done") is None          # no check-in yet: route normally
    companion.midday()
    reply = companion.handle_short_reply("done")
    assert reply and "Logged" in reply and "Commitment closed" in reply
    assert store.today()["done"][0]["text"] == "record the voiceover"
    assert mission.recent_wins()[0]["text"] == "record the voiceover"
    assert companion.handle_short_reply("later").startswith("Okay")
    assert mission.snoozed()
    assert companion.handle_short_reply("open chrome") is None
    assert "in the way" in companion.handle_short_reply("stuck")


def test_event_driven_messages_and_greeting(sandbox):
    assert companion.on_event("code_done", {"project": "dexter", "status": "done", "filesChanged": ["a.py"], "message": "Added the retry."})
    payload = sandbox["published"][-1][1]
    assert payload["kind"] == "code" and "dexter" in payload["text"] and "Added the retry." in payload["text"]
    assert companion.on_event("approval_needed", {"step": "script_draft", "name": "Bhangarh"})
    assert "waiting on us" in sandbox["published"][-1][1]["text"]
    mission.set_today_win("cut the trailer")
    assert "cut the trailer" in companion.greeting_reply()


def test_coaching_tail_after_tools(sandbox):
    store = sandbox["store"]
    a = store.add_commitment("edit episode two")
    store.add_commitment("send invoice", due_date="2020-01-01")
    companion._last_tail = 0
    tailed = companion.after_reply("add_task", True, "Committed to 'write hooks' today.")
    assert "send invoice" in tailed and "before this one" in tailed
    again = companion.after_reply("add_task", True, "Committed to 'x' today.")
    assert again == "Committed to 'x' today."                      # rate-limited
    store.complete_commitment(a["id"])
    assert companion.after_reply("complete_task", True, "Marked 'edit episode two' as done.").endswith("1 of 1 today.")
    assert companion.after_reply("browser_open", True, "Opened GitHub.") == "Opened GitHub."


def test_mission_phrases_route_without_the_model():
    tool, args = fast_path("today's win is recording the voiceover")
    assert tool == "set_today_win" and args == {"text": "recording the voiceover"}
    tool, args = fast_path("our mission is to get beyond3baje to 100k subscribers")
    assert tool == "set_mission" and args["north_star"].startswith("to get beyond3baje")
    tool, args = fast_path("this week's focus is finishing the bhangarh script")
    assert tool == "set_focus" and args == {"text": "finishing the bhangarh script"}
    tool, args = fast_path("I'm blocked by the render crashing")
    assert tool == "add_blocker" and args == {"text": "the render crashing"}
    tool, args = fast_path("log a win: the episode is live")
    assert tool == "log_win" and args == {"text": "the episode is live"}
    assert fast_path("how are we doing")[0] == "mission_status"
    assert fast_path("not now")[0] == "snooze_checkins"
    assert fast_path("check in with me")[0] == "check_in"
