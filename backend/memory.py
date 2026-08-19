"""
Conversation, fact, and semantic memory.

Chat was previously stateless - every request built a fresh prompt with no
history, so "open it", "do that again" and "no, the other one" could never
resolve. This module gives the router:

  conversations  recent turns, replayed into the agent context
  facts          durable knowledge the user asked Dexter to remember
  summaries      daily/weekly conversation rollups (episodic memory)
  documents      ingested file chunks, recallable with their source

Facts, summaries and document chunks carry MiniLM embeddings (shared with the
Tier-1 router via embeddings.py), so recall works on meaning - "what did I say
about the render rig last week" - not just on repeating the stored words.
Search runs through the sqlite-vec extension when it loads, and falls back to
a numpy scan over the same BLOB column when it does not; at this scale both
answer in single-digit milliseconds. Without sentence-transformers entirely,
the original keyword recall still works.

SQLite from the standard library rather than TinyDB or a JSON file: no new
dependency, real atomicity, and indexed lookups that stay fast as history grows.
"""

import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

import embeddings

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "dexter_memory.db")

DEFAULT_HISTORY_TURNS = 8
MAX_STORED_TURNS = 2000

# Below this cosine similarity a "match" is a coincidence, not a memory.
SEMANTIC_FLOOR = 0.35
# Above this, two facts say the same thing - used to dedup auto-extraction.
# Calibrated on MiniLM: direct paraphrases score ~0.85, related-but-distinct
# facts ~0.7, unrelated ~0.05. Kept strict so distinct facts never merge.
DUPLICATE_SIMILARITY = 0.82

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
    updated_at REAL NOT NULL,
    embedding  BLOB
);

