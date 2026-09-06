"""
The workbook: what you are trying to do, and what actually happened.

This is the thing Dexter was missing. It could execute commands beautifully and
remember facts semantically, but it held no model of the user's work over time -
no goals, no projects, no commitments, no history of what got done. So it could
not plan (only list today), could not coach (nothing to hold you to), could not
work for you (no project to carry), and could not be a second brain about your
actual life (only about floating facts).

The day planner it replaces was a flat list of today's {time, title, completed}.
Completed items were erased at midnight and incomplete ones rolled forward
forever, unchanged and unremarked - an accumulating guilt list with no memory.

    Goal         the outcome you want           "ship the channel relaunch"
     └─ Project  a body of work under a goal    "record 8 videos"
         └─ Commitment  something you said      "script ep.3 by Friday"
             └─ Entry   what actually happened  "worked 90m, blocked on b-roll"

Four roles, one store:

    planner       today() and week()
    coach         slipping(), close_out(), scorecard()
    employee      commitments carry across days and can be worked in background
    second brain  entries are the record of what happened, embedded for recall

SQLite from the standard library, next to memory.py, for the same reasons: real
atomicity, indexed lookups, no new dependency. Relationships are the entire
point here, which is why this is not a jsonstore file.
"""

import os
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import env_boot  # noqa: F401  - loads .env for every entry point

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "dexter_workbook.db")

# A commitment that has been rolled this many times has stopped being a plan and
# started being a decision you keep declining to make. Dexter says so.
STALE_ROLL_COUNT = 3

# Statuses. Kept as plain strings: they are read straight into spoken replies.
GOAL_STATUSES = ("active", "paused", "done", "dropped")
COMMITMENT_STATUSES = ("open", "done", "dropped")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    why         TEXT,
    status      TEXT    NOT NULL DEFAULT 'active',
    target_date TEXT,
    created_at  REAL    NOT NULL,
    closed_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);

CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id    INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    title      TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'active',
    created_at REAL    NOT NULL,
    closed_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_projects_goal ON projects(goal_id, status);

CREATE TABLE IF NOT EXISTS commitments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    goal_id      INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    text         TEXT    NOT NULL,
    due_date     TEXT,
    due_time     TEXT,
    status       TEXT    NOT NULL DEFAULT 'open',
    roll_count   INTEGER NOT NULL DEFAULT 0,
    created_at   REAL    NOT NULL,
    done_at      REAL,
    first_due    TEXT
);
CREATE INDEX IF NOT EXISTS idx_commitments_due ON commitments(status, due_date);

CREATE TABLE IF NOT EXISTS entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    commitment_id INTEGER REFERENCES commitments(id) ON DELETE SET NULL,
    project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    goal_id       INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    kind          TEXT    NOT NULL DEFAULT 'progress',
    text          TEXT    NOT NULL,
    minutes       INTEGER,
    on_date       TEXT    NOT NULL,
    created_at    REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(on_date);

