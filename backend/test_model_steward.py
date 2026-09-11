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
                        lambda p, m, k: warmed.append((p, m, k)) or True)
    # The load is "observed" the moment /api/ps lists it.
    monkeypatch.setattr(steward, "_observe_load", lambda model: True)

    decision = steward.ensure("qwen2.5:7b", "escalation", keep_alive="30m")

    assert decision.granted and decision.loaded
    assert warmed == [("ollama", "qwen2.5:7b", "30m")]
    assert client.reserves == [{"mib": 6000, "ram_mib": 2000, "for_pid": None, "ttl": 90}]
    assert client.releases == 1, "the booking must be handed back once the load is observed"
    assert steward.booked() is False


def test_a_failed_load_still_releases_the_booking(steward, monkeypatch):
    """Holding memory behind a model that never arrived starves everyone."""
    client = use(monkeypatch, FakeClient(granted=True))
    monkeypatch.setattr(steward.llm_router, "warm_model", lambda p, m, k: False)

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
                        lambda p, m, k: warmed.append(m) or True)

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
                        lambda p, m, k: pytest.fail("must not reload a resident model"))

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
    monkeypatch.setattr(steward.llm_router, "warm_model", lambda p, m, k: True)
    monkeypatch.setattr(steward, "_observe_load", lambda model: True)
    decision = steward.ensure("qwen2.5:7b", "escalation")
    assert decision.granted and decision.absent
    assert steward.booked() is False


# ───────────────────────── RAM admission ─────────────────────────


def test_ram_is_asked_for_and_the_old_service_simply_does_not_see_it(steward, monkeypatch):
    """`ram_mib` is passed to the client either way; the *client* is what drops
    it for a v1 service, which is where that decision belongs."""
    client = use(monkeypatch, FakeClient(granted=True, features=()))
    monkeypatch.setattr(steward.llm_router, "warm_model", lambda p, m, k: True)
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
    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b")
    released = shed_recorder(monkeypatch)
    summary = steward.shed("orange", "VramTight")
    assert [m for m, _ in released] == ["qwen2.5:7b"]
    assert summary["steps"] == ["unloaded qwen2.5:7b"]


def test_red_gives_up_the_small_model_and_anything_that_is_not_ours(steward, monkeypatch):
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
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
        {"VramTight": {"level": "orange", "over_budget": ["buzzcode"], "free": 3}},
        {"VramRelief": {"free": 9}},
        {"RamTight": {"zone": "Yellow", "ask": "hold", "over_budget": []}},
        {"Heartbeat": {"every_secs": 30}},
        "not even a dict",
        None,
    ):
        steward.on_event(event)
    assert released == []


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


# ───────────────────────── the snapshot ─────────────────────────


def test_the_snapshot_survives_an_absent_sentinel(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_SENTINEL", "0")
    snap = steward.snapshot()
    assert snap["sentinel"]["connected"] is False
    assert snap["budgets"]["engineVramMib"] == steward.ENGINE_VRAM_MIB
