"""Action-level tests for eve_trader/module_reprocessing/actions.py. Mirrors
tests/test_goonmetrics_price_failsafe_actions.py's mocking shape for the
Shortlist Refresh path, and tests Discover (the cheap Goonmetrics-only path -
see actions.py's own module docstring for why this runs synchronously, not
via pipeline_runner) separately.
"""
import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.config import TradingConfig
from eve_trader.esi_client import ESIClient, ESIError, OrderStats
from eve_trader.goonmetrics_client import CurrentPrice, GoonmetricsClient
from eve_trader.module_reprocessing import actions as mr_actions
from eve_trader.module_reprocessing.config import ModuleReprocessingConfig
from eve_trader.module_reprocessing.models import ModuleCandidate

_SDE_ROW = (100, 0, "200mm AutoCannon I", 0.01, 1, 0, 0, None, 1)


@pytest.fixture(autouse=True)
def _clear_discover_cache():
    # Module-level, per-tenant cache - clear between tests so one test's
    # Discover results can't leak into another's do_get_discovered_candidates
    # read (same reasoning CLAUDE.md's testing conventions give for any
    # module-level cache).
    mr_actions._discover_results.clear()
    yield
    mr_actions._discover_results.clear()


# ------------------------------------------------------------------ Discover
def test_discover_candidates_raises_when_universe_empty(monkeypatch):
    monkeypatch.setattr(mr_actions, "build_module_candidate_universe", lambda: [])
    with pytest.raises(ActionError, match="No T1/Meta module or drone types"):
        mr_actions.do_discover_candidates()


def test_discover_candidates_populates_cache_and_summarizes(monkeypatch):
    trading_cfg = TradingConfig(structure_market_slug="my-structure")
    monkeypatch.setattr(mr_actions, "build_module_candidate_universe",
                         lambda: [ModuleCandidate(type_id=100, item="200mm AutoCannon I", volume_m3=0.01)])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {100: _SDE_ROW})
    monkeypatch.setattr(storage, "get_type_materials_bulk", lambda type_ids: {100: [(34, 100.0)]})
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-1")

    def fake_current_prices(self, market):
        if market == mr_actions.JITA_MARKET:
            return [CurrentPrice(type_id=100, updated="2026-01-01", buy=0.5, sell=1.0)]
        return [CurrentPrice(type_id=34, updated="2026-01-01", buy=9.0, sell=10.0)]
    monkeypatch.setattr(GoonmetricsClient, "current_prices", fake_current_prices)

    result = mr_actions.do_discover_candidates(ModuleReprocessingConfig(), trading_cfg)

    assert result["scanned"] == 1
    assert result["estimated_profitable"] == 1
    cached = mr_actions.do_get_discovered_candidates()
    assert cached[0]["type_id"] == 100
    assert cached[0]["est_profit_per_unit"] > 0


def test_discover_candidates_wraps_jita_goonmetrics_outage(monkeypatch):
    import requests

    monkeypatch.setattr(mr_actions, "build_module_candidate_universe",
                         lambda: [ModuleCandidate(type_id=100, item="200mm AutoCannon I", volume_m3=0.01)])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {100: _SDE_ROW})
    monkeypatch.setattr(storage, "get_type_materials_bulk", lambda type_ids: {})

    def _boom(self, market):
        raise requests.RequestException("Goonmetrics down")
    monkeypatch.setattr(GoonmetricsClient, "current_prices", _boom)

    with pytest.raises(ActionError, match="Could not fetch Jita prices"):
        mr_actions.do_discover_candidates()


def test_discover_candidates_survives_home_market_outage(monkeypatch):
    """The Jita dump succeeded but the C-J home-market dump failed - mineral
    values are just missing (est_mineral_value=None), not a hard failure,
    same "best-effort" reasoning refining/actions.py's do_optimize_mineral_
    shopping_list already uses for its own Goonmetrics home-market call."""
    import requests

    trading_cfg = TradingConfig(structure_market_slug="my-structure")
    monkeypatch.setattr(mr_actions, "build_module_candidate_universe",
                         lambda: [ModuleCandidate(type_id=100, item="200mm AutoCannon I", volume_m3=0.01)])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {100: _SDE_ROW})
    monkeypatch.setattr(storage, "get_type_materials_bulk", lambda type_ids: {100: [(34, 100.0)]})
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-1")

    def fake_current_prices(self, market):
        if market == mr_actions.JITA_MARKET:
            return [CurrentPrice(type_id=100, updated="2026-01-01", buy=0.5, sell=1.0)]
        raise requests.RequestException("Goonmetrics home market down")
    monkeypatch.setattr(GoonmetricsClient, "current_prices", fake_current_prices)

    result = mr_actions.do_discover_candidates(ModuleReprocessingConfig(), trading_cfg)

    assert result["scanned"] == 1
    assert result["estimated_profitable"] == 0
    cached = mr_actions.do_get_discovered_candidates()
    assert cached[0]["est_mineral_value"] is None


