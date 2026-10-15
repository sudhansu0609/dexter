"""
Three-tier intent router.

Dexter runs on a local model, which cannot be trusted to pick correctly from
thirty tools or to chain four calls reliably. So the language model is the last
resort, not the first:

    Tier 0  regex fast path      ~1ms    the handful of verbs said constantly
    Tier 1  semantic match       ~15ms   embedding vs each tool's example phrasings
    Tier 2  constrained agent    LLM     one tool per iteration, validated, repairable

Most commands never reach Tier 2. That is what makes this both faster and more
accurate than sending everything to the model.
"""

import math
import os

import env_boot  # noqa: F401  - loads .env for every entry point
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import logging

import cancellation
import model_steward
import runtime_health
import sentinel_client
from hybrid_llm import extract_json, llm_router
from tool_registry import (
    ToolResult, all_tools, execute, get_tool, schemas_for, validate_args,
)

log = logging.getLogger("dexter.router")
import companion
import user_profile

# ───────────────────────── result type ─────────────────────────


@dataclass
class RouteResult:
    reply: str
    action: str = "chat"
    tier: int = 3
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    needs_confirmation: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "action": self.action,
            "tier": self.tier,
            "toolCalls": self.tool_calls,
            "details": self.data,
            "needsConfirmation": self.needs_confirmation,
        }


# ───────────────────────── Tier 0: fast path ─────────────────────────

_URLISH = re.compile(
    r"^(?:https?://|www\.)|(?:\.(?:com|org|net|io|dev|ai|co|gov|edu|tv|me|app)\b)",
    re.IGNORECASE,
)

# Spoken sentences arrive with the politeness attached, and Whisper puts a comma
# after it: "Okay, open Chrome for me please." v13.2 - the comma was not allowed
# for, so that exact sentence fell past the whole fast path and reached the model,
# which dutifully asked the launcher for an app called "okay open chrome for me".
_FILLER = re.compile(
    r"^\s*(?:please|hey|hi|yo|so|um|uh|well|alright|all right|ok|okay|kay|"
    r"can you|could you|would you|will you|i want you to|i need you to|i'd like you to|"
    r"let's|lets|for me|go ahead and|now)\b[\s,]*",
    re.IGNORECASE,
)

