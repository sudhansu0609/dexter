"""
Tests for atomic JSON persistence.

jsonstore holds the planner, reminders, tasks, and scheduled jobs, and it had no
tests. The failure that motivated these: on Windows `os.replace` raises
PermissionError when an antivirus scanner or indexer briefly holds the
destination open, and `save_json` printed the error and returned False. No
caller checks the return value, so state simply vanished - which is a large part
of why Dexter felt like it forgot things.
"""

import json
import os
from unittest import mock

import jsonstore


def test_round_trip(tmp_path):
    path = str(tmp_path / "state.json")
    assert jsonstore.save_json(path, {"tasks": [1, 2, 3]}) is True
    assert jsonstore.load_json(path, default={}) == {"tasks": [1, 2, 3]}


def test_missing_file_returns_the_default(tmp_path):
    assert jsonstore.load_json(str(tmp_path / "nope.json"), default={"a": 1}) == {"a": 1}


def test_empty_file_returns_the_default(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("", encoding="utf-8")
    assert jsonstore.load_json(str(path), default=[]) == []


def test_corrupt_file_is_preserved_not_overwritten(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    assert jsonstore.load_json(str(path), default={}) == {}
    # The point of the .corrupt copy: data loss stays recoverable rather than
    # being silently replaced by the default on the next save.
    assert os.path.exists(str(path) + ".corrupt")


def test_no_temp_files_are_left_behind(tmp_path):
    path = str(tmp_path / "state.json")
    jsonstore.save_json(path, {"x": 1})
    leftovers = [name for name in os.listdir(tmp_path) if name.endswith(".tmp")]
    assert leftovers == []


def test_write_survives_a_transient_sharing_violation(tmp_path):
    """The regression: one PermissionError used to lose the write entirely."""
    path = str(tmp_path / "state.json")
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    with mock.patch.object(jsonstore.os, "replace", side_effect=flaky_replace):
        assert jsonstore.save_json(path, {"kept": True}) is True

    assert calls["n"] == 2, "the write should have been retried exactly once"
    assert json.loads(open(path, encoding="utf-8").read()) == {"kept": True}


def test_a_persistent_failure_reports_false_rather_than_raising(tmp_path):
    path = str(tmp_path / "state.json")

    with mock.patch.object(
        jsonstore.os, "replace", side_effect=PermissionError(5, "Access is denied")
    ):
        assert jsonstore.save_json(path, {"lost": True}) is False


def test_existing_content_is_not_truncated_by_a_failed_write(tmp_path):
    """The original bug this module was written to fix, still holding."""
    path = str(tmp_path / "state.json")
    jsonstore.save_json(path, {"generation": 1})

    with mock.patch.object(
        jsonstore.os, "replace", side_effect=PermissionError(5, "Access is denied")
    ):
        jsonstore.save_json(path, {"generation": 2})

    assert jsonstore.load_json(path, default={}) == {"generation": 1}
