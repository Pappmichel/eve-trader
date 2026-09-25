"""Cross-cutting Trading + Production portfolio/risk overview.

The two tools stay fully independent everywhere else (separate config,
separate ESI scopes, separate UI sections/layouts) - this is the one place
that looks at both together, and it's deliberately additive: a combined
summary alongside each tool's own dedicated views, never a replacement for
either (confirmed with the user).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from . import storage
from .actions import ActionError
from .config import TRADING_CONFIG, TradingConfig


def portfolio_overview(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Combines Trading's realized P&L (latest reconciliation run) with
    Production's stock value into one read-only summary, plus a simple
    day-to-day profit volatility signal. Each half degrades independently to
    zero/None if that tool has no data yet, rather than failing the whole
    overview - e.g. a fresh install with Trading data but no Production stock
    targets configured still shows what it has.

    daily_profit_volatility is a plain population standard deviation of
    per-day realized profit (not a formal Value-at-Risk model - this app has
    no options-pricing-grade statistics infrastructure, and a plain stdev of
    realized daily P&L is the standard, well-understood starting point real
    trading tools use for an at-a-glance risk indicator). None below 2 days
    of data - a single day has no meaningful spread.
    """
    trades_df = storage.read_table("realized_trades")
    if trades_df.empty:
        trading_realized_profit = 0.0
        trading_average_margin = 0.0
        daily_profit_volatility: Optional[float] = None
        trade_count = 0
    else:
        latest_run = trades_df["run_ts"].max()
        latest = trades_df[trades_df["run_ts"] == latest_run]
        trading_realized_profit = float(latest["realized_profit"].sum())
        weighted_denom = float((latest["buy_unit_price"] * latest["matched_qty"]).sum())
        trading_average_margin = (trading_realized_profit / weighted_denom) if weighted_denom else 0.0
        daily = latest.assign(day=latest["sell_date"].str.slice(0, 10)).groupby("day")["realized_profit"].sum()
        daily_profit_volatility = float(daily.std(ddof=0)) if len(daily) >= 2 else None
        trade_count = int(len(latest))

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


def total_wealth(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Assets + blueprints + wallet balances across every owner sharing
    with "portfolio" specifically - sharing the same data with another
    tool does not expose it here (PORTFOLIO_REWORK_PLAN.md section 4).

    total_wealth/wealth_assets_value/wealth_blueprints_value/wealth_
    wallet_balance stay None (not 0.0) until any owner shares anything
    with "portfolio" at all - a real gap ("nobody has opted in yet"), not
    "everything they own is worthless" (confirmed real bug in review: the
    original version returned 0.0 here, which the History chart then drew
    as an indistinguishable flat zero line).

    Blueprint pricing caveat, stated in the UI, not silently approximated
    away: a blueprint's ME/TE materially changes what it would actually
    sell for, but Goonmetrics has one quote per type_id, not per ME/TE
    level - every copy of a blueprint type is valued at the same market
    quote regardless of its own research level. The manual-price override
    exists partly to let a user correct an individual high-value BPO.

    A second, cruder approximation for the same reason: a BPC is priced
    identically to a BPO of the same type, even though a copy is normally
    worth only a fraction of an original - Goonmetrics has no separate BPC
    quote to price it against. This can materially overstate Total Wealth
    for a character holding many copies; correcting it (e.g. treating an
    unpriced-by-manual-override BPC as unpriced rather than BPO-priced, or
    a dedicated copy-value discount) is a real follow-up, not done here
    without confirming the right approach with the user first.

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
    from .esi_data.access import shared_owner_ids

    asset_char_ids = shared_owner_ids("assets", "portfolio", "character")
    asset_corp_ids = shared_owner_ids("assets", "portfolio", "corporation")
    bp_char_ids = shared_owner_ids("blueprints", "portfolio", "character")
    bp_corp_ids = shared_owner_ids("blueprints", "portfolio", "corporation")
    wallet_char_ids = shared_owner_ids("wallet_balance", "portfolio", "character")
    wallet_corp_ids = shared_owner_ids("wallet_balance", "portfolio", "corporation")

    shared_character_ids = set(asset_char_ids) | set(bp_char_ids) | set(wallet_char_ids)

    if not (asset_char_ids or asset_corp_ids or bp_char_ids or bp_corp_ids
            or wallet_char_ids or wallet_corp_ids):
        return {
            "total_wealth": None,
            "wealth_assets_value": None,
            "wealth_blueprints_value": None,
            "wealth_wallet_balance": None,
            "wealth_priced_items": 0,
            "wealth_unpriced_items": 0,
            "characters_missing_wallet_scope": [],
        }

    assets = storage.load_all_assets(asset_char_ids, asset_corp_ids)
    raw_blueprints = storage.load_owned_blueprints(bp_char_ids, bp_corp_ids)
    blueprints = [(type_id, _blueprint_effective_quantity(quantity)) for type_id, quantity, *_rest in raw_blueprints]
    wallet_total = storage.sum_wallet_balances(wallet_char_ids, wallet_corp_ids)

    type_ids = {type_id for type_id, _qty in assets} | {type_id for type_id, _qty in blueprints}
    prices = _priced(type_ids)

    assets_value, assets_priced, assets_unpriced = _value_and_gaps(assets, prices)
    blueprints_value, bp_priced, bp_unpriced = _value_and_gaps(blueprints, prices)

    return {
        "total_wealth": assets_value + blueprints_value + wallet_total,
        "wealth_assets_value": assets_value,
        "wealth_blueprints_value": blueprints_value,
        "wealth_wallet_balance": wallet_total,
        "wealth_priced_items": assets_priced + bp_priced,
        "wealth_unpriced_items": assets_unpriced + bp_unpriced,
        "characters_missing_wallet_scope": _characters_missing_wallet_scope(shared_character_ids),
    }


def take_portfolio_snapshot(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Computes portfolio_overview() + total_wealth() and upserts today's
    row into portfolio_snapshots - idempotent, safe to call more than once
    on the same day (see storage.upsert_portfolio_snapshot). The two
    triggers (scheduler job + lazy fallback on page load) both call this
    same function, never portfolio_overview()/total_wealth() directly, so
    there is one write path, not two implementations that could drift.

    wealth_blueprints_value/wealth_priced_items/wealth_unpriced_items/
    characters_missing_wallet_scope are not portfolio_snapshots columns
    (blueprint value = total_wealth - wealth_assets_value -
    wealth_wallet_balance, per that table's own comment) - they pass
    through in the returned dict for the live overview read, but only the
    declared snapshot columns are persisted.
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
        return take_portfolio_snapshot(cfg)
    return portfolio_overview(cfg)


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
