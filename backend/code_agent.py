"""
Coding delegated to buzzcode (roadmap v6, C1).

buzzcode (B:\\youtubeProjects\\Buzzcaf_Media\\codeBuzz, installed as
~/.cargo/bin/buzzcode.exe) is the owner's local coding agent: it manages the
llama-server on port 8089 and edits a repository from a headless prompt,
emitting JSON-lines events. Dexter starts it as a background job in one of
the owner's own repositories, follows the events, and reports when it is
finished, so "fix the failing test in dexter" returns immediately and the
result arrives as a toast and a banner.

Permission modes map straight through: `edits` (default) lets it read and
write files but denies shell commands, `yolo` lets it run commands too and is
only used when the owner asks for it in so many words; `ask` would hang
headless and is refused.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

from events import bus
from jsonstore import load_json, save_json

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
JOBS_FILE = os.path.join(DATA_DIR, "code_jobs.json")
JOB_LOG_DIR = os.path.join(DATA_DIR, "code_jobs")
ENGINE_URL = (os.environ.get("LLAMACPP_URL") or "http://127.0.0.1:8089/v1").rstrip("/")
CODE_ROOTS = [
    p.strip() for p in (os.environ.get("DEXTER_CODE_ROOTS") or r"B:\youtubeProjects\Buzzcaf_Media").split(";") if p.strip()
]
MODES = {"edits": "edits", "edit": "edits", "yolo": "yolo"}
_PROJECT_MARKERS = (".git", "Cargo.toml", "package.json", "pyproject.toml", "requirements.txt", "go.mod")
_EDIT_TOOLS = {"write", "edit", "write_file", "edit_file", "create_file", "apply_patch", "multi_edit"}


def buzzcode_path() -> Optional[str]:
    configured = os.environ.get("BUZZCODE_BIN")
    if configured and os.path.exists(configured):
        return configured
    found = shutil.which("buzzcode")
    if found:
        return found
    cargo = os.path.join(os.path.expanduser("~"), ".cargo", "bin", "buzzcode.exe")
    return cargo if os.path.exists(cargo) else None


def known_projects() -> List[Dict[str, str]]:
    """Repositories under DEXTER_CODE_ROOTS (the owner's own checkouts)."""
    out: List[Dict[str, str]] = []
    for root in CODE_ROOTS:
        if os.path.isdir(root) and any(os.path.exists(os.path.join(root, m)) for m in _PROJECT_MARKERS):
            out.append({"name": os.path.basename(root.rstrip("\\/")), "path": root})
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            path = os.path.join(root, name)
            if name.startswith(".") or not os.path.isdir(path):
                continue
            if any(os.path.exists(os.path.join(path, m)) for m in _PROJECT_MARKERS):
                out.append({"name": name, "path": path})
    return out


def resolve_project(hint: Optional[str]) -> Optional[Dict[str, str]]:
    """A known folder name, exact or closest (case-insensitive); paths must be inside a code root."""
    hint = (hint or "").strip().strip("\"'")
    if not hint:
        return None
    projects = known_projects()
    if os.path.isdir(hint):
        full = os.path.abspath(hint)
        for p in projects:
            if os.path.normcase(full) == os.path.normcase(os.path.abspath(p["path"])):
                return p
        return None
    lower = hint.lower().replace(" ", "")
    for p in projects:
        if p["name"].lower() == lower:
            return p
    for p in projects:
        if lower in p["name"].lower() or p["name"].lower() in lower:
            return p
    import difflib

    names = [p["name"].lower() for p in projects]
    close = difflib.get_close_matches(lower, names, n=1, cutoff=0.6)
    if close:
        return projects[names.index(close[0])]
    return None


def project_from_text(text: str) -> Optional[Dict[str, str]]:
    """The first known project name mentioned anywhere in a sentence."""
    lower = (text or "").lower()
    best = None
    for p in known_projects():
        name = p["name"].lower()
        if len(name) >= 4 and name in lower:
            if best is None or len(name) > len(best["name"]):
                best = p
    return best


class CodeJob:
    def __init__(self, task: str, project: Dict[str, str], mode: str) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.task = task
        self.project = project
        self.mode = mode
        self.status = "running"
        self.started = time.time()
        self.finished: Optional[float] = None
        self.turns = 0
        self.tool_calls = 0
        self.files_changed: List[str] = []
        self.final_message = ""
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.finish_reason = ""
        self.diff_stat = ""
        self.exit_code: Optional[int] = None
        self.log_path = os.path.join(JOB_LOG_DIR, f"{self.id}.jsonl")
        self.process: Optional[subprocess.Popen] = None

    def ingest(self, line: str) -> Optional[Dict[str, Any]]:
        """Fold one JSON-lines event into the job; returns a UI-worthy event or None."""
        line = line.strip()
        if not line:
            return None
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        kind = event.get("type")
        if kind == "turn_start":
            self.turns = int(event.get("turn") or self.turns + 1)
        elif kind == "assistant_message":
            content = (event.get("content") or "").strip()
            if content:
                self.final_message = content
        elif kind == "tool_call_start":
            self.tool_calls += 1
            name = str(event.get("name") or "")
            args = event.get("arguments") or {}
            if name in _EDIT_TOOLS and isinstance(args, dict):
                path = args.get("path") or args.get("file") or args.get("file_path")
                if path and path not in self.files_changed:
                    self.files_changed.append(str(path))
            return {"kind": "tool", "name": name, "detail": _short_args(args)}
        elif kind == "warning":
            self.warnings.append(str(event.get("text") or ""))
        elif kind == "error":
            self.errors.append(str(event.get("text") or ""))
            return {"kind": "error", "detail": str(event.get("text") or "")[:200]}
        elif kind == "finished":
            self.finish_reason = str(event.get("reason") or "")
            self.turns = int(event.get("turns") or self.turns)
        elif kind == "verify":
            return {"kind": "verify", "ok": bool(event.get("ok")), "detail": str(event.get("command") or "")}
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "project": self.project,
            "mode": self.mode,
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
            "turns": self.turns,
            "toolCalls": self.tool_calls,
            "filesChanged": self.files_changed,
            "message": self.final_message,
            "errors": self.errors[-3:],
            "warnings": self.warnings[-3:],
            "finishReason": self.finish_reason,
            "diffStat": self.diff_stat,
            "exitCode": self.exit_code,
            "log": self.log_path,
        }

    def summary(self) -> str:
        head = f"Code task {self.id} ({self.project['name']}): {self.status}"
        if self.files_changed:
            head += f", {len(self.files_changed)} file(s) touched: " + ", ".join(os.path.basename(f) for f in self.files_changed[:6])
        if self.final_message:
            head += ". " + self.final_message[:400]
        if self.errors:
            head += " Errors: " + "; ".join(e[:120] for e in self.errors[-2:])
        return head


