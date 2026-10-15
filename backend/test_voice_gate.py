"""
Who an utterance was for (v13).

The complaint: "it's listening to some random stuff". The follow-up window was
how - for nine seconds after every reply, any speech at all became a command, and
a borderline speaker verdict inside that window counted as "addressed to me". So a
television, a phone call, or half of somebody else's sentence arrived in the chat.

These tests pin the new rule in both directions, because a gate that is too tight
is its own failure: a real follow-up, a one-word answer, and anything said with
Dexter's name in it must still land.
"""

import numpy as np
import pytest

import voice_pipeline
from stt_engine import Transcript
from vad import CHUNK_SAMPLES as VAD_CHUNK
from voice_pipeline import END_SILENCE_CHUNKS, VoiceSession, accept_utterance


# ───────────────────────── the rule itself ─────────────────────────


@pytest.mark.parametrize("text,reason", [
    ("open chrome", "manual"),          # the mic button: theirs by definition
    ("open chrome", "wake"),            # the wake word fired
    ("open chrome", "name"),            # the name gate let it through
    ("dexter open chrome", "followup"),  # said with the name in it
    ("what's on my agenda", "followup"),  # a real sentence, in the window
    ("yes", "followup"),                # answering a question Dexter asked
    ("go ahead", "followup"),
])
def test_these_are_for_dexter(text, reason):
    accepted, why = accept_utterance(text, reason, confidence=0.8)
    assert accepted, why


@pytest.mark.parametrize("text,reason,confidence", [
    ("hmm", "followup", 0.9),             # one word, not an answer
    ("the", "open", 0.9),
    ("and then he said", "followup", 0.2),  # the recogniser does not believe it
])
def test_these_are_not(text, reason, confidence):
    accepted, why = accept_utterance(text, reason, confidence=confidence)
    assert not accepted and why


def test_a_borderline_voice_needs_more_than_two_words_in_the_window():
    """A grey speaker verdict used to be treated as the owner purely because the
    follow-up window was open (v11.1). Now it needs a real sentence."""
    short = accept_utterance("sounds good", "followup", confidence=0.9, grey=True)
    assert short[0] is False
    real = accept_utterance("open the render folder", "followup", confidence=0.9, grey=True)
    assert real[0] is True


def test_a_borderline_voice_saying_the_name_is_always_heard():
    """The name is the override, and it is read from the *raw* transcript - it has
    been stripped out of the text by the time the gate runs."""
    assert accept_utterance("what's on today", "open", grey=True, by_name=True)[0] is True
    assert accept_utterance("what's on today", "open", grey=True, by_name=False)[0] is False


def test_an_empty_transcript_is_never_a_turn():
    assert accept_utterance("", "manual")[0] is False


# ───────────────────────── in the pipeline ─────────────────────────

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
    def __init__(self, text, confidence=0.9):
        self.text = text
        self.confidence = confidence
        self.calls = 0

    def available(self):
        return True

    def ready(self):
        return True

    @property
    def model_name(self):
        return "fake"

    def transcribe_detailed(self, audio):
        self.calls += 1
        return Transcript(self.text, confidence=self.confidence)

    def transcribe(self, audio):
        return self.transcribe_detailed(audio).text

    def quick_text(self, audio):
        self.calls += 1
        return self.text


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def session(text, clock=None, confidence=0.9, mode="model"):
    return VoiceSession(session="gate", wake=FakeWake(), vad=FakeVad(),
                        stt=FakeStt(text, confidence), time_fn=clock or Clock(),
                        barge_in=True, echo_guard_s=0.0)


def _say(vs, chunks=16):
    events = vs.feed(np.full(chunks * VAD_CHUNK, 10000, dtype=np.int16))
    events += vs.feed(np.zeros((END_SILENCE_CHUNKS + 2) * VAD_CHUNK, dtype=np.int16))
    return events


@pytest.fixture(autouse=True)
def phrase_mode(monkeypatch):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "model")
    monkeypatch.setattr(voice_pipeline, "WAKE_NAME", "dexter")


def test_a_word_overheard_in_the_followup_window_is_not_a_turn():
    vs = session("hmm")
    vs.arm_followup()
    events = _say(vs)
    assert [e["type"] for e in events] == ["listening"], events


def test_a_real_follow_up_still_lands():
    vs = session("what's on my agenda")
    vs.arm_followup()
    events = _say(vs)
    assert any(e["type"] == "transcript" for e in events)


def test_the_window_stops_re_opening_itself_after_two_misses(monkeypatch):
    monkeypatch.setattr(voice_pipeline, "FOLLOWUP_MISSES_MAX", 2)
    clock = Clock()
    vs = session("hmm", clock=clock)
    for _ in range(2):
        vs.arm_followup()
        _say(vs)
        clock.t += 1.0
    vs.arm_followup()
    assert vs._in_followup() is False, "two misses in a row: stop listening for more"


def test_a_real_turn_makes_the_window_trustworthy_again(monkeypatch):
    monkeypatch.setattr(voice_pipeline, "FOLLOWUP_MISSES_MAX", 1)
    clock = Clock()
    vs = session("hmm", clock=clock)
    vs.arm_followup()
    _say(vs)
    vs.arm_followup()
    assert vs._in_followup() is False
    # The owner presses the mic and says something real.
    vs._stt = FakeStt("open the renders folder")
    vs.listen("manual")
    events = _say(vs)
    assert any(e["type"] == "transcript" for e in events)
    vs.arm_followup()
    assert vs._in_followup() is True


def test_pressing_the_mic_always_works_however_short_the_answer():
    vs = session("yes")
    vs.listen("manual")
    events = _say(vs)
    assert any(e["type"] == "transcript" and e["text"] == "yes" for e in events)


def test_stop_is_still_obeyed_from_anywhere():
    """A one-word "stop" in the follow-up window is an instruction to the voice
    layer, and the word-count bar must never silence it."""
    vs = session("stop")
    vs.arm_followup()
    events = _say(vs)
    assert {"type": "command", "name": "stop", "text": "stop"} in events


def test_the_echo_guard_is_long_enough_for_a_speaker_tail():
    assert voice_pipeline.ECHO_GUARD_MS >= 1000, (
        "Dexter's own reverb opened the follow-up window at 800 ms"
    )


def test_the_followup_window_is_not_nine_seconds_any_more():
    assert voice_pipeline.FOLLOWUP_WINDOW <= 6.0


# ───────────────────────── muting by voice (v13.1) ─────────────────────────


@pytest.mark.parametrize("phrase,name", [
    ("mute yourself", "mute"),
    ("mute", "mute"),
    ("be silent", "mute"),
    ("dexter, don't speak", "mute"),
    ("unmute", "unmute"),
    ("you can speak again", "unmute"),
    ("speak to me again", "unmute"),
])
def test_mute_and_unmute_are_control_phrases_not_messages(phrase, name):
    from voice_pipeline import classify_command

    assert classify_command(phrase) == name


def test_stop_still_means_stop_talking_now_not_mute():
    """Two different things: "stop" cuts the reply that is playing, "mute
    yourself" latches the voice off and leaves the mic open."""
    from voice_pipeline import classify_command

    assert classify_command("stop") == "stop"
    assert classify_command("be quiet") == "stop"
    assert classify_command("shut up") == "stop"


def test_a_spoken_mute_never_reaches_the_chat():
    vs = session("mute yourself")
    vs.listen("manual")
    events = _say(vs)
    assert {"type": "command", "name": "mute", "text": "mute yourself"} in events
    assert not any(event["type"] == "transcript" for event in events)
