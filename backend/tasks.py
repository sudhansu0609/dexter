"""
Background tasks: "handle it while I do something else."

A task is a goal ("check my calendar and email, then brief me") that Dexter
plans into steps and works through on a worker thread, publishing progress on
the SSE bus so the UI's task tray and the voice channel can follow along.

Design choices:

  * Plan-then-execute, one router call per step. Each step goes through the
    same three-tier router as a spoken command, so every guard (validation,
    confirmations, tool selection) applies unchanged. Pausing for a
    confirmation is then trivial - the step returns needs_confirmation, the
    task parks in waiting_confirmation, and approval re-sends "yes" into the
    same task-scoped session that is holding the pending call.
  * One worker thread. Two tasks driving the same desktop at once fight over
    focus, clipboard and audio; sequential is a feature, not a limitation.
  * Guardrails: bounded steps, bounded total tool calls, and a full audit
    trail (plan, per-step results, timestamps) persisted to data/tasks.json.

Scheduled goals ("every friday at 5, curate ideas and email me the list")
persist to data/scheduled_tasks.json and re-arm on boot via the scheduler.
"""

import os
import queue
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from events import bus
from hybrid_llm import extract_json, llm_router
from jsonstore import load_json, save_json
from timeparse import humanize, parse_recurrence, parse_when

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")
SCHEDULED_FILE = os.path.join(DATA_DIR, "scheduled_tasks.json")

MAX_PLAN_STEPS = 8
MAX_TOOL_CALLS = 16
MAX_STORED_TASKS = 50
CONFIRMATION_TIMEOUT_SECONDS = 30 * 60  # an unanswered checkpoint fails the task

PLAN_PROMPT = (
    "You break a user's goal into the fewest concrete steps for an assistant "
    "that can use tools (apps, files, calendar, email, reminders, media, "
    "notes). Each step is one imperative instruction the assistant can act on "
    "directly. Simple goals are ONE step. Never exceed {max_steps} steps.\n"
    'Respond with ONLY JSON: {{"steps": ["...", "..."]}}\n\nGoal: {goal}'
)


