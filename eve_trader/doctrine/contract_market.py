"""Contract Market (GitHub issue #428): scan public + alliance item-exchange
contracts in one solar system and match them against Doctrine fittings.

Results are stored in ``doctrine_market_*`` tables only - never in
``doctrine_contracts``, which is the owner-issued snapshot used by the
ampel, alerts and stockpile counts.

ESI facts this relies on (checked live 2026-11-06): GET /contracts/public/
{region}/ lists open contracts only and has no status or availability field.
There is no alliance contract endpoint (esi-issues #1218), but GET /corporations/
{id}/contracts/ also returns contracts assigned to the corporation's alliance,
from any member corporation, with readable items. Those rows say availability
"personal" (esi-issues #1447), so only assignee_id identifies them. The
documentation does not promise this, so it may change.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from .. import storage
from ..actions import ActionError, _emit_progress
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError, ESIRateLimited
from ..esi_data.access import shared_owner_ids
from ..esi_data.selector import select_auth_role
from ..locations import reader as location_reader
from ..locations import resolver as location_resolver
from . import engine
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import CONTRACT_TYPE_ITEM_EXCHANGE, VALIDATION_TOLERABLE, VALIDATION_VALID
from .models import ContractItemRow

log = logging.getLogger("eve_trader.doctrine.contract_market")

SOURCE_PUBLIC = "public"
SOURCE_ALLIANCE = "alliance"
_CORP_CONTRACTS_SCOPE = "esi-contracts.read_corporation_contracts.v1"
# What a buyer can use as is. Invalid fits and non-doctrine ships are not listed.
_KEEP_STATUSES = frozenset({VALIDATION_VALID, VALIDATION_TOLERABLE})
# Item lists never change, so a scan only fetches contracts it has not seen.
# The cap keeps one scan of a busy region from holding the tenant's job slot
# for many minutes; the next scan continues where this one stopped.
MAX_NEW_ITEM_FETCHES = 1000
MIN_SECONDS_BETWEEN_SCANS = 120
# Above this share of failed item reads the last good result is kept.
# The corp-contract route group allows 600 tokens per 15 min at 2 per read, per
# character, and any member of the corporation can read its list. Reads spread
# over every member's token, up to this many each, which leaves room for the
# scheduled contract sync of the same character.
MAX_READS_PER_CHARACTER = 200
_MAX_FAILED_ITEM_SHARE = 0.2
_VOLUME_MARGIN = 0.9
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


def _min_contract_volume(candidates: list, client: ESIClient) -> float:
    """No contract smaller than the smallest doctrine hull, packaged, can hold a
    fit. Most contracts are modules and the like: in The Forge 413 of 34,459 reach
    a packaged frigate's 2,500 m3 (2026-11-06), so this saves nearly all item reads.
    0 when any hull's volume is unknown, because a wrong guess hides real fits."""
    volumes = []
    for hull in {c.fitting.hull_type_id for c in candidates}:
        volume = storage.get_cached_packaged_volume(hull)
        if volume is None:
            try:
                volume = client.get_packaged_volume(hull)
            except ESIError:
                return 0.0
            if volume is None:
                return 0.0
            storage.set_cached_packaged_volume(hull, volume)
        volumes.append(volume)
    return min(volumes) * _VOLUME_MARGIN if volumes else 0.0


def _big_enough(contract: dict, min_volume: float) -> bool:
    volume = contract.get("volume")
    return volume is None or volume >= min_volume


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


def _resolved_location_systems(location_ids: list[int]) -> dict[int, int]:
    """Same, after looking unknown ids up. For the small own-contract list only."""
    ids = sorted({int(i) for i in location_ids if i})
    known = _known_location_systems(ids)
    missing = [i for i in ids if i not in known]
    if missing:
        try:
            location_resolver.resolve_ids(missing)
        except Exception:  # noqa: BLE001 - a resolve failure must not abort the scan
            log.info("location resolve for contract market failed for %s ids", len(missing),
                     exc_info=True)
        known = _known_location_systems(ids)
    return known


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


