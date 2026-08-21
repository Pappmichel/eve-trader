"""Realized Trade History reconciliation.

Pulls wallet transactions for two characters (buyer imports in Jita, seller
sells in the structure) over a lookback window, and matches buys against
sells per item (FIFO) to compute realized profit.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import storage
from .config import TRADING_CONFIG, TradingConfig
from .esi_client import ESIClient
from .models import RealizedTrade


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


WALLET_TRANSACTIONS_PAGE_SIZE = 2500  # ESI's fixed per-call cap for this endpoint


def fetch_recent_transactions(character_id: int, auth_role: str, client: ESIClient,
                               lookback_days: int) -> list[dict]:
    """Pages through character_wallet_transactions via `from_id` (cursor
    pagination, oldest transaction_id of the previous page) until either a
    page's oldest transaction is older than the lookback cutoff, or a
    short page (< WALLET_TRANSACTIONS_PAGE_SIZE) signals there's nothing
    older left at all - a single un-paginated call only ever sees the most
    recent 2500 transactions, which silently dropped older-but-still-in-
    window trades for any character with more transaction volume than that
    within `lookback_days` (confirmed real-world symptom: a frequently-traded
    item like Oxygen Isotopes missing from Realized Trades even though it
    was clearly sold within the lookback window)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    all_txns: list[dict] = []
    from_id: Optional[int] = None
    while True:
        page = client.character_wallet_transactions(character_id, auth_role=auth_role, from_id=from_id)
        if not page:
            break
        all_txns.extend(page)
        oldest = min(page, key=lambda t: t["transaction_id"])
        # len(page) < WALLET_TRANSACTIONS_PAGE_SIZE as the "no more pages"
        # signal relies on ESI's per-call cap staying fixed at 2500 - correct
        # today, but would silently under-page (looking identical to "no
        # older transactions left") if CCP ever lowered it.
        if _parse_iso(oldest["date"]) < cutoff or len(page) < WALLET_TRANSACTIONS_PAGE_SIZE:
            break
        from_id = oldest["transaction_id"]
    return [t for t in all_txns if _parse_iso(t["date"]) >= cutoff]


