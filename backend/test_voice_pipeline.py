"""
Voice-pipeline tests (voice v10).

The three detectors (wake word, VAD, STT) are the only non-deterministic part of
the pipeline, so they are replaced with fakes keyed off the PCM content. That
lets the whole wake -> record -> silence -> transcribe -> barge-in state machine
be exercised with no models, no audio hardware, and a fake clock.

Run:  pytest backend/test_voice_pipeline.py -v
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cancellation
import speaker_id as speaker_mod
import voice_pipeline
from stt_engine import Transcript, looks_hallucinated
from vad import CHUNK_SAMPLES as VAD_CHUNK
from voice_pipeline import (
    BARGE_CHUNKS,
    BARGE_VERIFY_CHUNKS,
    END_SILENCE_CHUNKS,
    NO_SPEECH_CHUNKS,
    VoiceSession,
    classify_command,
    strip_wake_word,
)
from wakeword import FRAME_SAMPLES as WAKE_FRAME

WAKE_LEVEL = 30000  # loud sentinel the fake wake word fires on
SPEECH_LEVEL = 10000
SILENCE_LEVEL = 0


# ───────────────────────── fakes ─────────────────────────


class FakeWake:
    def available(self):
        return True

    @property
    def model_name(self):
        return "fake"

    def reset(self):
        pass

    def process(self, frame):
        return bool(frame.size) and int(abs(frame[0])) >= 25000


class FakeVad:
    """Speech if the chunk is loud; silence if it is near zero."""

    def available(self):
        return True

    def reset(self):
        pass

    def is_speech(self, chunk):
        return float(np.abs(chunk.astype(np.float32)).mean()) > 1000.0


class FakeStt:
    def __init__(self, text="turn on the lights", confidence=0.9):
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


# ───────────────────────── audio helpers ─────────────────────────


def _block(level, n_samples):
    return np.full(n_samples, level, dtype=np.int16)


def wake_block():
    return _block(WAKE_LEVEL, WAKE_FRAME)


def speech(n_chunks):
    return _block(SPEECH_LEVEL, n_chunks * VAD_CHUNK)


def silence(n_chunks):
    return _block(SILENCE_LEVEL, n_chunks * VAD_CHUNK)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def phrase_model_mode(monkeypatch):
    """The wake-model tests below were written for openWakeWord; the default
    since v10.1 is the name mode, which its own tests switch on explicitly."""
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "model")
    monkeypatch.setattr(voice_pipeline, "WAKE_NAME", "dexter")


def make_session(session="test", stt=None, clock=None, barge_in=True, echo_guard_s=0.0):
    # Tests default to barge_in=True and no echo guard so the wake/record/barge
    # mechanics stay deterministic; the anti-echo defaults are covered explicitly
    # in test_barge_in_disabled_* / test_echo_guard_* below.
    return VoiceSession(
        session=session,
        wake=FakeWake(),
        vad=FakeVad(),
        stt=stt or FakeStt(),
        time_fn=clock or Clock(),
        barge_in=barge_in,
        echo_guard_s=echo_guard_s,
    )


def types(events):
    return [e["type"] for e in events]


def say(vs, n_chunks=16):
    """Speak then fall silent; returns every event from the whole utterance."""
    events = vs.feed(speech(n_chunks))
    events += vs.feed(silence(END_SILENCE_CHUNKS + 2))
    return events


# ───────────────────────── wake / capture ─────────────────────────


def test_ambient_speech_without_wake_is_ignored():
    vs = make_session()
    events = []
    for _ in range(10):
        events += vs.feed(speech(4))
    assert events == []  # no wake word, no follow-up window -> nothing happens


def test_wake_then_utterance_produces_one_transcript():
    stt = FakeStt("open the calendar")
    vs = make_session(stt=stt)

    events = vs.feed(wake_block())
    assert events == [{"type": "listening", "reason": "wake"}]

    tail = say(vs)
    transcripts = [e for e in tail if e["type"] == "transcript"]
    assert len(transcripts) == 1
    assert transcripts[0]["text"] == "open the calendar"
    assert transcripts[0]["after"] == "wake"
    assert transcripts[0]["confidence"] == 0.9
    assert stt.calls == 1


def test_mic_off_ignores_everything_until_turned_back_on():
    """"I switched the mic off and Dexter still heard me." Muted, the wake word
    and the command that follows produce nothing at all; unmuted, it works
    again. This is the one gate that has to be immune to how many windows are
    open and which one holds the mic."""
    stt = FakeStt("open the calendar")
    vs = make_session(stt=stt)

    vs.set_muted(True)
    assert vs.muted is True
    assert vs.feed(wake_block()) == []       # the wake word is not even looked at
    assert say(vs) == []                      # nor the command after it
    assert stt.calls == 0                     # STT never ran

    vs.set_muted(False)
    assert vs.feed(wake_block()) == [{"type": "listening", "reason": "wake"}]
    transcripts = [e for e in say(vs) if e["type"] == "transcript"]
    assert len(transcripts) == 1 and transcripts[0]["text"] == "open the calendar"


def test_muting_mid_capture_drops_the_half_heard_command():
    """Muting while a command is being recorded throws the buffer away, so the
    tail is not transcribed the moment the mic comes back."""
    stt = FakeStt("secret half sentence")
    vs = make_session(stt=stt)

    vs.feed(wake_block())          # capture is now open
    vs.feed(speech(4))             # ...and half a command is in the buffer
    vs.set_muted(True)
    vs.set_muted(False)
    # Falling silent must not flush the pre-mute audio as a transcript.
    assert [e for e in vs.feed(silence(END_SILENCE_CHUNKS + 2)) if e["type"] == "transcript"] == []
    assert stt.calls == 0


def test_wake_word_alone_is_not_a_message():
    """'Hey Jarvis' then a pause: the tail of the wake word is speech to the
    VAD, and Whisper hears 'Jarvis'. Neither may reach the chat."""
    clock = Clock()
    stt = FakeStt("Jarvis.")
    vs = make_session(stt=stt, clock=clock)
    vs.feed(wake_block())

    # A pause right after the wake word does not end the capture...
    assert vs.feed(silence(END_SILENCE_CHUNKS + 2)) == []
    # ...but nobody speaking for a few seconds does, and it is "nothing".
    tail = vs.feed(silence(NO_SPEECH_CHUNKS))
    assert tail == [{"type": "heard_nothing", "reason": "silence", "after": "wake"}]

    # They can now just say it, no second wake word needed.
    stt.text = "what's next"
    events = say(vs)
    assert "listening" in types(events)
    assert any(e["type"] == "transcript" and e["text"] == "what's next" for e in events)


def test_wake_then_pause_then_command_is_one_capture():
    stt = FakeStt("Jarvis, open chrome")
    vs = make_session(stt=stt)
    vs.feed(wake_block())
    vs.feed(silence(END_SILENCE_CHUNKS))  # thinking for a moment
    events = say(vs)
    assert [e for e in events if e["type"] == "transcript"][0]["text"] == "open chrome"
    assert stt.calls == 1


def test_true_silence_after_wake_never_calls_stt():
    stt = FakeStt("should not be called")
    vs = VoiceSession(session="t", wake=FakeWake(), vad=FakeVad(), stt=stt, time_fn=Clock(),
                      barge_in=True, echo_guard_s=0.0)
    # A wake block that the VAD does not count as speech (quiet trigger).
    vs._begin_recording("wake")
    tail = vs.feed(silence(NO_SPEECH_CHUNKS + 2))
    assert tail == [{"type": "heard_nothing", "reason": "silence", "after": "wake"}]
    assert stt.calls == 0


def test_unclear_speech_reports_heard_nothing():
    stt = FakeStt("")  # the STT dropped it as a hallucination / low confidence
    vs = make_session(stt=stt)
    vs.feed(wake_block())
    tail = say(vs)
    assert [e for e in tail if e["type"] == "heard_nothing"] == [
        {"type": "heard_nothing", "reason": "unclear", "after": "wake"}
    ]
    assert "transcript" not in types(tail)


def test_followup_window_captures_without_wake_word():
    clock = Clock()
    stt = FakeStt("what time is it")
    vs = make_session(stt=stt, clock=clock)

    vs.set_speaking(True)
    vs.set_speaking(False)  # Dexter finished -> follow-up window opens

    events = say(vs)
    assert events[0] == {"type": "listening", "reason": "followup"}
    transcripts = [e for e in events if e["type"] == "transcript"]
    assert transcripts and transcripts[0]["text"] == "what time is it"


def test_followup_window_expires():
    clock = Clock()
    vs = make_session(clock=clock)
    vs.set_speaking(True)
    vs.set_speaking(False)
    clock.t = voice_pipeline.FOLLOWUP_WINDOW + 1
    assert say(vs) == []


def test_arm_followup_without_speech_output():
    """Voice output muted: the reply arrived silently, the client arms the window itself."""
    stt = FakeStt("and close it")
    vs = make_session(stt=stt)
    vs.arm_followup()
    events = say(vs)
    assert any(e["type"] == "transcript" and e["text"] == "and close it" for e in events)


# ───────────────────────── push-to-talk ─────────────────────────


def test_listen_starts_capture_without_wake_word():
    stt = FakeStt("play some music")
    vs = make_session(stt=stt)
    assert vs.listen() == [{"type": "listening", "reason": "manual"}]
    assert vs.state == "recording"
    events = say(vs)
    assert any(e["type"] == "transcript" and e["text"] == "play some music" and e["after"] == "manual" for e in events)


def test_listen_while_speaking_interrupts_and_cancels():
    cancellation.clear("ptt")
    vs = make_session(session="ptt", barge_in=False)
    vs.set_speaking(True)
    events = vs.listen()
    assert types(events) == ["interrupt", "listening"]
    assert cancellation.is_cancelled("ptt")
    assert not vs.speaking
    assert vs.state == "recording"


def test_listen_is_idempotent_while_recording():
    vs = make_session()
    vs.listen()
    assert vs.listen() == []


def test_manual_capture_waits_longer_for_speech():
    vs = make_session()
    vs.listen()
    # The wake-word timeout would already have fired; push-to-talk is patient.
    assert vs.feed(silence(NO_SPEECH_CHUNKS + 2)) == []
    tail = vs.feed(silence(voice_pipeline.NO_SPEECH_MANUAL_CHUNKS))
    assert tail == [{"type": "heard_nothing", "reason": "silence", "after": "manual"}]


def test_stop_drops_capture_and_cuts_reply():
    cancellation.clear("stopper")
    vs = make_session(session="stopper", barge_in=False)
    vs.listen()
    assert vs.stop() == []
    assert vs.state == "idle"

    vs.set_speaking(True)
    assert vs.stop() == [{"type": "interrupt"}]
    assert cancellation.is_cancelled("stopper")


# ───────────────────────── control phrases ─────────────────────────


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("stop", "stop"),
        ("Stop.", "stop"),
        ("okay stop", "stop"),
        ("stop talking", "stop"),
        ("shut up", "stop"),
        ("be quiet please", "stop"),
        ("that's enough", "stop"),
        ("cancel", "cancel"),
        ("cancel that", "cancel"),
        ("never mind", "cancel"),
        ("nevermind.", "cancel"),
        ("forget it", "cancel"),
        ("Stop. Stop.", "stop"),
        ("stop stop", "stop"),
        ("Wait wait", "stop"),
        ("Hold on, stop", "stop"),
        ("Dexter stop", "stop"),
        ("Dexter, stop", "stop"),
        ("Dexter, please stop", "stop"),
        ("Dexter wait", "stop"),
        ("Dexter pause", "stop"),
        ("Dexter shut up", "stop"),
        ("Dexter cancel", "cancel"),
        ("cancel that cancel that", "cancel"),
        ("stay", "stop"),
        ("stay!", "stop"),
        ("shh", "stop"),
        ("hey stop", "stop"),
        ("hold it", "stop"),
        ("hold up", "stop"),
        ("stop the timer", None),
        ("cancel my 3pm meeting", None),
        ("never mind the invoice, open chrome", None),
        ("", None),
    ],
)
def test_classify_command(phrase, expected):
    assert classify_command(phrase) == expected


def test_is_interrupt_phrase_extended():
    assert voice_pipeline.is_interrupt_phrase("stay") == "stop"
    assert voice_pipeline.is_interrupt_phrase("shh") == "stop"
    assert voice_pipeline.is_interrupt_phrase("Dexter please stop talking right now") == "stop"
    assert voice_pipeline.is_interrupt_phrase("Dexter hey please stop talking for a second") == "stop"
    assert voice_pipeline.is_interrupt_phrase("Dexter please cancel that right now") == "cancel"


def test_stop_phrase_is_a_command_not_a_message():
    cancellation.clear("cmd")
    stt = FakeStt("stop")
    vs = make_session(session="cmd", stt=stt)
    vs.arm_followup()
    events = say(vs)
    assert "transcript" not in types(events)
    assert {"type": "command", "name": "stop", "text": "stop"} in events
    assert cancellation.is_cancelled("cmd")


def test_cancel_phrase_closes_followup_window():
    stt = FakeStt("never mind")
    vs = make_session(stt=stt)
    vs.arm_followup()
    events = say(vs)
    assert any(e["type"] == "command" and e["name"] == "cancel" for e in events)
    # The window is consumed: more speech is ambient again.
    assert say(vs) == []


# ───────────────────────── barge-in / echo ─────────────────────────


class _FakeJudge:
    """Stand-in speaker_id for barge tests: enabled, with a fixed verdict."""

    def __init__(self, owner, reason="", enabled=True):
        self._owner, self._reason, self._enabled = owner, reason, enabled

    def enabled(self):
        return self._enabled

    def judge(self, audio):
        return speaker_mod.Verdict(
            self._owner, 0.9 if self._owner else 0.1, 0.2, self._reason
        )

    def summary(self):
        return {}


def test_owner_barge_in_interrupts_and_cancels(monkeypatch):
    # v14.1: barge is owner-gated. The owner talking over Dexter, once ~1 s is
    # buffered and ECAPA confirms it is them, stops Dexter and becomes a command.
    cancellation.clear("barge")
    monkeypatch.setattr(voice_pipeline, "speaker_id", _FakeJudge(owner=True))
    vs = make_session(session="barge")
    vs.set_speaking(True)

    # BARGE_CHUNKS to start the capture, then BARGE_VERIFY_CHUNKS more before the
    # speaker check runs (record_chunks only counts once capture has begun).
    events = vs.feed(speech(BARGE_CHUNKS + BARGE_VERIFY_CHUNKS + 2))
    assert "interrupt" in types(events)
    assert {"type": "listening", "reason": "followup"} in events
    assert cancellation.is_cancelled("barge")


def test_barge_in_ignores_non_owner_and_own_echo_over_tts(monkeypatch):
    # The safety guarantee: speech over Dexter that ECAPA says is NOT the owner
    # (a stranger, or Dexter's own speaker echo) never stops it - no interrupt,
    # no cancel, Dexter keeps talking.
    cancellation.clear("nonowner")
    monkeypatch.setattr(voice_pipeline, "speaker_id", _FakeJudge(owner=False, reason="not owner"))
    vs = make_session(session="nonowner")
    vs.set_speaking(True)

    events = vs.feed(speech(BARGE_CHUNKS + BARGE_VERIFY_CHUNKS + 4))
    assert "interrupt" not in types(events)
    assert not cancellation.is_cancelled("nonowner")
    assert vs.speaking  # Dexter kept talking


def test_barge_in_disabled_ignores_own_tts_echo():
    """Default half-duplex: mic input while Dexter speaks is neither an interrupt
    nor a captured command, so its own echoed output can't drive a reply loop."""
    cancellation.clear("echo")
    vs = make_session(session="echo", barge_in=False)
    vs.set_speaking(True)

    events = vs.feed(speech(BARGE_CHUNKS + 10))  # "Dexter's own voice" over TTS
    events += vs.feed(silence(END_SILENCE_CHUNKS + 2))

    assert events == []
    assert not cancellation.is_cancelled("echo")


