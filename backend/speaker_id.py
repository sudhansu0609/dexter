"""
Who is talking (voice v10.2).

Two questions the pipeline could not answer before: "is this the owner?" and
"is this Dexter hearing itself?". Resemblyzer's speaker encoder (a small
LSTM, weights bundled with the package, runs on the CPU torch already here)
turns a few seconds of speech into a 256-d voice print; cosine similarity
against the owner's enrolled print answers the first, and against a print of
Dexter's own Kokoro voice the second.

Enrolment is ~10 s of the owner talking (Settings -> Voice -> Learn my voice).
The profile lives in data/voice_profile.json. Without a profile nothing is
rejected; with one, `DEXTER_SPEAKER_ONLY` (default on) makes Dexter answer the
owner only. Dexter's own print is refreshed at startup for the current voice,
so its echo is rejected even when the echo guard misses.

resemblyzer imports webrtcvad at module level for one helper this module never
calls; that C extension has no wheel for this Python, so it is stubbed.
"""

import json
import os
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import env_boot  # noqa: F401  - loads .env for every entry point
import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_PATH = os.path.join(ROOT_DIR, "data", "voice_profile.json")

SAMPLE_RATE = 16000
EMBED_DIM = 192

STRICT = os.getenv("DEXTER_SPEAKER_ONLY", "1").strip().lower() not in ("0", "false", "no", "off")
# Measured with the real ECAPA-TDNN encoder: same-speaker cosine ~0.83,
# different-speaker cosine ~0.02 (0.008-0.026 across the pairs tried) - a
# clean separation, unlike resemblyzer's overlapping 0.45-0.74 same-speaker /
# 0.46-0.73 different-speaker ranges that let strangers through. 0.45 sits
# well clear of the different-speaker scores while leaving headroom below the
# same-speaker score, and the grey zone + adaptation below close the rest.
THRESHOLD = float(os.getenv("DEXTER_SPEAKER_THRESHOLD", "0.45"))
# Below the threshold by no more than this, the pipeline may still accept the
# sentence when it is plainly addressed to Dexter (it opens with the name, or
# it is a follow-up to something the owner just said). Nobody else in the room
# is calling Dexter by name mid-conversation.
GREY_ZONE = float(os.getenv("DEXTER_SPEAKER_GREY", "0.10"))
# Every confidently-accepted sentence nudges the owner's print toward how the
# owner actually sounds on this mic (an exponential moving average), so the
# scores climb with use instead of staying stuck at the enrolment conditions.
ADAPT_RATE = float(os.getenv("DEXTER_SPEAKER_ADAPT", "0.08"))
# How close a clip needs to be to Dexter's own enrolled voice to be treated
# as an echo rather than the owner speaking.
DEXTER_MATCH = float(os.getenv("DEXTER_SPEAKER_DEXTER", "0.50"))
# Under this much audio a print is too noisy to reject anyone on.
MIN_SECONDS = 0.9
ENROLL_SECONDS = float(os.getenv("DEXTER_ENROLL_SECONDS", "10"))

try:
    import torch  # type: ignore
    from speechbrain.inference.speaker import EncoderClassifier  # type: ignore
    from speechbrain.utils.fetching import LocalStrategy  # type: ignore

    _IMPORT_OK = True
except Exception as _exc:  # pragma: no cover - optional dependency
    torch = None  # type: ignore
    EncoderClassifier = None  # type: ignore
    LocalStrategy = None  # type: ignore
    _IMPORT_OK = False
    _IMPORT_ERROR = str(_exc)


@dataclass
class Verdict:
    owner: Optional[bool]     # True/False when a profile exists, None when unknown
    score: float = 0.0        # similarity to the owner
    dexter_score: float = 0.0  # similarity to Dexter's own voice
    reason: str = ""          # "" (accepted) | "not owner" | "dexter" | "too short" | "no profile"
    #: The print this verdict was made from, so an accepted sentence can teach
    #: the profile without embedding the clip twice.
    embedding: Optional[Any] = field(default=None, repr=False, compare=False)

    @property
    def reject(self) -> bool:
        return self.reason in ("not owner", "dexter")

    @property
    def grey(self) -> bool:
        """Rejected as "not owner", but only just: close enough that being
        addressed by name tips it. Never for Dexter's own echo."""
        return self.reason == "not owner" and self.score >= THRESHOLD - GREY_ZONE


