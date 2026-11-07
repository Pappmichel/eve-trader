"""Station Trading candidate discovery - the "Goonmetrics for cheap
market-wide discovery, ESI only for the bounded live confirmation"
two-stage shape CLAUDE.md's Price sources matrix documents (mirrors
Trading's own history_backtest.py -> shortlist.py split).

discover_candidates does one Goonmetrics current-price dump for the whole
Jita market (already a single HTTP call, see GoonmetricsClient.
current_prices) and ranks by bid-ask spread + real daily-traded volume -
never touches ESI. confirm_live is the only place this tool prices the
live station order book (ESIClient.region_orders_raw_bulk, filtered to
the station), and only against an already-narrowed, persisted shortlist -
see that function's own docstring for why (no bulk-region endpoint exists,
so pricing "everything Goonmetrics knows about Jita" would mean one ESI
call per item across the whole market).
"""
from __future__ import annotations

from typing import Optional

import logging

from ..esi_client import ESIClient, OrderStats, _summarize_orders
from ..goonmetrics_client import GoonmetricsClient
from .config import StationTradingConfig

log = logging.getLogger("eve_trader.station_trading.candidate_discovery")

JITA_MARKET = "jita"
JITA_REGION_ID = 10000002  # the only hub the Goonmetrics "jita" slug describes


def discover_candidates(cfg: StationTradingConfig, client: GoonmetricsClient | None = None,
                         top_n: Optional[int] = None) -> list[dict]:
    """Every Jita item whose current Goonmetrics spread clears
    cfg.min_spread_threshold and whose real average daily traded volume
    (Goonmetrics region history for cfg.hub_region_id, not
    order-book depth - see CLAUDE.md's "Theoretical ceiling figures" section
    for why depth is the wrong signal) clears cfg.min_daily_volume. Returns
    dicts sorted by spread * volume, richest first: {"type_id", "buy",
    "sell", "spread_pct", "avg_daily_volume"}.

    Unlike Production's own discover_build_candidates(top_n=200), there's no
    always-on cap here - min_daily_volume above is the real noise filter
    (confirmed live 2026-08-29: it already brings the real candidate count
    from 10,000+ down to ~1,300, a real opportunity set worth seeing in
    full). `top_n` only caps when explicitly passed (tests) or when
    cfg.enforce_shortlist_cap is on (cfg.max_active_shortlist_items) - same
    "off by default, user opts in" shape as TradingConfig's own
    enforce_shortlist_cap/max_active_shortlist_items.

    The volume history call is only made for items that already passed the
    spread filter - a cheap first pass over the one bulk price dump narrows
    thousands of Jita items down before the second, per-type_id history
    call, rather than fetching history for the entire market up front."""
    if cfg.hub_region_id != JITA_REGION_ID:
        # The discovery stage reads Goonmetrics' hardcoded "jita" market
        # dump; there is no equivalent slug wired up for other hubs, and
        # mixing Jita spreads into another hub's shortlist would be wrong.
        # Skip gracefully (existing shortlist rows stay, live prices still
        # come from ESI via confirm_live) rather than discovering from the
        # wrong market (#222).
        log.warning("Station Trading discovery skipped: hub region %s is not Jita and "
                    "Goonmetrics discovery is Jita-only", cfg.hub_region_id)
        return []
    client = client or GoonmetricsClient()
    spread_hits = []
    for p in client.current_prices(JITA_MARKET):
        if p.buy <= 0 or p.sell <= 0 or p.buy >= p.sell:
            continue
        spread = (p.sell - p.buy) / p.sell
        if spread >= cfg.min_spread_threshold:
            spread_hits.append((p.type_id, p.buy, p.sell, spread))
    if not spread_hits:
        return []

    movement_by_type: dict[int, list[float]] = {}
    for point in client.price_history_chunked(cfg.hub_region_id, [h[0] for h in spread_hits]):
        movement_by_type.setdefault(point.type_id, []).append(point.movement)

    results = []
    for type_id, buy, sell, spread in spread_hits:
        days = movement_by_type.get(type_id)
        avg_daily_volume = sum(days) / len(days) if days else 0.0
        if avg_daily_volume < cfg.min_daily_volume:
            continue
        results.append({"type_id": type_id, "buy": buy, "sell": sell,
                         "spread_pct": spread, "avg_daily_volume": avg_daily_volume})
    results.sort(key=lambda r: r["spread_pct"] * r["avg_daily_volume"], reverse=True)

    if top_n is None:
        top_n = cfg.max_active_shortlist_items if cfg.enforce_shortlist_cap else None
    return results[:top_n] if top_n is not None else results


def own_station_order_ids(client: ESIClient, station_id: int) -> set[int]:
    """order_ids of the trader's own open orders at `station_id`, read live
    like undercut.py does (character_orders per shared trader character).
    Best effort: with no shared character, no token, or an ESI failure for a
    character, that character contributes nothing and this never raises -
    the shortlist then simply keeps own orders in the book."""
    try:
        from .esi_sync import list_shared_trader_characters
        traders = [(character_id, role) for role, character_id, _name
                   in list_shared_trader_characters(client.tokens)]
    except Exception as e:  # no tenant/share/token: continue without exclusion
        log.info("Station Trading own-order exclusion skipped: %s", e)
        return set()
    ids: set[int] = set()
    for character_id, role in traders:
        try:
            for o in client.character_orders(character_id, auth_role=role):
                if o.get("location_id") == station_id and o.get("order_id") is not None:
                    ids.add(o["order_id"])
        except Exception as e:
            log.info("Station Trading own-order read failed for character %s: %s", character_id, e)
    return ids


def confirm_live(type_ids: list[int], station_id: int, hub_region_id: int = JITA_REGION_ID,
                 client: ESIClient | None = None,
                 own_order_ids: set[int] | None = None) -> dict[int, OrderStats]:
    """Live order-book confirmation at the configured station for an
    already-bounded set of type_ids (the persisted shortlist, never the
    whole market - see this module's own docstring).

    Reads the region's raw order books through `region_orders_raw_bulk` (the
    same class-wide cached `region_orders_raw` undercut.py uses), keeps only
    orders with `location_id == station_id`, drops the trader's own orders
    (`own_order_ids`; read via `own_station_order_ids` when not given) and
    summarizes each side with the usual 5th-percentile `_summarize_orders`.
    A side with no order at the station is None, never 0, so a margin cannot
    be computed from a region-wide price the station does not offer. The
    station's market is public (NPC trade hub); only the own-order read
    needs a token."""
    if not type_ids:
        return {}
    client = client or ESIClient()
    if own_order_ids is None:
        own_order_ids = own_station_order_ids(client, station_id)
    raw_by_type = client.region_orders_raw_bulk(hub_region_id, list(type_ids))
    return {
        type_id: _summarize_orders([
            o for o in raw_by_type.get(type_id, [])
            if o.get("location_id") == station_id and o.get("order_id") not in own_order_ids
        ])
        for type_id in type_ids
    }