def test_echo_guard_blocks_followup_until_elapsed():
    clock = Clock()
    stt = FakeStt("answering my own echo")
    vs = make_session(stt=stt, clock=clock, echo_guard_s=0.8)

    vs.set_speaking(True)
    vs.set_speaking(False)  # echo guard now runs until t = 0.8

    early = say(vs)  # echo tail arrives immediately
    assert early == []

    clock.t = 1.0  # past the guard, still inside the follow-up window
    late = say(vs)
    assert "listening" in types(late)
    assert any(e["type"] == "transcript" for e in late)


# ───────────────────────── wake-word stripping ─────────────────────────


@pytest.mark.parametrize(
    "heard, expected",
    [
        ("hey jarvis remind me at five", "remind me at five"),
        ("Jarvis, open chrome", "open chrome"),
        ("Hey Dexter! what's my agenda", "what's my agenda"),
        ("open chrome, jarvis", "open chrome"),
        ("okay dexter, play music", "play music"),
        ("what's the weather", "what's the weather"),
        ("jarvis", ""),  # nothing else was said - that is not a message
        ("Hey Jarvis.", ""),
        # What Whisper writes when the first syllable of the wake word is clipped.
        ("Service, open Chrome.", "open Chrome."),
        ("Jarves, what time is it", "what time is it"),
        # ...but never a real word in the middle of a sentence.
        ("Call the service desk", "Call the service desk"),
    ],
)
def test_strip_wake_word(heard, expected):
    assert strip_wake_word(heard) == expected


