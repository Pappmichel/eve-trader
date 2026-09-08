"""Phase F.2 — operator workflows through the FastAPI actions path."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.production import actions, preview_refresh

from . import pg_helpers, special_order_fixtures as fx
from .pg_helpers import tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

pytestmark = [pg_helpers.postgres_required(), pytest.mark.release]


@pytest.fixture
def order_sde(tenant, monkeypatch):
    pg_helpers.wipe_tables("character_assets", "character_industry_jobs", "character_slots")
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    return tenant


@pytest.fixture
def invention_sde(tenant, monkeypatch):
    fx.seed_invention()
    fx.patch_planner_network(monkeypatch, home={}, jita=fx.JITA)
    return tenant


def test_canonical_operator_path_survives_reload(order_sde):
    order_id = actions.do_create_special_order(
        [{"name": "Finished Widget A", "quantity": 10.0}], note="cli")["order_id"]
    preview_refresh.set_item_and_preview(order_id, fx.FINISHED_A, 12.0, cfg=fx.widget_cfg())
    actions.do_update_special_order(order_id, note="saved", status="done")
    reloaded = actions.do_get_special_order(order_id)
    assert reloaded["order"].status == "done"
    assert reloaded["order"].note == "saved"
    assert reloaded["items"][0]["quantity"] == 12.0
    other = actions.do_create_special_order([{"type_id": fx.FINISHED_B, "quantity": 5.0}])["order_id"]
    combined = actions.do_compute_combined_special_orders(
        [order_id, other], net_against_stock=False, cfg=fx.widget_cfg())
    assert fx.lines(combined)[fx.FINISHED_A] == 12.0
    final = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    assert fx.lines(final)[fx.FINISHED_A] == 12.0


def test_s2_shared_component_combined_not_isolated_sum(order_sde):
    storage.replace_assets("character_assets", [
        (1, fx.COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    a = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    b = actions.do_create_special_order([{"type_id": fx.FINISHED_B, "quantity": 10.0}])["order_id"]
    combined = actions.do_compute_combined_special_orders([a, b], net_against_stock=True, cfg=fx.widget_cfg())
    isolated = fx.runs(actions.do_compute_special_order(a, cfg=fx.widget_cfg()))[fx.COMPONENT] + \
        fx.runs(actions.do_compute_special_order(b, cfg=fx.widget_cfg()))[fx.COMPONENT]
    assert fx.runs(combined)[fx.COMPONENT] != isolated
    assert actions.do_get_special_order(a)["items"][0]["quantity"] == 10.0


def test_s3_t2_invention_preview(invention_sde):
    order_id = actions.do_create_special_order([{"type_id": fx.T2_MODULE, "quantity": 8.0}])["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=fx.invention_cfg())
    assert len(plan["invention_list"]) == 1
    assert plan["invention_list"][0].runs_needed == 8


def test_s5_missing_prices_unpriced_buy(order_sde, monkeypatch):
    fx.patch_planner_network(monkeypatch, home={}, jita={})
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 2.0}])["order_id"]
    before = fx.persistent_state()
    plan = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    assert plan["build_list"] == []
    assert fx.buy_qty(plan)[fx.FINISHED_A] == 2.0
    assert fx.persistent_state() == before
