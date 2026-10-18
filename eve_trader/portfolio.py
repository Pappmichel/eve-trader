"""Cross-cutting Trading + Production portfolio/risk overview.

The two tools stay fully independent everywhere else (separate config,
separate ESI scopes, separate UI sections/layouts) - this is the one place
that looks at both together, and it's deliberately additive: a combined
summary alongside each tool's own dedicated views, never a replacement for
either (confirmed with the user).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import storage
from .actions import ActionError
from .config import TRADING_CONFIG, TradingConfig
from .portfolio_wealth import assemble

# Same window vocabulary as the tool pages (production/realized/period.ts).
# "since_t0" leaves `start` unset so each tool's summary applies the ledger T0.
_PERIOD_DAYS = {"7": 7, "30": 30, "90": 90}
_CARD_LINKS = {
    "production": "/production/realized",
    "trading": "/trading/trades",
    "doctrine": "/doctrine/realized",
    "station_trading": "/station-trading/realized",
}
_CARD_ORDER = ("production", "trading", "doctrine", "station_trading")
_TOOL_LABELS = {
    "production": "Production",
    "trading": "Trading",
    "doctrine": "Doctrine",
    "station_trading": "Station Trading",
}


def _realized_window(period: str) -> dict:
    key = str(period or "30").strip()
    now = datetime.now(timezone.utc)
    if key == "since_t0":
        return {"start": None, "end": now, "since_t0": True, "days": None}
    if key not in _PERIOD_DAYS:
        raise ActionError("period must be 7, 30, 90, or since_t0.")
    days = _PERIOD_DAYS[key]
    return {"start": now - timedelta(days=days), "end": now, "since_t0": False, "days": days}


def _card_readers(tool_keys):
    keys = {str(key) for key in (tool_keys or ())}
    readers = []
    if "production" in keys:
        from .production.realized.report import summary as production_summary
        readers.append(("production", production_summary))
    if "trading" in keys:
        from .trade_realized.report import summary as trading_summary
        readers.append(("trading", trading_summary))
    if "doctrine" in keys:
        from .doctrine.realized import summary as doctrine_summary
        readers.append(("doctrine", doctrine_summary))
    if "station_trading" in keys:
        from .station_trading.realized import summary as station_summary
        readers.append(("station_trading", station_summary))
    return readers


def _sales_count(data: dict):
    if "sale_count" in data:
        return data["sale_count"]
    if "contracts" in data:
        return data["contracts"]
    return None


def _freight_of(data: dict) -> tuple[float, float]:
    """Courier and configured-rate freight from one tool's summary.

    Production and Trading put the split on `cost_structure`. Doctrine and
    Station Trading put it on `freight_from_couriers` / `freight_configured`.
    """
    rows = data.get("cost_structure")
    if isinstance(rows, list):
        amounts = {
            row.get("key"): row.get("amount")
            for row in rows if isinstance(row, dict)
        }
        if "freight_courier" in amounts or "freight_configured" in amounts:
            return (
                float(amounts.get("freight_courier") or 0.0),
                float(amounts.get("freight_configured") or 0.0),
            )
    courier = data.get("freight_from_couriers")
    if courier is None:
        courier = data.get("freight_courier")
    return float(courier or 0.0), float(data.get("freight_configured") or 0.0)


def realized_cards(tool_keys, period: str = "30") -> dict:
    """Realized profit for each granted tool over `period`, plus a total.

    `period` is 7, 30, 90, or since_t0. since_t0 is passed through to each
    tool's summary, which applies that ledger's T0. The total is the sum of
    every tool that has a figure, including when only one does (P2). A tool
    that still needs a recompute contributes no number, so a zero from an
    empty archive is not added in as a result. The page shows these numbers
    as returned.
    """
    window = _realized_window(period)
    rows = []
    courier = 0.0
    configured = 0.0
    saw_figure = False
    for tool, reader in _card_readers(tool_keys):
        data = reader(**window)
        empty = bool(data["status"]["empty"] or data["status"]["needs_rebuild"])
        if not empty:
            saw_figure = True
            part_courier, part_configured = _freight_of(data)
            courier += part_courier
            configured += part_configured
        rows.append({
            "tool": tool,
            "profit": None if empty else data["realized_profit"],
            "revenue": None if empty else data.get("revenue"),
            "margin": None if empty else data.get("margin"),
            "coverage": None if empty else data.get("coverage"),
            "estimated": False if empty else bool(data.get("estimated")),
            "empty": empty,
            "sales_count": None if empty else _sales_count(data),
            "link": _CARD_LINKS[tool],
        })
    rows.sort(key=lambda row: _CARD_ORDER.index(row["tool"]))
    ready = [row["profit"] for row in rows if row["profit"] is not None]
    return {
        "rows": rows,
        "total": sum(ready) if ready else None,
        "period": str(period or "30"),
        "freight": None if not saw_figure else {"courier": courier, "configured": configured},
    }


def _day(value) -> date:
    return date.fromisoformat(str(value)[:10])


def _week_start(day: date) -> date:
    """Monday, matching the tool pages' weekly bars."""
    return day - timedelta(days=day.weekday())


