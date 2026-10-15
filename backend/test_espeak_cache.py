"""
One espeak library per process (v13.7d).

`phonemizer` copies the entire espeak-ng DLL into a fresh temp directory every time a
backend is constructed, and `EspeakBackend.__init__` constructs **two** wrappers
(one inside `is_available()`), while `phonemizer.phonemize()` constructs a backend per
call. Measured on this machine: **13 copies for four spoken chunks**, and 1,172 of
them left behind in `%TEMP%` - 468 MB - one of which the synthesis thread happened to
be sitting in when a frozen Dexter was dumped.

Two properties are tested, and the second matters as much as the first:

* a wrapper is created **once**, however many backends are built;
* the sweep deletes **only** phonemizer's leftovers. It runs over the owner's whole
  temp folder, which also held 211 unrelated single-file directories, so "delete
  anything that looks a bit like it" is not good enough.
"""

import os
import sys
import types

import pytest

import espeak_cache


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """A clean, uninstalled cache for every test."""
    monkeypatch.setattr(espeak_cache, "_installed", False, raising=False)
    monkeypatch.setattr(espeak_cache, "_shared", None, raising=False)
    monkeypatch.setattr(espeak_cache, "_created", 0, raising=False)
    monkeypatch.setattr(espeak_cache, "_patched", [], raising=False)
    yield
    espeak_cache._installed = False
    espeak_cache._shared = None


class FakeWrapper:
    """Stands in for phonemizer's EspeakWrapper, counting how often it is built."""

    built = 0

    def __init__(self, *args, **kwargs):
        FakeWrapper.built += 1
        self.library_path = "espeak-ng.dll"
        self.version = (1, 51)


@pytest.fixture
def fake_phonemizer(monkeypatch):
    """The three phonemizer modules that each construct a wrapper of their own."""
    FakeWrapper.built = 0
    modules = {}
    for name in espeak_cache._PATCH_MODULES:
        module = types.ModuleType(name)
        module.EspeakWrapper = FakeWrapper
        monkeypatch.setitem(sys.modules, name, module)
        modules[name.rsplit(".", 1)[-1]] = module
    wrapper_module = types.ModuleType("phonemizer.backend.espeak.wrapper")
    wrapper_module.EspeakWrapper = FakeWrapper
    monkeypatch.setitem(sys.modules, "phonemizer.backend.espeak.wrapper", wrapper_module)
    return modules


# ───────────────────────── sharing the library ─────────────────────────


def test_a_wrapper_is_built_once_however_many_backends_are(fake_phonemizer):
    assert espeak_cache.install() is True
    base = fake_phonemizer["base"]
    # Four sentences, two wrappers each as phonemizer would have it.
    for _ in range(4):
        base.EspeakWrapper()
        base.EspeakWrapper()
    assert FakeWrapper.built == 1, f"{FakeWrapper.built} copies of the library"
    assert espeak_cache.stats()["wrappersCreated"] == 1


def test_every_module_that_builds_one_is_patched(fake_phonemizer):
    """Patching only `base` left a copy per sentence coming from
    `espeak.available_voices` - four per chunk became one, not none."""
    espeak_cache.install()
    for name, module in fake_phonemizer.items():
        assert module.EspeakWrapper is not FakeWrapper, f"{name} still builds its own"
        module.EspeakWrapper()
    assert FakeWrapper.built == 1


def test_installing_twice_changes_nothing(fake_phonemizer):
    assert espeak_cache.install() is True
    first = fake_phonemizer["base"].EspeakWrapper
    assert espeak_cache.install() is True
    assert fake_phonemizer["base"].EspeakWrapper is first


def test_warming_builds_it_before_the_first_reply_needs_it(fake_phonemizer):
    assert espeak_cache.warm() is True
    assert FakeWrapper.built == 1
    assert espeak_cache.stats()["shared"] is True


def test_without_phonemizer_it_declines_rather_than_breaking_voice(monkeypatch):
    monkeypatch.setitem(sys.modules, "phonemizer.backend.espeak.wrapper", None)
    assert espeak_cache.install() is False


def test_the_lock_is_reentrant():
    """voice_engine holds it around a Kokoro call that may phonemize more than once."""
    with espeak_cache.synthesis_lock:
        with espeak_cache.synthesis_lock:
            pass


# ───────────────────────── sweeping the leftovers ─────────────────────────


def _leftover(tmp_path, name="tmpab12cd34", filename="espeak-ng.dll", age=3600):
    directory = tmp_path / name
    directory.mkdir()
    (directory / filename).write_bytes(b"x" * 2048)
    old = os.stat(directory).st_mtime - age
    os.utime(directory, (old, old))
    return directory


def test_it_finds_phonemizers_leftovers(tmp_path):
    _leftover(tmp_path)
    _leftover(tmp_path, name="tmpzz99yy88", filename="libespeak-ng_3f2c1a9b-dead.dll")
    found = espeak_cache.find_leftovers(str(tmp_path))
    assert len(found) == 2


@pytest.mark.parametrize("name,filename", [
    # A temp directory with one file in it is a common shape - the owner's %TEMP%
    # had 211 holding a ports.json. None of these may be touched.
    ("tmpab12cd34", "ports.json"),
    ("tmpab12cd34", "espeak-ng.dll.txt"),
    ("my-cache", "espeak-ng.dll"),          # not a mkdtemp name
    ("tmpab12cd34-backup", "espeak-ng.dll"),
])
def test_it_leaves_everything_else_alone(tmp_path, name, filename):
    _leftover(tmp_path, name=name, filename=filename)
    assert espeak_cache.find_leftovers(str(tmp_path)) == []


def test_a_directory_with_anything_else_in_it_is_not_ours(tmp_path):
    directory = _leftover(tmp_path)
    (directory / "something-else.txt").write_text("not phonemizer's")
    assert espeak_cache.find_leftovers(str(tmp_path)) == []


def test_a_copy_in_use_right_now_is_spared(tmp_path):
    """A live synthesis in another process has a fresh directory open."""
    _leftover(tmp_path, name="tmpfresh0001", age=0)
    assert espeak_cache.find_leftovers(str(tmp_path)) == []


def test_the_sweep_removes_them_and_reports_the_space(tmp_path):
    for index in range(3):
        _leftover(tmp_path, name=f"tmpaa00bb{index:02d}")
    keep = _leftover(tmp_path, name="tmpkeepthis1", filename="ports.json")
    result = espeak_cache.sweep(str(tmp_path))
    assert result["removed"] == 3
    assert result["failed"] == 0
    assert result["freedMb"] >= 0
    assert keep.exists(), "only phonemizer's copies go"


def test_sweeping_an_empty_temp_folder_is_fine(tmp_path):
    assert espeak_cache.sweep(str(tmp_path))["removed"] == 0


def test_a_missing_temp_folder_does_not_raise():
    assert espeak_cache.find_leftovers(r"Z:\nothing\here\at\all") == []
