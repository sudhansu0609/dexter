"""
Voice pipeline orchestrator (voice v10).

One VoiceSession lives per chat session. It turns a raw stream of 16 kHz mono
int16 PCM frames from the renderer into high-level voice events the frontend
acts on, composing the three detectors - wakeword.py, vad.py, stt_engine.py -
and owning all the timing/state that ties them together:

  IDLE ── wake word / follow-up speech / push-to-talk ──► RECORDING
  RECORDING ── ~700 ms trailing silence (or max length) ──► STT ──► event ──► IDLE

Events out (each a dict with a "type"):

  listening      capture began: reason = wake | followup | manual
  transcript     a finished command: text, seconds, confidence
  command        a control phrase said on its own: name = stop | cancel (cut the
                 reply) or mute | unmute (latch Dexter's voice off/on, leaving the
                 mic open - v13.1). Never sent to the chat.
  heard_nothing  capture ended with nothing usable: reason = silence | unclear,
                 after = wake | followup | manual
  interrupt      the user talked over Dexter (barge-in, opt-in)
  dictation      while note-taking is on, what was written instead of answered:
                 state = written | ended | paused | resumed | scratched | failed

Controls in (from the socket): set_speaking(on), listen(), stop(),
arm_followup(), reset().

While Dexter is speaking, the mic is ignored by default (half-duplex): played
through speakers, its own output leaks back in, reads as speech, and Dexter
used to interrupt itself and answer its own echo. Barge-in is opt-in for
headphone users. The detector calls are the only non-deterministic part, so
the whole state machine is unit-testable with fakes and a fake clock
(test_voice_pipeline.py). Nothing here imports FastAPI - main.py adapts it to
the socket.
"""

import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import env_boot  # noqa: F401  - loads .env for every entry point
import numpy as np

import cancellation
from dictation import dictation
from speaker_id import speaker_id
from stt_engine import stt_engine
from vad import CHUNK_SAMPLES as VAD_CHUNK
from vad import VadGate
from wakeword import FRAME_SAMPLES as WAKE_FRAME
from wakeword import WakeWordDetector

SAMPLE_RATE = 16000

# All durations are converted to counts of 32 ms VAD chunks, the pipeline's clock.
_MS_PER_CHUNK = 1000 * VAD_CHUNK / SAMPLE_RATE  # 32 ms

END_SILENCE_CHUNKS = round(700 / _MS_PER_CHUNK)       # trailing silence that ends a command
BARGE_CHUNKS = round(150 / _MS_PER_CHUNK)             # speech over TTS = barge-in (lowered from 250ms for snappy interrupt)
# v14.1: owner-gated barge needs ~1 s of the interrupting speech before the ECAPA
# check can tell the owner from Dexter's own speaker echo (speaker_id.MIN_SECONDS
# is 0.9 s). Only then does Dexter actually stop - so it never cuts itself off on
# its own voice, and never stops for someone who is not the owner.
BARGE_VERIFY_CHUNKS = round(1000 / _MS_PER_CHUNK)
SPEECH_START_CHUNKS = 2                                # confirm a follow-up utterance began
MAX_UTTERANCE_CHUNKS = round(20000 / _MS_PER_CHUNK)   # hard cap on one command
NO_SPEECH_CHUNKS = round(4000 / _MS_PER_CHUNK)        # woke, but nobody spoke
NO_SPEECH_MANUAL_CHUNKS = round(6000 / _MS_PER_CHUNK) # pressed the mic, but nobody spoke
# Audio kept from before the capture started. The wake model fires at the end
# of "hey jarvis", so the whole phrase must already be in the buffer: with a
# short pre-roll Whisper heard "...vis, open Chrome" as "Service, open Chrome"
# and nothing stripped it.
PRE_ROLL_SAMPLES = SAMPLE_RATE * 1200 // 1000         # 1.2 s kept before capture starts
# The wake model fires on the tail of "...jarvis", and that tail is speech to
# the VAD. Without this grace a pause after the wake word ended the capture at
# once and "Jarvis" alone went to the chat as a message.
WAKE_TAIL_GRACE_CHUNKS = round(350 / _MS_PER_CHUNK)

FOLLOWUP_WINDOW = float(os.getenv("DEXTER_FOLLOWUP_WINDOW", "6"))

#: Listen for "stop" while Dexter is talking (v13.8). On by default, and safe in a way
#: full barge-in is not: a capture that starts while Dexter speaks may only produce a
#: control phrase, so its own voice coming back through the microphone cannot be read as
#: an instruction. "I am not able to interrupt it mid-sentence" was the report.
INTERRUPT_WORDS_ENABLED = (os.getenv("DEXTER_VOICE_INTERRUPT_WORDS", "1") or "1") \
    .strip().lower() not in ("0", "false", "no", "off")

BARGE_IN_ENABLED = os.getenv("DEXTER_VOICE_BARGE_IN", "0").strip().lower() not in (
    "", "0", "false", "no", "off",
)
# Grace period after Dexter stops speaking during which the mic is still
# ignored, so the reverb tail of its last words cannot open a follow-up capture.
ECHO_GUARD_MS = float(os.getenv("DEXTER_ECHO_GUARD_MS", "600"))

