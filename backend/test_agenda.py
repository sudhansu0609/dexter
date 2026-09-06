"""
One agenda (roadmap v5, 5.2): commitments, reminders and events in one list;
add creates a commitment; complete closes it; notes are markdown files.
"""

import os

import pytest

import agenda
import tools_core
from workbook import Workbook


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    store = Workbook(path=str(tmp_path / "wb.db"))
    monkeypatch.setattr(agenda, "workbook", store)
    monkeypatch.setattr(agenda.scheduler, "list_reminders", lambda include_fired=True: [
        {"id": "r1", "text": "stand up", "at": f"{agenda.date.today().isoformat()}T15:30:00"},
        {"id": "r2", "text": "tomorrow thing", "at": "2099-01-01T09:00:00"},
    ])
    monkeypatch.setattr(agenda.scheduler, "create_reminder", lambda text, when: {"scheduled": True})
    monkeypatch.setattr(agenda, "_calendar_items", lambda on_date: [])
    return store


def test_agenda_merges_commitments_and_todays_reminders(isolated):
    isolated.add_commitment("script episode three", due_time="10:00")
    isolated.add_commitment("send invoice", due_date="2020-01-01")  # overdue
    snapshot = agenda.compose()
    titles = [t["title"] for t in snapshot["tasks"]]
    assert titles[0] == "send invoice"          # overdue first
    assert "script episode three" in titles
    assert "stand up" in titles and "tomorrow thing" not in titles
    kinds = {t["title"]: t["kind"] for t in snapshot["tasks"]}
    assert kinds["stand up"] == "reminder" and kinds["send invoice"] == "commitment"
    assert snapshot["counts"]["overdue"] == 1 and snapshot["counts"]["reminders"] == 1


def test_add_creates_a_commitment_and_complete_closes_it(isolated):
    item = agenda.add("review the edit", "at 4pm")
    assert item["kind"] == "commitment" and item["time"].endswith("PM")
    assert isolated.find_commitment("review the edit")
    done = agenda.complete("review the edit")
    assert done and done["status"] == "done"
    assert agenda.toggle(item["id"]) == {"success": False, "reason": "already done"}
    assert agenda.toggle("r-1")["success"] is False


def test_notes_are_markdown_files_in_the_notes_folder(tmp_path, monkeypatch):
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr(tools_core.NotesTool, "notes_dir", staticmethod(lambda: str(notes_dir)))
    monkeypatch.setattr(tools_core, "_ingest_note", lambda path: None)
    tool = tools_core.NotesTool()
    note = tool.create_note("buy a new SSD for the render rig", title="Render rig")
    assert note["path"].endswith(".md") and os.path.exists(note["path"])
    with open(note["path"], encoding="utf-8") as handle:
        body = handle.read()
    assert body.startswith("# Render rig") and "SSD" in body
    listed = tool.list_notes()
    assert listed and listed[0]["title"] == "Render rig"
    assert tool.delete_note(listed[0]["id"]) is True
    assert tool.list_notes() == []


def test_complete_never_matches_on_stopwords_alone(isolated):
    isolated.add_commitment("editing the intro")
    assert agenda.complete("record the voiceover") is None          # only 'the' in common
    assert agenda.complete("intro editing") is not None              # the real words match
