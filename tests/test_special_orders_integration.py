"""Special orders in the regular build workflow, phase 2: plan_production
(docs/SPECIAL_ORDERS_INTEGRATION_PLAN.md D3-D6, D8, D10, D12, D13), and
phase 3: plan_asset_optimized (D4, D7, D16).

Synthetic BOM, everything Tech I with product_qty 1 and material_mult 1:
- SHIP (10) needs 10x COMPONENT per unit.
- OTHER (30) needs 10x COMPONENT per unit.
- COMPONENT (20) needs 5x MINERAL per unit.
- MINERAL (999) has no blueprint (always bought).
T2_ITEM (40) / T2_ONLY (50) are Tech II with no materials, for the
invention-row tests. FAST_A (60) / FAST_B (70) have no materials (always
ready), for the slot tests.
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
FAST_A, FAST_B = 60, 70

_CLASSIFY = {
    SHIP: ("Tech I", (110, 1, 1.0)),
    COMPONENT: ("Tech I", (120, 1, 1.0)),
    OTHER: ("Tech I", (130, 1, 1.0)),
    T2_ITEM: ("Tech II", (140, 1, 1.0)),
    T2_ONLY: ("Tech II", (150, 1, 1.0)),
    FAST_A: ("Tech I", (160, 1, 1.0)),
    FAST_B: ("Tech I", (170, 1, 1.0)),
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


# ------------------------------------------------- phase 3: plan_asset_optimized
def _asset_setup(monkeypatch, stock_targets=(), orders=None, stock=None, on_hand=None,
                 free_slots=0, times=None):
    """_setup plus what plan_asset_optimized reads. stock: {type_id:
    _current_stock}; on_hand: {type_id: _stock_on_hand}, defaults to stock.
    times: seconds per run by blueprint id (default 100). Every buildable
    type is an Advanced Component (slot-recommendation category)."""
    stock = dict(stock or {})
    on_hand = stock if on_hand is None else on_hand
    _setup(monkeypatch, stock_targets=stock_targets, orders=orders, stock=stock)
    monkeypatch.setattr(engine, "_buy_or_build_decision",
                        lambda type_id, cfg, home, jita, overrides, memo, bp, depth=0:
                        "Buy" if bp is None else "Build")
    monkeypatch.setattr(engine, "_stock_on_hand", lambda type_id, *a, **k: on_hand.get(type_id, 0.0))
    monkeypatch.setattr(storage, "get_sde_type",
                        lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))
    times = times or {}
    monkeypatch.setattr(storage, "get_blueprint_time",
                        lambda blueprint_id, activity_id: times.get(blueprint_id, 100.0))
    monkeypatch.setattr(engine, "job_category",
                        lambda type_id: "Advanced Components" if type_id in _CLASSIFY else None)
    monkeypatch.setattr(engine, "_free_slots_by_category", lambda: {"Manufacturing": free_slots})


def _jobs(result):
    return {job.type_id: job for job in result["jobs"]}


def _asset_cfg(**overrides):
    return ProductionConfig(**{"component_overbuild": 0.0, "min_margin": 0.0, **overrides})


def test_asset_plan_without_orders_is_unchanged(monkeypatch, tenant):
    _asset_setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0)], orders=[])

    result = engine.plan_asset_optimized(_asset_cfg(component_overbuild=0.7))

    jobs = _jobs(result)
    assert {t: j.job_runs for t, j in jobs.items()} == {OTHER: 1, COMPONENT: 17}
    assert all((j.special_order_runs, j.special_order_ready_runs, j.special_order_ids) == (0, 0, [])
               for j in jobs.values())
    assert result["order_progress"] == []


def test_asset_plan_reserves_a_dual_role_stock_target_like_plan_production(monkeypatch, tenant):
    # COMPONENT is a stock target (backup 10, 10 in stock) and a material of
    # the ordered SHIP. Its 10 units fill its own target in both lists, so
    # the order's 10 Component are built - the asset list used to hand the
    # same 10 units to the order as well and planned no Component job.
    _asset_setup(monkeypatch, stock_targets=[(COMPONENT, "Component", 10, 0, 0)],
                 orders=[("o1", None, SHIP, "Ship", 1.0)], stock={COMPONENT: 10.0})
    cfg = _asset_cfg()

    jobs = _jobs(engine.plan_asset_optimized(cfg))

    assert {t: j.job_runs for t, j in jobs.items()} == {SHIP: 1, COMPONENT: 10}
    # Readiness still sees the physical units: the Ship can start now.
    assert jobs[SHIP].runs_ready_now == 1
    assert _runs(engine.plan_production(cfg)) == {SHIP: 1, COMPONENT: 10}


def test_asset_plan_order_pass_is_unbuffered_and_attributed(monkeypatch, tenant):
    _asset_setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0)],
                 orders=[("o1", "Customer", SHIP, "Ship", 1.0)])
    cfg = _asset_cfg(component_overbuild=0.7)

    jobs = _jobs(engine.plan_asset_optimized(cfg))

    # Stock target: 10 Component + 7 buffer; order: exactly 10, unbuffered.
    assert {t: j.job_runs for t, j in jobs.items()} == {OTHER: 1, SHIP: 1, COMPONENT: 27}
    assert (jobs[SHIP].special_order_runs, jobs[COMPONENT].special_order_runs) == (1, 10)
    assert jobs[OTHER].special_order_runs == 0
    # The order's demand reaches the Component job too, the stock target's not.
    assert (jobs[SHIP].special_order_ids, jobs[SHIP].special_order_notes) == (["o1"], ["Customer"])
    assert jobs[COMPONENT].special_order_ids == ["o1"]
    assert jobs[OTHER].special_order_ids == []
    # Both lists agree on how much to build.
    assert _runs(engine.plan_production(cfg)) == {t: j.job_runs for t, j in jobs.items()}


def test_asset_plan_readiness_goes_to_order_claims_first(monkeypatch, tenant):
    # OTHER (stock target, claims 10 Component) and SHIP (order of 2, claims
    # 20) compete for 10 Component on hand. One shared pass would give it to
    # the smaller claim (OTHER); the order pass runs first and takes it.
    _asset_setup(monkeypatch, stock_targets=[(OTHER, "Other", 1, 0, 0)],
                 orders=[("o1", None, SHIP, "Ship", 2.0)], stock={COMPONENT: 10.0})

    jobs = _jobs(engine.plan_asset_optimized(_asset_cfg()))

    assert (jobs[SHIP].job_runs, jobs[SHIP].runs_ready_now, jobs[SHIP].special_order_ready_runs) == (2, 1, 1)
    assert jobs[OTHER].runs_ready_now == 0
    # 10 on hand went to the order; each pass builds its own 10 more.
    assert (jobs[COMPONENT].job_runs, jobs[COMPONENT].special_order_runs) == (20, 10)


def test_asset_plan_order_and_stock_target_for_one_item_merge(monkeypatch, tenant):
    # Ship is ordered (1) and a stock target (2). 10 Component on hand cover
    # one run - the order's.
    _asset_setup(monkeypatch, stock_targets=[(SHIP, "Ship", 2, 0, 0)],
                 orders=[("o1", "Customer", SHIP, "Ship", 1.0)], stock={COMPONENT: 10.0})

    result = engine.plan_asset_optimized(_asset_cfg())

    ship = _jobs(result)[SHIP]
    assert (ship.job_runs, ship.runs_ready_now) == (3, 1)
    assert (ship.special_order_runs, ship.special_order_ready_runs) == (1, 1)
    assert ship.special_order_ids == ["o1"]
    [progress] = result["order_progress"]
    assert (progress["remaining"], progress["ready_runs"], progress["missing"]) == (1.0, 1, 0.0)


def test_asset_plan_claimed_hangar_units_never_make_another_job_ready(monkeypatch, tenant):
    # The order claims all 10 Component in the hangar, so the Ship stock
    # target needs 10 newly built ones and is not Ready Now.
    _asset_setup(monkeypatch, stock_targets=[(SHIP, "Ship", 1, 0, 0)],
                 orders=[("o1", None, COMPONENT, "Component", 10.0)], stock={COMPONENT: 10.0})

    result = engine.plan_asset_optimized(_asset_cfg())

    jobs = _jobs(result)
    assert jobs[SHIP].runs_ready_now == 0
    assert (jobs[COMPONENT].job_runs, jobs[COMPONENT].special_order_runs) == (10, 0)
    [progress] = result["order_progress"]
    assert (progress["hangar_covered"], progress["ready_to_deliver"]) == (10.0, True)


def test_asset_plan_order_progress_splits_hangar_running_jobs_and_missing(monkeypatch, tenant):
    # Ship: 3 current, only 1 physically on hand (2 in running jobs).
    # o1 (oldest): 2 Ship -> 1 hangar + 1 running job, nothing to plan.
    # o2: 2 Ship + 5 Mineral -> last running-job Ship, 1 run to plan (ready:
    #     10 Component on hand); Mineral is bought, so all 5 are missing.
    # o3: 1 Other, in the hangar -> ready to deliver.
    _asset_setup(monkeypatch, orders=[
        ("o1", "First", SHIP, "Ship", 2.0),
        ("o2", "Second", SHIP, "Ship", 2.0),
        ("o2", "Second", MINERAL, "Mineral", 5.0),
        ("o3", "Third", OTHER, "Other", 1.0),
    ], stock={SHIP: 3.0, OTHER: 1.0, COMPONENT: 10.0}, on_hand={SHIP: 1.0, OTHER: 1.0, COMPONENT: 10.0})

    result = engine.plan_asset_optimized(_asset_cfg())

    rows = [(r["order_id"], r["type_id"], r["ordered"], r["hangar_covered"], r["job_covered"],
             r["ready_runs"], r["missing"], r["ready_to_deliver"]) for r in result["order_progress"]]
    assert rows == [
        ("o1", SHIP, 2.0, 1.0, 1.0, 0, 0.0, False),
        ("o2", SHIP, 2.0, 0.0, 1.0, 1, 0.0, False),
        ("o2", MINERAL, 5.0, 0.0, 0.0, 0, 5.0, False),
        ("o3", OTHER, 1.0, 1.0, 0.0, 0, 0.0, True),
    ]
    ship = _jobs(result)[SHIP]
    assert (ship.job_runs, ship.special_order_ready_runs, ship.special_order_ids) == (1, 1, ["o2"])


def test_asset_plan_days_target_queues_every_order_run_first(monkeypatch, tenant):
    # One-day runs, 5 free slots, 1-day target: FAST_A has 4 order runs,
    # FAST_B 20 stock-target runs. The plain fastest split would give FAST_A
    # one slot (3 order runs waiting); the order runs get the 4 slots they
    # need, and only stock-target runs wait.
    day = 86400.0
    _asset_setup(monkeypatch, stock_targets=[(FAST_B, "B", 20, 0, 0)],
                 orders=[("o1", None, FAST_A, "A", 4.0)], free_slots=5, times={160: day, 170: day})

    jobs = _jobs(engine.plan_asset_optimized(_asset_cfg(asset_plan_slot_days_target=1.0)))

    a, b = jobs[FAST_A], jobs[FAST_B]
    assert (a.special_order_ready_runs, b.runs_ready_now) == (4, 20)
    assert (a.recommended_slots, a.runs_per_slot) == (4, 1)
    assert (b.recommended_slots, b.runs_per_slot) == (1, 1)


def test_asset_plan_fewer_slots_than_jobs_starts_order_jobs_first(monkeypatch, tenant):
    # One free slot: FAST_B is longer (would start first by job time), but
    # FAST_A serves an order.
    _asset_setup(monkeypatch, stock_targets=[(FAST_B, "B", 20, 0, 0)],
                 orders=[("o1", None, FAST_A, "A", 1.0)], free_slots=1)

    jobs = _jobs(engine.plan_asset_optimized(_asset_cfg()))

    assert (jobs[FAST_A].recommended_slots, jobs[FAST_B].recommended_slots) == (1, 0)


def test_allocate_slots_order_runs_fill_before_stock_target_runs_wait():
    # Claims: (type_id, ready_seconds, runs_ready_now, unlock, coverage,
    # order_ready_runs). Job 1: 8 one-day runs, 2 of them for orders; job 2:
    # 20 stock-target runs. 4 slots, 1-day target: job 1 first gets the 2
    # slots its order runs need, job 2 its one slot, the last slot goes to
    # the longest per-slot job (job 2). Job 1's waiting runs are all
    # stock-target runs.
    day = 86400.0
    claims = [(1, 8 * day, 8, 0.0, None, 2), (2, 20 * day, 20, 0.0, None, 0)]
    assert engine._allocate_slots_min_makespan(claims, 4, target_seconds=day) == {1: 2, 2: 2}
    # Without a target, order jobs just take part in the normal split.
    assert engine._allocate_slots_min_makespan(claims, 4) == {1: 1, 2: 3}
    # The order top-up never pushes a slot past the target (one run each).
    assert engine._runs_per_slot(8, 2, day, day) == 1


def test_allocate_slots_fewer_slots_than_jobs_order_jobs_beat_unlock_time():
    claims = [(1, 900.0, 5, 50.0, 0.1), (2, 10.0, 5, 0.0, 0.9, 3), (3, 500.0, 5, 0.0, 0.5)]
    assert engine._allocate_slots_min_makespan(claims, 2) == {1: 1, 2: 1, 3: 0}


def test_asset_plan_drops_done_and_unticked_orders(monkeypatch, tenant):
    _asset_setup(monkeypatch, orders=None)
    included = storage.create_special_order_with_items(
        "Included", True, [(SHIP, "Ship", 2.0)], include_in_production=True)
    storage.create_special_order_with_items(
        "Not included", False, [(OTHER, "Other", 5.0)], include_in_production=False)

    result = engine.plan_asset_optimized(_asset_cfg())
    jobs = _jobs(result)
    assert {t: j.job_runs for t, j in jobs.items()} == {SHIP: 2, COMPONENT: 20}
    assert jobs[SHIP].special_order_ids == [str(included)]
    assert [row["order_id"] for row in result["order_progress"]] == [str(included)]

    storage.update_special_order(included, {"status": "done"})
    result = engine.plan_asset_optimized(_asset_cfg())
    assert result["jobs"] == []
    assert result["order_progress"] == []


def test_refresh_asset_plan_runs_with_integrated_orders_and_no_stock_targets(monkeypatch, tenant):
    from eve_trader.actions import ActionError
    from eve_trader.production import actions

    _asset_setup(monkeypatch, orders=[])
    monkeypatch.setattr(storage, "sde_row_counts", lambda: {"sde_types": 1})
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    with pytest.raises(ActionError, match="No stock targets"):
        actions.do_refresh_asset_plan(_asset_cfg())

    monkeypatch.setattr(storage, "list_integrated_special_order_items",
                        lambda: [("o1", None, SHIP, "Ship", 1.0)])
    result = actions.do_refresh_asset_plan(_asset_cfg())
    assert result["jobs"] == 2
    assert result["plan"]["order_progress"][0]["order_id"] == "o1"


def test_asset_plan_end_to_end_with_synthetic_sde_and_real_storage(monkeypatch, tenant):
    # Same data as the plan_production end-to-end test: 3 Finished Widget A
    # in the hangar, stock target 4 A, an integrated order for 2 A + 5 B.
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

    plan = actions.do_refresh_asset_plan(cfg)["plan"]

    jobs = _jobs(plan)
    # Both build lists agree on how much to build.
    assert {t: j.job_runs for t, j in jobs.items()} == fx.runs(actions.do_refresh_production(cfg)["plan"])
    assert (jobs[fx.FINISHED_A].job_runs, jobs[fx.FINISHED_A].special_order_runs) == (3, 0)
    assert (jobs[fx.FINISHED_B].job_runs, jobs[fx.FINISHED_B].special_order_runs) == (5, 5)
    assert jobs[fx.COMPONENT].special_order_runs == 14
    assert jobs[fx.FINISHED_B].special_order_notes == ["Customer"]
    progress = {row["type_id"]: (row["hangar_covered"], row["job_covered"], row["missing"], row["ready_to_deliver"])
                for row in plan["order_progress"]}
    # Nothing to build B from yet: all 5 missing; A is in the hangar but B isn't.
    assert progress == {fx.FINISHED_A: (2.0, 0.0, 0.0, False), fx.FINISHED_B: (0.0, 0.0, 5.0, False)}
