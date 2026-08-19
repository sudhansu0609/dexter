"""
Reminder and maintenance scheduler.

Dexter had no scheduler at all: day_planner.json stored times as display strings
like "09:00 AM" that nothing ever parsed, so no reminder could ever fire. This
module adds a real APScheduler background job store plus the recurring
housekeeping the app needs (audio cleanup, app index refresh, memory pruning).

When a reminder fires it goes out on all three channels at once - SSE for the
spoken reply and in-app banner, and a Windows toast that lands even if the
Dexter window is closed.

APScheduler is optional: without it reminders are still stored and listed, they
just do not fire. That degradation is reported through status() rather than
failing silently.
"""

import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import notify
from events import bus
from jsonstore import load_json, save_json
from timeparse import format_display, humanize, parse_recurrence, parse_when

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REMINDERS_FILE = os.path.join(DATA_DIR, "reminders.json")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")

AUDIO_MAX_AGE_SECONDS = 2 * 3600
PLANNER_LEAD_MINUTES = 5

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger

    _HAS_APS = True
except ImportError:
    BackgroundScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore
    DateTrigger = None  # type: ignore
    _HAS_APS = False


class DexterScheduler:
    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler(daemon=True) if _HAS_APS else None
        self._started = False

    # ───────────────────────── lifecycle ─────────────────────────

    def start(self) -> None:
        if not _HAS_APS:
            print("[Scheduler] APScheduler not installed - reminders will not fire. "
                  "Run: pip install apscheduler")
            return
        if self._started:
            return

        self._scheduler.start()
        self._started = True

        self._register_maintenance()
        restored = self._restore_reminders()
        print(f"[Scheduler] Started. Restored {restored} pending reminder(s).")

    def shutdown(self) -> None:
        if self._scheduler and self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False

    @property
    def running(self) -> bool:
        return self._started

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._started,
            "available": _HAS_APS,
            "jobs": len(self._scheduler.get_jobs()) if self._started else 0,
            "pendingReminders": len(self.list_reminders(include_fired=False)),
            "toastChannel": notify.available(),
        }

    # ───────────────────────── persistence ─────────────────────────

    def _load(self) -> List[Dict[str, Any]]:
        data = load_json(REMINDERS_FILE, [])
        return data if isinstance(data, list) else []

    def _save(self, reminders: List[Dict[str, Any]]) -> None:
        save_json(REMINDERS_FILE, reminders)

    def list_reminders(self, include_fired: bool = True) -> List[Dict[str, Any]]:
        reminders = self._load()
        if not include_fired:
            reminders = [r for r in reminders if not r.get("fired")]
        return sorted(reminders, key=lambda r: r.get("at") or "")

    # ───────────────────────── reminders ─────────────────────────

    def create_reminder(
        self, text: str, when_text: str, channels: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Schedule a reminder from a natural-language time.

        Returns a dict with `ok` False when the time could not be understood, so
        the caller can say something useful instead of silently doing nothing.
        """
        recurrence = parse_recurrence(when_text)
        fire_at = None if recurrence else parse_when(when_text)

        if not recurrence and not fire_at:
            return {
                "ok": False,
                "error": f"I couldn't work out when '{when_text}' is.",
            }

        reminder = {
            "id": f"r-{int(time.time() * 1000)}",
            "text": text.strip(),
            "whenText": when_text,
            "at": fire_at.isoformat(timespec="seconds") if fire_at else None,
            "cron": recurrence,
            "channels": channels or ["voice", "toast", "banner"],
            "createdAt": datetime.now().isoformat(timespec="seconds"),
            "fired": False,
        }

        reminders = self._load()
        reminders.append(reminder)
        self._save(reminders)

        scheduled = self._schedule(reminder)
        reminder["scheduled"] = scheduled
        reminder["ok"] = True
        reminder["humanWhen"] = (
            humanize(fire_at) if fire_at else f"repeating ({when_text})"
        )
        return reminder

    def cancel_reminder(self, reminder_id: str) -> bool:
        reminders = self._load()
        remaining = [r for r in reminders if r.get("id") != reminder_id]
        if len(remaining) == len(reminders):
            return False
        self._save(remaining)
        if self._started:
            try:
                self._scheduler.remove_job(reminder_id)
            except Exception:
                pass
        return True

    def cancel_by_text(self, fragment: str) -> Optional[Dict[str, Any]]:
        fragment = (fragment or "").lower().strip()
        for reminder in self.list_reminders(include_fired=False):
            if fragment and fragment in reminder.get("text", "").lower():
                self.cancel_reminder(reminder["id"])
                return reminder
        return None

    def _schedule(self, reminder: Dict[str, Any]) -> bool:
        if not self._started:
            return False
        try:
            if reminder.get("cron"):
                trigger = CronTrigger(**reminder["cron"])
            else:
                fire_at = datetime.fromisoformat(reminder["at"])
                if fire_at <= datetime.now():
                    return False
                trigger = DateTrigger(run_date=fire_at)

            self._scheduler.add_job(
                self._fire,
                trigger=trigger,
                args=[reminder["id"]],
                id=reminder["id"],
                replace_existing=True,
                misfire_grace_time=300,
            )
            return True
        except Exception as exc:
            print(f"[Scheduler] Could not schedule {reminder.get('id')}: {exc}")
            return False

    def _restore_reminders(self) -> int:
        """Re-arm reminders that survived a restart; drop the ones already past."""
        reminders = self._load()
        restored = 0
        now = datetime.now()
        changed = False

        for reminder in reminders:
            if reminder.get("fired") and not reminder.get("cron"):
                continue
            if reminder.get("at") and not reminder.get("cron"):
                try:
                    if datetime.fromisoformat(reminder["at"]) <= now:
                        # Missed while Dexter was down - surface it once, now.
                        reminder["fired"] = True
                        changed = True
                        self._deliver(reminder, missed=True)
                        continue
                except Exception:
                    continue
            if self._schedule(reminder):
                restored += 1

        if changed:
            self._save(reminders)
        return restored

    def _fire(self, reminder_id: str) -> None:
        reminders = self._load()
        reminder = next((r for r in reminders if r.get("id") == reminder_id), None)
        if not reminder:
            return

        if not reminder.get("cron"):
            reminder["fired"] = True
            reminder["firedAt"] = datetime.now().isoformat(timespec="seconds")
            self._save(reminders)

        self._deliver(reminder)

    def _deliver(self, reminder: Dict[str, Any], missed: bool = False) -> None:
        """Push a fired reminder out on every enabled channel."""
        text = reminder.get("text", "Reminder")
        channels = reminder.get("channels") or ["voice", "toast", "banner"]
        prefix = "You asked me to remind you" if not missed else "While I was away"
        spoken = f"{prefix}: {text}"

        if "toast" in channels:
            notify.toast_async("Dexter Reminder", text, urgent=not missed)

        # One SSE event drives both the spoken reply and the in-app banner; the
        # frontend decides which of those the user has enabled.
        bus.publish(
            "reminder",
            {
                "id": reminder.get("id"),
                "text": text,
                "spoken": spoken,
                "speak": "voice" in channels,
                "banner": "banner" in channels,
                "missed": missed,
                "at": reminder.get("at"),
            },
        )
        print(f"[Scheduler] Fired reminder: {text}")

    # ───────────────────────── planner integration ─────────────────────────

    def sync_planner_task(self, task: Dict[str, Any]) -> bool:
        """Arm a reminder a few minutes before a scheduled day-planner task."""
        if not task.get("at") or task.get("completed"):
            return False
        try:
            task_at = datetime.fromisoformat(task["at"])
        except Exception:
            return False

        fire_at = task_at - timedelta(minutes=PLANNER_LEAD_MINUTES)
        if fire_at <= datetime.now() or not self._started:
            return False

        job_id = f"task-{task['id']}"
        try:
            self._scheduler.add_job(
                self._fire_planner_task,
                trigger=DateTrigger(run_date=fire_at),
                args=[task["id"], task.get("title", ""), format_display(task_at)],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=300,
            )
            return True
        except Exception as exc:
            print(f"[Scheduler] Could not arm planner task {task.get('id')}: {exc}")
            return False

    def _fire_planner_task(self, task_id: str, title: str, when_display: str) -> None:
        from tools_core import day_planner

        task = day_planner.get_task(task_id)
        if not task or task.get("completed"):
            return

        notify.toast_async("Dexter - Upcoming", f"{title} at {when_display}")
        bus.publish(
            "reminder",
            {
                "id": job_id_safe(task_id),
                "text": f"{title} at {when_display}",
                "spoken": f"Coming up in {PLANNER_LEAD_MINUTES} minutes: {title}",
                "speak": True,
                "banner": True,
                "taskId": task_id,
            },
        )

    def rearm_planner(self) -> int:
        """Arm reminders for every future task on today's plan."""
        from tools_core import day_planner

        armed = 0
        for task in day_planner.get_agenda().get("tasks", []):
            if self.sync_planner_task(task):
                armed += 1
        return armed

    # ───────────────────────── maintenance ─────────────────────────

    def _register_maintenance(self) -> None:
        self._scheduler.add_job(
            self._cleanup_audio, CronTrigger(minute=17),
            id="maint-audio", replace_existing=True,
        )
        self._scheduler.add_job(
            self._refresh_app_index, CronTrigger(day_of_week="sun", hour=4),
            id="maint-appindex", replace_existing=True,
        )
        self._scheduler.add_job(
            self._prune_memory, CronTrigger(hour=3, minute=30),
            id="maint-memory", replace_existing=True,
        )
        self._scheduler.add_job(
            self._morning_brief, CronTrigger(hour=6, minute=0),
            id="maint-brief", replace_existing=True,
        )
        self._scheduler.add_job(
            self.rearm_planner, CronTrigger(hour=0, minute=5),
            id="maint-rearm", replace_existing=True,
        )

    def _cleanup_audio(self) -> int:
        """Generated TTS files accumulated forever; nothing ever removed them."""
        if not os.path.isdir(AUDIO_DIR):
            return 0
        cutoff = time.time() - AUDIO_MAX_AGE_SECONDS
        removed = 0
        for filename in os.listdir(AUDIO_DIR):
            path = os.path.join(AUDIO_DIR, filename)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except Exception:
                pass
        if removed:
            print(f"[Scheduler] Cleaned up {removed} old audio file(s).")
        return removed

    def _refresh_app_index(self) -> None:
        import app_index

        app_index.build(force=True)

    def _prune_memory(self) -> None:
        from memory import memory

        pruned = memory.prune()
        if pruned:
            print(f"[Scheduler] Pruned {pruned} old conversation turn(s).")

    def _morning_brief(self) -> None:
        from tools_core import day_planner

        tasks = [t for t in day_planner.get_agenda().get("tasks", []) if not t.get("completed")]
        if not tasks:
            return
        headline = f"{len(tasks)} task(s) today, starting with {tasks[0].get('title', '')}"
        notify.toast_async("Dexter - Good morning", headline)
        bus.publish("reminder", {
            "id": "morning-brief",
            "text": headline,
            "spoken": f"Good morning. {headline}.",
            "speak": True,
            "banner": True,
        })


def job_id_safe(value: str) -> str:
    return f"task-{value}"


scheduler = DexterScheduler()