def _to_float(pcm: Any) -> np.ndarray:
    if isinstance(pcm, (bytes, bytearray, memoryview)):
        return np.frombuffer(bytes(pcm), dtype=np.int16).astype(np.float32) / 32768.0
    arr = np.asarray(pcm)
    if arr.dtype == np.int16:
        return arr.astype(np.float32) / 32768.0
    return arr.astype(np.float32)


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
    return float(np.dot(a, b) / denom)


class SpeakerId:
    def __init__(self, path: str = PROFILE_PATH) -> None:
        self.path = path
        self._encoder = None
        self._load_failed = False
        self._lock = threading.Lock()
        self._profile: Dict[str, Any] = {}
        self._load()

    # ───────────────────────── state ─────────────────────────

    def available(self) -> bool:
        return _IMPORT_OK and not self._load_failed

    def enrolled(self) -> bool:
        owner = self._profile.get("owner")
        return isinstance(owner, list) and len(owner) == EMBED_DIM

    def has_dexter(self) -> bool:
        dexter = self._profile.get("dexter")
        return isinstance(dexter, list) and len(dexter) == EMBED_DIM

    def enabled(self) -> bool:
        """Whether judge() can reject anything at all."""
        return self.available() and ((STRICT and self.enrolled()) or self.has_dexter())

    def summary(self) -> Dict[str, Any]:
        return {
            "available": self.available(),
            "enrolled": self.enrolled(),
            "enrolledAt": self._profile.get("enrolled_at"),
            "enrolledSeconds": self._profile.get("owner_seconds"),
            "strict": STRICT,
            "threshold": THRESHOLD,
            "greyZone": GREY_ZONE,
            "adapted": int(self._profile.get("owner_adapted") or 0),
            "dexterVoice": self._profile.get("dexter_voice"),
        }

    # ───────────────────────── persistence ─────────────────────────

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self._profile = data
        except Exception:
            self._profile = {}

    def _save(self) -> None:
        try:
            import jsonstore

            jsonstore.save_json(self.path, self._profile)
        except Exception:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self._profile, handle)

    def forget(self) -> None:
        self._profile.pop("owner", None)
        self._profile.pop("owner_seconds", None)
        self._profile.pop("enrolled_at", None)
        self._profile.pop("owner_adapted", None)
        self._save()

    # ───────────────────────── learning from use ─────────────────────────

    def learn(self, verdict: Verdict) -> bool:
        """Nudge the owner's print toward a sentence that was confidently theirs.

        Only sentences over the threshold on their own teach; a grey-zone
        acceptance (right name, borderline print) never does, so a stranger
        who says "Dexter" cannot slowly become the owner. Saved every few
        updates: this runs after every sentence and the file is not the point.
        """
        if not self.enrolled() or verdict.embedding is None or ADAPT_RATE <= 0:
            return False
        if verdict.reason != "" or verdict.score < THRESHOLD:
            return False
        try:
            current = np.asarray(self._profile["owner"], dtype=np.float32)
            fresh = np.asarray(verdict.embedding, dtype=np.float32)
            if fresh.shape != current.shape:
                return False
            blended = (1.0 - ADAPT_RATE) * current + ADAPT_RATE * fresh
            norm = float(np.linalg.norm(blended)) or 1.0
            self._profile["owner"] = [float(x) for x in (blended / norm)]
            count = int(self._profile.get("owner_adapted") or 0) + 1
            self._profile["owner_adapted"] = count
            if count % 5 == 0:
                self._save()
            return True
        except Exception as exc:
            print(f"[Speaker] Could not adapt the voice print: {exc}")
            return False

    # ───────────────────────── the encoder ─────────────────────────

    def _ensure(self):
        if self._encoder is not None or self._load_failed or not _IMPORT_OK:
            return self._encoder
        with self._lock:
            if self._encoder is not None or self._load_failed:
                return self._encoder
            try:
                t0 = time.time()
                self._encoder = EncoderClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb",
                    savedir=os.path.join(ROOT_DIR, "models", "ecapa"),
                    run_opts={"device": "cpu"},
                    local_strategy=LocalStrategy.COPY_SKIP_CACHE,
                )
                # First call pays torch's thread start-up; take it now.
                warm = torch.from_numpy(np.zeros(SAMPLE_RATE * 2, dtype=np.float32) + 1e-4)
                self._encoder.encode_batch(warm.unsqueeze(0))
                print(f"[Speaker] Voice encoder ready in {time.time() - t0:.1f}s.")
            except Exception as exc:
                print(f"[Speaker] Voice encoder unavailable: {exc}")
                self._load_failed = True
        return self._encoder

    def warm_up(self) -> bool:
        return self._ensure() is not None

    def embed(self, pcm: Any) -> Optional[np.ndarray]:
        encoder = self._ensure()
        if encoder is None:
            return None
        wav = _to_float(pcm)
        if wav.size < int(SAMPLE_RATE * MIN_SECONDS):
            return None
        try:
            emb = encoder.encode_batch(torch.from_numpy(wav).unsqueeze(0)).squeeze().numpy()
            emb = np.asarray(emb, dtype=np.float32)
            norm = float(np.linalg.norm(emb)) or 1e-9
            return emb / norm
        except Exception as exc:
            print(f"[Speaker] Embedding failed: {exc}")
            return None

    # ───────────────────────── enrolment ─────────────────────────

    def enroll_owner(self, pcm: Any, seconds: Optional[float] = None) -> Dict[str, Any]:
        emb = self.embed(pcm)
        if emb is None:
            return {"ok": False, "reason": "could not read that voice"}
        wav = _to_float(pcm)
        self._profile["owner"] = [float(x) for x in emb]
        self._profile["owner_seconds"] = round(seconds if seconds is not None else wav.size / SAMPLE_RATE, 1)
        self._profile["enrolled_at"] = time.strftime("%Y-%m-%d %H:%M")
        self._profile["owner_adapted"] = 0  # a fresh enrolment starts learning again
        self._save()
        print(f"[Speaker] Owner enrolled from {self._profile['owner_seconds']}s of speech.")
        return {"ok": True, "seconds": self._profile["owner_seconds"]}

    def enroll_dexter(self, pcm: Any, voice_key: str = "") -> bool:
        emb = self.embed(pcm)
        if emb is None:
            return False
        self._profile["dexter"] = [float(x) for x in emb]
        self._profile["dexter_voice"] = voice_key
        self._save()
        return True

    # ───────────────────────── the question ─────────────────────────

    def judge(self, pcm: Any) -> Verdict:
        wav = _to_float(pcm)
        if wav.size < int(SAMPLE_RATE * MIN_SECONDS):
            return Verdict(None, reason="too short")
        if not self.enabled():
            return Verdict(None, reason="no profile")
        emb = self.embed(wav)
        if emb is None:
            return Verdict(None, reason="no profile")

        owner_score = _cos(emb, np.asarray(self._profile["owner"], dtype=np.float32)) if self.enrolled() else 0.0
        dexter_score = _cos(emb, np.asarray(self._profile["dexter"], dtype=np.float32)) if self.has_dexter() else 0.0

        # Dexter's own voice, leaking back in through the speakers.
        if self.has_dexter() and dexter_score >= DEXTER_MATCH and dexter_score > owner_score:
            return Verdict(False, owner_score, dexter_score, "dexter", embedding=emb)
        if STRICT and self.enrolled():
            if owner_score >= THRESHOLD:
                return Verdict(True, owner_score, dexter_score, "", embedding=emb)
            return Verdict(False, owner_score, dexter_score, "not owner", embedding=emb)
        return Verdict(None, owner_score, dexter_score, "", embedding=emb)


speaker_id = SpeakerId()
