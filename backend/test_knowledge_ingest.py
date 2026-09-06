"""
Folder ingestion and the profile (roadmap v5, 4.1 / 4.2).

What these protect: only the intended files are picked up (code-repo JSON and
excluded folders are not); a second pass skips unchanged files; a changed file
is re-indexed and a deleted one removed; the studio root only contributes its
include list; PDFs with no text are recorded as empty; search cites the tag
and file; the profile renders into a prompt block and round-trips fields.
"""

import os
import time

import pytest

import knowledge_ingest
import user_profile
from memory import Memory


@pytest.fixture
def store(tmp_path):
    return Memory(db_path=str(tmp_path / "mem.db"))


@pytest.fixture
def roots(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "relaunch.md").write_text(
        "# Channel relaunch plan\n\n" + ("We relaunch Beyond3Baje in October with eight documentary episodes. " * 8),
        encoding="utf-8",
    )
    (notes / "camera.txt").write_text("Camera settings: 24fps, ISO 400, shutter 1/50. " * 12, encoding="utf-8")
    (notes / "node_modules").mkdir()
    (notes / "node_modules" / "ignored.md").write_text("should never be indexed " * 20, encoding="utf-8")
    (notes / "package-lock.json").write_text('{"a": 1}', encoding="utf-8")
    (notes / "tiny.md").write_text("too short", encoding="utf-8")

    studio = tmp_path / "studio"
    (studio / "prompts" / "channels").mkdir(parents=True)
    (studio / "prompts" / "agents").mkdir(parents=True)
    (studio / "knowledge").mkdir()
    (studio / "prompts" / "channels" / "Beyond3Baje.md").write_text(
        "# Brand Style Guide: Beyond3Baje\n\nThe scholarly detective voice: calm, cinematic, evidence first. " * 6,
        encoding="utf-8",
    )
    (studio / "prompts" / "agents" / "CEO.md").write_text("persona noise " * 40, encoding="utf-8")
    (studio / "knowledge" / "saved_topics.json").write_text(
        '{"topics": [{"topic": "The vanishing guard of Bhangarh Fort", "channel": "Khayal3Baje", '
        '"viral_potential": 9, "notes": "3 AM guard shift testimonies"},'
        '{"topic": "Kafka and the office", "channel": "Life3Baje", "viral_potential": 7}]}',
        encoding="utf-8",
    )
    return [("notes", str(notes)), ("studio", str(studio))]


def test_scan_respects_excludes_and_the_studio_include_list(roots):
    files = {os.path.basename(p): tag for p, tag in knowledge_ingest.scan(roots)}
    assert files == {
        "relaunch.md": "notes", "camera.txt": "notes", "tiny.md": "notes",
        "Beyond3Baje.md": "studio", "saved_topics.json": "studio",
    }


def test_reindex_is_idempotent_and_tracks_changes(roots, store):
    first = knowledge_ingest.reindex(roots=roots, memory_store=store)
    assert first["scanned"] == 5
    assert first["updated"] == 4   # tiny.md is too short
    assert first["empty"] == 1

    second = knowledge_ingest.reindex(roots=roots, memory_store=store)
    assert second["updated"] == 0 and second["skipped"] == 5

    notes_dir = roots[0][1]
    camera = os.path.join(notes_dir, "camera.txt")
    time.sleep(0.01)
    with open(camera, "a", encoding="utf-8") as handle:
        handle.write("\n\nNew paragraph about lenses. " * 5)
    os.utime(camera, None)
    third = knowledge_ingest.reindex(roots=roots, memory_store=store)
    assert third["updated"] == 1

    os.remove(os.path.join(notes_dir, "relaunch.md"))
    fourth = knowledge_ingest.reindex(roots=roots, memory_store=store)
    assert fourth["removed"] == 1
    assert store.ingest_source(os.path.join(notes_dir, "relaunch.md")) is None


def test_studio_json_becomes_prose_and_search_cites_tag_and_file(roots, store):
    pytest.importorskip("sentence_transformers")
    knowledge_ingest.reindex(roots=roots, memory_store=store)
    hits = store.search_documents("the vanishing guard of Bhangarh Fort", top_k=3)
    assert hits, "expected a semantic hit"
    assert hits[0]["tag"] == "studio"
    assert hits[0]["source"].endswith("saved_topics.json")
    assert "Bhangarh" in hits[0]["text"]
    only_notes = store.search_documents("camera ISO shutter", top_k=3, tag="notes")
    assert only_notes and all(h["tag"] == "notes" for h in only_notes)
    assert store.document_label(hits[0]["source"]).startswith("studio/")


def test_docx_extraction(tmp_path):
    docx = pytest.importorskip("docx")
    path = tmp_path / "brief.docx"
    document = docx.Document()
    document.add_paragraph("Sponsor brief for the relaunch: three integrations, one per episode.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Episode"
    table.rows[0].cells[1].text = "Sponsor"
    document.save(str(path))
    title, text = knowledge_ingest.extract(str(path))
    assert title == "brief"
    assert "Sponsor brief" in text and "Episode | Sponsor" in text


def test_chunking_packs_paragraphs():
    text = "\n\n".join(f"paragraph {i} " + ("x" * 300) for i in range(10))
    chunks = knowledge_ingest.chunk_text(text, size=1000)
    assert len(chunks) >= 3
    assert all(len(c) <= 1000 for c in chunks)


def test_profile_round_trip_and_prompt_block(tmp_path, monkeypatch):
    monkeypatch.setattr(user_profile, "PROFILE_PATH", str(tmp_path / "profile.json"))
    assert user_profile.render_block() == ""
    user_profile.update_field("name", "Sudhansu")
    user_profile.update_field("channels", "Beyond3Baje=UC123, Khayal3Baje")
    user_profile.update_field("preferences", "short replies, Hinglish scripts")
    block = user_profile.render_block()
    assert "Name: Sudhansu" in block
    assert "Beyond3Baje, Khayal3Baje" in block
    assert "short replies" in block
    assert user_profile.owner_channel_ids() == ["UC123"]
    with pytest.raises(ValueError):
        user_profile.update_field("shoe size", "9")
