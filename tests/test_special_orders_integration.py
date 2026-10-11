"""Special orders in the regular build workflow, phase 2: plan_production
(docs/SPECIAL_ORDERS_INTEGRATION_PLAN.md D3-D6, D8, D10, D12, D13).

Synthetic BOM, everything Tech I with product_qty 1 and material_mult 1:
- SHIP (10) needs 10x COMPONENT per unit.
- OTHER (30) needs 10x COMPONENT per unit.
- COMPONENT (20) needs 5x MINERAL per unit.
- MINERAL (999) has no blueprint (always bought).
T2_ITEM (40) / T2_ONLY (50) are Tech II with no materials, for the
invention-row tests.
"""
import math

import pytest

from eve_trader import storage
from eve_trader.config import TRADING_CONFIG
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig
from eve_trader.production.models import InventionResult

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

SHIP, COMPONENT, OTHER, MINERAL = 10, 20, 30, 999
T2_ITEM, T2_ONLY = 40, 50

_CLASSIFY = {
    SHIP: ("Tech I", (110, 1, 1.0)),
    COMPONENT: ("Tech I", (120, 1, 1.0)),
    OTHER: ("Tech I", (130, 1, 1.0)),
    T2_ITEM: ("Tech II", (140, 1, 1.0)),
    T2_ONLY: ("Tech II", (150, 1, 1.0)),
}
_MATERIALS = {110: [(COMPONENT, 10)], 120: [(MINERAL, 5)], 130: [(COMPONENT, 10)]}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(storage, "save_latest_buy_list", lambda rows: None)
    monkeypatch.setattr(TRADING_CONFIG, "jita_buy_broker_fee", 0.0)
    monkeypatch.setattr(engine, "shared_production_owner_ids", lambda data_kind: (None, None))


def _fake_context(stock_targets):
    class _FakeCtx:
        captured_extra: list = []

        def __init__(self, cfg, extra_type_ids=()):
            _FakeCtx.captured_extra = list(extra_type_ids)
            self.stock_targets = list(stock_targets)
            self.manual_stock = {}
            self.manual_overrides = {}
            self.selected_decryptors = {}
            self.home = {}
            self.jita = {}
            self.cost_indices = {}
            self.adjusted_prices = {}
            self.adjusted_prices_available = True
    return _FakeCtx


_REAL_STOCK = object()


def _setup(monkeypatch, stock_targets=(), orders=None, stock=None, margins=None):
    """orders: storage.list_integrated_special_order_items rows, or None to
    read the real table. stock: {type_id: _current_stock}, or _REAL_STOCK to
    keep the real _current_stock."""
    ctx_cls = _fake_context(stock_targets)
    monkeypatch.setattr(engine, "_PlanContext", ctx_cls)
    if orders is not None:
        monkeypatch.setattr(storage, "list_integrated_special_order_items", lambda: list(orders))
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: _CLASSIFY.get(type_id, ("Input", None)))
    monkeypatch.setattr(storage, "get_blueprint_materials",
                        lambda blueprint_id, activity_id: _MATERIALS.get(blueprint_id, []))
    monkeypatch.setattr(storage, "get_blueprint_for_product",
                        lambda type_id: _CLASSIFY.get(type_id, ("Input", None))[1])
    monkeypatch.setattr(engine, "_activity_mods",
                        lambda activity, type_id, cfg, cost_indices, blueprint_id=None: (1.0, 1.0, 0.0))
    monkeypatch.setattr(engine, "_unit_cost", lambda *a, **k: 100.0)
    monkeypatch.setattr(engine, "_buy_or_build_decision",
                        lambda type_id, cfg, home, jita, overrides, memo, bp, depth: "Buy" if bp is None else "Build")
    if stock is not _REAL_STOCK:
        stock = stock or {}
        monkeypatch.setattr(engine, "_current_stock", lambda type_id, *a, **k: stock.get(type_id, 0.0))
    margins = margins or {}
    monkeypatch.setattr(engine, "_build_margin", lambda type_id, *a, **k: margins.get(type_id, 1.0))
    return ctx_cls


def _runs(result):
    return {row.type_id: row.job_runs for row in result["build_list"]}


