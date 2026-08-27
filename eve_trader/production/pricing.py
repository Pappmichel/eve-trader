"""Pricing for the Production tool: ESI-first current home/Jita buy/sell
(live order-book stats via ESIClient, falling back to GoonmetricsClient.
current_prices only when ESI is genuinely unavailable), ESI adjusted prices,
and ESI system cost indices. See esi_client.py / goonmetrics_client.py for
the actual HTTP calls this wraps.
"""
from __future__ import annotations

from typing import Optional

from ..config import TRADING_CONFIG
from ..esi_client import ESIClient, ESIError
from ..goonmetrics_client import CurrentPrice, GoonmetricsClient
from .config import PRODUCTION_CONFIG, ProductionConfig

JITA_MARKET = "jita"


def _goonmetrics_prices(market: str, type_ids: list[int]) -> dict[int, CurrentPrice]:
    if not market or not type_ids:
        return {}
    wanted = set(type_ids)
    return {p.type_id: p for p in GoonmetricsClient().current_prices(market) if p.type_id in wanted}


def _from_order_stats(stats: dict, type_ids: list[int]) -> dict[int, CurrentPrice]:
    # updated="" - nothing reads CurrentPrice.updated anywhere in the
    # codebase (confirmed repo-wide 2026-08-26), so a live ESI-sourced quote
    # (which has no equivalent "last updated" concept of its own - it's the
    # order book *right now*) doesn't need one.
    return {
        tid: CurrentPrice(
            type_id=tid, updated="",
            buy=stats[tid].buy_percentile or 0.0,
            sell=stats[tid].sell_percentile or 0.0,
        )
        for tid in type_ids
    }


def home_prices(cfg: ProductionConfig, type_ids: list[int]) -> dict[int, CurrentPrice]:
    """ESI-first: live C-J structure order book (via a registered producer
    character with docking access and esi-markets.structure_markets.v1),
    scoped to `type_ids` - one full-book download regardless of how many
    type_ids are requested (ESIClient.structure_order_stats_bulk). Falls
    back to a Goonmetrics current-price snapshot (cfg.home_market), filtered
    to `type_ids`, only when no producer character can complete the live
    call (missing scope, no docking access, ESI outage) - same failsafe
    shape ESIClient.structure_order_stats_bulk_or_goonmetrics already
    provides for Trading's Shortlist/Refining, reimplemented here since
    Production authorizes via its own producer characters, not Trading's
    seller (confirmed with the user 2026-08-26 - the Default tenant had
    zero registered sellers, so reusing Trading's role would never engage).

    The Goonmetrics fallback is only ever fetched lazily, on an actual ESI
    failure - not eagerly alongside the ESI attempt - both to avoid a
    wasted multi-megabyte download on the (expected-common) success path,
    and because eagerly calling it regardless of cfg.home_location_id would
    make a real network request even when the caller only wanted the
    Goonmetrics side deliberately skipped."""
    if not type_ids:
        return {}
    if cfg.home_location_id is not None:
        from . import esi_sync  # local import: avoids a module-level esi_sync<->pricing cycle
        from ..auth import TokenManager
        from ..config import OAUTH_CONFIG
        try:
            esi_client = ESIClient(tokens=TokenManager(OAUTH_CONFIG))
            for role, _character_id, _name in esi_sync.list_producer_characters():
                try:
                    stats = esi_client.structure_order_stats_bulk(cfg.home_location_id, type_ids, auth_role=role)
                except ESIError:
                    continue
                return _from_order_stats(stats, type_ids)
        except Exception:  # noqa: BLE001 - best-effort; Goonmetrics fallback (or {} if unset) is always safe
            pass
    return _goonmetrics_prices(cfg.home_market, type_ids) if cfg.home_market else {}


def jita_prices(type_ids: list[int]) -> dict[int, CurrentPrice]:
    """Same ESI-first/Goonmetrics-fallback shape as home_prices (including
    the fallback only ever being fetched lazily, on an actual ESI failure),
    but region-side (public data, no auth_role needed) via ESIClient.
    region_order_stats_bulk. Callers MUST scope `type_ids` to a real bounded
    set (see engine._structural_material_closure) - ESI has no bulk-region
    endpoint, so passing "every item Goonmetrics knows about" here would
    mean one ESI call per item across the whole Jita market.

    Jita's region_id comes from TRADING_CONFIG (not a ProductionConfig
    field - Jita itself is Trading's own concept, matching every other
    Production call site that already reaches into TRADING_CONFIG.
    jita_region_id, e.g. engine.py's market_status/stock_value)."""
    if not type_ids:
        return {}
    try:
        stats = ESIClient().region_order_stats_bulk(TRADING_CONFIG.jita_region_id, type_ids)
    except Exception:  # noqa: BLE001 - best-effort; Goonmetrics fallback is always safe
        return _goonmetrics_prices(JITA_MARKET, type_ids)
    return _from_order_stats(stats, type_ids)


def _candidate_prices(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
                       volume_m3: Optional[float], cfg: ProductionConfig) -> dict[str, float]:
    """Per-source landed unit price for `type_id`, for whichever sources
    actually have a sell order listed. Both are inflated by
    cfg.jita_buy_broker_fee (same buying character, same broker's-fee rate
    regardless of which market they buy in - confirmed against the in-game
    buy screen); Jita's is additionally inflated by haul cost since it still
    has to be moved to the home structure, while home's doesn't need hauling
    by definition."""
    candidates = {}
    home_quote = home.get(type_id)
    if home_quote and home_quote.sell > 0:
        candidates["C-J"] = home_quote.sell * (1 + cfg.jita_buy_broker_fee)
    jita_quote = jita.get(type_id)
    if jita_quote and jita_quote.sell > 0:
        candidates["Jita"] = jita_quote.sell * (1 + cfg.jita_buy_broker_fee) + cfg.haul_cost_per_m3 * (volume_m3 or 0)
    return candidates


def buy_source(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
                volume_m3: Optional[float] = None, cfg: ProductionConfig = PRODUCTION_CONFIG) -> Optional[str]:
    """Which market buy_price() sources `type_id` from - whichever of home
    ("C-J") or haul-adjusted Jita is actually cheaper, else whichever of the
    two has a sell order listed, else None (no sell order anywhere). Single
    source of truth for this decision - buy_price uses it internally so the
    two can never disagree."""
    candidates = _candidate_prices(type_id, home, jita, volume_m3, cfg)
    if not candidates:
        return None
    return min(candidates, key=candidates.get)


def buy_price(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
              volume_m3: Optional[float], cfg: ProductionConfig = PRODUCTION_CONFIG) -> Optional[float]:
    """Cheapest way to acquire one unit of `type_id` right now: home sell
    price, or Jita sell price plus haul cost, whichever is lower. None if
    neither market has a sell order."""
    candidates = _candidate_prices(type_id, home, jita, volume_m3, cfg)
    if not candidates:
        return None
    return min(candidates.values())


def system_cost_indices_for(esi_client: ESIClient, system_id: Optional[int]) -> dict[str, float]:
    """Manufacturing/reaction cost indices for `system_id`. Returns {} if
    `system_id` is None (job-cost modeling falls back to the flat ACTIVITY_MODS
    rate, not guessed, in that case - see engine.py)."""
    if system_id is None:
        return {}
    try:
        return esi_client.get_system_cost_indices(system_id, activities=("manufacturing", "reaction"))
    except Exception:  # noqa: BLE001 - best-effort; a transient ESI hiccup shouldn't block the whole plan
        return {}
