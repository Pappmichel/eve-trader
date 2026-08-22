"""Tests for eve_trader/refining/pricing.py - GitHub issue #91's per-row
profit calculation. Pure/no Postgres needed - storage.get_portion_size/
get_type_materials are monkeypatched (mirrors shortlist.py's own tests using
pre-fetched OrderStats dicts, no ESI/network calls)."""
import pytest

from eve_trader import storage
from eve_trader.config import TradingConfig
from eve_trader.esi_client import OrderStats
from eve_trader.refining.config import RefiningConfig
from eve_trader.refining.models import OreCandidate
from eve_trader.refining.pricing import evaluate_ore_item, mineral_type_ids_for


def _candidate(type_id=34, family="Veldspar", is_ice=False, volume_m3=0.01):
    return OreCandidate(type_id=type_id, item="Compressed Veldspar", family=family, is_ice=is_ice,
                         volume_m3=volume_m3)


@pytest.fixture
def trading_cfg():
    return TradingConfig(jita_buy_broker_fee=0.0147, structure_sell_haircut=0.9463, import_cost_per_m3=900.0,
                          min_profit_threshold=0.0, min_margin_threshold=0.05)


@pytest.fixture
def refining_cfg():
    return RefiningConfig(refining_tax_rate=0.0)


def test_evaluate_ore_item_no_jita_data_is_no_market_data(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 100)
    row = evaluate_ore_item(_candidate(), True, None, {}, trading_cfg, refining_cfg)
    assert row.decision == "No market data"
    assert row.landed_cost is None
    assert row.profit_per_unit is None


def test_evaluate_ore_item_no_portion_size_is_no_market_data(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: None)
    jita = OrderStats(sell_percentile=1000.0, sell_volume=5000.0, buy_percentile=None, buy_volume=0.0)
    row = evaluate_ore_item(_candidate(), True, jita, {}, trading_cfg, refining_cfg)
    assert row.decision == "No market data"


def test_evaluate_ore_item_missing_mineral_price_is_no_market_data(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 100)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 415.0)])
    jita = OrderStats(sell_percentile=1000.0, sell_volume=5000.0, buy_percentile=None, buy_volume=0.0)
    row = evaluate_ore_item(_candidate(), True, jita, {}, trading_cfg, refining_cfg)
    assert row.decision == "No market data"
    assert row.mineral_value is None


def test_evaluate_ore_item_profitable_is_import(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 100)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 415.0)])
    jita = OrderStats(sell_percentile=1.0, sell_volume=5000.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_ore_item(_candidate(volume_m3=0.001), True, jita, {35: tritanium}, trading_cfg, refining_cfg)

    # portion_size=100 -> landed_cost_per_portion = 100 x (1x1.0147 + 0.001x900) = 100 x 1.9147 = 191.47
    # yield_pct=0.50 (default RefiningConfig, no skills) -> minerals = floor(1x415x0.50)=207
    # mineral_value = 207 x 10 x 0.9463 = 1958.84, net_sell same (tax=0)
    assert row.decision == "Import"
    assert row.profit_per_unit is not None
    assert row.profit_per_unit > 0
    assert row.margin is not None and row.margin > trading_cfg.min_margin_threshold


def test_evaluate_ore_item_min_profit_threshold_is_compared_per_unit_not_per_portion(monkeypatch, refining_cfg):
    # Real bug found in code review: _decision used to receive profit_per_
    # PORTION (~17.67 x 100 = 1767.33 here) instead of profit_per_unit
    # (~17.67), making a per-unit threshold ~portion_size times too lenient.
    # min_profit_threshold=100 (per-unit): the real per-unit profit (~17.67)
    # is below it, so this must be "Skip", not "Import" - it would wrongly
    # pass as "Import" if compared against the per-portion figure instead.
    trading_cfg = TradingConfig(jita_buy_broker_fee=0.0147, structure_sell_haircut=0.9463, import_cost_per_m3=900.0,
                                 min_profit_threshold=100.0, min_margin_threshold=0.05)
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 100)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 415.0)])
    jita = OrderStats(sell_percentile=1.0, sell_volume=5000.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_ore_item(_candidate(volume_m3=0.001), True, jita, {35: tritanium}, trading_cfg, refining_cfg)

    assert row.profit_per_unit == pytest.approx(17.6733, abs=0.001)
    assert row.decision == "Skip"


def test_evaluate_ore_item_unprofitable_is_skip(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 100)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 415.0)])
    jita = OrderStats(sell_percentile=1000.0, sell_volume=5000.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_ore_item(_candidate(), True, jita, {35: tritanium}, trading_cfg, refining_cfg)

    assert row.decision == "Skip"


def test_evaluate_ore_item_inactive_overrides_everything(monkeypatch, trading_cfg, refining_cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 100)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 415.0)])
    jita = OrderStats(sell_percentile=1.0, sell_volume=5000.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    row = evaluate_ore_item(_candidate(volume_m3=0.001), False, jita, {35: tritanium}, trading_cfg, refining_cfg)

    assert row.decision == "Inactive"


def test_evaluate_ore_item_refining_tax_reduces_net_sell(monkeypatch, trading_cfg):
    monkeypatch.setattr(storage, "get_portion_size", lambda type_id: 100)
    monkeypatch.setattr(storage, "get_type_materials", lambda type_id: [(35, 415.0)])
    jita = OrderStats(sell_percentile=1.0, sell_volume=5000.0, buy_percentile=None, buy_volume=0.0)
    tritanium = OrderStats(sell_percentile=10.0, sell_volume=1_000_000.0, buy_percentile=None, buy_volume=0.0)

    no_tax = evaluate_ore_item(_candidate(volume_m3=0.001), True, jita, {35: tritanium}, trading_cfg,
                                RefiningConfig(refining_tax_rate=0.0))
    with_tax = evaluate_ore_item(_candidate(volume_m3=0.001), True, jita, {35: tritanium}, trading_cfg,
                                  RefiningConfig(refining_tax_rate=0.1))

    assert with_tax.net_sell < no_tax.net_sell
    assert with_tax.refining_tax > 0


def test_mineral_type_ids_for_collects_the_union_across_candidates(monkeypatch):
    def fake_materials(type_id):
        return [(35, 415.0), (36, 41.0)] if type_id == 34 else [(35, 100.0)]
    monkeypatch.setattr(storage, "get_type_materials", fake_materials)

    ids = mineral_type_ids_for([_candidate(type_id=34), _candidate(type_id=99, family="Scordite")])

    assert ids == [35, 36]
