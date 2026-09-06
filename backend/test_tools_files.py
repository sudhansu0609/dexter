"""
Tests for file mutation.

The safety properties matter more than the happy paths here: these tools act on
a spoken command that is occasionally misheard, so they must never escape the
allowlist, never silently overwrite, and never delete irreversibly.
"""

import os

import pytest

import tools_files
from tools_files import copy_file, delete_file, move_file, rename_file


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("DEXTER_FS_ROOTS", str(allowed))

    source = allowed / "invoice.pdf"
    source.write_text("bill", encoding="utf-8")
    return {"allowed": allowed, "outside": outside, "file": source}


# ───────────────────────── rename ─────────────────────────


def test_rename_keeps_the_file_in_place(sandbox):
    result = rename_file(str(sandbox["file"]), "invoice-march.pdf")
    assert result.ok
    assert (sandbox["allowed"] / "invoice-march.pdf").exists()
    assert not sandbox["file"].exists()


def test_rename_without_an_extension_keeps_the_original_one(sandbox):
    """'rename it to invoice' must not produce an extensionless file."""
    result = rename_file(str(sandbox["file"]), "march-invoice")
    assert result.ok
    assert (sandbox["allowed"] / "march-invoice.pdf").exists()


def test_rename_never_overwrites(sandbox):
    (sandbox["allowed"] / "taken.pdf").write_text("existing", encoding="utf-8")
    result = rename_file(str(sandbox["file"]), "taken.pdf")

    assert result.ok
    assert (sandbox["allowed"] / "taken (2).pdf").exists()
    assert (sandbox["allowed"] / "taken.pdf").read_text(encoding="utf-8") == "existing"


def test_rename_needs_a_name(sandbox):
    assert not rename_file(str(sandbox["file"]), "   ").ok


# ───────────────────────── move and copy ─────────────────────────


def test_move_into_a_folder_keeps_the_filename(sandbox):
    target = sandbox["allowed"] / "receipts"
    target.mkdir()

    result = move_file(str(sandbox["file"]), str(target))
    assert result.ok
    assert (target / "invoice.pdf").exists()


def test_copy_leaves_the_original(sandbox):
    target = sandbox["allowed"] / "backup"
    target.mkdir()

    result = copy_file(str(sandbox["file"]), str(target))
    assert result.ok
    assert (target / "invoice.pdf").exists()
    assert sandbox["file"].exists()


def test_copy_a_folder_works(sandbox):
    folder = sandbox["allowed"] / "project"
    folder.mkdir()
    (folder / "note.txt").write_text("hi", encoding="utf-8")

    result = copy_file(str(folder), str(sandbox["allowed"] / "project-copy"))
    assert result.ok
    assert (sandbox["allowed"] / "project-copy" / "note.txt").exists()


# ───────────────────────── the allowlist ─────────────────────────


def test_a_source_outside_the_allowlist_is_refused(sandbox):
    stranger = sandbox["outside"] / "secret.txt"
    stranger.write_text("no", encoding="utf-8")

    for call in (
        lambda: rename_file(str(stranger), "x.txt"),
        lambda: move_file(str(stranger), str(sandbox["allowed"])),
        lambda: copy_file(str(stranger), str(sandbox["allowed"])),
        lambda: delete_file(str(stranger)),
    ):
        assert not call().ok


def test_a_destination_outside_the_allowlist_is_refused(sandbox):
    """The subtle one: an unchecked destination is an escape hatch."""
    result = move_file(str(sandbox["file"]), str(sandbox["outside"]))
    assert not result.ok
    assert sandbox["file"].exists(), "the file must not have moved"
    assert not (sandbox["outside"] / "invoice.pdf").exists()


def test_a_traversal_destination_is_refused(sandbox):
    escape = str(sandbox["allowed"] / ".." / "outside")
    assert not copy_file(str(sandbox["file"]), escape).ok


def test_a_missing_file_is_reported_plainly(sandbox):
    result = rename_file(str(sandbox["allowed"] / "ghost.pdf"), "x.pdf")
    assert not result.ok
    assert "can't find" in result.summary.lower() or "find" in result.summary.lower()


# ───────────────────────── delete ─────────────────────────


def test_delete_uses_the_recycle_bin(sandbox, monkeypatch):
    binned = []
    monkeypatch.setattr(tools_files, "_HAS_TRASH", True)
    monkeypatch.setattr(tools_files, "send2trash", lambda p: binned.append(p))

    result = delete_file(str(sandbox["file"]))
    assert result.ok
    assert binned == [os.path.realpath(str(sandbox["file"]))]


def test_delete_refuses_rather_than_deleting_permanently(sandbox, monkeypatch):
    """No os.remove fallback: a misheard command must stay recoverable."""
    monkeypatch.setattr(tools_files, "_HAS_TRASH", False)

    result = delete_file(str(sandbox["file"]))
    assert not result.ok
    assert sandbox["file"].exists()
    assert "send2trash" in result.summary


def test_every_mutation_tool_requires_confirmation():
    from tool_registry import REGISTRY

    for name in ("rename_file", "move_file", "copy_file", "delete_file"):
        assert REGISTRY[name].confirm is True, f"{name} must ask before acting"
