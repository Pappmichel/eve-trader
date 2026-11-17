"""Encryption of stored SSO token records.

`tenant_tokens.record` holds `{"v": 1, "ct": "<fernet token>"}` instead of the
plaintext record. The key comes only from the environment
(`TOKEN_ENCRYPTION_KEY`: one or more Fernet keys, comma separated; the first
encrypts, every key decrypts, so a rotation is: put the new key first, run
`eve-trader tenant rotate-token-key`, then drop the old key). It is never
stored in the database, the repository or a backup.

The encrypted payload carries the whole record plus the `tenant_id` and `role`
it belongs to; both are checked on read, so a row copied to another tenant or
role is refused instead of decrypted for the wrong owner.

This module does not import `storage`. It fails closed: no key, a bad key, a
plaintext row or a failed decryption raise `TokenCryptoError` subclasses and
never return a record. These errors are not ESI errors: the orchestrator must
not read them as a dead owner (no stale clear).
"""
from __future__ import annotations

import json
import os
import threading
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

ENV_VAR = "TOKEN_ENCRYPTION_KEY"
FORMAT_VERSION = 1

GENERATE_COMMAND = (
    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)


class TokenCryptoError(RuntimeError):
    """Base class: a stored token cannot be used because of crypto state."""


class TokenKeyError(TokenCryptoError):
    """`TOKEN_ENCRYPTION_KEY` is missing or not a valid Fernet key list."""


class TokenDataError(TokenCryptoError):
    """A stored row is plaintext, damaged, written under another key, or
    belongs to another tenant or role."""


_lock = threading.Lock()
_cached: Optional[MultiFernet] = None


def reset_key_cache() -> None:
    """Forget the parsed key (tests, or after the environment changed)."""
    global _cached
    with _lock:
        _cached = None


def _parse_keys(raw: str) -> list[Fernet]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise TokenKeyError(
            f"{ENV_VAR} is not set. Generate a key with: {GENERATE_COMMAND}"
        )
    fernets = []
    for part in parts:
        try:
            fernets.append(Fernet(part.encode("ascii")))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise TokenKeyError(
                f"{ENV_VAR} holds an invalid Fernet key. Generate one with: {GENERATE_COMMAND}"
            ) from exc
    return fernets


def _multi() -> MultiFernet:
    global _cached
    cached = _cached
    if cached is not None:
        return cached
    with _lock:
        if _cached is None:
            _cached = MultiFernet(_parse_keys(os.environ.get(ENV_VAR, "")))
        return _cached


def require_key() -> None:
    """Raises `TokenKeyError` unless a usable key is configured."""
    _multi()


def is_encrypted(stored: object) -> bool:
    return isinstance(stored, dict) and stored.get("v") == FORMAT_VERSION and isinstance(stored.get("ct"), str)


def encrypt_record(tenant_id: str, role: str, record: dict) -> dict:
    payload = json.dumps(
        {"tenant_id": str(tenant_id), "role": role, "record": record},
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    token = _multi().encrypt(payload).decode("ascii")
    return {"v": FORMAT_VERSION, "ct": token}


def decrypt_record(tenant_id: str, role: str, stored: object) -> dict:
    """Returns the plain record. Raises `TokenKeyError` without a key and
    `TokenDataError` for a plaintext, undecryptable or misplaced row."""
    multi = _multi()
    if not is_encrypted(stored):
        raise TokenDataError("stored token row is not encrypted")
    try:
        payload = json.loads(multi.decrypt(stored["ct"].encode("ascii")))  # type: ignore[index]
    except (InvalidToken, ValueError) as exc:
        raise TokenDataError("stored token row cannot be decrypted with the configured key") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("tenant_id") != str(tenant_id)
        or payload.get("role") != role
        or not isinstance(payload.get("record"), dict)
    ):
        raise TokenDataError("stored token row does not belong to this tenant and role")
    return payload["record"]


def rotate_stored(stored: dict) -> dict:
    """Re-encrypts with the first key. Raises `TokenDataError` when the row
    cannot be decrypted with any configured key."""
    multi = _multi()
    if not is_encrypted(stored):
        raise TokenDataError("stored token row is not encrypted")
    try:
        token = multi.rotate(stored["ct"].encode("ascii")).decode("ascii")
    except InvalidToken as exc:
        raise TokenDataError("stored token row cannot be decrypted with the configured key") from exc
    return {"v": FORMAT_VERSION, "ct": token}