def test_get_discovered_candidates_empty_before_any_discover_run(monkeypatch):
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-1")
    assert mr_actions.do_get_discovered_candidates() == []


# --------------------------------------------------------------- Shortlist
def test_add_to_shortlist_requires_at_least_one_item():
    with pytest.raises(ActionError, match="No items selected"):
        mr_actions.do_add_to_shortlist([])


def test_add_to_shortlist_rejects_type_not_in_sde_cache(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)
    with pytest.raises(ActionError, match="isn't in the SDE cache"):
        mr_actions.do_add_to_shortlist([100])


def test_add_to_shortlist_upserts_resolved_names(monkeypatch):
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: _SDE_ROW)
    captured = {}
    monkeypatch.setattr(storage, "upsert_module_reprocessing_shortlist", lambda rows: captured.update(rows=list(rows)))

    result = mr_actions.do_add_to_shortlist([100])

    assert result == {"added": 1}
    assert captured["rows"] == [(100, "200mm AutoCannon I", True)]


def test_refresh_shortlist_raises_when_empty(monkeypatch):
    monkeypatch.setattr(storage, "load_module_reprocessing_shortlist", lambda: [])
    with pytest.raises(ActionError, match="Shortlist is empty"):
        mr_actions.do_refresh_shortlist()


def test_refresh_shortlist_surfaces_priced_via_fallback(monkeypatch):
    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(storage, "load_module_reprocessing_shortlist", lambda: [(100, "Test Module", True)])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {100: _SDE_ROW})
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    monkeypatch.setattr(mr_actions, "_seller_roles", lambda tm: [])
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {})
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_roles, goonmetrics_market_slug: ({}, True))
    monkeypatch.setattr(storage, "save_module_reprocessing_shortlist_snapshot", lambda rows, run_ts: None)
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda tool, run_ts: None)

    result = mr_actions.do_refresh_shortlist(ModuleReprocessingConfig(), trading_cfg)

    assert result["priced_via_fallback"] is True
    assert result["evaluated"] == 1


def test_refresh_shortlist_wraps_purchase_region_outage(monkeypatch):
    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(storage, "load_module_reprocessing_shortlist", lambda: [(100, "Test Module", True)])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {100: _SDE_ROW})
    monkeypatch.setattr(mr_actions, "_seller_roles", lambda tm: [])

    def _boom(self, region_id, type_ids):
        raise ESIError("ESI down")
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _boom)

    with pytest.raises(ActionError, match="Could not fetch the purchase region's order book"):
        mr_actions.do_refresh_shortlist(ModuleReprocessingConfig(), trading_cfg)


def test_deactivate_and_activate_shortlist_items(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage, "deactivate_module_reprocessing_shortlist_items",
                         lambda item_ids: captured.setdefault("deactivated", list(item_ids)))
    monkeypatch.setattr(storage, "activate_module_reprocessing_shortlist_items",
                         lambda item_ids: captured.setdefault("activated", list(item_ids)))

    assert mr_actions.do_deactivate_shortlist_items([1, 2]) == {"deactivated": 2}
    assert mr_actions.do_activate_shortlist_items([1]) == {"activated": 1}
    assert captured == {"deactivated": [1, 2], "activated": [1]}


def test_update_settings_persists_and_returns_updates(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "eve_trader.module_reprocessing.actions.save_tenant_config_overrides",
        lambda scope, updates, *cfgs, cfg_type: captured.update(scope=scope, updates=updates, cfg_type=cfg_type),
    )
    cfg = ModuleReprocessingConfig()

    result = mr_actions.do_update_settings({"scrapmetal_processing_skill_level": 5}, cfg)

    assert result == {"scrapmetal_processing_skill_level": 5}
    assert captured["scope"] == "module_reprocessing"
    assert captured["cfg_type"] is ModuleReprocessingConfig
