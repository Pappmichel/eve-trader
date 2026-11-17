"""PI with "All hubs": inputs are bought at the cheapest hub, outputs are
valued at the hub that pays the most (pure, fake order books)."""
from dataclasses import replace

import pytest

from eve_trader import esi_client
from eve_trader.esi_client import OrderStats
from eve_trader.hubs import ALL_HUBS
from eve_trader.pi import actions, economics as econ, static
from eve_trader.pi.config import PiConfig

from .test_pi_engine import _ROWS

HUB_A, HUB_B = 10000002, 10000043  # Jita, Amarr


@pytest.fixture
def sd(monkeypatch):
    s = static.build_static({k: [tuple(r) for r in v] for k, v in _ROWS.items()})
    monkeypatch.setattr(actions, "_static", lambda: s)
    monkeypatch.setattr(actions, "_trends", lambda static_, region: {})
    return s


def _install_books(monkeypatch, books):
    """books: {region_id: (sell, buy)} applied to every type."""

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def region_order_stats_bulk(self, region_id, type_ids):
            sell, buy = books.get(region_id, (None, None))
            return {t: OrderStats(sell, 10.0, buy, 10.0) for t in type_ids}

    monkeypatch.setattr(esi_client, "ESIClient", FakeClient)


def _market(valuation, broker=0.0, tax=0.0):
    return econ.MarketSettings(
        broker_fee=broker, sales_tax=tax, valuation=valuation, freight_per_m3=0.0,
        tax_rate=0.0, amortisation_days=30.0, min_isk_per_planet_day=0.0,
        market_share_warning=0.1, program_hours=24.0, interval_hours=24.0)


def _cfg(valuation="sell_orders"):
    return replace(PiConfig(), hub_region_id=ALL_HUBS, pi_valuation=valuation,
                   pi_broker_fee_rate=0.0, pi_sales_tax_rate=0.0, pi_price_structure_id=0)


def test_all_hubs_values_outputs_at_the_best_hub_and_buys_inputs_at_the_cheapest(sd, monkeypatch):
    _install_books(monkeypatch, {HUB_A: (100.0, 90.0), HUB_B: (150.0, 140.0)})
    t = sorted(sd.commodities)[0]

    prices = actions._prices(sd, _cfg("sell_orders"))
    assert econ.input_unit_cost(t, prices, _market("sell_orders")) == 100.0
    assert prices.hub_by_type[t] == HUB_A
    assert econ.output_unit_value(t, prices, _market("sell_orders")) == 150.0
    assert prices.output_hub_by_type[t] == HUB_B

    prices = actions._prices(sd, _cfg("buy_orders"))
    assert econ.output_unit_value(t, prices, _market("buy_orders")) == 140.0
    assert econ.input_unit_cost(t, prices, _market("buy_orders")) == 100.0


def test_buy_order_valuation_picks_the_hub_by_its_buy_side(sd, monkeypatch):
    # Hub A has the higher sell quote, hub B the higher buy quote.
    _install_books(monkeypatch, {HUB_A: (200.0, 50.0), HUB_B: (150.0, 140.0)})
    t = sorted(sd.commodities)[0]

    by_sell = actions._prices(sd, _cfg("sell_orders"))
    assert by_sell.output_hub_by_type[t] == HUB_A
    by_buy = actions._prices(sd, _cfg("buy_orders"))
    assert by_buy.output_hub_by_type[t] == HUB_B
    assert econ.output_unit_value(t, by_buy, _market("buy_orders")) == 140.0


def test_output_choice_uses_net_revenue_after_fees():
    stats = {HUB_A: {1: OrderStats(100.0, 1.0, 99.0, 1.0)},
             HUB_B: {1: OrderStats(101.0, 1.0, 10.0, 1.0)}}
    sell, buy, hub = econ.best_output_hubs(stats, [1, 2], "sell_orders", 0.03, 0.03)
    assert hub == {1: HUB_B} and sell[1] == 101.0
    assert sell[2] is None and 2 not in hub


def test_single_hub_has_no_separate_output_side(sd, monkeypatch):
    _install_books(monkeypatch, {HUB_A: (100.0, 90.0), HUB_B: (150.0, 140.0)})
    cfg = replace(_cfg(), hub_region_id=HUB_A)
    prices = actions._prices(sd, cfg)
    t = sorted(sd.commodities)[0]
    assert prices.output_sell is None and prices.output_hub_by_type is None
    assert econ.output_unit_value(t, prices, _market("sell_orders")) == 100.0


def test_price_rows_name_the_input_and_output_hub():
    prices = econ.Prices(sell={1: 100.0, 2: 100.0}, buy={1: 90.0, 2: 90.0},
                         hub_by_type={1: HUB_A, 2: HUB_A},
                         output_sell={1: 150.0, 2: 150.0}, output_buy={1: 140.0, 2: 140.0},
                         output_hub_by_type={1: HUB_B, 2: HUB_B})
    rows = actions._price_rows(prices, imports=[1], exports=[2])
    assert rows[1] == {"sell": 100.0, "buy": 90.0, "side": "input", "hub": "Jita"}
    assert rows[2] == {"sell": 150.0, "buy": 140.0, "side": "output", "hub": "Amarr"}
