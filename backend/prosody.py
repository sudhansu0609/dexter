"""
Speech delivery: emotion, prosody and text normalisation.

Kokoro is a strong neural voice, but it was being driven flat - one voice, fixed
speed, identical delivery for a fired alarm and a casual greeting - and
`clean_text_for_speech` actively *deleted* emotional cues like *excited* and
[laughs] before they could influence anything.

This module turns a reply into a `Delivery`: normalised speakable text plus the
voice, speed and pacing to say it with. Two things do most of the work:

  1. Normalisation. "10:30 AM" spoken as digits sounds robotic no matter how
     good the model is; "ten thirty AM" sounds human. Same for percentages,
     paths, and opaque ids that should never be read at all.
  2. Pacing. Kokoro honours punctuation, so converting a semicolon-separated
     list into sentences is what gives an agenda its natural pauses.

Emotion is expressed through speed and subtle voice blending rather than by
switching voices outright - Dexter should sound like one person in different
moods, not like several different people.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ───────────────────────── emotion profiles ─────────────────────────

BASE_VOICE = "am_michael"

# (blend partner, partner weight, speed). Weight stays low: past ~0.35 the
# speaker stops sounding like the same person.
EMOTION_PROFILES: Dict[str, Tuple[Optional[str], float, float]] = {
    "neutral":    (None,        0.00, 1.00),
    "upbeat":     ("am_puck",   0.22, 1.07),   # brighter, a little quicker
    "urgent":     ("am_eric",   0.18, 1.12),   # sharper, faster
    "calm":       ("am_onyx",   0.15, 0.94),   # deeper, slower
    "apologetic": ("am_onyx",   0.12, 0.93),
    "warm":       ("am_adam",   0.20, 0.97),
    "reading":    ("am_onyx",   0.25, 0.96),   # narrator register for long text
    "thinking":   ("am_onyx",   0.10, 0.90),
}

DEFAULT_EMOTION = "neutral"


@dataclass
class Delivery:
    text: str
    emotion: str = DEFAULT_EMOTION
    voice: str = BASE_VOICE
    blend_with: Optional[str] = None
    blend_weight: float = 0.0
    speed: float = 1.0
    meta: Dict[str, str] = field(default_factory=dict)

    @property
    def blended(self) -> bool:
        return bool(self.blend_with) and self.blend_weight > 0


# ───────────────────────── emotion cues in text ─────────────────────────

# Authors (and the LLM) write these; they used to be silently deleted. They are
# far better signal than anything we could infer, so they win over heuristics.
_CUE_PATTERN = re.compile(r"[\*\[\(]\s*([a-z][a-z \-']{1,24})\s*[\*\]\)]", re.IGNORECASE)

_CUE_TO_EMOTION = {
    "excited": "upbeat", "happy": "upbeat", "cheerful": "upbeat",
    "grinning": "upbeat", "laughs": "upbeat", "laughing": "upbeat",
    "chuckles": "warm", "smiles": "warm", "smiling": "warm", "warmly": "warm",
    "gently": "calm", "softly": "calm", "calm": "calm", "quietly": "calm",
    "thoughtful": "thinking", "thinking": "thinking", "pauses": "thinking",
    "urgent": "urgent", "urgently": "urgent", "alarmed": "urgent",
    "sorry": "apologetic", "apologetic": "apologetic", "sighs": "apologetic",
    "serious": "calm", "seriously": "calm",
}


def extract_cues(text: str) -> Tuple[str, Optional[str]]:
    """Strip inline stage directions, returning the emotion the first one names."""
    emotion: Optional[str] = None

    def replace(match: re.Match) -> str:
        nonlocal emotion
        word = match.group(1).strip().lower()
        if emotion is None and word in _CUE_TO_EMOTION:
            emotion = _CUE_TO_EMOTION[word]
        return " "

    cleaned = _CUE_PATTERN.sub(replace, text)
    return cleaned, emotion


# ───────────────────────── number → words ─────────────────────────

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]


def say_number(value: int) -> str:
    """Spell an integer. Kept local rather than adding a num2words dependency."""
    if value < 0:
        return f"minus {say_number(-value)}"
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        out = f"{_ONES[hundreds]} hundred"
        return out + (f" {say_number(rest)}" if rest else "")
    for limit, name in ((1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand")):
        if value >= limit:
            count, rest = divmod(value, limit)
            out = f"{say_number(count)} {name}"
            return out + (f" {say_number(rest)}" if rest else "")
    return str(value)


def _say_year(value: int) -> str:
    """2026 -> 'twenty twenty six', not 'two thousand and twenty six'."""
    if 1100 <= value <= 1999 or 2010 <= value <= 2099:
        high, low = divmod(value, 100)
        if low == 0:
            return f"{say_number(high)} hundred"
        return f"{say_number(high)} {'oh ' + _ONES[low] if low < 10 else say_number(low)}"
    return say_number(value)


# ───────────────────────── speech normalisation ─────────────────────────

# Spoken forms for things a TTS model mangles when read literally.
_ACRONYMS = {
    "vscode": "V S Code", "vs code": "V S Code", "url": "U R L", "urls": "U R Ls",
    "api": "A P I", "pc": "P C", "ai": "A I", "id": "I D", "ui": "U I",
    "cpu": "C P U", "gpu": "G P U", "os": "O S", "pdf": "P D F", "csv": "C S V",
    "json": "jason", "sql": "S Q L", "faq": "F A Q", "ceo": "C E O",
    "tts": "T T S", "stt": "S T T", "llm": "L L M", "sse": "S S E",
    "youtube": "YouTube", "github": "GitHub", "wifi": "wy-fy",
}

_UNITS = {
    "%": " percent", "&": " and ", "@": " at ", "+": " plus ",
    "°": " degrees", "€": " euros", "$": " dollars", "£": " pounds",
}


def _time_to_words(hour: int, minute: int, meridiem: str = "") -> str:
    hour_word = say_number(hour if hour else 12)
    if minute == 0:
        spoken = f"{hour_word} o'clock" if not meridiem else hour_word
    elif minute < 10:
        spoken = f"{hour_word} oh {_ONES[minute]}"
    else:
        spoken = f"{hour_word} {say_number(minute)}"
    if meridiem:
        spoken += " " + meridiem.upper().replace(".", "").replace("AM", "A M").replace("PM", "P M")
    return spoken


def _shorten_path(match: re.Match) -> str:
    """Never read a full path aloud - the basename is the only useful part."""
    raw = match.group(0).strip("\"'")
    # Don't swallow sentence-ending punctuation into the filename.
    trailing = ""
    while raw and raw[-1] in ".,;:!?":
        trailing = raw[-1] + trailing
        raw = raw[:-1]

    name = re.split(r"[\\/]", raw)[-1]
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem, ext = name, ""

    # Generated names carry a timestamp; reading ten digits aloud is unbearable.
    stem = re.sub(r"[_\-]?\d{6,}", "", stem).strip("_- ") or "file"

    spoken = f"{stem} dot {ext}" if ext and len(ext) <= 4 else stem
    return spoken + trailing


def normalize_for_speech(text: str) -> str:
    """Rewrite a reply into something that sounds right when spoken."""
    if not text:
        return ""

    out = text

    # Opaque identifiers ("t-1786555392658", "r-1786553641084") carry no meaning
    # aloud and take forever to read digit by digit.
    out = re.sub(r"\b[a-z]{1,3}-\d{6,}\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\b[0-9a-f]{16,}\b", "", out, flags=re.IGNORECASE)
    # Bare timestamps and other very long runs of digits.
    out = re.sub(r"\b\d{7,}\b", "", out)

    # File paths -> basename
    out = re.sub(r"[A-Za-z]:[\\/][^\s,;\"']+", _shorten_path, out)
    out = re.sub(r"(?:\.{1,2}[\\/]|/)[\w.\-]+(?:[\\/][\w.\-]+)+", _shorten_path, out)

    # URLs -> domain only
    out = re.sub(
        r"https?://(?:www\.)?([^\s/]+)\S*",
        lambda m: m.group(1).replace(".", " dot "),
        out,
    )

    # "task(s)" -> "tasks"
    out = re.sub(r"\b(\w+)\(s\)", r"\1s", out)

    # Times: 10:30 AM, 02:00 PM, 17:45
    out = re.sub(
        r"\b(\d{1,2}):(\d{2})\s*([AaPp]\.?[Mm]\.?)",
        lambda m: _time_to_words(int(m.group(1)) % 12, int(m.group(2)), m.group(3)),
        out,
    )
    out = re.sub(
        r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
        lambda m: _time_to_words(int(m.group(1)) % 12 or 12, int(m.group(2))),
        out,
    )
    # Bare "3pm"
    out = re.sub(
        r"\b(\d{1,2})\s*([AaPp]\.?[Mm]\.?)\b",
        lambda m: _time_to_words(int(m.group(1)) % 12 or 12, 0, m.group(2)),
        out,
    )

    # Percentages and symbols
    out = re.sub(r"(\d+)\s*%", lambda m: f"{say_number(int(m.group(1)))} percent", out)
    for symbol, spoken in _UNITS.items():
        out = out.replace(symbol, spoken)

    # Decimals, then remaining integers
    out = re.sub(
        r"\b(\d+)\.(\d+)\b",
        lambda m: f"{say_number(int(m.group(1)))} point " + " ".join(_ONES[int(d)] for d in m.group(2)),
        out,
    )
    out = re.sub(
        r"\b(\d{4})\b",
        lambda m: _say_year(int(m.group(1))),
        out,
    )
    out = re.sub(
        r"\b(\d{1,3}(?:,\d{3})*|\d+)\b",
        lambda m: say_number(int(m.group(1).replace(",", ""))),
        out,
    )

    # Acronyms, on word boundaries only
    for term, spoken in _ACRONYMS.items():
        out = re.sub(rf"\b{re.escape(term)}\b", spoken, out, flags=re.IGNORECASE)

    out = re.sub(r"\s+", " ", out).strip()
    return out


# ───────────────────────── pacing ─────────────────────────


def shape_pacing(text: str, emotion: str) -> str:
    """
    Convert list punctuation into real sentence breaks.

    Kokoro pauses on '.' but runs straight through ';' and '-', which is why a
    six-item agenda came out as one breathless sentence.
    """
    out = text

    # "10:30 AM - Review the script" reads better as a slight pause.
    out = re.sub(r"\s+[-–—]\s+", ", ", out)
    # Semicolons separate list items; make them full stops.
    out = re.sub(r"\s*;\s*", ". ", out)
    # A colon introducing a list becomes a pause rather than a run-on.
    out = re.sub(r":\s+", ". ", out)

    if emotion in ("calm", "reading", "thinking"):
        # Slightly longer beats when narrating.
        out = re.sub(r",\s+", ", ", out)
        out = re.sub(r"\.\s+", "... ", out)

    # Brackets carry meaning in text but nothing in speech; keep the words,
    # drop the characters so they cannot be voiced as a stumble.
    out = out.replace("(", "").replace(")", "").replace("[", "").replace("]", "")

    out = re.sub(r"\.{4,}", "...", out)
    out = re.sub(r"\s+([.,!?])", r"\1", out)
    out = re.sub(r"([.,!?])\1+", r"\1", out)
    return re.sub(r"\s+", " ", out).strip()


# ───────────────────────── emotion inference ─────────────────────────

_ERROR_HINTS = (
    "couldn't", "could not", "cannot", "can't", "failed", "error", "not found",
    "no such", "unavailable", "isn't connected", "offline", "went wrong",
    "didn't", "did not", "unable",
    # An empty result is not a failure, but delivering it brightly sounds wrong.
    "no files", "no matching", "nothing is", "nothing to", "you have no",
    "is empty", "no pending", "none found",
)
_UPBEAT_HINTS = (
    "done", "opened", "opening", "launched", "created", "added", "saved",
    "sent", "set", "noted", "cancelled", "copied", "switched", "finished",
    "success", "nice", "great",
)
_WARM_HINTS = ("hey", "hello", "good morning", "good evening", "anytime",
               "talk soon", "yes?", "i'm here", "welcome")


def infer_emotion(
    text: str,
    action: str = "",
    tool_ok: Optional[bool] = None,
    is_reminder: bool = False,
    long_form: bool = False,
) -> str:
    """Pick a delivery mood from the reply and the context that produced it."""
    if is_reminder or action == "reminder":
        return "urgent"
    if long_form:
        return "reading"

    lowered = text.lower()

    if tool_ok is False or any(hint in lowered for hint in _ERROR_HINTS):
        return "apologetic"
    if action in ("chitchat", "confirm") or any(lowered.startswith(h) for h in _WARM_HINTS):
        return "warm"
    if action == "confirmed" or any(lowered.startswith(h) for h in _UPBEAT_HINTS):
        return "upbeat"
    # Long informational answers land better delivered calmly.
    if len(text) > 320:
        return "calm"
    return DEFAULT_EMOTION


# ───────────────────────── entry point ─────────────────────────


def build_delivery(
    text: str,
    action: str = "",
    tool_ok: Optional[bool] = None,
    is_reminder: bool = False,
    long_form: bool = False,
    emotion: Optional[str] = None,
) -> Delivery:
    """Turn raw reply text into speakable text plus how to say it."""
    stripped, cue_emotion = extract_cues(text or "")

    # Explicit request > author's inline cue > inferred from context.
    chosen = emotion or cue_emotion or infer_emotion(
        stripped, action=action, tool_ok=tool_ok,
        is_reminder=is_reminder, long_form=long_form,
    )
    if chosen not in EMOTION_PROFILES:
        chosen = DEFAULT_EMOTION

    partner, weight, speed = EMOTION_PROFILES[chosen]
    spoken = shape_pacing(normalize_for_speech(stripped), chosen)

    return Delivery(
        text=spoken,
        emotion=chosen,
        voice=BASE_VOICE,
        blend_with=partner,
        blend_weight=weight,
        speed=speed,
        meta={"source": "cue" if cue_emotion else ("explicit" if emotion else "inferred")},
    )