def test_wake_prefix_is_stripped_from_transcript():
    stt = FakeStt("hey jarvis remind me at five")
    vs = make_session(stt=stt)
    vs.feed(wake_block())
    tail = say(vs)
    transcripts = [e for e in tail if e["type"] == "transcript"]
    assert transcripts and transcripts[0]["text"] == "remind me at five"


# ───────────────────────── STT hygiene ─────────────────────────


@pytest.mark.parametrize(
    "text, hallucinated",
    [
        ("Thank you.", True),
        ("thanks for watching", True),
        ("you", True),
        ("", True),
        ("Open the calendar.", False),
        ("thank you for the invoice", False),
    ],
)
def test_looks_hallucinated(text, hallucinated):
    assert looks_hallucinated(text) is hallucinated


def test_capabilities_shape():
    caps = voice_pipeline.capabilities()
    assert set(caps) >= {"sttReady", "wakeReady", "vadReady", "model", "wakeWord", "bargeIn"}


# ───────────────────────── cancellation short-circuits fallback ─────────────────────────


def test_cancelled_response_skips_provider_fallback(monkeypatch):
    from hybrid_llm import HybridLLMRouter, LLMResponse, llm_router

    # This one is *about* `chat()`, so it takes the real one back off the
    # conftest guard that otherwise keeps every test away from a live model.
    monkeypatch.setattr(llm_router, "chat", HybridLLMRouter.chat.__get__(llm_router))
    monkeypatch.setattr(llm_router, "_provider_chain", lambda *a, **k: ["lmstudio", "ollama"])
    calls = {"n": 0}

    def fake_chat_one(prov, *a, **k):
        calls["n"] += 1
        return LLMResponse(provider=prov, cancelled=True)

    monkeypatch.setattr(llm_router, "_chat_one", fake_chat_one)

    resp = llm_router.chat([{"role": "user", "content": "hi"}])
    assert resp.cancelled
    assert calls["n"] == 1  # no fallback attempt on the second provider


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ───────────────────────── the socket hub (two windows, one mic) ─────────────────────────


