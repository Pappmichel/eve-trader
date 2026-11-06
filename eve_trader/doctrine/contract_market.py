"""Contract Market (GitHub issue #428): scan public + alliance item-exchange
contracts in one solar system and match them against Doctrine fittings.

Results are stored in ``doctrine_market_*`` tables only - never in
``doctrine_contracts``, which is the owner-issued snapshot used by the
ampel, alerts and stockpile counts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .. import storage
from ..actions import ActionError, _emit_progress
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from ..esi_data.access import shared_owner_ids
from ..esi_data.selector import select_auth_role
from ..locations import reader as location_reader
from ..locations import resolver as location_resolver
from . import engine
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import CONTRACT_TYPE_ITEM_EXCHANGE
from .esi_sync import list_shared_doctrine_characters
from .models import ContractItemRow

log = logging.getLogger("eve_trader.doctrine.contract_market")

SOURCE_PUBLIC = "public"
SOURCE_ALLIANCE = "alliance"
_CHAR_CONTRACTS_SCOPE = "esi-contracts.read_character_contracts.v1"
_CORP_CONTRACTS_SCOPE = "esi-contracts.read_corporation_contracts.v1"
# Matched statuses we keep; NO_HULL_MATCH / unmatched are not doctrine hits.
_KEEP_STATUSES = frozenset({"valid", "tolerable", "critical"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_outstanding_item_exchange(contract: dict) -> bool:
    return (
        contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
        and contract.get("status") == "outstanding"
    )


def _system_region(system_id: int) -> tuple[int, str, int]:
    """Returns (system_id, system_name, region_id). Raises ActionError if unknown."""
    row = storage.get_solar_system(int(system_id))
    if row is None:
        raise ActionError(f"Unknown solar system {system_id}.")
    region_id = row[3]
    if region_id is None:
        raise ActionError(f"Solar system {system_id} has no region in the SDE.")
    return int(row[0]), str(row[1]), int(region_id)


def _location_system_map(location_ids: list[int], client: ESIClient) -> dict[int, int]:
    """Resolve each start_location_id to a solar_system_id. Unknown structures
    are looked up once via the location resolver; still-unknown ids are omitted
    (those contracts are dropped by the system filter)."""
    if not location_ids:
        return {}
    ids = sorted({int(i) for i in location_ids if i})
    infos = location_reader.describe(ids)
    missing = [i for i in ids if infos.get(i) is None or infos[i].solar_system_id is None]
    if missing:
        try:
            location_resolver.resolve_ids(missing)
        except Exception:  # noqa: BLE001 - a resolve failure must not abort the scan
            log.info("location resolve for contract market failed for %s ids", len(missing),
                     exc_info=True)
        infos = location_reader.describe(ids)
    out: dict[int, int] = {}
    for i in ids:
        info = infos.get(i)
        if info is not None and info.solar_system_id is not None:
            out[i] = int(info.solar_system_id)
    return out


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
                    progress_callback=None) -> list[dict]:
    _emit_progress(progress_callback, {"phase": "public", "message": "Fetching public contracts"})
    try:
        raw = client.public_contracts(region_id)
    except ESIError as e:
        raise ActionError(f"Public contracts ESI failed: {e}") from e
    candidates = [c for c in raw if _is_outstanding_item_exchange(c)]
    locs = [c.get("start_location_id") for c in candidates if c.get("start_location_id")]
    system_of = _location_system_map(locs, client)
    return _filter_by_system(candidates, system_id, system_of)


def _corp_auth_role(corp_id: int, tm: TokenManager, client: ESIClient) -> Optional[str]:
    """First token that can read corp contracts for ``corp_id``."""
    for rec in tm.list_records():
        role = select_auth_role(rec.character_id, _CORP_CONTRACTS_SCOPE, tokens=tm)
        if role is None:
            continue
        try:
            info = client.character_public_info(rec.character_id)
        except ESIError:
            continue
        if info.get("corporation_id") == corp_id:
            return role
    return None


def _collect_alliance(client: ESIClient, tm: TokenManager, system_id: int,
                      progress_callback=None) -> tuple[list[dict], dict[int, tuple[str, str, int]]]:
    """Alliance-available contracts visible to Doctrine-shared characters/corps.
    No 'issued by us' filter - these are market listings, not own contracts.
    Returns (contracts, item_auth_hints) where hints map contract_id ->
    (owner_type, auth_role, owner_id) for the items fetch."""
    _emit_progress(progress_callback, {"phase": "alliance", "message": "Fetching alliance contracts"})
    by_id: dict[int, dict] = {}
    hints: dict[int, tuple[str, str, int]] = {}

    for role, character_id, _name in list_shared_doctrine_characters(tm):
        try:
            raw = client.character_contracts(character_id, auth_role=role)
        except ESIError as e:
            log.info("alliance scan: character %s contracts failed: %s", character_id, e)
            continue
        for c in raw:
            if not _is_outstanding_item_exchange(c):
                continue
            if c.get("availability") != "alliance":
                continue
            cid = int(c["contract_id"])
            by_id[cid] = c
            hints.setdefault(cid, ("character", role, character_id))

    for corp_id in shared_owner_ids("contracts", "doctrine", "corporation"):
        role = _corp_auth_role(int(corp_id), tm, client)
        if role is None:
            continue
        try:
            raw = client.corporation_contracts(int(corp_id), auth_role=role)
        except ESIError as e:
            log.info("alliance scan: corp %s contracts failed: %s", corp_id, e)
            continue
        for c in raw:
            if not _is_outstanding_item_exchange(c):
                continue
            if c.get("availability") != "alliance":
                continue
            cid = int(c["contract_id"])
            by_id[cid] = c
            hints.setdefault(cid, ("corporation", role, int(corp_id)))

    candidates = list(by_id.values())
    locs = [c.get("start_location_id") for c in candidates if c.get("start_location_id")]
    system_of = _location_system_map(locs, client)
    kept = _filter_by_system(candidates, system_id, system_of)
    kept_ids = {int(c["contract_id"]) for c in kept}
    return kept, {cid: h for cid, h in hints.items() if cid in kept_ids}


def _items_from_stored(contract_id: int) -> Optional[list[ContractItemRow]]:
    rows = storage.load_doctrine_market_contract_items(contract_id)
    if not rows:
        return None
    return [ContractItemRow(contract_id, r[0], r[1], r[2], r[3], r[4]) for r in rows]


def _fetch_items(client: ESIClient, contract: dict, source: str,
                 auth_hint: Optional[tuple[str, str, int]] = None) -> Optional[list[ContractItemRow]]:
    """auth_hint: (owner_type, auth_role, owner_id) for alliance items."""
    cid = int(contract["contract_id"])
    stored = _items_from_stored(cid)
    if stored is not None:
        return stored
    try:
        if source == SOURCE_PUBLIC:
            raw_items = client.public_contract_items(cid)
        elif auth_hint is not None:
            owner_type, role, owner_id = auth_hint
            if owner_type == "corporation":
                raw_items = client.corporation_contract_items(owner_id, cid, auth_role=role)
            else:
                raw_items = client.character_contract_items(owner_id, cid, auth_role=role)
        else:
            return None
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
    (and is persisted via the action wrapper when provided).
    """
    sid = int(system_id) if system_id is not None else cfg.contract_market_system_id
    if not sid:
        raise ActionError("Pick a solar system for the Contract Market scan.")
    if not cfg.contract_market_include_public and not cfg.contract_market_include_alliance:
        raise ActionError("Enable Public and/or Alliance contracts in Doctrine Settings.")

    _system_id, system_name, region_id = _system_region(sid)
    tm = TokenManager(OAUTH_CONFIG)
    client = ESIClient(tokens=tm)

    public_list: list[dict] = []
    alliance_list: list[dict] = []
    alliance_hints: dict[int, tuple[str, str, int]] = {}
    if cfg.contract_market_include_public:
        public_list = _collect_public(client, region_id, _system_id, progress_callback)
    if cfg.contract_market_include_alliance:
        if not list_shared_doctrine_characters(tm) and not shared_owner_ids(
                "contracts", "doctrine", "corporation"):
            raise ActionError(
                "Alliance scan needs a character sharing Contracts with Doctrine "
                "(Characters page)."
            )
        alliance_list, alliance_hints = _collect_alliance(
            client, tm, _system_id, progress_callback)

    # Alliance wins on id collision (more specific view than public).
    combined: dict[int, tuple[str, dict]] = {}
    for c in public_list:
        combined[int(c["contract_id"])] = (SOURCE_PUBLIC, c)
    for c in alliance_list:
        combined[int(c["contract_id"])] = (SOURCE_ALLIANCE, c)

    candidates = engine.load_match_candidates()
    if not candidates:
        raise ActionError("No active Doctrine fittings to match against.")

    _emit_progress(progress_callback, {
        "phase": "match", "message": "Matching contracts",
        "batch": 0, "total_batches": max(len(combined), 1),
    })

    issuer_ids = sorted({int(c.get("issuer_id")) for _src, c in combined.values()
                         if c.get("issuer_id")})
    try:
        names = client.resolve_names(issuer_ids) if issuer_ids else {}
    except ESIError:
        names = {}

    scanned_at = _now_iso()
    contract_rows: list[tuple] = []
    item_rows: list[tuple] = []
    matched = 0
    for i, (cid, (source, raw)) in enumerate(sorted(combined.items())):
        if i % 10 == 0:
            _emit_progress(progress_callback, {
                "phase": "match", "message": f"Matching contracts ({i}/{len(combined)})",
                "batch": i, "total_batches": max(len(combined), 1),
            })
        hint = alliance_hints.get(cid) if source == SOURCE_ALLIANCE else None
        items = _fetch_items(client, raw, source, auth_hint=hint)
        if items is None:
            continue
        fitting_id, score, _devs, status = engine.match_and_validate_contract(
            cid, raw.get("title"), items, candidates, cfg)
        if fitting_id is None or status not in _KEEP_STATUSES:
            continue
        matched += 1
        issuer_id = raw.get("issuer_id")
        contract_rows.append((
            cid, source,
            int(issuer_id) if issuer_id is not None else None,
            names.get(int(issuer_id)) if issuer_id is not None else None,
            raw.get("start_location_id"), _system_id,
            raw.get("status") or "outstanding",
            raw.get("title"), raw.get("price"),
            raw.get("date_issued"), raw.get("date_expired"),
            fitting_id, score, status, scanned_at,
        ))
        for it in items:
            item_rows.append((cid, it.record_id, it.type_id, it.quantity,
                              it.is_included, it.is_singleton))

    storage.replace_doctrine_market_snapshot(
        contract_rows, item_rows,
        scanned_at=scanned_at, system_id=_system_id,
        include_public=bool(cfg.contract_market_include_public),
        include_alliance=bool(cfg.contract_market_include_alliance),
        public_candidates=len(public_list),
        alliance_candidates=len(alliance_list),
        matched=matched,
    )
    return {
        "system_id": _system_id,
        "system_name": system_name,
        "region_id": region_id,
        "public_candidates": len(public_list),
        "alliance_candidates": len(alliance_list),
        "matched": matched,
        "scanned_at": scanned_at,
    }


