"""
Tests for Dexter Changelog Tracker, self-awareness of changes, and what_changed tool.
"""

import json
import os
import tempfile
import pytest

import changelog_tracker
import tools_assistant  # noqa: F401  - registers tools
from router import fast_path
from tool_registry import get_tool


@pytest.fixture
def mock_state(monkeypatch):
    td = tempfile.mkdtemp()
    state_file = os.path.join(td, "changelog_state.json")
    monkeypatch.setattr(changelog_tracker, "STATE_FILE", state_file)
    monkeypatch.setattr(changelog_tracker, "DATA_DIR", td)
    monkeypatch.setattr(changelog_tracker, "_cached_changes", None)
    monkeypatch.setattr(changelog_tracker, "_cached_at", 0.0)
    yield state_file
    try:
        import shutil
        shutil.rmtree(td, ignore_errors=True)
    except Exception:
        pass


def test_get_changes_returns_landmark_updates(mock_state):
    changes = changelog_tracker.get_changes_since_last_run(force_refresh=True)
    assert changes["has_changes"] is True
    assert "summary" in changes
    assert len(changes["items"]) >= 3
    # Check that key features (interruption, Brio mic) are included
    text = " ".join(changes["items"]).lower()
    assert "interruption" in text or "interrupt" in text
    assert "brio" in text


def test_record_session_persists_state(mock_state):
    assert not os.path.exists(mock_state)
    result = changelog_tracker.record_session()
    assert os.path.exists(mock_state)
    with open(mock_state, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "last_run_timestamp" in data
    assert "last_commit_hash" in data


def test_context_block_formatting(mock_state):
    block = changelog_tracker.context_block()
    assert "## Recent updates to Dexter" in block
    assert "Logitech Brio" in block or "brio" in block.lower()
    assert "interrupt" in block.lower()


def test_what_changed_tool_registered_and_executes():
    tool_spec = get_tool("what_changed")
    assert tool_spec is not None
    assert tool_spec.name == "what_changed"

    result = tool_spec.fn()
    assert result.ok is True
    assert "Dexter" in result.summary or "Since last time" in result.summary
    assert "items" in result.data


def test_fast_path_routes_what_changed():
    phrases = [
        "what changes were done in you from last time",
        "what changes were done in you",
        "what changed in dexter",
        "what's new in dexter",
        "what is new in dexter",
        "what changed since last time",
        "tell me what changed since last time",
        "can you tell me what's new",
    ]
    for p in phrases:
        routed = fast_path(p)
        assert routed is not None, f"Phrase failed to route: {p}"
        tool_name, args = routed
        assert tool_name == "what_changed", f"Expected what_changed but got {tool_name} for '{p}'"