_TRAILING = re.compile(
    r"[\s,]*(?:please|for me|for us|right now|now|quickly|real quick|"
    r"on my pc|on my computer|on this pc|if you can|if you could|"
    r"would you|will you|thanks|thank you|ta|cheers)\s*[.!?]*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    out = text.strip()
    prev = None
    while prev != out:
        prev = out
        out = _FILLER.sub("", out)
        out = _TRAILING.sub("", out)
    return out.strip(" .!?,")


_REMINDER_LEAD = re.compile(
    r"^(?:remind me to|remind me|set a reminder to|set a reminder for|set a reminder|"
    r"ping me to|ping me about|ping me|wake me up|nudge me to|nudge me|"
    r"don'?t let me forget to|don'?t let me forget|remember to|don'?t forget to)\s*",
    re.IGNORECASE,
)


def _reminder_args(text: str) -> Optional[Dict[str, Any]]:
    """
    Split "remind me to check the render in 30 minutes" into text + when.

    Done deterministically rather than semantically because the reminder body is
    arbitrary user content - it dilutes any embedding, so Tier 1 scored these
    below threshold and they fell through to a very slow Tier 2.
    """
    from timeparse import parse_when, strip_time_phrases

    if not parse_when(text):
        return None
    without_time = re.sub(r"\s+", " ", strip_time_phrases(text)).strip()
    body = _REMINDER_LEAD.sub("", without_time).strip(" .!?,")
    body = re.sub(r"^(?:to|about)\s+", "", body, flags=re.I)
    if not body:
        # The whole phrase was the lead-in, e.g. "wake me up in 45 minutes".
        # Keep the verb and drop the pronoun so the reminder still reads right.
        body = re.sub(r"\bme\b", "", without_time, flags=re.I).strip(" .!?,")
        body = re.sub(r"\s+", " ", body)
    return {"text": body, "when": text} if body else None


_FACT_IS = re.compile(r"^(?:my|our|the)\s+(.+?)\s+(?:is|are|=|was)\s+(.+)$", re.I)
_FACT_I = re.compile(r"^i\s+(?:usually\s+|always\s+|mostly\s+|normally\s+)?(\w+)\s+(?:in|on|with|at|using|for|to|from)\s+(.+)$", re.I)


def _fact_args(fact: str) -> Optional[Dict[str, Any]]:
    """'my editor is davinci resolve' -> key 'editor'; 'I edit in davinci resolve'
    -> key 'edit in'. The whole sentence is kept as the value so nothing is lost."""
    fact = re.sub(r"\s+", " ", (fact or "").strip(" .!?,"))
    if not fact:
        return None
    # "remember to call mum at 6" is a reminder, not a fact.
    if re.match(r"^to\s+", fact, re.I):
        return None
    m = _FACT_IS.match(fact)
    if m:
        return {"key": m.group(1).strip(), "value": m.group(2).strip()}
    m = _FACT_I.match(fact)
    if m:
        prep = re.search(r"\b(in|on|with|at|using|for|to|from)\b", fact[m.end(1):], re.I)
        return {"key": f"{m.group(1).lower()} {prep.group(1).lower()}" if prep else m.group(1).lower(), "value": fact}
    words = [w for w in re.sub(r"[^\w\s]", "", fact).split() if w.lower() not in ("i", "my", "the", "a", "an", "that", "we", "our")]
    return {"key": " ".join(words[:4]).lower() or "note", "value": fact}


def _done_args(title: Optional[str]) -> Optional[Dict[str, Any]]:
    title = re.sub(r"\s+", " ", (title or "").strip(" .!?,"))
    title = re.sub(r"^(?:the|my|that|this)\s+", "", title, flags=re.I)
    title = re.sub(r"\s+(?:commitment|task|item|one|thing|todo|to-do)\s*$", "", title, flags=re.I).strip()
    return {"title": title} if title else None


# Ordered: first match wins. Each entry is (pattern, tool_name, arg_builder).
# An arg_builder returning None means "not actually this intent" - fall through.
def _app_args(name: str) -> Optional[Dict[str, Any]]:
    """`{"app": name}` only when the name is one of the owner's own projects.

    Returning None lets `fast_path` fall through to the next rule, which is what
    keeps "close notepad" going to `close_window` and "open chrome" to
    `launch_app` while "stop vrdeep" and "open buzzedit" become app commands.
    """
    name = (name or "").strip(" .!?,'\"")
    if not name:
        return None
    try:
        import apps_registry

        record = apps_registry.resolve(name)
    except Exception:
        return None
    if not record:
        return None
    if record.get("handler"):
        # The Studio has its own tools (`buzzcaf_start_studio`, `buzzcaf_status`)
        # that know its port walk and its runtime file. The registry delegates to
        # the same client, so routing "open the studio" here would only add a
        # second name for one behaviour.
        return None
    return {"app": name}


# ── v11 argument builders. Each one answers a single question the fast path
# could not answer before: is this a site or an app, a key or a button, a window
# or a sentence? Returning None means "not this rule" and costs nothing.


def _a_site(name: str) -> Optional[str]:
    try:
        from browser import known_site

        return known_site(name)
    except Exception:
        return None


def _site_args(name: str) -> Optional[Dict[str, Any]]:
    """'open youtube' is a tab in your Chrome; 'open chrome' is an application."""
    target = (name or "").strip(" .!?,'\"")
    if not target or _NOT_A_DESTINATION.match(target):
        return None
    if _a_site(target) or _URLISH.search(target):
        return {"target": target}
    return None


# "go to the top of the page" is a scroll and "go to tab 2" is a tab switch;
# both used to be opened as websites by the "go to …" rule (v11, P3.3).
_NOT_A_DESTINATION = re.compile(
    r"^(?:the\s+)?(?:top|bottom|start|end|beginning)(?:\s+of\s+(?:the\s+)?(?:page|document|file|window|list|tab))?$"
    r"|^tab\s+\d+$|^(?:the\s+)?(?:next|previous|last|first)\s+tab(?:\s+(?:in|on)\s+.*)?$"
    r"|^.+?\s+tabs?(?:\s+(?:in|on)\s+.*)?$"
    r"|^tabs?(?:\s+(?:in|on)\s+.*)?$"
    r"|^(?:bed|sleep|lunch)$",
    re.I,
)


def _goto_args(target: str) -> Optional[Dict[str, Any]]:
    text = (target or "").strip(" .!?,'\"")
    if not text or _NOT_A_DESTINATION.match(text):
        return None
    return {"target": text}


def _google_args(query: str) -> Optional[Dict[str, Any]]:
    text = (query or "").strip(" .!?,'\"")
    if not text:
        return None
    # "google calendar" / "google drive" name sites, not searches for the words.
    if _a_site(f"google {text}"):
        return {"target": f"google {text}"}
    return {"target": text}


_WINDOW_WORDS = re.compile(
    r"^(?:it|this|that|the\s+window|this\s+window|that\s+window|tab|this\s+tab|that\s+tab|"
    r"the\s+tab|current\s+tab|the\s+current\s+window|everything|all)$",
    re.I,
)


def _switch_args(name: str) -> Optional[Dict[str, Any]]:
    target = (name or "").strip(" .!?,'\"")
    if not target or _WINDOW_WORDS.match(target) or len(target.split()) > 3:
        return None
    if _a_site(target):
        # "switch to youtube" means the tab, which browser_switch_tab owns.
        return None
    return {"app": target}


def _close_app_args(name: str) -> Optional[Dict[str, Any]]:
    """'close notepad' / 'close chrome' - an application, all of its windows."""
    target = (name or "").strip(" .!?,'\"")
    if not target or _WINDOW_WORDS.match(target) or len(target.split()) > 3:
        return None
    if _CHAIN_WORD.search(target):
        return None
    if _a_site(target) and not _is_desktop_app(target):
        # "close youtube" is a tab, not an app: let the tiers below decide.
        return None
    return {"name": target}


# "shut down" is not "close the PC", and a bare pronoun is resolved elsewhere.
_NOT_CLOSABLE = re.compile(
    r"^(?:it|everything|all|down|pc|computer|the pc|window|this|that)$|\btabs?\b|\bbrowser\b",
    re.I,
)


def _close_here_args(name: str) -> Optional[Dict[str, Any]]:
    target = (name or "").strip()
    if _NOT_CLOSABLE.search(target):
        return None
    return _close_app_args(target)


def _is_desktop_app(name: str) -> bool:
    try:
        import desktop_control

        return desktop_control._alias_of(name) is not None
    except Exception:
        return False


def _arrange_args(verb: str, what: str) -> Optional[Dict[str, Any]]:
    """'minimize chrome' / 'maximize this window'. A pronoun means the window in
    focus, which the tool resolves (and which is never Dexter's own)."""
    action = "minimize" if verb.lower().startswith("minim") else (
        "maximize" if verb.lower().startswith("maxim") else "restore"
    )
    target = (what or "").strip(" .!?,'\"")
    if not target or len(target.split()) > 3:
        return None
    if _WINDOW_WORDS.match(target) or target.lower() in ("everything", "all windows"):
        return {"action": action}
    return {"action": action, "window": target}


def _snap_args(what: str, side: str) -> Optional[Dict[str, Any]]:
    action = "snap_left" if side.lower() == "left" else "snap_right"
    target = (what or "").strip(" .!?,'\"")
    if not target or len(target.split()) > 3:
        return None
    if _WINDOW_WORDS.match(target):
        return {"action": action}
    return {"action": action, "window": target}


# ── drives and folders. "open the c drive" used to reach launch_app and come
# back with "I don't have 'the c drive' as an app". A folder command is only a
# folder command when the words say so - a drive letter, a path separator, the
# word folder/directory/drive, or one of the user's own folders by name - so
# "open buzzanimate" still opens the app and "open the buzzanimate folder" does
# not.
_FOLDERISH = re.compile(
    r"\b(?:folder|directory|drive|disk|explorer)\b|[\\/]|^[a-z]:\s*$|^[a-z]\s*:\s*[\\/]",
    re.I,
)


def _folder_target(text: str) -> Optional[str]:
    """The folder a phrase names, when it is unmistakably about a folder."""
    raw = (text or "").strip().strip(" .!?,'\"")
    if not raw:
        return None
    try:
        import folders
    except Exception:
        return None
    if folders.is_this_pc(raw):
        return None  # that is the drive list, not a folder
    bare = re.sub(r"^(?:the|my)\s+", "", raw.lower())
    bare = re.sub(r"\s+(?:folder|directory|dir)$", "", bare).strip()
    if bare in ("explorer", "file explorer", "windows explorer", "files"):
        return None  # that is the application, which launch_app owns
    if not _FOLDERISH.search(raw) and bare not in folders.USER_FOLDERS:
        return None
    path = folders.spoken_path(raw)
    if path and os.path.isdir(path):
        return path
    # A written-out path that does not exist is still a folder command: the tool
    # says "there's nothing at D:\films", which is the answer. Only *names* have
    # to resolve, because a name that resolves to nothing might be an app.
    if path and re.match(r"^(?:[a-zA-Z]:[\\/]|\\\\[^\\])", raw):
        return path
    return None


def _folder_args(text: str) -> Optional[Dict[str, Any]]:
    target = _folder_target(text)
    return {"path": target} if target else None


def _this_pc_args(text: str) -> Optional[Dict[str, Any]]:
    try:
        import folders

        return {} if folders.is_this_pc(text) else None
    except Exception:
        return None


# "write this down in Notion" is a *place* to write, not a note whose content is
# the words "in Notion". Without this the note tool won a sentence that means
# dictation, and saved two words (v13).
_SINK_ONLY = re.compile(
    r"^(?:in|into|on|to|using|with)\s+(?:my\s+|the\s+|a\s+)?"
    r"(?:notion|notepad|note\s*pad|sticky\s*notes?|clipboard|notes?|word|"
    r"one\s*note|onenote|obsidian|a\s+file|[a-z]:[\\/].*)\s*$",
    re.I,
)


# Anything that would be a *reminder* (a ping) rather than a *task* (Dexter doing
# it) stays with create_reminder; anything whose "when" does not parse is not a
# schedule at all.
_NOT_A_SCHEDULE = re.compile(
    r"^(?:remind|ping|nudge|wake|tell|text|email)\s+(?:me|us)\b|^(?:i|we|you)\s+(?:am|are|will|should|have)\b",
    re.I,
)


#: "every time I save a file" is a trigger, not a clock, and Dexter has no such
#: thing. Saying so is better than scheduling it for 9am.
_NOT_A_CLOCK = re.compile(r"^(?:every|each)\s+(?:time|other\s+time|now\s+and\s+then|so\s+often)\b", re.I)
#: "every morning" + "at 8 review my missions": the clock belongs to the when.
_LEADING_TIME = re.compile(
    r"^(?:at|@)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|o'?clock)?|noon|midnight|midday)\s+", re.I
)


def _schedule_args(goal: str, when: str) -> Optional[Dict[str, Any]]:
    """`{"goal", "when"}` only when the time is real and the goal is an action."""
    task = (goal or "").strip(" .!?,")
    moment = (when or "").strip(" .!?,")
    if not task or not moment:
        return None
    if _NOT_A_CLOCK.match(moment):
        return None
    # "every morning | at 8 review my missions" -> when "every morning at 8".
    lead = _LEADING_TIME.match(task)
    if lead and not re.search(r"\bat\b|\d", moment):
        moment = f"{moment} at {lead.group(1)}"
        task = task[lead.end():].strip()
    if not task or len(task.split()) < 2:
        return None
    if _NOT_A_SCHEDULE.search(task):
        return None
    try:
        from timeparse import parse_recurrence, parse_when

        if not (parse_recurrence(moment) or parse_when(moment)):
            return None
    except Exception:
        return None
    return {"goal": task, "when": moment}


def _block_args(name: str, start: Optional[str], mins: Optional[str]) -> Optional[Dict[str, Any]]:
    """'add a block called deep work at 9am for 50 minutes' -> the app's fields."""
    title = (name or "").strip(" .!?,'\"")
    title = re.sub(r"^(?:in\s+mission\s+reminder\s+)?", "", title, flags=re.I).strip()
    if not title:
        return None
    args: Dict[str, Any] = {"name": title}
    if start:
        args["start"] = start.strip()
    if mins:
        try:
            args["minutes"] = int(mins)
        except ValueError:
            pass
    return args


def _note_args(content: str) -> Optional[Dict[str, Any]]:
    body = (content or "").strip(" .!?,")
    if not body or _SINK_ONLY.match(body):
        return None
    return {"content": body}


def _dictation_args(rest: str) -> Optional[Dict[str, Any]]:
    """'take notes in notepad about the video plan' -> into/title."""
    tail = (rest or "").strip(" .!?,")
    args: Dict[str, Any] = {}
    if not tail:
        return args
    tail = re.sub(r"^(?:from\s+me\s+)?(?:continuously|continuous|please)\s*", "", tail, flags=re.I)
    about = re.search(r"\babout\s+(.+)$", tail, re.I)
    if about:
        args["title"] = about.group(1).strip(" .!?,")
        tail = tail[: about.start()].strip()
    where = re.search(r"\b(?:in|into|on|to|using|with)\s+(.+)$", tail, re.I)
    if where:
        args["into"] = where.group(1).strip(" .!?,")
    return args


# ── "play the third one": which one, and what to do to it (v13).
_ORDINALS = {
    "first": 1, "1st": 1, "top": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10,
}


def _is_site_args(name: Optional[str]) -> Optional[Dict[str, Any]]:
    """A site goes to the tab check; anything else is somebody's application."""
    wanted = (name or "").strip().strip(" .!?,'\"")
    if not wanted:
        return None
    return {"site": wanted} if (_is_known_site(wanted) or "." in wanted) else None


def _is_app_args(name: Optional[str]) -> Optional[Dict[str, Any]]:
    wanted = (name or "").strip().strip(" .!?,'\"")
    return {"app": wanted} if wanted else None


#: Things that follow "click" but are not words on the screen - they belong to the
#: tools that already own them, and a rule this broad has to give way.
_NOT_SCREEN_TEXT = re.compile(
    r"^(?:it|that|this|there|here|them|ok|okay|yes|no|"
    r"(?:the\s+)?(?:first|second|third|fourth|fifth|last|next|previous)\b|"
    r"(?:the\s+)?(?:play|pause|like|dislike|subscribe|comment|share|save)\s*(?:button)?$|"
    r"(?:the\s+)?\w+\s+(?:video|result|profile|tab|row|option|item)s?\b)",
    re.IGNORECASE,
)


def _project_args(name: str) -> Optional[Dict[str, Any]]:
    """{"project": X} when X really is one of the owner's project folders.

    None otherwise, so "open chrome" and "open youtube" carry on to the rules that own
    them. The folder scan is the authority here rather than a list of names, because the
    point of the feature is that a project added tomorrow works without being registered.
    """
    wanted = (name or "").strip().strip(" .!?,'\"")
    if not wanted or len(wanted) < 3 or _NOT_AN_APP.match(wanted):
        return None
    try:
        import project_launcher

        project = project_launcher.find(wanted)
    except Exception:
        return None
    if project is None:
        return None
    # Only when it could actually be started: a folder of build scripts is not an answer
    # to "open it", and letting it through here would hide the real failure.
    best = project.best
    if best is None or best.score <= 0:
        return None
    return {"project": wanted}


def _click_text_args(text: str) -> Optional[Dict[str, Any]]:
    """The words to click, or None when another tool owns the phrase.

    "click the first video" is `youtube_play`, "click the second result" is
    `open_result`, "click it" needs the focus memory - none of them are text on screen.
    """
    wanted = _clean_tail(text or "")
    if not wanted or len(wanted) < 2 or _NOT_SCREEN_TEXT.match(wanted):
        return None
    if len(wanted.split()) > 8:
        return None          # a sentence, not a label
    return {"text": wanted}


def _on_page_args(kind: str, text: str) -> Optional[Dict[str, Any]]:
    """"is there a video called lofi hip hop" -> {kind: video, text: lofi hip hop}.

    None when no title was actually named, because "is there a video" on its own is a
    question about the page, not a search for one thing - `page_results` answers that
    better, and a rule that claims it would make the list unreachable.
    """
    wanted = _clean_tail(text or "")
    if not wanted or len(wanted) < 2:
        return None
    return {"text": wanted, "kind": (kind or "").strip().lower()}


def _clean_tail(text: str) -> str:
    """Trim the words people put on the end of a question about the page."""
    trimmed = (text or "").strip().strip(" .!?,'\"")
    trimmed = re.sub(
        r"\s+(?:on|in)\s+(?:this|the)\s+(?:page|screen|tab|list|window|youtube)$", "",
        trimmed, flags=re.I,
    )
    trimmed = re.sub(r"^(?:called|named|titled|about|for|with)\s+", "", trimmed, flags=re.I)
    return trimmed.strip(" .!?,'\"")


def _hand_bind_args(pose: str, action: str) -> Optional[Dict[str, Any]]:
    """"make a fist the right click" -> {pose: fist, action: right-pinch}.

    None unless **both** halves name something real, because this rule's phrasing
    ("make X the Y") is far too common to claim on a partial match - "make the
    window bigger" has to keep going.
    """
    try:
        import handmouse
    except Exception:
        return None
    which = handmouse.normalize_pose(pose or "")
    what = handmouse.normalize_action(action or "")
    if not which or not what:
        return None
    return {"pose": which, "action": what}


def _ordinal_args(word: str) -> Optional[Dict[str, Any]]:
    key = (word or "").strip().lower()
    position = _ORDINALS.get(key)
    if position is None:
        digits = re.match(r"^(\d{1,2})", key)
        position = int(digits.group(1)) if digits else None
    return {"position": position} if position else None


# "The first <thing>" means three different things, and sending all of them to one
# tool is why "click the first link" tried to play a YouTube video (v13.2).
#
#   video / one / thumbnail   -> the video list      (youtube_play)
#   result / link             -> the page's results  (open_result, YouTube-aware)
#   profile / option / row …  -> anything on screen   (select_nth)
_VIDEO_NOUNS = {"video", "one", "thumbnail"}
_RESULT_NOUNS = {"result", "link"}


def _nth_args(which: str, what: str, tool: str) -> Optional[Dict[str, Any]]:
    """The arguments for whichever of the three rules owns this noun."""
    ordinal = _ordinal_args(which)
    if not ordinal:
        return None
    noun = (what or "").strip().lower().rstrip("s")
    position = ordinal["position"]
    if tool == "youtube_play":
        return {"position": position} if noun in _VIDEO_NOUNS else None
    if tool == "open_result":
        return {"position": position} if noun in _RESULT_NOUNS else None
    if noun in _VIDEO_NOUNS or noun in _RESULT_NOUNS:
        return None
    return {"what": noun, "position": position}


def _grid_video_args(row_word: str, col_word: str) -> Optional[Dict[str, Any]]:
    r = _ordinal_args(row_word)
    c = _ordinal_args(col_word)
    if not r or not c:
        return None
    return {"row": r["position"], "col": c["position"]}


_SITE_LEAD = re.compile(
    r"^(?P<site>youtube|google\s+images|google|images|maps|wikipedia|github|bing)\b"
    r"(?:\s+for)?\s+", re.I)


def _search_here_args(query: Optional[str], site: Optional[str] = None) -> Optional[Dict[str, Any]]:
    text = (query or "").strip(" .!?,'\"")
    if not text or len(text) < 2:
        return None
    # "search youtube for rust tutorials in this tab": the site is inside the
    # query group, and leaving it there searched for the word "youtube".
    lead = _SITE_LEAD.match(text)
    if lead and not site:
        site = lead.group("site")
        text = text[lead.end():].strip(" .!?,'\"")
    text = re.sub(r"^for\s+", "", text, flags=re.I).strip()
    if not text:
        return None
    args: Dict[str, Any] = {"query": text}
    where = (site or "").strip().lower()
    if where:
        args["site"] = "images" if where in ("images", "google images") else where
    return args


def _search_open_args(query: Optional[str], which: Optional[str],
                      whole: str) -> Optional[Dict[str, Any]]:
    text = (query or "").strip(" .!?,'\"")
    if not text:
        return None
    args: Dict[str, Any] = {}
    ordinal = _ordinal_args(which or "first")
    args["position"] = ordinal["position"] if ordinal else 1
    if re.search(r"\byoutube\b", whole, re.I):
        args["site"] = "youtube"
        text = re.sub(r"\b(?:on\s+)?youtube\b", "", text, flags=re.I).strip(" .,")
    # The filler goes *after* the site word is removed: "youtube for lofi" leaves
    # "for lofi" otherwise, and Dexter searches for the word "for".
    text = re.sub(r"^(?:for|on|up|about)\s+", "", text, flags=re.I).strip(" .,")
    if not text:
        return None
    args["query"] = text
    return args


_NOT_APP_NOUNS = re.compile(
    r"^(?:this|that|the|current)?\s*(?:page|screen|window|tab|browser|website|site|dialog|menu)$", re.I
)
_FIELD_NOUNS = re.compile(
    r"\b(?:field|box|bar|input|search|text\s*box|address|comment|form|document|page|tab)\b", re.I
)


def _nth_result_args(pos: str, text: str) -> Optional[Dict[str, Any]]:
    ordinal = _ordinal_args(pos)
    if not ordinal:
        return None
    kind = "result"
    for word in ("video", "link", "item", "option", "profile", "button"):
        if word in text.lower():
            kind = word
            break
    return {"position": ordinal["position"], "kind": kind}


def _click_in_app_args(text: str, app: str) -> Optional[Dict[str, Any]]:
    target = (text or "").strip(" .!?,'\"")
    app_name = (app or "").strip(" .!?,'\"")
    if not target or not app_name:
        return None
    app_lower = app_name.lower()
    if app_lower in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser") or _is_known_site(app_lower):
        return None
    if _NOT_APP_NOUNS.match(app_lower):
        return None
    return {"text": target, "app": app_name}


def _type_in_app_args(text: str, app: str) -> Optional[Dict[str, Any]]:
    text_clean = (text or "").strip(" .!?,'\"")
    app_name = (app or "").strip(" .!?,'\"")
    if not text_clean or not app_name:
        return None
    app_lower = app_name.lower()
    if _FIELD_NOUNS.search(app_lower):
        return None
    if app_lower in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser") or _is_known_site(app_lower):
        return None
    try:
        import desktop_control as dc
        if dc._alias_of(app_lower) or not re.search(r"\s+", app_lower):
            return {"text": text_clean, "window": app_name}
    except Exception:
        pass
    return None


def _type_into_in_app_args(text: str, into: str, app: str) -> Optional[Dict[str, Any]]:
    text_clean = (text or "").strip(" .!?,'\"")
    into_clean = (into or "").strip(" .!?,'\"")
    app_name = (app or "").strip(" .!?,'\"")
    if not text_clean or not into_clean or not app_name:
        return None
    app_lower = app_name.lower()
    if app_lower in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser") or _is_known_site(app_lower):
        return None
    return {"text": text_clean, "into": into_clean, "window": app_name}


def _yt_page_args(phrase: str) -> Optional[Dict[str, Any]]:
    """The YouTube button a whole sentence asks for, via youtube_control's map."""
    try:
        import youtube_control

        action = youtube_control.normalize_page_action(phrase)
        return {"action": action} if action in youtube_control.PAGE_ACTIONS else None
    except Exception:
        return None


def _page_results_args(phrase: str) -> Dict[str, Any]:
    text = (phrase or "").lower()
    for word in ("video", "link", "option", "profile"):
        if word in text:
            return {"kind": word}
    return {"kind": "result"}


def _copy_args(what: Optional[str]) -> Dict[str, Any]:
    word = (what or "").strip().lower()
    if word in ("everything", "page", "all of this", "all of it"):
        return {"what": "all"}
    return {"what": "selection"}


def _paste_args(field: Optional[str], tab: Optional[str],
                send: Optional[str]) -> Optional[Dict[str, Any]]:
    args: Dict[str, Any] = {}
    name = (field or "").strip(" .!?,'\"")
    # "paste it in the gemini tab" puts the tab in the field slot; the tab group
    # only matches when the word "tab" follows something else.
    if name.lower().endswith(" tab"):
        args["tab"] = name[: -len(" tab")].strip()
    elif name and name.lower() not in ("here", "there", "it", "this", "that"):
        args["field"] = name
    if tab:
        args["tab"] = tab.strip()
    if send:
        args["submit"] = True
    return args


def _video_action(phrase: str) -> str:
    """The player action a whole sentence asks for ('skip the ad' -> 'skip ad')."""
    text = (phrase or "").strip().lower()
    if re.search(r"\bads?\b", text):
        return "skip ad"
    if re.search(r"captions|subtitles|\bcc\b", text):
        return "captions"
    if re.search(r"^next\b|\bnext video\b", text):
        return "next"
    if re.search(r"^(?:previous|prev)\b", text):
        return "previous"
    if "faster" in text:
        return "faster"
    if "slower" in text:
        return "slower"
    if re.search(r"skip ahead|fast forward", text):
        return "forward"
    if "rewind" in text:
        return "back"
    if re.search(r"\bunmute\b", text):
        return "unmute"
    if re.search(r"\bmute\b", text):
        return "mute"
    if re.search(r"full\s*screen", text):
        return "fullscreen"
    if re.search(r"\bpause\b", text):
        return "pause"
    return "play"


_TYPE_TAIL = re.compile(r"\s+and\s+(?:press|hit)\s+(?:enter|return)\s*$", re.I)


def _type_args(text: str) -> Optional[Dict[str, Any]]:
    """'type hello world' / 'type out "thanks" and press enter'."""
    body = (text or "").strip()
    submit = bool(_TYPE_TAIL.search(body))
    body = _TYPE_TAIL.sub("", body).strip().strip("\"'")
    if not body or re.match(r"^(?:of|a\s+haiku|an?\s+\w+\s+about)\b", body, re.I):
        return None
    return {"text": body, "submit": submit}


# ── MastGaane music (tools_music). A wish goes over as said; MastGaane resolves it.
_MUSIC_VERB = r"(?:play|put\s+on|chalao|sunao|bajao|lagao)"
_MUSIC_PROGRAMME = (r"(?:hawa\s*mahal|chhaya\s*geet|jai\s*mala|bhoole\s*bisre\s*geet|sangeet\s*sarita|"
                    r"(?:binaca\s+)?geet\s*mala|inse\s*miliye|triveni|vividh\s*bharati)")
_MUSIC_NOUN = re.compile(r"\b(?:ghazals?|geet|gaane|gane|gaana|songs|music|bhajans?|qawwali|sufi|folk)\b", re.I)
# What MastGaane holds: Hindi/Urdu film songs, ghazals, Hindi-belt folk, old radio. "lofi music" and
# "jazz" stay YouTube's (play_on_youtube); bare "some music" is the owner's own jockey.
_MASTGAANE_WISH = re.compile(
    r"\b(?:ghazals?|geet|gaane|gane|gaana|bhajans?|qawwali|sufi|folk|lok|hindi|urdu|bhojpuri|awadhi|maithili|"
    r"magahi|bollywood|filmi|film|purane|purana|old|retro|classic|[5-9]0s|(?:19|20)[0-9]0s)\b|\bsongs\s+(?:by|of)\s+\w|"
    r"\bke\s+gaane\b", re.I)
_NOT_MASTGAANE = re.compile(r"\b(?:youtube|video|videos|spotify|playlist|this|that|it|next|previous|last|"
                            r"first|second|third|\d+(?:st|nd|rd|th)?)\b", re.I)


def _music_args(focus: Optional[str]) -> Optional[Dict[str, Any]]:
    """'play some ghazals' → MastGaane; 'play the next song' / 'play lofi music' → not ours."""
    wish = re.sub(r"^(?:some|a\s+few|me\s+some|me|my)\s+", "", (focus or "").strip(), flags=re.I)
    if _NOT_MASTGAANE.search(wish):
        return None
    if re.fullmatch(r"(?:some\s+)?music", wish, re.I):
        return {"focus": ""}
    return {"focus": wish} if _MASTGAANE_WISH.search(wish) else None


_FAST_RULES: List[Tuple[re.Pattern, str, Callable[[re.Match], Optional[Dict[str, Any]]]]] = [
    # ── MastGaane: named outright, a radio programme, or a music word ("ghazals", "gaane", "songs").
    (
        re.compile(rf"^{_MUSIC_VERB}\s+(?:(?P<focus>.+?)\s+(?:on|from|in)\s+)?mastgaane(?:\s+fm)?$", re.I),
        "mastgaane_play",
        lambda m: {"focus": (m.group("focus") or "").strip()},
    ),
    (
        re.compile(r"^(?:start|open|turn\s+on)\s+mastgaane\s+fm$", re.I),
        "mastgaane_play",
        lambda m: {"focus": ""},
    ),
    (
        re.compile(rf"^{_MUSIC_VERB}\s+(?:the\s+)?(?P<focus>{_MUSIC_PROGRAMME})(?:\s+program(?:me)?)?$", re.I),
        "mastgaane_play",
        lambda m: {"focus": m.group("focus")},
    ),
    (
        re.compile(rf"^{_MUSIC_VERB}\s+(?P<focus>.*{_MUSIC_NOUN.pattern}.*)$", re.I),
        "mastgaane_play",
        lambda m: _music_args(m.group("focus")),
    ),
    (
        re.compile(r"^(?:i\s+)?(?:really\s+)?(?:like|love)\s+(?:this|that)\s+(?:song|ghazal|gaana|track)$"
                   r"|^more\s+(?:songs\s+)?like\s+this(?:\s+one)?$", re.I),
        "mastgaane_feedback",
        lambda m: {"verdict": "like"},
    ),
    (
        re.compile(r"^(?:i\s+)?(?:don'?t|do\s+not|hate)\s+(?:like\s+)?(?:this|that)\s+(?:song|ghazal|gaana|track)$"
                   r"|^never\s+play\s+(?:this|that)(?:\s+(?:song|ghazal|one))?\s+again$", re.I),
        "mastgaane_feedback",
        lambda m: {"verdict": "dislike"},
    ),
    # ── v11.1: power. These sit first so "restart my pc" is never mistaken for
    # restart_app's "restart <app>" (which yielded nothing) and "shut down the
    # computer" never lands on sleep_pc by embedding distance. Every one of the
    # destructive ones asks before it acts (confirm=True on the tool).
    (
        re.compile(
            r"^(?:(?:please|can you|could you|would you|i want you to|go ahead and)\s+)*"
            r"(?:restart|reboot)\s+(?:my|the|this|your)?\s*(?:pc|computer|machine|laptop|system|windows|desktop)\s*(?:now|please)?\s*[.!?]*$",
            re.I,
        ),
        "restart_pc",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:(?:please|can you|could you|would you|i want you to|go ahead and)\s+)*"
            r"(?:shut\s*down|power\s+off|turn\s+off|switch\s+off)\s+(?:my|the|this|your)?\s*(?:pc|computer|machine|laptop|system|windows|desktop)\s*(?:now|please)?\s*[.!?]*$",
            re.I,
        ),
        "shutdown_pc",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:(?:please|can you|could you|would you)\s+)*"
            r"(?:sign|log)\s*(?:me\s+)?(?:out|off)(?:\s+of\s+(?:windows|my\s+account|the\s+pc))?\s*[.!?]*$",
            re.I,
        ),
        "sign_out",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:(?:please|can you|could you|would you)\s+)*"
            r"lock\s+(?:my|the|this)?\s*(?:pc|computer|machine|screen|laptop|workstation|windows)\s*[.!?]*$",
            re.I,
        ),
        "lock_screen",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:(?:please|can you|could you|would you)\s+)*"
            r"(?:cancel|abort|stop|undo)\s+(?:the|that|this)?\s*(?:restart|reboot|shut\s*down|shutdown)\s*[.!?]*$"
            r"|^(?:don'?t|do\s+not)\s+(?:restart|reboot|shut\s*down)(?:\s+(?:my|the|this|your)?\s*(?:pc|computer|machine|laptop|system|windows|desktop))?\s*[.!?]*$",
            re.I,
        ),
        "cancel_shutdown",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:(?:please|can you|could you|would you)\s+)*"
            r"(?:empty|clear|purge|clean\s+out)\s+(?:my|the)?\s*(?:recycle\s*bin|recycling\s*bin|trash|bin)\s*[.!?]*$",
            re.I,
        ),
        "empty_recycle_bin",
        lambda m: {},
    ),
    # ── v13: drives and folders. Ahead of everything that opens things, because
    # "open the c drive" was reaching launch_app ("I don't have 'the c drive' as
    # an app on this PC"). Each builder yields None unless the words genuinely
    # name a folder, so "open chrome" and "open buzzedit" are untouched.
    (
        re.compile(
            r"^(?:what|which)\s+(?:drives|disks)\s+(?:do\s+i\s+have|are\s+(?:there|on\s+(?:this|my)\s+(?:pc|machine|computer)))\s*[.?!]*$"
            r"|^(?:list|show)\s+(?:me\s+)?(?:my\s+|all\s+(?:my\s+)?|the\s+)?(?:drives|disks)\s*[.?!]*$"
            r"|^how\s+(?:much\s+space|full)\s+(?:is\s+)?(?:left\s+)?on\s+(?:my|the)\s+drives?\s*[.?!]*$",
            re.I,
        ),
        "list_drives",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:open|show(?:\s+me)?|go\s+to)\s+(?:this\s+pc|my\s+computer|my\s+pc|the\s+drives?\s+list|drives)\s*[.?!]*$",
            re.I,
        ),
        "open_folder",
        lambda m: {"path": "this pc"},
    ),
    (
        re.compile(
            r"^(?:what(?:'s| is)\s+(?:in|inside|on)|what\s+(?:files|folders)\s+are\s+(?:in|inside|on)|"
            r"list\s+(?:the\s+)?(?:files|folders|contents|everything)\s+(?:in|inside|of|on|under)|"
            r"show\s+me\s+(?:what(?:'s| is)\s+(?:in|inside|on)|the\s+contents\s+of))"
            r"\s+(?:the\s+|my\s+)?(.+?)\s*[.?!]*$",
            re.I,
        ),
        "list_folder",
        lambda m: _folder_args(m.group(1)),
    ),
    (
        re.compile(r"^(?:list|ls|dir)\s+(?:the\s+|my\s+)?(.+?)\s*[.?!]*$", re.I),
        "list_folder",
        lambda m: _folder_args(m.group(1)),
    ),
    (
        re.compile(
            r"^(?:open|show(?:\s+me)?|go\s+to|take\s+me\s+to|bring\s+up|pull\s+up)\s+(?:the\s+|my\s+)?"
            r"(.+?)(?:\s+in\s+(?:file\s+)?explorer)?\s*[.?!]*$",
            re.I,
        ),
        "open_folder",
        lambda m: _folder_args(m.group(1)),
    ),
    (
        re.compile(r"^where(?:'s| is|\s+are)\s+(?:my\s+|the\s+)?(.+?)\s+folders?\s*[.?!]*$", re.I),
        "find_folder",
        lambda m: {"name": m.group(1).strip()} if m.group(1).strip() else None,
    ),
    # ── v13.6: the hand mouse, before the app launcher.
    #
    # "can you open the hand tracking?" reached `launch_app`, which searched the
    # installed-app index for "hand tracking" and started Git's `tr.exe`. The index
    # bug is fixed in app_index, but the deeper point is that this is a *feature of
    # Dexter's*, not an application on the disk, and it should never have been
    # looked for out there.
    (
        re.compile(
            r"^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
            r"(?:open|show(?:\s+me)?|start|turn\s+on|enable|use|launch|bring\s+up|give\s+me)\s+"
            r"(?:up\s+)?(?:the\s+|my\s+)?(?:webcam\s+)?"
            r"(?:hand|gesture)\s*(?:mouse|tracking|tracker|control|cursor|pointer)"
            r"(?:\s+(?:window|preview|panel|on|please))?\s*[.!?]*$",
            re.I,
        ),
        "start_hand_mouse",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
            r"(?:stop|turn\s+off|close|quit|end|disable|kill)\s+(?:the\s+|my\s+)?"
            r"(?:hand|gesture)\s*(?:mouse|tracking|tracker|control|cursor|pointer)\s*[.!?]*$",
            re.I,
        ),
        "stop_hand_mouse",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:is\s+(?:the\s+|my\s+)?(?:hand|gesture)\s*(?:mouse|tracking|tracker|control)\s+"
            r"(?:on|running|working|active)|"
            r"(?:hand|gesture)\s*(?:mouse|tracking)\s+status|"
            r"what\s+(?:pose|gesture)\s+(?:do\s+you\s+|can\s+you\s+)?see)\b",
            re.I,
        ),
        "hand_mouse_status",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:what|which|list|show(?:\s+me)?|tell\s+me)\b.*\bhand\s+(?:mouse\s+)?gestures?\b",
            re.I,
        ),
        "hand_gestures",
        lambda m: {},
    ),
    # v13.8: the speed of the pointer, out loud - which matters more than it looks,
    # because when the pointer is too fast, reaching the slider that fixes it is
    # precisely the thing you cannot do.
    (
        re.compile(
            r"^(?:(?:make|set)\s+)?(?:the\s+)?hand\s*"
            r"(?:mouse|pointer|cursor|tracking)?\s*(?:speed\s*)?(?:to\s+)?"
            r"(faster|slower|quicker|less\s+sensitive|more\s+sensitive|more\s+precise|"
            r"\d*\.?\d+)\s*[.!?]*$",
            re.I,
        ),
        "set_hand_speed",
        lambda m: {"speed": " ".join(m.group(1).split())},
    ),
    (
        re.compile(
            r"^(?:the\s+)?hand\s*(?:mouse|pointer|cursor|tracking)\s+is\s+(?:too\s+)?"
            r"(fast|slow|sensitive|twitchy|jumpy|sluggish)\b",
            re.I,
        ),
        "set_hand_speed",
        lambda m: {"speed": "faster" if m.group(1).lower() in ("slow", "sluggish")
                   else "slower"},
    ),
    # "make a fist the right click" - the builder returns None unless both halves
    # name something real, so an unrelated "make the window bigger" falls through
    # to the rules and tiers that own it.
    (
        re.compile(
            r"^(?:make|set|bind|change|map|turn)\s+(?:the\s+|a\s+|an\s+|my\s+)?(.+?)\s+"
            r"(?:to|into|as|the|do|mean|means)\s+(?:be\s+)?(?:the\s+|a\s+|an\s+)?(.+?)\s*[.!?]*$",
            re.I,
        ),
        "set_hand_gesture",
        lambda m: _hand_bind_args(m.group(1), m.group(2)),
    ),
    # ── v9: the owner's own projects. These come first so "stop vrdeep" is an
    # app command rather than close_window's guess at a window title, and they
    # yield (args=None) for anything not in the registry so the older rules
    # still own "close notepad" and "open chrome".
    (
        re.compile(
            r"^(?:what(?:'s| is)\s+running|what\s+apps?\s+(?:are|is)\s+running|"
            r"what\s+have\s+i\s+got\s+(?:open|running)|what(?:'s| is)\s+on\s+(?:my|this)\s+(?:machine|pc|computer)|"
            r"show\s+me\s+what(?:'s| is)\s+running|which\s+of\s+my\s+apps\s+are\s+(?:up|running)|"
            r"list\s+running\s+(?:projects|apps))\b",
            re.I,
        ),
        "apps_overview",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:is|are)\s+(?:the\s+|my\s+)?(.+?)\s+(?:still\s+)?(?:running|up|on|going|alive)\s*$", re.I),
        "app_status",
        lambda m: _app_args(m.group(1)),
    ),
    # v13.8: "click on the text X". Chrome publishes no page content to Windows
    # accessibility at all, so the words on screen are the only thing left that works
    # in any browser without a debug port - and it is what was asked for.
    (
        re.compile(
            # Deliberately only the *explicit* ask. Plain "click sign in" belongs to
            # `browser_click`, which tries the accessible name first - instant, and right
            # for the browser's own furniture - and falls back to reading the screen
            # itself (v13.8). Going straight to OCR there would be slower and no better.
            r"^(?:click|press|tap|hit)\s+(?:on\s+)?(?:the\s+)?"
            r"(?:(?:text|word|words)\s*(?:that\s+says|saying|labelled|labeled|called|named|"
            r"which\s+says)?|(?:link|button|option|item)\s*(?:that\s+says|saying|labelled|"
            r"labeled|called|named|which\s+says))\s*"
            r"[\"'“]?(.+?)[\"'”]?\s*"
            r"(?:\s+(?:on|in)\s+(?:the\s+)?(?:screen|page|browser|window|chrome))?\s*[.!?]*$",
            re.I,
        ),
        "click_on_screen",
        lambda m: _click_text_args(m.group(1)),
    ),
    (
        re.compile(
            # Not "what's on my screen" - that is `get_active_window`, which answers
            # with the window's name and has owned the phrase since v5. This is the
            # stronger ask: read the words.
            # "read this page" is `browser_read_page`, which reads the document rather
            # than a photograph of it - better text, when it can. This is the screen.
            r"^(?:what\s+does\s+(?:the\s+)?screen\s+say|"
            r"read\s+(?:me\s+)?(?:the\s+|my\s+)?screen(?:\s+to\s+me)?|"
            r"read\s+(?:out\s+)?what(?:'s| is)\s+on\s+(?:the\s+|my\s+)?screen)\s*[.!?]*$",
            re.I,
        ),
        "read_screen",
        lambda m: {},
    ),
    # "is there a video called X on this page", "can you see a link to the docs"
    (
        re.compile(
            r"^(?:please\s+)?"
            # A bare "see" and "spot" are in here because `_clean` has already taken
            # the politeness off the front: by the time a rule sees it, "can you see a
            # video about X" is "see a video about X".
            r"(?:is\s+there|are\s+there|do\s+you\s+see|(?:can|could)\s+you\s+(?:see|find|spot)|"
            r"check\s+(?:if|for)|see|spot|find|look\s+for)\s+(?:a\s+|an\s+|any\s+|the\s+)?"
            r"(video|link|button|result|tab|item|post|song|playlist|thing)s?\s*"
            r"(?:called|named|titled|about|for|with|that\s+says|matching)?\s*(.+?)"
            r"(?:\s+(?:on|in)\s+(?:this|the)\s+(?:page|screen|tab|list|window|youtube))?"
            r"\s*[.!?]*$",
            re.I,
        ),
        "is_on_page",
        lambda m: _on_page_args(m.group(1), m.group(2)),
    ),
    (
        re.compile(
            r"^(?:what|which)\s+(?:page|site|website|url|tab)\s+(?:is\s+this|am\s+i\s+on|"
            r"is\s+(?:that|it)|is\s+open|do\s+i\s+have\s+open)\s*[.!?]*$",
            re.I,
        ),
        "what_page_is_this",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:open|start|launch|run|fire up|boot up|bring up)\s+(?:the\s+|my\s+)?(.+?)\s*$", re.I),
        "open_app",
        lambda m: _app_args(m.group(1)),
    ),
    # v13.8: the owner's other projects. `open_app` above owns the twelve in the
    # registry (it knows their ports and asks Sentinel for memory first); this catches
    # every other folder next to Dexter that has a launcher of its own, and yields for
    # anything that is not one - so "open chrome" still reaches the app launcher.
    (
        re.compile(
            # Longest alternatives first, or "start" matches inside "start up" and the
            # "up" ends up in the project's name.
            r"^(?:start\s+up|boot\s+up|fire\s+up|bring\s+up|open|start|launch|run)\s+"
            r"(?:the\s+|my\s+)?(.+?)(?:\s+project)?\s*[.!?]*$",
            re.I,
        ),
        "launch_project",
        lambda m: _project_args(m.group(1)),
    ),
    (
        re.compile(
            r"^(?:what|which)\s+(?:of\s+my\s+)?(?:projects|apps)\s+"
            r"(?:do\s+i\s+have|are\s+there|can\s+you\s+(?:start|launch|open)|exist)"
            r"|^list\s+(?:my\s+)?projects"
            r"|^what\s+can\s+you\s+(?:start|launch)\b",
            re.I,
        ),
        "list_projects",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:stop|shut\s*down|close|quit|kill|end)\s+(?:the\s+|my\s+)?(.+?)\s*$",
            re.I,
        ),
        "stop_app",
        lambda m: _app_args(m.group(1)),
    ),
    (
        re.compile(r"^(?:restart|bounce|reboot|relaunch)\s+(?:the\s+|my\s+)?(.+?)\s*$", re.I),
        "restart_app",
        lambda m: _app_args(m.group(1)),
    ),
    (
        re.compile(
            r"^(?:clean\s*up\s+(?:some\s+)?(?:space|disk|the\s+drive|the\s+disk)|"
            r"free\s+up\s+(?:some\s+)?(?:disk\s+)?space|run\s+a\s+(?:safe\s+)?clean(?:up)?|"
            r"disk\s+clean\s*up|clear\s+out\s+the\s+temp\s+files|we(?:'re| are)\s+low\s+on\s+disk)\b",
            re.I,
        ),
        "request_cleanup",
        lambda m: {"mode": "safe"},
    ),
    # Sentinel's batch controls. These are said when the machine already feels
    # slow, which is the worst moment to make someone wait for a model.
    (
        re.compile(
            r"^(?:pause|freeze|stop|park|suspend)\s+(?:the\s+|my\s+|all\s+)?"
            r"(?:batch|background)\s+(?:jobs?|work|tasks?|renders?|processing)\b",
            re.I,
        ),
        "pause_batch_jobs",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:resume|unpause|unfreeze|restart|continue)\s+(?:the\s+|my\s+|all\s+)?"
            r"(?:batch|background)\s+(?:jobs?|work|tasks?|renders?|processing)\b",
            re.I,
        ),
        "resume_batch_jobs",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:remind me|set a reminder|ping me|nudge me|wake me up|don'?t let me forget|remember to|don'?t forget to)\b",
            re.I,
        ),
        "create_reminder",
        lambda m: _reminder_args(m.string),
    ),
    (
        re.compile(
            r"^(?:cancel|delete|remove|forget|drop)\s+(?:the\s+|my\s+|that\s+)?reminder"
            r"(?:\s+(?:about|for|to|regarding))?\s*(.*)$",
            re.I,
        ),
        "cancel_reminder",
        lambda m: {"text": m.group(1).strip(" .!?,")} if m.group(1).strip() else None,
    ),
    # "cancel the studio lights reminder"
    (
        re.compile(r"^(?:cancel|delete|remove|forget|drop)\s+(?:the\s+|my\s+|that\s+)?(.+?)\s+reminder\s*$", re.I),
        "cancel_reminder",
        lambda m: {"text": m.group(1).strip(" .!?,")} if m.group(1).strip() else None,
    ),
    # ── v8: the things said every day that used to reach the model and get a
    # promise instead of an action ("Noted." with no note, "I've remembered"
    # with nothing stored, "marked done" with nothing marked).
    (
        re.compile(r"^(?:remember|keep in mind|don'?t forget|bear in mind)\s+(?:that\s+)?(.+)$", re.I),
        "remember_fact",
        lambda m: _fact_args(m.group(1)),
    ),
    (
        re.compile(
            r"^(?:notes?|idea|thought)\s*[:\-]\s*(.+)$|"
            r"^(?:note that|note down|take a note|make a (?:sticky )?note|write (?:this |that |it )?down|"
            r"jot (?:this |that |it )?down|capture (?:this|that|it))\b\s*(?:[:\-]|about|that|of)?\s*(.*)$",
            re.I,
        ),
        "create_note",
        lambda m: _note_args(m.group(1) or m.group(2) or ""),
    ),
    (
        re.compile(r"^(?:drop|scrap|ditch|abandon|remove|delete)\s+(?:the\s+|my\s+|that\s+)?(.+?)\s+(?:commitment|task|todo|to-do)\b\s*(?:[,.-]\s*(.*))?$", re.I),
        "drop_commitment",
        lambda m: {"text": m.group(1).strip(), **({"why": m.group(2).strip()} if m.group(2) and m.group(2).strip() else {})} if m.group(1).strip() else None,
    ),
    (
        re.compile(
            r"^(?:mark|tick|check|cross)\s+(?:off\s+)?(?:the\s+|my\s+)?(.+?)\s+(?:as\s+)?(?:done|complete|completed|finished|off)\s*$|"
            r"^(?:tick|check|cross)\s+off\s+(?:the\s+|my\s+)?(.+)$",
            re.I,
        ),
        "complete_task",
        lambda m: _done_args(m.group(1) or m.group(2)),
    ),
    (
        re.compile(
            r"^(?:what(?:'s| is)\s+(?:the\s+)?(?:current\s+|local\s+)?time|what time is it|time)\s+(?:right now\s+)?"
            r"(?:in|at|for)\s+(?:the\s+)?([a-z][a-z .'\-]+?)\s*(?:right now|now|at the moment)?\s*$",
            re.I,
        ),
        "get_current_time",
        lambda m: {"place": m.group(1).strip()},
    ),
    (
        re.compile(
            r"^(?:(?:please\s+)?(?:can\s+you\s+)?(?:tell\s+me\s+)?(?:do\s+you\s+know\s+)?)*"
            r"(?:what(?:'?s|\s+is)\s+new(?:\s+in\s+dexter|\s+with\s+you)?|"
            r"what\s+changes?\s+(?:were\s+done|were\s+made|happened|did\s+you\s+get|have\s+been\s+made)(?:\s+in\s+you|\s+to\s+you)?(?:\s+from\s+last\s+time|\s+since\s+last\s+time)?|"
            r"what\s+changed(?:\s+in\s+you|\s+to\s+you|\s+in\s+dexter)?(?:\s+from\s+last\s+time|\s+since\s+last\s+time|\s+recently)?)\s*[.!?]*$",
            re.I,
        ),
        "what_changed",
        lambda m: {},
    ),
    # ── v13: continuous note-taking. After create_note on purpose: "write this
    # down: buy milk" is one note with content, and only a bare "write this down"
    # (create_note yields None) means "keep writing down what I say".
    (
        re.compile(
            r"^(?:please\s+)?(?:can\s+you\s+)?(?:start\s+|begin\s+|keep\s+)?"
            r"(?:taking|take|writing|write|jot(?:ting)?|noting)\s+(?:down\s+)?"
            r"(?:my\s+|some\s+|these\s+|the\s+)?(?:notes?|dictation|everything\s+i\s+say|"
            r"what\s+i\s+say|this\s+down|it\s+down)\b(?P<rest>.*)$",
            re.I,
        ),
        "start_dictation",
        lambda m: _dictation_args(m.group("rest")),
    ),
    (
        re.compile(r"^(?:start|begin|open)\s+dictation\b(?P<rest>.*)$", re.I),
        "start_dictation",
        lambda m: _dictation_args(m.group("rest")),
    ),
    (
        re.compile(
            r"^(?:stop|end|finish|that'?s\s+(?:it|all|the\s+end)\s+(?:for|of))\s*"
            r"(?:the\s+)?(?:taking\s+)?(?:notes?|dictation|dictating|writing\s+(?:this|that)\s+down)\s*[.!]*$",
            re.I,
        ),
        "stop_dictation",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:are\s+you\s+(?:still\s+)?taking\s+notes|dictation\s+status|"
            r"where\s+are\s+(?:my|the)\s+notes\s+going|how\s+much\s+have\s+you\s+written\s+down)\s*[.?!]*$",
            re.I,
        ),
        "dictation_status",
        lambda m: {},
    ),
    # ── v13: schedule anything. "Every morning at 8 review my missions" used to
    # reach the model, which promised to do it and scheduled nothing. These three
    # shapes cover how it is actually said; `_schedule_args` refuses unless the
    # time really parses, so "in the downloads folder find the invoice" is not
    # mistaken for a schedule.
    (
        re.compile(
            r"^(?P<when>(?:every|each)\s+[^,]{2,40}?)\s*,?\s+(?:please\s+)?(?P<goal>[a-z][^,]{3,}.*)$",
            re.I,
        ),
        "schedule_task",
        lambda m: _schedule_args(m.group("goal"), m.group("when")),
    ),
    (
        re.compile(
            r"^(?P<goal>.{5,}?)\s+(?P<when>(?:every|each)\s+[a-z][^,]{2,30}|daily|hourly|weekly)\s*[.!]*$",
            re.I,
        ),
        "schedule_task",
        lambda m: _schedule_args(m.group("goal"), m.group("when")),
    ),
    (
        re.compile(
            r"^(?:please\s+)?schedule\s+(?:a\s+task\s+)?(?P<goal>.+?)\s+"
            r"(?:for|at|on|every)\s+(?P<when>[^,]+?)\s*[.!]*$",
            re.I,
        ),
        "schedule_task",
        lambda m: _schedule_args(m.group("goal"), m.group("when")),
    ),
    (
        re.compile(
            r"^(?P<when>(?:at|on|in)\s+[^,]{2,28}|tomorrow(?:\s+(?:morning|afternoon|evening|at\s+[^,]{1,12}))?|"
            r"tonight|later\s+today)\s*,\s*(?:please\s+)?(?P<goal>[a-z].{3,})$",
            re.I,
        ),
        "schedule_task",
        lambda m: _schedule_args(m.group("goal"), m.group("when")),
    ),
    # ── v13: the Mission Reminder app. After the schedule rules, so "review my
    # missions every evening" is a schedule and "review my missions" is the
    # review itself.
    (
        re.compile(
            r"^(?:review|read)\s+(?:me\s+)?(?:my|the)\s+missions?(?:\s+(?:again|now))?\s*[.!?]*$"
            r"|^(?:what|how)\s+(?:are|about)\s+my\s+(?:mission\s+)?blocks(?:\s+today)?\s*[.?!]*$"
            r"|^what(?:'s| is)\s+my\s+next\s+(?:mission\s+)?block\s*[.?!]*$"
            r"|^(?:am\s+i\s+on\s+track\s+(?:with|on)\s+my\s+(?:time\s+)?blocks|"
            r"how\s+am\s+i\s+doing\s+on\s+my\s+blocks)\s*[.?!]*$"
            r"|^what\s+does\s+mission\s+reminder\s+say\s*[.?!]*$",
            re.I,
        ),
        "mission_review",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:start|begin)\s+(?:my\s+|the\s+)?(?:next\s+)?(?:time\s+)?"
            r"(?:block|mission\s+block|focus\s+session)\s*[.!]*$",
            re.I,
        ),
        "mission_start_block",
        lambda m: {"action": "start"},
    ),
    (
        re.compile(
            r"^(?:pause|resume|unpause)\s+(?:my\s+|the\s+)?(?:current\s+)?"
            r"(?:block|session|focus\s+session)\s*[.!]*$",
            re.I,
        ),
        "mission_start_block",
        lambda m: {"action": "pause"},
    ),
    (
        re.compile(
            r"^(?:add|save|create|put|schedule)\s+(?:a\s+|another\s+)?(?:time\s+|mission\s+)?block\s+"
            r"(?:called\s+|named\s+|for\s+|to\s+)?(?P<name>.+?)"
            r"(?:\s+at\s+(?P<start>\d{1,2}(?::\d{2})?\s*(?:am|pm)?))?"
            r"(?:\s+for\s+(?P<mins>\d{1,3})\s*(?:min|mins|minutes)?)?\s*[.!]*$",
            re.I,
        ),
        "mission_save_block",
        lambda m: _block_args(m.group("name"), m.group("start"), m.group("mins")),
    ),
    # v11: "close notepad" closes the *application* - every window of it, with
    # WM_CLOSE so it can ask about unsaved work. It used to call close_window,
    # which closed whichever single window matched the title best (and, because
    # of a misplaced decorator, actually failed outright).
    (
        re.compile(r"^(?:close|quit|exit|shut(?:\s+down)?)\s+(?!out\b)(?:the\s+|that\s+|this\s+)?(.+?)(?:\s+(?:window|app|application))?\s*$", re.I),
        "close_app",
        lambda m: _close_here_args(m.group(1)),
    ),
    (
        re.compile(
            r"^(?:what(?:'s| is)\s+(?:on|in)\s+(?:my|the)\s+(?:screen|display|window)|"
            r"what am i looking at|what(?:'s| is) (?:in )?focus|which (?:window|app) (?:is )?(?:open|active|in focus))\b",
            re.I,
        ),
        "get_active_window",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:what (?:did|have) we (?:get done|done|achieved?|accomplished?)|what got done|what(?:'s| is) done|"
            r"(?:show|list)(?: me)? (?:today'?s|our|the) wins|today'?s wins|what did i get done)\b",
            re.I,
        ),
        "mission_status",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:go to|navigate to|browse to|visit|open (?:the )?(?:website|url|site))\s+(.+)$", re.I),
        "browser_open",
        lambda m: _goto_args(m.group(1)),
    ),
    # ── file mutation ──
    # "move X to Y" and "copy X to Y" are the same sentence with one verb
    # changed, which the embedding matcher cannot reliably separate; both are
    # rigid enough to match deterministically instead.
    (
        re.compile(
            r"^(?:rename|call)\s+(?:the\s+|that\s+|this\s+|my\s+)?(?P<what>.+?)\s+"
            r"(?:to|as)\s+(?P<name>[^\s].*)$",
            re.I,
        ),
        "rename_file",
        lambda m: {
            "path": m.group("what").strip(" .!?,"),
            "new_name": m.group("name").strip(" .!?,"),
        },
    ),
    (
        re.compile(
            r"^(?:move|shift|put)\s+(?:the\s+|that\s+|this\s+|my\s+)?(?P<what>.+?)\s+"
            r"(?:to|into|in|onto)\s+(?:the\s+|my\s+)?(?P<where>[^\s].*)$",
            re.I,
        ),
        "move_file",
        lambda m: {
            "path": m.group("what").strip(" .!?,"),
            "destination": m.group("where").strip(" .!?,"),
        },
    ),
    (
        re.compile(
            r"^(?:copy|duplicate|back\s+up)\s+(?:the\s+|that\s+|this\s+|my\s+)?(?P<what>.+?)\s+"
            r"(?:to|into|in|onto)\s+(?:the\s+|my\s+)?(?P<where>[^\s].*)$",
            re.I,
        ),
        "copy_file",
        lambda m: {
            "path": m.group("what").strip(" .!?,"),
            "destination": m.group("where").strip(" .!?,"),
        },
    ),
    (
        re.compile(r"^(?:create|make|new)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\s+(?:called\s+|named\s+)?(.+)$", re.I),
        "create_folder",
        lambda m: {"path": m.group(1).strip().strip("'\"")},
    ),
    (
        re.compile(r"^(?:what(?:'s| is)(?: on)?(?: my)?(?: the)?\s+)?(?:my\s+)?(?:agenda|schedule|day plan|daily plan|today'?s? plan|plan for today)\s*$", re.I),
        "get_agenda",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:plan my day|what do i have (?:on )?today|what am i doing today|show (?:me )?my tasks|my tasks|what'?s? on today)\s*$", re.I),
        "get_agenda",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:show|list|read)\s+(?:me\s+)?my\s+notes\s*$", re.I),
        "list_notes",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:run|execute|do|continue(?: with)?)\s+(?:the\s+)?next\s+step\s*$", re.I),
        "buzzcaf_execute_step",
        lambda m: {},
    ),
    # "remember X is Y" and "what do you remember about X" are close enough in
    # embedding space that Tier 1 confused storing with recalling. Split them
    # deterministically on the phrasing instead.
    (
        re.compile(r"^(?:what do you |do you )?remember\s+(?:about|what i told you about)\s+(.+)$", re.I),
        "recall_fact",
        lambda m: {"key": m.group(1).strip(" ?.")},
    ),
    (
        re.compile(
            r"^(?:remember|keep in mind|note|don'?t forget)\s+(?:that\s+)?(.+?)\s+(?:is|are|=|lives? (?:at|in)|will be)\s+(.+)$",
            re.I,
        ),
        "remember_fact",
        lambda m: {"key": m.group(1).strip(), "value": m.group(2).strip(" .")},
    ),
    # "open the studio" is the Buzzcaf Studio, not an installed app called
    # "studio"; it must win before the generic launcher below.
    (
        re.compile(r"^(?:add|put|create)\s+(?:a\s+|new\s+)?(?:task|todo|to-do|commitment)\s*[:\-]\s*(.+)$", re.I),
        "add_task",
        lambda m: {"title": m.group(1).strip(" .!")},
    ),
    # ── Claude Code's logs (v7) ──
    (
        re.compile(r"^(?:show\s+me\s+|tell\s+me\s+about\s+)?(?:the\s+)?(?:last|latest)\s+claude(?:\s+code)?\s+session\W*$|^what\s+was\s+claude'?s\s+last\s+(?:message|reply)\W*$", re.I),
        "claude_session",
        lambda m: {"which": "last"},
    ),
    (
        re.compile(r"^(?:what\s+happened\s+in|show\s+me|tell\s+me\s+about)\s+the\s+claude(?:\s+code)?\s+session\s+(?:on|about|for|in)\s+(.+?)\W*$", re.I),
        "claude_session",
        lambda m: {"which": m.group(1).strip()},
    ),
    (
        re.compile(
            r"^(?:what\s+(?:was|am)\s+i\s+doing\s+in\s+claude(?:\s+code)?|what\s+(?:did|has)\s+claude(?:\s+code)?\s+(?:do|done|change|been\s+(?:doing|working\s+on)|work\s+on)|"
            r"what\s+did\s+i\s+ask\s+claude(?:\s+code)?|claude(?:\s+code)?\s+sessions?)"
            r"(?:\s+(?:in|on|for)\s+(?P<project>[\w.-]+))?(?:\s+(?P<when>today|yesterday|this\s+week))?(?:\s+(?:in|on|for)\s+(?P<project2>[\w.-]+))?\W*$",
            re.I,
        ),
        "claude_activity",
        lambda m: {"when": (m.group("when") or "today").lower(), "project": m.group("project") or m.group("project2")},
    ),
    # ── activity awareness (v7) ──
    (
        re.compile(r"^(?:what\s+(?:am|was)\s+i\s+(?:doing|working on|on)(?:\s+(?:right\s+)?now)?|where\s+was\s+i|what\s+(?:did|do)\s+i\s+have\s+open|what\s+have\s+i\s+been\s+(?:doing|working\s+on))\W*$", re.I),
        "what_am_i_doing",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:what\s+did\s+i\s+(?:do|work\s+on)|how\s+did\s+i\s+spend\s+(?:my\s+)?(?:time|day)|how\s+was\s+my\s+day|time\s+by\s+app)\s*(today|yesterday|this\s+morning|this\s+week|week)?\W*$", re.I),
        "activity_summary",
        lambda m: {"when": ("this week" if (m.group(1) or "").lower() in ("week", "this week") else (m.group(1) or "today").lower())},
    ),
    # ── the mission (v7) ──
    (
        re.compile(r"^(?:you\s+)?(?:take|keep)\s+(?:the\s+)?(?:lead|initiative)(?:\s+again)?\W*$|^(?:you|dexter)\s+(?:lead|drive|can lead(?: again)?)\W*$|^(?:take|lead)\s+(?:it\s+)?from here\W*$", re.I),
        "set_initiative",
        lambda m: {"mode": "dexter"},
    ),
    (
        re.compile(r"^(?:i'?ll|i will|let me)\s+(?:take (?:it|over|the lead)|lead|drive)(?:\s+(?:from here|for now|now))?\W*$|^(?:stop|no more|quit)\s+(?:taking\s+)?(?:the\s+)?(?:initiative|initiatives|proposals|nudging|check-?ins)\W*$", re.I),
        "set_initiative",
        lambda m: {"mode": "me"},
    ),
    (
        re.compile(r"^(?:today'?s win is|the one thing today is|my win (?:for )?today(?: is)?:?|if i do one thing today it'?s)\s+(.+)$", re.I),
        "set_today_win",
        lambda m: {"text": m.group(1).strip(" .!")},
    ),
    (
        re.compile(r"^(?:our mission is|north star:?|the mission is|we'?re working towards|the big goal is)\s+(.+)$", re.I),
        "set_mission",
        lambda m: {"north_star": m.group(1).strip(" .!")},
    ),
    (
        re.compile(r"^(?:this week'?s focus is|focus this week:?|the focus (?:for|of) (?:the|this) week is|this week (?:we'?re|i'?m) (?:on|focused on))\s+(.+)$", re.I),
        "set_focus",
        lambda m: {"text": m.group(1).strip(" .!")},
    ),
    (
        re.compile(r"^(?:i'?m |i am )?(?:blocked (?:by|on)|stuck on|blocker:?)\s+(.+)$", re.I),
        "add_blocker",
        lambda m: {"text": m.group(1).strip(" .!")},
    ),
    (
        re.compile(r"^(?:log a win:?|small win:?|win:?|big win:?)\s+(.+)$", re.I),
        "log_win",
        lambda m: {"text": m.group(1).strip(" .!")},
    ),
    (
        re.compile(r"^(?:how are we doing|where are we(?: on the mission)?|what'?s (?:our|the) (?:plan|mission)|what are we working towards|status check on us)\W*$", re.I),
        "mission_status",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:not(?: now)?|leave me alone(?: for a bit)?|pause (?:the )?check-?ins|no check-?ins(?: for .*)?|i'?m in deep work)\W*$", re.I),
        "snooze_checkins",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:check in with me|let'?s check in|give me a check-?in|where do we stand(?: today)?)\W*$", re.I),
        "check_in",
        lambda m: {},
    ),
    # ── v13.2: search in the tab you are in. Ahead of the generic browser rules,
    # which all open a *new* tab - "search a video by its name in current tab"
    # had no implementation at all and fell through to the model.
    (
        re.compile(
            r"^(?:search|look\s+up|find)\s+(?:for\s+)?(?P<query>.+?)\s+"
            r"(?:in|on)\s+(?:the\s+)?(?:this|current|same)\s+(?:tab|page)\s*[.!?]*$"
            r"|^search\s+(?:this|the\s+current)\s+(?:tab|page)\s+for\s+(?P<query2>.+?)\s*[.!?]*$",
            re.I,
        ),
        "search_here",
        lambda m: _search_here_args(m.group("query") or m.group("query2")),
    ),
    (
        re.compile(
            r"^(?:search|look\s+up|find)\s+(?:for\s+)?(?P<query>.+?)\s+on\s+"
            r"(?P<site>youtube|google|google\s+images|images|maps|wikipedia|github|bing)"
            r"(?:\s+(?:in|on)\s+(?:the\s+)?(?:this|current|same)\s+(?:tab|page))?\s*[.!?]*$",
            re.I,
        ),
        "search_here",
        lambda m: _search_here_args(m.group("query"), m.group("site")),
    ),
    (
        re.compile(
            r"^(?:search(?:\s+for)?|find)\s+(?:a\s+video\s+(?:called|named|about|with|for)\s+|a\s+video\s+)?(?P<query>.+?)\s+"
            r"(?:on|in)\s+(?:the\s+)?(?:this|current|same)\s+(?:page|tab)\s*[.!?]*$",
            re.I,
        ),
        "search_here",
        lambda m: _search_here_args(m.group("query")),
    ),
    # "google X and open the first link" - one sentence, two acts, and the model
    # used to answer it by searching for the whole sentence.
    (
        re.compile(
            r"^(?:google|search(?:\s+(?:for|on))?|look\s+up|find)\s+(?P<query>.+?)\s+and\s+"
            r"(?:then\s+)?(?:open|click|play|pick|select)\s+(?:on\s+)?(?:the\s+)?"
            r"(?P<which>first|second|third|top|1st|2nd|3rd|\d{1,2})?\s*"
            r"(?:result|link|one|video)s?\s*[.!?]*$",
            re.I,
        ),
        "search_and_open",
        lambda m: _search_open_args(m.group("query"), m.group("which"), m.string),
    ),
    # ── v13: a specific video on YouTube. Ahead of the browser block because
    # "click the second video" is not a button called "the second video", and
    # ahead of the app launcher because "play the first one" is not an app.
    #
    # v13.2: "select" and "pick" joined the verbs (the owner's words were "select
    # the first video", which matched nothing), and the nouns split in two: a
    # video/result/link is a *page* result, while a profile/option/row/item is
    # anything on screen and goes to select_nth - Chrome's profile picker being
    # the case that prompted it.
    # Grid coordinates: "play video in row 1 column 2", "row 1 col 2", "open row 2 col 1", "click row 1 column 3"
    (
        re.compile(
            r"^(?:(?:play|open|start|watch|click(?:\s+on)?|pick|choose|select)\s+)?"
            r"(?:the\s+)?(?:video\s+)?(?:in|at)?\s*"
            r"row\s+(?P<row>\d{1,2})\s*(?:,|and|\s+)?\s*(?:column|col)\s+(?P<col>\d{1,2})"
            r"(?:\s+(?:on|in)\s+(?:this|the)\s+(?:page|screen|tab|list|grid))?\s*[.!?]*$",
            re.I,
        ),
        "youtube_play",
        lambda m: {"row": int(m.group("row")), "col": int(m.group("col"))},
    ),
    # Grid words: "first row second video", "second row first video", "1st row 3rd video"
    (
        re.compile(
            r"^(?:(?:play|open|start|watch|click(?:\s+on)?|pick|choose|select)\s+)?"
            r"(?:the\s+)?"
            r"(?P<r_word>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|1st|2nd|3rd|\d{1,2}(?:st|nd|rd|th)?)\s+row\s+"
            r"(?P<c_word>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|1st|2nd|3rd|\d{1,2}(?:st|nd|rd|th)?)\s+(?:col|column\s+)?video"
            r"(?:\s+(?:on|in)\s+(?:this|the)\s+(?:page|screen|tab|list|grid))?\s*[.!?]*$",
            re.I,
        ),
        "youtube_play",
        lambda m: _grid_video_args(m.group("r_word"), m.group("c_word")),
    ),
    # Numbers with "video" or "#": "play video 1", "play video 2", "open video 3", "select video 4", "video 5"
    (
        re.compile(
            r"^(?:play|open|start|watch|click(?:\s+on)?|pick|choose|select)\s+"
            r"(?:the\s+)?(?:video\s+(?:number\s+|#\s*)?|number\s+|#\s*)(?P<num>\d{1,2})"
            r"(?:\s+(?:on|in)\s+(?:this|the)\s+(?:page|screen|tab|list))?\s*[.!?]*$",
            re.I,
        ),
        "youtube_play",
        lambda m: {"position": int(m.group("num"))},
    ),
    # Direct numbers: "play 1", "play 2", "click 1", "open 3", "watch 2"
    (
        re.compile(
            r"^(?:play|watch|click(?:\s+on)?)\s+(?P<num>\d{1,2})"
            r"(?:\s+(?:on|in)\s+(?:this|the)\s+(?:page|screen|tab|list))?\s*[.!?]*$",
            re.I,
        ),
        "youtube_play",
        lambda m: {"position": int(m.group("num"))},
    ),
    # Short "video 1", "number 2"
    (
        re.compile(
            r"^(?:video|number)\s+(?P<num>\d{1,2})\s*[.!?]*$",
            re.I,
        ),
        "youtube_play",
        lambda m: {"position": int(m.group("num"))},
    ),
    (
        re.compile(
            r"^(?:play|open|start|watch|click(?:\s+on)?|pick|choose|select|go\s+to|tap|press|hit)\s+"
            r"(?:the\s+)?(?P<which>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|top|last|"
            r"1st|2nd|3rd|\d{1,2}(?:st|nd|rd|th)?)\s+"
            r"(?P<what>video|one|result|link|item|thumbnail|profile|account|option|row|entry|"
            r"card|suggestion|person)s?"
            r"(?:\s+(?:on|in)\s+(?:this|the)\s+(?:page|list|screen|tab|window|picker))?\s*[.!?]*$",
            re.I,
        ),
        "youtube_play",
        lambda m: _nth_args(m.group("which"), m.group("what"), "youtube_play"),
    ),
    (
        re.compile(
            r"^(?:play|open|start|watch|click(?:\s+on)?|pick|choose|select|go\s+to|tap|press|hit)\s+"
            r"(?:the\s+)?(?P<which>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|top|last|"
            r"1st|2nd|3rd|\d{1,2}(?:st|nd|rd|th)?)\s+"
            r"(?P<what>result|link)s?"
            r"(?:\s+(?:on|in)\s+(?:this|the)\s+(?:page|list|screen|tab|window))?\s*[.!?]*$",
            re.I,
        ),
        "open_result",
        lambda m: _nth_args(m.group("which"), m.group("what"), "open_result"),
    ),
    (
        re.compile(
            r"^(?:play|open|start|watch|click(?:\s+on)?|pick|choose|select|go\s+to|tap|press|hit)\s+"
            r"(?:the\s+)?(?P<which>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|top|last|"
            r"1st|2nd|3rd|\d{1,2}(?:st|nd|rd|th)?)\s+"
            r"(?P<what>item|profile|account|option|row|entry|card|suggestion|person)s?"
            r"(?:\s+(?:on|in)\s+(?:this|the)\s+(?:page|list|screen|tab|window|picker))?\s*[.!?]*$",
            re.I,
        ),
        "select_nth",
        lambda m: _nth_args(m.group("which"), m.group("what"), "select_nth"),
    ),
    (
        re.compile(
            r"^(?:what(?:'s| is)\s+on\s+this\s+page|list\s+(?:the\s+)?(?:results|links|options|videos)"
            r"|what\s+(?:results|links|options)\s+are\s+(?:there|on\s+(?:this|the)\s+page))\s*[.?!]*$",
            re.I,
        ),
        "page_results",
        lambda m: _page_results_args(m.string),
    ),
    # The things around a video: like, subscribe, comments, share, save.
    (
        re.compile(
            r"^(?:please\s+)?(?:can\s+you\s+)?"
            r"(?P<action>like|unlike|dislike|subscribe|unsubscribe|share|save|"
            r"(?:open|show|read)\s+(?:me\s+)?(?:the\s+)?comments?|comments?|"
            r"(?:show|open)\s+(?:the\s+)?description|thumbs\s+up|thumbs\s+down)"
            r"(?:\s+(?:to|from|this|that|it|the)\b.*)?\s*[.!?]*$",
            re.I,
        ),
        "youtube_page_action",
        lambda m: _yt_page_args(m.string),
    ),
    # Copy here, paste there.
    (
        re.compile(
            r"^copy\s+(?:this|that|the)?\s*(?P<what>selection|selected\s+text|text|paragraph|"
            r"section(?:\s+of\s+text)?|everything|all\s+of\s+(?:this|it)|page|line)?\s*"
            r"(?:of\s+text)?(?:\s+(?:on|from|in)\s+(?:this|the|here)\s*(?:page|tab|window|field)?)?"
            r"\s*[.!?]*$",
            re.I,
        ),
        "copy_text",
        lambda m: _copy_args(m.group("what")),
    ),
    (
        re.compile(
            r"^paste\s+(?:it|that|this|them)?\s*"
            r"(?:in(?:to)?\s+(?:the\s+)?(?P<field>(?:(?!\s+and\s+(?:send|submit|press|hit))[^,])+?))?"
            r"(?:\s+(?:in|on|of)\s+(?:the\s+)?(?P<tab>[\w .-]+?)\s+tab)?"
            r"(?P<send>\s+and\s+(?:send|submit|press\s+enter|hit\s+enter|enter)\s*(?:it)?)?\s*[.!?]*$",
            re.I,
        ),
        "paste_text",
        lambda m: _paste_args(m.group("field"), m.group("tab"), m.group("send")),
    ),
    (
        re.compile(
            r"^(?:play|open|watch|put\s+on)\s+(?:the\s+)?(?:youtube\s+)?(?:video|one|clip)\s+"
            r"(?:called|titled|named|about|with|that\s+says)\s+(.+?)\s*[.!?]*$",
            re.I,
        ),
        "youtube_play",
        lambda m: {"title": m.group(1).strip(" .!?\"'")} if m.group(1).strip() else None,
    ),
    (
        re.compile(
            r"^(?:what|which)\s+videos?\s+(?:are\s+)?(?:on|in)\s+(?:this|the)\s+(?:page|screen|list|tab)\s*[.!?]*$"
            r"|^(?:list|read|show)\s+(?:me\s+)?(?:the\s+)?videos?\s*(?:titles?)?\s*(?:on\s+(?:this|the)\s+(?:page|screen|tab))?\s*[.!?]*$"
            r"|^(?:list|show)\s+(?:me\s+)?the\s+youtube\s+results\s*[.!?]*$",
            re.I,
        ),
        "youtube_results",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:skip|dismiss|close)\s+(?:the\s+|this\s+)?ads?\s*[.!?]*$"
            r"|^(?:turn\s+on|enable|show|put\s+on)\s+(?:the\s+)?(?:captions|subtitles|cc)\s*[.!?]*$"
            r"|^(?:turn\s+off|disable|hide)\s+(?:the\s+)?(?:captions|subtitles|cc)\s*[.!?]*$"
            r"|^(?:next|previous|prev)\s+video\s*[.!?]*$"
            r"|^(?:play|make)\s+(?:it|this|the\s+video)\s+(faster|slower)\s*[.!?]*$"
            r"|^(?:skip\s+ahead|fast\s+forward|rewind)(?:\s+the\s+video)?\s*[.!?]*$",
            re.I,
        ),
        "youtube_control",
        lambda m: {"action": _video_action(m.string)},
    ),
    (
        re.compile(
            r"^(?:pause|resume|play|mute|unmute|full\s*screen)\s+(?:the\s+)?youtube(?:\s+video)?\s*[.!?]*$"
            r"|^(?:pause|play)\s+(?:the\s+)?video\s+on\s+youtube\s*[.!?]*$",
            re.I,
        ),
        "youtube_control",
        lambda m: {"action": _video_action(m.string)},
    ),
    # ── v11: keystrokes. Ahead of the browser block because "click/press/hit X"
    # belongs to browser_click, and "press control s" would be read as a button
    # called "control s". The first token must be a real key for this to fire.
    (
        re.compile(
            r"^(?:press|hit|send|tap)\s+(?:the\s+)?"
            r"((?:ctrl|control|alt|shift|win|windows|cmd|command|super|fn|"
            r"f\d{1,2}|esc|escape|enter|return|tab|space|spacebar|backspace|"
            r"delete|del|home|end|page\s*up|page\s*down|insert|print\s*screen)"
            r"(?:\s*\+\s*|\s+)?[\w+ ]*?)\s*(?:key|keys)?\s*[.!]*$",
            re.I,
        ),
        "press_keys",
        lambda m: {"keys": m.group(1).strip()} if m.group(1).strip() else None,
    ),
    # ── Dexter's browser (v6) ──
    (
        re.compile(r"^(?:search|look up|find)\s+(?:on\s+)?youtube\s+(?:for\s+)?(.+)$", re.I),
        "browser_open",
        lambda m: {"target": "youtube " + m.group(1).strip(" .!?,")},
    ),
    # "google best mic for voiceover" is a search in your browser; "google
    # calendar" is a site. _google_args tells them apart (v11, P1.3).
    (
        re.compile(r"^(?:google|search\s+google\s+for)\s+(.+)$", re.I),
        "browser_open",
        lambda m: _google_args(m.group(1)),
    ),
    (
        re.compile(r"^(?:read|summari[sz]e|what(?:'s| is| does)(?: on)?)\s+(?:this|the|that)\s+(?:page|tab|site|article|website)(?:\s+say)?\s*[.!?]*$", re.I),
        "browser_read_page",
        lambda m: {},
    ),
    # ── Desktop app click: "click [button/tab] in [app]" ──
    (
        re.compile(
            r"^(?:click|press|hit|tap)\s+(?:on\s+)?(?:the\s+)?"
            r"(?P<text>.+?)(?:\s+(?:button|tab|menu|item|link))?\s+"
            r"(?:in|on|inside)\s+(?:the\s+)?(?P<app>[^,]+?)\s*[.!?]*$",
            re.I,
        ),
        "click_in_app",
        lambda m: _click_in_app_args(m.group("text"), m.group("app")),
    ),
    # ── Desktop app typing: "type [text] into [field] in [app]" ──
    (
        re.compile(
            r"^(?:type|write|enter)\s+(?:['\"](?P<text1>.+?)['\"]|(?P<text2>.+?))\s+"
            r"(?:in|into|inside|on)\s+(?:the\s+)?(?P<into>.+?)(?:\s+(?:field|box|bar|input))?\s+"
            r"(?:in|on|inside)\s+(?:the\s+)?(?P<app>[^,]+?)(?:\s+(?:window|app))?\s*[.!?]*$",
            re.I,
        ),
        "type_text",
        lambda m: _type_into_in_app_args(m.group("text1") or m.group("text2"), m.group("into"), m.group("app")),
    ),
    # ── Desktop app typing: "type [text] in [app]" ──
    (
        re.compile(
            r"^(?:type|write|enter)\s+(?:['\"](?P<text1>.+?)['\"]|(?P<text2>.+?))\s+"
            r"(?:in|into|inside|on)\s+(?:the\s+)?(?P<app>[^,]+?)(?:\s+(?:window|app))?\s*[.!?]*$",
            re.I,
        ),
        "type_text",
        lambda m: _type_in_app_args(m.group("text1") or m.group("text2"), m.group("app")),
    ),
    (
        re.compile(r"^(?:click|press|hit|tap)\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+(?:button|link|tab))?\s*[.!?]*$", re.I),
        "browser_click",
        lambda m: {"target": m.group(1).strip()} if m.group(1).strip() else None,
    ),
    (
        re.compile(r"^(?:type|enter|write)\s+['\"]?(.+?)['\"]?\s+(?:in|into)\s+(?:the\s+)?(.+?)(?:\s+(?:field|box|bar|input))?(?:\s+and\s+(?:press|hit)\s+enter)?\s*[.!?]*$", re.I),
        "browser_type",
        lambda m: {"text": m.group(1).strip(), "into": m.group(2).strip(), "submit": bool(re.search(r"enter\s*[.!?]*$", m.string, re.I))},
    ),
    (
        re.compile(r"^(?:scroll|page)\s*(down|up|to the top|to the bottom|to top|to bottom)?\s*(?:a bit|a little|more)?\s*[.!?]*$", re.I),
        "browser_scroll",
        lambda m: {"direction": (m.group(1) or "down").replace("to the ", "").replace("to ", "")},
    ),
    (
        re.compile(r"^(?:go\s+)?back(?:\s+(?:a|one)\s+page|\s+to\s+the\s+previous\s+page)?\s*[.!?]*$", re.I),
        "browser_back",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:what|which|list|show)\s+(?:my\s+|the\s+)?(?:browser\s+)?tabs(?:\s+(?:are|do i have)\s*(?:open)?)?\s*[.!?]*$", re.I),
        "browser_tabs",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:switch|go|jump|move)\s+to\s+(?:the\s+)?(.+?)\s+tab\s*[.!?]*$|^(next|previous|prev)\s+tab\s*[.!?]*$", re.I),
        "browser_switch_tab",
        lambda m: {"which": (m.group(1) or m.group(2)).strip()},
    ),
    (
        re.compile(r"^(?:close|shut)\s+(?:this|the|current)\s+tab\s*[.!?]*$", re.I),
        "browser_close_tab",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:take\s+a\s+|grab\s+a\s+)?screenshot\s+(?:of\s+)?(?:this|the)\s+(?:page|tab|browser)\s*[.!?]*$", re.I),
        "browser_screenshot",
        lambda m: {},
    ),
    # ── v11: the rest of the browser (P1.1) and the desktop (P2) ──
    # After the v6 browser block on purpose: "type X into the search box" stays
    # browser_type, and only a bare "type X" becomes a keystroke sequence.
    (
        re.compile(r"^(?:reload|refresh)\s*(?:this|the|that|it)?\s*(?:page|tab)?\s*[.!]*$", re.I),
        "browser_reload",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:hard[\s-]?(?:reload|refresh))\s*(?:this|the)?\s*(?:page|tab)?\s*[.!]*$", re.I),
        "browser_reload",
        lambda m: {"hard": True},
    ),
    (
        re.compile(r"^(?:go\s+)?forward(?:\s+(?:a|one)\s+page)?\s*[.!]*$", re.I),
        "browser_forward",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:reopen|bring\s+back|undo\s+closing)\s+(?:the\s+|that\s+|my\s+)?(?:last\s+)?(?:closed\s+)?tab\s*[.!]*$", re.I),
        "browser_reopen_tab",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:open\s+)?(?:a\s+)?new\s+(?:browser|chrome)\s+(?:tab|window)\s*[.!]*$", re.I),
        "browser_new_tab",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:go|jump|scroll)\s+to\s+the\s+(top|bottom|start|end|beginning)"
            r"(?:\s+of\s+(?:the\s+)?(?:page|document|tab|file))?\s*[.!]*$",
            re.I,
        ),
        "browser_scroll",
        lambda m: {"direction": {"start": "top", "beginning": "top", "end": "bottom"}.get(
            m.group(1).lower(), m.group(1).lower())},
    ),
    (
        re.compile(r"^(?:go|switch|jump|move)\s+to\s+tab\s+(\d+)\s*[.!]*$", re.I),
        "browser_switch_tab",
        lambda m: {"which": m.group(1)},
    ),
    (
        re.compile(r"^(?:go|switch|jump|move)\s+to\s+the\s+(next|previous|last|first)\s+tab\s*[.!]*$", re.I),
        "browser_switch_tab",
        lambda m: {"which": m.group(1).lower()},
    ),
    (
        re.compile(
            r"^(?:find|search\s+for|look\s+for)\s+(?:the\s+word\s+)?(.+?)\s+on\s+(?:this|the)\s+page\s*[.!?]*$"
            r"|^search\s+(?:this|the)\s+page\s+for\s+(.+?)\s*[.!?]*$"
            r"|^ctrl\s*\+?\s*f\s+(?:for\s+)?(.+?)\s*[.!?]*$",
            re.I,
        ),
        "browser_find_in_page",
        lambda m: {"text": (m.group(1) or m.group(2) or m.group(3) or "").strip()},
    ),
    (
        re.compile(r"^zoom\s+(in|out)(?:\s+a\s+(?:bit|little))?(?:\s+on\s+(?:this|the)\s+(?:page|tab))?\s*[.!]*$", re.I),
        "browser_zoom",
        lambda m: {"direction": m.group(1).lower()},
    ),
    (
        re.compile(r"^reset\s+the\s+zoom\s*[.!]*$", re.I),
        "browser_zoom",
        lambda m: {"direction": "reset"},
    ),
    (
        re.compile(
            r"^(?:(?:make\s+)?(?:this|it|the\s+browser|chrome)\s+full\s*screen|"
            r"full\s*screen(?:\s+this)?|exit\s+full\s*screen|toggle\s+full\s*screen)\s*[.!]*$",
            re.I,
        ),
        "browser_fullscreen",
        lambda m: {},
    ),
    # Typing and keys.
    (
        re.compile(r"^(?:type(?:\s+out)?|write\s+out)\s+(.+)$", re.I),
        "type_text",
        lambda m: _type_args(m.group(1)),
    ),
    (
        re.compile(r"^write\s+['\"](.+)['\"]\s*$", re.I),
        "type_text",
        lambda m: _type_args(m.group(1)),
    ),
    # Windows and apps.
    (
        re.compile(r"^(?:switch|flip|jump)\s+to\s+(?:the\s+)?(.+?)\s*[.!]*$", re.I),
        "switch_to",
        lambda m: _switch_args(m.group(1)),
    ),
    (
        re.compile(
            r"^(?P<verb>minimi[sz]e|maximi[sz]e|restore)\s+(?:the\s+|my\s+)?"
            r"(?P<what>.+?)(?:\s+window)?\s*[.!]*$",
            re.I,
        ),
        "arrange_window",
        lambda m: _arrange_args(m.group("verb"), m.group("what")),
    ),
    (
        re.compile(
            r"^(?:snap|put|move|dock)\s+(?:the\s+|my\s+)?(?P<what>.+?)\s+(?:to|on)\s+the\s+"
            r"(?P<side>left|right)(?:\s+half|\s+side)?\s*[.!]*$",
            re.I,
        ),
        "arrange_window",
        lambda m: _snap_args(m.group("what"), m.group("side")),
    ),
    (
        re.compile(r"^(?:kill|force[\s-]?(?:quit|stop|close))\s+(?:the\s+|my\s+)?(.+?)\s*[.!]*$", re.I),
        "kill_app",
        lambda m: _close_app_args(m.group(1)),
    ),
    # Reading and listing.
    (
        re.compile(
            r"^(?:read|what\s+does)\s+(?:this|the|that)\s+(?:window|dialog|app|screen)(?:\s+say)?\s*[.!?]*$"
            r"|^read\s+what(?:'s| is)\s+on\s+(?:the\s+)?screen\s*[.!?]*$",
            re.I,
        ),
        "read_screen_text",
        lambda m: {},
    ),
    (
        re.compile(
            r"^(?:which|what)\s+apps?\s+have\s+windows?\s+open\s*[.!?]*$"
            r"|^list\s+(?:my\s+)?windows\s+by\s+app(?:lication)?\s*[.!?]*$",
            re.I,
        ),
        "list_windows",
        lambda m: {},
    ),
    # Per-app shortcuts (P2.2).
    (
        re.compile(r"^(?:open\s+)?(?:a\s+)?new\s+(tab|file|document|window)\s*[.!]*$", re.I),
        "app_shortcut",
        lambda m: {"action": "new " + ("file" if m.group(1).lower() == "document" else m.group(1).lower())},
    ),
    (
        re.compile(r"^toggle\s+(?:the\s+)?sidebar\s*[.!]*$", re.I),
        "app_shortcut",
        lambda m: {"action": "toggle sidebar"},
    ),
    (
        re.compile(r"^(?:open\s+|show\s+)?(?:the\s+)?command\s+palette\s*[.!]*$", re.I),
        "app_shortcut",
        lambda m: {"action": "command palette"},
    ),
    (
        re.compile(r"^save\s+(?:all|everything|the\s+file|this\s+file|the\s+document)\s*[.!]*$", re.I),
        "app_shortcut",
        lambda m: {"action": "save all" if re.match(r"^save\s+(?:all|everything)", m.string, re.I) else "save"},
    ),
    # ── buzzcode and the local engine (v6) ──
    (
        re.compile(r"^(?:ask\s+|tell\s+|have\s+)?(?:buzzcode|codebuzz|the\s+coder)\s*[:,]?\s+(?:to\s+)?(.+)$", re.I),
        "code_task",
        lambda m: {"task": m.group(1).strip()},
    ),
    (
        re.compile(r"^code\s*[:\-]\s*(.+)$", re.I),
        "code_task",
        lambda m: {"task": m.group(1).strip()},
    ),
    (
        re.compile(r"^(?:code|coding|buzzcode)\s+status\s*[.!?]*$|^(?:is|has)\s+(?:buzzcode|the\s+coder|the\s+code\s+(?:task|job))\s+(?:done|finished|ready).*$", re.I),
        "code_status",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:start|launch|boot(?:\s+up)?|bring\s+up|turn\s+on|load)\s+(?:the\s+)?(?:local\s+)?(?:model|engine|llm|llama[\s-]?server|buzzcode\s+engine|27b(?:\s+model)?)\s*[.!?]*$", re.I),
        "start_local_model",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:is\s+)?(?:the\s+)?(?:local\s+)?(?:model|engine|llama[\s-]?server)\s+(?:status|running|up|online)\s*[?.!]*$", re.I),
        "local_model_status",
        lambda m: {},
    ),
    # ── Studio video intelligence (v6) ──
    (
        re.compile(
            r"^(?:analy[sz]e|break\s*down|study|dissect|why\s+(?:is|does)\s+this\s+(?:video|channel)\s+(?:work|perform|do\s+well))"
            r"\s*(?:this\s+|that\s+|the\s+)?(?:video|channel|link|url)?\s*[:\-]?\s*(https?://\S+)(?:\s+(?:for|as|on)\s+(.+))?\s*[.!?]*$",
            re.I,
        ),
        "studio_analyze_video",
        lambda m: {"url": m.group(1).strip(), "channel": (m.group(2) or "").strip() or None},
    ),
    (
        re.compile(
            r"^(?:open|launch|start|run|fire up|boot up|bring up)\s+(?:up\s+)?(?:the\s+)?"
            r"(?:buzzcaf(?:ai)?\s*)?(?:studio|buzzcaf(?:ai)?)(?:\s+(?:app|studio|desktop|window))?\s*$",
            re.I,
        ),
        "buzzcaf_start_studio",
        lambda m: {},
    ),
    # ── v13.8: the generic "is it open?", last of all ──
    #
    # Deliberately at the end: every specific checker above answers better than a
    # window scan can. `local_model_status` knows the port and which model is loaded,
    # `app_status` knows the owner's own projects and whether one is mid-job - and
    # this rule was stealing both of them until it was moved down here.
    (
        re.compile(
            # Either "is X open" or "check if X is open": people put the subject on
            # whichever side of the verb the sentence started on.
            r"^(?:(?:can|could)\s+you\s+)?(?:please\s+)?"
            r"(?:(?:check|see|confirm|verify|tell\s+me)\s+(?:if|whether)\s+(?P<subject>.+?)\s+is\s+"
            r"|(?:check\s+)?(?:if\s+|whether\s+)?(?:is\s+|are\s+|do\s+i\s+have\s+|have\s+i\s+got\s+)"
            r"(?:the\s+|my\s+|a\s+|an\s+)?(?P<thing>.+?)\s+)(?:open|opened)"
            r"(?:\s+(?:right\s+now|anywhere|in\s+a\s+tab|in\s+any\s+tab|somewhere|"
            r"on\s+(?:my|this)\s+(?:pc|computer|screen)))?\s*[.!?]*$",
            re.I,
        ),
        "is_site_open",
        lambda m: _is_site_args(m.group("subject") or m.group("thing")),
    ),
    (
        re.compile(
            r"^(?:(?:can|could)\s+you\s+)?(?:please\s+)?"
            r"(?:(?:check|see|confirm|verify|tell\s+me)\s+(?:if|whether)\s+(?P<subject>.+?)\s+is\s+"
            r"|(?:check\s+)?(?:if\s+|whether\s+)?(?:is\s+|are\s+|do\s+i\s+have\s+|have\s+i\s+got\s+)"
            r"(?:the\s+|my\s+|a\s+|an\s+)?(?P<thing>.+?)\s+)(?:open|opened|running)"
            r"(?:\s+(?:right\s+now|anywhere|somewhere|on\s+(?:my|this)\s+(?:pc|computer|screen)))?"
            r"\s*[.!?]*$",
            re.I,
        ),
        "is_app_open",
        lambda m: _is_app_args(m.group("subject") or m.group("thing")),
    ),
    # v11 P1.3: site names are sites, and this rule sits **before** the app
    # launcher. "open youtube" used to mean a filesystem search for youtube.exe
    # and came back with "I couldn't find 'youtube' on this PC".
    (
        re.compile(
            r"^(?:open|go\s+to|goto|visit|browse\s+to|navigate\s+to|pull\s+up|bring\s+up|"
            r"take\s+me\s+to|launch|start|fire\s+up)\s+(?:up\s+)?(?:the\s+|my\s+)?(.+?)\s*[.!?]*$",
            re.I,
        ),
        "browser_open",
        lambda m: _site_args(m.group(1)),
    ),
    (
        re.compile(r"^(?:open|launch|start|run|fire up|boot up|bring up)\s+(.+)$", re.I),
        "launch_app",
        lambda m: {"app_name": m.group(1).strip()},
    ),
]

