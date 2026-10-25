"""Dynamic stock targets, Postgres-backed parts (docs/DYNAMIC_STOCK_TARGETS_PLAN.md):
storage functions, the freeze lifecycle through both planners, the actions,
the cheap-page consumers and the invalidation allowlist of review finding
V-01."""
import ast
import inspect
from datetime import date, timedelta
from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions, dynamic_targets as dt, engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.models import CharacterSlotRow

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant, tenant_pair  # noqa: F401
from .test_production_engine import (  # noqa: F401 - their autouse fixtures keep plan_* tests off the real DB
    _default_manual_blueprints, _default_manual_incoming_qty, _default_manual_stock_at_location,
    _make_fake_plan_context, _noop_save_latest_buy_list, _reset_discover_cache, _stub_shared_production_owner_ids,
    _zero_trading_broker_fee,
)
from .test_storage_production_buy_list import _apply_production_buy_list_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

TODAY = dt._today
DOCS = Path(__file__).resolve().parent.parent / "docs"


@pytest.fixture(autouse=True)
def _wipe_dynamic_values():
    pg_helpers.wipe_tables("stock_target_dynamic_values")
    yield
    pg_helpers.wipe_tables("stock_target_dynamic_values")


@pytest.fixture(autouse=True)
def _reset_dynamic_module_state(monkeypatch):
    monkeypatch.setattr(dt, "_invalidated_at", None)


def _seed(type_id, name="Widget", backup=2.0, home=20.0, jita=0.0, mode=None, dmin=None, dmax=None):
    storage.upsert_stock_target(type_id, name, backup, home, jita)
    if mode is not None:
        storage.set_stock_target_mode(type_id, mode, dmin, dmax)


def _frozen(type_id, effective, smoothed=None, reason="ok", **extra):
    row = {c: None for c in storage.DYNAMIC_VALUE_COLUMNS if c not in ("day", "computed_at")}
    row.update(type_id=type_id, effective_qty=float(effective),
               smoothed_qty=float(effective if smoothed is None else smoothed), reason=reason)
    row.update(extra)
    return row


# ======================================================================= storage
def test_modes_default_to_fixed_and_upserts_do_not_touch_them(tenant):
    _seed(34, "Tritanium")
    assert storage.load_stock_target_modes() == {34: ("fixed", None, None)}

    assert storage.set_stock_target_mode(34, "dynamic", 1.0, 50.0) is True
    storage.upsert_stock_target(34, "Tritanium", 3.0, 25.0, 4.0)      # an ordinary edit
    assert storage.load_stock_target_modes() == {34: ("dynamic", 1.0, 50.0)}
    assert storage.load_stock_targets() == [(34, "Tritanium", 3.0, 25.0, 4.0)]    # 5-tuple shape unchanged

    assert storage.set_stock_target_mode(99999, "dynamic", 0.0, 1.0) is False


def test_bulk_mode_write_is_one_call_and_keeps_home_values(tenant):
    _seed(1, "A", home=5.0)
    _seed(2, "B", home=6.0)
    written = storage.set_stock_target_modes_bulk([(1, "dynamic", 0.0, 15.0), (2, "dynamic", 1.0, 20.0)])
    assert written == 2
    assert storage.load_stock_target_modes() == {1: ("dynamic", 0.0, 15.0), 2: ("dynamic", 1.0, 20.0)}
    assert [r[3] for r in sorted(storage.load_stock_targets())] == [5.0, 6.0]
    assert storage.set_stock_target_modes_bulk([]) == 0


def test_freeze_and_read_back_by_day(tenant):
    today = TODAY()
    yesterday = today - timedelta(days=1)
    storage.freeze_dynamic_values(yesterday, [_frozen(1, 10), _frozen(2, 20)])
    storage.freeze_dynamic_values(today, [_frozen(1, 12, 11.6, reason="ok", velocity_source="own", margin=0.3,
                                                  gate_blocked=False, max_day_share=0.4, region_id=10000009)])

    day_rows = storage.load_dynamic_values_for_day(today)
    assert set(day_rows) == {1}
    assert day_rows[1]["effective_qty"] == 12.0 and day_rows[1]["smoothed_qty"] == pytest.approx(11.6)
    assert day_rows[1]["velocity_source"] == "own" and day_rows[1]["gate_blocked"] is False
    assert day_rows[1]["computed_at"] is not None

    newest = storage.load_dynamic_values_on_or_before(today)
    assert {t: r["effective_qty"] for t, r in newest.items()} == {1: 12.0, 2: 20.0}       # type 2 falls back to yesterday
    before = storage.latest_dynamic_value_before(today)
    assert {t: r["effective_qty"] for t, r in before.items()} == {1: 10.0, 2: 20.0}       # strictly before today (E-05)


