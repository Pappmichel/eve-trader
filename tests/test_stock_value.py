from eve_trader import storage
from eve_trader.goonmetrics_client import CurrentPrice
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig


def _mock_quotes(monkeypatch, home, jita, volumes=None, manual=None):
    """Goonmetrics quotes behind portfolio._priced, no network."""
    from eve_trader import portfolio
    from eve_trader.production import pricing

    def fake_prices(market, ids):
        book = jita if market == pricing.JITA_MARKET else home
        return {t: CurrentPrice(type_id=t, updated="", buy=0.0, sell=book[t]) for t in ids if t in book}
    from eve_trader.production.config import PRODUCTION_CONFIG
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_market", "TestMarket")
    monkeypatch.setattr(pricing, "_goonmetrics_prices", fake_prices)
    monkeypatch.setattr(portfolio, "_haul_volumes", lambda ids: {t: (volumes or {}).get(t, 0.0) for t in ids})
    monkeypatch.setattr(storage, "load_manual_item_prices", lambda: dict(manual or {}))


def _cfg_with_stock(monkeypatch, stock):
    cfg = ProductionConfig(home_market="TestMarket", haul_cost_per_m3=800.0)
    from eve_trader.production.config import PRODUCTION_CONFIG
    monkeypatch.setattr(PRODUCTION_CONFIG, "haul_cost_per_m3", 800.0)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(t, "x", 0, None, None) for t in stock])
    monkeypatch.setattr(storage, "load_manual_stock", lambda: dict(stock))
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Input", None))
    monkeypatch.setattr(engine, "_current_stock", lambda type_id, manual_stock, cfg, bp: manual_stock.get(type_id, 0))
    return cfg


def test_stock_value_follows_the_portfolio_rule_cheaper_of_home_and_jita_plus_freight(monkeypatch):
    # Home 1000, Jita 100, 0.01 m3, freight 800/m3: Jita lands at 108.
    cfg = _cfg_with_stock(monkeypatch, {1: 1})
    _mock_quotes(monkeypatch, home={1: 1000.0}, jita={1: 100.0}, volumes={1: 0.01})
    result = engine.stock_value(cfg)
    assert result["total_value"] == 108.0
    assert result["priced_items"] == 1


def test_stock_value_manual_price_wins(monkeypatch):
    cfg = _cfg_with_stock(monkeypatch, {1: 2})
    _mock_quotes(monkeypatch, home={1: 1000.0}, jita={1: 100.0}, volumes={1: 0.01}, manual={1: 7.0})
    assert engine.stock_value(cfg)["total_value"] == 14.0


def test_stock_value_without_any_quote_is_unpriced(monkeypatch):
    cfg = _cfg_with_stock(monkeypatch, {1: 2})
    _mock_quotes(monkeypatch, home={}, jita={})
    result = engine.stock_value(cfg)
    assert result == {"total_value": 0.0, "priced_items": 0, "unpriced_items": 1}


def test_stock_value_prices_at_home_sell_falling_back_to_jita(monkeypatch):
    cfg = ProductionConfig(home_market="TestMarket")
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [
        (1, "Has Home Price", 0, None, None),
        (2, "Jita Only", 0, None, None),
        (3, "No Price Anywhere", 0, None, None),
        (4, "Zero Stock", 0, None, None),
    ])
    monkeypatch.setattr(storage, "load_manual_stock", lambda: {1: 10, 2: 5, 3: 3, 4: 0})
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Input", None))
    monkeypatch.setattr(engine, "_current_stock", lambda type_id, manual_stock, cfg, bp: manual_stock.get(type_id, 0))

    _mock_quotes(monkeypatch, home={1: 100.0}, jita={2: 50.0})

    result = engine.stock_value(cfg)

    # type 1: 10 * 100 = 1000 (priced at home) ; type 2: 5 * 50 = 250 (no home
    # quote, falls back to Jita) ; type 3: no quote anywhere -> unpriced,
    # excluded from total ; type 4: zero stock -> skipped before pricing.
    assert result["total_value"] == 1250.0
    assert result["priced_items"] == 2
    assert result["unpriced_items"] == 1
