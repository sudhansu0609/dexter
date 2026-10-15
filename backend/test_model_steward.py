"""
Tests for the model steward (roadmap v9, D2).

The steward exists so that exactly one place in Dexter can load or unload a
model, and that place always asks Sentinel first. Three things are worth
testing, in descending order of how expensive they are to get wrong:

1. **The monopoly holds.** A second module quietly calling `warm_model` is not
   a bug you notice — it is a machine that occasionally locks up. There is a
   grep test for it, because a code rule nothing checks is a comment.
2. **A refusal loads nothing**, and says who has the memory.
3. **The booking is always released** — after a successful load, after a failed
   one, and when we are preempted. A booking nobody releases starves the next
   claimant for its whole TTL.

Everything here runs against a fake: the real Sentinel is not installed as v2
yet, and the real Ollama is on the owner's machine with somebody's model in it.

Run:  python -m pytest backend/test_model_steward.py -q
"""

import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_steward


class FakeClient:
    """A Sentinel that answers whatever the test wants, and records the asks."""

    def __init__(self, granted=True, features=("ram_reserve", "query", "disk", "cleanup"),
                 details=None):
        self._granted = granted
        self._features = list(features)
        self._details = details or {}
        self.reserves = []
        self.queries = []
        self.releases = 0
        self.token = "tok"

    def features(self):
        return list(self._features)

    def supports(self, feature):
        return feature in self._features

    def version(self):
        return "2.0.0" if self._features else None

    def reserve(self, mib, ram_mib=None, for_pid=None, ttl_secs=None):
        self.reserves.append({"mib": mib, "ram_mib": ram_mib, "for_pid": for_pid, "ttl": ttl_secs})
        return self._granted, {"granted": self._granted, "free_mib": 1200,
                               "ram_free_mib": 2000, "reason": "not enough VRAM",
                               **self._details}

    def query(self, mib, ram_mib=None):
        self.queries.append({"mib": mib, "ram_mib": ram_mib})
        return self._granted, {"granted": self._granted, "free_mib": 1200, **self._details}

    def release(self):
        self.releases += 1

    def gpu_status(self):
        return None

    def ram_status(self):
        return None

    def disk_status(self):
        return None


@pytest.fixture()
def steward(monkeypatch):
    """A steward with no real Ollama and no real Sentinel behind it."""
    monkeypatch.setattr(model_steward.llm_router, "loaded_models", lambda: [])
    monkeypatch.setattr(model_steward, "MANAGE_LMSTUDIO", False)
    monkeypatch.setattr(model_steward, "vram_estimate", lambda model, provider="ollama": 6000)
    monkeypatch.setattr(model_steward, "ram_estimate", lambda model, provider="ollama": 2000)
    model_steward._booked = False
    model_steward._ours.clear()
    model_steward._last_shed.clear()
    model_steward._displaced.clear()
    monkeypatch.setattr(model_steward, "_last_pressure", 0.0)
    # No background restorer unless a test drives `restore_displaced` itself.
    monkeypatch.setattr(model_steward, "RESTORE_AFTER_PRESSURE", False)
    # The default offload mode, whatever the machine's .env says.
    monkeypatch.setenv("DEXTER_OFFLOAD_PREVIOUS", "rungs")
    return model_steward


def use(monkeypatch, client):
    monkeypatch.setattr(model_steward, "_client", lambda: client)
    return client


# ───────────────────────── the monopoly ─────────────────────────


def test_no_module_loads_a_model_behind_the_stewards_back():
    """The whole design in one assertion.

    `warm_model` / `release_model` / `set_keep_alive` may only be called by the
    steward and defined by `hybrid_llm`. If this fails, someone added a second
    door to the GPU — and the failure it causes in production is a hung machine
    at 2 a.m., not a red test.
    """
    backend = os.path.dirname(os.path.abspath(__file__))
    allowed = {"model_steward.py", "hybrid_llm.py"}
    call = re.compile(r"\.(warm_model|release_model|set_keep_alive)\s*\(")
    offenders = []

    for name in sorted(os.listdir(backend)):
        if not name.endswith(".py") or name in allowed or name.startswith("test_"):
            continue
        if name == "conftest.py":
            continue
        with open(os.path.join(backend, name), "r", encoding="utf-8", errors="ignore") as handle:
            for number, line in enumerate(handle, 1):
                if line.lstrip().startswith("#"):
                    continue
                if call.search(line):
                    offenders.append(f"{name}:{number}: {line.strip()}")

    assert offenders == [], (
        "these call the model residency API directly instead of going through "
        "model_steward:\n  " + "\n  ".join(offenders)
    )


# ───────────────────────── reserve / load / release ─────────────────────────


def test_a_granted_load_books_reserves_and_releases(steward, monkeypatch):
    client = use(monkeypatch, FakeClient(granted=True))
    warmed = []
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda p, m, k, options=None: warmed.append((p, m, k, options)) or True)
    # The load is "observed" the moment /api/ps lists it.
    monkeypatch.setattr(steward, "_observe_load", lambda model: True)

    decision = steward.ensure("qwen2.5:7b", "escalation", keep_alive="30m")

    assert decision.granted and decision.loaded
    # No `options`: the GPU is where Ollama puts a model when nobody says
    # otherwise, and saying so explicitly would be a second way to be wrong.
    assert warmed == [("ollama", "qwen2.5:7b", "30m", None)]
    assert client.reserves == [{"mib": 6000, "ram_mib": 2000, "for_pid": None, "ttl": 90}]
    assert client.releases == 1, "the booking must be handed back once the load is observed"
    assert steward.booked() is False


def test_a_failed_load_still_releases_the_booking(steward, monkeypatch):
    """Holding memory behind a model that never arrived starves everyone."""
    client = use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward.llm_router, "warm_model", lambda p, m, k, options=None: False)

    decision = steward.ensure("qwen2.5:7b", "escalation")

    assert decision.granted is True and decision.loaded is False
    assert client.releases == 1
    assert steward.booked() is False


