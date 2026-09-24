"""Corp/alliance allowlist policy for the access gate.

Pure decisions plus the one ESI affiliation fetch. No FastAPI. The login
callback, AccessGateMiddleware, /api/gate/status, and the admin actions all
call this module so they cannot disagree about who is allowed.

Constants live here, not in config: they are security policy, not a
per-tenant setting.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import NamedTuple, Optional

from . import storage
from .esi_client import ESIClient, ESIError

log = logging.getLogger("eve_trader.access_policy")

AFFILIATION_RECHECK_SECONDS = 6 * 3600
AFFILIATION_STALE_LIMIT_SECONDS = 7 * 24 * 3600
AFFILIATION_FETCH_TIMEOUT_SECONDS = 5

_locks_guard = threading.Lock()
_character_locks: dict[int, threading.Lock] = {}


class Verdict:
    OK = "ok"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


class Evaluation(NamedTuple):
    """`fresh_affiliation` is set only when this evaluation fetched ESI
    successfully. None means the verdict came from an exemption or from
    the stored affiliation."""
    verdict: str
    fresh_affiliation: Optional[tuple[int, Optional[int]]]


def _lock_for(character_id: int) -> threading.Lock:
    with _locks_guard:
        lock = _character_locks.get(character_id)
        if lock is None:
            lock = threading.Lock()
            _character_locks[character_id] = lock
        return lock


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_seconds(value: datetime) -> float:
    return (datetime.now(timezone.utc) - _as_utc(value)).total_seconds()


def is_allowed(corporation_id: Optional[int], alliance_id: Optional[int]) -> bool:
    """True if either id is on the allowlist."""
    return storage.allowlist_contains(corporation_id, alliance_id)


def allowlist_active() -> bool:
    """False while the allowlist is empty — deploying this changes nothing
    until the first corp or alliance is added."""
    return not storage.allowlist_is_empty()


def recheck_due(affiliation_checked_at: Optional[datetime], tool_keys: list[str],
                allowlist_active_flag: bool) -> bool:
    """Whether a session should fetch affiliation again. Admins and an empty
    allowlist are never due. A missing timestamp is due."""
    if "admin" in tool_keys or not allowlist_active_flag:
        return False
    if affiliation_checked_at is None:
        return True
    return _age_seconds(affiliation_checked_at) >= AFFILIATION_RECHECK_SECONDS


def fetch_affiliation(character_id: int) -> Optional[tuple[int, Optional[int]]]:
    """One public affiliation lookup. None on ESI failure, timeout, or an
    id ESI did not return. Short timeout: this runs on the login path and
    on a lazy session re-check, not a bulk sync."""
    try:
        found = ESIClient().character_affiliation(
            [character_id], timeout=AFFILIATION_FETCH_TIMEOUT_SECONDS, retries=1,
        )
    except ESIError:
        log.warning("affiliation lookup failed for character %s", character_id, exc_info=True)
        return None
    return found.get(int(character_id))


def _stored_usable(stored: Optional[dict]) -> bool:
    if not stored:
        return False
    checked = stored.get("affiliation_checked_at")
    if checked is None or stored.get("corporation_id") is None:
        return False
    return _age_seconds(checked) <= AFFILIATION_STALE_LIMIT_SECONDS


def evaluate_registered(character_id: int, tool_keys: list[str], stored: Optional[dict]) -> Evaluation:
    """ok | suspended | unknown.

    Admin grant and an empty allowlist are ok without a fetch. A fresh
    fetch is compared to the allowlist. If the fetch fails, a stored
    affiliation at most 7 days old decides; older than that (or missing)
    is unknown.
    """
    if "admin" in tool_keys or not allowlist_active():
        return Evaluation(Verdict.OK, None)
    fresh = fetch_affiliation(character_id)
    if fresh is not None:
        corp, alliance = fresh
        verdict = Verdict.OK if is_allowed(corp, alliance) else Verdict.SUSPENDED
        return Evaluation(verdict, fresh)
    if not _stored_usable(stored):
        return Evaluation(Verdict.UNKNOWN, None)
    stored_ok = is_allowed(stored["corporation_id"], stored.get("alliance_id"))
    return Evaluation(Verdict.OK if stored_ok else Verdict.SUSPENDED, None)


def refresh_registered(character_id: int, tool_keys: list[str], force: bool = False) -> str:
    """Re-check one registered character and write the registry row.

    Single-flight per character_id. Skips the ESI call when the last check
    is under 6 hours old, unless `force` (login always forces). Admin and
    an empty allowlist clear a stale suspension flag and do not fetch.
    """
    with _lock_for(character_id):
        stored = storage.get_registry_affiliation(character_id)
        if stored is None:
            return Verdict.UNKNOWN
        exempt = "admin" in tool_keys or not allowlist_active()
        if exempt:
            if stored["access_suspended"]:
                storage.update_registry_affiliation(
                    character_id, stored["corporation_id"], stored["alliance_id"],
                    False, touch_checked_at=False,
                )
            return Verdict.OK
        checked = stored.get("affiliation_checked_at")
        if not force and checked is not None and _age_seconds(checked) < AFFILIATION_RECHECK_SECONDS:
            return Verdict.SUSPENDED if stored["access_suspended"] else Verdict.OK
        evaluation = evaluate_registered(character_id, tool_keys, stored)
        if evaluation.fresh_affiliation is not None:
            corp, alliance = evaluation.fresh_affiliation
            storage.update_registry_affiliation(
                character_id, corp, alliance,
                evaluation.verdict == Verdict.SUSPENDED,
                touch_checked_at=True,
            )
        elif evaluation.verdict != Verdict.UNKNOWN:
            storage.update_registry_affiliation(
                character_id, stored["corporation_id"], stored["alliance_id"],
                evaluation.verdict == Verdict.SUSPENDED,
                touch_checked_at=False,
            )
        return evaluation.verdict
