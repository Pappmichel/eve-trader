"""Phase E.2 — special-order persistence / integrity (core planner frozen)."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions, engine

from . import pg_helpers, special_order_fixtures as fx
from .pg_helpers import tenant, tenant_pair  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

pytestmark = [pg_helpers.postgres_required(), pytest.mark.release]


@pytest.fixture
def order_sde(tenant, monkeypatch):
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home={}, jita={})
    return tenant


def test_create_pools_duplicate_type_ids(order_sde):
    order_id = actions.do_create_special_order([
        {"type_id": fx.FINISHED_A, "quantity": 10.0},
        {"type_id": fx.FINISHED_A, "quantity": 5.0},
        {"type_id": fx.FINISHED_B, "quantity": 2.0},
    ])["order_id"]
    by_id = {row["type_id"]: row["quantity"] for row in actions.do_get_special_order(order_id)["items"]}
    assert by_id == {fx.FINISHED_A: 15.0, fx.FINISHED_B: 2.0}
    assert actions.do_create_special_order([
        {"type_id": fx.FINISHED_A, "quantity": 1.0}])["item_count"] == 1


def test_create_is_one_transaction(order_sde):
    before = {row.order_id for row in actions.do_list_special_orders()}
    with pytest.raises(ActionError, match="Unknown type_id"):
        actions.do_create_special_order([
            {"type_id": fx.FINISHED_A, "quantity": 1.0},
            {"type_id": 999999, "quantity": 1.0},
        ])
    assert {row.order_id for row in actions.do_list_special_orders()} == before
    assert storage.list_special_order_events() == []


def test_list_special_orders_status_filter(order_sde):
    open_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 1.0}])["order_id"]
    done_id = actions.do_create_special_order([{"type_id": fx.FINISHED_B, "quantity": 1.0}])["order_id"]
    actions.do_update_special_order(done_id, status="done")
    assert {row.order_id for row in actions.do_list_special_orders(status="open")} == {open_id}
    assert {row.order_id for row in actions.do_list_special_orders(status="done")} == {done_id}
    with pytest.raises(ActionError, match="Unknown status"):
        actions.do_list_special_orders(status="bogus")


def test_update_net_against_stock_does_not_touch_planner(order_sde, monkeypatch):
    order_id = actions.do_create_special_order(
        [{"type_id": fx.FINISHED_A, "quantity": 4.0}], net_against_stock=False)["order_id"]
    calls = []
    monkeypatch.setattr(engine, "plan_special_order", lambda *a, **k: calls.append(1) or {})
    monkeypatch.setattr(actions, "plan_special_order", lambda *a, **k: calls.append(1) or {})
    actions.do_update_special_order(order_id, net_against_stock=True)
    assert calls == []
    assert actions.do_get_special_order(order_id)["order"].net_against_stock is True
    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 4.0


def test_events_survive_delete(order_sde):
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 1.0}])["order_id"]
    actions.do_set_special_order_item(order_id, fx.FINISHED_B, 2.0)
    actions.do_remove_special_order_item(order_id, fx.FINISHED_B)
    actions.do_update_special_order(order_id, note="n")
    actions.do_remove_special_order(order_id)
    events = [row["event"] for row in actions.do_list_special_order_events(order_id)["rows"]]
    assert events == ["created", "item_set", "item_removed", "updated", "deleted"]
    with pytest.raises(ActionError, match="not found"):
        actions.do_get_special_order(order_id)


def test_audit_reports_empty_and_does_not_call_planner(order_sde, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "plan_special_order", lambda *a, **k: calls.append(1) or {})
    healthy = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 1.0}])["order_id"]
    empty_id = storage.create_special_order("broken", False)
    result = actions.do_audit_special_orders()
    assert result["ok"] is False
    assert any(i["kind"] == "empty_order" and i["order_id"] == empty_id for i in result["issues"])
    assert not any(i["order_id"] == healthy for i in result["issues"])
    assert calls == []


def test_events_are_tenant_isolated(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        fx.seed_widgets()
        oid = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 1.0}])["order_id"]
        events_a = actions.do_list_special_order_events()["rows"]
    with storage.tenant_context(tenant_b):
        assert actions.do_list_special_order_events()["rows"] == []
        assert actions.do_list_special_orders() == []
    with storage.tenant_context(tenant_a):
        assert [r["order_id"] for r in events_a] == [oid]