def make_hub():
    return voice_pipeline.VoiceHub(session_factory=lambda sid: make_session(session=sid))


def test_first_window_gets_the_mic_and_the_second_waits():
    hub = make_hub()
    assert hub.join("s", "full") is True
    assert hub.join("s", "popup") is False
    assert hub.is_active("s", "full") and not hub.is_active("s", "popup")
    assert hub.connections() == 2


def test_claim_moves_the_mic_and_names_the_loser():
    hub = make_hub()
    hub.join("s", "full")
    hub.join("s", "popup")
    assert hub.claim("s", "popup") == "full"
    assert hub.is_active("s", "popup")
    assert hub.claim("s", "popup") is None  # already mine: nobody to tell


def test_leaving_active_window_promotes_the_other():
    hub = make_hub()
    hub.join("s", "full")
    hub.join("s", "popup")
    assert hub.leave("s", "full") == "popup"
    assert hub.is_active("s", "popup")
    assert hub.leave("s", "popup") is None
    assert hub.connections() == 0


def test_leaving_standby_window_changes_nothing():
    hub = make_hub()
    hub.join("s", "full")
    hub.join("s", "popup")
    assert hub.leave("s", "popup") is None
    assert hub.is_active("s", "full")


def test_last_window_leaving_resets_the_capture_without_deadlocking():
    """leave() resets the session while holding the hub lock; a plain Lock
    deadlocked here and froze the socket handler for good."""
    import threading

    hub = make_hub()
    hub.join("s", "full")
    vs = hub.session("s")
    vs.listen()
    assert vs.state == "recording"

    done = threading.Event()
    result = {}

    def leave():
        result["promoted"] = hub.leave("s", "full")
        done.set()

    threading.Thread(target=leave, daemon=True).start()
    assert done.wait(2.0), "hub.leave() deadlocked"
    assert result["promoted"] is None
    assert vs.state == "idle"


