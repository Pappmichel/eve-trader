"""Contract Market (GitHub issue #428): scan public item-exchange contracts in
one solar system and match them against Doctrine fittings.

Results are stored in ``doctrine_market_*`` tables only - never in
``doctrine_contracts``, which is the owner-issued snapshot used by the
ampel, alerts and stockpile counts.

ESI facts this relies on (checked live 2026-11-06): GET /contracts/public/
{region}/ lists open contracts only and has no status or availability field.
Alliance contracts are deliberately out: ESI has no endpoint for them, and a
live read of 3 corporations' and 21 characters' contract lists held none.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .. import storage
from ..actions import ActionError, _emit_progress
from ..esi_client import ESIClient, ESIError
from ..locations import reader as location_reader
from . import engine
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import CONTRACT_TYPE_ITEM_EXCHANGE, VALIDATION_TOLERABLE, VALIDATION_VALID
from .models import ContractItemRow

log = logging.getLogger("eve_trader.doctrine.contract_market")

# What a buyer can use as is. Invalid fits and non-doctrine ships are not listed.
_KEEP_STATUSES = frozenset({VALIDATION_VALID, VALIDATION_TOLERABLE})
# Item lists never change, so a scan only fetches contracts it has not seen.
# The cap keeps one scan of a busy region from holding the tenant's job slot
# for many minutes; the next scan continues where this one stopped.
MAX_NEW_ITEM_FETCHES = 1000
MIN_SECONDS_BETWEEN_SCANS = 120
# Above this share of failed item reads the last good result is kept.
_MAX_FAILED_ITEM_SHARE = 0.2
_MIN_FAILED_ITEMS_TO_ABORT = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expired(contract_date) -> bool:
    """True for a date in the past. Accepts ESI's ISO text and a stored datetime."""
    if not contract_date:
        return False
    when = contract_date
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            return False
    return when < datetime.now(timezone.utc)


def _is_open_public_item_exchange(contract: dict) -> bool:
    """The public list has no status: everything on it is open until it expires."""
    return (contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
            and not _expired(contract.get("date_expired")))


def _system_region(system_id: int) -> tuple[int, str, int]:
    """Returns (system_id, system_name, region_id). Raises ActionError if unknown."""
    row = storage.get_solar_system(int(system_id))
    if row is None:
        raise ActionError(f"Unknown solar system {system_id}.")
    region_id = row[3]
    if region_id is None:
        raise ActionError(f"Solar system {system_id} has no region in the SDE.")
    return int(row[0]), str(row[1]), int(region_id)


def reject_scan_too_soon(system_id: int) -> None:
    """One region list and possibly many item reads per scan: repeated clicks
    would spend the server's shared ESI budget for nothing new."""
    scan = storage.get_doctrine_market_scan()
    if scan is None or int(scan[1]) != int(system_id):
        return
    scanned_at = scan[0]
    if isinstance(scanned_at, str):
        scanned_at = datetime.fromisoformat(scanned_at.replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - scanned_at).total_seconds()
    if age < MIN_SECONDS_BETWEEN_SCANS:
        raise ActionError(
            f"This system was scanned {int(age)} s ago. Try again in "
            f"{int(MIN_SECONDS_BETWEEN_SCANS - age)} s.")


def _known_location_systems(location_ids: list[int]) -> dict[int, int]:
    """location id -> solar system for places this tenant already knows. Reads
    only: looking up every structure of a region would fill the location
    registry with strangers' structures and burn the ESI error budget on 403s."""
    ids = sorted({int(i) for i in location_ids if i})
    if not ids:
        return {}
    infos = location_reader.describe(ids)
    return {i: int(infos[i].solar_system_id) for i in ids
            if infos.get(i) is not None and infos[i].solar_system_id is not None}


def _filter_by_system(contracts: list[dict], system_id: int,
                      system_of: dict[int, int]) -> list[dict]:
    kept = []
    for c in contracts:
        loc = c.get("start_location_id")
        if loc is None:
            continue
        if system_of.get(int(loc)) == system_id:
            kept.append(c)
    return kept


def _collect_public(client: ESIClient, region_id: int, system_id: int,
                    progress_callback=None) -> tuple[list[dict], int]:
    """(contracts in the system, contracts at places this tenant cannot place)."""
    _emit_progress(progress_callback, {"phase": "public", "message": "Fetching public contracts"})
    try:
        raw = client.public_contracts(region_id)
    except ESIError as e:
        raise ActionError(f"Public contracts ESI failed: {e}") from e
    if not raw:
        previous = storage.get_doctrine_market_scan()
        if previous is not None and int(previous[1]) == system_id and previous[2]:
            raise ActionError("ESI returned an empty public contract list for a region that "
                              "had contracts. The last result was kept.")
    candidates = [c for c in raw if _is_open_public_item_exchange(c)]
    system_of = _known_location_systems(
        [c.get("start_location_id") for c in candidates if c.get("start_location_id")])
    unknown = sum(1 for c in candidates if int(c.get("start_location_id") or 0) not in system_of)
    return _filter_by_system(candidates, system_id, system_of), unknown


