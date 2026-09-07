"""Phase C — adversarial audit of the online special-order planner path."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions, engine

from . import pg_helpers, special_order_fixtures as fx
from .pg_helpers import tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

pytestmark = [pg_helpers.postgres_required(), pytest.mark.release]

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def order_sde(tenant, monkeypatch):
    pg_helpers.wipe_tables("character_assets", "character_industry_jobs", "character_slots")
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    return tenant


@pytest.fixture
def invention_sde(tenant, monkeypatch):
    pg_helpers.wipe_tables("character_assets", "character_industry_jobs", "character_slots")
    fx.seed_invention()
    fx.patch_planner_network(monkeypatch, home={}, jita=fx.JITA)
    return tenant


def _invention_need_row_call_sites() -> list[tuple[str, int]]:
    hits = []
    prod = REPO / "eve_trader"
    for path in prod.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "InventionNeedRow":
                hits.append((str(path.relative_to(REPO)), node.lineno))
            elif isinstance(func, ast.Attribute) and func.attr == "InventionNeedRow":
                hits.append((str(path.relative_to(REPO)), node.lineno))
    return hits


def test_inv1_invention_need_row_is_only_constructed_in_helper():
    engine_src = (REPO / "eve_trader" / "production" / "engine.py").read_text(encoding="utf-8")
    tree = ast.parse(engine_src)
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_invention_need_row")
    hits = _invention_need_row_call_sites()
    assert hits, "expected InventionNeedRow(...) somewhere in production code"
    for rel, lineno in hits:
        assert rel == "eve_trader/production/engine.py", hits
        assert helper.lineno <= lineno <= helper.end_lineno, hits


def test_inv1_api_and_frontend_do_not_call_engine_planner():
    forbidden = ("plan_special_order", "_invention_need_row", "_expand_all", "plan_production")
    router = (REPO / "eve_trader" / "api" / "routers" / "production.py").read_text(encoding="utf-8")
    tree = ast.parse(router)
    called = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in forbidden:
                called.append(func.id)
            elif isinstance(func, ast.Attribute) and func.attr in forbidden:
                called.append(func.attr)
    assert called == [], f"router calls planner internals: {called}"
    frontend = (REPO / "frontend" / "src" / "pages" / "production" / "SpecialOrders.tsx").read_text(encoding="utf-8")
    for name in forbidden:
        assert name not in frontend, name
    assert "computeSpecialOrder" in frontend
    assert "computeCombinedSpecialOrders" in frontend


def test_set_item_does_not_invoke_the_planner(order_sde, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "plan_special_order", lambda *a, **k: calls.append((a, k)) or {})
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    calls.clear()
    actions.do_set_special_order_item(order_id, "Finished Widget A", 12.0)
    assert calls == []
    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 12.0


def test_scenario_a_same_product_pooled_once(order_sde):
    first = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 20.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders([first, second], net_against_stock=False, cfg=fx.widget_cfg())
    assert fx.lines(plan) == {fx.FINISHED_A: 30.0}
    assert len(plan["line_items"]) == 1
    assert fx.runs(plan)[fx.FINISHED_A] == 30
    assert fx.runs(plan)[fx.COMPONENT] == 54
    assert fx.buy_qty(plan)[fx.MINERAL] == pytest.approx(486.0)


def test_scenario_a_last_write_wins_would_plan_20_not_30(order_sde):
    first = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 20.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders([first, second], net_against_stock=False, cfg=fx.widget_cfg())
    assert fx.runs(plan)[fx.FINISHED_A] == 30


def test_scenario_b_shared_component_stock_claimed_once(order_sde):
    storage.replace_assets("character_assets", [
        (1, fx.COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_B, "quantity": 10.0}], net_against_stock=True)["order_id"]
    combined = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=fx.widget_cfg())
    isolated_a = actions.do_compute_special_order(first, cfg=fx.widget_cfg())
    isolated_b = actions.do_compute_special_order(second, cfg=fx.widget_cfg())
    scratch = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=fx.widget_cfg())
    assert fx.runs(isolated_a)[fx.COMPONENT] == 12
    assert fx.runs(isolated_b)[fx.COMPONENT] == 21
    assert fx.runs(scratch)[fx.COMPONENT] == 45
    assert fx.runs(combined)[fx.COMPONENT] == 39
    assert fx.runs(combined)[fx.FINISHED_A] == 10
    assert fx.runs(combined)[fx.FINISHED_B] == 10


def test_scenario_c_same_product_plus_component_stock(order_sde):
    storage.replace_assets("character_assets", [
        (1, fx.COMPONENT, 60003760, "Hangar", 10, 0, "Test Character"),
    ])
    first = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 20.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders([first, second], net_against_stock=True, cfg=fx.widget_cfg())
    scratch = actions.do_compute_combined_special_orders([first, second], net_against_stock=False, cfg=fx.widget_cfg())
    assert fx.lines(plan) == {fx.FINISHED_A: 30.0}
    assert fx.runs(plan)[fx.FINISHED_A] == 30
    assert fx.runs(scratch)[fx.COMPONENT] == 54
    assert fx.runs(plan)[fx.COMPONENT] == 44


def test_component_as_top_level_is_not_netted(order_sde):
    storage.replace_assets("character_assets", [
        (1, fx.COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    product = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    component = actions.do_create_special_order([{"type_id": fx.COMPONENT, "quantity": 10.0}])["order_id"]
    scratch = actions.do_compute_combined_special_orders(
        [product, component], net_against_stock=False, cfg=fx.widget_cfg())
    netted = actions.do_compute_combined_special_orders(
        [product, component], net_against_stock=True, cfg=fx.widget_cfg())
    assert fx.runs(scratch)[fx.COMPONENT] == 28
    assert fx.runs(netted)[fx.COMPONENT] == 22
    assert fx.runs(netted)[fx.FINISHED_A] == 10


@pytest.mark.parametrize("hangar,ordered", [(100.0, 100.0), (50.0, 100.0), (200.0, 100.0)])
def test_b1_top_level_never_netted_single_order(order_sde, hangar, ordered):
    storage.replace_assets("character_assets", [
        (1, fx.FINISHED_A, 60003760, "Hangar", hangar, 0, "Test Character"),
    ])
    order_id = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_A, "quantity": ordered}], net_against_stock=True)["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    assert fx.runs(plan)[fx.FINISHED_A] == ordered


def test_b1_combined_same_product_with_full_hangar_cover(order_sde):
    storage.replace_assets("character_assets", [
        (1, fx.FINISHED_A, 60003760, "Hangar", 100, 0, "Test Character"),
    ])
    first = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 50.0}])["order_id"]
    second = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 50.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders([first, second], net_against_stock=True, cfg=fx.widget_cfg())
    assert fx.lines(plan) == {fx.FINISHED_A: 100.0}
    assert fx.runs(plan)[fx.FINISHED_A] == 100


def test_net_against_stock_preview_is_isolated_and_repeatable(order_sde):
    storage.replace_assets("character_assets", [
        (1, fx.COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_B, "quantity": 10.0}], net_against_stock=False)["order_id"]
    before = fx.persistent_state()
    scratch_1 = fx.fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=fx.widget_cfg()))
    netted = fx.fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=fx.widget_cfg()))
    scratch_2 = fx.fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=fx.widget_cfg()))
    assert scratch_1 == scratch_2
    assert scratch_1 != netted
    assert fx.persistent_state() == before
    assert actions.do_get_special_order(first)["order"].net_against_stock is True
    assert actions.do_get_special_order(second)["order"].net_against_stock is False


def test_preview_purity_does_not_mutate_orders_or_stock(order_sde):
    storage.replace_assets("character_assets", [
        (1, fx.COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
        (2, fx.FINISHED_A, 60003760, "Hangar", 4, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_A, "quantity": 10.0}], note="keep")["order_id"]
    second = actions.do_create_special_order([{"type_id": fx.FINISHED_B, "quantity": 7.0}])["order_id"]
    before = fx.persistent_state()
    actions.do_compute_combined_special_orders([first, second], net_against_stock=True, cfg=fx.widget_cfg())
    actions.do_compute_special_order(first, cfg=fx.widget_cfg())
    assert fx.persistent_state() == before


def test_repeated_compute_and_combined_are_stable(order_sde):
    first = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order([{"type_id": fx.FINISHED_B, "quantity": 10.0}])["order_id"]
    singles = [fx.fingerprint(actions.do_compute_special_order(first, cfg=fx.widget_cfg())) for _ in range(3)]
    assert singles[0] == singles[1] == singles[2]
    combined = [
        fx.fingerprint(actions.do_compute_combined_special_orders(
            [first, second], net_against_stock=False, cfg=fx.widget_cfg()))
        for _ in range(3)
    ]
    assert combined[0] == combined[1] == combined[2]


def test_three_order_permutation_is_semantically_identical(order_sde):
    a = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    b = actions.do_create_special_order([{"type_id": fx.FINISHED_B, "quantity": 4.0}])["order_id"]
    c = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 6.0}])["order_id"]
    fingerprints = [
        fx.fingerprint(actions.do_compute_combined_special_orders(order, net_against_stock=False, cfg=fx.widget_cfg()))
        for order in ([a, b, c], [b, a, c], [c, b, a])
    ]
    assert fingerprints[0] == fingerprints[1] == fingerprints[2]


def test_combined_preview_is_a_single_planner_run(order_sde, monkeypatch):
    calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        calls.append(tuple((t, q) for t, _n, q in items))
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)
    monkeypatch.setattr(actions, "plan_special_order", _wrap)
    ids = [
        actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": float(n)}])["order_id"]
        for n in (1, 2, 3, 4, 5)
    ]
    actions.do_compute_combined_special_orders(ids, net_against_stock=False, cfg=fx.widget_cfg())
    assert len(calls) == 1
    assert calls[0] == ((fx.FINISHED_A, 15.0),)


def test_invalid_inputs_do_not_leave_half_written_state(order_sde):
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    before = fx.persistent_state()
    with pytest.raises(ActionError):
        actions.do_set_special_order_item(order_id, "Finished Widget A", 0)
    with pytest.raises(ActionError):
        actions.do_set_special_order_item(order_id, "Not A Real Item", 1.0)
    with pytest.raises(ActionError):
        actions.do_compute_combined_special_orders([], net_against_stock=False)
    with pytest.raises(ActionError):
        actions.do_compute_combined_special_orders([order_id, order_id], net_against_stock=False)
    with pytest.raises(ActionError):
        actions.do_compute_combined_special_orders([order_id, "00000000-0000-0000-0000-000000000099"], net_against_stock=False)
    assert fx.persistent_state() == before


def test_repeated_upsert_does_not_create_silent_duplicates(order_sde):
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    for qty in (11.0, 12.0, 13.0, 13.0):
        result = actions.do_set_special_order_item(order_id, "Finished Widget A", qty)
        assert len(result["items"]) == 1
        assert result["items"][0]["quantity"] == qty
    plan = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    assert fx.runs(plan)[fx.FINISHED_A] == 13


def test_missing_sde_errors_without_creating_state(order_sde, monkeypatch):
    before = fx.persistent_state()
    monkeypatch.setattr(storage, "sde_row_counts", lambda: {"sde_types": 0})
    with pytest.raises(ActionError, match="SDE cache is empty"):
        actions.do_compute_special_order("anything")
    with pytest.raises(ActionError, match="SDE cache is empty"):
        actions.do_compute_combined_special_orders(["anything"], net_against_stock=False)
    assert fx.persistent_state() == before


def test_missing_market_prices_fall_back_to_unpriced_buy(order_sde, monkeypatch):
    fx.patch_planner_network(monkeypatch, home={}, jita={})
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    before = fx.persistent_state()
    plan = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    assert fx.lines(plan) == {fx.FINISHED_A: 10.0}
    assert plan["build_list"] == []
    assert fx.buy_qty(plan) == {fx.FINISHED_A: 10.0}
    assert all(row.unit_price is None for row in plan["buy_list"])
    assert fx.persistent_state() == before


def test_combined_t2_invention_is_one_row_and_not_isolated_sum(invention_sde):
    first = actions.do_create_special_order([{"type_id": fx.T2_MODULE, "quantity": 40.0}])["order_id"]
    second = actions.do_create_special_order([{"type_id": fx.T2_MODULE, "quantity": 60.0}])["order_id"]
    isolated_a = actions.do_compute_special_order(first, cfg=fx.invention_cfg())
    isolated_b = actions.do_compute_special_order(second, cfg=fx.invention_cfg())
    combined = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=fx.invention_cfg())
    assert len(combined["invention_list"]) == 1
    assert combined["invention_list"][0].runs_needed == 100
    assert isolated_a["invention_list"][0].runs_needed + isolated_b["invention_list"][0].runs_needed == 100
