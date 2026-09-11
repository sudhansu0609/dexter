"""
The model steward (roadmap v9, D2) — the only door to a model.

Before this module, five places in Dexter loaded or unloaded a model: startup
warm-up, the fast-model re-pin, the Tier 1.5 escalation, `code_agent` making
room for buzzcode, and the `VramTight` handler. Only one of them asked
Sentinel first, and none of them ever called `Release`. On a 16 GB card shared
with VrDeep, BuzzEdit's ComfyUI and buzzcode's 27B, that is how the machine
ends up paging and everything stalls at once.

So there is now exactly one rule, and a test that enforces it
(`test_model_steward.py::test_no_module_loads_a_model_behind_the_stewards_back`):

    **Nothing in Dexter calls `hybrid_llm.warm_model` / `release_model` /
    `set_keep_alive` except this module.**

What the steward does with that monopoly:

* `ensure()` — estimate VRAM *and* RAM, `Reserve` them from Sentinel, load,
  then `Release` as soon as the load is *observed* (Ollama's `/api/ps` lists
  the model) or has failed. A booking that is never released is a booking that
  starves the next claimant, and a booking held across a load that silently
  failed is worse.
* On a refusal it returns a `Decision` that names the holder and the move
  Dexter is allowed to make. Dexter then keeps answering on whatever is already
  loaded — a guardian saying "no" is not a reason to go quiet, and it is
  certainly not a reason to load anyway.
* `shed()` — the cooperative ladder Sentinel's `VramTight` / `RamTight` /
  `Preempt` events ask for. Dexter can *choose* to unload; a render cannot.
  Shedding first is what keeps VrDeep from being frozen and buzzcode's engine
  from being evicted.

Sentinel absent, or the old v1 build with no RAM admission, is not an error
state: `reserve` answers "granted (absent)", `ram_mib` is simply not sent, and
the behaviour is exactly what it was before Sentinel existed.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import sentinel_client
from events import bus
from hybrid_llm import llm_router

log = logging.getLogger("dexter.steward")

_MIB = 1024 * 1024


# ───────────────────────── settings ─────────────────────────
#
# Read through module attributes rather than captured constants so
# settings_store can hot-reload them (D7).

VRAM_BUDGET_MIB = int(os.getenv("DEXTER_VRAM_BUDGET_MIB", "5120") or 5120)
RAM_BUDGET_MIB = int(os.getenv("DEXTER_RAM_BUDGET_MIB", "3072") or 3072)
#: Context, KV cache and compute buffers are not in the model file's size, and
#: under-asking is the failure that matters: a granted reservation that turns
#: out to be too small puts us straight back to paging.
RESERVE_OVERHEAD_MIB = int(os.getenv("DEXTER_RESERVE_OVERHEAD_MIB", "1024") or 1024)
#: A model's host-side footprint while it loads, as a fraction of its weights.
#: Ollama reads the blob through the page cache before handing it to CUDA.
_RAM_FRACTION = 0.35
MANAGE_LMSTUDIO = os.getenv("DEXTER_MANAGE_LMSTUDIO", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
#: How long a booking is held before Sentinel expires it on its own. Long enough
#: for a cold 7B off a spinning disk, short enough that a crashed Dexter does
#: not keep 6 GB booked for the rest of the day.
RESERVE_TTL_SECS = int(os.getenv("DEXTER_RESERVE_TTL_SECS", "90") or 90)
#: Ceiling on waiting for `/api/ps` to show the model. `warm_model` is already
#: synchronous; this only covers Ollama publishing the fact.
_OBSERVE_TIMEOUT = 8.0

ENGINE_VRAM_MIB = int(os.getenv("DEXTER_ENGINE_VRAM_MIB", "12500") or 12500)
ENGINE_RAM_MIB = int(os.getenv("DEXTER_ENGINE_RAM_MIB", "6500") or 6500)


# ───────────────────────── the answer ─────────────────────────


@dataclass
class Decision:
    """What the steward allowed, and — when it did not — what to say about it."""

    granted: bool
    purpose: str
    vram_mib: int = 0
    ram_mib: int = 0
    model: Optional[str] = None
    provider: str = ""
    #: True when the model is actually resident afterwards.
    loaded: bool = False
    reason: str = ""
    blockers: List[Dict[str, Any]] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def absent(self) -> bool:
        return bool(self.details.get("absent"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "granted": self.granted, "purpose": self.purpose, "model": self.model,
            "provider": self.provider, "loaded": self.loaded, "vramMib": self.vram_mib,
            "ramMib": self.ram_mib, "reason": self.reason, "blockers": self.blockers,
        }

    def message(self) -> str:
        """The refusal, in the owner's terms: who has the memory, and the move.

        Vagueness here reads as a malfunction, so the number is the one Sentinel
        actually refused on and the way out is named as a phrase Dexter
        understands.
        """
        free_gb = int(self.details.get("free_mib") or 0) / 1024.0
        holder = _describe_blockers(self.blockers)
        want_gb = self.vram_mib / 1024.0
        head = (
            f"Sentinel won't give me {want_gb:.1f} GB of video memory right now "
            f"— {free_gb:.1f} GB is free"
        )
        if holder:
            head += f" and {holder}"
        if _is_ram_refusal(self.reason):
            ram_free = int(self.details.get("ram_free_mib") or 0) / 1024.0
            head = (
                f"Sentinel won't give me the memory for that — {ram_free:.1f} GB of RAM free"
                + (f", {holder}" if holder else "")
            )
        return head + ". " + _allowed_move(self.blockers)


#: "not enough VRAM" contains the letters r-a-m. A naive substring test read
#: every VRAM refusal as a RAM refusal and told the owner "0.0 GB of RAM free"
#: while 22 GB was free — caught live, hence the word boundary.
_RAM_REASON = re.compile(r"(?<!v)\bram\b|\bmemory\s+pressure\b|\bcommit\b|\bphys_avail\b", re.I)


def _is_ram_refusal(reason: Optional[str]) -> bool:
    return bool(_RAM_REASON.search(reason or ""))


def _describe_blockers(blockers: List[Dict[str, Any]]) -> str:
    parts = []
    for b in (blockers or [])[:3]:
        name = b.get("client") or b.get("process") or b.get("label") or "something"
        vram = int(b.get("vram_mib") or 0)
        ram = int(b.get("ram_mib") or 0)
        held = f"{vram / 1024.0:.1f} GB" if vram else (f"{ram / 1024.0:.1f} GB of RAM" if ram else "")
        parts.append(f"{name} has {held}".strip() if held else str(name))
    return ", ".join(parts)


def _allowed_move(blockers: List[Dict[str, Any]]) -> str:
    """Only offer moves Sentinel's own rules permit. Never "I'll kill it"."""
    batch = [b for b in (blockers or []) if str(b.get("class", "")).lower() == "batch"]
    names = {str(b.get("client") or b.get("process") or b.get("label") or "").lower() for b in blockers or []}
    if any("llama-server" in n or "buzzcode" in n for n in names):
        return (
            "Say \"stop the coding engine\" to free it, \"pause batch jobs\" to park "
            "the background work, or wait — your call. I'll keep going on the small model."
        )
    if batch:
        return (
            "I can park the background work (\"pause batch jobs\") or we wait for it "
            "to finish — your call. I'll keep going on the small model meanwhile."
        )
    return (
        "I'll keep answering on the model that is already loaded. Say \"pause batch jobs\" "
        "if you want me to have the card."
    )


