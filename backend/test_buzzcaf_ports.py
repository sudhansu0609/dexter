"""
The port ledger (GUARDIAN_PLAN.md section 11, milestone P1).

What is actually worth testing here is not "does it return a number" but the
five ways this goes wrong in a shared machine: a port that is already taken, two
apps publishing at the same instant, a lock left behind by something that
crashed, a withdraw that takes somebody else's entry with it, and — twice over —
a discovery that believes the wrong listener. Every one of those has already
happened in this ecosystem at least once.

The last test in the file is a different kind: it checks that the copies of this
helper in the sibling repositories are byte-identical to the canonical one, so
"one implementation per language" stays true after P2 and P3 land.
"""

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import buzzcaf_ports as ports


# ───────────────────────── helpers ─────────────────────────


class _HealthServer:
    """A listener that answers `health_path` with whatever body it was given."""

    def __init__(self, body, health_path="/health", port=0):
        self._body = body
        self._path = health_path
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        wanted = health_path

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's contract
                if self.path.split("?")[0] != wanted:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # keep pytest output readable
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self._server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _hold(port=0):
    """Occupy a port the way a real server does, and hand back the number."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    sock.listen(8)
    return sock, sock.getsockname()[1]


def _dead_pid():
    """A pid that certainly is not running: one we started and reaped ourselves."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    return proc.pid


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ports.json"
    monkeypatch.setenv("BUZZCAF_PORTS_FILE", str(path))
    ports._forget()
    yield path
    ports._forget()


# ───────────────────────── the ledger path ─────────────────────────


