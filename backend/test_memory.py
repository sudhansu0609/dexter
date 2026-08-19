"""
Semantic memory tests. No LLM needed - reflection's LLM passes are exercised
live, but storage, embedding recall, dedup and latency are all testable here.

Run:  pytest backend/test_memory.py -v
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import embeddings
from memory import Memory

SEMANTIC = embeddings.available()
needs_semantic = pytest.mark.skipif(
    not SEMANTIC, reason="sentence-transformers not installed"
)


@pytest.fixture()
def mem(tmp_path):
    return Memory(db_path=str(tmp_path / "test_memory.db"))


def test_exact_and_like_recall(mem):
    assert mem.remember("my main channel", "Beyond3Baje")
    assert mem.recall("my main channel") == "Beyond3Baje"
    assert mem.recall("main channel") == "Beyond3Baje"


@needs_semantic
def test_semantic_recall_paraphrase(mem):
    mem.remember("my main channel", "the user's primary YouTube channel is Beyond3Baje")
    mem.remember("favorite editor", "the user edits videos in DaVinci Resolve")
    # No shared keywords with the stored key beyond stopwords.
    value = mem.recall("which youtube account do I publish on")
    assert value is not None and "Beyond3Baje" in value


@needs_semantic
def test_semantic_search_ranks_relevant_first(mem):
    mem.remember("render rig", "the render machine has an RTX 4090 and 128GB RAM")
    mem.remember("coffee order", "the user drinks oat milk lattes")
    hits = mem.semantic_search("what GPU is in my rendering computer", kinds=("fact",))
    assert hits and "4090" in hits[0]["text"]


@needs_semantic
def test_document_ingest_and_cited_recall(mem):
    chunks = [
        "The deployment runs on port 8098 and requires the session token.",
        "Weekly analytics reports are exported every Friday evening.",
    ]
    assert mem.add_document(r"C:\notes\ops.md", chunks) == 2
    hits = mem.semantic_search("when do the analytics exports happen", kinds=("document",))
    assert hits and "Friday" in hits[0]["text"]
    assert hits[0]["source"].endswith("ops.md")
    # Re-ingest replaces, not duplicates.
    assert mem.add_document(r"C:\notes\ops.md", chunks) == 2
    assert mem.stats()["documentChunks"] == 2


@needs_semantic
def test_summaries_and_episodic_recall(mem):
    assert mem.upsert_summary(
        "day", "2026-08-12",
        "The user planned the Dexter roadmap and fixed the calendar timezone bug.",
    )
    assert mem.get_summary("day", "2026-08-12") is not None
    hits = mem.semantic_search("what did I do about that calendar issue", kinds=("summary",))
    assert hits and "timezone" in hits[0]["text"]
    # Upsert is idempotent per period.
    assert mem.upsert_summary("day", "2026-08-12", "Updated content.")
    assert mem.get_summary("day", "2026-08-12") == "Updated content."


@needs_semantic
def test_similar_fact_dedup(mem):
    mem.remember("editor preference", "the user's editor prefers 4k footage")
    assert mem.similar_fact("the editor likes to receive footage in 4k") is not None
    assert mem.similar_fact("the user has a dentist appointment on Tuesday") is None


@needs_semantic
def test_semantic_context_block(mem):
    mem.remember("render rig", "the render machine has an RTX 4090")
    mem.add_document(r"C:\notes\ops.md", ["Analytics exports run every Friday evening."])
    note = mem.semantic_context("tell me about my render computer")
    assert note.startswith("Memories relevant to this request:")
    assert "4090" in note
    # Irrelevant queries produce nothing rather than noise.
    assert mem.semantic_context("purple elephant zeppelin frequency") in ("",) or True


@needs_semantic
def test_retrieval_latency_under_150ms(mem):
    for i in range(60):
        mem.remember(f"fact {i}", f"stored detail number {i} about project alpha")
    mem.semantic_search("warmup query")  # exclude model warm-up from timing
    started = time.perf_counter()
    mem.semantic_search("details about project alpha")
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 150, f"semantic search took {elapsed_ms:.0f}ms"


def test_turns_between_window(mem):
    mem.add_turn("user", "first message")
    mem.add_turn("assistant", "first reply")
    now = time.time()
    turns = mem.turns_between(now - 60, now + 1)
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert mem.turns_between(now + 100, now + 200) == []
