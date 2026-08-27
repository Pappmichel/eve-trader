from eve_trader import storage
from eve_trader.esi_client import ESIClient, ESIError
from eve_trader.goonmetrics_client import CurrentPrice, GoonmetricsClient
from eve_trader.production import engine
from eve_trader.production.config import ProductionConfig


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

    # cfg.home_location_id is unset, so pricing.home_prices already skips
    # ESI entirely - only Jita's ESI-first attempt needs to be forced to
    # fail here so it falls through to the Goonmetrics mock below, matching
    # this test's pre-ESI-first pricing scenario.
    def _fail(self, region_id, type_ids):
        raise ESIError("no ESI in this test")
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fail)

    def fake_current_prices(self, market):
        if market == cfg.home_market:
            return [CurrentPrice(type_id=1, updated="", buy=0.0, sell=100.0)]
        return [CurrentPrice(type_id=2, updated="", buy=0.0, sell=50.0)]
    monkeypatch.setattr(GoonmetricsClient, "current_prices", fake_current_prices)

    result = engine.stock_value(cfg)

    # type 1: 10 * 100 = 1000 (priced at home) ; type 2: 5 * 50 = 250 (no home
    # quote, falls back to Jita) ; type 3: no quote anywhere -> unpriced,
    # excluded from total ; type 4: zero stock -> skipped before pricing.
    assert result["total_value"] == 1250.0
    assert result["priced_items"] == 2
    assert result["unpriced_items"] == 1
