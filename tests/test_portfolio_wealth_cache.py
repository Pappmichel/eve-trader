"""Total Wealth's per-tenant 120 s cache of priced rows (`wealth_cache`)."""
import threading
import time

import pytest

from eve_trader import portfolio, storage, wealth_cache
from eve_trader.config import TradingConfig
from eve_trader.esi_data import access as esi_access

TENANT_A = "00000000-0000-0000-0000-00000000000a"
TENANT_B = "00000000-0000-0000-0000-00000000000b"


class _World:
    """One character with 100 of type 34; the price book is mutable."""

    def __init__(self, monkeypatch):
        self.loads = 0
        self.price = 5.0
        self.quantity = {TENANT_A: 100, TENANT_B: 100}
        self.quotes_ok = True
        self.freshness: list[tuple] = []
        self.delay = 0.0
        self.manual: dict[int, float] = {}

        monkeypatch.setattr(
            esi_access, "shared_owner_ids",
            lambda kind, tool, owner_type: [1] if (kind == "assets" and owner_type == "character") else [],
        )
        monkeypatch.setattr(storage, "list_esi_freshness", lambda: list(self.freshness))
        monkeypatch.setattr(storage, "load_assets_grouped", self._assets)
        monkeypatch.setattr(storage, "load_blueprints_grouped", lambda c, o: [])
        monkeypatch.setattr(portfolio, "_priced", lambda ids: {34: self.manual.get(34, self.price)})
        monkeypatch.setattr(portfolio, "_characters_missing_wallet_scope", lambda ids: [])
        monkeypatch.setattr(portfolio, "_quotes_available", lambda: self.quotes_ok)
        monkeypatch.setattr(portfolio, "portfolio_overview", lambda cfg=None: {})
        from eve_trader.realized import names
        monkeypatch.setattr(names, "owner_names", lambda ids: {i: f"Owner {i}" for i in ids})
        monkeypatch.setattr(storage, "get_location_names", lambda ids: {})
        monkeypatch.setattr(storage, "sde_categories_for_types", lambda ids: {})
        monkeypatch.setattr(storage, "sde_type_names", lambda ids: {})
        self.snapshots: list[dict] = []
        monkeypatch.setattr(storage, "upsert_portfolio_snapshot", lambda day, row: self.snapshots.append(row))

    def _assets(self, chars, corps):
        self.loads += 1
        if self.delay:
            time.sleep(self.delay)
        tenant = storage.get_current_tenant()
        return [("character", 1, 10, 34, self.quantity[tenant])]


@pytest.fixture
def world(monkeypatch):
    with storage.tenant_context(TENANT_A):
        yield _World(monkeypatch)


def test_two_calls_compute_once(world):
    first = portfolio.total_wealth(TradingConfig())
    second = portfolio.total_wealth(TradingConfig())
    breakdown = portfolio.do_get_portfolio_breakdown(TradingConfig())
    assert world.loads == 1
    assert first["total_wealth"] == second["total_wealth"] == breakdown["total"] == 500.0


def test_the_rows_expire_after_the_window(world, monkeypatch):
    portfolio.total_wealth(TradingConfig())
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + wealth_cache.TTL_SECONDS + 1)
    world.price = 6.0
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 600.0
    assert world.loads == 2


def test_parallel_calls_compute_once(world):
    world.delay = 0.2
    results = []
    start = threading.Barrier(6)

    def run():
        start.wait()
        results.append(portfolio.total_wealth(TradingConfig())["total_wealth"])

    threads = [threading.Thread(target=storage.with_current_tenant(run)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [500.0] * 6
    assert world.loads == 1


def test_a_reader_cannot_change_what_the_next_one_gets(world):
    first = portfolio._collect_wealth_cached(TradingConfig())
    first["items"][0]["price"] = 999.0
    first["items"].clear()
    first["shares"]["assets"][0].append(77)
    second = portfolio._collect_wealth_cached(TradingConfig())
    assert [item["price"] for item in second["items"]] == [5.0]
    assert second["shares"]["assets"][0] == [1]
    assert world.loads == 1


def test_the_callers_structure_id_is_always_used(world):
    portfolio.total_wealth(TradingConfig(structure_id=1))
    again = portfolio._collect_wealth_cached(TradingConfig(structure_id=2))
    assert again["structure_id"] == 2


def test_a_manual_price_applies_immediately(world, monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=2: [(34, "Tritanium")])
    monkeypatch.setattr(storage, "upsert_manual_item_price", lambda tid, name, price: world.manual.__setitem__(tid, price))
    monkeypatch.setattr(storage, "delete_manual_item_price", lambda tid: world.manual.pop(tid, None))

    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 500.0
    portfolio.do_set_manual_item_price("Tritanium", 8.0)
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 800.0
    portfolio.do_remove_manual_item_price(34)
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 500.0
    assert world.loads == 3


def test_two_tenants_do_not_mix(world):
    world.quantity[TENANT_B] = 10
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 500.0
    with storage.tenant_context(TENANT_B):
        assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 50.0
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 500.0
    assert world.loads == 2
    # Invalidating one tenant leaves the other cached.
    wealth_cache.invalidate(TENANT_B)
    portfolio.total_wealth(TradingConfig())
    assert world.loads == 2
    with storage.tenant_context(TENANT_B):
        portfolio.total_wealth(TradingConfig())
    assert world.loads == 3


def test_a_snapshot_computes_fresh_and_does_not_store(world):
    # Cache filled at 5.0, then the price moves: the snapshot must see 6.0 ...
    portfolio.total_wealth(TradingConfig())
    world.price = 6.0
    portfolio.take_portfolio_snapshot(TradingConfig())
    assert world.snapshots[-1]["total_wealth"] == 600.0
    # ... and must not have replaced the cached rows.
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 500.0
    assert world.loads == 2


def test_a_snapshot_does_not_fill_an_empty_cache(world):
    portfolio.take_portfolio_snapshot(TradingConfig())
    assert world.loads == 1
    portfolio.total_wealth(TradingConfig())
    assert world.loads == 2


def test_an_outage_is_not_cached(world):
    world.quotes_ok = False
    world.price = 0.0
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 0.0
    world.quotes_ok = True
    world.price = 5.0
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 500.0
    assert world.loads == 2
    portfolio.total_wealth(TradingConfig())
    assert world.loads == 2


def test_nothing_shared_is_cached_too(world, monkeypatch):
    monkeypatch.setattr(esi_access, "shared_owner_ids", lambda kind, tool, owner_type: [])
    calls = []
    real = portfolio._collect_wealth
    monkeypatch.setattr(portfolio, "_collect_wealth", lambda cfg: calls.append(1) or real(cfg))
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] is None
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] is None
    assert len(calls) == 1


