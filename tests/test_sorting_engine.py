"""Tests for eve_trader/sorting/engine.py's do_sorting_list - the
Wareneingang/hangar-sorting helper. Unit-level: every storage/engine call
it makes is monkeypatched rather than exercised against a real Postgres
schema (unlike tests/test_storage_stock.py and tests/test_storage_sorting.py,
which are genuine DB-level tests) - do_sorting_list itself is pure
aggregation glue over already-tested lower-level functions, so this only
needs to confirm the glue is correct.
"""
from __future__ import annotations

import pandas as pd
import pytest

from eve_trader import storage
from eve_trader.models import ShortlistItem
from eve_trader.doctrine.config import DoctrineConfig
from eve_trader.doctrine.models import StockpileRow
from eve_trader.production.config import ProductionConfig
from eve_trader.sorting import engine as sorting_engine
from eve_trader.sorting.actions import ActionError, do_add_intake_source


def _empty_snapshot_df() -> pd.DataFrame:
    return pd.DataFrame()


def _one_corp_source():
    return [(1, "corp", "RichlTech (corp)", "CorpSAG1", "Intake")]


@pytest.fixture(autouse=True)
def _stub_everything(monkeypatch):
    """Every do_sorting_list call site defaults to 'nothing here' - each
    test below overrides just the one or two it cares about."""
    monkeypatch.setattr(storage, "load_sorting_intake_sources", lambda: [])
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [])
    monkeypatch.setattr(storage, "latest_snapshot", _empty_snapshot_df)
    monkeypatch.setattr(storage, "load_shortlist", lambda: [])
    monkeypatch.setattr(storage, "load_manual_stock", lambda: {})
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    monkeypatch.setattr(storage, "load_latest_buy_list", lambda: {})
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id, allowed_flags=None, exclude_intake_at_location_id=None: 0.0)
    monkeypatch.setattr(storage, "sell_order_qty_at_location", lambda type_id, location_id: 0.0)
    monkeypatch.setattr(storage, "sell_order_qty_in_region", lambda type_id, region_id: 0.0)
    monkeypatch.setattr(sorting_engine, "stockpile_rows_for_doctrine", lambda cfg=None: ([], False))
    monkeypatch.setattr(storage, "load_mineral_requirements", lambda: [])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))


def test_no_intake_sources_returns_empty_list():
    result = sorting_engine.do_sorting_list()
    assert result == {"rows": []}


def test_empty_intake_returns_empty_list(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [])

    result = sorting_engine.do_sorting_list()

    assert result == {"rows": []}


def test_item_nobody_wants_is_included_and_marked_unclaimed(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])

    result = sorting_engine.do_sorting_list()

    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["type_id"] == 34
    assert row["intake_qty"] == 500.0
    assert row["wanted_by_tool"] == []
    assert row["unclaimed"] is True
    assert row["by_source"] == [{"source_label": "Intake", "qty": 500.0}]


def test_trading_wanted_qty_from_import_decision_snapshot(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "decision": "Import", "avg_daily_volume": 300.0, "sell_volume": 50.0,
         "own_orders_remaining": 0.0},
    ]))

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    # sell_volume is listed qty, not a reason to drop hangar demand
    assert row["wanted_by_tool"] == [{"tool": "trading", "wanted_qty": 300.0}]
    assert row["unclaimed"] is False


def test_trading_still_wants_item_when_listings_cover_daily_volume(monkeypatch):
    # The stack in Wareneingang still belongs in the market hangar even
    # when open sell orders already cover avg_daily_volume (no further
    # import needed today).
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "decision": "Import", "avg_daily_volume": 300.0, "sell_volume": 400.0,
         "own_orders_remaining": 0.0},
    ]))

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    assert row["wanted_by_tool"] == [{"tool": "trading", "wanted_qty": 300.0}]
    assert row["unclaimed"] is False


def test_trading_already_ordered_still_claims_market_hangar(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "decision": "Already ordered", "avg_daily_volume": 80.0, "sell_volume": 80.0,
         "own_orders_remaining": 10.0},
    ]))

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    assert row["wanted_by_tool"] == [{"tool": "trading", "wanted_qty": 80.0}]
    assert row["unclaimed"] is False


def test_trading_import_with_no_avg_daily_volume_still_claimed(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "decision": "Import", "avg_daily_volume": 0.0, "sell_volume": 50.0,
         "own_orders_remaining": 0.0},
    ]))

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    assert row["wanted_by_tool"] == [{"tool": "trading", "wanted_qty": 1.0}]
    assert row["unclaimed"] is False


