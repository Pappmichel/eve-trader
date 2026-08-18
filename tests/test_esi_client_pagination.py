from eve_trader.esi_client import ESIClient


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
