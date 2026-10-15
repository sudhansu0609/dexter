"""
One model at a time (v13.2).

The owner's ask: "make sure if Dexter uses one model and switches to another, or
I switch it, the previous model gets offloaded to save memory." Before this, a
model left memory only when its keep-alive expired - 30 minutes for a smart rung,
two hours for the fast one - so switching *added* a model rather than replacing
one, and changing a tier in Settings left the old tag holding gigabytes.

What these tests pin down is as much about restraint as about unloading: nothing
that belongs to another app is ever touched, a tag that is still a tier is left
alone, and "off" really does nothing.
"""

import pytest

import model_steward
import router
import settings_store


@pytest.fixture
def steward(monkeypatch):
    """A steward whose unloads are recorded instead of performed."""
    state = {"unloaded": [], "released": [], "resident": ["gemma-4-12b", "qwen3.5:4b"]}

    def _unload(provider, model):
        state["unloaded"].append((provider, model))
        model_steward._forget_ours(model)
        return True

    def _release_soon(provider, model, after="30m"):
        state["released"].append((provider, model, after))

    monkeypatch.setattr(model_steward, "unload", _unload)
    monkeypatch.setattr(model_steward, "release_soon", _release_soon)
    monkeypatch.setattr(model_steward, "_publish", lambda extra=None: None)
    model_steward._ours.clear()
    for name in state["resident"]:
        model_steward._mark_ours(name)
    # The real `_tier_models` reads the router's constants, which is what makes
    # the order in `update()` matter: the setting is applied (so the router has
    # the *new* tier) and only then is the old tag offloaded. Stubbing the tuple
    # would hide exactly that.
    monkeypatch.setattr(router, "SMART_MODEL", "gemma-4-12b", raising=False)
    monkeypatch.setattr(router, "FAST_MODEL", "qwen3.5:4b", raising=False)
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "qwen2.5:7b", raising=False)
    monkeypatch.setenv("DEXTER_OFFLOAD_PREVIOUS", "rungs")
    return state


# ───────────────────────── retire ─────────────────────────


def test_retire_drops_what_is_not_wanted_and_keeps_what_is(steward):
    dropped = model_steward.retire(["gemma-4-12b"], why="test")
    assert dropped == ["qwen3.5:4b"]
    assert steward["unloaded"] == [("ollama", "qwen3.5:4b")]


def test_retire_can_spare_the_fast_tier(steward):
    """The 3 GB model every chat turn needs is not worth reloading constantly."""
    assert model_steward.retire(["gemma-4-12b"], include_fast=False) == []
    assert steward["unloaded"] == []


def test_retire_never_touches_a_model_dexter_did_not_load(steward):
    model_steward._ours.clear()
    model_steward._mark_ours("gemma-4-12b")
    assert model_steward.retire([], why="test") == ["gemma-4-12b"]
    assert ("ollama", "caliberai-runner") not in steward["unloaded"]


