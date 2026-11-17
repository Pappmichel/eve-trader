"""One user agent for every outgoing client; the ESI compatibility date is a
switch that stays off (today's `/latest`, no header) until a date is pinned."""
from eve_trader import esi_client, goonmetrics_client
from eve_trader.config import ESI_BASE_DEFAULT, ESI_COMPATIBILITY_DATE, TradingConfig
from eve_trader.production import sde


def test_default_keeps_latest_base_and_sends_no_compatibility_header():
    assert ESI_COMPATIBILITY_DATE is None
    assert TradingConfig().esi_base == ESI_BASE_DEFAULT == "https://esi.evetech.net/latest"
    assert "X-Compatibility-Date" not in esi_client.ESIClient().session.headers


def test_pinned_date_is_sent_on_the_session(monkeypatch):
    monkeypatch.setattr(esi_client, "ESI_COMPATIBILITY_DATE", "2099-01-01")
    client = esi_client.ESIClient()
    assert client.session.headers["X-Compatibility-Date"] == "2099-01-01"
    assert client.session.headers["User-Agent"] == esi_client.USER_AGENT


def test_goonmetrics_and_sde_use_the_esi_user_agent():
    assert goonmetrics_client.USER_AGENT is esi_client.USER_AGENT
    assert sde.USER_AGENT is esi_client.USER_AGENT
    assert goonmetrics_client.GoonmetricsClient().session.headers["User-Agent"] == esi_client.USER_AGENT
