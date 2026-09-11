"""
Server-side speech-to-text (voice v10).

faster-whisper (CTranslate2) runs Whisper locally so nothing spoken leaves the
machine. Three things make spoken *commands* land, as opposed to transcribing
a lecture:

  1. The device is chosen at load time, not by a config line that is wrong on
     the next machine: "auto" tries CUDA and proves it with a tiny transcription
     (the CUDA build loads fine and then fails on the first call when the cuBLAS
     DLLs are missing), then settles on CPU int8.
  2. A vocabulary prompt. Whisper spells "Dexter", "buzzcode" and "Sentinel"
     however it likes unless told these words exist.
  3. A hallucination filter. On near-silence Whisper confidently produces
     "Thank you." / "Thanks for watching." / "you"; sent to the router these
     became real turns. Low-confidence, no-speech and stock-phrase outputs are
     dropped here so the pipeline reports "heard nothing" instead.

The model loads lazily on first use so importing this module (and starting the
server) stays cheap and never fails because the optional dependency is absent.
"""

import os
import re
from dataclasses import dataclass
from typing import Optional

import env_boot  # noqa: F401  - loads .env for every entry point
import numpy as np

SAMPLE_RATE = 16000

_MODEL_SIZE = os.getenv("DEXTER_WHISPER_MODEL", "small").strip() or "small"
# The model that checks whether a sentence opens with Dexter's name (the wake
# gate). It runs on every sentence the room says, so it is the cheap one:
# `base` spotted the name as reliably as `small` at a third of the time
# (0.38 s vs 1.16 s per check on this CPU). Same as the main model = one load.
_GATE_MODEL_SIZE = os.getenv("DEXTER_NAME_GATE_MODEL", "base").strip() or _MODEL_SIZE
_DEVICE = os.getenv("DEXTER_WHISPER_DEVICE", "auto").strip().lower() or "auto"
_COMPUTE_TYPE = os.getenv("DEXTER_WHISPER_COMPUTE", "").strip()
_LANGUAGE = os.getenv("DEXTER_WHISPER_LANG", "en").strip() or None

# Names the owner actually says. Whisper treats the prompt as preceding text, so
# it is written as a sentence rather than a word list.
VOCABULARY_PROMPT = os.getenv(
    "DEXTER_WHISPER_PROMPT",
    "Hey Jarvis. Dexter, open Chrome, buzzcode, Buzzcaf Studio, Sentinel, "
    "YouTube, Ollama, LM Studio, VS Code, ComfyUI, remind me, my agenda.",
)

# What Whisper says when it hears nothing. Compared after lower-casing and
# stripping punctuation, and only as a *whole* transcript.
_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thank you very much", "thanks for watching",
    "thank you for watching", "bye", "goodbye", "subscribe", "please subscribe",
    "the end", "so", "um", "uh", "hmm", "mm", "oh", "okay", "ok", "yeah",
    "i'm sorry", "sorry", "music", "applause", "laughter", "silence",
}
_NO_SPEECH_LIMIT = float(os.getenv("DEXTER_STT_NO_SPEECH", "0.6"))
_LOGPROB_FLOOR = float(os.getenv("DEXTER_STT_LOGPROB_FLOOR", "-1.2"))

try:
    from faster_whisper import WhisperModel  # type: ignore

    _IMPORT_OK = True
except Exception as _exc:  # pragma: no cover - depends on optional install
    WhisperModel = None  # type: ignore
    _IMPORT_OK = False
    _IMPORT_ERROR = str(_exc)


@dataclass
class Transcript:
    text: str
    confidence: float = 0.0     # 0..1, rough: exp(avg_logprob) averaged over segments
    no_speech: float = 1.0      # whisper's no-speech probability, worst segment
    dropped: str = ""           # why an output was discarded ("" = kept)


def looks_hallucinated(text: str) -> bool:
    """A whole-transcript stock phrase Whisper emits on silence."""
    bare = re.sub(r"[^a-z' ]+", " ", (text or "").lower())
    bare = re.sub(r"\s+", " ", bare).strip()
    if not bare:
        return True
    return bare in _HALLUCINATIONS


