"""
Closing any app, not just the twenty with an alias (v13).

"Close BuzzAnimate" failed twice over: the registry record named the wrong
executable (`buzz-app.exe`; the binary is `buzzanimate.exe`) and
`desktop_control` only knew an app by a hand-written alias table plus an exact
`<name>.exe` guess. Both halves are covered here - the generic matcher, and the
registry's fallback to "whatever is running out of that app's folder".
"""

import os

import pytest

import apps_registry
import desktop_control as dc
import tools_desktop


def W(hwnd, title, pid=0, process=""):
    return dc.Win(hwnd=hwnd, title=title, pid=pid, process=process)


ANIMATE = W(20, "BuzzAnimate", 500, "buzzanimate.exe")
TEAMS = W(21, "Chat | Microsoft Teams", 501, "ms-teams.exe")
DEXTER = W(22, "Dexter", os.getpid(), "pythonw.exe")


@pytest.fixture
def machine(monkeypatch):
    """Windows and processes, both faked. Nothing real is touched."""
    state = {
        "windows": [ANIMATE, TEAMS, DEXTER],
        "front": ANIMATE.hwnd,
        "processes": [
            {"pid": 500, "name": "buzzanimate.exe",
             "exe": r"B:\youtubeProjects\Buzzcaf_Media\BuzzAnimate\target\release\buzzanimate.exe",
             "cmdline": "buzzanimate.exe"},
            {"pid": 501, "name": "ms-teams.exe",
             "exe": r"C:\Users\x\AppData\Local\Microsoft\Teams\ms-teams.exe",
             "cmdline": "ms-teams.exe"},
            {"pid": 502, "name": "python.exe",
             "exe": r"B:\youtubeProjects\Buzzcaf_Media\VrDeep\.venv\Scripts\python.exe",
             "cmdline": "python.exe render.py"},
            {"pid": 503, "name": "code.exe", "exe": r"C:\Program Files\Code\code.exe",
             "cmdline": r"code.exe B:\youtubeProjects\Buzzcaf_Media\BuzzAnimate"},
        ],
        "closed": [], "killed": [],
    }
    monkeypatch.setattr(dc, "_enum_windows", lambda: list(state["windows"]))
    monkeypatch.setattr(dc, "_foreground_hwnd", lambda: state["front"])
    monkeypatch.setattr(dc, "_process_table", lambda: list(state["processes"]))
    monkeypatch.setattr(dc, "_post_close", lambda hwnd: state["closed"].append(hwnd))
    monkeypatch.setattr(dc, "_terminate", lambda pid: state["killed"].append(pid) or True)
    monkeypatch.setattr(dc, "_file_description", lambda exe: "")
    return state


# ───────────────────────── finding an app by any name ─────────────────────────


def test_an_app_is_found_by_its_image_name(machine):
    found = dc.find_processes("buzzanimate")
    assert [row["pid"] for row in found] == [500]
    assert found[0]["why"] == "image name"


def test_an_app_whose_exe_says_nothing_is_found_by_its_file_description(machine, monkeypatch):
    """The name a vendor compiles into the binary is sometimes the only place the
    spoken name appears - "open broadcaster software" is `obs64.exe`."""
    machine["processes"] = [{"pid": 600, "name": "obs64.exe",
                             "exe": r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
                             "cmdline": "obs64.exe"}]
    monkeypatch.setattr(dc, "_file_description",
                        lambda exe: "Open Broadcaster Software" if "obs" in exe else "")
    found = dc.find_processes("open broadcaster software")
    assert [row["pid"] for row in found] == [600]
    assert found[0]["why"] == "file description"


def test_an_app_is_found_by_the_path_its_exe_sits_in(machine):
    found = dc.find_processes("microsoft teams")
    assert [row["pid"] for row in found] == [501]
    assert found[0]["why"] == "executable path"


def test_a_project_is_found_by_the_folder_it_runs_from(machine, monkeypatch):
    """VrDeep is `python.exe`. The only thing that makes it VrDeep is its path."""
    monkeypatch.setattr(dc, "_registry_record",
                        lambda name: {"id": "vrdeep", "root": "VrDeep"} if "vrdeep" in name else None)
    monkeypatch.setattr(apps_registry, "root_of",
                        lambda record: r"B:\youtubeProjects\Buzzcaf_Media\VrDeep")
    found = dc.find_processes("vrdeep")
    assert [row["pid"] for row in found] == [502]
    assert "project folder" in found[0]["why"]


def test_dexter_never_matches_itself(machine):
    assert all(row["pid"] != os.getpid() for row in dc.find_processes("pythonw"))


def test_a_window_is_found_through_the_process_when_the_title_does_not_say_it(machine):
    """A window called "Chat | Microsoft Teams" is not matched by the word
    "teams" alone in the old title search - the process is what finds it."""
    windows = dc.find_windows("ms-teams")
    assert [w.hwnd for w in windows] == [TEAMS.hwnd]


def test_closing_an_app_asks_its_windows_to_close(machine):
    info = dc.close_app("buzzanimate", wait=0)
    assert machine["closed"] == [ANIMATE.hwnd]
    assert info["asked"] == 1
    assert machine["killed"] == [], "closing is never a kill"


