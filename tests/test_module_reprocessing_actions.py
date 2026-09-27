"""Action-level tests for eve_trader/module_reprocessing/actions.py. Mirrors
tests/test_goonmetrics_price_failsafe_actions.py's mocking shape for the
Shortlist Refresh path. do_refresh_shortlist now folds discovery (auto-add)
and live-pricing into one call (confirmed with the user 2026-09-27: manual
per-item shortlist curation doesn't scale over this tool's large candidate
universe) - the discovery/filtering math itself is tested separately in
tests/test_module_reprocessing_candidate_discovery.py's discover_candidates
tests; here candidate_discovery.discover_candidates is monkeypatched to a
fixed result, same as test_goonmetrics_price_failsafe_actions.py treats
history_backtest as already covered elsewhere.
"""
import pytest

from eve_trader import storage
from eve_trader.actions import ActionError
from eve_trader.config import TradingConfig
from eve_trader.esi_client import ESIClient, ESIError
from eve_trader.module_reprocessing import actions as mr_actions
from eve_trader.module_reprocessing.config import ModuleReprocessingConfig
from eve_trader.module_reprocessing.models import DiscoveredModuleResult

_SDE_ROW = (100, 0, "200mm AutoCannon I", 0.01, 1, 0, 0, None, 1)


def _discovered(type_id=100, item="200mm AutoCannon I"):
    return DiscoveredModuleResult(type_id=type_id, item=item, volume_m3=0.01, est_landed_cost=1.0,
                                   est_mineral_value=10.0, est_profit_per_unit=9.0, est_margin=9.0)


def test_refresh_shortlist_wraps_jita_goonmetrics_outage(monkeypatch):
    import requests

    def _boom(cfg, trading_cfg):
        raise requests.RequestException("Goonmetrics down")
    monkeypatch.setattr(mr_actions, "discover_candidates", _boom)

    with pytest.raises(ActionError, match="Could not fetch Jita prices"):
        mr_actions.do_refresh_shortlist()


def test_refresh_shortlist_raises_when_nothing_ever_cleared_the_bar(monkeypatch):
    monkeypatch.setattr(mr_actions, "discover_candidates", lambda cfg, trading_cfg: [])
    monkeypatch.setattr(storage, "load_module_reprocessing_shortlist", lambda: [])

    with pytest.raises(ActionError, match="No candidates clear"):
        mr_actions.do_refresh_shortlist()


def test_refresh_shortlist_auto_upserts_discovered_candidates(monkeypatch):
    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(mr_actions, "discover_candidates", lambda cfg, tc: [_discovered()])
    captured = {}
    monkeypatch.setattr(storage, "upsert_module_reprocessing_shortlist",
                         lambda rows: captured.update(rows=list(rows)))
    monkeypatch.setattr(storage, "load_module_reprocessing_shortlist", lambda: [(100, "200mm AutoCannon I", True)])
    monkeypatch.setattr(storage, "get_sde_types_bulk", lambda type_ids: {100: _SDE_ROW})
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    monkeypatch.setattr(mr_actions, "_seller_roles", lambda tm: [])
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {})
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_roles, goonmetrics_market_slug: ({}, False))
    monkeypatch.setattr(storage, "save_module_reprocessing_shortlist_snapshot", lambda rows, run_ts: None)
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda tool, run_ts: None)

    result = mr_actions.do_refresh_shortlist(ModuleReprocessingConfig(), trading_cfg)

    assert captured["rows"] == [(100, "200mm AutoCannon I")]
    assert result["discovered"] == 1
    assert result["evaluated"] == 1


def test_refresh_shortlist_surfaces_priced_via_fallback(monkeypatch):
    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(mr_actions, "discover_candidates", lambda cfg, tc: [])
    monkeypatch.setattr(storage, "upsert_module_reprocessing_shortlist", lambda rows: None)
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
    monkeypatch.setattr(mr_actions, "discover_candidates", lambda cfg, tc: [])
    monkeypatch.setattr(storage, "upsert_module_reprocessing_shortlist", lambda rows: None)
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
