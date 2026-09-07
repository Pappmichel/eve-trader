"""Phase E.4 — auto-recompute wrapper stays outside Set/Remove/Compute."""
from __future__ import annotations

import pytest

from eve_trader.actions import ActionError
from eve_trader.production import actions, engine, preview_refresh

from . import pg_helpers, special_order_fixtures as fx
from .pg_helpers import tenant  # noqa: F401
from .special_order_fixtures import _apply_special_orders_schema  # noqa: F401

pytestmark = [pg_helpers.postgres_required(), pytest.mark.release]


@pytest.fixture
def order_sde(tenant, monkeypatch):
    fx.seed_widgets()
    fx.patch_planner_network(monkeypatch, home=fx.HOME, jita={})
    return tenant


def test_wrapper_computes_after_set(order_sde):
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    result = preview_refresh.set_item_and_preview(order_id, fx.FINISHED_A, 12.0, cfg=fx.widget_cfg())
    assert result["items"][0]["quantity"] == 12.0
    assert fx.runs(result["plan"])[fx.FINISHED_A] == 12
    assert "plan" in result


def test_bare_set_does_not_compute(order_sde, monkeypatch):
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    calls = []
    monkeypatch.setattr(engine, "plan_special_order", lambda *a, **k: calls.append(1) or {})
    monkeypatch.setattr(actions, "plan_special_order", lambda *a, **k: calls.append(1) or {})
    actions.do_set_special_order_item(order_id, fx.FINISHED_A, 11.0)
    assert calls == []


def test_failed_set_skips_compute(order_sde, monkeypatch):
    order_id = actions.do_create_special_order([{"type_id": fx.FINISHED_A, "quantity": 10.0}])["order_id"]
    calls = []
    real = actions.do_compute_special_order

    def _wrap(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(actions, "do_compute_special_order", _wrap)
    with pytest.raises(ActionError):
        preview_refresh.set_item_and_preview(order_id, fx.FINISHED_A, 0, cfg=fx.widget_cfg())
    assert calls == []


def test_remove_and_preview(order_sde):
    order_id = actions.do_create_special_order([
        {"type_id": fx.FINISHED_A, "quantity": 10.0},
        {"type_id": fx.FINISHED_B, "quantity": 5.0},
    ])["order_id"]
    result = preview_refresh.remove_item_and_preview(order_id, fx.FINISHED_B, cfg=fx.widget_cfg())
    assert [i["type_id"] for i in result["items"]] == [fx.FINISHED_A]
    assert fx.FINISHED_A in fx.runs(result["plan"])


def test_preview_refresh_does_not_import_engine():
    import ast
    from pathlib import Path
    src = Path(preview_refresh.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "engine" in node.module:
            imported.append(node.module)
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names if "engine" in a.name)
    assert imported == []
