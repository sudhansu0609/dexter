"""
Tests for the Sentinel pipe client.

Two things have to be true, and both are about *not* hurting Dexter:

1. When Sentinel is there, the wire shapes match what the Rust service expects.
   Those shapes are serde's externally-tagged enums; getting one field name
   wrong is a silent no-op in production, so they are asserted literally here
   and mirrored by a Rust test on the other side
   (`ipc::tests::the_wire_shapes_are_the_ones_the_python_client_writes`).
2. When Sentinel is *not* there — not installed, not running, wedged — every
   call degrades to an "absent" answer promptly and nothing raises.

The fake server is a real named pipe when Windows allows it, so the transport
itself is exercised. Where it cannot be (no pywin32, non-Windows), the tests
that need a server are skipped and the "absent" tests still run — those are the
ones that protect the user's turn latency.

Run:  python -m pytest backend/test_sentinel_client.py -q
"""

import json
import os
import sys
import threading
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sentinel_client
from sentinel_client import SentinelClient

try:
    import win32file  # type: ignore
    import win32pipe  # type: ignore

    HAVE_PIPES = True
except Exception:  # pragma: no cover - depends on the machine
    HAVE_PIPES = False

needs_pipes = pytest.mark.skipif(
    not HAVE_PIPES, reason="a real named-pipe server needs pywin32"
)


class FakeSentinel:
    """A one-connection-at-a-time named pipe that answers canned replies.

    `handler` receives each decoded command and returns the reply to send, or a
    list of replies (used for the event stream).
    """

    def __init__(self, handler):
        self.name = r"\\.\pipe\SentinelTest-" + uuid.uuid4().hex[:12]
        self.handler = handler
        self.received = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        time.sleep(0.05)  # let the first instance exist before a client connects
        return self

    def __exit__(self, *exc):
        self._stop.set()
        # Unblock a pending ConnectNamedPipe by connecting to it ourselves.
        try:
            with open(self.name, "r+b", buffering=0):
                pass
        except Exception:
            pass
        self._thread.join(timeout=2)

    def _serve(self):
        while not self._stop.is_set():
            handle = win32pipe.CreateNamedPipe(
                self.name,
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
                win32pipe.PIPE_UNLIMITED_INSTANCES,
                4096, 4096, 0, None,
            )
            try:
                win32pipe.ConnectNamedPipe(handle, None)
                if self._stop.is_set():
                    return
                self._session(handle)
            except Exception:
                pass
            finally:
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass

    def _session(self, handle):
        while not self._stop.is_set():
            line = b""
            while not line.endswith(b"\n"):
                _, chunk = win32file.ReadFile(handle, 1)
                if not chunk:
                    return
                line += chunk
            command = json.loads(line.decode())
            self.received.append(command)
            reply = self.handler(command)
            if reply is None:
                return
            for item in reply if isinstance(reply, list) else [reply]:
                win32file.WriteFile(handle, (json.dumps(item) + "\n").encode())


def gpu_status_payload(free_bytes=10 * 1024**3):
    return {
        "Gpu": {
            "adapters": [
                {
                    "name": "NVIDIA GeForce RTX 5060 Ti",
                    "luid": "0x00000000_0x000165BB",
                    "total": 16 * 1024**3,
                    "used": 16 * 1024**3 - free_bytes,
                    "free": free_bytes,
                    "is_primary": True,
                }
            ],
            "zone": "Green",
            "top": [],
            "reservations": [],
            "registrations": [],
            "frozen": [],
        }
    }


# --------------------------------------------------------------------- absent

def absent_client():
    """A client pointed at a pipe that certainly does not exist."""
    return SentinelClient(pipe=r"\\.\pipe\SentinelDefinitelyNotThere-" + uuid.uuid4().hex)


def test_an_absent_sentinel_is_reported_absent_quickly():
    client = absent_client()
    started = time.time()
    assert client.available() is False
    assert time.time() - started < 0.5


def test_every_call_degrades_rather_than_raising():
    client = absent_client()
    assert client.gpu_status() is None
    assert client.vram_free_mib() is None
    assert client.frozen() == []
    assert client.register(1234, "assistant", 5120) is False
    assert client.unregister() is False
    assert client.pause_batch() is None
    assert client.resume_batch() is None
    client.release()  # must not raise


