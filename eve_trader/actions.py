"""Pipeline actions - the actual work behind every CLI command / API route.
Kept UI-agnostic (no click.echo calls) and returns plain dicts/strings so both
`cli.py` and the FastAPI routers (eve_trader/api/routers/) call the exact same
code and can't drift apart.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import sqlite3

import pandas as pd

from . import backup, candidate_discovery, history_backtest, own_orders, storage
from .auth import TokenManager
from .config import OAUTH_CONFIG, TRADING_CONFIG, ConfigError, OAuthConfig, TradingConfig, save_config_overrides
from .esi_client import ESIClient, ESIError
from .goonmetrics_client import GoonmetricsClient
from .models import Candidate, ShortlistItem, UndercutRow, UnlistedStockRow
from .shortlist import audit_shortlist, evaluate_shortlist, summary_counts, top_imports_by_daily_profit
from .trade_reconciliation import reconcile_realized_trades, summarize_realized

log = logging.getLogger("eve_trader.actions")


class ActionError(RuntimeError):
    """Raised for expected/user-facing problems (missing auth, empty tables, ...)."""


def now_ts() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def _int_or_none(v):
    """SQLite NULL round-trips through pandas as NaN/None depending on column dtype."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return int(v)


def do_auth(role: str, scopes: list[str] | None = None, oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """`scopes` overrides oauth_cfg.scopes for this role only - lets a new role
    (e.g. "producer") request its own scope set without changing what buyer/
    seller request, so an unrelated role's re-auth can't suddenly ask for
    scopes the EVE dev-portal app doesn't have enabled."""
    tm = TokenManager(oauth_cfg)
    record = tm.get_token_interactive(role, scopes=scopes)
    return {"role": role, "character_name": record.character_name, "character_id": record.character_id}


def do_update_settings(updates: dict, cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Persists `updates` to config.yaml and applies them to the live
    TRADING_CONFIG immediately (see Settings tab in the dashboard)."""
    try:
        save_config_overrides(updates, cfg)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return updates


def do_build_universe(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    client = ESIClient(cfg)
    candidates = candidate_discovery.build_candidate_universe(client)
    storage.save_candidate_universe(candidates, now_ts(), table="candidate_universe")
    return {"count": len(candidates)}


def do_build_focused(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    universe_df = storage.read_table("candidate_universe")
    if universe_df.empty:
        raise ActionError("Candidate Universe is empty - run 'Load Market Groups' first.")
    universe = [Candidate(item=r.item, type_id=r.type_id, volume_m3=r.volume_m3,
                           category=r.category, market_group_path=r.market_group_path,
                           meta_level=_int_or_none(r.meta_level))
                for r in universe_df.itertuples()]
    focused = candidate_discovery.build_focused_candidate_universe(universe, cfg)
    storage.save_candidate_universe(focused, now_ts(), table="focused_candidates")
    return {"count": len(focused)}


def do_find_new_candidates(safe: bool = True, cfg: TradingConfig = TRADING_CONFIG) -> dict:
    df = storage.read_table("focused_candidates")
    if df.empty:
        raise ActionError("Focused Candidates is empty - run 'Filter Candidates' first.")
    candidates = [Candidate(item=r.item, type_id=r.type_id, volume_m3=r.volume_m3,
                             category=r.category, market_group_path=r.market_group_path,
                             meta_level=_int_or_none(r.meta_level))
                  for r in df.itertuples()]
    existing_ids = {i.item_id for i in storage.load_shortlist() if i.active}
    gm = GoonmetricsClient(cfg)
    run_ts = now_ts()
    # Every internal batch is saved as soon as it's scored (not just once at
    # the very end) - a full (non-safe) run over the whole candidate universe
    # can take minutes and touch thousands of Goonmetrics/ESI requests, so if
    # it gets interrupted partway through (restart, crash, an unrecoverable
    # error in one batch - see find_new_import_candidates' per-batch
    # isolation), whatever was already scored is kept instead of lost.
    results_sink = lambda batch: storage.save_new_candidates(batch, run_ts)  # noqa: E731
    # Safe mode rotates through the candidate list max_ids at a time (see
    # history_backtest.select_candidate_window) instead of re-testing the
    # same items every run - persisting the cursor here guarantees every
    # candidate eventually gets backtested, not just "maybe, eventually".
    if safe:
        offset = storage.get_candidate_search_offset()
        results, next_offset = history_backtest.find_new_import_candidates_safe(
            candidates, existing_ids, gm, cfg, offset=offset,
            history_sink=storage.save_goonmetrics_history, results_sink=results_sink)
        storage.set_candidate_search_offset(next_offset)
    else:
        results, _ = history_backtest.find_new_import_candidates(
            candidates, existing_ids, gm, cfg,
            history_sink=storage.save_goonmetrics_history, results_sink=results_sink)
    return {"evaluated": len(results), "recommended": sum(r.add for r in results)}


def do_add_to_shortlist() -> dict:
    df = storage.read_table("new_candidates")
    if df.empty:
        raise ActionError("No 'New Candidates' available - run '⚡ Search + Add + Clean Up' first.")
    latest_run = df["run_ts"].max()
    df = df[(df["run_ts"] == latest_run) & (df["add_flag"] == 1)]
    # Re-derive category fresh from the SDE instead of trusting new_candidates.
    # category - that value was computed whenever candidate_universe/
    # focused_candidates were last rebuilt (① Load Market Groups / ② Filter
    # Candidates), which can be long before this add and can predate a
    # categorization logic change, silently reintroducing stale labels (e.g.
    # skills/implants showing as "Material" again) for every item added after
    # such a change but before the next rebuild. Confirmed real bug: 441 of
    # 1305 shortlist items had drifted this way.
    category_names = storage.load_sde_category_names()
    items = [ShortlistItem(item=r.item, item_id=r.type_id,
                            category=candidate_discovery.guess_category(
                                "", r.item, r.volume_m3, storage.get_type_category(r.type_id), category_names),
                            volume_m3=r.volume_m3, active=True,
                            meta_level=_int_or_none(r.meta_level)) for r in df.itertuples()]
    storage.upsert_shortlist(items)
    return {"added": len(items)}


def _backfill_meta_levels(items: list[ShortlistItem], client: ESIClient) -> dict:
    """Fills in meta_level for shortlist items that don't have one cached yet
    (e.g. added manually, or added before this field existed) and persists it
    so future runs don't re-fetch it. Best-effort - a lookup failure for one
    item shouldn't block the whole refresh.
    """
    missing = [i for i in items if i.item_id and i.meta_level is None]
    if not missing:
        return {"checked": 0, "fetched": 0, "no_attribute": 0, "failed": 0}
    fetched: dict[int, int] = {}
    failed = 0
    for item in missing:
        try:
            level = client.get_meta_level(item.item_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not fetch meta level for %s (%s): %s", item.item, item.item_id, e)
            failed += 1
            continue
        if level is not None:
            fetched[item.item_id] = level
            item.meta_level = level
    if fetched:
        storage.update_shortlist_meta_levels(fetched)
    return {
        "checked": len(missing),
        "fetched": len(fetched),
        "no_attribute": len(missing) - len(fetched) - failed,
        "failed": failed,
    }


def _refresh_shortlist_rows(cfg: TradingConfig = TRADING_CONFIG,
                             oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> tuple[list[ShortlistItem], list, dict]:
    """Shared core of do_refresh_shortlist: re-fetches live market data for
    every active shortlist item and recomputes each one's decision (does NOT
    save the snapshot itself - callers do that once they're done using the
    rows, see do_refresh_shortlist / do_refresh_and_prune_candidates). Returns
    (items, rows, extra) where `extra` holds the ESI-derived counts both
    callers surface in their result dict - factored out so
    do_refresh_and_prune_candidates can reuse the freshly computed `rows`
    (in particular each item's decision) without a second recompute."""
    items = storage.load_shortlist()
    if not items:
        raise ActionError("Shortlist is empty.")
    tm = TokenManager(oauth_cfg)
    if not tm.has_token("seller"):
        raise ActionError("Seller character isn't logged in yet (Login → Seller).")
    client = ESIClient(cfg, tm)

    meta_backfill = _backfill_meta_levels(items, client)

    seller_character_id = tm.get_token("seller").character_id
    own_remaining = own_orders.fetch_own_sell_orders(seller_character_id, "seller", client, cfg)

    buyer_already_covered_ids: frozenset[int] = frozenset()
    if tm.has_token("buyer"):
        buyer_character_id = tm.get_token("buyer").character_id
        try:
            buyer_already_covered_ids = frozenset(
                own_orders.fetch_buyer_already_covered(buyer_character_id, "buyer", client, cfg)
            )
        except ESIError as e:
            # Separate scope (esi-assets.read_assets.v1) - a buyer added before
            # this scope existed won't have it until re-added; degrade
            # gracefully rather than block the whole refresh.
            log.warning("Could not fetch buyer's Jita buy-orders/assets (%s) - re-add buyer character?", e)

    active_item_ids = [i.item_id for i in items if i.active and i.item_id]
    # Fetch every item's market stats up front instead of one-by-one inside
    # evaluate_shortlist: the structure endpoint has no type_id filter, so a
    # per-item call there re-downloaded the *entire* order book every time
    # (the main cause of a slow refresh) - one full download now covers every
    # item. Jita's per-type_id calls are parallelized since ESI has no
    # multi-type_id batch endpoint for regional orders.
    try:
        structure_stats_by_item = client.structure_order_stats_bulk(
            cfg.structure_id, active_item_ids, auth_role="seller")
    except ESIError as e:
        # esi-markets.structure_markets.v1 additionally requires the seller
        # character to actually have current docking access to the structure
        # - confirmed real bug: this used to be unhandled here, becoming a raw
        # 500 from clicking "Refresh Shortlist" directly (do_pipeline already
        # handled this gracefully one level up, but the direct action didn't -
        # inconsistent). A 403 here is a fixable, actionable problem (re-dock/
        # re-invite the seller character), so raise a clear ActionError
        # instead of either crashing raw or silently degrading every row to
        # "no market data."
        raise ActionError(f"Could not fetch the structure's order book ({e}). "
                           f"Does the seller character still have docking access?") from e
    jita_stats_by_item = client.region_order_stats_bulk(cfg.jita_region_id, active_item_ids)

    rows = evaluate_shortlist(items, own_remaining, jita_stats_by_item, structure_stats_by_item, cfg=cfg,
                               buyer_already_covered_ids=buyer_already_covered_ids)
    extra = {
        "own_sell_orders_found": sum(1 for v in own_remaining.values() if v > 0),
        "buyer_already_covered_found": len(buyer_already_covered_ids),
        "meta_level_backfill": meta_backfill,
    }
    return items, rows, extra


def do_refresh_shortlist(cfg: TradingConfig = TRADING_CONFIG,
                          oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    items, rows, extra = _refresh_shortlist_rows(cfg, oauth_cfg)
    run_ts = now_ts()
    storage.save_shortlist_snapshot(rows, run_ts)
    storage.set_esi_sync_time("trading", run_ts)
    return {
        **extra,
        "summary": summary_counts(rows),
        "top_imports": top_imports_by_daily_profit(rows),
        "audit": audit_shortlist(items),
    }


def do_shortlist_trends(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Momentum signal for every shortlist item with a real item_id - see
    history_backtest.compute_margin_trends. Pure local computation over
    already-persisted Goonmetrics history, no ESI/network calls, so unlike
    the unlisted-stock/undercut checks this doesn't need a login and is cheap
    enough to call on every Shortlist page load."""
    items = storage.load_shortlist()
    volumes = {i.item_id: i.volume_m3 for i in items if i.item_id and i.volume_m3}
    history_df = storage.read_table("goonmetrics_history")
    return history_backtest.compute_margin_trends(history_df, volumes, cfg)


def do_check_seller_unlisted_stock(cfg: TradingConfig = TRADING_CONFIG,
                                    oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Live-fetches the seller's structure assets/sell orders and returns
    every shortlist item (active *or* inactive/deactivated) sitting there
    that has NO open sell order at all right now - see
    own_orders.fetch_seller_stock_without_order (only a complete absence of a
    sell order counts, and only shortlist items are considered - confirmed
    scope). Deliberately not filtered to active-only: an item can be
    deactivated (Skip-grace-period prune, or the optional 300-item cap) while
    you still physically hold stock of it - that stock is still worth
    selling, "not actively tracked for re-pricing anymore" isn't the same
    thing as "don't bother selling what you already have" (confirmed with
    the user). Not cached/persisted anywhere (unlike the shortlist snapshot)
    - always a fresh live read, since this is a lightweight one-shot check,
    not a recurring pipeline step."""
    tm = TokenManager(oauth_cfg)
    if not tm.has_token("seller"):
        raise ActionError("Seller character isn't logged in yet (Login → Seller).")
    client = ESIClient(cfg, tm)
    seller_character_id = tm.get_token("seller").character_id
    shortlist_item_ids = {i.item_id for i in storage.load_shortlist() if i.item_id}

    try:
        unlisted = own_orders.fetch_seller_stock_without_order(
            seller_character_id, "seller", client, shortlist_item_ids, cfg)
    except ESIError as e:
        # Most likely cause: the seller was authorized before
        # esi-assets.read_assets.v1 was added to the shared scope list -
        # surface a fixable message instead of a raw 401/500.
        raise ActionError(
            f"ESI access failed ({e}). If the seller was logged in before "
            "esi-assets.read_assets.v1 existed: log in via 'Login Seller' again once."
        ) from e

    rows = []
    for entry in unlisted:
        sde_type = storage.get_sde_type(entry["type_id"])
        name = sde_type[2] if sde_type else str(entry["type_id"])
        rows.append(UnlistedStockRow(
            type_id=entry["type_id"], item=name, asset_quantity=entry["asset_quantity"],
            sell_order_remaining=entry["sell_order_remaining"], unlisted_quantity=entry["unlisted_quantity"],
        ))
    rows.sort(key=lambda r: r.unlisted_quantity, reverse=True)
    return {"rows": rows}


def do_check_undercut(cfg: TradingConfig = TRADING_CONFIG, oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Live-fetches the seller's own sell orders and the structure's full
    order book, and returns every one of the seller's orders that's currently
    beaten by a cheaper competing order - see own_orders.check_undercut. No
    ESI "you got undercut" notification exists, so this is only ever a
    fresh live check, same one-shot (not cached/persisted) shape as
    do_check_seller_unlisted_stock."""
    tm = TokenManager(oauth_cfg)
    if not tm.has_token("seller"):
        raise ActionError("Seller character isn't logged in yet (Login → Seller).")
    client = ESIClient(cfg, tm)
    seller_character_id = tm.get_token("seller").character_id

    try:
        undercut = own_orders.check_undercut(seller_character_id, "seller", client, cfg)
    except ESIError as e:
        raise ActionError(f"ESI access failed ({e}).") from e

    rows = []
    for entry in undercut:
        sde_type = storage.get_sde_type(entry["type_id"])
        name = sde_type[2] if sde_type else str(entry["type_id"])
        rows.append(UndercutRow(
            type_id=entry["type_id"], item=name, my_price=entry["my_price"],
            competitor_price=entry["competitor_price"], difference=entry["difference"],
        ))
    return {"rows": rows}


SKIP_DECISION = "No market data / Skip"


def _items_past_skip_grace_period(rows: list, skip_since: dict[int, str], grace_period_days: int,
                                   now: dt.datetime) -> list[tuple[int, str]]:
    """Pure decision logic for do_refresh_and_prune_candidates, split out so
    it's testable without ESI/DB access. `skip_since` is the current
    {item_id: ISO timestamp} skip-streak-start map (storage.
    get_shortlist_skip_since, read *before* this run's streak updates are
    applied) - an item is due for deactivation only if it's Skip *this* run
    AND its streak (possibly starting before this run) has run for at least
    `grace_period_days`. Returns [(item_id, item_name), ...]."""
    grace_period = dt.timedelta(days=grace_period_days)
    due = []
    for r in rows:
        if r.decision != SKIP_DECISION or not r.item_id:
            continue
        since_str = skip_since.get(r.item_id)
        if since_str is None:
            continue  # streak just started this run (see do_refresh_and_prune_candidates) - not due yet
        since = dt.datetime.fromisoformat(since_str)
        if now - since >= grace_period:
            due.append((r.item_id, r.item))
    return due


def _items_beyond_rank(rows: list, max_active_items: int) -> list[tuple[int, str]]:
    """Pure ranking logic for the optional hard shortlist-size cap
    (TradingConfig.enforce_shortlist_cap/max_active_shortlist_items) - ranks
    `rows` (expected: already filtered down to the still-active-this-run set)
    by max daily profit (profit_per_unit x sell_volume, descending - margin x
    liquidity, confirmed with the user over margin alone, since a high-margin
    item with near-zero sell volume isn't worth much operationally) and
    returns every item beyond the `max_active_items`'th rank. Items with no
    computable profit/sell_volume (None) sort last, same as if their daily
    profit were 0 - they're not being unfairly pushed out ahead of items that
    do have data, just never prioritized over them either."""
    def _daily_profit(r):
        if r.profit_per_unit is None or r.sell_volume is None:
            return float("-inf")
        return r.profit_per_unit * r.sell_volume

    ranked = sorted(rows, key=_daily_profit, reverse=True)
    return [(r.item_id, r.item) for r in ranked[max_active_items:] if r.item_id]


def do_refresh_and_prune_candidates(safe: bool = True, cfg: TradingConfig = TRADING_CONFIG,
                                     oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """One-button candidate maintenance, combining three of the manual steps
    into one: ③ find new import candidates (against the already-built
    focused_candidates universe - run ①②  yourself first, they're an
    occasional step, not a daily one, same as do_pipeline's rebuild_universe),
    ④ add the recommended ones to the shortlist, then re-evaluate every
    shortlist item's current profitability and deactivate (active=False, not
    deleted - shortlist_snapshot history is kept) two kinds of items:

    1. Whichever have been continuously "No market data / Skip" for at least
       cfg.skip_grace_period_days (default 30) - tracked per item_id in
       storage.shortlist_skip_streak, reset the moment the item is profitable
       again even once. Only targets that one decision - "Inactive" items are
       already deactivated, and "Missing ID" means a data problem to fix
       manually, not a profitability judgment to act on automatically. The
       grace period exists so a single temporary market-data gap (not a real,
       sustained loss of profitability) can't knock an otherwise-fine item
       off the active shortlist - the streak only leads to deactivation once
       it's been unbroken for the full grace period.
    2. If cfg.enforce_shortlist_cap is on (checkbox on the Shortlist page,
       off by default): whichever *remaining* active items (after #1) rank
       beyond cfg.max_active_shortlist_items by max daily profit - see
       _items_beyond_rank. Applied on top of, not instead of, #1.
    """
    find_result = do_find_new_candidates(safe=safe, cfg=cfg)
    add_result = do_add_to_shortlist()

    items, rows, extra = _refresh_shortlist_rows(cfg, oauth_cfg)
    run_ts = now_ts()

    skip_since = storage.get_shortlist_skip_since()
    skip_deactivate = _items_past_skip_grace_period(rows, skip_since, cfg.skip_grace_period_days,
                                                      dt.datetime.fromisoformat(run_ts))

    skip_item_ids = [r.item_id for r in rows if r.decision == SKIP_DECISION and r.item_id]
    recovered_item_ids = [r.item_id for r in rows if r.decision != SKIP_DECISION and r.item_id]
    storage.clear_shortlist_skip_streak(recovered_item_ids)
    storage.start_shortlist_skip_streak(skip_item_ids, run_ts)  # no-op for ids already mid-streak

    cap_deactivate: list[tuple[int, str]] = []
    if cfg.enforce_shortlist_cap:
        skip_deactivated_ids = {item_id for item_id, _ in skip_deactivate}
        still_active = [r for r in rows if r.active and r.item_id and r.item_id not in skip_deactivated_ids]
        cap_deactivate = _items_beyond_rank(still_active, cfg.max_active_shortlist_items)

    to_deactivate = skip_deactivate + cap_deactivate
    if to_deactivate:
        deactivated_ids = [item_id for item_id, _ in to_deactivate]
        storage.deactivate_shortlist_items(deactivated_ids)
        storage.clear_shortlist_skip_streak(deactivated_ids)
        # Reflect this run's deactivations in `rows` *before* they're saved as
        # the snapshot the Shortlist page reads - otherwise the snapshot would
        # keep showing these items as active/Skip for one more refresh cycle
        # even though storage.shortlist already has them deactivated (a real,
        # confirmed bug: shortlist_snapshot was previously saved before this
        # deactivation step ran at all, so it never reflected same-run prunes).
        deactivated_id_set = set(deactivated_ids)
        for r in rows:
            if r.item_id in deactivated_id_set:
                r.active = False
                r.decision = "Inactive"

    storage.save_shortlist_snapshot(rows, run_ts)
    storage.set_esi_sync_time("trading", run_ts)

    return {
        "new_candidates_evaluated": find_result["evaluated"],
        "new_candidates_added": add_result["added"],
        **extra,
        "summary": summary_counts(rows),
        "top_imports": top_imports_by_daily_profit(rows),
        "audit": audit_shortlist(items),
        "deactivated_count": len(to_deactivate),
        "deactivated_items": [item for _, item in to_deactivate],
        "cap_deactivated_count": len(cap_deactivate),
    }


def shortlist_skip_deactivation_days(cfg: TradingConfig = TRADING_CONFIG) -> dict[int, int]:
    """{item_id: days_remaining} for every item currently on an unbroken
    "No market data / Skip" streak (storage.get_shortlist_skip_since - see
    do_refresh_and_prune_candidates) - an item not present in the returned
    dict isn't currently on a streak. Used by api/routers/trading.py's
    /shortlist/snapshot to surface a "days until auto-deactivation" column."""
    skip_since = storage.get_shortlist_skip_since()
    now = dt.datetime.utcnow()
    return {
        item_id: max(0, math.ceil(cfg.skip_grace_period_days
                                   - (now - dt.datetime.fromisoformat(since_str)).total_seconds() / 86400))
        for item_id, since_str in skip_since.items()
    }


def do_reconcile_trades(cfg: TradingConfig = TRADING_CONFIG,
                         oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    tm = TokenManager(oauth_cfg)
    if not (tm.has_token("buyer") and tm.has_token("seller")):
        raise ActionError("Buyer and seller both need to be logged in.")
    client = ESIClient(cfg, tm)
    buyer_id = tm.get_token("buyer").character_id
    seller_id = tm.get_token("seller").character_id

    items = storage.load_shortlist()
    item_names = {i.item_id: i.item for i in items}
    item_volumes = {i.item_id: i.volume_m3 for i in items}

    trades = reconcile_realized_trades(buyer_id, seller_id, "buyer", "seller", client,
                                        item_names, item_volumes, cfg)
    storage.save_realized_trades(trades, now_ts())
    summary = summarize_realized(trades)
    return {"matched_trades": len(trades), **summary}


def do_pipeline(safe: bool = True, rebuild_universe: bool = False) -> dict:
    """Runs the daily workflow. `rebuild_universe` re-crawls the *entire* ESI
    market-group tree (hundreds/thousands of requests) and is off by default -
    that's an occasional setup step, not something to redo every day. Each
    step is isolated so a failure (missing auth, network hiccup) in one step
    doesn't prevent the others from running.

    Uses do_refresh_and_prune_candidates (not the separate find-candidates +
    refresh-shortlist calls this used to make) so a "complete" pipeline run
    actually finishes the job: newly found candidates get added to the
    shortlist and stale/over-cap items get pruned in the same run, instead of
    silently requiring a separate manual step afterwards (confirmed bug: this
    used to call do_refresh_shortlist + do_find_new_candidates only, so
    "Run Complete Pipeline" never added or pruned anything).
    """
    results = {}

    if rebuild_universe:
        try:
            results["build_universe"] = do_build_universe()
            results["build_focused"] = do_build_focused()
        except (ActionError, ESIError) as e:
            results["build_universe"] = {"error": str(e)}

    try:
        results["refresh_and_prune_candidates"] = do_refresh_and_prune_candidates(safe=safe)
    except (ActionError, ESIError) as e:
        results["refresh_and_prune_candidates"] = {"error": str(e)}

    try:
        results["reconcile_trades"] = do_reconcile_trades()
    except ActionError as e:
        results["reconcile_trades"] = {"error": str(e)}

    return results


def do_create_backup() -> dict:
    """Backs up data/eve_trader.db, config.yaml, and data/tokens.json into a
    single timestamped .zip (see backup.py) - this app's only persistence
    (no git repo) so this is the only way to recover from a lost/corrupted
    disk short of redoing every ESI sync and Settings change by hand."""
    try:
        return backup.create_backup()
    except (OSError, sqlite3.Error) as e:
        # sqlite3.Error (e.g. OperationalError on a locked/corrupt DB during
        # the online backup) is not an OSError subclass - confirmed real gap:
        # this used to only catch OSError, so a locked-DB failure escaped as
        # a raw 500 instead of the ActionError every other user-facing
        # failure in this app converts to.
        raise ActionError(f"Backup failed: {e}") from e


def do_list_backups() -> dict:
    return {"rows": backup.list_backups()}
