"""
Continuous note-taking (v13).

The interesting cases are not "does it write the line" but the ones that decide
whether dictation is usable: spoken punctuation, "scratch that", the phrases that
end it, and - most of all - that while it is running nothing the owner says
becomes a chat turn or reaches the language model.

Nothing here types into a real app: the window sinks go through
`desktop_control.type_text`, which conftest has already made impossible to call
for real.
"""

import os

import numpy as np
import pytest

import dictation as dictation_module
import voice_pipeline
from dictation import Dictation
from stt_engine import Transcript
from vad import CHUNK_SAMPLES as VAD_CHUNK
from voice_pipeline import END_SILENCE_CHUNKS, VoiceSession


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Dictation, "_notes_dir", lambda self: str(tmp_path))
    return tmp_path


@pytest.fixture
def dictation(notes_dir):
    return Dictation()


# ───────────────────────── where the words go ─────────────────────────


def test_taking_notes_writes_a_markdown_file(dictation, notes_dir):
    started = dictation.start("s1", title="video plan")
    assert started["ok"] is True
    dictation.handle("the cold open should be the render failing", session="s1")
    path = dictation.get("s1").path
    text = open(path, encoding="utf-8").read()
    assert "# video plan" in text
    assert "The cold open should be the render failing." in text


def test_stopping_reports_what_was_written(dictation):
    dictation.start("s1")
    dictation.handle("first thought", session="s1")
    dictation.handle("second thought", session="s1")
    summary = dictation.stop("s1")
    assert summary["lines"] == 2
    assert summary["words"] == 4
    assert dictation.active("s1") is False


def test_a_named_file_is_used_when_one_is_given(dictation, tmp_path, monkeypatch):
    monkeypatch.setenv("DEXTER_FS_SCOPE", "machine")
    target = tmp_path / "ideas.md"
    result = dictation.start("s1", target=str(target))
    assert result["ok"] is True
    dictation.handle("an idea", session="s1")
    assert "An idea." in target.read_text(encoding="utf-8")