def test_freshness_lines_stay_live(world):
    assert portfolio.total_wealth(TradingConfig())["sync"]["assets"] is None
    world.freshness = [("character", 1, "assets", "2026-11-07T09:00:00+00:00", None, None, "2026-11-07T08:59:00+00:00")]
    assert portfolio.total_wealth(TradingConfig())["sync"]["assets"] == "2026-11-07T08:59:00+00:00"
    assert world.loads == 1


def test_attention_reads_stale_labels_live(world, monkeypatch):
    labels = [["Assets"], []]
    monkeypatch.setattr(portfolio, "_stale_portfolio_labels", lambda cfg, now: labels.pop(0))
    monkeypatch.setattr(portfolio, "_card_readers", lambda tool_keys: [])
    first = portfolio.attention((), TradingConfig())
    second = portfolio.attention((), TradingConfig())
    assert [i["key"] for i in first["items"]] == ["stale_esi"]
    assert second["items"] == []
    assert world.loads == 1


def test_an_invalidation_during_a_computation_wins(world, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    real = world._assets

    def slow(chars, corps):
        rows = real(chars, corps)
        started.set()
        release.wait(5)
        return rows

    monkeypatch.setattr(storage, "load_assets_grouped", slow)
    t = threading.Thread(target=storage.with_current_tenant(lambda: portfolio.total_wealth(TradingConfig())))
    t.start()
    assert started.wait(5)
    world.price = 7.0
    wealth_cache.invalidate_current_tenant()
    release.set()
    t.join()
    monkeypatch.setattr(storage, "load_assets_grouped", real)
    # The old result was not stored: the next read prices anew.
    assert portfolio.total_wealth(TradingConfig())["total_wealth"] == 700.0


def test_a_sharing_change_invalidates(world, monkeypatch):
    from eve_trader.esi_data import actions as esi_actions
    monkeypatch.setattr(storage, "upsert_esi_sharing", lambda *a: None)
    monkeypatch.setattr(storage, "delete_esi_sharing", lambda *a: None)
    portfolio.total_wealth(TradingConfig())
    esi_actions.do_set_sharing("character", 1, "assets", "portfolio", True)
    portfolio.total_wealth(TradingConfig())
    assert world.loads == 2


def test_a_sync_invalidates_even_when_it_fails(world, monkeypatch):
    from eve_trader.esi_data import orchestrator

    def boom(*args, **kwargs):
        raise RuntimeError("sync failed")

    monkeypatch.setattr(orchestrator, "_sync_owners", boom)
    portfolio.total_wealth(TradingConfig())
    with pytest.raises(RuntimeError):
        orchestrator._sync([], client=None, tool_key=None)
    portfolio.total_wealth(TradingConfig())
    assert world.loads == 2


def test_the_asset_manager_hook_invalidates(world):
    from eve_trader.asset_manager import actions as asset_actions
    portfolio.total_wealth(TradingConfig())
    asset_actions.invalidate_cache(TENANT_A)
    portfolio.total_wealth(TradingConfig())
    assert world.loads == 2


@pytest.mark.parametrize("scope", ["production", "trading"])
def test_a_price_setting_save_invalidates(world, monkeypatch, scope):
    from eve_trader import config as config_module
    monkeypatch.setattr(storage, "save_tenant_settings", lambda s, u: None)
    monkeypatch.setattr(config_module, "validate_config_overrides", lambda cfg, u, t: None)
    monkeypatch.setattr(config_module, "apply_config_overrides", lambda cfg, u: None)
    portfolio.total_wealth(TradingConfig())
    config_module.save_tenant_config_overrides(scope, {"haul_cost_per_m3": 900.0}, object(), cfg_type=TradingConfig)
    portfolio.total_wealth(TradingConfig())
    assert world.loads == 2


def test_an_sde_apply_invalidates_every_tenant(world):
    portfolio.total_wealth(TradingConfig())
    with storage.tenant_context(TENANT_B):
        portfolio.total_wealth(TradingConfig())
    assert world.loads == 2
    wealth_cache.invalidate(None)
    portfolio.total_wealth(TradingConfig())
    with storage.tenant_context(TENANT_B):
        portfolio.total_wealth(TradingConfig())
    assert world.loads == 4