def test_sessions_are_independent():
    hub = make_hub()
    hub.join("a", "w1")
    hub.join("b", "w2")
    assert hub.is_active("a", "w1") and hub.is_active("b", "w2")
    assert hub.session("a") is not hub.session("b")


# ───────────────────────── "Dexter" as the wake word (name mode) ─────────────────────────


class NameStt(FakeStt):
    """Returns what the whole clip 'says'; the gate sees the same words."""

    def quick_text(self, audio):
        self.gate_calls = getattr(self, "gate_calls", 0) + 1
        return self.text


@pytest.fixture
def name_mode(monkeypatch):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "name")
    monkeypatch.setattr(voice_pipeline, "WAKE_NAME", "dexter")


@pytest.mark.parametrize(
    "text, addressed",
    [
        ("Dexter, open Chrome.", True),
        ("dexter open chrome", True),
        ("Hey Dexter, what's next?", True),
        ("Dextor, play music", True),      # Whisper's spelling
        ("Dexter's", True),
        ("Dexter", True),
        ("I was thinking about lunch.", False),
        ("The dexterity test", False),
        ("Ask Dexter later", False),       # not at the start
        ("", False),
    ],
)
def test_starts_with_name(text, addressed):
    assert voice_pipeline.starts_with_name(text, "dexter") is addressed


