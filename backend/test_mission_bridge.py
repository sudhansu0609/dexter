"""
The Mission Reminder bridge (v13).

Reading is a file the app publishes, so that half is tested against a real
mirror. Writing goes through the app's own UI - tabs by Ctrl+1..7, fields and
buttons by name - so those tests assert on the *sequence* that gets sent, and on
the thing that matters when the UI moves: the request is recorded in the outbox
and the reply says it failed, rather than claiming a save that never happened.
"""

import json
import os

import pytest

import mission_bridge as bridge


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """A runtime mirror like the one the app writes."""
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({
        "mission": {"statement": "One video a week, forever",
                    "whys": ["So the studio runs without me", "To stop starting over"]},
        "today_blocks": [
            {"id": "b1", "name": "Deep work", "start": "2026-09-11T09:00:00+00:00",
             "end": "2026-09-11T09:50:00+00:00", "status": "done"},
            {"id": "b2", "name": "Edit", "start": "2026-09-11T14:00:00+00:00",
             "end": "2026-09-11T15:00:00+00:00", "status": "upcoming"},
            {"id": "b3", "name": "Gym", "start": "2026-09-11T07:00:00+00:00",
             "end": "2026-09-11T07:40:00+00:00", "status": "missed"},
        ],
        "session": {"block_id": "b2", "name": "Edit",
                    "started_at": "2026-09-11T14:02:00+00:00", "paused": False},
        "next_nudge": {"at": "2026-09-11T14:30:00+00:00", "kind": "drift",
                       "text": "You said you would be editing"},
        "updated_at": "2026-09-11T14:05:00+00:00",
    }), encoding="utf-8")
    monkeypatch.setenv("MISSION_REMINDER_RUNTIME", str(path))
    return path


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "OUTBOX_FILE", str(tmp_path / "mission_outbox.json"))
    monkeypatch.setattr(bridge, "DATA_DIR", str(tmp_path))
    return tmp_path / "mission_outbox.json"


# ───────────────────────── reading ─────────────────────────


def test_review_reads_the_mission_and_the_blocks(mirror):
    state = bridge.review()
    assert state["running"] is True
    assert state["statement"].startswith("One video a week")
    assert len(state["blocks"]) == 3
    assert state["session"]["name"] == "Edit"
    assert state["nudge"]["text"].startswith("You said")


def test_the_review_is_said_as_one_sentence(mirror):
    said = bridge.describe(bridge.review())
    assert "One video a week" in said
    assert "1 kept" in said and "1 missed" in said
    assert "'Edit' is running" in said


def test_without_the_app_running_it_says_so_instead_of_guessing(monkeypatch, tmp_path):
    monkeypatch.setenv("MISSION_REMINDER_RUNTIME", str(tmp_path / "absent.json"))
    state = bridge.review()
    assert state == {"running": False}
    assert "isn't running" in bridge.describe(state)


def test_a_stale_mirror_is_not_treated_as_now(mirror, monkeypatch):
    """The app removes the file when it quits, but a crash can leave it behind -
    and "a block is running" about yesterday would mute Dexter for ever."""
    old = os.path.getmtime(mirror) - 3600
    os.utime(mirror, (old, old))
    assert bridge.review() == {"running": False}


# ───────────────────────── the labels are data ─────────────────────────


def test_the_ui_labels_can_be_overridden_without_a_code_change(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "UI_FILE", str(tmp_path / "mission_ui.json"))
    (tmp_path / "mission_ui.json").write_text(
        json.dumps({"newBlock": "Add block", "tabs": {"blocks": 5}}), encoding="utf-8"
    )
    labels = bridge.ui()
    assert labels["newBlock"] == "Add block"
    assert labels["tabs"]["blocks"] == 5
    assert labels["saveBlock"] == "Save block", "unmentioned labels keep their default"


@pytest.mark.parametrize("spoken,value", [
    ("9", "09:00"), ("9am", "09:00"), ("9:30", "09:30"), ("2pm", "14:00"),
    ("12am", "00:00"), ("12pm", "12:00"), ("21:30", "21:30"),
])
def test_spoken_times_become_what_a_time_field_wants(spoken, value):
    assert bridge._time_value(spoken) == value


# ───────────────────────── writing ─────────────────────────