def test_warm_model_raising_still_releases(steward, monkeypatch):
    client = use(monkeypatch, FakeClient(granted=True))

    def boom(*_a, **_k):
        raise RuntimeError("ollama fell over")

    monkeypatch.setattr(steward.llm_router, "warm_model", boom)
    decision = steward.ensure("qwen2.5:7b", "escalation")
    assert decision.loaded is False
    assert client.releases == 1


def test_a_refusal_loads_nothing_and_names_the_holder(steward, monkeypatch):
    client = use(monkeypatch, FakeClient(granted=False, details={
        "blockers": [{"process": "VrDeep", "pid": 33228, "vram_mib": 10240, "class": "batch"}],
    }))
    warmed = []
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda p, m, k, options=None: warmed.append(m) or True)

    decision = steward.ensure("qwen2.5:7b", "escalation")

    assert decision.granted is False
    assert warmed == [], "a refusal must never load"
    assert client.releases == 0, "nothing was booked, so nothing is released"
    message = decision.message()
    assert "VrDeep" in message and "10.0 GB" in message
    assert "pause batch jobs" in message.lower()


def test_a_refusal_blocked_by_the_coding_engine_offers_stopping_it(steward, monkeypatch):
    use(monkeypatch, FakeClient(granted=False, details={
        "blockers": [{"client": "buzzcode", "pid": 1, "vram_mib": 12400, "class": "batch"}],
    }))
    decision = steward.ensure("qwen2.5:7b", "escalation")
    assert "stop the coding engine" in decision.message().lower()


def test_an_already_resident_model_needs_no_reservation(steward, monkeypatch):
    """Re-using what is loaded consumes nothing new; booking for it would be a lie."""
    client = use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward.llm_router, "loaded_models",
                        lambda: [{"name": "qwen2.5:7b", "sizeMb": 5000, "vramMb": 5000, "onGpu": True}])
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda p, m, k, options=None: pytest.fail("must not reload a resident model"))

    decision = steward.ensure("qwen2.5:7b", "escalation")
    assert decision.granted and decision.loaded
    assert client.reserves == []


def test_a_non_ollama_provider_is_left_alone(steward, monkeypatch):
    """LM Studio and the llama-server hold their own model; Dexter has no lever."""
    client = use(monkeypatch, FakeClient(granted=True))
    decision = steward.ensure("whatever", "escalation", provider="lmstudio")
    assert decision.granted and decision.loaded is False
    assert client.reserves == []


def test_with_sentinel_off_everything_is_granted_and_nothing_is_booked(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_SENTINEL", "0")
    monkeypatch.setattr(steward.llm_router, "warm_model", lambda p, m, k, options=None: True)
    monkeypatch.setattr(steward, "_observe_load", lambda model: True)
    decision = steward.ensure("qwen2.5:7b", "escalation")
    assert decision.granted and decision.absent
    assert steward.booked() is False


# ───────────────────────── LM Studio residency ─────────────────────────
#
# `ensure()` used to return early for every non-Ollama provider, so a named LM
# Studio model never actually became resident and the first reply paid for
# LM Studio's own JIT load. These mock `lms` entirely — nothing here spawns a
# real process or touches the network.

_LMS_MODEL = "gemma-4-e4b-uncensored-hauhaucs-aggressive"


def test_ensure_lmstudio_loads_via_lms_and_confirms_residency(steward, monkeypatch):
    """The fix, end to end: a named LM Studio model becomes resident."""
    client = use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward, "MANAGE_LMSTUDIO", True)
    calls = []
    monkeypatch.setattr(steward, "_lms_load", lambda model: calls.append(model) or True)
    monkeypatch.setattr(steward, "_lmstudio_resident", lambda model: bool(calls))

    decision = steward.ensure(_LMS_MODEL, "startup", provider="lmstudio")

    assert calls == [_LMS_MODEL]
    assert decision.granted and decision.loaded
    assert client.reserves and client.releases == 1


def test_ensure_lmstudio_already_resident_skips_the_load(steward, monkeypatch):
    """`/v1/models` already lists it: granted+loaded, no reservation, no `lms load`."""
    client = use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward, "MANAGE_LMSTUDIO", True)
    monkeypatch.setattr(steward, "_lms_probe", lambda: [_LMS_MODEL])
    monkeypatch.setattr(steward, "_lms_load",
                        lambda model: pytest.fail("must not reload a resident model"))

    decision = steward.ensure(_LMS_MODEL, "startup", provider="lmstudio")

    assert decision.granted and decision.loaded
    assert client.reserves == []


def test_ensure_lmstudio_refusal_loads_nothing(steward, monkeypatch):
    """A Sentinel refusal must not run `lms load`."""
    monkeypatch.setattr(steward, "MANAGE_LMSTUDIO", True)
    use(monkeypatch, FakeClient(granted=False))
    calls = []
    monkeypatch.setattr(steward, "_lms_load", lambda model: calls.append(model) or True)
    monkeypatch.setattr(steward, "_lmstudio_resident", lambda model: False)

    decision = steward.ensure(_LMS_MODEL, "startup", provider="lmstudio")

    assert calls == []
    assert not decision.granted
    assert decision.loaded is False


def test_ensure_lmstudio_with_management_off_is_the_old_early_return(steward, monkeypatch):
    """`DEXTER_MANAGE_LMSTUDIO=0` (the fixture default) must not change behavior."""
    client = use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward, "MANAGE_LMSTUDIO", False)
    calls = []
    monkeypatch.setattr(steward, "_lms_load", lambda model: calls.append(model) or True)

    decision = steward.ensure(_LMS_MODEL, "startup", provider="lmstudio")

    assert calls == []
    assert decision.granted and decision.loaded is False
    assert decision.details.get("reason") == "provider manages its own residency"
    assert client.reserves == []