def test_name_mode_ambient_speech_is_dropped_after_the_gate(name_mode):
    stt = NameStt("I was thinking about lunch.")
    vs = make_session(stt=stt)
    assert say(vs) == []
    assert stt.gate_calls == 1       # the opening was checked...
    assert stt.calls == 0            # ...and nothing more was transcribed


def test_name_mode_command_in_one_breath(name_mode):
    stt = NameStt("Dexter, open Chrome.")
    vs = make_session(stt=stt)
    events = say(vs)
    assert events[0] == {"type": "listening", "reason": "name"}
    assert events[1]["type"] == "transcript"
    assert events[1]["text"] == "open Chrome."
    assert events[1]["after"] == "name"


def test_name_mode_name_alone_opens_a_followup_window(name_mode):
    stt = NameStt("Dexter.")
    vs = make_session(stt=stt)
    events = say(vs)
    assert events == [
        {"type": "listening", "reason": "name"},
        {"type": "heard_nothing", "reason": "silence", "after": "name"},
    ]
    stt.text = "what's on today"
    events = say(vs)
    assert events[0] == {"type": "listening", "reason": "followup"}
    assert any(e["type"] == "transcript" and e["text"] == "what's on today" for e in events)


def test_name_mode_stop_phrase(name_mode):
    cancellation.clear("namestop")
    stt = NameStt("Dexter, stop.")
    vs = make_session(session="namestop", stt=stt)
    events = say(vs)
    assert any(e["type"] == "command" and e["name"] == "stop" for e in events)
    assert cancellation.is_cancelled("namestop")


def test_name_mode_ignores_the_phrase_model(name_mode):
    stt = NameStt("open the calendar")
    vs = make_session(stt=stt)
    assert vs.feed(wake_block()) == []  # openWakeWord is not consulted


def test_both_mode_answers_to_either(monkeypatch):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "both")
    stt = NameStt("open the calendar")
    vs = make_session(stt=stt)
    assert vs.feed(wake_block()) == [{"type": "listening", "reason": "wake"}]
    tail = say(vs)
    assert any(e["type"] == "transcript" for e in tail)

    stt.text = "Dexter, what time is it"
    events = say(vs)
    assert events[0] == {"type": "listening", "reason": "name"}


def test_name_mode_still_ignores_the_mic_while_dexter_speaks(name_mode):
    stt = NameStt("Dexter, open Chrome.")
    vs = make_session(stt=stt, barge_in=False)
    vs.set_speaking(True)
    assert say(vs) == []
    assert stt.gate_calls if hasattr(stt, "gate_calls") else 0 == 0


def test_name_mode_noise_blip_is_silent(name_mode):
    stt = NameStt("Dexter")
    vs = make_session(stt=stt)
    vs._begin_recording("name")  # a blip the VAD took for speech onset
    assert vs.feed(silence(NO_SPEECH_CHUNKS + 2)) == []
    assert stt.calls == 0


def test_capabilities_name_the_wake_word_for_the_mode(monkeypatch):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "name")
    monkeypatch.setattr(voice_pipeline, "WAKE_NAME", "dexter")
    assert voice_pipeline.capabilities()["wakeWord"] == "dexter"
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "model")
    assert voice_pipeline.capabilities()["wakeWord"] == voice_pipeline.WakeWordDetector().model_name