# Words that follow "open/start/run" but never name an application.
# v11 (P3.3): tabs, windows, pages and pronouns joined the list - "open a new
# tab" went to the app launcher and came back with "I couldn't find 'a new tab'".
_NOT_AN_APP = re.compile(
    r"^(?:a |an |the |my |new |another |this |that )?"
    r"(?:video|project|task|note|reminder|folder|file|step|workflow|meeting|"
    r"song|playlist|conversation|chat|over|again|"
    r"tab|window|page|site|website|url|link|search|dialog|menu|"
    r"it|this|that|them|these|those|one|everything|anything|something)\b",
    re.IGNORECASE,
)

# ───────────────────────── what "it" means (v11, P3.1) ─────────────────────────
#
# "open notepad" … "close it". Without a memory of what Dexter just did, "it" is
# unanswerable, and the model's guess is whatever window happens to be in front.
# So each session keeps one slot: the last thing Dexter opened or focused, for
# five minutes. Long enough for a follow-up, short enough that tomorrow's "close
# it" is not about yesterday's Notepad.

FOCUS_TTL_SECONDS = 300
_focus: Dict[str, Dict[str, Any]] = {}

#: tool -> (what kind of thing it leaves in focus, which argument names it).
_FOCUS_SETTERS: Dict[str, Tuple[str, Optional[str]]] = {
    "launch_app": ("app", "app_name"),
    "open_app": ("app", "app"),
    "switch_to": ("app", "app"),
    "focus_window": ("app", "title"),
    "buzzcaf_start_studio": ("app", None),
    "browser_open": ("tab", "target"),
    "open_website": ("tab", "url"),
    "browser_new_tab": ("tab", None),
    "browser_switch_tab": ("tab", "which"),
    "browser_reopen_tab": ("tab", None),
    "play_on_youtube": ("tab", "query"),
}
#: tools after which there is nothing left to refer to.
_FOCUS_CLEARERS = ("close_app", "kill_app", "stop_app", "browser_close_tab", "close_window")