def test_trading_ignores_snapshot_skip_when_not_on_shortlist(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "decision": "Skip", "avg_daily_volume": 300.0, "sell_volume": 0.0,
         "own_orders_remaining": 0.0},
    ]))

    result = sorting_engine.do_sorting_list()

    assert result["rows"][0]["unclaimed"] is True


def test_active_shortlist_item_is_claimed_for_trading_even_when_snapshot_says_skip(monkeypatch):
    # shortlist._decision returns Skip when sell_volume is 0 (no C-J
    # listings yet) even for a profitable import-list item. Sorting still
    # sends that stack to the Trading hangar so it can be listed.
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "decision": "Skip", "avg_daily_volume": 300.0, "sell_volume": 0.0,
         "own_orders_remaining": 0.0},
    ]))
    monkeypatch.setattr(storage, "load_shortlist", lambda: [
        ShortlistItem(item="Tritanium", item_id=34, category="Material", volume_m3=0.01, active=True),
    ])

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    assert row["wanted_by_tool"] == [{"tool": "trading", "wanted_qty": 300.0}]
    assert row["unclaimed"] is False


def test_snapshot_import_still_claims_when_live_shortlist_is_inactive(monkeypatch):
    # The Shortlist page renders latest_snapshot(), so an Import row there
    # is what the operator sees as "on the import list". GitHub issue #35
    # left Booster/Drugs items inactive on the live shortlist while the
    # snapshot still said Import — Sorting used to drop those as nobody.
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(33332, 12.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 33332, "item": "Navy Cap Booster 3200", "decision": "Import",
         "avg_daily_volume": 40.0, "sell_volume": 80.0, "own_orders_remaining": 0.0},
    ]))
    monkeypatch.setattr(storage, "load_shortlist", lambda: [
        ShortlistItem(item="Navy Cap Booster 3200", item_id=33332, category="Charge",
                      volume_m3=0.01, active=False),
    ])
    monkeypatch.setattr(storage, "get_sde_type",
                        lambda type_id: (type_id, 1, "Navy Cap Booster 3200", 0.01, 1, 1, 0, None))

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    assert row["type_name"] == "Navy Cap Booster 3200"
    assert row["wanted_by_tool"] == [{"tool": "trading", "wanted_qty": 40.0}]
    assert row["unclaimed"] is False


def test_trading_matches_snapshot_import_by_item_name_when_type_ids_differ(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(33332, 12.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 99999, "item": "Navy Cap Booster 3200", "decision": "Import",
         "avg_daily_volume": 40.0, "sell_volume": 80.0, "own_orders_remaining": 0.0},
    ]))
    monkeypatch.setattr(storage, "get_sde_type",
                        lambda type_id: (type_id, 1, "Navy Cap Booster 3200", 0.01, 1, 1, 0, None))

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    assert row["wanted_by_tool"] == [{"tool": "trading", "wanted_qty": 40.0}]
    assert row["unclaimed"] is False


def test_snapshot_inactive_without_active_shortlist_stays_unclaimed(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "item": "Tritanium", "decision": "Inactive",
         "avg_daily_volume": 300.0, "sell_volume": 50.0, "own_orders_remaining": 0.0},
    ]))
    monkeypatch.setattr(storage, "load_shortlist", lambda: [
        ShortlistItem(item="Tritanium", item_id=34, category="Material", volume_m3=0.01, active=False),
    ])

    result = sorting_engine.do_sorting_list()

    assert result["rows"][0]["unclaimed"] is True


def test_active_shortlist_item_without_snapshot_is_still_claimed(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "load_shortlist", lambda: [
        ShortlistItem(item="Tritanium", item_id=34, category="Material", volume_m3=0.01, active=True),
    ])

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    assert row["wanted_by_tool"] == [{"tool": "trading", "wanted_qty": 1.0}]
    assert row["unclaimed"] is False


def test_material_wanted_qty_from_persisted_buy_list_not_stock_targets(monkeypatch):
    # The reported bug: a real buy-list material (Mexallon, Platinum, ...) is
    # never itself a stock_targets row - those are finished products. Sorting
    # must still claim it via the persisted plan_production buy list.
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(36, 500.0)])
    monkeypatch.setattr(storage, "load_latest_buy_list", lambda: {36: 21100000.0})
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    assert row["type_id"] == 36
    assert row["wanted_by_tool"] == [{"tool": "material", "wanted_qty": 21100000.0}]
    assert row["unclaimed"] is False


def test_material_wanted_empty_when_no_buy_list_saved(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(36, 500.0)])
    monkeypatch.setattr(storage, "load_latest_buy_list", lambda: {})
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(36, "Mexallon", 200.0, None, None)])

    result = sorting_engine.do_sorting_list()

    assert result["rows"][0]["wanted_by_tool"] == []
    assert result["rows"][0]["unclaimed"] is True