def _collect_public(client: ESIClient, region_id: int, system_id: int, min_volume: float = 0.0,
                    progress_callback=None, open_ids: Optional[set[int]] = None) -> tuple[list[dict], int]:
    """(contracts in the system, contracts at places this tenant cannot place)."""
    _emit_progress(progress_callback, {"phase": "public", "message": "Fetching public contracts"})
    try:
        raw = client.public_contracts(region_id)
    except ESIError as e:
        raise ActionError(f"Public contracts ESI failed: {e}") from e
    if not raw:
        previous = storage.get_doctrine_market_scan()
        if previous is not None and int(previous[1]) == system_id and previous[4]:
            raise ActionError("ESI returned an empty public contract list for a region that "
                              "had contracts. The last result was kept.")
    open_public = [c for c in raw if _is_open_public_item_exchange(c)]
    if open_ids is not None:
        open_ids.update(int(c["contract_id"]) for c in open_public)
    candidates = [c for c in open_public if _big_enough(c, min_volume)]
    system_of = _known_location_systems(
        [c.get("start_location_id") for c in candidates if c.get("start_location_id")])
    unknown = sum(1 for c in candidates if int(c.get("start_location_id") or 0) not in system_of)
    return _filter_by_system(candidates, system_id, system_of), unknown


class _CorpReaders:
    """The tokens of one corporation's members, used in turn for item reads."""

    def __init__(self, roles: list[str]):
        self.roles = roles
        self.reads: dict[str, int] = {}
        self.exhausted: set[str] = set()

    def next_role(self) -> Optional[str]:
        usable = [r for r in self.roles if r not in self.exhausted
                  and self.reads.get(r, 0) < MAX_READS_PER_CHARACTER]
        if not usable:
            return None
        return min(usable, key=lambda r: self.reads.get(r, 0))


def _corp_readers(corp_id: int, tm: TokenManager, client: ESIClient) -> tuple[list[str], Optional[int]]:
    """(auth roles of every member with the corp contract scope, alliance id)."""
    roles: list[str] = []
    alliance_id = None
    seen: set[int] = set()
    for rec in tm.list_records():
        if rec.character_id in seen:
            continue
        seen.add(rec.character_id)
        role = select_auth_role(rec.character_id, _CORP_CONTRACTS_SCOPE, tokens=tm)
        if role is None:
            continue
        try:
            info = client.character_public_info(rec.character_id)
        except ESIError:
            continue
        if info.get("corporation_id") == corp_id:
            roles.append(role)
            alliance_id = info.get("alliance_id")
    return roles, alliance_id


def _is_open_alliance_contract(contract: dict, alliance_id: int) -> bool:
    """assignee_id, not availability: ESI reports these rows as "personal"."""
    return (contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
            and contract.get("status") == "outstanding"
            and contract.get("assignee_id") == alliance_id
            and not _expired(contract.get("date_expired")))


class _NoAllianceSource(Exception):
    """No Doctrine-shared corporation has both an alliance and a member token
    that can read corporation contracts. Raised before any ESI call so a
    scan does not look like an empty alliance market."""


_NO_ALLIANCE_SOURCE_MSG = (
    "No character of a sharing corporation can read alliance contracts: "
    "the corporation needs an alliance and a member token with the "
    "corporation contracts scope. The last result was kept."
)