def test_freezing_the_same_day_again_replaces_the_row(tenant):
    today = TODAY()
    storage.freeze_dynamic_values(today, [_frozen(1, 10)])
    storage.freeze_dynamic_values(today, [_frozen(1, 14, reason="clamped_max")])
    rows = storage.load_dynamic_values_for_day(today)
    assert rows[1]["effective_qty"] == 14.0 and rows[1]["reason"] == "clamped_max"
    assert len(storage.dynamic_value_history(1, 30)) == 1


def test_rows_older_than_ninety_days_are_pruned_on_write(tenant):
    today = TODAY()
    old = today - timedelta(days=91)
    with storage.connect() as conn:         # plant an old row directly
        conn.execute("INSERT INTO stock_target_dynamic_values (type_id, day, effective_qty, smoothed_qty, reason) "
                     "VALUES (?,?,?,?,?)", (1, old, 5.0, 5.0, "ok"))
    storage.freeze_dynamic_values(today, [_frozen(1, 9)])
    assert [r["day"] for r in storage.dynamic_value_history(1, 365)] == [today]


def test_history_is_newest_first_and_limited(tenant):
    today = TODAY()
    for i in range(5):
        storage.freeze_dynamic_values(today - timedelta(days=i), [_frozen(1, i)])
    history = storage.dynamic_value_history(1, 3)
    assert [r["effective_qty"] for r in history] == [0.0, 1.0, 2.0]


def test_delete_for_day_all_or_selected_types(tenant):
    today = TODAY()
    storage.freeze_dynamic_values(today, [_frozen(1, 1), _frozen(2, 2), _frozen(3, 3)])
    storage.freeze_dynamic_values(today - timedelta(days=1), [_frozen(1, 9)])
    assert storage.delete_dynamic_values_for_day(today, [2]) == 1
    assert set(storage.load_dynamic_values_for_day(today)) == {1, 3}
    assert storage.delete_dynamic_values_for_day(today, []) == 0
    assert storage.delete_dynamic_values_for_day(today) == 2
    assert storage.load_dynamic_values_for_day(today) == {}
    assert 1 in storage.load_dynamic_values_for_day(today - timedelta(days=1))          # earlier days never touched


def test_deleting_a_stock_target_deletes_its_frozen_values(tenant):
    today = TODAY()
    _seed(1, "A")
    _seed(2, "B")
    storage.freeze_dynamic_values(today, [_frozen(1, 1), _frozen(2, 2)])
    storage.delete_stock_target(1)
    assert set(storage.load_dynamic_values_for_day(today)) == {2}         # P-24 / E-20


def test_frozen_values_are_invisible_to_other_tenants(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    today = TODAY()
    with storage.tenant_context(tenant_a):
        storage.freeze_dynamic_values(today, [_frozen(1, 5)])
    with storage.tenant_context(tenant_b):
        assert storage.load_dynamic_values_for_day(today) == {}
        storage.freeze_dynamic_values(today, [_frozen(1, 77)])
    with storage.tenant_context(tenant_a):
        assert storage.load_dynamic_values_for_day(today)[1]["effective_qty"] == 5.0
        storage.delete_dynamic_values_for_day(today)
    with storage.tenant_context(tenant_b):
        assert storage.load_dynamic_values_for_day(today)[1]["effective_qty"] == 77.0


def test_schema_files_are_idempotent_and_leave_one_check_constraint():
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        for _ in range(2):
            conn.execute((DOCS / "phase1_schema.sql").read_text(encoding="utf-8"))
            conn.execute((DOCS / "production_buy_list_schema.sql").read_text(encoding="utf-8"))
        checks = conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = 'stock_targets'::regclass AND contype = 'c'"
        ).fetchone()[0]
        privileges = conn.execute(
            "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) FROM information_schema.role_table_grants "
            "WHERE table_name = 'stock_target_dynamic_values' AND grantee = 'eve_trader_app'").fetchone()[0]
    assert checks == 1
    assert privileges == "DELETE,INSERT,SELECT,UPDATE"                  # P-26