def _history_bucket(period: str, bucket: Optional[str], summaries: list[dict]) -> str:
    if bucket in ("day", "week"):
        return bucket
    if bucket not in (None, ""):
        raise ActionError("bucket must be day or week.")
    key = str(period or "30").strip()
    if key in _PERIOD_DAYS:
        return "week" if _PERIOD_DAYS[key] > 90 else "day"
    starts = []
    for data in summaries:
        start = (data.get("period") or {}).get("start")
        if start:
            starts.append(_day(start))
    if not starts:
        return "day"
    return "week" if (date.today() - min(starts)).days > 90 else "day"


def realized_history(tool_keys, period: str = "30", bucket: Optional[str] = None) -> dict:
    """Per-bucket realized profit for the channels this session holds.

    Built from each tool's summary()['by_day']. Days with no sale in any
    held channel are omitted. A held channel with no sale in a bucket is 0.
    Week buckets start on Monday. The running total is `cumulative`.
    """
    window = _realized_window(period)
    channels = []
    days_by_tool: dict[str, dict[date, float]] = {}
    summaries = []
    for tool, reader in _card_readers(tool_keys):
        data = reader(**window)
        channels.append(tool)
        summaries.append(data)
        empty = bool(data["status"]["empty"] or data["status"]["needs_rebuild"])
        grouped: dict[date, float] = {}
        if not empty:
            for row in data.get("by_day") or []:
                day = _day(row["date"])
                grouped[day] = grouped.get(day, 0.0) + float(row["profit"])
        days_by_tool[tool] = grouped
    chosen = _history_bucket(period, bucket, summaries)
    buckets: dict[date, dict[str, float]] = {}
    for tool, grouped in days_by_tool.items():
        for day, profit in grouped.items():
            key = _week_start(day) if chosen == "week" else day
            slot = buckets.setdefault(key, {})
            slot[tool] = slot.get(tool, 0.0) + profit
    rows = []
    running = 0.0
    for key in sorted(buckets):
        slot = buckets[key]
        total = sum(slot.get(tool, 0.0) for tool in channels)
        running += total
        row = {"date": key.isoformat(), "total": total, "cumulative": running}
        for tool in channels:
            row[tool] = slot.get(tool, 0.0)
        rows.append(row)
    return {
        "bucket": chosen,
        "channels": channels,
        "period": str(period or "30"),
        "rows": rows,
    }


