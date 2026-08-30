"""Realized Trade History reconciliation.

Pulls wallet transactions for two characters (buyer imports in Jita, seller
sells in the structure) over a lookback window, and matches buys against
sells per item (FIFO) to compute realized profit. The sell side's tax
deduction uses the real per-sale amount from the wallet *journal* when
available (see fetch_recent_journal_entries/_ASSUMED_TAX_RATE_IN_DEFAULT_
HAIRCUT), falling back to a fully modeled haircut otherwise; the buy side
and broker's fee stay modeled (ESI has no per-fill broker-fee attribution -
see PB-03 in the 2026-08-29 business-logic audit).
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

# Buys are fetched over a longer window than sells - a sale inside
# cfg.lookback_days can legitimately be funded by inventory bought well
# before that window started (an item just sitting at the structure waiting
# to sell), and the FIFO matcher's buy_date <= sell_date rule (PB-02, a
# separate confirmed bug fixed the same day this was found) means a too-old
# buy that was never even fetched looks identical to "no real cost basis" -
# the sale is dropped instead of matched to a fabricated later buy, which is
# safe but an avoidable under-report (PB-05, business-logic audit,
# 2026-08-29). A flat multiplier (not a separate persisted config field)
# scales with however long the user has already configured "recent" to
# mean, while staying bounded - unlike an unbounded/no-cutoff buy fetch,
# which would risk very slow reconciliation for a character with years of
# trading history.
_BUY_LOOKBACK_MULTIPLIER = 3

# PB-03 (business-logic audit, 2026-08-29): net_sell used to be entirely
# modeled (sell_unit_price x structure_sell_haircut) even though ESI's
# wallet journal has the REAL sales tax for each specific sell (via a
# transaction's own journal_ref_id -> the matching journal entry's `amount`,
# already net of that real tax - confirmed against ESI's own OpenAPI spec).
# Only the tax portion is fixable this way: broker's fee is charged once per
# ORDER, not per fill, so it can't be attributed to one specific FIFO-matched
# sale the way tax can - confirmed with the user (2026-08-29) to leave that
# portion modeled rather than guess at an order-level allocation.
#
# structure_sell_haircut bundles SCC surcharge + broker's fee + sales tax
# into one multiplier (see its own default-derivation comment in config.py:
# "SCC surcharge 0.5% + Broker's fee 1.5% + Sales tax 3.37% = 5.37% total").
# To swap in the real tax without a new config field to hold the SCC+broker
# portion separately, this is the assumed tax rate baked into that *default*
# 0.9463 value, used only to back it out: (structure_sell_haircut +
# _ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT) isolates the SCC+broker-only
# retention ratio, which then multiplies the *real* post-tax proceeds
# instead of the raw unit price. Exact when structure_sell_haircut is still
# its default; a tenant who has customized it away from 0.9463 (different
# real skills/standings) gets a close-but-not-exact SCC+broker estimate -
# still strictly more accurate on the tax term than the fully-modeled
# formula, which is the one thing this fix set out to improve.
_ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT = 0.0337

_MARKET_TRANSACTION_REF_TYPE = "market_transaction"


def fetch_recent_journal_entries(character_id: int, auth_role: str, client: ESIClient,
                                  lookback_days: int) -> dict[int, float]:
    """{journal entry id: amount} for this character's `market_transaction`
    journal entries within `lookback_days` - the lookup reconcile_realized_
    trades uses to find a specific sell's real post-tax proceeds via its
    wallet-transaction's own `journal_ref_id`. Best-effort: any ESI failure
    (missing scope, outage, ...) returns {} rather than raising, so a wallet-
    journal problem degrades reconciliation to the fully-modeled formula
    instead of blocking it entirely - same spirit as this module's other
    best-effort fallbacks (_type_info below)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        entries = client.character_wallet_journal(character_id, auth_role=auth_role)
    except Exception:  # noqa: BLE001 - best-effort; modeled fallback is always safe
        return {}
    return {
        entry["id"]: entry["amount"]
        for entry in entries
        if entry.get("ref_type") == _MARKET_TRANSACTION_REF_TYPE and _parse_iso(entry["date"]) >= cutoff
    }


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
        buys.extend(fetch_recent_transactions(character_id, role, client,
                                               cfg.lookback_days * _BUY_LOOKBACK_MULTIPLIER))
    sells = []
    for character_id, role in seller_characters:
        sells.extend(fetch_recent_transactions(character_id, role, client, cfg.lookback_days))

    # PB-03: real post-tax proceeds per journal entry id, for whichever
    # sells actually have one - see fetch_recent_journal_entries/
    # _ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT's own comments above.
    journal_amount_by_ref_id: dict[int, float] = {}
    for character_id, role in seller_characters:
        journal_amount_by_ref_id.update(fetch_recent_journal_entries(character_id, role, client, cfg.lookback_days))

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
                if buy["date"] > sell["date"]:
                    # Confirmed real bug (business-logic audit, 2026-08-29):
                    # a sale can't be funded by inventory bought *after* it
                    # sold - but FIFO here only ordered buys chronologically,
                    # never checked a matched buy actually predates its sell.
                    # Live evidence: 324 of 1640 realized_trades rows (20%)
                    # had buy_date > sell_date, accounting for 21.8% of the
                    # reported net realized profit - happens whenever a sell
                    # has no in-window buy old enough to be its real cost
                    # basis (most commonly: pre-window inventory, bought
                    # before cfg.lookback_days even started) and FIFO reached
                    # for the next available buy regardless of its date.
                    # buy_queue is sorted ascending and buy_idx never
                    # rewinds, so every later buy is >= this one's date too -
                    # break (not skip past it) leaves it for a later,
                    # actually-later-dated sell to still reach; the
                    # unmatched remainder of *this* sell is simply dropped,
                    # same "no real data yet" honesty as elsewhere in this
                    # module (see average_daily_sold_by_type's own docstring)
                    # rather than fabricating a cost basis. Doesn't recover a
                    # sell's true pre-window cost basis (a separate, larger
                    # fix - seed the FIFO queue with real pre-window
                    # inventory) - this only stops it from silently
                    # substituting a wrong, later one.
                    break
                matched = min(remaining_to_match, buy["quantity"])
                if matched <= 0:
                    buy_idx += 1
                    continue
                # cfg.import_cost_per_m3 is an ISK-per-m3 *rate*, never a flat
                # per-unit fee - freight must always scale with the item's own
                # per-unit volume, or cheap/small/bulk-traded items (ammo, ice
                # products, ...) get a wildly overstated landed cost.
                freight = volumes[type_id] * cfg.import_cost_per_m3
                # jita_buy_broker_fee is still fully *modeled* (config.py) -
                # broker's fee is charged once per order, not per fill, so it
                # can't be attributed to this specific matched buy the way
                # sales tax can be (see PB-03's comment above) - real
                # skill/standing changes over the lookback window could still
                # make this side of Realized Trades drift from what actually
                # landed in the wallet.
                landed = buy["unit_price"] * (1 + cfg.jita_buy_broker_fee) + freight
                journal_amount = journal_amount_by_ref_id.get(sell.get("journal_ref_id"))
                if journal_amount is not None and sell["quantity"]:
                    # Real post-tax proceeds (ESI wallet journal) scaled by
                    # the SCC+broker-only retention ratio backed out of the
                    # modeled haircut - see _ASSUMED_TAX_RATE_IN_DEFAULT_
                    # HAIRCUT's own comment for why this, not the real tax
                    # amount, replaces the sell["unit_price"] x haircut term.
                    net_sell = (journal_amount / sell["quantity"]) * (
                        cfg.structure_sell_haircut + _ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT
                    )
                else:
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