def test_off_means_off(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_OFFLOAD_PREVIOUS", "off")
    assert model_steward.offload_mode() == "off"
    assert model_steward.retire([], why="test") == []
    assert steward["unloaded"] == []


def test_a_family_tag_counts_as_the_same_model(steward):
    model_steward._ours.clear()
    model_steward._mark_ours("qwen2.5:7b-instruct-q4")
    assert model_steward.retire(["qwen2.5:7b"]) == []


# ───────────────────────── the owner switching a tier ─────────────────────────


def test_switching_a_tier_offloads_the_one_it_replaced(steward, monkeypatch):
    # As it happens in practice: the setting has been applied, so the router's
    # fast tier is already the new model.
    monkeypatch.setattr(router, "FAST_MODEL", "granite4.1:3b", raising=False)
    assert model_steward.replaced("qwen3.5:4b", "granite4.1:3b") is True
    assert steward["unloaded"] == [("ollama", "qwen3.5:4b")]


def test_a_tier_that_is_still_in_use_is_not_offloaded_mid_switch(steward):
    """The guard against the order being wrong: if this is called *before* the
    setting is applied, the old tag is still a tier and is left alone rather than
    being unloaded out from under the next turn."""
    assert model_steward.replaced("qwen3.5:4b", "granite4.1:3b") is False
    assert steward["unloaded"] == []


def test_a_tag_that_is_still_another_tier_is_left_alone(steward):
    """Changing the fallback to the model that is already the smart rung must not
    unload it - the next turn would only load it again."""
    assert model_steward.replaced("gemma-4-12b", "qwen2.5:7b") is False
    assert steward["unloaded"] == []


def test_switching_to_the_same_model_does_nothing(steward):
    assert model_steward.replaced("qwen3.5:4b", "qwen3.5:4b") is False
    assert steward["unloaded"] == []


def test_a_model_that_was_never_resident_is_not_unloaded(steward):
    assert model_steward.replaced("llama3.2", "qwen3.5:4b") is False
    assert steward["unloaded"] == []


def test_settings_offload_the_replaced_model(steward, tmp_path, monkeypatch):
    """The hook the Settings panel goes through, end to end.

    `before` is the *effective* value - stored, then .env, then the default - so
    the environment is what says which model is being switched away from here.
    """
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("DEXTER_FAST_MODEL", "qwen3.5:4b")
    result = settings_store.update({"fastModel": "granite4.1:3b"})
    assert result["ok"] is True
    assert "qwen3.5:4b" in result["offloaded"]
    assert ("ollama", "qwen3.5:4b") in steward["unloaded"]


def test_changing_something_that_is_not_a_model_offloads_nothing(steward, tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    result = settings_store.update({"followUpWindow": 5.0})
    assert result["offloaded"] == []
    assert steward["unloaded"] == []


# ───────────────────────── after an escalated turn ─────────────────────────


def test_a_superseded_rung_goes_and_the_fast_model_stays(steward, monkeypatch):
    """A turn that stepped down used to leave both rungs resident for half an hour."""
    monkeypatch.setattr(model_steward, "memory_is_tight", lambda: False)
    model_steward._ours.clear()
    for name in ("gemma-4-12b", "qwen2.5:7b", "qwen3.5:4b"):
        model_steward._mark_ours(name)
    router._release_smart_model_soon("ollama", "qwen2.5:7b")
    assert steward["unloaded"] == [("ollama", "gemma-4-12b")], "the rung not in use goes"
    assert ("ollama", "qwen3.5:4b") not in steward["unloaded"], "the fast tier stays"
    assert steward["released"] == [("ollama", "qwen2.5:7b", router.SMART_KEEP_ALIVE)]


def test_when_memory_is_tight_the_big_model_goes_now(steward, monkeypatch):
    monkeypatch.setattr(model_steward, "memory_is_tight", lambda: True)
    router._release_smart_model_soon("ollama", "gemma-4-12b")
    assert ("ollama", "gemma-4-12b") in steward["unloaded"]
    assert steward["released"] == [], "no point scheduling what has already gone"


def test_always_keeps_only_the_model_that_answered(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_OFFLOAD_PREVIOUS", "always")
    router._release_smart_model_soon("ollama", "gemma-4-12b")
    assert steward["unloaded"] == [("ollama", "qwen3.5:4b")]


def test_off_restores_the_v13_behaviour(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_OFFLOAD_PREVIOUS", "off")
    router._release_smart_model_soon("ollama", "gemma-4-12b")
    assert steward["unloaded"] == []
    assert steward["released"] == [("ollama", "gemma-4-12b", router.SMART_KEEP_ALIVE)]


def test_memory_is_tight_reads_sentinels_zones(monkeypatch):
    monkeypatch.setattr(model_steward, "snapshot",
                        lambda: {"vram": {"zone": "green"}, "ram": {"zone": "green"}})
    assert model_steward.memory_is_tight() is False
    monkeypatch.setattr(model_steward, "snapshot",
                        lambda: {"vram": {"zone": "orange"}, "ram": {"zone": "green"}})
    assert model_steward.memory_is_tight() is True


# ───────────── strictly one Dexter model at a time (2026-10-15) ─────────────


def _loaded(monkeypatch, *names):
    monkeypatch.setattr(model_steward.llm_router, "loaded_models",
                        lambda: [{"name": n, "vramMb": 3000, "onGpu": True} for n in names])


def test_make_way_unloads_the_other_tier_model_before_a_load(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_OFFLOAD_PREVIOUS", "always")
    _loaded(monkeypatch, "qwen3.5:4b", "caliberai-runner")
    assert model_steward.make_way("gemma-4-12b") == ["qwen3.5:4b"]
    assert ("ollama", "caliberai-runner") not in steward["unloaded"], "never another app's model"


def test_make_way_does_nothing_in_the_other_modes(steward, monkeypatch):
    _loaded(monkeypatch, "qwen3.5:4b")
    assert model_steward.make_way("gemma-4-12b") == []
    assert steward["unloaded"] == []


def test_with_the_smart_model_loaded_the_turn_stays_on_it(steward, monkeypatch):
    """Trying the fast tier first would swap 12B -> e4b -> 12B."""
    _loaded(monkeypatch, "gemma-4-12b")
    assert model_steward.resident_smart() == "gemma-4-12b"
    _loaded(monkeypatch, "gemma-4-12b", "qwen3.5:4b")
    assert model_steward.resident_smart() is None, "the fast model is there: use it"
    _loaded(monkeypatch)
    assert model_steward.resident_smart() is None


def test_an_idle_smart_model_settles_back_to_the_fast_one(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_OFFLOAD_PREVIOUS", "always")
    _loaded(monkeypatch, "gemma-4-12b")
    loads = []
    monkeypatch.setattr(model_steward, "ensure", lambda model, purpose, **kw: loads.append(model)
                        or model_steward.Decision(True, purpose, model=model, loaded=True))
    model_steward._settle_gen = 7
    model_steward._settle_after(7, "gemma-4-12b", 0)
    assert loads == ["qwen3.5:4b"]


def test_a_newer_smart_turn_restarts_the_idle_clock(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_OFFLOAD_PREVIOUS", "always")
    _loaded(monkeypatch, "gemma-4-12b")
    loads = []
    monkeypatch.setattr(model_steward, "ensure", lambda model, purpose, **kw: loads.append(model))
    model_steward._settle_gen = 8
    model_steward._settle_after(7, "gemma-4-12b", 0)  # generation 7 is stale
    assert loads == []


def test_after_pressure_only_one_model_comes_back(steward, monkeypatch):
    monkeypatch.setenv("DEXTER_OFFLOAD_PREVIOUS", "always")
    monkeypatch.setattr(model_steward, "_card", lambda: {"zone": "green", "used": 1000, "total": 16000})
    model_steward._displaced.clear()
    model_steward._displaced.update({
        "gemma-4-12b": {"role": "smart", "mib": 8000, "at": 0},
        "qwen3.5:4b": {"role": "fast", "mib": 3000, "at": 0},
    })
    monkeypatch.setattr(model_steward, "RESTORE_WINDOW_SECS", 10 ** 12)
    monkeypatch.setattr(model_steward, "_last_pressure", 0.0)
    loads = []
    monkeypatch.setattr(model_steward, "ensure", lambda model, purpose, **kw: loads.append(model)
                        or model_steward.Decision(True, purpose, model=model, loaded=True))
    assert model_steward.restore_displaced() is True
    assert loads == ["qwen3.5:4b"]