CREATE TABLE IF NOT EXISTS closeouts (
    on_date    TEXT PRIMARY KEY,
    done       INTEGER NOT NULL DEFAULT 0,
    rolled     INTEGER NOT NULL DEFAULT 0,
    dropped    INTEGER NOT NULL DEFAULT 0,
    note       TEXT,
    created_at REAL    NOT NULL
);
"""


def _today() -> str:
    return date.today().isoformat()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class Workbook:
    def __init__(self, path: str = DB_PATH) -> None:
        self._path = path
        self._local = threading.local()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = self._connect()
        with conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        """One connection per thread - sqlite3 objects are not thread-safe."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    # ───────────────────────── goals ─────────────────────────

    def add_goal(self, title: str, why: str = "", target_date: Optional[str] = None) -> Dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("A goal needs a title.")
        conn = self._connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO goals (title, why, status, target_date, created_at) "
                "VALUES (?, ?, 'active', ?, ?)",
                (title, why.strip() or None, target_date, time.time()),
            )
        return self.get_goal(cur.lastrowid)  # type: ignore[arg-type]

    def get_goal(self, goal_id: int) -> Dict[str, Any]:
        row = self._connect().execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return _row_to_dict(row) if row else {}

    def find_goal(self, needle: str) -> Optional[Dict[str, Any]]:
        """Match a goal the way a person would refer to it: loosely, by name."""
        needle = (needle or "").strip().lower()
        if not needle:
            return None
        for goal in self.list_goals(status=None):
            title = goal["title"].lower()
            if needle == title or needle in title or title in needle:
                return goal
        # Fall back to word overlap so "the relaunch" finds "channel relaunch".
        words = set(needle.split())
        best, best_score = None, 0
        for goal in self.list_goals(status=None):
            score = len(words & set(goal["title"].lower().split()))
            if score > best_score:
                best, best_score = goal, score
        return best if best_score else None

    def list_goals(self, status: Optional[str] = "active") -> List[Dict[str, Any]]:
        conn = self._connect()
        if status:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = ? ORDER BY created_at", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM goals ORDER BY created_at").fetchall()
        return [_row_to_dict(r) for r in rows]

    def set_goal_status(self, goal_id: int, status: str) -> Dict[str, Any]:
        if status not in GOAL_STATUSES:
            raise ValueError(f"Unknown goal status: {status}")
        closed = time.time() if status in ("done", "dropped") else None
        conn = self._connect()
        with conn:
            conn.execute(
                "UPDATE goals SET status = ?, closed_at = ? WHERE id = ?",
                (status, closed, goal_id),
            )
        return self.get_goal(goal_id)

    # ───────────────────────── projects ─────────────────────────

    def add_project(self, title: str, goal_id: Optional[int] = None) -> Dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("A project needs a title.")
        conn = self._connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO projects (goal_id, title, status, created_at) "
                "VALUES (?, ?, 'active', ?)",
                (goal_id, title, time.time()),
            )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)

    def list_projects(self, goal_id: Optional[int] = None, status: Optional[str] = "active") -> List[Dict[str, Any]]:
        conn = self._connect()
        clauses, params = [], []
        if goal_id is not None:
            clauses.append("goal_id = ?")
            params.append(goal_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(f"SELECT * FROM projects {where} ORDER BY created_at", params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def find_project(self, needle: str) -> Optional[Dict[str, Any]]:
        needle = (needle or "").strip().lower()
        if not needle:
            return None
        for project in self.list_projects(status=None):
            if needle in project["title"].lower():
                return project
        return None

    # ───────────────────────── commitments ─────────────────────────

    def add_commitment(
        self,
        text: str,
        due_date: Optional[str] = None,
        due_time: Optional[str] = None,
        project_id: Optional[int] = None,
        goal_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("A commitment needs to say what you are committing to.")
        due_date = due_date or _today()

        # Inherit the goal from the project so every commitment can be rolled up
        # without the caller having to know the hierarchy.
        if goal_id is None and project_id is not None:
            row = self._connect().execute(
                "SELECT goal_id FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row:
                goal_id = row["goal_id"]

        conn = self._connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO commitments (project_id, goal_id, text, due_date, due_time, "
                "status, roll_count, created_at, first_due) "
                "VALUES (?, ?, ?, ?, ?, 'open', 0, ?, ?)",
                (project_id, goal_id, text, due_date, due_time, time.time(), due_date),
            )
        return self.get_commitment(cur.lastrowid)  # type: ignore[arg-type]

    def get_commitment(self, commitment_id: int) -> Dict[str, Any]:
        row = self._connect().execute(
            "SELECT * FROM commitments WHERE id = ?", (commitment_id,)
        ).fetchone()
        return _row_to_dict(row) if row else {}

    def list_commitments(
        self,
        status: Optional[str] = "open",
        due_on: Optional[str] = None,
        due_before: Optional[str] = None,
        goal_id: Optional[int] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if due_on:
            clauses.append("due_date = ?")
            params.append(due_on)
        if due_before:
            clauses.append("due_date < ?")
            params.append(due_before)
        if goal_id is not None:
            clauses.append("goal_id = ?")
            params.append(goal_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connect().execute(
            f"SELECT * FROM commitments {where} ORDER BY due_date, due_time, id LIMIT ?",
            params + [limit],
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def find_commitment(self, needle: str, status: Optional[str] = "open") -> Optional[Dict[str, Any]]:
        """Loose match, because people refer to their own commitments loosely."""
        needle = (needle or "").strip().lower()
        if not needle:
            return None
        candidates = self.list_commitments(status=status)
        for commitment in candidates:
            if needle in commitment["text"].lower():
                return commitment
        words = set(needle.split())
        best, best_score = None, 0
        for commitment in candidates:
            score = len(words & set(commitment["text"].lower().split()))
            if score > best_score:
                best, best_score = commitment, score
        return best if best_score else None

    def complete_commitment(self, commitment_id: int, note: str = "") -> Dict[str, Any]:
        conn = self._connect()
        with conn:
            conn.execute(
                "UPDATE commitments SET status = 'done', done_at = ? WHERE id = ?",
                (time.time(), commitment_id),
            )
        commitment = self.get_commitment(commitment_id)
        self.log_entry(
            note or f"Completed: {commitment.get('text', '')}",
            commitment_id=commitment_id,
            kind="done",
        )
        return commitment

    def drop_commitment(self, commitment_id: int, why: str = "") -> Dict[str, Any]:
        conn = self._connect()
        with conn:
            conn.execute("UPDATE commitments SET status = 'dropped' WHERE id = ?", (commitment_id,))
        self.log_entry(why or "Dropped.", commitment_id=commitment_id, kind="dropped")
        return self.get_commitment(commitment_id)

    def roll_commitment(self, commitment_id: int, to_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Move a commitment to another day, and *count* it.

        The count is the whole point. The old planner rolled silently forever, so
        a task you had been avoiding for two weeks looked exactly like one you
        wrote down this morning.
        """
        to_date = to_date or (date.today() + timedelta(days=1)).isoformat()
        conn = self._connect()
        with conn:
            conn.execute(
                "UPDATE commitments SET due_date = ?, roll_count = roll_count + 1 WHERE id = ?",
                (to_date, commitment_id),
            )
        return self.get_commitment(commitment_id)

    # ───────────────────────── entries ─────────────────────────

    def log_entry(
        self,
        text: str,
        commitment_id: Optional[int] = None,
        project_id: Optional[int] = None,
        goal_id: Optional[int] = None,
        kind: str = "progress",
        minutes: Optional[int] = None,
        on_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("An entry needs some text.")

        # Inherit the hierarchy from the commitment so a log line is findable
        # from the goal without the caller wiring up the ids.
        if commitment_id and (project_id is None or goal_id is None):
            commitment = self.get_commitment(commitment_id)
            project_id = project_id if project_id is not None else commitment.get("project_id")
            goal_id = goal_id if goal_id is not None else commitment.get("goal_id")

        conn = self._connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO entries (commitment_id, project_id, goal_id, kind, text, minutes, "
                "on_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (commitment_id, project_id, goal_id, kind, text, minutes,
                 on_date or _today(), time.time()),
            )
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)

    def entries_on(self, on_date: str) -> List[Dict[str, Any]]:
        rows = self._connect().execute(
            "SELECT * FROM entries WHERE on_date = ? ORDER BY created_at", (on_date,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def entries_between(self, start: str, end: str) -> List[Dict[str, Any]]:
        rows = self._connect().execute(
            "SELECT * FROM entries WHERE on_date >= ? AND on_date <= ? ORDER BY on_date, created_at",
            (start, end),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ───────────────────────── the planner surface ─────────────────────────

    def today(self, on_date: Optional[str] = None) -> Dict[str, Any]:
        """Everything due today, plus anything already overdue."""
        on_date = on_date or _today()
        due_today = self.list_commitments(status="open", due_on=on_date)
        overdue = self.list_commitments(status="open", due_before=on_date)
        done_today = [
            c for c in self.list_commitments(status="done", limit=500)
            if c.get("done_at") and date.fromtimestamp(c["done_at"]).isoformat() == on_date
        ]
        return {
            "date": on_date,
            "due": due_today,
            "overdue": overdue,
            "done": done_today,
            "goals": self.list_goals(status="active"),
        }

    def upcoming(self, days: int = 7, on_date: Optional[str] = None) -> Dict[str, Any]:
        """
        The next N days, for *planning*.

        Deliberately not the ISO week. Asked "what's due this week" on a Sunday
        evening, the calendar week ends in three hours - so a commitment made for
        tomorrow comes back as "nothing outstanding this week", which is true and
        useless. Reviewing a week is the opposite case and still uses week().
        """
        start = date.fromisoformat(on_date) if on_date else date.today()
        end = start + timedelta(days=days)
        commitments = [
            c for c in self.list_commitments(status="open", limit=500)
            if c.get("due_date") and c["due_date"] <= end.isoformat()
        ]
        commitments.sort(key=lambda c: (c.get("due_date") or "", c.get("due_time") or ""))
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "commitments": commitments,
            # Overdue work is part of "what's ahead" whether or not it is dated
            # inside the window - it is still owed.
            "overdue": [c for c in commitments if (c.get("due_date") or "") < start.isoformat()],
        }

    def week(self, start: Optional[str] = None) -> Dict[str, Any]:
        """The current Monday-Sunday window."""
        today = date.fromisoformat(start) if start else date.today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        commitments = [
            c for c in self.list_commitments(status=None, limit=500)
            if c.get("due_date") and monday.isoformat() <= c["due_date"] <= sunday.isoformat()
        ]
        return {
            "start": monday.isoformat(),
            "end": sunday.isoformat(),
            "commitments": commitments,
            "entries": self.entries_between(monday.isoformat(), sunday.isoformat()),
        }

    # ───────────────────────── the coach surface ─────────────────────────

    def slipping(self, on_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Commitments that have earned a conversation.

        Either overdue, or rolled enough times that the honest question is no
        longer "when" but "is this actually real".
        """
        on_date = on_date or _today()
        flagged = []
        for commitment in self.list_commitments(status="open", limit=500):
            due = commitment.get("due_date") or ""
            overdue = bool(due and due < on_date)
            stale = commitment.get("roll_count", 0) >= STALE_ROLL_COUNT
            if overdue or stale:
                days_late = 0
                if due:
                    try:
                        days_late = (date.fromisoformat(on_date) - date.fromisoformat(due)).days
                    except ValueError:
                        days_late = 0
                flagged.append({
                    **commitment,
                    "overdue": overdue,
                    "stale": stale,
                    "daysLate": max(0, days_late),
                })
        flagged.sort(key=lambda c: (-c["roll_count"], -c["daysLate"]))
        return flagged

    def close_out(
        self,
        on_date: Optional[str] = None,
        roll_unfinished: bool = True,
        note: str = "",
    ) -> Dict[str, Any]:
        """
        End the day deliberately.

        The old planner did this silently at 00:05: completed items erased,
        everything else moved to today with no record. Here the day is recorded
        first - what was done, what moved, how many times it has moved - so the
        week has something true to report on.
        """
        on_date = on_date or _today()
        snapshot = self.today(on_date)
        unfinished = snapshot["due"] + snapshot["overdue"]

        rolled = 0
        if roll_unfinished:
            tomorrow = (date.fromisoformat(on_date) + timedelta(days=1)).isoformat()
            for commitment in unfinished:
                self.roll_commitment(commitment["id"], tomorrow)
                rolled += 1

        conn = self._connect()
        with conn:
            conn.execute(
                "INSERT INTO closeouts (on_date, done, rolled, dropped, note, created_at) "
                "VALUES (?, ?, ?, 0, ?, ?) "
                "ON CONFLICT(on_date) DO UPDATE SET done=excluded.done, rolled=excluded.rolled, "
                "note=excluded.note, created_at=excluded.created_at",
                (on_date, len(snapshot["done"]), rolled, note.strip() or None, time.time()),
            )

        return {
            "date": on_date,
            "done": len(snapshot["done"]),
            "doneItems": snapshot["done"],
            "rolled": rolled,
            "rolledItems": unfinished,
            "entries": len(self.entries_on(on_date)),
        }

    def was_closed_out(self, on_date: str) -> bool:
        row = self._connect().execute(
            "SELECT 1 FROM closeouts WHERE on_date = ?", (on_date,)
        ).fetchone()
        return row is not None

    def scorecard(self, start: Optional[str] = None) -> Dict[str, Any]:
        """An honest week: kept, slipped, worked on, and what never moved."""
        window = self.week(start)
        commitments = window["commitments"]
        done = [c for c in commitments if c["status"] == "done"]
        open_ = [c for c in commitments if c["status"] == "open"]
        dropped = [c for c in commitments if c["status"] == "dropped"]
        minutes = sum(e.get("minutes") or 0 for e in window["entries"])

        per_goal = []
        for goal in self.list_goals(status="active"):
            goal_commitments = [c for c in commitments if c.get("goal_id") == goal["id"]]
            goal_done = [c for c in goal_commitments if c["status"] == "done"]
            per_goal.append({
                "id": goal["id"],
                "title": goal["title"],
                "committed": len(goal_commitments),
                "done": len(goal_done),
                # A goal with no commitments at all this week is the most useful
                # thing a weekly review can tell you.
                "untouched": len(goal_commitments) == 0,
            })

        kept_ratio = round(len(done) / len(commitments), 2) if commitments else None
        return {
            "start": window["start"],
            "end": window["end"],
            "committed": len(commitments),
            "done": len(done),
            "open": len(open_),
            "dropped": len(dropped),
            "keptRatio": kept_ratio,
            "minutesLogged": minutes,
            "entries": len(window["entries"]),
            "goals": per_goal,
            "slipping": self.slipping(),
        }

    def stats(self) -> Dict[str, Any]:
        conn = self._connect()

        def count(sql: str, params: tuple = ()) -> int:
            return int(conn.execute(sql, params).fetchone()[0])

        return {
            "goals": count("SELECT COUNT(*) FROM goals WHERE status = 'active'"),
            "projects": count("SELECT COUNT(*) FROM projects WHERE status = 'active'"),
            "openCommitments": count("SELECT COUNT(*) FROM commitments WHERE status = 'open'"),
            "entries": count("SELECT COUNT(*) FROM entries"),
            "slipping": len(self.slipping()),
        }


workbook = Workbook()
