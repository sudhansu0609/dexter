"""
Conversation and fact memory.

Chat was previously stateless - every request built a fresh prompt with no
history, so "open it", "do that again" and "no, the other one" could never
resolve. This module gives the router two things:

  conversations  recent turns, replayed into the agent context
  facts          durable key/value knowledge the user asked Dexter to remember

SQLite from the standard library rather than TinyDB or a JSON file: no new
dependency, real atomicity, and indexed lookups that stay fast as history grows.
"""

import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "dexter_memory.db")

DEFAULT_HISTORY_TURNS = 8
MAX_STORED_TURNS = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    session     TEXT    NOT NULL DEFAULT 'default',
    role        TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    tool_calls  TEXT
);
CREATE INDEX IF NOT EXISTS idx_conv_session_ts ON conversations(session, ts DESC);

CREATE TABLE IF NOT EXISTS facts (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    source     TEXT,
    updated_at REAL NOT NULL
);
"""


class Memory:
    def __init__(self, db_path: str = DB_PATH) -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._path = db_path
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        """One connection per thread - sqlite3 objects are not thread-safe."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # ───────────────────────── conversation ─────────────────────────

    def add_turn(
        self,
        role: str,
        content: str,
        tool_calls: Optional[str] = None,
        session: str = "default",
    ) -> None:
        if not content:
            return
        try:
            conn = self._connect()
            with conn:
                conn.execute(
                    "INSERT INTO conversations (ts, session, role, content, tool_calls) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time.time(), session, role, content, tool_calls),
                )
        except Exception as exc:
            print(f"[Memory] Could not store turn: {exc}")

    def recent_turns(
        self, limit: int = DEFAULT_HISTORY_TURNS, session: str = "default"
    ) -> List[Dict[str, str]]:
        """Oldest-first, ready to splice into a messages array."""
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT role, content FROM conversations WHERE session = ? "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (session, limit),
            ).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        except Exception as exc:
            print(f"[Memory] Could not read history: {exc}")
            return []

    def clear_history(self, session: str = "default") -> int:
        try:
            conn = self._connect()
            with conn:
                cur = conn.execute("DELETE FROM conversations WHERE session = ?", (session,))
            return cur.rowcount
        except Exception:
            return 0

    def prune(self, keep: int = MAX_STORED_TURNS) -> int:
        """Trim old turns so the DB does not grow without bound."""
        try:
            conn = self._connect()
            with conn:
                cur = conn.execute(
                    "DELETE FROM conversations WHERE id NOT IN "
                    "(SELECT id FROM conversations ORDER BY ts DESC LIMIT ?)",
                    (keep,),
                )
            return cur.rowcount
        except Exception:
            return 0

    # ───────────────────────── facts ─────────────────────────

    def remember(self, key: str, value: str, source: str = "user") -> bool:
        key = (key or "").strip().lower()
        if not key or not value:
            return False
        try:
            conn = self._connect()
            with conn:
                conn.execute(
                    "INSERT INTO facts (key, value, source, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "source=excluded.source, updated_at=excluded.updated_at",
                    (key, value, source, time.time()),
                )
            return True
        except Exception as exc:
            print(f"[Memory] Could not store fact: {exc}")
            return False

    _RECALL_STOPWORDS = frozenset(
        "my your the a an is are was were of about for to and or i you".split()
    )

    def recall(self, key: str) -> Optional[str]:
        """
        Look up a fact.

        The user almost never repeats the exact label they stored - they say
        "my channel" having stored "my main channel". So exact match falls back
        to substring, then to sharing a significant word.
        """
        key = (key or "").strip().lower()
        if not key:
            return None
        try:
            conn = self._connect()
            row = conn.execute("SELECT value FROM facts WHERE key = ?", (key,)).fetchone()
            if row:
                return row["value"]

            row = conn.execute(
                "SELECT value FROM facts WHERE key LIKE ? ORDER BY updated_at DESC LIMIT 1",
                (f"%{key}%",),
            ).fetchone()
            if row:
                return row["value"]

            tokens = {
                t for t in key.replace("?", "").split()
                if t not in self._RECALL_STOPWORDS and len(t) > 2
            }
            if not tokens:
                return None

            best_value, best_overlap = None, 0
            for candidate in conn.execute(
                "SELECT key, value FROM facts ORDER BY updated_at DESC"
            ).fetchall():
                candidate_tokens = {
                    t for t in candidate["key"].split()
                    if t not in self._RECALL_STOPWORDS and len(t) > 2
                }
                overlap = len(tokens & candidate_tokens)
                if overlap > best_overlap:
                    best_value, best_overlap = candidate["value"], overlap
            return best_value
        except Exception:
            return None

    def all_facts(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT key, value, source, updated_at FROM facts "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def forget(self, key: str) -> bool:
        try:
            conn = self._connect()
            with conn:
                cur = conn.execute("DELETE FROM facts WHERE key = ?", ((key or "").strip().lower(),))
            return cur.rowcount > 0
        except Exception:
            return False

    def context_note(self, limit: int = 12) -> str:
        """Compact block of known facts for the agent system prompt."""
        facts = self.all_facts(limit=limit)
        if not facts:
            return ""
        lines = [f"- {f['key']}: {f['value']}" for f in facts]
        return "Things you know about the user:\n" + "\n".join(lines)

    def stats(self) -> Dict[str, int]:
        try:
            conn = self._connect()
            turns = conn.execute("SELECT COUNT(*) c FROM conversations").fetchone()["c"]
            facts = conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
            return {"turns": turns, "facts": facts}
        except Exception:
            return {"turns": 0, "facts": 0}


memory = Memory()
