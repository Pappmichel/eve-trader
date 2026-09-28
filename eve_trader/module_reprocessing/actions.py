"""Pipeline actions for the Module Reprocessing Import tool - see CLAUDE.md's
"Architecture" section: cli.py/the FastAPI router call these do_* functions,
never storage.py/engine.py directly.

do_refresh_shortlist runs synchronously in the request, not via
pipeline_runner - same shape as station_trading/actions.py's own
do_refresh_shortlist (a single Goonmetrics current-price bulk dump, already
tolerated there as a plain blocking call for an even larger "the whole Jita
market" universe), not Trading's own multi-day chunked-history Search+Add+
Clean Up (a pipeline_runner job, since that one is minutes-long, not one
~30s HTTP call - see goonmetrics_client.py's current_prices docstring for
the measured figure this is based on).

Discovery/add-to-shortlist is NOT a separate manual step here (confirmed
with the user 2026-09-27, after building an earlier manual-pick version
first: reviewing and picking candidates one by one doesn't scale over this
tool's large T1/Meta module+drone universe). do_refresh_shortlist auto-
discovers and auto-adds every candidate clearing cfg.min_profit_threshold/
min_margin_threshold on every run, mirroring station_trading/actions.py's
own do_refresh_shortlist exactly - the real noise filter is that threshold,
not a manual review step (see module_reprocessing/config.py's own
enforce_shortlist_cap/max_active_shortlist_items docstring for the opt-in
safety valve on top of it).
"""
from __future__ import annotations

import datetime as dt

import requests

from .. import storage
from ..actions import ActionError, list_shared_trading_characters, structure_book_auth_roles
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, TRADING_CONFIG, ConfigError, OAuthConfig, TradingConfig, save_tenant_config_overrides
from ..esi_client import ESIClient, ESIError
from .candidate_discovery import discover_candidates
from .config import MODULE_REPROCESSING_CONFIG, ModuleReprocessingConfig
from .models import ModuleCandidate, ModuleShortlistRow
from .pricing import evaluate_module_shortlist, mineral_type_ids_for


def now_ts() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def _seller_roles(tm: TokenManager) -> list[str]:
    """Same reasoning as refining/actions.py's own _seller_roles - reuses
    whichever of Trading's shared seller characters actually has docking
    access to C-J for the structure order book (mineral sell side); no
    Module-Reprocessing-specific login (this tool has no OAuth role
    namespace of its own, same as Ore & Minerals - see auth.py's
    TOOL_ROLE_PREFIXES)."""
    return structure_book_auth_roles(list_shared_trading_characters(tm))