# ───────────────────────── estimates ─────────────────────────


def vram_estimate(model: Optional[str], provider: str = "ollama") -> int:
    """How much VRAM to book before loading `model`, in MiB.

    Ollama knows the real file size; when it cannot be read we fall back to the
    configured budget, which is the number the owner already tuned.
    """
    if not model:
        return VRAM_BUDGET_MIB
    size = None
    try:
        import runtime_health

        size = runtime_health._model_size_mb(llm_router.ollama_url, model)
    except Exception:
        size = None
    return int((size or VRAM_BUDGET_MIB) + RESERVE_OVERHEAD_MIB)


def ram_estimate(model: Optional[str], provider: str = "ollama") -> int:
    """Host memory the load itself needs, in MiB (never below a small floor)."""
    weights = vram_estimate(model, provider) - RESERVE_OVERHEAD_MIB
    return max(512, int(weights * _RAM_FRACTION))


# ───────────────────────── bookkeeping ─────────────────────────

_lock = threading.RLock()
#: Models this process asked Ollama to hold. Anything resident that is *not*
#: here belongs to CaliberAI, MastGaane or a stray JIT load, and is rung 3 of
#: the shed ladder.
_ours: Dict[str, float] = {}
_last_warm: Dict[str, float] = {}
_last_shed: Dict[str, Any] = {}
#: True only between a granted Reserve and its Release, so two turns racing
#: cannot both think they hold the booking.
_booked = False