def _fetch_items(client: ESIClient, contract: dict) -> Optional[list[ContractItemRow]]:
    """None when the read failed."""
    cid = int(contract["contract_id"])
    try:
        raw_items = client.public_contract_items(cid)
    except ESIError as e:
        log.info("contract market items for %s failed: %s", cid, e)
        return None
    return [
        ContractItemRow(cid, it["record_id"], it["type_id"], it["quantity"],
                        it.get("is_included", True), it.get("is_singleton", False))
        for it in (raw_items or [])
    ]


def scan_contract_market(cfg: DoctrineConfig = DOCTRINE_CONFIG,
                         system_id: Optional[int] = None,
                         progress_callback=None) -> dict:
    """Fetch, filter, match and replace the Contract Market snapshot.

    ``system_id`` overrides ``cfg.contract_market_system_id`` for this run
    (and is persisted via the action wrapper when provided). A failed list read
    or too many failed item reads raise ActionError before the replace, so the
    last good result stays.
    """
    sid = int(system_id) if system_id is not None else cfg.contract_market_system_id
    if not sid:
        raise ActionError("Pick a solar system for the Contract Market scan.")

    _system_id, system_name, region_id = _system_region(sid)

    candidates = engine.load_match_candidates()
    if not candidates:
        raise ActionError("No active Doctrine fittings to match against.")

    client = ESIClient()
    public_list, unknown_locations = _collect_public(
        client, region_id, _system_id, progress_callback)
    combined = {int(c["contract_id"]): c for c in public_list}

    _emit_progress(progress_callback, {
        "phase": "match", "message": "Matching contracts",
        "batch": 0, "total_batches": max(len(combined), 1),
    })

    known_items = storage.load_all_doctrine_market_items()
    item_rows: list[tuple] = []
    hits: list[tuple] = []
    new_fetches = failed_items = items_pending = asks_for_items = 0
    for i, (cid, raw) in enumerate(sorted(combined.items())):
        if i % 10 == 0:
            _emit_progress(progress_callback, {
                "phase": "match", "message": f"Matching contracts ({i}/{len(combined)})",
                "batch": i, "total_batches": max(len(combined), 1),
            })
        items = known_items.get(cid)
        if items is None:
            if new_fetches >= MAX_NEW_ITEM_FETCHES:
                items_pending += 1
                continue
            new_fetches += 1
            items = _fetch_items(client, raw)
            if items is None:
                failed_items += 1
                continue
        for it in items:
            item_rows.append((cid, it.record_id, it.type_id, it.quantity,
                              it.is_included, it.is_singleton))
        # The price of a contract that also demands items from the buyer says
        # nothing about the fit: "1 ISK" plus 500 PLEX would win every minimum.
        if any(not it.is_included for it in items):
            asks_for_items += 1
            continue
        fitting_id, score, _devs, status = engine.match_and_validate_contract(
            cid, raw.get("title"), items, candidates, cfg)
        if fitting_id is None or status not in _KEEP_STATUSES:
            continue
        hits.append((cid, raw, fitting_id, score, status))

    if failed_items and (failed_items == new_fetches or (
            failed_items >= _MIN_FAILED_ITEMS_TO_ABORT
            and failed_items / new_fetches > _MAX_FAILED_ITEM_SHARE)):
        raise ActionError(f"{failed_items} of {new_fetches} contract item reads failed. "
                          "The last result was kept.")

    issuer_ids = sorted({int(h[1]["issuer_id"]) for h in hits if h[1].get("issuer_id")})
    try:
        names = client.resolve_names(issuer_ids) if issuer_ids else {}
    except ESIError:
        names = {}

    scanned_at = _now_iso()
    contract_rows: list[tuple] = []
    for cid, raw, fitting_id, score, status in hits:
        issuer_id = raw.get("issuer_id")
        contract_rows.append((
            cid,
            int(issuer_id) if issuer_id is not None else None,
            names.get(int(issuer_id)) if issuer_id is not None else None,
            raw.get("start_location_id"), _system_id,
            raw.get("status") or "outstanding",
            raw.get("title"), raw.get("price"),
            raw.get("date_issued"), raw.get("date_expired"),
            fitting_id, score, status, scanned_at,
        ))
    matched = len(contract_rows)

    storage.replace_doctrine_market_snapshot(
        contract_rows, item_rows,
        scanned_at=scanned_at, system_id=_system_id,
        public_candidates=len(public_list),
        matched=matched,
        unknown_locations=unknown_locations,
        items_pending=items_pending,
        items_failed=failed_items,
        asks_for_items=asks_for_items,
    )
    return {
        "system_id": _system_id,
        "system_name": system_name,
        "region_id": region_id,
        "public_candidates": len(public_list),
        "matched": matched,
        "scanned_at": scanned_at,
        "unknown_locations": unknown_locations,
        "items_pending": items_pending,
        "items_failed": failed_items,
        "asks_for_items": asks_for_items,
    }


