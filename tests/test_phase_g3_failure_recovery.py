"""Phase G.3 — failed mutations must not leave half-written special orders."""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.production import actions, preview_refresh

from . import pg_helpers, special_order_fixtures as fx
from .pg_helpers import tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

pytestmark = [pg_helpers.postgres_required(), pytest.mark.release]


@pytest.fixture
def order_sde(tenant, monkeypatch):
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    return tenant


def _events(order_id: str) -> list[str]:
    return [row["event"] for row in actions.do_list_special_order_events(order_id)["rows"]]


def test_invalid_create_payloads_leave_no_rows(order_sde):
    before_orders = storage.list_special_orders()
    before_events = storage.list_special_order_events()
    with pytest.raises(ActionError):
        actions.do_create_special_order([])
    with pytest.raises(ActionError, match="must be positive"):
        actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 0}])
    with pytest.raises(ActionError, match="Unknown type_id"):
        actions.do_create_special_order([{"type_id": 999999, "quantity": 1.0}])
    with pytest.raises(ActionError, match="Unknown type_id"):
        actions.do_create_special_order([
            {"type_id": fx.FINISHED_A, "quantity": 1.0},
            {"type_id": 999999, "quantity": 1.0},
        ])
    assert storage.list_special_orders() == before_orders
    assert storage.list_special_order_events() == before_events
    assert actions.do_audit_special_orders()["ok"] is True


def test_failed_set_and_remove_do_not_write_events(order_sde):
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 2.0}])["order_id"]
    before = actions.do_get_special_order(order_id)
    event_ids = {row["event_id"] for row in actions.do_list_special_order_events()["rows"]}
    with pytest.raises(ActionError, match="must be positive"):
        actions.do_set_special_order_item(order_id, "Finished Widget A", 0)
    with pytest.raises(ActionError, match="not found"):
        actions.do_set_special_order_item("00000000-0000-0000-0000-000000000099", "Finished Widget A", 1)
    with pytest.raises(ActionError, match="at least one item"):
        actions.do_remove_special_order_item(order_id, "Finished Widget A")
    with pytest.raises(ActionError, match="is not on"):
        actions.do_remove_special_order_item(order_id, "Finished Widget B")
    with pytest.raises(ActionError):
        preview_refresh.set_item_and_preview(order_id, "Finished Widget A", -1, cfg=fx.widget_cfg())
    assert actions.do_get_special_order(order_id)["items"] == before["items"]
    assert _events(order_id) == ["created"]
    assert {row["event_id"] for row in actions.do_list_special_order_events()["rows"]} == event_ids


def test_injected_failure_after_header_rolls_back(order_sde, monkeypatch):
    snapshot_orders = storage.list_special_orders()
    snapshot_items = storage.list_all_special_order_item_rows()
    snapshot_events = storage.list_special_order_events()
    fake_id = "99999999-9999-9999-9999-999999999901"

    def exploding(note, net_against_stock, items):
        with storage.batch_session():
            with storage.connect() as conn:
                conn.execute(
                    "INSERT INTO special_orders (order_id, note, net_against_stock) VALUES (?, ?, ?)",
                    (fake_id, note, net_against_stock),
                )
                raise RuntimeError("injected failure after header")
        return fake_id

    monkeypatch.setattr(storage, "create_special_order_with_items", exploding)
    with pytest.raises(RuntimeError, match="injected failure"):
        actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 1.0}])
    assert storage.list_special_orders() == snapshot_orders
    assert storage.list_all_special_order_item_rows() == snapshot_items
    assert storage.list_special_order_events() == snapshot_events
    assert storage.get_special_order(fake_id) is None
    assert actions.do_audit_special_orders()["ok"] is True


def test_reload_and_audit_after_failures(order_sde):
    healthy = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 4.0}])["order_id"]
    with pytest.raises(ActionError):
        actions.do_create_special_order([{"type_id": fx.FINISHED_B, "quantity": -2}])
    assert actions.do_get_special_order(healthy)["items"][0]["quantity"] == 4.0
    empty_id = storage.create_special_order("broken", False)
    result = actions.do_audit_special_orders()
    assert result["ok"] is False
    kinds = {(issue["kind"], issue["order_id"]) for issue in result["issues"]}
    assert ("empty_order", empty_id) in kinds
    actions.do_remove_special_order(healthy)
    with pytest.raises(ActionError, match="not found"):
        actions.do_get_special_order(healthy)
    assert _events(healthy)[-1] == "deleted"
    leftover = actions.do_audit_special_orders()
    assert any(issue["order_id"] == empty_id for issue in leftover["issues"])
    assert not any(issue["order_id"] == healthy for issue in leftover["issues"])