def _short_args(args: Any) -> str:
    if isinstance(args, dict):
        for key in ("path", "file", "command", "pattern", "query"):
            if key in args:
                return str(args[key])[:120]
    return ""


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class CodeAgent:
    def __init__(self) -> None:
        self.jobs: Dict[str, CodeJob] = {}
        self._lock = threading.Lock()
        self._engine_proc: Optional[subprocess.Popen] = None
        self.history: List[Dict[str, Any]] = load_json(JOBS_FILE, [])

    # ───────────────────────── engine ─────────────────────────

    def engine_status(self) -> Dict[str, Any]:
        try:
            res = requests.get(f"{ENGINE_URL}/models", timeout=2)
            if res.ok:
                data = res.json().get("data") or []
                model = data[0].get("id") if data else None
                return {"online": True, "model": model, "url": ENGINE_URL, "startedByDexter": self._engine_proc is not None}
        except Exception:
            pass
        return {"online": False, "model": None, "url": ENGINE_URL, "startedByDexter": self._engine_proc is not None}

    def start_engine(self, wait_seconds: int = 90) -> Dict[str, Any]:
        status = self.engine_status()
        if status["online"]:
            return {**status, "started": False}
        exe = buzzcode_path()
        if not exe:
            raise RuntimeError("buzzcode is not installed (expected ~/.cargo/bin/buzzcode.exe).")
        os.makedirs(JOB_LOG_DIR, exist_ok=True)
        log = open(os.path.join(JOB_LOG_DIR, "engine.log"), "ab")
        self._engine_proc = subprocess.Popen(
            [exe, "engine", "serve"], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=_no_window(),
        )
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self._engine_proc.poll() is not None:
                raise RuntimeError("buzzcode engine exited while starting; see data/code_jobs/engine.log.")
            status = self.engine_status()
            if status["online"]:
                return {**status, "started": True}
            time.sleep(2)
        return {**self.engine_status(), "started": True, "warming": True}

    def stop_engine(self) -> bool:
        if self._engine_proc is None:
            return False
        try:
            self._engine_proc.terminate()
        except Exception:
            pass
        self._engine_proc = None
        return True

    # ───────────────────────── jobs ─────────────────────────

    def running(self) -> List[CodeJob]:
        return [j for j in self.jobs.values() if j.status == "running"]

    def start(self, task: str, project: Dict[str, str], mode: str = "edits") -> CodeJob:
        task = (task or "").strip()
        if not task:
            raise ValueError("What should be coded?")
        mode = MODES.get((mode or "edits").lower())
        if mode is None:
            raise ValueError("Mode must be 'edits' (files only) or 'yolo' (files and commands).")
        exe = buzzcode_path()
        if not exe:
            raise RuntimeError("buzzcode is not installed (expected ~/.cargo/bin/buzzcode.exe).")
        if self.running():
            current = self.running()[0]
            raise RuntimeError(f"A code task is already running ({current.id}: {current.task[:60]}). Wait for it or cancel it.")
        job = CodeJob(task, project, mode)
        os.makedirs(JOB_LOG_DIR, exist_ok=True)
        env = dict(os.environ)
        env.setdefault("NO_COLOR", "1")
        job.process = subprocess.Popen(
            [exe, "-C", project["path"], "--mode", mode, "--json", "-p", task],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            creationflags=_no_window(), env=env, cwd=project["path"],
        )
        with self._lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._follow, args=(job,), name=f"dexter-code-{job.id}", daemon=True).start()
        bus.publish("code", {"jobId": job.id, "status": "started", "task": task, "project": project["name"]})
        return job

    def _follow(self, job: CodeJob) -> None:
        proc = job.process
        assert proc is not None and proc.stdout is not None
        last_publish = 0.0
        with open(job.log_path, "a", encoding="utf-8") as log:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                log.write(line)
                notable = job.ingest(line)
                now = time.time()
                if notable and now - last_publish > 1.5:
                    last_publish = now
                    bus.publish("code", {"jobId": job.id, "status": "running", **notable, "toolCalls": job.tool_calls})
        stderr = b""
        try:
            stderr = proc.stderr.read() if proc.stderr else b""
        except Exception:
            pass
        job.exit_code = proc.wait()
        job.finished = time.time()
        if stderr.strip():
            job.errors.append(stderr.decode("utf-8", errors="replace").strip()[-800:])
        if job.status == "running":
            job.status = "done" if job.exit_code == 0 and (job.final_message or not job.errors) else "failed"
        job.diff_stat = self._diff_stat(job.project["path"])
        self._remember(job)
        self._announce(job)

    def _diff_stat(self, path: str) -> str:
        if not os.path.isdir(os.path.join(path, ".git")):
            return ""
        try:
            out = subprocess.run(
                ["git", "diff", "--stat"], cwd=path, capture_output=True, text=True, timeout=15, creationflags=_no_window(),
            )
            return out.stdout.strip()[-1200:]
        except Exception:
            return ""

    def _remember(self, job: CodeJob) -> None:
        self.history.insert(0, job.to_dict())
        self.history = self.history[:40]
        save_json(JOBS_FILE, self.history)

    def _announce(self, job: CodeJob) -> None:
        try:
            import notify

            state = "finished" if job.status == "done" else "failed"
            notify.toast_async(f"Dexter - code task {state}", f"{job.project['name']}: {job.task[:80]}")
        except Exception:
            pass
        bus.publish("code", {"jobId": job.id, "status": job.status, "summary": job.summary(), "job": job.to_dict()})
        try:
            import companion

            companion.on_event("code_done", {**job.to_dict(), "project": job.project["name"]})
        except Exception as exc:
            print(f"[Code] companion message failed: {exc}")

    def cancel(self, job_id: Optional[str] = None) -> Optional[CodeJob]:
        job = self.jobs.get(job_id) if job_id else (self.running()[0] if self.running() else None)
        if not job or job.status != "running":
            return None
        job.status = "cancelled"
        try:
            if job.process:
                job.process.terminate()
        except Exception:
            pass
        return job

    def status(self, job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if job_id and job_id in self.jobs:
            return self.jobs[job_id].to_dict()
        if self.running():
            return self.running()[0].to_dict()
        latest = max(self.jobs.values(), key=lambda j: j.started, default=None)
        if latest:
            return latest.to_dict()
        return self.history[0] if self.history else None

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        live = sorted((j.to_dict() for j in self.jobs.values()), key=lambda d: d["started"], reverse=True)
        seen = {d["id"] for d in live}
        return (live + [h for h in self.history if h.get("id") not in seen])[:limit]


code_agent = CodeAgent()
