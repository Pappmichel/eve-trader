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


@pytest.fixture(autouse=True)
def _no_integrated_orders(monkeypatch):
    monkeypatch.setattr(storage, "list_integrated_special_order_items", lambda: [])


@pytest.fixture(autouse=True)
def _every_tenant_active(monkeypatch):
    # The refresh reads tenant activity first; unmocked, that query hit the
    # real database (its rows decided the result, and without Postgres each
    # test waited 30 s for the pool). The activity tests override this.
    monkeypatch.setattr(storage, "list_tenant_last_active", lambda: {})


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
    # Tier 3 "config-dependent test error" (business-logic audit follow-up,
    # 2026-09-26): refresh_jita_price_cache() enters tenant_scope.enter_tenant
    # for every tenant BEFORE checking load_stock_targets() (see that
    # function's own code) - the real enter_tenant needs a live Postgres
    # connection regardless of what load_stock_targets ends up returning, so
    # without this mock (already used two tests below, in
    # test_refresh_prices_the_union_of_every_tenants_structural_material_closure)
    # this test hung on a real pool checkout instead of testing the
    # short-circuit it's named for. Not a production bug - real callers
    # always run inside a real tenant scope already.
    monkeypatch.setattr(tenant_scope, "enter_tenant", _fake_enter_tenant())
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


def test_refresh_also_prices_integrated_special_order_closure(monkeypatch):
    # SPECIAL_ORDERS_INTEGRATION_PLAN.md D14: plan_production prices
    # integrated order items, so the shared cache must cover them too.
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 10, 0, 0)])
    monkeypatch.setattr(storage, "list_integrated_special_order_items",
                        lambda: [("o1", None, 24692, "Abaddon", 1.0)])
    monkeypatch.setattr(tenant_scope, "enter_tenant", _fake_enter_tenant())
    monkeypatch.setattr(engine, "_structural_material_closure",
                        lambda seed_type_ids: {x for t in seed_type_ids for x in (t, t * 10)})
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["type_ids"] = sorted(type_ids)
        return {t: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=1.0, buy_volume=1.0)
                for t in type_ids}
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)

    jita_price_cache.refresh_jita_price_cache()

    assert seen["type_ids"] == [587, 5870, 24692, 246920]


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


def test_refresh_keeps_previous_snapshot_when_esi_returns_no_quotes(monkeypatch):
    # region_order_stats_bulk isolates per-type failures as empty OrderStats.
    # Writing those as buy=sell=0 would poison the cache (jita_prices treats
    # a cached 0 as a hit and never falls through to Goonmetrics).
    previous = CurrentPrice(type_id=34, updated="", buy=5.0, sell=5.5)
    jita_price_cache._cache[34] = previous
    jita_price_cache._updated_at = "2026-09-01T00:00:00+00:00"
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 10, 0, 0)])
    monkeypatch.setattr(tenant_scope, "enter_tenant", _fake_enter_tenant())
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed_type_ids: set(seed_type_ids))
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {
        587: OrderStats(sell_percentile=None, sell_volume=0.0, buy_percentile=None, buy_volume=0.0),
    })

    count = jita_price_cache.refresh_jita_price_cache()

    assert count == 1
    assert jita_price_cache.get_cached_prices([34])[34].sell == 5.5
    assert jita_price_cache.last_updated_at() == "2026-09-01T00:00:00+00:00"


# ------------------------------------------------ scheduler rework: who gets priced
TENANT_ACTIVE = "11111111-1111-1111-1111-111111111111"
TENANT_IDLE = "22222222-2222-2222-2222-222222222222"
TENANT_NO_PRODUCTION = "33333333-3333-3333-3333-333333333333"


def _eligibility_env(monkeypatch, *, stock_by_tenant):
    import datetime as dt

    from eve_trader import tenant_eligibility

    tenants = [(t, t, None) for t in stock_by_tenant]
    monkeypatch.setattr(storage, "list_tenants", lambda: tenants)
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)
    monkeypatch.setattr(storage, "list_tenant_last_active", lambda: {TENANT_IDLE: old})
    monkeypatch.setattr(config.ACCESS_CONFIG, "access_gate_enabled", True)
    grants = {TENANT_ACTIVE: ["production"], TENANT_IDLE: ["production"], TENANT_NO_PRODUCTION: ["trading"]}
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant", lambda t: grants.get(t, []))
    current = {}

    @contextmanager
    def _enter(tenant_id):
        current["tenant"] = tenant_id
        token = config._trading_config_var.set(TradingConfig())
        try:
            yield
        finally:
            config._trading_config_var.reset(token)

    monkeypatch.setattr(tenant_scope, "enter_tenant", _enter)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: stock_by_tenant[current["tenant"]])
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed_type_ids: set(seed_type_ids))
    return tenant_eligibility


