"""ESIClient.structure_order_stats_bulk_or_goonmetrics - the Goonmetrics
failsafe for structure pricing (confirmed with the user 2026-08-24): tries
the real order book first, falls back to a Goonmetrics current-price
snapshot when no seller is logged in or the real call fails. Deliberately
NOT wired into own_orders.check_undercut - see that decision's own
docstring in esi_client.py.
"""
import pytest
import requests

from eve_trader.esi_client import ESIClient, ESIError, OrderStats
from eve_trader.goonmetrics_client import CurrentPrice, GoonmetricsClient


def test_uses_real_order_book_when_a_seller_is_logged_in(monkeypatch):
    real_stats = {34: OrderStats(sell_percentile=5.5, sell_volume=100.0, buy_percentile=5.0, buy_volume=50.0)}
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                         lambda self, structure_id, type_ids, auth_role: real_stats)

    def _boom(*args, **kwargs):
        raise AssertionError("must not call Goonmetrics when the real order book succeeds")
    monkeypatch.setattr(GoonmetricsClient, "current_prices", _boom)

    stats, used_fallback = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_roles=["seller"], goonmetrics_market_slug="my-structure")

    assert stats == real_stats
    assert used_fallback is False


def test_falls_back_to_goonmetrics_when_no_seller_is_logged_in(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("must not call the real order book with auth_roles=[]")
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", _boom)
    monkeypatch.setattr(GoonmetricsClient, "current_prices",
                         lambda self, market: [CurrentPrice(type_id=34, updated="now", buy=4.5, sell=5.5)])

    stats, used_fallback = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_roles=[], goonmetrics_market_slug="my-structure")

    assert used_fallback is True
    assert stats == {34: OrderStats(sell_percentile=5.5, sell_volume=0.0, buy_percentile=4.5, buy_volume=0.0)}


def test_falls_back_to_goonmetrics_when_the_real_esi_call_fails(monkeypatch):
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                         lambda self, structure_id, type_ids, auth_role: (_ for _ in ()).throw(ESIError("403")))
    monkeypatch.setattr(GoonmetricsClient, "current_prices",
                         lambda self, market: [CurrentPrice(type_id=34, updated="now", buy=4.5, sell=5.5)])

    stats, used_fallback = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_roles=["seller"], goonmetrics_market_slug="my-structure")

    assert used_fallback is True
    assert stats[34].sell_percentile == 5.5


def test_filters_goonmetrics_response_to_only_the_requested_type_ids(monkeypatch):
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market: [
        CurrentPrice(type_id=34, updated="now", buy=4.5, sell=5.5),
        CurrentPrice(type_id=999, updated="now", buy=1.0, sell=2.0),
    ])

    stats, _ = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_roles=[], goonmetrics_market_slug="my-structure")

    assert set(stats) == {34}


def test_raises_when_no_sharing_character_and_no_fallback_market_configured():
    with pytest.raises(ESIError, match="No character shares the structure market book"):
        ESIClient().structure_order_stats_bulk_or_goonmetrics(
            1000, [34], auth_roles=[], goonmetrics_market_slug=None)


def test_raises_original_esi_error_when_esi_fails_and_no_fallback_market_configured(monkeypatch):
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                         lambda self, structure_id, type_ids, auth_role: (_ for _ in ()).throw(ESIError("403 boom")))

    with pytest.raises(ESIError, match="403 boom"):
        ESIClient().structure_order_stats_bulk_or_goonmetrics(
            1000, [34], auth_roles=["seller"], goonmetrics_market_slug=None)


def test_raises_when_both_esi_and_goonmetrics_fail(monkeypatch):
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                         lambda self, structure_id, type_ids, auth_role: (_ for _ in ()).throw(ESIError("403 boom")))
    monkeypatch.setattr(GoonmetricsClient, "current_prices",
                         lambda self, market: (_ for _ in ()).throw(requests.ConnectionError("offline")))

    with pytest.raises(ESIError, match="403 boom"):
        ESIClient().structure_order_stats_bulk_or_goonmetrics(
            1000, [34], auth_roles=["seller"], goonmetrics_market_slug="my-structure")


def test_tries_the_next_candidate_when_the_first_has_no_docking_access(monkeypatch):
    """Confirmed real bug 2026-09-25: a capability-ticked character with a
    scope-complete token can still get a live 403 "Market access denied"
    for a structure it has no actual in-game docking access to (e.g. a
    buyer character that only ever operates at Jita). The fix is to try
    every candidate in auth_roles, not just the first."""
    attempted = []

    def fake_bulk(self, structure_id, type_ids, auth_role):
        attempted.append(auth_role)
        if auth_role == "buyer:no-docking-access":
            raise ESIError("403 Market access denied")
        return {34: OrderStats(sell_percentile=5.5, sell_volume=100.0, buy_percentile=5.0, buy_volume=50.0)}
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", fake_bulk)

    def _boom(*args, **kwargs):
        raise AssertionError("must not fall back to Goonmetrics when a later candidate succeeds")
    monkeypatch.setattr(GoonmetricsClient, "current_prices", _boom)

    stats, used_fallback = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_roles=["buyer:no-docking-access", "producer:has-docking-access"],
        goonmetrics_market_slug="my-structure")

    assert attempted == ["buyer:no-docking-access", "producer:has-docking-access"]
    assert used_fallback is False
    assert stats[34].sell_percentile == 5.5
