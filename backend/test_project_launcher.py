"""
Launching any of the owner's projects by name (v13.8).

"I shall just tell the name or a matching name and it shall launch it using the
corresponding batch or ps1 files."

Two problems, and the second is the one with teeth. Finding the project is fuzzy
matching: "mission reminder" has to reach a folder spelled `missionRemider`. Finding the
*launcher* is a judgement, because the real folders look like this:

    VrDeep/     launch.bat  launch_app.bat  launch_app.ps1  launch_debug.bat
    CaliberAI/  start.bat   start_app.bat   stop_app.bat
    BuzzScan/   run.bat     setup.bat       start.bat
    BuzzCode/   scripts/build-llama.ps1
    Sentinel/   build.ps1   make-installer.bat  sign.ps1

So scripts are scored rather than picked, and a project whose only scripts are builders
is **refused with an explanation** rather than having `uninstall.bat` run at it. On the
owner's machine that comes out as twelve startable projects and four that get named
instead - BuzzCode, BuzzWorlds, MastGaane, Sentinel.
"""

import os

import pytest

import project_launcher as pl


@pytest.fixture
def folder(tmp_path):
    """A projects root shaped like the real one, including its awkward cases."""
    def make(project, *scripts):
        directory = tmp_path / project
        directory.mkdir(parents=True, exist_ok=True)
        for script in scripts:
            path = directory / script
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("echo hi\n", encoding="utf-8")
        return directory

    make("VrDeep", "launch.bat", "launch_app.bat", "launch_app.ps1", "launch_debug.bat")
    make("CaliberAI", "start.bat", "start_app.bat", "stop_app.bat")
    make("BuzzScan", "run.bat", "setup.bat", "start.bat")
    make("BuzzAnimate", "BuzzAnimate.bat", "Create Desktop Shortcut.bat")
    make("missionRemider", "start.ps1")
    make("BuzzCode", os.path.join("scripts", "build-llama.ps1"))
    make("Sentinel", "build.ps1", "make-installer.bat", "rebuild-installer.bat")
    make("NoScripts")
    make("node_modules", "start.bat")          # must not be a project
    return tmp_path


# ───────────────────────── which script ─────────────────────────


def test_a_script_named_after_the_project_wins(folder):
    project = pl.find("buzzanimate", root=str(folder))
    assert project.best.name == "BuzzAnimate.bat"


def test_a_shortcut_maker_is_not_a_way_in(folder):
    project = pl.find("buzzanimate", root=str(folder))
    names = [launcher.name for launcher in project.launchers]
    assert names.index("BuzzAnimate.bat") < names.index("Create Desktop Shortcut.bat")


def test_stopping_is_never_starting(folder):
    """`stop_app.bat` sits next to `start_app.bat` in a real project, and one of them
    must never answer "open CaliberAI"."""
    project = pl.find("caliberai", root=str(folder))
    assert project.best.name in ("start_app.bat", "start.bat")
    stop = next(l for l in project.launchers if l.name == "stop_app.bat")
    assert stop.score < 0


def test_setup_is_not_starting_either(folder):
    project = pl.find("buzzscan", root=str(folder))
    assert project.best.name in ("start.bat", "run.bat")
    setup = next(l for l in project.launchers if l.name == "setup.bat")
    assert setup.score < project.best.score


def test_a_debug_launcher_loses_to_the_ordinary_one(folder):
    project = pl.find("vrdeep", root=str(folder))
    assert "debug" not in project.best.name.lower()


def test_a_batch_file_is_preferred_to_a_powershell_one_all_else_equal(folder):
    project = pl.find("vrdeep", root=str(folder))
    names = [l.name for l in project.launchers]
    assert names.index("launch_app.bat") < names.index("launch_app.ps1")


def test_a_project_of_only_build_scripts_is_refused_not_guessed(folder):
    """The important one: better to say nothing was started than to run a build."""
    result = pl.launch("buzzcode", root=str(folder), dry_run=True)
    assert result["ok"] is False
    assert "setup or teardown" in result["error"]
    assert "build-llama" in result["launcher"]


def test_an_installer_maker_is_not_a_launch(folder):
    result = pl.launch("sentinel", root=str(folder), dry_run=True)
    assert result["ok"] is False


# ───────────────────────── which project ─────────────────────────