CREATE TABLE IF NOT EXISTS summaries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,
    period     TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL,
    embedding  BLOB,
    UNIQUE(scope, period)
);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    added_at    REAL NOT NULL,
    embedding   BLOB,
    UNIQUE(source, chunk_index)
);
"""


class Memory:
    def __init__(self, db_path: str = DB_PATH) -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._path = db_path
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns that predate-this-version databases are missing."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(facts)").fetchall()}
        if "embedding" not in cols:
            conn.execute("ALTER TABLE facts ADD COLUMN embedding BLOB")

    def _connect(self) -> sqlite3.Connection:
        """One connection per thread - sqlite3 objects are not thread-safe."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.vec_sql = self._load_vec_extension(conn)
            self._local.conn = conn
        return conn

    @staticmethod
    def _load_vec_extension(conn: sqlite3.Connection) -> bool:
        try:
            import sqlite_vec  # type: ignore

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            return True
        except Exception:
            return False

    @property
    def _vec_sql(self) -> bool:
        self._connect()
        return bool(getattr(self._local, "vec_sql", False))

    @staticmethod
    def _embed_one(text: str) -> Optional[bytes]:
        vectors = embeddings.embed([text])
        if vectors is None:
            return None
        return embeddings.to_blob(vectors[0])

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
            blob = self._embed_one(f"{key}: {value}")
            conn = self._connect()
            with conn:
                conn.execute(
                    "INSERT INTO facts (key, value, source, updated_at, embedding) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "source=excluded.source, updated_at=excluded.updated_at, "
                    "embedding=excluded.embedding",
                    (key, value, source, time.time(), blob),
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

            # Semantic: "what channel do I run" finds "my main channel is ...".
            hits = self.semantic_search(key, top_k=1, kinds=("fact",))
            if hits and hits[0]["score"] >= SEMANTIC_FLOOR:
                return hits[0]["text"]

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

    # ───────────────────────── semantic search ─────────────────────────

    _KIND_QUERIES = {
        "fact": ("facts", "key || ': ' || value", "source"),
        "summary": ("summaries", "content", "scope || ' ' || period"),
        "document": ("documents", "content", "source"),
    }

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        kinds: tuple = ("fact", "summary", "document"),
    ) -> List[Dict[str, Any]]:
        """
        Meaning-based recall across memory kinds. Returns
        [{kind, text, source, score}] best-first; empty when the embedding
        model is unavailable (keyword recall still covers facts then).
        """
        query = (query or "").strip()
        if not query:
            return []
        vectors = embeddings.embed([query])
        if vectors is None:
            return []
        query_blob = embeddings.to_blob(vectors[0])

        results: List[Dict[str, Any]] = []
        try:
            conn = self._connect()
            for kind in kinds:
                table, text_expr, source_expr = self._KIND_QUERIES[kind]
                if self._vec_sql:
                    rows = conn.execute(
                        f"SELECT {text_expr} AS text, {source_expr} AS source, "
                        f"vec_distance_cosine(embedding, ?) AS dist FROM {table} "
                        "WHERE embedding IS NOT NULL ORDER BY dist LIMIT ?",
                        (query_blob, top_k),
                    ).fetchall()
                    for row in rows:
                        results.append({
                            "kind": kind, "text": row["text"], "source": row["source"],
                            "score": 1.0 - float(row["dist"]),
                        })
                else:
                    query_vec = embeddings.from_blob(query_blob)
                    rows = conn.execute(
                        f"SELECT {text_expr} AS text, {source_expr} AS source, embedding "
                        f"FROM {table} WHERE embedding IS NOT NULL"
                    ).fetchall()
                    for row in rows:
                        doc_vec = embeddings.from_blob(row["embedding"])
                        score = float((query_vec * doc_vec).sum())
                        results.append({
                            "kind": kind, "text": row["text"], "source": row["source"],
                            "score": score,
                        })
        except Exception as exc:
            print(f"[Memory] Semantic search failed: {exc}")
            return []

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def semantic_context(self, message: str, limit: int = 4) -> str:
        """
        Memories relevant to the current message, for the agent system prompt.
        This is what turns stored history into a working second brain: the
        model sees what matters for *this* request without being handed the
        whole database.
        """
        hits = [
            h for h in self.semantic_search(message, top_k=limit)
            if h["score"] >= SEMANTIC_FLOOR
        ]
        if not hits:
            return ""
        lines = []
        for hit in hits:
            text = hit["text"][:300]
            if hit["kind"] == "document":
                lines.append(f"- (from {os.path.basename(str(hit['source']))}) {text}")
            elif hit["kind"] == "summary":
                lines.append(f"- (past conversations, {hit['source']}) {text}")
            else:
                lines.append(f"- {text}")
        return "Memories relevant to this request:\n" + "\n".join(lines)

    def backfill_embeddings(self, limit: int = 500) -> int:
        """
        Embed rows stored before semantic memory existed (or while the model
        was unavailable). Run from the startup warm thread, never at import -
        it may trigger the multi-second model load.
        """
        if not embeddings.available():
            return 0
        done = 0
        try:
            conn = self._connect()
            for table, text_sql, key_col in (
                ("facts", "key || ': ' || value", "key"),
                ("summaries", "content", "id"),
                ("documents", "content", "id"),
            ):
                rows = conn.execute(
                    f"SELECT {key_col} AS k, {text_sql} AS text FROM {table} "
                    "WHERE embedding IS NULL LIMIT ?",
                    (limit,),
                ).fetchall()
                for row in rows:
                    blob = self._embed_one(row["text"])
                    if blob is None:
                        continue
                    with conn:
                        conn.execute(
                            f"UPDATE {table} SET embedding = ? WHERE {key_col} = ?",
                            (blob, row["k"]),
                        )
                    done += 1
        except Exception as exc:
            print(f"[Memory] Embedding backfill failed: {exc}")
        if done:
            print(f"[Memory] Backfilled embeddings for {done} memory row(s).")
        return done

    def similar_fact(self, text: str, threshold: float = DUPLICATE_SIMILARITY) -> Optional[str]:
        """The key of an existing fact that already says this, if any."""
        hits = self.semantic_search(text, top_k=1, kinds=("fact",))
        if hits and hits[0]["score"] >= threshold:
            return str(hits[0]["text"]).split(":", 1)[0]
        return None

    # ───────────────────────── summaries (episodic) ─────────────────────────

    def upsert_summary(self, scope: str, period: str, content: str) -> bool:
        if not content or scope not in ("day", "week"):
            return False
        try:
            blob = self._embed_one(content)
            conn = self._connect()
            with conn:
                conn.execute(
                    "INSERT INTO summaries (scope, period, content, created_at, embedding) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(scope, period) DO UPDATE SET content=excluded.content, "
                    "created_at=excluded.created_at, embedding=excluded.embedding",
                    (scope, period, content, time.time(), blob),
                )
            return True
        except Exception as exc:
            print(f"[Memory] Could not store summary: {exc}")
            return False

    def get_summary(self, scope: str, period: str) -> Optional[str]:
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT content FROM summaries WHERE scope = ? AND period = ?",
                (scope, period),
            ).fetchone()
            return row["content"] if row else None
        except Exception:
            return None

    def turns_between(self, start_ts: float, end_ts: float) -> List[Dict[str, Any]]:
        """All sessions' turns in a window, oldest first - summarizer input."""
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT ts, role, content FROM conversations "
                "WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
                (start_ts, end_ts),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ───────────────────────── documents ─────────────────────────

    def add_document(self, source: str, chunks: List[str]) -> int:
        """(Re)ingest a document's chunks; returns how many were stored."""
        chunks = [c.strip() for c in chunks if c and c.strip()]
        if not chunks:
            return 0
        vectors = embeddings.embed(chunks)
        try:
            conn = self._connect()
            with conn:
                conn.execute("DELETE FROM documents WHERE source = ?", (source,))
                now = time.time()
                for index, chunk in enumerate(chunks):
                    blob = embeddings.to_blob(vectors[index]) if vectors is not None else None
                    conn.execute(
                        "INSERT INTO documents (source, chunk_index, content, added_at, embedding) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (source, index, chunk, now, blob),
                    )
            return len(chunks)
        except Exception as exc:
            print(f"[Memory] Could not ingest document: {exc}")
            return 0

    def stats(self) -> Dict[str, Any]:
        try:
            conn = self._connect()
            counts = {
                name: conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
                for name, table in (
                    ("turns", "conversations"), ("facts", "facts"),
                    ("summaries", "summaries"), ("documentChunks", "documents"),
                )
            }
            counts["semantic"] = embeddings.available()
            counts["vecExtension"] = self._vec_sql
            return counts
        except Exception:
            return {"turns": 0, "facts": 0, "summaries": 0, "documentChunks": 0}


memory = Memory()
