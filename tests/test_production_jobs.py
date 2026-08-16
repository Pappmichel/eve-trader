"""Tests for production/jobs.py's list_current_jobs - specifically
output_value pricing (same C-J-sell-falling-back-to-Jita convention as
engine.stock_value, see tests/test_stock_value.py for that precedent).
"""
from eve_trader import storage
from eve_trader.goonmetrics_client import CurrentPrice, GoonmetricsClient
from eve_trader.production import jobs
from eve_trader.production.config import ProductionConfig


def _job(job_id, activity_id, blueprint_type_id, product_type_id, type_name, runs, status="active"):
    return (job_id, activity_id, blueprint_type_id, product_type_id, type_name, runs,
            None, status, "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z", "Some Character")


def test_output_value_prices_at_home_sell_falling_back_to_jita(monkeypatch):
    cfg = ProductionConfig(home_market="TestMarket")
    monkeypatch.setattr(storage, "list_industry_jobs", lambda: [
        _job(1, 1, 101, 10, "Has Home Price", runs=2),
        _job(2, 1, 102, 20, "Jita Only", runs=3),
        _job(3, 1, 103, 30, "No Price Anywhere", runs=1),
    ])
    monkeypatch.setattr(storage, "get_product_quantity", lambda bp, act, prod: 5.0)

    def fake_current_prices(self, market):
        if market == cfg.home_market:
            return [CurrentPrice(type_id=10, updated="", buy=0.0, sell=100.0)]
        return [CurrentPrice(type_id=20, updated="", buy=0.0, sell=50.0)]
    monkeypatch.setattr(GoonmetricsClient, "current_prices", fake_current_prices)

    rows = jobs.list_current_jobs(cfg)
    by_id = {r.job_id: r for r in rows}

    # job 1: quantity = 2*5=10, priced at home (100) -> 1000
    assert by_id[1].quantity == 10.0
    assert by_id[1].output_value == 1000.0
    # job 2: quantity = 3*5=15, no home quote -> falls back to Jita (50) -> 750
    assert by_id[2].quantity == 15.0
    assert by_id[2].output_value == 750.0
    # job 3: quantity = 1*5=5, no quote anywhere -> unpriced
    assert by_id[3].quantity == 5.0
    assert by_id[3].output_value is None


def test_output_value_is_none_for_jobs_without_a_product(monkeypatch):
    # Research/copying jobs have no product_type_id, hence no quantity -
    # output_value must stay None rather than pricing a nonexistent quantity.
    cfg = ProductionConfig(home_market="TestMarket")
    monkeypatch.setattr(storage, "list_industry_jobs", lambda: [
        (1, 5, 101, None, None, 1, None, "active", "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z", "Some Character"),
    ])
    monkeypatch.setattr(storage, "get_product_quantity", lambda bp, act, prod: 5.0)
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market: [])

    rows = jobs.list_current_jobs(cfg)

    assert rows[0].quantity is None
    assert rows[0].output_value is None
