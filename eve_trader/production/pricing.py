"""Pricing for the Production tool: ESI-first current home/Jita buy/sell
(live order-book stats via ESIClient, falling back to GoonmetricsClient.
current_prices only when ESI is genuinely unavailable), ESI adjusted prices,
and ESI system cost indices. See esi_client.py / goonmetrics_client.py for
the actual HTTP calls this wraps.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

from .. import hubs
from ..config import TRADING_CONFIG
from ..esi_client import ESIClient, ESIError
from ..goonmetrics_client import CurrentPrice, GoonmetricsClient
from . import jita_price_cache
from .config import PRODUCTION_CONFIG, ProductionConfig

log = logging.getLogger("eve_trader.production.pricing")

JITA_MARKET = "jita"


@dataclass(frozen=True)
class HubQuote(CurrentPrice):
    """A best-hub buy quote (hub setting ALL_HUBS, #222): `sell`/`buy` come
    from the hub that won this item on landed cost, `freight_per_m3` is that
    hub's freight rate, and `ref` is the plain Jita quote - what every
    non-buying reader (margins, output valuation) uses instead, since
    Production never sells at a hub."""
    hub_region_id: int = 0
    freight_per_m3: float = 0.0
    ref: Optional[CurrentPrice] = None


def reference_quote(quote: Optional[CurrentPrice]) -> Optional[CurrentPrice]:
    """The Jita reference price for non-buying readers: a best-hub quote's
    Jita `ref`, otherwise the quote itself (single-hub mode, unchanged)."""
    if isinstance(quote, HubQuote):
        return quote.ref
    return quote


def quote_hub(quote: Optional[CurrentPrice]) -> tuple[Optional[int], Optional[str]]:
    """(region id, hub name) a best-hub quote was priced at; (None, None) in
    single-hub mode (the Buy list then needs no per-item hub)."""
    if isinstance(quote, HubQuote):
        return quote.hub_region_id, hubs.hub_name(quote.hub_region_id)
    return None, None


def _goonmetrics_prices(market: str, type_ids: list[int]) -> dict[int, CurrentPrice]:
    if not market or not type_ids:
        return {}
    wanted = set(type_ids)
    try:
        prices = GoonmetricsClient().current_prices(market)
    except requests.RequestException as e:
        # Best-effort: appraise.gnf.lt is a no-SLA third party. An outage
        # here used to 500 every Production page that builds a _PlanContext
        # (Buy/Build, stock value, margins, invention estimate) plus
        # portfolio_overview and Doctrine's shopping list, which reuse
        # home_prices/jita_prices. Empty quotes match home_prices already
        # returning {} when home_market is unset - callers treat missing
        # quotes as unpriced, not as zero.
        log.warning("Goonmetrics current_prices(%s) failed (%s) - treating those quotes as missing.",
                    market, e)
        return {}
    return {p.type_id: p for p in prices if p.type_id in wanted}


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
            # Group 3 ("structure_market_book"), not producer sharing
            # (docs/ESI_ACCESS_PLAN.md Known gap 4, closed) - this needs a
            # character with esi-markets.structure_markets.v1 ticked on the
            # Characters page's Access table, not one sharing Assets/Market
            # Orders with production; those are unrelated facts about the
            # same character.
            for role, _character_id, _name in esi_sync.list_capability_characters("structure_market_book"):
                try:
                    stats = esi_client.structure_order_stats_bulk(cfg.home_location_id, type_ids, auth_role=role)
                except ESIError:
                    continue
                return _from_order_stats(stats, type_ids)
        except Exception:  # noqa: BLE001 - best-effort; Goonmetrics fallback (or {} if unset) is always safe
            pass
    return _goonmetrics_prices(cfg.home_market, type_ids) if cfg.home_market else {}


def jita_prices(type_ids: list[int], hub_region_id: Optional[int] = None) -> dict[int, CurrentPrice]:
    """Same ESI-first/Goonmetrics-fallback shape as home_prices (including
    the fallback only ever being fetched lazily, on an actual ESI failure),
    but region-side (public data, no auth_role needed) via ESIClient.
    region_order_stats_bulk. Callers MUST scope `type_ids` to a real bounded
    set (see engine._structural_material_closure) - ESI has no bulk-region
    endpoint, so passing "every item Goonmetrics knows about" here would
    mean one ESI call per item across the whole Jita market.

    Reads production.jita_price_cache's shared, hourly-refreshed snapshot
    first (see that module's own docstring) - confirmed real perf issue,
    2026-09-01: live-calling ESI for every priced material on every
    plan_production() run cost ~15s by itself. Only whichever type_ids
    aren't cached yet (e.g. a stock target added since the last refresh)
    fall through to a live per-type fetch below, same as before.

    The hub is `hub_region_id` (default: ProductionConfig.hub_region_id, #222).
    The shared cache is a Jita-only cache and the Goonmetrics fallback uses
    the hardcoded "jita" slug, so both apply only when the hub is Jita; any
    other hub goes straight to live ESI with no fallback (missing stays
    missing - never substitute Jita prices for another hub)."""
    if not type_ids:
        return {}
    hub = hub_region_id if hub_region_id is not None else PRODUCTION_CONFIG.hub_region_id
    if hub == hubs.ALL_HUBS:
        return _best_hub_prices(type_ids)
    is_jita = hub == jita_price_cache.JITA_REGION_ID
    result = jita_price_cache.get_cached_prices(type_ids) if is_jita else {}
    missing = [tid for tid in type_ids if tid not in result]
    if not missing:
        return result
    try:
        stats = ESIClient().region_order_stats_bulk(hub, missing)
        result.update(_from_order_stats(stats, missing))
    except Exception:  # noqa: BLE001 - best-effort; Goonmetrics fallback is always safe
        if is_jita:
            result.update(_goonmetrics_prices(JITA_MARKET, missing))
        else:
            log.warning("ESI order stats for hub region %s failed; no Goonmetrics fallback for non-Jita hubs", hub)
    return result


def _best_hub_prices(type_ids: list[int]) -> dict[int, CurrentPrice]:
    """ALL_HUBS: per item, the quote of the hub with the lowest landed cost
    (hubs.hub_pricing; freight from the shared table, fallback
    haul_cost_per_m3), as HubQuote. Always live ESI per hub (ESIClient caches
    per region/type); the Jita-only price cache and Goonmetrics fallback are
    used only for the Jita reference carried in each quote's `ref`. An ESI
    failure leaves items unpriced - never substituted from another source."""
    from ..production.engine import _haul_volume  # local: engine imports pricing

    cfg = PRODUCTION_CONFIG
    reference = jita_prices(type_ids, jita_price_cache.JITA_REGION_ID)
    volumes = {tid: _haul_volume(tid, cfg) or 0.0 for tid in type_ids}
    try:
        priced = hubs.hub_pricing(ESIClient(), hubs.ALL_HUBS, type_ids, volumes,
                                  TRADING_CONFIG.jita_buy_broker_fee, cfg.haul_cost_per_m3)
    except Exception:  # noqa: BLE001 - best-effort, like single-hub ESI failures
        log.warning("ESI order stats for the best-hub lookup failed; items stay unpriced")
        priced = hubs.HubPricing()
    out: dict[int, CurrentPrice] = {}
    for tid, s in priced.stats.items():
        ref = reference.get(tid) or CurrentPrice(type_id=tid, updated="", buy=0.0, sell=0.0)
        out[tid] = HubQuote(
            type_id=tid, updated="", buy=s.buy_percentile or 0.0, sell=s.sell_percentile or 0.0,
            hub_region_id=priced.hub_by_type[tid], freight_per_m3=priced.freight_by_type[tid], ref=ref)
    return out


def _candidate_prices(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
                       volume_m3: Optional[float], cfg: ProductionConfig) -> dict[str, float]:
    """Per-source landed unit price for `type_id`, for whichever sources
    actually have a sell order listed. Both are inflated by
    TRADING_CONFIG.jita_buy_broker_fee (same buying character, same broker's-
    fee rate regardless of which market they buy in - confirmed against the
    in-game buy screen; T3-04, 2026-09-26, merged Production's own former
    duplicate of this field into TradingConfig's copy, the single source of
    truth for it now); Jita's is additionally inflated by haul cost since it
    still has to be moved to the home structure, while home's doesn't need
    hauling by definition."""
    candidates = {}
    home_quote = home.get(type_id)
    if home_quote and home_quote.sell > 0:
        candidates["C-J"] = home_quote.sell * (1 + TRADING_CONFIG.jita_buy_broker_fee)
    jita_quote = jita.get(type_id)
    if jita_quote and jita_quote.sell > 0:
        candidates["Jita"] = (jita_quote.sell * (1 + TRADING_CONFIG.jita_buy_broker_fee)
                               + _freight_rate(jita_quote, cfg) * (volume_m3 or 0))
    return candidates


def _freight_rate(quote: CurrentPrice, cfg: ProductionConfig) -> float:
    """Freight for a hub quote: the winning hub's own rate in best-hub mode,
    otherwise Production's single haul_cost_per_m3."""
    return quote.freight_per_m3 if isinstance(quote, HubQuote) else cfg.haul_cost_per_m3


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
