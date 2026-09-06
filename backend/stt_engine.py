"""
Server-side speech-to-text (Phase 4.1).

faster-whisper (CTranslate2) runs Whisper locally at int8 - fine on CPU, better
on GPU - so transcription never leaves the machine, unlike the browser's
webkitSpeechRecognition it replaces. The model is loaded lazily on the first
transcription so importing this module (and starting the server) stays cheap and
never fails just because the optional dependency is absent: `available()` reports
the capability and the pipeline falls back to browser recognition when it is off.
"""

import os

import env_boot  # noqa: F401  - loads .env for every entry point
from typing import Optional

import numpy as np

# Whisper wants 16 kHz mono; the whole voice pipeline standardises on it.
SAMPLE_RATE = 16000

_MODEL_SIZE = os.getenv("DEXTER_WHISPER_MODEL", "base")
_DEVICE = os.getenv("DEXTER_WHISPER_DEVICE", "cpu")
_COMPUTE_TYPE = os.getenv("DEXTER_WHISPER_COMPUTE", "int8")
_LANGUAGE = os.getenv("DEXTER_WHISPER_LANG", "en") or None

try:
    from faster_whisper import WhisperModel  # type: ignore

    _IMPORT_OK = True
except Exception as _exc:  # pragma: no cover - depends on optional install
    WhisperModel = None  # type: ignore
    _IMPORT_OK = False
    _IMPORT_ERROR = str(_exc)


class SttEngine:
    def __init__(self) -> None:
        self._model = None
        self._load_failed = False

    def available(self) -> bool:
        """True if a transcription could plausibly run (import OK, load not broken)."""
        return _IMPORT_OK and not self._load_failed

    @property
    def model_name(self) -> str:
        return _MODEL_SIZE

    def _ensure_model(self):
        if self._model is not None or self._load_failed or not _IMPORT_OK:
            return self._model
        try:
            print(f"[STT] Loading faster-whisper '{_MODEL_SIZE}' ({_DEVICE}/{_COMPUTE_TYPE})...")
            self._model = WhisperModel(_MODEL_SIZE, device=_DEVICE, compute_type=_COMPUTE_TYPE)
            print("[STT] Model ready.")
        except Exception as exc:
            print(f"[STT] Model load failed: {exc}")
            self._load_failed = True
        return self._model

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

    def transcribe(self, pcm) -> str:
        """
        Transcribe one utterance of 16 kHz mono audio to text.

        Returns an empty string when STT is unavailable, the clip is too short,
        or nothing intelligible was heard - callers treat "" as "no command".
        """
        model = self._ensure_model()
        if model is None:
            return ""
        audio = self._to_float32(pcm)
        # Under ~0.25s there is not enough signal to bother the model with.
        if audio.size < SAMPLE_RATE // 4:
            return ""
        try:
            segments, _info = model.transcribe(
                audio,
                language=_LANGUAGE,
                beam_size=1,
                vad_filter=False,  # the pipeline already gates on silero VAD
                condition_on_previous_text=False,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return text
        except Exception as exc:
            print(f"[STT] Transcription failed: {exc}")
            return ""


# Shared singleton so the model is loaded at most once per process.
stt_engine = SttEngine()
