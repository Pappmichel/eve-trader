"""Sorting's "reprocess" tag: refined value (C-J minerals) vs the stack's
landed hub (Jita) value - intake stacks were bought in the hub."""
from eve_trader.refining.actions import _worth_reprocessing
NO_TAX = 0.0


def test_refined_value_above_the_jita_value_is_worth_reprocessing():
    # 1,000 units: 1,450,000 ISK of minerals vs 1,050 ISK landed per unit.
    assert _worth_reprocessing(1_450_000.0, 1050.0, 1000, NO_TAX)


def test_jita_value_above_the_refined_value_is_not():
    assert not _worth_reprocessing(1_450_000.0, 1600.0, 1000, NO_TAX)


def test_refining_tax_reduces_the_refined_value():
    assert not _worth_reprocessing(1_000_000.0, 950.0, 1000, 0.1)


def test_no_hub_price_means_reprocess_and_unpriced_minerals_never():
    assert _worth_reprocessing(1.0, None, 1, NO_TAX)
    assert not _worth_reprocessing(None, 1.0, 1, NO_TAX)


def test_mineral_value_uses_c_j_sell_prices_and_needs_every_mineral():
    from eve_trader.config import TradingConfig
    from eve_trader.esi_client import OrderStats
    from eve_trader.refining.actions import _mineral_value
    cfg = TradingConfig(structure_sell_haircut=1.0)
    stats = {34: OrderStats(4.0, 1e9, None, 0.0), 35: OrderStats(None, 0, 3.0, 1e6)}
    assert _mineral_value({34: 100}, stats, cfg) == 400.0
    assert _mineral_value({34: 100, 35: 10}, stats, cfg) is None
    assert _mineral_value({}, stats, cfg) is None
