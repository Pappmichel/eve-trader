"""Tests for production/jita_price_cache.py - the shared, hourly-refreshed
Jita price snapshot pricing.jita_prices() reads before falling back to a
live per-type ESI fetch (see that module's own docstring for why this
exists: a ~15s live ESI cost on every plan_production() run, profiled
live 2026-09-01)."""
from contextlib import contextmanager

import pytest

from eve_trader import config, storage, tenant_scope
from eve_trader.config import TradingConfig
from eve_trader.esi_client import ESIClient, OrderStats
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production import engine, jita_price_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    jita_price_cache._cache.clear()
    jita_price_cache._updated_at = None
    yield
    jita_price_cache._cache.clear()
    jita_price_cache._updated_at = None


def _fake_enter_tenant(jita_region_id: int = 10000002):
    # Same stand-in tenant_scope.enter_tenant already used by
    # tests/test_scheduler.py - resolves TRADING_CONFIG via a direct
    # ContextVar.set rather than a real Postgres tenant.
    @contextmanager
    def _enter(tenant_id):
        token = config._trading_config_var.set(TradingConfig(jita_region_id=jita_region_id))
        try:
            yield
        finally:
            config._trading_config_var.reset(token)
    return _enter


def test_get_cached_prices_returns_only_requested_ids_present_in_cache():
    jita_price_cache._cache[34] = CurrentPrice(type_id=34, updated="", buy=5.0, sell=5.5)
    jita_price_cache._cache[35] = CurrentPrice(type_id=35, updated="", buy=1.0, sell=1.5)

    result = jita_price_cache.get_cached_prices([34, 99])

    assert set(result) == {34}
    assert result[34].sell == 5.5


def test_last_updated_at_is_none_before_first_refresh():
    assert jita_price_cache.last_updated_at() is None


def test_refresh_returns_zero_and_skips_esi_when_no_tenant_has_stock_targets(monkeypatch):
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids:
                         pytest.fail("must not call ESI when no tenant has any stock targets"))

    count = jita_price_cache.refresh_jita_price_cache()

    assert count == 0
    assert jita_price_cache.last_updated_at() is None


def test_refresh_prices_the_union_of_every_tenants_structural_material_closure(monkeypatch):
    monkeypatch.setattr(storage, "list_tenants", lambda: [
        ("11111111-1111-1111-1111-111111111111", "Tenant A", None),
        ("22222222-2222-2222-2222-222222222222", "Tenant B", None),
    ])
    # Order-matched to storage.list_tenants()'s own return order above,
    # rather than keyed by tenant_id - the fake enter_tenant below (same
    # stand-in tests/test_scheduler.py already uses) only resolves
    # TRADING_CONFIG, not storage's own ambient tenant contextvar, so
    # storage.get_current_tenant() isn't meaningful inside this test.
    stock_targets_sequence = iter([[(587, "Rifter", 10, 0, 0)], [(588, "Slasher", 5, 0, 0)]])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: next(stock_targets_sequence))
    monkeypatch.setattr(tenant_scope, "enter_tenant", _fake_enter_tenant(jita_region_id=10000002))
    # Unit-testing jita_price_cache's own orchestration (tenant fan-out ->
    # union -> one bulk ESI call), not engine's own BOM-walk logic (covered
    # separately) - stub the closure to each type_id's own singleton set.
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed_type_ids: set(seed_type_ids))
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["region_id"] = region_id
        seen["type_ids"] = sorted(type_ids)
        return {
            587: OrderStats(sell_percentile=100.0, sell_volume=1.0, buy_percentile=90.0, buy_volume=1.0),
            588: OrderStats(sell_percentile=200.0, sell_volume=1.0, buy_percentile=190.0, buy_volume=1.0),
        }
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)

    count = jita_price_cache.refresh_jita_price_cache()

    assert count == 2
    assert seen == {"region_id": 10000002, "type_ids": [587, 588]}
    cached = jita_price_cache.get_cached_prices([587, 588])
    assert cached[587].sell == 100.0
    assert cached[588].sell == 200.0
    assert jita_price_cache.last_updated_at() is not None


def test_refresh_replaces_the_cache_wholesale(monkeypatch):
    # A type_id present in an earlier refresh but no longer priced by any
    # tenant's current stock targets must not linger in the cache forever.
    jita_price_cache._cache[999] = CurrentPrice(type_id=999, updated="", buy=1.0, sell=1.0)
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 10, 0, 0)])
    monkeypatch.setattr(tenant_scope, "enter_tenant", _fake_enter_tenant())
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed_type_ids: set(seed_type_ids))
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {
        587: OrderStats(sell_percentile=100.0, sell_volume=1.0, buy_percentile=90.0, buy_volume=1.0),
    })

    jita_price_cache.refresh_jita_price_cache()

    assert jita_price_cache.get_cached_prices([999]) == {}
