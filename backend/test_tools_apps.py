"""
Tests for the app tools (roadmap v9, D3/D4).

The two behaviours worth protecting are the ones that cost real time when they
go wrong:

* **`open_app` asks Sentinel before it launches.** Starting BuzzEdit's ComfyUI
  next to a render is how a 16 GB card ends up paging, and a refusal has to be
  a refusal — not a launch with a warning attached.
* **`stop_app` asks the owner** when a batch app is mid-job, and does not ask
  when it is not. A tool that confirms every time is a tool people learn to say
  yes to without reading.

`open_app` never runs a real launcher here: every test replaces
`apps_registry.launch` with a fake, so nothing on the machine is started.

Run:  python -m pytest backend/test_tools_apps.py -q
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apps_registry
import model_steward
import tools_apps
from tool_registry import get_tool


HEAVY = {
    "id": "batchy", "label": "Batchy", "names": ["batchy"], "root": "Batchy",
    "launch": {"cmd": "run.bat"},
    "stop": {"strategy": "process_tree", "process": "python.exe", "path_contains": "\\Batchy\\"},
    "probes": [], "class": "batch", "vram_mib": 9000, "ram_mib": 4000,
    "exclusive_with": ["toy"], "logs": [], "batch_job": True,
}
LIGHT = {
    "id": "toy", "label": "Toy", "names": ["toy"], "root": "Toy",
    "launch": {"cmd": "start.bat"}, "stop": {"strategy": "port_owner", "port": 9911},
    "probes": [], "class": "interactive", "vram_mib": 0, "ram_mib": 128,
    "exclusive_with": [], "logs": [],
}


@pytest.fixture()
def fake_world(monkeypatch):
    """A registry of two toy apps, nothing running, nothing launchable for real."""
    state = {"running": set(), "launched": [], "stopped": [], "mid_job": None,
             "granted": True, "blockers": [], "ram_zone": "Green"}

    records = {"batchy": dict(HEAVY), "toy": dict(LIGHT)}
    monkeypatch.setattr(apps_registry, "all_apps", lambda: list(records.values()))
    monkeypatch.setattr(apps_registry, "get", lambda app_id: records.get(app_id))
    monkeypatch.setattr(apps_registry, "resolve",
                        lambda hint: records.get(str(hint or "").strip().lower()))
    monkeypatch.setattr(apps_registry, "probe", lambda app: {
        "running": app["id"] in state["running"], "evidence": "test", "pids": [], "detail": "",
    })
    monkeypatch.setattr(apps_registry, "root_of", lambda app: os.path.join("X:", app["root"]))
    monkeypatch.setattr(apps_registry, "mid_job", lambda app: state["mid_job"])
    monkeypatch.setattr(apps_registry, "conflicts", lambda app: [
        records[o] for o in app.get("exclusive_with", []) if o in state["running"]
    ])
    monkeypatch.setattr(apps_registry, "launch", lambda app: (
        state["launched"].append(app["id"]) or {"ok": True, "message": "", "pid": 1}
    ))
    monkeypatch.setattr(apps_registry, "wait_until_up", lambda app, seconds=30: (
        state["running"].add(app["id"]) or True
    ))
    monkeypatch.setattr(apps_registry, "stop", lambda app: (
        state["stopped"].append(app["id"]) or state["running"].discard(app["id"])
        or {"ok": True, "message": "", "how": "test"}
    ))

    def fake_query(purpose, vram, ram=None):
        return model_steward.Decision(
            granted=state["granted"], purpose=purpose, vram_mib=vram, ram_mib=ram or 0,
            reason="not enough VRAM", blockers=list(state["blockers"]),
            details={"free_mib": 1200, "blockers": state["blockers"]},
        )

    monkeypatch.setattr(model_steward, "query", fake_query)
    monkeypatch.setattr(model_steward, "snapshot", lambda: {
        "vram": {"usedMib": 4000, "totalMib": 16311, "freeMib": 12311, "zone": "Green",
                 "name": "RTX 5060 Ti"},
        "ram": {"zone": state["ram_zone"], "totalMib": 32000, "availMib": 12000},
        "disk": None, "holders": [], "frozen": [], "reservations": [],
        "models": {"loaded": [], "ours": []}, "lastShed": {},
        "sentinel": {"connected": True, "version": "2.0.0", "features": []},
        "trayAlive": True, "budgets": {},
    })
    return state


# ───────────────────────── status ─────────────────────────


def test_app_status_answers_for_a_stopped_app(fake_world):
    result = tools_apps.app_status("batchy")
    assert result.ok and "not running" in result.summary


def test_an_unknown_name_lists_what_dexter_does_know(fake_world):
    result = tools_apps.app_status("photoshop")
    assert result.ok is False
    assert "Batchy" in result.summary and "Toy" in result.summary


def test_the_overview_says_what_is_running_and_what_the_gpu_is_doing(fake_world):
    fake_world["running"].add("batchy")
    result = tools_apps.apps_overview()
    assert "1 running" in result.summary and "Batchy" in result.summary
    assert "GPU:" in result.summary
    assert len(result.data["apps"]) == 2


# ───────────────────────── D4: resource-aware launch ─────────────────────────


def test_a_heavy_app_is_not_started_when_sentinel_says_no(fake_world):
    fake_world["granted"] = False
    fake_world["blockers"] = [{"process": "VrDeep", "pid": 33228, "vram_mib": 10240,
                               "class": "batch"}]
    result = tools_apps.open_app("batchy")

    assert result.ok is False
    assert fake_world["launched"] == [], "a refusal must not launch"
    assert "VrDeep" in result.summary
    assert "8.8 GB" in result.summary, result.summary


def test_a_heavy_app_starts_when_there_is_room(fake_world):
    result = tools_apps.open_app("batchy")
    assert result.ok and fake_world["launched"] == ["batchy"]
    assert "Batchy is up" in result.summary


def test_a_light_app_does_not_bother_sentinel(fake_world, monkeypatch):
    monkeypatch.setattr(model_steward, "query",
                        lambda *a, **k: pytest.fail("an app with no VRAM appetite needs no query"))
    result = tools_apps.open_app("toy")
    assert result.ok and fake_world["launched"] == ["toy"]


def test_an_exclusive_app_that_is_running_blocks_the_launch(fake_world):
    """On this card, one of these at a time. Say which one has it."""
    fake_world["running"].add("toy")
    result = tools_apps.open_app("batchy")
    assert result.ok is False
    assert "Toy is running" in result.summary
    assert fake_world["launched"] == []


def test_ram_in_the_red_refuses_even_when_vram_is_free(fake_world):
    fake_world["ram_zone"] = "Red"
    result = tools_apps.open_app("batchy")
    assert result.ok is False
    assert "RAM is in the red" in result.summary
    assert fake_world["launched"] == []


def test_ram_in_the_yellow_warns_but_lets_it_through(fake_world):
    fake_world["ram_zone"] = "Yellow"
    result = tools_apps.open_app("batchy")
    assert result.ok and fake_world["launched"] == ["batchy"]
    assert "already tight" in result.summary


def test_an_app_that_is_already_running_is_not_started_twice(fake_world):
    fake_world["running"].add("toy")
    result = tools_apps.open_app("toy")
    assert result.ok and "already running" in result.summary
    assert fake_world["launched"] == []


def test_a_launch_that_never_comes_up_says_so_rather_than_claiming_success(fake_world, monkeypatch):
    monkeypatch.setattr(apps_registry, "wait_until_up", lambda app, seconds=30: False)
    result = tools_apps.open_app("toy")
    assert result.ok
    assert "hasn't answered its health check" in result.summary
    assert result.data["confirmed"] is False


def test_a_warning_on_the_record_is_passed_on(fake_world, monkeypatch):
    """MidnightBuzz binds 0.0.0.0; the owner should hear that as it starts."""
    monkeypatch.setitem(apps_registry.get("toy"), "warn", "Toy binds 0.0.0.0.")
    result = tools_apps.open_app("toy")
    assert "0.0.0.0" in result.summary


# ───────────────────────── v12: Dexter steps aside ─────────────────────────
#
# Owner, 2026-09-11: "buzzcode does not run every time, neither does buzzedit,
# how can sentinel handle this situation?" Most of the time the thing holding
# the last gigabytes was Dexter's own model, and Dexter read the refusal out
# instead of giving it back. It yields to what the owner asked for — and only
# ever its own models.


OUR_BLOCKER = {"label": "Ollama runner (Dexter)", "pid": 4711, "vram_mib": 5000,
               "class": "assistant"}


@pytest.fixture()
def room_maker(monkeypatch, fake_world):
    """Shedding recorded, never real; the second query is granted."""
    calls = {"shed": [], "released": []}

    def fake_shed(level="orange", reason="", want_mib=0, move_fast=True):
        calls["shed"].append((level, reason, move_fast))
        fake_world["granted"] = True       # the card is free now
        fake_world["blockers"] = []
        return {"level": level, "steps": ["unloaded qwen2.5:7b"], "freedMib": 5017}

    monkeypatch.setattr(model_steward, "shed", fake_shed)
    monkeypatch.setattr(model_steward, "release", lambda why="": calls["released"].append(why))
    monkeypatch.setattr(model_steward, "_resident_mib", lambda: 0)
    return calls


def test_dexter_gives_its_own_model_back_so_the_app_can_start(fake_world, room_maker):
    fake_world["granted"] = False
    fake_world["blockers"] = [OUR_BLOCKER]

    result = tools_apps.open_app("batchy")

    assert result.ok and fake_world["launched"] == ["batchy"]
    # move_fast=False: the owner asked for an app; loading 3 GB back into RAM in
    # the same breath is the opposite of making room (v13, P3.3).
    assert room_maker["shed"] == [("preempt", "making room for Batchy", False)]
    assert room_maker["released"] == ["making room"]
    assert result.summary.startswith("Gave back qwen2.5:7b (4.9 GB) so Batchy could start.")
    assert "Batchy is up" in result.summary


def test_someone_elses_render_is_sentinels_call_not_dexters(fake_world, room_maker):
    """VrDeep has the card: Dexter unloading its own 3B frees nothing that
    matters, and the app still cannot start. Say so, shed nothing."""
    fake_world["granted"] = False
    fake_world["blockers"] = [{"process": "VrDeep", "pid": 33228, "vram_mib": 10240,
                               "class": "batch"}]

    result = tools_apps.open_app("batchy")

    assert result.ok is False and fake_world["launched"] == []
    assert room_maker["shed"] == [], "Dexter must never shed for someone else's work"
    assert "Not enough room for Batchy" in result.summary and "VrDeep" in result.summary


def test_a_refusal_that_survives_the_shed_still_launches_nothing(fake_world, monkeypatch):
    fake_world["granted"] = False
    fake_world["blockers"] = [OUR_BLOCKER]
    shed = []
    monkeypatch.setattr(model_steward, "shed", lambda level="orange", reason="", want_mib=0, move_fast=True: (
        shed.append(level) or {"level": level, "steps": [], "freedMib": 0}
    ))
    monkeypatch.setattr(model_steward, "release", lambda why="": None)
    monkeypatch.setattr(model_steward, "_resident_mib", lambda: 0)

    result = tools_apps.open_app("batchy")

    assert shed == ["preempt"], "it tried"
    assert result.ok is False and fake_world["launched"] == []
    assert "Not enough room for Batchy" in result.summary


# ───────────────────────── stopping, and asking first ─────────────────────────


def test_stopping_a_busy_batch_app_asks_first(fake_world):
    fake_world["running"].add("batchy")
    fake_world["mid_job"] = "3 job(s) in its queue"
    spec = get_tool("stop_app")

    assert spec.needs_confirmation({"app": "batchy"}) is True
    question = spec.confirmation_prompt({"app": "batchy"})
    assert "3 job(s)" in question and "Batchy" in question
    assert fake_world["stopped"] == [], "asking must not also do it"


def test_stopping_an_idle_app_does_not_ask(fake_world):
    """A tool that confirms every time is a tool people stop reading."""
    fake_world["running"].add("toy")
    assert get_tool("stop_app").needs_confirmation({"app": "toy"}) is False
    result = tools_apps.stop_app("toy")
    assert result.ok and fake_world["stopped"] == ["toy"]


def test_stopping_something_that_is_not_running_is_not_an_error(fake_world):
    result = tools_apps.stop_app("toy")
    assert result.ok and "not running" in result.summary
    assert fake_world["stopped"] == []


def test_an_app_with_no_stop_route_says_so(fake_world, monkeypatch):
    monkeypatch.setitem(apps_registry.get("toy"), "stop",
                        {"strategy": "none", "why": "it has no shutdown route"})
    fake_world["running"].add("toy")
    result = tools_apps.stop_app("toy")
    assert result.ok is False and "no shutdown route" in result.summary


def test_restart_stops_then_starts(fake_world):
    fake_world["running"].add("toy")
    result = tools_apps.restart_app("toy")
    assert fake_world["stopped"] == ["toy"] and fake_world["launched"] == ["toy"]
    assert result.ok


# ───────────────────────── cleanup ─────────────────────────


class FakeSentinelClient:
    def __init__(self, features=("cleanup", "disk"), queued=True):
        self._features = list(features)
        self.asked = []
        self._queued = queued

    def supports(self, feature):
        return feature in self._features

    def disk_status(self):
        return {"drives": [{"letter": "C:", "total": 500 * 1024**3, "free": 20 * 1024**3,
                            "zone": "Yellow"}]}

    def request_cleanup(self, mode="safe", drive=None):
        self.asked.append((mode, drive))
        return {"queued": True} if self._queued else None


def test_clean_up_space_forwards_to_sentinel(monkeypatch):
    client = FakeSentinelClient()
    monkeypatch.setattr(tools_apps.sentinel_client, "client", lambda: client)
    monkeypatch.setenv("DEXTER_AUTO_CLEAN", "safe")
    result = tools_apps.request_cleanup("safe")
    assert result.ok and client.asked == [("safe", None)]
    assert "20 GB free" in result.summary


def test_an_old_sentinel_admits_it_cannot_clean_rather_than_claiming_it_did(monkeypatch):
    """The build installed today has no cleanup command. Reporting "done" over a
    no-op is the one thing worse than not being able to do it."""
    client = FakeSentinelClient(features=())
    monkeypatch.setattr(tools_apps.sentinel_client, "client", lambda: client)
    monkeypatch.setenv("DEXTER_AUTO_CLEAN", "safe")
    result = tools_apps.request_cleanup("safe")
    assert result.ok is False
    assert "haven't freed anything" in result.summary
    assert client.asked == []


def test_cleanup_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("DEXTER_AUTO_CLEAN", "off")
    monkeypatch.setattr(tools_apps.sentinel_client, "client",
                        lambda: pytest.fail("must not reach Sentinel when switched off"))
    result = tools_apps.request_cleanup()
    assert result.ok is False and "switched off" in result.summary


# ───────────────────────── the thin readers ─────────────────────────


def test_vrdeep_queue_reads_the_apps_own_file(monkeypatch):
    monkeypatch.setattr(apps_registry, "read_queue", lambda app_id="vrdeep": {
        "found": True, "id": "vrdeep", "pending": 3,
        "queue": [{"status": "pending"}], "queueAge": "updated 2 min ago",
        "session": {"current": "clip_04.mp4"},
    })
    monkeypatch.setattr(apps_registry, "get", lambda app_id: {"id": "vrdeep"})
    monkeypatch.setattr(apps_registry, "probe", lambda app: {"running": True})
    result = tools_apps.vrdeep_queue()
    assert result.ok
    assert "rendering" in result.summary and "3 job(s)" in result.summary
    assert "clip_04.mp4" in result.summary


def test_a_reader_for_an_app_that_is_down_says_how_to_start_it(monkeypatch):
    monkeypatch.setattr(apps_registry, "get", lambda app_id: {"id": app_id})
    monkeypatch.setattr(apps_registry, "probe", lambda app: {"running": False})
    result = tools_apps.finance_status()
    assert result.ok is False
    assert "open buzzfin" in result.summary


# ───────────────────────── registration ─────────────────────────


def test_every_app_tool_is_registered_with_phrasings():
    for name in ("app_status", "apps_overview", "open_app", "stop_app", "restart_app",
                 "app_logs", "request_cleanup", "vrdeep_queue", "buzzedit_status",
                 "finance_status", "coffee_brief", "mastgaane_report"):
        spec = get_tool(name)
        assert spec is not None, f"{name} is not registered"
        assert spec.category == "apps", name
        assert len(spec.examples) >= 4, f"{name} needs phrasings for the semantic tier"


# ───────── the port in the sentence is one we identified (P1) ─────────


def test_a_running_app_is_named_with_the_port_it_actually_holds():
    from tools_apps import _row_line

    line = _row_line({"label": "BuzzEdit", "running": True, "livePort": 8100, "port": 8100})
    assert "BuzzEdit: running on 8100" in line


def test_an_app_whose_port_we_could_not_identify_is_not_given_one():
    """Live bug: BuzzEdit's Electron was up and Dexter held 8099, and the
    summary read "BuzzEdit: running on 8099". `port` still carries the preferred
    number for the panel; the sentence must not."""
    from tools_apps import _row_line

    line = _row_line({"label": "BuzzEdit", "running": True, "livePort": None, "port": 8099})
    assert line == "BuzzEdit: running"


# ───── the readers follow the app to the port it took (P4 integration) ─────


def _capture_reader_url(monkeypatch, live_port, call):
    """Run one reader and hand back the URL it asked for."""
    asked = {}

    class _Res:
        headers = {"content-type": "application/json"}

        @staticmethod
        def json():
            return {"ok": True}

    class _Requests:
        @staticmethod
        def get(url, timeout=None):
            asked["url"] = url
            return _Res()

    monkeypatch.setattr(apps_registry, "probe", lambda app: {"running": True})
    monkeypatch.setattr(apps_registry, "live_port", lambda app: live_port)
    monkeypatch.setitem(sys.modules, "requests", _Requests)
    result = call()
    return asked.get("url"), result


def test_the_buzzedit_reader_asks_the_port_buzzedit_is_on_not_the_one_it_wanted(monkeypatch):
    """The live case that broke it: the Studio holds 8099, so BuzzEdit stepped
    to 8100 - and the reader was interrogating the Studio."""
    url, result = _capture_reader_url(monkeypatch, 8100, tools_apps.buzzedit_status)
    assert url == "http://127.0.0.1:8100/api/health", url
    assert result.ok


def test_the_finance_reader_uses_the_route_that_is_outside_buzzfins_lock(monkeypatch):
    """A bare `/health` is 404 behind BuzzFin's security gate."""
    url, _ = _capture_reader_url(monkeypatch, 8757, tools_apps.finance_status)
    assert url == "http://127.0.0.1:8757/api/v1/health", url


