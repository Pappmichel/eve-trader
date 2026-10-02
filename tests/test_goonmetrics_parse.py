import pytest
import requests

from eve_trader.config import TradingConfig
from eve_trader.esi_client import ESIClient
from eve_trader.goonmetrics_client import (
    PRICE_DATA_MAX_TYPES_PER_CALL, GoonmetricsClient, clear_prices_cache, _parse_history_xml, _parse_price_data_xml,
)


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


# ---------------------------------------------------- station_current_prices / price_data
# Real response shape confirmed live 2026-09-28 against goonmetrics.apps.gnf.lt
# for a real private player structure ID that has no appraise.gnf.lt market
# slug at all - see module_reprocessing/candidate_discovery.py's own comment
# on why structure_id is now preferred over structure_market_slug.
SAMPLE_PRICE_DATA_XML = """<goonmetrics method="price_data" version="1.0">
  <price_data>
    <type id="34">
      <updated>2026-09-28T17:49:10Z</updated>
      <all><weekly_movement>10854709540.3</weekly_movement></all>
      <buy><max>3.69</max><listed>1556115691</listed></buy>
      <sell><min>3.87</min><listed>2790543698</listed></sell>
    </type>
    <type id="35">
      <updated>2026-09-28T17:49:10Z</updated>
      <all><weekly_movement>6876363497.0</weekly_movement></all>
      <buy><max>16.15</max><listed>2138518668</listed></buy>
      <sell><min>19.00</min><listed>2305531227</listed></sell>
    </type>
  </price_data>
</goonmetrics>
"""


def test_parses_price_data_xml():
    prices = _parse_price_data_xml(SAMPLE_PRICE_DATA_XML)
    assert set(prices) == {34, 35}
    assert prices[34].buy == 3.69
    assert prices[34].sell == 3.87
    assert prices[34].updated == "2026-09-28T17:49:10Z"
    assert prices[35].buy == 16.15
    assert prices[35].sell == 19.00


def test_parses_price_data_xml_skips_types_with_no_buy_or_sell_side():
    xml = """<goonmetrics method="price_data" version="1.0">
      <price_data>
        <type id="34">
          <updated>2026-09-28T17:49:10Z</updated>
          <all><weekly_movement>0</weekly_movement></all>
        </type>
      </price_data>
    </goonmetrics>
    """
    assert _parse_price_data_xml(xml) == {}


def test_station_current_prices_builds_expected_url_and_parses(monkeypatch):
    captured = {}

    def _fake_get(self, url, timeout):
        captured["url"] = url
        return _XmlResponse(SAMPLE_PRICE_DATA_XML)
    monkeypatch.setattr(requests.Session, "get", _fake_get)

    client = GoonmetricsClient()
    prices = client.station_current_prices(1049588174021, [34, 35])

    assert "station_id=1049588174021" in captured["url"]
    assert "type_id=34,35" in captured["url"]
    assert prices[34].sell == 3.87
    assert prices[35].sell == 19.00


def test_station_current_prices_returns_empty_dict_for_no_type_ids():
    client = GoonmetricsClient()
    assert client.station_current_prices(1049588174021, []) == {}


def test_station_current_prices_chunks_over_the_per_call_cap(monkeypatch):
    # PRICE_DATA_MAX_TYPES_PER_CALL (50) is the API's own documented limit -
    # a caller asking for more type_ids than that must transparently become
    # more than one HTTP call, each within the cap, and the merged result
    # must cover every requested type_id.
    type_ids = list(range(1, PRICE_DATA_MAX_TYPES_PER_CALL + 11))  # 60 ids -> 2 calls
    calls = []

    def _fake_get(self, url, timeout):
        calls.append(url)
        ids_param = url.split("type_id=")[1]
        ids = [int(t) for t in ids_param.split(",")]
        xml_types = "".join(
            f'<type id="{t}"><updated>2026-09-28T00:00:00Z</updated>'
            f'<buy><max>1.0</max></buy><sell><min>2.0</min></sell></type>'
            for t in ids
        )
        return _XmlResponse(f'<goonmetrics><price_data>{xml_types}</price_data></goonmetrics>')
    monkeypatch.setattr(requests.Session, "get", _fake_get)

    client = GoonmetricsClient()
    prices = client.station_current_prices(1049588174021, type_ids)

    assert len(calls) == 2
    assert set(prices) == set(type_ids)


class _XmlResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


EMPTY_HISTORY_XML = '<goonmetrics method="price_history" version="1.0"><price_history /></goonmetrics>'


class _XmlResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def _esi_rows(dates):
    return [{"date": d, "average": 5.0, "highest": 5.5, "lowest": 4.5, "order_count": 3, "volume": 10}
            for d in dates]


def test_region_goonmetrics_does_not_track_goes_to_esi(monkeypatch):
    # Goonmetrics answers an empty history for Domain (Amarr), even for
    # Tritanium; that region must be read from ESI instead.
    cfg = TradingConfig()
    client = GoonmetricsClient(cfg)
    urls = []
    monkeypatch.setattr(client.session, "get",
                        lambda url, timeout=None: urls.append(url) or _XmlResponse(EMPTY_HISTORY_XML))
    esi_calls = []
    monkeypatch.setattr(ESIClient, "region_market_history",
                        lambda self, region_id, type_id: esi_calls.append((region_id, type_id)) or _esi_rows(["2026-09-30"]))

    points = client.price_history(10000043, [34, 35])
    client.price_history(10000043, [36])

    assert sorted(esi_calls) == [(10000043, 34), (10000043, 35), (10000043, 36)]
    assert {p.type_id for p in points} == {34, 35}
    assert len(urls) == 1  # one coverage probe, remembered; no batch request


def test_tracked_region_keeps_using_goonmetrics(monkeypatch):
    cfg = TradingConfig()
    client = GoonmetricsClient(cfg)
    monkeypatch.setattr(client.session, "get", lambda url, timeout=None: _XmlResponse(SAMPLE_XML))
    monkeypatch.setattr(ESIClient, "region_market_history",
                        lambda self, region_id, type_id: pytest.fail("ESI must not be called"))

    points = client.price_history(cfg.jita_region_id, [34])

    assert points and all(p.region_id == cfg.jita_region_id for p in points)


def test_failed_probe_is_not_remembered_as_untracked(monkeypatch):
    cfg = TradingConfig()
    client = GoonmetricsClient(cfg)

    def _down(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(client.session, "get", _down)
    assert client.region_has_goonmetrics_history(10000043) is True
    monkeypatch.setattr(client.session, "get", lambda url, timeout=None: _XmlResponse(EMPTY_HISTORY_XML))
    assert client.region_has_goonmetrics_history(10000043) is False


def test_esi_history_is_cut_to_goonmetrics_window(monkeypatch):
    cfg = TradingConfig()
    client = GoonmetricsClient(cfg)
    monkeypatch.setattr(client.session, "get", lambda url, timeout=None: _XmlResponse(EMPTY_HISTORY_XML))
    dates = ["2025-10-01", "2026-08-31", "2026-09-01", "2026-09-30"]
    monkeypatch.setattr(ESIClient, "region_market_history", lambda self, region_id, type_id: _esi_rows(dates))

    points = client.price_history(10000043, [34])

    assert sorted(p.date for p in points) == ["2026-09-01", "2026-09-30"]