def do_refresh_shortlist(cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG,
                          trading_cfg: TradingConfig = TRADING_CONFIG,
                          oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """One button, does everything (mirrors station_trading/actions.py's own
    do_refresh_shortlist):
      1. Auto-discover: a cheap Goonmetrics-only scan of the whole candidate
         universe (candidate_discovery.discover_candidates), auto-upserted
         into the persisted shortlist - a re-discovered item's `active` flag
         is left untouched (see storage.upsert_module_reprocessing_shortlist),
         so a manually deactivated item never silently comes back just
         because it's still economically plausible.
      2. Live-price every item now on the shortlist via real ESI order-book
         stats and save a new snapshot - the module's own purchase side from
         cfg.purchase_region_id's public regional order book (Default Jita,
         no auth needed), the mineral sell side from Trading's own seller
         character at C-J's structure order book (with a Goonmetrics
         fallback), same as Ore & Minerals."""
    try:
        discovered = discover_candidates(cfg, trading_cfg)
    except requests.RequestException as e:
        raise ActionError(f"Could not fetch Jita prices from Goonmetrics ({e}).") from e
    if discovered:
        storage.upsert_module_reprocessing_shortlist([(d.type_id, d.item) for d in discovered])

    shortlist = storage.load_module_reprocessing_shortlist()
    if not shortlist:
        raise ActionError("No candidates clear the configured margin/profit threshold yet - "
                           "lower it in Settings (or turn on 'Ignore margin/profit thresholds "
                           "entirely'), or check back once Goonmetrics has fresher data.")
    active_by_id = {item_id: active for item_id, _item, active in shortlist}
    item_ids = list(active_by_id.keys())

    sde_rows = storage.get_sde_types_bulk(item_ids)
    candidates = [
        ModuleCandidate(type_id=item_id, item=item_name,
                         volume_m3=(sde_rows.get(item_id)[3] if sde_rows.get(item_id) else 0.0) or 0.0)
        for item_id, item_name, _active in shortlist
    ]

    tm = TokenManager(oauth_cfg)
    seller_roles = _seller_roles(tm)
    client = ESIClient(trading_cfg, tm)

    try:
        item_stats_by_id = client.region_order_stats_bulk(cfg.purchase_region_id, item_ids)
    except (ESIError, requests.RequestException) as e:
        raise ActionError(f"Could not fetch the purchase region's order book ({e}).") from e

    mineral_ids = mineral_type_ids_for(candidates)
    try:
        # Falls back to a Goonmetrics current-price snapshot when no seller
        # is logged in or the real call fails - see
        # structure_order_stats_bulk_or_goonmetrics's own docstring.
        mineral_stats_by_id, priced_via_fallback = client.structure_order_stats_bulk_or_goonmetrics(
            trading_cfg.structure_id, mineral_ids, auth_roles=seller_roles,
            goonmetrics_market_slug=trading_cfg.structure_market_slug)
    except ESIError as e:
        raise ActionError(f"Could not fetch the structure's order book ({e}). "
                           f"Does the seller character still have docking access?") from e

    rows = evaluate_module_shortlist(candidates, active_by_id, item_stats_by_id, mineral_stats_by_id, trading_cfg, cfg)
    run_ts = now_ts()
    storage.save_module_reprocessing_shortlist_snapshot([_row_to_tuple(r) for r in rows], run_ts)
    storage.set_esi_sync_time("module_reprocessing", run_ts)

    import_count = sum(1 for r in rows if r.decision == "Import")
    return {"discovered": len(discovered), "evaluated": len(rows), "import_candidates": import_count,
            "priced_via_fallback": priced_via_fallback}


def _row_to_tuple(r: ModuleShortlistRow) -> tuple:
    return (r.item_id, r.item, r.active, r.volume_m3, r.landed_cost, r.yield_pct, r.mineral_value,
            r.refining_tax, r.net_sell, r.sell_listed_qty, r.profit_per_unit, r.margin, r.profit_per_m3, r.decision)


def do_deactivate_shortlist_items(item_ids: list[int]) -> dict:
    """Manual override - a deactivated item is never re-discovered back onto
    the shortlist automatically (see storage.upsert_module_reprocessing_
    shortlist's own docstring); do_activate_shortlist_items below undoes it."""
    storage.deactivate_module_reprocessing_shortlist_items(item_ids)
    return {"deactivated": len(item_ids)}


def do_activate_shortlist_items(item_ids: list[int]) -> dict:
    storage.activate_module_reprocessing_shortlist_items(item_ids)
    return {"activated": len(item_ids)}


def do_update_settings(updates: dict, cfg: ModuleReprocessingConfig = MODULE_REPROCESSING_CONFIG) -> dict:
    """Persists `updates` to tenant_settings and applies them to the live
    MODULE_REPROCESSING_CONFIG immediately (see Module Reprocessing Settings
    tab). No enum-style fields exist on ModuleReprocessingConfig (no
    structure_type/rig_tier/implant - see that class's own docstring), so
    the generic type/range check inside save_tenant_config_overrides is
    enough; unlike refining/actions.py's do_update_settings, there is no
    extra validate_module_reprocessing_overrides step to call first."""
    try:
        save_tenant_config_overrides("module_reprocessing", updates, cfg, cfg_type=ModuleReprocessingConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return updates
