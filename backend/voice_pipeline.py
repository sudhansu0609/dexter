"""
Voice pipeline orchestrator (Phase 4).

One VoiceSession lives per WebSocket connection. It turns a raw stream of 16 kHz
mono int16 PCM frames from the renderer into high-level voice events the frontend
acts on: `wake` (Dexter is now listening), `transcript` (a finished command to
send to chat), and `interrupt` (the user talked over Dexter -> stop). It composes
the three detectors - wakeword.py, vad.py, stt_engine.py - and owns all the
timing/state that ties them together:

  IDLE ── wake word, or speech during the follow-up window ──► RECORDING
  RECORDING ── ~600ms trailing silence (or max length) ──► STT ──► `transcript` ──► IDLE

While Dexter is speaking (the client reports it via a control message), the only
thing watched for is barge-in: sustained speech emits `interrupt`, cancels the
in-flight generation for the chat session, and captures the over-talk as the next
command.

The detector calls are the only non-deterministic part, so the whole state
machine is unit-testable by injecting fakes plus a `time_fn` (see
test_voice_pipeline.py). Nothing here imports FastAPI - main.py adapts it to the
socket.
"""

import os
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

import cancellation
from stt_engine import stt_engine
from vad import CHUNK_SAMPLES as VAD_CHUNK
from vad import VadGate
from wakeword import FRAME_SAMPLES as WAKE_FRAME
from wakeword import WakeWordDetector

SAMPLE_RATE = 16000

# All durations are converted to counts of 32 ms VAD chunks, the pipeline's clock.
_MS_PER_CHUNK = 1000 * VAD_CHUNK / SAMPLE_RATE  # 32 ms

END_SILENCE_CHUNKS = round(600 / _MS_PER_CHUNK)       # ~600ms silence ends an utterance
BARGE_CHUNKS = round(250 / _MS_PER_CHUNK)             # ~250ms speech over TTS = barge-in
SPEECH_START_CHUNKS = 2                                # confirm a follow-up utterance began
MAX_UTTERANCE_CHUNKS = round(15000 / _MS_PER_CHUNK)   # hard cap on one command
NO_SPEECH_CHUNKS = round(2500 / _MS_PER_CHUNK)        # armed but silent -> give up
PRE_ROLL_SAMPLES = SAMPLE_RATE * 300 // 1000          # 300ms kept before capture starts

FOLLOWUP_WINDOW = float(os.getenv("DEXTER_FOLLOWUP_WINDOW", "9"))

# Voice barge-in (talking over Dexter to cut it off) needs the mic NOT to pick up
# Dexter's own TTS. Played through speakers, that output leaks back into the mic,
# reads as "speech over TTS", and Dexter interrupts itself and answers its own
# echo in a loop. VAD cannot tell that echo from a real interruption without echo
# cancellation, so barge-in is OFF by default: while Dexter speaks the mic is
# ignored entirely (half-duplex). Enable it only with headphones or reliable AEC.
BARGE_IN_ENABLED = os.getenv("DEXTER_VOICE_BARGE_IN", "0").strip().lower() not in (
    "",
    "0",
    "false",
    "no",
    "off",
)
# Grace period after Dexter stops speaking during which the mic is still ignored,
# so the trailing echo / room reverb of its last words cannot open a follow-up
# capture and get answered as if it were a command.
ECHO_GUARD_MS = float(os.getenv("DEXTER_ECHO_GUARD_MS", "800"))


def capabilities() -> Dict[str, Any]:
    """Snapshot for /status so the UI knows whether the local pipeline is usable."""
    return {
        "sttReady": stt_engine.available(),
        "wakeReady": WakeWordDetector().available(),
        "vadReady": VadGate().available(),
        "model": stt_engine.model_name,
    }


