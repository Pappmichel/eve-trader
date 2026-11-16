import pytest

from eve_trader.esi_client import ESIClient, ESIError, ESIStatusError


class _FakeResp:
    def __init__(self, payload, total_pages=1):
        self._payload = payload
        self.headers = {"X-Pages": str(total_pages)}

    def json(self):
        return self._payload


def test_get_all_pages_single_page_does_not_fetch_a_second(monkeypatch):
    calls = []

    def _fake_get_response(self, path, params=None, auth_role=None, retries=3):
        calls.append(params["page"])
        return _FakeResp([{"item_id": 1}], total_pages=1)
    monkeypatch.setattr(ESIClient, "_get_response", _fake_get_response)

    result = ESIClient()._get_all_pages("/some/path/")

    assert calls == [1]
    assert result == [{"item_id": 1}]


def test_get_all_pages_empty_first_page_returns_empty_list_with_no_further_calls(monkeypatch):
    calls = []

    def _fake_get_response(self, path, params=None, auth_role=None, retries=3):
        calls.append(params["page"])
        return _FakeResp([], total_pages=1)
    monkeypatch.setattr(ESIClient, "_get_response", _fake_get_response)

    result = ESIClient()._get_all_pages("/some/path/")

    assert calls == [1]
    assert result == []


def test_get_all_pages_fetches_remaining_pages_and_preserves_order(monkeypatch):
    # Real gap confirmed via cProfile (2026-08-16): pages 2..N used to be
    # fetched one at a time, strictly sequentially, even though they're
    # independent requests once page 1's X-Pages header is known - a corp
    # with 9k+ assets (confirmed real account data) pages into ~10
    # sequential round-trips for that call alone. Pages 2..N must now be
    # requested (in any order/concurrently) but the *final result* must
    # still come back in page order, matching the old sequential behavior.
    pages = {
        1: [{"item_id": 1}, {"item_id": 2}],
        2: [{"item_id": 3}, {"item_id": 4}],
        3: [{"item_id": 5}],
    }
    requested_pages = []

    def _fake_get_response(self, path, params=None, auth_role=None, retries=3):
        page = params["page"]
        requested_pages.append(page)
        return _FakeResp(pages[page], total_pages=3)
    monkeypatch.setattr(ESIClient, "_get_response", _fake_get_response)

    result = ESIClient()._get_all_pages("/some/path/")

    assert sorted(requested_pages) == [1, 2, 3]  # every page requested exactly once
    assert result == [{"item_id": 1}, {"item_id": 2}, {"item_id": 3}, {"item_id": 4}, {"item_id": 5}]


def test_get_all_pages_forwards_params_and_auth_role_to_every_page(monkeypatch):
    seen = []

    def _fake_get_response(self, path, params=None, auth_role=None, retries=3):
        seen.append((path, dict(params), auth_role))
        return _FakeResp([{"n": params["page"]}], total_pages=2)
    monkeypatch.setattr(ESIClient, "_get_response", _fake_get_response)

    ESIClient()._get_all_pages("/some/path/", params={"datasource": "tranquility"}, auth_role="producer:1")

    assert all(p == "/some/path/" for p, _, _ in seen)
    assert all(a == "producer:1" for _, _, a in seen)
    assert all(prm["datasource"] == "tranquility" for _, prm, _ in seen)
    assert sorted(prm["page"] for _, prm, _ in seen) == [1, 2]


def test_get_all_pages_retries_empty_follow_up_page_once(monkeypatch):
    """An empty page 2 is retried once; a second good response is kept."""
    attempts = {1: 0, 2: 0}

    def _fake_get_response(self, path, params=None, auth_role=None, retries=3):
        page = params["page"]
        attempts[page] = attempts.get(page, 0) + 1
        if page == 1:
            return _FakeResp([{"item_id": 1}], total_pages=2)
        if attempts[page] == 1:
            return _FakeResp([], total_pages=2)
        return _FakeResp([{"item_id": 2}], total_pages=2)

    monkeypatch.setattr(ESIClient, "_get_response", _fake_get_response)
    result = ESIClient()._get_all_pages("/some/path/")
    assert attempts[2] == 2
    assert result == [{"item_id": 1}, {"item_id": 2}]


