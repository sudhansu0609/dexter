"""
Voice-pipeline tests (Phase 4).

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
from vad import CHUNK_SAMPLES as VAD_CHUNK
from voice_pipeline import BARGE_CHUNKS, END_SILENCE_CHUNKS, VoiceSession
from wakeword import FRAME_SAMPLES as WAKE_FRAME

WAKE_LEVEL = 30000  # loud sentinel the fake wake word fires on
SPEECH_LEVEL = 10000
SILENCE_LEVEL = 0


# ───────────────────────── fakes ─────────────────────────


class FakeWake:
    def available(self):
        return True

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
    def __init__(self, text="turn on the lights"):
        self.text = text
        self.calls = 0

    def available(self):
        return True

    @property
    def model_name(self):
        return "fake"

    def transcribe(self, audio):
        self.calls += 1
        return self.text


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


# ───────────────────────── tests ─────────────────────────


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
    assert "wake" in types(events)
    assert "speech_start" in types(events)

    vs.feed(speech(6))  # the actual command
    tail = vs.feed(silence(END_SILENCE_CHUNKS + 2))

    transcripts = [e for e in tail if e["type"] == "transcript"]
    assert len(transcripts) == 1
    assert transcripts[0]["text"] == "open the calendar"
    assert stt.calls == 1


def test_followup_window_captures_without_wake_word():
    clock = Clock()
    stt = FakeStt("what time is it")
    vs = make_session(stt=stt, clock=clock)

    # Dexter spoke and just finished -> follow-up window opens.
    vs.set_speaking(True)
    vs.set_speaking(False)

    events = vs.feed(speech(4))  # user replies with no wake word
    assert "speech_start" in types(events)

    tail = vs.feed(silence(END_SILENCE_CHUNKS + 2))
    transcripts = [e for e in tail if e["type"] == "transcript"]
    assert len(transcripts) == 1
    assert transcripts[0]["text"] == "what time is it"


def test_barge_in_during_speech_interrupts_and_cancels():
    cancellation.clear("barge")
    vs = make_session(session="barge")
    vs.set_speaking(True)

    events = vs.feed(speech(BARGE_CHUNKS + 2))
    assert "interrupt" in types(events)
    assert cancellation.is_cancelled("barge")


def test_barge_in_disabled_ignores_own_tts_echo():
    """Default half-duplex: mic input while Dexter speaks is neither an interrupt
    nor a captured command, so its own echoed output can't drive a reply loop."""
    cancellation.clear("echo")
    vs = make_session(session="echo", barge_in=False)
    vs.set_speaking(True)

    events = vs.feed(speech(BARGE_CHUNKS + 10))  # "Dexter's own voice" over TTS
    events += vs.feed(silence(END_SILENCE_CHUNKS + 2))

    assert "interrupt" not in types(events)
    assert "transcript" not in types(events)
    assert not cancellation.is_cancelled("echo")


def test_echo_guard_blocks_followup_until_elapsed():
    """The reverb tail right after Dexter stops speaking is ignored; a genuine
    follow-up once the guard elapses is still captured."""
    clock = Clock()
    stt = FakeStt("answering my own echo")
    vs = make_session(stt=stt, clock=clock, echo_guard_s=0.8)

    vs.set_speaking(True)
    vs.set_speaking(False)  # echo guard now runs until t = 0.8

    early = vs.feed(speech(4))  # echo tail arrives immediately
    early += vs.feed(silence(END_SILENCE_CHUNKS + 2))
    assert "speech_start" not in types(early)
    assert "transcript" not in types(early)

    clock.t = 1.0  # past the guard, still inside the follow-up window
    late = vs.feed(speech(4))
    assert "speech_start" in types(late)
    tail = vs.feed(silence(END_SILENCE_CHUNKS + 2))
    assert any(e["type"] == "transcript" for e in tail)


def test_wake_prefix_is_stripped_from_transcript():
    stt = FakeStt("hey jarvis remind me at five")
    vs = make_session(stt=stt)
    vs.feed(wake_block())
    vs.feed(speech(4))
    tail = vs.feed(silence(END_SILENCE_CHUNKS + 2))
    transcripts = [e for e in tail if e["type"] == "transcript"]
    assert transcripts and transcripts[0]["text"] == "remind me at five"


def test_capabilities_shape():
    caps = voice_pipeline.capabilities()
    assert set(caps) >= {"sttReady", "wakeReady", "vadReady", "model"}


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
