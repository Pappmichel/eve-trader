"""Do not persist or promote a plan built without market quotes."""
from __future__ import annotations

import logging

import pytest

from eve_trader.actions import ActionError
from eve_trader.production import actions, engine
from eve_trader.production.config import ProductionConfig
from tests import pg_helpers
from tests.pg_helpers import tenant  # noqa: F401


def _ctx_without_prices(stock_targets):
    class _FakeCtx:
        def __init__(self, cfg, extra_type_ids=()):
            self.stock_targets = list(stock_targets)
            self.manual_stock = {}
            self.manual_overrides = {}
            self.selected_decryptors = {}
            self.home = {}
            self.jita = {}
            self.cost_indices = {}
            self.adjusted_prices = {tid: 1.0 for tid, *_ in stock_targets}
            self.adjusted_prices_available = True
            self.cost_indices_available = True
            self.prices_available = False
            self.price_data_stale_hours = None
            self._reaction_solve_cache = None

        @property
        def job_costs_reliable(self):
            return True
    return _FakeCtx


@pg_helpers.postgres_required()
def test_plan_production_skips_buy_list_save_without_prices(monkeypatch, tenant, caplog):
    saved = []
    stock_targets = [(34, "Tritanium", 1, 0, 0)]
    monkeypatch.setattr(engine, "_PlanContext", _ctx_without_prices(stock_targets))
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Input", None))
    monkeypatch.setattr(engine, "_current_stock", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_buy_or_build_decision", lambda *a, **k: "Buy")
    monkeypatch.setattr(engine.storage, "get_type_category", lambda type_id: 4)
    monkeypatch.setattr(engine.storage, "load_sde_category_names", lambda: {4: "Material"})
    monkeypatch.setattr(engine.storage, "save_latest_buy_list",
                        lambda rows: saved.append(list(rows)))
    monkeypatch.setattr(engine.storage, "list_integrated_special_order_items", lambda: [])

    with caplog.at_level(logging.WARNING):
        result = engine.plan_production(ProductionConfig())

    assert result["prices_available"] is False
    assert saved == []
    assert "Skipping save_latest_buy_list" in caplog.text


def test_do_refresh_production_raises_when_prices_unavailable(monkeypatch):
    monkeypatch.setattr(actions.storage, "sde_row_counts", lambda: {"sde_types": 1})
    monkeypatch.setattr(actions.storage, "load_stock_targets",
                        lambda: [(34, "Tritanium", 1, 0, 0)])
    monkeypatch.setattr(actions.storage, "list_integrated_special_order_items", lambda: [])
    monkeypatch.setattr(actions, "plan_production", lambda cfg: {
        "inventory": [], "buy_list": [], "build_list": [], "invention_list": [],
        "prices_available": False, "adjusted_prices_available": True,
        "cost_indices_available": True,
    })
    with pytest.raises(ActionError, match="Market quotes are unavailable"):
        actions.do_refresh_production(ProductionConfig())


def test_do_refresh_asset_plan_raises_when_prices_unavailable(monkeypatch):
    monkeypatch.setattr(actions.storage, "sde_row_counts", lambda: {"sde_types": 1})
    monkeypatch.setattr(actions.storage, "load_stock_targets",
                        lambda: [(34, "Tritanium", 1, 0, 0)])
    monkeypatch.setattr(actions.storage, "list_integrated_special_order_items", lambda: [])
    monkeypatch.setattr(actions, "plan_asset_optimized", lambda cfg: {
        "jobs": [], "prices_available": False,
        "adjusted_prices_available": True, "cost_indices_available": True,
    })
    with pytest.raises(ActionError, match="Market quotes are unavailable"):
        actions.do_refresh_asset_plan(ProductionConfig())


def test_refresh_router_does_not_overwrite_last_plan_on_action_error(monkeypatch):
    from eve_trader.api.routers import production as router
    from fastapi import HTTPException

    monkeypatch.setattr(router, "_tenant_key", lambda: "t-keep")
    router._last_plan["t-keep"] = {"buy_list": ["kept"], "stale": False}

    def _fail(**kwargs):
        raise ActionError(
            "Market quotes are unavailable right now; keeping the last good plan."
        )

    monkeypatch.setattr(router.actions, "do_refresh_production", _fail)
    with pytest.raises(HTTPException) as exc:
        router.refresh_production()
    assert exc.value.status_code == 400
    assert router._last_plan["t-keep"]["buy_list"] == ["kept"]