def market_results(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Grouped-by-doctrine view of the last Contract Market scan."""
    scan = storage.get_doctrine_market_scan()
    contracts = storage.list_doctrine_market_contracts()
    if scan is None and not contracts:
        return {
            "scan": None,
            "doctrines": [],
            "settings": {"system_id": cfg.contract_market_system_id},
        }

    system_id = scan[1] if scan else (contracts[0][4] if contracts else None)
    system_name = None
    if system_id is not None:
        row = storage.get_solar_system(int(system_id))
        system_name = row[1] if row else None

    # Listings stay stored until the next scan; an expired one is gone in game.
    contracts = [c for c in contracts if not _expired(c[9])]

    loc_ids = [c[3] for c in contracts if c[3] is not None]
    loc_infos = location_reader.describe(loc_ids) if loc_ids else {}

    fittings = {str(r[0]): r for r in storage.list_active_fittings()}
    doctrines = {str(r[0]): r for r in storage.list_doctrines() if r[3]}  # active
    # A contract counts for every fitting with the same identity, so identical
    # fits in two doctrines both show it (engine.pooled_contract_counts' rule).
    identity_of = {str(c.fitting.fitting_id): engine._fit_identity(c)
                   for c in engine.load_match_candidates()}
    same_fit: dict[tuple, list[str]] = {}
    for fitting_key, identity in identity_of.items():
        same_fit.setdefault(identity, []).append(fitting_key)
    # fitting_id -> contracts
    by_fitting: dict[str, list[dict]] = {}
    for row in contracts:
        (cid, issuer_id, issuer_name, start_location_id, _sys, status, title,
         price, date_issued, date_expired, fitting_id, match_score, validation_status,
         scanned_at) = row
        fid = str(fitting_id) if fitting_id else None
        if not fid:
            continue
        loc = loc_infos.get(int(start_location_id)) if start_location_id else None
        listing = {
            "contract_id": int(cid),
            "issuer_id": issuer_id,
            "issuer_name": issuer_name,
            "start_location_id": start_location_id,
            "location_name": loc.name if loc else None,
            "status": status,
            "title": title,
            "price": price,
            "date_issued": date_issued.isoformat() if hasattr(date_issued, "isoformat") else date_issued,
            "date_expired": date_expired.isoformat() if hasattr(date_expired, "isoformat") else date_expired,
            "match_score": match_score,
            "validation_status": validation_status,
        }
        for target in same_fit.get(identity_of.get(fid), [fid]):
            by_fitting.setdefault(target, []).append(listing)

    doctrine_groups: dict[str, dict] = {}
    for fid, clist in by_fitting.items():
        fitting = fittings.get(fid)
        if fitting is None:
            # Inactive / deleted fitting: still show under a catch-all.
            doctrine_id = "_other"
            doctrine_name = "Other"
            fitting_name = "Deleted fitting"
            hull_type_id = None
            hull_name = None
        else:
            doctrine_id = str(fitting[1])
            drow = doctrines.get(doctrine_id)
            doctrine_name = drow[1] if drow else doctrine_id
            fitting_name = fitting[2]
            hull_type_id = fitting[4]
            hull_name = None
            if hull_type_id is not None:
                sde = storage.get_sde_type(int(hull_type_id))
                hull_name = sde[2] if sde else str(hull_type_id)
        prices = [c["price"] for c in clist if c["price"] is not None]
        group = doctrine_groups.setdefault(doctrine_id, {
            "doctrine_id": doctrine_id if doctrine_id != "_other" else None,
            "doctrine_name": doctrine_name,
            "fittings": [],
        })
        group["fittings"].append({
            "fitting_id": fid,
            "fitting_name": fitting_name,
            "hull_type_id": hull_type_id,
            "hull_name": hull_name,
            "count": len(clist),
            "min_price": min(prices) if prices else None,
            "avg_price": (sum(prices) / len(prices)) if prices else None,
            "contracts": sorted(clist, key=lambda c: (c["price"] is None, c["price"] or 0)),
        })

    doctrines_out = sorted(doctrine_groups.values(), key=lambda d: d["doctrine_name"].lower())
    for d in doctrines_out:
        d["fittings"].sort(key=lambda f: f["fitting_name"].lower())

    scan_view = None
    if scan is not None:
        (scanned_at, sys_id, pub_c, matched,
         unknown_locations, items_pending, items_failed, asks_for_items) = scan
        scan_view = {
            "scanned_at": scanned_at.isoformat() if hasattr(scanned_at, "isoformat") else scanned_at,
            "system_id": sys_id,
            "system_name": system_name,
            "public_candidates": pub_c,
            "matched": matched,
            "unknown_locations": unknown_locations,
            "items_pending": items_pending,
            "items_failed": items_failed,
            "asks_for_items": asks_for_items,
        }

    return {
        "scan": scan_view,
        "doctrines": doctrines_out,
        "settings": {"system_id": cfg.contract_market_system_id},
    }
