from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production.config import ProductionConfig
from eve_trader.production.pricing import buy_price, buy_source

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
