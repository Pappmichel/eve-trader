"""Action-level wiring tests for the Goonmetrics price failsafe (confirmed
with the user 2026-08-24): Shortlist refresh, Ore Shortlist refresh and
Reprocessing Quote all fall back to a Goonmetrics snapshot for structure
pricing when no seller is logged in - these confirm `priced_via_fallback`
actually reaches each action's return value, not just the underlying
ESIClient method (see test_esi_client_goonmetrics_fallback.py for that).
Everything below `ESIClient.structure_order_stats_bulk_or_goonmetrics` is
mocked out, since that method's own behavior is already covered there.
"""
from eve_trader import actions
from eve_trader import storage
from eve_trader.config import TradingConfig
from eve_trader.esi_client import ESIClient
from eve_trader.models import ShortlistItem
from eve_trader.refining import actions as refining_actions


def test_refresh_shortlist_surfaces_priced_via_fallback(monkeypatch):
    cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(storage, "load_shortlist",
                         lambda: [ShortlistItem(item="Test", item_id=34, category="X", volume_m3=1.0, meta_level=5)])
    monkeypatch.setattr(actions, "_list_role_characters", lambda tm, prefix: [])
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {})
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_role, goonmetrics_market_slug: ({}, True))
    monkeypatch.setattr(storage, "save_shortlist_snapshot", lambda rows, run_ts: None)
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda tool, run_ts: None)

    result = actions.do_refresh_shortlist(cfg)

    assert result["priced_via_fallback"] is True


def test_refresh_shortlist_no_fallback_when_seller_logged_in(monkeypatch):
    cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(storage, "load_shortlist",
                         lambda: [ShortlistItem(item="Test", item_id=34, category="X", volume_m3=1.0, meta_level=5)])
    monkeypatch.setattr(actions, "_list_role_characters", lambda tm, prefix: [("seller", 1, "Seller One")]
                         if prefix == "seller" else [])
    monkeypatch.setattr(actions.own_orders, "fetch_own_sell_orders", lambda char_id, role, client, cfg: {})
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {})
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_role, goonmetrics_market_slug: ({}, False))
    monkeypatch.setattr(storage, "save_shortlist_snapshot", lambda rows, run_ts: None)
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda tool, run_ts: None)

    result = actions.do_refresh_shortlist(cfg)

    assert result["priced_via_fallback"] is False


def test_refresh_ore_shortlist_surfaces_priced_via_fallback(monkeypatch):
    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(refining_actions, "build_ore_candidate_universe", lambda: [])
    monkeypatch.setattr(storage, "load_ore_shortlist", lambda: [(34, "Test Ore", "Veldspar", False, True)])
    monkeypatch.setattr(refining_actions, "_seller_role", lambda tm: None)
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {})
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_role, goonmetrics_market_slug: ({}, True))
    monkeypatch.setattr(storage, "save_ore_shortlist_snapshot", lambda rows, run_ts: None)
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda tool, run_ts: None)

    result = refining_actions.do_refresh_ore_shortlist(trading_cfg)

    assert result["priced_via_fallback"] is True


def test_quote_reprocessing_surfaces_priced_via_fallback(monkeypatch):
    from eve_trader.refining import reprocessing

    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    # evaluate_reprocessing_line (called inside do_quote_reprocessing) looks
    # up resolve_type_id from its own module, not the reference re-exported
    # into refining_actions - both need patching, or the real one still runs
    # and hits storage.connect() with no tenant set.
    monkeypatch.setattr(refining_actions, "resolve_type_id", lambda name: None)
    monkeypatch.setattr(reprocessing, "resolve_type_id", lambda name: None)
    monkeypatch.setattr(refining_actions, "_seller_role", lambda tm: None)
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_role, goonmetrics_market_slug: ({}, True))

    result = refining_actions.do_quote_reprocessing("Tritanium\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t",
                                                      trading_cfg=trading_cfg)

    assert result["priced_via_fallback"] is True
