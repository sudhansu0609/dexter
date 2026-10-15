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
from typing import Any, Callable, Dict, List, Optional

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

#: Dexter is last in line for the card (owner's rule, 2026-10-15): an orange
#: caused by anyone else costs Dexter its smart model, not just a red. Off
#: restores "shed only on red, or when named over budget".
YIELD_ON_ORANGE = os.getenv("DEXTER_YIELD_ON_ORANGE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
#: ...and once the other app is done, the models it displaced come back.
RESTORE_AFTER_PRESSURE = os.getenv("DEXTER_RESTORE_AFTER_PRESSURE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
#: Pressure must have been gone this long before anything is reloaded.
RESTORE_SETTLE_SECS = float(os.getenv("DEXTER_RESTORE_SETTLE_SECS", "30") or 30)
RESTORE_POLL_SECS = float(os.getenv("DEXTER_RESTORE_POLL_SECS", "15") or 15)
#: A restore must leave the card below this. Orange (88%) is where Dexter
#: yields, so restoring past it would only shed again: a reload loop.
RESTORE_MAX_USED_PCT = float(os.getenv("DEXTER_RESTORE_MAX_USED_PCT", "85") or 85)
#: A displaced model not back within this long is forgotten; the next turn
#: that needs it loads it the ordinary way.
RESTORE_WINDOW_SECS = float(os.getenv("DEXTER_RESTORE_WINDOW_SECS", "10800") or 10800)


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
    # Which number this is matters when a ladder rung is refused: "5.5 GB" read
    # off the real file is a fact, and "6.1 GB" from the budget is a guess.
    log.debug(
        "steward: %s estimated at %s MiB + %s overhead (%s)",
        model, size if size else VRAM_BUDGET_MIB, RESERVE_OVERHEAD_MIB,
        "Ollama's own file size" if size else f"the configured budget ({provider})",
    )
    return int((size or VRAM_BUDGET_MIB) + RESERVE_OVERHEAD_MIB)


def ram_estimate(model: Optional[str], provider: str = "ollama") -> int:
    """Host memory the load itself needs, in MiB (never below a small floor)."""
    weights = vram_estimate(model, provider) - RESERVE_OVERHEAD_MIB
    return max(512, int(weights * _RAM_FRACTION))


def file_size_estimate(model: Optional[str], provider: str = "ollama") -> int:
    """The model's own weights in MiB, without the reservation overhead."""
    return max(0, vram_estimate(model, provider) - RESERVE_OVERHEAD_MIB)


# ───────────────────────── where a model runs (v13, D4) ─────────────────────
#
# A third place to run. Ollama loads a model wherever `options.num_gpu` says:
# 0 puts the whole thing in system RAM (measured on this machine: a 3B at
# 24 tok/s and 0 MiB of VRAM), N splits it. A later request *without* the option
# keeps using the instance that is already there, so the placement is decided at
# load time and holds until the next load. That is what lets the fast model keep
# answering at about a fifth of its speed while a render owns the card, instead
# of not answering at all.

PLACES = ("gpu", "cpu", "split")
#: A model bigger than this never runs on the CPU. The 12B manages 8 tok/s in
#: RAM, which is not an assistant; below about 5 GB it is merely slower.
CPU_RUNG_MAX_MIB = int(os.getenv("DEXTER_CPU_RUNG_MAX_MIB", "5200") or 5200)
#: Weights in RAM cost more than the file: the runtime's own buffers ride along.
_CPU_RAM_FRACTION = 1.15
#: VRAM left unclaimed when computing a partial split, so the layers we do put
#: on the card have somewhere to put their KV cache.
SPLIT_HEADROOM_MIB = int(os.getenv("DEXTER_SPLIT_HEADROOM_MIB", "1024") or 1024)
#: Try a partial split for the fast model when a full GPU load is refused.
SPLIT_FAST = os.getenv("DEXTER_SPLIT_FAST", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def fits_on_cpu(model: Optional[str], provider: str = "ollama") -> bool:
    """Small enough to be worth running in RAM at all."""
    size = file_size_estimate(model, provider)
    return 0 < size <= CPU_RUNG_MAX_MIB


def _layer_count(model: Optional[str]) -> int:
    """Transformer blocks in `model`, per Ollama's `/api/show`. 0 when unknown."""
    try:
        return max(0, int(llm_router.model_layers(model or "")))
    except Exception:
        return 0


def split_layers(model: Optional[str], free_mib: int, provider: str = "ollama") -> int:
    """How many of `model`'s layers fit in `free_mib` of VRAM.

    `(free - headroom) / (file size / layer count)`, floored, and never more
    layers than the model has. 0 means "do not try": either Ollama could not say
    how many blocks the model has, or there is not room for a single layer.
    """
    layers = _layer_count(model)
    size = file_size_estimate(model, provider)
    if layers <= 0 or size <= 0:
        return 0
    per_layer = size / float(layers)
    usable = int(free_mib or 0) - SPLIT_HEADROOM_MIB
    if usable <= 0 or per_layer <= 0:
        return 0
    return max(0, min(layers, int(usable // per_layer)))


def _placement(
    model: Optional[str], place: str, provider: str = "ollama", free_mib: int = 0,
) -> Dict[str, Any]:
    """`{vram_mib, ram_mib, options, place, layers}` for one way of loading `model`.

    The reservation follows the placement rather than the other way round: a CPU
    load consumes no VRAM and a lot of RAM, and asking Sentinel for the wrong one
    is how a machine ends up paging with a green GPU gauge.
    """
    size = file_size_estimate(model, provider)
    if place == "cpu":
        return {
            "place": "cpu", "vram_mib": 0,
            "ram_mib": max(512, int(size * _CPU_RAM_FRACTION)),
            "options": {"num_gpu": 0}, "layers": 0,
        }
    if place == "split":
        total = _layer_count(model)
        layers = split_layers(model, free_mib, provider) if total else 0
        if layers <= 0:
            return {"place": "split", "vram_mib": 0, "ram_mib": 0, "options": None, "layers": 0}
        on_card = int(size * (layers / float(total)))
        return {
            "place": "split", "vram_mib": on_card + SPLIT_HEADROOM_MIB,
            "ram_mib": max(512, int((size - on_card) * _CPU_RAM_FRACTION)),
            "options": {"num_gpu": layers}, "layers": layers,
        }
    return {
        "place": "gpu", "vram_mib": vram_estimate(model, provider),
        "ram_mib": ram_estimate(model, provider), "options": None, "layers": 0,
    }


# ───────────────────────── bookkeeping ─────────────────────────

_lock = threading.RLock()
#: Models this process asked Ollama to hold. Anything resident that is *not*
#: here belongs to CaliberAI, MastGaane or a stray JIT load, and is rung 3 of
#: the shed ladder.
_ours: Dict[str, float] = {}
#: Where this process last put each model (v13, P3.1). Ollama's `/api/ps` says
#: how much of a model is on the card, which is the truth; this says what was
#: *asked* for, which is what the log lines and the panel explain.
_places: Dict[str, str] = {}
_last_warm: Dict[str, float] = {}
_last_shed: Dict[str, Any] = {}
#: The rung the last escalated turn actually ran on, and why (v12, P3.3).
_last_ladder: Dict[str, Any] = {}
#: Set by Sentinel's `VramRelief`. `make_room` waits on it rather than sleeping
#: the full grace period for memory that came back in 200 ms.
_relief = threading.Event()
#: True only between a granted Reserve and its Release, so two turns racing
#: cannot both think they hold the booking.
_booked = False
#: Models a shed took off the card, waiting to come back:
#: name -> {"role": "fast"|"smart", "mib": its real VRAM, "at": when}.
_displaced: Dict[str, Dict[str, Any]] = {}
#: When Dexter last shed for pressure; restoring waits for this to go quiet.
_last_pressure = 0.0
_restore_wake = threading.Event()
_restorer: Optional[threading.Thread] = None


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
        _places.pop(model, None)


def _note_place(model: str, place: str) -> None:
    with _lock:
        _places[model] = place


def place_of(model: Optional[str]) -> str:
    """Where this process last loaded `model` ("gpu" / "cpu" / "split" / "")."""
    if not model:
        return ""
    with _lock:
        for name, place in _places.items():
            if _same_model(name, model):
                return place
    return ""


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
    place: str = "gpu",
    free_mib: int = 0,
    known_place: Optional[str] = None,
) -> Decision:
    """Get `model` resident, with Sentinel's permission. The only load path.

    Returns a granted `Decision` when the model is loaded (or when there is
    nothing to load — a provider Dexter does not manage serves whatever it
    serves and Dexter has no lever on it), and a refused one carrying the
    blockers otherwise. **A refusal never loads anything.**

    `provider="lmstudio"` gets the same reserve/load/observe/release shape as
    Ollama when `MANAGE_LMSTUDIO` is on (see `_ensure_lmstudio`) — LM Studio's
    own JIT loader is what used to leave the fast model's first reply waiting
    on a cold load every time Dexter started.

    `place` says *where* (v13, D4/P3.1): "gpu" as always, "cpu" reserves RAM
    only and loads with `options.num_gpu: 0`, "split" puts as many layers on the
    card as `free_mib` allows and the rest in RAM. The placement is part of the
    reservation, not an afterthought — booking VRAM for a model that is about to
    be loaded into RAM would refuse the next claimant for memory nobody used.
    """
    if provider == "lmstudio" and MANAGE_LMSTUDIO and model:
        return _ensure_lmstudio(model, purpose)

    if provider != "ollama" or not model:
        # The llama-server always holds its own model, and so does LM Studio
        # when nobody has told Dexter to manage it (`DEXTER_MANAGE_LMSTUDIO=0`
        # — BuzzEdit may be the one that owns it here): nothing to reserve and
        # nothing to warm.
        return Decision(True, purpose, model=model, provider=provider, loaded=False,
                        details={"absent": True, "reason": "provider manages its own residency"})

    # `known_place=""` is a caller saying "I have just unloaded it myself" —
    # `/api/ps` lags behind a load and an unload alike (that is why
    # `_observe_load` exists), and a second unload of a model that is already
    # gone is a wasted call at the worst possible moment.
    current = _resident_place(model) if known_place is None else known_place
    if current and not _needs_move(current, place):
        # Already in memory, and in a place worth keeping: no reservation,
        # because nothing new is consumed.
        _mark_ours(model)
        return Decision(True, purpose, model=model, provider=provider, loaded=True,
                        reason="already resident")

    plan = _placement(model, place, provider, free_mib)
    if place == "split" and not plan["options"]:
        return Decision(False, purpose, model=model, provider=provider,
                        reason="no layers fit on the card",
                        details={"place": "split", "free_mib": free_mib})

    vram, ram = int(plan["vram_mib"]), int(plan["ram_mib"])
    decision = reserve(purpose, vram, ram)
    decision.model, decision.provider = model, provider
    decision.details = {**dict(decision.details), "place": plan["place"]}
    if not decision.granted:
        print(f"[Steward] {purpose}: refused {vram} MiB VRAM + {ram} MiB RAM for "
              f"{model} on the {plan['place'].upper()} — {decision.reason}")
        return decision

    if plan["place"] != "cpu":
        # One model at a time: the grant stands, so the other tier goes first.
        make_way(model, provider)

    started = time.time()
    ok = False
    try:
        if current:
            # Ollama keeps a model where it was put, so a move is an unload and
            # a fresh load — and only ever after the memory for the new place
            # has been granted.
            unload(provider, model)
        ok = bool(llm_router.warm_model(provider, model, keep_alive, options=plan["options"]))
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
        _note_place(model, plan["place"])
        where = {"gpu": "on the GPU", "cpu": "in RAM",
                 "split": f"split, {plan['layers']} layers on the card"}[plan["place"]]
        print(f"[Steward] {purpose}: {model} resident {where} in {time.time() - started:.1f}s "
              f"({vram} MiB VRAM + {ram} MiB RAM booked, released).")
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
    _smart, fast, _fallback = _tier_models()
    for m in loaded:
        if fast and _same_model(m.get("name"), fast):
            return m.get("name")
    # No fast model in memory: any resident rung beats going quiet.
    for rung in _smart_rungs():
        for m in loaded:
            if _same_model(m.get("name"), rung):
                return m.get("name")
    return loaded[0].get("name")


def _resident(model: str) -> bool:
    try:
        return any(_same_model(m.get("name"), model) for m in llm_router.loaded_models())
    except Exception:
        return False


def _resident_place(model: str) -> str:
    """Where `model` actually is right now, per Ollama: "gpu"/"cpu"/"split"/""."""
    try:
        for m in llm_router.loaded_models():
            if _same_model(m.get("name"), model):
                return str(m.get("place") or ("gpu" if m.get("onGpu") else "cpu"))
    except Exception:
        pass
    return ""


def _needs_move(current: str, wanted: str) -> bool:
    """Is it worth unloading and reloading to get from `current` to `wanted`?

    Deliberately asymmetric. A model Ollama chose to split by itself counts as
    "on the card" and is left alone — treating a partial offload as the wrong
    place would have the keep-warm loop reloading the fast model every two
    minutes for ever. Only a model sitting entirely in RAM is worth moving back,
    and only a model with any VRAM at all is worth moving out of the way.
    """
    if not current:
        return False
    if wanted == "cpu":
        return current != "cpu"
    return current == "cpu"


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


# ───────────────────────── LM Studio residency ─────────────────────────
#
# Ollama's `warm_model` is an HTTP call whose 200 already means "loading
# started"; LM Studio has no such endpoint, so residency is asked for with the
# `lms` CLI and *confirmed* by polling `/v1/models` — the loaded models it
# actually reports (`data[].id`). Reached only when `MANAGE_LMSTUDIO` says
# Dexter, not BuzzEdit, owns this LM Studio.

#: Test seam: the tests replace this so nothing real is spawned.
_lms_run: Callable[[List[str]], Any] = lambda args: subprocess.run(  # noqa: E731
    args, capture_output=True, text=True, timeout=60,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)
#: Test seam: the tests replace this so the poll never touches the network.
_lms_probe: Callable[[], List[str]] = lambda: _lmstudio_loaded_ids_http()  # noqa: E731


def _lmstudio_loaded_ids_http() -> List[str]:
    """Model ids LM Studio currently reports resident, via its `/v1/models`."""
    try:
        import llm_boot
        import requests

        res = requests.get(llm_boot._lmstudio_probe_url(), timeout=3)
        if res.status_code != 200:
            return []
        return [str(m.get("id")) for m in (res.json().get("data") or []) if m.get("id")]
    except Exception:
        return []


def _lms_same(a: Optional[str], b: Optional[str]) -> bool:
    """LM Studio's id and Dexter's model key usually match exactly; when the id
    carries a publisher prefix (`lmstudio-community/...`) only the tail has to
    agree — the same tolerance `_same_model` gives Ollama's tags."""
    if not a or not b:
        return False
    a, b = a.strip().lower(), b.strip().lower()
    return a == b or a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


def _lmstudio_resident(model: str) -> bool:
    try:
        ids = _lms_probe()
    except Exception:
        ids = []
    return any(_lms_same(i, model) for i in ids or [])


def _observe_lmstudio_load(model: str) -> bool:
    """Wait (briefly) for `/v1/models` to admit the model. Unlike Ollama's
    `_observe_load`, this does not assume success on timeout: `lms load`'s exit
    code is not as trustworthy as Ollama's 200, so silence here means "no"."""
    deadline = time.time() + _OBSERVE_TIMEOUT
    while True:
        if _lmstudio_resident(model):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.4)


def _lms_load(model: str) -> bool:
    """`lms load <key> --gpu max -y`. Only ever called after Sentinel has
    granted the reservation — a refusal must never reach here."""
    try:
        import llm_boot

        exe = llm_boot.lms_exe() or "lms"
        res = _lms_run([exe, "load", model, "--gpu", "max", "-y"])
        return getattr(res, "returncode", 1) == 0
    except Exception:
        log.debug("steward: lms load %s failed", model, exc_info=True)
        return False


def _lms_unload_one(model: str) -> bool:
    """`lms unload <key>`, for the shed ladder's per-model offload path."""
    try:
        import llm_boot

        exe = llm_boot.lms_exe() or "lms"
        res = _lms_run([exe, "unload", model])
        return getattr(res, "returncode", 1) == 0
    except Exception:
        log.debug("steward: lms unload %s failed", model, exc_info=True)
        return False


def _ensure_lmstudio(model: str, purpose: str) -> Decision:
    """`ensure()`'s LM Studio branch: the same reserve/load/observe/release
    shape Ollama gets, with `lms` standing in for the HTTP API. Only reached
    when `MANAGE_LMSTUDIO` is on; a refusal here loads nothing, same contract.
    """
    if _lmstudio_resident(model):
        _mark_ours(model)
        return Decision(True, purpose, model=model, provider="lmstudio", loaded=True,
                        reason="already resident")

    vram = vram_estimate(model, provider="lmstudio")
    ram = ram_estimate(model, provider="lmstudio")
    decision = reserve(purpose, vram, ram)
    decision.model, decision.provider = model, "lmstudio"
    decision.details = {**dict(decision.details), "place": "gpu"}
    if not decision.granted:
        print(f"[Steward] {purpose}: refused {vram} MiB VRAM + {ram} MiB RAM for "
              f"{model} on LM Studio — {decision.reason}")
        return decision

    started = time.time()
    ok = False
    try:
        ok = _lms_load(model)
    except Exception as exc:
        log.warning("steward: loading %s on LM Studio failed: %s", model, exc)
    finally:
        # Released once the load is *observed*, or straight away when it failed
        # — same rule as the Ollama path, for the same reason.
        if ok:
            ok = _observe_lmstudio_load(model)
        release(f"{purpose}:{model}")

    decision.loaded = ok
    if ok:
        _mark_ours(model)
        _note_place(model, "gpu")
        print(f"[Steward] {purpose}: {model} resident on LM Studio in "
              f"{time.time() - started:.1f}s ({vram} MiB VRAM + {ram} MiB RAM booked, released).")
    else:
        decision.reason = decision.reason or "the model did not load"
        print(f"[Steward] {purpose}: {model} did not load on LM Studio ({time.time() - started:.1f}s).")
    _publish()
    return decision


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
    no reservation; a cold load does, and goes through `ensure`.

    A cold load that Sentinel refuses is not the end of the fast tier any more
    (v13, D4): as many layers as the free VRAM allows, then failing that, RAM.
    Each step is a fresh reservation, so a refusal still loads nothing at all.
    """
    try:
        where = _resident_place(model)
        if where and where != "cpu":
            llm_router.warm_model(provider, model, keep_alive)
            _mark_ours(model)
            return
        if where == "cpu":
            # Moved to RAM while something else had the card (P3.3). Ask for the
            # card back — never eagerly on a relief event, only here, on the next
            # turn that used it. A refusal leaves it answering from RAM.
            back = ensure(model, "fast-model", provider=provider, keep_alive=keep_alive)
            if not (back.granted and back.loaded):
                llm_router.warm_model(provider, model, keep_alive)
                _mark_ours(model)
            return
        decision = ensure(model, "fast-model", provider=provider, keep_alive=keep_alive)
        if decision.granted or provider != "ollama":
            return
        free = int(decision.details.get("free_mib") or 0)
        if SPLIT_FAST and free > SPLIT_HEADROOM_MIB:
            split = ensure(model, "fast-model", provider=provider,
                           keep_alive=keep_alive, place="split", free_mib=free)
            if split.granted and split.loaded:
                return
        if fits_on_cpu(model):
            ensure(model, "fast-model", provider=provider, keep_alive=keep_alive, place="cpu")
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
    if provider == "lmstudio":
        # BuzzEdit may be the one that owns LM Studio on this machine; without
        # the flag this is someone else's model to drop, not ours.
        if not MANAGE_LMSTUDIO:
            return False
        ok = _lms_unload_one(model)
        if ok:
            _forget_ours(model)
        return ok
    try:
        ok = bool(llm_router.release_model(provider, model, "0"))
    except Exception:
        log.debug("steward: unload %s failed", model, exc_info=True)
        return False
    if ok:
        _forget_ours(model)
    return ok


# ───────────────────────── one model at a time (v13.2) ─────────────────────────
#
# Until now a model left memory only when its keep-alive expired: the smart rung
# after 30 minutes, the fast one after two hours. So switching models *added* one
# instead of replacing it - a stepped-down ladder left both rungs resident, and
# changing the model in Settings left the old tag holding gigabytes for the rest
# of its keep-alive. The owner's ask: "if Dexter uses one model and switches to
# another, or I switch it, the previous model gets offloaded to save memory."
#
# Three rules, in increasing order of how much latency they cost:
#
#   * a model that has been **replaced** (the owner changed a tier, or a rung was
#     superseded) is unloaded at once - it is not coming back this turn, and
#     nothing is lost by dropping it;
#   * the **smart** rung leaves on a short keep-alive rather than half an hour,
#     and immediately when Sentinel says memory is not green;
#   * with `DEXTER_OFFLOAD_PREVIOUS=always`, only the model that just answered
#     stays resident at all - the strictest setting, and the one that makes a
#     chat turn after an action pay for a reload.
#
# Never anyone else's models: `retire` only ever drops what `_ours` says this
# process loaded.


def offload_mode() -> str:
    """'rungs' (default), 'always' or 'off'. Read live so Settings applies now."""
    value = (os.getenv("DEXTER_OFFLOAD_PREVIOUS", "rungs") or "rungs").strip().lower()
    return value if value in ("rungs", "always", "off") else "rungs"


#: One model at a time (`always`): how long the smart model may sit idle before
#: Dexter swaps back to the small one, so quick replies are instant again.
ONE_MODEL_IDLE_SECS = float(os.getenv("DEXTER_ONE_MODEL_IDLE_SECS", "600") or 600)
_settle_gen = 0


def one_at_a_time() -> bool:
    return offload_mode() == "always"


def _tier_resident() -> List[str]:
    """Dexter's own tier models that Ollama has in memory right now."""
    tier = [m for m in _tier_models() if m]
    try:
        names = [str(m.get("name")) for m in llm_router.loaded_models() if m.get("name")]
    except Exception:
        return []
    return [n for n in names if any(_same_model(n, t) for t in tier)]


def resident_smart() -> Optional[str]:
    """The smart rung that is loaded, when the fast model is not (else None).

    Under one-at-a-time this is the turn's cue to stay on the loaded model:
    trying the fast tier first would swap 12B -> e4b -> 12B on a hard question.
    """
    resident = _tier_resident()
    _smart, fast, _fallback = _tier_models()
    if fast and any(_same_model(n, fast) for n in resident):
        return None
    rungs = _smart_rungs()
    return next((n for n in resident if any(_same_model(n, r) for r in rungs)), None)


def make_way(model: Optional[str], provider: str = "ollama") -> List[str]:
    """One model at a time: unload Dexter's other loaded tier models before
    `model` loads. Never anyone else's models, and a no-op in other modes."""
    if provider != "ollama" or not model or not one_at_a_time():
        return []
    dropped = [n for n in _tier_resident()
               if not _same_model(n, model) and unload(provider, n)]
    if dropped:
        print(f"[Steward] One model at a time: offloaded {', '.join(dropped)} for {model}.")
        _publish()
    return dropped


def settle_to_fast(smart: Optional[str], after_secs: Optional[float] = None) -> None:
    """After a smart turn, swap back to the fast model once the smart one has
    been idle `after_secs`. A newer smart turn restarts the clock."""
    global _settle_gen
    if not smart or not one_at_a_time():
        return
    with _lock:
        _settle_gen += 1
        gen = _settle_gen
    wait = ONE_MODEL_IDLE_SECS if after_secs is None else after_secs
    threading.Thread(target=_settle_after, args=(gen, smart, wait),
                     name="dexter-settle-fast", daemon=True).start()


def _settle_after(gen: int, smart: str, wait: float) -> None:
    time.sleep(max(0.0, wait))
    with _lock:
        if gen != _settle_gen:
            return  # a newer smart turn owns the clock
    _smart, fast, _fallback = _tier_models()
    if not fast or not any(_same_model(n, smart) for n in _tier_resident()):
        return
    try:
        decision = ensure(fast, "settle-to-fast", provider="ollama", keep_alive=_fast_keep_alive())
        if decision.granted and decision.loaded:
            print(f"[Steward] {smart} idle for {wait / 60:.0f} min; back on {fast}.")
    except Exception:
        log.debug("steward: settling back to the fast model failed", exc_info=True)


def retire(
    keep: Any = (),
    provider: str = "ollama",
    why: str = "",
    include_fast: bool = True,
) -> List[str]:
    """Unload every model *we* loaded except the ones named in `keep`.

    Returns what was dropped, so the caller can say so. `include_fast=False`
    spares the fast tier, which is the 3 GB one that every chat turn needs.
    """
    if provider != "ollama" or offload_mode() == "off":
        return []
    keepers = {str(m).strip() for m in ([keep] if isinstance(keep, str) else keep) if m}
    _smart, fast, _fallback = _tier_models()
    if not include_fast and fast:
        keepers.add(fast)
    dropped: List[str] = []
    for model in ours():
        if any(_same_model(model, keeper) for keeper in keepers):
            continue
        if unload(provider, model):
            dropped.append(model)
    if dropped:
        print(f"[Steward] Offloaded {', '.join(dropped)}"
              + (f" ({why})" if why else "") + ".")
        _publish()
    return dropped


def replaced(old: Optional[str], new: Optional[str], provider: str = "ollama") -> bool:
    """The owner changed a model: drop the one they changed away from.

    Refuses when the old tag is still wanted by another tier - a fallback that
    equals the new smart model is still in use, and unloading it would only make
    the next turn reload it.
    """
    previous = (old or "").strip()
    if not previous or offload_mode() == "off":
        return False
    if _same_model(previous, (new or "").strip()):
        return False
    if any(_same_model(previous, tier) for tier in _tier_models() if tier):
        log.debug("steward: %s is still a tier, not unloading it", previous)
        return False
    if not any(_same_model(previous, m) for m in ours()):
        return False
    if unload(provider, previous):
        print(f"[Steward] {previous} is no longer a tier; offloaded it.")
        _publish()
        return True
    return False


def memory_is_tight() -> bool:
    """Sentinel's own verdict, for deciding whether to wait out a keep-alive."""
    try:
        state = snapshot()
    except Exception:
        return False
    for part in ("vram", "ram"):
        zone = str(((state.get(part) or {}).get("zone") or "")).lower()
        if zone in ("orange", "yellow", "red"):
            return True
    return False


# ───────────────────────── the shed ladder ─────────────────────────


def shed(
    level: str = "orange", reason: str = "", want_mib: int = 0, move_fast: bool = True,
) -> Dict[str, Any]:
    """Give memory back, cheapest thing first. Sentinel asks; Dexter obeys.

    Rungs, in order (a `red` level or a `Preempt` goes further down the ladder
    than an `orange` warning):

      1. the smart model                     — reloads in seconds when needed
      2. the fast model, on red only         — **moved** to RAM, not dropped (v13)
      3. foreign Ollama models               — CaliberAI/MastGaane runners, JIT loads
      4. LM Studio (`lms unload --all`)      — only when we are told to manage it
      5. an engine Dexter itself started     — buzzcode's llama-server

    `move_fast=False` skips the move at rung 2 and simply unloads: `make_room`
    wants the memory back *now* for an app the owner asked for, and spending the
    next four seconds loading 3 GB into RAM is the opposite of making room.

    Freezing does not free VRAM on Windows, which is exactly why cooperative
    shedding has to come first: it is the only rung that actually returns
    memory without killing anyone's work.
    """
    started = time.time()
    level = (level or "orange").strip().lower()
    hard = level in ("red", "preempt", "critical")
    before = _resident_mib()
    steps: List[str] = []

    _smart, fast, _fallback = _tier_models()
    rungs = _smart_rungs()
    # Work out what is foreign *before* unloading anything: `unload` forgets the
    # model it dropped, which would make our own model look like someone else's
    # a moment later and get "unloaded" a second time.
    foreign = _foreign_models(also_ours=tuple(rungs) + (fast,))
    # What is on the card *now*, so the restorer brings back only what this
    # shed actually took (an unload of a model that was never loaded still
    # "succeeds"), sized by what it really used rather than the estimate.
    on_card = _on_card_mib()

    # Every smart rung that is resident goes, not just the top one: with a 12B
    # and a 7B both in memory, giving back one of them still leaves the card
    # full (v12, P1.3).
    for rung in rungs:
        if unload("ollama", rung):
            steps.append(f"unloaded {rung}")

    if hard and fast and unload("ollama", fast):
        # v13, D4/P3.3: the card is needed, the assistant is not gone. Moving
        # the small model into RAM keeps quick answers coming at about a fifth
        # of the speed while a render owns the GPU; before this it simply
        # stopped answering until something freed up. Sentinel still decides:
        # if it will not grant the RAM, this is the plain unload it always was.
        steps.append(_give_up_the_fast_model(fast, reason) if move_fast else f"unloaded {fast}")

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

    taken = {r: "smart" for r in rungs if f"unloaded {r}" in steps}
    if hard and fast and any(s.startswith(("unloaded " + fast, "moved " + fast)) for s in steps):
        taken[fast] = "fast"
    _note_displaced({m: role for m, role in taken.items() if _on_card(on_card, m)}, on_card)

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


def _give_up_the_fast_model(fast: str, reason: str = "") -> str:
    """Move the fast model to RAM if we can, or leave it unloaded. One line back.

    It has already been dropped by the time this runs, which is the point: the
    VRAM is back either way, and what happens next only decides whether Dexter
    keeps answering. Nothing here may raise — this runs on Sentinel's event
    thread, and a failed move must cost the fast tier, never the subscription.
    """
    if not fits_on_cpu(fast):
        return f"unloaded {fast}"
    try:
        decision = ensure(
            fast, "fast-model-to-cpu", provider="ollama",
            keep_alive=_fast_keep_alive(), place="cpu", known_place="",
        )
    except Exception:
        log.debug("steward: moving %s to the CPU failed", fast, exc_info=True)
        return f"unloaded {fast}"
    if decision.granted and decision.loaded:
        log.warning("steward: moved %s to CPU (%s)", fast, reason or "memory pressure")
        print(f"[Steward] Moved {fast} to CPU — quick answers keep coming while the card is busy.")
        return f"moved {fast} to CPU"
    return f"unloaded {fast}"


def _fast_keep_alive() -> str:
    """The router's own keep-alive for the small model, read live like the tags."""
    try:
        import router

        return str(getattr(router, "FAST_KEEP_ALIVE", "2h") or "2h")
    except Exception:
        return os.getenv("DEXTER_FAST_KEEP_ALIVE", "2h").strip() or "2h"


def _tier_models() -> tuple:
    """(smart, fast, smart_fallback) as the router currently has them.

    Imported lazily because `router` imports this module: the steward is the
    door, so it must not be behind the thing that walks through it. Reading the
    router's constants rather than the environment means a Settings change
    takes effect here too.

    Since v12 the smart tier is a *ladder*, so this returns both of its rungs:
    either may be the one that is resident, and `shed` has to give back
    whichever actually is.
    """
    try:
        import router

        # The router's value wins even when it is empty: "no fast model
        # configured" is a real answer, and falling through to the environment
        # would re-introduce a model the owner switched off.
        return (str(getattr(router, "SMART_MODEL", "") or "").strip(),
                str(getattr(router, "FAST_MODEL", "") or "").strip(),
                str(getattr(router, "SMART_MODEL_FALLBACK", "") or "").strip())
    except Exception:
        return (os.getenv("DEXTER_SMART_MODEL", "").strip(),
                os.getenv("DEXTER_FAST_MODEL", "").strip(),
                os.getenv("DEXTER_SMART_MODEL_FALLBACK", "qwen2.5:7b").strip())


def _smart_rungs() -> List[str]:
    """Every configured smart rung, best first, without duplicates.

    Mirrors `router.smart_ladder`: with no top rung configured there is no
    ladder at all (the escalation uses the provider's own default model), so
    the fallback tag is not ours to unload either.
    """
    smart, _fast, fallback = _tier_models()
    if not smart:
        return []
    out: List[str] = [smart]
    if fallback and fallback.lower() != smart.lower():
        out.append(fallback)
    return out


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
        import llm_boot

        exe = llm_boot.lms_exe() or "lms"
        res = _lms_run([exe, "unload", "--all"])
        return getattr(res, "returncode", 1) == 0
    except Exception:
        return False


def last_shed() -> Dict[str, Any]:
    with _lock:
        return dict(_last_shed)


def note_ladder(info: Dict[str, Any]) -> None:
    """Remember which smart rung the last escalated turn ran on (v12, P3.3).

    The panel asks "what is Dexter thinking on, and why is it not the good
    one" — a question that has no answer anywhere else, because the rung is
    chosen per turn and nothing persists it.
    """
    with _lock:
        _last_ladder.clear()
        _last_ladder.update({**(info or {}), "at": time.time()})
    _publish()


def last_ladder() -> Dict[str, Any]:
    with _lock:
        return dict(_last_ladder)


# ───────────────────────── making room (v12, P2) ─────────────────────────

#: How long to wait for the memory we just gave back to show up as free. Ollama
#: unloads in well under a second; this is the ceiling, not the cost.
RELEASE_GRACE_SECS = float(os.getenv("DEXTER_RELEASE_GRACE_SECS", "10") or 10)


def blockers_are_ours(blockers: List[Dict[str, Any]]) -> bool:
    """Is *every* thing standing in the way one of Dexter's own models? (P2.1)

    This is the question behind "buzzcode does not run every time": when the
    only reason BuzzEdit cannot start is Dexter's own 7B sitting in the card,
    Dexter should give it back rather than report a refusal. When anyone else
    is in the list — a render, LM Studio, buzzcode's engine — it is Sentinel's
    call by priority, not Dexter's, so the answer is no.

    An empty list is also no: there was nothing to give back, so shedding would
    free nothing and only cost the next turn a reload.
    """
    if not blockers:
        return False
    return all(_blocker_is_ours(b) for b in blockers if isinstance(b, dict))


def _blocker_is_ours(blocker: Dict[str, Any]) -> bool:
    names = " ".join(
        str(blocker.get(key) or "") for key in ("client", "process", "label", "name")
    ).lower()
    if not names.strip() and blocker.get("pid") is None:
        return False
    # Sentinel labels our runner "Ollama runner (Dexter)"; the client name on a
    # reservation Dexter made is "dexter".
    if "dexter" in names:
        return True
    # buzzcode's engine is a llama-server too, and it is emphatically not ours.
    if "buzzcode" in names or "llama.cpp" in names:
        return False
    if "ollama" in names:
        return True
    if "llama-server" in names:
        # An Ollama runner is a llama-server whose parent is ollama.exe. Anyone
        # else's llama-server has a different parent, and stays theirs.
        return "ollama" in (_parent_name(blocker.get("pid")) or "")
    return False


def _parent_name(pid: Optional[Any]) -> str:
    """The lower-cased image name of `pid`'s parent, or "" when unknowable."""
    try:
        import psutil

        return str(psutil.Process(int(pid)).parent().name() or "").lower()
    except Exception:
        return ""


def make_room(
    purpose: str,
    label: str,
    vram_mib: int,
    ram_mib: Optional[int] = None,
    blockers: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Give our own models back so the owner's app can start (v12, D3/P2.2).

    Only when *we* are the blocker. Dexter never sheds someone else's work to
    make room — that is Sentinel's call, by priority — and it never launches
    anything on a refusal that still stands: the second `query` decides.

    Returns `{"tried", "granted", "decision", "freedMib", "gave", "note"}`.
    """
    if not blockers_are_ours(list(blockers or [])):
        return {"tried": False, "granted": False, "decision": None,
                "freedMib": 0, "gave": [], "note": ""}

    _relief.clear()
    # Not `move_fast`: the owner asked for an app, and reloading 3 GB into RAM
    # in the same breath delays the launch we are trying to allow.
    summary = shed("preempt", f"making room for {label}", move_fast=False)
    release("making room")

    # Wait for the memory to actually be free — Sentinel's `VramRelief`, or the
    # loaded-model list going quiet, whichever comes first.
    deadline = time.time() + RELEASE_GRACE_SECS
    while True:
        if not _resident_mib() or time.time() >= deadline:
            break
        if _relief.wait(0.4):
            break

    decision = query(purpose, vram_mib, ram_mib)
    gave = [
        step.split(" ", 1)[1] for step in summary.get("steps", [])
        if step.startswith("unloaded ") and "not ours" not in step
    ]
    note = ""
    if gave:
        freed = float(summary.get("freedMib") or 0) / 1024.0
        size = f" ({freed:.1f} GB)" if freed > 0 else ""
        note = f"Gave back {', '.join(gave)}{size} so {label} could start."
    return {
        "tried": True, "granted": decision.granted, "decision": decision,
        "freedMib": int(summary.get("freedMib") or 0), "gave": gave, "note": note,
    }


def _publish(extra: Optional[Dict[str, Any]] = None) -> None:
    """Tell the UI something about memory changed (SSE `resources`, D5)."""
    try:
        payload: Dict[str, Any] = {"loaded": llm_router.loaded_models(), "ours": ours()}
        if extra:
            payload["shed"] = extra
        bus.publish("resources", payload)
    except Exception:
        pass


# ───────────────────────── coming back (2026-10-15) ─────────────────────────
#
# Dexter yields first (above), and until now nothing ever came back: a fast
# model moved to RAM kept answering at a fifth of the speed for the rest of its
# two-hour keep-alive, long after the render that pushed it out had finished.
# Now the models a shed displaced are reloaded once the other app is done, and
# carefully, because loading into a card that just calmed down is how it gets
# busy again:
#
#   * no shed for RESTORE_SETTLE_SECS, and Sentinel's zone below orange;
#   * the card still under RESTORE_MAX_USED_PCT *after* the load, so a restore
#     can never be the cause of the next orange (and so of a reload loop);
#   * Sentinel grants the reservation — `ensure` stays the only load path.
#
# Fast model first, then the smart rung. A model the owner has since swapped
# out of the tier is forgotten, not restored.


#: An orange within this long of a smart turn is most likely that turn's own
#: load, and yielding would unload the model it is answering on. Red still sheds.
ORANGE_GRACE_SECS = float(os.getenv("DEXTER_ORANGE_GRACE_SECS", "90") or 90)


def _smart_in_use() -> bool:
    now = time.time()
    with _lock:
        stamps = [t for name, t in _ours.items()
                  if any(_same_model(name, r) for r in _smart_rungs())]
    return any(now - t < ORANGE_GRACE_SECS for t in stamps)


def _on_card_mib() -> Dict[str, int]:
    """Resident Ollama models that hold VRAM -> how much, per `/api/ps`."""
    out: Dict[str, int] = {}
    try:
        for m in llm_router.loaded_models():
            mib = int(m.get("vramMb") or 0)
            if m.get("name") and mib > 0:
                out[str(m["name"])] = mib
    except Exception:
        pass
    return out


def _on_card(on_card: Dict[str, int], model: str) -> int:
    return next((mib for name, mib in on_card.items() if _same_model(name, model)), 0)


def _note_displaced(taken: Dict[str, str], on_card: Dict[str, int]) -> None:
    global _last_pressure
    now = time.time()
    with _lock:
        _last_pressure = now
        for model, role in taken.items():
            _displaced[model] = {"role": role, "mib": _on_card(on_card, model), "at": now}
        pending = bool(_displaced)
    if pending:
        _start_restorer()


def displaced() -> Dict[str, Dict[str, Any]]:
    """What is waiting to come back (for the panel and the tests)."""
    with _lock:
        return {k: dict(v) for k, v in _displaced.items()}


def _start_restorer() -> None:
    global _restorer
    if not RESTORE_AFTER_PRESSURE:
        return
    with _lock:
        if _restorer is not None and _restorer.is_alive():
            return
        _restorer = threading.Thread(target=_restore_loop, name="dexter-restore", daemon=True)
        _restorer.start()


def _restore_loop() -> None:
    """Poll until everything displaced is back or forgotten. Woken early by
    `VramRelief`; never raises, because a failed restore must cost speed only."""
    while True:
        _restore_wake.wait(RESTORE_POLL_SECS)
        _restore_wake.clear()
        try:
            if restore_displaced():
                return
        except Exception:
            log.debug("steward: restore pass failed", exc_info=True)


def _card() -> Optional[Dict[str, Any]]:
    """Sentinel's view of the primary card: zone, used and total MiB."""
    client = _client()
    if client is None:
        return None
    try:
        gpu = client.gpu_status()
    except Exception:
        return None
    if not gpu:
        return None
    primary = next((a for a in gpu.get("adapters", []) if a.get("is_primary")), None)
    if not primary:
        return None
    return {"zone": str(gpu.get("zone") or "").lower(),
            "used": int(primary.get("used") or 0) // _MIB,
            "total": int(primary.get("total") or 0) // _MIB}


def _smart_keep_alive() -> str:
    try:
        import router

        return str(getattr(router, "SMART_KEEP_ALIVE", "30m") or "30m")
    except Exception:
        return os.getenv("DEXTER_SMART_KEEP_ALIVE", "30m").strip() or "30m"


def restore_displaced() -> bool:
    """One restore pass. True when nothing is left waiting (the loop may stop)."""
    smart, fast, fallback = _tier_models()
    tier = [m for m in (fast, smart, fallback) if m]
    now = time.time()
    with _lock:
        for model in list(_displaced):
            stale = now - float(_displaced[model].get("at") or now) > RESTORE_WINDOW_SECS
            if stale or not any(_same_model(model, t) for t in tier):
                _displaced.pop(model, None)
        pending = sorted(_displaced.items(), key=lambda kv: kv[1].get("role") != "fast")
        if one_at_a_time() and len(pending) > 1:
            # Only one comes back: the fast model when it was displaced. The
            # smart one returns on the next turn that needs it.
            for model, _info in pending[1:]:
                _displaced.pop(model, None)
            pending = pending[:1]
        quiet = now - _last_pressure
    if not pending:
        return True
    if quiet < RESTORE_SETTLE_SECS:
        return False

    card = _card()
    if card and card["zone"] in ("orange", "red"):
        return False
    for model, info in pending:
        need = int(info.get("mib") or 0) or vram_estimate(model)
        if card and card["total"] and (card["used"] + need) * 100.0 / card["total"] > RESTORE_MAX_USED_PCT:
            return False  # still no room; the smart rung is bigger, so stop here
        role = info.get("role")
        keep = _fast_keep_alive() if role == "fast" else _smart_keep_alive()
        decision = ensure(model, f"restore-{role}", provider="ollama", keep_alive=keep, place="gpu")
        if not (decision.granted and decision.loaded):
            return False
        with _lock:
            _displaced.pop(model, None)
        log.info("steward: restored %s (%s) after pressure", model, role)
        print(f"[Steward] Pressure is over — {model} is back on the GPU.")
        card = _card()
    _publish()
    with _lock:
        return not _displaced


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
        free = f"{int(tight.get('free', 0)) // _MIB} MiB free"
        if level == "red" or "dexter" in over:
            shed(level or "red", f"VramTight, {free}")
        elif level == "orange" and YIELD_ON_ORANGE and not _smart_in_use():
            # Dexter is last in line: someone else is filling the card, so the
            # smart model goes now rather than at red. The fast model stays —
            # an orange shed never touches it — and both come back afterwards.
            shed("orange", f"VramTight orange, {free}; yielding to other apps")
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
        # Nothing is loaded *here*: re-warming into a card that just went green
        # is how you make it red again (v12, D5). The restorer is only woken;
        # it still waits out RESTORE_SETTLE_SECS and checks the fit. The flag
        # also unblocks a `make_room` waiting for exactly this news.
        _relief.set()
        _restore_wake.set()
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
        if any(_same_model(ev.get("name"), rung) for rung in _smart_rungs()):
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
        "ladder": last_ladder(),
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