def average_daily_sold_by_type(cfg: TradingConfig = TRADING_CONFIG) -> dict[int, float]:
    """Real average daily *sold* quantity per type_id, computed from the last
    Reconcile Trades run's realized_trades rows (storage.save_realized_trades
    wholesale-replaces that table every run with exactly one
    cfg.lookback_days window's worth of matched sells, not an accumulating
    log - see that function's own comment). Sums `matched_qty` (the actual
    FIFO-matched sale amount, not the original transaction's full
    buy_qty/sell_qty, which can span multiple matches) per type_id and
    divides by cfg.lookback_days.

    GitHub issue #51: this - not the structure's live order-book remaining
    quantity (esi_client.OrderStats.sell_volume, "how much is listed right
    now") - is what the Shortlist's "Profit / Day" figure is computed from.
    An item never actually sold (e.g. a fresh candidate with a large order
    book from a single seller) is simply absent here, not estimated from
    listed quantity - see shortlist.evaluate_shortlist_item's
    avg_daily_sold parameter.

    Returns {} if Reconcile Trades has never been run (or found nothing to
    match) - every item's avg_daily_sold then stays None, an honest "no real
    sales data yet" rather than a number derived from something else."""
    df = storage.read_table("realized_trades")
    if df.empty or cfg.lookback_days <= 0:
        return {}
    latest = df[df["run_ts"] == df["run_ts"].max()]
    sold = latest.groupby("type_id")["matched_qty"].sum()
    return {int(type_id): float(qty) / cfg.lookback_days for type_id, qty in sold.items()}
