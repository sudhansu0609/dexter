"""
Coding delegated to buzzcode (roadmap v6, C1; own window and memory release, v8).

buzzcode (B:\\youtubeProjects\\Buzzcaf_Media\\BuzzCode, installed from
crates/buzzcode as ~/.cargo/bin/buzzcode.exe) is the owner's local coding agent: it manages the
llama-server on port 8089 and edits a repository from a one-shot prompt,
emitting JSON-lines events. Dexter opens it in its own terminal window
(code_console.py renders the events live and mirrors them into a job log),
tails that log, and reports when it is finished - so "fix the failing test in
dexter" returns immediately, the work is visible in the buzzcode window, and
the result arrives as a toast and a banner.

Memory: buzzcode stops the llama-server it started when the task ends, and
Dexter stops any engine it started itself, so the big model leaves the GPU
once the job is done. Set DEXTER_CODE_WINDOW=0 to go back to a hidden
background process.

Permission modes map straight through: `edits` (default) lets it read and
write files but denies shell commands, `yolo` lets it run commands too and is
only used when the owner asks for it in so many words; `ask` would hang in
one-shot mode and is refused.
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
#: buzzcode's llama-server. Not a constant any more: it steps forward from 8089
#: when something holds that port, and the ledger is how Dexter follows it
#: (GUARDIAN_PLAN.md section 11). `LLAMACPP_URL` still overrides everything.
from hybrid_llm import engine_url  # noqa: E402  - one resolver, two callers
CODE_ROOTS = [
    p.strip() for p in (os.environ.get("DEXTER_CODE_ROOTS") or r"B:\youtubeProjects\Buzzcaf_Media").split(";") if p.strip()
]
MODES = {"edits": "edits", "edit": "edits", "yolo": "yolo"}
# Open buzzcode in its own console window (default) instead of a hidden pipe.
WINDOWED = os.environ.get("DEXTER_CODE_WINDOW", "1").strip().lower() not in ("0", "false", "no", "off")
CONSOLE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "code_console.py")
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
        self.windowed = False
        self.launcher_exited = False

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
        elif kind == "launcher_exit":
            # Written by code_console.py once buzzcode has exited (the window
            # itself stays open for reading, so this is the real end of the job).
            self.launcher_exited = True
            self.exit_code = int(event.get("code") if event.get("code") is not None else 1)
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
            "windowed": self.windowed,
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


def _new_console() -> int:
    return getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if sys.platform == "win32" else 0


def console_python() -> Optional[str]:
    """A python.exe (not pythonw) to run code_console.py in a visible window."""
    exe = sys.executable or ""
    base = os.path.basename(exe).lower()
    if base.startswith("pythonw"):
        candidate = os.path.join(os.path.dirname(exe), base.replace("pythonw", "python", 1))
        exe = candidate if os.path.exists(candidate) else ""
    if exe and os.path.exists(exe):
        return exe
    return shutil.which("python") or shutil.which("python3")


def _kill_tree(proc: subprocess.Popen) -> None:
    """End a process and everything it spawned (the launcher, buzzcode, its llama-server)."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15, creationflags=_no_window(),
            )
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


