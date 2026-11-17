"""current_prices remembers a final failure per market for 45 s."""
import threading

import pytest
import requests

from eve_trader import goonmetrics_client as gm


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    gm.clear_prices_cache()
    monkeypatch.setattr(gm.time, "sleep", lambda s: None)
    yield
    gm.clear_prices_cache()


class _Resp:
    def __init__(self, payload=None, bad_json=False):
        self._payload, self._bad = payload, bad_json

    def raise_for_status(self):
        pass

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


_ITEM = {"typeID": 34, "prices": {"updated": "x", "buy": {"max": 1.0}, "sell": {"min": 2.0}}}


def test_hanging_server_only_first_of_three_threads_uses_the_network():
    client = gm.GoonmetricsClient()
    calls = []
    release = threading.Event()

    def _get(url, timeout=None):
        calls.append(url)
        release.wait(5)
        raise requests.Timeout("hang")
    client.session.get = _get
    outcomes = []

    def _run():
        try:
            client.current_prices("jita")
        except requests.RequestException as e:
            outcomes.append(type(e))

    threads = [threading.Thread(target=_run) for _ in range(3)]
    for t in threads:
        t.start()
    release.set()
    for t in threads:
        t.join(10)
    assert outcomes == [requests.Timeout] * 3
    assert len(calls) == 3  # one caller's three attempts, nobody else's


def test_new_attempt_after_the_window(monkeypatch):
    client = gm.GoonmetricsClient()
    now = [1000.0]
    monkeypatch.setattr(gm.time, "time", lambda: now[0])
    calls = []

    def _get(url, timeout=None):
        calls.append(1)
        raise requests.ConnectionError("down")
    client.session.get = _get
    with pytest.raises(requests.ConnectionError):
        client.current_prices("jita")
    assert len(calls) == 3
    with pytest.raises(requests.ConnectionError):
        client.current_prices("jita")
    assert len(calls) == 3
    now[0] += 46
    with pytest.raises(requests.ConnectionError):
        client.current_prices("jita")
    assert len(calls) == 6


def test_other_market_is_not_blocked_by_a_failure():
    client = gm.GoonmetricsClient()
    answers = {"jita": requests.ConnectionError("down"), "home": _Resp([_ITEM])}

    def _get(url, timeout=None):
        a = answers["jita" if "/jita/" in url else "home"]
        if isinstance(a, Exception):
            raise a
        return a
    client.session.get = _get
    with pytest.raises(requests.ConnectionError):
        client.current_prices("jita")
    assert client.current_prices("home")[0].type_id == 34


def test_html_with_status_200_then_json_succeeds_on_second_attempt():
    client = gm.GoonmetricsClient()
    answers = [_Resp(bad_json=True), _Resp([_ITEM])]
    client.session.get = lambda url, timeout=None: answers.pop(0)
    assert client.current_prices("jita")[0].sell == 2.0
    assert answers == []


def test_unparseable_every_time_raises_request_exception_with_cause():
    client = gm.GoonmetricsClient()
    client.session.get = lambda url, timeout=None: _Resp(bad_json=True)
    with pytest.raises(requests.RequestException) as info:
        client.current_prices("jita")
    assert isinstance(info.value.__cause__, ValueError)


def test_empty_list_is_neither_cached_nor_remembered():
    client = gm.GoonmetricsClient()
    calls = []

    def _get(url, timeout=None):
        calls.append(1)
        return _Resp([])
    client.session.get = _get
    assert client.current_prices("jita") == []
    assert client.current_prices("jita") == []
    assert len(calls) == 2