def remember_focus(session: str, kind: str, name: str = "", extra: Optional[Dict[str, Any]] = None) -> None:
    now = time.time()
    entry = _focus.get(session) or {}
    clean_name = (name or "").strip()
    entry["kind"] = kind
    entry["name"] = clean_name
    entry["ts"] = now

    if kind == "app" and clean_name:
        entry["app"] = {"name": clean_name, "ts": now}
    elif kind == "tab":
        app_name = (extra or {}).get("app") or (entry.get("app") or {}).get("name", "")
        tab_kind = (extra or {}).get(
            "kind",
            "browser" if not app_name or app_name.lower() in ("chrome", "google chrome", "edge", "firefox", "browser") else "desktop",
        )
        entry["tab"] = {"name": clean_name or "new tab", "app": app_name, "kind": tab_kind, "ts": now}

    if extra:
        for k, v in extra.items():
            if k not in ("kind", "name", "ts"):
                entry[k] = v
    _focus[session] = entry


def remember_app_focus(session: str, app_name: str, title: str = "") -> None:
    now = time.time()
    entry = _focus.get(session) or {}
    clean_app = (app_name or "").strip()
    entry["kind"] = "app"
    entry["name"] = clean_app
    entry["ts"] = now
    entry["app"] = {"name": clean_app, "title": title, "ts": now}
    _focus[session] = entry


def remember_tab_focus(session: str, tab_name: str, app_name: str = "", kind: str = "desktop") -> None:
    now = time.time()
    entry = _focus.get(session) or {}
    clean_tab = (tab_name or "").strip()
    clean_app = (app_name or "").strip() or (entry.get("app") or {}).get("name", "")
    entry["kind"] = "tab"
    entry["name"] = clean_tab
    entry["ts"] = now
    entry["tab"] = {"name": clean_tab, "app": clean_app, "kind": kind, "ts": now}
    if clean_app and not entry.get("app"):
        entry["app"] = {"name": clean_app, "ts": now}
    _focus[session] = entry