class TaskQueue:
    def __init__(self) -> None:
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._lock = threading.Lock()
        self._confirm_events: Dict[str, threading.Event] = {}
        self._confirm_answers: Dict[str, bool] = {}
        self._cancelled: set = set()
        self._worker: Optional[threading.Thread] = None

    # ───────────────────────── persistence ─────────────────────────

    def _load(self) -> List[Dict[str, Any]]:
        data = load_json(TASKS_FILE, [])
        return data if isinstance(data, list) else []

    def _save(self, tasks: List[Dict[str, Any]]) -> None:
        save_json(TASKS_FILE, tasks[:MAX_STORED_TASKS])

    def _update(self, task_id: str, **fields) -> Optional[Dict[str, Any]]:
        with self._lock:
            tasks = self._load()
            record = next((t for t in tasks if t.get("id") == task_id), None)
            if not record:
                return None
            record.update(fields)
            self._save(tasks)
        self._publish(record)
        return record

    def _append_progress(self, task_id: str, text: str) -> None:
        with self._lock:
            tasks = self._load()
            record = next((t for t in tasks if t.get("id") == task_id), None)
            if not record:
                return
            record.setdefault("progress", []).append(
                {"ts": datetime.now().isoformat(timespec="seconds"), "text": text[:300]}
            )
            self._save(tasks)
        self._publish(record)

    def _publish(self, record: Dict[str, Any]) -> None:
        bus.publish("task", {
            "id": record.get("id"),
            "title": record.get("title"),
            "status": record.get("status"),
            "progress": (record.get("progress") or [])[-3:],
            "question": record.get("question"),
            "result": record.get("result"),
        })

    # ───────────────────────── public API ─────────────────────────

    def submit(self, goal: str, session: str = "default", source: str = "user") -> Dict[str, Any]:
        goal = (goal or "").strip()
        if not goal:
            return {"ok": False, "error": "Empty goal."}
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        record = {
            "id": task_id,
            "title": goal[:120],
            "goal": goal,
            "status": "queued",
            "source": source,
            "requestedBy": session,
            "createdAt": datetime.now().isoformat(timespec="seconds"),
            "plan": [],
            "progress": [],
            "toolCallsUsed": 0,
        }
        with self._lock:
            tasks = self._load()
            tasks.insert(0, record)
            self._save(tasks)
        self._publish(record)
        self._ensure_worker()
        self._queue.put(task_id)
        return {"ok": True, "id": task_id, "title": record["title"]}

    def list(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._load()[:limit]

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self._load() if t.get("id") == task_id), None)

    def cancel(self, task_id: str) -> bool:
        record = self.get(task_id)
        if not record or record.get("status") in ("done", "failed", "cancelled"):
            return False
        self._cancelled.add(task_id)
        # Unblock a task parked on a confirmation.
        event = self._confirm_events.get(task_id)
        if event:
            self._confirm_answers[task_id] = False
            event.set()
        if record.get("status") == "queued":
            self._update(task_id, status="cancelled",
                         finishedAt=datetime.now().isoformat(timespec="seconds"))
        return True

    def confirm(self, task_id: str, approve: bool) -> bool:
        """Answer a waiting checkpoint. Returns False when nothing is waiting."""
        event = self._confirm_events.get(task_id)
        if not event:
            return False
        self._confirm_answers[task_id] = bool(approve)
        event.set()
        return True

    def waiting_task(self) -> Optional[Dict[str, Any]]:
        """The single waiting-confirmation task, if there is exactly one."""
        waiting = [t for t in self._load() if t.get("status") == "waiting_confirmation"]
        return waiting[0] if len(waiting) == 1 else None

    def try_voice_confirmation(self, message: str) -> Optional[Dict[str, Any]]:
        """
        Let a spoken "yes"/"no" answer a background task's checkpoint when no
        chat confirmation is pending and exactly one task is waiting - so the
        user can approve by voice without knowing task ids exist.
        """
        import router as intent_router

        text = (message or "").strip()
        if intent_router._has_pending("default"):
            return None  # the chat's own confirmation takes precedence
        record = self.waiting_task()
        if not record:
            return None
        if intent_router._AFFIRMATIVE.match(text):
            self.confirm(record["id"], True)
            return {"reply": f"Go-ahead given - continuing '{record['title']}'.",
                    "action": "task_confirmed", "tier": 0, "toolCalls": [],
                    "details": {"taskId": record["id"]}, "needsConfirmation": False}
        if intent_router._NEGATIVE.match(text):
            self.confirm(record["id"], False)
            return {"reply": f"Understood - stopping '{record['title']}'.",
                    "action": "task_denied", "tier": 0, "toolCalls": [],
                    "details": {"taskId": record["id"]}, "needsConfirmation": False}
        return None

    # ───────────────────────── execution ─────────────────────────

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._work_loop, name="dexter-task-worker", daemon=True
        )
        self._worker.start()

    def _work_loop(self) -> None:
        while True:
            task_id = self._queue.get()
            if task_id is None:
                return
            try:
                self._run_task(task_id)
            except Exception as exc:
                self._update(task_id, status="failed", result=f"Crashed: {exc}",
                             finishedAt=datetime.now().isoformat(timespec="seconds"))
                self._announce(task_id, f"Background task failed: {exc}")

    def _plan(self, goal: str) -> List[str]:
        raw = llm_router.generate(
            PLAN_PROMPT.format(goal=goal, max_steps=MAX_PLAN_STEPS),
            system_prompt="You plan tasks as JSON. No commentary.",
            temperature=0.2,
            max_tokens=1024,
        )
        parsed = extract_json(raw)
        steps: List[str] = []
        if isinstance(parsed, dict) and isinstance(parsed.get("steps"), list):
            steps = [str(s).strip() for s in parsed["steps"] if str(s).strip()]
        return steps[:MAX_PLAN_STEPS] or [goal]

    def _run_task(self, task_id: str) -> None:
        import router as intent_router

        record = self.get(task_id)
        if not record or task_id in self._cancelled:
            return
        session = f"task:{task_id}"
        self._update(task_id, status="running",
                     startedAt=datetime.now().isoformat(timespec="seconds"))

        steps = self._plan(record["goal"])
        self._update(task_id, plan=steps)
        self._append_progress(task_id, f"Planned {len(steps)} step(s).")

        tool_calls_used = 0
        outcomes: List[str] = []

        def on_event(event_type: str, payload: Dict[str, Any]) -> None:
            if event_type == "tool_start":
                self._append_progress(task_id, f"Running {payload.get('name', 'tool')}...")

        for index, step in enumerate(steps, 1):
            if task_id in self._cancelled:
                self._update(task_id, status="cancelled", result="; ".join(outcomes) or None,
                             finishedAt=datetime.now().isoformat(timespec="seconds"))
                return
            if tool_calls_used >= MAX_TOOL_CALLS:
                self._finish(task_id, outcomes,
                             failed_note=f"stopped at the {MAX_TOOL_CALLS} tool-call limit")
                return

            context = f"You are executing step {index} of {len(steps)} of a background task.\n" \
                      f"Overall goal: {record['goal']}"
            if outcomes:
                context += "\nResults so far:\n" + "\n".join(f"- {o}" for o in outcomes[-4:])

            result = intent_router.route(step, session=session, context_note=context,
                                         on_event=on_event)
            tool_calls_used += max(len(result.tool_calls), 1)
            self._update(task_id, toolCallsUsed=tool_calls_used)

            # A destructive tool inside the step: park until the user answers.
            if result.needs_confirmation:
                approved = self._await_confirmation(task_id, result.reply)
                if approved is not True:
                    verdict = "denied" if approved is False else "timed out"
                    intent_router.route("no", session=session)  # clear the pending call
                    self._update(task_id, status="cancelled", question=None,
                                 result=f"Checkpoint {verdict} at step {index}: {step}",
                                 finishedAt=datetime.now().isoformat(timespec="seconds"))
                    self._announce(task_id, f"Task stopped - checkpoint {verdict}.")
                    return
                self._update(task_id, status="running", question=None)
                result = intent_router.route("yes", session=session, on_event=on_event)
                tool_calls_used += max(len(result.tool_calls), 1)

            outcomes.append(result.reply)
            self._append_progress(task_id, f"Step {index}/{len(steps)}: {result.reply[:160]}")

        self._finish(task_id, outcomes)

    def _await_confirmation(self, task_id: str, question: str) -> Optional[bool]:
        event = threading.Event()
        self._confirm_events[task_id] = event
        self._update(task_id, status="waiting_confirmation", question=question)
        self._announce(task_id, f"I need a go-ahead: {question}", urgent=True)
        answered = event.wait(CONFIRMATION_TIMEOUT_SECONDS)
        self._confirm_events.pop(task_id, None)
        if not answered:
            return None
        return self._confirm_answers.pop(task_id, False)

    def _finish(self, task_id: str, outcomes: List[str], failed_note: str = "") -> None:
        summary = outcomes[-1] if outcomes else "No steps produced output."
        if failed_note:
            self._update(task_id, status="failed", result=f"{summary} ({failed_note})",
                         finishedAt=datetime.now().isoformat(timespec="seconds"))
            self._announce(task_id, f"Background task stopped: {failed_note}.")
            return
        self._update(task_id, status="done", result=summary,
                     finishedAt=datetime.now().isoformat(timespec="seconds"))
        record = self.get(task_id)
        title = record["title"] if record else "task"
        self._announce(task_id, f"Done with '{title}'. {summary}")

    def _announce(self, task_id: str, spoken: str, urgent: bool = False) -> None:
        """Voice + banner via the reminder channel the frontend already handles."""
        bus.publish("reminder", {
            "id": f"{task_id}-note-{int(time.time())}",
            "text": spoken,
            "spoken": spoken,
            "speak": True,
            "banner": True,
            "missed": not urgent,
            "taskId": task_id,
        })


