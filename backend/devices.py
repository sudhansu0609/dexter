"""
Persistent device tokens + short-lived pairing codes.

Dexter's only auth used to be a per-run session token handed to local UI shells
by an unauthenticated bootstrap endpoint that was safe *only* because the server
bound to loopback. The moment the bind opens up (Tailscale, so the phone can
reach it) that model breaks: anything that can open the socket could fetch the
shared token.

This module is the fix. A phone *pairs once*: the PC (on loopback) mints a
short-lived pairing code shown as a QR; the phone redeems it for a long-lived
**device token** that it stores and sends on every call. Only the SHA-256 hash of
each device token is persisted, so `data/devices.json` leaking does not hand out
access. Pairing codes live in memory only - they never touch disk and they expire.
"""

import hashlib
import os
import secrets
import string
import time
from typing import Any, Dict, List, Optional, Tuple

from jsonstore import load_json, save_json

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")

#: A pairing code is shown on the PC screen and typed/scanned on the phone within
#: a couple of minutes. Single-use, high-entropy, and rate-limited by its own TTL.
PAIRING_TTL_SECONDS = 120
_PAIRING_ALPHABET = string.ascii_uppercase + string.digits  # no lowercase: easy to read/type
_PAIRING_LEN = 6

#: Don't rewrite the file on every request just to bump lastSeen.
_LASTSEEN_THROTTLE_SECONDS = 60

# code -> expiry epoch. In-memory only: a restart invalidates every pending code,
# which is the safe direction.
_pairing_codes: Dict[str, float] = {}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load() -> List[Dict[str, Any]]:
    data = load_json(DEVICES_FILE, [])
    return data if isinstance(data, list) else []


def _save(devices: List[Dict[str, Any]]) -> None:
    save_json(DEVICES_FILE, devices)


# ───────────────────────── pairing ─────────────────────────


def new_pairing_code() -> Tuple[str, int]:
    """Mint a single-use pairing code (called on the PC, loopback-gated).

    Returns the code and its TTL in seconds. Expired codes are swept here so the
    dict never grows without bound.
    """
    now = time.time()
    for code, expiry in list(_pairing_codes.items()):
        if expiry <= now:
            _pairing_codes.pop(code, None)
    code = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(_PAIRING_LEN))
    _pairing_codes[code] = now + PAIRING_TTL_SECONDS
    return code, PAIRING_TTL_SECONDS


def redeem_pairing(code: str, name: str) -> Optional[Dict[str, str]]:
    """Redeem a code (called by the phone) for a fresh device token.

    Returns {"token": ..., "id": ...} on success, or None when the code is
    unknown/expired. The plaintext token is returned exactly once - only its hash
    is stored.
    """
    code = (code or "").strip().upper()
    expiry = _pairing_codes.get(code)
    if not expiry or expiry <= time.time():
        _pairing_codes.pop(code, None)
        return None
    _pairing_codes.pop(code, None)  # single use

    token = secrets.token_urlsafe(32)
    device = {
        "id": secrets.token_hex(8),
        "name": (name or "phone").strip()[:60] or "phone",
        "tokenHash": _hash(token),
        "createdAt": int(time.time()),
        "lastSeen": int(time.time()),
    }
    devices = _load()
    devices.append(device)
    _save(devices)
    return {"token": token, "id": device["id"]}


# ───────────────────────── verification ─────────────────────────


def verify(token: str) -> Optional[Dict[str, Any]]:
    """Return the matching device (and bump lastSeen) or None.

    Constant-time-ish: every stored hash is compared with compare_digest so a
    caller cannot time which device (if any) matched.
    """
    if not token:
        return None
    supplied = _hash(token)
    devices = _load()
    matched: Optional[Dict[str, Any]] = None
    for device in devices:
        if secrets.compare_digest(device.get("tokenHash", ""), supplied):
            matched = device
    if not matched:
        return None

    now = int(time.time())
    if now - int(matched.get("lastSeen", 0)) >= _LASTSEEN_THROTTLE_SECONDS:
        matched["lastSeen"] = now
        _save(devices)
    return matched


# ───────────────────────── management ─────────────────────────


def list_devices() -> List[Dict[str, Any]]:
    """Devices without their hashes - safe to hand to the UI."""
    return [
        {k: v for k, v in d.items() if k != "tokenHash"}
        for d in sorted(_load(), key=lambda d: d.get("createdAt", 0), reverse=True)
    ]


def revoke(device_id: str) -> bool:
    devices = _load()
    remaining = [d for d in devices if d.get("id") != device_id]
    if len(remaining) == len(devices):
        return False
    _save(remaining)
    return True