def test_a_windowless_app_is_reported_as_running_not_as_absent(machine):
    """"It doesn't have a window open" reads as "it isn't running", which is how
    "close X" became a dead end for tray apps and servers."""
    machine["windows"] = [DEXTER]
    info = dc.close_app("buzzanimate", wait=0)
    assert info["windowless"] is True
    assert info["pids"] == [500]
    assert machine["closed"] == []


def test_something_that_is_not_running_at_all_still_says_so(machine):
    machine["windows"] = [DEXTER]
    machine["processes"] = []
    with pytest.raises(dc.DesktopError):
        dc.close_app("buzzanimate", wait=0)


def test_the_tool_offers_a_force_stop_for_a_windowless_app(machine, monkeypatch):
    machine["windows"] = [DEXTER]
    monkeypatch.setattr(tools_desktop, "_stop_via_registry", lambda name: None)
    result = tools_desktop.close_app("buzzanimate")
    assert result.ok is False
    assert "force stop" in result.summary.lower()
    assert machine["killed"] == []


def test_a_project_with_a_stop_route_is_shut_down_properly_not_killed(machine, monkeypatch):
    """For one of the owner's own apps, "close it" means the shutdown the registry
    knows about - the Studio's /shutdown POST, not a terminate."""
    machine["windows"] = [DEXTER]
    called = {}

    def _stopped(name):
        called["name"] = name
        return tools_desktop.ToolResult(ok=True, summary="BuzzAnimate is stopped.")

    monkeypatch.setattr(tools_desktop, "_stop_via_registry", _stopped)
    result = tools_desktop.close_app("buzzanimate")
    assert result.ok is True and "stopped" in result.summary
    assert called["name"] == "buzzanimate"


def test_killing_uses_the_generic_matcher_too(machine):
    machine["windows"] = [DEXTER]
    info = dc.kill_app("buzzanimate")
    assert machine["killed"] == [500]
    assert info["found"] == 1


# ───────────────────────── the registry's own fallback ─────────────────────────


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A registry whose record names the wrong executable, as BuzzAnimate's did."""
    root = tmp_path / "Toy"
    root.mkdir()
    record = {
        "id": "toy", "label": "Toy", "root": str(root),
        "launch": {"cmd": "toy.vbs"},
        "stop": {"strategy": "process_tree", "process": "wrong-name.exe"},
        "probes": [{"process": "wrong-name.exe"}],
    }
    monkeypatch.setattr(apps_registry, "load", lambda force=False: {"root": str(tmp_path),
                                                                   "apps": [record]})
    apps_registry._cache = None
    return record, root


def test_a_record_that_names_the_wrong_binary_still_sees_the_app(registry, monkeypatch):
    record, root = registry
    import procs

    running = [{"pid": 900, "name": "toy-app.exe", "exe": str(root / "toy-app.exe"),
                "cmdline": "toy-app.exe"}]
    monkeypatch.setattr(procs, "find", lambda names=None, path_contains=None, **kw:
                        [] if names else list(running))
    state = apps_registry.probe(record)
    assert state["running"] is True
    assert "process in Toy" in state["evidence"]
    assert state["pids"] == [900]


def test_an_editor_with_the_folder_open_is_not_the_app_running(registry, monkeypatch):
    record, root = registry
    import procs

    bystander = [{"pid": 901, "name": "code.exe", "exe": r"C:\Program Files\Code\code.exe",
                  "cmdline": f"code.exe {root}"}]
    monkeypatch.setattr(procs, "find", lambda names=None, path_contains=None, **kw:
                        [] if names else list(bystander))
    assert apps_registry.probe(record)["running"] is False


def test_stopping_falls_back_to_whatever_runs_from_that_folder(registry, monkeypatch):
    record, root = registry
    import procs

    running = [{"pid": 902, "name": "toy-app.exe", "exe": str(root / "toy-app.exe"),
                "cmdline": "toy-app.exe"}]
    killed = []
    monkeypatch.setattr(procs, "find", lambda names=None, path_contains=None, **kw:
                        [] if names else list(running))
    monkeypatch.setattr(procs, "kill_tree", lambda pid, timeout=5.0: killed.append(pid) or True)
    result = apps_registry.stop(record)
    assert result["ok"] is True
    assert killed == [902]
    assert "process in folder" in result["how"]


def test_a_record_that_says_do_not_stop_is_still_never_stopped(registry, monkeypatch):
    record, root = registry
    record = {**record, "stop": {"strategy": "none", "why": "it stops itself"}}
    import procs

    monkeypatch.setattr(procs, "find", lambda *a, **k: [
        {"pid": 903, "name": "toy-app.exe", "exe": str(root / "toy-app.exe"), "cmdline": ""}
    ])
    monkeypatch.setattr(procs, "kill_tree",
                        lambda pid, timeout=5.0: pytest.fail("nothing may be killed here"))
    result = apps_registry.stop(record)
    assert result["ok"] is False
    assert "stops itself" in result["message"]
