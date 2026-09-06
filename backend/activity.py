"""
Activity awareness (roadmap v7, K7): what the owner is working on right now.

Two cheap signals, sampled in a background thread:

- the foreground window every 30 s (app, window title, idle time), folded
  into sessions ("VS Code on dexter, 42 min") and daily totals by app and
  by project;
- the files most recently modified under the owner's folders (the knowledge
  roots plus B:\\youtubeProjects), refreshed every few minutes within a time
  budget, so "last saved router.py in dexter 3 min ago" is always at hand.

Everything stays on this machine: data/activity.jsonl, seven days, and the
"Activity awareness" setting (DEXTER_ACTIVITY=0) turns the sampler off.
Window titles are recorded as they are, so private tabs show up too; that is
the price of "you were on YouTube Studio for 40 minutes".
"""

import ctypes
import fnmatch
import json
import os
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOG_FILE = os.path.join(DATA_DIR, "activity.jsonl")
ENABLED = os.environ.get("DEXTER_ACTIVITY", "1").strip().lower() not in ("0", "false", "no", "off")
SAMPLE_SECONDS = 30
KEEP_DAYS = 7
AWAY_SECONDS = 300           # no input for five minutes = away
GAP_SECONDS = 150            # samples further apart than this end a session
FILES_WINDOW_HOURS = 6
FILES_REFRESH_SECONDS = 300
FILES_BUDGET_SECONDS = 3.0
EXTRA_ROOTS = [p for p in (os.environ.get("DEXTER_ACTIVITY_ROOTS") or r"B:\youtubeProjects").split(";") if p.strip()]

APP_NAMES = {
    "code.exe": "VS Code", "code - insiders.exe": "VS Code", "cursor.exe": "Cursor", "antigravity.exe": "Antigravity",
    "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "opera.exe": "Opera", "brave.exe": "Brave",
    "adobe premiere pro.exe": "Premiere Pro", "afterfx.exe": "After Effects", "photoshop.exe": "Photoshop",
    "adobe audition.exe": "Audition", "illustrator.exe": "Illustrator", "resolve.exe": "DaVinci Resolve",
    "wondershare filmora.exe": "Filmora", "filmora.exe": "Filmora", "capcut.exe": "CapCut", "obs64.exe": "OBS",
    "blender.exe": "Blender", "unrealeditor.exe": "Unreal Editor", "comfyui.exe": "ComfyUI",
    "explorer.exe": "Explorer", "windowsterminal.exe": "Terminal", "powershell.exe": "PowerShell",
    "pwsh.exe": "PowerShell", "cmd.exe": "Command Prompt", "pythonw.exe": "Dexter", "python.exe": "Python",
    "lm studio.exe": "LM Studio", "ollama app.exe": "Ollama", "notion.exe": "Notion", "obsidian.exe": "Obsidian",
    "notepad.exe": "Notepad", "notepad++.exe": "Notepad++", "winword.exe": "Word", "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint", "discord.exe": "Discord", "whatsapp.exe": "WhatsApp", "telegram.exe": "Telegram",
    "slack.exe": "Slack", "zoom.exe": "Zoom", "spotify.exe": "Spotify", "vlc.exe": "VLC", "mpc-hc64.exe": "MPC",
    "stremio.exe": "Stremio", "steam.exe": "Steam", "audacity.exe": "Audacity", "reaper.exe": "Reaper",
}
# Apps that are clearly "work" for the off-mission nudge.
WORK_APPS = {
    "VS Code", "Cursor", "Antigravity", "Premiere Pro", "After Effects", "Photoshop", "Audition", "Illustrator",
    "DaVinci Resolve", "Filmora", "CapCut", "OBS", "Blender", "Unreal Editor", "ComfyUI", "Terminal", "PowerShell",
    "Command Prompt", "Notion", "Obsidian", "Word", "Excel", "Audacity", "Reaper",
}
_TITLE_SEPARATORS = (" - ", " – ", " — ", " | ", " • ")