def _collect_alliance(client: ESIClient, tm: TokenManager, system_id: int, min_volume: float = 0.0,
                      progress_callback=None,
                      open_ids: Optional[set[int]] = None) -> tuple[list[dict], dict[int, tuple[int, _CorpReaders]]]:
    """Open contracts assigned to the alliance of a Doctrine-shared corporation.
    One successful list read per alliance: every member corp gets the same
    list, so only one corp reads it. Prefer the corp with the most member
    tokens (then lowest corp id) so item reads spread over the largest
    character budget; each character may only call its own corp's endpoint.
    Returns (contracts, hints) where hints map contract_id -> (corp_id, its
    readers) for the items fetch. Raises _NoAllianceSource when no shared
    corp can read (before any ESI call). Raises ActionError when every read
    failed, so an outage does not look like an empty alliance market.
    `open_ids` collects every open contract, in any system, so known item
    lists survive a change of system."""
    _emit_progress(progress_callback, {"phase": "alliance", "message": "Fetching alliance contracts"})
    # Discover readable sources first. Skipping every corp with reads==0 used
    # to look like a successful empty scan and wipe the last good snapshot.
    by_alliance: dict[int, list[tuple[int, list[str]]]] = {}
    for corp_id in shared_owner_ids("contracts", "doctrine", "corporation"):
        roles, alliance_id = _corp_readers(int(corp_id), tm, client)
        if roles and alliance_id:
            by_alliance.setdefault(int(alliance_id), []).append((int(corp_id), roles))
    if not by_alliance:
        raise _NoAllianceSource()

    by_id: dict[int, dict] = {}
    hints: dict[int, tuple[int, _CorpReaders]] = {}
    reads = 0
    failures = 0

    for alliance_id in sorted(by_alliance):
        # Most readers first (item budget), then lowest corp id for a stable pick.
        corps = sorted(by_alliance[alliance_id], key=lambda t: (-len(t[1]), t[0]))
        raw = None
        winning_corp: Optional[int] = None
        winning_readers: Optional[_CorpReaders] = None
        for corp_id, roles in corps:
            reads += 1
            corp_raw = None
            for role in roles:
                try:
                    corp_raw = client.corporation_contracts(corp_id, auth_role=role)
                    break
                except ESIRateLimited:
                    continue  # each character has its own bucket
                except ESIError as e:
                    log.info("alliance scan: corp %s contracts failed: %s", corp_id, e)
                    break
            if corp_raw is None:
                failures += 1
                continue
            raw = corp_raw
            winning_corp = corp_id
            winning_readers = _CorpReaders(roles)
            break
        if raw is None or winning_corp is None or winning_readers is None:
            continue
        for c in raw:
            if not _is_open_alliance_contract(c, alliance_id):
                continue
            if open_ids is not None:
                open_ids.add(int(c["contract_id"]))
            if _big_enough(c, min_volume):
                cid = int(c["contract_id"])
                by_id[cid] = c
                hints.setdefault(cid, (winning_corp, winning_readers))

    if reads and failures == reads:
        raise ActionError("Every alliance contract read failed. The last result was kept.")

    candidates = list(by_id.values())
    system_of = _resolved_location_systems(
        [c.get("start_location_id") for c in candidates if c.get("start_location_id")])
    kept = _filter_by_system(candidates, system_id, system_of)
    kept_ids = {int(c["contract_id"]) for c in kept}
    return kept, {cid: h for cid, h in hints.items() if cid in kept_ids}


class _NoReaderLeft(Exception):
    """Every character's budget for this scan is used up."""


def _read_alliance_items(client: ESIClient, cid: int, corp_id: int, readers: _CorpReaders) -> list[dict]:
    while True:
        role = readers.next_role()
        if role is None:
            raise _NoReaderLeft
        readers.reads[role] = readers.reads.get(role, 0) + 1
        try:
            return client.corporation_contract_items(corp_id, cid, auth_role=role)
        except ESIRateLimited:
            readers.exhausted.add(role)


