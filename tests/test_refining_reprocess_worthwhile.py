"""Sorting's "reprocess" tag when C-J has no sell order for the item."""
from eve_trader.config import TradingConfig
from eve_trader.esi_client import OrderStats
from eve_trader.refining.actions import _worth_reprocessing_without_listing
from eve_trader.refining.config import RefiningConfig
from eve_trader.refining.reprocessing import (
    NO_MARKET_DATA_DECISION, SELL_DECISION, ReprocessingQuoteRow,
)


def _row(decision=NO_MARKET_DATA_DECISION, mineral_value=1_450_000.0, sell_as_is_value=None):
    return ReprocessingQuoteRow(name="Compressed Brimful Sylvite", quantity=1000, type_id=62461, category="",
                                sell_as_is_value=sell_as_is_value, refined_value=None, mineral_value=mineral_value,
                                refining_tax=None, decision=decision)


def _stats(buy):
    return OrderStats(sell_percentile=None, sell_volume=0, buy_percentile=buy, buy_volume=1e6)


CFGS = (TradingConfig(structure_sell_haircut=1.0), RefiningConfig(refining_tax_rate=0.0))


def test_no_listing_but_buy_orders_below_refined_value_is_worth_reprocessing():
    # Real case: 222 ISK buy orders vs ~1,450 ISK of minerals per unit.
    assert _worth_reprocessing_without_listing(_row(), _stats(222.3), *CFGS)


def test_buy_orders_above_refined_value_are_not():
    assert not _worth_reprocessing_without_listing(_row(), _stats(2000.0), *CFGS)


def test_no_orders_at_all_means_reprocess():
    assert _worth_reprocessing_without_listing(_row(), None, *CFGS)


def test_unpriced_minerals_or_a_normal_decision_are_left_alone():
    assert not _worth_reprocessing_without_listing(_row(mineral_value=None), None, *CFGS)
    assert not _worth_reprocessing_without_listing(_row(decision=SELL_DECISION, sell_as_is_value=1.0), None, *CFGS)
