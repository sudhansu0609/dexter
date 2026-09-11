"""
Speech output (voice v10): Kokoro ONNX first, edge-tts as the online fallback.

Kokoro loads lazily (first synthesis, or the startup warm-up) so importing this
module costs nothing and a missing model only means "no local voice", never a
server that refuses to start. Clips are written under data/audio and pruned as
they age; before v10 every spoken sentence was left on disk forever.
"""

import asyncio
import os
import re
import threading
import time
from typing import Any, List, Optional, Tuple

import env_boot  # noqa: F401  - loads .env for every entry point

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(ROOT_DIR, "data", "audio")

# Kokoro model location: KOKORO_DIR, then ./models, then the sibling project
# that already carries the files.
_DEFAULT_KOKORO_DIR = os.path.join(os.path.dirname(ROOT_DIR), "MidnightBuzz", "backend")
KOKORO_DIR = os.getenv("KOKORO_DIR", "")


def _resolve_kokoro(filename: str) -> str:
    for candidate_dir in (KOKORO_DIR, os.path.join(ROOT_DIR, "models"), _DEFAULT_KOKORO_DIR):
        if candidate_dir:
            path = os.path.join(candidate_dir, filename)
            if os.path.exists(path):
                return path
    return os.path.join(_DEFAULT_KOKORO_DIR, filename)


KOKORO_MODEL_PATH = _resolve_kokoro("kokoro-v1.0.onnx")
KOKORO_VOICES_PATH = _resolve_kokoro("voices-v1.0.bin")

# Clips older than this are deleted on the next synthesis.
CLIP_MAX_AGE_S = float(os.getenv("DEXTER_AUDIO_CLIP_MAX_AGE", "900"))

# Which Kokoro voice Dexter speaks with (Settings -> Voice -> Dexter's voice).
DEFAULT_VOICE_KEY = os.getenv("DEXTER_VOICE_KEY", "michael").strip().lower() or "michael"


def default_voice_key() -> str:
    return DEFAULT_VOICE_KEY if DEFAULT_VOICE_KEY in DexterVoiceEngine.KOKORO_VOICES else "michael"

# Longest chunk handed to a synthesiser in one call. Sentence-level streaming
# means this is a safety cap, not the response length limit it used to be.
MAX_SPEECH_CHARS = 800

# Semicolons and colons matter as much as full stops here: tool summaries are
# often a single sentence of "A; B; C", which without this synthesised as one
# long clip and delayed the very first audio by seconds.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:])\s+")

# Synthesis time scales with chunk length, so the opening chunk is kept short to
# get sound playing fast; later chunks can be longer since they are generated
# while earlier audio is still playing.
FIRST_CHUNK_CHARS = 90
CHUNK_CHARS = 240


def _split_long(text: str, limit: int) -> list:
    """Break an over-long run on the most natural boundary available."""
    parts = []
    while len(text) > limit:
        cut = max(text.rfind(", ", 0, limit), text.rfind(" - ", 0, limit))
        if cut < limit // 3:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip(" ,-"))
        text = text[cut:].strip(" ,-")
    if text:
        parts.append(text)
    return parts


def split_sentences(
    text: str,
    max_chars: int = CHUNK_CHARS,
    first_chunk_chars: int = FIRST_CHUNK_CHARS,
    prepared: bool = False,
) -> list:
    """
    Break a reply into speakable chunks.

    Streaming audio per chunk is what drops time-to-first-audio from seconds to
    a fraction of one - the user hears chunk one while chunk two is still
    synthesising. `prepared=True` skips the scrubber for text the prosody layer
    already normalised.
    """
    cleaned = text.strip() if prepared else clean_text_for_speech(text, limit=MAX_SPEECH_CHARS * 4)
    if not cleaned:
        return []

    chunks: list = []
    for sentence in _SENTENCE_SPLIT.split(cleaned):
        sentence = sentence.strip()
        if not sentence:
            continue
        if not chunks and len(sentence) > first_chunk_chars:
            head = _split_long(sentence, first_chunk_chars)[0]
            chunks.append(head)
            sentence = sentence[len(head):].strip(" ,-")
            if not sentence:
                continue
        limit = first_chunk_chars if not chunks else max_chars
        if len(sentence) <= limit:
            chunks.append(sentence)
        else:
            chunks.extend(_split_long(sentence, max_chars))
    return [c for c in chunks if c]