def test_trading_and_production_listing_are_separate_pots(monkeypatch):
    production_cfg = ProductionConfig(home_location_id=1000000000001)
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "decision": "Import", "avg_daily_volume": 100.0, "sell_volume": 0.0,
         "own_orders_remaining": 0.0},
    ]))
    # home_market_stock=40, no current stock, no listings -> market shortfall 40
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(34, "Tritanium", 0.0, 40.0, None)])

    result = sorting_engine.do_sorting_list(production_cfg=production_cfg)

    wanted = {w["tool"]: w["wanted_qty"] for w in result["rows"][0]["wanted_by_tool"]}
    assert wanted == {"trading": 100.0, "markt": 40.0}


def test_doctrine_wanted_qty_sums_shortfall_across_fittings(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    rows = [
        StockpileRow(fitting_id="f1", fitting_name="Fit 1", doctrine_id="d1", doctrine_name="Doctrine 1",
                     type_id=34, type_name="Tritanium", slot_section="drone/cargo/charge",
                     required_total=100.0, available=20.0, shortfall=80.0, severity="critical"),
        StockpileRow(fitting_id="f2", fitting_name="Fit 2", doctrine_id="d1", doctrine_name="Doctrine 1",
                     type_id=34, type_name="Tritanium", slot_section="drone/cargo/charge",
                     required_total=50.0, available=50.0, shortfall=0.0, severity=None),
    ]
    monkeypatch.setattr(sorting_engine, "stockpile_rows_for_doctrine", lambda cfg=None: (rows, True))

    result = sorting_engine.do_sorting_list()

    assert result["rows"][0]["wanted_by_tool"] == [{"tool": "doctrine", "wanted_qty": 80.0}]


def test_ore_minerals_wanted_qty_from_mineral_requirements(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "load_mineral_requirements", lambda: [(34, "Tritanium", 75.0)])

    result = sorting_engine.do_sorting_list()

    assert result["rows"][0]["wanted_by_tool"] == [{"tool": "ore_minerals", "wanted_qty": 75.0}]


def test_multiple_pots_can_each_want_more_than_is_actually_in_the_intake(monkeypatch):
    # No reservation/allocation logic - the backend just reports each pot's
    # own raw demand; it's up to the human sorting the hangar to see that
    # 300+75 > 500 and decide by hand.
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 100.0)])
    monkeypatch.setattr(storage, "load_latest_buy_list", lambda: {34: 300.0})
    monkeypatch.setattr(storage, "load_mineral_requirements", lambda: [(34, "Tritanium", 75.0)])

    result = sorting_engine.do_sorting_list()

    row = result["rows"][0]
    assert row["intake_qty"] == 100.0
    wanted = {w["tool"]: w["wanted_qty"] for w in row["wanted_by_tool"]}
    assert wanted == {"material": 300.0, "ore_minerals": 75.0}
    assert row["unclaimed"] is False


def test_by_source_keeps_two_characters_and_a_corp_division_separate(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", lambda: [
        (1, "character", "Alice", "Hangar", None),
        (2, "character", "Bob", "Hangar", None),
        (3, "corp", "RichlTech (corp)", "CorpSAG3", "Corp intake"),
    ])

    def fake_assets(flag, tables=(), owner_name=None, location_id=None):
        if tables == ("character_assets",) and owner_name == "Alice":
            return [(34, 10.0)]
        if tables == ("character_assets",) and owner_name == "Bob":
            return [(34, 20.0)]
        if tables == ("corp_assets",) and owner_name == "RichlTech (corp)":
            return [(34, 5.0), (35, 7.0)]
        return []
    monkeypatch.setattr(storage, "assets_at_flag", fake_assets)

    result = sorting_engine.do_sorting_list()

    by_type = {r["type_id"]: r for r in result["rows"]}
    trit = by_type[34]
    assert trit["intake_qty"] == 35.0
    assert trit["by_source"] == [
        {"source_label": "Alice (Hangar)", "qty": 10.0},
        {"source_label": "Bob (Hangar)", "qty": 20.0},
        {"source_label": "Corp intake", "qty": 5.0},
    ]
    assert by_type[35]["intake_qty"] == 7.0
    assert by_type[35]["by_source"] == [{"source_label": "Corp intake", "qty": 7.0}]


def test_type_name_resolved_from_sde(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (34, 18, "Tritanium", 0.01, 1, None, None, None))

    result = sorting_engine.do_sorting_list()

    assert result["rows"][0]["type_name"] == "Tritanium"


