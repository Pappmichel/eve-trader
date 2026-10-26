"""Resolves player-structure names for one tenant (docs/LOCATION_RESOLUTION_PLAN.md
phases 2-4).

`resolve_pending` is the bulk entry point (run after every ESI sync pass and by
the Admin bulk job); `resolve_ids` is the on-demand one for a few ids. Both
register the ids involved, work out what each one is (Offices and containers
resolve through their parent and never reach ESI), then look up only the
structures that are due: never tried, a failure whose retry date has passed (or
whose capable characters changed), or a name older than the refresh age.

Outcomes are classified from the HTTP status: 403 forbidden (7 days), 404 not
found (30 days), anything else transient (short exponential backoff, never
hides an existing name). A run spends at most `budget` ESI calls; ids it did not
reach stay due for the next run. A run with nothing due builds no ESI client.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import storage
from ..esi_client import ESIError
from . import access, store
from .diagnostics import SOLAR_SYSTEM_RANGE, STRUCTURE_ID_MIN

log = logging.getLogger(__name__)

FORBIDDEN_RETRY = timedelta(days=7)
NOT_FOUND_RETRY = timedelta(days=30)
TRANSIENT_BASE = timedelta(minutes=15)
TRANSIENT_MAX = timedelta(hours=6)
REFRESH_AGE = timedelta(days=30)
DEFAULT_BUDGET = 200  # ESI calls per run

_STATUS_RE = re.compile(r"HTTP (\d{3})")
_running: set[str] = set()
_running_guard = threading.Lock()


def classify_error(exc: BaseException) -> tuple[str, str]:
    """(status, error text) for a failed lookup. The text is the HTTP status
    only; ESI's response body is never stored."""
    match = _STATUS_RE.search(str(exc))
    code = int(match.group(1)) if match else None
    if code == 403:
        return "forbidden", "HTTP 403"
    if code == 404:
        return "not_found", "HTTP 404"
    return "transient", f"HTTP {code}" if code else "network error"


def retry_after(status: str, attempts: int, now: datetime) -> datetime:
    if status == "forbidden":
        return now + FORBIDDEN_RETRY
    if status == "not_found":
        return now + NOT_FOUND_RETRY
    return now + min(TRANSIENT_MAX, TRANSIENT_BASE * (2 ** max(0, attempts)))


def characters_digest(characters: list[tuple[str, int, str]]) -> str:
    roles = ",".join(sorted(role for role, _cid, _name in characters))
    return hashlib.sha1(roles.encode()).hexdigest()[:12]


def _combine(outcomes: list[tuple[str, str]]) -> tuple[str, str]:
    """One status for an id several characters failed on: a transient error
    anywhere means we cannot call it forbidden yet; otherwise a refusal beats
    not-found (the structure exists but those characters cannot see it)."""
    for wanted in ("transient", "forbidden", "not_found"):
        for status, error in outcomes:
            if status == wanted:
                return status, error
    return outcomes[0]


def register_seen(now: datetime) -> dict:
    """Registers every location id any table of this tenant references."""
    sources = storage.location_id_sources()
    all_ids: set[int] = set().union(*sources.values()) if sources else set()
    return _register(all_ids, now)


def register_ids(location_ids, now: datetime) -> dict:
    """Registers just these ids (the on-demand path)."""
    return _register({int(i) for i in location_ids if i}, now)


def _register(all_ids: set[int], now: datetime) -> dict:
    """Adds every id that is not an NPC station or solar system to the
    registry, with its kind and (for Offices and containers) its parent. New
    structure ids start from what is already known: this tenant's
    structure_names, the shared global names."""
    if not all_ids:
        return {"registered": 0, "ids": 0}
    stations, offices, items = storage.location_kind_hints(all_ids)
    candidates = [
        i for i in all_ids
        if i not in stations and not (SOLAR_SYSTEM_RANGE[0] <= i < SOLAR_SYSTEM_RANGE[1])
    ]
    existing = store.load(candidates)
    parents = storage.asset_item_parents([i for i in candidates if i in offices or i in items])
    cached = storage.get_cached_structure_names(candidates)
    global_names = storage.get_global_structure_names([i for i in candidates if i not in existing])

    entries = []
    for i in candidates:
        if i in offices:
            kind = "office"
        elif i in items:
            kind = "container"
        elif i >= STRUCTURE_ID_MIN:
            kind = "structure"
        else:
            kind = "other"
        parent = parents.get(i) if kind in ("office", "container") else None
        old = existing.get(i)
        if old and old["kind"] == kind and old["parent_location_id"] == parent:
            continue
        entry = {"location_id": i, "kind": kind, "parent_location_id": parent}
        if old is None and kind == "structure":
            _, own_name = cached[i]
            if own_name:
                entry.update(name=own_name, status="resolved", resolved_at=now)
            elif i in global_names:
                entry.update(name=global_names[i][0], solar_system_id=global_names[i][1],
                             status="resolved", resolved_at=now)
        entries.append(entry)
    store.register(entries)
    return {"registered": len(entries), "ids": len(candidates)}


def resolve_pending(
    *, budget: int = DEFAULT_BUDGET, force: bool = False, progress_callback=None,
    now: Optional[datetime] = None, client=None,
    characters: Optional[list[tuple[str, int, str]]] = None,
    corp_roles: Optional[dict[int, str]] = None,
) -> dict:
    """See the module docstring. `client`, `characters` and `corp_roles` are
    injectable for tests; in production they are built only when something is
    due. Never raises for an ESI failure."""
    return _guarded(
        lambda now_: _run(now_, budget, force, progress_callback, client, characters, corp_roles,
                          scan=True, only_ids=None),
        now)


