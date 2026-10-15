"""
Tests for the apps registry (roadmap v9, D3).

Everything here runs against a **temp registry** and fake probes. That is not
squeamishness: the real registry names VrDeep, which was rendering while this
was written, and a test that resolved "vrdeep" to the real record and called
`stop` would have cost the owner hours. `conftest.py` also refuses `kill_tree`
outright.

What matters:

* the shipped `apps.default.json` is real, complete and internally consistent —
  it is the thing Dexter acts on, so a typo in it is a production bug;
* resolution never guesses ("open code" is VS Code, not BuzzCode);
* probes match on the *path*, because half these apps are `python.exe`;
* nothing is stopped by a strategy the record did not name.

Run:  python -m pytest backend/test_apps_registry.py -q
"""

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apps_registry


# ───────────────────────── the shipped registry ─────────────────────────


def test_the_shipped_registry_covers_every_app_in_the_guardian_plan():
    ids = {a["id"] for a in apps_registry.all_apps()}
    expected = {
        "dexter", "buzzcafai", "buzzbrain", "buzzcode", "buzzedit", "vrdeep",
        "midnightbuzz", "mastgaane", "caliberai", "buzzfin", "buzzcafcoffee",
        "buzzanimate", "buzzviolin", "buzzworlds", "smartcleaner",
        "missionreminder", "buzzcaf",
    }
    assert ids == expected, f"missing {expected - ids}, unexpected {ids - expected}"


def test_every_record_is_shaped_the_way_the_code_reads_it():
    for app in apps_registry.all_apps():
        where = app.get("id")
        assert app.get("label"), where
        assert isinstance(app.get("names"), list) and app["names"], where
        assert app.get("class") in ("assistant", "interactive", "batch"), where
        assert isinstance(app.get("vram_mib"), int), where
        assert isinstance(app.get("ram_mib"), int), where
        assert isinstance(app.get("exclusive_with"), list), where
        strategy = (app.get("stop") or {}).get("strategy")
        assert strategy in ("none", "health_route", "runtime_pid", "port_owner",
                            "process_tree", "script", "window"), f"{where}: {strategy}"
        # A launch entry with no cmd has to explain itself, or "open buzzworlds"
        # answers with a shrug.
        launch = app.get("launch") or {}
        if not launch.get("cmd"):
            assert launch.get("why"), f"{where} has no launch cmd and no reason"


def test_the_heavy_apps_declare_what_they_cost_and_what_they_exclude():
    """A 16 GB card fits one of these at a time; the registry has to know that."""
    for app_id in ("vrdeep", "buzzcode", "buzzedit", "buzzworlds"):
        app = apps_registry.get(app_id)
        assert app["vram_mib"] >= 8000, app_id
        assert app["class"] == "batch", app_id
        assert app["exclusive_with"], f"{app_id} must name what it cannot share with"


def test_the_studio_keeps_its_specialist():
    """buzzcaf_client knows the port walk and the runtime file; the registry
    delegates rather than reimplementing it badly."""
    assert apps_registry.get("buzzcafai")["handler"] == "buzzcaf_client"


def test_the_launch_scripts_exist_on_this_machine():
    """The registry names the owner's real launchers. If a checkout moved, this
    is where it should be noticed - not in a failed launch at 1 a.m."""
    missing = []
    for app in apps_registry.all_apps():
        launch = app.get("launch") or {}
        cmd = launch.get("cmd")
        if not cmd or launch.get("resolve") in ("path", "python", "shell"):
            continue
        root = apps_registry.root_of(app)
        if not os.path.isdir(root):
            continue  # a checkout that is not here is the owner's business
        if not os.path.exists(os.path.join(root, str(cmd))):
            missing.append(f"{app['id']}: {cmd}")
    assert missing == [], f"launch scripts not found: {missing}"


# ───────────────────────── resolution ─────────────────────────


@pytest.mark.parametrize("said,expected", [
    ("vrdeep", "vrdeep"),
    ("vr deep", "vrdeep"),
    ("VrDeep", "vrdeep"),
    ("buzzedit", "buzzedit"),
    ("buzz edit", "buzzedit"),
    ("the studio", "buzzcafai"),
    ("caliber", "caliberai"),
    ("finance", "buzzfin"),
    ("mission reminder", "missionreminder"),
    ("the coding engine", "buzzcode"),
])
def test_the_names_the_owner_actually_says_resolve(said, expected):
    record = apps_registry.resolve(said)
    assert record and record["id"] == expected


