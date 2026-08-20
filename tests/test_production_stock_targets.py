import pytest

from eve_trader import storage
from eve_trader.production import actions
from eve_trader.production.actions import ActionError


def test_do_update_stock_target_passes_through_partial_fields(monkeypatch):
    # GitHub issue #16: editing just one column in the table must not
    # require resending the other two - storage.upsert_stock_target already
    # treats None as "leave this field alone" (its own COALESCE-based
    # semantics), so do_update_stock_target just needs to pass None through
    # untouched for whichever fields weren't part of this edit.
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Tritanium", 0.01, 1, 1, 0, None))
    captured = {}
    monkeypatch.setattr(storage, "upsert_stock_target",
                         lambda *a: captured.setdefault("args", a))

    result = actions.do_update_stock_target(34, backup_stock=4242)

    assert captured["args"] == (34, "Tritanium", 4242, None, None)
    assert result == {"type_id": 34, "type_name": "Tritanium", "backup_stock": 4242,
                       "home_market_stock": None, "jita_market_stock": None}


def test_do_update_stock_target_rejects_negative_values(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, "Tritanium", 0.01, 1, 1, 0, None))
    monkeypatch.setattr(storage, "upsert_stock_target", lambda *a: pytest.fail("must not reach storage"))

    with pytest.raises(ActionError, match="cannot be negative"):
        actions.do_update_stock_target(34, backup_stock=-1)


def test_do_update_stock_target_unknown_type_id_raises(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)
    monkeypatch.setattr(storage, "upsert_stock_target", lambda *a: pytest.fail("must not reach storage"))

    with pytest.raises(ActionError, match="Unknown type_id"):
        actions.do_update_stock_target(999999999, backup_stock=1)
