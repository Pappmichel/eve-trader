"""Tests for eve_trader/refining/reprocessing.py - GitHub issue #92's
scrapmetal-path quote calculation. Pure/no Postgres needed - storage lookups
are monkeypatched."""
import pytest

from eve_trader import storage
from eve_trader.config import TradingConfig
from eve_trader.esi_client import OrderStats
from eve_trader.refining.config import RefiningConfig
from eve_trader.refining.paste_parser import ParsedPasteLine
from eve_trader.refining.reprocessing import (
    NOT_REPROCESSABLE_DECISION, NO_MARKET_DATA_DECISION, REPROCESS_DECISION, SELL_DECISION,
    UNRESOLVED_DECISION, evaluate_reprocessing_line, mineral_type_ids_for_lines, resolve_type_id,
)


@pytest.fixture
def trading_cfg():
    return TradingConfig(structure_sell_haircut=0.9463)


@pytest.fixture
def refining_cfg():
    return RefiningConfig(scrapmetal_processing_skill_level=5, refining_tax_rate=0.0)  # 55% yield


def _line(name="Antimatter Charge S", quantity=1000):
    return ParsedPasteLine(raw_line="", name=name, quantity=quantity, category="Charge", volume_m3=0.0025)


def test_resolve_type_id_requires_exact_case_insensitive_match(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [(34, "Compressed Veldspar")])
    assert resolve_type_id("compressed veldspar") == 34
    assert resolve_type_id("Compressed Veld") is None  # substring match only - not exact


def test_resolve_type_id_no_candidates_returns_none(monkeypatch):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [])
    assert resolve_type_id("Nonexistent Item") is None


def test_evaluate_line_with_parse_error_is_unresolved():
    error_line = ParsedPasteLine(raw_line="bad", name="bad", quantity=0, category="", volume_m3=None,
                                  error="Not tab-separated")
    row = evaluate_reprocessing_line(error_line, None, {})
    assert row.decision == UNRESOLVED_DECISION
    assert row.error == "Not tab-separated"


def test_evaluate_line_unresolved_item_name(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [])
    row = evaluate_reprocessing_line(_line(), None, {}, trading_cfg, refining_cfg)
    assert row.decision == UNRESOLVED_DECISION
    assert row.type_id is None


def test_evaluate_line_not_reprocessable_when_no_portion_size(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [(100, "Antimatter Charge S")])
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: None)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [])
    item_stats = OrderStats(sell_percentile=1.0, sell_volume=1000.0, buy_percentile=None, buy_volume=0.0)
    row = evaluate_reprocessing_line(_line(), item_stats, {}, trading_cfg, refining_cfg)
    assert row.decision == NOT_REPROCESSABLE_DECISION
    # still priced, even though not reprocessable - 1.0 x 1000 x 0.9463 haircut
    assert row.sell_as_is_value == pytest.approx(946.3)


def test_evaluate_line_no_market_data_when_item_unpriced(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [(100, "Antimatter Charge S")])
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 0.1)])
    row = evaluate_reprocessing_line(_line(), None, {}, trading_cfg, refining_cfg)
    assert row.decision == NO_MARKET_DATA_DECISION


