import pytest

from eve_trader.esi_client import ESIClient, ESIError, OrderStats
from eve_trader.goonmetrics_client import CurrentPrice, GoonmetricsClient
from eve_trader.production import esi_sync
from eve_trader.production.config import ProductionConfig
from eve_trader.production.pricing import buy_price, buy_source, home_prices, jita_prices

# jita_buy_broker_fee=0.0 keeps these source-picking tests' arithmetic clean -
# see test_buy_price_includes_buy_broker_fee for the fee itself.
CFG = ProductionConfig(haul_cost_per_m3=900.0, jita_buy_broker_fee=0.0)


def _price(sell: float) -> CurrentPrice:
    return CurrentPrice(type_id=1, updated="", buy=0.0, sell=sell)


def test_buy_source_picks_jita_when_haul_adjusted_price_is_cheaper():
    # Home has a listed sell order, but it's far pricier than hauling from
    # Jita - the cheaper source must win, not "home whenever it has stock".
    home = {1: _price(10_000.0)}
    jita = {1: _price(100.0)}  # + 900 * 1 m3 haul = 1000, still far under home
    assert buy_source(1, home, jita, volume_m3=1.0, cfg=CFG) == "Jita"
    assert buy_price(1, home, jita, volume_m3=1.0, cfg=CFG) == 1000.0


def test_buy_source_picks_home_when_it_is_actually_cheaper():
    home = {1: _price(500.0)}
    jita = {1: _price(100.0)}  # + 900 haul = 1000, more than home
    assert buy_source(1, home, jita, volume_m3=1.0, cfg=CFG) == "C-J"
    assert buy_price(1, home, jita, volume_m3=1.0, cfg=CFG) == 500.0


def test_buy_source_falls_back_to_whichever_market_has_stock():
    assert buy_source(1, {}, {1: _price(50.0)}, volume_m3=0.0, cfg=CFG) == "Jita"
    assert buy_source(1, {1: _price(50.0)}, {}, volume_m3=0.0, cfg=CFG) == "C-J"
    assert buy_source(1, {}, {}, volume_m3=0.0, cfg=CFG) is None


def test_buy_price_none_when_no_sell_order_anywhere():
    assert buy_price(1, {}, {}, volume_m3=1.0, cfg=CFG) is None


def test_buy_price_includes_buy_broker_fee():
    cfg = ProductionConfig(haul_cost_per_m3=900.0, jita_buy_broker_fee=0.0147)
    home = {1: _price(500.0)}
    assert buy_price(1, home, {}, volume_m3=1.0, cfg=cfg) == 500.0 * 1.0147


# ---------------------------------------------------------- home_prices/jita_prices
HOME_CFG = ProductionConfig(home_market="c-j6mt", home_location_id=1049588174021)


def test_home_prices_uses_live_esi_when_producer_character_succeeds(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", lambda self, location_id, type_ids, auth_role: {
        587: OrderStats(sell_percentile=1234.5, sell_volume=10.0, buy_percentile=1000.0, buy_volume=5.0),
    })
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         pytest.fail("must not fall back to Goonmetrics when ESI succeeds"))

    result = home_prices(HOME_CFG, [587])

    assert result[587].sell == 1234.5
    assert result[587].buy == 1000.0


def test_home_prices_reports_empty_market_as_zero_sell_not_a_stale_goonmetrics_price(monkeypatch):
    # Direct regression test for the reported bug: Goonmetrics may still
    # show a stale nonzero sell price for an item the real C-J market has
    # zero sell orders for right now - the live ESI check must win, and a
    # confirmed-empty market must resolve to sell=0.0 (excluded by every
    # downstream `> 0` check), not the stale Goonmetrics number.
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", lambda self, location_id, type_ids, auth_role: {
        587: OrderStats(sell_percentile=None, sell_volume=0.0, buy_percentile=None, buy_volume=0.0),
    })
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=587, updated="", buy=0.0, sell=99999.0)])

    result = home_prices(HOME_CFG, [587])

    assert result[587].sell == 0.0


def test_home_prices_falls_back_to_goonmetrics_when_every_producer_character_fails(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda: [("producer:1", 1, "A"), ("producer:2", 2, "B")])

    def _fail(self, location_id, type_ids, auth_role):
        raise ESIError("no docking access")
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", _fail)
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=587, updated="2026-08-26", buy=0.0, sell=42.0)])

    result = home_prices(HOME_CFG, [587])

    assert result[587].sell == 42.0


def test_home_prices_falls_back_to_goonmetrics_when_no_producer_characters(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_producer_characters", lambda: [])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", lambda *a, **k:
                         pytest.fail("must not attempt ESI with no producer characters"))
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=587, updated="", buy=0.0, sell=42.0)])

    result = home_prices(HOME_CFG, [587])

    assert result[587].sell == 42.0


def test_home_prices_skips_esi_when_home_location_id_unset(monkeypatch):
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", lambda *a, **k:
                         pytest.fail("must not attempt ESI with no home_location_id configured"))
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=587, updated="", buy=0.0, sell=42.0)])

    cfg = ProductionConfig(home_market="c-j6mt", home_location_id=None)
    result = home_prices(cfg, [587])

    assert result[587].sell == 42.0


def test_home_prices_empty_type_ids_returns_empty_dict():
    assert home_prices(HOME_CFG, []) == {}


def test_jita_prices_uses_live_esi_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {
        34: OrderStats(sell_percentile=5.5, sell_volume=1000.0, buy_percentile=5.0, buy_volume=900.0),
    })
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         pytest.fail("must not fall back to Goonmetrics when ESI succeeds"))

    result = jita_prices([34])

    assert result[34].sell == 5.5


def test_jita_prices_falls_back_to_goonmetrics_when_esi_fails(monkeypatch):
    def _fail(self, region_id, type_ids):
        raise ESIError("ESI outage")
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fail)
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=34, updated="", buy=0.0, sell=6.0)])

    result = jita_prices([34])

    assert result[34].sell == 6.0


def test_jita_prices_empty_type_ids_returns_empty_dict():
    assert jita_prices([]) == {}
