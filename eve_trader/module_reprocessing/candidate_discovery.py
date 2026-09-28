"""Builds and prices the Module Reprocessing Import tool's candidate
universe - the "Goonmetrics for cheap market-wide discovery, ESI only for
the bounded live confirmation" two-stage shape CLAUDE.md's Price Sources
Matrix documents (mirrors station_trading/candidate_discovery.py's own
split, and Trading's own history_backtest.py -> shortlist.py split).

Unlike Ore & Minerals' own fixed, tiny compressed-ore/ice universe (~80
types, every one added to the shortlist automatically - see refining/
candidate_discovery.py), the T1/Meta module+drone universe is large
(thousands of published types) - manually reviewing and picking candidates
one by one doesn't scale at this size (confirmed with the user 2026-09-27).
So, same as station_trading's own discover_candidates: `discover_candidates`
below does one Goonmetrics current-price dump per market (already a single
HTTP call each, see GoonmetricsClient.current_prices) over the *whole*
universe and returns everything that clears cfg.min_profit_threshold/
min_margin_threshold - never touches ESI. module_reprocessing/actions.py's
do_refresh_shortlist auto-adds every result of this to the persisted
shortlist and is the only place that then calls ESI, and only against that
already-narrowed, persisted list.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from ..config import TradingConfig
from ..goonmetrics_client import GoonmetricsClient
from .. import storage
from .config import ModuleReprocessingConfig
from .models import DiscoveredModuleResult, ModuleCandidate
from .pricing import estimate_discovered_candidates, mineral_type_ids_for

log = logging.getLogger("eve_trader.module_reprocessing.candidate_discovery")

JITA_MARKET = "jita"


def build_module_candidate_universe() -> list[ModuleCandidate]:
    """Every published T1/Meta module or drone type - see storage.
    module_reprocessing_candidate_types's own docstring for the exact
    SDE-driven inclusion/exclusion rules (category, meta-group, rig-slot)."""
    rows = storage.module_reprocessing_candidate_types()
    return [
        ModuleCandidate(type_id=type_id, item=type_name, volume_m3=volume)
        for type_id, type_name, volume in rows
        if type_name and volume
    ]


def discover_candidates(cfg: ModuleReprocessingConfig, trading_cfg: TradingConfig,
                         client: Optional[GoonmetricsClient] = None) -> list[DiscoveredModuleResult]:
    """Every candidate whose Goonmetrics-estimated economics clear cfg.
    min_profit_threshold/min_margin_threshold, sorted by estimated profit
    (richest first). Raises requests.RequestException on a Jita Goonmetrics
    outage (the caller, do_refresh_shortlist, wraps this into ActionError -
    same split as station_trading's own discover_candidates/do_refresh_
    shortlist); a home-market (mineral pricing) outage is best-effort and
    only means fewer results have a usable estimate, not a hard failure.

    Warms get_sde_type/get_type_materials' lru_caches in two bulk calls up
    front so the per-candidate reprocessing math never opens its own DB
    connection - with those warmed, the only real cost left is the
    Goonmetrics dump(s).

    Unlike Production's own discover_build_candidates(top_n=200), there's no
    always-on cap here - min_profit_threshold/min_margin_threshold are this
    tool's real noise filter, same "off by default, user opts in" reasoning
    as station_trading's own enforce_shortlist_cap/max_active_shortlist_items
    (see ModuleReprocessingConfig's own docstring on those two fields)."""
    candidates = build_module_candidate_universe()
    if not candidates:
        return []

    type_ids = [c.type_id for c in candidates]
    storage.get_sde_types_bulk(type_ids)
    storage.get_type_materials_bulk(type_ids)

    client = client or GoonmetricsClient(trading_cfg)
    jita_prices_by_id = {p.type_id: p for p in client.current_prices(JITA_MARKET)}

    mineral_ids = set(mineral_type_ids_for(candidates))
    mineral_prices_by_id = {}
    if trading_cfg.structure_market_slug:
        try:
            mineral_prices_by_id = {
                p.type_id: p for p in client.current_prices(trading_cfg.structure_market_slug)
                if p.type_id in mineral_ids
            }
        except requests.RequestException as e:
            log.warning("Goonmetrics home-market fetch failed (%s) - mineral values will be missing this run.", e)

    results = estimate_discovered_candidates(candidates, jita_prices_by_id, mineral_prices_by_id, trading_cfg, cfg)
    hits = [
        r for r in results
        if r.est_profit_per_unit is not None and r.est_profit_per_unit > cfg.min_profit_threshold
        and r.est_margin is not None and r.est_margin >= cfg.min_margin_threshold
    ]
    hits.sort(key=lambda r: r.est_profit_per_unit, reverse=True)

    if cfg.enforce_shortlist_cap:
        return hits[:cfg.max_active_shortlist_items]
    return hits