def friendly_app(exe: str) -> str:
    exe = (exe or "").strip()
    if not exe:
        return ""
    return APP_NAMES.get(exe.lower(), exe[:-4] if exe.lower().endswith(".exe") else exe)


# ───────────────────────── Windows probes ─────────────────────────


def foreground() -> Dict[str, Any]:
    """{app, exe, title, pid} for the window in focus; empty strings when nothing sensible is there."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        if pid.value:
            try:
                import psutil

                exe = psutil.Process(pid.value).name()
            except Exception:
                exe = ""
        return {"app": friendly_app(exe), "exe": exe, "title": title, "pid": int(pid.value)}
    except Exception:
        return {"app": "", "exe": "", "title": "", "pid": 0}


def idle_seconds() -> float:
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(millis, 0) / 1000.0
    except Exception:
        return 0.0


# ───────────────────────── projects ─────────────────────────

_projects_cache: Tuple[float, List[Tuple[str, str]]] = (0.0, [])


def known_projects() -> List[Tuple[str, str]]:
    """(name, path) for every folder that could be 'the project': code repos, channel folders, knowledge roots' children."""
    global _projects_cache
    if time.time() - _projects_cache[0] < 3600 and _projects_cache[1]:
        return _projects_cache[1]
    found: Dict[str, str] = {}
    try:
        from code_agent import known_projects as repos

        for p in repos():
            found.setdefault(p["name"], p["path"])
    except Exception:
        pass
    roots: List[str] = list(EXTRA_ROOTS)
    try:
        from knowledge_ingest import parse_roots

        roots += [path for _, path in parse_roots()]
    except Exception:
        pass
    for root in roots:
        try:
            for entry in os.scandir(root):
                if entry.is_dir() and not entry.name.startswith(".") and len(entry.name) >= 4:
                    found.setdefault(entry.name, entry.path)
        except OSError:
            continue
    projects = sorted(found.items(), key=lambda kv: -len(kv[0]))   # longest names first
    _projects_cache = (time.time(), projects)
    return projects


def project_for_title(title: str, projects: Optional[List[Tuple[str, str]]] = None) -> str:
    lower = (title or "").lower()
    if not lower:
        return ""
    for name, _ in projects if projects is not None else known_projects():
        if name.lower() in lower:
            return name
    return ""


def project_for_path(path: str, projects: Optional[List[Tuple[str, str]]] = None) -> str:
    norm = os.path.normcase(os.path.abspath(path or ""))
    best = ""
    for name, root in projects if projects is not None else known_projects():
        r = os.path.normcase(os.path.abspath(root)) + os.sep
        if norm.startswith(r) and len(r) > len(best):
            best = r
            best_name = name
    return best_name if best else ""


def _title_tail(title: str) -> str:
    """'router.py - dexter - Visual Studio Code' -> 'router.py - dexter'; the app name is already known."""
    t = title or ""
    for sep in _TITLE_SEPARATORS:
        if sep in t:
            parts = [p.strip() for p in t.split(sep) if p.strip()]
            if len(parts) > 1:
                t = sep.join(parts[:-1])
            break
    return t[:80]


# ───────────────────────── the log ─────────────────────────