def test_a_reserve_is_granted_when_sentinel_is_absent():
    """The guardian not running must never be the reason Dexter refuses to work."""
    granted, details = absent_client().reserve(4096)
    assert granted is True
    assert details.get("absent") is True


# ------------------------------------------------------------------- present

@needs_pipes
def test_register_sends_the_shape_the_service_expects():
    with FakeSentinel(lambda cmd: "Ok") as server:
        client = SentinelClient(pipe=server.name, client="dexter")
        assert client.register(4242, "assistant", 5120) is True
    assert server.received[0] == {
        "Register": {"client": "dexter", "pid": 4242, "class": "assistant", "budget_mib": 5120}
    }


@needs_pipes
def test_a_granted_reserve_is_reported_with_its_figures():
    reply = {
        "Reserved": {
            "granted": True,
            "free_mib": 7820,
            "reserved_mib": 4096,
            "expires_in_secs": 90,
            "reason": "4096 MiB reserved for 90s",
        }
    }
    with FakeSentinel(lambda cmd: reply) as server:
        granted, details = SentinelClient(pipe=server.name).reserve(4096)
    assert granted is True
    assert details["free_mib"] == 7820
    assert server.received[0] == {"Reserve": {"client": "dexter", "mib": 4096}}


@needs_pipes
def test_a_denied_reserve_carries_the_reason_dexter_reads_out():
    reply = {
        "Reserved": {
            "granted": False,
            "free_mib": 2048,
            "reserved_mib": 0,
            "expires_in_secs": 0,
            "reason": "not enough VRAM: 12000 MiB requested but only 1280 MiB available",
        }
    }
    with FakeSentinel(lambda cmd: reply) as server:
        granted, details = SentinelClient(pipe=server.name).reserve(12000)
    assert granted is False
    assert details["free_mib"] == 2048
    assert "not enough VRAM" in details["reason"]


@needs_pipes
def test_gpu_status_is_unwrapped_and_free_vram_is_read_from_the_primary_adapter():
    with FakeSentinel(lambda cmd: gpu_status_payload(free_bytes=10 * 1024**3)) as server:
        client = SentinelClient(pipe=server.name)
        assert client.available() is True
        status = client.gpu_status()
        assert status["adapters"][0]["is_primary"] is True
        assert client.vram_free_mib() == 10 * 1024


@needs_pipes
def test_events_are_dispatched_to_the_handler():
    events = [
        "Subscribed",
        {"VramTight": {"level": "red", "used": 1, "total": 2, "free": 3,
                       "over_budget": ["dexter"], "deadline_secs": 10}},
        {"Frozen": {"pid": 900, "name": "vramstress.exe", "class": "batch",
                    "by": "gpu", "reason": "VRAM Red"}},
        {"Heartbeat": {"every_secs": 30}},
    ]
    seen = []
    done = threading.Event()

    def handler(cmd):
        return events if "Subscribe" in cmd else "Ok"

    def on_event(event):
        seen.append(event)
        if len(seen) >= 3:
            done.set()

    with FakeSentinel(handler) as server:
        client = SentinelClient(pipe=server.name)
        client.subscribe(on_event)
        assert done.wait(timeout=5), f"only saw {seen}"
        client.stop()

    assert "VramTight" in seen[0]
    assert seen[0]["VramTight"]["level"] == "red"
    assert "Frozen" in seen[1]
    assert "Heartbeat" in seen[2]


@needs_pipes
def test_a_handler_that_raises_does_not_kill_the_stream():
    events = ["Subscribed", {"Heartbeat": {"every_secs": 30}}, {"Heartbeat": {"every_secs": 30}}]
    seen = []
    done = threading.Event()

    def on_event(event):
        seen.append(event)
        if len(seen) >= 2:
            done.set()
        raise RuntimeError("handler is buggy")

    with FakeSentinel(lambda cmd: events if "Subscribe" in cmd else "Ok") as server:
        client = SentinelClient(pipe=server.name)
        client.subscribe(on_event)
        assert done.wait(timeout=5), f"stream died after {seen}"
        client.stop()


def test_the_pipe_name_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("SENTINEL_PIPE", r"\\.\pipe\SentinelDev")
    assert SentinelClient().pipe == r"\\.\pipe\SentinelDev"
    monkeypatch.delenv("SENTINEL_PIPE")
    assert SentinelClient().pipe == r"\\.\pipe\Sentinel"