@pytest.mark.parametrize("said", ["code", "chrome", "notepad", "buzz", "", "   ", "spotify"])
def test_it_refuses_to_guess(said):
    """"open code" is VS Code, and "open chrome" is Chrome. Starting a 9 GB
    project because a substring matched is not a small mistake."""
    assert apps_registry.resolve(said) is None


# ───────────────────────── a temp registry ─────────────────────────


@pytest.fixture()
def temp_registry(tmp_path, monkeypatch):
    """A two-app registry rooted in a temp folder. Nothing real is reachable."""
    root = tmp_path / "projects"
    (root / "Toy").mkdir(parents=True)
    (root / "Toy" / "start.bat").write_text("@echo off\n", encoding="utf-8")
    (root / "Toy" / "app.log").write_text("first line\nlast line\n", encoding="utf-8")
    (root / "Batchy").mkdir(parents=True)
    (root / "Batchy" / "out").mkdir()

    registry = {
        "root": str(root),
        "apps": [
            {
                "id": "toy", "label": "Toy", "names": ["toy"], "root": "Toy",
                "launch": {"cmd": "start.bat"},
                "stop": {"strategy": "port_owner", "port": 9911},
                "probes": [{"file": "ready.json"}],
                "class": "interactive", "vram_mib": 0, "ram_mib": 128,
                "exclusive_with": [], "logs": ["app.log"],
            },
            {
                "id": "batchy", "label": "Batchy", "names": ["batchy"], "root": "Batchy",
                "launch": {"cmd": "run.bat"},
                "stop": {"strategy": "process_tree", "process": "python.exe",
                         "path_contains": "\\Batchy\\"},
                "probes": [{"process": "python.exe", "path_contains": "\\Batchy\\"}],
                "class": "batch", "vram_mib": 9000, "ram_mib": 4000,
                "exclusive_with": ["toy"], "logs": [],
                "batch_job": True, "queue_file": "out\\queue.json",
            },
        ],
    }
    path = tmp_path / "apps.default.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(apps_registry, "DEFAULT_FILE", str(path))
    monkeypatch.setattr(apps_registry, "OVERRIDE_FILE", str(tmp_path / "nope.json"))
    apps_registry.load(force=True)
    yield {"root": root, "path": path, "tmp": tmp_path}
    apps_registry._cache = None


def test_a_file_probe_answers_from_disk(temp_registry, monkeypatch):
    toy = apps_registry.get("toy")
    assert apps_registry.probe(toy)["running"] is False
    (temp_registry["root"] / "Toy" / "ready.json").write_text("{}", encoding="utf-8")
    state = apps_registry.probe(toy)
    assert state["running"] is True
    assert state["evidence"] == "file ready.json"


def test_a_process_probe_matches_on_the_path_not_the_name(temp_registry, monkeypatch):
    """Half these apps are python.exe. Matching on the name alone would report
    VrDeep as running because MidnightBuzz is."""
    seen = {}

    def fake_find(names=None, path_contains=None, cmdline_contains=None):
        seen["names"], seen["path"] = names, path_contains
        return [{"pid": 4242, "name": "python.exe", "exe": r"C:\py\python.exe",
                 "cmdline": r"python.exe B:\projects\Batchy\main.py"}]

    monkeypatch.setattr(apps_registry.procs, "find", fake_find)
    state = apps_registry.probe(apps_registry.get("batchy"))
    assert state["running"] is True and state["pids"] == [4242]
    assert seen["names"] == ["python.exe"]
    assert seen["path"] == "\\Batchy\\"


def test_status_reports_what_the_ui_and_the_tools_need(temp_registry, monkeypatch):
    monkeypatch.setattr(apps_registry.procs, "find", lambda *a, **k: [])
    row = apps_registry.status(apps_registry.get("batchy"))
    assert row["running"] is False
    assert row["vramMib"] == 9000 and row["class"] == "batch"
    assert row["canLaunch"] is True and row["canStop"] is True
    assert row["exclusiveWith"] == ["toy"]


