"""Response models of the money endpoints (Portfolio cards/history/attention,
hub freight, Trading / Station Trading / Doctrine realized summary): each
payload validates against its model, a field the model does not name still
reaches the client, `None` stays `None`, and an int stays an int (a plain
`float` field would turn 5 into 5.0). The action results are canned through the
module objects; the golden byte comparison against the pre-model responses was
done once by hand when the models were added."""
import json

import pytest
from fastapi.testclient import TestClient

from eve_trader import hubs, portfolio
from eve_trader.api import schemas
from eve_trader.api.app import create_app
from eve_trader.doctrine import realized as doctrine_realized
from eve_trader.station_trading import realized as station_realized
from eve_trader.trade_realized import actions as trade_realized_actions

client = TestClient(create_app())

SUMMARY = {
    "period": {"start": None, "end": "2026-11-17T00:00:00+00:00", "since_t0": False, "days": 30},
    "status": {"empty": False, "needs_rebuild": False},
    "revenue": 10, "cost_of_goods_sold": 4.5, "realized_profit": 5.5, "margin": 0.55, "coverage": None,
    "estimated": False, "units_sold": 3.0, "sale_count": 2, "price_missing": False, "tax_missing_sales": 0,
    "fees_missing_sales": 1, "fees_missing_revenue": 2, "fees_missing_profit": 0.0, "cost_structure": [],
    "by_day": [], "flags": {"estimated": False, "price_missing": True, "fees_missing": True},
    "warnings": ["fees_missing"], "extra_probe": {"kept": True},
}


@pytest.mark.parametrize("module, name, url", [
    (trade_realized_actions, "do_get_trading_realized_summary", "/api/trading/realized/summary"),
    (station_realized, "do_get_station_realized_summary", "/api/station-trading/realized/summary"),
    (doctrine_realized, "do_get_doctrine_realized_summary", "/api/doctrine/realized/summary"),
])
def test_realized_channel_summary_passes_every_field_through(monkeypatch, module, name, url):
    monkeypatch.setattr(module, name, lambda **kwargs: SUMMARY)
    schemas.RealizedChannelSummary.model_validate(SUMMARY)

    resp = client.get(url)

    assert resp.status_code == 200
    assert resp.json() == SUMMARY            # same key set: flags, warnings, fees_missing_*, extra_probe
    assert '"revenue":10,' in resp.text      # int stays int
    assert '"coverage":null' in resp.text    # None stays None


def test_realized_cards_keep_flags_and_unknown_fields(monkeypatch):
    card = {"tool": "production", "profit": 1500000, "revenue": 2500000.5, "margin": 0.6, "coverage": None,
            "estimated": True, "empty": False, "sales_count": 12, "fees_missing_sales": 2,
            "fees_missing_revenue": 10, "link": "/production/realized", "extra_probe": 1}
    payload = {"rows": [card], "unreported_sales": None, "unreported_revenue": None, "unreported_profit": None,
               "total": 1500000, "fees_missing_sales": 2, "fees_missing_revenue": 10, "period": "30",
               "freight": {"courier": 5, "configured": 7.5}, "extra_probe": [1]}
    monkeypatch.setattr(portfolio, "realized_cards", lambda tool_keys, period="30": payload)
    schemas.PortfolioRealizedCards.model_validate(payload)

    resp = client.get("/api/portfolio/realized-cards")

    assert resp.json() == payload
    assert '"profit":1500000,' in resp.text and '"total":1500000,' in resp.text


def test_realized_history_keeps_channel_columns(monkeypatch):
    payload = {"bucket": "day", "channels": ["production", "trading"], "period": "30", "rows": [
        {"date": "2026-01-01", "total": 5, "cumulative": 5.5, "production": 5, "trading": 0.0}]}
    monkeypatch.setattr(portfolio, "realized_history", lambda tool_keys, period="30", bucket=None: payload)
    schemas.PortfolioRealizedHistory.model_validate(payload)

    resp = client.get("/api/portfolio/realized-history")

    assert resp.json() == payload
    assert '"total":5,' in resp.text


def test_attention_items_keep_unknown_fields(monkeypatch):
    payload = {"items": [{"key": "wallet_scope", "text": "x", "link": "/y", "extra_probe": True}]}
    monkeypatch.setattr(portfolio, "attention", lambda tool_keys: payload)
    schemas.PortfolioAttention.model_validate(payload)

    assert client.get("/api/portfolio/attention").json() == payload


def test_hub_freight_rows_keep_null_and_unknown_fields(monkeypatch):
    rows = [{"region_id": 10000002, "hub": "Jita", "freight_cost_per_m3": 900.0},
            {"region_id": 10000043, "hub": "Amarr", "freight_cost_per_m3": None, "extra_probe": 1}]
    monkeypatch.setattr(hubs, "do_get_hub_freight", lambda: rows)
    monkeypatch.setattr(hubs, "do_update_hub_freight", lambda rates: rows)
    for row in rows:
        schemas.HubFreightEntry.model_validate(row)

    assert client.get("/api/hubs/freight").json() == rows
    posted = client.post("/api/hubs/freight", json=[{"region_id": 10000002, "freight_cost_per_m3": 900}])
    assert posted.json() == rows
    assert json.loads(posted.text)[1]["freight_cost_per_m3"] is None