def test_unload_lmstudio_uses_lms_and_forgets_it(steward, monkeypatch):
    monkeypatch.setattr(steward, "MANAGE_LMSTUDIO", True)
    steward._mark_ours(_LMS_MODEL)
    calls = []
    monkeypatch.setattr(steward, "_lms_unload_one", lambda model: calls.append(model) or True)

    assert steward.unload("lmstudio", _LMS_MODEL) is True
    assert calls == [_LMS_MODEL]
    assert _LMS_MODEL not in steward.ours()


def test_unload_lmstudio_is_a_noop_when_management_is_off(steward, monkeypatch):
    monkeypatch.setattr(steward, "MANAGE_LMSTUDIO", False)
    calls = []
    monkeypatch.setattr(steward, "_lms_unload_one", lambda model: calls.append(model) or True)

    assert steward.unload("lmstudio", _LMS_MODEL) is False
    assert calls == []


# ───────────────────────── RAM admission ─────────────────────────


def test_ram_is_asked_for_and_the_old_service_simply_does_not_see_it(steward, monkeypatch):
    """`ram_mib` is passed to the client either way; the *client* is what drops
    it for a v1 service, which is where that decision belongs."""
    client = use(monkeypatch, FakeClient(granted=True, features=()))
    monkeypatch.setattr(steward.llm_router, "warm_model", lambda p, m, k, options=None: True)
    monkeypatch.setattr(steward, "_observe_load", lambda model: True)
    steward.ensure("qwen2.5:7b", "escalation")
    assert client.reserves[0]["ram_mib"] == 2000


def test_the_engine_booking_names_the_process_that_will_consume_it(steward, monkeypatch):
    client = use(monkeypatch, FakeClient(granted=True))
    steward.reserve("buzzcode-engine", 12500, 6500, for_pid=4711, ttl_secs=180)
    assert client.reserves == [{"mib": 12500, "ram_mib": 6500, "for_pid": 4711, "ttl": 180}]


# ───────────────────────── the shed ladder ─────────────────────────


def shed_recorder(monkeypatch, loaded=()):
    released = []
    monkeypatch.setattr(model_steward.llm_router, "release_model",
                        lambda p, m, after="0": released.append((m, after)) or True)
    monkeypatch.setattr(model_steward.llm_router, "loaded_models", lambda: list(loaded))
    return released


def test_orange_gives_up_only_the_smart_model(steward, monkeypatch):
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "")
    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b")
    released = shed_recorder(monkeypatch)
    summary = steward.shed("orange", "VramTight")
    assert [m for m, _ in released] == ["qwen2.5:7b"]
    assert summary["steps"] == ["unloaded qwen2.5:7b"]


def test_red_gives_up_the_small_model_and_anything_that_is_not_ours(steward, monkeypatch):
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "")
    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b")
    # A CaliberAI/MastGaane runner's model, which Dexter never warmed.
    loaded = [{"name": "llama3.1:8b", "sizeMb": 5000, "vramMb": 5000, "onGpu": True}]
    released = shed_recorder(monkeypatch, loaded=loaded)

    steward.shed("red", "VramTight red")

    assert [m for m, _ in released] == ["qwen2.5:7b", "qwen2.5:3b", "llama3.1:8b"]


def test_a_model_we_warmed_is_never_treated_as_foreign(steward, monkeypatch):
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "")
    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b")
    loaded = [{"name": "qwen2.5:3b", "sizeMb": 2500, "vramMb": 2500, "onGpu": True}]
    released = shed_recorder(monkeypatch, loaded=loaded)
    steward._mark_ours("qwen2.5:3b")

    steward.shed("red", "VramTight red")

    # Unloaded once, as the fast model - not a second time as "not ours".
    assert [m for m, _ in released] == ["qwen2.5:3b"]


def test_every_smart_rung_that_is_resident_is_given_back(steward, monkeypatch):
    """v12: with the 12B and the 7B both in memory, handing back one of them
    still leaves the card full — and the turn that follows is still refused."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "gemma-4-12b")
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "qwen2.5:7b")
    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b")
    loaded = [
        {"name": "gemma-4-12b", "sizeMb": 7000, "vramMb": 7000, "onGpu": True},
        {"name": "qwen2.5:7b", "sizeMb": 4900, "vramMb": 4900, "onGpu": True},
    ]
    released = shed_recorder(monkeypatch, loaded=loaded)

    summary = steward.shed("orange", "VramTight")

    assert [m for m, _ in released] == ["gemma-4-12b", "qwen2.5:7b"]
    assert summary["steps"] == ["unloaded gemma-4-12b", "unloaded qwen2.5:7b"]
    # Neither rung is "somebody else's JIT load" on the way past.
    assert not any("not ours" in step for step in summary["steps"])


def test_the_fallback_rung_is_not_ours_to_unload_without_a_top_rung(steward, monkeypatch):
    """Blank smart model = no ladder (the backend's own default answers), so
    the configured fallback tag may well be someone else's model."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "")
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "qwen2.5:7b")
    monkeypatch.setattr(router, "FAST_MODEL", "")
    released = shed_recorder(monkeypatch)
    steward.shed("orange", "VramTight")
    assert released == []


def test_resident_fallback_takes_a_smart_rung_when_the_fast_one_is_gone(steward, monkeypatch):
    import router

    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b")
    monkeypatch.setattr(router, "SMART_MODEL", "gemma-4-12b")
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "qwen2.5:7b")
    monkeypatch.setattr(steward.llm_router, "loaded_models", lambda: [
        {"name": "llama3.1:8b"}, {"name": "qwen2.5:7b"},
    ])
    assert steward.resident_fallback("ollama") == "qwen2.5:7b"


