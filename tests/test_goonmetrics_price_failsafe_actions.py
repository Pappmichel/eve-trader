"""Action-level wiring tests for the Goonmetrics price failsafe (confirmed
with the user 2026-08-24): Shortlist refresh, Ore Shortlist refresh and
Reprocessing Quote all fall back to a Goonmetrics snapshot for structure
pricing when no seller is logged in - these confirm `priced_via_fallback`
actually reaches each action's return value, not just the underlying
ESIClient method (see test_esi_client_goonmetrics_fallback.py for that).
Everything below `ESIClient.structure_order_stats_bulk_or_goonmetrics` is
mocked out, since that method's own behavior is already covered there.
"""
import pytest

from eve_trader import actions
from eve_trader import storage
from eve_trader.config import TradingConfig
from eve_trader.esi_client import ESIClient
from eve_trader.goonmetrics_client import GoonmetricsClient
from eve_trader.models import ShortlistItem
from eve_trader.refining import actions as refining_actions


def test_refresh_shortlist_surfaces_priced_via_fallback(monkeypatch):
    cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(storage, "load_shortlist",
                         lambda: [ShortlistItem(item="Test", item_id=34, category="X", volume_m3=1.0, meta_level=5)])
    monkeypatch.setattr(actions, "list_shared_trading_characters", lambda tm: [])
    monkeypatch.setattr(actions, "structure_book_auth_roles", lambda chars=None: ["seller:1"])
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {})
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_roles, goonmetrics_market_slug: ({}, True))
    monkeypatch.setattr(storage, "replace_shortlist_snapshot_run", lambda rows, run_ts: None)
    monkeypatch.setattr(storage, "load_latest_shortlist_rows", lambda: [])
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda tool, run_ts: None)
    monkeypatch.setattr(storage, "mark_shortlist_refreshed", lambda item_ids, ts: None)
    monkeypatch.setattr(GoonmetricsClient, "price_history_chunked", lambda self, *a, **k: [])

    result = actions.do_refresh_shortlist(cfg)

    assert result["priced_via_fallback"] is True


def test_refresh_shortlist_no_fallback_when_seller_logged_in(monkeypatch):
    cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(storage, "load_shortlist",
                         lambda: [ShortlistItem(item="Test", item_id=34, category="X", volume_m3=1.0, meta_level=5)])
    monkeypatch.setattr(actions, "list_shared_trading_characters", lambda tm: [("seller", 1, "Seller One")])
    monkeypatch.setattr(actions, "structure_book_auth_roles", lambda chars=None: ["seller:1"])
    monkeypatch.setattr(actions.own_orders, "fetch_own_sell_orders", lambda char_id, role, client, cfg: {})
    # Buyer/seller are now the same shared-characters list (see
    # list_shared_trading_characters), so this "seller" record also flows
    # through the buyer branch - mock it too rather than hitting a real
    # unmocked ESI call.
    monkeypatch.setattr(actions.own_orders, "fetch_buyer_already_covered",
                         lambda char_id, role, client, cfg: set())
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {})
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_roles, goonmetrics_market_slug: ({}, False))
    monkeypatch.setattr(storage, "replace_shortlist_snapshot_run", lambda rows, run_ts: None)
    monkeypatch.setattr(storage, "load_latest_shortlist_rows", lambda: [])
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda tool, run_ts: None)
    monkeypatch.setattr(storage, "mark_shortlist_refreshed", lambda item_ids, ts: None)
    monkeypatch.setattr(GoonmetricsClient, "price_history_chunked", lambda self, *a, **k: [])

    result = actions.do_refresh_shortlist(cfg)

    assert result["priced_via_fallback"] is False