def test_conflicts_name_what_is_actually_running(temp_registry, monkeypatch):
    monkeypatch.setattr(apps_registry, "probe", lambda app: {"running": app["id"] == "toy",
                                                            "evidence": "", "pids": [], "detail": ""})
    clash = apps_registry.conflicts(apps_registry.get("batchy"))
    assert [c["id"] for c in clash] == ["toy"]


def test_a_batch_app_with_a_queue_is_mid_job(temp_registry):
    batchy = apps_registry.get("batchy")
    assert apps_registry.mid_job(batchy) is None
    queue = temp_registry["root"] / "Batchy" / "out" / "queue.json"
    queue.write_text(json.dumps([{"status": "pending"}, {"status": "done"}]), encoding="utf-8")
    busy = apps_registry.mid_job(batchy)
    assert busy and "1 job(s)" in busy


def test_an_interactive_app_is_never_mid_job(temp_registry):
    """The confirmation exists for work that would be lost, not for every stop."""
    assert apps_registry.mid_job(apps_registry.get("toy")) is None


def test_launch_runs_the_script_the_record_names(temp_registry, monkeypatch):
    ran = {}

    class FakeProc:
        pid = 1234

    def fake_popen(argv, **kwargs):
        ran["argv"], ran["cwd"] = argv, kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(apps_registry.subprocess, "Popen", fake_popen)
    result = apps_registry.launch(apps_registry.get("toy"))
    assert result["ok"] and result["pid"] == 1234
    assert ran["argv"][0].endswith("start.bat")
    assert ran["cwd"].endswith("Toy")


def test_a_missing_launch_script_fails_honestly(temp_registry, monkeypatch):
    monkeypatch.setattr(apps_registry.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("nothing should be spawned"))
    result = apps_registry.launch(apps_registry.get("batchy"))
    assert result["ok"] is False
    assert "no launch script" in result["message"]


def test_a_vbs_launcher_goes_through_cscript(temp_registry, monkeypatch):
    """wscript pops a modal dialog behind everything when a launcher fails;
    cscript says so on stderr instead."""
    (temp_registry["root"] / "Toy" / "go.vbs").write_text("", encoding="utf-8")
    app = dict(apps_registry.get("toy"), launch={"cmd": "go.vbs"})
    argv = apps_registry._resolve_command(app)
    assert argv[:2] == ["cscript", "//nologo"]


def test_stop_only_uses_the_strategy_the_record_names(temp_registry, monkeypatch):
    calls = []
    monkeypatch.setattr(apps_registry.procs, "port_owner",
                        lambda port: calls.append(port) or {"pid": 77})
    monkeypatch.setattr(apps_registry.procs, "kill_tree", lambda pid, timeout=5.0: True)
    result = apps_registry.stop(apps_registry.get("toy"))
    assert result["ok"] and result["how"] == "port_owner"
    assert calls == [9911]


def test_stop_none_is_a_real_answer(temp_registry, monkeypatch):
    app = dict(apps_registry.get("toy"), stop={"strategy": "none", "why": "it stops itself"})
    monkeypatch.setattr(apps_registry.procs, "kill_tree",
                        lambda *a, **k: pytest.fail("nothing may be killed"))
    result = apps_registry.stop(app)
    assert result["ok"] is False and "stops itself" in result["message"]


def test_the_log_tail_is_read_from_the_apps_own_file(temp_registry):
    text = apps_registry.last_log_line(apps_registry.get("toy"), lines=1)
    assert text == "last line"


def test_owner_overrides_win_without_losing_the_rest(tmp_path, monkeypatch, temp_registry):
    """The shipped file is Dexter's; apps.json is the owner's, and is never written."""
    override = tmp_path / "apps.json"
    override.write_text(json.dumps({"apps": [{"id": "toy", "vram_mib": 4096}]}), encoding="utf-8")
    monkeypatch.setattr(apps_registry, "OVERRIDE_FILE", str(override))
    apps_registry.load(force=True)
    toy = apps_registry.get("toy")
    assert toy["vram_mib"] == 4096
    assert toy["label"] == "Toy", "an override must not wipe the fields it did not set"


# ───────── a file is not a process (found live, roadmap v9) ─────────
#
# The first live run of `/api/dexter/apps` reported VrDeep, MastGaane and
# CaliberAI as running. None of them were. Their file probes were matching a
# two-hour-old session file, a database that is always on disk, and a log file
# that is always on disk. "This file exists" is evidence that something once
# wrote it, not that anything is running now.


