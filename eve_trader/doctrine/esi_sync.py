"""ESI sync for the Doctrine tool - two entirely independent flows sharing
this one module because they're both "Doctrine's own ESI sync", nothing
more:

1. Contract sync (Phase 2 spec E.3, Phase 3 spec D.3's error table): pulls
   character + corporation item_exchange contracts for every registered
   "doctrine:<character_id>" character, pre-filters to this app's own
   structure before ever fetching a contract's items (items are 1 ESI call
   per contract), matches each surviving contract against the active
   Fitting pool (doctrine/engine.py), and writes one full snapshot.
2. Asset sync (sync_assets, below): pulls character + corporation assets
   for every registered "doctrine-assets:<character_id>" character into
   Doctrine's own doctrine_character_assets/doctrine_corp_assets tables -
   deliberately a second, separate character-auth group from #1, not the
   same "doctrine" role and not a read of Production's own asset tables (an
   earlier design that assumed Stockpile could depend on Production ever
   being set up - reversed after real use: a Doctrine-only tenant must be
   able to use Stockpile standalone). Splitting the two ESI scope groups
   also means a character that should only ever read contracts is never
   asked to grant asset-read access, and vice versa.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG
from ..esi_client import ESIClient, ESIError
from . import engine
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import CONTRACT_TYPE_ITEM_EXCHANGE, SYNCABLE_CONTRACT_STATUSES
from .models import ContractItemRow

log = logging.getLogger("eve_trader.doctrine.esi_sync")

DOCTRINE_ROLE_PREFIX = "doctrine"
DOCTRINE_SCOPES = [
    "esi-contracts.read_character_contracts.v1",
    "esi-contracts.read_corporation_contracts.v1",
    "esi-universe.read_structures.v1",   # structure name resolution, same as Production
]

# Separate role/scope group from DOCTRINE_ROLE_PREFIX above - see this
# module's own docstring point 2 for why asset-scanning characters are kept
# distinct from contract-reading ones.
DOCTRINE_ASSET_ROLE_PREFIX = "doctrine-assets"
DOCTRINE_ASSET_SCOPES = [
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
]


def list_doctrine_characters(tm: Optional[TokenManager] = None) -> list[tuple[str, int, str]]:
    """Returns (role_key, character_id, character_name) for every registered
    doctrine character - same get_record (no refresh) pattern as
    production/esi_sync.py's list_producer_characters, for the same reason
    (this runs on every "characters" sidebar render, one dead token must
    not take the whole list down)."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    out = []
    for role in tm.list_roles(DOCTRINE_ROLE_PREFIX):
        record = tm.get_record(role)
        if record is not None:
            out.append((role, record.character_id, record.character_name))
    return out


def _not_expired(date_expired: Optional[str]) -> bool:
    if not date_expired:
        return True
    try:
        expired_at = datetime.fromisoformat(date_expired.replace("Z", "+00:00"))
    except ValueError:
        return True
    return expired_at > datetime.now(timezone.utc)


def _passes_prefilter(contract: dict, structure_id: Optional[int]) -> bool:
    """Phase 2 E.3 point 2 - applied before any items fetch, never after."""
    return (
        contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
        and contract.get("status") in SYNCABLE_CONTRACT_STATUSES
        and structure_id is not None
        and contract.get("start_location_id") == structure_id
        and _not_expired(contract.get("date_expired"))
    )


def _fetch_character_contracts(client: ESIClient, role: str, character_id: int) -> dict:
    """One character's own contracts + (best-effort) corporation_id - errors
    are returned, not raised, so one bad token/scope only drops that
    character (Phase 3 spec D.3's degradation table)."""
    result: dict = {"role": role, "contracts": [], "corporation_id": None, "error": None}
    try:
        result["contracts"] = client.character_contracts(character_id, auth_role=role)
    except ESIError as e:
        result["error"] = str(e)
        return result
    try:
        result["corporation_id"] = client.character_public_info(character_id)["corporation_id"]
    except ESIError:
        pass  # corp contracts just won't be attempted for this character
    return result


