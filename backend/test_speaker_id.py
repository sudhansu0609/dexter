"""
Who is talking (v10.2): speaker_id with a fake encoder, and the pipeline's use
of it - open mic, enrolment, rejecting other people and Dexter's own echo.

Run:  pytest backend/test_speaker_id.py -v
"""

import os
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import speaker_id as speaker_mod
import voice_pipeline
from speaker_id import SpeakerId
from test_voice_pipeline import END_SILENCE_CHUNKS, FakeVad, FakeWake, NameStt, silence, speech, types
from vad import CHUNK_SAMPLES as VAD_CHUNK

SR = 16000


class FakeEncoder:
    """A 'voice print' that is just the mean level of the clip, so different
    'speakers' are different amplitudes. Mirrors the ECAPA encoder's shape:
    encode_batch(tensor) -> (1, 1, D) tensor that speaker_id squeezes+normalises."""

    def encode_batch(self, wav):
        w = np.asarray(wav).reshape(-1).astype(np.float32)
        level = float(np.abs(w).mean()) * 10.0
        vec = np.array([1.0, level, level * level], dtype=np.float32)
        return torch.from_numpy(vec).reshape(1, 1, -1)


@pytest.fixture
def sid(tmp_path, monkeypatch):
    monkeypatch.setattr(speaker_mod, "_IMPORT_OK", True)
    monkeypatch.setattr(speaker_mod, "EMBED_DIM", 3)  # FakeEncoder yields a 3-dim print
    monkeypatch.setattr(speaker_mod, "STRICT", True)
    monkeypatch.setattr(speaker_mod, "THRESHOLD", 0.995)
    # The fake's cross-"speaker" cosine is ~0.7 (its vectors share a constant
    # term); real ECAPA is ~0.02, so the shipped DEXTER_MATCH=0.50 is fine live
    # but would false-trigger on the fake. Pin it near the fake's self-cosine.
    monkeypatch.setattr(speaker_mod, "DEXTER_MATCH", 0.98)
    s = SpeakerId(path=str(tmp_path / "profile.json"))
    s._encoder = FakeEncoder()
    return s


def voice(level, seconds=2.0):
    return np.full(int(SR * seconds), level, dtype=np.int16)


# ───────────────────────── speaker_id ─────────────────────────


def test_nothing_is_rejected_before_enrolment(sid):
    assert not sid.enabled()
    assert sid.judge(voice(3000)).owner is None


def test_enrol_then_recognise_owner_and_reject_others(sid):
    assert sid.enroll_owner(voice(3000), seconds=10)["ok"]
    assert sid.enrolled() and sid.enabled()
    assert sid.judge(voice(3000)).owner is True
    other = sid.judge(voice(12000))
    assert other.owner is False and other.reason == "not owner"


def test_profile_survives_a_restart(sid):
    sid.enroll_owner(voice(3000), seconds=10)
    again = SpeakerId(path=sid.path)
    assert again.enrolled()
    assert again.summary()["enrolledSeconds"] == 10


def test_forget(sid):
    sid.enroll_owner(voice(3000), seconds=10)
    sid.forget()
    assert not sid.enrolled()
    assert sid.judge(voice(3000)).owner is None


def test_dexters_own_voice_is_rejected_even_without_an_owner(sid, monkeypatch):
    monkeypatch.setattr(speaker_mod, "STRICT", False)
    assert sid.enroll_dexter(voice(12000), voice_key="michael")
    assert sid.enabled()
    echo = sid.judge(voice(12000))
    assert echo.reject and echo.reason == "dexter"
    assert sid.judge(voice(3000)).reject is False


def test_too_short_is_never_judged(sid):
    sid.enroll_owner(voice(3000), seconds=10)
    assert sid.judge(voice(12000, seconds=0.5)).reason == "too short"


# ───────────────────────── in the pipeline ─────────────────────────


class Judge:
    """Stand-in for speaker_id inside the pipeline."""

    def __init__(self, reason=""):
        self.reason = reason
        self.calls = 0

    def enabled(self):
        return True

    def judge(self, audio):
        self.calls += 1
        return speaker_mod.Verdict(self.reason == "", 0.9 if self.reason == "" else 0.4, 0.2, self.reason)

    def summary(self):
        return {}

    def enroll_owner(self, audio, seconds=None):
        self.enrolled_seconds = seconds
        return {"ok": True, "seconds": round(seconds or 0, 1)}


def make(stt, judge, mode="name"):
    vs = voice_pipeline.VoiceSession(session="sid", wake=FakeWake(), vad=FakeVad(), stt=stt,
                                     time_fn=lambda: 0.0, barge_in=False, echo_guard_s=0.0)
    return vs


def say(vs, n=16):
    ev = vs.feed(speech(n))
    ev += vs.feed(silence(END_SILENCE_CHUNKS + 2))
    return ev


@pytest.fixture
def judge(monkeypatch):
    j = Judge()
    monkeypatch.setattr(voice_pipeline, "speaker_id", j)
    return j