def recall_focus(session: str = "default") -> Optional[Dict[str, Any]]:
    entry = _focus.get(session)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > FOCUS_TTL_SECONDS:
        _focus.pop(session, None)
        return None
    return entry


def recall_focused_app(session: str = "default") -> Optional[str]:
    entry = recall_focus(session)
    if not entry:
        return None
    app_entry = entry.get("app")
    if app_entry and (time.time() - app_entry.get("ts", 0) <= FOCUS_TTL_SECONDS):
        return app_entry.get("name")
    if entry.get("kind") == "app" and entry.get("name"):
        return entry.get("name")
    return None


def recall_focused_tab(session: str = "default") -> Optional[Dict[str, Any]]:
    entry = recall_focus(session)
    if not entry:
        return None
    tab_entry = entry.get("tab")
    if tab_entry and (time.time() - tab_entry.get("ts", 0) <= FOCUS_TTL_SECONDS):
        return tab_entry
    if entry.get("kind") == "tab":
        return {
            "name": entry.get("name", "new tab"),
            "app": (entry.get("app") or {}).get("name", ""),
            "kind": "browser",
        }
    return None


def forget_focus(session: str = "default") -> None:
    _focus.pop(session, None)


def focus_context_note(session: str = "default") -> str:
    """A natural briefing for the model on what app and tab Dexter recently touched."""
    entry = recall_focus(session)
    if not entry:
        return ""
    lines = []
    app = recall_focused_app(session)
    tab = recall_focused_tab(session)
    if app:
        lines.append(f"Recently active application: {app}")
    if tab and tab.get("name"):
        tab_app = tab.get("app") or app or "current window"
        lines.append(f"Recently active tab: '{tab['name']}' in {tab_app}")
    if lines:
        lines.append(
            "Instructions referring to 'it', 'this app', 'this tab', 'that tab', or actions like 'open a tab in it', "
            "'in this tab', 'go to <tab> tab', or clicking/typing in the app apply to this recently opened application and tab."
        )
    return "\n".join(lines)


def note_focus(session: str, tool_name: str, args: Dict[str, Any], ok: bool, data: Optional[Dict[str, Any]] = None) -> None:
    """Called after every executed tool: keep the referent up to date."""
    if not ok:
        return
    if tool_name in _FOCUS_CLEARERS:
        forget_focus(session)
        return

    args = args or {}
    if tool_name in ("launch_app", "open_app", "switch_to", "focus_window", "buzzcaf_start_studio"):
        app = args.get("app_name") or args.get("app") or args.get("title") or ("buzzcafai" if tool_name == "buzzcaf_start_studio" else "")
        if app:
            remember_app_focus(session, app)
        return

    if tool_name == "click_in_app":
        app = args.get("app", "") or recall_focused_app(session) or ""
        text = args.get("text", "")
        remember_tab_focus(session, text, app_name=app, kind="desktop")
        return

    if tool_name == "app_shortcut":
        action = (args.get("action", "") or "").lower()
        win = args.get("window", "") or recall_focused_app(session) or ""
        if "tab" in action:
            remember_tab_focus(session, action, app_name=win, kind="desktop")
        return

    if tool_name in ("browser_open", "open_website"):
        target = args.get("target") or args.get("url") or ""
        remember_tab_focus(session, target, app_name="chrome", kind="browser")
        return
    if tool_name == "browser_new_tab":
        remember_tab_focus(session, "new tab", app_name="chrome", kind="browser")
        return
    if tool_name == "browser_switch_tab":
        which = args.get("which", "")
        remember_tab_focus(session, which, app_name="chrome", kind="browser")
        return
    if tool_name == "play_on_youtube":
        query = args.get("query", "")
        remember_tab_focus(session, f"youtube {query}".strip(), app_name="chrome", kind="browser")
        return

    # The v13 YouTube tools: opening/searching sets the tab, and the ones that act
    # on the video already in front (play/pause, like, comment) keep YouTube as the
    # referent so "like it", "pause", "post it" all keep landing there.
    if tool_name in ("youtube_play", "youtube_results", "youtube_search_page"):
        q = args.get("query") or args.get("title") or ""
        remember_tab_focus(session, f"youtube {q}".strip(), app_name="chrome", kind="browser")
        return
    if tool_name in ("youtube_control", "youtube_page_action", "youtube_comment"):
        tab = recall_focused_tab(session)
        label = (tab or {}).get("name") or "youtube"
        remember_tab_focus(session, label, app_name="chrome", kind="browser")
        return
    if tool_name in ("search_here", "open_result", "search_and_open", "page_results"):
        remember_tab_focus(session, args.get("query") or "current page",
                           app_name="chrome", kind="browser")
        return

    setter = _FOCUS_SETTERS.get(tool_name)
    if not setter:
        return
    kind, key = setter
    name = str(args.get(key, "") or "") if key else ""
    remember_focus(session, kind, name)


_IT = r"(?:the\s+current\s+window|the\s+active\s+window|this\s+window|that\s+window|the\s+window|this\s+app|that\s+app|the\s+app|it|this|that)"
_THIS_TAB = r"(?:this|that|the|the\s+current|current)?\s*tab"
_CHAIN_WORD = re.compile(r"\b(?:and then|after that|and|then)\b", re.IGNORECASE)


def resolve_reference(text: str, session: str = "default") -> Optional[Tuple[str, Dict[str, Any]]]:
    """Turn "close it" / "close that tab" / "open a tab in it" into a real call.

    A tab is always a tab. A bare "it" is whatever Dexter last opened, and with
    no memory it is the window in focus - which the desktop tools resolve, and
    which is never Dexter's own window.
    """
    phrase = re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" .!?,")
    if not phrase:
        return None
    memory = recall_focus(session)
    recent_app = recall_focused_app(session)
    recent_tab = recall_focused_tab(session)

    # 1. Closing tab or app
    if re.match(rf"^(?:close|shut)\s+{_THIS_TAB}$", phrase):
        if recent_tab and recent_tab.get("kind") == "browser":
            return "browser_close_tab", {}
        if recent_app:
            return "app_shortcut", {"action": "close tab", "window": recent_app}
        return "browser_close_tab", {}

    if re.match(r"^(?:close|quit|shut\s*down|shut|exit)\s+(?:the\s+)?(?:browser|web\s*browser)$", phrase):
        return "close_app", {"name": "chrome"}

    if re.match(rf"^(?:close|shut)\s+(?:the\s+|that\s+|this\s+)?tab\s+(?:in|on)\s+{_IT}$", phrase) or re.match(
        rf"^(?:in|on)\s+{_IT}\s*,?\s*(?:close|shut)\s+(?:the\s+|that\s+|this\s+)?tab$", phrase
    ):
        if recent_app and recent_app.lower() in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser"):
            return "browser_close_tab", {}
        if recent_app:
            return "app_shortcut", {"action": "close tab", "window": recent_app}
        return "app_shortcut", {"action": "close tab"}

    if re.match(rf"^(?:close|quit|shut\s*down|shut|exit)\s+{_IT}$", phrase):
        if memory and memory.get("kind") == "tab":
            if (recent_tab and recent_tab.get("kind") == "browser") or not recent_app:
                return "browser_close_tab", {}
            return "app_shortcut", {"action": "close tab", "window": recent_app}
        if recent_app:
            return "close_app", {"name": recent_app}
        return "arrange_window", {"action": "close"}

    if re.match(rf"^(?:kill|force\s*(?:quit|stop|close))\s+{_IT}$", phrase):
        if recent_app:
            return "kill_app", {"name": recent_app}
        return None

    # 2. Window arrangements
    for verb, action in (("minimi[sz]e|hide", "minimize"),
                         ("maximi[sz]e|full\\s*size", "maximize"),
                         ("restore|unmaximi[sz]e", "restore")):
        if re.match(rf"^(?:{verb})\s+{_IT}$", phrase):
            return "arrange_window", {"action": action}
    if re.match(rf"^(?:snap|put|move)\s+{_IT}\s+(?:to|on)\s+the\s+left(?:\s+half|\s+side)?$", phrase):
        return "arrange_window", {"action": "snap_left"}
    if re.match(rf"^(?:snap|put|move)\s+{_IT}\s+(?:to|on)\s+the\s+right(?:\s+half|\s+side)?$", phrase):
        return "arrange_window", {"action": "snap_right"}

    m_scroll = re.match(r"^(?:scroll|page)\s+(down|up)\s+(?:here|in\s+(?:this|that)\s+(?:window|app))$", phrase)
    if m_scroll:
        return "scroll_window", {"direction": m_scroll.group(1)}

    if re.match(rf"^(?:read|what(?:'s| is) (?:on|in))\s+{_IT}$", phrase):
        if (recent_tab and recent_tab.get("kind") == "browser") or (memory and memory.get("kind") == "tab"):
            return "browser_read_page", {}
        if recent_app:
            return "copy_text_from", {"window": recent_app, "select_all": True}

    # 3. New tab in it ("open a tab in it", "open new tab in it", "new tab in it", "in it open a tab")
    if re.match(
        rf"^(?:(?:open\s+)?(?:a\s+)?(?:new\s+|another\s+)?tab\s+(?:in|on)\s+{_IT}|"
        rf"(?:in|on)\s+{_IT}\s*,?\s*(?:open\s+)?(?:a\s+)?(?:new\s+|another\s+)?tab)$",
        phrase,
    ):
        if recent_app and recent_app.lower() in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser"):
            return "browser_new_tab", {}
        if recent_app:
            return "app_shortcut", {"action": "new tab", "window": recent_app}
        return "app_shortcut", {"action": "new tab"}

    # 4. Go to / switch to / open specific named tab in an app or in it
    # ("go to the projects tab in it", "open goals tab in mission reminder", "switch to goals tab in machine reminder")
    m_app_tab = re.match(
        r"^(?:go\s+to|switch\s+to|open|jump\s+to|navigate\s+to|show)\s+(?:the\s+)?(?P<tab>.+?)\s+tab\s+(?:in|on)\s+(?:the\s+)?(?P<app>[^,]+)$",
        phrase,
    )
    if not m_app_tab:
        m_app_tab = re.match(
            r"^(?:in|on)\s+(?:the\s+)?(?P<app>[^,]+)\s*,?\s*(?:go\s+to|switch\s+to|open|jump\s+to|navigate\s+to|show)\s+(?:the\s+)?(?P<tab>.+?)(?:\s+tab)?$",
            phrase,
        )
    if m_app_tab:
        tab_name = m_app_tab.group("tab").strip()
        app_raw = m_app_tab.group("app").strip()
        if re.match(rf"^{_IT}$", app_raw, re.I):
            target_app = recent_app
        else:
            target_app = app_raw

        target_lower = (target_app or "").lower()
        if target_lower in ("mission reminder", "missionreminder", "machine reminder", "missing reminder", "messenger minder"):
            return "mission_go_to_tab", {"tab": tab_name}
        if target_lower in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser"):
            return "browser_switch_tab", {"which": tab_name}
        if tab_name.lower() in ("next", "next tab"):
            return "app_shortcut", {"action": "next tab", "window": target_app} if target_app else {"action": "next tab"}
        if tab_name.lower() in ("previous", "prev", "previous tab", "prev tab"):
            return "app_shortcut", {"action": "previous tab", "window": target_app} if target_app else {"action": "previous tab"}
        return "click_in_app", {"text": tab_name, "app": target_app} if target_app else {"text": tab_name}

    m_tab = re.match(
        rf"^(?:go\s+to|switch\s+to|open|jump\s+to|navigate\s+to)\s+(?:the\s+)?(?P<tab>.+?)\s+tab\s+(?:in|on)\s+{_IT}$",
        phrase,
    )
    if not m_tab:
        m_tab = re.match(
            rf"^(?:in|on)\s+{_IT}\s*,?\s*(?:go\s+to|switch\s+to|open|jump\s+to|navigate\s+to)\s+(?:the\s+)?(?P<tab>.+?)(?:\s+tab)?$",
            phrase,
        )
    if m_tab:
        tab_name = m_tab.group("tab").strip()
        if recent_app and recent_app.lower() in ("mission reminder", "missionreminder", "machine reminder", "missing reminder", "messenger minder"):
            return "mission_go_to_tab", {"tab": tab_name}
        if recent_app and recent_app.lower() in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser"):
            return "browser_switch_tab", {"which": tab_name}
        if tab_name.lower() in ("next", "next tab"):
            return "app_shortcut", {"action": "next tab", "window": recent_app} if recent_app else {"action": "next tab"}
        if tab_name.lower() in ("previous", "prev", "previous tab", "prev tab"):
            return "app_shortcut", {"action": "previous tab", "window": recent_app} if recent_app else {"action": "previous tab"}
        return "click_in_app", {"text": tab_name, "app": recent_app} if recent_app else {"text": tab_name}

    # 5. "go to the <name> tab" / "open <name> tab" when a desktop app is active and tab is not a known site
    m_bare_tab = re.match(
        r"^(?:switch|go|jump|move|open|show)\s+(?:to\s+)?(?:the\s+)?(?P<tab>.+?)\s+tab$",
        phrase,
    )
    if m_bare_tab:
        tab_name = m_bare_tab.group("tab").strip()
        if recent_app and recent_app.lower() in ("mission reminder", "missionreminder", "machine reminder", "missing reminder", "messenger minder"):
            return "mission_go_to_tab", {"tab": tab_name}
        if recent_app and recent_app.lower() not in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser") and not _is_known_site(tab_name):
            return "click_in_app", {"text": tab_name, "app": recent_app}

    # 6. Listing tabs in it ("what tabs are in it", "what tabs does it have", "list tabs in it")
    if re.match(
        rf"^(?:(?:what|which|list|show)\s+(?:the\s+|all\s+)?tabs\s+(?:are\s+)?(?:in|on|of)\s+{_IT}|"
        rf"what\s+tabs\s+does\s+{_IT}\s+have|"
        rf"(?:in|on)\s+{_IT}\s*,?\s*(?:what|which|list|show)\s+(?:the\s+|all\s+)?tabs(?:\s+are\s+there)?)$",
        phrase,
    ):
        if recent_app and recent_app.lower() in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser"):
            return "browser_tabs", {}
        if recent_app:
            return "list_controls", {"window": recent_app, "kind": "tab"}
        return "list_controls", {"kind": "tab"}

    return None


# ───────────────────────── "in chrome, …" (v11, P3.2) ─────────────────────────

_PREFIX_APPS = (
    r"chrome|google chrome|the browser|browser|edge|firefox|explorer|file explorer|"
    r"spotify|notepad|discord|premiere(?: pro)?|photoshop|vs ?code|visual studio code|"
    r"obs|word|excel|terminal|powershell"
)
_IN_APP = re.compile(rf"^in\s+(?P<app>{_PREFIX_APPS})\s*,?\s+(?P<rest>.+)$", re.IGNORECASE)
_IN_ANY = re.compile(r"^in\s+(?P<app>[\w .+#-]{2,24}?)\s*[,:]\s+(?P<rest>.+)$", re.IGNORECASE)
_APP_COLON = re.compile(rf"^(?P<app>{_PREFIX_APPS})\s*:\s*(?P<rest>.+)$", re.IGNORECASE)
_IN_PRONOUN = re.compile(
    r"^in\s+(?:this\s+app|that\s+app|the\s+app|this\s+window|that\s+window|the\s+window|it|this|that)\b\s*[,:]?\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
_IN_TAB_PRONOUN = re.compile(
    r"^(?:in|on)\s+(?:this|that|the|the\s+current|current)\s+tab\b\s*[,:]?\s*(?P<rest>.+)$",
    re.IGNORECASE,
)


def split_app_prefix(text: str, session: str = "default") -> Optional[Tuple[str, str]]:
    """'in chrome, go to gmail' -> ('chrome', 'go to gmail').
    Also supports 'in it, click settings' -> (recall_focused_app, 'click settings').
    """
    raw = (text or "").strip()
    m_pronoun = _IN_PRONOUN.match(raw)
    if m_pronoun:
        app = recall_focused_app(session)
        rest = m_pronoun.group("rest").strip(" ,.")
        if app and rest:
            return app, rest

    for pattern in (_IN_APP, _APP_COLON, _IN_ANY):
        match = pattern.match(raw)
        if not match:
            continue
        app = re.sub(r"^the\s+", "", match.group("app").strip(" ,:"), flags=re.I)
        rest = match.group("rest").strip(" ,.")
        if not (app and rest):
            continue
        # "in youtube, like this video" is a *tab*, not a window: the loose
        # comma form would otherwise claim it and then fail to focus a window
        # called youtube (v13.2). A name that is both - "edge", say - stays an app.
        if pattern is _IN_ANY and not _alias_is_app(app) and _is_known_site(app):
            return None
        return app, rest
    return None


def split_tab_prefix(text: str, session: str = "default") -> Optional[Tuple[str, str, str]]:
    """('browser' | 'desktop', target, rest) when instruction starts with 'in this tab, ...'."""
    raw = (text or "").strip()
    match = _IN_TAB_PRONOUN.match(raw)
    if not match:
        return None
    rest = match.group("rest").strip(" ,.")
    if not rest:
        return None
    tab_info = recall_focused_tab(session)
    if not tab_info:
        app = recall_focused_app(session)
        if app and app.lower() not in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser"):
            return "desktop", app, rest
        return "browser", "", rest
    kind = tab_info.get("kind", "desktop")
    name = tab_info.get("name", "")
    app = tab_info.get("app", "") or recall_focused_app(session) or ""
    return kind, (name if kind == "browser" else app), rest


def _is_known_site(name: str) -> bool:
    try:
        from browser import known_site

        return bool(known_site(name))
    except Exception:
        return False


def is_desktop_chain(text: str) -> bool:
    """"open youtube and search for lo-fi beats" - two acts in one sentence.

    These are the sentences the fast path used to swallow whole and the app
    launcher used to look up as one name. They belong to the agent loop, which
    can do one step at a time and report once.
    """
    raw = (text or "").strip()
    if not _CHAIN_WORD.search(raw):
        return False
    hits = re.findall(
        r"\b(?:open|close|launch|start|type|press|click|scroll|switch|search|google|save|copy|paste|"
        r"select|minimi[sz]e|maximi[sz]e|snap|reload|refresh|zoom|play|pause|"
        r"tab|window|browser|chrome|edge|youtube|gmail|reddit|github|page|notepad|spotify|code|explorer)\b",
        raw,
        re.IGNORECASE,
    )
    return len(hits) >= 2


# Greetings and pleasantries. Handled here rather than by the model for two
# reasons: they are said constantly and deserve an instant reply, and "how are
# you doing today" otherwise collides with the agenda tool on "doing/today".
_CHITCHAT: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:hi|hey|hello|yo|sup)\b.{0,15}$", re.I), "__greeting__"),
    (re.compile(r"^good (?:morning|afternoon|evening)\b.{0,15}$", re.I), "__greeting__"),
    (re.compile(r"^how (?:are|r) (?:you|u)\b.{0,20}$", re.I), "__greeting__"),
    (re.compile(r"^(?:what'?s up|how'?s it going|you there|are you there)\b.{0,15}$", re.I), "__greeting__"),
    (re.compile(r"^(?:thanks|thank you|cheers|nice one|perfect|great)\b.{0,15}$", re.I), "Anytime."),
    (re.compile(r"^(?:bye|goodbye|good night|goodnight|see you|later)\b.{0,15}$", re.I), "Talk soon."),
    (
        re.compile(
            r"^(?:(?:ok|okay|thanks|thank you|cheers|alright)[, ]+)?(?:dexter[, ]+)?"
            r"(?:that'?s (?:all|it|enough|everything)|that(?:\s+will|'ll)\s+be\s+all|that'?ll do|i'?m done|we'?re done|"
            r"i'?m (?:off|out|heading off|calling it)|signing off|logging off|good ?night|nothing else)\b.{0,25}$",
            re.I,
        ),
        "__signoff__",
    ),
    (re.compile(r"^(?:dexter|hey dexter)\s*[?!.]*$", re.I), "Yes? I'm listening."),
]


def chitchat(message: str) -> Optional[str]:
    text = message.strip()
    for pattern, reply in _CHITCHAT:
        if pattern.match(text):
            # A greeting from a colleague knows what today is about (v7).
            if reply == "__greeting__":
                return companion.greeting_reply()
            if reply == "__signoff__":
                return "Done for today. I'll keep an eye on things - talk tomorrow."
            return reply
    return None


def fast_path(message: str, session: str = "default") -> Optional[Tuple[str, Dict[str, Any]]]:
    """Deterministic matches for the verbs said many times a day. No LLM, no embeddings."""
    text = _clean(message)
    if not text:
        return None

    # "close it" / "minimize this" / "close that tab": resolved from what Dexter
    # last opened, before any rule gets to guess at a window title (v11, P3.1).
    reference = resolve_reference(text, session)
    if reference:
        return reference

    for pattern, tool_name, build_args in _FAST_RULES:
        match = pattern.match(text)
        if not match:
            continue
        args = build_args(match)
        if args is None:
            # This rule matched but could not build usable args - a later rule
            # or tier may still handle the phrase, so keep going.
            continue

        # "open github.com" is a website, not an application.
        if tool_name == "launch_app":
            target = args.get("app_name", "")
            if _URLISH.search(target) or _a_site(target):
                return "browser_open", {"target": target}
            # "start a video about X" is a Buzzcaf project, "run the next step"
            # is a workflow action - neither is an app. Let the later tiers decide.
            if _NOT_AN_APP.match(target):
                return None
            # v11 P3.3: the open-rule stops swallowing sentences. "open youtube
            # and search for lo-fi beats" was looked up as one application name,
            # which is where "I couldn't find 'youtube and search...'" came from.
            if _CHAIN_WORD.search(target):
                return None
            if len(target.split()) > 3:
                return None

        # Disambiguate browser vs desktop tools when active window is NOT Chrome
        if tool_name == "browser_click":
            target = args.get("target", "")
            recent_app = recall_focused_app(session)
            if recent_app and recent_app.lower() not in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser"):
                return "click_in_app", {"text": target, "app": recent_app}
            try:
                import desktop_control as dc
                win = dc.active_window()
                if win and win.app and win.app.lower() not in ("chrome", "edge", "firefox", "google chrome", "browser"):
                    return "click_text", {"text": target}
            except Exception:
                pass

        if tool_name == "browser_type":
            text_val = args.get("text", "")
            into_val = args.get("into", "")
            submit_val = args.get("submit", False)
            recent_app = recall_focused_app(session)
            try:
                import desktop_control as dc
                if into_val and dc._alias_of(into_val):
                    return "type_text", {"text": text_val, "window": into_val, "submit": submit_val}
                if recent_app and recent_app.lower() not in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser"):
                    return "type_text", {"text": text_val, "into": into_val, "submit": submit_val, "window": recent_app}
                win = dc.active_window()
                if win and win.app and win.app.lower() not in ("chrome", "edge", "firefox", "google chrome", "browser"):
                    return "type_text", {"text": text_val, "into": into_val, "submit": submit_val}
            except Exception:
                pass

        if tool_name == "app_shortcut" and args.get("action") == "new tab":
            recent_app = recall_focused_app(session)
            if recent_app and recent_app.lower() in ("chrome", "google chrome", "edge", "firefox", "browser", "the browser"):
                return "browser_new_tab", {}
            try:
                import desktop_control as dc
                win = dc.active_window()
                if win and win.app and win.app.lower() in ("chrome", "edge", "firefox", "google chrome", "browser"):
                    return "browser_new_tab", {}
            except Exception:
                pass

        return tool_name, args

    return None


# ───────────────────────── Tier 1: semantic match ─────────────────────────


class _Embedder:
    """
    sentence-transformers when available, lexical fallback when not.

    The fallback matters: it keeps intent matching working on a bare install
    before `pip install sentence-transformers`, just with lower recall.
    """

    def __init__(self) -> None:
        self._model = None
        self._mode = "pending"
        self._lock = threading.Lock()
        self._idf: Dict[str, float] = {}

    def _ensure(self) -> None:
        if self._mode != "pending":
            return
        with self._lock:
            if self._mode != "pending":
                return
            try:
                # Shared instance: memory.py embeds against the same model, so
                # loading here must not create a second copy.
                import embeddings as shared_embeddings

                model = shared_embeddings.get_model()
                if model is None:
                    raise RuntimeError("shared embedding model unavailable")
                self._model = model
                self._mode = "neural"
                print(f"[Router] Semantic matching: {shared_embeddings.MODEL_NAME}")
            except Exception as exc:
                self._mode = "lexical"
                print(f"[Router] sentence-transformers unavailable ({exc}); using lexical matching.")

    @property
    def mode(self) -> str:
        self._ensure()
        return self._mode

    # Function words carry no intent signal, but they are the tokens most likely
    # to be shared between an unrelated question and a tool description. Left in,
    # "what is the capital of France" scored 0.49 against buzzcaf_status purely on
    # "what/is/the/of". Dropping them makes an unrelated query score ~0.
    _STOPWORDS = frozenset("""
        a an the this that these those my your our their its his her it they them
        i me we us you he she is are was were be been being am do does did doing
        have has had having will would shall should can could may might must
        of in on at to for with from by about into over under again further
        and or but if then else when while as so than too very just also
        what which who whom whose where why how there here
        please thanks thank ok okay hey yeah yep
    """.split())

    @classmethod
    def _tokens(cls, text: str) -> List[str]:
        # Bare numbers are argument values, never intent. Left in, the literal
        # "40" inside the example "set brightness to 40" is a rare, high-IDF
        # token that pulled "set volume to 40" over to set_brightness.
        return [
            tok for tok in re.findall(r"[a-z0-9]+", text.lower())
            if tok not in cls._STOPWORDS and len(tok) > 1 and not tok.isdigit()
        ]

    def fit_lexical(self, corpus: List[str]) -> None:
        doc_count = len(corpus) or 1
        seen = Counter()
        for phrase in corpus:
            for token in set(self._tokens(phrase)):
                seen[token] += 1
        self._idf = {
            token: math.log((doc_count + 1) / (count + 1)) + 1.0
            for token, count in seen.items()
        }

    def encode(self, texts: List[str]):
        self._ensure()
        if self._mode == "neural":
            return self._model.encode(texts, normalize_embeddings=True)  # type: ignore
        return [self._lexical_vector(t) for t in texts]

    def _lexical_vector(self, text: str) -> Dict[str, float]:
        tokens = self._tokens(text)
        if not tokens:
            return {}
        vec: Dict[str, float] = {}
        for token in tokens:
            vec[token] = vec.get(token, 0.0) + self._idf.get(token, 1.0)
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {k: v / norm for k, v in vec.items()}

    def similarity(self, query_vec, doc_vec) -> float:
        if self._mode == "neural":
            return float(sum(a * b for a, b in zip(query_vec, doc_vec)))
        if not query_vec or not doc_vec:
            return 0.0
        smaller, larger = (
            (query_vec, doc_vec) if len(query_vec) < len(doc_vec) else (doc_vec, query_vec)
        )
        return float(sum(weight * larger.get(token, 0.0) for token, weight in smaller.items()))