# ===================================================== planners and the freeze
def _signals(velocity=2.0, margin=0.30, gate_blocked=False, type_id=10):
    own = dt.OwnSales(True, None, 20.0, {type_id: dt.OwnRow(velocity, 40.0, 0.2, 0.3)})
    margins = {type_id: dt.MarginInfo(margin, 100.0, margin, gate_blocked)}
    return dt.Signals(own, {}, margins, 10000009)


def _patch_planner(monkeypatch, stock_targets):
    bp_by_id = {tid: (100 + tid, 11, 1) for tid, *_ in stock_targets}
    monkeypatch.setattr(engine, "_PlanContext", _make_fake_plan_context(stock_targets))
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", bp_by_id[type_id]))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [])
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_stock_on_hand", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Build")
    monkeypatch.setattr(engine, "_build_margin", lambda *a, **k: 0.5)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    monkeypatch.setattr(storage, "get_blueprint_time", lambda blueprint_id, activity_id: 100.0)
    monkeypatch.setattr(engine, "_activity_mods", lambda *a, **k: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine, "job_category", lambda type_id: "Reactions")
    monkeypatch.setattr(engine, "character_slot_overview", lambda: [
        CharacterSlotRow(character_name="A", job_type="Reactions", total_slots=5, used_slots=0, free_slots=5),
        CharacterSlotRow(character_name="A", job_type="Manufacturing", total_slots=5, used_slots=0, free_slots=5)])


