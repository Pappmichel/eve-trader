"""Regression: during a price / job-cost outage, do not freeze bad values."""
from __future__ import annotations

import logging

import pytest

from eve_trader.api.routers import production as production_router
from eve_trader.production import dynamic_targets, engine
from eve_trader.production.config import ProductionConfig
from tests import pg_helpers
from tests.pg_helpers import tenant  # noqa: F401


class _UnreliableCtx:
    stock_targets = [(34, "Tritanium", 0, 10, 0)]
    job_costs_reliable = False
    prices_available = True
    adjusted_prices_available = False
    cost_indices_available = True


def test_ensure_dynamic_targets_frozen_skips_when_unreliable(caplog):
    cfg = ProductionConfig(dynamic_targets_enabled=True)
    with caplog.at_level(logging.WARNING):
        assert dynamic_targets.ensure_dynamic_targets_frozen(
            cfg, _UnreliableCtx(), {}, {}, {},
        ) == 0
    assert "skipping freeze" in caplog.text


def test_recalculate_dynamic_targets_checks_ctx_before_invalidate(monkeypatch):
    cfg = ProductionConfig(dynamic_targets_enabled=True, home_location_id=1)
    invalidated = []
    monkeypatch.setattr(dynamic_targets.storage, "load_stock_targets",
                        lambda: [(34, "Tritanium", 0, 10, 0)])
    monkeypatch.setattr(dynamic_targets.storage, "load_stock_target_modes",
                        lambda: {34: "dynamic"})
    monkeypatch.setattr(dynamic_targets, "dynamic_specs",
                        lambda rows, modes: [object()])
    monkeypatch.setattr(dynamic_targets, "invalidate_today",
                        lambda *a, **k: invalidated.append(1))
    monkeypatch.setattr(dynamic_targets, "_make_context", lambda cfg: _UnreliableCtx())

    with pytest.raises(ValueError, match="unavailable"):
        dynamic_targets.recalculate_dynamic_targets(cfg)
    assert invalidated == []


def test_peek_cached_unit_build_cost_skips_unreliable_plan(monkeypatch):
    class Job:
        type_id = 34
        unit_build_cost = 12.0

    monkeypatch.setattr(production_router, "_tenant_key", lambda: "t1")
    production_router._last_plan["t1"] = {
        "build_list": [Job()],
        "adjusted_prices_available": False,
        "cost_indices_available": True,
    }
    production_router._last_asset_plan.pop("t1", None)
    assert production_router.peek_cached_unit_build_cost(34) is None

    production_router._last_plan["t1"]["adjusted_prices_available"] = True
    assert production_router.peek_cached_unit_build_cost(34) == (12.0, "build_plan")


def test_planner_unit_values_raises_when_job_costs_unreliable(monkeypatch):
    class Ctx:
        home = {}
        jita = {}
        selected_decryptors = {}
        cost_indices = {}
        adjusted_prices = {}
        adjusted_prices_available = False
        cost_indices_available = True
        job_costs_reliable = False

    monkeypatch.setattr(engine, "_PlanContext", lambda cfg, extra_type_ids=(): Ctx())
    with pytest.raises(engine.PlannerValuationError):
        engine.planner_unit_values.__wrapped__([34])


@pg_helpers.postgres_required()
def test_discover_does_not_cache_when_ctx_unreliable(monkeypatch, tenant):
    engine.invalidate_discover_cache(all_tenants=True)

    class Unreliable:
        def __init__(self, cfg, extra_type_ids=()):
            self.stock_targets = []
            self.manual_stock = {}
            self.manual_overrides = {}
            self.selected_decryptors = {}
            self.home = {}
            self.jita = {}
            self.cost_indices = {}
            self.adjusted_prices = {}
            self.adjusted_prices_available = False
            self.cost_indices_available = True
            self.prices_available = True
            self.price_data_stale_hours = None

        @property
        def job_costs_reliable(self):
            return False

    monkeypatch.setattr(engine, "_PlanContext", Unreliable)
    monkeypatch.setattr(engine.storage, "load_sde_types_with_market_group", lambda: [])
    results = engine.discover_build_candidates(ProductionConfig(), client=None)
    assert results == []
    tenant_id = engine.storage.get_current_tenant()
    assert tenant_id not in engine._discover_cache


def test_bpc_run_values_skip_cache_when_fee_rates_missing(monkeypatch):
    from eve_trader import portfolio
    from eve_trader.production import invention

    monkeypatch.setattr(portfolio, "_bpc_cache", {})
    monkeypatch.setattr(portfolio, "_bpc_cache_at", {})
    monkeypatch.setattr(portfolio.storage, "get_current_tenant", lambda: "t")
    monkeypatch.setattr(
        portfolio.storage, "find_invention_recipe_candidates_by_product_type_id",
        lambda tid: (999,),
    )
    monkeypatch.setattr(
        portfolio.storage, "get_invention_recipe",
        lambda candidate, tid: {"datacores": []},
    )
    monkeypatch.setattr(invention, "fee_rates", lambda cfg=None: None)
    monkeypatch.setattr(
        invention, "best_recipe_and_decryptor",
        lambda *a, **k: type("R", (), {"expected_cost_per_run": 42.0})(),
    )
    # Stub Goonmetrics so home/jita are empty → also uncacheable.
    monkeypatch.setattr(
        "eve_trader.production.pricing._goonmetrics_prices",
        lambda market, ids: {},
    )

    assert portfolio._bpc_run_values({1}) == {1: 42.0}
    assert portfolio._bpc_cache == {}  # must not have been written


def test_bpc_run_values_keep_cached_on_partial_outage(monkeypatch):
    from eve_trader import portfolio

    monkeypatch.setattr(portfolio, "_bpc_cache", {("t", 1): 10.0, ("t", 2): 20.0})
    monkeypatch.setattr(portfolio, "_bpc_cache_at", {"t": __import__("time").time()})
    monkeypatch.setattr(portfolio.storage, "get_current_tenant", lambda: "t")

    def _compute(type_ids):
        return {3: 30.0}, False

    monkeypatch.setattr(portfolio, "_compute_bpc_run_values", _compute)
    assert portfolio._bpc_run_values({1, 2, 3}) == {1: 10.0, 2: 20.0, 3: 30.0}
    assert ("t", 3) not in portfolio._bpc_cache


def test_bpc_run_values_outage_without_new_value_keeps_cache(monkeypatch):
    from eve_trader import portfolio

    monkeypatch.setattr(portfolio, "_bpc_cache", {("t", 1): 10.0, ("t", 2): 20.0})
    monkeypatch.setattr(portfolio, "_bpc_cache_at", {"t": __import__("time").time()})
    monkeypatch.setattr(portfolio.storage, "get_current_tenant", lambda: "t")
    monkeypatch.setattr(portfolio, "_compute_bpc_run_values", lambda type_ids: ({3: None}, False))
    assert portfolio._bpc_run_values({1, 2, 3}) == {1: 10.0, 2: 20.0}
    assert ("t", 3) not in portfolio._bpc_cache