@pytest.fixture
def app(monkeypatch):
    """The app's window, with every keystroke, click and field write recorded."""
    import desktop_control as dc

    win = dc.Win(hwnd=11, title="Mission Reminder", pid=77, process="electron.exe")
    sent: list = []
    monkeypatch.setattr(bridge, "_window", lambda: win)
    monkeypatch.setattr(dc, "_focused_target", lambda name=None: win)
    monkeypatch.setattr(dc, "_send_keys", lambda combo: sent.append(("key", combo)))
    monkeypatch.setattr(dc, "_write_text", lambda text: sent.append(("type", text)))
    monkeypatch.setattr(dc, "click_text",
                        lambda label, window=None, nth=1: sent.append(("click", label))
                        or {"clicked": label})

    class Edit:
        def __init__(self, name):
            self.name = name

        def set_focus(self):
            sent.append(("focus", self.name))

    def _clickables(label, window=None, types=None, **kwargs):
        return [{"element": Edit(label), "label": label, "kind": "edit", "rank": 3}]

    monkeypatch.setattr(dc, "find_clickables", _clickables)
    return sent


def test_adding_a_block_drives_the_apps_own_ui(app, outbox):
    result = bridge.add_block("Deep work", start="9am", minutes=50)
    assert result["saved"] is True
    keys = [value for kind, value in app if kind == "key"]
    clicks = [value for kind, value in app if kind == "click"]
    typed = [value for kind, value in app if kind == "type"]
    assert "ctrl+4" in keys, "the Blocks tab is Ctrl+4"
    assert clicks == ["New block", "Save block"]
    assert "Deep work" in typed and "09:00" in typed and "50" in typed


def test_a_block_that_saved_is_recorded_as_saved(app, outbox):
    bridge.add_block("Deep work", start="9am")
    items = json.loads(outbox.read_text(encoding="utf-8"))
    assert items[-1]["kind"] == "block"
    assert items[-1]["state"] == "saved"


def test_a_missing_button_fails_loudly_and_keeps_the_request(app, outbox, monkeypatch):
    import desktop_control as dc

    def _no_button(label, window=None, nth=1):
        raise dc.DesktopError(f"Nothing called '{label}'")

    monkeypatch.setattr(dc, "click_text", _no_button)
    with pytest.raises(bridge.MissionError) as excinfo:
        bridge.add_block("Deep work")
    assert "mission_ui.json" in str(excinfo.value), "say how to fix it"
    items = json.loads(outbox.read_text(encoding="utf-8"))
    assert items[-1]["state"] == "failed"
    assert items[-1]["payload"]["name"] == "Deep work"
    assert bridge.pending() == [], "a failed write is not left pending"


def test_a_missing_field_refuses_before_typing_into_the_wrong_place(app, outbox, monkeypatch):
    import desktop_control as dc

    monkeypatch.setattr(dc, "find_clickables", lambda *a, **k: [])
    with pytest.raises(bridge.MissionError) as excinfo:
        bridge.add_block("Deep work", start="9am")
    assert "Nothing was saved" in str(excinfo.value)


def test_saving_a_goal_and_the_mission_use_their_own_tabs_and_buttons(app, outbox):
    bridge.add_goal("Ship version two", why="It is the whole year")
    bridge.save_mission("One video a week, forever")
    keys = [value for kind, value in app if kind == "key"]
    clicks = [value for kind, value in app if kind == "click"]
    assert "ctrl+3" in keys and "ctrl+2" in keys
    assert "Save goal" in clicks and "Save mission" in clicks


def test_an_empty_block_name_is_a_question_not_a_save(outbox):
    with pytest.raises(bridge.MissionError):
        bridge.add_block("   ")
    assert not os.path.exists(outbox) or json.loads(outbox.read_text(encoding="utf-8")) == []


def test_starting_the_next_block_uses_the_apps_shortcut(app, mirror, outbox):
    result = bridge.start_next_block()
    keys = [value for kind, value in app if kind == "key"]
    assert "ctrl+1" in keys and "ctrl+enter" in keys
    assert result["session"]["name"] == "Edit"


def test_the_outbox_keeps_a_history_that_can_be_read_back(app, outbox):
    bridge.add_block("One")
    bridge.add_goal("Two")
    history = bridge.history(10)
    assert [item["kind"] for item in history] == ["block", "goal"]
    assert all(item["state"] == "saved" for item in history)


def test_go_to_tab_sends_app_shortcut(app):
    info = bridge.go_to("goals")
    keys = [value for kind, value in app if kind == "key"]
    assert "ctrl+3" in keys
    assert info["tab"] == "goals"


def test_go_to_tab_normalizes_aliases(app):
    info1 = bridge.go_to("goal")
    assert info1["tab"] == "goals"
    info2 = bridge.go_to("goals tab")
    assert info2["tab"] == "goals"
    info3 = bridge.go_to("blocks tab")
    assert info3["tab"] == "blocks"


def test_mission_go_to_tab_tool(app):
    from tools_mission_app import mission_go_to_tab

    res = mission_go_to_tab("goals")
    assert res.ok
    assert "Goals" in res.summary