# ── Who an unaddressed sentence was for (v13) ──
#
# "It's listening to some random stuff." The follow-up window is how: for nine
# seconds after every reply, *any* speech onset became a command, and a grey
# speaker verdict inside that window was treated as "addressed to me". So the
# television, a phone call in the room, or half of someone else's sentence
# arrived in the chat as a turn.
#
# A sentence that was not addressed to Dexter by name, by the wake word or by
# the mic button now has to look like something said *to* an assistant: more
# than one word, and a transcript the recognizer stands behind. Short answers
# ("yes", "no", "go ahead") are the exception, because they are the whole point
# of the follow-up window.
MIN_WORDS_UNADDRESSED = int(os.getenv("DEXTER_VOICE_MIN_WORDS", "2") or 2)
MIN_CONFIDENCE_UNADDRESSED = float(os.getenv("DEXTER_VOICE_MIN_CONFIDENCE", "0.45") or 0.45)
#: A borderline speaker verdict inside the follow-up window needs a real
#: sentence, not two words that happened to land in the window.
GREY_FOLLOWUP_WORDS = int(os.getenv("DEXTER_VOICE_GREY_WORDS", "3") or 3)
GREY_FOLLOWUP_CONFIDENCE = float(os.getenv("DEXTER_VOICE_GREY_CONFIDENCE", "0.5") or 0.5)
#: "Only me" taken literally. Off (default): a borderline (grey) voice that says
#: the name or trips the wake word is treated as the owner speaking off-profile
#: and is answered - which also lets a stranger who happens to score in the grey
#: band and says "Dexter" through. On: nothing but a confident owner match may
#: open a turn from cold; a grey score is only honoured as a follow-up the owner
#: already started. This is the switch behind "make it only respond to me".
HARD_ONLY_ME = os.getenv("DEXTER_SPEAKER_HARD", "0").strip().lower() in ("1", "true", "yes", "on")
#: How many empty or rejected captures in a row before Dexter stops re-opening
#: the follow-up window by itself. Two misses means nobody is talking to it.
FOLLOWUP_MISSES_MAX = int(os.getenv("DEXTER_VOICE_FOLLOWUP_MISSES", "2") or 2)

#: Said alone, these are answers to Dexter, so they skip the word-count bar.
_SHORT_REPLIES = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|no|nope|nah|next|again|"
    r"do it|go ahead|please do|not now|later|carry on|continue|go on)\b[.!?]*$",
    re.IGNORECASE,
)

# Said on their own, these are instructions to the voice layer, not messages.
_CMD_PREFIX = (
    r"(?:(?:hey|ok(?:ay)?|hi|hello|please|can you|could you|just|um|uh|so|yo)[,\s]+)*"
    r"(?:(?:dexter|jarvis)[,\s]+)?"
    r"(?:(?:hey|ok(?:ay)?|hi|hello|please|can you|could you|just|um|uh|so|yo)[,\s]+)*"
)

_STOP_UNIT = (
    r"(?:stop(?: (?:it|talking|speaking|that|now|stop|stop stop))?|"
    r"shut up|be quiet|quiet|enough|that'?s enough|hush(?: now)?|"
    r"stay(?:!| here| now)?|shh+|"
    r"wait(?: (?:a second|a sec|a minute|a moment|up|now|wait|wait wait))?|"
    r"hold on(?: (?:a second|a sec|a minute|now))?|"
    r"hold it|hold up|"
    r"pause(?: now)?|"
    r"hang on(?: (?:a second|a sec|a minute|now))?)"
)

_STOP_WORDS = re.compile(
    rf"^(?:{_CMD_PREFIX})?"
    rf"(?:{_STOP_UNIT}[,\s.!?-]+)*{_STOP_UNIT}"
    rf"(?:[,\s]+(?:please|dexter|jarvis|now|already|talking|speaking))?[.!]*$",
    re.IGNORECASE,
)

_CANCEL_UNIT = (
    r"(?:cancel(?: (?:that|it))?|never ?mind|forget it|forget that|scratch that|no thanks|nothing)"
)

_CANCEL_WORDS = re.compile(
    rf"^(?:{_CMD_PREFIX})?"
    rf"(?:{_CANCEL_UNIT}[,\s.!?-]+)*{_CANCEL_UNIT}"
    rf"(?:[,\s]+(?:please|dexter|jarvis|now|already))?[.!]*$",
    re.IGNORECASE,
)
# v13.1: muting Dexter's *voice* without closing the mic. Distinct from "stop",
# which cuts the reply that is playing: these latch, and the pop-up's speaker
# button is the same switch.
_MUTE_WORDS = re.compile(
    r"^(?:(?:ok(?:ay)?|hey|please)[,\s]+)?(?:dexter[,\s]+)?"
    r"(?:mute(?: yourself| your voice)?|be silent|stay (?:quiet|silent)|"
    r"don'?t speak(?: out loud)?|no voice|voice off|stop speaking out loud|"
    r"(?:turn|switch) off your voice)"
    r"(?:[,\s]+(?:please|dexter|now))?[.!]*$",
    re.IGNORECASE,
)
_UNMUTE_WORDS = re.compile(
    r"^(?:(?:ok(?:ay)?|hey|please)[,\s]+)?(?:dexter[,\s]+)?"
    r"(?:unmute(?: yourself| your voice)?|you can (?:speak|talk)(?: again)?|"
    r"speak(?: to me)? again|talk to me again|voice on|"
    r"(?:turn|switch) (?:on|back on) your voice)"
    r"(?:[,\s]+(?:please|dexter|now))?[.!]*$",
    re.IGNORECASE,
)

# "jarvis" as Whisper actually writes it when the first syllable was clipped or
# the speaker was quick: these are only ever stripped at the very start.
_WAKE_NAMES = (
    r"(?:dexter'?s?|dextor|dexta|daxter|dexster|deckster|dexterous|"
    r"jarvis|jarvis's|jarves|jarvish|jarvas|harvis|service|travis)"
)
_WAKE_PREFIX = re.compile(
    r"^\s*(?:(?:hey|ok|okay|hi|yo|a|um|uh|so)[,\s]+){0,2}" + _WAKE_NAMES + r"[,!.:]?\s*",
    re.IGNORECASE,
)
_WAKE_SUFFIX = re.compile(r"[,\s]+(?:dexter|jarvis)[.!?]*\s*$", re.IGNORECASE)

