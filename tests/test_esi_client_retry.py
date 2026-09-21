"""Tests for ESIClient._get_response's rate-limit retry behavior. GitHub
issue #99: confirmed live 2026-08-22 that region_order_stats_bulk under real
shortlist-sized concurrent load gets plain HTTP 429 ("Rate limit exceeded")
responses from a burst limiter distinct from ESI's own 420 error-limit
mechanism - only 420 was retried before this fix, so every 429 immediately
became a silent "no market data" for that item (no retry at all)."""
import time

import requests

from eve_trader.auth import TokenManager, TokenRecord
from eve_trader.esi_client import ESIClient, ESIError


class _FakeResp:
    def __init__(self, status_code, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self):
        return {}


def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def test_get_response_retries_on_429_and_eventually_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    responses = [_FakeResp(429, text='{"error":"Rate limit exceeded"}'), _FakeResp(200)]
    calls = []

    def fake_get(self, url, params=None, headers=None, timeout=30):
        calls.append(url)
        return responses.pop(0)
    monkeypatch.setattr(requests.Session, "get", fake_get)

    resp = ESIClient()._get_response("/some/path/")

    assert resp.status_code == 200
    assert len(calls) == 2


def test_get_response_429_uses_retry_after_header_when_present(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
    responses = [_FakeResp(429, headers={"Retry-After": "7"}), _FakeResp(200)]

    def fake_get(self, url, params=None, headers=None, timeout=30):
        return responses.pop(0)
    monkeypatch.setattr(requests.Session, "get", fake_get)

    ESIClient()._get_response("/some/path/")

    assert sleeps == [7.0]


def test_get_response_420_still_retries_same_as_before(monkeypatch):
    _no_sleep(monkeypatch)
    responses = [_FakeResp(420), _FakeResp(200)]

    def fake_get(self, url, params=None, headers=None, timeout=30):
        return responses.pop(0)
    monkeypatch.setattr(requests.Session, "get", fake_get)

    resp = ESIClient()._get_response("/some/path/")

    assert resp.status_code == 200


def test_get_response_raises_esierror_after_exhausting_retries_on_429(monkeypatch):
    _no_sleep(monkeypatch)

    def fake_get(self, url, params=None, headers=None, timeout=30):
        return _FakeResp(429, text='{"error":"Rate limit exceeded"}')
    monkeypatch.setattr(requests.Session, "get", fake_get)

    try:
        ESIClient()._get_response("/some/path/", retries=3)
        assert False, "expected ESIError"
    except ESIError:
        pass


def test_get_response_does_not_retry_a_non_rate_limit_4xx(monkeypatch):
    _no_sleep(monkeypatch)
    calls = []

    def fake_get(self, url, params=None, headers=None, timeout=30):
        calls.append(url)
        return _FakeResp(404, text="not found")
    monkeypatch.setattr(requests.Session, "get", fake_get)

    try:
        ESIClient()._get_response("/some/path/", retries=3)
        assert False, "expected ESIError"
    except ESIError:
        pass
    assert len(calls) == 1  # a genuine 404 isn't a rate-limit signal - no point retrying it


def test_get_response_converts_transport_failure_to_esierror(monkeypatch):
    # Timeouts/connection errors used to leak as requests.RequestException
    # past every do_* that only catches ESIError. Retried like a 502, then
    # raised as ESIError so existing ActionError wraps keep covering them.
    _no_sleep(monkeypatch)
    calls = []

    def fake_get(self, url, params=None, headers=None, timeout=30):
        calls.append(url)
        raise requests.ConnectionError("timed out")
    monkeypatch.setattr(requests.Session, "get", fake_get)

    try:
        ESIClient()._get_response("/some/path/", retries=3)
        assert False, "expected ESIError"
    except ESIError as e:
        assert "timed out" in str(e)
    except requests.RequestException:
        assert False, "transport failure must not leak as RequestException"
    assert len(calls) == 3


def test_post_response_converts_transport_failure_to_esierror(monkeypatch):
    _no_sleep(monkeypatch)

    def fake_post(self, url, json=None, params=None, timeout=30):
        raise requests.Timeout("read timed out")
    monkeypatch.setattr(requests.Session, "post", fake_post)

    try:
        ESIClient()._post_response("/universe/ids/", json_body=["Jita"], retries=2)
        assert False, "expected ESIError"
    except ESIError as e:
        assert "read timed out" in str(e)
    except requests.RequestException:
        assert False, "transport failure must not leak as RequestException"


def _expired_token_manager(role: str = "producer:1") -> TokenManager:
    """In-memory TokenManager so this file stays Postgres-free. `_loaded`
    skips storage; expires_at=0 forces get_token onto _refresh."""
    tm = TokenManager()
    tm._loaded = True
    tm._tokens[role] = TokenRecord(
        role=role, character_id=1, character_name="Alice",
        access_token="stale", refresh_token="revoked", expires_at=0.0,
        scopes="esi-assets.read_assets.v1",
    )
    return tm


def _invalid_grant_response(url: str) -> requests.Response:
    resp = requests.Response()
    resp.status_code = 400
    resp.reason = "Bad Request"
    resp.url = url
    resp._content = b'{"error":"invalid_grant"}'
    return resp


def test_get_response_converts_dead_refresh_token_to_esierror(monkeypatch):
    # TokenManager._refresh calls resp.raise_for_status(), so a revoked
    # refresh token used to leak as requests.HTTPError past every
    # except ESIError handler. Fail fast (not retried like a 502).
    _no_sleep(monkeypatch)
    esi_calls = []
    refresh_calls = []

    def fake_get(self, url, params=None, headers=None, timeout=30):
        esi_calls.append(url)
        return _FakeResp(200)

    def fake_post(url, data=None, headers=None, timeout=30, **kwargs):
        refresh_calls.append(data)
        return _invalid_grant_response(url)

    monkeypatch.setattr(requests.Session, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    tm = _expired_token_manager()
    # get_token re-calls _load() after the refresh lock; skip storage so
    # this file stays Postgres-free while still exercising real _refresh.
    monkeypatch.setattr(tm, "_load", lambda: None)

    try:
        ESIClient(tokens=tm)._get_response(
            "/characters/1/assets/", auth_role="producer:1", retries=3,
        )
        assert False, "expected ESIError"
    except ESIError as e:
        message = str(e)
        assert "Token refresh failed for role 'producer:1'" in message
        assert "Re-authorize this character" in message
    except requests.HTTPError:
        assert False, "dead refresh token must not leak as HTTPError"
    assert len(refresh_calls) == 1  # not retried like a 502
    assert esi_calls == []  # never reached the ESI GET


def test_get_response_converts_refresh_transport_failure_to_esierror(monkeypatch):
    """`_refresh`'s requests.post can fail with Timeout/ConnectionError,
    which are siblings of HTTPError (both RequestException), not subclasses.
    An HTTPError-only handler here let those leak exactly like the revoked
    token above did - verified live 2026-09-21 - so the orchestrator never
    stamped esi_freshness and a corp's first member still aborted the whole
    corporation."""
    _no_sleep(monkeypatch)
    esi_calls = []

    def fake_get(self, url, params=None, headers=None, timeout=30):
        esi_calls.append(url)
        return _FakeResp(200)

    def fake_post(url, data=None, headers=None, timeout=30, **kwargs):
        raise requests.ConnectionError("connection reset during refresh")

    monkeypatch.setattr(requests.Session, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    tm = _expired_token_manager()
    monkeypatch.setattr(tm, "_load", lambda: None)

    try:
        ESIClient(tokens=tm)._get_response(
            "/characters/1/assets/", auth_role="producer:1", retries=3,
        )
        assert False, "expected ESIError"
    except ESIError as e:
        assert "Token refresh failed for role 'producer:1'" in str(e)
    except requests.RequestException:
        assert False, "a transport failure during refresh must not leak"
    assert esi_calls == []  # never reached the ESI GET