def clean_text_for_speech(text: str, limit: int = MAX_SPEECH_CHARS) -> str:
    if not text:
        return ""
    # Stage directions and markdown emphasis: *waves*, **bold**
    text = re.sub(r"\*[^*]+\*", "", text)
    # Bracketed actions: [laughs], (chuckles)
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\([^\)]+\)", "", text)
    # Code and URLs are unspeakable.
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", "", text)
    # Leftover markdown symbols
    text = re.sub(r"[*_#`~>|]", "", text)
    # Emojis and other non-ASCII symbols, keeping ordinary letters and punctuation
    text = "".join(c for c in text if ord(c) < 128 or c.isalnum() or c in " .,!?'\"-")
    return re.sub(r"\s+", " ", text).strip()[:limit]


class DexterVoiceEngine:
    VOICE_PROFILES = {
        "christopher": "en-US-ChristopherNeural",
        "aria": "en-US-AriaNeural",
        "prabhat": "en-IN-PrabhatNeural",
        "swara": "hi-IN-SwaraNeural",
    }

    KOKORO_VOICES = {
        "heart": "af_heart",        # warm female - Kokoro's most natural
        "bella": "af_bella",        # soft female
        "nova": "af_nova",
        "sarah": "af_sarah",
        "emma": "bf_emma",          # British female
        "adam": "am_adam",          # deep warm male
        "michael": "am_michael",    # the default: expressive male assistant
        "fenrir": "am_fenrir",      # lively male
        "puck": "am_puck",
        "onyx": "am_onyx",          # deep male
        "george": "bm_george",      # British male
        "daniel": "bm_daniel",      # British male
    }

    def __init__(self) -> None:
        os.makedirs(AUDIO_DIR, exist_ok=True)
        self.kokoro_instance = None
        self._kokoro_tried = False
        self._style_cache: dict = {}
        self._lock = threading.Lock()
        self._last_prune = 0.0

    # ───────────────────────── availability ─────────────────────────

    def kokoro_available(self) -> bool:
        return os.path.exists(KOKORO_MODEL_PATH) and os.path.exists(KOKORO_VOICES_PATH)

    def ready(self) -> bool:
        return self.kokoro_instance is not None

    def warm_up(self) -> bool:
        """Load Kokoro and render one short clip so the first reply is not slow."""
        if self._ensure_kokoro() is None:
            return False
        try:
            self.generate_speech_kokoro("Ready.", prepared=True)
        except Exception:
            pass
        return True

    def _ensure_kokoro(self):
        if self.kokoro_instance is not None or self._kokoro_tried:
            return self.kokoro_instance
        with self._lock:
            if self.kokoro_instance is not None or self._kokoro_tried:
                return self.kokoro_instance
            self._kokoro_tried = True
            if not self.kokoro_available():
                print(f"[Voice] Kokoro model not found (looked for {KOKORO_MODEL_PATH}); using edge-tts.")
                return None
            try:
                t0 = time.time()
                from kokoro_onnx import Kokoro

                self.kokoro_instance = Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
                print(f"[Voice] Kokoro ONNX loaded in {time.time() - t0:.1f}s.")
            except Exception as exc:
                print(f"[Voice] Kokoro load failed: {exc}")
        return self.kokoro_instance

    # ───────────────────────── housekeeping ─────────────────────────

    def prune_clips(self, max_age_s: float = CLIP_MAX_AGE_S) -> int:
        """Delete generated clips older than max_age_s. Returns how many went."""
        now = time.time()
        removed = 0
        try:
            for name in os.listdir(AUDIO_DIR):
                if not name.startswith("speech_"):
                    continue
                path = os.path.join(AUDIO_DIR, name)
                try:
                    if now - os.path.getmtime(path) > max_age_s:
                        os.remove(path)
                        removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        self._last_prune = now
        return removed

    def _maybe_prune(self) -> None:
        if time.time() - self._last_prune > 120:
            self.prune_clips()

    # ───────────────────────── synthesis ─────────────────────────

    def _blended_style(self, base: str, partner: str, weight: float):
        """
        Mix two Kokoro style vectors. Emotion nudges the timbre instead of
        swapping voices, so Dexter stays one person in a different mood.
        """
        cache_key = f"{base}|{partner}|{weight:.2f}"
        if cache_key in self._style_cache:
            return self._style_cache[cache_key]
        try:
            import numpy as np

            base_style = self.kokoro_instance.get_voice_style(base)
            partner_style = self.kokoro_instance.get_voice_style(partner)
            mixed = ((1.0 - weight) * base_style + weight * partner_style).astype(np.float32)
            self._style_cache[cache_key] = mixed
            return mixed
        except Exception as exc:
            print(f"[Voice] Voice blend failed ({exc}); using base voice.")
            return base

    def generate_speech_kokoro(
        self,
        text: str,
        voice_name: str = "am_michael",
        speed: float = 1.0,
        blend_with: Optional[str] = None,
        blend_weight: float = 0.0,
        prepared: bool = False,
    ) -> Optional[str]:
        """Synthesise one chunk with Kokoro; None when Kokoro is unavailable."""
        if self._ensure_kokoro() is None:
            return None
        try:
            import soundfile as sf

            clean_text = text if prepared else clean_text_for_speech(text)
            if not clean_text.strip():
                return None
            voice = voice_name
            if blend_with and blend_weight > 0:
                voice = self._blended_style(voice_name, blend_with, blend_weight)
            samples, sample_rate = self.kokoro_instance.create(
                clean_text, voice=voice, speed=speed, lang="en-us"
            )
            filename = f"speech_kokoro_{int(time.time() * 1000000)}.wav"
            file_path = os.path.join(AUDIO_DIR, filename)
            sf.write(file_path, samples, sample_rate)
            self._maybe_prune()
            return file_path
        except Exception as exc:
            print(f"[Voice] Kokoro synthesis error: {exc}")
            return None

    async def generate_speech_async(
        self, text: str, voice_key: str = "michael", rate: str = "+0%", pitch: str = "+0Hz"
    ) -> Optional[str]:
        kokoro_voice = self.KOKORO_VOICES.get(voice_key.lower(), "am_michael")
        kokoro_file = self.generate_speech_kokoro(text, voice_name=kokoro_voice)
        if kokoro_file and os.path.exists(kokoro_file):
            return kokoro_file
        try:
            import edge_tts
        except Exception:
            return None
        voice = self.VOICE_PROFILES.get(voice_key.lower(), "en-US-ChristopherNeural")
        clean_text = clean_text_for_speech(text)
        if not clean_text:
            return None
        file_path = os.path.join(AUDIO_DIR, f"speech_{int(time.time() * 1000)}.mp3")
        communicate = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
        await communicate.save(file_path)
        return file_path

    def plan_speech(
        self,
        text: str,
        action: str = "",
        tool_ok: Optional[bool] = None,
        is_reminder: bool = False,
        long_form: bool = False,
        emotion: Optional[str] = None,
    ) -> Tuple[Any, List[str]]:
        """Decide how a reply is delivered and split it into chunks."""
        from prosody import build_delivery

        delivery = build_delivery(
            text, action=action, tool_ok=tool_ok,
            is_reminder=is_reminder, long_form=long_form, emotion=emotion,
        )
        return delivery, split_sentences(delivery.text, prepared=True)

    def synthesize_chunk(self, chunk: str, delivery: Any, voice_key: str = "michael") -> Optional[str]:
        """Render one planned chunk with the delivery's voice, blend and speed."""
        base_voice = self.KOKORO_VOICES.get((voice_key or "").lower(), getattr(delivery, "voice", "am_michael"))
        partner = getattr(delivery, "blend_with", None)
        if partner and base_voice[:1] in ("a", "b") and base_voice[1:2] == "f":
            # A female base voice keeps its gender: same mood, female partner.
            from prosody import FEMALE_PARTNERS

            partner = FEMALE_PARTNERS.get(partner, partner)
        path = self.generate_speech_kokoro(
            chunk,
            voice_name=base_voice,
            speed=getattr(delivery, "speed", 1.0),
            blend_with=partner,
            blend_weight=getattr(delivery, "blend_weight", 0.0),
            prepared=True,
        )
        return path or self.generate_speech(chunk, voice_key)

    def speak_expressive(
        self,
        text: str,
        action: str = "",
        tool_ok: Optional[bool] = None,
        is_reminder: bool = False,
        long_form: bool = False,
        emotion: Optional[str] = None,
        voice_key: str = "michael",
    ) -> Tuple[List[str], Any]:
        """Full expressive pipeline for non-streaming callers (reminders, check-ins)."""
        delivery, chunks = self.plan_speech(
            text, action=action, tool_ok=tool_ok,
            is_reminder=is_reminder, long_form=long_form, emotion=emotion,
        )
        paths = [p for p in (self.synthesize_chunk(c, delivery, voice_key) for c in chunks) if p]
        return paths, delivery

    def generate_speech(
        self, text: str, voice_key: str = "michael", rate: str = "+0%", pitch: str = "+0Hz"
    ) -> Optional[str]:
        """Synchronous wrapper; safe to call from a worker thread."""
        kokoro_voice = self.KOKORO_VOICES.get(voice_key.lower(), "am_michael")
        kokoro_file = self.generate_speech_kokoro(text, voice_name=kokoro_voice)
        if kokoro_file and os.path.exists(kokoro_file):
            return kokoro_file
        try:
            return asyncio.run(self.generate_speech_async(text, voice_key, rate, pitch))
        except RuntimeError:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run, self.generate_speech_async(text, voice_key, rate, pitch)
                ).result()
        except Exception as exc:
            print(f"[Voice] Speech generation failed: {exc}")
            return None