class CodeAgent:
    def __init__(self) -> None:
        self.jobs: Dict[str, CodeJob] = {}
        self._lock = threading.Lock()
        self._engine_proc: Optional[subprocess.Popen] = None
        self.history: List[Dict[str, Any]] = load_json(JOBS_FILE, [])

    # ───────────────────────── engine ─────────────────────────

    def engine_status(self) -> Dict[str, Any]:
        from hybrid_llm import port_open

        if not port_open(engine_url()):
            return {"online": False, "model": None, "url": engine_url(), "startedByDexter": self._engine_proc is not None}
        try:
            res = requests.get(f"{engine_url()}/models", timeout=2)
            if res.ok:
                data = res.json().get("data") or []
                model = data[0].get("id") if data else None
                return {"online": True, "model": model, "url": engine_url(), "startedByDexter": self._engine_proc is not None}
        except Exception:
            pass
        return {"online": False, "model": None, "url": engine_url(), "startedByDexter": self._engine_proc is not None}

    def start_engine(self, wait_seconds: int = 90) -> Dict[str, Any]:
        status = self.engine_status()
        if status["online"]:
            return {**status, "started": False}
        exe = buzzcode_path()
        if not exe:
            raise RuntimeError("buzzcode is not installed (expected ~/.cargo/bin/buzzcode.exe).")
        # 12.1 GB of a 16 GB card. Ask before spawning, not after.
        decision = self._reserve_engine()
        if not decision.granted:
            raise RuntimeError(decision.message())
        os.makedirs(JOB_LOG_DIR, exist_ok=True)
        log = open(os.path.join(JOB_LOG_DIR, "engine.log"), "ab")
        self._engine_proc = subprocess.Popen(
            [exe, "engine", "serve"], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=_no_window(),
        )
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self._engine_proc.poll() is not None:
                self._release_booking()
                raise RuntimeError("buzzcode engine exited while starting; see data/code_jobs/engine.log.")
            status = self.engine_status()
            if status["online"]:
                # Consumed: the server holds the memory now, so the booking is
                # handed straight back rather than double-counting it.
                self._release_booking()
                return {**status, "started": True}
            time.sleep(2)
        return {**self.engine_status(), "started": True, "warming": True}

    def _release_booking(self) -> None:
        try:
            import model_steward

            model_steward.release("buzzcode-engine")
        except Exception:
            pass

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
        # Sentinel first, then the job. Refusing here is the whole point: two
        # parties both deciding there is room, and both then discovering there
        # is not, is how the card ends up paging and everything stalls.
        decision = self._reserve_engine()
        if not decision.granted:
            raise RuntimeError(decision.message())
        job = CodeJob(task, project, mode)
        os.makedirs(JOB_LOG_DIR, exist_ok=True)
        self._make_room()
        env = dict(os.environ)
        env.setdefault("NO_COLOR", "1")
        py = console_python() if WINDOWED else None
        if py:
            # Its own terminal: code_console.py runs buzzcode, shows the events
            # live and mirrors them into the job log that _follow_log tails.
            spec_path = os.path.join(JOB_LOG_DIR, f"{job.id}.json")
            save_json(spec_path, {
                "jobId": job.id, "exe": exe, "cwd": project["path"], "project": project["name"],
                "mode": mode, "task": task, "log": job.log_path,
            })
            open(job.log_path, "a", encoding="utf-8").close()
            job.process = subprocess.Popen(
                [py, "-X", "utf8", CONSOLE_SCRIPT, spec_path],
                creationflags=_new_console(), env=env, cwd=project["path"], close_fds=True,
            )
            job.windowed = True
            follow = self._follow_log
        else:
            job.process = subprocess.Popen(
                [exe, "-C", project["path"], "--mode", mode, "--json", "-p", task],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                creationflags=_no_window(), env=env, cwd=project["path"],
            )
            follow = self._follow
        with self._lock:
            self.jobs[job.id] = job
        threading.Thread(target=follow, args=(job,), name=f"dexter-code-{job.id}", daemon=True).start()
        bus.publish("code", {
            "jobId": job.id, "status": "started", "task": task, "project": project["name"], "windowed": job.windowed,
        })
        return job

    def _publish_progress(self, job: CodeJob, notable: Dict[str, Any], last_publish: float) -> float:
        now = time.time()
        if now - last_publish > 1.5:
            bus.publish("code", {"jobId": job.id, "status": "running", **notable, "toolCalls": job.tool_calls})
            return now
        return last_publish

    def _follow(self, job: CodeJob) -> None:
        """Hidden mode: buzzcode's stdout is a pipe; mirror it into the log."""
        proc = job.process
        assert proc is not None and proc.stdout is not None
        last_publish = 0.0
        with open(job.log_path, "a", encoding="utf-8") as log:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                log.write(line)
                notable = job.ingest(line)
                if notable:
                    last_publish = self._publish_progress(job, notable, last_publish)
        stderr = b""
        try:
            stderr = proc.stderr.read() if proc.stderr else b""
        except Exception:
            pass
        job.exit_code = proc.wait()
        if stderr.strip():
            job.errors.append(stderr.decode("utf-8", errors="replace").strip()[-800:])
        self._finish(job)

    def _follow_log(self, job: CodeJob) -> None:
        """Windowed mode: tail the job log the console launcher writes. The job
        ends at the launcher's exit record (the window stays open for reading)
        or when the window is closed early."""
        proc = job.process
        assert proc is not None
        last_publish = 0.0
        position = 0
        while True:
            try:
                with open(job.log_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(position)
                    lines = fh.readlines()
                    position = fh.tell()
            except OSError:
                lines = []
            for line in lines:
                notable = job.ingest(line)
                if notable:
                    last_publish = self._publish_progress(job, notable, last_publish)
            if job.launcher_exited or job.status != "running":
                break
            if proc.poll() is not None:
                if job.exit_code is None:
                    job.exit_code = proc.returncode
                    job.errors.append("the buzzcode window was closed before the task finished")
                break
            time.sleep(0.4)
        self._finish(job)

    def _finish(self, job: CodeJob) -> None:
        job.finished = time.time()
        if job.status == "running":
            job.status = "done" if job.exit_code == 0 and (job.final_message or not job.errors) else "failed"
        job.diff_stat = self._diff_stat(job.project["path"])
        self._remember(job)
        self._release_memory(job)
        self._announce(job)

    def _reserve_engine(self) -> Any:
        """Ask Sentinel for buzzcode's 27B *before* starting anything.

        buzzcode's llama-server wants ~12 GB of a 16 GB card plus a 6 GB cache.
        Starting it while VrDeep is rendering is how the machine locks up, so a
        refusal here refuses the job — with the holder named, not a shrug. The
        booking is `for_pid` the server that will consume it once we know it;
        at task time we do not yet, so it is booked against Dexter and released
        when the engine is up or the job ends.
        """
        import model_steward

        decision = model_steward.reserve(
            "buzzcode-engine",
            model_steward.ENGINE_VRAM_MIB,
            model_steward.ENGINE_RAM_MIB,
            for_pid=self._engine_server_pid(),
            ttl_secs=180,
        )
        return decision

    def _engine_server_pid(self) -> Optional[int]:
        """The llama-server that will hold the memory, once it exists."""
        if self._engine_proc is not None and self._engine_proc.poll() is None:
            return self._engine_proc.pid
        return None

    def _make_room(self) -> None:
        """buzzcode's model wants most of the GPU: unload Dexter's own models now
        (the steward decides which; the small fast one survives an orange)."""
        try:
            import model_steward

            threading.Thread(
                target=model_steward.shed, args=("red", "buzzcode is starting"),
                name="dexter-make-room", daemon=True,
            ).start()
        except Exception:
            pass

    def _release_memory(self, job: CodeJob) -> None:
        """The big model leaves the GPU once the job is over: buzzcode already
        stops the server it started; an engine Dexter started goes too, and the
        provider cache forgets 8089 so the next turn does not wait on it."""
        released = False
        self._release_booking()
        if self._engine_proc is not None and not self.running():
            released = self.stop_engine()
        try:
            from hybrid_llm import llm_router

            llm_router.invalidate_health()
        except Exception:
            pass
        if released:
            job.warnings.append("stopped the local model server Dexter had started")

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
        if job.process:
            _kill_tree(job.process)
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
