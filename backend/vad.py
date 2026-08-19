"""
Voice-activity detection (Phase 4.4).

silero-vad is a tiny neural VAD that runs on the torch already pulled in for the
router/memory embeddings (Decision D1), so it adds no new heavy dependency. It
gates the pipeline two ways: whisper only transcribes segments that actually
contain speech (cheap, and it stops whisper hallucinating on silence), and it is
what notices the user talking over Dexter for barge-in (Phase 4.5).

This wrapper exposes per-chunk speech probability and leaves start/end/hangover
logic to VoiceSession, which needs different timing for command capture vs.
barge-in. The bundled model loads offline; `available()` is False without it.
"""

import os
from typing import Optional

import numpy as np

# silero-vad requires exactly 512-sample (32 ms) chunks of 16 kHz mono audio.
CHUNK_SAMPLES = 512

_THRESHOLD = float(os.getenv("DEXTER_VAD_THRESHOLD", "0.5"))

try:
    import torch  # type: ignore
    from silero_vad import load_silero_vad  # type: ignore

    _IMPORT_OK = True
except Exception:  # pragma: no cover - depends on optional install
    torch = None  # type: ignore
    load_silero_vad = None  # type: ignore
    _IMPORT_OK = False


class VadGate:
    def __init__(self, threshold: float = _THRESHOLD) -> None:
        self.threshold = threshold
        self._model = None
        self._load_failed = False

    def available(self) -> bool:
        return _IMPORT_OK and not self._load_failed

    def _ensure_model(self):
        if self._model is not None or self._load_failed or not _IMPORT_OK:
            return self._model
        try:
            print("[VAD] Loading silero-vad...")
            self._model = load_silero_vad(onnx=False)
            print("[VAD] Ready.")
        except Exception as exc:
            print(f"[VAD] Load failed: {exc}")
            self._load_failed = True
        return self._model

    def reset(self) -> None:
        if self._model is not None:
            try:
                self._model.reset_states()
            except Exception:
                pass

    def speech_prob(self, chunk_int16: np.ndarray) -> float:
        """Probability that a single 512-sample int16 chunk contains speech (0..1)."""
        model = self._ensure_model()
        if model is None:
            return 0.0
        try:
            audio = chunk_int16.astype(np.float32) / 32768.0
            with torch.no_grad():
                return float(model(torch.from_numpy(audio), 16000).item())
        except Exception as exc:
            print(f"[VAD] inference failed: {exc}")
            return 0.0

    def is_speech(self, chunk_int16: np.ndarray) -> bool:
        return self.speech_prob(chunk_int16) >= self.threshold


def available() -> bool:
    return _IMPORT_OK
