"""Phase E.3 — create-by-name and list-filter UX around the frozen core."""
from __future__ import annotations

import pytest

from eve_trader.production import actions

from . import pg_helpers, special_order_fixtures as fx
from .pg_helpers import tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

pytestmark = [pg_helpers.postgres_required(), pytest.mark.release]


@pytest.fixture
def order_sde(tenant, monkeypatch):
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    return tenant


def test_create_by_name(order_sde):
    order_id = actions.do_create_special_order(
        [{"name": "Finished Widget A", "quantity": 3.0}])["order_id"]
    items = actions.do_get_special_order(order_id)["items"]
    assert items[0]["type_id"] == fx.FINISHED_A
    assert items[0]["quantity"] == 3.0


def test_create_by_type_id_or_name_field(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id_or_name": "Finished Widget B", "quantity": 2.0}])["order_id"]
    assert actions.do_get_special_order(order_id)["items"][0]["type_id"] == fx.FINISHED_B


def test_buy_total_is_display_only(order_sde):
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 1.0}])["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
    total = sum(row.total_price or 0 for row in plan["buy_list"])
    assert total >= 0
    assert "buy_total" not in plan
