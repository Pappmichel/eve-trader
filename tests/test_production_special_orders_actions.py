import pytest

from eve_trader import storage
from eve_trader.production import actions
from eve_trader.production.actions import ActionError


def test_do_create_special_order_validates_and_creates_items(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Type{type_id}", 0.01, 1, 1, 0, None))
    monkeypatch.setattr(storage, "create_special_order", lambda note, net_against_stock: "order-1")
    upserted = []
    monkeypatch.setattr(storage, "upsert_special_order_item",
                         lambda order_id, type_id, type_name, quantity: upserted.append((order_id, type_id, type_name, quantity)))

    result = actions.do_create_special_order(
        [{"type_id": 12058, "quantity": 6000.0}, {"type_id": 638, "quantity": 10.0}],
        note="Customer X", net_against_stock=True,
    )

    assert result == {"order_id": "order-1"}
    assert upserted == [
        ("order-1", 12058, "Type12058", 6000.0),
        ("order-1", 638, "Type638", 10.0),
    ]


def test_do_create_special_order_rejects_empty_item_list():
    with pytest.raises(ActionError, match="at least one item"):
        actions.do_create_special_order([])


def test_do_create_special_order_rejects_non_positive_quantity(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Tritanium", 0.01, 1, 1, 0, None))
    monkeypatch.setattr(storage, "create_special_order", lambda *a: pytest.fail("must not reach storage"))

    with pytest.raises(ActionError, match="must be positive"):
        actions.do_create_special_order([{"type_id": 34, "quantity": 0}])


def test_do_create_special_order_rejects_unknown_type_id(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)
    monkeypatch.setattr(storage, "create_special_order", lambda *a: pytest.fail("must not reach storage"))

    with pytest.raises(ActionError, match="Unknown type_id"):
        actions.do_create_special_order([{"type_id": 999999999, "quantity": 1.0}])


def test_do_list_special_orders_includes_item_count(monkeypatch):
    monkeypatch.setattr(storage, "list_special_orders", lambda: [
        ("order-1", "Note A", True, "open", "2026-09-01T00:00:00"),
        ("order-2", None, False, "done", "2026-08-31T00:00:00"),
    ])
    items_by_order = {"order-1": [(34, "Tritanium", 100.0)], "order-2": []}
    monkeypatch.setattr(storage, "list_special_order_items", lambda order_id: items_by_order[order_id])

    result = actions.do_list_special_orders()

    assert [(o.order_id, o.item_count) for o in result] == [("order-1", 1), ("order-2", 0)]
    assert result[0].note == "Note A"
    assert result[0].net_against_stock is True
    assert result[1].status == "done"


def test_do_get_special_order_not_found_raises(monkeypatch):
    monkeypatch.setattr(storage, "get_special_order", lambda order_id: None)

    with pytest.raises(ActionError, match="not found"):
        actions.do_get_special_order("missing-order")


def test_do_update_special_order_rejects_unknown_status(monkeypatch):
    monkeypatch.setattr(storage, "get_special_order", lambda order_id: ("order-1", None, False, "open", None))
    monkeypatch.setattr(storage, "update_special_order", lambda *a: pytest.fail("must not reach storage"))

    with pytest.raises(ActionError, match="Unknown status"):
        actions.do_update_special_order("order-1", status="archived")


def test_do_update_special_order_mark_complete(monkeypatch):
    monkeypatch.setattr(storage, "get_special_order", lambda order_id: ("order-1", None, False, "open", None))
    captured = {}
    monkeypatch.setattr(storage, "update_special_order", lambda order_id, updates: captured.setdefault("updates", updates))
    monkeypatch.setattr(storage, "list_special_order_items", lambda order_id: [])

    actions.do_update_special_order("order-1", status="done")

    assert captured["updates"] == {"status": "done"}


def test_do_compute_special_order_raises_when_sde_cache_empty(monkeypatch):
    monkeypatch.setattr(storage, "sde_row_counts", lambda: {"sde_types": 0})

    with pytest.raises(ActionError, match="SDE cache is empty"):
        actions.do_compute_special_order("order-1")


def test_do_compute_special_order_not_found_raises(monkeypatch):
    monkeypatch.setattr(storage, "sde_row_counts", lambda: {"sde_types": 1})
    monkeypatch.setattr(storage, "get_special_order", lambda order_id: None)

    with pytest.raises(ActionError, match="not found"):
        actions.do_compute_special_order("missing-order")


def test_do_compute_special_order_calls_plan_special_order_with_stored_items(monkeypatch):
    monkeypatch.setattr(storage, "sde_row_counts", lambda: {"sde_types": 1})
    monkeypatch.setattr(storage, "get_special_order", lambda order_id: (order_id, "Note", True, "open", None))
    monkeypatch.setattr(storage, "list_special_order_items", lambda order_id: [(34, "Tritanium", 100.0)])
    captured = {}

    def _fake_plan(items, cfg, net_against_stock):
        captured["items"] = items
        captured["net_against_stock"] = net_against_stock
        return {"buy_list": [], "build_list": [], "invention_list": [], "line_items": [], "stock_overlap_warning": []}
    monkeypatch.setattr(actions, "plan_special_order", _fake_plan)

    actions.do_compute_special_order("order-1")

    assert captured["items"] == [(34, "Tritanium", 100.0)]
    assert captured["net_against_stock"] is True
