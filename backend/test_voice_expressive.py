"""Expressive voice (v14): edge-tts primary with per-emotion prosody, Kokoro
fallback. These guard the two behaviours most likely to regress - the emotion
reaching the Delivery as rate/pitch/volume, and edge failing over to Kokoro."""

import os

import prosody
import voice_engine


def test_delivery_carries_per_emotion_edge_prosody():
    """Emotion must move rate/pitch off neutral, or edge-tts sounds flat."""
    neutral = prosody.build_delivery("Okay.", emotion="neutral")
    assert (neutral.rate, neutral.pitch, neutral.volume) == ("+0%", "+0Hz", "+0%")

    upbeat = prosody.build_delivery("Done, that's sorted!", emotion="upbeat")
    assert upbeat.rate != "+0%" and upbeat.pitch != "+0Hz"

    apologetic = prosody.build_delivery("Sorry, that failed.", emotion="apologetic")
    # A slower, lower register than upbeat: the two moods must not collapse.
    assert apologetic.rate != upbeat.rate and apologetic.pitch != upbeat.pitch


def test_synthesize_chunk_prefers_edge(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_engine, "TTS_ENGINE", "auto")
    monkeypatch.setattr(voice_engine, "_edge_cooldown_until", 0.0)

    def fake_edge(text, voice, rate, pitch, volume, path):
        with open(path, "wb") as fh:
            fh.write(b"ID3edge-audio")

    monkeypatch.setattr(voice_engine, "_run_edge_synth", fake_edge)

    eng = voice_engine.DexterVoiceEngine()
    delivery = prosody.build_delivery("Opening Chrome now.", emotion="upbeat")
    path = eng.synthesize_chunk(delivery.text, delivery, "michael")

    assert path and path.endswith(".mp3") and os.path.exists(path)


def test_synthesize_chunk_falls_back_to_kokoro_when_edge_fails(monkeypatch):
    monkeypatch.setattr(voice_engine, "TTS_ENGINE", "auto")
    monkeypatch.setattr(voice_engine, "_edge_cooldown_until", 0.0)

    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(voice_engine, "_run_edge_synth", boom)

    eng = voice_engine.DexterVoiceEngine()
    # Kokoro stands in for the offline path; return a sentinel so we can assert
    # the chunk came back through the fallback rather than edge.
    monkeypatch.setattr(eng, "generate_speech_kokoro", lambda *a, **k: "kokoro-fallback.wav")

    delivery = prosody.build_delivery("Sorry, I could not reach the server.", emotion="apologetic")
    path = eng.synthesize_chunk(delivery.text, delivery, "michael")

    assert path == "kokoro-fallback.wav"


def test_kokoro_only_mode_never_calls_edge(monkeypatch):
    monkeypatch.setattr(voice_engine, "TTS_ENGINE", "kokoro")

    def boom(*_a, **_k):
        raise AssertionError("edge must not run when DEXTER_TTS_ENGINE=kokoro")

    monkeypatch.setattr(voice_engine, "_run_edge_synth", boom)

    eng = voice_engine.DexterVoiceEngine()
    monkeypatch.setattr(eng, "generate_speech_kokoro", lambda *a, **k: "kokoro.wav")
    delivery = prosody.build_delivery("Local only.", emotion="calm")

    assert eng.synthesize_chunk(delivery.text, delivery, "michael") == "kokoro.wav"