def resolve_ids(
    location_ids, *, force: bool = False, budget: int = DEFAULT_BUDGET, now: Optional[datetime] = None,
    client=None, characters: Optional[list[tuple[str, int, str]]] = None,
    corp_roles: Optional[dict[int, str]] = None,
) -> dict:
    """The on-demand path: registers just these ids and looks up the ones that
    are due (all of them with `force`). The summary carries `ids`:
    {location_id: {name, status, attempted}} read back from the registry, so a
    caller can tell a fresh answer from a remembered failure."""
    from . import reader

    ids = sorted({int(i) for i in location_ids if i})
    run_at = now or datetime.now(timezone.utc)
    summary = _guarded(
        lambda now_: _run(now_, budget, force, None, client, characters, corp_roles,
                          scan=False, only_ids=ids, register=ids),
        run_at)
    rows = store.load(ids)
    info = reader.describe(ids)
    summary["ids"] = {
        i: {
            "name": info[i].name,
            "status": (rows.get(i) or {}).get("status"),
            "attempted": bool((rows.get(i) or {}).get("last_attempt_at") and rows[i]["last_attempt_at"] >= run_at),
        }
        for i in ids
    }
    return summary


def _guarded(fn, now: Optional[datetime]) -> dict:
    tenant = storage.get_current_tenant()
    with _running_guard:
        if tenant in _running:
            return {"skipped": "already running"}
        _running.add(tenant)
    try:
        return fn(now or datetime.now(timezone.utc))
    finally:
        with _running_guard:
            _running.discard(tenant)


def _run(now, budget, force, progress_callback, client, characters, corp_roles, *, scan, only_ids,
         register=None) -> dict:
    summary = {"registered": 0, "due": 0, "resolved": 0, "from_global": 0, "forbidden": 0,
               "not_found": 0, "transient": 0, "calls": 0, "deferred": 0}
    if scan:
        summary["registered"] = register_seen(now)["registered"]
    elif register:
        summary["registered"] = register_ids(register, now)["registered"]

    if characters is None:
        characters = access.list_capability_characters("structure_name_resolution")
    digest = characters_digest(characters)
    todo = store.due(now, digest, now - REFRESH_AGE, limit=10_000, force=force, only_ids=only_ids)
    summary["due"] = len(todo)
    if not todo:
        return summary

    if not force:
        # Another tenant may already have named it: free, no ESI call.
        shared = storage.get_global_structure_names([r["location_id"] for r in todo if r["status"] == "pending"])
        for loc, (name, system_id) in shared.items():
            store.record_success(loc, name, system_id, None, now)
            storage.set_cached_structure_name(loc, name, system_id)
            summary["from_global"] += 1
        todo = [r for r in todo if r["location_id"] not in shared]
    if not todo:
        return summary
    if not characters:
        summary["skipped"] = "no character has Structure name resolution ticked"
        return summary

    if client is None:
        from ..auth import TokenManager
        from ..config import OAUTH_CONFIG
        from ..esi_client import ESIClient
        client = ESIClient(tokens=TokenManager(OAUTH_CONFIG))
        if corp_roles is None:
            corp_roles = access.corp_roles_for_characters(client, characters)
    corp_roles = corp_roles or {}

    listed: dict[int, tuple[Optional[str], Optional[int]]] = {}
    for corporation_id, role in corp_roles.items():
        summary["calls"] += 1
        try:
            for structure in client.corporation_structures(corporation_id, auth_role=role):
                listed[structure.get("structure_id")] = (structure.get("name"), structure.get("solar_system_id"))
        except ESIError:
            continue

    for index, row in enumerate(todo):
        loc = row["location_id"]
        if loc in listed and listed[loc][0]:
            name, system_id = listed[loc]
            _succeed(loc, name, system_id, None, now)
            summary["resolved"] += 1
            continue
        if summary["calls"] >= budget:
            summary["deferred"] = len(todo) - index
            break
        # The character that last succeeded for this structure goes first.
        ordered = sorted(characters, key=lambda c: c[0] != row.get("resolved_via"))
        outcomes: list[tuple[str, str]] = []
        found = None
        for role, _cid, _name in ordered:
            summary["calls"] += 1
            try:
                info = client.get_structure_name(loc, auth_role=role)
            except ESIError as e:
                outcomes.append(classify_error(e))
                continue
            if info.get("name"):
                found = (info["name"], info.get("solar_system_id"), role)
                break
            outcomes.append(("transient", "empty response"))
        if found:
            _succeed(loc, found[0], found[1], found[2], now)
            summary["resolved"] += 1
        else:
            status, error = _combine(outcomes)
            store.record_failure(loc, status, error, retry_after(status, row["attempts"], now), digest, now)
            summary[status] += 1
        if progress_callback and index % 25 == 0:
            progress_callback({"message": f"Resolved {summary['resolved']} of {len(todo)}"})
    return summary


def record_resolved(location_id: int, name: str, solar_system_id: Optional[int], via: Optional[str] = None,
                    now: Optional[datetime] = None) -> None:
    """For a name found outside the resolver (the operator fallback): registers
    the id if needed and stores the name everywhere the resolver would."""
    run_at = now or datetime.now(timezone.utc)
    register_ids([location_id], run_at)
    _succeed(location_id, name, solar_system_id, via, run_at)


def _succeed(loc: int, name: str, system_id: Optional[int], via: Optional[str], now: datetime) -> None:
    store.record_success(loc, name, system_id, via, now)
    # Write-through for the readers that still use the older tables.
    storage.set_cached_structure_name(loc, name, system_id)
    storage.upsert_global_structure_name(loc, name, system_id)
