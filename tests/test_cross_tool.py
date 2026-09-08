"""Tests for eve_trader/cross_tool.py's do_sorting_list - the Wareneingang/
hangar-sorting helper (GitHub issue #90-era work, see cross_tool.py's own
module docstring). Unit-level: every storage/engine call it makes is
monkeypatched rather than exercised against a real Postgres schema (unlike
tests/test_storage_stock.py's esi_stock_at_location/assets_at_flag tests,
which are genuine DB-level tests) - do_sorting_list itself is pure
aggregation glue over already-tested lower-level functions, so this only
needs to confirm the glue is correct.
"""
from __future__ import annotations

import pandas as pd
import pytest

from eve_trader import cross_tool, storage
from eve_trader.config import TradingConfig
from eve_trader.doctrine import engine as doctrine_engine
from eve_trader.doctrine.config import DoctrineConfig
from eve_trader.doctrine.models import StockpileRow
from eve_trader.production.config import ProductionConfig


def _empty_snapshot_df() -> pd.DataFrame:
    return pd.DataFrame()


@pytest.fixture(autouse=True)
def _stub_everything(monkeypatch):
    """Every do_sorting_list call site defaults to "nothing here" - each
    test below overrides just the one or two it cares about."""
    monkeypatch.setattr(storage, "latest_snapshot", _empty_snapshot_df)
    monkeypatch.setattr(storage, "load_manual_stock", lambda: {})
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [])
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id, allowed_flags=None: 0.0)
    monkeypatch.setattr(doctrine_engine, "stockpile_rows_for_doctrine", lambda cfg=None: ([], False))
    monkeypatch.setattr(cross_tool, "stockpile_rows_for_doctrine", lambda cfg=None: ([], False))
    monkeypatch.setattr(storage, "load_mineral_requirements", lambda: [])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))


def test_empty_intake_hangar_flag_returns_empty_list():
    cfg = TradingConfig(intake_hangar_flag="")

    result = cross_tool.do_sorting_list(cfg=cfg)

    assert result == {"rows": []}


def test_empty_intake_returns_empty_list(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [])

    result = cross_tool.do_sorting_list(cfg=cfg)

    assert result == {"rows": []}


def test_item_nobody_wants_is_included_and_marked_unclaimed(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(34, 500.0)])

    result = cross_tool.do_sorting_list(cfg=cfg)

    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["type_id"] == 34
    assert row["intake_qty"] == 500.0
    assert row["wanted_by_tool"] == []
    assert row["unclaimed"] is True


def test_trading_wanted_qty_from_import_decision_snapshot(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "decision": "Import", "avg_daily_volume": 300.0, "sell_volume": 50.0,
         "own_orders_remaining": 20.0},
    ]))

    result = cross_tool.do_sorting_list(cfg=cfg)

    row = result["rows"][0]
    assert row["wanted_by_tool"] == [{"tool": "trading", "wanted_qty": 230.0}]  # 300 - 50 - 20
    assert row["unclaimed"] is False


def test_trading_ignores_non_import_decisions(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "latest_snapshot", lambda: pd.DataFrame([
        {"item_id": 34, "decision": "Skip", "avg_daily_volume": 300.0, "sell_volume": 0.0,
         "own_orders_remaining": 0.0},
    ]))

    result = cross_tool.do_sorting_list(cfg=cfg)

    assert result["rows"][0]["unclaimed"] is True


def test_production_wanted_qty_from_stock_target_backup_minus_ist(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    production_cfg = ProductionConfig(home_location_id=1000000000001, stock_hangar_flags=("CorpSAG1",))
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(34, "Tritanium", 200.0, None, None)])
    monkeypatch.setattr(storage, "load_manual_stock", lambda: {34: 10.0})

    def fake_stock(type_id, location_id, allowed_flags=None):
        assert location_id == 1000000000001
        assert allowed_flags == ("CorpSAG1",)
        return 40.0
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    result = cross_tool.do_sorting_list(cfg=cfg, production_cfg=production_cfg)

    # backup_stock 200 - (manual 10 + esi 40 = 50) = 150
    assert result["rows"][0]["wanted_by_tool"] == [{"tool": "production", "wanted_qty": 150.0}]


def test_doctrine_wanted_qty_sums_shortfall_across_fittings(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(34, 500.0)])
    rows = [
        StockpileRow(fitting_id="f1", fitting_name="Fit 1", doctrine_id="d1", doctrine_name="Doctrine 1",
                     type_id=34, type_name="Tritanium", slot_section="drone/cargo/charge",
                     required_total=100.0, available=20.0, shortfall=80.0, severity="critical"),
        StockpileRow(fitting_id="f2", fitting_name="Fit 2", doctrine_id="d1", doctrine_name="Doctrine 1",
                     type_id=34, type_name="Tritanium", slot_section="drone/cargo/charge",
                     required_total=50.0, available=50.0, shortfall=0.0, severity=None),
    ]
    monkeypatch.setattr(cross_tool, "stockpile_rows_for_doctrine", lambda cfg=None: (rows, True))

    result = cross_tool.do_sorting_list(cfg=cfg)

    assert result["rows"][0]["wanted_by_tool"] == [{"tool": "doctrine", "wanted_qty": 80.0}]


def test_ore_minerals_wanted_qty_from_mineral_requirements(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "load_mineral_requirements", lambda: [(34, "Tritanium", 75.0)])

    result = cross_tool.do_sorting_list(cfg=cfg)

    assert result["rows"][0]["wanted_by_tool"] == [{"tool": "ore_minerals", "wanted_qty": 75.0}]


def test_multiple_tools_can_each_want_more_than_is_actually_in_the_intake(monkeypatch):
    # No reservation/allocation logic (see CLAUDE.md's "Nicht tun") - the
    # backend just reports each tool's own raw demand; it's up to the human
    # sorting the hangar to see that 300+75 > 500 and decide by hand.
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(34, 100.0)])
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(34, "Tritanium", 300.0, None, None)])
    monkeypatch.setattr(storage, "load_mineral_requirements", lambda: [(34, "Tritanium", 75.0)])

    result = cross_tool.do_sorting_list(cfg=cfg)

    row = result["rows"][0]
    assert row["intake_qty"] == 100.0
    wanted = {w["tool"]: w["wanted_qty"] for w in row["wanted_by_tool"]}
    assert wanted == {"production": 300.0, "ore_minerals": 75.0}
    assert row["unclaimed"] is False


def test_type_name_resolved_from_sde(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(34, 500.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (34, 18, "Tritanium", 0.01, 1, None, None, None))

    result = cross_tool.do_sorting_list(cfg=cfg)

    assert result["rows"][0]["type_name"] == "Tritanium"


def test_type_name_falls_back_to_type_id_when_sde_unknown(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(999999, 1.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: None)

    result = cross_tool.do_sorting_list(cfg=cfg)

    assert result["rows"][0]["type_name"] == "999999"


def test_doctrine_wanted_empty_when_no_doctrine_assets_synced(monkeypatch):
    cfg = TradingConfig(intake_hangar_flag="CorpSAG1")
    monkeypatch.setattr(storage, "assets_at_flag", lambda flag, tables=None: [(34, 500.0)])
    doctrine_cfg = DoctrineConfig()
    monkeypatch.setattr(cross_tool, "stockpile_rows_for_doctrine", lambda cfg=None: ([], False))

    result = cross_tool.do_sorting_list(cfg=cfg, doctrine_cfg=doctrine_cfg)

    assert result["rows"][0]["unclaimed"] is True