def test_evaluate_line_recommends_reprocess_when_refined_value_higher(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [(100, "Antimatter Charge S")])
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 0.01)])  # 0.01 Tritanium/unit
    item_stats = OrderStats(sell_percentile=0.01, sell_volume=1000.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=1000.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_reprocessing_line(_line(quantity=1000), item_stats, {35: tritanium}, trading_cfg, refining_cfg)

    # sell_as_is = 1000 x 0.01 x 0.9463 = 9.463; minerals = floor(1000 x 0.01 x 0.55) = 5;
    # mineral_value = 5 x 1000 x 0.9463 = 4731.5 >> sell_as_is
    assert row.decision == REPROCESS_DECISION
    assert row.refined_value > row.sell_as_is_value


def test_evaluate_line_recommends_sell_instead_when_sell_as_is_higher(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [(100, "Antimatter Charge S")])
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 0.01)])
    item_stats = OrderStats(sell_percentile=1000.0, sell_volume=1000.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=1.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_reprocessing_line(_line(quantity=1), item_stats, {35: tritanium}, trading_cfg, refining_cfg)

    assert row.decision == SELL_DECISION
    assert row.sell_as_is_value > row.refined_value


def test_evaluate_line_refining_tax_reduces_refined_value(monkeypatch, trading_cfg):
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [(100, "Antimatter Charge S")])
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 0.01)])
    item_stats = OrderStats(sell_percentile=0.01, sell_volume=1000.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=1000.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    no_tax = evaluate_reprocessing_line(_line(quantity=1000), item_stats, {35: tritanium}, trading_cfg,
                                         RefiningConfig(scrapmetal_processing_skill_level=5, refining_tax_rate=0.0))
    with_tax = evaluate_reprocessing_line(_line(quantity=1000), item_stats, {35: tritanium}, trading_cfg,
                                           RefiningConfig(scrapmetal_processing_skill_level=5, refining_tax_rate=0.1))

    assert with_tax.refined_value < no_tax.refined_value
    assert with_tax.refining_tax > 0


def test_evaluate_line_sell_as_is_value_includes_structure_sell_haircut(monkeypatch, trading_cfg, refining_cfg):
    """Regression test for a confirmed real bug (business-logic audit,
    2026-08-29): sell_as_is_value used to be a bare gross quantity x price,
    while refined_value already netted out structure_sell_haircut on the
    mineral side - both options end with a C-J sell order, so both must
    incur the same fee, or the comparison is apples-to-oranges."""
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [(100, "Antimatter Charge S")])
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: None)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [])
    item_stats = OrderStats(sell_percentile=1.0, sell_volume=1000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_reprocessing_line(_line(quantity=1000), item_stats, {}, trading_cfg, refining_cfg)

    assert row.sell_as_is_value == pytest.approx(1000 * 1.0 * trading_cfg.structure_sell_haircut)


def test_evaluate_line_reprocess_decision_flips_once_fee_consistency_is_fixed(monkeypatch, trading_cfg):
    """Concrete PB-01 scenario: refined_value sits strictly between the net
    (haircut-adjusted) and gross sell-as-is values - the old, buggy formula
    (bare gross sell_as_is, no haircut) would have wrongly said "Sell
    instead" here; the fixed formula must say "Reprocess"."""
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=5: [(100, "Antimatter Charge S")])
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 0.19)])
    refining_cfg = RefiningConfig(scrapmetal_processing_skill_level=5, refining_tax_rate=0.0)  # 55% yield
    item_stats = OrderStats(sell_percentile=1.0, sell_volume=1000.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.5, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_reprocessing_line(_line(quantity=100), item_stats, {35: tritanium}, trading_cfg, refining_cfg)

    gross_sell_as_is = 100 * 1.0  # what the old, buggy formula would have compared against
    net_sell_as_is = gross_sell_as_is * trading_cfg.structure_sell_haircut  # 94.63
    # minerals = floor(100 x 0.19 x 0.55) = 10; mineral_value = 10 x 10.5 x 0.9463 = 99.3615
    assert net_sell_as_is < row.refined_value < gross_sell_as_is
    assert row.sell_as_is_value == pytest.approx(net_sell_as_is)
    assert row.decision == REPROCESS_DECISION  # would have been SELL_DECISION before the fix


def test_mineral_type_ids_for_lines_collects_union(monkeypatch):
    def fake_materials(type_id):
        return [(35, 1.0), (36, 2.0)] if type_id == 100 else [(35, 3.0)]
    monkeypatch.setattr(storage, "get_type_materials", fake_materials)
    assert mineral_type_ids_for_lines([100, 200]) == [35, 36]