def test_refresh_ore_shortlist_surfaces_priced_via_fallback(monkeypatch):
    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(refining_actions, "build_ore_candidate_universe", lambda: [])
    monkeypatch.setattr(storage, "load_ore_shortlist", lambda: [(34, "Test Ore", "Veldspar", False, True)])
    monkeypatch.setattr(refining_actions, "_seller_roles", lambda tm: [])
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {})
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_roles, goonmetrics_market_slug: ({}, True))
    monkeypatch.setattr(storage, "save_ore_shortlist_snapshot", lambda rows, run_ts: None)
    monkeypatch.setattr(storage, "set_esi_sync_time", lambda tool, run_ts: None)

    result = refining_actions.do_refresh_ore_shortlist(trading_cfg)

    assert result["priced_via_fallback"] is True


def test_refresh_ore_shortlist_wraps_jita_order_book_outage(monkeypatch):
    from eve_trader.actions import ActionError
    from eve_trader.esi_client import ESIError

    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    monkeypatch.setattr(refining_actions, "build_ore_candidate_universe", lambda: [])
    monkeypatch.setattr(storage, "load_ore_shortlist", lambda: [(34, "Test Ore", "Veldspar", False, True)])
    monkeypatch.setattr(refining_actions, "_seller_roles", lambda tm: [])

    def _boom(self, region_id, type_ids):
        raise ESIError("ESI down")
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _boom)

    try:
        refining_actions.do_refresh_ore_shortlist(trading_cfg)
        assert False, "expected ActionError"
    except ActionError as e:
        assert "Could not fetch Jita's order book" in str(e)


def test_quote_reprocessing_surfaces_priced_via_fallback(monkeypatch):
    from eve_trader.refining import reprocessing

    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    # evaluate_reprocessing_line (called inside do_quote_reprocessing) looks
    # up resolve_type_id from its own module, not the reference re-exported
    # into refining_actions - both need patching, or the real one still runs
    # and hits storage.connect() with no tenant set.
    monkeypatch.setattr(refining_actions, "resolve_type_id", lambda name: None)
    monkeypatch.setattr(reprocessing, "resolve_type_id", lambda name: None)
    monkeypatch.setattr(refining_actions, "_seller_roles", lambda tm: [])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk_or_goonmetrics",
                         lambda self, structure_id, type_ids, auth_roles, goonmetrics_market_slug: ({}, True))

    result = refining_actions.do_quote_reprocessing("Tritanium\t100\tMineral\tMaterial\t\t\t0.01 m3\t\t",
                                                      trading_cfg=trading_cfg)

    assert result["priced_via_fallback"] is True


def test_quote_reprocessing_resolves_each_distinct_name_once(monkeypatch):
    from eve_trader.esi_client import OrderStats
    from eve_trader.refining import reprocessing

    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure")
    searches = []

    def fake_search(name, limit=5):
        searches.append(name)
        by_name = {"Item A": 100, "Item B": 200}
        tid = by_name.get(name)
        return [(tid, name)] if tid else []

    monkeypatch.setattr(storage, "search_sde_types", fake_search)
    monkeypatch.setattr(storage, "get_type_materials_bulk", lambda type_ids: {tid: [] for tid in type_ids})
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: None)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [])
    # T2-01 (business-logic audit follow-up, 2026-09-26): do_quote_reprocessing
    # now also bulk-resolves ore/ice family info up front (candidate_
    # discovery.ore_ice_families_for_types -> storage.get_types_names_and_
    # groups_bulk) - mocked here like every other storage call this action
    # makes, none of these items are ore/ice so an empty result is correct.
    monkeypatch.setattr(storage, "get_types_names_and_groups_bulk", lambda type_ids: {})
    monkeypatch.setattr(reprocessing, "resolve_type_id", refining_actions.resolve_type_id)
    monkeypatch.setattr(refining_actions, "_seller_roles", lambda tm: [])
    monkeypatch.setattr(
        ESIClient, "structure_order_stats_bulk_or_goonmetrics",
        lambda self, structure_id, type_ids, auth_roles, goonmetrics_market_slug: (
            {tid: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=None, buy_volume=0.0)
             for tid in type_ids}, False))

    paste = (
        "Item A\t10\tCharge\tMaterial\t\t\t0.01 m3\t\t\n"
        "Item B\t20\tCharge\tMaterial\t\t\t0.01 m3\t\t\n"
        "Item A\t5\tCharge\tMaterial\t\t\t0.01 m3\t\t"
    )
    refining_actions.do_quote_reprocessing(paste, trading_cfg=trading_cfg)

    # Two distinct names, even though Item A appears twice (and used to be
    # resolved once in the type_ids pass and again per evaluate_reprocessing_line).
    assert searches == ["Item A", "Item B"]