# ───────────────────────── scheduled goals ─────────────────────────


def _load_scheduled() -> List[Dict[str, Any]]:
    data = load_json(SCHEDULED_FILE, [])
    return data if isinstance(data, list) else []


def _save_scheduled(items: List[Dict[str, Any]]) -> None:
    save_json(SCHEDULED_FILE, items)


def _entry_action(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The action for an entry, defaulting a legacy bare-goal entry to a goal."""
    action = entry.get("action")
    if isinstance(action, dict) and action.get("type"):
        return action
    return {"type": "goal", "goal": entry.get("goal", "")}


def action_summary(action: Dict[str, Any]) -> str:
    """A short human label for an action - used by the UI list and by voice."""
    kind = (action or {}).get("type", "goal")
    if kind == "goal":
        return action.get("goal", "").strip() or "a task"
    if kind == "command":
        return f"run: {action.get('command', '').strip()[:80]}"
    if kind == "app":
        return f"open {action.get('app', '').strip()}"
    if kind == "workflow":
        return f"run workflow {action.get('projectId', '').strip()}"
    if kind == "code_task":
        proj = action.get("project")
        return f"code: {action.get('task', '').strip()[:70]}" + (f" ({proj})" if proj else "")
    return kind


def _run_action(entry: Dict[str, Any]) -> None:
    """Dispatch a fired scheduled entry to the right subsystem.

    Every branch reuses an existing capability rather than reimplementing it, and
    the whole thing is best-effort: a failing action must never take down the
    scheduler thread. A banner goes out either way so a scheduled action is never
    silent.
    """
    action = _entry_action(entry)
    kind = action.get("type", "goal")
    label = action_summary(action)
    detail = ""
    try:
        if kind == "goal":
            task_queue.submit(action.get("goal", ""), source=f"schedule:{entry['id']}")
            detail = "started"
        elif kind == "command":
            import shlex
            import subprocess

            cmd = (action.get("command") or "").strip()
            if not cmd:
                raise ValueError("empty command")
            shell = (action.get("shell") or "powershell").lower()
            if os.name == "nt" and shell == "powershell":
                argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd]
            elif os.name == "nt":
                argv = ["cmd", "/c", cmd]
            else:
                argv = shlex.split(cmd)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            subprocess.Popen(argv, creationflags=flags)  # fire-and-forget
            detail = "launched"
        elif kind == "app":
            from tools_core import AppLauncherTool

            result = AppLauncherTool.launch_app(action.get("app", ""))
            detail = "opened" if (result or {}).get("success", True) else (result or {}).get("error", "failed")
        elif kind == "workflow":
            from buzzcaf_client import buzzcaf_client

            buzzcaf_client.execute_step(action.get("projectId", ""), action.get("feedback"))
            detail = "workflow step run"
        elif kind == "code_task":
            import tools_code

            res = tools_code.code_task(
                action.get("task", ""), action.get("project"), action.get("mode", "edits")
            )
            detail = "started" if getattr(res, "ok", True) else getattr(res, "message", "failed")
        else:
            raise ValueError(f"unknown action type {kind!r}")
        ok = True
    except Exception as exc:
        ok, detail = False, str(exc)
        print(f"[Scheduler] Scheduled action {entry.get('id')} ({kind}) failed: {exc}")

    spoken = f"Scheduled: {label}" + (f" - {detail}" if detail else "")
    try:
        import notify

        notify.toast_async("Dexter - scheduled", label)
    except Exception:
        pass
    bus.publish("reminder", {
        "id": f"sched-{entry.get('id')}",
        "text": label,
        "spoken": spoken,
        "speak": False,
        "banner": True,
        "ok": ok,
    })


def _fire_scheduled(entry_id: str) -> None:
    entry = next((e for e in _load_scheduled() if e.get("id") == entry_id), None)
    if not entry:
        return
    _run_action(entry)
    items = _load_scheduled()
    for item in items:
        if item.get("id") == entry_id:
            item["lastRun"] = datetime.now().isoformat(timespec="seconds")
    # One-shot entries retire after firing.
    if not entry.get("cron"):
        items = [i for i in items if i.get("id") != entry_id]
    _save_scheduled(items)


def _arm(entry: Dict[str, Any]) -> bool:
    from scheduler import scheduler

    job_id = f"goal-{entry['id']}"
    if entry.get("cron"):
        return scheduler.add_recurring_job(job_id, entry["cron"], _fire_scheduled, [entry["id"]])
    try:
        fire_at = datetime.fromisoformat(entry["at"])
    except Exception:
        return False
    return scheduler.add_date_job(job_id, fire_at, _fire_scheduled, [entry["id"]])


def create_scheduled(action: Dict[str, Any], when_text: str) -> Dict[str, Any]:
    """Schedule any action (goal / command / app / workflow / code_task).

    This is the one place a scheduled entry is born - the voice path
    (`schedule_goal`) and the REST path both funnel through here so cron vs
    one-shot parsing, persistence and arming stay in a single spot.
    """
    action = action or {}
    kind = action.get("type", "goal")
    if kind == "goal" and not (action.get("goal") or "").strip():
        return {"ok": False, "error": "Empty goal."}
    if kind == "command" and not (action.get("command") or "").strip():
        return {"ok": False, "error": "Empty command."}
    if kind == "app" and not (action.get("app") or "").strip():
        return {"ok": False, "error": "Which app?"}
    if kind == "code_task" and not (action.get("task") or "").strip():
        return {"ok": False, "error": "What should be coded?"}

    recurrence = parse_recurrence(when_text)
    fire_at = None if recurrence else parse_when(when_text)
    if not recurrence and not fire_at:
        return {"ok": False, "error": f"I couldn't work out when '{when_text}' is."}

    entry = {
        "id": uuid.uuid4().hex[:10],
        "action": action,
        # Keep `goal` populated for legacy readers when the action is a goal.
        "goal": action.get("goal", "") if kind == "goal" else action_summary(action),
        "whenText": when_text,
        "cron": recurrence,
        "at": fire_at.isoformat(timespec="seconds") if fire_at else None,
        "createdAt": datetime.now().isoformat(timespec="seconds"),
    }
    items = _load_scheduled()
    items.append(entry)
    _save_scheduled(items)
    armed = _arm(entry)
    human = humanize(fire_at) if fire_at else f"repeating ({when_text})"
    return {"ok": True, "id": entry["id"], "when": human, "armed": armed, "entry": entry}


def schedule_goal(goal: str, when_text: str) -> Dict[str, Any]:
    """Voice-schedulable automation: recurring cron or one-shot datetime."""
    return create_scheduled({"type": "goal", "goal": (goal or "").strip()}, when_text)


def update_scheduled(entry_id: str, action: Optional[Dict[str, Any]] = None,
                     when_text: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Edit a scheduled entry's action and/or time, then re-arm it."""
    from scheduler import scheduler

    items = _load_scheduled()
    entry = next((e for e in items if e.get("id") == entry_id), None)
    if not entry:
        return None

    if action:
        entry["action"] = action
        entry["goal"] = action.get("goal", "") if action.get("type") == "goal" else action_summary(action)
    if when_text is not None:
        recurrence = parse_recurrence(when_text)
        fire_at = None if recurrence else parse_when(when_text)
        if not recurrence and not fire_at:
            return {"ok": False, "error": f"I couldn't work out when '{when_text}' is."}
        entry["whenText"] = when_text
        entry["cron"] = recurrence
        entry["at"] = fire_at.isoformat(timespec="seconds") if fire_at else None

    _save_scheduled(items)
    scheduler.remove_job(f"goal-{entry_id}")
    entry["armed"] = _arm(entry)
    entry["ok"] = True
    return entry


def run_now(entry_id: str) -> bool:
    """Fire a scheduled entry immediately without disturbing its schedule."""
    entry = next((e for e in _load_scheduled() if e.get("id") == entry_id), None)
    if not entry:
        return False
    _run_action(entry)
    return True


def list_scheduled() -> List[Dict[str, Any]]:
    return _load_scheduled()


def cancel_scheduled_by_id(entry_id: str) -> bool:
    """Cancel one scheduled entry by id (the REST/UI path)."""
    from scheduler import scheduler

    items = _load_scheduled()
    remaining = [e for e in items if e.get("id") != entry_id]
    if len(remaining) == len(items):
        return False
    _save_scheduled(remaining)
    scheduler.remove_job(f"goal-{entry_id}")
    return True


def cancel_scheduled(fragment: str) -> Optional[Dict[str, Any]]:
    from scheduler import scheduler

    fragment = (fragment or "").strip().lower()
    items = _load_scheduled()
    match = next(
        (e for e in items if fragment and fragment in (e.get("goal", "") or "").lower()),
        None,
    )
    if not match:
        return None
    _save_scheduled([e for e in items if e.get("id") != match["id"]])
    scheduler.remove_job(f"goal-{match['id']}")
    return match


def rearm_scheduled() -> int:
    """Re-arm persisted goals after a restart; drop stale one-shots."""
    armed = 0
    keep: List[Dict[str, Any]] = []
    for entry in _load_scheduled():
        if not entry.get("cron"):
            try:
                if datetime.fromisoformat(entry["at"]) <= datetime.now():
                    continue  # missed while offline; goals are not reminders - drop
            except Exception:
                continue
        keep.append(entry)
        if _arm(entry):
            armed += 1
    _save_scheduled(keep)
    return armed


task_queue = TaskQueue()