def test_a_stale_file_is_not_evidence_that_an_app_is_running(temp_registry, monkeypatch):
    monkeypatch.setattr(apps_registry.procs, "find", lambda *a, **k: [])
    app = dict(apps_registry.get("toy"),
               probes=[{"file": "session.json", "max_age_secs": 900}])
    path = temp_registry["root"] / "Toy" / "session.json"
    path.write_text("{}", encoding="utf-8")

    assert apps_registry.probe(app)["running"] is True

    os.utime(path, (time.time() - 3 * 3600, time.time() - 3 * 3600))
    assert apps_registry.probe(app)["running"] is False, \
        "a three-hour-old session file must not report a render that finished"


def test_a_file_probe_can_opt_out_of_freshness(temp_registry, monkeypatch):
    """The Studio's runtime file is deleted on a clean exit, so its mere
    existence really is the signal."""
    monkeypatch.setattr(apps_registry.procs, "find", lambda *a, **k: [])
    app = dict(apps_registry.get("toy"),
               probes=[{"file": "runtime.json", "max_age_secs": 0}])
    path = temp_registry["root"] / "Toy" / "runtime.json"
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (time.time() - 90000, time.time() - 90000))
    assert apps_registry.probe(app)["running"] is True


def test_process_evidence_is_preferred_over_a_file(temp_registry, monkeypatch):
    """Probe order in the record must not decide which evidence wins."""
    monkeypatch.setattr(apps_registry.procs, "find", lambda *a, **k: [
        {"pid": 99, "name": "python.exe", "exe": "", "cmdline": ""}])
    app = dict(apps_registry.get("toy"), probes=[
        {"file": "ready.json"},
        {"process": "python.exe", "path_contains": "\\Toy\\"},
    ])
    (temp_registry["root"] / "Toy" / "ready.json").write_text("{}", encoding="utf-8")
    state = apps_registry.probe(app)
    assert state["evidence"] == "process python.exe"
    assert state["pids"] == [99]


def test_the_shipped_registry_has_no_bare_file_liveness_probes():
    """Every shipped file probe either carries a freshness window or explicitly
    opts out. A new one added without thinking fails here rather than in a
    'what's running' that quietly lies."""
    offenders = []
    for app in apps_registry.all_apps():
        for spec in app.get("probes", []) or []:
            if ("file" in spec or "file_env" in spec) and "max_age_secs" not in spec:
                offenders.append(f"{app['id']}: {spec}")
    assert offenders == [], (
        "these file probes would report an app as running off a file that is "
        "simply always on disk:\n  " + "\n  ".join(offenders)
    )


def test_overview_probes_the_apps_in_parallel_and_keeps_their_order(monkeypatch):
    """Seventeen apps, most of them stopped, each paying a dead-port timeout.

    Sequentially that measured **16 s** on the live machine: `/api/dexter/apps`
    blew the verification suite's timeout and the Apps panel sat blank. The
    probes are independent and read-only, so they run on a pool — but the rows
    must still come back in registry order, and one app that throws must not
    take the other sixteen with it.
    """
    apps = [{"id": f"app{i}", "label": f"App {i}"} for i in range(8)]
    monkeypatch.setattr(apps_registry, "all_apps", lambda: apps)

    concurrent = []
    live = {"n": 0}
    lock = __import__("threading").Lock()

    def slow_status(app):
        with lock:
            live["n"] += 1
            concurrent.append(live["n"])
        time.sleep(0.15)
        with lock:
            live["n"] -= 1
        return {"id": app["id"], "label": app["label"], "running": False}

    monkeypatch.setattr(apps_registry, "status", slow_status)
    started = time.time()
    rows = apps_registry.overview()
    elapsed = time.time() - started

    assert [r["id"] for r in rows] == [a["id"] for a in apps], "rows lost their order"
    assert max(concurrent) > 1, "the probes still ran one after another"
    assert elapsed < 8 * 0.15, f"no better than sequential ({elapsed:.2f}s)"


# ───────────── ports: a preferred number is not an address (P1) ─────────────


