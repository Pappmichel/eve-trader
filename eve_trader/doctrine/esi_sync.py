"""ESI sync for the Doctrine tool - two wrappers around the Phase 3
orchestrator, plus tool-specific post-processing:

1. Contract sync: `do_sync_for_tool("doctrine")` writes the snapshot
   (structure pre-filter happens in the contracts fetcher before the
   per-contract items call). Matching against fittings and finished-
   contract history stay here.
2. Asset sync: the same `do_sync_for_tool("doctrine")` call writes
   character_assets / corp_assets. Sharing with `tool_key="doctrine"`
   is what keeps a Doctrine-only tenant's stockpile independent of
   Production (the property the doctrine asset tables existed for).
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
from . import engine
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .models import ContractItemRow
from .constants import CONTRACT_TYPE_ITEM_EXCHANGE, FINISHED_CONTRACT_STATUSES, SYNCABLE_CONTRACT_STATUSES

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


def list_shared_doctrine_characters(tm: Optional[TokenManager] = None) -> list[tuple[str, int, str]]:
    """Returns (auth_role, character_id, character_name) for every character
    currently sharing Contracts with `doctrine` - the sharing-based
    replacement for `list_doctrine_characters` (Known-gap-4-class bug,
    closed 2026-09-21). Same shape as production/esi_sync.py's
    `list_shared_producer_characters` - see that function's own docstring
    for the full reasoning."""
    from ..esi_data.access import shared_owner_ids
    from ..esi_data.selector import select_auth_role

    tm = tm or TokenManager(OAUTH_CONFIG)
    char_ids = sorted(shared_owner_ids("contracts", "doctrine", "character"))
    out = []
    for character_id in char_ids:
        role = select_auth_role(character_id, "esi-contracts.read_character_contracts.v1", tokens=tm)
        if role is None:
            continue
        record = tm.get_record(role)
        out.append((role, character_id, record.character_name if record else str(character_id)))
    return out


def _passes_history_filter(contract: dict, structure_id: Optional[int]) -> bool:
    """Same shape as _passes_prefilter below, for FINISHED_CONTRACT_STATUSES
    instead of SYNCABLE_CONTRACT_STATUSES (GitHub issue #19) - a finished
    contract is about to drop out of the *active* snapshot (see
    _passes_prefilter's own SYNCABLE_CONTRACT_STATUSES, which doesn't
    include "finished"), so this is checked separately, over the same
    already-fetched raw contract list, to catch it for doctrine_contract_
    history before that happens."""
    return (
        contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
        and contract.get("status") in FINISHED_CONTRACT_STATUSES
        and structure_id is not None
        and contract.get("start_location_id") == structure_id
    )


def _passes_prefilter(contract: dict, structure_id: Optional[int]) -> bool:
    """Phase 2 E.3 point 2 - applied before any items fetch, never after.
    Deliberately no date_expired/"is this actually still alive" check here
    anymore - SYNCABLE_CONTRACT_STATUSES (ESI's own status field) is now the
    single authoritative signal for that, see its own comment for why."""
    return (
        contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
        and contract.get("status") in SYNCABLE_CONTRACT_STATUSES
        and structure_id is not None
        and contract.get("start_location_id") == structure_id
    )


def _issued_by_own_identity(contract: dict, character_id: int, corporation_id: Optional[int]) -> bool:
    """ESI's character-contracts endpoint returns every contract the
    character is issuer, acceptor, OR ASSIGNEE of (confirmed via esi_client.
    ESIClient.character_contracts' own docstring) - a public/third-party
    contract merely *assigned* to this character or their corp (never
    actually created by them) would otherwise leak into the Doctrine match
    pool. Only contracts this character (or their own corp) actually issued
    are relevant - `issuer_corporation_id` is transient here, never stored
    (_CONTRACT_COLUMNS keeps its existing issuer_id-only shape)."""
    return contract.get("issuer_id") == character_id or (
        corporation_id is not None and contract.get("issuer_corporation_id") == corporation_id
    )


def sync_contracts(cfg: DoctrineConfig = DOCTRINE_CONFIG, progress_callback=None) -> dict:
    """Fetch is the Phase 3 orchestrator (`do_sync_for_tool("doctrine")`).
    Matching against fittings stays here. Finished contracts the snapshot
    had matched are recorded into history by the contracts fetcher itself
    (every sync path drops them from the snapshot); the rest are matched
    from their ESI items here (`_record_unmatched_finished`)."""
    from ..esi_data.orchestrator import do_sync_for_tool
    from .actions import do_validate_contracts

    structure_id = cfg.effective_structure_id
    if structure_id is None:
        raise ActionError(
            "No structure configured for Doctrine contract sync. Set it in Doctrine Settings "
            "(or Trading's own structure_id, which this falls back to)."
        )

    tm = TokenManager(OAUTH_CONFIG)
    characters = list_shared_doctrine_characters(tm)
    if not characters:
        raise ActionError("No Doctrine character shared yet. Share Contracts on the Characters page.")

    result = do_sync_for_tool("doctrine", extra={"structure_id": structure_id})
    match_result = do_validate_contracts(cfg)

    role_by_character = {cid: role for role, cid, _name in characters}
    fallback_role = characters[0][0] if characters else ""
    finished: dict[int, tuple[dict, list[tuple[str, int, str]]]] = {}
    history_written = 0
    for owner in result.get("owners") or []:
        kinds = owner.get("kinds") or {}
        contracts = kinds.get("contracts")
        if not isinstance(contracts, dict):
            continue
        history_written += contracts.get("history_written") or 0
        owner_type, owner_id = owner.get("owner_type"), owner.get("owner_id")
        if owner_type == "character":
            role = role_by_character.get(owner_id, fallback_role)
        else:
            role = contracts.get("auth_role") or fallback_role
        for raw in contracts.get("finished") or []:
            entry = finished.setdefault(raw["contract_id"], (raw, []))
            entry[1].append((owner_type, owner_id, role))

    history_written += _record_unmatched_finished(finished, tm)
    storage.set_esi_sync_time("doctrine", datetime.now(timezone.utc).isoformat())
    _emit_progress(progress_callback, {"phase": "sync", "message": "Matching contracts"})
    result["contracts_synced"] = match_result.get("revalidated", match_result.get("contracts_synced"))
    result["history_written"] = history_written
    return result


def _record_unmatched_finished(finished: dict[int, tuple[dict, list[tuple[str, int, str]]]],
                               tm: TokenManager, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> int:
    """History for finished contracts the contracts fetcher could not record
    (it only records ones the snapshot still had matched to a fitting): a
    contract sold before it was ever matched, or one whose snapshot row an
    earlier sync already dropped. Its items come from ESI (immutable, still
    readable for ESI's 30-day contract window) and are matched like a live
    contract. `finished`: contract_id -> (raw, [(owner_type, owner_id, role)])."""
    recorded = storage.doctrine_contract_history_ids(list(finished))
    todo = [(raw, owners) for cid, (raw, owners) in finished.items() if cid not in recorded]
    if not todo:
        return 0
    client = ESIClient(tokens=tm)
    candidates = engine.load_match_candidates()
    matched: list[tuple[dict, str, str, tuple]] = []
    # Corp owners first: a corp contract's items are read through the corp endpoint.
    for raw, owners in todo:
        cid = raw["contract_id"]
        items_raw, role = None, None
        for owner_type, owner_id, owner_role in sorted(owners, key=lambda o: o[0] != "corporation"):
            try:
                if owner_type == "corporation":
                    items_raw = client.corporation_contract_items(owner_id, cid, auth_role=owner_role)
                else:
                    items_raw = client.character_contract_items(owner_id, cid, auth_role=owner_role)
                role = owner_role
                break
            except ESIError as e:
                log.info("contract %s items via %s %s failed: %s", cid, owner_type, owner_id, e)
        if items_raw is None:
            continue
        items = [ContractItemRow(cid, it["record_id"], it["type_id"], it["quantity"],
                                 it.get("is_included", True), it.get("is_singleton", False))
                 for it in items_raw]
        fitting_id, _score, _devs, _status = engine.match_and_validate_contract(
            cid, raw.get("title"), items, candidates, cfg)
        fitting = storage.get_fitting(fitting_id) if fitting_id else None
        if fitting is not None:
            matched.append((raw, role, fitting_id, fitting))
    if not matched:
        return 0
    acceptor_ids = sorted({raw["acceptor_id"] for raw, *_ in matched if raw.get("acceptor_id")})
    try:
        names = client.resolve_names(acceptor_ids) if acceptor_ids else {}
    except ESIError:
        names = {}
    storage.upsert_doctrine_contract_history([
        (raw["contract_id"], role, fitting_id, fitting[2], fitting[4], raw.get("title"), raw.get("price"),
         raw.get("acceptor_id"), names.get(raw.get("acceptor_id")), raw.get("date_issued"),
         raw.get("date_completed"))
        for raw, role, fitting_id, fitting in matched
    ])
    return len(matched)


# =========================================================== asset sync (Stockpile)
def list_shared_doctrine_asset_characters(tm: Optional[TokenManager] = None) -> list[tuple[str, int, str]]:
    """Returns (auth_role, character_id, character_name) for every character
    currently sharing Assets with `doctrine` - the sharing-based
    replacement for `list_doctrine_asset_characters` (Known-gap-4-class
    bug, closed 2026-09-21)."""
    from ..esi_data.access import shared_owner_ids
    from ..esi_data.selector import select_auth_role

    tm = tm or TokenManager(OAUTH_CONFIG)
    char_ids = sorted(shared_owner_ids("assets", "doctrine", "character"))
    out = []
    for character_id in char_ids:
        role = select_auth_role(character_id, "esi-assets.read_assets.v1", tokens=tm)
        if role is None:
            continue
        record = tm.get_record(role)
        out.append((role, character_id, record.character_name if record else str(character_id)))
    return out


def sync_assets() -> dict:
    """Delegates the fetch to the Phase 3 orchestrator. Sharing with
    `tool_key="doctrine"` is what keeps a Doctrine-only tenant's stockpile
    independent of Production."""
    from ..esi_data.orchestrator import do_sync_for_tool

    tm = TokenManager(OAUTH_CONFIG)
    characters = list_shared_doctrine_asset_characters(tm)
    if not characters:
        raise ActionError(
            "No Doctrine character shared yet. Share Assets with Doctrine on the Characters page."
        )
    extra = {}
    structure_id = DOCTRINE_CONFIG.effective_structure_id
    if structure_id is not None:
        extra["structure_id"] = structure_id
    result = do_sync_for_tool("doctrine", extra=extra or None)
    storage.set_esi_sync_time("doctrine_assets", datetime.now(timezone.utc).isoformat())
    return result