def _parse_time(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _portfolio_data_kinds():
    from .esi_data.registry import OWNED_DATA_KINDS
    return [
        kind for kind in OWNED_DATA_KINDS
        if "portfolio" in kind.consuming_tools and not kind.live_only
    ]


def _tier_hours(cfg: TradingConfig, tier: str) -> float:
    return {
        "frequent": cfg.esi_frequent_interval_hours,
        "normal": cfg.esi_normal_interval_hours,
        "rare": cfg.esi_rare_interval_hours,
    }.get(tier, cfg.esi_normal_interval_hours)


def _stale_portfolio_labels(cfg: TradingConfig, now: datetime) -> list[str]:
    """Labels of Portfolio kinds whose shared owners are past the tier interval."""
    shares = storage.list_esi_sharing("portfolio")
    freshness = {
        (row[0], int(row[1]), row[2]): row[3]
        for row in storage.list_esi_freshness()
    }
    labels = []
    for kind in _portfolio_data_kinds():
        owners = [
            (owner_type, int(owner_id))
            for owner_type, owner_id, data_kind, _tool in shares
            if data_kind == kind.key
        ]
        if not owners:
            continue
        limit = now - timedelta(hours=_tier_hours(cfg, kind.freshness_tier))
        stale = False
        for owner in owners:
            synced = _parse_time(freshness.get((*owner, kind.key)))
            if synced is None or synced <= limit:
                stale = True
                break
        if stale:
            labels.append(kind.label)
    return labels


def attention(tool_keys, cfg: TradingConfig = TRADING_CONFIG, now: Optional[datetime] = None) -> dict:
    """What the Portfolio page should ask the viewer to do.

    Each item is `{key, text, link}`. A realized tool is listed only when
    its summary says `needs_rebuild`. An empty archive that already has a
    run is not a recompute. Stale ESI is one line naming the Portfolio
    kinds that are past their tier interval for an owner shared with
    Portfolio. Stock targets are listed only when this session holds
    Production and none are saved.
    """
    now = now or datetime.now(timezone.utc)
    items = []
    wealth = total_wealth(cfg)
    missing = wealth.get("characters_missing_wallet_scope") or []
    if missing:
        names = ", ".join(row["character_name"] for row in missing)
        verb = "has" if len(missing) == 1 else "have"
        items.append({
            "key": "wallet_scope",
            "text": (
                f"{names} {verb} no wallet scope, so their balance is left "
                "out of Total Wealth."
            ),
            "link": "/character-management/characters",
        })
    for tool, reader in _card_readers(tool_keys):
        data = reader(**_realized_window("30"))
        status = data.get("status") or {}
        if status.get("needs_rebuild"):
            items.append({
                "key": f"recompute_{tool}",
                "text": f"{_TOOL_LABELS[tool]} realized profit needs a recompute.",
                "link": _CARD_LINKS[tool],
            })
    unpriced = int(wealth.get("wealth_unpriced_items") or 0)
    if unpriced:
        noun = "item is" if unpriced == 1 else "items are"
        items.append({
            "key": "unpriced",
            "text": f"{unpriced} unpriced {noun} excluded from Total Wealth.",
            "link": "#unpriced",
        })
    stale = _stale_portfolio_labels(cfg, now)
    if stale:
        items.append({
            "key": "stale_esi",
            "text": "ESI data shared with Portfolio is stale: " + ", ".join(stale) + ".",
            "link": "/character-management/characters",
        })
    if "production" in {str(key) for key in (tool_keys or ())} and not storage.load_stock_targets():
        items.append({
            "key": "stock_targets",
            "text": "Production has no stock targets configured.",
            "link": "/production/stock-targets",
        })
    return {"items": items}


def wealth_change(today: Optional[date] = None) -> dict:
    """ISK and percent change of total_wealth versus the snapshot 1, 7 and
    30 days earlier. A missing row, or a row whose total_wealth is still
    NULL, is None for that horizon. Percent is None when the earlier
    wealth is 0. The page shows these numbers as returned.
    """
    today = today or date.today()
    rows = storage.load_portfolio_snapshots(since=today - timedelta(days=30))
    columns = ("snapshot_date",) + storage.PORTFOLIO_SNAPSHOT_COLUMNS
    by_date = {row[0]: dict(zip(columns, row)) for row in rows}
    current = by_date.get(today, {}).get("total_wealth")

    def point(days: int):
        past = by_date.get(today - timedelta(days=days))
        if current is None or past is None or past.get("total_wealth") is None:
            return None
        earlier = past["total_wealth"]
        isk = current - earlier
        return {"isk": isk, "pct": None if earlier == 0 else isk / earlier}

    return {"d1": point(1), "d7": point(7), "d30": point(30)}


def portfolio_overview(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Combines Trading's realized P&L (last 30 days of the ledger's Trading
    channel) with Production's stock value into one read-only summary, plus
    a simple day-to-day profit volatility signal. Each half degrades
    independently to zero/None if that tool has no data yet, rather than
    failing the whole overview - e.g. a fresh install with Trading data but
    no Production stock targets configured still shows what it has.

    daily_profit_volatility is a plain population standard deviation of
    per-day realized profit (not a formal Value-at-Risk model - this app has
    no options-pricing-grade statistics infrastructure, and a plain stdev of
    realized daily P&L is the standard, well-understood starting point real
    trading tools use for an at-a-glance risk indicator). None below 2 days
    of data - a single day has no meaningful spread.
    """
    from .trade_realized.report import portfolio_figures
    trading = portfolio_figures()
    trading_realized_profit = trading["trading_realized_profit"]
    trading_average_margin = trading["trading_average_margin"]
    daily_profit_volatility = trading["trading_daily_profit_volatility"]
    trade_count = trading["trading_trade_count"]

    production_stock_value = 0.0
    stock_targets_configured = bool(storage.load_stock_targets())
    if stock_targets_configured:
        from .production.config import PRODUCTION_CONFIG
        from .production.engine import stock_value
        production_stock_value = stock_value(PRODUCTION_CONFIG)["total_value"]

    return {
        "trading_realized_profit": trading_realized_profit,
        "trading_average_margin": trading_average_margin,
        "trading_daily_profit_volatility": daily_profit_volatility,
        "trading_trade_count": trade_count,
        "production_stock_value": production_stock_value,
        "production_stock_targets_configured": stock_targets_configured,
        "combined_value": trading_realized_profit + production_stock_value,
    }


def _priced(type_ids: set[int]) -> dict[int, float]:
    """Goonmetrics current-price quotes for `type_ids`, manual_item_prices
    overriding per type_id. Deliberately reuses production/pricing.py's
    raw Goonmetrics plumbing (`_goonmetrics_prices`, one full-market-list
    call each) rather than the narrower home_prices/jita_prices - those are
    ESI-first and scoped to explicit stock-target type_ids by design, while
    Total Wealth needs every type_id actually owned, a much larger,
    unpredictable set (PORTFOLIO_REWORK_PLAN.md section 6)."""
    ids = list(type_ids)
    if not ids:
        return {}
    from .production.config import PRODUCTION_CONFIG
    from .production.pricing import JITA_MARKET, _goonmetrics_prices
    manual = storage.load_manual_item_prices()
    home = _goonmetrics_prices(PRODUCTION_CONFIG.home_market, ids) if PRODUCTION_CONFIG.home_market else {}
    jita = _goonmetrics_prices(JITA_MARKET, ids)
    prices: dict[int, float] = {}
    for tid in ids:
        if tid in manual:
            prices[tid] = manual[tid]
            continue
        home_quote = home.get(tid)
        jita_quote = jita.get(tid)
        if home_quote and home_quote.sell > 0:
            prices[tid] = home_quote.sell
        elif jita_quote and jita_quote.sell > 0:
            prices[tid] = jita_quote.sell
    return prices


def _manual_only_priced(type_ids: set[int]) -> dict[int, float]:
    """Manual overrides only, no Goonmetrics fallback - used for blueprint
    *copies* (see total_wealth's own docstring): a BPC has no separate
    market quote from its BPO, so pricing it against the BPO's Goonmetrics
    quote would systematically overstate Total Wealth for anyone holding
    many copies. Confirmed with the user: an unpriced BPC is excluded from
    the total, same as any other unpriced item, until a manual price is
    set for it specifically."""
    if not type_ids:
        return {}
    manual = storage.load_manual_item_prices()
    return {tid: manual[tid] for tid in type_ids if tid in manual}


def _value_and_gaps(rows: list[tuple], prices: dict[int, float]) -> tuple[float, int, int]:
    """`rows`: tuples of (type_id, quantity) - the *effective* quantity,
    already resolved by the caller (see _blueprint_effective_quantity for
    why load_owned_blueprints' raw quantity is not usable as-is). Unpriced
    items are excluded from the total, not counted as 0 - same "explicit
    gap over silent understatement" precedent as
    production.engine.stock_value."""
    value = 0.0
    priced = 0
    unpriced = 0
    for type_id, quantity in rows:
        price = prices.get(type_id)
        if price is None:
            unpriced += 1
            continue
        value += quantity * price
        priced += 1
    return value, priced, unpriced


def _blueprint_effective_quantity(quantity: int) -> int:
    """ESI's `quantity` field on a blueprint item is usually a sentinel
    (-1 original / -2 copy), not a real stack size - same interpretation
    production/actions.py's do_list_owned_blueprints already applies to
    this exact field. Using the raw value directly (confirmed real bug in
    review) prices every owned BPO at *minus* one unit and every BPC at
    *minus* two."""
    return quantity if quantity and quantity > 0 else 1


def _characters_missing_wallet_scope(character_ids: set[int]) -> list[dict]:
    """`character_ids`: every character sharing *any* of assets/blueprints/
    wallet_balance with Portfolio (not just wallet_balance - confirmed real
    bug in review: a character who has never ticked wallet_balance at all
    would never be flagged under that narrower set, even though they are
    exactly the audience this banner exists to nudge). Flags whichever of
    them has no stored token carrying the wallet scope yet - real
    limitation stated in the UI, not hidden (see PORTFOLIO_REWORK_PLAN.md
    section 2): a character added only through Production ("producer"
    role) has no wallet scope at all until re-authorized via the
    Characters page."""
    if not character_ids:
        return []
    from .auth import TokenManager
    from .config import OAUTH_CONFIG
    has_scope: dict[int, bool] = {}
    names: dict[int, str] = {}
    for rec in TokenManager(OAUTH_CONFIG).list_records():
        if rec.character_id not in character_ids:
            continue
        if rec.character_name:
            names[rec.character_id] = rec.character_name
        if "esi-wallet.read_character_wallet.v1" in (rec.scopes or "").split():
            has_scope[rec.character_id] = True
        else:
            has_scope.setdefault(rec.character_id, False)
    return [
        {"character_id": cid, "character_name": names.get(cid, str(cid))}
        for cid in sorted(character_ids)
        if not has_scope.get(cid, False)
    ]


_WEALTH_KINDS = ("assets", "blueprints", "wallet_balance", "market_orders", "contracts")
_EMPTY_SYNC = {
    "assets": None, "blueprints": None, "listed": None,
    "contracts": None, "wallet": None, "escrow": None,
}
_EMPTY_WEALTH = {
    "total_wealth": None,
    "wealth_assets_value": None,
    "wealth_blueprints_value": None,
    "wealth_wallet_balance": None,
    "wealth_listed_value": None,
    "wealth_contract_value": None,
    "wealth_escrow": None,
    "wealth_priced_items": 0,
    "wealth_unpriced_items": 0,
    "characters_missing_wallet_scope": [],
    "sync": dict(_EMPTY_SYNC),
}
_EMPTY_BREAKDOWN = {
    "total": None, "by_owner": [], "by_location": [], "by_category": [], "top": [], "unpriced": [],
}


def _portfolio_shares() -> dict[str, tuple[list[int], list[int]]]:
    from .esi_data.access import shared_owner_ids
    return {
        kind: (
            shared_owner_ids(kind, "portfolio", "character"),
            shared_owner_ids(kind, "portfolio", "corporation"),
        )
        for kind in _WEALTH_KINDS
    }


def _any_shared(shares: dict) -> bool:
    return any(ids for pair in shares.values() for ids in pair)


def _owner_pairs(chars: list[int], corps: list[int]) -> list[tuple[str, int]]:
    return [("character", int(i)) for i in chars] + [("corporation", int(i)) for i in corps]


def _sync_times(shares: dict, escrow_known: bool) -> dict:
    rows = storage.list_esi_freshness()
    kind_for = {
        "assets": "assets", "blueprints": "blueprints", "listed": "market_orders",
        "contracts": "contracts", "wallet": "wallet_balance", "escrow": "market_orders",
    }
    out = {}
    for label, kind in kind_for.items():
        if label == "escrow" and not escrow_known:
            out[label] = None
            continue
        chars, corps = shares[kind]
        owners = set(_owner_pairs(chars, corps))
        times = [
            row[3] for row in rows
            if row[2] == kind and (row[0], int(row[1])) in owners and row[3]
        ]
        out[label] = max(times) if times else None
    return out


def _collect_wealth(cfg: TradingConfig = TRADING_CONFIG):
    """Rows Total Wealth and the breakdown both price. None when nobody
    shares any Portfolio kind. Listed sells and outstanding contracts are
    their own rows, never folded into assets."""
    shares = _portfolio_shares()
    if not _any_shared(shares):
        return None
    asset_chars, asset_corps = shares["assets"]
    bp_chars, bp_corps = shares["blueprints"]
    wallet_chars, wallet_corps = shares["wallet_balance"]
    order_chars, order_corps = shares["market_orders"]
    contract_chars, contract_corps = shares["contracts"]

    assets = storage.load_assets_grouped(asset_chars, asset_corps) if (asset_chars or asset_corps) else []
    blueprints = storage.load_blueprints_grouped(bp_chars, bp_corps) if (bp_chars or bp_corps) else []
    listed = storage.load_sell_orders_grouped(order_chars, order_corps) if (order_chars or order_corps) else []
    contract_rows = []
    if contract_chars or contract_corps:
        from .realized.db import outstanding_contract_lines
        contract_rows = outstanding_contract_lines(_owner_pairs(contract_chars, contract_corps))
    wallet_rows = (
        storage.wallet_balances_by_owner(wallet_chars, wallet_corps)
        if (wallet_chars or wallet_corps) else []
    )
    escrow_rows = []
    escrow_known = True
    if order_chars or order_corps:
        from .realized.db import open_buy_escrow_by_owner
        escrow_rows = open_buy_escrow_by_owner(_owner_pairs(order_chars, order_corps))
        escrow_known = escrow_rows is not None

    items = []
    for owner_type, owner_id, location_id, type_id, quantity in assets:
        items.append({
            "component": "assets", "owner_type": owner_type, "owner_id": owner_id,
            "location_id": location_id, "type_id": type_id, "quantity": quantity,
            "manual_only": False,
        })
    for owner_type, owner_id, location_id, type_id, quantity, runs in blueprints:
        items.append({
            "component": "blueprints", "owner_type": owner_type, "owner_id": owner_id,
            "location_id": location_id, "type_id": type_id,
            "quantity": _blueprint_effective_quantity(quantity),
            "manual_only": runs != -1,
        })
    for owner_type, owner_id, location_id, type_id, quantity in listed:
        items.append({
            "component": "listed", "owner_type": owner_type, "owner_id": owner_id,
            "location_id": location_id, "type_id": type_id, "quantity": quantity,
            "manual_only": False,
        })
    for owner_type, owner_id, type_id, quantity in contract_rows:
        items.append({
            "component": "contracts", "owner_type": owner_type, "owner_id": owner_id,
            "location_id": None, "type_id": type_id, "quantity": quantity,
            "manual_only": False,
        })

    market_ids = {item["type_id"] for item in items if not item["manual_only"]}
    manual_ids = {item["type_id"] for item in items if item["manual_only"]}
    prices = _priced(market_ids)
    manuals = _manual_only_priced(manual_ids)
    for item in items:
        book = manuals if item["manual_only"] else prices
        item["price"] = book.get(item["type_id"])

    wallets = [
        {"owner_type": owner_type, "owner_id": owner_id, "amount": amount}
        for owner_type, owner_id, amount in wallet_rows
    ]
    return {
        "items": items,
        "wallets": wallets,
        "escrow": None if not escrow_known else [
            {"owner_type": owner_type, "owner_id": owner_id, "amount": amount}
            for owner_type, owner_id, amount in (escrow_rows or [])
        ],
        "shares": shares,
        "escrow_known": escrow_known,
        "structure_id": cfg.structure_id,
    }


def _wealth_from(collected: dict) -> dict:
    built = assemble(
        collected["items"], collected["wallets"], collected["escrow"],
        structure_id=collected["structure_id"],
    )
    parts = built["components"]
    shared_characters = set()
    for kind in ("assets", "blueprints", "wallet_balance"):
        shared_characters.update(collected["shares"][kind][0])
    return {
        "total_wealth": built["total"],
        "wealth_assets_value": parts["assets"],
        "wealth_blueprints_value": parts["blueprints"],
        "wealth_wallet_balance": parts["wallet"],
        "wealth_listed_value": parts["listed"],
        "wealth_contract_value": parts["contracts"],
        "wealth_escrow": parts["escrow"],
        "wealth_priced_items": built["priced_items"],
        "wealth_unpriced_items": built["unpriced_items"],
        "characters_missing_wallet_scope": _characters_missing_wallet_scope(shared_characters),
        "sync": _sync_times(collected["shares"], collected["escrow_known"]),
        "_built": built,
    }


def total_wealth(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Assets + blueprints + wallet balances across every owner sharing
    with "portfolio" specifically - sharing the same data with another
    tool does not expose it here (PORTFOLIO_REWORK_PLAN.md section 4).

    total_wealth and each component stay None (not 0.0) until any owner
    shares anything with "portfolio" at all - a real gap ("nobody has
    opted in yet"), not "everything they own is worthless" (confirmed
    real bug in review: the original version returned 0.0 here, which the
    History chart then drew as an indistinguishable flat zero line).

    Listed sell orders and outstanding contract items are added on top of
    assets. ESI does not list those in the asset endpoint, so the same
    type in both piles is not subtracted. Buy-order escrow is None until
    a sync stores it, and that None is not added into the total. A kind
    that is not shared is 0 (known empty), not None.

    Blueprint pricing caveat, stated in the UI, not silently approximated
    away: a blueprint's ME/TE materially changes what it would actually
    sell for, but Goonmetrics has one quote per type_id, not per ME/TE
    level - every BPO is valued at the same market quote regardless of its
    own research level. The manual-price override exists partly to let a
    user correct an individual high-value BPO.

    A BPC (runs != -1) is deliberately priced *only* against
    manual_item_prices, never against a BPO's Goonmetrics quote - a copy
    has no separate market quote of its own, and pricing it against the
    original's would systematically overstate Total Wealth for a
    character holding many copies (confirmed with the user: an unpriced
    BPC is excluded from the total, exactly like any other unpriced item,
    until a manual price is set for it specifically - see
    _manual_only_priced).

    Sharing scope note (working as designed, not a bug, but easy to
    misread): a character who shares assets but not blueprints with
    Portfolio contributes their non-blueprint assets to wealth_assets_value
    but their BPOs/BPCs do not appear anywhere in Total Wealth at all
    (neither counted as assets - load_all_assets always excludes blueprint
    item_ids - nor as blueprints, since that requires its own separate
    opt-in). Each data kind's sharing is independent by design (section 4);
    this is just that design's visible edge case for blueprints
    specifically, since (unlike assets/wallet) there's no partial-credit
    fallback for them."""
    collected = _collect_wealth(cfg)
    if collected is None:
        return {**_EMPTY_WEALTH, "sync": dict(_EMPTY_SYNC)}
    result = _wealth_from(collected)
    result.pop("_built", None)
    return result


def do_get_portfolio_breakdown(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Owner, location, category and top-20 shares of Total Wealth.

    The same rows as total_wealth(), so the three full breakdowns sum to
    that total. Top 20 is a slice. Unpriced rows are listed and excluded
    from the sums. Names are resolved here; the pure totals do not need them.
    """
    collected = _collect_wealth(cfg)
    if collected is None:
        return dict(_EMPTY_BREAKDOWN)
    from .realized.names import owner_names
    items = collected["items"]
    wallets = collected["wallets"]
    escrow = collected["escrow"] or []
    owner_ids = {int(item["owner_id"]) for item in items}
    owner_ids.update(int(row["owner_id"]) for row in wallets)
    owner_ids.update(int(row["owner_id"]) for row in escrow)
    location_ids = [item["location_id"] for item in items if item.get("location_id") is not None]
    type_ids = {int(item["type_id"]) for item in items}
    built = assemble(
        items, wallets, collected["escrow"],
        structure_id=collected["structure_id"],
        owner_names=owner_names(owner_ids),
        location_names=storage.get_location_names(location_ids),
        category_names=storage.sde_categories_for_types(type_ids),
        type_names=storage.sde_type_names(type_ids),
    )
    return {
        "total": built["total"],
        "by_owner": built["by_owner"],
        "by_location": built["by_location"],
        "by_category": built["by_category"],
        "top": built["top"],
        "unpriced": built["unpriced"],
    }


def do_get_portfolio_capital(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Cost and market value of capital in the realized-profit ledger.

    Owners are the union of realized-tool shares, not the Portfolio share.
    Prices are the same manual / home sell / Jita sell function as Total Wealth.
    """
    from .portfolio_capital import report
    from .realized.access import shared_item_exchange_owners, shared_owners
    from .realized import db as ledger_db

    order_owners = shared_owners("market_orders")
    contract_owners = shared_item_exchange_owners()
    layers = [
        {
            "type_id": row[0], "quantity": row[2], "unit_cost": row[3], "origin": row[4],
            "assumed_me": row[5], "assumed_decryptor": row[6], "estimated_origin": row[7],
            "price_missing": row[8],
        }
        for row in ledger_db.list_remaining_layers()
    ]
    listed = [
        {"type_id": type_id, "quantity": quantity, "order_price": price}
        for type_id, quantity, price in ledger_db.open_sell_order_lines(order_owners)
    ]
    contracts = [
        {"contract_id": contract_id, "type_id": type_id, "quantity": quantity, "contract_price": price}
        for contract_id, type_id, quantity, price in ledger_db.outstanding_priced_contract_items(contract_owners)
    ]
    escrow_rows = ledger_db.open_buy_escrow_by_owner(order_owners)
    escrow = None if escrow_rows is None else sum(amount for _ot, _oid, amount in escrow_rows)
    type_ids = {int(layer["type_id"]) for layer in layers}
    type_ids.update(int(row["type_id"]) for row in listed)
    type_ids.update(int(row["type_id"]) for row in contracts)
    prices = _priced(type_ids)
    count, cost = ledger_db.running_job_cost()
    in_production = {"count": int(count or 0), "cost": float(cost or 0)}
    return report(layers, listed, contracts, escrow, prices, in_production)


def take_portfolio_snapshot(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Computes portfolio_overview() + total_wealth() and upserts today's
    row into portfolio_snapshots - idempotent, safe to call more than once
    on the same day (see storage.upsert_portfolio_snapshot). The two
    triggers (scheduler job + lazy fallback on page load) both call this
    same function, never portfolio_overview()/total_wealth() directly, so
    there is one write path, not two implementations that could drift.

    wealth_blueprints_value, the priced/unpriced counts, the wallet-scope
    list and sync times are not snapshot columns. wealth_listed_value,
    wealth_contract_value and wealth_escrow are. Escrow stays NULL until
    a sync stores it. Only the declared snapshot columns are persisted.
    """
    overview = portfolio_overview(cfg)
    wealth = total_wealth(cfg)
    storage.upsert_portfolio_snapshot(date.today(), {**overview, **wealth})
    return {**overview, **wealth}


def do_get_portfolio_overview(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """`GET /api/portfolio/overview`'s own action - a real decision (has a
    snapshot been taken today yet?), not a bare passthrough, so it lives
    here rather than directly in the router. Takes today's snapshot lazily
    on the first overview read of the day - the fallback path for when the
    scheduler is disabled (the default), so history still fills in one row
    per day purely from normal page usage. Every later read that same day
    is the cheap, plain live read - take_portfolio_snapshot() is never
    called more than once per day from here."""
    if storage.latest_portfolio_snapshot_date() != date.today():
        overview = take_portfolio_snapshot(cfg)
    else:
        overview = portfolio_overview(cfg)
    return {**overview, "wealth_change": wealth_change()}


def do_get_portfolio_history(days: Optional[int] = None) -> list[dict]:
    """`GET /api/portfolio/history`'s own action. `days=None` returns every
    snapshot this tenant has ever taken (unbounded retention); otherwise
    only the last `days` days, inclusive of today. `days=0` (or negative)
    is clamped to 1 - the formula below computes a `since` date one day in
    the *future* for a non-positive `days`, which filtered out every row
    including today's (confirmed real bug in review), rather than a
    reasonable "just today" reading."""
    since = date.today() - timedelta(days=max(days, 1) - 1) if days is not None else None
    rows = storage.load_portfolio_snapshots(since=since)
    columns = ("snapshot_date",) + storage.PORTFOLIO_SNAPSHOT_COLUMNS
    return [dict(zip(columns, row)) for row in rows]


def do_list_manual_item_prices() -> dict:
    """Manual prices used only by Total Wealth when Goonmetrics has no
    quote for an owned item type (PORTFOLIO_REWORK_PLAN.md section 7) -
    Production's/Trading's own pricing chains are untouched, this table is
    Portfolio's own."""
    rows = [
        {"type_id": type_id, "type_name": type_name, "price": price, "updated_at": updated_at}
        for type_id, type_name, price, updated_at in storage.list_manual_item_prices()
    ]
    return {"rows": rows}


def do_set_manual_item_price(item_name: str, price: float) -> dict:
    """Resolves `item_name` (exact match, same lookup Production's manual
    override actions use) to a type_id and registers/updates its manual
    price."""
    if price < 0:
        raise ActionError("Price must not be negative.")
    matches = storage.search_sde_types(item_name, limit=2)
    exact = [m for m in matches if m[1].lower() == item_name.strip().lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{item_name}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{item_name}'. Did you mean: {matches[0][1]}?")
    type_id, resolved_name = exact[0]
    storage.upsert_manual_item_price(type_id, resolved_name, price)
    return {"type_id": type_id, "type_name": resolved_name, "price": price}


def do_remove_manual_item_price(type_id: int) -> dict:
    storage.delete_manual_item_price(type_id)
    return {"removed": type_id}