def test_lm_studio_is_only_touched_when_we_are_told_to_manage_it(steward, monkeypatch):
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "")
    monkeypatch.setattr(router, "FAST_MODEL", "")
    shed_recorder(monkeypatch)
    called = []
    monkeypatch.setattr(steward, "_lms_unload", lambda: called.append(True) or True)

    monkeypatch.setattr(steward, "MANAGE_LMSTUDIO", False)
    steward.shed("red", "x")
    assert called == [], "BuzzEdit may own LM Studio; do not take its model"

    monkeypatch.setattr(steward, "MANAGE_LMSTUDIO", True)
    steward.shed("red", "x")
    assert called == [True]


# ───────────────────────── events ─────────────────────────


def test_events_that_should_change_nothing_change_nothing(steward, monkeypatch):
    """Dropping several GB of model is not free; it must not happen on a twitch."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    released = shed_recorder(monkeypatch)
    for event in (
        {"VramTight": {"level": "yellow", "over_budget": ["buzzcode"], "free": 3}},
        {"VramRelief": {"free": 9}},
        {"RamTight": {"zone": "Yellow", "ask": "hold", "over_budget": []}},
        {"Heartbeat": {"every_secs": 30}},
        "not even a dict",
        None,
    ):
        steward.on_event(event)
    assert released == []


# ───────────── last in line, and coming back (2026-10-15) ─────────────


def _tier(monkeypatch, smart="gemma-4-12b", fast="gemma4:e4b", fallback=""):
    import router

    monkeypatch.setattr(router, "SMART_MODEL", smart)
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", fallback)
    monkeypatch.setattr(router, "FAST_MODEL", fast)


def test_an_orange_from_another_app_costs_dexter_its_smart_model(steward, monkeypatch):
    """Dexter is last in line: someone else's orange is enough, not only red."""
    _tier(monkeypatch)
    released = shed_recorder(monkeypatch)
    steward.on_event({"VramTight": {"level": "orange", "over_budget": ["buzzcode"], "free": 3}})
    assert [m for m, _ in released] == ["gemma-4-12b"]  # the fast model stays


def test_an_orange_right_after_a_smart_turn_is_probably_ours(steward, monkeypatch):
    """Unloading the model a turn is answering on would only make it reload."""
    _tier(monkeypatch)
    released = shed_recorder(monkeypatch)
    steward._ours["gemma-4-12b"] = time.time()
    steward.on_event({"VramTight": {"level": "orange", "over_budget": [], "free": 3}})
    assert released == []


def test_yielding_on_orange_can_be_switched_off(steward, monkeypatch):
    _tier(monkeypatch)
    released = shed_recorder(monkeypatch)
    monkeypatch.setattr(steward, "YIELD_ON_ORANGE", False)
    steward.on_event({"VramTight": {"level": "orange", "over_budget": ["buzzcode"], "free": 3}})
    assert released == []


def test_a_shed_remembers_only_what_was_really_on_the_card(steward, monkeypatch):
    _tier(monkeypatch)
    loaded = [{"name": "gemma4:e4b", "vramMb": 3100, "onGpu": True}]  # the 12B was not loaded
    shed_recorder(monkeypatch, loaded=loaded)
    steward.shed("red", "VramTight red", move_fast=False)
    waiting = steward.displaced()
    assert list(waiting) == ["gemma4:e4b"]
    assert waiting["gemma4:e4b"]["role"] == "fast" and waiting["gemma4:e4b"]["mib"] == 3100


def _displace(steward, quiet_for=600.0, **models):
    now = time.time()
    for name, (role, mib) in models.items():
        steward._displaced[name] = {"role": role, "mib": mib, "at": now - quiet_for}
    steward._last_pressure = now - quiet_for


def _record_ensure(steward, monkeypatch, ok=True):
    calls = []

    def fake(model, purpose, provider="ollama", keep_alive="30m", place="gpu", **_):
        calls.append((model, place))
        return model_steward.Decision(ok, purpose, model=model, loaded=ok)

    monkeypatch.setattr(steward, "ensure", fake)
    monkeypatch.setattr(steward, "_publish", lambda extra=None: None)
    return calls


def test_once_the_other_app_is_done_the_fast_model_comes_back_first(steward, monkeypatch):
    _tier(monkeypatch)
    _displace(steward, **{"gemma-4-12b": ("smart", 8000), "gemma4:e4b": ("fast", 3100)})
    monkeypatch.setattr(steward, "_card", lambda: {"zone": "green", "used": 2000, "total": 16000})
    calls = _record_ensure(steward, monkeypatch)
    assert steward.restore_displaced() is True
    assert calls == [("gemma4:e4b", "gpu"), ("gemma-4-12b", "gpu")]
    assert steward.displaced() == {}


def test_nothing_comes_back_while_the_pressure_is_recent(steward, monkeypatch):
    _tier(monkeypatch)
    _displace(steward, quiet_for=5, **{"gemma4:e4b": ("fast", 3100)})
    monkeypatch.setattr(steward, "_card", lambda: {"zone": "green", "used": 2000, "total": 16000})
    calls = _record_ensure(steward, monkeypatch)
    assert steward.restore_displaced() is False
    assert calls == []


def test_nothing_comes_back_while_the_card_is_still_orange(steward, monkeypatch):
    _tier(monkeypatch)
    _displace(steward, **{"gemma4:e4b": ("fast", 3100)})
    monkeypatch.setattr(steward, "_card", lambda: {"zone": "orange", "used": 14000, "total": 16000})
    calls = _record_ensure(steward, monkeypatch)
    assert steward.restore_displaced() is False
    assert calls == []