def test_the_shipped_registry_has_no_fixed_port_key_left():
    """`port` meant "this is where it is". `preferred_port` means "this is what
    it asks for", and the difference is the whole of GUARDIAN_PLAN.md section 11.
    A record that still carries the old key would be read as an address again."""
    shipped = json.load(open(apps_registry.DEFAULT_FILE, "r", encoding="utf-8"))
    stale = [a["id"] for a in shipped["apps"] if "port" in a]
    assert not stale, f"these records still carry a fixed `port`: {stale}"


def test_every_discoverable_app_declares_a_port_to_start_looking_from():
    """A `health_path` with no `preferred_port` can never be scanned for."""
    for app in apps_registry.all_apps():
        if app.get("health_path"):
            assert apps_registry.preferred_port(app), f"{app['id']} has no preferred_port"


def test_the_studio_publishes_under_its_own_name_not_its_record_id():
    """Its record is `buzzcafai`; its health has always said `app: "buzzcaf"`."""
    studio = apps_registry.get("buzzcafai")
    assert apps_registry.ledger_id(studio) == "buzzcaf"
    assert apps_registry.ledger_id(apps_registry.get("dexter")) == "dexter"


def test_sub_service_ports_are_recorded_as_preferences_too():
    """ComfyUI, XTTS, Vite and Next are ports as much as the parent's is."""
    assert apps_registry.get("buzzedit")["extra_preferred"] == {"comfyui": 8188}
    assert apps_registry.get("midnightbuzz")["extra_preferred"] == {"xtts": 8096, "vite": 3005}
    assert apps_registry.get("caliberai")["extra_preferred"] == {"vite": 5173}
    assert apps_registry.get("buzzcafcoffee")["extra_preferred"] == {"next": 3000}


def test_live_port_follows_the_app_that_stepped_forward(monkeypatch):
    """BuzzEdit asked for 8099, the Studio had it, so BuzzEdit is on 8100 — and
    that is the number the owner has to be told."""
    app = {"id": "buzzedit", "preferred_port": 8099, "health_path": "/api/health"}
    monkeypatch.setattr(
        apps_registry.buzzcaf_ports, "discover",
        lambda name, port, path, *a, **k: "http://127.0.0.1:8100",
    )
    assert apps_registry.live_port(app) == 8100


def test_live_port_is_none_when_the_thing_on_the_preferred_port_is_someone_else(monkeypatch):
    """Caught live: BuzzEdit's Electron was up, Dexter had taken 8099, and the
    registry reported "BuzzEdit: running on 8099". Something *is* listening on
    the preferred port - it is simply not this app, and an app reported on a
    port it does not hold sends the next launch straight into a collision. An
    app that can be identified and is not there has no live port."""
    app = {"id": "buzzedit", "preferred_port": 8099, "health_path": "/api/health"}
    monkeypatch.setattr(apps_registry.buzzcaf_ports, "discover", lambda *a, **k: None)
    monkeypatch.setattr(apps_registry.buzzcaf_ports, "entry", lambda *a, **k: None)
    monkeypatch.setattr(apps_registry.procs, "port_open", lambda *a, **k: True)
    assert apps_registry.live_port(app) is None


def test_an_app_without_an_identity_route_is_read_from_its_own_ledger_entry(monkeypatch):
    """buzzcode's llama-server is llama.cpp and will never say `app` — but
    buzzcode itself publishes, and a live pid plus an open port is enough."""
    app = {"id": "buzzcode", "preferred_port": 8089}
    monkeypatch.setattr(
        apps_registry.buzzcaf_ports, "entry",
        lambda name, *a, **k: {"port": 8091, "pid": os.getpid()},
    )
    monkeypatch.setattr(apps_registry.procs, "port_open", lambda port, *a, **k: port == 8091)
    assert apps_registry.live_port(app) == 8091