@pytest.mark.parametrize("said,expected", [
    ("vrdeep", "VrDeep"),
    ("VrDeep", "VrDeep"),
    ("vr deep", "VrDeep"),
    ("mission reminder", "missionRemider"),     # the folder's own spelling is wrong
    ("missionreminder", "missionRemider"),
    ("buzz scan", "BuzzScan"),
    ("caliber", "CaliberAI"),
    ("animate", "BuzzAnimate"),
])
def test_the_name_people_say_finds_the_folder(folder, said, expected):
    project = pl.find(said, root=str(folder))
    assert project is not None and project.name == expected


def test_something_that_is_not_there_is_not_invented(folder):
    assert pl.find("quantum tractors", root=str(folder)) is None
    assert pl.find("", root=str(folder)) is None


def test_a_folder_with_no_launcher_is_not_offered(folder):
    names = [project.name for project in pl.projects(root=str(folder))]
    assert "NoScripts" not in names
    assert "node_modules" not in names, "not every directory is a project"


# ───────────────────────── how it is run ─────────────────────────


def test_a_batch_file_gets_its_own_console_and_no_stdin(folder, monkeypatch):
    """The v13.7 lesson, applied here too: nothing Dexter starts keeps an open stdin,
    and a launcher should not have Dexter as its parent for the rest of the session."""
    import subprocess

    seen = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda command, **kwargs: seen.update(command=command, **kwargs))
    result = pl.launch("buzzanimate", root=str(folder))
    assert result["ok"] is True
    assert seen["command"][:3] == ["cmd", "/c", "start"]
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["cwd"].endswith("BuzzAnimate")


def test_a_powershell_script_is_run_not_opened(folder, monkeypatch):
    """Double-clicking a .ps1 opens an editor; it has to be handed to powershell."""
    import subprocess

    seen = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda command, **kwargs: seen.update(command=command))
    pl.launch("mission reminder", root=str(folder))
    assert seen["command"][0] == "powershell"
    assert "-ExecutionPolicy" in seen["command"] and "Bypass" in seen["command"]
    assert seen["command"][-1].endswith("start.ps1")


def test_a_dry_run_touches_nothing(folder, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("a dry run must not start anything")))
    result = pl.launch("vrdeep", root=str(folder), dry_run=True)
    assert result["ok"] is True and result["dryRun"] is True


# ───────────────────────── the tools and the routing ─────────────────────────


def test_the_registry_is_asked_first(monkeypatch, folder):
    """Twelve of these are registered, and the registry knows their ports, their memory
    and that VrDeep's real launcher is a hidden .vbs. That route has to win."""
    import tools_projects

    monkeypatch.setattr(tools_projects, "_registered", lambda name: "vrdeep")
    called = {}

    def fake_execute(tool, args):
        called.update(tool=tool, args=args)
        from tool_registry import ToolResult

        return ToolResult(ok=True, summary="VrDeep is starting.", data={})

    monkeypatch.setattr("tool_registry.execute", fake_execute)
    result = tools_projects.launch_project("vrdeep")
    assert result.ok is True
    assert called == {"tool": "open_app", "args": {"app": "vrdeep"}}


def test_an_unregistered_project_goes_by_its_folder(monkeypatch, folder):
    import tools_projects

    monkeypatch.setattr(tools_projects, "_registered", lambda name: None)
    monkeypatch.setattr(pl, "ROOT", str(folder))
    monkeypatch.setattr(pl, "launch",
                        lambda name, root=None, dry_run=False: {
                            "ok": True, "project": "BuzzScan", "launcher": "x/start.bat",
                            "launcherName": "start.bat", "alternatives": ["run.bat"]})
    result = tools_projects.launch_project("buzz scan")
    assert result.ok is True
    assert "BuzzScan" in result.summary and "start.bat" in result.summary


def test_the_phrases_reach_it_and_other_opens_still_work():
    import tools_apps  # noqa: F401
    import tools_projects  # noqa: F401
    from router import fast_path

    assert fast_path("run the buzzscan project") == ("launch_project", {"project": "buzzscan"})
    assert fast_path("start up midnight buzz")[1]["project"] == "midnight buzz"
    assert fast_path("list my projects") == ("list_projects", {})
    # The registry still owns the ones it knows, and these are not projects at all.
    assert fast_path("launch vrdeep")[0] == "open_app"
    assert fast_path("open chrome")[0] == "launch_app"
    assert fast_path("open youtube")[0] == "browser_open"