def test_capture_does_not_double_the_block_it_started_in(name_mode):
    """The block in which speech begins is already in the pre-roll; appending
    it again doubled the first 128 ms and Whisper heard 'Hey, hey Dexter'."""

    class RecordingStt(NameStt):
        def transcribe_detailed(self, audio):
            self.audio = audio
            return super().transcribe_detailed(audio)

    stt = RecordingStt("Dexter, hi.")
    vs = make_session(stt=stt)
    # One block that is silence then speech: the onset (two speech chunks)
    # lands inside it, so capture starts mid-block.
    block = np.concatenate([silence(1), speech(2)])
    vs.feed(block)
    assert vs.state == "recording"
    vs.feed(speech(15))
    vs.feed(silence(END_SILENCE_CHUNKS + 2))
    audio = stt.audio
    # The pre-roll (which holds the first block, once) plus the speech block.
    # The utterance is finalized *inside* the silence block, before that block
    # would be appended, and by then only silence is missing. With the bug the
    # first block was counted twice: 21 chunks instead of 18.
    assert audio.size == (3 + 15) * VAD_CHUNK


@pytest.mark.parametrize("text", ["Hey, hey Dexter, remind me", "Okay, um, Dexter what's up", "so Dexter, play music"])
def test_name_after_a_stumble(text):
    assert voice_pipeline.starts_with_name(text, "dexter")
    assert strip_wake_word(text) in ("remind me", "what's up", "play music")


# ───────── interrupting a reply mid-sentence (v13.8) ─────────
#
# "I am not able to interrupt it mid-sentence, shall be able to do that."
#
# The machinery was all there - "stop" has cut a reply since v10 - but the microphone was
# ignored *entirely* while Dexter spoke, so the word never reached it. Full barge-in was
# the only alternative and it is off by default for a good reason: it fires on any sound,
# including Dexter's own voice coming back through the speakers, which is the echo loop
# that cost an evening in v10.
#
# So the mic now stays open while Dexter talks, and what it hears may be **one thing
# only**: a control phrase. Anything else from that window is dropped unheard. The echo
# cannot become an instruction by construction rather than by a threshold.


def test_saying_stop_while_dexter_talks_cuts_the_reply():
    cancellation.clear("cut")
    vs = make_session(session="cut", stt=FakeStt(text="stop"), barge_in=False)
    vs.set_speaking(True)

    events = vs.feed(speech(BARGE_CHUNKS + 4))
    events += vs.feed(silence(END_SILENCE_CHUNKS + 2))

    assert "interrupt" in types(events), "the reply has to actually stop"
    assert {"type": "command", "name": "stop", "text": "stop"} in events
    assert cancellation.is_cancelled("cut")


def test_quiet_and_never_mind_work_the_same_way():
    for word, name in (
        ("be quiet", "stop"),
        ("never mind", "cancel"),
        ("wait", "stop"),
        ("hold on", "stop"),
        ("pause", "stop"),
        ("hang on", "stop"),
        ("Stop. Stop.", "stop"),
        ("stop stop", "stop"),
        ("Wait wait", "stop"),
        ("Hold on, stop", "stop"),
        ("Dexter stop", "stop"),
        ("Dexter, please stop", "stop"),
    ):
        cancellation.clear(word)
        vs = make_session(session=word, stt=FakeStt(text=word), barge_in=False)
        vs.set_speaking(True)
        events = vs.feed(speech(BARGE_CHUNKS + 4)) + vs.feed(silence(END_SILENCE_CHUNKS + 2))
        assert any(e["type"] == "command" and e["name"] == name for e in events), word
        if name == "stop":
            assert "interrupt" in types(events), word
            assert cancellation.is_cancelled(word)


def test_interrupt_bypasses_speaker_id_rejection(monkeypatch):
    """When Dexter is speaking, mixed speaker audio must not be dropped before
    Whisper transcribes 'stop'."""
    monkeypatch.setattr(voice_pipeline.speaker_id, "enabled", lambda: True)
    class RejectVerdict:
        reject = True
        grey = False
        score = 0.1
        dexter_score = 0.9
        reason = "dexter"
    monkeypatch.setattr(voice_pipeline.speaker_id, "judge", lambda audio: RejectVerdict())

    cancellation.clear("spk_test")
    vs = make_session(session="spk_test", stt=FakeStt(text="stop"), barge_in=False)
    vs.set_speaking(True)
    events = vs.feed(speech(BARGE_CHUNKS + 4)) + vs.feed(silence(END_SILENCE_CHUNKS + 2))
    assert "interrupt" in types(events)
    assert cancellation.is_cancelled("spk_test")


