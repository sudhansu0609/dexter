import os
import time
import asyncio
import edge_tts
from typing import Optional

AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "audio")

# Kokoro Model Paths
KOKORO_MODEL_PATH = r"B:\youtubeProjects\Buzzcaf Media\MidnightBuzz\backend\kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = r"B:\youtubeProjects\Buzzcaf Media\MidnightBuzz\backend\voices-v1.0.bin"

import re

def clean_text_for_speech(text: str) -> str:
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
    return clean[:400] if clean else "I understand."

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
        self._init_kokoro()

    def _init_kokoro(self):
        if os.path.exists(KOKORO_MODEL_PATH) and os.path.exists(KOKORO_VOICES_PATH):
            try:
                from kokoro_onnx import Kokoro
                self.kokoro_instance = Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
                print("[DexterVoiceEngine] Kokoro ONNX loaded successfully!")
            except Exception as e:
                print(f"[DexterVoiceEngine] Kokoro load warning: {e}")

    def generate_speech_kokoro(self, text: str, voice_name: str = "am_michael") -> Optional[str]:
        if not self.kokoro_instance:
            return None
        try:
            import soundfile as sf
            clean_text = clean_text_for_speech(text)
            
            samples, sample_rate = self.kokoro_instance.create(clean_text, voice=voice_name, speed=1.0, lang="en-us")
            filename = f"speech_kokoro_{int(time.time() * 1000)}.wav"
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

    def generate_speech(self, text: str, voice_key: str = "christopher", rate: str = "+0%", pitch: str = "+0Hz") -> str:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, self.generate_speech_async(text, voice_key, rate, pitch)).result()
            else:
                return loop.run_until_complete(self.generate_speech_async(text, voice_key, rate, pitch))
        except Exception:
            return asyncio.run(self.generate_speech_async(text, voice_key, rate, pitch))
