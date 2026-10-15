"""
Device pairing + token store (mobile access).

These are the auth core that makes opening the bind to a phone safe: a phone
pairs once with a short-lived code and gets a long-lived token whose *hash* is
all that is stored. The tests pin the properties that matter - codes are single
use and expire, only the hash is persisted, an unknown token verifies to nothing,
and revocation is real.

Run:  pytest backend/test_devices.py -v
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import devices


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(devices, "DEVICES_FILE", str(tmp_path / "devices.json"))
    devices._pairing_codes.clear()
    yield devices
    devices._pairing_codes.clear()


def test_pair_then_verify_round_trip(store):
    code, ttl = store.new_pairing_code()
    assert ttl > 0 and len(code) == store._PAIRING_LEN

    result = store.redeem_pairing(code, "my phone")
    assert result and result["token"] and result["id"]

    device = store.verify(result["token"])
    assert device and device["name"] == "my phone"


def test_only_the_hash_is_persisted(store):
    code, _ = store.new_pairing_code()
    result = store.redeem_pairing(code, "phone")
    raw = open(store.DEVICES_FILE, encoding="utf-8").read()
    assert result["token"] not in raw, "the plaintext token must never touch disk"
    assert "tokenHash" in raw


def test_a_code_is_single_use(store):
    code, _ = store.new_pairing_code()
    assert store.redeem_pairing(code, "phone")
    assert store.redeem_pairing(code, "phone") is None, "a code works exactly once"


def test_an_expired_code_is_refused(store, monkeypatch):
    code, _ = store.new_pairing_code()
    # Push its expiry into the past.
    store._pairing_codes[code] = time.time() - 1
    assert store.redeem_pairing(code, "phone") is None


def test_unknown_token_verifies_to_nothing(store):
    store.redeem_pairing(store.new_pairing_code()[0], "phone")
    assert store.verify("not-a-real-token") is None
    assert store.verify("") is None


def test_revoke_removes_access(store):
    result = store.redeem_pairing(store.new_pairing_code()[0], "phone")
    assert store.verify(result["token"])
    assert store.revoke(result["id"]) is True
    assert store.verify(result["token"]) is None
    assert store.revoke(result["id"]) is False, "revoking twice is a no-op"


def test_list_devices_hides_the_hash(store):
    store.redeem_pairing(store.new_pairing_code()[0], "phone")
    listed = store.list_devices()
    assert len(listed) == 1
    assert "tokenHash" not in listed[0]
    assert listed[0]["name"] == "phone"