def test_the_shared_client_is_a_singleton():
    sentinel_client._client = None
    first = sentinel_client.client()
    assert sentinel_client.client() is first


# ─────────────────────── protocol v2 (roadmap v9, D1/D8) ───────────────────────
#
# The service being built in parallel speaks v2: `Hello` advertises features,
# `Register` answers `Registered{token}`, `Reserve` takes `ram_mib`/`for_pid`,
# `Query` books nothing, and there are RAM/disk/app/cleanup commands. The v1
# tests above stay exactly as they are, because the build installed on this
# machine today *is* v1 and that path must keep working unchanged.


V2_FEATURES = ["ram_reserve", "query", "priority", "disk", "evict", "app_status", "cleanup"]


def v2_handler(extra=None):
    """A fake service that speaks v2. `extra` overrides individual replies."""
    extra = extra or {}

    def handle(cmd):
        name = cmd if isinstance(cmd, str) else next(iter(cmd))
        if name in extra:
            reply = extra[name]
            return reply(cmd) if callable(reply) else reply
        if name == "Hello":
            return {"Hello": {"version": "2.0.0", "features": V2_FEATURES}}
        if name == "Register":
            return {"Registered": {"token": "tok-123"}}
        if name in ("Reserve", "Query"):
            return {"Reserved": {
                "granted": True, "free_mib": 8000, "reserved_mib": 4096,
                "ram_free_mib": 12000, "ram_reserved_mib": 2048,
                "expires_in_secs": 90, "reason": "ok", "blockers": [],
            }}
        if name == "RamStatus":
            return {"Ram": {"zone": "Green", "total": 32 * 1024**3, "avail": 12 * 1024**3,
                            "commit_total": 20 * 1024**3, "commit_limit": 64 * 1024**3,
                            "reservations": [], "top": []}}
        if name == "DiskStatus":
            return {"Disk": {"drives": [{"letter": "C:", "total": 500 * 1024**3,
                                         "free": 97 * 1024**3, "zone": "Green"}],
                             "last_cleanup": {"at": "2026-09-09T04:30:00",
                                              "freed_bytes": 3_400_000_000, "mode": "safe"},
                             "pending": False}}
        if name == "RequestCleanup":
            return {"Cleanup": {"queued": True, "reason": "the owner asked"}}
        if name == "AppStatus":
            return {"Apps": [{"label": "VrDeep", "class": "batch", "priority": 90,
                              "evictable": False, "pids": [33228], "vram_mib": 10240,
                              "ram_mib": 14000, "frozen": False, "registered_as": None}]}
        return "Ok"

    return handle


@needs_pipes
def test_hello_reports_the_version_and_features_and_is_cached():
    with FakeSentinel(v2_handler()) as server:
        client = SentinelClient(pipe=server.name)
        assert client.version() == "2.0.0"
        assert client.supports("ram_reserve") is True
        assert client.supports("teleportation") is False
        client.features()  # a second read must not go back to the pipe
    assert [next(iter(c)) if isinstance(c, dict) else c for c in server.received] == ["Hello"]


@needs_pipes
def test_an_old_service_that_does_not_know_hello_degrades_to_v1():
    """The build installed today has never heard of Hello.

    That is not an error: it means no RAM admission, no Query, and v1 behaviour
    throughout. In particular `ram_mib` must not reach the wire, because an old
    service refuses a Reserve carrying keys it does not know - which would turn
    "the guardian is old" into "Dexter cannot load a model".
    """
    with FakeSentinel(lambda cmd: "Ok") as server:
        client = SentinelClient(pipe=server.name)
        assert client.features() == []
        assert client.version() is None
        assert client.reserve(4096, ram_mib=2048)[0] is True
    assert server.received[-1] == {"Reserve": {"client": "dexter", "mib": 4096}}


@needs_pipes
def test_register_sends_the_v2_fields_and_keeps_the_token():
    with FakeSentinel(v2_handler()) as server:
        client = SentinelClient(pipe=server.name)
        client.hello()
        assert client.register(4242, "assistant", 5120, ram_budget_mib=3072,
                               priority=60, label="Dexter") is True
        assert client.token == "tok-123"
    assert server.received[-1] == {"Register": {
        "client": "dexter", "pid": 4242, "class": "assistant", "budget_mib": 5120,
        "ram_budget_mib": 3072, "priority": 60, "label": "Dexter",
    }}


