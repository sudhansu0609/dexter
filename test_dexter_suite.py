"""
Dexter end-to-end verification suite.

Run the backend first:  python backend/main.py

These are live HTTP checks against a running server. Pure routing logic is
covered much more thoroughly by backend/test_router.py, which needs no server.

Note on assertions: intent used to be asserted via the `action` string
("launch_app", "browse_url", ...). Those were branch labels from the old keyword
chain. `action` now reports which tier answered (fast_path / semantic / agent),
and the tool that actually ran is in `toolCalls` - so that is what these assert.
It is a stricter check than before, since it verifies execution rather than a
routing label.
"""

import json
import os
import sys
import time

import requests

# Scenario E imports a couple of backend modules directly (the refusal wording
# is a pure function and worth asserting without a round trip).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))


def _dexter_base_url() -> str:
    """Where Dexter actually is, not where it wished to be.

    Dexter takes the first free port from `DEXTER_PORT` (8098) upwards and
    publishes it (GUARDIAN_PLAN.md section 11). Assuming 8098 here is how this
    suite would "fail" against a perfectly healthy Dexter that had to step
    forward because something else got to 8098 first.
    """
    try:
        import buzzcaf_ports

        found = buzzcaf_ports.discover(
            "dexter", int(os.getenv("DEXTER_PORT") or 8098), "/health", ttl=0,
        )
        if found:
            return found
    except Exception as exc:  # the helper is stdlib-only; this should not happen
        print(f"[suite] port discovery unavailable ({exc}); falling back")
    return f"http://127.0.0.1:{os.getenv('DEXTER_PORT') or 8098}"


BASE_URL = _dexter_base_url()

# Every /api/ endpoint requires the per-run session token; fetch it once from
# the bootstrap endpoint and carry it on a shared session.
_session = requests.Session()


def _authenticate() -> None:
    try:
        tok = _session.get(f"{BASE_URL}/api/dexter/auth/token", timeout=5).json().get("token")
        if tok:
            _session.headers["X-Dexter-Token"] = tok
    except Exception:
        pass

# Tier 2 falls through to a local model, which can take a minute on consumer
# hardware. Short timeouts here were reporting slowness as failure.
FAST_TIMEOUT = 15
LLM_TIMEOUT = 150

_passed = 0
_failed = 0


def check(name, fn):
    global _passed, _failed
    try:
        detail = fn()
        _passed += 1
        print(f"[OK]   {name}: {detail}")
    except Exception as exc:
        _failed += 1
        print(f"[FAIL] {name}: {exc}")


def chat(message, timeout=FAST_TIMEOUT):
    res = _session.post(
        f"{BASE_URL}/api/dexter/chat", json={"message": message}, timeout=timeout
    )
    res.raise_for_status()
    return res.json()


def tools_used(data):
    return [c["name"] for c in data.get("toolCalls", [])]


def assert_tool(message, expected, timeout=FAST_TIMEOUT):
    data = chat(message, timeout)
    used = tools_used(data)
    assert expected in used, f"{message!r} ran {used}, expected {expected} (tier {data.get('tier')})"
    return f"{message!r} -> {expected} (tier {data.get('tier')}, {len(data['reply'])} char reply)"