def test_a_ledger_entry_from_a_dead_process_is_not_a_port(monkeypatch):
    app = {"id": "buzzcode", "preferred_port": 8089}
    monkeypatch.setattr(
        apps_registry.buzzcaf_ports, "entry",
        lambda name, *a, **k: {"port": 8091, "pid": 4},
    )
    monkeypatch.setattr(apps_registry.buzzcaf_ports, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(apps_registry.procs, "port_open", lambda port, *a, **k: port == 8091)
    assert apps_registry.live_port(app) is None


def test_status_reports_where_it_is_and_where_it_wanted_to_be(monkeypatch):
    app = {"id": "buzzedit", "label": "BuzzEdit", "preferred_port": 8099,
           "health_path": "/api/health"}
    monkeypatch.setattr(apps_registry, "probe",
                        lambda a: {"running": True, "evidence": "port 8100", "pids": [], "detail": ""})
    monkeypatch.setattr(apps_registry, "live_port", lambda a: 8100)
    row = apps_registry.status(app)
    assert row["port"] == 8100
    assert row["livePort"] == 8100
    assert row["preferredPort"] == 8099


def test_a_stopped_app_falls_back_to_the_port_it_would_ask_for(monkeypatch):
    app = {"id": "buzzfin", "label": "BuzzFin", "preferred_port": 8756, "health_path": "/health"}
    monkeypatch.setattr(apps_registry, "probe",
                        lambda a: {"running": False, "evidence": "", "pids": [], "detail": ""})
    row = apps_registry.status(app)
    assert row["port"] == 8756
    assert row["livePort"] is None


def test_stopping_by_port_owner_follows_the_live_port(monkeypatch):
    """`port_owner` on the *preferred* port would kill whoever got there first."""
    killed = {}
    monkeypatch.setattr(apps_registry, "live_port", lambda a: 8757)
    monkeypatch.setattr(apps_registry.procs, "port_owner",
                        lambda port: {"pid": 4242} if port == 8757 else None)
    monkeypatch.setattr(apps_registry.procs, "kill_tree",
                        lambda pid, timeout=5.0: killed.setdefault("pid", pid) or True)
    app = {"id": "buzzfin", "preferred_port": 8756,
           "stop": {"strategy": "port_owner", "port": 8756}}
    result = apps_registry.stop(app)
    assert result["ok"] and killed["pid"] == 4242


def test_a_probe_url_follows_the_app_to_the_port_it_took(monkeypatch):
    """`{base}` instead of a literal `http://127.0.0.1:8099/...` — a fixed URL in
    the registry is a hardcoded port under another name, and goes stale the
    first time the app it names has to step forward."""
    app = {"id": "buzzbrain", "preferred_port": 8099, "ledger_id": "buzzcaf"}
    monkeypatch.setattr(apps_registry, "live_port", lambda a: 8101)
    assert apps_registry._probe_url("{base}/api/buzzbrain/channels", app) == (
        "http://127.0.0.1:8101/api/buzzbrain/channels"
    )


def test_a_probe_url_is_dropped_when_we_do_not_know_where_the_app_is(monkeypatch):
    app = {"id": "buzzbrain", "preferred_port": 8099, "ledger_id": "buzzcaf"}
    monkeypatch.setattr(apps_registry, "live_port", lambda a: None)
    assert apps_registry._probe_url("{base}/api/buzzbrain/channels", app) == ""


def test_the_shipped_registry_carries_no_literal_loopback_url():
    """Every probe and shutdown route is either `{base}`-relative or a port
    number the record also declares as a preference."""
    raw = open(apps_registry.DEFAULT_FILE, "r", encoding="utf-8").read()
    assert "127.0.0.1" not in raw, "a fixed loopback URL is back in apps.default.json"


def test_an_app_that_publishes_under_someone_elses_name_is_not_probed_by_identity(monkeypatch):
    """BuzzBrain shares the Studio's port. The Studio answering there is
    evidence about the Studio — BuzzBrain still has to be asked separately, or
    "is BuzzBrain loaded" becomes "is the Studio up"."""
    app = apps_registry.get("buzzbrain")
    assert apps_registry.ledger_id(app) == "buzzcaf" != app["id"]
    monkeypatch.setattr(apps_registry, "live_port", lambda a: 8099)
    asked = []
    monkeypatch.setattr(apps_registry, "_http_probe",
                        lambda url, expect: asked.append(url) or False)
    state = apps_registry.probe(app)
    assert state["running"] is False
    assert asked == ["http://127.0.0.1:8099/api/buzzbrain/channels"]


#: What each app's health route really answers, verified live during P4 by
#: starting the app and reading the body. The registry is a map of the
#: ecosystem, and a map with a wrong path on it sends every consumer - Dexter's
#: `/api/dexter/apps`, the launchers, `setup_guardian.ps1` - to a 404 and lets
#: them conclude the app is down while it is running perfectly.
HEALTH_ROUTES = {
    "dexter": ("/health", "dexter"),
    "buzzcafai": ("/health", "buzzcaf"),
    "buzzbrain": ("/health", "buzzcaf"),
    # BuzzFin's security gate answers 404 on a bare `/health`; only the
    # versioned route is outside the lock. This was wrong in the shipped
    # registry until P4 and made BuzzFin permanently "not running".
    "buzzfin": ("/api/v1/health", "buzzfin"),
    "buzzcafcoffee": ("/health", "buzzcafcoffee"),
    "mastgaane": ("/health", "mastgaane"),
    "midnightbuzz": ("/health", "midnightbuzz"),
    "caliberai": ("/api/health", "caliberai"),
    "buzzedit": ("/api/health", "buzzedit"),
    # Vite serves `public/health.json`; there is no server to add a route to.
    "buzzviolin": ("/health.json", "buzzviolin"),
    # BuzzAnimate's local API (its docs/ECOSYSTEM_BRIDGE_PLAN.md step E1).
    "buzzanimate": ("/health", "buzzanimate"),
}


@pytest.mark.parametrize("app_id", sorted(HEALTH_ROUTES))
def test_the_registry_knows_each_apps_real_health_route(app_id):
    path, ledger = HEALTH_ROUTES[app_id]
    app = apps_registry.get(app_id)
    assert app, f"{app_id} is missing from the shipped registry"
    assert app.get("health_path") == path, (
        f"{app_id} answers {path}, the registry looks at {app.get('health_path')!r}"
    )
    assert apps_registry.ledger_id(app) == ledger


def test_the_apps_that_answer_no_identity_route_declare_none():
    """`buzzcode` is the honest exception, and it has to stay honest.

    Its listener is a llama-server: it answers `/health` but the body is
    llama.cpp's own and will never carry `app: "buzzcode"`. Giving the record a
    `health_path` would send discovery down the identity path, which correctly
    refuses that body - so the record deliberately has none and is read from its
    ledger entry instead. Any *other* record without a health path is simply an
    app nobody can find.
    """
    portless = {a["id"] for a in apps_registry.all_apps()
                if apps_registry.preferred_port(a) and not a.get("health_path")}
    assert portless == {"buzzcode"}, (
        f"these records have a port to look on but no identity route: {sorted(portless)}"
    )


# ───────────────── the owner's setup script finds Dexter the same way ─────────────────


def _setup_guardian_source():
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(apps_registry.__file__))))
    path = os.path.join(root, "setup_guardian.ps1")
    if not os.path.exists(path):
        pytest.skip("setup_guardian.ps1 is not next door")
    return open(path, "r", encoding="utf-8-sig").read()