_embedder = _Embedder()

# example phrase -> tool name, plus the encoded matrix
_example_tools: List[str] = []
_example_vectors: List[Any] = []
_tool_vocab: Dict[str, set] = {}
_index_ready = False
_index_lock = threading.Lock()

# A short query shares one incidental word with a tool and scores high: "tell me
# a joke" hit recall_fact purely on "tell" (from "what did I tell you about..."),
# and "write a haiku" hit create_note on "write". Demanding two overlapping
# content words costs nothing and removes that whole class of false positive.
MIN_TOKEN_OVERLAP = 2

SEMANTIC_THRESHOLD = 0.62
LEXICAL_THRESHOLD = 0.55


def build_intent_index(force: bool = False) -> None:
    """Encode every tool's example phrasings once. Called at startup."""
    global _example_tools, _example_vectors, _index_ready

    with _index_lock:
        if _index_ready and not force:
            return

        phrases: List[str] = []
        owners: List[str] = []
        for spec in all_tools():
            for example in spec.examples:
                phrases.append(example)
                owners.append(spec.name)
            # The description carries signal the examples may miss.
            phrases.append(spec.description)
            owners.append(spec.name)

        if not phrases:
            _index_ready = True
            return

        # Vocabulary per tool, used to demand real overlap rather than a single
        # incidental shared word (see semantic_match).
        _tool_vocab.clear()
        for phrase, owner in zip(phrases, owners):
            _tool_vocab.setdefault(owner, set()).update(_Embedder._tokens(phrase))

        if _embedder.mode == "lexical":
            _embedder.fit_lexical(phrases)

        _example_vectors = list(_embedder.encode(phrases))
        _example_tools = owners
        _index_ready = True
        print(f"[Router] Intent index built: {len(phrases)} phrases over {len(all_tools())} tools.")


def rank_tools(message: str, top_k: int = 8) -> List[Tuple[str, float]]:
    """Best-scoring tools for a message, highest first. Used for Tier 1 and to
    shrink the tool list handed to the model in Tier 2."""
    build_intent_index()
    if not _example_vectors:
        return []

    query = _embedder.encode([message])[0]
    best: Dict[str, float] = {}
    for tool_name, vector in zip(_example_tools, _example_vectors):
        score = _embedder.similarity(query, vector)
        if score > best.get(tool_name, -1.0):
            best[tool_name] = score

    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:top_k]


def semantic_match(message: str) -> Optional[Tuple[str, float]]:
    ranked = rank_tools(message, top_k=3)
    if not ranked:
        return None
    threshold = SEMANTIC_THRESHOLD if _embedder.mode == "neural" else LEXICAL_THRESHOLD
    name, score = ranked[0]
    if score < threshold:
        return None
    # Require a margin over the runner-up, otherwise it is a coin flip and the
    # agent loop should decide instead.
    if len(ranked) > 1 and (score - ranked[1][1]) < 0.04:
        return None

    query_tokens = set(_Embedder._tokens(message))
    if len(query_tokens) >= MIN_TOKEN_OVERLAP:
        overlap = len(query_tokens & _tool_vocab.get(name, set()))
        if overlap < MIN_TOKEN_OVERLAP:
            return None

    return name, score


# ───────────────────────── slot extraction ─────────────────────────

_VERB_STRIP = re.compile(
    r"^(?:can you|could you|please|hey|ok|okay)?\s*"
    r"(?:go and |just )?"
    r"(?:open|launch|start|run|play|put on|find|search for|search|look for|locate|"
    r"create|make|add|write|take|jot|note|show|list|read|get|give me|tell me)\s+"
    r"(?:me\s+)?(?:a\s+|an\s+|the\s+|some\s+|my\s+)?",
    re.IGNORECASE,
)


# Spoken phrasings mapped onto media_control's enum. Without this, Tier 1 passed
# the whole utterance ("pause the music") as the enum value and validation
# rejected it.
_MEDIA_ACTIONS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:skip|next|forward|advance)\b", re.I), "next"),
    (re.compile(r"\b(?:previous|last|back|go back|rewind|replay)\b", re.I), "previous"),
    (re.compile(r"\b(?:pause|hold on|freeze)\b", re.I), "pause"),
    (re.compile(r"\b(?:resume|unpause|continue|carry on|keep playing)\b", re.I), "play"),
    (re.compile(r"\bstop\b", re.I), "stop"),
    (re.compile(r"\bplay\b", re.I), "play"),
]


_STEP = 10
_RE_LEVEL = re.compile(r"\b(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?\b|\b(\d{1,3})\s*(?:%|percent)\b", re.I)
_RE_UP = re.compile(r"\b(up|louder|higher|brighter|increase|raise|more)\b", re.I)
_RE_DOWN = re.compile(r"\b(down|quieter|lower|softer|dimmer|dim|decrease|reduce|less)\b", re.I)


def _level_slots(tool_name: str, text: str) -> Optional[Dict[str, Any]]:
    """Parse "set volume to 25", "turn it up", "mute" into the right arguments."""
    lowered = text.lower()

    if tool_name == "set_volume":
        if re.search(r"\bunmute\b|\bsound (?:back )?on\b", lowered):
            return {"mute": False}
        if re.search(r"\bmute\b|\bsilence\b|\bsound off\b|\bshut the sound\b", lowered):
            return {"mute": True}

    if re.search(r"\bmax(?:imum)?\b|\bfull\b|\ball the way up\b", lowered):
        return {"level": 100}
    if re.search(r"\bmin(?:imum)?\b|\ball the way down\b", lowered):
        return {"level": 0}

    match = _RE_LEVEL.search(lowered)
    if match:
        value = int(match.group(1) or match.group(2))
        if 0 <= value <= 100:
            return {"level": value}

    if _RE_UP.search(lowered):
        return {"change": _STEP}
    if _RE_DOWN.search(lowered):
        return {"change": -_STEP}

    # A bare "volume?" is a query - the tool reports the current level.
    return {}


def _enum_slot(spec, param: str, text: str) -> Optional[str]:
    """Pick an enum value that the utterance actually names."""
    options = spec.params[param].get("enum") or []
    lowered = text.lower()
    if param == "action" and spec.name == "media_control":
        for pattern, value in _MEDIA_ACTIONS:
            if pattern.search(lowered) and value in options:
                return value
    for option in options:
        if re.search(rf"\b{re.escape(option)}\b", lowered):
            return option
    return None


def _primary_param(spec) -> Optional[str]:
    required = spec.required
    if len(required) == 1:
        name = required[0]
        if spec.params[name].get("type") == "string":
            return name
    return None


# ───────────────────────── workbook slots ─────────────────────────

# "I'm committing to ...", "put me down for ...", "hold me to ..."
_RE_COMMIT_LEAD = re.compile(
    r"^(?:i(?:'m| am)?\s+)?(?:committing|commit)\s+(?:me\s+)?(?:to\s+)?"
    r"|^put\s+me\s+down\s+(?:for\s+)?"
    r"|^hold\s+me\s+to\s+"
    r"|^add\s+a\s+commitment\s+(?:to\s+)?"
    r"|^(?:i\s+)?(?:will|'ll)\s+"
    r"|^i\s+am\s+going\s+to\s+",
    re.IGNORECASE,
)

# "I finished ...", "done with ...", "mark ... as done", "check off ..."
_RE_KEPT_LEAD = re.compile(
    r"^(?:i\s+)?(?:have\s+|just\s+)?(?:finished|completed|did|done\s+with)\s+"
    r"|^mark\s+(?:the\s+|my\s+)?"
    r"|^check\s+off\s+(?:the\s+|my\s+)?",
    re.IGNORECASE,
)
_RE_KEPT_TAIL = re.compile(
    r"\s+(?:as\s+)?(?:done|complete|completed|finished|off)\s*$", re.IGNORECASE
)

# "push X to tomorrow", "move X to Friday", "bump X to Monday"
_RE_MOVE = re.compile(
    r"^(?:push|move|bump|reschedule|postpone|shift)\s+(?:the\s+|my\s+)?(?P<what>.+?)"
    r"(?:\s+(?:to|until|till)\s+(?P<when>.+?))?\s*$",
    re.IGNORECASE,
)

# "log that I worked 90 minutes on X", "note that I'm blocked on Y"
_RE_LOG_LEAD = re.compile(
    r"^(?:log|note|record)\s+(?:that\s+|some\s+progress\s+(?:on\s+)?)?"
    r"|^make\s+a\s+log\s+(?:that\s+)?",
    re.IGNORECASE,
)
_RE_MINUTES = re.compile(
    r"\b(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE
)

# "my goal is to X", "set a goal to X"
_RE_GOAL_LEAD = re.compile(
    r"^(?:my\s+)?(?:new\s+)?goal(?:\s+this\s+\w+)?\s+is\s+(?:to\s+)?"
    r"|^set\s+a\s+goal\s+(?:to\s+|of\s+)?"
    r"|^new\s+goal\s*:?\s*"
    r"|^i\s+want\s+to\s+work\s+towards\s+",
    re.IGNORECASE,
)

_RE_TRAILING_WHEN = re.compile(
    r"\s+(?:by|on|before|due)\s+(?P<when>today|tomorrow|tonight|next\s+week|"
    r"this\s+week|end\s+of\s+(?:the\s+)?week|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday|in\s+\d+\s+days?)\s*$",
    re.IGNORECASE,
)


def _split_due(text: str):
    """('script episode three', 'friday') from 'script episode three by friday'."""
    match = _RE_TRAILING_WHEN.search(text)
    if not match:
        # A bare trailing "tomorrow"/"today" with no preposition is just as common.
        bare = re.search(
            r"\s+(today|tomorrow|tonight|next\s+week|this\s+week|monday|tuesday|"
            r"wednesday|thursday|friday|saturday|sunday)\s*$",
            text, re.IGNORECASE,
        )
        if bare:
            return text[: bare.start()].strip(" .!?,"), bare.group(1)
        return text.strip(" .!?,"), ""
    return text[: match.start()].strip(" .!?,"), match.group("when")


def _workbook_slots(tool_name: str, text: str):
    """Arguments for the workbook tools, or None to let Tier 2 reason it out."""
    if tool_name == "my_commitments":
        lowered = text.lower()
        if "week" in lowered:
            return {"scope": "week"}
        if any(word in lowered for word in ("all", "everything", "outstanding")):
            return {"scope": "all"}
        return {"scope": "today"}

    if tool_name == "make_commitment":
        body = _RE_COMMIT_LEAD.sub("", text).strip(" .!?,")
        if not body:
            return None
        what, when = _split_due(body)
        return {"text": what, "when": when} if what else None

    if tool_name == "keep_commitment":
        body = _RE_KEPT_LEAD.sub("", text)
        body = _RE_KEPT_TAIL.sub("", body).strip(" .!?,")
        return {"text": body} if body else None

    if tool_name == "reschedule_commitment":
        match = _RE_MOVE.match(text)
        if not match:
            return None
        what = (match.group("what") or "").strip(" .!?,")
        when = (match.group("when") or "tomorrow").strip(" .!?,")
        return {"text": what, "when": when} if what else None

    if tool_name == "log_progress":
        body = _RE_LOG_LEAD.sub("", text).strip(" .!?,")
        if not body:
            return None
        args = {"text": body}
        minutes = _RE_MINUTES.search(body)
        if minutes:
            value = float(minutes.group("num"))
            if minutes.group("unit").lower().startswith(("hour", "hr")):
                value *= 60
            args["minutes"] = int(value)
        return args

    if tool_name == "set_goal":
        body = _RE_GOAL_LEAD.sub("", text).strip(" .!?,")
        if not body:
            return None
        title, by = _split_due(body)
        return {"title": title, "by": by} if title else None

    return None


def _extract_slots(tool_name: str, message: str) -> Optional[Dict[str, Any]]:
    """
    Fill a tool's arguments from the raw utterance without calling the model.
    Returns None when the arguments need real reasoning.
    """
    spec = get_tool(tool_name)
    if not spec:
        return None

    text = _clean(message)

    # The shell tool takes a *composed* command, never the sentence itself:
    # "what's my ip address" is not PowerShell. Only a model fills this one.
    if tool_name == "run_command":
        return None

    # Tool-specific handlers run before the "no required args" shortcut below,
    # because set_volume and set_brightness take only optional arguments yet
    # still need them parsed - otherwise "set volume to 25" would call the tool
    # with nothing and merely report the current level.
    if tool_name in ("set_volume", "set_brightness"):
        return _level_slots(tool_name, text)

    workbook_args = _workbook_slots(tool_name, text)
    if workbook_args is not None:
        return workbook_args

    if not spec.required:
        # Nothing required means the tool is callable as-is; optional args can
        # stay at their defaults rather than forcing an LLM round trip.
        return {}

    if tool_name == "complete_task":
        title = re.sub(
            r"^(?:mark|check|tick)\s+(?:off\s+)?(?:the\s+|my\s+)?", "", text, flags=re.I
        )
        title = re.sub(
            r"\s+(?:as\s+)?(?:done|complete|completed|finished|off)\s*$", "", title, flags=re.I
        )
        title = re.sub(
            r"^(?:i\s+)?(?:have\s+|just\s+)?(?:finished|completed|did|done with)\s+(?:the\s+|my\s+)?",
            "", title, flags=re.I,
        )
        title = re.sub(r"\s+task\s*$", "", title, flags=re.I).strip(" .!?,")
        return {"title": title} if title else None

    if tool_name == "create_reminder":
        return _reminder_args(text)

    if tool_name == "cancel_reminder":
        body = re.sub(
            r"^(?:cancel|delete|remove|forget|drop)\s+(?:the\s+|my\s+|that\s+)?"
            r"reminder(?:\s+(?:about|for|to|regarding))?\s*",
            "", text, flags=re.I,
        ).strip(" .!?,")
        return {"text": body} if body else None

    if tool_name == "add_task":
        from timeparse import parse_when, strip_time_phrases

        title = strip_time_phrases(text)
        title = _VERB_STRIP.sub("", title).strip()
        if not title:
            return None
        args: Dict[str, Any] = {"title": title}
        if parse_when(text):
            args["when"] = text
        return args

    if tool_name == "create_note":
        body = re.sub(
            r"^(?:i\s+)?(?:need|want)\s+to\s+", "", text, flags=re.IGNORECASE
        )
        body = re.sub(
            r"^(?:take a note|make a (?:sticky )?note|write (?:this )?down|jot (?:this )?down|"
            r"capture this|note that|note|remember that)\s*(?:about|that|:)?\s*",
            "", body, flags=re.IGNORECASE,
        ).strip(" .!?,")
        return {"content": body} if body else None

    primary = _primary_param(spec)
    if not primary:
        return None

    if "enum" in spec.params[primary]:
        value = _enum_slot(spec, primary, text)
        return {primary: value} if value else None

    stripped = _VERB_STRIP.sub("", text).strip(" .!?,")
    if not stripped or len(stripped) > 120:
        return None
    return {primary: stripped}


# ───────────────────────── confirmation for risky tools ─────────────────────────

# Keyed by session so one user's stale "yes" can never fire another session's
# forgotten destructive call; entries expire so an answer given minutes later
# does not trigger an action the user no longer remembers being asked about.
_PENDING_TTL_SECONDS = 120
_pending: Dict[str, Dict[str, Any]] = {}
_AFFIRMATIVE = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|confirm|please do)\b", re.I)
_NEGATIVE = re.compile(r"^\s*(no|nope|cancel|stop|don'?t|nevermind|never mind)\b", re.I)


def _remember_pending(tool_name: str, args: Dict[str, Any], session: str = "default") -> None:
    _pending[session] = {"tool": tool_name, "args": args, "ts": time.time()}


def _has_pending(session: str = "default") -> bool:
    entry = _pending.get(session)
    if not entry:
        return False
    if time.time() - entry["ts"] > _PENDING_TTL_SECONDS:
        _pending.pop(session, None)
        return False
    return True


def _take_pending(session: str = "default") -> Optional[Tuple[str, Dict[str, Any]]]:
    entry = _pending.pop(session, None)
    if not entry or time.time() - entry["ts"] > _PENDING_TTL_SECONDS:
        return None
    return entry["tool"], entry.get("args", {})


def _describe_call(tool_name: str, args: Dict[str, Any]) -> str:
    if not args:
        return tool_name.replace("_", " ")
    rendered = ", ".join(f"{k}={str(v)[:60]}" for k, v in args.items())
    return f"{tool_name.replace('_', ' ')} ({rendered})"


# Observers notified after each executed tool call: (session, tool, args, ok).
# Macro recording hooks in here; observers must never raise into the turn.
TOOL_OBSERVERS: List[Callable[[str, str, Dict[str, Any], bool], None]] = []


def _run_tool(
    tool_name: str,
    args: Dict[str, Any],
    confirmed: bool = False,
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
) -> Tuple[ToolResult, bool]:
    """Execute, unless the tool is marked confirm and the user has not agreed yet."""
    spec = get_tool(tool_name)
    if spec and not confirmed and spec.needs_confirmation(args):
        _remember_pending(tool_name, args, session=session)
        # A tool that knows *why* it is asking words its own question; stopping
        # a render deserves "VrDeep has 3 jobs queued", not "stop app (app=...)".
        question = spec.confirmation_prompt(args)
        return (
            ToolResult(
                ok=True,
                summary=question or f"Just to confirm - you want me to {_describe_call(tool_name, args)}?",
                data={"pending": True},
            ),
            True,
        )

    if on_event:
        on_event("tool_start", {"name": tool_name, "args": args})
    # Session-scoped tools (dictation) read this rather than taking a parameter.
    import tool_registry as _registry

    token = _registry.set_session(session)
    try:
        result = execute(tool_name, args)
    finally:
        _registry.reset_session(token)
    if on_event:
        on_event("tool_result", {"name": tool_name, "ok": result.ok, "summary": result.summary})
    # What "it" means next time (v11, P3.1). Never allowed to break a turn.
    try:
        note_focus(session, tool_name, args, result.ok)
    except Exception:
        pass
    for observer in TOOL_OBSERVERS:
        try:
            observer(session, tool_name, args, result.ok)
        except Exception:
            pass
    return result, False


def _speech_hint(result: ToolResult) -> Dict[str, Any]:
    """Let a tool ask for a particular delivery, e.g. read_aloud wants narration."""
    emotion = result.data.get("_speech_emotion") if isinstance(result.data, dict) else None
    return {"speechEmotion": emotion} if emotion else {}


# ───────────────────────── Tier 2: constrained agent loop ─────────────────────────

MAX_ITERATIONS = 5
TOOLS_PER_CALL = 8

