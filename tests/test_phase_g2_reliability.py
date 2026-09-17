"""Phase G.2 — Edit → auto-recompute → reload → compute → combined is repeatable."""
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
    pg_helpers.wipe_tables("character_assets")
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    return tenant


def test_twenty_edit_recompute_cycles(order_sde):
    storage.replace_assets("character_assets", [
        (1, fx.COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    other = actions.do_create_special_order([{"type_id": fx.FINISHED_B, "quantity": 5.0}])["order_id"]
    hangar_before = storage.esi_stock_at_location(fx.COMPONENT, None)
    for i in range(20):
        wrapped = preview_refresh.set_item_and_preview(
            order_id, fx.FINISHED_A, 10.0 + i, cfg=fx.widget_cfg())
        reloaded = actions.do_get_special_order(order_id)
        compute = actions.do_compute_special_order(order_id, cfg=fx.widget_cfg())
        combined = actions.do_compute_combined_special_orders(
            [order_id, other], net_against_stock=False, cfg=fx.widget_cfg())
        assert reloaded["items"][0]["quantity"] == 10.0 + i
        assert fx.fingerprint(wrapped["plan"]) == fx.fingerprint(compute)
        assert fx.lines(combined)[fx.FINISHED_A] == 10.0 + i
        assert storage.esi_stock_at_location(fx.COMPONENT, None) == hangar_before
    events = [r["event"] for r in actions.do_list_special_order_events(order_id)["rows"]]
    assert events.count("item_set") == 20
    assert "created" in events
    assert events.count("deleted") == 0
