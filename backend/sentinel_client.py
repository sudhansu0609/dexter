"""
Talk to Sentinel, the machine's memory/VRAM guardian, over its named pipe.

Sentinel decides who gets the 16 GB of VRAM on this PC. Dexter is one of the
claimants, and the only one that can *choose* not to load a model. So this
client does three things:

* **Register** — "PID 47776 is me, I am the assistant, my budget is 5 GB."
  That beats Sentinel's name-based rules, which cannot tell Dexter's
  `pythonw.exe` from anyone else's.
* **Reserve** — ask for VRAM *before* loading a model, and take no for an
  answer. Two parties both deciding there is room, and both then discovering
  there is not, is how the card ends up paging to system RAM and everything
  stalls.
* **Subscribe** — get told when VRAM is tight, and drop the big model rather
  than wait to be frozen.

Everything here is best-effort and non-blocking by design. Sentinel not being
installed, not running, or being slow must never make Dexter slower: every call
has a short timeout and returns an "absent" value on any failure. Nothing in
Dexter may depend on this succeeding.

Transport is the same newline-delimited JSON the Sentinel tray speaks, and the
JSON shapes are serde's default externally-tagged enums, e.g.
``{"Reserve": {"client": "dexter", "mib": 4096}}`` in and
``{"Reserved": {...}}`` back.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger("dexter.sentinel")

DEFAULT_PIPE = r"\\.\pipe\Sentinel"
#: Every request gets this long before we give up and behave as if Sentinel is
#: absent. It is deliberately short: this sits in front of a model load.
DEFAULT_TIMEOUT = 0.3
#: A reserve or a status call may legitimately take a little longer than a
#: liveness probe, but still nowhere near long enough to be noticed.
REQUEST_TIMEOUT = 1.0


class SentinelClient:
    """A thin, forgiving client for Sentinel's pipe.

    Thread-safe for the calls Dexter makes: each request opens its own short
    lived connection, and the subscription runs on its own connection so a
    command can never be mistaken for part of the event stream.
    """

    def __init__(
        self,
        pipe: Optional[str] = None,
        client: str = "dexter",
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self.pipe = pipe or os.environ.get("SENTINEL_PIPE", DEFAULT_PIPE)
        self.client = client
        self.timeout = timeout
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        #: Set once a Register has been accepted, so the subscriber thread can
        #: re-register after Sentinel restarts.
        self._registration: Optional[Dict[str, Any]] = None
        #: Protocol v2 handshake. `None` = not asked yet, `{}` = asked and the
        #: service did not answer (an old build, or absent).
        self._hello: Optional[Dict[str, Any]] = None
        #: The opaque token a v2 service hands back from `Register`. Not sent on
        #: anything yet — the protocol binds identity to the pipe's client PID —
        #: but kept so `/api/dexter/resources` can show we are registered.
        self.token: Optional[str] = None

    # ---------------------------------------------------------------- transport

    def _round_trip(self, command: Any) -> Optional[Dict[str, Any]]:
        """Open the pipe, send one command, read one reply. Blocking."""
        line = (json.dumps(command) + "\n").encode("utf-8")
        with open(self.pipe, "r+b", buffering=0) as handle:
            handle.write(line)
            return _read_line(handle)

    def _request(self, command: Any, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """`_round_trip` with a deadline, on a worker thread.

        Windows named-pipe I/O has no per-call timeout we can set from Python,
        so the call is made on a daemon thread and simply abandoned if it takes
        too long. An abandoned thread costs one handle until the pipe closes;
        that is a far better outcome than blocking a user's turn.
        """
        result: Dict[str, Any] = {}

        def run() -> None:
            try:
                result["value"] = self._round_trip(command)
            except Exception as exc:  # pipe missing, ACL, service restarting
                result["error"] = exc

        worker = threading.Thread(target=run, name="sentinel-request", daemon=True)
        worker.start()
        worker.join(timeout if timeout is not None else self.timeout)
        if worker.is_alive():
            log.debug("sentinel: %s timed out", _command_name(command))
            return None
        if "error" in result:
            log.debug("sentinel: %s failed: %s", _command_name(command), result["error"])
            return None
        return result.get("value")

    # -------------------------------------------------------------- handshake

    def hello(self, force: bool = False) -> Dict[str, Any]:
        """Ask the service what protocol it speaks. `{}` means "an old build".

        Protocol v2 answers ``{"Hello": {"version": "2.0.0", "features": [...]}}``.
        The v1 service that is installed today has never heard of `Hello`: it
        either answers something else or drops the connection, and both land
        here as `{}`. Everything downstream keys off `supports()` rather than a
        version number, so a partial v2 rollout degrades feature by feature
        instead of all at once.
        """
        if self._hello is not None and not force:
            return self._hello
        reply = self._request("Hello", timeout=DEFAULT_TIMEOUT)
        if isinstance(reply, dict) and isinstance(reply.get("Hello"), dict):
            self._hello = reply["Hello"]
        else:
            self._hello = {}
        return self._hello

    def features(self) -> list:
        """The feature list from `Hello`; empty on a v1 service or when absent."""
        return list(self.hello().get("features") or [])

    def supports(self, feature: str) -> bool:
        return feature in self.features()

    def version(self) -> Optional[str]:
        return self.hello().get("version")

    # ------------------------------------------------------------------ queries

    def available(self) -> bool:
        """Is Sentinel there and answering? Fast, and never raises."""
        return self._request("GpuStatus", timeout=DEFAULT_TIMEOUT) is not None

    def gpu_status(self) -> Optional[Dict[str, Any]]:
        """The full GPU picture, or None when Sentinel cannot answer.

        The returned dict is Sentinel's `GpuStatus`: `adapters`, `zone`, `top`,
        `reservations`, `registrations`, `frozen`.
        """
        reply = self._request("GpuStatus")
        if isinstance(reply, dict) and "Gpu" in reply:
            return reply["Gpu"]
        return None

    def vram_free_mib(self) -> Optional[int]:
        """Free dedicated VRAM on the primary adapter, in MiB."""
        status = self.gpu_status()
        if not status:
            return None
        for adapter in status.get("adapters", []):
            if adapter.get("is_primary"):
                return int(adapter.get("free", 0)) // (1024 * 1024)
        return None

    def frozen(self) -> list:
        """Everything Sentinel is currently holding frozen."""
        reply = self._request("ListFrozen")
        if isinstance(reply, dict) and "Frozen" in reply:
            return reply["Frozen"]
        return []

    # ------------------------------------------------------------- registration

    def register(
        self,
        pid: int,
        cls: str = "assistant",
        budget_mib: Optional[int] = None,
        ram_budget_mib: Optional[int] = None,
        priority: Optional[int] = None,
        label: Optional[str] = None,
    ) -> bool:
        """Declare this PID's class, and optionally its VRAM/RAM budgets.

        The v2 fields are only put on the wire when they are set, because a v1
        service rejects a `Register` carrying keys it does not know — and the v1
        service is the one installed right now. Both replies are accepted:
        `"Ok"` from v1, `{"Registered": {"token": …}}` from v2.
        """
        payload: Dict[str, Any] = {
            "client": self.client, "pid": pid, "class": cls, "budget_mib": budget_mib,
        }
        if ram_budget_mib is not None:
            payload["ram_budget_mib"] = ram_budget_mib
        if priority is not None:
            payload["priority"] = priority
        if label:
            payload["label"] = label

        reply = self._request({"Register": payload})
        ok = reply == "Ok"
        if isinstance(reply, dict) and isinstance(reply.get("Registered"), dict):
            ok = True
            self.token = reply["Registered"].get("token")
        if ok:
            self._registration = payload
        return ok

    def unregister(self) -> bool:
        self._registration = None
        self.token = None
        return self._request({"Unregister": {"client": self.client}}) == "Ok"

    # -------------------------------------------------------------- reservation

    def reserve(
        self,
        mib: int,
        ram_mib: Optional[int] = None,
        for_pid: Optional[int] = None,
        ttl_secs: Optional[int] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Ask for `mib` of VRAM (and optionally `ram_mib` of RAM) before loading.

        Returns `(granted, details)`. **When Sentinel is absent this returns
        `(True, {...absent...})`** — a guardian that is not running must not
        stop Dexter from working, and the pre-Sentinel behaviour was to load
        the model and hope.

        `for_pid` names the process that will actually consume the memory (the
        llama-server Dexter is about to spawn, not Dexter), so Sentinel tracks
        consumption where it happens. The optional fields are omitted when
        unset so the v1 wire shape is unchanged.
        """
        return self._book("Reserve", mib, ram_mib, for_pid, ttl_secs)

    def query(self, mib: int, ram_mib: Optional[int] = None) -> Tuple[bool, Dict[str, Any]]:
        """Would a reservation of this size be granted? Books nothing.

        Used before launching an app: the answer names the holders, which is
        what Dexter reads out instead of just refusing. A v1 service has no
        `Query`, so it degrades to "would be granted" (absent) and the launch
        proceeds exactly as it did before Sentinel existed.
        """
        if not self.supports("query"):
            return True, {"absent": True, "reason": "this Sentinel has no Query"}
        return self._book("Query", mib, ram_mib, None, None)

    def _book(
        self,
        command: str,
        mib: int,
        ram_mib: Optional[int],
        for_pid: Optional[int],
        ttl_secs: Optional[int],
    ) -> Tuple[bool, Dict[str, Any]]:
        payload: Dict[str, Any] = {"client": self.client, "mib": int(mib)}
        # RAM admission only exists on v2; sending it to v1 would fail the whole
        # reservation, which is exactly the wrong way to fail.
        if ram_mib is not None and self.supports("ram_reserve"):
            payload["ram_mib"] = int(ram_mib)
        if for_pid is not None:
            payload["for_pid"] = int(for_pid)
        if ttl_secs is not None:
            payload["ttl_secs"] = int(ttl_secs)
        reply = self._request({command: payload})
        if not isinstance(reply, dict) or "Reserved" not in reply:
            return True, {"absent": True, "reason": "Sentinel is not answering"}
        details = reply["Reserved"]
        return bool(details.get("granted")), details

    def release(self) -> None:
        """Give back whatever this client had reserved."""
        self._request({"Release": {"client": self.client}})

    # ------------------------------------------------------- RAM / disk / apps

    def ram_status(self) -> Optional[Dict[str, Any]]:
        """Zones, physical availability, commit and the top RAM holders."""
        reply = self._request("RamStatus")
        if isinstance(reply, dict) and "Ram" in reply:
            return reply["Ram"]
        return None

    def disk_status(self) -> Optional[Dict[str, Any]]:
        """Per-drive free space, zone, and the last cleanup the tray ran."""
        reply = self._request("DiskStatus")
        if isinstance(reply, dict) and "Disk" in reply:
            return reply["Disk"]
        return None

    def request_cleanup(self, mode: str = "safe", drive: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Ask the service to queue a SmartCleaner run (the tray executes it)."""
        payload: Dict[str, Any] = {"client": self.client, "mode": mode}
        if drive:
            payload["drive"] = drive
        reply = self._request({"RequestCleanup": payload}, timeout=5.0)
        if isinstance(reply, dict) and "Cleanup" in reply:
            return reply["Cleanup"]
        return None

    def app_status(self) -> Optional[list]:
        """One row per classified app with a live process, from Sentinel's own
        table — so "what's running" costs no second process scan."""
        reply = self._request("AppStatus")
        if isinstance(reply, dict) and isinstance(reply.get("Apps"), list):
            return reply["Apps"]
        return None

    # ------------------------------------------------------------ batch control

    def pause_batch(self) -> Optional[list]:
        """Freeze every batch job. They stay frozen until `resume_batch`."""
        reply = self._request("PauseBatch", timeout=5.0)
        if isinstance(reply, dict) and "Frozen" in reply:
            return reply["Frozen"]
        return None

    def resume_batch(self) -> Optional[list]:
        reply = self._request("ResumeBatch", timeout=5.0)
        if isinstance(reply, dict) and "Frozen" in reply:
            return reply["Frozen"]
        return None

    # -------------------------------------------------------------- event stream

    def subscribe(self, on_event: Callable[[Dict[str, Any]], None]) -> threading.Thread:
        """Start a daemon thread that pushes Sentinel's events to `on_event`.

        Reconnects with backoff, so a Sentinel restart is invisible. Each event
        is a single-key dict, e.g. `{"VramTight": {...}}`. An exception from
        `on_event` is logged and swallowed: a handler bug must not kill the
        stream, and the stream must not kill Dexter.
        """
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._subscribe_loop, args=(on_event,), name="sentinel-events", daemon=True
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        """Ask the subscription thread to finish."""
        self._stop.set()

    def _subscribe_loop(self, on_event: Callable[[Dict[str, Any]], None]) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with open(self.pipe, "r+b", buffering=0) as handle:
                    handle.write(
                        (json.dumps({"Subscribe": {"client": self.client}}) + "\n").encode("utf-8")
                    )
                    first = _read_line(handle)
                    if first != "Subscribed":
                        raise RuntimeError(f"unexpected reply to Subscribe: {first!r}")
                    log.info("sentinel: subscribed to events on %s", self.pipe)
                    backoff = 1.0
                    # Sentinel may have restarted since we registered, in which
                    # case it has forgotten us; say who we are again.
                    if self._registration:
                        prior = dict(self._registration)
                        self._hello = None  # it may have come back as a new build
                        self.register(
                            int(prior.get("pid") or 0),
                            str(prior.get("class") or "assistant"),
                            prior.get("budget_mib"),
                            prior.get("ram_budget_mib"),
                            prior.get("priority"),
                            prior.get("label"),
                        )
                    while not self._stop.is_set():
                        event = _read_line(handle)
                        if event is None:
                            break
                        try:
                            on_event(event)
                        except Exception:
                            log.exception("sentinel: event handler raised")
            except Exception as exc:
                log.debug("sentinel: event stream unavailable (%s)", exc)
            if self._stop.is_set():
                return
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _read_line(handle) -> Optional[Any]:
    """Read one newline-terminated JSON value from a byte-mode pipe."""
    buf = bytearray()
    while True:
        chunk = handle.read(1)
        if not chunk:
            return None
        if chunk == b"\n":
            break
        buf += chunk
    text = buf.decode("utf-8").strip()
    if not text:
        return None
    return json.loads(text)


def _command_name(command: Any) -> str:
    if isinstance(command, str):
        return command
    if isinstance(command, dict) and command:
        return next(iter(command))
    return str(command)


# A single shared client, so the registration and the event subscription are
# one per process rather than one per caller.
_client: Optional[SentinelClient] = None
_client_lock = threading.Lock()


def client() -> SentinelClient:
    """The process-wide Sentinel client."""
    global _client
    with _client_lock:
        if _client is None:
            _client = SentinelClient()
        return _client
