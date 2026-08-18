import pytest

from eve_trader.esi_client import ESIClient


@pytest.fixture(autouse=True)
def _reset_price_caches():
    # get_adjusted_prices/get_system_cost_indices cache class-wide (see
    # esi_client.py's _adjusted_prices_cache comment), not per-instance - a
    # test that populates it would otherwise leak into whichever test (in
    # this file or, since ESIClient is imported everywhere, potentially
    # another file too) runs next.
    ESIClient.clear_price_caches()
    yield
    ESIClient.clear_price_caches()


def _fake_get(monkeypatch, responses):
    """responses: {path: return_value} - counts calls per path."""
    calls: dict[str, int] = {}

    def _get(self, path_or_url, params=None, auth_role=None, retries=3):
        calls[path_or_url] = calls.get(path_or_url, 0) + 1
        return responses[path_or_url]
    monkeypatch.setattr(ESIClient, "_get", _get)
    return calls


def test_get_adjusted_prices_caches_across_fresh_client_instances(monkeypatch):
    # Real perf gap confirmed via cProfile (2026-08-16): production/engine.py's
    # _PlanContext creates a *fresh* ESIClient() on every plan_production/
    # plan_asset_optimized call - an instance-level cache (what this used to
    # be) never actually hit across separate plan computations, despite
    # cache_seconds implying it should. Two calls, even across two different
    # client *instances*, within the TTL must hit ESI only once.
    calls = _fake_get(monkeypatch, {
        "/markets/prices/": [{"type_id": 34, "adjusted_price": 5.5}],
    })

    first = ESIClient().get_adjusted_prices()
    second = ESIClient().get_adjusted_prices()

    assert calls["/markets/prices/"] == 1
    assert first == second == {34: 5.5}


def test_get_adjusted_prices_filters_to_requested_type_ids(monkeypatch):
    _fake_get(monkeypatch, {
        "/markets/prices/": [{"type_id": 34, "adjusted_price": 5.5}, {"type_id": 35, "adjusted_price": 10.0}],
    })

    result = ESIClient().get_adjusted_prices(type_ids=[34, 999])

    assert result == {34: 5.5, 999: 0}  # missing id defaults to 0, not KeyError


def test_get_system_cost_indices_caches_across_fresh_client_instances(monkeypatch):
    calls = _fake_get(monkeypatch, {
        "/industry/systems/": [
            {"solar_system_id": 30000142, "cost_indices": [
                {"activity": "manufacturing", "cost_index": 0.02},
                {"activity": "reaction", "cost_index": 0.01},
            ]},
        ],
    })

    first = ESIClient().get_system_cost_indices(30000142)
    second = ESIClient().get_system_cost_indices(30000142)

    assert calls["/industry/systems/"] == 1
    assert first == second == {"manufacturing": 0.02, "reaction": 0.01}


def test_get_system_cost_indices_refetches_after_clear_price_caches(monkeypatch):
    calls = _fake_get(monkeypatch, {
        "/industry/systems/": [
            {"solar_system_id": 30000142, "cost_indices": [{"activity": "manufacturing", "cost_index": 0.02}]},
        ],
    })

    ESIClient().get_system_cost_indices(30000142)
    ESIClient.clear_price_caches()
    ESIClient().get_system_cost_indices(30000142)

    assert calls["/industry/systems/"] == 2