def test_get_all_pages_empty_follow_up_page_after_retry_raises(monkeypatch):
    """Page 2 stays empty after one retry → fail closed with ESIError."""
    attempts = {1: 0, 2: 0}

    def _fake_get_response(self, path, params=None, auth_role=None, retries=3):
        page = params["page"]
        attempts[page] = attempts.get(page, 0) + 1
        if page == 1:
            return _FakeResp([{"item_id": 1}], total_pages=2)
        return _FakeResp([], total_pages=2)

    monkeypatch.setattr(ESIClient, "_get_response", _fake_get_response)
    with pytest.raises(ESIError, match="Empty ESI page 2/2"):
        ESIClient()._get_all_pages("/some/path/")
    assert attempts[2] == 2


class _HdrResp:
    def __init__(self, payload, x_pages=2, last_modified=None):
        self._payload = payload
        self.headers = {"X-Pages": str(x_pages)}
        if last_modified:
            self.headers["Last-Modified"] = last_modified

    def json(self):
        return self._payload


_LM1 = "Mon, 16 Nov 2026 10:00:00 GMT"
_LM2 = "Mon, 16 Nov 2026 10:20:00 GMT"


def _patch(monkeypatch, handler):
    calls = []

    def _fake(self, path, params=None, auth_role=None, retries=3):
        page = params["page"]
        calls.append(page)
        return handler(page, calls.count(page))
    monkeypatch.setattr(ESIClient, "_get_response", _fake)
    return calls


def test_x_pages_mismatch_once_then_consistent_succeeds(monkeypatch):
    def handler(page, n):
        if page == 1:
            return _HdrResp([{"i": 1}])
        return _HdrResp([{"i": 2}], x_pages=3 if n == 1 else 2)
    calls = _patch(monkeypatch, handler)
    assert ESIClient()._get_all_pages("/p/") == [{"i": 1}, {"i": 2}]
    assert calls.count(1) == 2


def test_last_modified_mismatch_twice_raises(monkeypatch):
    def handler(page, n):
        return _HdrResp([{"i": page}], last_modified=_LM1 if page == 1 else _LM2)
    calls = _patch(monkeypatch, handler)
    with pytest.raises(ESIError, match="Last-Modified"):
        ESIClient()._get_all_pages("/p/")
    assert calls.count(1) == 2


def test_last_modified_missing_on_one_page_is_not_an_error(monkeypatch):
    def handler(page, n):
        return _HdrResp([{"i": page}], last_modified=_LM1 if page == 1 else None)
    calls = _patch(monkeypatch, handler)
    assert ESIClient()._get_all_pages("/p/") == [{"i": 1}, {"i": 2}]
    assert calls.count(1) == 1


def _full():
    return [{"i": n} for n in range(1000)]


def test_full_last_page_probe_404_is_end(monkeypatch):
    def handler(page, n):
        if page == 2:
            raise ESIStatusError(404, "HTTP 404")
        return _HdrResp(_full(), x_pages=1)
    calls = _patch(monkeypatch, handler)
    assert len(ESIClient()._get_all_pages("/p/")) == 1000
    assert calls == [1, 2]


def test_full_last_page_probe_empty_200_is_end(monkeypatch):
    def handler(page, n):
        return _HdrResp(_full() if page == 1 else [], x_pages=1)
    _patch(monkeypatch, handler)
    assert len(ESIClient()._get_all_pages("/p/")) == 1000


def test_full_last_page_probe_with_data_raises_after_one_reload(monkeypatch):
    def handler(page, n):
        return _HdrResp(_full() if page == 1 else [{"i": "x"}], x_pages=1)
    calls = _patch(monkeypatch, handler)
    with pytest.raises(ESIError, match="beyond the last page"):
        ESIClient()._get_all_pages("/p/")
    assert calls == [1, 2, 1, 2]


def test_short_last_page_is_never_probed(monkeypatch):
    def handler(page, n):
        return _HdrResp(_full() if page == 1 else [{"i": 1}], x_pages=2)
    calls = _patch(monkeypatch, handler)
    ESIClient()._get_all_pages("/p/")
    assert calls == [1, 2]


def test_collector_keeps_only_stamps_of_the_accepted_pass(monkeypatch):
    from eve_trader.esi_client import collect_last_modified

    def handler(page, n):
        if n == 1 and page == 2:
            return _HdrResp([{"i": 2}], x_pages=3, last_modified=_LM1)
        return _HdrResp([{"i": page}], last_modified=_LM2)
    _patch(monkeypatch, handler)
    with collect_last_modified() as seen:
        # first pass: page 1 carries LM2, page 2 LM1 and X-Pages 3 -> rejected
        ESIClient()._get_all_pages("/p/", auth_role="producer:1")
    assert len(seen) == 2
    assert {s.minute for s in seen} == {20}