def market_results(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Grouped-by-doctrine view of the last Contract Market scan."""
    scan = storage.get_doctrine_market_scan()
    contracts = storage.list_doctrine_market_contracts()
    if scan is None and not contracts:
        return {
            "scan": None,
            "doctrines": [],
            "settings": {
                "system_id": cfg.contract_market_system_id,
                "include_public": cfg.contract_market_include_public,
                "include_alliance": cfg.contract_market_include_alliance,
            },
        }

    system_id = scan[1] if scan else (contracts[0][5] if contracts else None)
    system_name = None
    if system_id is not None:
        row = storage.get_solar_system(int(system_id))
        system_name = row[1] if row else None

    loc_ids = [c[4] for c in contracts if c[4] is not None]
    loc_infos = location_reader.describe(loc_ids) if loc_ids else {}

    fittings = {str(r[0]): r for r in storage.list_active_fittings()}
    doctrines = {str(r[0]): r for r in storage.list_doctrines() if r[3]}  # active
    # fitting_id -> contracts
    by_fitting: dict[str, list[dict]] = {}
    for row in contracts:
        (cid, source, issuer_id, issuer_name, start_location_id, _sys, status, title,
         price, date_issued, date_expired, fitting_id, match_score, validation_status,
         scanned_at) = row
        fid = str(fitting_id) if fitting_id else None
        if not fid:
            continue
        loc = loc_infos.get(int(start_location_id)) if start_location_id else None
        by_fitting.setdefault(fid, []).append({
            "contract_id": int(cid),
            "source": source,
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
        })

    doctrine_groups: dict[str, dict] = {}
    for fid, clist in by_fitting.items():
        fitting = fittings.get(fid)
        if fitting is None:
            # Inactive / deleted fitting: still show under a catch-all.
            doctrine_id = "_other"
            doctrine_name = "Other"
            fitting_name = fid
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
        scanned_at, sys_id, include_public, include_alliance, pub_c, all_c, matched = scan
        scan_view = {
            "scanned_at": scanned_at.isoformat() if hasattr(scanned_at, "isoformat") else scanned_at,
            "system_id": sys_id,
            "system_name": system_name,
            "include_public": include_public,
            "include_alliance": include_alliance,
            "public_candidates": pub_c,
            "alliance_candidates": all_c,
            "matched": matched,
        }

    return {
        "scan": scan_view,
        "doctrines": doctrines_out,
        "settings": {
            "system_id": cfg.contract_market_system_id,
            "include_public": cfg.contract_market_include_public,
            "include_alliance": cfg.contract_market_include_alliance,
        },
    }