def run_tests():
    print("=" * 60)
    print("Dexter Super Assistant - Verification Suite")
    print("=" * 60)
    _authenticate()

    # ── core endpoints ──
    def status():
        data = _session.get(f"{BASE_URL}/api/dexter/status", timeout=FAST_TIMEOUT).json()
        assert data["status"] == "online"
        assert data["toolCount"] > 0, "no tools registered"
        return (
            f"v{data['version']}, {data['toolCount']} tools, "
            f"{data['appsIndexed']} apps indexed, intent={data['intentMode']}, "
            f"scheduler={'up' if data['scheduler']['running'] else 'DOWN'}"
        )

    check("Status endpoint", status)

    def identity():
        """Rule 4: whoever is on this port has to say who it is, so a scan that
        lands on somebody else's server can tell and keep looking."""
        body = _session.get(f"{BASE_URL}/health", timeout=FAST_TIMEOUT).json()
        assert body["app"] == "dexter", f"the listener at {BASE_URL} says {body.get('app')!r}"
        assert body["status"] == "ok"
        assert int(body["port"]) == int(BASE_URL.rsplit(":", 1)[1])
        assert int(body["pid"]) > 0 and body["version"]
        return f"app={body['app']} port={body['port']} pid={body['pid']} v{body['version']}"

    check("Ports: /health identifies this server", identity)

    def published():
        """And it is in the shared ledger, where every other app looks."""
        import buzzcaf_ports

        entry = buzzcaf_ports.entry("dexter")
        assert entry, f"no dexter entry in {buzzcaf_ports.ledger_path()}"
        assert int(entry["port"]) == int(BASE_URL.rsplit(":", 1)[1]), (
            f"the ledger says {entry['port']}, the suite is talking to {BASE_URL}"
        )
        assert buzzcaf_ports._pid_alive(entry["pid"]), "the published pid is dead"
        return f"{buzzcaf_ports.ledger_path()} -> port {entry['port']}, pid {entry['pid']}"

    check("Ports: Dexter is published in the ledger", published)

    def port_reported():
        data = _session.get(f"{BASE_URL}/api/dexter/status", timeout=FAST_TIMEOUT).json()
        res = _session.get(f"{BASE_URL}/api/dexter/resources", timeout=FAST_TIMEOUT).json()
        expected = int(BASE_URL.rsplit(":", 1)[1])
        assert int(data["port"]) == expected, f"/status says {data.get('port')}"
        assert int(res["port"]) == expected, f"/resources says {res.get('port')}"
        return f"/status and /resources both report {expected}"

    check("Ports: /status and /resources report the live port", port_reported)

    check("Tool registry", lambda: (
        f"{len(_session.get(f'{BASE_URL}/api/dexter/tools', timeout=FAST_TIMEOUT).json())} tools exposed"
    ))

    # ── planner ──
    check("Planner read", lambda: (
        f"{len(_session.get(f'{BASE_URL}/api/dexter/planner', timeout=FAST_TIMEOUT).json().get('tasks', []))} tasks"
    ))

    def add_task():
        res = _session.post(
            f"{BASE_URL}/api/dexter/planner/add",
            json={"title": "Verification run", "time": "09:00 PM"}, timeout=FAST_TIMEOUT,
        )
        body = res.json()
        assert body["title"] == "Verification run"
        # A commitment lands on a day with its time, and a future time also
        # arms a reminder - that is what makes it fire.
        assert body.get("dueDate"), "task has no dueDate - it is not on the planner"
        assert body.get("time"), "task lost its time"
        return f"added for {body['dueDate']} {body['time']}, reminder={'armed' if body.get('reminder') else 'none (time already passed)'}"

    check("Planner add (ISO timestamp)", add_task)

    def toggle_task():
        planner = _session.get(f"{BASE_URL}/api/dexter/planner", timeout=FAST_TIMEOUT).json()
        tasks = planner.get("tasks", [])
        if not tasks:
            return "skipped - no tasks"
        # Only a *commitment* can be ticked off. Adding one with a future time
        # also arms a reminder, and the reminder row sorts last — so `tasks[-1]`
        # was asking the planner to complete a reminder and being told, quite
        # correctly, "only commitments can be ticked off".
        # ...and only one this suite created. `commitments[-1]` was two bugs at
        # once: the row it picked was usually a commitment an earlier run had
        # already ticked ("already done", a failure with no cause), and on a
        # planner without one it would have ticked off something of the owner's.
        mine = [t for t in tasks
                if t.get("kind") == "commitment" and t.get("title") == "Verification run"]
        if not mine:
            return "skipped - no commitment of ours to tick"
        for task in reversed(mine):
            body = _session.post(
                f"{BASE_URL}/api/dexter/planner/toggle/{task['id']}", timeout=FAST_TIMEOUT,
            ).json()
            if body.get("success"):
                return f"toggled {task['id']}"
            if body.get("reason") != "already done":
                raise AssertionError(f"{task['id']}: {body.get('reason') or body}")
        return f"skipped - all {len(mine)} of our commitments are already ticked"

    check("Planner toggle", toggle_task)

    # ── intent routing (the old tests 5-8, rewritten against toolCalls) ──
    check("Intent: agenda not app launcher",
          lambda: assert_tool("what is on my agenda", "get_agenda"))
    check("Intent: media", lambda: assert_tool("play lofi beats", "play_on_youtube"))
    # v6: sites open in Dexter's own controlled browser (browser_open), not the shell.
    check("Intent: browse", lambda: assert_tool("go to google.com", "browser_open"))
    check("Intent: app launch", lambda: assert_tool("open notepad", "launch_app"))
    check("Intent: time", lambda: assert_tool("what time is it", "get_current_time"))
    check("Intent: volume", lambda: assert_tool("what is the volume", "set_volume"))

    def general_question_reaches_llm():
        data = chat("What is machine learning?", timeout=LLM_TIMEOUT)
        assert not tools_used(data), f"a general question wrongly ran tools: {tools_used(data)}"
        assert len(data["reply"]) > 20, "reply suspiciously short"
        return f"tier {data.get('tier')}, {len(data['reply'])} char answer"

    check("General question reaches the model, runs no tools", general_question_reaches_llm)

    # ── reminders ──
    def reminder_lifecycle():
        data = chat("remind me to run the verification in 3 hours")
        assert "create_reminder" in tools_used(data), f"ran {tools_used(data)}"
        pending = _session.get(f"{BASE_URL}/api/dexter/reminders", timeout=FAST_TIMEOUT).json()
        match = next((r for r in pending if "verification" in r["text"].lower()), None)
        assert match, "reminder was not persisted"
        assert match["at"], "reminder has no fire time"
        _session.delete(f"{BASE_URL}/api/dexter/reminders/{match['id']}", timeout=FAST_TIMEOUT)
        return f"created and cancelled, fire at {match['at']}"

    check("Reminder create / list / cancel", reminder_lifecycle)

    # ── memory ──
    def memory_roundtrip():
        chat("remember that my test project is dexter-rebuild")
        data = chat("what do you remember about my test project")
        assert "dexter-rebuild" in data["reply"], f"recall failed: {data['reply']}"
        _session.delete(f"{BASE_URL}/api/dexter/memory/my test project", timeout=FAST_TIMEOUT)
        return "stored and recalled"

    check("Memory store / recall", memory_roundtrip)

    def conversation_history():
        stats = _session.get(f"{BASE_URL}/api/dexter/memory", timeout=FAST_TIMEOUT).json()
        assert stats["stats"]["turns"] > 0, "no conversation turns recorded"
        return f"{stats['stats']['turns']} turns stored"

    check("Conversation history persists", conversation_history)

    # ── streaming: tokens arrive before the reply (speech is synthesised in the
    # voice pipeline from these same token events, not sent over this stream) ──
    def streaming():
        started = time.time()
        first_token = None
        tokens = 0
        reply = ""
        done = False
        res = _session.post(
            f"{BASE_URL}/api/dexter/chat/stream",
            json={"message": "in one sentence, why is the sky blue?"},
            stream=True, timeout=LLM_TIMEOUT,
        )
        for line in res.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            evt = json.loads(line[5:])
            if evt["type"] == "token":
                tokens += 1
                if first_token is None:
                    first_token = int((time.time() - started) * 1000)
            elif evt["type"] == "reply":
                reply = evt["payload"]["reply"]
            elif evt["type"] == "done":
                done = True
        total = int((time.time() - started) * 1000)
        assert reply, "no reply event received"
        assert done, "stream ended without a done event"
        assert tokens > 0, "no token events - the reply was not streamed"
        assert "ESCALATE" not in reply.upper(), "the fast model's hand-off sentinel leaked into the reply"
        return f"first token {first_token}ms, {tokens} token event(s), reply after {total}ms"

    check("Streaming chat", streaming)

    # ── tiered models: a small model answers simple turns, the big one gets tools ──
    def tiers():
        status = _session.get(f"{BASE_URL}/api/dexter/status", timeout=FAST_TIMEOUT).json().get("tiers") or {}
        if not status.get("enabled"):
            return "skipped - DEXTER_FAST_MODEL not set"
        assert status["fast"]["provider"], "fast model is configured but no online server lists it"
        started = time.time()
        data = chat("what's the capital of australia?", timeout=LLM_TIMEOUT)
        ms = int((time.time() - started) * 1000)
        assert not tools_used(data), f"a trivia question ran tools: {tools_used(data)}"
        assert data.get("action") == "fast_chat", f"answered by {data.get('action')} instead of the fast model"
        assert "canberra" in data["reply"].lower(), f"wrong answer: {data['reply']}"
        loaded = ", ".join(f"{m['name']}{' (GPU)' if m.get('onGpu') else ' (CPU)'}" for m in status.get("loaded", []))
        return (
            f"{status['fast']['model']} on {status['fast']['provider']} answered in {ms}ms; "
            f"smart={status['smart']['model']} on {status['smart']['provider']}; loaded: {loaded or 'nothing'}"
        )

    check("Tiered models: fast reply", tiers)

    # ── Scenario E: the guardian and the apps (roadmap v9, D8) ──
    #
    # Skipped entirely when no Sentinel pipe answers: on a machine without the
    # service these would fail for the wrong reason, and "Dexter works without
    # a guardian" is the behaviour we actually ship.

    def sentinel_present():
        import os

        pipe = os.environ.get("SENTINEL_PIPE", r"\\.\pipe\Sentinel")
        try:
            with open(pipe, "r+b", buffering=0):
                return True
        except Exception:
            return False

    if not sentinel_present():
        print("[SKIP] Sentinel scenario: no pipe is answering (set SENTINEL_PIPE for the dev service)")
    else:
        def resources():
            data = _session.get(f"{BASE_URL}/api/dexter/resources", timeout=FAST_TIMEOUT).json()
            sentinel = data["sentinel"]
            assert sentinel["connected"], "the pipe answered but /resources says disconnected"
            vram = data.get("vram")
            assert vram and vram["totalMib"] > 0, f"no VRAM figures: {vram}"
            version = sentinel.get("version") or "v1 (no Hello)"
            return (
                f"Sentinel {version}, features {sentinel.get('features') or 'none (old build)'}; "
                f"{vram['freeMib'] / 1024:.1f} GB free of {vram['totalMib'] / 1024:.1f}, "
                f"zone {vram.get('zone')}; tray_alive={data.get('trayAlive')}"
            )

        check("Sentinel: /api/dexter/resources", resources)

        def gpu_phrase():
            data = chat("gpu status")
            assert tools_used(data) == ["gpu_status"], f"ran {tools_used(data)}"
            assert data["details"].get("connected") is True, data["reply"]
            return data["reply"][:150]

        check("Sentinel: 'gpu status' is deterministic", gpu_phrase)

        def frozen_list():
            data = _session.get(f"{BASE_URL}/api/dexter/resources", timeout=FAST_TIMEOUT).json()
            frozen = data.get("frozen") or []
            return f"{len(frozen)} frozen: {[f.get('name') for f in frozen] or 'nothing'}"

        check("Sentinel: frozen list", frozen_list)

        def registration():
            """Dexter must appear in Sentinel's own registration table."""
            status = _session.get(f"{BASE_URL}/api/dexter/status", timeout=FAST_TIMEOUT).json()
            sentinel = status.get("sentinel") or {}
            assert sentinel.get("connected"), "status does not see Sentinel"
            # Sentinel's `GpuStatus.registrations` rows are tuples on the wire
            # (`["dexter", 43728, "assistant", 5120]`), not objects — `r.get`
            # raised `'list' object has no attribute 'get'` the first time this
            # ran against the real v2 service. Read whichever shape arrives.
            rows = sentinel.get("registrations") or []
            names = [
                str(r.get("client", r)).lower() if isinstance(r, dict) else str(r).lower()
                for r in rows
            ]
            assert any("dexter" in n for n in names), f"Dexter is not registered: {names}"
            return f"registered among {names}"

        check("Sentinel: Dexter is registered as the assistant", registration)

        def refusal_text():
            """A refusal has to name the holder and the move, without loading."""
            import model_steward

            decision = model_steward.Decision(
                granted=False, purpose="smart-model", vram_mib=6000,
                reason="not enough VRAM",
                blockers=[{"process": "VrDeep", "pid": 0, "vram_mib": 10240, "class": "batch"}],
                details={"free_mib": 1200},
            )
            message = decision.message()
            assert "VrDeep" in message and "pause batch jobs" in message.lower(), message
            return message[:150]

        check("Sentinel: a refusal names the holder and the allowed move", refusal_text)

    # ── The apps registry, live (read-only: nothing is started or stopped) ──

    def apps_endpoint():
        data = _session.get(f"{BASE_URL}/api/dexter/apps", timeout=FAST_TIMEOUT).json()
        rows = data["apps"]
        assert len(rows) >= 17, f"only {len(rows)} apps in the registry"
        live = [r["label"] for r in rows if r["running"]]
        return f"{len(rows)} apps, running: {live or 'none'}"

    check("Apps: /api/dexter/apps", apps_endpoint)

    def whats_running():
        data = chat("what's running")
        assert tools_used(data) == ["apps_overview"], f"ran {tools_used(data)}"
        return data["reply"][:180]

    check("Apps: \"what's running\" is deterministic", whats_running)

    def is_it_running():
        data = chat("is vrdeep running")
        assert tools_used(data) == ["app_status"], f"ran {tools_used(data)}"
        return data["reply"][:120]

    check("Apps: 'is vrdeep running'", is_it_running)

    def queue_reader():
        data = chat("what's in the vrdeep queue")
        assert tools_used(data) == ["vrdeep_queue"], f"ran {tools_used(data)}"
        return data["reply"][:150]

    check("Apps: VrDeep queue reader", queue_reader)

    # ── remaining endpoints ──
    check("Skills", lambda: (
        f"{len(_session.get(f'{BASE_URL}/api/dexter/skills', timeout=FAST_TIMEOUT).json())} skills"
    ))
    check("Direct browse endpoint", lambda: _session.post(
        f"{BASE_URL}/api/dexter/browser/navigate", json={"url": "example.com"},
        timeout=FAST_TIMEOUT).json()["url"])
    check("Filesystem search", lambda: (
        f"{_session.post(f'{BASE_URL}/api/dexter/filesystem/search', json={'query': 'dexter'}, timeout=FAST_TIMEOUT).json()['count']} results"
    ))
    check("SpilledCoffee curate", lambda: (
        f"{len(_session.post(f'{BASE_URL}/api/dexter/spilledcoffee/curate', json={'niche': 'AI', 'count': 2}, timeout=LLM_TIMEOUT).json())} ideas"
    ))

    print("=" * 60)
    print(f"{_passed} passed, {_failed} failed")
    print("=" * 60)
    return _failed


if __name__ == "__main__":
    try:
        _session.get(f"{BASE_URL}/api/dexter/status", timeout=FAST_TIMEOUT)
    except Exception:
        print(f"Dexter is not running at {BASE_URL}. Start it with: python backend/main.py")
        print("(that address came from the port ledger / a scan from DEXTER_PORT upwards)")
        sys.exit(2)
    sys.exit(1 if run_tests() else 0)