def _buys(result):
    return {row.type_id: row.quantity for row in result["buy_list"]}


def _order_share(result):
    shares = {row.type_id: row.special_order_quantity for row in result["build_list"]}
    shares.update({row.type_id: row.special_order_quantity for row in result["buy_list"]})
    return shares


def test_no_integrated_orders_keeps_plan_unchanged(monkeypatch, tenant):
    _setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0)], orders=[])

    result = engine.plan_production(ProductionConfig(component_overbuild=0.7, min_margin=0.0))

    # 1 Other -> 10 Component + 7 buffer = 17 runs; Mineral 85 + 0.7 * 5 * 10 = 120.
    assert _runs(result) == {OTHER: 1, COMPONENT: 17}
    assert _buys(result) == {MINERAL: pytest.approx(120.0)}
    assert result["order_progress"] == []
    assert all(row.special_order_quantity == 0.0 for row in result["build_list"] + result["buy_list"])
    assert result["inventory"][0].reserved_for_orders == 0.0


def test_order_demand_gets_no_overbuild(monkeypatch, tenant):
    ctx_cls = _setup(monkeypatch, orders=[("o1", "Customer", SHIP, "Ship", 2.0)])

    result = engine.plan_production(ProductionConfig(component_overbuild=0.7, min_margin=0.0))

    assert ctx_cls.captured_extra == [SHIP]
    assert _runs(result) == {SHIP: 2, COMPONENT: 20}
    assert _buys(result) == {MINERAL: pytest.approx(100.0)}  # exact, not x1.7
    assert _order_share(result) == {SHIP: 2.0, COMPONENT: 20.0, MINERAL: pytest.approx(100.0)}
    assert result["order_progress"] == [{
        "order_id": "o1", "note": "Customer", "type_id": SHIP, "type_name": "Ship",
        "ordered": 2.0, "covered": 0.0, "remaining": 2.0,
    }]


def test_shared_component_keeps_stock_target_buffer(monkeypatch, tenant):
    _setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0)],
           orders=[("o1", None, SHIP, "Ship", 1.0)])

    result = engine.plan_production(ProductionConfig(component_overbuild=0.7, min_margin=0.0))

    # Stock-target pass alone: Component 17 runs, Mineral 120 (see the
    # unchanged-plan test). The order adds exactly 10 Component / 50 Mineral.
    assert _runs(result) == {OTHER: 1, SHIP: 1, COMPONENT: 27}
    assert _buys(result) == {MINERAL: pytest.approx(170.0)}
    shares = _order_share(result)
    assert shares[COMPONENT] == 10.0
    assert shares[MINERAL] == pytest.approx(50.0)
    assert shares[OTHER] == 0.0


def test_orders_consume_finished_stock_before_the_stock_target(monkeypatch, tenant):
    # 1 Ship in stock: the order takes it (needs 1 more), the stock target
    # (backup 3) sees 0 and is short all 3.
    _setup(monkeypatch, stock_targets=[(SHIP, "Ship", 3, 0, 0)],
           orders=[("o1", None, SHIP, "Ship", 2.0)], stock={SHIP: 1.0})

    result = engine.plan_production(ProductionConfig(component_overbuild=0.0, min_margin=0.0))

    inventory = result["inventory"][0]
    assert inventory.current_stock == 0.0
    assert inventory.reserved_for_orders == 1.0
    assert inventory.total_missing == 3.0
    assert _runs(result)[SHIP] == 4
    assert _order_share(result)[SHIP] == 1.0
    assert result["order_progress"][0]["covered"] == 1.0
    assert result["order_progress"][0]["remaining"] == 1.0


def test_fully_covered_order_plans_nothing_and_leaves_the_rest_to_the_target(monkeypatch, tenant):
    _setup(monkeypatch, stock_targets=[(SHIP, "Ship", 3, 0, 0)],
           orders=[("o1", None, SHIP, "Ship", 2.0)], stock={SHIP: 4.0})

    result = engine.plan_production(ProductionConfig(component_overbuild=0.0, min_margin=0.0))

    inventory = result["inventory"][0]
    assert (inventory.current_stock, inventory.reserved_for_orders, inventory.total_missing) == (2.0, 2.0, 1.0)
    assert _runs(result)[SHIP] == 1
    assert _order_share(result)[SHIP] == 0.0


