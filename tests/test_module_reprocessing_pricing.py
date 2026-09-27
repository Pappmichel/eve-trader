"""Tests for eve_trader/module_reprocessing/pricing.py. Pure/no Postgres
needed - storage.get_portion_size/get_type_materials are monkeypatched,
same pattern as test_refining_pricing.py."""
import pytest

from eve_trader import storage
from eve_trader.config import TradingConfig
from eve_trader.esi_client import OrderStats
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.module_reprocessing.config import ModuleReprocessingConfig
from eve_trader.module_reprocessing.models import ModuleCandidate
from eve_trader.module_reprocessing.pricing import (
    estimate_discovered_candidate, evaluate_module_item, mineral_type_ids_for,
)


def _candidate(type_id=100, volume_m3=0.01):
    return ModuleCandidate(type_id=type_id, item="200mm AutoCannon I", volume_m3=volume_m3)


@pytest.fixture
def trading_cfg():
    return TradingConfig(jita_buy_broker_fee=0.0147, structure_sell_haircut=0.9463,
                          min_profit_threshold=0.0, min_margin_threshold=0.05)


@pytest.fixture
def cfg():
    return ModuleReprocessingConfig(freight_cost_per_m3=900.0, refining_tax_rate=0.0)


# ------------------------------------------------------- evaluate_module_item
def test_evaluate_module_item_no_market_data_when_no_buy_price(monkeypatch, trading_cfg, cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    row = evaluate_module_item(_candidate(), True, None, {}, trading_cfg, cfg)
    assert row.decision == "No market data"
    assert row.landed_cost is None


def test_evaluate_module_item_no_market_data_when_no_portion_size(monkeypatch, trading_cfg, cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: None)
    item_stats = OrderStats(sell_percentile=1000.0, sell_volume=5.0, buy_percentile=None, buy_volume=0.0)
    row = evaluate_module_item(_candidate(), True, item_stats, {}, trading_cfg, cfg)
    assert row.decision == "No market data"


def test_evaluate_module_item_missing_mineral_price_is_no_market_data(monkeypatch, trading_cfg, cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    item_stats = OrderStats(sell_percentile=1000.0, sell_volume=5.0, buy_percentile=None, buy_volume=0.0)
    row = evaluate_module_item(_candidate(), True, item_stats, {}, trading_cfg, cfg)
    assert row.decision == "No market data"
    assert row.mineral_value is None


def test_evaluate_module_item_profitable_is_import(monkeypatch, trading_cfg, cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    item_stats = OrderStats(sell_percentile=1.0, sell_volume=5.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_module_item(_candidate(volume_m3=0.001), True, item_stats, {34: tritanium}, trading_cfg, cfg)

    # yield_pct=0.50 (default ModuleReprocessingConfig, skill level 0) ->
    # minerals = floor(1x100x0.50)=50; mineral_value = 50x10x0.9463 = 473.15
    assert row.decision == "Import"
    assert row.yield_pct == pytest.approx(0.50)
    assert row.profit_per_unit is not None and row.profit_per_unit > 0


def test_evaluate_module_item_scrapmetal_skill_increases_yield(monkeypatch, trading_cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    item_stats = OrderStats(sell_percentile=1.0, sell_volume=5.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    unskilled = evaluate_module_item(_candidate(), True, item_stats, {34: tritanium}, trading_cfg,
                                      ModuleReprocessingConfig(scrapmetal_processing_skill_level=0))
    maxed = evaluate_module_item(_candidate(), True, item_stats, {34: tritanium}, trading_cfg,
                                  ModuleReprocessingConfig(scrapmetal_processing_skill_level=5))

    assert maxed.yield_pct == pytest.approx(0.55)
    assert unskilled.yield_pct == pytest.approx(0.50)
    assert maxed.mineral_value > unskilled.mineral_value


def test_evaluate_module_item_inactive_overrides_everything(monkeypatch, trading_cfg, cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    item_stats = OrderStats(sell_percentile=1.0, sell_volume=5.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_module_item(_candidate(volume_m3=0.001), False, item_stats, {34: tritanium}, trading_cfg, cfg)

    assert row.decision == "Inactive"


def test_evaluate_module_item_refining_tax_reduces_net_sell(monkeypatch, trading_cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    item_stats = OrderStats(sell_percentile=1.0, sell_volume=5.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    no_tax = evaluate_module_item(_candidate(volume_m3=0.001), True, item_stats, {34: tritanium}, trading_cfg,
                                   ModuleReprocessingConfig(refining_tax_rate=0.0))
    with_tax = evaluate_module_item(_candidate(volume_m3=0.001), True, item_stats, {34: tritanium}, trading_cfg,
                                     ModuleReprocessingConfig(refining_tax_rate=0.1))

    assert with_tax.net_sell < no_tax.net_sell
    assert with_tax.refining_tax > 0


def test_mineral_type_ids_for_collects_the_union_across_candidates(monkeypatch):
    def fake_materials(type_id):
        return [(34, 100.0), (35, 20.0)] if type_id == 100 else [(34, 50.0)]
    monkeypatch.setattr(storage, "get_type_materials", fake_materials)

    ids = mineral_type_ids_for([_candidate(type_id=100), _candidate(type_id=200)])

    assert ids == [34, 35]


# --------------------------------------------- estimate_discovered_candidate
def test_estimate_discovered_candidate_no_price_is_none(monkeypatch, trading_cfg, cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    result = estimate_discovered_candidate(_candidate(), None, {}, trading_cfg, cfg)
    assert result.est_landed_cost is None
    assert result.est_profit_per_unit is None


def test_estimate_discovered_candidate_computes_from_current_price_dump(monkeypatch, trading_cfg, cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    jita_price = CurrentPrice(type_id=100, updated="2026-01-01", buy=0.5, sell=1.0)
    tritanium_price = CurrentPrice(type_id=34, updated="2026-01-01", buy=9.0, sell=10.0)

    result = estimate_discovered_candidate(_candidate(volume_m3=0.001), jita_price, {34: tritanium_price},
                                            trading_cfg, cfg)

    assert result.est_profit_per_unit is not None and result.est_profit_per_unit > 0
    assert result.est_margin is not None


def test_estimate_discovered_candidate_missing_mineral_price_leaves_profit_none(monkeypatch, trading_cfg, cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 1)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(34, 100.0)])
    jita_price = CurrentPrice(type_id=100, updated="2026-01-01", buy=0.5, sell=1.0)

    result = estimate_discovered_candidate(_candidate(), jita_price, {}, trading_cfg, cfg)

    assert result.est_mineral_value is None
    assert result.est_profit_per_unit is None
    assert result.est_landed_cost is not None
