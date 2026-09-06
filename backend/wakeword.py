"""
Wake-word detection (Phase 4.3).

openWakeWord (Apache-2.0, fully local, ONNX inference) listens for the activation
phrase in the streamed mic audio so nothing reaches the LLM until the user
addresses Dexter. openWakeWord ships no "Dexter" model yet, so the default is
`hey_jarvis` (the closest pretrained phrase); point DEXTER_WAKEWORD_MODEL at a
custom-trained `dexter.onnx` to use a real "Dexter" word once one is trained.

Like the STT engine this loads lazily and degrades gracefully: `available()` is
False when the package or its models are missing, and the pipeline then relies on
the conversational follow-up window and the browser-recognition fallback.
"""

import os

import env_boot  # noqa: F401  - loads .env for every entry point
from typing import List, Optional

import numpy as np

# openWakeWord consumes 80 ms (1280-sample) frames of 16 kHz int16 audio.
FRAME_SAMPLES = 1280

_MODEL = os.getenv("DEXTER_WAKEWORD_MODEL", "hey_jarvis")
_THRESHOLD = float(os.getenv("DEXTER_WAKEWORD_THRESHOLD", "0.5"))
# Ignore repeat firings for this long after a detection so one "Dexter" does not
# arm the pipeline several times in a row.
_REFRACTORY_FRAMES = int(os.getenv("DEXTER_WAKEWORD_REFRACTORY_FRAMES", "25"))  # ~2s

try:
    import openwakeword  # type: ignore
    from openwakeword.model import Model as _OwwModel  # type: ignore

    _IMPORT_OK = True
except Exception:  # pragma: no cover - depends on optional install
    openwakeword = None  # type: ignore
    _OwwModel = None  # type: ignore
    _IMPORT_OK = False


class WakeWordDetector:
    """Stateful per-connection detector: feed 1280-sample frames, get fire events."""

    def __init__(self) -> None:
        self._model = None
        self._load_failed = False
        self._since_fire = _REFRACTORY_FRAMES  # ready to fire immediately

    def available(self) -> bool:
        return _IMPORT_OK and not self._load_failed

    @property
    def model_name(self) -> str:
        return _MODEL

    def _ensure_model(self):
        if self._model is not None or self._load_failed or not _IMPORT_OK:
            return self._model
        print(f"[WakeWord] Loading openWakeWord '{_MODEL}'...")
        # The common pretrained phrases (hey_jarvis, alexa, ...) ship with the
        # wheel, so construction is tried offline first. Only if that fails is a
        # one-off download attempted for the *specific* model - never the full
        # set, whose optional extras (e.g. timer) can hang on a blocked network.
        try:
            self._model = _OwwModel(wakeword_models=[_MODEL], inference_framework="onnx")
        except Exception as first_exc:
            if os.path.sep in _MODEL or _MODEL.endswith(".onnx"):
                print(f"[WakeWord] Load failed for custom model: {first_exc}")
                self._load_failed = True
                return None
            try:
                openwakeword.utils.download_models([_MODEL])
                self._model = _OwwModel(wakeword_models=[_MODEL], inference_framework="onnx")
            except Exception as exc:
                print(f"[WakeWord] Load failed (offline and download): {exc}")
                self._load_failed = True
                return None
        print(f"[WakeWord] Ready: {list(self._model.models.keys())}")
        return self._model

    def reset(self) -> None:
        if self._model is not None:
            try:
                self._model.reset()
            except Exception:
                pass
        self._since_fire = _REFRACTORY_FRAMES

    def process(self, frame_int16: np.ndarray) -> bool:
        """
        Feed one 1280-sample int16 frame. Returns True on a fresh detection.

        Scores are always fed so the model's internal history stays warm; the
        refractory counter only suppresses the *event*, not the inference.
        """
        model = self._ensure_model()
        if model is None:
            return False
        self._since_fire += 1
        try:
            scores = model.predict(frame_int16)
        except Exception as exc:
            print(f"[WakeWord] predict failed: {exc}")
            return False
        top = max(scores.values()) if scores else 0.0
        if top >= _THRESHOLD and self._since_fire >= _REFRACTORY_FRAMES:
            self._since_fire = 0
            return True
        return False


def available() -> bool:
    return _IMPORT_OK