def test_several_orders_for_one_item_claim_oldest_first(monkeypatch, tenant):
    _setup(monkeypatch, orders=[("old", None, SHIP, "Ship", 2.0), ("new", None, SHIP, "Ship", 3.0)],
           stock={SHIP: 3.0})

    result = engine.plan_production(ProductionConfig(component_overbuild=0.0, min_margin=0.0))

    claims = {row["order_id"]: (row["covered"], row["remaining"]) for row in result["order_progress"]}
    assert claims == {"old": (2.0, 0.0), "new": (1.0, 2.0)}
    assert _runs(result)[SHIP] == 2


def test_top_level_netting_counts_running_jobs(monkeypatch, tenant):
    # Real _current_stock: nothing in a hangar, a running job of 2 Ship runs.
    _setup(monkeypatch, orders=[("o1", None, SHIP, "Ship", 3.0)], stock=_REAL_STOCK)
    monkeypatch.setattr(engine, "_stock_at_location", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_esi_incoming_industry_qty",
                        lambda type_id: {"runs": 2 if type_id == SHIP else 0})
    monkeypatch.setattr(storage, "manual_incoming_qty", lambda type_id: 0.0)

    result = engine.plan_production(ProductionConfig(component_overbuild=0.0, min_margin=0.0))

    assert result["order_progress"][0]["covered"] == 2.0
    assert result["order_progress"][0]["remaining"] == 1.0
    assert _runs(result)[SHIP] == 1


def test_done_or_unticked_order_drops_out(monkeypatch, tenant):
    _setup(monkeypatch, orders=None)
    included = storage.create_special_order_with_items(
        "Included", True, [(SHIP, "Ship", 2.0)], include_in_production=True)
    storage.create_special_order_with_items(
        "Not included", False, [(OTHER, "Other", 5.0)], include_in_production=False)
    cfg = ProductionConfig(component_overbuild=0.0, min_margin=0.0)

    result = engine.plan_production(cfg)
    assert _runs(result) == {SHIP: 2, COMPONENT: 20}
    assert [row["order_id"] for row in result["order_progress"]] == [str(included)]

    storage.update_special_order(included, {"status": "done"})
    result = engine.plan_production(cfg)
    assert _runs(result) == {}
    assert result["order_progress"] == []


def test_order_demand_bypasses_margin_gate_but_stock_target_does_not(monkeypatch, tenant):
    _setup(monkeypatch, stock_targets=[(SHIP, "Ship", 2, 0, 0), (OTHER, "Other", 1, 0, 0)],
           orders=[("o1", None, SHIP, "Ship", 1.0)], margins={SHIP: 0.01, OTHER: 0.01})

    result = engine.plan_production(ProductionConfig(component_overbuild=0.0, min_margin=0.5))

    assert _runs(result) == {SHIP: 1, COMPONENT: 10}  # only the order's unit
    assert _order_share(result)[SHIP] == 1.0
    inventory = {row.type_id: row.total_missing for row in result["inventory"]}
    assert inventory == {SHIP: 2.0, OTHER: 1.0}  # real shortfall still reported


