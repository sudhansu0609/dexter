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
import voice_pipeline
from stt_engine import Transcript, looks_hallucinated
from vad import CHUNK_SAMPLES as VAD_CHUNK
from voice_pipeline import (
    BARGE_CHUNKS,
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
        ("stop the timer", None),
        ("cancel my 3pm meeting", None),
        ("never mind the invoice, open chrome", None),
        ("", None),
    ],
)
def test_classify_command(phrase, expected):
    assert classify_command(phrase) == expected


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


def test_barge_in_during_speech_interrupts_and_cancels():
    cancellation.clear("barge")
    vs = make_session(session="barge")
    vs.set_speaking(True)

    events = vs.feed(speech(BARGE_CHUNKS + 2))
    assert "interrupt" in types(events)
    assert {"type": "listening", "reason": "followup"} in events
    assert cancellation.is_cancelled("barge")


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
    from hybrid_llm import LLMResponse, llm_router

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