# How Dexter is woken (v10.1):
#   open   - everything said is for Dexter, no name needed, until muted (v10.2).
#            Meant to be paired with speaker_id so "everything" means the owner.
#   name   - say "Dexter". openWakeWord ships no such model, so the recognizer
#            does it: every utterance the VAD hears gets its first couple of
#            seconds transcribed, and only sentences that start with the name
#            go further. No training, nothing to download; costs a short
#            Whisper pass per sentence spoken in the room.
#   model  - openWakeWord's phrase model (DEXTER_WAKEWORD_MODEL, stock
#            "hey jarvis"): a chime the instant the phrase ends, no STT until
#            then, but the phrase cannot be "Dexter" without training a model.
#   both   - either one wakes Dexter.
WAKE_MODE = (os.getenv("DEXTER_WAKE_MODE", "name").strip().lower() or "name")
WAKE_NAME = (os.getenv("DEXTER_WAKE_NAME", "dexter").strip().lower() or "dexter")
# How much of an utterance is checked for the name before the rest is transcribed.
NAME_GATE_SECONDS = float(os.getenv("DEXTER_NAME_GATE_SECONDS", "2.0"))
# Log what the gate rejected (ambient speech). Off by default - privacy.
VOICE_DEBUG = os.getenv("DEXTER_VOICE_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")

# Whisper's spellings of the names we answer to, at the start of a sentence.
_NAME_SPELLINGS: Dict[str, str] = {
    "dexter": r"(?:dexter'?s?|dextor|dexta|daxter|dexster|deckster|dexterous)",
    "jarvis": r"(?:jarvis|jarvis's|jarves|jarvish|jarvas|harvis)",
}


def _name_pattern(name: str) -> "re.Pattern[str]":
    spelled = _NAME_SPELLINGS.get(name, re.escape(name))
    # Up to two lead-in words: "hey Dexter", "hey, um, Dexter", "okay hey Dexter".
    return re.compile(
        r"^\s*(?:(?:hey|ok|okay|hi|yo|a|um|uh|so)[,\s]+){0,2}" + spelled + r"(?:[,!.:?]|\s|$)",
        re.IGNORECASE,
    )


def speaker_id_threshold() -> float:
    """The live bar (Settings can change it while running)."""
    try:
        import speaker_id as _sid

        return float(_sid.THRESHOLD)
    except Exception:
        return 0.0


def starts_with_name(text: str, name: Optional[str] = None) -> bool:
    """True when the utterance is addressed to Dexter ("Dexter, open Chrome")."""
    chosen = (name or WAKE_NAME or "dexter").strip().lower()
    return bool(text) and bool(_name_pattern(chosen).match(text))


def wake_word_label() -> str:
    """What the UI tells people to say."""
    if WAKE_MODE == "model":
        return WakeWordDetector().model_name
    return WAKE_NAME


ENROLL_SPEECH_SECONDS = float(os.getenv("DEXTER_ENROLL_SECONDS", "10"))
ENROLL_TIMEOUT_CHUNKS = round(45000 / _MS_PER_CHUNK)  # give up after 45 s of trying


def classify_command(text: str) -> Optional[str]:
    """'stop' / 'cancel' / 'mute' / 'unmute' when the whole utterance is a control
    phrase for the voice layer rather than a message, else None."""
    bare = (text or "").strip()
    if not bare:
        return None
    if _UNMUTE_WORDS.match(bare):
        return "unmute"
    if _MUTE_WORDS.match(bare):
        return "mute"
    if _STOP_WORDS.match(bare):
        return "stop"
    if _CANCEL_WORDS.match(bare):
        return "cancel"
    return None


def is_interrupt_phrase(text: str) -> Optional[str]:
    """Check whether text spoken while Dexter is speaking conveys interrupt intent."""
    cmd = classify_command(text)
    if cmd:
        return cmd
    bare = (text or "").strip().lower()
    if not bare:
        return None
    stop_match = re.search(r"\b(stop|wait|pause|hold on|hang on|shut up|be quiet|quiet|enough|hush|stay|shh)\b", bare, re.I)
    cancel_match = re.search(r"\b(cancel|never ?mind|forget it|scratch that)\b", bare, re.I)
    if not (stop_match or cancel_match):
        return None
    if re.search(r"\b(timer|meeting|alarm|reminder|event|calendar|task|process|job|download)\b", bare, re.I):
        return None
    words = _words(bare)
    if len(words) <= 10:
        return "cancel" if cancel_match and not stop_match else "stop"
    return None


def _words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9']+", text or "")


def _dict_event(verdict: Dict[str, Any]) -> Dict[str, Any]:
    """The parts of a dictation verdict the UI should see (never the whole note)."""
    return {
        "line": verdict.get("line", ""),
        "words": verdict.get("words", 0),
        "lines": verdict.get("lines", 0),
        "where": verdict.get("where", ""),
        "path": verdict.get("path", ""),
        "error": verdict.get("error", ""),
    }


def addressed_directly(text: str, reason: str) -> bool:
    """Was this capture unambiguously started *by* the owner, for Dexter?"""
    return reason in ("manual", "wake", "name") or starts_with_name(text or "")