class SttEngine:
    def __init__(self) -> None:
        self._model = None
        self._gate_model = None
        self._gate_failed = False
        self._load_failed = False
        self.device = ""
        self.compute_type = ""

    def available(self) -> bool:
        """True if a transcription could plausibly run (import OK, load not broken)."""
        return _IMPORT_OK and not self._load_failed

    def ready(self) -> bool:
        return self._model is not None

    @property
    def model_name(self) -> str:
        return _MODEL_SIZE

    # ───────────────────────── loading ─────────────────────────

    def _try_load(self, device: str, compute: str):
        model = WhisperModel(_MODEL_SIZE, device=device, compute_type=compute)
        # Prove the device works: the CUDA build constructs happily and only
        # fails on the first inference when cublas/cudnn are missing.
        probe = np.zeros(SAMPLE_RATE // 2, dtype=np.float32)
        segments, _info = model.transcribe(probe, language=_LANGUAGE, beam_size=1, vad_filter=False)
        list(segments)
        return model

    def _ensure_model(self):
        if self._model is not None or self._load_failed or not _IMPORT_OK:
            return self._model
        attempts = []
        if _DEVICE in ("auto", "cuda"):
            attempts.append(("cuda", _COMPUTE_TYPE or "float16"))
        if _DEVICE in ("auto", "cpu"):
            attempts.append(("cpu", _COMPUTE_TYPE or "int8"))
        if not attempts:
            attempts.append((_DEVICE, _COMPUTE_TYPE or "int8"))
        for device, compute in attempts:
            try:
                print(f"[STT] Loading faster-whisper '{_MODEL_SIZE}' ({device}/{compute})...")
                self._model = self._try_load(device, compute)
                self.device, self.compute_type = device, compute
                print(f"[STT] Ready on {device} ({compute}).")
                return self._model
            except Exception as exc:
                print(f"[STT] {device} unavailable: {str(exc)[:160]}")
        self._load_failed = True
        return None

    def _ensure_gate_model(self):
        """The name-gate model: the main one when they are the same size."""
        if _GATE_MODEL_SIZE == _MODEL_SIZE:
            return self._ensure_model()
        if self._gate_model is not None or self._gate_failed:
            return self._gate_model
        if self._ensure_model() is None:
            return None
        try:
            print(f"[STT] Loading name-gate model '{_GATE_MODEL_SIZE}' ({self.device}/{self.compute_type})...")
            self._gate_model = WhisperModel(_GATE_MODEL_SIZE, device=self.device, compute_type=self.compute_type)
        except Exception as exc:
            # Fall back to the main model rather than losing the wake word.
            print(f"[STT] Gate model unavailable ({str(exc)[:120]}); using '{_MODEL_SIZE}' for the gate.")
            self._gate_failed = True
        return self._gate_model or self._model

    @property
    def gate_model_name(self) -> str:
        return _MODEL_SIZE if (self._gate_failed or _GATE_MODEL_SIZE == _MODEL_SIZE) else _GATE_MODEL_SIZE

    def warm_up(self) -> bool:
        """Load off the request path so the first spoken command is not the slow one."""
        ok = self._ensure_model() is not None
        if ok:
            self._ensure_gate_model()
        return ok

    # ───────────────────────── transcription ─────────────────────────

    @staticmethod
    def _to_float32(pcm) -> np.ndarray:
        """Accept int16 PCM (bytes or ndarray) or float32 ndarray -> float32 [-1, 1]."""
        if isinstance(pcm, (bytes, bytearray, memoryview)):
            audio = np.frombuffer(bytes(pcm), dtype=np.int16).astype(np.float32) / 32768.0
        elif isinstance(pcm, np.ndarray):
            if pcm.dtype == np.int16:
                audio = pcm.astype(np.float32) / 32768.0
            else:
                audio = pcm.astype(np.float32)
        else:
            audio = np.asarray(pcm, dtype=np.float32)
        return np.ascontiguousarray(audio)

    def transcribe_detailed(self, pcm) -> Transcript:
        """Transcribe one utterance of 16 kHz mono audio, with the reasons for a miss."""
        model = self._ensure_model()
        if model is None:
            return Transcript("", dropped="stt unavailable")
        audio = self._to_float32(pcm)
        # Under ~0.3s there is not enough signal to bother the model with.
        if audio.size < SAMPLE_RATE * 3 // 10:
            return Transcript("", dropped="too short")
        try:
            segments, _info = model.transcribe(
                audio,
                language=_LANGUAGE,
                beam_size=2,
                best_of=1,
                vad_filter=False,  # the pipeline already gates on silero VAD
                condition_on_previous_text=False,
                initial_prompt=VOCABULARY_PROMPT or None,
                temperature=0.0,
            )
            parts, logprobs, no_speech = [], [], 0.0
            for seg in segments:
                if seg.text.strip():
                    parts.append(seg.text.strip())
                logprobs.append(float(getattr(seg, "avg_logprob", 0.0)))
                no_speech = max(no_speech, float(getattr(seg, "no_speech_prob", 0.0)))
        except Exception as exc:
            print(f"[STT] Transcription failed: {exc}")
            return Transcript("", dropped="error")

        text = " ".join(parts).strip()
        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else -10.0
        confidence = float(np.exp(max(avg_logprob, -10.0)))
        result = Transcript(text, confidence=round(confidence, 3), no_speech=round(no_speech, 3))

        if not text:
            result.dropped = "empty"
        elif looks_hallucinated(text) and (no_speech > 0.3 or avg_logprob < -0.6 or len(text) < 12):
            result.dropped = "hallucination"
        elif no_speech > _NO_SPEECH_LIMIT and avg_logprob < _LOGPROB_FLOOR:
            result.dropped = "low confidence"
        if result.dropped:
            print(f"[STT] Dropped {result.dropped}: {text!r} (conf={confidence:.2f}, no_speech={no_speech:.2f})")
            result.text = ""
        return result

    def quick_text(self, pcm) -> str:
        """
        The fastest honest transcription of a short clip: greedy decoding, no
        filtering. Used to check whether an utterance opens with Dexter's name
        before spending a full pass on it.
        """
        model = self._ensure_gate_model()
        if model is None:
            return ""
        audio = self._to_float32(pcm)
        if audio.size < SAMPLE_RATE * 3 // 10:
            return ""
        try:
            segments, _info = model.transcribe(
                audio,
                language=_LANGUAGE,
                beam_size=1,
                best_of=1,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt=VOCABULARY_PROMPT or None,
                temperature=0.0,
                without_timestamps=True,
            )
            return " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
        except Exception as exc:
            print(f"[STT] Quick transcription failed: {exc}")
            return ""

    def transcribe(self, pcm) -> str:
        """
        Transcribe one utterance to text.

        Returns an empty string when STT is unavailable, the clip is too short,
        or nothing intelligible was heard - callers treat "" as "no command".
        """
        return self.transcribe_detailed(pcm).text


# Shared singleton so the model is loaded at most once per process.
stt_engine = SttEngine()