def test_refresh_only_prices_active_tenants_that_hold_production(monkeypatch):
    _eligibility_env(monkeypatch, stock_by_tenant={
        TENANT_ACTIVE: [(587, "Rifter", 1, 0, 0)],
        TENANT_IDLE: [(588, "Slasher", 1, 0, 0)],
        TENANT_NO_PRODUCTION: [(589, "Breacher", 1, 0, 0)],
    })
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["type_ids"] = sorted(type_ids)
        return {587: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=1.0, buy_volume=1.0)}

    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)
    assert jita_price_cache.refresh_jita_price_cache() == 1
    assert seen["type_ids"] == [587]


def test_refresh_makes_no_esi_call_when_no_tenant_is_eligible(monkeypatch):
    _eligibility_env(monkeypatch, stock_by_tenant={
        TENANT_IDLE: [(588, "Slasher", 1, 0, 0)],
        TENANT_NO_PRODUCTION: [(589, "Breacher", 1, 0, 0)],
    })

    def _boom(self, region_id, type_ids):
        raise AssertionError("no ESI call expected")

    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _boom)
    assert jita_price_cache.refresh_jita_price_cache() == 0


def test_refresh_treats_everyone_as_active_when_the_activity_lookup_fails(monkeypatch):
    _eligibility_env(monkeypatch, stock_by_tenant={TENANT_IDLE: [(588, "Slasher", 1, 0, 0)]})
    monkeypatch.setattr(storage, "list_tenant_last_active", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {
        588: OrderStats(sell_percentile=2.0, sell_volume=1.0, buy_percentile=1.0, buy_volume=1.0)})
    assert jita_price_cache.refresh_jita_price_cache() == 1



def test_refresh_always_prices_jita_regardless_of_trading_hub(monkeypatch):
    # The cache is process-wide and region-less, so it must stay Jita-only (#222).
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 10, 0, 0)])
    monkeypatch.setattr(tenant_scope, "enter_tenant", _fake_enter_tenant(jita_region_id=10000043))
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed_type_ids: set(seed_type_ids))
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["region_id"] = region_id
        return {587: OrderStats(sell_percentile=100.0, sell_volume=1.0, buy_percentile=90.0, buy_volume=1.0)}
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)

    jita_price_cache.refresh_jita_price_cache()

    assert seen["region_id"] == jita_price_cache.JITA_REGION_ID == 10000002


# ------------------------------------------------ Doctrine types (Paket G Fix 3)
from eve_trader.doctrine import config as doctrine_config_mod
from eve_trader.doctrine.config import DoctrineConfig


def _doctrine_enter(hub_region_id: int = 10000002, *, accept_equivalents: bool = True):
    @contextmanager
    def _enter(tenant_id):
        trading_token = config._trading_config_var.set(TradingConfig())
        doctrine_token = doctrine_config_mod._doctrine_config_var.set(
            DoctrineConfig(hub_region_id=hub_region_id,
                           accept_equivalent_variants=accept_equivalents))
        try:
            yield
        finally:
            doctrine_config_mod._doctrine_config_var.reset(doctrine_token)
            config._trading_config_var.reset(trading_token)
    return _enter


def _fitting_row(fitting_id: str, hull: int):
    # Columns match storage._FITTING_COLUMNS; only fitting_id and hull_type_id matter.
    return (fitting_id, "doc", "Fit", None, hull, "", 1, 1, None, True, None, None, None, None, None)


def test_refresh_includes_doctrine_hulls_and_items_when_hub_is_jita(monkeypatch):
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 1, 0, 0)])
    monkeypatch.setattr(storage, "list_active_fittings",
                        lambda: [_fitting_row("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 24692)])
    monkeypatch.setattr(storage, "load_fitting_items_bulk",
                        lambda ids: {ids[0]: [(1, "low", 2048, 1, False), (2, "med", 2049, 1, False)]})
    monkeypatch.setattr(tenant_scope, "enter_tenant", _doctrine_enter(10000002))
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed: set(seed))
    monkeypatch.setattr(config.ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant",
                        lambda t: ["production", "doctrine"])
    # No equivalents for this test.
    monkeypatch.setattr(
        "eve_trader.doctrine.equivalents.lookup",
        lambda: (lambda type_id: (int(type_id),)))
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["type_ids"] = sorted(type_ids)
        return {t: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=1.0, buy_volume=1.0)
                for t in type_ids}

    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)
    jita_price_cache.refresh_jita_price_cache()
    assert seen["type_ids"] == [587, 2048, 2049, 24692]
    assert jita_price_cache.last_doctrine_extra_count() == 3
    assert not jita_price_cache.last_doctrine_extra_skipped()


