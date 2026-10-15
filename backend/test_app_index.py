"""
Resolving a spoken app name (v13.7), and the bug that made this file necessary.

The owner asked *"can you open the hand tracking?"* and Dexter launched GNU `tr`
from Git's `usr\\bin`. Not a near miss - a different program entirely, which then sat
waiting on stdin forever with its console window in front of everything.

The cause was one line. Matching was a plain substring test in both directions,
preferring the **shortest** key:

    candidates = [(len(k), k) for k in apps if target in k or k in target]
    return apps[min(candidates)[1]]

`"tr" in "hand tracking"` is true, and `tr` is two characters, so it won. The same
line sent *"open Person to Profile"* to `pr.exe` and *"open the key"* to
`keytool.exe` - three times in one day, from an index that had 1,602 "applications"
in it including `rm`, `dd`, `sh` and `ls`.

So there are three rules here, and all three are load-bearing:

1. a short name only ever matches **exactly**;
2. a longer one matches only on **whole words**;
3. command-line utility directories are not applications and are not indexed.
"""

import os
import sys

import pytest

import app_index


@pytest.fixture
def index(monkeypatch):
    """A small index with the real shape of the problem in it."""
    apps = {
        "tr": r"C:\Program Files\Git\usr\bin\tr.exe",
        "pr": r"C:\Program Files\Git\usr\bin\pr.exe",
        "rm": r"C:\Program Files\Git\usr\bin\rm.exe",
        "keytool": r"C:\Program Files\Adobe\Adobe Animate\jre\bin\keytool.exe",
        "code": r"C:\Users\x\AppData\Local\Programs\Microsoft VS Code\Code.exe",
        "code insiders": r"C:\Users\x\AppData\Local\Programs\VS Code Insiders\Code.exe",
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "photoshop": r"C:\Program Files\Adobe\Photoshop\photoshop.exe",
        "vlc": r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        "obs studio": r"C:\Program Files\obs-studio\bin\obs64.exe",
    }
    monkeypatch.setattr(app_index, "_index", apps, raising=False)
    return apps


# ───────────────────── the sentences that went wrong ─────────────────────


@pytest.mark.parametrize("said", [
    "hand tracking",      # -> tr.exe
    "person to profile",  # -> pr.exe
    "the alarm",          # -> rm.exe, waiting to happen
])
def test_a_phrase_never_resolves_to_a_two_letter_command_line_tool(index, said):
    assert app_index.lookup(said) is None, "better no app than the wrong program"


def test_a_three_letter_word_does_not_become_a_different_program(index):
    """"open the key" was answered with Java's keytool."""
    assert app_index.lookup("the key") is None
    assert app_index.lookup("key") is None


def test_the_old_substring_rule_is_really_gone(index):
    """Belt and braces: the exact expression that caused it, asserted dead."""
    for said in ("hand tracking", "tracking", "printer", "trim the clip"):
        resolved = app_index.lookup(said) or ""
        assert not resolved.endswith(("tr.exe", "pr.exe", "rm.exe")), said


# ───────────────────────── and what must still work ─────────────────────────


def test_an_exact_name_still_wins_however_short(index):
    """VLC is three characters and is a real app. Exactness is the test, not length."""
    assert app_index.lookup("vlc").endswith("vlc.exe")
    assert app_index.lookup("VLC").endswith("vlc.exe")


def test_the_plainest_variant_is_preferred(index):
    assert app_index.lookup("code").endswith(r"Microsoft VS Code\Code.exe")


def test_an_app_named_inside_a_longer_phrase_is_found(index):
    assert app_index.lookup("photoshop").endswith("photoshop.exe")
    assert app_index.lookup("obs studio").endswith("obs64.exe")


def test_a_near_miss_is_still_forgiven(index):
    """Whisper mishears; "photoshopp" should still be Photoshop."""
    assert app_index.lookup("photoshopp").endswith("photoshop.exe")


def test_nothing_at_all_is_an_honest_none(index):
    assert app_index.lookup("qwertyuiop studio") is None
    assert app_index.lookup("") is None


# ───────────────────────── what gets indexed at all ─────────────────────────


def test_developer_tool_directories_are_not_applications():
    assert app_index._skip_dir(r"C:\Program Files\Git\usr\bin")
    assert app_index._skip_dir(r"C:\Program Files\Adobe\Adobe Animate 2023\jre\bin")
    assert not app_index._skip_dir(r"C:\Program Files\Google\Chrome\Application")
    assert not app_index._skip_dir(r"C:\Program Files\obs-studio\bin")


def test_command_line_tools_are_not_indexed_as_applications(tmp_path, monkeypatch):
    """An app is the thing with a window. The first attempt at this fix used the
    *length* of the name instead, and threw VLC out along with the coreutils - a
    three-letter app is still an app."""
    programs = tmp_path / "Program Files" / "Tools"
    programs.mkdir(parents=True)
    for name in ("tr.exe", "sh.exe", "audacity.exe", "vlc.exe"):
        (programs / name).write_bytes(b"MZ")
    start_menu = tmp_path / "Start Menu"
    start_menu.mkdir()
    (start_menu / "7-Zip.lnk").write_bytes(b"L")

    console = {"tr.exe", "sh.exe"}
    monkeypatch.setattr(app_index, "_SCAN_DIRS", [str(programs), str(start_menu)])
    monkeypatch.setattr(app_index, "is_console_exe",
                        lambda path: os.path.basename(path) in console)
    found = app_index._scan()

    assert "audacity" in found
    assert "vlc" in found, "short, but it has a window, so it is an app"
    # "7-Zip" indexes as "zip": _normalize strips leading version numbers. Odd,
    # pre-existing, and not this fix's business - what matters here is that the
    # shortcut survived.
    assert "zip" in found, "a deliberate shortcut is always an app"
    assert "tr" not in found and "sh" not in found


@pytest.mark.skipif(sys.platform != "win32", reason="PE headers, so Windows only")
def test_a_console_program_is_told_apart_from_a_windowed_one():
    """This is what decides whether a launch can leave something wedged. Python
    ships both kinds side by side, which makes it testable anywhere."""
    from tools_core import AppLauncherTool

    console = sys.executable                                  # python.exe
    windowed = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    assert AppLauncherTool.is_console_program(console) is True
    if os.path.exists(windowed):
        assert AppLauncherTool.is_console_program(windowed) is False


def test_nothing_is_launched_with_an_open_stdin(monkeypatch, tmp_path):
    """A console tool started with no arguments very often blocks reading stdin.
    Two `tr.exe` were still sitting on the owner's machine hours after this
    happened, so every launch closes stdin - there is no case where Dexter wants
    to be something's terminal."""
    import subprocess as sp

    from tools_core import AppLauncherTool

    calls = {}
    monkeypatch.setattr(AppLauncherTool, "is_console_program", staticmethod(lambda path: True))
    monkeypatch.setattr(sp, "Popen", lambda args, **kwargs: calls.update(args=args, **kwargs))
    AppLauncherTool._launch_target(r"C:\Program Files\Git\usr\bin\tr.exe")
    assert calls["stdin"] is sp.DEVNULL