def test_end_to_end_with_synthetic_sde_and_real_storage(monkeypatch, tenant):
    # No engine mocks: synthetic SDE (Finished Widget A = 2 Component,
    # Component = 10 Tritanium), real stock target, real asset rows and an
    # order integrated through the actions. The order's own preview keeps
    # SF-1 (full ordered quantity, never netted against finished stock).
    from eve_trader.production import actions

    from . import special_order_fixtures as fx
    pg_helpers.wipe_tables("character_assets", "character_industry_jobs", "character_slots")
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    storage.replace_assets("character_assets", [
        (1, fx.FINISHED_A, 60003760, "Hangar", 3, 0, "Test Character"),
    ])
    storage.upsert_stock_target(fx.FINISHED_A, "Finished Widget A", backup_stock=4.0)
    order_id = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_A, "quantity": 2.0}, {"type_id": fx.FINISHED_B, "quantity": 5.0}],
        note="Customer")["order_id"]
    actions.do_update_special_order(order_id, include_in_production=True)
    cfg = fx.widget_cfg(component_overbuild=0.7)

    plan = actions.do_refresh_production(cfg)["plan"]

    inventory = {row.type_id: row for row in plan["inventory"]}
    assert inventory[fx.FINISHED_A].reserved_for_orders == 2.0
    assert inventory[fx.FINISHED_A].current_stock == 1.0
    assert inventory[fx.FINISHED_A].total_missing == 3.0
    runs = fx.runs(plan)
    # Tech I assumes ME10 (material_mult 0.9).
    # Order: 0 A (covered by stock), 5 B -> ceil(3 x 0.9 x 5) = 14 Component, unbuffered.
    # Stock target: 3 A -> ceil(2 x 0.9 x 3) = 6 Component + 0.7 x 2 x 0.9 x 4 base runs
    # = 11.04 -> 12.
    assert runs[fx.FINISHED_A] == 3
    assert runs[fx.FINISHED_B] == 5
    assert runs[fx.COMPONENT] == 14 + 12
    shares = {row.type_id: row.special_order_quantity for row in plan["build_list"]}
    assert shares == {fx.FINISHED_A: 0.0, fx.FINISHED_B: 5.0, fx.COMPONENT: 14.0}
    progress = {row["type_id"]: (row["covered"], row["remaining"]) for row in plan["order_progress"]}
    assert progress == {fx.FINISHED_A: (2.0, 0.0), fx.FINISHED_B: (0.0, 5.0)}

    preview = actions.do_compute_special_order(order_id, cfg=cfg)
    assert fx.runs(preview)[fx.FINISHED_A] == 2
    assert fx.runs(preview)[fx.FINISHED_B] == 5


def _patch_invention(monkeypatch, owned=0):
    chosen = InventionResult(
        t1_blueprint_type_id=200, t1_blueprint_name="Widget Blueprint",
        product_type_id=140, product_name="T2 Widget", decryptor="None",
        probability=0.5, output_runs=5.0,
        datacore_cost=0.0, decryptor_cost=0.0, relic_cost=0.0,
        total_attempt_cost=0.0, expected_cost_per_success=0.0, expected_cost_per_run=0.0,
        me=2, te=4, material_savings_per_run=0.0, net_cost_per_run=0.0,
    )
    monkeypatch.setattr(engine, "_tech_ii_mods", lambda *a, **k: (1.0, 1.0, "None", chosen))
    monkeypatch.setattr(storage, "available_blueprint_copies", lambda *a, **k: owned)
    monkeypatch.setattr(storage, "manual_bpc_runs", lambda *a, **k: 0)
    monkeypatch.setattr(storage, "esi_expected_invention_successes", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_merge_invention_inputs_into_buy_totals", lambda *a, **k: None)


def test_shared_t2_item_gets_one_invention_row_with_combined_target(monkeypatch, tenant):
    _setup(monkeypatch, stock_targets=[(T2_ITEM, "T2 Item", 2, 0, 0)],
           orders=[("o1", None, T2_ITEM, "T2 Item", 3.0), ("o2", None, T2_ONLY, "T2 Only", 7.0)])
    _patch_invention(monkeypatch, owned=4)

    result = engine.plan_production(ProductionConfig(component_overbuild=0.0, min_margin=0.0, bpc_inventory=4.0))

    rows = {row.type_id: row for row in result["invention_list"]}
    assert set(rows) == {T2_ITEM, T2_ONLY}
    shared = rows[T2_ITEM]
    # 4 x 2 buffered stock-target runs + 3 order runs, owned BPCs netted once.
    assert shared.bpc_target_runs == 11
    assert shared.special_order_runs == 3
    assert shared.runs_needed == 11 - 4
    # T1: 4 x ceil(ceil(2/5)/0.5) = 8, plus ceil(ceil(3/5)/0.5) = 2.
    assert shared.t1_bpc_target_runs == 10
    order_only = rows[T2_ONLY]
    # Multiplier 1.0: exactly the ordered runs, unbuffered.
    assert order_only.bpc_target_runs == 7
    assert order_only.special_order_runs == 7
    assert order_only.runs_needed == 7 - 4
    assert order_only.t1_bpc_target_runs == math.ceil(math.ceil(7 / 5) / 0.5)