def reconcile_realized_trades(buyer_characters: list[tuple[int, str]], seller_characters: list[tuple[int, str]],
                               client: ESIClient, item_names: dict[int, str],
                               item_volumes: dict[int, float],
                               cfg: TradingConfig = TRADING_CONFIG) -> list[RealizedTrade]:
    """Matches every buyer character's Jita buy transactions against every
    seller character's structure sell transactions per type_id, FIFO, within
    cfg.lookback_days. `buyer_characters`/`seller_characters` are lists of
    (character_id, auth_role) pairs - GitHub issue #46: multiple buyer/seller
    characters are pooled together (every buyer's buys vs. every seller's
    sells, not paired 1:1 by character), matching how the shortlist's own
    "own orders remaining"/undercut checks already pool across characters.
    """
    buys = []
    for character_id, role in buyer_characters:
        buys.extend(fetch_recent_transactions(character_id, role, client, cfg.lookback_days))
    sells = []
    for character_id, role in seller_characters:
        sells.extend(fetch_recent_transactions(character_id, role, client, cfg.lookback_days))

    # Confirmed real bug: unlike `sells` (correctly scoped to cfg.structure_id
    # below), `buys` had no location filter at all - any wallet transaction
    # by the buyer character anywhere, not just The Forge (matching this
    # module's own "buyer imports in Jita" docstring - confirmed with the
    # user that this means the whole region, not just Jita's own solar
    # system, since a trader can legitimately buy from any station in The
    # Forge), could enter the FIFO match and get paired against an unrelated
    # structure sale, producing a wrong landed/profit/margin for that trade.
    # Wallet transactions only carry a station/structure location_id (no
    # region_id), so resolve every NPC station in cfg.jita_region_id from the
    # local SDE cache instead.
    jita_region_stations = storage.get_station_ids_in_region(cfg.jita_region_id)
    buys = [t for t in buys if t.get("is_buy") and t.get("location_id") in jita_region_stations]
    sells = [t for t in sells if not t.get("is_buy") and t.get("location_id") == cfg.structure_id]

    buys_by_type: dict[int, list[dict]] = defaultdict(list)
    for t in buys:
        buys_by_type[t["type_id"]].append(t)
    for lst in buys_by_type.values():
        lst.sort(key=lambda t: t["date"])

    sells_by_type: dict[int, list[dict]] = defaultdict(list)
    for t in sells:
        sells_by_type[t["type_id"]].append(t)
    for lst in sells_by_type.values():
        lst.sort(key=lambda t: t["date"])

    # item_names/item_volumes only cover the *current* shortlist - a type traded
    # historically but since removed (or never added, e.g. incidental moon/ice
    # product income) falls through both. Backfill those on demand from ESI's
    # public /universe/types/ endpoint instead of guessing, and cache per type_id
    # so each one is only fetched once regardless of how many trades match it.
    names = dict(item_names)
    volumes = dict(item_volumes)

    def _type_info(type_id: int) -> None:
        if type_id in volumes:
            return
        try:
            info = client.get_type_info(type_id)
        except Exception:  # noqa: BLE001 - best-effort backfill, never block reconciliation
            volumes[type_id] = 0.0
            return
        volumes[type_id] = info.get("volume") or 0.0
        names.setdefault(type_id, info.get("name", str(type_id)))

    results: list[RealizedTrade] = []
    for type_id, sell_txns in sells_by_type.items():
        buy_queue = [dict(t) for t in buys_by_type.get(type_id, [])]
        buy_idx = 0
        _type_info(type_id)
        for sell in sell_txns:
            remaining_to_match = sell["quantity"]
            while remaining_to_match > 0 and buy_idx < len(buy_queue):
                buy = buy_queue[buy_idx]
                matched = min(remaining_to_match, buy["quantity"])
                if matched <= 0:
                    buy_idx += 1
                    continue
                # cfg.import_cost_per_m3 is an ISK-per-m3 *rate*, never a flat
                # per-unit fee - freight must always scale with the item's own
                # per-unit volume, or cheap/small/bulk-traded items (ammo, ice
                # products, ...) get a wildly overstated landed cost.
                freight = volumes[type_id] * cfg.import_cost_per_m3
                # jita_buy_broker_fee/structure_sell_haircut are *modeled*
                # rates (config.py), applied on top of the real observed
                # buy["unit_price"]/sell["unit_price"] from the wallet
                # transaction itself - not the real fee actually charged for
                # that specific transaction (ESI's wallet *journal*, a
                # separate endpoint from wallet *transactions*, has the real
                # per-transaction brokers_fee/transaction_tax entries, not
                # pulled here). An approximation, same spirit as this app's
                # other documented simplifications (e.g. invention's job-fee
                # estimate) - real skill/standing changes over the lookback
                # window could make Realized Trades' own profit/margin drift
                # from what actually landed in the wallet.
                landed = buy["unit_price"] * (1 + cfg.jita_buy_broker_fee) + freight
                net_sell = sell["unit_price"] * cfg.structure_sell_haircut
                profit_per_unit = net_sell - landed
                results.append(RealizedTrade(
                    type_id=type_id,
                    item=names.get(type_id, str(type_id)),
                    buy_date=buy["date"], buy_qty=buy["quantity"], buy_unit_price=buy["unit_price"],
                    sell_date=sell["date"], sell_qty=sell["quantity"], sell_unit_price=sell["unit_price"],
                    matched_qty=matched,
                    realized_profit=profit_per_unit * matched,
                    margin=(profit_per_unit / landed) if landed else 0.0,
                ))
                buy["quantity"] -= matched
                remaining_to_match -= matched
                if buy["quantity"] <= 0:
                    buy_idx += 1
    results.sort(key=lambda r: r.sell_date)
    return results


def summarize_realized(trades: list[RealizedTrade]) -> dict:
    """Equivalent of the 'Gesamtgewinn' / 'Durchschnittsmarge' / Top-3 block."""
    total_profit = sum(t.realized_profit for t in trades)
    weighted_denom = sum(t.buy_unit_price * t.matched_qty for t in trades)
    avg_margin = (total_profit / weighted_denom) if weighted_denom else 0.0

    by_item: dict[str, float] = defaultdict(float)
    for t in trades:
        by_item[t.item] += t.realized_profit
    top3 = sorted(by_item.items(), key=lambda kv: kv[1], reverse=True)[:3]

    return {
        "total_realized_profit": total_profit,
        "average_margin": avg_margin,
        "top3_items_by_profit": top3,
    }
