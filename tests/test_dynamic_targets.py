"""Dynamic stock targets (docs/DYNAMIC_STOCK_TARGETS_PLAN.md): pure resolver,
signals with fakes, the read side with the switch off, and config validation.
No database - the storage/planner/action tests live in
test_dynamic_targets_integration.py."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from eve_trader import config as base_config
from eve_trader import storage
from eve_trader.production import dynamic_targets as dt
from eve_trader.production.config import ProductionConfig, validate_production_merged


def _cfg(**overrides) -> ProductionConfig:
    defaults = dict(min_margin=0.10)
    defaults.update(overrides)
    return ProductionConfig(**defaults)


def _row(**kw) -> dt.RowInput:
    base = dict(configured_home=20.0, row_min=0.0, row_max=60.0, velocity_own=2.0, velocity_market=None,
                has_own_sale=True, margin=0.30)
    base.update(kw)
    return dt.RowInput(**base)


# ------------------------------------------------------------------ combine
@pytest.mark.parametrize("own, market, expected", [
    (None, None, (None, None)),
    (2.0, None, (2.0, "own")),
    (None, 3.0, (3.0, "market")),
    (2.0, 3.0, (3.0, "market")),
    (4.0, 3.0, (4.0, "own")),
    (3.0, 3.0, (3.0, "own")),
    (0.0, 1.5, (1.5, "market")),
])
def test_combine_velocity(own, market, expected):
    assert dt.combine_velocity(own, market) == expected


# ------------------------------------------------------------------- factor
def test_profit_factor_scale_and_clamps():
    cfg = _cfg()  # min 0.10, reference 0.30, factor 0.25..2.0
    assert dt.profit_factor(0.30, cfg) == pytest.approx(1.0)
    assert dt.profit_factor(0.20, cfg) == pytest.approx(0.5)
    assert dt.profit_factor(0.12, cfg) == 0.25          # clamped to factor_min
    assert dt.profit_factor(0.90, cfg) == 2.0           # clamped to factor_max
    assert dt.profit_factor(0.50, cfg) == pytest.approx(2.0)


def test_profit_factor_is_one_when_reference_no_longer_exceeds_min_margin():
    cfg = _cfg(min_margin=0.40, dynamic_target_reference_margin=0.30)  # E-16
    assert dt.profit_factor(0.9, cfg) == 1.0


# ----------------------------------------------------------------- resolver
def test_hold_without_velocity_keeps_configured_value_on_first_day():
    result = dt.resolve_row(_row(velocity_own=None, velocity_market=None), _cfg())
    assert result.reason == dt.REASON_HELD
    assert result.effective_qty == 20.0 and result.smoothed_qty == 20.0
    assert result.raw_qty is None and result.velocity_used is None


def test_hold_without_margin_keeps_previous_value():
    result = dt.resolve_row(_row(margin=None, prev_effective=33.0, prev_smoothed=31.5), _cfg())
    assert result.reason == dt.REASON_HELD
    assert (result.effective_qty, result.smoothed_qty) == (33.0, 31.5)


def test_below_min_margin_gives_zero_and_keeps_the_smoothed_level():
    result = dt.resolve_row(_row(margin=0.05, prev_effective=30.0, prev_smoothed=28.0), _cfg())
    assert result.reason == dt.REASON_BELOW_MIN_MARGIN
    assert result.effective_qty == 0.0
    assert result.smoothed_qty == 28.0          # E-06: recovery resumes at the old level


def test_normal_path_smooths_from_the_configured_value_on_the_first_day():
    # raw = 2/day x 14 days x factor 1.0 = 28; smoothed = 20 + 0.3 x 8 = 22.4 -> 22
    result = dt.resolve_row(_row(), _cfg())
    assert result.raw_qty == pytest.approx(28.0)
    assert result.smoothed_qty == pytest.approx(22.4)
    assert result.effective_qty == 22.0
    assert result.reason == dt.REASON_OK
    assert result.velocity_source == "own" and result.factor == pytest.approx(1.0)


def test_dead_band_keeps_the_previous_value_but_stores_the_smoothed_one():
    # raw 21, smoothed 20.3 -> 20 = no move
    result = dt.resolve_row(_row(velocity_own=1.5), _cfg())
    assert result.reason == dt.REASON_WITHIN_DEADBAND
    assert result.effective_qty == 20.0
    assert result.smoothed_qty == pytest.approx(20.3)


def test_dead_band_accumulates_small_moves_until_they_cross_it():
    cfg = _cfg()
    prev_eff, prev_sm = 100.0, 100.0
    effective = []
    for _ in range(6):
        r = dt.resolve_row(_row(configured_home=100.0, row_max=300.0, velocity_own=9.0,  # raw 126
                                prev_effective=prev_eff, prev_smoothed=prev_sm), cfg)
        effective.append(r.effective_qty)
        prev_eff, prev_sm = r.effective_qty, r.smoothed_qty
    assert effective[0] == 100.0                    # 107.8 -> 108, |8| < 10
    assert max(effective) > 100.0                   # eventually crosses the band (E-08)
    assert effective == sorted(effective)           # monotone


def test_clamp_to_row_max_and_min():
    high = dt.resolve_row(_row(velocity_own=500.0, row_max=40.0), _cfg())
    assert (high.effective_qty, high.reason) == (40.0, dt.REASON_CLAMPED_MAX)
    low = dt.resolve_row(_row(velocity_own=0.5, row_min=15.0, configured_home=1.0, prev_effective=1.0,
                              prev_smoothed=1.0), _cfg())
    assert (low.effective_qty, low.reason) == (15.0, dt.REASON_CLAMPED_MIN)


def test_d12_a_selling_row_never_rounds_below_one_unit():
    cfg = _cfg()
    tiny = dict(configured_home=0.0, row_max=10.0, velocity_own=0.01, prev_effective=0.0, prev_smoothed=0.0)
    selling = dt.resolve_row(_row(has_own_sale=True, **tiny), cfg)
    assert (selling.effective_qty, selling.reason) == (1.0, dt.REASON_MIN_ONE)
    idle = dt.resolve_row(_row(has_own_sale=False, **tiny), cfg)
    assert idle.effective_qty == 0.0               # no own sale: may stay at 0


def test_d12_does_not_override_the_below_min_margin_zero():
    r = dt.resolve_row(_row(has_own_sale=True, margin=0.01), _cfg())
    assert r.effective_qty == 0.0 and r.reason == dt.REASON_BELOW_MIN_MARGIN


def test_d12_respects_a_larger_row_minimum():
    r = dt.resolve_row(_row(velocity_own=0.01, row_min=5.0, configured_home=0.0, prev_effective=0.0,
                            prev_smoothed=0.0), _cfg())
    assert r.effective_qty == 5.0 and r.reason == dt.REASON_CLAMPED_MIN


def test_market_velocity_alone_is_enough_when_own_is_unavailable():
    r = dt.resolve_row(_row(velocity_own=None, velocity_market=1.0, has_own_sale=False), _cfg())
    assert r.velocity_source == "market"
    assert r.raw_qty == pytest.approx(14.0)


def test_converged_view_ignores_smoothing_and_dead_band():
    cfg = _cfg()
    day1 = dt.resolve_row(_row(velocity_own=1.5), cfg)
    converged = dt.resolve_row(_row(velocity_own=1.5), cfg, converged=True)
    assert day1.effective_qty == 20.0
    assert converged.effective_qty == 21.0
    assert converged.smoothed_qty == pytest.approx(converged.raw_qty)


def test_previous_value_outside_changed_bounds_is_pulled_back():
    # The user lowered the max to 50 while the frozen value was 100 and the new
    # candidate is within the dead band of it.
    r = dt.resolve_row(_row(configured_home=100.0, row_max=50.0, velocity_own=7.5, prev_effective=100.0,
                            prev_smoothed=100.0), _cfg())
    assert r.effective_qty == 50.0


def test_recovery_from_below_min_margin_skips_the_dead_band():
    r = dt.resolve_row(_row(configured_home=100.0, row_max=300.0, velocity_own=7.5,  # raw 105
                            prev_effective=0.0, prev_smoothed=100.0, prev_reason=dt.REASON_BELOW_MIN_MARGIN),
                       _cfg())
    # smoothed 100 + 0.3 x 5 = 101.5 -> 102; the 0 stored while below min margin is not a real level
    assert r.reason == dt.REASON_OK and r.effective_qty == 102.0


# ------------------------------------------------------------------- bounds
def test_default_bounds_follow_d6_and_d13():
    assert dt.default_max(20) == 60.0
    assert dt.default_max(2) == 10.0            # D13 floor
    assert dt.default_max(0) == 10.0
    assert dt.default_bounds(20, None, None) == (0.0, 60.0)
    assert dt.default_bounds(20, 5, 30) == (5.0, 30.0)
    assert dt.default_bounds(20, 50, 30) == (50.0, 50.0)   # max never below min


# ---------------------------------------------------------- market velocity
class _FakeClient:
    def __init__(self, points=None, error=None):
        self.points, self.error, self.calls = points or [], error, []

    def price_history_chunked(self, region_id, type_ids):
        self.calls.append((region_id, list(type_ids)))
        if self.error:
            raise self.error
        return self.points


def _pt(type_id, day, movement):
    return SimpleNamespace(type_id=type_id, date=day.isoformat(), movement=movement)


TODAY = date(2026, 10, 24)


def test_market_velocity_divides_by_calendar_days_and_counts_missing_days_as_zero():
    # 10 traded days of 100 units over a 28-day window = 1000 / 28 x 0.10 (P-13, V-02)
    points = [_pt(5, TODAY - timedelta(days=d), 100.0) for d in range(1, 11)]
    out = dt.compute_market_velocity(_cfg(), [5], today=TODAY, client=_FakeClient(points), region_id=10000009)
    assert out == {5: pytest.approx(1000 / 28 * 0.10)}


def test_market_velocity_ignores_today_and_points_before_the_window():
    points = [
        _pt(5, TODAY, 9999.0),                         # today is incomplete
        _pt(5, TODAY - timedelta(days=1), 280.0),
        _pt(5, TODAY - timedelta(days=28), 280.0),     # first day of the window
        _pt(5, TODAY - timedelta(days=29), 9999.0),    # before the window
    ]
    out = dt.compute_market_velocity(_cfg(), [5], today=TODAY, client=_FakeClient(points), region_id=1)
    assert out[5] == pytest.approx(560 / 28 * 0.10)


def test_market_velocity_window_is_capped_at_28_days():
    points = [_pt(5, TODAY - timedelta(days=d), 10.0) for d in range(1, 60)]
    out = dt.compute_market_velocity(_cfg(dynamic_target_window_days=60), [5], today=TODAY,
                                     client=_FakeClient(points), region_id=1)
    assert out[5] == pytest.approx(28 * 10.0 / 28 * 0.10)


def test_market_velocity_leaves_out_types_without_history_and_survives_an_outage():
    client = _FakeClient([_pt(5, TODAY - timedelta(days=2), 50.0)])
    out = dt.compute_market_velocity(_cfg(), [5, 6], today=TODAY, client=client, region_id=1)
    assert set(out) == {5}
    assert client.calls == [(1, [5, 6])]
    assert dt.compute_market_velocity(_cfg(), [5], today=TODAY, client=_FakeClient(error=RuntimeError("down")),
                                      region_id=1) == {}
    assert dt.compute_market_velocity(_cfg(), [], today=TODAY, client=client, region_id=1) == {}


# ---------------------------------------------------------------- own sales
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _patch_ledger(monkeypatch, *, t0, rows, ran_at=None, sharing_changed=False, run="run"):
    from eve_trader.production.realized import report
    from eve_trader.realized import db as realized_db

    ran_at = ran_at or NOW
    run_row = None if run is None else (1, ran_at, t0, None, b"fp")
    monkeypatch.setattr(report, "run_state", lambda: (run_row, t0, sharing_changed))
    monkeypatch.setattr(report, "production_scope",
                        lambda: {"channel": "production", "wallet_owners": [], "contract_owners": []})
    calls = []

    def fake_query(start, end, location_id, **scope):
        calls.append((start, end, location_id, scope))
        return rows
    monkeypatch.setattr(realized_db, "market_units_sold_daily", fake_query)
    return calls


def test_own_sales_caps_one_bulk_day_and_reports_its_share(monkeypatch):
    t0 = NOW - timedelta(days=20)
    day = date(2026, 10, 20)
    rows = [(5, day, 100.0, 50.0, 100.0)] + [(5, day - timedelta(days=i), 1.0, 0.5, 1.0) for i in range(1, 10)]
    calls = _patch_ledger(monkeypatch, t0=t0, rows=rows)
    own = dt.compute_own_sales(_cfg(home_location_id=123, dynamic_target_day_cap_multiple=3.0), now=NOW)

    assert own.available and own.covered_days == pytest.approx(20.0)
    row = own.per_type[5]
    mean_day = 109 / 20.0
    assert row.velocity == pytest.approx((3 * mean_day + 9) / 20.0)     # bulk day capped at 3x the mean day (V-04)
    assert row.units == 109 and row.max_day_share == pytest.approx(100 / 109)
    assert row.realized_margin == pytest.approx(54.5 / 109.0)
    start, end, location, scope = calls[0]
    assert location == 123 and scope["channel"] == "production" and start == t0 and end == NOW


def test_own_sales_cap_off_uses_every_unit(monkeypatch):
    t0 = NOW - timedelta(days=20)
    rows = [(5, date(2026, 10, 20), 100.0, None, None), (5, date(2026, 10, 19), 1.0, None, None)]
    _patch_ledger(monkeypatch, t0=t0, rows=rows)
    own = dt.compute_own_sales(_cfg(home_location_id=1, dynamic_target_day_cap_multiple=0), now=NOW)
    assert own.per_type[5].velocity == pytest.approx(101 / 20.0)
    assert own.per_type[5].realized_margin is None      # no basis -> None, never 0


def test_own_sales_window_ends_at_the_last_rebuild_not_now(monkeypatch):
    t0 = NOW - timedelta(days=60)
    ran_at = NOW - timedelta(days=3)
    calls = _patch_ledger(monkeypatch, t0=t0, rows=[], ran_at=ran_at)
    own = dt.compute_own_sales(_cfg(home_location_id=1), now=NOW)
    start, end, *_ = calls[0]
    assert end == ran_at and start == ran_at - timedelta(days=28)     # E-03 and the 28-day window
    assert own.available and own.covered_days == pytest.approx(28.0)


def test_own_sales_unavailable_below_the_minimum_history(monkeypatch):
    t0 = NOW - timedelta(days=10)
    rows = [(5, date(2026, 10, 20), 10.0, 1.0, 5.0)]
    _patch_ledger(monkeypatch, t0=t0, rows=rows)
    own = dt.compute_own_sales(_cfg(home_location_id=1), now=NOW)
    assert not own.available and own.reason == "history_too_short"
    assert 5 in own.per_type            # still known: D12 only needs "has a sale in the window"


@pytest.mark.parametrize("kwargs, home, reason", [
    (dict(run=None, t0=None), 1, "no_ledger_run"),
    (dict(sharing_changed=True, t0=NOW - timedelta(days=30)), 1, "sharing_changed"),
    (dict(t0=None), 1, "no_cutover"),
    (dict(t0=NOW - timedelta(days=30)), None, "no_home_location"),
])
def test_own_sales_unavailable_reasons(monkeypatch, kwargs, home, reason):
    _patch_ledger(monkeypatch, rows=[], **kwargs)
    own = dt.compute_own_sales(_cfg(home_location_id=home), now=NOW)
    assert not own.available and own.reason == reason and own.per_type == {}


# ------------------------------------------------------------ resolve_specs
def _spec(type_id=5, home=20.0, mode="dynamic", dmin=None, dmax=None):
    return dt.RowSpec(type_id, f"Item{type_id}", 1.0, home, 0.0, mode, dmin, dmax)


def _signals(own=None, market=None, margins=None):
    return dt.Signals(own or dt.OwnSales(True, None, 20.0, {}), market or {}, margins or {}, 10000009)


def test_resolve_specs_uses_live_margin_then_realized_fallback_and_reports_sources():
    own = dt.OwnSales(True, None, 20.0, {5: dt.OwnRow(2.0, 40.0, 0.3, 0.25), 6: dt.OwnRow(2.0, 40.0, 0.3, 0.25)})
    margins = {5: dt.MarginInfo(0.30, 100.0, 0.30, False), 6: dt.MarginInfo(None, None, None, False)}
    out = dt.resolve_specs(_cfg(), [_spec(5), _spec(6)], _signals(own, {}, margins), {})
    live, realized = out
    assert live["margin_source"] == "live" and live["margin"] == 0.30
    assert realized["margin_source"] == "realized" and realized["margin"] == 0.25      # E-11
    assert live["velocity_source"] == "own" and live["max_day_share"] == 0.3
    assert live["region_id"] == 10000009 and live["configured_home"] == 20.0


def test_resolve_specs_no_margin_anywhere_holds_the_row():
    out = dt.resolve_specs(_cfg(), [_spec(5)], _signals(market={5: 1.0}), {})
    assert out[0]["reason"] == dt.REASON_HELD and out[0]["effective_qty"] == 20.0


def test_resolve_specs_marks_zero_own_velocity_when_the_ledger_is_usable_but_the_row_never_sold():
    margins = {5: dt.MarginInfo(0.30, 100.0, None, False)}
    out = dt.resolve_specs(_cfg(), [_spec(5)], _signals(market={5: 1.0}, margins=margins), {})[0]
    assert out["velocity_own"] == 0.0 and out["velocity_source"] == "market"


def test_resolve_specs_own_unavailable_is_none_not_zero():
    own = dt.OwnSales(False, "history_too_short", 10.0, {5: dt.OwnRow(9.0, 90.0, 0.2, 0.3)})
    margins = {5: dt.MarginInfo(0.30, 100.0, None, False)}
    out = dt.resolve_specs(_cfg(), [_spec(5)], _signals(own, {5: 1.0}, margins), {})[0]
    assert out["velocity_own"] is None and out["velocity_source"] == "market"


def test_resolve_specs_continues_from_the_previous_frozen_row():
    margins = {5: dt.MarginInfo(0.30, 100.0, None, False)}
    previous = {5: {"effective_qty": 40.0, "smoothed_qty": 38.0, "reason": dt.REASON_OK}}
    out = dt.resolve_specs(_cfg(), [_spec(5)], _signals(market={5: 0.0}, margins=margins), previous)[0]
    # velocity 0, own 0 (usable ledger, no sale) -> raw 0, smoothed 38 -> 26.6
    assert out["smoothed_qty"] == pytest.approx(26.6)
    assert out["effective_qty"] == 27.0


# ------------------------------------------------- read side, switch off
def test_effective_stock_targets_with_the_switch_off_reads_nothing_else(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not be called with the switch off")
    monkeypatch.setattr(storage, "load_stock_target_modes", boom)
    monkeypatch.setattr(storage, "load_dynamic_values_on_or_before", boom)
    rows = [(1, "A", 0.0, 5.0, None)]
    assert dt.effective_stock_targets(_cfg(), rows) == rows
    monkeypatch.setattr(storage, "load_stock_targets", lambda: rows)
    assert dt.effective_stock_targets(_cfg()) == rows
    assert dt.dynamic_type_ids(_cfg()) == set()


def test_effective_stock_targets_replaces_only_dynamic_home_values(monkeypatch):
    rows = [(1, "A", 1.0, 20.0, 3.0), (2, "B", 0.0, 8.0, None), (3, "C", 0.0, 5.0, None)]
    monkeypatch.setattr(storage, "load_stock_target_modes", lambda: {
        1: ("dynamic", None, None), 2: ("fixed", None, None), 3: ("dynamic", None, None)})
    monkeypatch.setattr(storage, "load_dynamic_values_on_or_before", lambda day: {
        1: {"effective_qty": 33.0}, 2: {"effective_qty": 999.0}})      # 3 has no frozen row yet
    cfg = _cfg(dynamic_targets_enabled=True)
    out = dt.effective_stock_targets(cfg, rows, day=TODAY)
    assert out == [(1, "A", 1.0, 33.0, 3.0), (2, "B", 0.0, 8.0, None), (3, "C", 0.0, 5.0, None)]
    assert dt.dynamic_type_ids(cfg) == {1, 3}


def test_effective_stock_targets_without_any_dynamic_row_skips_the_frozen_read(monkeypatch):
    monkeypatch.setattr(storage, "load_stock_target_modes", lambda: {1: ("fixed", None, None)})
    monkeypatch.setattr(storage, "load_dynamic_values_on_or_before",
                        lambda day: (_ for _ in ()).throw(AssertionError("no dynamic row, nothing to read")))
    rows = [(1, "A", 1.0, 20.0, 3.0)]
    assert dt.effective_stock_targets(_cfg(dynamic_targets_enabled=True), rows) == rows


# ----------------------------------------------------------------- settings
def test_new_fields_have_ranges_and_defaults():
    cfg = ProductionConfig()
    assert cfg.dynamic_targets_enabled is False
    assert cfg.dynamic_target_window_days == 28 and cfg.dynamic_target_day_cap_multiple == 0.0   # R-15: no cap by default
    assert cfg.dynamic_target_margin_hysteresis == 0.02
    base_config.validate_config_overrides(cfg, {"dynamic_target_window_days": 28})
    with pytest.raises(base_config.ConfigError):
        base_config.validate_config_overrides(cfg, {"dynamic_target_window_days": 29})      # V-02
    with pytest.raises(base_config.ConfigError):
        base_config.validate_config_overrides(cfg, {"dynamic_target_window_days": 6})
    with pytest.raises(base_config.ConfigError):
        base_config.validate_config_overrides(cfg, {"dynamic_target_smoothing": 0})
    with pytest.raises(base_config.ConfigError):
        base_config.validate_config_overrides(cfg, {"dynamic_targets_enabled": "yes"})


def test_merged_validation_is_skipped_while_the_feature_is_unused():
    cfg = ProductionConfig()
    validate_production_merged(cfg, {"min_margin": 0.60})          # V-06: no error for a tenant not using it
    validate_production_merged(cfg, {})


def test_merged_validation_checks_the_dynamic_fields_when_touched_or_enabled():
    cfg = ProductionConfig()
    with pytest.raises(base_config.ConfigError, match="reference_margin"):
        validate_production_merged(cfg, {"dynamic_targets_enabled": True, "min_margin": 0.35})
    with pytest.raises(base_config.ConfigError, match="factor_min"):
        validate_production_merged(cfg, {"dynamic_target_factor_min": 3.0})
    with pytest.raises(base_config.ConfigError, match="min_history_days"):
        validate_production_merged(cfg, {"dynamic_target_min_history_days": 20, "dynamic_target_window_days": 10})
    validate_production_merged(cfg, {"dynamic_targets_enabled": True})
    enabled = ProductionConfig(dynamic_targets_enabled=True)
    with pytest.raises(base_config.ConfigError, match="reference_margin"):
        validate_production_merged(enabled, {"min_margin": 0.35})   # enabled: a plain min_margin save is checked too


# --------------------------------------------------------------- hysteresis
def test_a_row_at_zero_resumes_only_above_min_margin_plus_hysteresis():
    cfg = _cfg()                                         # min 0.10, hysteresis 0.02
    stuck = dt.resolve_row(_row(margin=0.11, prev_effective=0.0, prev_smoothed=30.0,
                                prev_reason=dt.REASON_BELOW_MIN_MARGIN), cfg)
    assert (stuck.effective_qty, stuck.reason) == (0.0, dt.REASON_BELOW_MIN_MARGIN)
    assert stuck.smoothed_qty == 30.0                    # E-06: the level is kept either way
    back = dt.resolve_row(_row(margin=0.13, prev_effective=0.0, prev_smoothed=30.0,
                               prev_reason=dt.REASON_BELOW_MIN_MARGIN), cfg)
    assert back.reason != dt.REASON_BELOW_MIN_MARGIN and back.effective_qty > 0


def test_the_hysteresis_only_applies_to_a_row_that_is_already_below():
    fresh = dt.resolve_row(_row(margin=0.11, prev_effective=20.0, prev_smoothed=20.0, prev_reason=dt.REASON_OK), _cfg())
    assert fresh.reason != dt.REASON_BELOW_MIN_MARGIN and fresh.effective_qty > 0
    first_day = dt.resolve_row(_row(margin=0.11), _cfg())
    assert first_day.reason != dt.REASON_BELOW_MIN_MARGIN


def test_hysteresis_zero_restores_the_plain_threshold():
    cfg = _cfg(dynamic_target_margin_hysteresis=0.0)
    r = dt.resolve_row(_row(margin=0.11, prev_effective=0.0, prev_smoothed=30.0, prev_reason=dt.REASON_BELOW_MIN_MARGIN), cfg)
    assert r.reason != dt.REASON_BELOW_MIN_MARGIN


def test_a_margin_hovering_at_the_threshold_flips_far_less_with_the_hysteresis():
    margins = [0.09, 0.11, 0.09, 0.115, 0.095, 0.11, 0.09, 0.118, 0.1, 0.11]

    def flips(cfg):
        prev, count = None, 0
        for m in margins:
            r = dt.resolve_row(_row(margin=m, velocity_own=2.0,
                                    prev_effective=None if prev is None else prev.effective_qty,
                                    prev_smoothed=None if prev is None else prev.smoothed_qty,
                                    prev_reason=None if prev is None else prev.reason), cfg)
            if prev is not None and (prev.effective_qty == 0) != (r.effective_qty == 0):
                count += 1
            prev = r
        return count
    assert flips(_cfg(dynamic_target_margin_hysteresis=0.0)) > flips(_cfg())
    assert flips(_cfg()) <= 1
