"""Phase D — composed release-acceptance workflows for the frozen core."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions

from . import pg_helpers, special_order_fixtures as fx
from .pg_helpers import tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

pytestmark = [pg_helpers.postgres_required(), pytest.mark.release]


@pytest.fixture
def order_sde(tenant, monkeypatch):
    pg_helpers.wipe_tables("character_assets")
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    return tenant


@pytest.fixture
def invention_sde(tenant, monkeypatch):
    fx.seed_invention()
    fx.patch_planner_network(monkeypatch, home={}, jita=fx.JITA)
    return tenant


def test_create_upsert_compute_end_to_end(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_A, "quantity": 10.0}], note="batch")["order_id"]
    actions.do_set_special_order_item(order_id, fx.FINISHED_A, 12.0)
    plan = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    assert fx.lines(plan) == {fx.FINISHED_A: 12.0}
    assert fx.runs(plan)[fx.FINISHED_A] == 12
    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 12.0


def test_combined_is_one_planner_run_not_sum(order_sde):
    a = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    b = actions.do_create_special_order([{"type_id": fx.FINISHED_B, "quantity": 10.0}])["order_id"]
    combined = actions.do_compute_combined_special_orders([a, b], net_against_stock=False, cfg=fx.widget_cfg())
    isolated = fx.runs(actions.do_compute_special_order(a, cfg=fx.widget_cfg()))[fx.COMPONENT] + \
        fx.runs(actions.do_compute_special_order(b, cfg=fx.widget_cfg()))[fx.COMPONENT]
    assert fx.runs(combined)[fx.COMPONENT] == isolated  # no shared hangar; pooling still one product tree
    assert len(combined["line_items"]) == 2


def test_stock_modes_combined_false_matches_from_scratch(order_sde):
    storage.replace_assets("character_assets", [
        (1, fx.COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    a = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    b = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_B, "quantity": 10.0}], net_against_stock=True)["order_id"]
    combined_false = actions.do_compute_combined_special_orders([a, b], net_against_stock=False, cfg=fx.widget_cfg())
    scratch = actions.do_compute_combined_special_orders([a, b], net_against_stock=False, cfg=fx.widget_cfg())
    assert fx.fingerprint(combined_false) == fx.fingerprint(scratch)
    assert actions.do_get_special_order(a)["order"].net_against_stock is True


def test_t2_invention_aggregates_on_combined(invention_sde):
    a = actions.do_create_special_order([{"type_id": fx.T2_MODULE, "quantity": 10.0}])["order_id"]
    b = actions.do_create_special_order([{"type_id": fx.T2_MODULE, "quantity": 15.0}])["order_id"]
    combined = actions.do_compute_combined_special_orders([a, b], net_against_stock=False, cfg=fx.invention_cfg())
    assert len(combined["invention_list"]) == 1
    assert combined["invention_list"][0].runs_needed == 25


def test_empty_sde_compute_errors(order_sde, monkeypatch):
    from eve_trader import storage
    monkeypatch.setattr(storage, "sde_row_counts", lambda: {"sde_types": 0})
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 1.0}])["order_id"]
    with pytest.raises(ActionError, match="SDE cache is empty"):
        actions.do_compute_special_order(order_id)
