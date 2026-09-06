"""
Tests for the filesystem allowlist.

pathguard is the single choke point protecting every file tool and every
/filesystem/* endpoint, and it had no tests at all - a regression here fails
silently and dangerously, which is the worst combination.
"""

import os

import pytest

import pathguard


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """Two allowed roots and one that is deliberately outside them."""
    allowed = tmp_path / "allowed"
    other = tmp_path / "also-allowed"
    forbidden = tmp_path / "forbidden"
    for path in (allowed, other, forbidden):
        path.mkdir()

    monkeypatch.setenv("DEXTER_FS_ROOTS", os.pathsep.join([str(allowed), str(other)]))
    return {"allowed": allowed, "other": other, "forbidden": forbidden}


def test_path_inside_an_allowed_root_resolves(roots):
    target = roots["allowed"] / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    assert pathguard.resolve(str(target)) == os.path.realpath(str(target))


def test_a_file_that_does_not_exist_yet_is_still_allowed(roots):
    # write_file must be able to create new files inside an allowed root.
    target = roots["allowed"] / "new" / "draft.md"
    assert pathguard.resolve(str(target)) is not None


def test_path_outside_every_root_is_refused(roots):
    target = roots["forbidden"] / "secret.txt"
    target.write_text("no", encoding="utf-8")
    assert pathguard.resolve(str(target)) is None


def test_dot_dot_traversal_cannot_escape(roots):
    # The canonicalisation is the whole defence: '..' must be resolved *before*
    # the root comparison, or "allowed/../forbidden" walks straight out.
    escape = roots["allowed"] / ".." / "forbidden" / "secret.txt"
    assert pathguard.resolve(str(escape)) is None


def test_second_configured_root_is_honoured(roots):
    target = roots["other"] / "file.txt"
    assert pathguard.resolve(str(target)) is not None


def test_quoted_and_padded_paths_are_cleaned(roots):
    target = roots["allowed"] / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    for variant in (f'  "{target}"  ', f"'{target}'", f" {target} "):
        assert pathguard.resolve(variant) is not None, variant


def test_empty_input_is_refused(roots):
    for value in ("", "   ", None):
        assert pathguard.resolve(value) is None  # type: ignore[arg-type]


def test_sibling_prefix_is_not_treated_as_inside(tmp_path, monkeypatch):
    """'/data' must not authorise '/data-secrets' - a plain prefix check would."""
    root = tmp_path / "data"
    sibling = tmp_path / "data-secrets"
    root.mkdir()
    sibling.mkdir()
    monkeypatch.setenv("DEXTER_FS_ROOTS", str(root))

    assert pathguard.resolve(str(sibling / "keys.txt")) is None


def test_denial_message_names_the_allowed_folders(roots):
    message = pathguard.denial(str(roots["forbidden"] / "x.txt"))
    assert message, "a denial must explain itself so the agent can relay it"
    assert "allowed" in message.lower() or "allow" in message.lower()


def test_default_roots_include_the_user_folders(monkeypatch):
    monkeypatch.delenv("DEXTER_FS_ROOTS", raising=False)
    roots = [os.path.basename(r) for r in pathguard.allowed_roots()]
    assert "Documents" in roots
    assert "Desktop" in roots