def test_type_name_falls_back_to_type_id_when_sde_unknown(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(999999, 1.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)

    result = sorting_engine.do_sorting_list()

    assert result["rows"][0]["type_name"] == "999999"


def test_doctrine_wanted_empty_when_no_doctrine_assets_synced(monkeypatch):
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=(), owner_name=None, location_id=None: [(34, 500.0)])
    doctrine_cfg = DoctrineConfig()
    monkeypatch.setattr(sorting_engine, "stockpile_rows_for_doctrine", lambda cfg=None: ([], False))

    result = sorting_engine.do_sorting_list(doctrine_cfg=doctrine_cfg)

    assert result["rows"][0]["unclaimed"] is True


def test_two_corp_sources_count_only_their_own_corp(monkeypatch):
    # The bug this covers: a corp intake source used to pass owner_name=None
    # to assets_at_flag, so two corps' CorpSAG1 stacks were silently summed.
    monkeypatch.setattr(storage, "load_sorting_intake_sources", lambda: [
        (1, "corp", "RichlTech (corp)", "CorpSAG1", None),
        (2, "corp", "building mining and research corporation (corp)", "CorpSAG1", None),
    ])

    def fake_assets(flag, tables=(), owner_name=None, location_id=None):
        assert flag == "CorpSAG1"
        assert tables == ("corp_assets",)
        if owner_name == "RichlTech (corp)":
            return [(34, 100.0)]
        if owner_name == "building mining and research corporation (corp)":
            return [(34, 40.0), (35, 8.0)]
        return []
    monkeypatch.setattr(storage, "assets_at_flag", fake_assets)

    result = sorting_engine.do_sorting_list()

    by_type = {r["type_id"]: r for r in result["rows"]}
    trit = by_type[34]
    assert trit["intake_qty"] == 140.0
    assert trit["by_source"] == [
        {"source_label": "RichlTech (corp) (Corp, CorpSAG1)", "qty": 100.0},
        {"source_label": "building mining and research corporation (corp) (Corp, CorpSAG1)", "qty": 40.0},
    ]
    assert by_type[35]["intake_qty"] == 8.0
    assert by_type[35]["by_source"] == [
        {"source_label": "building mining and research corporation (corp) (Corp, CorpSAG1)", "qty": 8.0},
    ]


def test_intake_scoped_to_production_home_location(monkeypatch):
    # The bug this covers: a character/corp routinely has cargo sitting in
    # a same-named hangar flag (e.g. "Hangar") at more than one station -
    # a Wareneingang source without a location filter counted all of them,
    # not just the C-J structure Production is actually configured for.
    monkeypatch.setattr(storage, "load_sorting_intake_sources", _one_corp_source)
    production_cfg = ProductionConfig(home_location_id=1000000000042)

    def fake_assets(flag, tables=(), owner_name=None, location_id=None):
        assert location_id == 1000000000042
        return [(34, 500.0)]
    monkeypatch.setattr(storage, "assets_at_flag", fake_assets)

    result = sorting_engine.do_sorting_list(production_cfg=production_cfg)

    assert result["rows"][0]["intake_qty"] == 500.0


def test_do_add_intake_source_rejects_unknown_kind(monkeypatch):
    with pytest.raises(ActionError, match="source_kind"):
        do_add_intake_source("alliance", "Hangar")


def test_do_add_intake_source_rejects_unknown_hangar_flag(monkeypatch):
    with pytest.raises(ActionError, match="hangar_flag"):
        do_add_intake_source("corp", "NotARealFlag", owner_name="RichlTech (corp)")


def test_do_add_intake_source_requires_owner_name(monkeypatch):
    with pytest.raises(ActionError, match="owner_name"):
        do_add_intake_source("character", "Hangar")
    with pytest.raises(ActionError, match="owner_name"):
        do_add_intake_source("corp", "CorpSAG1")


def test_do_add_intake_source_accepts_deliveries_as_intake_flag(monkeypatch):
    # Regression: "Deliveries" is a legitimate Wareneingang location (a
    # courier/market delivery routinely lands there) even though it must
    # NOT be a valid ProductionConfig.stock_hangar_flags/DoctrineConfig.
    # stockpile_hangar_flags choice (see production/constants.py's
    # INTAKE_HANGAR_FLAGS docstring) - do_add_intake_source validates
    # against INTAKE_HANGAR_FLAGS, not the narrower HANGAR_DIVISION_FLAGS.
    monkeypatch.setattr(storage, "add_sorting_intake_source", lambda *a, **kw: 1)

    result = do_add_intake_source("corp", "Deliveries", owner_name="RichlTech (corp)")

    assert result["hangar_flag"] == "Deliveries"
    assert result["owner_name"] == "RichlTech (corp)"