def test_dexters_own_voice_mid_sentence_is_dropped_unheard():
    """The safety property, and the reason this is not plain barge-in: what comes back
    through the microphone while Dexter speaks is Dexter's own reply, and those words are
    not a control phrase - so they are discarded rather than answered."""
    cancellation.clear("ownvoice")
    vs = make_session(session="ownvoice",
                      stt=FakeStt(text="the weather today is mostly cloudy"),
                      barge_in=False)
    vs.set_speaking(True)

    events = vs.feed(speech(BARGE_CHUNKS + 10))
    events += vs.feed(silence(END_SILENCE_CHUNKS + 2))

    assert events == [], f"nothing may come out of that window: {events}"
    assert not cancellation.is_cancelled("ownvoice")


def test_a_command_mid_sentence_is_not_obeyed_either():
    """Even a real instruction from the owner is dropped while Dexter is mid-reply: it
    cannot be told apart from the echo without a speaker check, and obeying the echo is
    the failure that matters. Say "stop" first, then the instruction."""
    cancellation.clear("midcmd")
    vs = make_session(session="midcmd", stt=FakeStt(text="open chrome for me"),
                      barge_in=False)
    vs.set_speaking(True)
    events = vs.feed(speech(BARGE_CHUNKS + 6)) + vs.feed(silence(END_SILENCE_CHUNKS + 2))
    assert "transcript" not in types(events)


def test_with_interrupt_words_off_the_mic_is_ignored_completely(monkeypatch):
    """The old half-duplex behaviour is still one setting away."""
    monkeypatch.setattr(voice_pipeline, "INTERRUPT_WORDS_ENABLED", False)
    cancellation.clear("deaf")
    vs = make_session(session="deaf", stt=FakeStt(text="stop"), barge_in=False)
    vs.set_speaking(True)
    events = vs.feed(speech(BARGE_CHUNKS + 6)) + vs.feed(silence(END_SILENCE_CHUNKS + 2))
    assert events == []
    assert not cancellation.is_cancelled("deaf")


def test_grey_by_name_is_soft_accepted_by_default(monkeypatch):
    """Default (HARD_ONLY_ME off): a borderline print that says the name is taken
    as the owner speaking off-profile and answered."""
    monkeypatch.setattr(voice_pipeline, "HARD_ONLY_ME", False)
    accepted, _ = voice_pipeline.accept_utterance(
        "close vs code", "name", grey=True, by_name=True)
    assert accepted


def test_hard_only_me_rejects_a_stranger_who_says_the_name(monkeypatch):
    """The fix: with HARD_ONLY_ME on, the name and the wake word no longer rescue
    a grey (not-owner) score - a stranger saying 'Dexter' is ignored."""
    monkeypatch.setattr(voice_pipeline, "HARD_ONLY_ME", True)
    for reason in ("name", "wake"):
        accepted, why = voice_pipeline.accept_utterance(
            "close vs code", reason, grey=True, by_name=True)
        assert not accepted and why == "not owner", (reason, accepted, why)


def test_hard_only_me_still_trusts_the_mic_button_and_followups(monkeypatch):
    """Only-me must not lock the owner out: pressing the mic (reason 'manual') is
    the owner by definition, and a grey score can still ride a real follow-up the
    owner already started."""
    monkeypatch.setattr(voice_pipeline, "HARD_ONLY_ME", True)
    assert voice_pipeline.accept_utterance("do it", "manual", grey=True)[0]
    followup, _ = voice_pipeline.accept_utterance(
        "open the project folder please", "followup", grey=True, confidence=0.9)
    assert followup


def test_barge_is_skipped_without_enrolment(monkeypatch):
    """Owner-gated barge needs an enrolled voice to tell the owner from the echo.
    With no enrolment (speaker_id disabled), talking over Dexter does NOT cut it
    off - it falls back to the safe stop-word path - so an un-enrolled setup can
    never self-interrupt on its own speaker echo."""
    cancellation.clear("noenrol")
    monkeypatch.setattr(voice_pipeline, "speaker_id", _FakeJudge(owner=True, enabled=False))
    vs = make_session(session="noenrol", barge_in=True)
    vs.set_speaking(True)
    events = vs.feed(speech(BARGE_VERIFY_CHUNKS + 4))
    assert "interrupt" not in types(events)
    assert not cancellation.is_cancelled("noenrol")