def test_a_restore_never_pushes_the_card_into_the_zone_that_sheds_it(steward, monkeypatch):
    """Restoring past 85% would trigger the next orange: a reload loop."""
    _tier(monkeypatch)
    _displace(steward, **{"gemma4:e4b": ("fast", 3100), "gemma-4-12b": ("smart", 8000)})
    # 8000 + 3100 = 69% -> the fast model fits; + 8000 more = 119% -> the smart one waits.
    usage = {"used": 8000}
    monkeypatch.setattr(steward, "_card", lambda: {"zone": "yellow", "total": 16000, **usage})
    calls = _record_ensure(steward, monkeypatch)
    assert steward.restore_displaced() is False
    assert calls == [("gemma4:e4b", "gpu")]
    assert list(steward.displaced()) == ["gemma-4-12b"]


def test_a_refused_reservation_keeps_the_model_waiting(steward, monkeypatch):
    _tier(monkeypatch)
    _displace(steward, **{"gemma4:e4b": ("fast", 3100)})
    monkeypatch.setattr(steward, "_card", lambda: {"zone": "green", "used": 2000, "total": 16000})
    _record_ensure(steward, monkeypatch, ok=False)
    assert steward.restore_displaced() is False
    assert list(steward.displaced()) == ["gemma4:e4b"]


def test_a_model_swapped_out_of_the_tier_is_forgotten_not_restored(steward, monkeypatch):
    _tier(monkeypatch, fast="qwen3.5:4b")
    _displace(steward, **{"gemma4:e4b": ("fast", 3100)})
    calls = _record_ensure(steward, monkeypatch)
    assert steward.restore_displaced() is True
    assert calls == [] and steward.displaced() == {}


def test_relief_wakes_the_restorer(steward, monkeypatch):
    steward._restore_wake.clear()
    steward.on_event({"VramRelief": {"free": 9}})
    assert steward._restore_wake.is_set()


def test_an_eviction_is_reported_in_words_the_owner_can_act_on(steward, monkeypatch):
    published = []
    monkeypatch.setattr(steward.bus, "publish", lambda kind, payload=None: published.append((kind, payload)))
    steward.on_event({"Evicted": {"pid": 900, "name": "llama-server.exe",
                                  "label": "buzzcode engine", "reason": "VRAM Red"}})
    kind, payload = published[-1]
    assert kind == "evicted"
    assert "buzzcode engine" in payload["text"] and "VRAM Red" in payload["text"]


def test_disk_pressure_reaches_the_ui(steward, monkeypatch):
    published = []
    monkeypatch.setattr(steward.bus, "publish", lambda kind, payload=None: published.append((kind, payload)))
    steward.on_event({"DiskTight": {"drive": "C:", "free_bytes": 12 * 1024**3,
                                    "level": "red", "ask": "cleanup"}})
    assert published[-1][0] == "disk_tight"
    assert published[-1][1]["drive"] == "C:"