def test_a_file_outside_the_allowed_folders_is_refused_before_a_word_is_said(
    dictation, tmp_path, monkeypatch
):
    monkeypatch.delenv("DEXTER_FS_SCOPE", raising=False)
    monkeypatch.setenv("DEXTER_FS_ROOTS", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()
    result = dictation.start("s1", target=str(tmp_path / "elsewhere" / "x.md"))
    assert result["ok"] is False
    assert "outside the folders" in result["error"]
    assert dictation.active("s1") is False


def test_the_clipboard_sink_collects_everything_said(dictation, monkeypatch):
    import desktop_control as dc

    written = []
    monkeypatch.setattr(dc, "_set_clipboard", lambda text: written.append(text) or True)
    dictation.start("s1", target="the clipboard")
    dictation.handle("one", session="s1")
    dictation.handle("two", session="s1")
    assert written[-1] == "One.\nTwo."


def test_an_app_sink_types_into_that_window(dictation, monkeypatch):
    import desktop_control as dc

    typed = []
    monkeypatch.setattr(dc, "find_windows", lambda name: [dc.Win(hwnd=1, title="Untitled - Notepad",
                                                                pid=5, process="notepad.exe")])
    monkeypatch.setattr(dc, "type_text",
                        lambda text, **kwargs: typed.append((text, kwargs.get("window"))) or {})
    assert dictation.start("s1", target="notepad")["ok"] is True
    dictation.handle("into notepad it goes", session="s1")
    assert typed == [("Into notepad it goes.\n", "notepad")]


def test_an_app_that_is_not_open_says_so_rather_than_losing_the_note(dictation, monkeypatch):
    import desktop_control as dc

    monkeypatch.setattr(dc, "find_windows", lambda name: [])
    result = dictation.start("s1", target="buzzedit")
    assert result["ok"] is False
    assert "buzzedit" in result["error"].lower()


@pytest.mark.parametrize("spoken,sink", [
    ("notepad", "notepad"),
    ("in notepad", "notepad"),
    ("sticky notes", "sticky"),
    ("notion", "notion"),
    ("the clipboard", "clipboard"),
    ("", "notes"),
    ("my notes", "notes"),
])
def test_the_sink_is_read_from_how_it_was_said(dictation, spoken, sink):
    assert dictation.parse_target(spoken)[0] == sink


def test_a_path_is_a_file_and_an_app_name_is_a_window(dictation):
    assert dictation.parse_target(r"C:\notes\plan.md") == ("file", r"C:\notes\plan.md")
    assert dictation.parse_target("buzzedit") == ("window", "buzzedit")


# ───────────────────────── how it is spoken ─────────────────────────


@pytest.mark.parametrize("said,written", [
    ("the render finished", "The render finished."),
    ("the render finished comma finally", "The render finished, finally."),
    ("is it done question mark", "Is it done?"),
    ("new paragraph", ""),
    ("bullet", "-"),
    ("full stop", "."),
])
def test_spoken_punctuation_becomes_punctuation(dictation, said, written):
    assert dictation.format_line(said) == written


def test_nothing_but_filler_is_not_a_line(dictation):
    assert dictation.format_line("   ") is None
    assert dictation.format_line(",") is None


def test_scratch_that_removes_the_last_line(dictation, notes_dir):
    dictation.start("s1")
    dictation.handle("keep this", session="s1")
    dictation.handle("drop this", session="s1")
    verdict = dictation.handle("scratch that", session="s1")
    assert verdict["action"] == "scratched"
    assert [line for line in dictation.get("s1").lines] == ["Keep this."]
    text = open(dictation.get("s1").path, encoding="utf-8").read()
    assert "Drop this." not in text


@pytest.mark.parametrize("phrase", [
    "stop taking notes", "stop dictation", "that's it", "ok stop", "finish the note",
    "dexter stop taking notes",
])
def test_the_phrases_that_end_it(dictation, phrase):
    dictation.start("s1")
    assert dictation.handle(phrase, session="s1")["action"] == "ended"
    assert dictation.active("s1") is False


def test_pausing_and_resuming(dictation):
    dictation.start("s1")
    assert dictation.handle("pause the notes", session="s1")["action"] == "paused"
    assert dictation.handle("this should not be written", session="s1")["action"] == "ignored"
    assert dictation.handle("resume notes", session="s1")["action"] == "resumed"
    assert dictation.handle("but this should", session="s1")["action"] == "written"
    assert dictation.get("s1").lines == ["But this should."]


def test_two_sessions_do_not_share_a_note(dictation):
    dictation.start("popup", title="popup note")
    dictation.start("full", title="full note")
    dictation.handle("said in the pop-up", session="popup")
    assert dictation.get("full").lines == []
    assert dictation.get("popup").lines == ["Said in the pop-up."]


# ───────────────────────── the voice pipeline hands over ─────────────────────────


class FakeWake:
    def available(self):
        return True

    @property
    def model_name(self):
        return "fake"

    def reset(self):
        pass

    def process(self, frame):
        return False


class FakeVad:
    def available(self):
        return True

    def reset(self):
        pass

    def is_speech(self, chunk):
        return float(np.abs(chunk.astype(np.float32)).mean()) > 1000.0


class FakeStt:
    def __init__(self, text):
        self.text = text

    def available(self):
        return True

    def ready(self):
        return True

    @property
    def model_name(self):
        return "fake"

    def transcribe_detailed(self, audio):
        return Transcript(self.text, confidence=0.9)

    def transcribe(self, audio):
        return self.text

    def quick_text(self, audio):
        return self.text


def _say(session, text, chunks=16):
    speech = np.full(chunks * VAD_CHUNK, 10000, dtype=np.int16)
    silence = np.zeros((END_SILENCE_CHUNKS + 2) * VAD_CHUNK, dtype=np.int16)
    events = session.feed(speech)
    events += session.feed(silence)
    return events


@pytest.fixture
def live_dictation(monkeypatch, tmp_path):
    """The real singleton, writing into a temp folder."""
    monkeypatch.setattr(Dictation, "_notes_dir", lambda self: str(tmp_path))
    instance = Dictation()
    monkeypatch.setattr(voice_pipeline, "dictation", instance)
    monkeypatch.setattr(dictation_module, "dictation", instance)
    return instance


def test_while_dictating_nothing_reaches_the_chat(live_dictation, monkeypatch):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    live_dictation.start("test")
    session = VoiceSession(session="test", wake=FakeWake(), vad=FakeVad(),
                           stt=FakeStt("the thumbnail needs more contrast"),
                           time_fn=lambda: 10.0, barge_in=True, echo_guard_s=0.0)
    events = _say(session, "x")
    kinds = [event["type"] for event in events]
    assert "transcript" not in kinds, "a dictated sentence is not a turn"
    assert "dictation" in kinds
    assert live_dictation.get("test").lines == ["The thumbnail needs more contrast."]


def test_in_name_mode_dictation_does_not_need_the_name(live_dictation, monkeypatch):
    """A person dictating does not say "Dexter" before every sentence, and in the
    default wake mode the name gate would have thrown the whole note away."""
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "name")
    live_dictation.start("test")
    session = VoiceSession(session="test", wake=FakeWake(), vad=FakeVad(),
                           stt=FakeStt("no name in this sentence at all"),
                           time_fn=lambda: 10.0, barge_in=True, echo_guard_s=0.0)
    _say(session, "x")
    assert live_dictation.get("test").lines == ["No name in this sentence at all."]


def test_saying_stop_while_dictating_ends_the_note_rather_than_the_speech(
    live_dictation, monkeypatch
):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    live_dictation.start("test")
    session = VoiceSession(session="test", wake=FakeWake(), vad=FakeVad(),
                           stt=FakeStt("stop taking notes"), time_fn=lambda: 10.0,
                           barge_in=True, echo_guard_s=0.0)
    events = _say(session, "x")
    assert [e for e in events if e["type"] == "dictation"][0]["state"] == "ended"
    assert live_dictation.active("test") is False


def test_with_dictation_off_the_same_sentence_is_a_turn_again(live_dictation, monkeypatch):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    session = VoiceSession(session="test", wake=FakeWake(), vad=FakeVad(),
                           stt=FakeStt("what is on my agenda"), time_fn=lambda: 10.0,
                           barge_in=True, echo_guard_s=0.0)
    events = _say(session, "x")
    assert any(event["type"] == "transcript" for event in events)