def test_quote_reprocessing_returns_mineral_totals_aggregated_across_reprocess_rows(monkeypatch):
    """The Reprocessing tab used to price the mineral basket as one ISK
    figure without ever naming the minerals themselves (real user feedback,
    2026-09-27, testing a ratting-loot+salvage paste: "eine Tabelle die
    sagt welche Minerals dabei rauskommen, wär hilfreich"). mineral_totals
    aggregates every REPROCESS-decision row's own minerals dict (same scope
    as totals.total_mineral_value) into one per-mineral quantity/value table."""
    from eve_trader.esi_client import OrderStats
    from eve_trader.refining import reprocessing
    from eve_trader.refining.config import RefiningConfig

    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure", structure_sell_haircut=1.0)
    refining_cfg = RefiningConfig(scrapmetal_processing_skill_level=5, refining_tax_rate=0.0)  # 55% yield, no tax

    by_name = {"Item A": 100, "Item B": 200}
    materials_by_type = {100: [(35, 1.0)], 200: [(35, 2.0)]}  # both refine into Tritanium (35)
    portion_size_by_type = {100: 1, 200: 1}

    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: (
        [(by_name[name], name)] if name in by_name else []))
    monkeypatch.setattr(storage, "get_type_materials_bulk",
                         lambda type_ids: {tid: materials_by_type.get(tid, []) for tid in type_ids})
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: portion_size_by_type.get(type_id))
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: materials_by_type.get(type_id, []))
    monkeypatch.setattr(storage, "get_types_names_and_groups_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_sde_types_bulk",
                         lambda type_ids: {35: (35, 0, "Tritanium", 0.01, True, None, None, None)})
    monkeypatch.setattr(reprocessing, "resolve_type_id", refining_actions.resolve_type_id)
    monkeypatch.setattr(refining_actions, "_seller_roles", lambda tm: [])

    # Cheap items, valuable Tritanium - both rows come back REPROCESS_DECISION.
    item_stats = OrderStats(sell_percentile=0.01, sell_volume=1.0, buy_percentile=None, buy_volume=0.0)
    tritanium_stats = OrderStats(sell_percentile=5.0, sell_volume=1.0, buy_percentile=None, buy_volume=0.0)
    monkeypatch.setattr(
        ESIClient, "structure_order_stats_bulk_or_goonmetrics",
        lambda self, structure_id, type_ids, auth_roles, goonmetrics_market_slug: (
            {tid: (tritanium_stats if tid == 35 else item_stats) for tid in type_ids}, False))

    paste = "Item A\t100\tCharge\tMaterial\t\t\t0.01 m3\t\t\nItem B\t50\tCharge\tMaterial\t\t\t0.01 m3\t\t"
    result = refining_actions.do_quote_reprocessing(paste, trading_cfg=trading_cfg, refining_cfg=refining_cfg)

    assert [r["decision"] for r in result["rows"]] == ["Reprocess", "Reprocess"]
    # Item A: floor(100 x 1.0 x 0.55) = 55 Tritanium; Item B: floor(50 x 2.0 x 0.55) = 55 Tritanium.
    assert result["mineral_totals"] == [
        {"type_id": 35, "name": "Tritanium", "quantity": 110, "unit_sell_price": 5.0, "value": 550.0},
    ]