def test_a_handler_error_never_escapes(steward, monkeypatch):
    monkeypatch.setattr(steward, "shed", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    steward.on_event({"VramTight": {"level": "red", "over_budget": [], "free": 1}})  # must not raise


# ───────────────────────── falling back ─────────────────────────


def test_resident_fallback_prefers_the_fast_model(steward, monkeypatch):
    import router

    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b")
    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(steward.llm_router, "loaded_models", lambda: [
        {"name": "llama3.1:8b"}, {"name": "qwen2.5:3b"},
    ])
    assert steward.resident_fallback("ollama") == "qwen2.5:3b"


def test_resident_fallback_is_none_when_nothing_is_loaded(steward):
    assert steward.resident_fallback("ollama") is None
    assert steward.resident_fallback("lmstudio") is None


# ───────────────────────── v12: making room ─────────────────────────
#
# "buzzcode does not run every time, neither does buzzedit" was usually Dexter
# itself: its own 7B held the last gigabytes and `open_app` dutifully read out
# the refusal. Dexter yields to what the owner asked for — but only ever its
# own models. Someone else's render is Sentinel's call, by priority.


OUR_RUNNER = {"client": "dexter", "process": "ollama.exe", "pid": 4711,
              "vram_mib": 5000, "class": "assistant"}
OUR_LABELLED = {"label": "Ollama runner (Dexter)", "pid": 4712, "vram_mib": 5000}
VRDEEP = {"process": "VrDeep", "pid": 33228, "vram_mib": 10240, "class": "batch"}
BUZZCODE = {"client": "buzzcode", "process": "llama-server.exe", "pid": 1,
            "vram_mib": 12400, "class": "batch"}


def test_only_dexters_own_runner_counts_as_ours(steward, monkeypatch):
    assert steward.blockers_are_ours([OUR_RUNNER]) is True
    assert steward.blockers_are_ours([OUR_LABELLED]) is True
    assert steward.blockers_are_ours([OUR_RUNNER, OUR_LABELLED]) is True
    # One stranger in the list is enough: shedding would free nothing that
    # matters and the refusal would stand anyway.
    assert steward.blockers_are_ours([OUR_RUNNER, VRDEEP]) is False
    assert steward.blockers_are_ours([VRDEEP]) is False
    assert steward.blockers_are_ours([BUZZCODE]) is False
    # Nothing to give back is not a reason to unload the model we are using.
    assert steward.blockers_are_ours([]) is False


def test_a_stray_llama_server_is_only_ours_when_ollama_is_its_parent(steward, monkeypatch):
    stray = {"process": "llama-server.exe", "pid": 900, "vram_mib": 8000}
    monkeypatch.setattr(steward, "_parent_name", lambda pid: "lms.exe")
    assert steward.blockers_are_ours([stray]) is False
    monkeypatch.setattr(steward, "_parent_name", lambda pid: "ollama.exe")
    assert steward.blockers_are_ours([stray]) is True


def _room_world(steward, monkeypatch, granted_after=True):
    """`shed` and `query` recorded, nothing real touched."""
    calls = {"shed": [], "queries": [], "released": []}
    monkeypatch.setattr(steward, "shed", lambda level="orange", reason="", want_mib=0, move_fast=True: (
        calls["shed"].append((level, reason))
        or {"level": level, "steps": ["unloaded qwen2.5:7b"], "freedMib": 5017}
    ))
    monkeypatch.setattr(steward, "release", lambda why="": calls["released"].append(why))
    monkeypatch.setattr(steward, "query", lambda purpose, vram, ram=None: (
        calls["queries"].append(purpose)
        or steward.Decision(granted=granted_after, purpose=purpose, vram_mib=vram,
                            ram_mib=ram or 0, reason="not enough VRAM",
                            details={"free_mib": 1200})
    ))
    monkeypatch.setattr(steward, "_resident_mib", lambda: 0)
    return calls


def test_making_room_sheds_releases_and_asks_again(steward, monkeypatch):
    calls = _room_world(steward, monkeypatch)
    room = steward.make_room("open:buzzedit", "BuzzEdit", 9216, 8192, blockers=[OUR_RUNNER])

    assert room["tried"] is True and room["granted"] is True
    assert calls["shed"] == [("preempt", "making room for BuzzEdit")]
    assert calls["released"] == ["making room"]
    assert calls["queries"] == ["open:buzzedit"], "the second query is what decides"
    assert room["note"] == "Gave back qwen2.5:7b (4.9 GB) so BuzzEdit could start."


def test_nothing_is_given_back_for_someone_elses_work(steward, monkeypatch):
    calls = _room_world(steward, monkeypatch)
    room = steward.make_room("open:buzzedit", "BuzzEdit", 9216, 8192, blockers=[VRDEEP])

    assert room["tried"] is False and room["granted"] is False
    assert calls["shed"] == [] and calls["queries"] == []


def test_a_refusal_that_still_stands_after_shedding_launches_nothing(steward, monkeypatch):
    calls = _room_world(steward, monkeypatch, granted_after=False)
    room = steward.make_room("open:buzzedit", "BuzzEdit", 9216, 8192, blockers=[OUR_RUNNER])
    assert room["tried"] is True and room["granted"] is False
    assert calls["shed"] and calls["queries"] == ["open:buzzedit"]


def test_relief_ends_the_wait_early(steward, monkeypatch):
    """The grace period is a ceiling, not a cost: Sentinel saying "relief" is
    the news `make_room` was standing there waiting for."""
    import time as _time

    calls = _room_world(steward, monkeypatch)
    # Ollama is slow to drop the model out of /api/ps, but Sentinel has already
    # seen the VRAM come back and said so.
    monkeypatch.setattr(steward, "_resident_mib", lambda: 4000)
    monkeypatch.setattr(steward, "RELEASE_GRACE_SECS", 30.0)
    real_shed = calls["shed"]
    monkeypatch.setattr(steward, "shed", lambda level="orange", reason="", want_mib=0, move_fast=True: (
        real_shed.append((level, reason)) or steward._relief.set()
        or {"level": level, "steps": ["unloaded qwen2.5:7b"], "freedMib": 5017}
    ))
    started = _time.time()
    room = steward.make_room("open:buzzedit", "BuzzEdit", 9216, 8192, blockers=[OUR_RUNNER])
    assert room["granted"] is True
    assert _time.time() - started < 5, "it waited out the whole grace period"
    assert calls["queries"] == ["open:buzzedit"]


def test_the_panel_can_read_back_which_rung_the_last_turn_used(steward, monkeypatch):
    steward.note_ladder({"model": "qwen2.5:7b", "provider": "ollama",
                         "steppedDownFrom": "gemma-4-12b", "blockers": [VRDEEP]})
    assert steward.last_ladder()["model"] == "qwen2.5:7b"
    assert steward.last_ladder()["steppedDownFrom"] == "gemma-4-12b"
    monkeypatch.setenv("DEXTER_SENTINEL", "0")
    assert steward.snapshot()["ladder"]["model"] == "qwen2.5:7b"


# ───────────────────────── the snapshot ─────────────────────────


def test_the_snapshot_survives_an_absent_sentinel(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_SENTINEL", "0")
    snap = steward.snapshot()
    assert snap["sentinel"]["connected"] is False
    assert snap["budgets"]["engineVramMib"] == steward.ENGINE_VRAM_MIB


# ───────────────────────── v13: a third place to run ─────────────────────────
#
# Measured on this machine before any of this was written: `options.num_gpu: 0`
# loads a 3B entirely into system RAM at 24 tok/s and 0 MiB of VRAM, and a later
# request without the option keeps using that instance. So a model can live in
# RAM while a render owns the card, and come back to the card on its next load.
# Everything below is about making that Sentinel's decision rather than ours.


def test_a_cpu_load_books_ram_only_and_tells_ollama_where_to_put_it(steward, monkeypatch):
    client = use(monkeypatch, FakeClient(granted=True))
    warmed = []
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda p, m, k, options=None: warmed.append((m, options)) or True)
    monkeypatch.setattr(steward, "_observe_load", lambda model: True)

    decision = steward.ensure("qwen3.5:4b", "fast-model", keep_alive="2h", place="cpu")

    assert decision.granted and decision.loaded
    assert warmed == [("qwen3.5:4b", {"num_gpu": 0})], "the load must say where it goes"
    booked = client.reserves[0]
    assert booked["mib"] == 0, "a model in RAM consumes no video memory"
    # The weights, plus the runtime's own buffers riding along with them.
    size = steward.file_size_estimate("qwen3.5:4b")
    assert booked["ram_mib"] == int(size * 1.15)
    assert client.releases == 1
    assert steward.place_of("qwen3.5:4b") == "cpu"


def test_a_refused_ram_reservation_loads_nothing_either(steward, monkeypatch):
    """RAM is Sentinel's to give as well. Being refused the card is not a
    licence to fill the machine's memory instead."""
    client = use(monkeypatch, FakeClient(granted=False))
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda *a, **k: pytest.fail("a refusal must never load"))

    decision = steward.ensure("qwen3.5:4b", "fast-model", place="cpu")

    assert decision.granted is False and decision.loaded is False
    assert client.releases == 0
    assert steward.place_of("qwen3.5:4b") == ""