class ActivityLog:
    def __init__(self, path: str = LOG_FILE) -> None:
        self.path = path
        self.samples: Deque[Dict[str, Any]] = deque(maxlen=int(KEEP_DAYS * 86400 / SAMPLE_SECONDS))
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._files: Tuple[float, List[Dict[str, Any]]] = (0.0, [])
        self._loaded = False

    # ── persistence ──

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        cutoff = time.time() - KEEP_DAYS * 86400
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        sample = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if float(sample.get("ts") or 0) >= cutoff:
                        self.samples.append(sample)
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"[Activity] Could not read the log: {exc}")

    def _persist(self, sample: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def compact(self) -> None:
        """Rewrite the file with only the last KEEP_DAYS (called nightly)."""
        with self._lock:
            keep = list(self.samples)
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                for sample in keep:
                    handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── sampling ──

    def add_sample(self, ts: float, app: str, title: str, idle: float = 0.0, project: Optional[str] = None, persist: bool = True) -> Dict[str, Any]:
        away = idle >= AWAY_SECONDS or not (app or title)
        sample = {
            "ts": round(ts, 1),
            "app": "" if away else app,
            "title": "" if away else _title_tail(title),
            "project": "" if away else (project if project is not None else project_for_title(title)),
            "away": away,
        }
        with self._lock:
            self.samples.append(sample)
        if persist:
            self._persist(sample)
        return sample

    def sample_now(self) -> Optional[Dict[str, Any]]:
        if not ENABLED:
            return None
        fg = foreground()
        return self.add_sample(time.time(), fg["app"], fg["title"], idle_seconds())

    def _loop(self) -> None:
        self.load()
        next_files = 0.0
        while True:
            try:
                self.sample_now()
                if time.time() >= next_files:
                    self.recent_files(force=True)
                    next_files = time.time() + FILES_REFRESH_SECONDS
            except Exception as exc:
                print(f"[Activity] sample failed: {exc}")
            time.sleep(SAMPLE_SECONDS)

    def start(self) -> None:
        if not ENABLED or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._loop, name="dexter-activity", daemon=True)
        self._thread.start()

    # ── reading it back ──

    def sessions(self, since: float, until: Optional[float] = None) -> List[Dict[str, Any]]:
        """Consecutive samples with the same app + project become one session."""
        until = until or time.time()
        with self._lock:
            rows = [s for s in self.samples if since <= s["ts"] <= until]
        out: List[Dict[str, Any]] = []
        for s in rows:
            if s.get("away"):
                continue
            last = out[-1] if out else None
            if last and last["app"] == s["app"] and last["project"] == s["project"] and s["ts"] - last["end"] <= GAP_SECONDS:
                last["end"] = s["ts"]
                last["title"] = s["title"] or last["title"]
                last["samples"] += 1
            else:
                out.append({"app": s["app"], "project": s["project"], "title": s["title"], "start": s["ts"], "end": s["ts"], "samples": 1})
        for session in out:
            session["minutes"] = max(1, round((session["end"] - session["start"] + SAMPLE_SECONDS) / 60))
        return out

    def totals(self, since: float, until: Optional[float] = None) -> Dict[str, Any]:
        apps: Dict[str, int] = {}
        projects: Dict[str, int] = {}
        titles: Dict[str, Dict[str, int]] = {}
        for s in self.sessions(since, until):
            apps[s["app"]] = apps.get(s["app"], 0) + s["minutes"]
            if s["project"]:
                projects[s["project"]] = projects.get(s["project"], 0) + s["minutes"]
            if s["title"]:
                titles.setdefault(s["app"], {})
                titles[s["app"]][s["title"]] = titles[s["app"]].get(s["title"], 0) + s["minutes"]
        top_apps = sorted(apps.items(), key=lambda kv: -kv[1])
        return {
            "minutes": sum(apps.values()),
            "apps": [{"app": a, "minutes": m, "top": sorted(titles.get(a, {}).items(), key=lambda kv: -kv[1])[:2]} for a, m in top_apps[:6]],
            "projects": [{"project": p, "minutes": m} for p, m in sorted(projects.items(), key=lambda kv: -kv[1])[:5]],
        }

    def current(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            last = self.samples[-1] if self.samples else None
        if not last or time.time() - last["ts"] > GAP_SECONDS or last.get("away"):
            return None
        sessions = self.sessions(time.time() - 8 * 3600)
        if not sessions:
            return None
        s = sessions[-1]
        return {"app": s["app"], "project": s["project"], "title": s["title"], "minutes": s["minutes"], "since": s["start"]}

    # ── files ──

    def recent_files(self, hours: float = FILES_WINDOW_HOURS, limit: int = 12, force: bool = False) -> List[Dict[str, Any]]:
        stamp, cached = self._files
        if cached and not force and time.time() - stamp < FILES_REFRESH_SECONDS:
            return cached[:limit]
        roots: List[str] = list(EXTRA_ROOTS)
        try:
            from knowledge_ingest import parse_roots

            roots += [path for _, path in parse_roots()]
        except Exception:
            pass
        files = scan_recent_files(roots, hours=hours, budget=FILES_BUDGET_SECONDS)
        self._files = (time.time(), files)
        return files[:limit]


def _excluded_dir(name: str) -> bool:
    try:
        from knowledge_ingest import EXCLUDE_DIRS, EXCLUDE_DIR_GLOBS
    except Exception:
        EXCLUDE_DIRS, EXCLUDE_DIR_GLOBS = {"node_modules", ".git", "__pycache__", "dist", "build", ".venv"}, (".*",)
    if name in EXCLUDE_DIRS or name.lower() in ("appdata", "temp", "tmp", "cache", "$recycle.bin", "system volume information"):
        return True
    return any(fnmatch.fnmatch(name, g) for g in EXCLUDE_DIR_GLOBS)


_SKIP_FILES = ("*.tmp", "*.log", "*.jsonl", "~$*", "*.lock", "*.pyc", "*.db-journal", "*.db-wal", "*.db-shm", ".DS_Store", "Thumbs.db")


def scan_recent_files(roots: List[str], hours: float = FILES_WINDOW_HOURS, budget: float = FILES_BUDGET_SECONDS, max_depth: int = 7) -> List[Dict[str, Any]]:
    """Newest-first files modified in the window, visiting recently changed folders first within a time budget."""
    cutoff = time.time() - hours * 3600
    deadline = time.time() + budget
    found: List[Tuple[float, str]] = []
    seen: set = set()
    queue: List[Tuple[float, int, str]] = []
    for root in roots:
        if root and os.path.isdir(root):
            try:
                queue.append((os.stat(root).st_mtime, 0, root))
            except OSError:
                continue
    while queue and time.time() < deadline:
        queue.sort(key=lambda item: -item[0])
        _, depth, folder = queue.pop(0)
        key = os.path.normcase(folder)
        if key in seen:
            continue
        seen.add(key)
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth < max_depth and not _excluded_dir(entry.name):
                                queue.append((entry.stat(follow_symlinks=False).st_mtime, depth + 1, entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            if any(fnmatch.fnmatch(entry.name, g) for g in _SKIP_FILES):
                                continue
                            mtime = entry.stat(follow_symlinks=False).st_mtime
                            if mtime >= cutoff:
                                found.append((mtime, entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    found.sort(key=lambda item: -item[0])
    projects = known_projects()
    now = time.time()
    return [
        {
            "path": path,
            "name": os.path.basename(path),
            "project": project_for_path(path, projects) or os.path.basename(os.path.dirname(path)),
            "minutes_ago": max(0, int((now - mtime) / 60)),
            "ext": os.path.splitext(path)[1].lower().lstrip("."),
        }
        for mtime, path in found[:40]
    ]


# ───────────────────────── what to say about it ─────────────────────────


def _mins(m: int) -> str:
    if m >= 60:
        h, r = divmod(m, 60)
        return f"{h}h{r:02d}" if r else f"{h}h"
    return f"{m} min"


def _day_bounds(day: date) -> Tuple[float, float]:
    start = datetime.combine(day, datetime.min.time()).timestamp()
    return start, start + 86400


def now_line(log: Optional["ActivityLog"] = None) -> str:
    """'VS Code on dexter · 42 min · last saved router.py 3 min ago' or ''."""
    log = log or activity_log
    parts = []
    current = log.current()
    if current:
        where = f"{current['app']}" + (f" on {current['project']}" if current["project"] else "")
        parts.append(f"{where} · {_mins(current['minutes'])}")
    files = log.recent_files(hours=3, limit=1)
    if files:
        f = files[0]
        parts.append(f"last saved {f['name']} ({f['project']}) {f['minutes_ago']} min ago" if f["minutes_ago"] else f"just saved {f['name']} ({f['project']})")
    try:
        import claude_sessions

        fragment = claude_sessions.now_fragment()
        if fragment:
            parts.append(fragment)
    except Exception:
        pass
    return " · ".join(parts)


def context_lines(log: Optional["ActivityLog"] = None) -> List[str]:
    """For the model: what is in focus, how long, what was touched, and today's shape."""
    log = log or activity_log
    lines: List[str] = []
    current = log.current()
    if current:
        title = f" ('{current['title']}')" if current["title"] else ""
        lines.append(f"In focus now: {current['app']}{' on ' + current['project'] if current['project'] else ''}{title} for {_mins(current['minutes'])}.")
    files = log.recent_files(hours=3, limit=4)
    if files:
        lines.append("Recently saved: " + "; ".join(f"{f['name']} in {f['project']} ({f['minutes_ago']} min ago)" for f in files))
    try:
        import claude_sessions

        lines.extend(claude_sessions.context_lines())
    except Exception:
        pass
    start, _ = _day_bounds(date.today())
    totals = log.totals(start)
    if totals["minutes"]:
        apps = ", ".join(f"{a['app']} {_mins(a['minutes'])}" for a in totals["apps"][:4])
        projects = ", ".join(f"{p['project']} {_mins(p['minutes'])}" for p in totals["projects"][:3])
        lines.append(f"Today so far ({_mins(totals['minutes'])} active): {apps}" + (f"; by project: {projects}" if projects else "") + ".")
    return lines


def narrative(when: str = "now", log: Optional["ActivityLog"] = None) -> str:
    """The spoken answer to 'what am I working on' / 'what did I do yesterday'."""
    log = log or activity_log
    when = (when or "now").lower()
    if when in ("now", "right now", "currently"):
        current = log.current()
        files = log.recent_files(hours=3, limit=3)
        if not current and not files:
            return "I can't see anything in focus right now, and nothing in your folders has changed in the last few hours."
        parts = []
        if current:
            where = f"{current['app']}" + (f" on {current['project']}" if current["project"] else "")
            title = f", window '{current['title']}'" if current["title"] else ""
            parts.append(f"You're in {where}{title}, {_mins(current['minutes'])} so far.")
        try:
            import claude_sessions

            live = claude_sessions.active_session()
            if live:
                parts.append(f"Claude Code is open on {live['project']}: '{live['title']}'.")
        except Exception:
            pass
        if files:
            parts.append("Recently saved: " + "; ".join(f"{f['name']} in {f['project']} ({f['minutes_ago']} min ago)" for f in files) + ".")
        start, _ = _day_bounds(date.today())
        totals = log.totals(start)
        if totals["projects"]:
            parts.append("Most of today went to " + ", ".join(f"{p['project']} ({_mins(p['minutes'])})" for p in totals["projects"][:2]) + ".")
        return " ".join(parts)
    if when in ("yesterday",):
        start, end = _day_bounds(date.today() - timedelta(days=1))
        label = "Yesterday"
    elif when in ("this morning", "morning"):
        start, _ = _day_bounds(date.today())
        end = start + 12 * 3600
        label = "This morning"
    elif when in ("this week", "week"):
        start, _ = _day_bounds(date.today() - timedelta(days=date.today().weekday()))
        end = time.time()
        label = "This week"
    else:
        start, end = _day_bounds(date.today())
        end = min(end, time.time())
        label = "Today"
    totals = log.totals(start, end)
    if not totals["minutes"]:
        return f"{label}: I don't have any activity recorded."
    apps = "; ".join(
        f"{a['app']} {_mins(a['minutes'])}" + (f" (mostly {a['top'][0][0]})" if a["top"] else "") for a in totals["apps"][:4]
    )
    text = f"{label}: {_mins(totals['minutes'])} active. {apps}."
    if totals["projects"]:
        text += " By project: " + ", ".join(f"{p['project']} {_mins(p['minutes'])}" for p in totals["projects"][:3]) + "."
    return text


def brief(log: Optional["ActivityLog"] = None) -> Dict[str, Any]:
    log = log or activity_log
    start, _ = _day_bounds(date.today())
    return {
        "enabled": ENABLED,
        "now": log.current(),
        "idleMinutes": int(idle_seconds() // 60),
        "nowLine": now_line(log),
        "today": log.totals(start),
        "recentFiles": log.recent_files(hours=3, limit=8),
    }


activity_log = ActivityLog()