def accept_utterance(
    text: str,
    reason: str,
    confidence: float = 0.0,
    grey: bool = False,
    by_name: bool = False,
) -> Tuple[bool, str]:
    """Should this transcript become a turn? `(accepted, why not)`.

    The one place the "was this for me" question is answered, so the rule is
    testable and the same for every way in. `by_name` is whether the *raw*
    transcript opened with Dexter's name, and it has to be passed in: the name is
    stripped out of `text` before this runs, so "Dexter, what's on today" would
    otherwise look like an unaddressed "what's on today" (caught by
    test_borderline_owner_in_open_mode_saying_the_name_is_heard).
    """
    body = (text or "").strip()
    if not body:
        return False, "empty"
    words = _words(body)
    short_reply = bool(_SHORT_REPLIES.match(body))
    named = bool(by_name) or starts_with_name(body)

    if grey:
        # The voice scored between "someone else" and "the owner". Only an
        # explicit address, or a properly-formed sentence in the follow-up
        # window, is enough to act on.
        #
        # HARD_ONLY_ME closes the hole where a stranger who lands in the grey
        # band and says "Dexter" was let through: with it on, the name and the
        # wake word no longer rescue a grey score from cold. A press of the mic
        # button is still the owner by definition (it never even reaches a
        # speaker verdict), and a grey score can still ride a follow-up the owner
        # already started - so "make it only respond to me" costs at most the
        # occasional repeat, not the conversation.
        if reason == "manual":
            return True, ""
        if not HARD_ONLY_ME and (named or reason in ("name", "wake")):
            return True, ""
        if reason == "followup" and len(words) >= GREY_FOLLOWUP_WORDS \
                and (confidence or 1.0) >= GREY_FOLLOWUP_CONFIDENCE:
            return True, ""
        return False, "not owner"

    if named or addressed_directly(body, reason):
        return True, ""

    # Unaddressed: the follow-up window, or open-mic mode.
    if short_reply:
        return True, ""
    if len(words) < MIN_WORDS_UNADDRESSED:
        return False, "too short to be meant for me"
    if confidence and confidence < MIN_CONFIDENCE_UNADDRESSED:
        return False, f"unclear (confidence {confidence:.2f})"
    return True, ""


def strip_wake_word(text: str) -> str:
    """Drop a leading/trailing wake phrase the mic caught around the real command."""
    if not text:
        return text
    out = _WAKE_PREFIX.sub("", text, count=1)
    out = _WAKE_SUFFIX.sub("", out, count=1)
    # Only the wake phrase was said: that is "nothing", not a message.
    return out.strip()