def test_a_model_already_in_ram_is_not_reloaded_into_ram(steward, monkeypatch):
    use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward.llm_router, "loaded_models", lambda: [
        {"name": "qwen3.5:4b", "sizeMb": 3000, "vramMb": 0, "onGpu": False, "place": "cpu"},
    ])
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda *a, **k: pytest.fail("it is already there"))
    assert steward.ensure("qwen3.5:4b", "fast-model", place="cpu").loaded is True


def test_a_model_ollama_split_by_itself_counts_as_on_the_card(steward, monkeypatch):
    """Ollama partially offloads all the time. Treating that as "the wrong
    place" would have the keep-warm loop reloading the fast model every two
    minutes, for ever."""
    assert steward._needs_move("split", "gpu") is False
    assert steward._needs_move("gpu", "gpu") is False
    assert steward._needs_move("cpu", "gpu") is True
    assert steward._needs_move("gpu", "cpu") is True
    assert steward._needs_move("", "cpu") is False, "nothing to move"


# ───────────────────────── moving, not dropping (P3.3) ─────────────────────────


def test_red_moves_the_fast_model_to_ram_instead_of_dropping_it(steward, monkeypatch):
    """The card is needed; the assistant is not gone. Quick answers keep coming
    at about a fifth of the speed instead of stopping altogether."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "")
    monkeypatch.setattr(router, "FAST_MODEL", "qwen3.5:4b")
    use(monkeypatch, FakeClient(granted=True))
    released = shed_recorder(monkeypatch, loaded=[
        {"name": "qwen3.5:4b", "sizeMb": 3000, "vramMb": 3000, "onGpu": True, "place": "gpu"},
    ])
    warmed = []
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda p, m, k, options=None: warmed.append((m, options)) or True)
    monkeypatch.setattr(steward, "_observe_load", lambda model: True)
    monkeypatch.setattr(steward, "fits_on_cpu", lambda model, provider="ollama": True)

    summary = steward.shed("red", "VramTight red")

    assert [m for m, _ in released] == ["qwen3.5:4b"], "the VRAM goes back first, always"
    assert warmed == [("qwen3.5:4b", {"num_gpu": 0})]
    assert summary["steps"] == ["moved qwen3.5:4b to CPU"]


def test_when_sentinel_refuses_the_ram_it_is_the_plain_unload_it_always_was(steward, monkeypatch):
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "")
    monkeypatch.setattr(router, "FAST_MODEL", "qwen3.5:4b")
    use(monkeypatch, FakeClient(granted=False))
    released = shed_recorder(monkeypatch, loaded=[
        {"name": "qwen3.5:4b", "sizeMb": 3000, "vramMb": 3000, "onGpu": True, "place": "gpu"},
    ])
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda *a, **k: pytest.fail("a refusal must never load"))
    monkeypatch.setattr(steward, "fits_on_cpu", lambda model, provider="ollama": True)

    summary = steward.shed("red", "VramTight red")

    assert [m for m, _ in released] == ["qwen3.5:4b"]
    assert summary["steps"] == ["unloaded qwen3.5:4b"]


def test_a_model_too_big_for_ram_is_simply_dropped(steward, monkeypatch):
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "")
    monkeypatch.setattr(router, "FAST_MODEL", "gemma-4-12b")
    use(monkeypatch, FakeClient(granted=True))
    shed_recorder(monkeypatch, loaded=[
        {"name": "gemma-4-12b", "sizeMb": 7600, "vramMb": 7600, "onGpu": True, "place": "gpu"},
    ])
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda *a, **k: pytest.fail("8 tok/s is not an assistant"))
    monkeypatch.setattr(steward, "fits_on_cpu", lambda model, provider="ollama": False)

    assert steward.shed("red", "VramTight red")["steps"] == ["unloaded gemma-4-12b"]


def test_making_room_for_an_app_never_moves_the_fast_model(steward, monkeypatch):
    """The owner asked for an app. Loading 3 GB back into RAM in the same breath
    is the opposite of making room."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "")
    monkeypatch.setattr(router, "FAST_MODEL", "qwen3.5:4b")
    use(monkeypatch, FakeClient(granted=True))
    shed_recorder(monkeypatch, loaded=[
        {"name": "qwen3.5:4b", "sizeMb": 3000, "vramMb": 3000, "onGpu": True, "place": "gpu"},
    ])
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda *a, **k: pytest.fail("not while an app is waiting"))
    monkeypatch.setattr(steward, "fits_on_cpu", lambda model, provider="ollama": True)
    monkeypatch.setattr(steward, "_resident_mib", lambda: 0)
    monkeypatch.setattr(steward, "release", lambda why="": None)

    room = steward.make_room("open:buzzedit", "BuzzEdit", 9216, 8192, blockers=[OUR_RUNNER])
    assert room["tried"] is True
    assert steward.last_shed()["steps"] == ["unloaded qwen3.5:4b"]


def test_the_fast_model_comes_back_to_the_card_on_the_next_turn_that_used_it(steward, monkeypatch):
    """Never on a relief event (D5): the card that just went green goes straight
    back to red if everyone reloads at once. It comes back here, on the next
    quick reply, and only if Sentinel grants the VRAM."""
    client = use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward.llm_router, "loaded_models", lambda: [
        {"name": "qwen3.5:4b", "sizeMb": 3000, "vramMb": 0, "onGpu": False, "place": "cpu"},
    ])
    warmed = []
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda p, m, k, options=None: warmed.append((m, options)) or True)
    monkeypatch.setattr(steward.llm_router, "release_model", lambda p, m, after="0": True)
    monkeypatch.setattr(steward, "_observe_load", lambda model: True)

    steward._warm_quietly("ollama", "qwen3.5:4b", "2h")

    assert warmed == [("qwen3.5:4b", None)], "back on the card, with no placement option"
    assert client.reserves[0]["mib"] > 0, "a move back to the card books VRAM"


