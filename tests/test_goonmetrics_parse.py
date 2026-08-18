import pytest
import requests

from eve_trader.config import TradingConfig
from eve_trader.esi_client import ESIClient
from eve_trader.goonmetrics_client import GoonmetricsClient, clear_prices_cache, _parse_history_xml


@pytest.fixture(autouse=True)
def _reset_prices_cache():
    # current_prices' module-level cache (see goonmetrics_client.py) is
    # process-wide, not per-instance - without this, a test that populates it
    # would silently leak that cached result into whichever test runs next
    # within the same _PRICES_CACHE_TTL window.
    clear_prices_cache()
    yield
    clear_prices_cache()


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<evec_api version="2" method="marketstat">
  <result>
    <rowset name="types">
      <type id="34">
        <history date="2026-01-01" minPrice="4.5" maxPrice="5.5" avgPrice="5.0"
                 movement="120000" numOrders="42"/>
        <history date="2026-01-02" minPrice="4.6" maxPrice="5.6" avgPrice="5.1"
                 movement="130000" numOrders="45"/>
      </type>
    </rowset>
  </result>
</evec_api>
"""


def test_parses_history_points():
    points = _parse_history_xml(SAMPLE_XML, region_id=10000002)
    assert len(points) == 2
    p0 = points[0]
    assert p0.type_id == 34
    assert p0.region_id == 10000002
    assert p0.date == "2026-01-01"
    assert p0.avg_price == 5.0
    assert p0.num_orders == 42


def test_price_history_falls_back_to_esi_on_goonmetrics_failure(monkeypatch):
    cfg = TradingConfig()
    client = GoonmetricsClient(cfg)

    def _raise(*args, **kwargs):
        raise requests.ConnectionError("Goonmetrics is down")
    monkeypatch.setattr(client.session, "get", _raise)

    def _fake_history(self, region_id, type_id):
        return [{"date": "2026-01-01", "average": 5.0, "highest": 5.5, "lowest": 4.5,
                  "order_count": 42, "volume": 1000}]
    monkeypatch.setattr(ESIClient, "region_market_history", _fake_history)

    points = client.price_history(cfg.jita_region_id, [34])

    assert len(points) == 1
    p = points[0]
    assert p.type_id == 34
    assert p.region_id == cfg.jita_region_id
    assert p.avg_price == 5.0
    assert p.min_price == 4.5
    assert p.max_price == 5.5
    assert p.num_orders == 42
    assert p.movement == 1000  # ESI's own volume (units/day), not average*volume - see goonmetrics_client.py


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


_FAKE_PAYLOAD = [
    {"typeID": 34, "prices": {"updated": "2026-01-01", "buy": {"max": 4.5}, "sell": {"min": 5.0}}},
]


def test_current_prices_caches_across_fresh_client_instances(monkeypatch):
    # Real perf gap confirmed via cProfile (2026-08-16): a GoonmetricsClient
    # is instantiated fresh on every _PlanContext (production/engine.py), so
    # an instance-level cache never actually hits - current_prices' cache has
    # to be module-level to survive that. Two calls (even across two
    # different client *instances*, matching what _PlanContext.__init__
    # does for home vs Jita, or what two consecutive plan computations do)
    # for the same market within the TTL must hit the network only once.
    calls = []

    def _fake_get(self, url, timeout):
        calls.append(url)
        return _FakeResponse(_FAKE_PAYLOAD)
    monkeypatch.setattr(requests.Session, "get", _fake_get)

    first = GoonmetricsClient().current_prices("jita")
    second = GoonmetricsClient().current_prices("jita")

    assert len(calls) == 1  # second call served from cache, no re-fetch
    assert first == second
    assert first[0].type_id == 34


def test_current_prices_cache_is_per_market(monkeypatch):
    calls = []

    def _fake_get(self, url, timeout):
        calls.append(url)
        return _FakeResponse(_FAKE_PAYLOAD)
    monkeypatch.setattr(requests.Session, "get", _fake_get)

    GoonmetricsClient().current_prices("jita")
    GoonmetricsClient().current_prices("home-structure-slug")

    assert len(calls) == 2  # different markets - each must fetch independently


def test_current_prices_refetches_after_clear_prices_cache(monkeypatch):
    calls = []

    def _fake_get(self, url, timeout):
        calls.append(url)
        return _FakeResponse(_FAKE_PAYLOAD)
    monkeypatch.setattr(requests.Session, "get", _fake_get)

    GoonmetricsClient().current_prices("jita")
    clear_prices_cache()
    GoonmetricsClient().current_prices("jita")

    assert len(calls) == 2


def test_current_prices_does_not_serialize_unrelated_markets(monkeypatch):
    # Confirmed real gap (2026-08-16): the cache lock used to be a single
    # lock shared across every market, held for the whole (multi-second, per
    # current_prices' own docstring) fetch - a concurrent request for a
    # *different* market (e.g. Jita for a Buy/Build plan refresh, home
    # market for a Material Tree lookup at the same time) blocked on it for
    # no reason, since they don't share a cache entry at all. Two threads
    # fetching two different markets, each with an artificial per-request
    # delay, must overlap (finish in roughly one delay's worth of wall time,
    # not two serialized ones).
    import threading
    import time as time_module

    DELAY = 0.3

    def _fake_get(self, url, timeout):
        time_module.sleep(DELAY)
        return _FakeResponse(_FAKE_PAYLOAD)
    monkeypatch.setattr(requests.Session, "get", _fake_get)

    results = {}

    def _fetch(market):
        results[market] = GoonmetricsClient().current_prices(market)

    start = time_module.monotonic()
    t1 = threading.Thread(target=_fetch, args=("jita",))
    t2 = threading.Thread(target=_fetch, args=("home-structure-slug",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    elapsed = time_module.monotonic() - start

    assert "jita" in results and "home-structure-slug" in results
    assert elapsed < DELAY * 2  # ran concurrently, not serialized behind one shared lock