class _Gather:
    """Stands in for dynamic_targets._gather_signals and counts the calls."""
    def __init__(self, signals=None, error=None):
        self.signals, self.error, self.calls = signals, error, 0

    def __call__(self, cfg, ctx, rows, cost_memo, t2_memo, alchemy_memo, *, today, client=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.signals


def _cfg(**kw):
    base = dict(component_overbuild=0.0, min_margin=0.10, home_location_id=999, dynamic_targets_enabled=True)
    base.update(kw)
    return ProductionConfig(**base)


def _missing(result, type_id=10):
    return {row.type_id: row.total_missing for row in result["inventory"]}[type_id]


def test_plan_production_uses_the_frozen_effective_value_for_a_dynamic_row(monkeypatch, tenant):
    _seed(10, backup=2.0, home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    targets = [(10, "Widget", 2.0, 20.0, 0.0)]
    _patch_planner(monkeypatch, targets)
    gather = _Gather(_signals(velocity=2.0))
    monkeypatch.setattr(dt, "_gather_signals", gather)

    result = engine.plan_production(_cfg())

    # raw = 2/day x 14 d = 28; smoothed from 20 with alpha 0.3 = 22.4 -> 22; backup 2 + home 22
    assert _missing(result) == 24.0
    frozen = storage.load_dynamic_values_for_day(TODAY())[10]
    assert frozen["effective_qty"] == 22.0 and frozen["velocity_source"] == "own" and frozen["margin_source"] == "live"
    assert frozen["region_id"] == 10000009

    # the switch off is exactly today's behaviour: configured 20, nothing computed
    gather.calls = 0
    off = engine.plan_production(_cfg(dynamic_targets_enabled=False))
    assert _missing(off) == 22.0 and gather.calls == 0


def test_frozen_once_per_day_and_both_planners_read_the_same_number(monkeypatch, tenant):
    _seed(10, backup=2.0, home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    targets = [(10, "Widget", 2.0, 20.0, 0.0)]
    _patch_planner(monkeypatch, targets)
    gather = _Gather(_signals(velocity=2.0))
    monkeypatch.setattr(dt, "_gather_signals", gather)
    seen = []
    real_base_runs = engine._base_runs

    def spy(cfg, home, jita, overrides, cost_memo, decryptors, t2, indices, adjusted, stock_targets, *rest):
        seen.append(list(stock_targets))
        return real_base_runs(cfg, home, jita, overrides, cost_memo, decryptors, t2, indices, adjusted, stock_targets, *rest)
    monkeypatch.setattr(engine, "_base_runs", spy)

    engine.plan_production(_cfg())
    engine.plan_asset_optimized(_cfg())
    engine.plan_production(_cfg())

    assert gather.calls == 1                                             # computed by the first plan only
    assert [row[3] for rows in seen for row in rows] == [22.0, 22.0, 22.0]   # P-06: _base_runs sees the effective rows


def test_fixed_only_tenant_computes_nothing_and_matches_the_switch_off_plan(monkeypatch, tenant):
    _seed(10, backup=2.0, home=20.0)                                     # mode stays 'fixed' (E-19)
    targets = [(10, "Widget", 2.0, 20.0, 0.0), (11, "Other", 0.0, 5.0, 0.0)]
    _seed(11, "Other", 0.0, 5.0)
    _patch_planner(monkeypatch, targets)
    gather = _Gather(error=AssertionError("a fixed-only tenant must not fetch anything"))
    monkeypatch.setattr(dt, "_gather_signals", gather)

    on = engine.plan_production(_cfg())
    off = engine.plan_production(_cfg(dynamic_targets_enabled=False))

    assert repr(on["inventory"]) == repr(off["inventory"])
    assert repr(on["build_list"]) == repr(off["build_list"])
    assert repr(on["buy_list"]) == repr(off["buy_list"])
    assert gather.calls == 0 and storage.load_dynamic_values_for_day(TODAY()) == {}


def test_a_dynamic_row_changes_only_its_home_pool(monkeypatch, tenant):
    _seed(10, backup=2.0, home=20.0, jita=5.0, mode="dynamic", dmin=0.0, dmax=60.0)
    targets = [(10, "Widget", 2.0, 20.0, 5.0)]
    _patch_planner(monkeypatch, targets)
    monkeypatch.setattr(storage, "esi_sell_order_qty_in_region", lambda *a, **k: 0.0, raising=False)
    monkeypatch.setattr(dt, "_gather_signals", _Gather(_signals(velocity=2.0)))
    monkeypatch.setattr(engine, "_sell_order_qty_in_region", lambda *a, **k: 0.0)

    on = _missing(engine.plan_production(_cfg()))
    off = _missing(engine.plan_production(_cfg(dynamic_targets_enabled=False)))

    assert off == 2.0 + 20.0 + 5.0
    assert on == 2.0 + 22.0 + 5.0                                       # D1: backup and Jita pools stay fixed


def test_below_min_margin_drops_the_home_pool_to_zero(monkeypatch, tenant):
    _seed(10, backup=2.0, home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    _patch_planner(monkeypatch, [(10, "Widget", 2.0, 20.0, 0.0)])
    monkeypatch.setattr(dt, "_gather_signals", _Gather(_signals(margin=0.04)))

    assert _missing(engine.plan_production(_cfg())) == 2.0
    frozen = storage.load_dynamic_values_for_day(TODAY())[10]
    assert frozen["effective_qty"] == 0.0 and frozen["reason"] == "below_min_margin"       # D5
    assert frozen["smoothed_qty"] == pytest.approx(20.0)                                    # E-06


def test_smoothing_continues_from_yesterday_and_recomputing_today_is_idempotent(monkeypatch, tenant):
    _seed(10, backup=0.0, home=20.0, mode="dynamic", dmin=0.0, dmax=200.0)
    yesterday = TODAY() - timedelta(days=1)
    storage.freeze_dynamic_values(yesterday, [_frozen(10, 40, 38.0)])
    _patch_planner(monkeypatch, [(10, "Widget", 0.0, 20.0, 0.0)])
    gather = _Gather(_signals(velocity=3.0))        # raw 42
    monkeypatch.setattr(dt, "_gather_signals", gather)

    engine.plan_production(_cfg())
    first = storage.load_dynamic_values_for_day(TODAY())[10]
    dt.invalidate_today()
    engine.plan_production(_cfg())
    second = storage.load_dynamic_values_for_day(TODAY())[10]

    assert first["smoothed_qty"] == pytest.approx(38.0 + 0.3 * (42 - 38.0))     # base = yesterday, not 20 (E-05)
    assert second["smoothed_qty"] == pytest.approx(first["smoothed_qty"])        # never smoothed twice
    assert gather.calls == 2


def test_a_stale_base_older_than_the_window_restarts_from_the_configured_value(monkeypatch, tenant):
    _seed(10, backup=0.0, home=20.0, mode="dynamic", dmin=0.0, dmax=200.0)
    storage.freeze_dynamic_values(TODAY() - timedelta(days=40), [_frozen(10, 150, 150.0)])      # E-25
    _patch_planner(monkeypatch, [(10, "Widget", 0.0, 20.0, 0.0)])
    monkeypatch.setattr(dt, "_gather_signals", _Gather(_signals(velocity=2.0)))
    engine.plan_production(_cfg())
    assert storage.load_dynamic_values_for_day(TODAY())[10]["effective_qty"] == 22.0


def test_without_a_home_location_dynamic_rows_hold_their_value(monkeypatch, tenant):
    _seed(10, backup=0.0, home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    _patch_planner(monkeypatch, [(10, "Widget", 0.0, 20.0, 0.0)])
    gather = _Gather(error=AssertionError("no home location: nothing to measure against"))
    monkeypatch.setattr(dt, "_gather_signals", gather)

    result = engine.plan_production(_cfg(home_location_id=None))
    assert _missing(result) == 20.0 and gather.calls == 0                        # E-18
    assert storage.load_dynamic_values_for_day(TODAY())[10]["reason"] == "held_no_data"


def test_a_failing_refresh_never_fails_the_plan(monkeypatch, tenant):
    _seed(10, backup=0.0, home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    storage.freeze_dynamic_values(TODAY() - timedelta(days=1), [_frozen(10, 31, 31.0)])
    _patch_planner(monkeypatch, [(10, "Widget", 0.0, 20.0, 0.0)])
    monkeypatch.setattr(dt, "_gather_signals", _Gather(error=RuntimeError("history down")))
    assert _missing(engine.plan_production(_cfg())) == 31.0           # last frozen value, not an error


def test_an_all_tenant_invalidation_makes_older_rows_count_as_missing(monkeypatch, tenant):
    _seed(10, backup=0.0, home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    _patch_planner(monkeypatch, [(10, "Widget", 0.0, 20.0, 0.0)])
    gather = _Gather(_signals(velocity=2.0))
    monkeypatch.setattr(dt, "_gather_signals", gather)
    engine.plan_production(_cfg())
    dt.mark_all_invalidated()                                           # P-21 (SDE apply)
    engine.plan_production(_cfg())
    assert gather.calls == 2


def test_plan_context_never_triggers_the_resolver():
    assert "dynamic_targets" not in inspect.getsource(engine._PlanContext)     # E-14 / P-09


# ===================================================================== actions
def test_set_mode_defaults_follow_d6_and_d13(tenant):
    _seed(1, "Big", home=20.0)
    _seed(2, "Small", home=2.0)
    _seed(3, "None", home=0.0)
    assert actions.do_set_stock_target_mode(1, "dynamic")["dynamic_max"] == 60.0
    assert actions.do_set_stock_target_mode(2, "dynamic")["dynamic_max"] == 10.0      # D13 floor
    with pytest.raises(ActionError, match="maximum"):
        actions.do_set_stock_target_mode(3, "dynamic")
    assert actions.do_set_stock_target_mode(3, "dynamic", dynamic_max=7)["dynamic_max"] == 7


def test_set_mode_validation(tenant):
    _seed(1, "A", home=20.0)
    with pytest.raises(ActionError, match="'fixed' or 'dynamic'"):
        actions.do_set_stock_target_mode(1, "auto")
    with pytest.raises(ActionError, match="larger than the maximum"):
        actions.do_set_stock_target_mode(1, "dynamic", dynamic_min=9, dynamic_max=3)
    with pytest.raises(ActionError, match="negative"):
        actions.do_set_stock_target_mode(1, "dynamic", dynamic_min=-1)
    with pytest.raises(ActionError, match="not a stock target"):
        actions.do_set_stock_target_mode(404, "dynamic")
    with pytest.raises(ActionError, match="above 0"):
        actions.do_set_stock_target_mode(1, "dynamic", dynamic_max=0)


def test_switching_back_to_fixed_keeps_min_max_and_the_home_value(tenant):
    _seed(1, "A", home=20.0)
    actions.do_set_stock_target_mode(1, "dynamic", dynamic_min=2, dynamic_max=40)
    out = actions.do_set_stock_target_mode(1, "fixed")
    assert out["target_mode"] == "fixed" and (out["dynamic_min"], out["dynamic_max"]) == (2, 40)
    assert storage.load_stock_targets()[0][3] == 20.0                    # reversible (4.1)
    again = actions.do_set_stock_target_mode(1, "dynamic")
    assert (again["dynamic_min"], again["dynamic_max"]) == (2, 40)


def test_a_mode_change_drops_only_that_rows_value_of_today(tenant, monkeypatch):
    monkeypatch.setattr(actions, "_mark_cached_production_plans_stale", lambda: None)
    _seed(1, "A", home=20.0)
    _seed(2, "B", home=20.0)
    storage.freeze_dynamic_values(TODAY(), [_frozen(1, 5), _frozen(2, 6)])
    actions.do_set_stock_target_mode(1, "dynamic")
    assert set(storage.load_dynamic_values_for_day(TODAY())) == {2}


def test_bulk_to_dynamic_skips_rows_without_a_home_target(tenant):
    _seed(1, "Home", home=20.0)
    _seed(2, "Backup only", backup=5.0, home=0.0)
    _seed(3, "Jita only", backup=0.0, home=0.0, jita=4.0)
    _seed(4, "Has bounds", home=8.0, mode="fixed", dmin=2.0, dmax=30.0)
    out = actions.do_set_all_stock_target_modes("dynamic")
    assert out["updated"] == 2 and {s["type_id"] for s in out["skipped"]} == {2, 3}       # E-26
    modes = storage.load_stock_target_modes()
    assert modes[1] == ("dynamic", 0.0, 60.0) and modes[4] == ("dynamic", 2.0, 30.0)     # existing min/max kept
    assert modes[2][0] == "fixed" and modes[3][0] == "fixed"

    back = actions.do_set_all_stock_target_modes("fixed")
    assert back["updated"] == 4 and back["skipped"] == []
    assert storage.load_stock_target_modes()[4] == ("fixed", 2.0, 30.0)                  # min/max stay stored
    with pytest.raises(ActionError):
        actions.do_set_all_stock_target_modes("sometimes")


def test_editing_a_dynamic_rows_home_value_is_rejected_only_while_the_switch_is_on(tenant, monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "A", 1.0, 1, 1, 0, None))
    _seed(1, "A", home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    on = _cfg()
    with pytest.raises(ActionError, match="dynamic"):
        actions.do_update_stock_target(1, home_market_stock=30.0, cfg=on)
    # backup/Jita edits are fine, and so is a home edit while everything acts as fixed
    actions.do_update_stock_target(1, backup_stock=9.0, cfg=on)
    actions.do_update_stock_target(1, home_market_stock=33.0, cfg=_cfg(dynamic_targets_enabled=False))
    row = storage.load_stock_targets()[0]
    assert row[2] == 9.0 and row[3] == 33.0


def test_list_view_shows_configured_and_effective_values(tenant):
    _seed(1, "Dyn", home=20.0, mode="dynamic", dmin=1.0, dmax=50.0)
    _seed(2, "Fix", home=8.0)
    storage.freeze_dynamic_values(TODAY(), [_frozen(1, 33, velocity_source="market", margin=0.2, reason="ok",
                                                    gate_margin=0.2, gate_blocked=True)])
    rows = {r["type_id"]: r for r in actions.do_list_stock_targets(_cfg())}
    assert rows[1]["home_market_stock"] == 20.0 and rows[1]["effective_home_market_stock"] == 33.0
    assert rows[1]["target_mode"] == "dynamic" and (rows[1]["dynamic_min"], rows[1]["dynamic_max"]) == (1.0, 50.0)
    assert rows[1]["velocity_source"] == "market" and rows[1]["gate_blocked"] is True
    assert rows[1]["dynamic_day"] == TODAY().isoformat()
    assert rows[2]["effective_home_market_stock"] == 8.0 and rows[2]["reason"] is None

    off = {r["type_id"]: r for r in actions.do_list_stock_targets(_cfg(dynamic_targets_enabled=False))}
    assert off[1]["effective_home_market_stock"] == 20.0 and off[1]["target_mode"] == "dynamic"


def test_recalculate_requires_the_switch_and_a_home_location(tenant, monkeypatch):
    monkeypatch.setattr(actions, "_mark_cached_production_plans_stale", lambda: None)
    with pytest.raises(ActionError, match="switched off"):
        actions.do_recalculate_dynamic_targets(_cfg(dynamic_targets_enabled=False))
    with pytest.raises(ActionError, match="home location"):
        actions.do_recalculate_dynamic_targets(_cfg(home_location_id=None))


def test_recalculate_recomputes_today_through_the_same_freeze(tenant, monkeypatch):
    monkeypatch.setattr(actions, "_mark_cached_production_plans_stale", lambda: None)
    _seed(10, home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    storage.freeze_dynamic_values(TODAY(), [_frozen(10, 5, 5.0)])
    monkeypatch.setattr(dt, "_make_context", lambda cfg: _make_fake_plan_context([(10, "Widget", 2.0, 20.0, 0.0)])(cfg))
    _patch_planner(monkeypatch, [(10, "Widget", 2.0, 20.0, 0.0)])
    monkeypatch.setattr(dt, "_gather_signals", _Gather(_signals(velocity=2.0)))
    assert actions.do_recalculate_dynamic_targets(_cfg()) == {"written": 1}
    assert storage.load_dynamic_values_for_day(TODAY())[10]["effective_qty"] == 22.0


def test_history_action_serialises_days(tenant):
    storage.freeze_dynamic_values(TODAY(), [_frozen(1, 4)])
    out = actions.do_dynamic_target_history(1, days=5000)
    assert out["rows"][0]["day"] == TODAY().isoformat() and isinstance(out["rows"][0]["computed_at"], str)


def test_preview_covers_fixed_rows_too_and_writes_nothing(tenant, monkeypatch):
    _seed(10, "Widget", home=20.0)                      # fixed
    _seed(11, "Backup only", backup=3.0, home=0.0)      # no home target: not part of the preview
    ctx = _make_fake_plan_context([(10, "Widget", 2.0, 20.0, 0.0)])(_cfg())
    monkeypatch.setattr(dt, "_gather_signals", _Gather(_signals(velocity=1.5)))
    out = dt.preview_dynamic_targets(_cfg(dynamic_targets_enabled=False), ctx=ctx)
    assert [r["type_id"] for r in out["rows"]] == [10]
    row = out["rows"][0]
    assert row["mode"] == "fixed" and row["day1_qty"] == 20.0 and row["converged_qty"] == 21.0
    assert row["tied_current"] == 0.0 or row["cost"] is not None
    assert out["summary"]["rows"] == 1 and out["summary"]["up"] == 1
    assert row["listed_home_qty"] == 0.0                                    # R-16 precondition: nothing listed at 999
    assert out["summary"]["unlisted"] == 1 and out["summary"]["listing_known"] == 1
    assert storage.load_dynamic_values_for_day(TODAY()) == {}


def test_preview_without_a_home_location_reports_no_listing_data(tenant, monkeypatch):
    _seed(10, "Widget", home=20.0)
    ctx = _make_fake_plan_context([(10, "Widget", 2.0, 20.0, 0.0)])(_cfg())
    monkeypatch.setattr(dt, "_gather_signals", _Gather(_signals(velocity=1.5)))
    out = dt.preview_dynamic_targets(_cfg(home_location_id=None, dynamic_targets_enabled=False), ctx=ctx)
    assert out["rows"][0]["listed_home_qty"] is None
    assert out["summary"]["listing_known"] == 0 and out["summary"]["unlisted"] == 0


# =================================================== consumers and allowlist
def test_market_status_keeps_a_dynamic_row_at_effective_zero(tenant, monkeypatch):
    _seed(1, "Dyn", backup=0.0, home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    _seed(2, "Backup only", backup=4.0, home=0.0)
    storage.freeze_dynamic_values(TODAY(), [_frozen(1, 0, reason="below_min_margin")])
    monkeypatch.setattr(engine, "classify_activity", lambda t: ("Tech I", None))
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_sell_order_qty_in_region", lambda *a, **k: 0.0)
    cfg = _cfg()

    rows = {r.type_id: r for r in engine.market_status(cfg)}
    assert 1 in rows and rows[1].home_target == 0.0 and 2 not in rows          # E-09; #33 still hides backup-only

    off = {r.type_id for r in engine.market_status(_cfg(dynamic_targets_enabled=False))}
    assert off == {1}                                                           # configured 20 with the switch off


def test_listing_shortfall_follows_the_effective_value(tenant, monkeypatch):
    _seed(1, "Dyn", backup=0.0, home=20.0, mode="dynamic", dmin=0.0, dmax=60.0)
    storage.freeze_dynamic_values(TODAY(), [_frozen(1, 7)])
    monkeypatch.setattr(engine, "_stock_on_hand", lambda *a, **k: 0.0)
    cfg = _cfg(home_location_id=None)
    assert engine.market_listing_shortfall_by_type(cfg) == {1: 7.0}
    assert engine.market_listing_shortfall_by_type(_cfg(home_location_id=None, dynamic_targets_enabled=False)) == {1: 20.0}


def test_settings_save_invalidates_only_when_the_feature_is_in_use(tenant, monkeypatch):
    calls = []
    monkeypatch.setattr(dt, "invalidate_today", lambda type_ids=None: calls.append(type_ids))
    monkeypatch.setattr(actions, "_mark_cached_production_plans_stale", lambda: None)
    monkeypatch.setattr(actions, "invalidate_ship_margin_cache", lambda *a, **k: None)
    monkeypatch.setattr(actions, "invalidate_discover_cache", lambda *a, **k: None)

    cfg = ProductionConfig()
    actions.do_update_settings({"min_margin": 0.12}, cfg)
    assert calls == []                                                          # P-02: nothing touched while off

    actions.do_update_settings({"dynamic_targets_enabled": True}, cfg)
    actions.do_update_settings({"min_margin": 0.11}, cfg)
    actions.do_update_settings({"dynamic_targets_enabled": False}, cfg)
    assert len(calls) == 3                                                      # on, any save while on, off


def test_settings_save_rejects_inconsistent_dynamic_values(tenant):
    cfg = ProductionConfig()
    with pytest.raises(ActionError, match="factor_min"):
        actions.do_update_settings({"dynamic_target_factor_min": 5.0}, cfg)
    with pytest.raises(ActionError, match="above the maximum"):
        actions.do_update_settings({"dynamic_target_window_days": 29}, cfg)
    actions.do_update_settings({"min_margin": 0.6}, cfg)                         # V-06: unused feature stays unconstrained


def _calls_of(path: Path, name: str) -> set[tuple[str, str]]:
    """(file, enclosing function) for every call of `name` in `path`."""
    text = path.read_text(encoding="utf-8")
    if name not in text:  # skip parsing the files that cannot contain the call
        return set()
    tree = ast.parse(text)
    found = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    target = node.func
                    called = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
                    if called == name:
                        found.add((path.name, fn.name))
    return found


def test_frozen_rows_are_only_invalidated_from_the_closed_allowlist():
    """V-01: never hook the deletion into invalidate_discover_cache (it runs after
    every ESI sync); only these call sites may drop today's frozen rows."""
    package = Path(inspect.getsourcefile(actions)).parent.parent
    found = set()
    for path in package.rglob("*.py"):
        found |= _calls_of(path, "invalidate_today")
        found |= _calls_of(path, "mark_all_invalidated")
    allowed = {
        ("actions.py", "do_update_settings"), ("actions.py", "do_set_stock_target_mode"),
        ("actions.py", "do_set_all_stock_target_modes"),
        ("dynamic_targets.py", "recalculate_dynamic_targets"),
        ("admin.py", "do_apply_sde"),
    }
    assert found <= allowed, f"unexpected invalidation call sites: {found - allowed}"
    assert allowed - found == set(), f"allowlisted call sites that disappeared: {allowed - found}"
    assert ("engine.py", "invalidate_discover_cache") not in found
    assert "dynamic_targets" not in inspect.getsource(engine.invalidate_discover_cache)
