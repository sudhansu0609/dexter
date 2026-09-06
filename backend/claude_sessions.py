"""
Reading Claude Code's own logs (roadmap v7, K8).

Claude Code keeps every conversation as JSONL under
~/.claude/projects/<folder-slug>/<session-id>.jsonl: user prompts, assistant
text, tool calls (Write/Edit/Bash...), the working directory, git branch, and
an AI-generated title. Dexter never runs Claude Code (buzzcode is the
coder); it reads these files so it knows what the owner did there and what
Claude changed, per session and per day: "Claude Code today: 3 sessions;
BuzzAnimate (rigging-sound...): 'Director or scenery not responding', 14
prompts, wrote rig.py, sound.py; ...".

Read-only. Nothing is written back. DEXTER_CLAUDE_LOG=0 turns it off.
"""

import json
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

ENABLED = os.environ.get("DEXTER_CLAUDE_LOG", "1").strip().lower() not in ("0", "false", "no", "off")
CLAUDE_PROJECTS = os.environ.get("CLAUDE_PROJECTS_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "projects")
ACTIVE_SECONDS = 180          # a transcript touched in the last three minutes = a session in progress
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_NOISE_PREFIXES = ("<local-command", "<command-name>", "<command-message>", "<system-reminder>", "[Request interrupted", "<task-notification", "<ide_")
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _user_text(content: Any) -> str:
    """The owner's own words in a user record; '' for tool results and injected noise."""
    texts: List[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text") or ""))
    text = "\n".join(t for t in texts if t).strip()
    if not text or text.startswith(_NOISE_PREFIXES):
        return ""
    # A pasted system reminder can follow the real words; keep the words.
    for marker in ("<system-reminder>", "<local-command-caveat>"):
        if marker in text:
            text = text.split(marker)[0].strip()
    return text


def parse_session(path: str) -> Dict[str, Any]:
    """One transcript, folded: who/where/when, prompts, files written, commands, last reply."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    info: Dict[str, Any] = {
        "id": os.path.splitext(os.path.basename(path))[0], "path": path, "cwd": "", "project": "", "branch": "",
        "title": "", "started": None, "last_active": mtime, "prompts": [], "files": [], "commands": [],
        "tools": {}, "last_reply": "", "turns": 0, "version": "",
    }
    tools: Counter = Counter()
    files: List[str] = []
    commands: List[str] = []
    prompts: List[str] = []
    last_reply = ""
    started: Optional[float] = None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                kind = record.get("type")
                if kind == "ai-title" and record.get("aiTitle"):
                    info["title"] = str(record["aiTitle"])[:120]
                    continue
                if record.get("isSidechain"):
                    continue
                if kind not in ("user", "assistant"):
                    continue
                if not info["cwd"] and record.get("cwd"):
                    info["cwd"] = record["cwd"]
                    info["project"] = os.path.basename(str(record["cwd"]).rstrip("\\/"))
                    info["branch"] = record.get("gitBranch") or ""
                    info["version"] = record.get("version") or ""
                stamp = record.get("timestamp")
                if stamp and started is None:
                    try:
                        started = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        started = None
                message = record.get("message") or {}
                content = message.get("content")
                if kind == "user":
                    text = _user_text(content)
                    if text:
                        prompts.append(text[:300])
                elif kind == "assistant" and isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and (block.get("text") or "").strip():
                            last_reply = block["text"].strip()[:600]
                        elif block.get("type") == "tool_use":
                            name = str(block.get("name") or "")
                            tools[name] += 1
                            args = block.get("input") or {}
                            if name in EDIT_TOOLS and isinstance(args, dict):
                                fp = args.get("file_path") or args.get("notebook_path")
                                if fp and fp not in files:
                                    files.append(str(fp))
                            elif name in ("Bash", "PowerShell") and isinstance(args, dict) and args.get("command"):
                                commands.append(str(args["command"]).strip().splitlines()[0][:100])
    except OSError:
        return {}
    info.update({
        "started": started, "prompts": prompts, "files": files, "commands": commands, "tools": dict(tools),
        "last_reply": last_reply, "turns": len(prompts),
    })
    if not info["title"] and prompts:
        info["title"] = prompts[0][:100]
    _cache[path] = (mtime, info)
    return info


def list_sessions(days: float = 7, project: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
    """Recent sessions, newest first, by transcript modification time."""
    if not ENABLED or not os.path.isdir(CLAUDE_PROJECTS):
        return []
    cutoff = time.time() - days * 86400
    candidates: List[Tuple[float, str]] = []
    try:
        for slug in os.listdir(CLAUDE_PROJECTS):
            folder = os.path.join(CLAUDE_PROJECTS, slug)
            if not os.path.isdir(folder):
                continue
            try:
                for name in os.listdir(folder):
                    if not name.endswith(".jsonl"):
                        continue
                    path = os.path.join(folder, name)
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    if mtime >= cutoff:
                        candidates.append((mtime, path))
            except OSError:
                continue
    except OSError:
        return []
    candidates.sort(reverse=True)
    out: List[Dict[str, Any]] = []
    needle = (project or "").lower()
    for _, path in candidates[: limit * 3]:
        info = parse_session(path)
        if not info or not info.get("turns"):
            continue
        if needle and needle not in info["project"].lower() and needle not in info["cwd"].lower():
            continue
        out.append(info)
        if len(out) >= limit:
            break
    return out


def active_session() -> Optional[Dict[str, Any]]:
    """The session whose transcript changed in the last few minutes, if any."""
    for info in list_sessions(days=1, limit=3):
        if time.time() - info["last_active"] <= ACTIVE_SECONDS:
            return info
    return None


def find_session(which: Optional[str]) -> Optional[Dict[str, Any]]:
    sessions = list_sessions(days=30, limit=60)
    if not sessions:
        return None
    needle = (which or "").strip().lower()
    if not needle or needle in ("last", "latest", "the last one", "most recent"):
        return sessions[0]
    for info in sessions:
        if needle in info["id"].lower() or needle in info["project"].lower() or needle in info["title"].lower():
            return info
    return None


# ───────────────────────── words ─────────────────────────


def _ago(ts: float) -> str:
    minutes = int((time.time() - ts) / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _day_window(when: str) -> Tuple[float, float, str]:
    when = (when or "today").lower()
    today = date.today()
    if when in ("yesterday",):
        day = today - timedelta(days=1)
        start = datetime.combine(day, datetime.min.time()).timestamp()
        return start, start + 86400, "Yesterday"
    if when in ("this week", "week"):
        start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time()).timestamp()
        return start, time.time(), "This week"
    start = datetime.combine(today, datetime.min.time()).timestamp()
    return start, time.time(), "Today"


def _session_line(info: Dict[str, Any], with_files: bool = True) -> str:
    where = info["project"] + (f" ({info['branch']})" if info.get("branch") else "")
    line = f"{where}: '{info['title']}' - {info['turns']} prompt(s), {_ago(info['last_active'])}"
    if with_files and info["files"]:
        names = [os.path.basename(f) for f in info["files"]]
        line += f"; wrote {', '.join(names[:5])}" + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    return line


def narrative(when: str = "today", project: Optional[str] = None) -> str:
    if not ENABLED:
        return "Reading Claude Code's logs is off (DEXTER_CLAUDE_LOG=0)."
    start, end, label = _day_window(when)
    sessions = [s for s in list_sessions(days=8, project=project, limit=40) if start <= s["last_active"] <= end]
    if not sessions:
        scope = f" on {project}" if project else ""
        return f"{label}: no Claude Code sessions{scope}."
    lines = [f"{label} in Claude Code: {len(sessions)} session(s)."]
    for info in sessions[:6]:
        lines.append(_session_line(info) + ".")
    files = sum(len(s["files"]) for s in sessions)
    commands = sum(len(s["commands"]) for s in sessions)
    if files or commands:
        lines.append(f"In total {files} file(s) written and {commands} command(s) run.")
    return " ".join(lines)


def session_story(info: Dict[str, Any]) -> str:
    parts = [f"Claude Code session on {info['project']}" + (f" ({info['branch']})" if info.get("branch") else "") + f": '{info['title']}', last active {_ago(info['last_active'])}."]
    if info["prompts"]:
        asked = "; ".join(p.replace("\n", " ")[:90] for p in info["prompts"][:5])
        parts.append(f"You asked: {asked}" + (f"; and {len(info['prompts']) - 5} more" if len(info["prompts"]) > 5 else "") + ".")
    if info["files"]:
        names = [os.path.basename(f) for f in info["files"]]
        parts.append(f"Files written: {', '.join(names[:8])}" + (f" and {len(names) - 8} more" if len(names) > 8 else "") + ".")
    if info["commands"]:
        parts.append(f"Commands run: {len(info['commands'])}, e.g. {info['commands'][-1]}.")
    if info["last_reply"]:
        parts.append(f"Claude's last words: {info['last_reply'][:300]}")
    return " ".join(parts)


def context_lines() -> List[str]:
    """For the model and the activity card: a live session, and today's sessions in one line."""
    if not ENABLED:
        return []
    lines: List[str] = []
    live = active_session()
    if live:
        last_prompt = live["prompts"][-1].replace("\n", " ")[:120] if live["prompts"] else ""
        lines.append(f"Claude Code is active on {live['project']}: '{live['title']}'" + (f"; last prompt: {last_prompt}" if last_prompt else "") + ".")
    start, _, _ = _day_window("today")
    today = [s for s in list_sessions(days=1, limit=10) if s["last_active"] >= start and (not live or s["id"] != live["id"])]
    if today:
        lines.append("Claude Code today: " + "; ".join(_session_line(s, with_files=False) for s in today[:4]) + ".")
    return lines


def now_fragment() -> str:
    live = active_session() if ENABLED else None
    return f"Claude Code on {live['project']}: {live['title'][:50]}" if live else ""
