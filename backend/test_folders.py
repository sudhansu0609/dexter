"""
Drives and folders (v13).

The complaint these answer: "it's not able to open C drive or any such drive or
list folders or open a specific folder". Two separate things had to change - what
Dexter is *allowed* to touch (pathguard's scope) and whether it can turn "the C
drive" into a path at all (folders.spoken_path) - so both are tested here,
including the thing that must not change: a write into Windows itself is still
refused.
"""

import os

import pytest

import folders
import pathguard


@pytest.fixture
def machine(monkeypatch):
    monkeypatch.delenv("DEXTER_FS_ROOTS", raising=False)
    monkeypatch.delenv("DEXTER_FS_DENY", raising=False)
    monkeypatch.setenv("DEXTER_FS_SCOPE", "machine")


@pytest.fixture
def tree(tmp_path):
    """A small folder with one sub-folder and two files."""
    (tmp_path / "renders").mkdir()
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    (tmp_path / "clip.mp4").write_bytes(b"0" * 2048)
    return tmp_path


# ───────────────────────── the scope ─────────────────────────


def test_machine_scope_reaches_the_whole_disk(machine):
    assert pathguard.scope() == "machine"
    assert pathguard.resolve(r"C:\Windows\notepad.exe") is not None, (
        "reading anywhere is the point of the machine scope"
    )


def test_spelling_out_roots_still_pins_dexter_to_them(tmp_path, monkeypatch):
    """An install that listed its roots keeps exactly the reach it had."""
    monkeypatch.delenv("DEXTER_FS_SCOPE", raising=False)
    monkeypatch.setenv("DEXTER_FS_ROOTS", str(tmp_path))
    assert pathguard.scope() == "user"
    assert pathguard.resolve(str(tmp_path / "a.txt")) is not None
    assert pathguard.resolve(r"C:\Windows\notepad.exe") is None


def test_windows_is_readable_but_never_writable(machine):
    assert pathguard.resolve(r"C:\Windows\System32\drivers\etc\hosts") is not None
    assert pathguard.resolve_write(r"C:\Windows\System32\drivers\etc\hosts") is None
    assert "Windows itself" in pathguard.denial(r"C:\Windows\x.txt", write=True)


def test_a_drive_root_is_not_written_to(machine):
    assert pathguard.resolve_write("C:\\") is None
    assert pathguard.resolve("C:\\") is not None


def test_a_denied_folder_is_refused_both_ways(tmp_path, monkeypatch):
    secret = tmp_path / "private"
    secret.mkdir()
    monkeypatch.setenv("DEXTER_FS_SCOPE", "machine")
    monkeypatch.setenv("DEXTER_FS_DENY", str(secret))
    assert pathguard.resolve(str(secret / "keys.txt")) is None
    assert pathguard.resolve_write(str(secret / "keys.txt")) is None
    assert "stay out of" in pathguard.denial(str(secret / "keys.txt"))


def test_traversal_cannot_escape_a_denied_folder(tmp_path, monkeypatch):
    secret = tmp_path / "private"
    secret.mkdir()
    monkeypatch.setenv("DEXTER_FS_DENY", str(secret))
    monkeypatch.setenv("DEXTER_FS_SCOPE", "machine")
    sneaky = tmp_path / "open" / ".." / "private" / "keys.txt"
    assert pathguard.resolve(str(sneaky)) is None


# ───────────────────────── the words for a place ─────────────────────────


@pytest.mark.parametrize("spoken", ["c drive", "the c drive", "C:", "c:\\", "drive c"])
def test_every_way_of_saying_the_c_drive(machine, spoken):
    assert folders.spoken_path(spoken) == "C:\\"


def test_a_drive_that_does_not_exist_is_not_invented(machine):
    assert folders.spoken_path("drive q") is None


@pytest.mark.parametrize("spoken,tail", [
    ("my downloads", "Downloads"),
    ("the desktop", "Desktop"),
    ("documents", "Documents"),
    ("pictures folder", "Pictures"),
])
def test_the_users_own_folders_by_name(machine, spoken, tail):
    path = folders.spoken_path(spoken)
    assert path and os.path.basename(path) == tail


def test_a_written_path_is_taken_as_written(machine, tree):
    assert folders.spoken_path(str(tree)) == os.path.realpath(str(tree))


def test_this_pc_is_the_drive_list_not_a_folder(machine):
    for spoken in ("this pc", "my computer", "drives"):
        assert folders.is_this_pc(spoken) is True
        assert folders.spoken_path(spoken) is None


def test_a_folder_is_found_by_name_near_the_places_things_live(machine, tree):
    found = folders.find_folder("renders", bases=[str(tree)])
    assert found and os.path.basename(found[0]) == "renders"


def test_find_ignores_the_noise_folders(machine, tmp_path):
    for name in ("node_modules", "__pycache__", ".git"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "renders").mkdir()
    assert folders.find_folder("renders", bases=[str(tmp_path)]) == []


# ───────────────────────── listing ─────────────────────────


def test_listing_a_folder_puts_folders_first(machine, tree):
    result = folders.list_folder(str(tree))
    assert result["ok"] is True
    assert result["folders"] == 1 and result["files"] == 2
    assert result["entries"][0]["kind"] == "folder"
    assert result["totalBytes"] == 2053


def test_a_listing_says_its_own_sentence(machine, tree):
    said = folders.describe_listing(folders.list_folder(str(tree)))
    assert "1 folder(s), 2 file(s)" in said
    assert "renders\\" in said and "clip.mp4" in said


def test_a_pattern_narrows_the_listing(machine, tree):
    result = folders.list_folder(str(tree), pattern="mp4")
    assert [e["name"] for e in result["entries"]] == ["clip.mp4"]


def test_a_missing_folder_is_refused_with_a_sentence(machine, tmp_path):
    result = folders.list_folder(str(tmp_path / "nope"))
    assert result["ok"] is False
    assert "no folder" in result["error"].lower()


def test_a_file_named_where_a_folder_was_expected_says_so(machine, tree):
    result = folders.list_folder(str(tree / "notes.md"))
    assert result["ok"] is False and result["isFile"] is True


def test_listing_respects_the_user_scope(tmp_path, monkeypatch):
    monkeypatch.delenv("DEXTER_FS_SCOPE", raising=False)
    monkeypatch.setenv("DEXTER_FS_ROOTS", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()
    (tmp_path / "elsewhere").mkdir()
    result = folders.list_folder(str(tmp_path / "elsewhere"))
    assert result["ok"] is False
    assert "outside the folders" in result["error"]


def test_the_listing_knows_whether_it_could_be_written_to(machine, tree):
    assert folders.list_folder(str(tree))["writable"] is True
    system = folders.list_folder(os.environ.get("SystemRoot", r"C:\Windows"))
    assert system["ok"] is True and system["writable"] is False


# ───────────────────────── drives ─────────────────────────


def test_the_drive_list_has_c_and_says_how_full_it_is(machine):
    rows = folders.drives()
    letters = [row["letter"] for row in rows]
    assert "C:" in letters
    c = next(row for row in rows if row["letter"] == "C:")
    assert c["ready"] is True and c["totalGb"] > 0
    assert "C:" in folders.describe_drives(rows)


def test_human_size_reads_like_a_person_wrote_it():
    assert folders.human_size(512) == "512 B"
    assert folders.human_size(2048) == "2 KB"
    assert folders.human_size(5 * 1024 * 1024) == "5.0 MB"