def test_the_setup_script_discovers_dexter_before_it_decides_anything():
    """`setup_guardian.ps1` must find Dexter by identity, not by process path.

    Two things were wrong at once, and together they meant the script could
    never see a running Dexter:

    * it matched the repo folder inside `$_.Path`, but Dexter runs on the
      *system* interpreter (`C:/Python314/pythonw.exe`) with no venv, so the
      executable path never carries the repo name;
    * the other half of the test read `$_.CommandLine`, which `Get-Process` does
      not expose in Windows PowerShell at all, so it was always `$null`.

    The visible symptom in the P4 run: with Dexter answering on 8099 (8098 was
    held by a squatter), `-Check` reported "would launch Dexter.vbs" - and a
    real run would have launched a *second* Dexter beside the first.
    """
    source = _setup_guardian_source()
    dexter_section = source.split("4. Dexter", 1)[1]

    assert "$_.CommandLine" not in dexter_section, (
        "Get-Process has no CommandLine in Windows PowerShell; that test is always null"
    )
    assert r'$_.Path -like "*\dexter\*"' not in dexter_section, (
        r"Dexter runs on the system interpreter; its path never contains \dexter\ "
    )
    assert "Discover-App 'dexter'" in dexter_section, (
        "the Dexter step must locate Dexter through the ledger + identity scan"
    )
    # And it must do so before branching on -Check, or check mode is blind again.
    assert (dexter_section.index("Discover-App 'dexter'")
            < dexter_section.index("if ($Check)")), (
        "discovery has to run in check mode too, not only on a real run"
    )
    assert "Step 'Dexter discovery'" in dexter_section, (
        "check mode has to report where Dexter is, not only what it would do"
    )
