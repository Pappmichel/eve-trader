import datetime as dt

from eve_trader import actions, storage
from eve_trader.actions import (NO_MARKET_DATA_DECISION, SKIP_DECISION, _items_beyond_rank,
                                 _items_past_skip_grace_period, _items_to_reactivate)
from eve_trader.config import TradingConfig
from eve_trader.models import ShortlistRow

NOW = dt.datetime(2026, 7, 15, 12, 0, 0)


def _row(item_id: int, item: str, decision: str, profit_per_unit=None, sell_volume=None, active=True,
         margin=None, avg_daily_volume=None) -> ShortlistRow:
    return ShortlistRow(
        item=item, category="Material", landed_cost=None, net_sell=None, sell_volume=sell_volume,
        own_orders_remaining=0.0, profit_per_unit=profit_per_unit, margin=margin, profit_per_m3=None,
        decision=decision, active=active, item_id=item_id, volume_m3=1.0, jita_sell=None, import_cost=None,
        avg_daily_volume=avg_daily_volume,
    )


def test_not_due_when_streak_just_started():
    # No entry in skip_since yet - this is the first Skip evaluation, not due
    # regardless of grace_period_days.
    rows = [_row(1, "Widget", SKIP_DECISION)]
    assert _items_past_skip_grace_period(rows, {}, grace_period_days=30, now=NOW) == []


def test_not_due_within_grace_period():
    since = (NOW - dt.timedelta(days=29)).isoformat()
    rows = [_row(1, "Widget", SKIP_DECISION)]
    assert _items_past_skip_grace_period(rows, {1: since}, grace_period_days=30, now=NOW) == []


def test_due_after_grace_period():
    since = (NOW - dt.timedelta(days=31)).isoformat()
    rows = [_row(1, "Widget", SKIP_DECISION)]
    assert _items_past_skip_grace_period(rows, {1: since}, grace_period_days=30, now=NOW) == [(1, "Widget")]


def test_due_exactly_at_grace_period_boundary():
    since = (NOW - dt.timedelta(days=30)).isoformat()
    rows = [_row(1, "Widget", SKIP_DECISION)]
    assert _items_past_skip_grace_period(rows, {1: since}, grace_period_days=30, now=NOW) == [(1, "Widget")]


def test_not_due_when_currently_profitable_even_with_old_streak_entry():
    # Stale skip_since entry (e.g. from before it recovered) - decision this
    # run is "Import", so it's not touched regardless of the map.
    since = (NOW - dt.timedelta(days=90)).isoformat()
    rows = [_row(1, "Widget", "Import")]
    assert _items_past_skip_grace_period(rows, {1: since}, grace_period_days=30, now=NOW) == []


def test_multiple_items_mixed():
    rows = [
        _row(1, "Old Skip", SKIP_DECISION),
        _row(2, "New Skip", SKIP_DECISION),
        _row(3, "Profitable", "Already ordered"),
    ]
    skip_since = {
        1: (NOW - dt.timedelta(days=45)).isoformat(),
        2: (NOW - dt.timedelta(days=5)).isoformat(),
    }
    assert _items_past_skip_grace_period(rows, skip_since, grace_period_days=30, now=NOW) == [(1, "Old Skip")]


def test_no_market_data_counts_toward_the_same_streak_as_skip():
    # "No market data" and "Skip" are distinct labels (see shortlist.py's
    # 2026-08-18 split) but must still feed the same deactivation streak -
    # a candidate stuck with zero C-J listings shouldn't linger forever just
    # because its label differs from a priced-but-unprofitable item's.
    since = (NOW - dt.timedelta(days=31)).isoformat()
    rows = [_row(1, "Never Listed", NO_MARKET_DATA_DECISION)]
    assert _items_past_skip_grace_period(rows, {1: since}, grace_period_days=30, now=NOW) == [(1, "Never Listed")]


def test_skip_deactivation_days_counts_down(monkeypatch):
    cfg = TradingConfig(skip_grace_period_days=30)
    since_10_days_ago = (dt.datetime.utcnow() - dt.timedelta(days=10)).isoformat()
    monkeypatch.setattr(storage, "get_shortlist_skip_since", lambda: {1: since_10_days_ago})

    result = actions.shortlist_skip_deactivation_days(cfg)

    assert result[1] == 20