def test_quote_reprocessing_totals_scope_sell_as_is_to_reprocess_rows(monkeypatch):
    """Real user feedback (2026-09-27): total_sell_as_is_value used to sum
    every parsed row regardless of decision, while total_mineral_value/
    total_refined_value only ever covered the REPROCESS_DECISION subset -
    three cards that looked like a matched set but weren't comparable.
    total_sell_as_is_value is now scoped to the same reprocess_rows (a fair
    comparison against total_refined_value for that subset), and a new
    total_batch_value_optimal = total_refined_value + Sell-As-Is for every
    non-reprocess row answers what the old, batch-wide total was actually
    reaching for: the whole paste's value if you follow each item's own
    recommendation."""
    from eve_trader.esi_client import OrderStats
    from eve_trader.refining import reprocessing
    from eve_trader.refining.config import RefiningConfig

    trading_cfg = TradingConfig(structure_id=1000, structure_market_slug="my-structure", structure_sell_haircut=1.0)
    refining_cfg = RefiningConfig(scrapmetal_processing_skill_level=5, refining_tax_rate=0.0)  # 55% yield, no tax

    by_name = {"Item A": 100, "Item B": 200}
    # Item A: cheap to sell, refines into a lot of Tritanium -> Reprocess.
    # Item B: refines into essentially nothing, but sells for real ISK as-is -> Sell instead.
    materials_by_type = {100: [(35, 1.0)], 200: [(35, 0.01)]}
    portion_size_by_type = {100: 1, 200: 1}
    item_stats_by_type = {
        100: OrderStats(sell_percentile=0.01, sell_volume=1.0, buy_percentile=None, buy_volume=0.0),
        200: OrderStats(sell_percentile=1.0, sell_volume=1.0, buy_percentile=None, buy_volume=0.0),
    }
    tritanium_stats = OrderStats(sell_percentile=5.0, sell_volume=1.0, buy_percentile=None, buy_volume=0.0)

    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: (
        [(by_name[name], name)] if name in by_name else []))
    monkeypatch.setattr(storage, "get_type_materials_bulk",
                         lambda type_ids: {tid: materials_by_type.get(tid, []) for tid in type_ids})
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: portion_size_by_type.get(type_id))
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: materials_by_type.get(type_id, []))
    monkeypatch.setattr(storage, "get_types_names_and_groups_bulk", lambda type_ids: {})
    monkeypatch.setattr(storage, "get_sde_types_bulk",
                         lambda type_ids: {35: (35, 0, "Tritanium", 0.01, True, None, None, None)})
    monkeypatch.setattr(reprocessing, "resolve_type_id", refining_actions.resolve_type_id)
    monkeypatch.setattr(refining_actions, "_seller_roles", lambda tm: [])
    monkeypatch.setattr(
        ESIClient, "structure_order_stats_bulk_or_goonmetrics",
        lambda self, structure_id, type_ids, auth_roles, goonmetrics_market_slug: (
            {tid: (tritanium_stats if tid == 35 else item_stats_by_type[tid]) for tid in type_ids}, False))

    paste = "Item A\t100\tCharge\tMaterial\t\t\t0.01 m3\t\t\nItem B\t10\tCharge\tMaterial\t\t\t0.01 m3\t\t"
    result = refining_actions.do_quote_reprocessing(paste, trading_cfg=trading_cfg, refining_cfg=refining_cfg)

    assert [(r["name"], r["decision"]) for r in result["rows"]] == [
        ("Item A", "Reprocess"), ("Item B", "Sell instead"),
    ]
    # Item A: sell_as_is = 100 x 0.01 = 1.0; minerals = floor(100 x 1.0 x 0.55) = 55 -> mineral/refined value = 55 x 5.0 = 275.0
    # Item B: sell_as_is = 10 x 1.0 = 10.0; minerals = floor(10 x 0.01 x 0.55) = 0 -> refined_value 0, so "Sell instead"
    assert result["totals"]["total_mineral_value"] == pytest.approx(275.0)
    assert result["totals"]["total_refined_value"] == pytest.approx(275.0)
    # Scoped to Item A only now, not Item B's 10.0 too.
    assert result["totals"]["total_sell_as_is_value"] == pytest.approx(1.0)
    # Item A reprocessed (275.0) + Item B sold as-is (10.0), the optimal per-item outcome for the whole paste.
    assert result["totals"]["total_batch_value_optimal"] == pytest.approx(285.0)