def _fetch_items(client: ESIClient, contract: dict, source: str,
                 hint: Optional[tuple[int, _CorpReaders]] = None) -> list[ContractItemRow]:
    """hint: (corp_id, readers) for alliance items. Raises ESIError when the read
    failed, ESIRateLimited or _NoReaderLeft when there is no budget for it."""
    cid = int(contract["contract_id"])
    if source == SOURCE_PUBLIC:
        raw_items = client.public_contract_items(cid)
    else:
        if hint is None:
            raise ESIError("no token can read this contract")
        raw_items = _read_alliance_items(client, cid, hint[0], hint[1])
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
    (and is persisted via the action wrapper when provided). A source that
    failed outright or too many failed item reads raise ActionError before the
    replace, so the last good result stays.
    """
    sid = int(system_id) if system_id is not None else cfg.contract_market_system_id
    if not sid:
        raise ActionError("Pick a solar system for the Contract Market scan.")
    if not cfg.contract_market_include_public and not cfg.contract_market_include_alliance:
        raise ActionError("Enable Public and/or Alliance contracts in Doctrine Settings.")

    _system_id, system_name, region_id = _system_region(sid)
    tm = TokenManager(OAUTH_CONFIG)

    # Public needs no token, so a tenant without a readable alliance source
    # still gets the public half (alliance_skipped). Alliance-only with no
    # source raises before replace so the last good snapshot stays.
    scan_alliance = bool(cfg.contract_market_include_alliance)
    alliance_skipped = False

    candidates = engine.load_all_match_candidates()
    if not candidates:
        raise ActionError("No Doctrine fittings to match against.")

    client = ESIClient(tokens=tm)
    min_volume = _min_contract_volume(candidates, client)
    public_list: list[dict] = []
    alliance_list: list[dict] = []
    alliance_hints: dict[int, tuple[int, _CorpReaders]] = {}
    open_ids: set[int] = set()
    unknown_locations = 0
    if cfg.contract_market_include_public:
        public_list, unknown_locations = _collect_public(
            client, region_id, _system_id, min_volume, progress_callback, open_ids)
    if scan_alliance:
        try:
            alliance_list, alliance_hints = _collect_alliance(
                client, tm, _system_id, min_volume, progress_callback, open_ids)
        except _NoAllianceSource:
            if not cfg.contract_market_include_public:
                raise ActionError(_NO_ALLIANCE_SOURCE_MSG)
            alliance_skipped = True
            scan_alliance = False

    # Alliance wins on id collision (more specific view than public).
    combined: dict[int, tuple[str, dict]] = {}
    for c in public_list:
        combined[int(c["contract_id"])] = (SOURCE_PUBLIC, c)
    for c in alliance_list:
        combined[int(c["contract_id"])] = (SOURCE_ALLIANCE, c)

    _emit_progress(progress_callback, {
        "phase": "match", "message": "Matching contracts",
        "batch": 0, "total_batches": max(len(combined), 1),
    })

    known_items = storage.load_all_doctrine_market_items()
    item_rows: list[tuple] = []
    hits: list[tuple] = []
    new_fetches = failed_items = items_pending = asks_for_items = 0
    rate_limited: set[str] = set()
    failure_reasons: Counter[str] = Counter()
    for i, (cid, (source, raw)) in enumerate(sorted(combined.items())):
        if i % 10 == 0:
            _emit_progress(progress_callback, {
                "phase": "match", "message": f"Matching contracts ({i}/{len(combined)})",
                "batch": i, "total_batches": max(len(combined), 1),
            })
        items = known_items.get(cid)
        if items is None:
            if new_fetches >= MAX_NEW_ITEM_FETCHES or source in rate_limited:
                items_pending += 1
                continue
            hint = alliance_hints.get(cid) if source == SOURCE_ALLIANCE else None
            try:
                items = _fetch_items(client, raw, source, hint=hint)
                new_fetches += 1
            except (ESIRateLimited, _NoReaderLeft):
                rate_limited.add(source)
                items_pending += 1
                continue
            except ESIError as e:
                new_fetches += 1
                failed_items += 1
                failure_reasons[str(e).split(" for ")[0][:60]] += 1
                log.info("contract market items for %s failed: %s", cid, e)
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
        hits.append((cid, source, raw, fitting_id, score, status))

    if failed_items and (failed_items == new_fetches or (
            failed_items >= _MIN_FAILED_ITEMS_TO_ABORT
            and failed_items / new_fetches > _MAX_FAILED_ITEM_SHARE)):
        reason = failure_reasons.most_common(1)[0][0]
        raise ActionError(f"{failed_items} of {new_fetches} contract item reads failed, "
                          f"mostly {reason}. The last result was kept.")

    # Lists never change, so ones from another system of this tenant stay for as
    # long as the contract is open.
    for cid, items in known_items.items():
        if cid in open_ids and cid not in combined:
            item_rows.extend((cid, it.record_id, it.type_id, it.quantity,
                              it.is_included, it.is_singleton) for it in items)

    issuer_ids = sorted({int(h[2]["issuer_id"]) for h in hits if h[2].get("issuer_id")})
    try:
        names = client.resolve_names(issuer_ids) if issuer_ids else {}
    except ESIError:
        names = {}

    scanned_at = _now_iso()
    contract_rows: list[tuple] = []
    for cid, source, raw, fitting_id, score, status in hits:
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
    matched = len(contract_rows)

    storage.replace_doctrine_market_snapshot(
        contract_rows, item_rows,
        scanned_at=scanned_at, system_id=_system_id,
        include_public=bool(cfg.contract_market_include_public),
        include_alliance=scan_alliance,
        public_candidates=len(public_list),
        alliance_candidates=len(alliance_list),
        matched=matched,
        unknown_locations=unknown_locations,
        items_pending=items_pending,
        items_failed=failed_items,
        asks_for_items=asks_for_items,
        alliance_skipped=alliance_skipped,
    )
    return {
        "system_id": _system_id,
        "system_name": system_name,
        "region_id": region_id,
        "public_candidates": len(public_list),
        "alliance_candidates": len(alliance_list),
        "matched": matched,
        "scanned_at": scanned_at,
        "unknown_locations": unknown_locations,
        "items_pending": items_pending,
        "items_failed": failed_items,
        "asks_for_items": asks_for_items,
        "alliance_skipped": alliance_skipped,
    }


def _empty_market_fitting(row: tuple, hull_names: dict[int, Optional[str]]) -> dict:
    hull_type_id = row[4]
    hull_name = None
    if hull_type_id is not None:
        hull_name = hull_names.get(int(hull_type_id)) or str(hull_type_id)
    return {
        "fitting_id": str(row[0]),
        "fitting_name": row[2],
        "hull_type_id": hull_type_id,
        "hull_name": hull_name,
        "count": 0,
        "min_price": None,
        "avg_price": None,
        "contracts": [],
    }


def _market_doctrine_skeleton() -> tuple[dict[str, dict], dict[str, dict]]:
    """All configured doctrines and fittings (including inactive), each with
    count 0. Returns (doctrine_groups, fittings_by_id)."""
    all_fittings = storage.list_all_fittings()
    hull_ids = [int(r[4]) for r in all_fittings if r[4] is not None]
    hull_rows = storage.get_sde_types_bulk(hull_ids) if hull_ids else {}
    hull_names = {
        tid: (row[2] if row else None) for tid, row in hull_rows.items()
    }
    doctrine_groups: dict[str, dict] = {}
    for drow in storage.list_doctrines():
        doctrine_groups[str(drow[0])] = {
            "doctrine_id": str(drow[0]),
            "doctrine_name": drow[1],
            "fittings": [],
        }
    fittings_by_id: dict[str, dict] = {}
    for row in all_fittings:
        entry = _empty_market_fitting(row, hull_names)
        fittings_by_id[entry["fitting_id"]] = entry
        doctrine_id = str(row[1])
        if doctrine_id in doctrine_groups:
            doctrine_groups[doctrine_id]["fittings"].append(entry)
    return doctrine_groups, fittings_by_id


def market_results(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Grouped-by-doctrine view of the last Contract Market scan.

    Always lists every configured doctrine and fitting (including inactive
    and those with no targets), with count 0 when there is no match. A
    deleted fitting that still has stored hits appears under Other.
    """
    settings = {
        "system_id": cfg.contract_market_system_id,
        "include_public": cfg.contract_market_include_public,
        "include_alliance": cfg.contract_market_include_alliance,
    }
    scan = storage.get_doctrine_market_scan()
    contracts = storage.list_doctrine_market_contracts()
    doctrine_groups, fittings_by_id = _market_doctrine_skeleton()

    def _finalize(scan_view):
        doctrines_out = sorted(
            doctrine_groups.values(), key=lambda d: d["doctrine_name"].lower(),
        )
        for d in doctrines_out:
            d["fittings"].sort(key=lambda f: f["fitting_name"].lower())
        return {"scan": scan_view, "doctrines": doctrines_out, "settings": settings}

    if scan is None and not contracts:
        return _finalize(None)

    system_id = scan[1] if scan else (contracts[0][5] if contracts else None)
    system_name = None
    if system_id is not None:
        row = storage.get_solar_system(int(system_id))
        system_name = row[1] if row else None

    # Listings stay stored until the next scan: an expired one is gone in game,
    # and alliance rows follow the same sharing rule as every other shared read.
    alliance_visible = bool(shared_owner_ids("contracts", "doctrine", "corporation"))
    contracts = [c for c in contracts if not _expired(c[10])
                 and (c[1] != SOURCE_ALLIANCE or alliance_visible)]

    loc_ids = [c[4] for c in contracts if c[4] is not None]
    loc_infos = location_reader.describe(loc_ids) if loc_ids else {}

    # A contract counts for every fitting with the same identity, so identical
    # fits in two doctrines both show it (engine.pooled_contract_counts' rule).
    identity_of = {str(c.fitting.fitting_id): engine._fit_identity(c)
                   for c in engine.load_all_match_candidates()}
    same_fit: dict[tuple, list[str]] = {}
    for fitting_key, identity in identity_of.items():
        same_fit.setdefault(identity, []).append(fitting_key)
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
        listing = {
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
        }
        for target in same_fit.get(identity_of.get(fid), [fid]):
            by_fitting.setdefault(target, []).append(listing)

    for fid, clist in by_fitting.items():
        entry = fittings_by_id.get(fid)
        if entry is None:
            # Truly deleted fitting: only show under Other when it has hits.
            prices = [c["price"] for c in clist if c["price"] is not None]
            group = doctrine_groups.setdefault("_other", {
                "doctrine_id": None,
                "doctrine_name": "Other",
                "fittings": [],
            })
            group["fittings"].append({
                "fitting_id": fid,
                "fitting_name": "Deleted fitting",
                "hull_type_id": None,
                "hull_name": None,
                "count": len(clist),
                "min_price": min(prices) if prices else None,
                "avg_price": (sum(prices) / len(prices)) if prices else None,
                "contracts": sorted(clist, key=lambda c: (c["price"] is None, c["price"] or 0)),
            })
            continue
        prices = [c["price"] for c in clist if c["price"] is not None]
        entry["count"] = len(clist)
        entry["min_price"] = min(prices) if prices else None
        entry["avg_price"] = (sum(prices) / len(prices)) if prices else None
        entry["contracts"] = sorted(clist, key=lambda c: (c["price"] is None, c["price"] or 0))

    scan_view = None
    if scan is not None:
        (scanned_at, sys_id, include_public, include_alliance, pub_c, all_c, matched,
         unknown_locations, items_pending, items_failed, asks_for_items,
         alliance_skipped) = scan
        scan_view = {
            "scanned_at": scanned_at.isoformat() if hasattr(scanned_at, "isoformat") else scanned_at,
            "system_id": sys_id,
            "system_name": system_name,
            "include_public": include_public,
            "include_alliance": include_alliance,
            "public_candidates": pub_c,
            "alliance_candidates": all_c,
            "matched": matched,
            "unknown_locations": unknown_locations,
            "items_pending": items_pending,
            "items_failed": items_failed,
            "asks_for_items": asks_for_items,
            "alliance_skipped": bool(alliance_skipped),
        }
    return _finalize(scan_view)