def capabilities() -> Dict[str, Any]:
    """Snapshot for /status so the UI knows whether the local pipeline is usable."""
    return {
        "sttReady": stt_engine.available(),
        "sttLoaded": stt_engine.ready(),
        "wakeReady": WakeWordDetector().available(),
        "vadReady": VadGate().available(),
        "model": stt_engine.model_name,
        "device": stt_engine.device or None,
        "wakeWord": wake_word_label(),
        "wakeMode": WAKE_MODE,
        "wakeName": WAKE_NAME,
        "gateModel": getattr(stt_engine, "gate_model_name", None),
        "speaker": speaker_id.summary(),
        "wakeModel": WakeWordDetector().model_name,
        "bargeIn": BARGE_IN_ENABLED,
        "interruptWords": INTERRUPT_WORDS_ENABLED,
        "followUpWindow": FOLLOWUP_WINDOW,
        "dictation": dictation.status(),
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
        followup_s: Optional[float] = None,
    ) -> None:
        self.session = session
        self._wake = wake if wake is not None else WakeWordDetector()
        self._vad = vad if vad is not None else VadGate()
        self._stt = stt
        self._now = time_fn
        self._barge_in = BARGE_IN_ENABLED if barge_in is None else barge_in
        self._echo_guard_s = (ECHO_GUARD_MS / 1000.0) if echo_guard_s is None else echo_guard_s
        self._followup_s = followup_s

        self._state = "idle"  # "idle" | "recording"
        self._reason = ""     # why the current capture started
        self._speaking = False
        #: Mic off (v13.11). The single, session-wide answer to "should Dexter
        #: listen at all", set from the UI's "Mic off". It lives on the session,
        #: not the window, on purpose: the mic is handed between the two shell
        #: windows, and muting *a window* used to just close its socket and hand
        #: the still-open mic to the other one - so "I switched the mic off and
        #: Dexter still heard me". While this is set, `feed` ignores every frame,
        #: whichever window sent it.
        self._muted = False
        self._armed_until = 0.0  # follow-up window: capture without a wake word until then
        self._ignore_until = 0.0  # echo guard: ignore the mic until then (post-TTS reverb)

        self._wake_buf = np.zeros(0, dtype=np.int16)
        self._vad_buf = np.zeros(0, dtype=np.int16)
        self._preroll = np.zeros(0, dtype=np.int16)
        self._record: List[np.ndarray] = []

        self._had_speech = False   # speech after the wake-tail grace: ends on silence
        self._any_speech = False   # any speech at all: worth transcribing on timeout
        self._silence_run = 0
        self._speech_run = 0
        self._record_chunks = 0
        self._barge_run = 0
        self._barge_decided = False  # has an in-progress barge capture been speaker-verified yet

        # Enrolment: collecting the owner's voice for speaker_id.
        self._enrolling = False
        self._enroll_audio: List[np.ndarray] = []
        self._enroll_speech_chunks = 0
        self._enroll_total_chunks = 0
        self._enroll_reported = 0

        # How many captures in a row came to nothing. Drives the follow-up
        # window's backoff (v13).
        self._followup_misses = 0

    # ───────────────────────── introspection ─────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        """Turn the microphone off (or back on) for the whole session.

        The one switch that decides whether Dexter listens, so that turning the
        mic off in either window is honoured no matter which window holds it.
        Muting also drops any half-captured command, or the tail already in the
        buffer would be transcribed the moment the mic came back."""
        self._muted = bool(muted)
        if self._muted:
            self._reset_capture()
            self._state = "idle"
            self._armed_until = 0.0
            self._barge_run = 0

    def _followup_window(self) -> float:
        return FOLLOWUP_WINDOW if self._followup_s is None else self._followup_s

    # ───────────────────────── control ─────────────────────────

    def set_speaking(self, speaking: bool) -> None:
        """
        Client tells us whether TTS is playing. Turning it off opens the
        follow-up window so a reply can be answered without the wake word.
        """
        was = self._speaking
        self._speaking = speaking
        if speaking:
            # Dexter took the floor; abandon any half-captured command and
            # watch only for barge-in.
            self._reset_capture()
            self._state = "idle"
            self._barge_run = 0
        elif was and not speaking:
            now = self._now()
            self._ignore_until = now + self._echo_guard_s
            self._armed_until = now + self._followup_window()

    def arm_followup(self, seconds: Optional[float] = None, force: bool = False) -> None:
        """Open the follow-up window now (a reply landed without any speech).

        Stops opening itself after `FOLLOWUP_MISSES_MAX` empty or rejected
        captures in a row: if the last two things the window caught were not for
        Dexter, the next one will not be either, and an always-open window is how
        a room's conversation ends up in the chat.
        """
        if not force and self._followup_misses >= FOLLOWUP_MISSES_MAX:
            if VOICE_DEBUG:
                print(f"[Voice] Not re-opening the follow-up window after "
                      f"{self._followup_misses} misses; say my name.")
            return
        self._armed_until = self._now() + (self._followup_window() if seconds is None else seconds)

    def listen(self, reason: str = "manual") -> List[Dict[str, Any]]:
        """
        Push-to-talk: start capturing immediately, no wake word needed. Over
        a reply this is also the clearest possible interruption.
        """
        events: List[Dict[str, Any]] = []
        if self._speaking:
            cancellation.request_cancel(self.session)
            self._speaking = False
            self._barge_run = 0
            events.append({"type": "interrupt"})
        if self._state == "recording":
            return events
        self._ignore_until = 0.0
        self._followup_misses = 0
        self._begin_recording(reason)
        events.append({"type": "listening", "reason": reason})
        return events

    def stop(self) -> List[Dict[str, Any]]:
        """The user said stop from the UI: drop any capture, cut any reply."""
        events: List[Dict[str, Any]] = []
        if self._speaking:
            cancellation.request_cancel(self.session)
            events.append({"type": "interrupt"})
        self._reset_capture()
        self._state = "idle"
        self._armed_until = 0.0
        return events

    def begin_enroll(self, seconds: Optional[float] = None) -> List[Dict[str, Any]]:
        """Start collecting the owner's voice; ends itself with an `enrolled` event."""
        self._reset_capture()
        self._state = "idle"
        self._armed_until = 0.0
        self._enrolling = True
        self._enroll_audio = []
        self._enroll_speech_chunks = 0
        self._enroll_total_chunks = 0
        self._enroll_reported = 0
        self._enroll_target = seconds if seconds else ENROLL_SPEECH_SECONDS
        return [{"type": "enroll_started", "seconds": self._enroll_target}]

    def cancel_enroll(self) -> None:
        self._enrolling = False
        self._enroll_audio = []

    def reset(self) -> None:
        self._reset_capture()
        self._state = "idle"
        self._armed_until = 0.0
        self._barge_run = 0
        self._followup_misses = 0
        self.cancel_enroll()
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
        # Mic off: hear nothing, from any window. Checked before the audio is
        # even looked at, so a promoted standby window cannot slip a frame past.
        if self._muted:
            return []
        if isinstance(pcm, (bytes, bytearray, memoryview)):
            block = np.frombuffer(bytes(pcm), dtype=np.int16)
        else:
            block = np.asarray(pcm, dtype=np.int16)
        if block.size == 0:
            return []

        events: List[Dict[str, Any]] = []

        # A rolling pre-roll so the first word is not clipped when capture
        # starts part-way through this block.
        self._preroll = np.concatenate([self._preroll, block])[-PRE_ROLL_SAMPLES:]

        if self._enrolling:
            self._run_enroll(block, events)
            return events

        was_recording = self._state == "recording"
        self._run_wake(block, events)
        self._run_vad(block, events)

        # A capture that began inside this block already has the block via the
        # pre-roll copy; appending it again doubled the first 128 ms and Whisper
        # heard "Hey, hey Dexter". Only blocks after the start are added here.
        if self._state == "recording" and was_recording:
            self._record.append(block)
        return events

    # ───────────────────────── enrolment ─────────────────────────

    def _run_enroll(self, block: np.ndarray, events: List[Dict[str, Any]]) -> None:
        self._vad_buf = np.concatenate([self._vad_buf, block])
        while self._vad_buf.size >= VAD_CHUNK and self._enrolling:
            chunk = self._vad_buf[:VAD_CHUNK]
            self._vad_buf = self._vad_buf[VAD_CHUNK:]
            self._enroll_total_chunks += 1
            if self._vad.is_speech(chunk):
                self._enroll_audio.append(chunk)
                self._enroll_speech_chunks += 1
            have = self._enroll_speech_chunks * _MS_PER_CHUNK / 1000.0
            if have - self._enroll_reported >= 1.0:
                self._enroll_reported = have
                events.append({"type": "enroll_progress", "seconds": round(have, 1), "target": self._enroll_target})
            if have >= self._enroll_target:
                self._finish_enroll(events)
            elif self._enroll_total_chunks >= ENROLL_TIMEOUT_CHUNKS:
                self._enrolling = False
                events.append({"type": "enrolled", "ok": False, "seconds": round(have, 1),
                               "reason": "I did not hear enough of you. Try again, a little closer to the mic."})

    def _finish_enroll(self, events: List[Dict[str, Any]]) -> None:
        self._enrolling = False
        audio = np.concatenate(self._enroll_audio) if self._enroll_audio else np.zeros(0, dtype=np.int16)
        self._enroll_audio = []
        result = speaker_id.enroll_owner(audio, seconds=audio.size / SAMPLE_RATE)
        events.append({"type": "enrolled", **result})

    # ───────────────────────── wake word ─────────────────────────

    def _run_wake(self, block: np.ndarray, events: List[Dict[str, Any]]) -> None:
        # The wake word only matters when idle, not mid-reply, and outside the
        # follow-up window (inside it, VAD speech-start captures directly).
        if WAKE_MODE == "name" or self._speaking or self._state == "recording" or self._in_followup():
            self._wake_buf = np.zeros(0, dtype=np.int16)
            return
        self._wake_buf = np.concatenate([self._wake_buf, block])
        while self._wake_buf.size >= WAKE_FRAME:
            frame = self._wake_buf[:WAKE_FRAME]
            self._wake_buf = self._wake_buf[WAKE_FRAME:]
            if self._wake.process(frame):
                print("[Voice] Wake word - listening.")
                self._begin_recording("wake")
                events.append({"type": "listening", "reason": "wake"})
                break

    # ───────────────────────── VAD-driven capture / barge-in ─────────────────────────

    def _run_vad(self, block: np.ndarray, events: List[Dict[str, Any]]) -> None:
        self._vad_buf = np.concatenate([self._vad_buf, block])
        while self._vad_buf.size >= VAD_CHUNK:
            chunk = self._vad_buf[:VAD_CHUNK]
            self._vad_buf = self._vad_buf[VAD_CHUNK:]
            speech = self._vad.is_speech(chunk)

            # An interrupt capture that has already begun keeps going, even though Dexter
            # is still speaking - otherwise the branch below swallows every chunk after
            # the first and the capture never ends or gets transcribed.
            if self._speaking and self._state != "recording":
                if self._barge_in and speaker_id.enabled():
                    # Owner-gated barge (v14.1): speaking over Dexter starts a capture,
                    # but Dexter keeps talking until the ECAPA check (in the recording
                    # branch below, once ~1 s is buffered) confirms it is the owner and
                    # not Dexter's own speaker echo. That is what makes "talk to
                    # interrupt" safe by default where plain barge-in was not - the echo
                    # is Dexter's synthetic voice, which never matches the owner's print.
                    if self._in_echo_guard():
                        continue
                    self._barge_run = self._barge_run + 1 if speech else 0
                    if self._barge_run >= BARGE_CHUNKS:
                        self._barge_run = 0
                        self._begin_recording("barge")
                    continue
                if not INTERRUPT_WORDS_ENABLED:
                    # Properly half-duplex: the mic is ignored entirely.
                    continue
                # v13.8, "I am not able to interrupt it mid-sentence": the mic stays
                # open while Dexter speaks, but what it hears is allowed to be **one
                # thing only** - "stop", "quiet", "never mind". A capture that starts
                # here is marked, and `_handle_utterance` discards anything from it
                # that is not a control phrase.
                #
                # That is what makes this safe where plain barge-in was not. Dexter's
                # own voice is in that microphone; it transcribes to the words of its
                # own reply, which are not control phrases, so the echo cannot become a
                # command *by construction* rather than by a threshold. The echo loop
                # that cost an evening in v10 is not reachable from here.
                self._barge_run = self._barge_run + 1 if speech else 0
                if self._barge_run >= BARGE_CHUNKS:
                    self._barge_run = 0
                    self._begin_recording("interrupt")
                continue

            if self._state == "idle":
                if self._in_echo_guard():
                    continue
                self._speech_run = self._speech_run + 1 if speech else 0
                if self._speech_run < SPEECH_START_CHUNKS:
                    continue
                if self._in_followup():
                    # Inside the follow-up window a confirmed speech onset
                    # starts a command with no wake word.
                    print("[Voice] Follow-up speech - listening.")
                    self._begin_recording("followup")
                    events.append({"type": "listening", "reason": "followup"})
                elif WAKE_MODE == "open":
                    # Open mic: whatever is said is for Dexter (speaker_id
                    # decides whether "whatever" includes other people).
                    self._begin_recording("open")
                    events.append({"type": "listening", "reason": "open"})
                elif WAKE_MODE in ("name", "both"):
                    # Someone is talking. Capture it; whether it was for us is
                    # decided when it ends, by whether it starts with the name.
                    # No event yet - most of what a room says is not for Dexter.
                    self._begin_recording("name")
                continue

            # state == recording
            self._record_chunks += 1

            # Owner-gated barge decision (v14.1): a "barge" capture is one that
            # began while Dexter was still speaking. Once ~1 s is buffered, ask
            # the ECAPA check who it is. Only the owner stops Dexter; its own
            # echo or a stranger is dropped and Dexter keeps talking.
            if self._reason == "barge" and not self._barge_decided \
                    and self._record_chunks >= BARGE_VERIFY_CHUNKS:
                audio = np.concatenate(self._record) if self._record else np.zeros(0, dtype=np.int16)
                verdict = speaker_id.judge(audio)
                if verdict.reason == "too short":
                    pass  # keep buffering; decide on the next chunks
                else:
                    self._barge_decided = True
                    if verdict.owner is True:
                        # The owner is talking over Dexter: stop now and keep the
                        # capture as their command.
                        events.append({"type": "interrupt"})
                        cancellation.request_cancel(self.session)
                        self._speaking = False
                        self._reason = "followup"
                        events.append({"type": "listening", "reason": "followup"})
                    else:
                        # Dexter's own echo, or someone who is not the owner: let
                        # Dexter finish, and don't re-arm on the same sound.
                        self._reset_capture()
                        self._state = "idle"
                        self._ignore_until = self._now() + max(self._echo_guard_s, 0.8)
                        continue

            if speech:
                self._any_speech = True
                if self._reason != "wake" or self._record_chunks > WAKE_TAIL_GRACE_CHUNKS:
                    self._had_speech = True
                    self._silence_run = 0
            else:
                self._silence_run += 1

            no_speech_limit = NO_SPEECH_MANUAL_CHUNKS if self._reason == "manual" else NO_SPEECH_CHUNKS
            done = (
                (self._had_speech and self._silence_run >= END_SILENCE_CHUNKS)
                or self._record_chunks >= MAX_UTTERANCE_CHUNKS
                or (not self._had_speech and self._record_chunks >= no_speech_limit)
            )
            if done:
                self._finalize(events)

    # ───────────────────────── helpers ─────────────────────────

    def _in_followup(self) -> bool:
        return self._now() < self._armed_until

    def _in_echo_guard(self) -> bool:
        return self._now() < self._ignore_until

    def _ignore_speaker(self, verdict, reason: str, events: List[Dict[str, Any]]) -> None:
        """Log and signal a rejected speaker, and say what would fix it."""
        who = "my own voice" if verdict.reason == "dexter" else "someone else"
        hint = ""
        if verdict.reason == "not owner":
            hint = " If that was you: start with my name, or lower Voice match strictness in Settings."
        print(f"[Voice] Ignored {who} (owner {verdict.score:.2f}, dexter {verdict.dexter_score:.2f}).{hint}")
        if verdict.reason == "not owner" and reason in ("name", "open", "wake"):
            events.append({"type": "not_you", "score": round(verdict.score, 2), "after": reason})
        if reason != "open":
            self._armed_until = 0.0

    def _begin_recording(self, reason: str) -> None:
        self._state = "recording"
        self._reason = reason
        self._record = [self._preroll.copy()]
        self._had_speech = False
        self._any_speech = False
        self._silence_run = 0
        self._speech_run = 0
        self._record_chunks = 0
        self._barge_decided = False
        self._armed_until = 0.0  # a fresh capture consumes the follow-up window
        self._vad.reset()

    def _reset_capture(self) -> None:
        self._record = []
        self._had_speech = False
        self._any_speech = False
        self._silence_run = 0
        self._speech_run = 0
        self._record_chunks = 0

    def _finalize(self, events: List[Dict[str, Any]]) -> None:
        audio = np.concatenate(self._record) if self._record else np.zeros(0, dtype=np.int16)
        had_speech, any_speech, reason = self._had_speech, self._any_speech, self._reason
        self._reset_capture()
        self._state = "idle"

        seconds = audio.size / SAMPLE_RATE if audio.size else 0.0
        if not any_speech:
            self._followup_misses += 1
            if reason in ("name", "open"):
                return  # a noise the VAD briefly took for speech; nobody's business
            print(f"[Voice] Woke ({reason}) but heard nothing for {seconds:.1f}s.")
            events.append({"type": "heard_nothing", "reason": "silence", "after": reason})
            # Give them a moment to just say it, without another wake word.
            if reason == "wake":
                self.arm_followup(min(self._followup_window(), 6.0))
            return

        if reason == "name" and not dictation.active(self.session):
            # The cheap gate: only the opening of the utterance, only to see
            # whether it was addressed to us. Skipped while dictation is running:
            # a person dictating a note does not say "Dexter" before every
            # sentence, and in name mode that would have thrown the note away. Everything else stays private
            # and costs nothing more.
            head = audio[: int(NAME_GATE_SECONDS * SAMPLE_RATE) + PRE_ROLL_SAMPLES]
            quick = getattr(self._stt, "quick_text", None)
            opening = quick(head) if callable(quick) else self._stt.transcribe(head)
            if not starts_with_name(opening):
                if VOICE_DEBUG:
                    # Off by default: what the room says when it is not
                    # talking to Dexter does not belong in the log.
                    print(f"[Voice] Not for me ({seconds:.1f}s): {opening!r}")
                return
            print(f"[Voice] Name heard - listening: {opening!r}")
            events.append({"type": "listening", "reason": "name"})
            self._wake.reset()

        # Who said it. A press of the mic is the owner's by definition; every
        # other way in is checked against the enrolled voice, and against
        # Dexter's own so its echo never becomes a command.
        #
        # v11.1: a borderline print is not thrown away unheard. On the owner's
        # real mic most sentences scored 0.45-0.74 against a 0.75 bar, and
        # "Dexter, close VS Code" was being logged as "someone else". A grey
        # verdict is transcribed first; if the sentence opens with the name, or
        # the owner was already mid-conversation (name gate, wake word, follow-
        # up), it is theirs. Only a confident verdict teaches the profile.
        verdict = None
        borderline = False
        if reason not in ("manual", "interrupt") and speaker_id.enabled():
            verdict = speaker_id.judge(audio)
            if verdict.reject:
                borderline = bool(getattr(verdict, "grey", False))
                if not borderline:
                    self._ignore_speaker(verdict, reason, events)
                    return

        text, confidence = "", 0.0
        detailed = getattr(self._stt, "transcribe_detailed", None)
        if callable(detailed):
            result = detailed(audio)
            text, confidence = result.text, float(getattr(result, "confidence", 0.0))
        else:
            text = self._stt.transcribe(audio)

        stripped = strip_wake_word(text)

        # ── Dictation (v13): while it is on, what is said is written down, not
        # answered. Ahead of the control phrases because "stop" during dictation
        # means "stop taking notes", and ahead of the chat because a person
        # thinking out loud does not want a reply to every sentence.
        if dictation.active(self.session):
            verdict = dictation.handle(stripped, session=self.session)
            action = verdict.get("action")
            if action == "ended":
                print(f"[Voice] Dictation ended: {verdict.get('words', 0)} word(s).")
                events.append({"type": "dictation", "state": "ended", **_dict_event(verdict)})
                self.arm_followup(force=True)
                return
            if action in ("paused", "resumed"):
                events.append({"type": "dictation", "state": action, **_dict_event(verdict)})
                self.arm_followup(force=True)
                return
            if action == "failed":
                print(f"[Voice] Dictation could not write: {verdict.get('error')}")
                events.append({"type": "dictation", "state": "failed", **_dict_event(verdict)})
                return
            if action == "written":
                self._followup_misses = 0
                events.append({"type": "dictation", "state": "written", **_dict_event(verdict)})
                return
            if action == "scratched":
                events.append({"type": "dictation", "state": "scratched", **_dict_event(verdict)})
                return
            return  # ignored: filler, or paused

        # "Stop" / "never mind" first, and whatever else the gate below would say
        # about them: a one-word instruction to the voice layer must always land,
        # and stopping a reply that was not wanted is never the wrong answer.
        control = classify_command(stripped)
        if not control and reason == "interrupt":
            control = is_interrupt_phrase(stripped)
        if control:
            print(f"[Voice] Control phrase: {stripped!r} -> {control}")
            if control == "stop":
                cancellation.request_cancel(self.session)
                self._speaking = False
                events.append({"type": "interrupt"})
            self._armed_until = 0.0
            events.append({"type": "command", "name": control, "text": stripped})
            return

        # Captured *while Dexter was speaking* (v13.8). Only a control phrase is allowed
        # out of that window, and the branch above has already taken those - so anything
        # still here is either the owner saying something else mid-sentence, or Dexter's
        # own voice coming back through the microphone. Dropping both is what makes
        # listening-while-speaking safe: the echo can never be read as an instruction.
        if reason == "interrupt":
            print(f"[Voice] Heard while speaking, not an interrupt word: {stripped[:60]!r} "
                  "- ignored.")
            return

        # ── Was this for Dexter at all? (v13) ──
        # One rule, for every way in, applied *after* transcription because the
        # words are the evidence: a grey speaker verdict plus "the news at ten"
        # in the follow-up window is not a command, and a nine-second window that
        # accepted anything is why Dexter was "listening to random stuff".
        accepted, why_not = accept_utterance(
            stripped, reason, confidence=confidence, grey=borderline,
            by_name=starts_with_name(text or ""),
        )
        if not accepted and stripped:
            self._followup_misses += 1
            if borderline and verdict is not None:
                self._ignore_speaker(verdict, reason, events)
                return
            if VOICE_DEBUG:
                print(f"[Voice] Not for me - {why_not} ({seconds:.1f}s): {stripped!r}")
            else:
                print(f"[Voice] Ignored an unaddressed {seconds:.1f}s utterance ({why_not}).")
            if reason != "open":
                self._armed_until = 0.0
            return

        if borderline and verdict is not None:
            print(
                f"[Voice] Close enough - owner {verdict.score:.2f} is under the {speaker_id_threshold():.2f} bar, "
                f"but it was addressed to me ({'by name' if starts_with_name(text or '') else reason})."
            )
        elif verdict is not None and verdict.owner:
            try:
                speaker_id.learn(verdict)
            except Exception:
                pass

        text = stripped
        if not text:
            self._followup_misses += 1
            if reason == "open":
                return  # a cough, a hum: nothing to say about it
            # Only the wake word, or nothing Whisper would stand behind.
            why = "silence" if (not had_speech or reason == "name") else "unclear"
            print(f"[Voice] Captured {seconds:.1f}s but nothing usable ({why}).")
            events.append({"type": "heard_nothing", "reason": why, "after": reason})
            self.arm_followup(min(self._followup_window(), 6.0))
            return

        # A real turn: the room is talking to Dexter, so the follow-up window is
        # trustworthy again.
        self._followup_misses = 0
        print(f"[Voice] Heard ({seconds:.1f}s, conf {confidence:.2f}): {text!r}")
        events.append({
            "type": "transcript",
            "text": text,
            "seconds": round(seconds, 2),
            "confidence": round(confidence, 3),
            "after": reason,
        })