@needs_pipes
def test_a_v1_register_still_gets_the_old_shape_and_the_old_reply():
    """No new keys when nothing new was asked for, and Ok is still a yes."""
    with FakeSentinel(lambda cmd: "Ok") as server:
        assert SentinelClient(pipe=server.name).register(1, "assistant", 5120) is True
    assert server.received[-1] == {
        "Register": {"client": "dexter", "pid": 1, "class": "assistant", "budget_mib": 5120}
    }


@needs_pipes
def test_reserve_carries_ram_for_pid_and_ttl_on_a_v2_service():
    with FakeSentinel(v2_handler()) as server:
        client = SentinelClient(pipe=server.name)
        client.hello()
        granted, details = client.reserve(12500, ram_mib=6500, for_pid=4711, ttl_secs=180)
    assert granted is True
    assert details["ram_free_mib"] == 12000
    assert server.received[-1] == {"Reserve": {
        "client": "dexter", "mib": 12500, "ram_mib": 6500, "for_pid": 4711, "ttl_secs": 180,
    }}


@needs_pipes
def test_query_books_nothing_and_is_skipped_on_a_service_without_it():
    with FakeSentinel(v2_handler()) as server:
        client = SentinelClient(pipe=server.name)
        client.hello()
        granted, _ = client.query(9000, ram_mib=4000)
    assert granted is True
    assert server.received[-1] == {"Query": {"client": "dexter", "mib": 9000, "ram_mib": 4000}}

    with FakeSentinel(lambda cmd: "Ok") as old:
        client = SentinelClient(pipe=old.name)
        granted, details = client.query(9000)
    assert granted is True and details["absent"] is True
    assert not any("Query" in c for c in old.received if isinstance(c, dict))


@needs_pipes
def test_a_refusal_carries_the_blockers_dexter_reads_out():
    """The whole point of the v2 refusal: Dexter can name who has the memory."""
    refused = {"Reserved": {
        "granted": False, "free_mib": 1200, "reserved_mib": 0,
        "ram_free_mib": 2000, "ram_reserved_mib": 0, "expires_in_secs": 0,
        "reason": "not enough VRAM",
        "blockers": [{"process": "VrDeep", "pid": 33228, "vram_mib": 10240,
                      "ram_mib": 14000, "class": "batch", "evictable": False}],
    }}
    with FakeSentinel(v2_handler({"Reserve": refused})) as server:
        client = SentinelClient(pipe=server.name)
        client.hello()
        granted, details = client.reserve(12500, ram_mib=6500)
    assert granted is False
    assert details["blockers"][0]["process"] == "VrDeep"


@needs_pipes
def test_ram_disk_cleanup_and_app_status_are_unwrapped():
    with FakeSentinel(v2_handler()) as server:
        client = SentinelClient(pipe=server.name)
        client.hello()
        assert client.ram_status()["zone"] == "Green"
        assert client.disk_status()["drives"][0]["letter"] == "C:"
        assert client.request_cleanup("safe", "C:")["queued"] is True
        assert client.app_status()[0]["label"] == "VrDeep"
    assert server.received[-2] == {
        "RequestCleanup": {"client": "dexter", "mode": "safe", "drive": "C:"}
    }


@needs_pipes
def test_unregister_forgets_the_token():
    with FakeSentinel(v2_handler()) as server:
        client = SentinelClient(pipe=server.name)
        client.hello()
        client.register(7, "assistant", 512)
        assert client.token == "tok-123"
        assert client.unregister() is True
        assert client.token is None


def test_the_v2_commands_all_degrade_when_sentinel_is_absent():
    """Every new call has to answer absent, fast, exactly like the v1 ones."""
    client = absent_client()
    started = time.time()
    assert client.hello() == {}
    assert client.features() == []
    assert client.ram_status() is None
    assert client.disk_status() is None
    assert client.request_cleanup() is None
    assert client.app_status() is None
    assert client.query(4096)[0] is True
    assert client.reserve(4096, ram_mib=2048, for_pid=1, ttl_secs=90)[0] is True
    assert time.time() - started < 5.0
