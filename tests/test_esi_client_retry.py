"""Tests for ESIClient._get_response's rate-limit retry behavior. GitHub
issue #99: confirmed live 2026-08-22 that region_order_stats_bulk under real
shortlist-sized concurrent load gets plain HTTP 429 ("Rate limit exceeded")
responses from a burst limiter distinct from ESI's own 420 error-limit
mechanism - only 420 was retried before this fix, so every 429 immediately
became a silent "no market data" for that item (no retry at all)."""
import time

import requests

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
