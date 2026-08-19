import os
import time
import asyncio
import edge_tts
from typing import Any, List, Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(ROOT_DIR, "data", "audio")

# Kokoro model location. Previously hardcoded to a sibling project's absolute
# path, which silently disabled Kokoro on any other machine; now overridable and
# checked against a local copy first.
_DEFAULT_KOKORO_DIR = os.path.join(
    os.path.dirname(ROOT_DIR), "MidnightBuzz", "backend"
)
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

import re

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
    synthesising.

    `prepared=True` skips the scrubber for text the prosody layer already
    normalised, which would otherwise strip its deliberate pacing.
    """
    cleaned = text.strip() if prepared else clean_text_for_speech(text, limit=MAX_SPEECH_CHARS * 4)
    if not cleaned:
        return []

    chunks: list = []
    for sentence in _SENTENCE_SPLIT.split(cleaned):
        sentence = sentence.strip()
        if not sentence:
            continue

        # Only the very first chunk of the whole reply gets the tighter limit.
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
        return "Hello!"
    # 1. Remove stage directions / actions in asterisks e.g. *waves hand*, *smiles*, **bold**
    text = re.sub(r'\*[^*]+\*', '', text)
    # 2. Remove actions in brackets/parentheses e.g. [laughs], (chuckles)
    text = re.sub(r'\[[^\]]+\]', '', text)
    text = re.sub(r'\([^\)]+\)', '', text)
    # 3. Remove URLs
    text = re.sub(r'https?://\S+', '', text)
    # 4. Remove leftover markdown symbols (*, _, #, `, ~, >)
    text = re.sub(r'[*_#`~>]', '', text)
    # 5. Filter out emojis & non-standard symbols while preserving normal punctuation
    text = ''.join(c for c in text if ord(c) < 128 or c.isalnum() or c in ' .,!?\'\"-')
    # 6. Normalize whitespace
    clean = re.sub(r'\s+', ' ', text).strip()
    return clean[:limit] if clean else "I understand."

class DexterVoiceEngine:
    VOICE_PROFILES = {
        "christopher": "en-US-ChristopherNeural",
        "aria": "en-US-AriaNeural",
        "prabhat": "en-IN-PrabhatNeural",
        "swara": "hi-IN-SwaraNeural"
    }

    # Kokoro Expressive Voices mapping
    KOKORO_VOICES = {
        "heart": "af_heart",        # Ultra-natural warm female
        "adam": "am_adam",          # Deep warm male
        "michael": "am_michael",    # Energetic expressive male assistant
        "bella": "af_bella",        # Soft intuitive female
        "george": "bm_george"       # Smooth British male
    }

    def __init__(self):
        os.makedirs(AUDIO_DIR, exist_ok=True)
        self.kokoro_instance = None
        self._style_cache: dict = {}
        self._init_kokoro()

    def _init_kokoro(self):
        if os.path.exists(KOKORO_MODEL_PATH) and os.path.exists(KOKORO_VOICES_PATH):
            try:
                from kokoro_onnx import Kokoro
                self.kokoro_instance = Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
                print("[DexterVoiceEngine] Kokoro ONNX loaded successfully!")
            except Exception as e:
                print(f"[DexterVoiceEngine] Kokoro load warning: {e}")

    def _blended_style(self, base: str, partner: str, weight: float):
        """
        Mix two Kokoro style vectors.

        Emotion is expressed by nudging the timbre rather than swapping voices,
        so Dexter still sounds like one person in a different mood. Weights stay
        low for the same reason; past roughly 0.35 the speaker identity shifts.
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
            print(f"[DexterVoiceEngine] Voice blend failed ({exc}); using base voice.")
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
        """
        Synthesise one chunk.

        `prepared=True` means the caller already ran the text through the
        prosody layer, so the aggressive scrubbing in clean_text_for_speech
        would only undo that work.
        """
        if not self.kokoro_instance:
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
            return file_path
        except Exception as e:
            print(f"[DexterVoiceEngine] Kokoro synthesis error: {e}")
            return None

    async def generate_speech_async(self, text: str, voice_key: str = "christopher", rate: str = "+0%", pitch: str = "+0Hz") -> str:
        # Try Kokoro ONNX first for expressive, emotional human voice
        kokoro_voice = self.KOKORO_VOICES.get(voice_key.lower(), "am_michael")
        kokoro_file = self.generate_speech_kokoro(text, voice_name=kokoro_voice)
        if kokoro_file and os.path.exists(kokoro_file):
            return kokoro_file

        # Fallback to Edge-TTS Neural Voice
        voice = self.VOICE_PROFILES.get(voice_key.lower(), "en-US-ChristopherNeural")
        filename = f"speech_{int(time.time() * 1000)}.mp3"
        file_path = os.path.join(AUDIO_DIR, filename)
        
        clean_text = clean_text_for_speech(text)

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
        """
        Decide how a reply should be delivered and split it into chunks.

        Kept separate from synthesis so the streaming endpoint can emit each
        chunk's audio as it is produced instead of waiting for the whole reply.
        """
        from prosody import build_delivery

        delivery = build_delivery(
            text, action=action, tool_ok=tool_ok,
            is_reminder=is_reminder, long_form=long_form, emotion=emotion,
        )
        return delivery, split_sentences(delivery.text, prepared=True)

    def synthesize_chunk(
        self, chunk: str, delivery: Any, voice_key: str = "michael"
    ) -> Optional[str]:
        """Render one planned chunk with the delivery's voice, blend and speed."""
        base_voice = self.KOKORO_VOICES.get(voice_key.lower(), getattr(delivery, "voice", "am_michael"))
        path = self.generate_speech_kokoro(
            chunk,
            voice_name=base_voice,
            speed=getattr(delivery, "speed", 1.0),
            blend_with=getattr(delivery, "blend_with", None),
            blend_weight=getattr(delivery, "blend_weight", 0.0),
            prepared=True,
        )
        # Kokoro missing or failed: keep speech working via the edge-tts path.
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
        """
        Full expressive pipeline for non-streaming callers.

        This is what makes an alarm sound urgent and a greeting sound warm,
        instead of every line coming out at the same pace in the same tone.
        """
        delivery, chunks = self.plan_speech(
            text, action=action, tool_ok=tool_ok,
            is_reminder=is_reminder, long_form=long_form, emotion=emotion,
        )
        paths = [p for p in (self.synthesize_chunk(c, delivery, voice_key) for c in chunks) if p]
        return paths, delivery

    def generate_speech(self, text: str, voice_key: str = "michael", rate: str = "+0%", pitch: str = "+0Hz") -> Optional[str]:
        """
        Synchronous wrapper.

        Callers run this inside run_in_threadpool, so there is no event loop on
        this thread and asyncio.run is safe - no executor gymnastics needed.
        Kokoro is fully synchronous and skips asyncio entirely.
        """
        kokoro_voice = self.KOKORO_VOICES.get(voice_key.lower(), "am_michael")
        kokoro_file = self.generate_speech_kokoro(text, voice_name=kokoro_voice)
        if kokoro_file and os.path.exists(kokoro_file):
            return kokoro_file

        try:
            return asyncio.run(self.generate_speech_async(text, voice_key, rate, pitch))
        except RuntimeError:
            # Already inside a loop - fall back to a dedicated thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run, self.generate_speech_async(text, voice_key, rate, pitch)
                ).result()
        except Exception as exc:
            print(f"[DexterVoiceEngine] Speech generation failed: {exc}")
            return None
