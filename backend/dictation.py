"""
Continuous note-taking (v13).

"I want it to take notes from me continuously if I say so - it can use Notion or
Notepad or sticky notes or any tool I say."

Dictation is a *mode*, not a command. While it is on, everything the mic hears
from the owner is written down instead of being answered: no "shall I note that",
no model in the loop, no turn in the chat. That is the whole point - a person
thinking out loud does not want a conversation partner, and a round trip through
a language model would lose half of what they said.

Where the words go is a sink, and the sinks are deliberately different kinds of
thing:

* `notes`    - a Markdown file in Dexter's notes folder (the default). Indexed,
               searchable, and still there tomorrow.
* `file`     - a path the owner named.
* `notepad`  - typed into Notepad, as if they were typing it.
* `sticky`   - typed into Windows Sticky Notes.
* `notion`   - typed into Notion (the app if it is running, otherwise the web
               app in their Chrome).
* `window`   - typed into any window they name ("take notes in BuzzEdit").
* `clipboard`- collected, and kept on the clipboard.

Typing into someone else's app is the honest way to support "any tool I say":
Dexter has no Notion API key and no business having one, and an app that can be
typed into can be dictated into.

Spoken punctuation ("new paragraph", "full stop", "question mark") and "scratch
that" are handled here, because they are how dictation is actually used.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

#: Spoken names for each sink. First match wins, longest phrases first.
SINK_WORDS: List[Tuple[str, str]] = [
    ("sticky notes", "sticky"),
    ("sticky note", "sticky"),
    ("a sticky", "sticky"),
    ("notepad", "notepad"),
    ("note pad", "notepad"),
    ("notion", "notion"),
    ("the clipboard", "clipboard"),
    ("clipboard", "clipboard"),
    ("my notes", "notes"),
    ("your notes", "notes"),
    ("notes", "notes"),
    ("a note", "notes"),
    ("a file", "file"),
    ("a text file", "file"),
]

#: What to say to get a character instead of the word.
PUNCTUATION = {
    "full stop": ".", "period": ".", "comma": ",", "question mark": "?",
    "exclamation mark": "!", "exclamation point": "!", "colon": ":",
    "semicolon": ";", "dash": " - ", "hyphen": "-", "open bracket": "(",
    "close bracket": ")", "open quote": '"', "close quote": '"',
    "ellipsis": "...", "dot dot dot": "...",
}

_NEW_PARAGRAPH = re.compile(r"^(?:new|next)\s+para(?:graph)?[.!]*$", re.I)
_NEW_LINE = re.compile(r"^(?:new\s+line|newline|line\s+break|next\s+line)[.!]*$", re.I)
_SCRATCH = re.compile(
    r"^(?:scratch|delete|remove|undo|strike)\s+(?:that|the\s+last\s+(?:line|bit|sentence))[.!]*$",
    re.I,
)
_BULLET = re.compile(r"^(?:new\s+)?(?:bullet|bullet\s+point|dash\s+point)[.!]*$", re.I)
#: Said while dictating, these end it. "stop" and "cancel" alone count too -
#: nobody dictating a note means the word "stop" as content.
_END_WORDS = re.compile(
    r"^(?:ok(?:ay)?[,\s]+)?(?:dexter[,\s]+)?"
    r"(?:stop|end|finish|done|that'?s (?:it|all|enough|the end))"
    r"(?:\s+(?:taking\s+)?(?:notes?|dictation|dictating|writing|recording|the\s+note))?"
    r"(?:[,\s]+(?:please|dexter|now))?[.!]*$",
    re.I,
)
#: And these pause it without ending it.
_PAUSE_WORDS = re.compile(r"^(?:pause|hold)\s+(?:the\s+)?(?:notes?|dictation)[.!]*$", re.I)
_RESUME_WORDS = re.compile(
    r"^(?:resume|carry\s+on|keep\s+going|continue)\s*(?:the\s+)?(?:notes?|dictation)?[.!]*$", re.I
)


@dataclass
class Session:
    """One dictation: where it is going and what has been said so far."""

    sink: str = "notes"
    target: str = ""            # path, window title, or ""
    title: str = ""
    started: float = field(default_factory=time.time)
    paused: bool = False
    lines: List[str] = field(default_factory=list)
    path: str = ""              # the file being written, for file-ish sinks
    #: Whatever was in the file before the first dictated line. Kept so "scratch
    #: that" can rewrite it without counting lines and hoping.
    header: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(len(re.findall(r"[\w']+", line)) for line in self.lines)

    def describe(self) -> str:
        where = {
            "notes": f"your notes ({os.path.basename(self.path) or 'a new note'})",
            "file": self.path or "a file",
            "notepad": "Notepad",
            "sticky": "Sticky Notes",
            "notion": "Notion",
            "clipboard": "the clipboard",
            "window": self.target or "that window",
        }.get(self.sink, self.sink)
        return where


class Dictation:
    """The dictation sessions, one per chat session. Thread-safe."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.RLock()

    # ───────────────────────── state ─────────────────────────

    def active(self, session: str = "default") -> bool:
        with self._lock:
            return session in self._sessions

    def get(self, session: str = "default") -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session)

    def status(self, session: str = "default") -> Dict[str, Any]:
        state = self.get(session)
        if state is None:
            return {"active": False}
        return {
            "active": True,
            "paused": state.paused,
            "sink": state.sink,
            "where": state.describe(),
            "path": state.path,
            "lines": len(state.lines),
            "words": state.words,
            "minutes": round((time.time() - state.started) / 60.0, 1),
            "preview": " ".join(state.lines)[-400:],
            "errors": state.errors[-3:],
        }

    # ───────────────────────── starting and stopping ─────────────────────────

    def parse_target(self, spoken: Optional[str]) -> Tuple[str, str]:
        """('notepad', '') / ('file', 'C:\\notes\\ideas.md') / ('window', 'buzzedit')."""
        text = (spoken or "").strip().strip(" .!?,'\"").lower()
        text = re.sub(r"^(?:in|into|on|to|using|with)\s+", "", text)
        text = re.sub(r"^(?:my|the|a|an)\s+", "", text)
        if not text:
            return "notes", ""
        for phrase, sink in SINK_WORDS:
            if text == phrase or text.startswith(phrase + " ") or text.endswith(" " + phrase):
                if sink == "file":
                    # "a file called ideas.md"
                    match = re.search(r"(?:called|named)\s+(.+)$", text)
                    return "file", (match.group(1).strip() if match else "")
                return sink, ""
        # A path is a file; anything else is taken to be a window/app name.
        if re.match(r"^(?:[a-z]:[\\/]|\\\\|~|%)", text) or text.endswith((".md", ".txt")):
            return "file", spoken.strip().strip("'\"")
        return "window", spoken.strip().strip(" .!?,'\"")

    def start(
        self,
        session: str = "default",
        target: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        # No sink named? The owner's default (Settings: "Where take notes writes").
        if not (target or "").strip():
            target = os.environ.get("DEXTER_DICTATION_TARGET", "notes") or "notes"
        sink, where = self.parse_target(target)
        state = Session(sink=sink, target=where, title=(title or "").strip())
        prepared = self._prepare(state)
        if not prepared.get("ok"):
            return prepared
        with self._lock:
            self._sessions[session] = state
        return {"ok": True, "where": state.describe(), **self.status(session)}

    def stop(self, session: str = "default") -> Dict[str, Any]:
        with self._lock:
            state = self._sessions.pop(session, None)
        if state is None:
            return {"ok": False, "active": False}
        summary = {
            "ok": True,
            "where": state.describe(),
            "path": state.path,
            "lines": len(state.lines),
            "words": state.words,
            "minutes": round((time.time() - state.started) / 60.0, 1),
            "text": "\n".join(state.lines),
        }
        if state.sink in ("notes", "file") and state.path:
            self._index(state.path)
        return summary

    def pause(self, session: str = "default", paused: bool = True) -> Dict[str, Any]:
        state = self.get(session)
        if state is None:
            return {"ok": False, "active": False}
        state.paused = bool(paused)
        return {"ok": True, "paused": state.paused, **self.status(session)}

    # ───────────────────────── the words ─────────────────────────

    def handle(self, text: str, session: str = "default") -> Dict[str, Any]:
        """One heard utterance while dictation is on.

        Returns `{"action": ...}` - `written`, `ended`, `paused`, `resumed`,
        `scratched`, `ignored` - so the caller can say the right thing and the
        voice layer knows whether this was a turn at all.
        """
        state = self.get(session)
        if state is None:
            return {"action": "ignored", "why": "no dictation running"}
        body = (text or "").strip()
        if not body:
            return {"action": "ignored", "why": "empty"}

        if _END_WORDS.match(body):
            return {"action": "ended", **self.stop(session)}
        if _PAUSE_WORDS.match(body):
            self.pause(session, True)
            return {"action": "paused", "where": state.describe()}
        if _RESUME_WORDS.match(body):
            self.pause(session, False)
            return {"action": "resumed", "where": state.describe()}
        if state.paused:
            return {"action": "ignored", "why": "paused"}
        if _SCRATCH.match(body):
            removed = state.lines.pop() if state.lines else ""
            self._rewrite(state)
            return {"action": "scratched", "removed": removed, "lines": len(state.lines)}

        line = self.format_line(body)
        if line is None:
            return {"action": "ignored", "why": "nothing but filler"}
        state.lines.append(line)
        written = self._write(state, line)
        if not written.get("ok"):
            state.errors.append(str(written.get("error") or "could not write it"))
            return {"action": "failed", "error": written.get("error"), "line": line}
        return {"action": "written", "line": line, "lines": len(state.lines),
                "words": state.words, "where": state.describe()}

    def format_line(self, text: str) -> Optional[str]:
        """Turn one utterance into the line that gets written.

        Spoken punctuation becomes punctuation, "new paragraph" becomes a blank
        line, and a sentence gets a capital and a full stop - because a wall of
        lower-case fragments is not notes.
        """
        body = (text or "").strip()
        if not body:
            return None
        if _NEW_PARAGRAPH.match(body):
            return ""
        if _NEW_LINE.match(body):
            return ""
        if _BULLET.match(body):
            return "-"
        low = body.lower().strip(" .!?")
        if low in PUNCTUATION:
            return PUNCTUATION[low]
        # Inline spoken punctuation: "the render finished comma finally full stop"
        for phrase in sorted(PUNCTUATION, key=len, reverse=True):
            body = re.sub(rf"\s+{re.escape(phrase)}\b", PUNCTUATION[phrase], body, flags=re.I)
        body = re.sub(r"\s+([.,;:!?])", r"\1", body).strip()
        if not body or body in ".,;:!?":
            return None
        body = body[0].upper() + body[1:]
        if body[-1] not in ".!?:,-)\"'":
            body += "."
        return body

    # ───────────────────────── sinks ─────────────────────────

    def _notes_dir(self) -> str:
        try:
            from tools_core import NotesTool

            return NotesTool.notes_dir()
        except Exception:
            path = os.path.join(DATA_DIR, "notes")
            os.makedirs(path, exist_ok=True)
            return path

    def _prepare(self, state: Session) -> Dict[str, Any]:
        """Open the sink, or refuse before a word is said into a void."""
        if state.sink == "notes":
            stamp = time.strftime("%Y-%m-%d-%H%M")
            slug = re.sub(r"[^a-z0-9]+", "-", (state.title or "dictated note").lower()).strip("-")
            state.path = os.path.join(self._notes_dir(), f"{stamp}-{slug[:40]}.md")
            state.header = (
                f"# {state.title or 'Dictated note'}\n\n"
                f"_{time.strftime('%Y-%m-%d %H:%M')}_\n\n"
            )
            return self._create(state.path, state.header)
        if state.sink == "file":
            import pathguard

            raw = state.target or os.path.join(self._notes_dir(), "dictation.md")
            if not os.path.isabs(os.path.expanduser(raw)):
                raw = os.path.join(self._notes_dir(), raw)
            allowed = pathguard.resolve_write(raw)
            if not allowed:
                return {"ok": False, "error": pathguard.denial(raw, write=True)}
            state.path = allowed
            created = self._create(allowed, "")
            # A file that already has something in it is appended to, and all of
            # that counts as the header as far as "scratch that" is concerned.
            try:
                with open(allowed, "r", encoding="utf-8", errors="ignore") as handle:
                    state.header = handle.read()
            except OSError:
                state.header = ""
            return created
        if state.sink == "clipboard":
            return {"ok": True}
        # Everything else is typed into an app, so the app has to be there.
        return self._prepare_window(state)

    _APP_SINKS = {
        "notepad": ("notepad", "notepad.exe"),
        "sticky": ("sticky notes", "ms-stk:"),
        "notion": ("notion", "notion"),
    }

    def _prepare_window(self, state: Session) -> Dict[str, Any]:
        import desktop_control as dc

        wanted = state.target or self._APP_SINKS.get(state.sink, (state.sink,))[0]
        state.target = wanted
        if dc.find_windows(wanted):
            return {"ok": True}
        if state.sink == "window":
            return {"ok": False,
                    "error": f"I can't see a window for '{wanted}'. Open it and ask me again."}
        launched = self._launch(state.sink)
        if not launched.get("ok"):
            return launched
        # Give it a moment to put a window up; report honestly if it does not.
        deadline = time.time() + 12
        while time.time() < deadline:
            if dc.find_windows(wanted):
                return {"ok": True}
            time.sleep(0.4)
        return {"ok": False,
                "error": f"I started {wanted} but no window appeared, so I haven't "
                         "written anything yet."}

    def _launch(self, sink: str) -> Dict[str, Any]:
        """Start the app a sink types into."""
        if sink == "notion":
            # The desktop app if it is installed; their Chrome if it is not.
            try:
                import app_index

                found = app_index.lookup("notion")
                if found:
                    os.startfile(found)  # noqa: S606 - a path from the app index
                    return {"ok": True}
            except Exception:
                pass
            try:
                import chrome_control

                chrome_control.open_url("https://www.notion.so")
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": f"I couldn't open Notion ({exc})."}
        target = self._APP_SINKS.get(sink, (sink, sink))[1]
        try:
            os.startfile(target)  # noqa: S606 - a fixed constant per sink
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": f"I couldn't start {sink} ({exc})."}

    def _create(self, path: str, header: str) -> Dict[str, Any]:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(header)
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": f"I couldn't open {path} to write into ({exc})."}

    def _write(self, state: Session, line: str) -> Dict[str, Any]:
        if state.sink in ("notes", "file"):
            try:
                with open(state.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n" if line else "\n")
                return {"ok": True}
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
        if state.sink == "clipboard":
            import desktop_control as dc

            return {"ok": dc._set_clipboard("\n".join(state.lines))}
        import desktop_control as dc

        try:
            dc.type_text(line + "\n" if line else "\n", window=state.target)
            return {"ok": True}
        except dc.DesktopError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def _rewrite(self, state: Session) -> None:
        """After "scratch that": rewrite a file sink; an app keeps a backspace."""
        if state.sink in ("notes", "file") and state.path:
            # The header plus the lines still held, rather than arithmetic on the
            # file's line count - which breaks the moment a header changes shape.
            try:
                body = state.header + "".join(line + "\n" for line in state.lines)
                with open(state.path, "w", encoding="utf-8") as handle:
                    handle.write(body)
            except OSError:
                pass
            return
        if state.sink == "clipboard":
            import desktop_control as dc

            dc._set_clipboard("\n".join(state.lines))
            return
        import desktop_control as dc

        try:
            # One line back: select it and delete it the way a person would.
            dc.press_keys("shift+up", window=state.target)
            dc.press_keys("delete", window=state.target)
        except Exception:
            pass

    def _index(self, path: str) -> None:
        """Make a finished note searchable straight away."""
        try:
            import knowledge_ingest

            knowledge_ingest.ingest_file(path, "notes", force=True)
        except Exception:
            pass


dictation = Dictation()