def test_skip_deactivation_days_floors_at_zero_past_grace_period(monkeypatch):
    cfg = TradingConfig(skip_grace_period_days=30)
    since_90_days_ago = (dt.datetime.utcnow() - dt.timedelta(days=90)).isoformat()
    monkeypatch.setattr(storage, "get_shortlist_skip_since", lambda: {1: since_90_days_ago})

    result = actions.shortlist_skip_deactivation_days(cfg)

    assert result[1] == 0


def test_skip_deactivation_days_empty_when_no_streaks(monkeypatch):
    monkeypatch.setattr(storage, "get_shortlist_skip_since", lambda: {})
    assert actions.shortlist_skip_deactivation_days() == {}


def test_items_beyond_rank_keeps_top_n_by_daily_profit():
    rows = [
        _row(1, "Best", "Import", profit_per_unit=100, avg_daily_volume=100),   # 10,000/day
        _row(2, "Middle", "Import", profit_per_unit=50, avg_daily_volume=100),  # 5,000/day
        _row(3, "Worst", "Import", profit_per_unit=1, avg_daily_volume=10),     # 10/day
    ]
    assert _items_beyond_rank(rows, max_active_items=2) == [(3, "Worst")]
    assert _items_beyond_rank(rows, max_active_items=3) == []


def test_items_beyond_rank_sorts_missing_data_last():
    rows = [
        _row(1, "Has Data", "Import", profit_per_unit=10, avg_daily_volume=10),  # 100/day
        _row(2, "No Avg Daily Volume", "Import", profit_per_unit=10, avg_daily_volume=None),
        _row(3, "No Profit", "Import", profit_per_unit=None, avg_daily_volume=10),
    ]
    beyond = _items_beyond_rank(rows, max_active_items=1)
    assert {item_id for item_id, _ in beyond} == {2, 3}


def test_items_beyond_rank_uses_avg_daily_volume_not_sell_volume():
    # Same class of inflation as GitHub issue #51: a parked listing with huge
    # sell_volume (order-book depth) must not outrank a liquid item whose
    # real market-wide daily turnover is higher. Cap ranking follows Profit /
    # Day, which uses avg_daily_volume.
    parked = _row(1, "Parked", "Import", profit_per_unit=10, sell_volume=100_000, avg_daily_volume=1)
    liquid = _row(2, "Liquid", "Import", profit_per_unit=10, sell_volume=5, avg_daily_volume=500)
    assert _items_beyond_rank([parked, liquid], max_active_items=1) == [(1, "Parked")]


# ------------------------------------------------------------- reactivation (GitHub issue #35)
def test_reactivates_inactive_item_that_now_clears_the_import_bar():
    # Confirmed live (2026-08-21): several Booster/Drugs items sat inactive
    # with 100%+ margins and real daily sell volume, with no path back to
    # active - _decision short-circuits to "Inactive" whenever active=False,
    # regardless of the real (still-computed, see issue #6) numbers.
    cfg = TradingConfig(min_profit_threshold=0, min_margin_threshold=0.1)
    rows = [_row(1, "Recovered Booster", "Inactive", profit_per_unit=100, sell_volume=10, margin=1.15, active=False)]
    assert _items_to_reactivate(rows, cfg) == [(1, "Recovered Booster")]


def test_does_not_reactivate_already_active_items():
    cfg = TradingConfig(min_profit_threshold=0, min_margin_threshold=0.1)
    rows = [_row(1, "Already Active", "Import", profit_per_unit=100, sell_volume=10, margin=1.15, active=True)]
    assert _items_to_reactivate(rows, cfg) == []


def test_does_not_reactivate_inactive_item_still_below_margin_threshold():
    cfg = TradingConfig(min_profit_threshold=0, min_margin_threshold=0.5)
    rows = [_row(1, "Still Weak", "Inactive", profit_per_unit=100, sell_volume=10, margin=0.2, active=False)]
    assert _items_to_reactivate(rows, cfg) == []


def test_does_not_reactivate_inactive_item_with_no_sell_volume():
    cfg = TradingConfig(min_profit_threshold=0, min_margin_threshold=0.1)
    rows = [_row(1, "No Volume", "Inactive", profit_per_unit=100, sell_volume=0, margin=1.15, active=False)]
    assert _items_to_reactivate(rows, cfg) == []


def test_does_not_reactivate_inactive_item_with_missing_data():
    cfg = TradingConfig(min_profit_threshold=0, min_margin_threshold=0.1)
    rows = [_row(1, "No Data", "Inactive", profit_per_unit=None, sell_volume=None, margin=None, active=False)]
    assert _items_to_reactivate(rows, cfg) == []