def test_refresh_skips_doctrine_when_hub_is_not_jita(monkeypatch):
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 1, 0, 0)])
    monkeypatch.setattr(storage, "list_active_fittings",
                        lambda: (_ for _ in ()).throw(AssertionError("must not read fittings")))
    monkeypatch.setattr(tenant_scope, "enter_tenant", _doctrine_enter(10000043))
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed: set(seed))
    monkeypatch.setattr(config.ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant",
                        lambda t: ["production", "doctrine"])
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["type_ids"] = sorted(type_ids)
        return {587: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=1.0, buy_volume=1.0)}

    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)
    jita_price_cache.refresh_jita_price_cache()
    assert seen["type_ids"] == [587]
    assert jita_price_cache.last_doctrine_extra_count() == 0


def test_refresh_skips_doctrine_without_grant(monkeypatch):
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 1, 0, 0)])
    monkeypatch.setattr(storage, "list_active_fittings",
                        lambda: (_ for _ in ()).throw(AssertionError("must not read fittings")))
    monkeypatch.setattr(tenant_scope, "enter_tenant", _doctrine_enter(10000002))
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed: set(seed))
    monkeypatch.setattr(config.ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant", lambda t: ["production"])
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["type_ids"] = sorted(type_ids)
        return {587: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=1.0, buy_volume=1.0)}

    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)
    jita_price_cache.refresh_jita_price_cache()
    assert seen["type_ids"] == [587]


def test_refresh_includes_equivalents_only_when_switch_on(monkeypatch):
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    monkeypatch.setattr(storage, "list_active_fittings",
                        lambda: [_fitting_row("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 24692)])
    monkeypatch.setattr(storage, "load_fitting_items_bulk",
                        lambda ids: {ids[0]: [(1, "low", 2048, 1, False)]})
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed: set(seed))
    monkeypatch.setattr(config.ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant", lambda t: ["doctrine"])
    monkeypatch.setattr(
        "eve_trader.doctrine.equivalents.lookup",
        lambda: (lambda type_id: (2048, 9999) if int(type_id) == 2048 else (int(type_id),)))

    # Switch on.
    monkeypatch.setattr(tenant_scope, "enter_tenant",
                        _doctrine_enter(10000002, accept_equivalents=True))
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["type_ids"] = sorted(type_ids)
        return {t: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=1.0, buy_volume=1.0)
                for t in type_ids}

    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)
    jita_price_cache.refresh_jita_price_cache()
    assert 9999 in seen["type_ids"]

    # Switch off: twin must not appear.
    monkeypatch.setattr(tenant_scope, "enter_tenant",
                        _doctrine_enter(10000002, accept_equivalents=False))
    jita_price_cache.refresh_jita_price_cache()
    assert 9999 not in seen["type_ids"]
    assert 2048 in seen["type_ids"]


def test_refresh_keeps_production_types_when_doctrine_collection_fails(monkeypatch):
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 1, 0, 0)])
    monkeypatch.setattr(storage, "list_active_fittings",
                        lambda: (_ for _ in ()).throw(RuntimeError("doctrine boom")))
    monkeypatch.setattr(tenant_scope, "enter_tenant", _doctrine_enter(10000002))
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed: set(seed))
    monkeypatch.setattr(config.ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant",
                        lambda t: ["production", "doctrine"])
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["type_ids"] = sorted(type_ids)
        return {587: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=1.0, buy_volume=1.0)}

    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)
    assert jita_price_cache.refresh_jita_price_cache() == 1
    assert seen["type_ids"] == [587]


def test_refresh_skips_doctrine_extra_above_cap(monkeypatch):
    monkeypatch.setattr(storage, "list_tenants", lambda: [(storage.DEFAULT_TENANT_ID, "Default", None)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(587, "Rifter", 1, 0, 0)])
    monkeypatch.setattr(storage, "list_active_fittings",
                        lambda: [_fitting_row("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 1)])
    # One hull + many items, all outside Production's {587}.
    many = list(range(10_000, 10_000 + jita_price_cache._MAX_DOCTRINE_EXTRA_TYPES + 50))
    monkeypatch.setattr(storage, "load_fitting_items_bulk",
                        lambda ids: {ids[0]: [(i, "low", tid, 1, False)
                                              for i, tid in enumerate(many, start=1)]})
    monkeypatch.setattr(tenant_scope, "enter_tenant",
                        _doctrine_enter(10000002, accept_equivalents=False))
    monkeypatch.setattr(engine, "_structural_material_closure", lambda seed: set(seed))
    monkeypatch.setattr(config.ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(storage, "list_tool_grants_for_tenant",
                        lambda t: ["production", "doctrine"])
    seen = {}

    def _fetch(self, region_id, type_ids):
        seen["type_ids"] = sorted(type_ids)
        return {t: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=1.0, buy_volume=1.0)
                for t in type_ids}

    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)
    jita_price_cache.refresh_jita_price_cache()
    assert seen["type_ids"] == [587]
    assert jita_price_cache.last_doctrine_extra_skipped()
    assert jita_price_cache.last_doctrine_extra_count() > jita_price_cache._MAX_DOCTRINE_EXTRA_TYPES