# ── Tiered models: a small fast model answers simple turns instantly and hands
# off ("escalates") to a bigger model for anything needing tools/actions. Both
# are optional; unset DEXTER_FAST_MODEL disables the tier (single-model as
# before). SMART model, when unset, falls back to the provider's default.
#: v13, D1: `qwen3.5:4b`. Measured against six others on this card — 27 of 30
#: tool choices right, 111 tok/s, 3.0 GB — it is the first small model good
#: enough to be *handed the tools* rather than only asked to step aside. An
#: explicitly blank DEXTER_FAST_MODEL still switches the whole tier off.
FAST_MODEL = os.getenv("DEXTER_FAST_MODEL", "qwen3.5:4b").strip()
SMART_MODEL = os.getenv("DEXTER_SMART_MODEL", "gemma-4-12b").strip()
# v12, D1: a ladder, not one fixed model. The top rung is the better model the
# owner would like every escalated turn to use; the fallback is the one that
# still fits when something else has most of the card. Sentinel decides which
# of the two Dexter actually gets — the ladder only changes what is *asked*
# for, and in what order.
SMART_MODEL_FALLBACK = os.getenv("DEXTER_SMART_MODEL_FALLBACK", "gemma-4-12b").strip()
# Where each model runs. The fast model has its own home (Ollama by default,
# whichever online server lists it) so it keeps answering instantly even while
# the shared llama-server on 8089 holds buzzcode's big model; the smart model
# follows the provider preference unless pinned here.
FAST_PROVIDER = os.getenv("DEXTER_FAST_PROVIDER", "").strip().lower()
SMART_PROVIDER = os.getenv("DEXTER_SMART_PROVIDER", "lmstudio").strip().lower()
# Residency: keep the small model loaded for hours (an instant reply is only
# instant when nothing has to load), let the big one go once its task is over.
FAST_KEEP_ALIVE = os.getenv("DEXTER_FAST_KEEP_ALIVE", "2h").strip() or "2h"
SMART_KEEP_ALIVE = os.getenv("DEXTER_SMART_KEEP_ALIVE", "30m").strip() or "30m"
TIERED_ENABLED = os.getenv("DEXTER_TIERED_LLM", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
    "",
)
# v13, D1: the fast model may *act*. One tool call on the small model, with the
# same candidate set the big one would get, before any escalation — most
# commands then finish in about a second without the 12B ever loading. Off with
# DEXTER_FAST_ACT=0, which restores v12 behaviour exactly (every instruction
# escalates).
FAST_ACT_ENABLED = os.getenv("DEXTER_FAST_ACT", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# The sentinel the fast model returns instead of answering when a turn needs the
# big model. Kept short and unlikely to appear in a genuine reply.
_ESCALATE = "<ESCALATE>"

_FAST_SYSTEM = (
    # Speed guard (v14.1): this tier runs on a small model that may be a reasoning
    # finetune. Through LM Studio's OpenAI shim there is no `think:false`, so left
    # alone it spends its whole budget deliberating - measured 4.2 s and 1300+
    # characters of reasoning on a plain "how are you" once tools are attached, and
    # when the budget runs out before an answer the turn escalates to the big model
    # for nothing. Telling it not to think cuts that to ~0.8 s with the answer intact
    # and does not hurt tool choice (an action still calls its tool immediately).
    "Answer or call a tool DIRECTLY and IMMEDIATELY. Do NOT think out loud, "
    "deliberate, plan, or output any reasoning, analysis, or <think> blocks - emit "
    "only the final one- or two-sentence reply, the tool call, or the escalation "
    "token below.\n\n"
    "You are only the instant-response layer of Dexter, a capable assistant that "
    "CAN take actions on the user's PC through tools (apps, files, media, email, "
    "calendar, reminders, memory, the web, and more) via its larger model. Your "
    "job is a fast first pass. Reply yourself ONLY when the message is simple "
    "conversation, chit-chat, or general knowledge you can fully and correctly "
    "answer in 1-2 short sentences with no tools and no external or personal data.\n\n"
    "For ANYTHING else, reply with exactly "
    f"{_ESCALATE} and nothing else - so the bigger model can handle it. In "
    "particular escalate (never refuse, never say you cannot do it) when the "
    "message asks you to DO or perform anything, or needs:\n"
    "- opening, closing, or controlling apps, windows, media, volume, or brightness\n"
    "- files, the clipboard, screenshots, or the screen's current contents\n"
    "- the web, app data, email, calendar, reminders, scheduling, or the news/time/date\n"
    "- the user's saved notes, memories, tasks, or anything personal or current\n"
    "- multi-step work, or anything you are not fully certain you can answer alone\n\n"
    f"You must NEVER say you can't do something - reply {_ESCALATE} instead. When "
    f"in doubt, reply {_ESCALATE}. Never explain the escalation."
)

AGENT_SYSTEM_PROMPT = """You are Dexter, a personal AI assistant running on the user's Windows PC.

You can act on the computer by calling tools. Rules:
- Call ONE tool at a time. After you see its result, decide the next single action.
- Use a tool only when the request needs real action or live information.
- For general questions, conversation or knowledge, just answer directly. Do not call a tool.
- When the task is done, reply in 1-2 short sentences describing what happened.
- Never invent file paths or results. If a tool fails, say so plainly.
- When you use a passage from the user's files, name the file (the context shows it as "from ...").

Keep replies short and natural. The sections below say how you and the user work together; follow them."""

JSON_PROTOCOL_HINT = """If you cannot use the native tool-calling format, respond with a single JSON object instead:
{"tool": "tool_name", "args": {...}}
or, if no tool is needed:
{"answer": "your reply"}
Respond with JSON only, no other text."""


def _parse_text_tool_call(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Recover a tool call that the model wrote as JSON text instead of using
    the native protocol. Common with small local models."""
    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        return None
    name = parsed.get("tool") or parsed.get("name") or parsed.get("function")
    if not name or not isinstance(name, str):
        return None
    args = parsed.get("args") or parsed.get("arguments") or parsed.get("parameters") or {}
    if not isinstance(args, dict):
        args = {}
    return name, args


def _parse_text_answer(text: str) -> Optional[str]:
    parsed = extract_json(text)
    if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
        return parsed["answer"]
    return None


class _TokenGate:
    """
    Forward LLM content deltas to the event stream - unless the reply turns out
    to be a text-protocol tool call. A small model may answer with raw JSON
    ({"tool": ...} or {"answer": ...}); streaming that to the UI and voice
    would leak protocol, so emission is deferred until the first non-space
    character shows the reply is prose.
    """

    def __init__(self, on_event: Callable[[str, Dict[str, Any]], None]) -> None:
        self._on_event = on_event
        self.reset()

    def reset(self) -> None:
        self._buffer = ""
        self._decided: Optional[bool] = None  # None undecided, True prose, False JSON

    def feed(self, piece: str) -> None:
        if self._decided is False:
            return
        if self._decided is True:
            self._on_event("token", {"text": piece})
            return
        self._buffer += piece
        head = self._buffer.lstrip()
        if not head:
            return
        if head[0] in "{[`":
            self._decided = False
            return
        self._decided = True
        self._on_event("token", {"text": self._buffer})


class _SentinelGate:
    """
    Stream the fast model's tokens as they arrive, but hold them back while the
    text so far could still turn into the escalation sentinel. "<ESCALATE>" is
    never shown; ordinary prose is released from its first character on.
    """

    def __init__(self, on_event: Optional[Callable[[str, Dict[str, Any]], None]]) -> None:
        self._on_event = on_event
        self._buffer = ""
        self._released = False
        self._blocked = False

    def feed(self, piece: str) -> None:
        if self._blocked or not self._on_event:
            return
        if self._released:
            self._on_event("token", {"text": piece})
            return
        self._buffer += piece
        head = self._buffer.lstrip().upper()
        if not head:
            return
        if head.startswith(_ESCALATE) or head.startswith("ESCALATE"):
            self._blocked = True  # a real sentinel: nothing is ever shown
            return
        if _ESCALATE.startswith(head) or "ESCALATE".startswith(head):
            return  # could still become the sentinel; keep holding
        # Hold the opening words too: "I'll drop that commitment now" must be
        # judged (and discarded) before the UI and the voice get it.
        if len(self._buffer) < 40 and not re.search(r"[.!?\n]", self._buffer):
            return
        if claims_action(self._buffer) or is_refusal(self._buffer):
            self._blocked = True
            return
        self._released = True
        self._on_event("token", {"text": self._buffer})

    @property
    def released(self) -> bool:
        return self._released


# The small model sometimes "does" things it cannot do ("Understood, the
# commitment will be dropped." - nothing ran). Two cheap guards keep it honest:
# a request that reads as an instruction skips the fast tier, and a fast reply
# that claims an action is thrown away in favour of the tool-enabled pass.
_ACTION_VERBS = (
    "open|close|launch|start|stop|play|pause|resume|skip|mute|unmute|set|turn|adjust|raise|lower|"
    "remind|schedule|book|plan|add|create|make|new|drop|delete|remove|clear|cancel|send|email|mail|"
    "message|text|call|search|find|look up|lookup|google|show|list|save|note|record|take|grab|read|"
    "summarize|summarise|run|code|fix|build|write|rename|move|copy|paste|check|log|mark|toggle|switch|"
    "restart|reboot|shut|lock|sleep|connect|disconnect|update|install|download|upload|post|push|commit|"
    "deploy|test|analyze|analyse|watch|track|monitor|forget|remember|store|pin|snooze|dismiss|"
    "increase|decrease|enable|disable|activate|deactivate|type|click|scroll|navigate|browse|go to|"
    "print|screenshot|capture|kill|end|finish|complete|done with|tick|untick|archive|restore|get rid of|scrap|ditch"
)
_ACTION_RE = re.compile(
    r"^\s*(?:(?:hey|ok|okay|yo|hi)[, ]+)?(?:dexter[, ]+)?(?:please[, ]+)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need)\s+(?:you\s+)?to\s+|"
    r"let'?s\s+|go\s+(?:ahead\s+)?and\s+)?"
    rf"(?:{_ACTION_VERBS})\b",
    re.IGNORECASE,
)
_ACT_PAST = (
    "opened|set|added|dropped|deleted|removed|sent|started|stopped|scheduled|created|reminded|cancelled|"
    "canceled|marked|saved|noted|recorded|played|launched|closed|turned|updated|cleared|muted|unmuted|logged|"
    "moved|renamed|completed|finished|booked|emailed|texted|paused|resumed|enabled|disabled|archived|"
    "restored|snoozed|dismissed|stored|remembered|forgotten|pinned|installed|downloaded|uploaded|posted|"
    "committed|pushed|deployed|adjusted|raised|lowered|toggled|switched|locked|connected|disconnected|"
    "increased|decreased|activated|deactivated|captured|printed|killed|ticked|unticked"
)
_ACT_VERB = (
    "open|set|add|drop|delete|remove|send|start|stop|schedule|create|remind|cancel|mark|save|note|record|"
    "play|launch|close|turn|update|clear|mute|unmute|log|move|rename|complete|finish|book|email|text|"
    "pause|resume|enable|disable|archive|restore|snooze|dismiss|store|pin|install|download|upload|post|"
    "commit|push|deploy|adjust|raise|lower|toggle|switch|lock|connect|disconnect|increase|decrease|"
    "activate|deactivate|capture|print|kill|tick|untick|take care of|handle|sort|get on|proceed"
)
_ACT_OBJECT = (
    "reminder|task|commitment|note|alarm|timer|event|volume|brightness|app|file|folder|email|message|"
    "window|music|song|playlist|calendar|goal|memory|setting|tab|browser|screen|screenshot|clipboard|"
    "meeting|appointment|item|entry|record|job|code task|order"
)
_CLAIMS_ACTION_RE = re.compile(
    # "I'll drop it", "I've added that", "I will set a reminder", "I'll be opening Spotify"
    rf"\bi(?:'ll| will|'ve| have)\s+(?:now\s+|just\s+|be\s+|go\s+ahead\s+and\s+|gladly\s+|happily\s+)?"
    rf"(?:{_ACT_VERB}|{_ACT_PAST}|(?:{_ACT_VERB})ing)\b"
    # "I'm setting a reminder", "I'm on it"
    rf"|\bi(?:'m| am)\s+(?:now\s+|just\s+)?(?:(?:{_ACT_VERB})ing|on it)\b"
    # "... will be dropped", "has been added", "is now set"
    rf"|\b(?:will|shall)\s+(?:now\s+)?be\s+(?:{_ACT_PAST})\b"
    rf"|\b(?:has|have)\s+(?:now\s+|just\s+)?been\s+(?:{_ACT_PAST})\b"
    # "the reminder is set", "task added", "commitment dropped"
    rf"|\b(?:{_ACT_OBJECT})s?\s+(?:is\s+|are\s+|was\s+|were\s+|got\s+)?(?:now\s+)?(?:{_ACT_PAST})\b"
    # bare acknowledgements that imply an action happened
    r"|^(?:done|all set|all done|consider it done|on it|sure thing|you got it|will do|understood|okay done|ok done)[.!,]?(?:\s|$)",
    re.IGNORECASE,
)


_PERSONAL_RE = re.compile(
    r"\b(?:my|our|the|this|that)\s+(?:screen|display|window|clipboard|desktop|files?|folders?|documents?|"
    r"inbox|e-?mails?|mail|calendar|schedule|agenda|meetings?|appointments?|reminders?|alarms?|timers?|"
    r"notes?|tasks?|to-?dos?|commitments?|goals?|wins?|blockers?|mission|focus|plan|day|week|"
    r"memory|memories|history|activity|sessions?|projects?|repos?|code|render|video|channel|studio|pc|computer)\b"
    r"|\b(?:what am i|what was i|what have i|what did i|what did we|what have we|where (?:am|was) i)\b"
    r"|\b(?:do|did|does|am|have|should|can|could|will) (?:i|we)\b|\bwhat(?:'s| is| are) (?:my|our)\b|\b(?:my|our) (?:name|age|birthday|address|email|phone)\b"
    r"|\b(?:weather|news|headlines|traffic|stock|price of|exchange rate)\b",
    re.IGNORECASE,
)
_REFUSAL_RE = re.compile(
    r"\b(?:i (?:do not|don'?t) have (?:direct |any )?(?:access|the ability|a way)|i (?:can|could)n'?t (?:access|see|check|view|open|do|perform)|"
    r"i cannot (?:access|see|check|view|open|do|perform)|i(?:'m| am) (?:not able|unable)|as an ai|i(?:'m| am) just (?:a|an) (?:ai|assistant|language model)|"
    r"i (?:do not|don'?t) have the (?:capability|ability)|i recommend (?:checking|taking a screenshot)|you (?:can|could|should) (?:check|open|look at) (?:your|the))",
    re.IGNORECASE,
)


def looks_like_action(text: str) -> bool:
    """An instruction to do something on the PC or in Dexter's data, as opposed
    to a question or conversation. 'tell me a joke' / 'what is ...' stay fast."""
    return bool(_ACTION_RE.search(text or ""))


def needs_live_data(text: str) -> bool:
    """A question about the user's own things or the present moment: the small
    model has no tools, so it must not be the one to answer."""
    return bool(_PERSONAL_RE.search(text or ""))


def is_refusal(reply: str) -> bool:
    """'I don't have access to your screen' - the small model declining what
    the big one can do. Never shown; escalated instead."""
    return bool(_REFUSAL_RE.search(reply or ""))


def claims_action(reply: str) -> bool:
    """A reply that says something was (or will be) done, when nothing ran."""
    return bool(_CLAIMS_ACTION_RE.search(reply or ""))


_last_warm: Dict[str, float] = {}

# ── Sentinel: the machine's VRAM guardian ───────────────────────────────────
# Sentinel decides who gets the card. Dexter registers as the `assistant`, asks
# before loading the big model, and drops it when told VRAM is tight — which is
# strictly better than being frozen, and is the only way the small model stays
# instant while a render or a second model server is running.
#
# Every call is best-effort: with Sentinel absent, `reserve` answers "granted"
# and Dexter behaves exactly as it did before.
DEXTER_VRAM_BUDGET_MIB = int(os.getenv("DEXTER_VRAM_BUDGET_MIB", "5120") or 5120)
DEXTER_RAM_BUDGET_MIB = int(os.getenv("DEXTER_RAM_BUDGET_MIB", "3072") or 3072)
SENTINEL_ENABLED = os.getenv("DEXTER_SENTINEL", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
# Context, KV cache and compute buffers are not in the model file's size, and
# under-asking is the failure that matters: a granted reservation that turns out
# to be too small puts us straight back to paging.
_RESERVE_OVERHEAD_MIB = int(os.getenv("DEXTER_RESERVE_OVERHEAD_MIB", "1024") or 1024)
# Dexter is last in line for the card (owner's rule, 2026-10-15): 10 sits below
# every other client Sentinel knows (buzzcode and unregistered apps are 20), so
# any of them may preempt Dexter's booking. Only Sentinel's test tools (5) rank
# lower. Sentinel never freezes an assistant, so this costs latency, not a hang.
DEXTER_PRIORITY = int(os.getenv("DEXTER_PRIORITY", "10") or 10)

_sentinel_lock = threading.Lock()
_sentinel_started = False


def sentinel() -> Optional[Any]:
    """The shared Sentinel client, or None when the integration is off."""
    if not SENTINEL_ENABLED:
        return None
    return sentinel_client.client()


def sentinel_start() -> None:
    """Register with Sentinel and subscribe to its events. Idempotent.

    Called once at startup and cheap enough to call again; if Sentinel is not
    running the subscription thread simply retries with backoff and nothing
    else in Dexter notices.
    """
    global _sentinel_started
    if not SENTINEL_ENABLED:
        return
    with _sentinel_lock:
        if _sentinel_started:
            return
        _sentinel_started = True
    client = sentinel_client.client()
    try:
        # `Hello` first: it tells us whether this build understands RAM
        # admission, and the answer decides what the steward is allowed to put
        # on the wire. The v1 service that is installed today answers nothing,
        # which is exactly the "features = []" case.
        features = client.features()
        registered = client.register(
            os.getpid(), "assistant", DEXTER_VRAM_BUDGET_MIB,
            ram_budget_mib=DEXTER_RAM_BUDGET_MIB if "ram_reserve" in features else None,
            priority=DEXTER_PRIORITY if features else None,
            label="Dexter" if features else None,
        )
        if registered:
            log.info(
                "sentinel: registered pid %s as assistant (%s MiB VRAM, %s MiB RAM); protocol %s",
                os.getpid(), DEXTER_VRAM_BUDGET_MIB, DEXTER_RAM_BUDGET_MIB,
                client.version() or "1 (no Hello)",
            )
            print(
                f"[Sentinel] Registered as assistant; guardian speaks "
                f"{client.version() or 'v1 (no Hello — RAM admission unavailable)'}."
            )
        client.subscribe(_on_sentinel_event)
    except Exception:
        log.debug("sentinel: startup failed", exc_info=True)


def sentinel_stop() -> None:
    """Unregister and stop listening. Called from the lifespan shutdown so a
    restart does not leave a dead client holding a booking."""
    global _sentinel_started
    if not _sentinel_started:
        return
    try:
        client = sentinel_client.client()
        model_steward.release("shutdown")
        client.unregister()
        client.stop()
    except Exception:
        log.debug("sentinel: shutdown failed", exc_info=True)
    finally:
        _sentinel_started = False


def _on_sentinel_event(event: Dict[str, Any]) -> None:
    """Every Sentinel event goes to the steward — it owns the shed ladder."""
    model_steward.on_event(event)


def _smart_model_reserve_mib(model: str) -> int:
    """How much VRAM to book before loading `model`."""
    return model_steward.vram_estimate(model)


def sentinel_reserve_for_smart(model: Optional[str]) -> Tuple[bool, Dict[str, Any]]:
    """Book VRAM (and RAM) for the smart model. `(granted, details)`.

    A thin wrapper over the steward, kept because it is the name the escalation
    path and the tests use. Granted when Sentinel says yes *or* is not there at
    all — a guardian that is not running must never be the reason Dexter
    refuses to think.
    """
    if not SENTINEL_ENABLED or not model:
        return True, {"absent": True}
    if model_steward._resident_place(model) not in ("", "cpu"):
        # Already on the card: nothing new is consumed, so nothing is booked
        # (as `ensure` does). Asking anyway counts its gigabytes twice, gets
        # refused, and steps down to the fallback — which, one model at a time,
        # then unloads the very model that was ready. Found live 2026-10-15.
        return True, {"resident": True}
    decision = model_steward.reserve(
        "smart-model",
        model_steward.vram_estimate(model),
        model_steward.ram_estimate(model),
    )
    details = dict(decision.details)
    details["_decision"] = decision
    return decision.granted, details


def cpu_rung(rungs: List[Tuple[str, Optional[str]]]) -> Optional[Tuple[str, Optional[str]]]:
    """The rung worth running in RAM when the card is refused (v13, P3.2).

    The *fallback* rung only, and only when its file is small enough to be an
    assistant off the GPU. The 12B manages 8 tok/s in system memory; waiting for
    the card is better than that, and saying so is better than both.
    """
    candidates = [(prov, model) for prov, model in rungs if prov == "ollama" and model]
    if not candidates:
        return None
    provider, model = candidates[-1]
    if not model_steward.fits_on_cpu(model):
        log.info("cpu rung: %s is too big to run in RAM (> %s MiB)",
                 model, model_steward.CPU_RUNG_MAX_MIB)
        return None
    return provider, model


def gpu_busy_reply(details: Dict[str, Any]) -> str:
    """What to say when the GPU is too busy to load the big model.

    Honest and specific: the number is the one Sentinel refused on, the holder
    is named, and the way out is a phrase Dexter understands. Vagueness here
    reads as a malfunction.
    """
    decision = details.get("_decision")
    if decision is None and details.get("blockers"):
        # A v2 refusal that did not come through the steward (a direct reserve,
        # or a stub in a test) still carries the holders; use them.
        decision = model_steward.Decision(
            granted=False, purpose="smart-model",
            vram_mib=model_steward.vram_estimate(SMART_MODEL or None),
            reason=str(details.get("reason") or ""),
            blockers=list(details.get("blockers") or []),
            details=details,
        )
    if decision is not None:
        return (
            decision.message()
            + " I'm still here — ask away and I'll answer on the model that's loaded."
        )
    free_gb = int(details.get("free_mib", 0)) / 1024.0
    return (
        f"The GPU is busy right now — only {free_gb:.1f} GB of video memory is free, "
        "which is not enough for me to load my larger model. Ask again in a moment, "
        "or say \"pause batch jobs\" and I will have Sentinel park the background "
        "work first."
    )


def _keep_fast_model_warm(provider: str) -> None:
    """Re-pin the small model in memory. Delegated: the steward is the only
    module allowed to touch model residency."""
    model_steward.keep_fast_warm(provider, FAST_MODEL, FAST_KEEP_ALIVE)


def _release_smart_model_soon(provider: str, model: Optional[str]) -> None:
    """Let the big model leave memory again, and take the rungs it replaced with it.

    Three things happen here, and the order matters (v13.2, the owner's ask:
    "if Dexter switches model, or I switch it, the previous one gets offloaded"):

    1. **Every other rung we loaded goes now.** The ladder's rungs are
       alternatives - the top one and the fallback are never both needed - so a
       turn that stepped down used to leave two models resident for half an hour.
       The fast tier is spared: it is the 3 GB one every chat turn needs.
    2. The rung that just answered leaves on a keep-alive, so a second action in
       the same conversation does not pay for a reload.
    3. Unless memory is already tight, or the owner asked for `always` - then it
       goes immediately, and so does the fast model.
    """
    mode = model_steward.offload_mode()
    if mode == "off":
        model_steward.release_soon(provider, model, SMART_KEEP_ALIVE)
        return

    keep = [model] if model else []
    if mode == "always":
        # Strictest: only the model that just answered is left in memory, the
        # fast tier included. A chat turn after this pays for a reload.
        model_steward.retire(keep, provider=provider, why="one model at a time",
                             include_fast=True)
        model_steward.release_soon(provider, model, SMART_KEEP_ALIVE)
        # ...and once it has been idle a while, back to the small model, so the
        # next quick reply is instant again.
        model_steward.settle_to_fast(model)
        return

    # 'rungs': the superseded rungs go, the fast tier stays.
    model_steward.retire(keep, provider=provider, why="superseded rung",
                         include_fast=False)
    if model and model_steward.memory_is_tight():
        # Sentinel is already warning about memory: 7 GB held against a possible
        # second action is the wrong trade, so hand it back now.
        if model_steward.unload(provider, model):
            print(f"[Router] Memory is tight; offloaded {model} rather than holding it "
                  f"for {SMART_KEEP_ALIVE}.")
            return
    model_steward.release_soon(provider, model, SMART_KEEP_ALIVE)


def fast_backend(preferred: str = "auto") -> Optional[str]:
    """The online server that serves FAST_MODEL, or None when the tier cannot run."""
    if not (TIERED_ENABLED and FAST_MODEL):
        return None
    if FAST_PROVIDER:
        return FAST_PROVIDER
    hint = preferred if preferred in ("ollama", "lmstudio") else ""
    return llm_router.provider_for_model(FAST_MODEL, preferred=hint)


def _rung_backend(model: str, preferred: str = "auto") -> Tuple[str, Optional[str]]:
    """(provider, model) for one rung of the ladder, or (provider, None) when no
    online server lists it. The shared llama-server serves exactly one model of
    its own, so a named tag only applies to Ollama / LM Studio."""
    # A remote provider the owner picked in the UI (Claude, OpenAI) is an
    # explicit choice: it wins over DEXTER_SMART_PROVIDER, which only says which
    # *local* server holds the smart rungs. Its own model default applies.
    if preferred in ("claude", "openai", "gemini"):
        return preferred, None
    provider = SMART_PROVIDER or llm_router.resolve_provider(preferred)
    if not model or provider not in ("ollama", "lmstudio"):
        return provider, None
    # Go where the model actually is: LM Studio being up must not send an
    # Ollama tag to LM Studio (a guaranteed "model not found").
    home = llm_router.provider_for_model(model, preferred=provider)
    if home and home != provider and not SMART_PROVIDER:
        provider = home
    return provider, (model if home == provider else None)


def smart_backend(preferred: str = "auto") -> Tuple[str, Optional[str]]:
    """(provider, model) for the tool-enabled pass — the top rung, or the
    provider's own default model when nobody serves it."""
    return _rung_backend(SMART_MODEL, preferred)


def smart_ladder(preferred: str = "auto") -> List[Tuple[str, Optional[str]]]:
    """The rungs to ask Sentinel for, best first (v12, D1).

    `DEXTER_SMART_MODEL` then `DEXTER_SMART_MODEL_FALLBACK`, deduplicated, each
    resolved to the server that actually lists it. A rung nobody serves is
    skipped (one log line — a silently missing model reads as "Dexter got
    dumber", which is the thing worth saying out loud).

    With no top rung configured there is no ladder: the escalation goes to the
    provider's own default model, exactly as it did before v12.
    """
    if not SMART_MODEL:
        return [smart_backend(preferred)]
    rungs: List[Tuple[str, Optional[str]]] = []
    seen = set()
    for name in (SMART_MODEL, SMART_MODEL_FALLBACK):
        name = (name or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        provider, model = _rung_backend(name, preferred)
        if not model:
            log.info("smart ladder: no online server lists %s — skipping that rung", name)
            continue
        rungs.append((provider, model))
    return rungs or [smart_backend(preferred)]


def _fast_reply(
    message: str,
    history: Optional[List[Dict[str, str]]] = None,
    provider: str = "auto",
    context_note: str = "",
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
) -> Optional[RouteResult]:
    """
    First pass with the small/fast model, no tools.

    Returns a RouteResult when the fast model confidently answers a simple turn
    (streamed for an instant reply), or None to signal "escalate" - the caller
    then runs the full agent loop on the bigger model. Any failure also returns
    None so the smart model is the safe fallback.
    """
    system = _FAST_SYSTEM
    profile_block = user_profile.render_block()
    if profile_block:
        system += f"\n\n{profile_block}"
    try:
        system += "\n\n" + companion.fast_stance()
    except Exception:
        pass
    if context_note:
        system += f"\n\nContext you may use:\n{context_note}"

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    gate = _SentinelGate(on_event)
    response = llm_router.chat(
        messages,
        provider=provider,
        model=FAST_MODEL,
        temperature=0.3,
        max_tokens=200,
        tools=None,
        timeout=30,
        on_token=gate.feed,
        should_cancel=lambda: cancellation.is_cancelled(session),
    )

    if response.cancelled:
        return RouteResult(reply="", action="interrupted", tier=1)
    if not response.ok:
        print(f"[Router] fast model unavailable ({response.error}); using the smart model.")
        return None  # fall through to the smart model

    answer = (response.content or "").strip()
    # Escalate on the sentinel, an empty reply, or a model that leaked the tag.
    if not answer or "ESCALATE" in answer.upper():
        return None
    # ... or on a small model pretending to act ("the commitment will be dropped").
    if claims_action(answer) and not gate.released:
        print(f"[Router] fast model claimed an action without tools; escalating: {answer[:80]!r}")
        return None
    # ... or declining something Dexter can do ("I don't have access to your screen").
    if is_refusal(answer) and not gate.released:
        print(f"[Router] fast model refused; escalating: {answer[:80]!r}")
        return None

    # A confident quick answer. Tokens already streamed as they arrived so the
    # frontend and sentence-level TTS started immediately; a reply the gate
    # never released (edge case: whole answer in one chunk) goes out now.
    if on_event and not gate.released:
        on_event("token", {"text": answer})
    _keep_fast_model_warm(provider)
    return RouteResult(reply=answer, action="fast_chat", tier=1, data={"model": FAST_MODEL, "provider": provider})


#: The hands. A chained desktop sentence needs these in front of the model even
#: when the embedding ranking puts a note tool first, because the second half of
#: "open youtube and search for lo-fi" is a browser act, not a note (v11, P3.2).
CHAIN_CATEGORIES = ("desktop", "browser")
CHAIN_EXTRA_TOOLS = 6


def candidate_tools(message: str) -> List[str]:
    """The tools handed to the model for this message, best first."""
    names = [name for name, _ in rank_tools(message, top_k=TOOLS_PER_CALL)]
    if not names:
        names = [spec.name for spec in all_tools()][:TOOLS_PER_CALL]
    if re.search(r"\b(?:tabs?|controls?|buttons?|in it|in this|click|type|write|switch|keys?|window|app)\b", message, re.I):
        for tool in ("click_in_app", "click_text", "type_text", "list_controls", "app_shortcut", "switch_to"):
            if tool not in names and get_tool(tool):
                names.append(tool)
    if re.search(r"\b(?:mission\s+reminder|machine\s+reminder|missing\s+reminder|messenger\s+minder|goals?|blocks?|forest)\b", message, re.I):
        if "mission_go_to_tab" not in names and get_tool("mission_go_to_tab"):
            names.append("mission_go_to_tab")
    if not is_desktop_chain(message):
        return names
    ranked = [name for name, _ in rank_tools(message, top_k=40)]
    for name in ranked:
        if len(names) >= TOOLS_PER_CALL + CHAIN_EXTRA_TOOLS:
            break
        if name in names:
            continue
        spec = get_tool(name)
        if spec and spec.category in CHAIN_CATEGORIES:
            names.append(name)
    return names


# ───────────────────────── Tier 1.6: the fast model acts (v13, D1) ─────────────
#
# Until v13 the small model was only ever allowed to *decline*: any instruction
# went straight to the big one, which meant "close notepad" paid for a 7 GB
# load. qwen3.5:4b picks the right tool in 27 of 30 measured cases with the same
# eight candidates Dexter hands the big model, so it gets one round with the
# tools. One. If it produces no call, an invalid one, or a tool that was not on
# the list — or the call fails — the turn escalates exactly as it did before,
# because a wrong action is far worse than a slow one.

_FAST_ACT_SYSTEM = (
    "You are Dexter, a personal assistant on the user's Windows PC. The user has "
    "asked you to DO something. Call exactly one of the provided tools, with correct "
    "arguments taken from the user's words. Do not explain, do not ask, do not "
    "call more than one tool. If none of the tools can do it, reply with the single "
    f"word {_ESCALATE} and nothing else."
)

_FAST_VOICE_SYSTEM = (
    "You are Dexter. An action just ran on the user's PC. Report it back in ONE "
    "short, natural sentence, in the past tense, using only what the result says. "
    "Add nothing, promise nothing, ask nothing."
)
#: A voiced line longer than this is the small model padding, not reporting.
_VOICE_MAX_CHARS = 220


def _voice_tool_result(
    message: str, tool_name: str, summary: str, provider: str, session: str,
) -> str:
    """The tool's own summary, said in Dexter's voice. Falls back to the summary.

    The summaries are written for correctness, not for conversation ("Opened
    Notepad (notepad.exe)."). One 80-token pass on a model that is already
    resident costs about a fifth of a second; anything that goes wrong with it —
    an empty reply, padding, a model that starts inventing — falls straight back
    to the summary, which was always a correct answer.
    """
    if not summary:
        return summary
    try:
        response = llm_router.chat(
            [
                {"role": "system", "content": _FAST_VOICE_SYSTEM},
                {"role": "user", "content": f"The user said: {message}\nResult of {tool_name}: {summary}"},
            ],
            provider=provider, model=FAST_MODEL, temperature=0.2, max_tokens=80,
            tools=None, timeout=20, think=False,
            should_cancel=lambda: cancellation.is_cancelled(session),
        )
    except Exception:
        return summary
    if response.cancelled or not response.ok:
        return summary
    line = " ".join((response.content or "").split()).strip()
    if not line or len(line) > _VOICE_MAX_CHARS or "ESCALATE" in line.upper():
        return summary
    return line


def _fast_act(
    message: str,
    provider: str,
    history: Optional[List[Dict[str, str]]] = None,
    context_note: str = "",
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
) -> Optional[RouteResult]:
    """One tool round on the fast model. `None` means "escalate, as before"."""
    if not FAST_ACT_ENABLED or not FAST_MODEL:
        return None
    names = candidate_tools(message)
    schemas = schemas_for(names)
    if not schemas:
        return None

    focus_note = focus_context_note(session=session)
    if focus_note and focus_note not in context_note:
        context_note = f"{focus_note}\n\n{context_note}".strip()
    system = _FAST_ACT_SYSTEM
    if context_note:
        system += f"\n\nContext you may use:\n{context_note}"
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in (history or [])[-4:]:
        role, content = turn.get("role"), turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    # No `on_token`: a tool call streamed to the UI is leaked protocol, and the
    # reply the user sees is the tool's result, not the model's preamble.
    response = llm_router.chat(
        messages, provider=provider, model=FAST_MODEL, temperature=0.1,
        max_tokens=300, tools=schemas, timeout=45, think=False,
        should_cancel=lambda: cancellation.is_cancelled(session),
    )
    if response.cancelled:
        return RouteResult(reply="", action="interrupted", tier=1)
    if not response.ok:
        log.info("fast act: %s could not answer (%s); escalating", FAST_MODEL, response.error)
        return None

    call: Optional[Tuple[str, Dict[str, Any]]] = None
    if response.tool_calls:
        first = response.tool_calls[0]
        call = (first["name"], first["arguments"])
    elif response.content:
        call = _parse_text_tool_call(response.content)
    if not call:
        print(f"[Router] fast act: no tool call from {FAST_MODEL}; escalating.")
        return None

    tool_name, tool_args = call
    spec = get_tool(tool_name)
    if not spec or tool_name not in names:
        # A tool it was never offered is a hallucinated one. The big model gets
        # the turn rather than Dexter guessing what was meant.
        print(f"[Router] fast act: {FAST_MODEL} asked for {tool_name!r}, which was not on offer; escalating.")
        return None
    ok, cleaned, why = validate_args(spec, tool_args or {})
    if not ok:
        print(f"[Router] fast act: invalid arguments for {tool_name} ({why}); escalating.")
        return None

    if cancellation.is_cancelled(session):
        return RouteResult(reply="", action="interrupted", tier=1)
    result, awaiting = _run_tool(tool_name, cleaned, on_event=on_event, session=session)
    if cancellation.is_cancelled(session):
        return RouteResult(reply="", action="interrupted", tier=1)
    if awaiting:
        # A confirm tool asks exactly as it always has; the answer is resolved
        # at the top of the next turn, whichever model handles it.
        return RouteResult(
            reply=result.summary, action="confirm", tier=1,
            data={**result.data, "fastAct": True}, needs_confirmation=True,
        )
    if not result.ok:
        # One round only, and this round did not do it. The big model gets a
        # clean try rather than the small one repairing its own mistake.
        print(f"[Router] fast act: {tool_name} failed ({result.summary}); escalating.")
        return None

    reply = _voice_tool_result(message, tool_name, result.summary, provider, session)
    if on_event:
        on_event("token", {"text": reply})
    _keep_fast_model_warm(provider)
    print(f"[Router] fast act: {tool_name} on {FAST_MODEL} — settled without the big model.")
    return RouteResult(
        reply=reply, action="fast_act", tier=1,
        tool_calls=[{"name": tool_name, "args": cleaned, "ok": result.ok,
                     "summary": result.summary, **_speech_hint(result)}],
        data={**result.data, "model": FAST_MODEL, "provider": provider,
              "fastAct": True, **_speech_hint(result)},
    )


def agent_loop(
    message: str,
    history: Optional[List[Dict[str, str]]] = None,
    provider: str = "auto",
    model: Optional[str] = None,
    context_note: str = "",
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
) -> RouteResult:
    """
    ReAct-style loop, deliberately constrained for small local models:
    one tool per iteration, only the most relevant tools in context, and a
    single repair retry on invalid arguments.
    """
    candidate_names = candidate_tools(message)
    tool_schemas = schemas_for(candidate_names)

    system = AGENT_SYSTEM_PROMPT
    profile_block = user_profile.render_block()
    if profile_block:
        system += f"\n\n{profile_block}"
    try:
        system += "\n\n" + companion.context_block()
    except Exception as exc:
        print(f"[Router] companion context skipped: {exc}")
    focus_note = focus_context_note(session=session)
    if focus_note and focus_note not in context_note:
        context_note = f"{focus_note}\n\n{context_note}".strip()
    if context_note:
        system += f"\n\nContext you may use:\n{context_note}"

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in (history or [])[-8:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    executed: List[Dict[str, Any]] = []
    last_result: Optional[ToolResult] = None
    repair_used = False

    gate = _TokenGate(on_event) if on_event else None

    def _on_fallback(failed: str, next_provider: str) -> None:
        # Tokens already shown came from a stream that died; the retry starts
        # the reply over, so the UI must clear what it has.
        if gate:
            gate.reset()
        if on_event:
            on_event("provider_switched", {"from": failed, "to": next_provider})

    def _should_cancel() -> bool:
        return cancellation.is_cancelled(session)

    nudged = False
    for iteration in range(MAX_ITERATIONS):
        if _should_cancel():
            return RouteResult(reply="", action="interrupted", tier=2, tool_calls=executed)
        if gate:
            gate.reset()
        response = llm_router.chat(
            messages,
            provider=provider,
            model=model,
            temperature=0.1 if tool_schemas else 0.5,
            max_tokens=1024,
            tools=tool_schemas or None,
            on_token=gate.feed if gate else None,
            on_fallback=_on_fallback,
            should_cancel=_should_cancel,
        )

        # Barge-in: the user talked over the reply. Stop cleanly, no apology.
        if response.cancelled:
            return RouteResult(reply="", action="interrupted", tier=2, tool_calls=executed)

        if not response.ok:
            if executed:
                return RouteResult(
                    reply=last_result.summary if last_result else "Done.",
                    action="agent", tier=2, tool_calls=executed,
                    data={"warning": response.error},
                )
            # Say what actually went wrong. "Make sure Ollama is running" was
            # the whole message while LM Studio was up and refusing to load a
            # model - the owner could not have known which.
            detail = (response.error or "").strip()
            detail = detail[:160] + ("…" if len(detail) > 160 else "")
            return RouteResult(
                reply=(
                    "My local reasoning engine isn't answering"
                    + (f": {detail}" if detail else ".")
                    + " I start Ollama myself when it's down; if this keeps happening, "
                    "check the model server in the pop-up's quick settings."
                ),
                action="llm_offline", tier=2,
                data={"error": response.error},
            )

        # Native tool call, or a JSON one recovered from the text body.
        call: Optional[Tuple[str, Dict[str, Any]]] = None
        if response.tool_calls:
            first = response.tool_calls[0]
            call = (first["name"], first["arguments"])
        elif response.content:
            call = _parse_text_tool_call(response.content)

        if not call:
            answer = _parse_text_answer(response.content) or response.content
            answer = (answer or "").strip()
            if not answer and last_result:
                answer = last_result.summary
            if not executed and not nudged and looks_like_action(message) and claims_action(answer):
                # "Got it, the commitment is dropped." - and nothing ran. Send it
                # back once with the plain truth; the UI drops the partial text.
                nudged = True
                print(f"[Router] model claimed an action without a tool; nudging: {answer[:80]!r}")
                if on_event:
                    on_event("provider_switched", {"from": provider, "to": provider, "reason": "no tool ran"})
                messages.append({"role": "assistant", "content": answer})
                messages.append({
                    "role": "user",
                    "content": (
                        "Nothing happened - you did not call any tool. Call the right tool now to actually do it "
                        "(one tool call, nothing else). If no tool can do it, say plainly that you can't and why."
                    ),
                })
                continue
            return RouteResult(
                reply=answer or "Done.",
                action="agent" if executed else "chat",
                tier=2,
                tool_calls=executed,
                data={"iterations": iteration + 1},
            )

        if _should_cancel():
            return RouteResult(reply="", action="interrupted", tier=2, tool_calls=executed)

        tool_name, tool_args = call
        result, awaiting = _run_tool(tool_name, tool_args, on_event=on_event, session=session)
        last_result = result

        if _should_cancel():
            return RouteResult(reply="", action="interrupted", tier=2, tool_calls=executed)

        if awaiting:
            return RouteResult(
                reply=result.summary, action="confirm", tier=2,
                tool_calls=executed, data=result.data, needs_confirmation=True,
            )

        executed.append({
            "name": tool_name,
            "args": tool_args,
            "ok": result.ok,
            "summary": result.summary,
            **_speech_hint(result),
        })

        # One repair attempt on a validation failure, then move on.
        if not result.ok and not repair_used and "argument" in result.summary.lower():
            repair_used = True
            messages.append({"role": "assistant", "content": f"I called {tool_name}."})
            messages.append({
                "role": "user",
                "content": f"That call was invalid: {result.summary} Try again with correct arguments.",
            })
            continue

        messages.append({
            "role": "assistant",
            "content": f"[called {tool_name}]",
        })
        messages.append({
            "role": "user",
            "content": (
                f"Result of {tool_name}: {result.summary}\n\n"
                "If the user's request is now complete, reply to them directly in "
                "1-2 short sentences. Otherwise call the next tool."
            ),
        })

    # Hit the iteration cap - report honest partial progress.
    summary = last_result.summary if last_result else "I couldn't complete that."
    return RouteResult(
        reply=f"{summary} (I stopped after {MAX_ITERATIONS} steps.)",
        action="agent_max_steps", tier=2, tool_calls=executed,
        data={"iterations": MAX_ITERATIONS},
    )


# ───────────────────────── entry point ─────────────────────────


# "In YouTube, select the first video" (v13.2). A site is not a window - there is
# nothing to focus - so the prefix means "switch to that tab first". Without this
# the whole sentence reached the model, which is what "it says it is doing it but
# it does nothing" looked like from the outside.
_IN_SITE = re.compile(
    r"^(?:in|on|over\s+in|back\s+in)\s+(?:the\s+|my\s+)?(?P<site>[\w.-]{3,24})"
    r"(?:\s+tab|\s+page|\.com)?\s*,?\s+(?P<rest>.{3,})$",
    re.IGNORECASE,
)


def split_site_prefix(text: str) -> Optional[Tuple[str, str]]:
    """('youtube', 'select the first video') when the sentence opens with a site."""
    match = _IN_SITE.match((text or "").strip())
    if not match:
        return None
    site = match.group("site").strip().lower()
    rest = match.group("rest").strip()
    if not rest or _alias_is_app(site):
        return None  # a window, not a tab: _route_in_app owns that
    try:
        from browser import known_site

        if not known_site(site):
            return None
    except Exception:
        return None
    return site, rest


def _alias_is_app(name: str) -> bool:
    try:
        import desktop_control

        return desktop_control._alias_of(name) is not None
    except Exception:
        return False


def _route_in_tab(
    site: str,
    rest: str,
    history: Optional[List[Dict[str, str]]] = None,
    provider: str = "auto",
    model: Optional[str] = None,
    context_note: str = "",
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
) -> RouteResult:
    """Switch to that site's tab, then run the rest of the sentence there.

    The switch is a real tool call with a real result: if no tab matches, the turn
    says so rather than acting on whatever tab happened to be in front.
    """
    switch, _awaiting = _run_tool("browser_switch_tab", {"which": site}, confirmed=True,
                                  on_event=on_event, session=session)
    call = {"name": "browser_switch_tab", "args": {"which": site},
            "ok": switch.ok, "summary": switch.summary}
    if not switch.ok:
        return RouteResult(
            reply=f"{switch.summary} Open it first, or say \"open {site}\".",
            action="tab_not_found", tier=0, tool_calls=[call], data=switch.data,
        )
    inner = route(rest, history=history, provider=provider, model=model,
                  context_note=context_note, on_event=on_event, session=session,
                  allow_app_prefix=False)
    inner.tool_calls = [call] + list(inner.tool_calls)
    return inner


def _route_in_app(
    app: str,
    rest: str,
    history: Optional[List[Dict[str, str]]] = None,
    provider: str = "auto",
    model: Optional[str] = None,
    context_note: str = "",
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
) -> RouteResult:
    """Focus the app, then run the rest of the sentence there. One reply.

    The focus step is a real tool call with a real verification, so if Windows
    refuses to bring the window forward the turn stops there instead of sending
    the command to whatever was in front.
    """
    focus_result, _awaiting = _run_tool("switch_to", {"app": app}, confirmed=True,
                                       on_event=on_event, session=session)
    focus_call = {"name": "switch_to", "args": {"app": app},
                  "ok": focus_result.ok, "summary": focus_result.summary}
    if not focus_result.ok:
        return RouteResult(reply=focus_result.summary, action="focus_failed", tier=0,
                           tool_calls=[focus_call], data=focus_result.data)
    inner = route(rest, history=history, provider=provider, model=model,
                  context_note=context_note, on_event=on_event, session=session,
                  allow_app_prefix=False)
    inner.tool_calls = [focus_call] + list(inner.tool_calls)
    inner.reply = f"{focus_result.summary} {inner.reply}".strip()
    return inner


def route(
    message: str,
    history: Optional[List[Dict[str, str]]] = None,
    provider: str = "auto",
    model: Optional[str] = None,
    context_note: str = "",
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
    allow_app_prefix: bool = True,
) -> RouteResult:
    """Route a user message through the three tiers and return a reply."""
    text = (message or "").strip()
    if not text:
        return RouteResult(reply="I didn't catch that.", action="empty", tier=0)

    # "in chrome, go to gmail" - focus first, then run the rest as its own turn
    # and answer once (v11, P3.2). Stripped only once, so "in chrome, in edge, …"
    # cannot recurse.
    if allow_app_prefix:
        prefix = split_app_prefix(text, session=session)
        if prefix:
            return _route_in_app(
                prefix[0], prefix[1], history=history, provider=provider, model=model,
                context_note=context_note, on_event=on_event, session=session,
            )
        tab_prefix = split_tab_prefix(text, session=session)
        if tab_prefix:
            kind, target, rest = tab_prefix
            if kind == "browser" and target:
                return _route_in_tab(
                    target, rest, history=history, provider=provider, model=model,
                    context_note=context_note, on_event=on_event, session=session,
                )
            if kind == "desktop" and target:
                return _route_in_app(
                    target, rest, history=history, provider=provider, model=model,
                    context_note=context_note, on_event=on_event, session=session,
                )
        # "in youtube, select the first video": a site is a *tab*, not a window
        # (v13.2). Checked after the app prefix, so "in chrome, ..." still focuses
        # the browser rather than looking for a tab called chrome.
        site = split_site_prefix(text)
        if site:
            return _route_in_tab(
                site[0], site[1], history=history, provider=provider, model=model,
                context_note=context_note, on_event=on_event, session=session,
            )

    # Resolve an outstanding confirmation before anything else.
    if _has_pending(session):
        if _AFFIRMATIVE.match(text):
            pair = _take_pending(session)
            if pair:
                result = execute(pair[0], pair[1])
                return RouteResult(
                    reply=result.summary, action="confirmed", tier=0,
                    tool_calls=[{"name": pair[0], "args": pair[1], "ok": result.ok,
                                 "summary": result.summary}],
                    data=result.data,
                )
        elif _NEGATIVE.match(text):
            _take_pending(session)
            return RouteResult(reply="Cancelled.", action="cancelled", tier=0)

    # ── Tier 0 ──
    small_talk = chitchat(text)
    if small_talk:
        return RouteResult(reply=small_talk, action="chitchat", tier=0)

    hit = fast_path(text, session=session)
    if hit:
        tool_name, args = hit
        result, awaiting = _run_tool(tool_name, args, on_event=on_event, session=session)
        return RouteResult(
            reply=result.summary,
            action="confirm" if awaiting else "fast_path",
            tier=0,
            tool_calls=[] if awaiting else [
                {"name": tool_name, "args": args, "ok": result.ok, "summary": result.summary}
            ],
            data={**result.data, **_speech_hint(result)},
            needs_confirmation=awaiting,
        )

    # ── Tier 1 ──
    # A chained desktop sentence ("open youtube and search for lo-fi beats") is
    # two acts. Tier 1 can only pick one tool, so it would do the first half and
    # silently drop the second; the agent loop does both and reports once.
    match = None if is_desktop_chain(text) else semantic_match(text)
    if match:
        tool_name, score = match
        args = _extract_slots(tool_name, text)
        if args is not None:
            result, awaiting = _run_tool(tool_name, args, on_event=on_event, session=session)
            return RouteResult(
                reply=result.summary,
                action="confirm" if awaiting else "semantic",
                tier=1,
                tool_calls=[] if awaiting else [
                    {"name": tool_name, "args": args, "ok": result.ok, "summary": result.summary}
                ],
                data={**result.data, "score": round(score, 3), **_speech_hint(result)},
                needs_confirmation=awaiting,
            )

    # ── Tier 1.5: instant fast-model reply, escalate to the smart model ──
    # Only when the caller has not pinned a specific model (an explicit UI pick
    # is honoured exactly). The fast model runs wherever it is served (Ollama
    # by default) - independent of the smart backend, so the tier keeps working
    # while the shared llama-server on 8089 holds buzzcode's big model.
    fast_prov = fast_backend(provider) if model is None else None
    if fast_prov:
        # One model at a time (2026-10-15): with the smart model already loaded
        # the turn stays on it. Trying the fast tier first would swap
        # 12B -> e4b -> 12B on any question that escalates.
        stay = model_steward.resident_smart() if model_steward.one_at_a_time() else None
        # An instruction goes straight to the model that has the tools; the
        # fast pass is for conversation and knowledge only.
        fast = None if (stay or looks_like_action(text) or needs_live_data(text)) else _fast_reply(
            text, history=history, provider=fast_prov,
            context_note=context_note, on_event=on_event, session=session,
        )
        if fast is not None:
            return fast
        # ── Tier 1.6: let the fast model act (v13, D1) ──
        # An instruction that is one act, with tools that fit it, gets one round
        # on the small model. A chain sentence does not: it is two acts, and the
        # agent loop is the only thing that does both and reports once.
        # Anything that reached this point is either an instruction, a question
        # about the present moment, or a sentence the fast chat pass declined
        # to answer - and each of those may be one tool call. Found live: "what
        # track is playing right now" matched neither predicate, so it went to
        # the 12B (7.8 s) while the small model settles it in about a second.
        # Only a chain sentence is excluded: two acts need the agent loop.
        if not stay and not is_desktop_chain(text):
            acted = _fast_act(
                text, provider=fast_prov, history=history,
                context_note=context_note, on_event=on_event, session=session,
            )
            if acted is not None:
                return acted
        # Escalated: run the full tool-enabled loop on the bigger model, then
        # let it leave memory once idle so the GPU is free again.
        #
        # v12, D1/D2: walk the ladder. Ask Sentinel for the top rung first and
        # take the fallback when it is refused — the guardian stays the judge,
        # the ladder only decides what is asked for. A refusal is not a
        # failure: it means someone else is using the card, and saying so is
        # better than loading anyway and making the whole machine crawl.
        rungs = smart_ladder(provider)
        smart_prov, smart_model = rungs[0]
        granted, reservation = False, {}
        stepped_down_from: Optional[str] = None
        place = "gpu"
        refused: List[Dict[str, Any]] = []
        for index, (rung_prov, rung_model) in enumerate(rungs):
            granted, reservation = sentinel_reserve_for_smart(
                rung_model if rung_prov == "ollama" else None
            )
            if granted:
                smart_prov, smart_model = rung_prov, rung_model
                if index:
                    # We are answering on a lesser model because a better one
                    # was refused. D4: the turn carries why, quietly.
                    stepped_down_from = rungs[0][1]
                break
            refused.append({"model": rung_model, "reservation": reservation})
            if index + 1 < len(rungs):
                log.info("smart ladder: %s refused, trying %s",
                         rung_model, rungs[index + 1][1])
        if not granted:
            # ── Rung 3: RAM (v13, D4/P3.2) ──
            # Every place on the card was refused. A small rung can still run in
            # system memory at about a fifth of the speed, which is a slower
            # answer rather than no answer. Sentinel still decides — this is a
            # RAM reservation, and a refusal loads nothing, exactly as before.
            cpu = cpu_rung(rungs)
            if cpu:
                decision = model_steward.ensure(
                    cpu[1], "smart-model", provider=cpu[0],
                    keep_alive=SMART_KEEP_ALIVE, place="cpu",
                )
                if decision.granted and decision.loaded:
                    granted = True
                    smart_prov, smart_model = cpu
                    place = "cpu"
                    if cpu[1] != rungs[0][1]:
                        stepped_down_from = rungs[0][1]
                    log.info("smart ladder: running %s in RAM — the card was refused", cpu[1])
                else:
                    log.info("smart ladder: %s was refused RAM too (%s)",
                             cpu[1], decision.reason or "no reason given")
        if not granted:
            # Refused. Two rules, and both matter: **nothing is loaded** (going
            # ahead anyway is how the machine ends up paging), and Dexter does
            # not go quiet — it says who has the memory and what it is allowed
            # to do about it, then answers on whatever is already resident.
            note = gpu_busy_reply(reservation)
            # Also on the push channel, so the banner and the pop-up show it
            # even when the turn came from somewhere other than the open window.
            try:
                from events import bus as _bus

                _bus.publish("gpu_busy", {
                    "freeMib": reservation.get("free_mib", 0),
                    "blockers": reservation.get("blockers", []),
                    "message": note,
                })
            except Exception:
                pass
            if on_event:
                on_event("gpu_busy", {
                    "free_mib": reservation.get("free_mib", 0),
                    "blockers": reservation.get("blockers", []),
                    "message": note,
                })
            fallback = model_steward.resident_fallback(smart_prov)
            if fallback:
                result = agent_loop(
                    text, history=history, provider=smart_prov, model=fallback,
                    context_note=context_note, on_event=on_event, session=session,
                )
                result.reply = f"{note}\n\n{result.reply}".strip()
                result.data["sentinel"] = {k: v for k, v in reservation.items() if k != "_decision"}
                result.action = "gpu_busy_degraded"
                return result
            return RouteResult(
                reply=note,
                action="gpu_busy",
                tier=1,
                data={"sentinel": {k: v for k, v in reservation.items() if k != "_decision"}},
            )
        ladder_info = {
            "model": smart_model or "",
            "provider": smart_prov,
            "steppedDownFrom": stepped_down_from,
            # Where the rung is running (v13). "cpu" is worth saying out loud:
            # the answer is right but slow, and a user who knows why waits
            # instead of assuming Dexter broke.
            "place": place,
            "blockers": [
                b for step in refused
                for b in (step["reservation"].get("blockers") or [])
            ],
        }
        # The panel wants the current rung whether or not we stepped down; the
        # reply only mentions it when we did (D4 — no apology on a good turn).
        model_steward.note_ladder(ladder_info)
        if place == "gpu":
            # One model at a time: the rung is granted, so the fast model goes
            # before this one loads (a no-op in the other offload modes).
            model_steward.make_way(smart_model, smart_prov)
        if on_event:
            on_event("escalated", {"provider": smart_prov, "model": smart_model or "",
                                   "steppedDownFrom": stepped_down_from, "place": place})
        try:
            result = agent_loop(
                text, history=history, provider=smart_prov, model=smart_model,
                context_note=context_note, on_event=on_event, session=session,
            )
            if (stepped_down_from or place != "gpu") and result is not None:
                result.data["ladder"] = ladder_info
        finally:
            # Hand the booking back. The model is resident now, so Sentinel can
            # see its real VRAM in the process table; holding the reservation on
            # top of that counts the same gigabytes twice and refuses the next
            # claimant for up to the whole TTL. Found live: an escalated turn
            # left 5490 MiB booked for 90 s after the model had finished
            # answering. `release()` is a no-op when nothing is booked.
            model_steward.release("smart-model")
        _release_smart_model_soon(smart_prov, smart_model)
        return result

    # ── Tier 2 ──
    # A model the owner picked in the UI bypasses the tiers entirely, so the
    # tiers are not in use while it is selected: the pinned fast model and any
    # smart rung are dead weight on the card. Hand them back (v13.2).
    try:
        result = agent_loop(text, history=history, provider=provider, model=model,
                            context_note=context_note, on_event=on_event, session=session)
    finally:
        if model:
            model_steward.retire([model], provider=provider,
                                 why=f"{model} is the model you picked", include_fast=True)
    return result
