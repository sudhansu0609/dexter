"""
Reminder and maintenance scheduler.

Dexter had no scheduler at all: the old day planner stored times as display strings
like "09:00 AM" that nothing ever parsed, so no reminder could ever fire. This
module adds a real APScheduler background job store plus the recurring
housekeeping the app needs (app index refresh, memory pruning).

When a reminder fires it goes out on all three channels at once - SSE for the
spoken reply and in-app banner, and a Windows toast that lands even if the
Dexter window is closed.

APScheduler is optional: without it reminders are still stored and listed, they
just do not fire. That degradation is reported through status() rather than
failing silently.
"""

import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import notify
from events import bus
from jsonstore import load_json, save_json
from timeparse import format_display, humanize, parse_recurrence, parse_when

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REMINDERS_FILE = os.path.join(DATA_DIR, "reminders.json")
PLANNER_LEAD_MINUTES = 5
REMINDER_RETENTION_DAYS = 7

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
                        reminder["firedAt"] = now.isoformat(timespec="seconds")
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

    # ───────────────────────── generic jobs (background tasks) ─────────────────────────

    def add_recurring_job(self, job_id: str, cron_kwargs: Dict[str, Any], fn, args=None) -> bool:
        """Arm a recurring callable (used by scheduled background goals)."""
        if not self._started:
            return False
        try:
            self._scheduler.add_job(
                fn, trigger=CronTrigger(**cron_kwargs), args=args or [],
                id=job_id, replace_existing=True, misfire_grace_time=600,
            )
            return True
        except Exception as exc:
            print(f"[Scheduler] Could not arm recurring job {job_id}: {exc}")
            return False

    def add_date_job(self, job_id: str, fire_at: datetime, fn, args=None) -> bool:
        """Arm a one-shot callable at a specific time."""
        if not self._started or fire_at <= datetime.now():
            return False
        try:
            self._scheduler.add_job(
                fn, trigger=DateTrigger(run_date=fire_at), args=args or [],
                id=job_id, replace_existing=True, misfire_grace_time=600,
            )
            return True
        except Exception as exc:
            print(f"[Scheduler] Could not arm job {job_id}: {exc}")
            return False

    def remove_job(self, job_id: str) -> None:
        if self._started:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass

    # ───────────────────────── planner integration ─────────────────────────

    def sync_commitment(self, commitment: Dict[str, Any]) -> bool:
        """Arm a heads-up a few minutes before a timed commitment due today."""
        due_date, due_time = commitment.get("due_date"), commitment.get("due_time")
        if not due_time or commitment.get("status") != "open":
            return False
        try:
            at = datetime.strptime(f"{due_date} {due_time}", "%Y-%m-%d %H:%M")
        except Exception:
            return False
        fire_at = at - timedelta(minutes=PLANNER_LEAD_MINUTES)
        if fire_at <= datetime.now() or not self._started:
            return False
        try:
            self._scheduler.add_job(
                self._fire_commitment,
                trigger=DateTrigger(run_date=fire_at),
                args=[int(commitment["id"]), commitment.get("text", ""), format_display(at)],
                id=f"commit-{commitment['id']}",
                replace_existing=True,
                misfire_grace_time=300,
            )
            return True
        except Exception as exc:
            print(f"[Scheduler] Could not arm commitment {commitment.get('id')}: {exc}")
            return False

    def _fire_commitment(self, commitment_id: int, text: str, when_display: str) -> None:
        from workbook import workbook

        commitment = workbook.get_commitment(commitment_id)
        if not commitment or commitment.get("status") != "open":
            return
        notify.toast_async("Dexter - Upcoming", f"{text} at {when_display}")
        bus.publish("reminder", {
            "id": f"commit-{commitment_id}",
            "text": f"{text} at {when_display}",
            "spoken": f"Coming up in {PLANNER_LEAD_MINUTES} minutes: {text}",
            "speak": False,
            "banner": True,
            "commitmentId": commitment_id,
        })

    def rearm_commitments(self) -> int:
        """Arm heads-ups for every timed commitment still ahead today."""
        from workbook import workbook

        armed = 0
        try:
            snapshot = workbook.today()
        except Exception:
            return 0
        for commitment in snapshot.get("due", []):
            if self.sync_commitment(commitment):
                armed += 1
        return armed

    def rearm_planner(self) -> int:
        """Kept for callers of the old name."""
        return self.rearm_commitments()

    # ───────────────────────── maintenance ─────────────────────────

    def _register_maintenance(self) -> None:
        self._scheduler.add_job(
            self._refresh_app_index, CronTrigger(day_of_week="sun", hour=4),
            id="maint-appindex", replace_existing=True,
        )
        self._scheduler.add_job(
            self._prune_memory, CronTrigger(hour=3, minute=30),
            id="maint-memory", replace_existing=True,
        )
        self._scheduler.add_job(
            self._reindex_knowledge, CronTrigger(hour=2, minute=30),
            id="maint-knowledge", replace_existing=True,
        )
        self._scheduler.add_job(
            self._morning_brief, CronTrigger(hour=6, minute=0),
            id="maint-brief", replace_existing=True,
        )
        self._scheduler.add_job(
            self._prune_reminders, CronTrigger(hour=3, minute=40),
            id="maint-reminders", replace_existing=True,
        )
        self._scheduler.add_job(
            self._reflect_evening, CronTrigger(hour=23, minute=40),
            id="maint-reflect", replace_existing=True,
        )
        self._scheduler.add_job(
            self._proactive_scan, CronTrigger(minute="3,18,33,48"),
            id="maint-proactive", replace_existing=True,
        )
        self._scheduler.add_job(
            self._roll_and_rearm_planner, CronTrigger(hour=0, minute=5),
            id="maint-rearm", replace_existing=True,
        )

        # ── the daily loop ──
        # A planner that only ever adds is a to-do list. The loop is what makes
        # it a practice: plan in the morning, close out in the evening, review
        # on Sunday. The evening prompt asks; the late job records regardless,
        # so a day is never silently lost the way the old midnight roll lost it.
        self._scheduler.add_job(
            self._evening_prompt,
            CronTrigger(hour=int(os.getenv("DEXTER_CLOSEOUT_HOUR", "21")), minute=0),
            id="loop-closeout-prompt", replace_existing=True,
        )
        self._scheduler.add_job(
            self._auto_close_out, CronTrigger(hour=23, minute=50),
            id="loop-closeout-auto", replace_existing=True,
        )
        self._scheduler.add_job(
            self._weekly_review, CronTrigger(day_of_week="sun", hour=18, minute=0),
            id="loop-weekly-review", replace_existing=True,
        )

    def _prune_reminders(self) -> int:
        """Fired one-shot reminders accumulated forever in reminders.json."""
        reminders = self._load()
        cutoff = datetime.now() - timedelta(days=REMINDER_RETENTION_DAYS)
        kept = []
        for reminder in reminders:
            if reminder.get("fired") and not reminder.get("cron"):
                stamp = reminder.get("firedAt") or reminder.get("at") or reminder.get("createdAt")
                try:
                    if stamp and datetime.fromisoformat(stamp) < cutoff:
                        continue
                except Exception:
                    pass
            kept.append(reminder)
        removed = len(reminders) - len(kept)
        if removed:
            self._save(kept)
            print(f"[Scheduler] Pruned {removed} fired reminder(s).")
        return removed

    def _roll_and_rearm_planner(self) -> None:
        # Just after midnight: arm heads-ups for the new day's timed commitments.
        self.rearm_commitments()

    def _reindex_knowledge(self) -> None:
        """Nightly: bring the folder index up to date (roadmap v5, 4.1)."""
        try:
            import knowledge_ingest

            stats = knowledge_ingest.reindex()
            print(
                f"[Scheduler] Knowledge reindex: {stats['scanned']} scanned, {stats['updated']} updated, "
                f"{stats['removed']} removed in {stats['seconds']}s."
            )
        except Exception as exc:
            print(f"[Scheduler] Knowledge reindex failed: {exc}")

    def _refresh_app_index(self) -> None:
        import app_index

        app_index.build(force=True)

    def _prune_memory(self) -> None:
        from memory import memory

        pruned = memory.prune()
        if pruned:
            print(f"[Scheduler] Pruned {pruned} old conversation turn(s).")

    def _proactive_scan(self) -> None:
        import proactive

        try:
            proactive.run_scan()
        except Exception as exc:
            print(f"[Scheduler] Proactive scan failed: {exc}")

    def _reflect_evening(self) -> None:
        """End-of-day reflection: summarize today, promote durable facts, and
        recap the week on Sundays. The startup catch-up covers machines that
        are already asleep by now."""
        import reflection

        try:
            reflection.summarize_day()
            reflection.extract_facts(hours=24)
            if datetime.now().weekday() == 6:
                reflection.summarize_week()
        except Exception as exc:
            print(f"[Scheduler] Evening reflection failed: {exc}")

    def _morning_brief(self) -> None:
        from workbook import workbook

        tasks: List[Dict[str, Any]] = []

        # What the user actually committed to, which is the thing a briefing is
        # about. The bare task count this used to open with told them nothing.
        try:
            snapshot = workbook.today()
            commitments = snapshot["due"]
            overdue = snapshot["overdue"]
            slipping = workbook.slipping()
        except Exception:
            commitments, overdue, slipping = [], [], []

        # Open with yesterday's episodic summary when there is one - the brief
        # should feel like a colleague catching you up, not a bare task count.
        yesterday_line = ""
        try:
            from memory import memory as _memory

            yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
            summary = _memory.get_summary("day", yesterday)
            if summary:
                yesterday_line = summary.split(". ")[0].rstrip(".") + "."
        except Exception:
            pass

        if not tasks and not yesterday_line and not commitments and not overdue:
            return

        if commitments:
            headline = f"{len(commitments)} commitment(s) today, starting with {commitments[0]['text']}"
        elif tasks:
            headline = f"{len(tasks)} task(s) today, starting with {tasks[0].get('title', '')}"
        else:
            headline = "Nothing committed today."

        spoken = "Good morning. "
        if yesterday_line:
            spoken += f"Yesterday: {yesterday_line} "
        spoken += f"{headline}."
        if overdue:
            spoken += f" You're still carrying {len(overdue)} from before."
        if slipping:
            worst = slipping[0]
            if worst.get("roll_count", 0) >= 3:
                spoken += f" {worst['text']} has moved {worst['roll_count']} times now."
        notify.toast_async("Dexter - Good morning", headline)
        bus.publish("reminder", {
            "id": "morning-brief",
            "text": headline,
            "spoken": spoken,
            "speak": True,
            "banner": True,
        })


    # ───────────────────────── the daily loop ─────────────────────────

    def _evening_prompt(self) -> None:
        """Ask to close out the day - never close it silently."""
        from workbook import workbook

        today = date.today().isoformat()
        if workbook.was_closed_out(today):
            return

        snapshot = workbook.today(today)
        open_items = snapshot["due"] + snapshot["overdue"]
        done = snapshot["done"]
        if not open_items and not done:
            return

        if open_items:
            headline = f"{len(done)} done, {len(open_items)} still open. Close out the day?"
        else:
            headline = f"All {len(done)} kept today. Close out the day?"

        notify.toast_async("Dexter - end of day", headline)
        bus.publish("reminder", {
            "id": "evening-closeout",
            "text": headline,
            "spoken": f"That's the day. {headline}",
            "speak": True,
            "banner": True,
        })

    def _auto_close_out(self) -> None:
        """Record the day even if nobody answered the prompt."""
        from workbook import workbook

        today = date.today().isoformat()
        if workbook.was_closed_out(today):
            return
        try:
            result = workbook.close_out(today, note="auto")
            if result["done"] or result["rolled"]:
                print(
                    f"[Loop] Auto-closed {today}: {result['done']} kept, "
                    f"{result['rolled']} rolled."
                )
        except Exception as exc:
            print(f"[Loop] Auto close-out failed: {exc}")

    def _weekly_review(self) -> None:
        from workbook import workbook

        try:
            card = workbook.scorecard()
        except Exception as exc:
            print(f"[Loop] Weekly review failed: {exc}")
            return

        if not card["committed"]:
            return

        headline = f"This week: {card['done']} of {card['committed']} commitments kept."
        spoken = headline
        untouched = [g["title"] for g in card["goals"] if g["untouched"]]
        if untouched:
            spoken += f" Nothing moved on {untouched[0]}."
        if card["slipping"]:
            spoken += f" {len(card['slipping'])} item(s) are slipping."
        spoken += " Want to run the full review?"

        notify.toast_async("Dexter - weekly review", headline)
        bus.publish("reminder", {
            "id": "weekly-review",
            "text": headline,
            "spoken": spoken,
            "speak": True,
            "banner": True,
        })


def job_id_safe(value: str) -> str:
    return f"task-{value}"


scheduler = DexterScheduler()