def test_the_coffee_reader_uses_the_route_the_ops_app_actually_serves(monkeypatch):
    """`/ops/api/dashboard` is a 404: `run.py` mounts no `root_path`."""
    url, _ = _capture_reader_url(monkeypatch, 8011, tools_apps.coffee_brief)
    assert url == "http://127.0.0.1:8011/api/dashboard", url


def test_a_reader_that_cannot_place_the_app_refuses_rather_than_guessing(monkeypatch):
    """No port is better than the wrong port: a guess sends the reader into
    whatever else happens to be listening on the number we wanted."""
    monkeypatch.setattr(apps_registry, "probe", lambda app: {"running": True})
    monkeypatch.setattr(apps_registry, "live_port", lambda app: None)
    result = tools_apps.coffee_brief()
    assert result.ok is False
    assert "did not identify itself" in result.summary


def test_no_reader_carries_a_literal_loopback_port():
    """The registry has been free of these since P1 (`test_the_shipped_registry_
    carries_no_literal_loopback_url`); the tools that read those same apps kept
    three of them, which is the same hardcoded port wearing a different hat."""
    source = open(tools_apps.__file__, "r", encoding="utf-8").read()
    offenders = re.findall(r"127\.0\.0\.1:\d+", source)
    # The one in `_reader` is built from a discovered port, not written down.
    assert not offenders, f"literal loopback ports left in tools_apps.py: {offenders}"
