"""Pipeline actions for the Ore Shortlist (GitHub issue #91) - see CLAUDE.md's
"Architecture" section: `cli.py`/the FastAPI router call these do_* functions,
never storage.py/engine.py directly.
"""
from __future__ import annotations

import datetime as dt
import logging

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, TRADING_CONFIG, ConfigError, OAuthConfig, TradingConfig, save_tenant_config_overrides
from ..esi_client import ESIClient, ESIError
from .candidate_discovery import build_ore_candidate_universe
from .config import REFINING_CONFIG, RefiningConfig, validate_refining_overrides
from .models import OreShortlistRow
from .pricing import evaluate_ore_shortlist, mineral_type_ids_for

log = logging.getLogger("eve_trader.refining.actions")


def now_ts() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def do_add_ore_to_shortlist() -> dict:
    """Adds every candidate from the fixed SDE-derived universe
    (build_ore_candidate_universe) not already on the shortlist - unlike
    Trading's own add step, there's no backtest/recommendation gate first
    (confirmed with the user: the set of compressed ore/ice types is small
    and stable, every candidate is worth tracking)."""
    candidates = build_ore_candidate_universe()
    if not candidates:
        raise ActionError("No compressed ore/ice types found in the SDE cache - run Refresh SDE first.")
    existing_ids = {item_id for item_id, *_ in storage.load_ore_shortlist()}
    new_rows = [(c.type_id, c.item, c.family, c.is_ice, True) for c in candidates if c.type_id not in existing_ids]
    if new_rows:
        storage.upsert_ore_shortlist(new_rows)
    return {"added": len(new_rows), "already_tracked": len(candidates) - len(new_rows)}


def do_refresh_ore_shortlist(trading_cfg: TradingConfig = TRADING_CONFIG,
                              refining_cfg: RefiningConfig = REFINING_CONFIG,
                              oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Re-fetches live market data for every shortlist item and recomputes
    each one's profit/decision, then saves a new snapshot. Reuses Trading's
    own seller character (same C-J structure both tools sell at - confirmed
    with the user during planning rather than a separate Ore-specific login)
    for the structure order book; Jita's regional order book needs no auth."""
    candidates = build_ore_candidate_universe()
    shortlist = storage.load_ore_shortlist()
    if not shortlist:
        raise ActionError("Ore Shortlist is empty - click 'Add Candidates' first.")
    active_by_id = {item_id: active for item_id, _item, _family, _is_ice, active in shortlist}
    tracked_ids = set(active_by_id)
    tracked_candidates = [c for c in candidates if c.type_id in tracked_ids]

    tm = TokenManager(oauth_cfg)
    # Reuses Trading's own seller role/token - no Ore-specific login (see
    # module docstring). Any one registered seller with docking access is
    # enough (GitHub issue #46's own multi-character precedent) - falls back
    # to the legacy fixed "seller" key for a not-yet-re-logged-in setup, same
    # fallback doctrine/engine.py's own seller-role lookup uses.
    seller_role = next(iter(tm.list_roles("seller")), None) or ("seller" if tm.has_token("seller") else None)
    if seller_role is None:
        raise ActionError("Seller character isn't logged in yet (Trading -> Login -> Seller).")
    client = ESIClient(trading_cfg, tm)

    ore_type_ids = [c.type_id for c in tracked_candidates]
    jita_stats_by_id = client.region_order_stats_bulk(trading_cfg.jita_region_id, ore_type_ids)

    mineral_ids = mineral_type_ids_for(tracked_candidates)
    try:
        mineral_stats_by_id = client.structure_order_stats_bulk(
            trading_cfg.structure_id, mineral_ids, auth_role=seller_role)
    except ESIError as e:
        raise ActionError(f"Could not fetch the structure's order book ({e}). "
                           f"Does the seller character still have docking access?") from e

    rows = evaluate_ore_shortlist(tracked_candidates, active_by_id, jita_stats_by_id, mineral_stats_by_id,
                                   trading_cfg, refining_cfg)
    run_ts = now_ts()
    storage.save_ore_shortlist_snapshot([_row_to_tuple(r) for r in rows], run_ts)
    storage.set_esi_sync_time("refining", run_ts)

    import_count = sum(1 for r in rows if r.decision == "Import")
    return {"evaluated": len(rows), "import_candidates": import_count}


def _row_to_tuple(r: OreShortlistRow) -> tuple:
    return (r.item_id, r.item, r.family, r.is_ice, r.active, r.volume_m3, r.landed_cost, r.yield_pct,
            r.mineral_value, r.refining_tax, r.net_sell, r.sell_listed_qty, r.profit_per_unit, r.margin,
            r.profit_per_m3, r.decision)


def do_deactivate_ore_shortlist_items(item_ids: list[int]) -> dict:
    storage.deactivate_ore_shortlist_items(item_ids)
    return {"deactivated": len(item_ids)}


def do_update_settings(updates: dict, cfg: RefiningConfig = REFINING_CONFIG) -> dict:
    """Persists `updates` to tenant_settings and applies them to the live
    REFINING_CONFIG immediately (see Ore & Minerals Settings tab)."""
    try:
        validate_refining_overrides(updates)
        save_tenant_config_overrides("refining", updates, cfg, cfg_type=RefiningConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return updates