def test_owner_passes_the_speaker_check(monkeypatch, judge):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "name")
    vs = make(NameStt("Dexter, open Chrome."), judge)
    ev = say(vs)
    assert any(e["type"] == "transcript" and e["text"] == "open Chrome." for e in ev)
    assert judge.calls == 1


def test_someone_else_is_told_apart(monkeypatch, judge):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "name")
    judge.reason = "not owner"
    vs = make(NameStt("Dexter, open Chrome."), judge)
    ev = say(vs)
    assert "transcript" not in types(ev)
    assert {"type": "not_you", "score": 0.4, "after": "name"} in ev


def test_dexters_echo_is_dropped_silently(monkeypatch, judge):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "name")
    judge.reason = "dexter"
    vs = make(NameStt("Dexter, open Chrome."), judge)
    ev = say(vs)
    assert "transcript" not in types(ev) and "not_you" not in types(ev)


def test_pressing_the_mic_skips_the_check(monkeypatch, judge):
    judge.reason = "not owner"
    vs = make(NameStt("open chrome"), judge)
    vs.listen()
    ev = say(vs)
    assert any(e["type"] == "transcript" for e in ev)
    assert judge.calls == 0


# ───────────────────────── open mic ─────────────────────────


def test_open_mode_takes_everything(monkeypatch, judge):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    stt = NameStt("what's on today")
    vs = make(stt, judge)
    ev = say(vs)
    assert ev[0] == {"type": "listening", "reason": "open"}
    assert any(e["type"] == "transcript" and e["text"] == "what's on today" and e["after"] == "open" for e in ev)
    assert getattr(stt, "gate_calls", 0) == 0  # no name gate


def test_open_mode_strips_the_name_if_said_anyway(monkeypatch, judge):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    vs = make(NameStt("Dexter, what's on today"), judge)
    ev = say(vs)
    assert any(e["type"] == "transcript" and e["text"] == "what's on today" for e in ev)


def test_open_mode_other_people_are_ignored_quietly(monkeypatch, judge):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    judge.reason = "not owner"
    vs = make(NameStt("pass the salt"), judge)
    ev = say(vs)
    assert "transcript" not in types(ev)
    assert {"type": "not_you", "score": 0.4, "after": "open"} in ev


def test_open_mode_stays_quiet_on_noise(monkeypatch, judge):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    vs = make(NameStt(""), judge)
    assert say(vs) == [{"type": "listening", "reason": "open"}]  # a cough: no heard_nothing nagging


# ───────────────────────── enrolment ─────────────────────────


def test_enrolment_collects_speech_and_reports_progress(monkeypatch, judge):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "name")
    vs = make(NameStt("Dexter"), judge)
    assert vs.begin_enroll(3) == [{"type": "enroll_started", "seconds": 3}]
    ev = vs.feed(speech(40))            # 1.28 s of speech
    ev += vs.feed(silence(20))          # silence does not count
    ev += vs.feed(speech(60))           # 1.92 s more -> 3.2 s
    progress = [e for e in ev if e["type"] == "enroll_progress"]
    assert progress and progress[0]["seconds"] == 1.0
    done = [e for e in ev if e["type"] == "enrolled"]
    # It stops the moment the target is reached (94 chunks = 3.0 s), not at the
    # end of the block.
    assert done == [{"type": "enrolled", "ok": True, "seconds": 3.0}]
    assert abs(judge.enrolled_seconds - 3.0) < 0.05
    # Back to normal listening afterwards.
    assert not vs._enrolling


def test_enrolment_gives_up_after_a_while(monkeypatch, judge):
    vs = make(NameStt("Dexter"), judge)
    vs.begin_enroll(10)
    ev = vs.feed(silence(voice_pipeline.ENROLL_TIMEOUT_CHUNKS + 1))
    done = [e for e in ev if e["type"] == "enrolled"]
    assert done and done[0]["ok"] is False


def test_enrolment_can_be_cancelled(monkeypatch, judge):
    vs = make(NameStt("Dexter"), judge)
    vs.begin_enroll(10)
    vs.cancel_enroll()
    assert vs.feed(speech(40)) == []  # name mode: silent capture, no enrol events


# ───────────────────────── v11.1: the grey zone, and learning ─────────────────────────
#
# On the owner's real microphone most sentences scored 0.45-0.74 against a
# 0.75 bar and 141 of 169 were thrown away as "someone else". Two fixes:
# a borderline print is transcribed and accepted when the sentence is plainly
# addressed to Dexter, and every confident match teaches the print.


def test_grey_zone_is_only_just_under_the_bar(sid, monkeypatch):
    monkeypatch.setattr(speaker_mod, "THRESHOLD", 0.95)
    monkeypatch.setattr(speaker_mod, "GREY_ZONE", 0.10)
    sid.enroll_owner(voice(3000), seconds=10)
    assert sid.judge(voice(3000)).owner is True
    near = sid.judge(voice(1500))      # a different level: below the bar, but close
    assert near.reject and near.grey, near
    far = sid.judge(voice(300))
    assert far.reject and not far.grey, far


