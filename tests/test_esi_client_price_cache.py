import pytest

from eve_trader.esi_client import ESIClient, ESIError


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
    """responses: {path: return_value} - counts calls per path.
    A value that is an Exception instance is raised instead of returned."""
    calls: dict[str, int] = {}

    def _get(self, path_or_url, params=None, auth_role=None, retries=3):
        calls[path_or_url] = calls.get(path_or_url, 0) + 1
        value = responses[path_or_url]
        if isinstance(value, Exception):
            raise value
        return value
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


def test_get_adjusted_prices_raises_and_never_caches_an_empty_response(monkeypatch):
    # Confirmed real 2026-09-16: a 200 response with zero rows is an ESI-side
    # anomaly (a genuine response always covers thousands of published
    # types), but used to be cached as a "successful" empty result for the
    # full cache_seconds window - silently zeroing every job_cost for up to
    # an hour with no error anywhere. Must raise instead of caching {}, and
    # a later call (once ESI is healthy again) must retry rather than being
    # stuck on the earlier empty result.
    calls = _fake_get(monkeypatch, {"/markets/prices/": []})

    with pytest.raises(ESIError):
        ESIClient().get_adjusted_prices()

    assert calls["/markets/prices/"] == 1
    assert ESIClient._adjusted_prices_cache is None  # never cached

    _fake_get(monkeypatch, {"/markets/prices/": [{"type_id": 34, "adjusted_price": 5.5}]})
    assert ESIClient().get_adjusted_prices() == {34: 5.5}  # retries cleanly


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


def test_get_adjusted_prices_serves_stale_on_error(monkeypatch):
    _fake_get(monkeypatch, {"/markets/prices/": [{"type_id": 34, "adjusted_price": 5.5}]})
    assert ESIClient().get_adjusted_prices() == {34: 5.5}
    ESIClient._adjusted_prices_cache_at = (
        __import__("time").time() - 3601
    )
    calls = _fake_get(monkeypatch, {"/markets/prices/": ESIError("down")})

    assert ESIClient().get_adjusted_prices() == {34: 5.5}
    assert calls["/markets/prices/"] == 1
    assert ESIClient.adjusted_prices_stale_seconds() is not None


def test_get_adjusted_prices_raises_when_stale_too_old(monkeypatch):
    _fake_get(monkeypatch, {"/markets/prices/": [{"type_id": 34, "adjusted_price": 5.5}]})
    ESIClient().get_adjusted_prices()
    ESIClient._adjusted_prices_cache_at = (
        __import__("time").time() - ESIClient.PRICE_DATA_MAX_STALE_SECONDS - 1
    )
    _fake_get(monkeypatch, {"/markets/prices/": ESIError("down")})

    with pytest.raises(ESIError):
        ESIClient().get_adjusted_prices()


def test_get_adjusted_prices_raises_with_no_cache(monkeypatch):
    _fake_get(monkeypatch, {"/markets/prices/": ESIError("down")})
    with pytest.raises(ESIError):
        ESIClient().get_adjusted_prices()


def test_get_adjusted_prices_fail_backoff_skips_second_http(monkeypatch):
    _fake_get(monkeypatch, {"/markets/prices/": [{"type_id": 34, "adjusted_price": 5.5}]})
    ESIClient().get_adjusted_prices()
    ESIClient._adjusted_prices_cache_at = __import__("time").time() - 3601
    calls = _fake_get(monkeypatch, {"/markets/prices/": ESIError("down")})

    assert ESIClient().get_adjusted_prices() == {34: 5.5}
    assert ESIClient().get_adjusted_prices() == {34: 5.5}
    assert calls["/markets/prices/"] == 1  # second call stayed in backoff


def test_get_system_cost_indices_empty_response_keeps_old_cache(monkeypatch):
    _fake_get(monkeypatch, {
        "/industry/systems/": [
            {"solar_system_id": 30000142, "cost_indices": [
                {"activity": "manufacturing", "cost_index": 0.02},
            ]},
        ],
    })
    assert ESIClient().get_system_cost_indices(30000142) == {"manufacturing": 0.02}
    ESIClient._cost_indices_cache_at = __import__("time").time() - 21601
    _fake_get(monkeypatch, {"/industry/systems/": []})

    assert ESIClient().get_system_cost_indices(30000142) == {"manufacturing": 0.02}
    assert ESIClient.cost_indices_loaded() is True


def test_cost_indices_loaded_and_stale_helpers(monkeypatch):
    assert ESIClient.cost_indices_loaded() is False
    assert ESIClient.adjusted_prices_stale_seconds() is None
    assert ESIClient.cost_indices_stale_seconds() is None

    _fake_get(monkeypatch, {
        "/markets/prices/": [{"type_id": 34, "adjusted_price": 1.0}],
        "/industry/systems/": [
            {"solar_system_id": 1, "cost_indices": [{"activity": "manufacturing", "cost_index": 0.1}]},
        ],
    })
    ESIClient().get_adjusted_prices()
    ESIClient().get_system_cost_indices(1)
    assert ESIClient.cost_indices_loaded() is True
    assert ESIClient.adjusted_prices_stale_seconds() is None
    assert ESIClient.cost_indices_stale_seconds() is None

    ESIClient.clear_price_caches()
    assert ESIClient.cost_indices_loaded() is False
    assert ESIClient._adjusted_prices_fail_at == 0.0
    assert ESIClient._cost_indices_fail_at == 0.0


def test_cost_indices_loaded_false_when_past_hard_stale_ceiling(monkeypatch):
    _fake_get(monkeypatch, {
        "/industry/systems/": [
            {"solar_system_id": 1, "cost_indices": [{"activity": "manufacturing", "cost_index": 0.1}]},
        ],
    })
    ESIClient().get_system_cost_indices(1)
    assert ESIClient.cost_indices_loaded() is True
    ESIClient._cost_indices_cache_at = __import__("time").time() - (
        ESIClient.PRICE_DATA_MAX_STALE_SECONDS + 8 * 3600
    )
    assert ESIClient.cost_indices_loaded() is False