def test_a_refused_card_leaves_it_answering_from_ram(steward, monkeypatch):
    use(monkeypatch, FakeClient(granted=False))
    monkeypatch.setattr(steward.llm_router, "loaded_models", lambda: [
        {"name": "qwen3.5:4b", "sizeMb": 3000, "vramMb": 0, "onGpu": False, "place": "cpu"},
    ])
    warmed = []
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda p, m, k, options=None: warmed.append((m, options)) or True)
    monkeypatch.setattr(steward.llm_router, "release_model",
                        lambda *a, **k: pytest.fail("a refusal must not cost us the model we have"))

    steward._warm_quietly("ollama", "qwen3.5:4b", "2h")

    assert warmed == [("qwen3.5:4b", None)], "only the expiry is pushed out; it stays in RAM"


# ───────────────────────── the partial split (P3.4) ─────────────────────────


def test_the_split_arithmetic_is_layers_that_fit_and_no_more(steward, monkeypatch):
    monkeypatch.setattr(steward, "file_size_estimate", lambda model, provider="ollama": 3600)
    monkeypatch.setattr(steward.llm_router, "model_layers", lambda model: 36)
    monkeypatch.setattr(steward, "SPLIT_HEADROOM_MIB", 1024)
    # 3600 MiB over 36 blocks is 100 MiB each; 2000 free, less 1024 of headroom,
    # is 976 - nine layers, not nine and a half.
    assert steward.split_layers("qwen3.5:4b", 2000) == 9
    # Not one layer's worth once the headroom is taken out: do not try.
    assert steward.split_layers("qwen3.5:4b", 1100) == 0
    assert steward.split_layers("qwen3.5:4b", 0) == 0
    # Never more layers than the model has, however much room there is.
    assert steward.split_layers("qwen3.5:4b", 100_000) == 36
    # Ollama could not say how many blocks it has: not a guess we should make.
    monkeypatch.setattr(steward.llm_router, "model_layers", lambda model: 0)
    assert steward.split_layers("qwen3.5:4b", 8000) == 0


def test_a_split_load_books_both_halves_and_names_the_layer_count(steward, monkeypatch):
    client = use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward, "file_size_estimate", lambda model, provider="ollama": 3600)
    monkeypatch.setattr(steward.llm_router, "model_layers", lambda model: 36)
    monkeypatch.setattr(steward, "SPLIT_HEADROOM_MIB", 1024)
    warmed = []
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda p, m, k, options=None: warmed.append((m, options)) or True)
    monkeypatch.setattr(steward, "_observe_load", lambda model: True)

    decision = steward.ensure("qwen3.5:4b", "fast-model", place="split", free_mib=2000)

    assert decision.loaded and warmed == [("qwen3.5:4b", {"num_gpu": 9})]
    booked = client.reserves[0]
    assert booked["mib"] == 900 + 1024, "nine layers on the card, plus their headroom"
    assert booked["ram_mib"] == int((3600 - 900) * 1.15)
    assert steward.place_of("qwen3.5:4b") == "split"


def test_a_split_that_fits_nothing_loads_nothing(steward, monkeypatch):
    use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward, "file_size_estimate", lambda model, provider="ollama": 3600)
    monkeypatch.setattr(steward.llm_router, "model_layers", lambda model: 36)
    monkeypatch.setattr(steward.llm_router, "warm_model",
                        lambda *a, **k: pytest.fail("there was no room for a single layer"))

    decision = steward.ensure("qwen3.5:4b", "fast-model", place="split", free_mib=200)
    assert decision.granted is False and decision.loaded is False


def test_a_refused_card_is_tried_split_then_in_ram(steward, monkeypatch):
    """The fast tier stops being all-or-nothing: as many layers as will fit,
    then all of it in RAM, and only ever with a fresh grant for each."""
    asked = []

    def fake_ensure(model, purpose, provider="ollama", keep_alive="30m", place="gpu", **kw):
        asked.append(place)
        granted = place == "cpu"
        return model_steward.Decision(granted, purpose, model=model, provider=provider,
                                      loaded=granted, details={"free_mib": 2000})

    monkeypatch.setattr(steward.llm_router, "loaded_models", lambda: [])
    monkeypatch.setattr(steward, "ensure", fake_ensure)
    monkeypatch.setattr(steward, "fits_on_cpu", lambda model, provider="ollama": True)
    monkeypatch.setattr(steward, "SPLIT_FAST", True)

    steward._warm_quietly("ollama", "qwen3.5:4b", "2h")
    assert asked == ["gpu", "split", "cpu"]


def test_what_is_loaded_says_where_each_model_is():
    """The panel's question, answered off Ollama's own numbers rather than off
    what we remember asking for - a model somebody else loaded is described just
    as honestly as ours."""
    from hybrid_llm import HybridLLMRouter

    class _Res:
        status_code = 200

        @staticmethod
        def json():
            gb = 1024 ** 3
            return {"models": [
                {"name": "qwen3.5:4b", "size": 3 * gb, "size_vram": 3 * gb},
                {"name": "qwen2.5:7b", "size": 5 * gb, "size_vram": 0},
                {"name": "gemma-4-12b", "size": 8 * gb, "size_vram": 2 * gb},
            ]}

    router = HybridLLMRouter()
    router.session = type("S", (), {"get": staticmethod(lambda url, timeout=0: _Res())})()
    assert [m["place"] for m in router.loaded_models()] == ["gpu", "cpu", "split"]