def _client() -> Optional[Any]:
    """The shared Sentinel client, or None when the integration is switched off."""
    if os.getenv("DEXTER_SENTINEL", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    return sentinel_client.client()


def ours() -> List[str]:
    with _lock:
        return sorted(_ours)


def _mark_ours(model: str) -> None:
    with _lock:
        _ours[model] = time.time()


def _forget_ours(model: str) -> None:
    with _lock:
        _ours.pop(model, None)


# ───────────────────────── reservations ─────────────────────────


def reserve(
    purpose: str,
    vram_mib: int,
    ram_mib: Optional[int] = None,
    for_pid: Optional[int] = None,
    ttl_secs: Optional[int] = None,
) -> Decision:
    """Book memory from Sentinel. The only place in Dexter that calls `Reserve`.

    A refusal is a `Decision`, not an exception: every caller has something
    useful to do with it (refuse the job and name the holder, or carry on with
    what is already loaded).
    """
    global _booked
    client = _client()
    if client is None:
        return Decision(True, purpose, vram_mib, int(ram_mib or 0), details={"absent": True})

    started = time.time()
    granted, details = client.reserve(
        vram_mib, ram_mib=ram_mib, for_pid=for_pid,
        ttl_secs=ttl_secs if ttl_secs is not None else RESERVE_TTL_SECS,
    )
    log.info(
        "steward: reserve %s MiB VRAM + %s MiB RAM for %s -> %s in %.0f ms (%s)",
        vram_mib, ram_mib or 0, purpose, "granted" if granted else "DENIED",
        (time.time() - started) * 1000.0, details.get("reason", ""),
    )
    if granted and not details.get("absent"):
        with _lock:
            _booked = True
    return Decision(
        granted=granted, purpose=purpose, vram_mib=vram_mib, ram_mib=int(ram_mib or 0),
        reason=str(details.get("reason") or ""),
        blockers=list(details.get("blockers") or []),
        details=details,
    )


def query(purpose: str, vram_mib: int, ram_mib: Optional[int] = None) -> Decision:
    """Would this be granted? Books nothing — used before launching an app (D4)."""
    client = _client()
    if client is None:
        return Decision(True, purpose, vram_mib, int(ram_mib or 0), details={"absent": True})
    granted, details = client.query(vram_mib, ram_mib=ram_mib)
    return Decision(
        granted=granted, purpose=purpose, vram_mib=vram_mib, ram_mib=int(ram_mib or 0),
        reason=str(details.get("reason") or ""),
        blockers=list(details.get("blockers") or []),
        details=details,
    )


def release(why: str = "") -> None:
    """Hand the booking back. Safe to call when nothing is booked."""
    global _booked
    with _lock:
        had = _booked
        _booked = False
    if not had:
        return
    client = _client()
    if client is None:
        return
    try:
        client.release()
        log.debug("steward: released the booking (%s)", why or "done")
    except Exception:
        log.debug("steward: release failed", exc_info=True)


def booked() -> bool:
    with _lock:
        return _booked


# ───────────────────────── the door ─────────────────────────


def ensure(
    model: Optional[str],
    purpose: str,
    provider: str = "ollama",
    keep_alive: str = "30m",
) -> Decision:
    """Get `model` resident, with Sentinel's permission. The only load path.

    Returns a granted `Decision` when the model is loaded (or when there is
    nothing to load — a non-Ollama provider serves whatever it serves and
    Dexter has no lever on it), and a refused one carrying the blockers
    otherwise. **A refusal never loads anything.**
    """
    if provider != "ollama" or not model:
        # LM Studio and the llama-server hold their own model; Dexter does not
        # get to choose, so there is nothing to reserve and nothing to warm.
        return Decision(True, purpose, model=model, provider=provider, loaded=False,
                        details={"absent": True, "reason": "provider manages its own residency"})

    if _resident(model):
        # Already in memory: no reservation, because nothing new is consumed.
        _mark_ours(model)
        return Decision(True, purpose, model=model, provider=provider, loaded=True,
                        reason="already resident")

    vram = vram_estimate(model, provider)
    ram = ram_estimate(model, provider)
    decision = reserve(purpose, vram, ram)
    decision.model, decision.provider = model, provider
    if not decision.granted:
        print(f"[Steward] {purpose}: refused {vram} MiB for {model} — {decision.reason}")
        return decision

    started = time.time()
    ok = False
    try:
        ok = bool(llm_router.warm_model(provider, model, keep_alive))
    except Exception as exc:
        log.warning("steward: warming %s failed: %s", model, exc)
    finally:
        # Released once the load is *observed*, or straight away when it failed.
        # Holding a booking behind a model that never arrived starves everyone.
        if ok:
            ok = _observe_load(model)
        release(f"{purpose}:{model}")

    decision.loaded = ok
    if ok:
        _mark_ours(model)
        print(f"[Steward] {purpose}: {model} resident in {time.time() - started:.1f}s "
              f"({vram} MiB booked, released).")
    else:
        decision.reason = decision.reason or "the model did not load"
        print(f"[Steward] {purpose}: {model} did not load ({time.time() - started:.1f}s).")
    _publish()
    return decision


def resident_fallback(provider: str) -> Optional[str]:
    """A model that is *already* in memory, for when a reservation was refused.

    Being told "no" must not make Dexter go quiet: it answers on what is loaded
    (usually the pinned 3B) and says who has the card. Returns None when
    nothing is resident — then there is genuinely nothing to answer with, and
    saying so is the honest move.
    """
    if provider != "ollama":
        return None
    try:
        loaded = llm_router.loaded_models()
    except Exception:
        return None
    if not loaded:
        return None
    _, fast = _tier_models()
    for m in loaded:
        if fast and _same_model(m.get("name"), fast):
            return m.get("name")
    return loaded[0].get("name")


def _resident(model: str) -> bool:
    try:
        return any(_same_model(m.get("name"), model) for m in llm_router.loaded_models())
    except Exception:
        return False


def _observe_load(model: str) -> bool:
    """Wait (briefly) for Ollama to admit the model is in memory."""
    deadline = time.time() + _OBSERVE_TIMEOUT
    while time.time() < deadline:
        if _resident(model):
            return True
        time.sleep(0.4)
    # `warm_model` returned 200 but /api/ps has not caught up. Believe the 200:
    # the alternative is refusing a turn on a model that is in fact loaded.
    return True


def _same_model(a: Optional[str], b: Optional[str]) -> bool:
    """`qwen2.5:7b` and `qwen2.5:7b-instruct-q4` name the same family to Ollama."""
    if not a or not b:
        return False
    a, b = a.strip().lower(), b.strip().lower()
    return a == b or a.split(":")[0] == b.split(":")[0]


# ───────────────────────── residency helpers ─────────────────────────


def keep_fast_warm(provider: str, model: Optional[str], keep_alive: str = "2h") -> None:
    """Re-pin the small model (Ollama resets its expiry to 5 min on every
    request). Throttled, and off the request thread — an instant reply is only
    instant when nothing has to load."""
    if provider != "ollama" or not model:
        return
    now = time.time()
    with _lock:
        if now - _last_warm.get("fast", 0.0) < 120:
            return
        _last_warm["fast"] = now
    threading.Thread(
        target=_warm_quietly, args=(provider, model, keep_alive),
        name="dexter-warm-fast", daemon=True,
    ).start()


def _warm_quietly(provider: str, model: str, keep_alive: str) -> None:
    """A re-pin of something already resident consumes nothing new, so it needs
    no reservation; a cold load does, and goes through `ensure`."""
    try:
        if _resident(model):
            llm_router.warm_model(provider, model, keep_alive)
            _mark_ours(model)
        else:
            ensure(model, "fast-model", provider=provider, keep_alive=keep_alive)
    except Exception:
        log.debug("steward: fast warm failed", exc_info=True)


def release_soon(provider: str, model: Optional[str], after: str = "30m") -> None:
    """Let a model leave memory once it has been idle for `after`."""
    if provider != "ollama" or not model:
        return
    threading.Thread(
        target=_release_after, args=(provider, model, after),
        name="dexter-release-model", daemon=True,
    ).start()


def _release_after(provider: str, model: str, after: str) -> None:
    try:
        llm_router.release_model(provider, model, after)
    except Exception:
        log.debug("steward: release_soon failed", exc_info=True)


def unload(provider: str, model: str) -> bool:
    """Drop a model from memory now."""
    try:
        ok = bool(llm_router.release_model(provider, model, "0"))
    except Exception:
        log.debug("steward: unload %s failed", model, exc_info=True)
        return False
    if ok:
        _forget_ours(model)
    return ok


# ───────────────────────── the shed ladder ─────────────────────────


def shed(level: str = "orange", reason: str = "", want_mib: int = 0) -> Dict[str, Any]:
    """Give memory back, cheapest thing first. Sentinel asks; Dexter obeys.

    Rungs, in order (a `red` level or a `Preempt` goes further down the ladder
    than an `orange` warning):

      1. the smart model                     — reloads in seconds when needed
      2. the fast model, on red only         — costs an instant first reply
      3. foreign Ollama models               — CaliberAI/MastGaane runners, JIT loads
      4. LM Studio (`lms unload --all`)      — only when we are told to manage it
      5. an engine Dexter itself started     — buzzcode's llama-server

    Freezing does not free VRAM on Windows, which is exactly why cooperative
    shedding has to come first: it is the only rung that actually returns
    memory without killing anyone's work.
    """
    started = time.time()
    level = (level or "orange").strip().lower()
    hard = level in ("red", "preempt", "critical")
    before = _resident_mib()
    steps: List[str] = []

    smart, fast = _tier_models()
    # Work out what is foreign *before* unloading anything: `unload` forgets the
    # model it dropped, which would make our own model look like someone else's
    # a moment later and get "unloaded" a second time.
    foreign = _foreign_models(also_ours=(smart, fast))

    if smart and unload("ollama", smart):
        steps.append(f"unloaded {smart}")

    if hard and fast and unload("ollama", fast):
        # The next simple turn is a second slower. That is a far better trade
        # than the whole machine paging, and VramRelief re-warms it.
        steps.append(f"unloaded {fast}")

    if hard or want_mib:
        for name in foreign:
            if unload("ollama", name):
                steps.append(f"unloaded {name} (not ours)")

    if hard and MANAGE_LMSTUDIO and _lms_unload():
        steps.append("unloaded LM Studio's models")

    if hard:
        try:
            from code_agent import code_agent

            if code_agent.stop_engine():
                steps.append("stopped the local model server Dexter had started")
        except Exception:
            log.debug("steward: engine stop skipped", exc_info=True)

    freed = max(0, before - _resident_mib())
    elapsed = time.time() - started
    summary = {
        "level": level, "reason": reason, "steps": steps,
        "freedMib": freed, "elapsedMs": int(elapsed * 1000),
        "at": time.time(),
    }
    with _lock:
        _last_shed.clear()
        _last_shed.update(summary)
    if steps:
        log.warning("steward: shed on %s (%s) freed %s MiB in %.0f ms: %s",
                    level, reason or "no reason given", freed, elapsed * 1000, "; ".join(steps))
        print(f"[Steward] Shed on {level}: {'; '.join(steps)} — freed {freed} MiB in {elapsed:.1f}s.")
    _publish(summary)
    return summary


def _tier_models() -> tuple:
    """(smart, fast) as the router currently has them.

    Imported lazily because `router` imports this module: the steward is the
    door, so it must not be behind the thing that walks through it. Reading the
    router's constants rather than the environment means a Settings change
    takes effect here too.
    """
    try:
        import router

        # The router's value wins even when it is empty: "no fast model
        # configured" is a real answer, and falling through to the environment
        # would re-introduce a model the owner switched off.
        return (str(getattr(router, "SMART_MODEL", "") or "").strip(),
                str(getattr(router, "FAST_MODEL", "") or "").strip())
    except Exception:
        return (os.getenv("DEXTER_SMART_MODEL", "").strip(),
                os.getenv("DEXTER_FAST_MODEL", "").strip())


def _resident_mib() -> int:
    try:
        return sum(int(m.get("vramMb") or 0) for m in llm_router.loaded_models())
    except Exception:
        return 0


def _foreign_models(also_ours: tuple = ()) -> List[str]:
    """Resident Ollama models Dexter did not warm — someone else's JIT load.

    `also_ours` is the configured tier, which is ours by definition even if this
    process never warmed it (Dexter restarted while the model stayed resident).
    """
    mine = [m for m in list(ours()) + list(also_ours) if m]
    out = []
    try:
        for m in llm_router.loaded_models():
            name = m.get("name")
            if name and not any(_same_model(name, one) for one in mine):
                out.append(name)
    except Exception:
        pass
    return out


def _lms_unload() -> bool:
    """`lms unload --all`. Only ever when DEXTER_MANAGE_LMSTUDIO says so —
    BuzzEdit may be the one that owns LM Studio on this machine."""
    try:
        res = subprocess.run(
            ["lms", "unload", "--all"], capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return res.returncode == 0
    except Exception:
        return False


def last_shed() -> Dict[str, Any]:
    with _lock:
        return dict(_last_shed)


def _publish(extra: Optional[Dict[str, Any]] = None) -> None:
    """Tell the UI something about memory changed (SSE `resources`, D5)."""
    try:
        payload: Dict[str, Any] = {"loaded": llm_router.loaded_models(), "ours": ours()}
        if extra:
            payload["shed"] = extra
        bus.publish("resources", payload)
    except Exception:
        pass


# ───────────────────────── Sentinel's events ─────────────────────────


def on_event(event: Dict[str, Any]) -> None:
    """Route one Sentinel event. Never raises — a handler bug must not kill the
    subscription, and the subscription must not kill Dexter."""
    if not isinstance(event, dict):
        return
    try:
        _dispatch(event)
    except Exception:
        log.exception("steward: event handling failed")


def _dispatch(event: Dict[str, Any]) -> None:
    if "VramTight" in event:
        tight = event["VramTight"] or {}
        level = str(tight.get("level", "")).lower()
        over = [str(c).lower() for c in tight.get("over_budget", [])]
        # Dropping several GB of model is not free: only on red, or when
        # Sentinel names us specifically.
        if level == "red" or "dexter" in over:
            shed(level or "red", f"VramTight, {int(tight.get('free', 0)) // _MIB} MiB free")
        return

    if "RamTight" in event:
        tight = event["RamTight"] or {}
        zone = str(tight.get("zone", "")).lower()
        ask = str(tight.get("ask", "")).lower()
        over = [str(c).lower() for c in tight.get("over_budget", [])]
        if ask == "shed" or zone == "red" or "dexter" in over:
            shed("red" if zone == "red" else "orange",
                 f"RamTight {zone or '?'}, {int(tight.get('phys_avail', 0)) // _MIB} MiB free")
        return

    if "Preempt" in event:
        pre = event["Preempt"] or {}
        # Someone with a higher priority needs what we booked. Shed and hand
        # the booking back inside the deadline rather than being taken from.
        shed("preempt", f"preempted by {pre.get('by') or 'a higher-priority client'}")
        release("preempted")
        bus.publish("resources", {"preempt": pre})
        return

    if "VramRelief" in event or "RamRelief" in event:
        # Nothing to do: the model comes back lazily on the next turn that needs
        # it, and re-warming into a card that just went green is how you make it
        # red again.
        bus.publish("resources", {"relief": True})
        return

    if "DiskTight" in event:
        disk = event["DiskTight"] or {}
        bus.publish("disk_tight", {
            "drive": disk.get("drive"),
            "freeBytes": disk.get("free_bytes"),
            "level": disk.get("level"),
            "ask": disk.get("ask"),
        })
        return

    if "CleanupDone" in event:
        done = event["CleanupDone"] or {}
        bus.publish("resources", {"cleanup": done})
        return

    if "Evicted" in event:
        ev = event["Evicted"] or {}
        name = ev.get("label") or ev.get("name") or "a background process"
        if _same_model(ev.get("name"), os.getenv("DEXTER_SMART_MODEL", "")):
            _forget_ours(str(ev.get("name")))
        bus.publish("evicted", {
            "pid": ev.get("pid"), "name": ev.get("name"),
            "label": ev.get("label"), "reason": ev.get("reason"),
            "text": f"Sentinel stopped {name} to free memory ({ev.get('reason') or 'memory pressure'}).",
        })
        return

    if "Frozen" in event or "Resumed" in event:
        bus.publish("resources", {"frozen": event.get("Frozen"), "resumed": event.get("Resumed")})
        return


# ───────────────────────── what the UI shows ─────────────────────────


def snapshot() -> Dict[str, Any]:
    """Everything `/api/dexter/resources` needs, with Sentinel absent handled."""
    client = _client()
    out: Dict[str, Any] = {
        "sentinel": {"connected": False, "version": None, "features": []},
        "vram": None, "ram": None, "disk": None,
        "holders": [], "frozen": [], "reservations": [],
        "trayAlive": None,
        "models": {"loaded": [], "ours": ours()},
        "lastShed": last_shed(),
        "budgets": {"vramMib": VRAM_BUDGET_MIB, "ramMib": RAM_BUDGET_MIB,
                    "engineVramMib": ENGINE_VRAM_MIB, "engineRamMib": ENGINE_RAM_MIB},
    }
    try:
        out["models"]["loaded"] = llm_router.loaded_models()
    except Exception:
        pass
    if client is None:
        return out

    gpu = None
    try:
        gpu = client.gpu_status()
    except Exception:
        gpu = None
    if not gpu:
        return out

    out["sentinel"] = {
        "connected": True,
        "version": client.version() or gpu.get("sentinel_version"),
        "features": client.features(),
        "registered": bool(client.token) or None,
    }
    primary = next((a for a in gpu.get("adapters", []) if a.get("is_primary")), None)
    if primary:
        out["vram"] = {
            "name": primary.get("name"),
            "totalMib": int(primary.get("total") or 0) // _MIB,
            "usedMib": int(primary.get("used") or 0) // _MIB,
            "freeMib": int(primary.get("free") or 0) // _MIB,
            "zone": gpu.get("zone"),
        }
    out["holders"] = [
        {"name": p.get("name"), "pid": p.get("pid"),
         "vramMib": int(p.get("vram_bytes") or 0) // _MIB, "frozen": bool(p.get("frozen"))}
        for p in gpu.get("top", []) if p.get("valid")
    ][:6]
    out["frozen"] = gpu.get("frozen", [])
    out["reservations"] = gpu.get("reservations", [])
    if "tray_alive" in gpu:
        out["trayAlive"] = bool(gpu.get("tray_alive"))

    ram = None
    try:
        ram = client.ram_status()
    except Exception:
        ram = None
    if ram:
        out["ram"] = {
            "zone": ram.get("zone"),
            "totalMib": int(ram.get("total") or 0) // _MIB,
            "availMib": int(ram.get("avail") or 0) // _MIB,
            "commitMib": int(ram.get("commit_total") or 0) // _MIB,
            "commitLimitMib": int(ram.get("commit_limit") or 0) // _MIB,
            "top": ram.get("top") or [],
        }
    else:
        out["ram"] = _local_ram()

    if client.supports("disk"):
        try:
            disk = client.disk_status()
        except Exception:
            disk = None
        if disk:
            out["disk"] = disk
    if out["disk"] is None:
        out["disk"] = _local_disk()
    return out


def _local_ram() -> Optional[Dict[str, Any]]:
    """A v1 Sentinel has no `RamStatus`; psutil still knows the numbers, and a
    blank gauge would read as "Dexter is broken" rather than "the guardian is old"."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {
            "zone": "Green" if vm.percent < 75 else ("Yellow" if vm.percent < 90 else "Red"),
            "totalMib": vm.total // _MIB, "availMib": vm.available // _MIB,
            "commitMib": None, "commitLimitMib": None, "top": [], "source": "local",
        }
    except Exception:
        return None


def _local_disk() -> Optional[Dict[str, Any]]:
    try:
        import shutil as _shutil

        drives = []
        for letter in (os.getenv("DEXTER_DISK_DRIVES") or "C:,B:").split(","):
            letter = letter.strip()
            if not letter or not os.path.exists(letter + "\\"):
                continue
            usage = _shutil.disk_usage(letter + "\\")
            free_gb = usage.free / (1024 ** 3)
            drives.append({
                "letter": letter, "total": usage.total, "free": usage.free,
                "zone": "Green" if free_gb >= 40 else ("Yellow" if free_gb >= 15 else "Red"),
            })
        return {"drives": drives, "last_cleanup": None, "pending": False, "source": "local"}
    except Exception:
        return None