class VoiceHub:
    """
    One VoiceSession per chat session, shared by every window that opens the
    socket. Both the full window and the pop-up run a mic, but only one of them
    - the one most recently shown or focused, which sent `claim` - may feed
    audio and receive events. Otherwise a spoken command reached the chat
    twice and two windows spoke the reply at once.

    Sockets are opaque here (any hashable object); main.py hands in the
    WebSocket. Thread-safe: the socket handler and the pipeline thread both
    touch it.
    """

    def __init__(self, session_factory: Callable[[str], "VoiceSession"] = None) -> None:
        self._sessions: Dict[str, VoiceSession] = {}
        self._sockets: Dict[str, List[Any]] = {}
        self._active: Dict[str, Optional[Any]] = {}
        self._factory = session_factory or (lambda sid: VoiceSession(session=sid))
        # Re-entrant: leave() resets the session while holding it.
        self._lock = threading.RLock()

    def session(self, session_id: str) -> "VoiceSession":
        with self._lock:
            vs = self._sessions.get(session_id)
            if vs is None:
                vs = self._factory(session_id)
                self._sessions[session_id] = vs
            return vs

    def connections(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._sockets.values())

    def set_muted(self, session_id: str, muted: bool) -> List[Any]:
        """Mute/unmute the whole session; returns its sockets so the caller can
        tell every window (both should show it, and release the mic device)."""
        with self._lock:
            self.session(session_id).set_muted(muted)
            return list(self._sockets.get(session_id, []))

    def is_muted(self, session_id: str) -> bool:
        with self._lock:
            vs = self._sessions.get(session_id)
            return bool(vs and vs.muted)

    def join(self, session_id: str, sock: Any) -> bool:
        """Register a socket; it becomes active if nobody else holds the mic."""
        with self._lock:
            self._sockets.setdefault(session_id, []).append(sock)
            if self._active.get(session_id) is None:
                self._active[session_id] = sock
            return self._active[session_id] is sock

    def leave(self, session_id: str, sock: Any) -> Optional[Any]:
        """Unregister; returns the socket promoted to active, if any."""
        with self._lock:
            socks = self._sockets.get(session_id, [])
            if sock in socks:
                socks.remove(sock)
            promoted = None
            if self._active.get(session_id) is sock:
                promoted = socks[-1] if socks else None
                self._active[session_id] = promoted
                if promoted is None:
                    # Nobody is listening: drop any half-captured command so
                    # the next window starts clean.
                    self.session(session_id).reset()
            return promoted

    def claim(self, session_id: str, sock: Any) -> Optional[Any]:
        """Make `sock` the active mic; returns the socket that lost it, if any."""
        with self._lock:
            previous = self._active.get(session_id)
            if previous is sock:
                return None
            self._active[session_id] = sock
            return previous

    def is_active(self, session_id: str, sock: Any) -> bool:
        with self._lock:
            return self._active.get(session_id) is sock
