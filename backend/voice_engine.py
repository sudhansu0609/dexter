import os
import time
import asyncio
import edge_tts
from typing import Optional

AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "audio")

class DexterVoiceEngine:
    VOICE_PROFILES = {
        "christopher": "en-US-ChristopherNeural",  # Warm, intelligent, expressive male voice
        "aria": "en-US-AriaNeural",                # Warm, cheerful female voice
        "prabhat": "en-IN-PrabhatNeural",          # Fluent Indian English / Hinglish male
        "swara": "hi-IN-SwaraNeural"               # Expressive Hindi female voice
    }

    def __init__(self):
        os.makedirs(AUDIO_DIR, exist_ok=True)

    async def generate_speech_async(self, text: str, voice_key: str = "christopher", rate: str = "+0%", pitch: str = "+0Hz") -> str:
        voice = self.VOICE_PROFILES.get(voice_key.lower(), "en-US-ChristopherNeural")
        filename = f"speech_{int(time.time() * 1000)}.mp3"
        file_path = os.path.join(AUDIO_DIR, filename)
        
        # Clean text
        clean_text = text[:400].strip()
        if not clean_text:
            clean_text = "Hello! I am Dexter."

        communicate = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
        await communicate.save(file_path)
        return file_path

    def generate_speech(self, text: str, voice_key: str = "christopher", rate: str = "+0%", pitch: str = "+0Hz") -> str:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Run in new thread or event loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, self.generate_speech_async(text, voice_key, rate, pitch)).result()
            else:
                return loop.run_until_complete(self.generate_speech_async(text, voice_key, rate, pitch))
        except Exception:
            return asyncio.run(self.generate_speech_async(text, voice_key, rate, pitch))