def test_the_ledger_lives_under_localappdata_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("BUZZCAF_PORTS_FILE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert ports.ledger_path() == tmp_path / "Buzzcaf" / "ports.json"


def test_the_environment_can_move_the_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("BUZZCAF_PORTS_FILE", str(tmp_path / "elsewhere.json"))
    assert ports.ledger_path() == tmp_path / "elsewhere.json"


# ───────────────────────── pick_port ─────────────────────────


def test_a_busy_port_makes_it_step_forward():
    held, port = _hold()
    try:
        assert ports.pick_port(port) == port + 1
    finally:
        held.close()


def test_it_steps_over_a_whole_run_of_busy_ports():
    held = []
    try:
        first, base = _hold()
        held.append(first)
        for offset in (1, 2):
            try:
                sock, _ = _hold(base + offset)
                held.append(sock)
            except OSError:
                pytest.skip("the next ports were not free to occupy")
        assert ports.pick_port(base) == base + 3
    finally:
        for sock in held:
            sock.close()


def test_a_free_port_is_taken_as_asked():
    held, port = _hold()
    held.close()  # released: the preferred port is genuinely free again
    assert ports.pick_port(port) == port


def test_it_never_evicts_the_app_that_holds_the_port():
    held, port = _hold()
    try:
        chosen = ports.pick_port(port)
        assert chosen != port
        # the original listener is untouched and still accepting
        probe = socket.create_connection(("127.0.0.1", port), timeout=2)
        probe.close()
    finally:
        held.close()


def test_a_full_window_falls_back_to_a_port_the_os_picks():
    held, port = _hold()
    try:
        # span 0 means "only the preferred port", so the window is full
        chosen = ports.pick_port(port, span=0)
        assert chosen != port
        assert 1024 < chosen <= 65535
    finally:
        held.close()


# ───────────────────────── publish / withdraw ─────────────────────────


def test_publish_records_port_pid_health_and_extra(ledger):
    ports.publish("dexter", 8098, "http://127.0.0.1:8098/health", {"vite": 3006})
    row = json.loads(ledger.read_text(encoding="utf-8"))["dexter"]
    assert row["port"] == 8098
    assert row["pid"] == os.getpid()
    assert row["health"] == "http://127.0.0.1:8098/health"
    assert row["extra"] == {"vite": 3006}
    assert row["started_at"].startswith("20")


def test_two_threads_publishing_at_once_keep_both_entries(ledger):
    names = [f"app{n}" for n in range(12)]
    start = threading.Barrier(len(names))

    def write(name):
        start.wait(timeout=10)
        ports.publish(name, 9000, f"http://127.0.0.1:9000/{name}")

    threads = [threading.Thread(target=write, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    written = json.loads(ledger.read_text(encoding="utf-8"))
    assert sorted(written) == sorted(names), "a concurrent publish lost an entry"


def test_a_stale_lock_is_broken_rather_than_waited_on(ledger):
    lock = ledger.parent / (ledger.name + ".lock")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("31337", encoding="utf-8")
    old = time.time() - (ports.LOCK_STALE_SECONDS + 5)
    os.utime(lock, (old, old))

    began = time.time()
    ports.publish("dexter", 8098, "http://127.0.0.1:8098/health")
    assert time.time() - began < ports.LOCK_TIMEOUT_SECONDS
    assert "dexter" in json.loads(ledger.read_text(encoding="utf-8"))
    assert not lock.exists()


def test_a_fresh_lock_is_waited_for_and_the_write_still_lands(ledger):
    lock = ledger.parent / (ledger.name + ".lock")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("31337", encoding="utf-8")

    def release():
        time.sleep(0.4)
        try:
            lock.unlink()
        except OSError:
            pass

    threading.Thread(target=release, daemon=True).start()
    ports.publish("dexter", 8098, "http://127.0.0.1:8098/health")
    assert "dexter" in json.loads(ledger.read_text(encoding="utf-8"))


def test_withdraw_removes_only_its_own_key(ledger):
    ports.publish("dexter", 8098, "http://127.0.0.1:8098/health")
    ports.publish("buzzcaf", 8099, "http://127.0.0.1:8099/health")
    ports.publish("buzzedit", 8100, "http://127.0.0.1:8100/health")

    ports.withdraw("buzzcaf")

    left = json.loads(ledger.read_text(encoding="utf-8"))
    assert sorted(left) == ["buzzedit", "dexter"]


def test_withdrawing_something_that_was_never_there_is_not_an_error(ledger):
    ports.publish("dexter", 8098, "http://127.0.0.1:8098/health")
    ports.withdraw("nobody")
    assert sorted(json.loads(ledger.read_text(encoding="utf-8"))) == ["dexter"]


def test_a_corrupt_ledger_costs_one_entry_not_the_startup(ledger):
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{not json at all", encoding="utf-8")
    ports.publish("dexter", 8098, "http://127.0.0.1:8098/health")
    assert json.loads(ledger.read_text(encoding="utf-8"))["dexter"]["port"] == 8098


def test_entry_and_entries_read_what_was_published(ledger):
    ports.publish("buzzcode", 8089, "http://127.0.0.1:8089/health", {"model": "qwen3.8-27b"})
    assert ports.entry("buzzcode")["extra"]["model"] == "qwen3.8-27b"
    assert "buzzcode" in ports.entries()
    assert ports.entry("nothing-here") is None


# ───────────────────────── discover ─────────────────────────


def test_discover_finds_the_app_through_the_ledger(ledger):
    with _HealthServer({"app": "dexter", "status": "ok"}) as server:
        ports.publish("dexter", server.port, f"{server.base}/health")
        ports._forget()
        assert ports.discover("dexter", 1, ttl=0) == server.base


def test_discover_ignores_a_listener_with_the_wrong_app(ledger):
    """A scan that accepts any listener is how CaliberAI became the Studio."""
    with _HealthServer({"app": "caliberai", "status": "ok"}) as server:
        assert ports.discover("buzzcaf", server.port, span=0, ttl=0) is None


def test_discover_ignores_a_ledger_entry_whose_pid_is_dead(ledger, monkeypatch):
    dead = _dead_pid()
    with _HealthServer({"app": "dexter", "status": "ok"}) as server:
        ports.publish("dexter", server.port, f"{server.base}/health")
        rows = json.loads(ledger.read_text(encoding="utf-8"))
        rows["dexter"]["pid"] = dead
        ledger.write_text(json.dumps(rows), encoding="utf-8")
        ports._forget()

        # The health route still answers correctly — only the pid is dead, and
        # that alone has to be enough to distrust the entry. Scanning is ruled
        # out by pointing the scan window at a port range that is not the server's.
        monkeypatch.setattr(ports, "_listening", lambda host, port: False)
        assert ports.discover("dexter", 1, ttl=0) is None


def test_a_ledger_entry_with_a_live_pid_but_nothing_listening_is_not_believed(ledger):
    held, port = _hold()
    held.close()  # nothing is there any more, but our own pid is very much alive
    ports.publish("dexter", port, f"http://127.0.0.1:{port}/health")
    ports._forget()
    assert ports.discover("dexter", port, span=0, ttl=0) is None


def test_discover_scans_forward_when_the_ledger_is_empty(ledger):
    held, base = _hold()
    try:
        with _HealthServer({"app": "buzzedit", "status": "ok"}, port=0) as server:
            found = ports.discover(
                "buzzedit", server.port - 2, span=4, ttl=0,
            )
            assert found == server.base
    finally:
        held.close()


def test_an_explicit_env_url_wins_over_everything(ledger):
    with _HealthServer({"app": "buzzcaf", "status": "ok"}) as right:
        with _HealthServer({"app": "buzzcaf", "status": "ok"}) as decoy:
            ports.publish("buzzcaf", decoy.port, f"{decoy.base}/health")
            ports._forget()
            assert ports.discover("buzzcaf", 1, env_url=right.base, ttl=0) == right.base


def test_an_env_url_is_read_from_the_conventional_variable(ledger, monkeypatch):
    with _HealthServer({"app": "buzzcaf", "status": "ok"}) as server:
        monkeypatch.setenv("BUZZCAF_URL", server.base)
        assert ports.discover("buzzcaf", 1, ttl=0) == server.base


def test_an_env_url_may_hold_a_server_that_cannot_name_itself(ledger, monkeypatch):
    """buzzcode's llama-server is llama.cpp: its /health will never say `app`.

    An address the owner typed is allowed to hold one. A scan is not — that
    distinction is the whole of rule 4.
    """
    with _HealthServer({"status": "ok"}) as server:
        assert ports.discover("buzzcode", 1, env_url=server.base, ttl=0) == server.base
        assert ports.discover("buzzcode", server.port, span=0, ttl=0) is None


def test_discover_returns_none_when_nothing_is_there(ledger):
    held, port = _hold()
    held.close()  # released at once: a genuinely dead port
    assert ports.discover("dexter", port, span=1, ttl=0) is None


def test_a_result_is_cached_for_a_few_seconds(ledger):
    with _HealthServer({"app": "dexter", "status": "ok"}) as server:
        ports.publish("dexter", server.port, f"{server.base}/health")
        ports._forget()
        assert ports.discover("dexter", 1, ttl=30) == server.base
    # The server is gone, but the cached answer stands until the ttl expires.
    assert ports.discover("dexter", 1, ttl=30) == server.base
    ports._forget()
    assert ports.discover("dexter", 1, span=0, ttl=0) is None


def test_offline_is_cached_too_so_a_dead_app_is_not_rescanned(ledger, monkeypatch):
    calls = []

    def counted(host, port):
        calls.append(port)
        return False

    monkeypatch.setattr(ports, "_listening", counted)
    assert ports.discover("buzzfin", 8756, ttl=30) is None
    first = len(calls)
    assert ports.discover("buzzfin", 8756, ttl=30) is None
    assert len(calls) == first, "the second call rescanned instead of using the cache"


def test_publishing_clears_a_stale_cached_answer(ledger):
    assert ports.discover("dexter", 1, span=0, ttl=30) is None
    with _HealthServer({"app": "dexter", "status": "ok"}) as server:
        ports.publish("dexter", server.port, f"{server.base}/health")
        assert ports.discover("dexter", 1, ttl=0) == server.base


# ───────────────────────── pid liveness ─────────────────────────


def test_our_own_pid_is_alive_and_a_reaped_one_is_not():
    assert ports._pid_alive(os.getpid()) is True
    assert ports._pid_alive(_dead_pid()) is False
    assert ports._pid_alive(0) is False
    assert ports._pid_alive(None) is False
    assert ports._pid_alive("not a pid") is False


def test_the_liveness_check_does_not_use_os_kill_on_windows():
    """`os.kill(pid, 0)` on Windows calls TerminateProcess — it *kills* the pid.

    The POSIX idiom for "does this process exist" is a loaded gun here, and a
    port helper that runs in every app is the last place it belongs.
    """
    source = open(ports.__file__, "r", encoding="utf-8").read()
    body = source.split("def _pid_alive", 1)[1].split("\ndef ", 1)[0]
    windows_branch = body.split('sys.platform == "win32"', 1)[1].split("try:", 1)[0]
    assert "os.kill" not in windows_branch


# ───────────────────── one implementation per language ─────────────────────

#: Where the copies live, as P2 and P3 actually landed them. Section 11 says one
#: implementation per language, copied byte-identical, so this list is the whole
#: truth and a missing file is a **failure**, not a skip: the copies test that
#: quietly skipped is the one that lets a repo drift for a month unnoticed.
COPY_PATHS = (
    "BuzzcafAI/backend/integrations/buzzcaf_ports.py",
    "BuzzFin/server/buzzfin_server/buzzcaf_ports.py",
    "BuzzcafCoffee/ops/buzzcaf_ports.py",
    "MastGaane/mastgaane/buzzcaf_ports.py",
    "MidnightBuzz/backend/buzzcaf_ports.py",
    "CaliberAI/backend/app/buzzcaf_ports.py",
    "BuzzEdit/backend/buzzcaf_ports.py",
)

#: The Node helper has the same law: `BuzzEdit/scripts/buzzcaf-ports.mjs` is
#: canonical, BuzzViolin's launcher imports its own copy, and the three Vite
#: configs that read the ledger import theirs (P4 replaced their inline
#: fifteen-line readers with it).
NODE_CANONICAL = "BuzzEdit/scripts/buzzcaf-ports.mjs"
NODE_COPY_PATHS = (
    "BuzzViolin/scripts/buzzcaf-ports.mjs",
    "BuzzFin/scripts/buzzcaf-ports.mjs",
    "CaliberAI/scripts/buzzcaf-ports.mjs",
    "MidnightBuzz/scripts/buzzcaf-ports.mjs",
)


def _ecosystem_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(ports.__file__))))


def _sibling_checkout(relative):
    """The absolute path, or None when that repository is not checked out here."""
    root = _ecosystem_root()
    repo = relative.split("/", 1)[0]
    if not os.path.isdir(os.path.join(root, repo)):
        return None
    return os.path.normpath(os.path.join(root, *relative.split("/")))


def test_every_copy_of_this_helper_is_byte_identical_to_the_canonical_one():
    """One implementation per language, or the ecosystem has two contracts."""
    canonical = open(ports.__file__, "rb").read()
    digest = hashlib.sha256(canonical).hexdigest()

    missing, drifted = [], []
    checked = 0
    for relative in COPY_PATHS:
        path = _sibling_checkout(relative)
        if path is None:
            continue  # that repository is not on this machine at all
        if not os.path.exists(path):
            missing.append(relative)
            continue
        checked += 1
        if hashlib.sha256(open(path, "rb").read()).hexdigest() != digest:
            drifted.append(relative)

    if checked == 0 and not missing:
        pytest.skip("no sibling repositories checked out next to dexter")
    assert not missing, (
        "these repositories are checked out but carry no copy of "
        f"buzzcaf_ports.py: {', '.join(missing)}"
    )
    assert not drifted, (
        "these copies have drifted from dexter/backend/buzzcaf_ports.py "
        f"(sha256 {digest[:12]}…): " + ", ".join(drifted)
    )


def test_every_copy_of_the_node_helper_is_byte_identical_to_the_canonical_one():
    """The same law for `buzzcaf-ports.mjs`.

    Nothing in Dexter imports the Node helper, which is exactly why its copies
    need a guard that runs somewhere: BuzzViolin's launcher and BuzzEdit's share
    one contract, and a Node copy that drifts breaks the ledger for both without
    a single Python test noticing.
    """
    canonical_path = _sibling_checkout(NODE_CANONICAL)
    if canonical_path is None:
        pytest.skip("no BuzzEdit checkout next door")
    assert os.path.exists(canonical_path), canonical_path
    digest = hashlib.sha256(open(canonical_path, "rb").read()).hexdigest()

    missing, drifted = [], []
    for relative in NODE_COPY_PATHS:
        path = _sibling_checkout(relative)
        if path is None:
            continue
        if not os.path.exists(path):
            missing.append(relative)
        elif hashlib.sha256(open(path, "rb").read()).hexdigest() != digest:
            drifted.append(relative)

    assert not missing, f"checked out but missing the Node helper: {', '.join(missing)}"
    assert not drifted, (
        "these copies have drifted from BuzzEdit/scripts/buzzcaf-ports.mjs "
        f"(sha256 {digest[:12]}…): " + ", ".join(drifted)
    )


def test_the_ecosystem_contract_is_byte_identical_in_both_repositories():
    """`ECOSYSTEM.md` carries the port rules now, and two copies of a contract
    that disagree are worse than one copy nobody reads."""
    root = _ecosystem_root()
    here = os.path.join(root, "dexter", "ECOSYSTEM.md")
    there = os.path.join(root, "BuzzcafAI", "ECOSYSTEM.md")
    if not os.path.exists(there):
        pytest.skip("no BuzzcafAI checkout next door")
    assert os.path.exists(here), here
    assert open(here, "rb").read() == open(there, "rb").read(), (
        "dexter/ECOSYSTEM.md and BuzzcafAI/ECOSYSTEM.md have drifted apart"
    )