def sync_contracts(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Phase 2 spec E.3, full flow. Raises ActionError only if no doctrine
    character is registered at all, or the structure isn't configured -
    every per-character/per-contract failure degrades into the returned
    report instead (Phase 3 spec D.3)."""
    structure_id = cfg.effective_structure_id
    if structure_id is None:
        raise ActionError(
            "No structure configured for Doctrine contract sync. Set it in Doctrine Settings "
            "(or Trading's own structure_id, which this falls back to)."
        )

    tm = TokenManager(OAUTH_CONFIG)
    characters = list_doctrine_characters(tm)
    if not characters:
        raise ActionError("No doctrine character logged in yet. Use 'Add Character' in the sidebar.")

    client = ESIClient(tokens=tm)
    per_character: dict = {}
    # (raw_contract, source_role, for_corporation, corporation_id) -
    # corporation_id is the corp the contract-list call itself used (the
    # required path param for corporation_contract_items below) - NOT
    # necessarily the same as the contract's own issuer_corporation_id, a
    # real ESI distinction (a corp can see a contract it's merely assigned
    # to, issued by someone else).
    all_contracts: list[tuple[dict, str, bool, Optional[int]]] = []
    seen_contract_ids: set[int] = set()
    corp_contracts_done: dict[int, list[dict]] = {}
    corp_error_by_id: dict[int, str] = {}

    for role, character_id, character_name in characters:
        result = _fetch_character_contracts(client, role, character_id)
        if result["error"] is not None:
            per_character[character_name] = f"skipped ({result['error']})"
            continue
        char_contracts = [c for c in result["contracts"] if _passes_prefilter(c, structure_id)]
        for c in char_contracts:
            if c["contract_id"] not in seen_contract_ids:
                seen_contract_ids.add(c["contract_id"])
                all_contracts.append((c, role, False, None))
        per_character[character_name] = {"contracts_seen": len(result["contracts"]),
                                          "contracts_matched_filter": len(char_contracts)}

        corporation_id = result["corporation_id"]
        if corporation_id is None or corporation_id in corp_contracts_done or corporation_id in corp_error_by_id:
            continue
        try:
            corp_raw = client.corporation_contracts(corporation_id, auth_role=role)
        except ESIError as e:
            corp_error_by_id[corporation_id] = str(e)  # a later character in this corp might have access
            continue
        corp_contracts_done[corporation_id] = corp_raw
        corp_filtered = [c for c in corp_raw if _passes_prefilter(c, structure_id)]
        for c in corp_filtered:
            if c["contract_id"] not in seen_contract_ids:
                seen_contract_ids.add(c["contract_id"])
                all_contracts.append((c, role, True, corporation_id))

    if not all_contracts and not per_character:
        raise ActionError("No doctrine character's contracts could be fetched - check tokens/scopes.")

    # Phase 2 E.3 point 3: only fetch items for contracts that are new or
    # whose status changed since the last snapshot - an already-known,
    # unchanged contract's items are immutable in ESI and just carried
    # forward from the existing snapshot instead.
    existing_by_id = {row[0]: row for row in storage.load_doctrine_contracts()}
    to_fetch: list[tuple[dict, str, bool, Optional[int]]] = []
    carried_items: dict[int, list[tuple]] = {}
    for raw, role, for_corp, corp_id in all_contracts:
        cid = raw["contract_id"]
        existing = existing_by_id.get(cid)
        if existing is not None and existing[5] == raw.get("status"):
            carried_items[cid] = storage.load_doctrine_contract_items(cid)
        else:
            to_fetch.append((raw, role, for_corp, corp_id))

    def _fetch_items(entry: tuple[dict, str, bool, Optional[int]]) -> tuple[int, Optional[list[dict]], Optional[str]]:
        raw, role, for_corp, corp_id = entry
        cid = raw["contract_id"]
        try:
            if for_corp:
                items = client.corporation_contract_items(corp_id, cid, auth_role=role)
            else:
                character_id = next(c_id for r, c_id, _n in characters if r == role)
                items = client.character_contract_items(character_id, cid, auth_role=role)
            return cid, items, None
        except ESIError as e:
            return cid, None, str(e)

    fetched_items: dict[int, list[dict]] = {}
    fetch_errors: dict[int, str] = {}
    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(8, len(to_fetch))) as pool:
            for cid, items, error in pool.map(storage.with_current_tenant(_fetch_items), to_fetch):
                if error is not None:
                    fetch_errors[cid] = error
                else:
                    fetched_items[cid] = items or []

    # Contracts whose items fetch failed this run are dropped from this
    # snapshot entirely (Phase 3 spec D.3: never write a contract with no
    # items - that would look like a real "invalid" doctrine violation
    # instead of a transient data gap). They're simply retried next sync.
    usable = [(raw, role, for_corp) for raw, role, for_corp, _corp_id in all_contracts
              if raw["contract_id"] in carried_items or raw["contract_id"] in fetched_items]

    candidates = engine.load_match_candidates()
    contract_rows: list[tuple] = []
    item_rows: list[tuple] = []
    deviation_rows: list[tuple] = []
    synced_at = datetime.now(timezone.utc).isoformat()

    for raw, role, for_corp in usable:
        cid = raw["contract_id"]
        raw_items = carried_items.get(cid)
        if raw_items is not None:
            items = [ContractItemRow(cid, record_id, type_id, qty, bool(is_incl), bool(is_single))
                     for record_id, type_id, qty, is_incl, is_single in raw_items]
        else:
            items = [ContractItemRow(cid, it["record_id"], it["type_id"], it["quantity"],
                                      it.get("is_included", True), it.get("is_singleton", False))
                     for it in fetched_items[cid]]

        matched_fitting_id, score, deviations, status = engine.match_and_validate_contract(
            cid, raw.get("title"), items, candidates, cfg)

        contract_rows.append((
            cid, role, for_corp, raw.get("issuer_id"), raw.get("start_location_id"), raw.get("status"),
            raw.get("title"), raw.get("price"), raw.get("date_expired"), matched_fitting_id, score,
            status, synced_at,
        ))
        for it in items:
            item_rows.append((cid, it.record_id, it.type_id, it.quantity, it.is_included, it.is_singleton))
        for d in deviations:
            deviation_rows.append((cid, d.type_id, d.kind, d.expected_qty, d.actual_qty, d.severity))

    with storage.batch_session():
        storage.replace_doctrine_sync_snapshot(contract_rows, item_rows, deviation_rows)
        storage.set_esi_sync_time("doctrine", synced_at)

    return {
        "characters": per_character,
        "contracts_synced": len(contract_rows),
        "contracts_dropped_this_run": len(all_contracts) - len(usable),
        "corp_errors": {str(k): v for k, v in corp_error_by_id.items()},
        "item_fetch_errors": {str(k): v for k, v in fetch_errors.items()},
    }


# =========================================================== asset sync (Stockpile)
def list_doctrine_asset_characters(tm: Optional[TokenManager] = None) -> list[tuple[str, int, str]]:
    """Returns (role_key, character_id, character_name) for every registered
    asset-scanning character - same get_record (no refresh) pattern as
    list_doctrine_characters above, for the same reason."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    out = []
    for role in tm.list_roles(DOCTRINE_ASSET_ROLE_PREFIX):
        record = tm.get_record(role)
        if record is not None:
            out.append((role, record.character_id, record.character_name))
    return out


def _asset_rows(assets: list[dict]) -> list[tuple]:
    return [
        (a["item_id"], a["type_id"], a["location_id"], a["location_flag"],
         a["quantity"], int(bool(a.get("is_blueprint_copy"))), a.get("owner_name"))
        for a in assets
    ]


def _fetch_character_assets(client: ESIClient, role: str, character_id: int, character_name: str) -> dict:
    """The parallelizable, per-character half of sync_assets (Phase A) -
    mirrors production/esi_sync.py's _fetch_character_data, narrowed to just
    assets + the corp lookup the corp-asset half (Phase B, sequential) needs."""
    result: dict = {"character_name": character_name, "role": role, "assets": [],
                     "corporation_id": None, "error": None}
    try:
        assets = client.character_assets(character_id, auth_role=role)
    except ESIError as e:
        result["error"] = str(e)
        return result
    for a in assets:
        a["owner_name"] = character_name
    result["assets"] = assets
    try:
        result["corporation_id"] = client.character_public_info(character_id)["corporation_id"]
    except ESIError:
        pass  # corp assets just won't be attempted for this character
    return result


def sync_assets() -> dict:
    """Doctrine's own independent asset sync (Stockpile's Ist side) - see
    this module's own top docstring point 2 for why this is deliberately
    separate from Production's do_sync_esi/sync_esi, even though the
    mechanics are nearly identical. Same two-phase shape as production/
    esi_sync.py's sync_esi (see its own docstring for the full reasoning):
    Phase A (parallel, per character) fetches each character's own assets;
    Phase B (sequential) fetches each distinct corp's assets once, retried
    with a later character in the same corp if an earlier one lacks the
    Director role."""
    tm = TokenManager(OAUTH_CONFIG)
    characters = list_doctrine_asset_characters(tm)
    if not characters:
        raise ActionError(
            "No asset-scanning character logged in yet. Use 'Add Character' under Stockpile in the sidebar."
        )
    client = ESIClient(tokens=tm)

    with ThreadPoolExecutor(max_workers=min(8, len(characters))) as pool:
        char_results = list(pool.map(
            storage.with_current_tenant(lambda c: _fetch_character_assets(client, c[0], c[1], c[2])), characters,
        ))

    all_char_assets: list[dict] = []
    all_corp_assets: list[dict] = []
    per_character: dict = {}
    per_corporation: dict = {}
    corp_done: set[int] = set()
    corp_error: dict[int, str] = {}

    for r in char_results:
        character_name = r["character_name"]
        if r["error"] is not None:
            per_character[character_name] = f"skipped ({r['error']})"
            continue
        all_char_assets.extend(r["assets"])
        per_character[character_name] = {"assets": len(r["assets"])}

        corporation_id = r["corporation_id"]
        if corporation_id is None or corporation_id in corp_done or corporation_id in corp_error:
            continue
        try:
            corp_assets = client.corporation_assets(corporation_id, auth_role=r["role"])
        except ESIError as e:
            corp_error[corporation_id] = str(e)  # a later character in this corp might have the Director role
            continue
        try:
            corp_name = client.corporation_public_info(corporation_id).get("name", str(corporation_id))
        except ESIError:
            corp_name = str(corporation_id)
        for a in corp_assets:
            a["owner_name"] = f"{corp_name} (corp)"
        corp_done.add(corporation_id)
        all_corp_assets.extend(corp_assets)
        per_corporation[corp_name] = {"assets": len(corp_assets)}

    storage.replace_assets("doctrine_character_assets", _asset_rows(all_char_assets))
    storage.replace_assets("doctrine_corp_assets", _asset_rows(all_corp_assets))
    storage.set_esi_sync_time("doctrine_assets", datetime.now(timezone.utc).isoformat())

    return {"characters": per_character, "corporations": per_corporation}
