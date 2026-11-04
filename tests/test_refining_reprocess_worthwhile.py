"""Sorting's "reprocess" tag: refined value (C-J minerals) vs the stack's
landed hub (Jita) value - intake stacks were bought in the hub."""
from eve_trader.refining.actions import _worth_reprocessing
from eve_trader.refining.config import RefiningConfig

NO_TAX = RefiningConfig(refining_tax_rate=0.0)


def test_refined_value_above_the_jita_value_is_worth_reprocessing():
    # 1,000 units: 1,450,000 ISK of minerals vs 1,050 ISK landed per unit.
    assert _worth_reprocessing(1_450_000.0, 1050.0, 1000, NO_TAX)


def test_jita_value_above_the_refined_value_is_not():
    assert not _worth_reprocessing(1_450_000.0, 1600.0, 1000, NO_TAX)


def test_refining_tax_reduces_the_refined_value():
    assert not _worth_reprocessing(1_000_000.0, 950.0, 1000, RefiningConfig(refining_tax_rate=0.1))


def test_no_hub_price_means_reprocess_and_unpriced_minerals_never():
    assert _worth_reprocessing(1.0, None, 1, NO_TAX)
    assert not _worth_reprocessing(None, 1.0, 1, NO_TAX)