class VoiceSession:
    def __init__(
        self,
        session: str = "default",
        wake: Optional[WakeWordDetector] = None,
        vad: Optional[VadGate] = None,
        stt: Any = stt_engine,
        time_fn: Callable[[], float] = time.monotonic,
        barge_in: Optional[bool] = None,
        echo_guard_s: Optional[float] = None,
    ) -> None:
        self.session = session
        self._wake = wake if wake is not None else WakeWordDetector()
        self._vad = vad if vad is not None else VadGate()
        self._stt = stt
        self._now = time_fn
        self._barge_in = BARGE_IN_ENABLED if barge_in is None else barge_in
        self._echo_guard_s = (ECHO_GUARD_MS / 1000.0) if echo_guard_s is None else echo_guard_s

        self._state = "idle"  # "idle" | "recording"
        self._speaking = False
        self._armed_until = 0.0  # follow-up window: capture without a wake word until then
        self._ignore_until = 0.0  # echo guard: ignore the mic until then (post-TTS reverb)

        self._wake_buf = np.zeros(0, dtype=np.int16)
        self._vad_buf = np.zeros(0, dtype=np.int16)
        self._preroll = np.zeros(0, dtype=np.int16)
        self._record: List[np.ndarray] = []

        self._had_speech = False
        self._silence_run = 0
        self._speech_run = 0
        self._record_chunks = 0
        self._barge_run = 0

    # ───────────────────────── control ─────────────────────────

    def set_speaking(self, speaking: bool) -> None:
        """
        Client tells us whether TTS is playing.

        Turning it off opens the conversational follow-up window (mirrors the
        frontend), so a reply can be answered without repeating the wake word.
        """
        was = self._speaking
        self._speaking = speaking
        if speaking:
            # Dexter took the floor; abandon any half-captured command and watch
            # only for barge-in.
            self._reset_capture()
            self._state = "idle"
            self._barge_run = 0
        elif was and not speaking:
            now = self._now()
            # Swallow the trailing echo/reverb of Dexter's own words before the
            # follow-up window starts listening, so it never answers itself.
            self._ignore_until = now + self._echo_guard_s
            self._armed_until = now + FOLLOWUP_WINDOW

    def reset(self) -> None:
        self._reset_capture()
        self._state = "idle"
        self._armed_until = 0.0
        self._barge_run = 0
        self._wake_buf = np.zeros(0, dtype=np.int16)
        self._vad_buf = np.zeros(0, dtype=np.int16)
        self._wake.reset()
        self._vad.reset()

    # ───────────────────────── audio ─────────────────────────

    def feed(self, pcm: Any) -> List[Dict[str, Any]]:
        """
        Process one incoming block of 16 kHz mono int16 PCM (bytes or ndarray).

        Returns the voice events triggered by this block, in order.
        """
        if isinstance(pcm, (bytes, bytearray, memoryview)):
            block = np.frombuffer(bytes(pcm), dtype=np.int16)
        else:
            block = np.asarray(pcm, dtype=np.int16)
        if block.size == 0:
            return []

        events: List[Dict[str, Any]] = []

        # Keep a short rolling pre-roll so the first word is not clipped when
        # capture starts part-way through this block.
        self._preroll = np.concatenate([self._preroll, block])[-PRE_ROLL_SAMPLES:]

        self._run_wake(block, events)
        self._run_vad(block, events)

        # Whatever we are still recording, keep the raw audio for transcription.
        if self._state == "recording":
            self._record.append(block)

        return events

    # ───────────────────────── wake word ─────────────────────────

    def _run_wake(self, block: np.ndarray, events: List[Dict[str, Any]]) -> None:
        # Wake word only matters when idle, not mid-reply, and outside the
        # follow-up window (inside it, VAD speech-start captures directly).
        if self._speaking or self._state == "recording" or self._in_followup():
            self._wake_buf = np.zeros(0, dtype=np.int16)
            return
        self._wake_buf = np.concatenate([self._wake_buf, block])
        while self._wake_buf.size >= WAKE_FRAME:
            frame = self._wake_buf[:WAKE_FRAME]
            self._wake_buf = self._wake_buf[WAKE_FRAME:]
            if self._wake.process(frame):
                self._begin_recording()
                events.append({"type": "wake"})
                events.append({"type": "speech_start"})
                break

    # ───────────────────────── VAD-driven capture / barge-in ─────────────────────────

    def _run_vad(self, block: np.ndarray, events: List[Dict[str, Any]]) -> None:
        self._vad_buf = np.concatenate([self._vad_buf, block])
        while self._vad_buf.size >= VAD_CHUNK:
            chunk = self._vad_buf[:VAD_CHUNK]
            self._vad_buf = self._vad_buf[VAD_CHUNK:]
            speech = self._vad.is_speech(chunk)

            if self._speaking:
                if not self._barge_in:
                    # Half-duplex (default): ignore the mic entirely while Dexter
                    # speaks so its own output, leaking in through the speakers,
                    # is never heard as a command or a barge-in.
                    continue
                self._barge_run = self._barge_run + 1 if speech else 0
                if self._barge_run >= BARGE_CHUNKS:
                    events.append({"type": "interrupt"})
                    cancellation.request_cancel(self.session)
                    # The over-talk becomes the next command.
                    self._speaking = False
                    self._barge_run = 0
                    self._begin_recording()
                continue

            if self._state == "idle":
                # Inside the follow-up window a confirmed speech onset starts a
                # command with no wake word required - but not during the echo
                # guard, when the mic is still hearing the tail of Dexter's reply.
                if self._in_followup() and not self._in_echo_guard():
                    self._speech_run = self._speech_run + 1 if speech else 0
                    if self._speech_run >= SPEECH_START_CHUNKS:
                        self._begin_recording()
                        events.append({"type": "speech_start"})
                continue

            # state == recording
            self._record_chunks += 1
            if speech:
                self._had_speech = True
                self._silence_run = 0
            else:
                self._silence_run += 1

            done = (
                (self._had_speech and self._silence_run >= END_SILENCE_CHUNKS)
                or self._record_chunks >= MAX_UTTERANCE_CHUNKS
                or (not self._had_speech and self._record_chunks >= NO_SPEECH_CHUNKS)
            )
            if done:
                self._finalize(events)

    # ───────────────────────── helpers ─────────────────────────

    def _in_followup(self) -> bool:
        return self._now() < self._armed_until

    def _in_echo_guard(self) -> bool:
        return self._now() < self._ignore_until

    def _begin_recording(self) -> None:
        self._state = "recording"
        self._record = [self._preroll.copy()]
        self._had_speech = False
        self._silence_run = 0
        self._speech_run = 0
        self._record_chunks = 0
        self._armed_until = 0.0  # a fresh capture consumes the follow-up window
        self._vad.reset()

    def _reset_capture(self) -> None:
        self._record = []
        self._had_speech = False
        self._silence_run = 0
        self._speech_run = 0
        self._record_chunks = 0

    def _finalize(self, events: List[Dict[str, Any]]) -> None:
        audio = np.concatenate(self._record) if self._record else np.zeros(0, dtype=np.int16)
        self._reset_capture()
        self._state = "idle"
        text = ""
        if self._had_speech or audio.size:
            text = self._stt.transcribe(audio)
        text = self._strip_wake_prefix(text)
        if text:
            events.append({"type": "transcript", "text": text})

    @staticmethod
    def _strip_wake_prefix(text: str) -> str:
        """Drop a leading wake phrase the mic caught before the real command."""
        if not text:
            return text
        import re

        return re.sub(
            r"^\s*(hey|ok|okay|hi)?\s*(dexter|jarvis)[,!.:]?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip() or text