def test_select_shortlist_refresh_batches_orders_never_refreshed_first():
    from eve_trader.actions import select_shortlist_refresh_batches
    from eve_trader.models import ShortlistItem

    items = [
        ShortlistItem(item="Old", item_id=1, category="X", volume_m3=1.0, refreshed_at="2026-01-01T00:00:00"),
        ShortlistItem(item="Never", item_id=2, category="X", volume_m3=1.0, refreshed_at=None),
        ShortlistItem(item="Older", item_id=3, category="X", volume_m3=1.0, refreshed_at="2025-12-01T00:00:00"),
        ShortlistItem(item="Never2", item_id=4, category="X", volume_m3=1.0, refreshed_at=None),
    ]
    batches = select_shortlist_refresh_batches(items, batch_size=2)
    assert len(batches) == 2
    assert [i.item_id for i in batches[0]] == [2, 4]  # NULLs first, then item_id
    assert [i.item_id for i in batches[1]] == [3, 1]  # older timestamp before newer


def test_select_shortlist_refresh_batches_covers_everything_in_ceil_n_over_batch():
    from eve_trader.actions import select_shortlist_refresh_batches
    from eve_trader.models import ShortlistItem

    items = [ShortlistItem(item=f"I{i}", item_id=i, category="X", volume_m3=1.0) for i in range(10)]
    batches = select_shortlist_refresh_batches(items, batch_size=3)
    seen = [i.item_id for batch in batches for i in batch]
    assert sorted(seen) == list(range(10))
    assert len(batches) == 4  # 3+3+3+1


def test_refresh_shortlist_prices_jita_in_oldest_first_batches(monkeypatch):
    """Cleanup must not dump the whole shortlist into one region_order_stats_bulk
    call - that would spawn max_workers concurrent ESI lookups per id-wave
    but still hold every type_id in one future-set. Batches of
    shortlist_refresh_batch_size, never-refreshed first, then oldest
    refreshed_at, so a crash mid-job retries the stalest items first."""
    from eve_trader import actions, storage
    from eve_trader.config import TradingConfig
    from eve_trader.esi_client import ESIClient
    from eve_trader.goonmetrics_client import GoonmetricsClient
    from eve_trader.models import ShortlistItem

    items = [
        ShortlistItem(item="Old", item_id=1, category="X", volume_m3=1.0, meta_level=5,
                      refreshed_at="2026-01-01T00:00:00"),
        ShortlistItem(item="Never", item_id=2, category="X", volume_m3=1.0, meta_level=5, refreshed_at=None),
        ShortlistItem(item="Older", item_id=3, category="X", volume_m3=1.0, meta_level=5,
                      refreshed_at="2025-12-01T00:00:00"),
        ShortlistItem(item="Never2", item_id=4, category="X", volume_m3=1.0, meta_level=5, refreshed_at=None),
        ShortlistItem(item="Mid", item_id=5, category="X", volume_m3=1.0, meta_level=5,
                      refreshed_at="2025-12-15T00:00:00"),
    ]
    bulk_calls: list[list[int]] = []
    marked: list[list[int]] = []

    def fake_bulk(self, region_id, type_ids, max_workers=10):
        bulk_calls.append(list(type_ids))
        return {}

    monkeypatch.setattr(storage, "load_shortlist", lambda: items)
    monkeypatch.setattr(actions, "_list_role_characters", lambda tm, prefix: [])
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", fake_bulk)
    monkeypatch.setattr(
        ESIClient, "structure_order_stats_bulk_or_goonmetrics",
        lambda self, structure_id, type_ids, auth_role, goonmetrics_market_slug: ({}, False),
    )
    monkeypatch.setattr(GoonmetricsClient, "price_history_chunked", lambda self, *a, **k: [])
    monkeypatch.setattr(storage, "save_shortlist_snapshot", lambda rows, run_ts: None)
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda tool, run_ts: None)
    monkeypatch.setattr(storage, "mark_shortlist_refreshed",
                        lambda item_ids, ts: marked.append(list(item_ids)))

    result = actions.do_refresh_shortlist(
        TradingConfig(structure_id=1000, shortlist_refresh_batch_size=2),
    )

    assert bulk_calls == [[2, 4], [3, 5], [1]]
    assert marked == [[2, 4], [3, 5], [1]]
    assert result["cleanup_batches_total"] == 3
    assert result["cleanup_batches_done"] == 3
    assert result["cleanup_items_refreshed"] == 5
    assert result["cleanup_batch_size"] == 2