def test_dexters_echo_is_never_grey(sid, monkeypatch):
    monkeypatch.setattr(speaker_mod, "THRESHOLD", 0.95)
    monkeypatch.setattr(speaker_mod, "GREY_ZONE", 0.5)
    sid.enroll_owner(voice(3000), seconds=10)
    sid.enroll_dexter(voice(2900), voice_key="michael")
    v = sid.judge(voice(2900))
    assert v.reason == "dexter" and not v.grey


def test_learning_moves_the_print_toward_how_the_owner_sounds(sid, monkeypatch):
    monkeypatch.setattr(speaker_mod, "THRESHOLD", 0.90)
    monkeypatch.setattr(speaker_mod, "ADAPT_RATE", 0.5)
    sid.enroll_owner(voice(3000), seconds=10)
    before = sid.judge(voice(2000))
    assert before.owner is True and before.embedding is not None
    assert sid.learn(before) is True
    after = sid.judge(voice(2000))
    assert after.score > before.score
    assert sid.summary()["adapted"] == 1


def test_a_grey_acceptance_never_teaches(sid, monkeypatch):
    monkeypatch.setattr(speaker_mod, "THRESHOLD", 0.95)
    monkeypatch.setattr(speaker_mod, "GREY_ZONE", 0.10)
    sid.enroll_owner(voice(3000), seconds=10)
    grey = sid.judge(voice(1500))
    assert grey.grey
    assert sid.learn(grey) is False
    assert sid.summary()["adapted"] == 0


def test_learning_survives_a_restart(sid, monkeypatch):
    monkeypatch.setattr(speaker_mod, "THRESHOLD", 0.90)
    monkeypatch.setattr(speaker_mod, "ADAPT_RATE", 0.5)
    sid.enroll_owner(voice(3000), seconds=10)
    for _ in range(5):
        sid.learn(sid.judge(voice(2000)))
    again = SpeakerId(path=sid.path)
    again._encoder = FakeEncoder()
    assert again.summary()["adapted"] == 5
    assert again.judge(voice(2000)).score == pytest.approx(sid.judge(voice(2000)).score)


class GreyJudge(Judge):
    """A print just under the bar: rejected on its own, grey by the numbers."""

    def judge(self, audio):
        self.calls += 1
        return speaker_mod.Verdict(False, speaker_mod.THRESHOLD - 0.05, 0.1, "not owner")


@pytest.fixture
def grey(monkeypatch):
    j = GreyJudge("not owner")
    monkeypatch.setattr(voice_pipeline, "speaker_id", j)
    return j


def test_borderline_owner_addressing_dexter_by_name_is_heard(monkeypatch, grey):
    # This asserts the SOFT behaviour (a grey score rescued by the name). The
    # hard "only me" gate deliberately closes that door, so pin it off here.
    monkeypatch.setattr(voice_pipeline, "HARD_ONLY_ME", False)
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "name")
    vs = make(NameStt("Dexter, close VS Code."), grey)
    ev = say(vs)
    assert any(e["type"] == "transcript" and e["text"] == "close VS Code." for e in ev), ev
    assert "not_you" not in types(ev)


def test_borderline_stranger_in_open_mode_is_still_ignored(monkeypatch, grey):
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    vs = make(NameStt("pass the salt"), grey)
    ev = say(vs)
    assert "transcript" not in types(ev)
    assert any(e["type"] == "not_you" for e in ev)


def test_borderline_owner_in_open_mode_saying_the_name_is_heard(monkeypatch, grey):
    monkeypatch.setattr(voice_pipeline, "HARD_ONLY_ME", False)  # soft path (see above)
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    vs = make(NameStt("Dexter, what's on today"), grey)
    ev = say(vs)
    assert any(e["type"] == "transcript" and e["text"] == "what's on today" for e in ev), ev


def test_clearly_someone_else_is_not_transcribed_at_all(monkeypatch, judge):
    """The privacy rule stands: a print well under the bar never reaches Whisper."""
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "open")
    # Pin the bar so the stand-in's 0.4 is clearly below THRESHOLD - GREY_ZONE
    # (the shipped ECAPA default of 0.45 would make 0.4 count as grey).
    monkeypatch.setattr(speaker_mod, "THRESHOLD", 0.60)
    judge.reason = "not owner"  # scores 0.4: under THRESHOLD - GREY_ZONE
    stt = NameStt("pass the salt")
    vs = make(stt, judge)
    ev = say(vs)
    assert "transcript" not in types(ev)
    assert stt.calls == 0


def test_confident_owner_teaches_the_profile(monkeypatch):
    class Learner(Judge):
        def __init__(self):
            super().__init__("")
            self.learned = 0

        def learn(self, verdict):
            self.learned += 1
            return True

    j = Learner()
    monkeypatch.setattr(voice_pipeline, "speaker_id", j)
    monkeypatch.setattr(voice_pipeline, "WAKE_MODE", "name")
    vs = make(NameStt("Dexter, open Chrome."), j)
    say(vs)
    assert j.learned == 1
