"""F-04 / F-NEW-01: bounded, locked pending OAuth state."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from fastapi.testclient import TestClient

from eve_trader.api.app import create_app
from eve_trader.api.routers import auth as auth_router
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG

client = TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_pending():
    with auth_router._pending_lock:
        auth_router._pending.clear()
    yield
    with auth_router._pending_lock:
        auth_router._pending.clear()


def test_concurrent_insert_and_prune_does_not_raise(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_TTL", 0.05)

    errors = []

    def _one(_i):
        try:
            resp = client.get("/api/auth/buyer/start")
            assert resp.status_code in (200, 429)
        except Exception as e:  # noqa: BLE001 - we want any RuntimeError
            errors.append(e)

    with ThreadPoolExecutor(max_workers=40) as pool:
        list(pool.map(_one, range(80)))

    assert errors == []


def test_max_capacity_returns_429(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_MAX", 3)
    monkeypatch.setattr(auth_router, "_PENDING_MAX_PER_IP", 100)

    codes = [client.get("/api/auth/buyer/start").status_code for _ in range(5)]
    assert codes.count(200) == 3
    assert codes.count(429) == 2
    with auth_router._pending_lock:
        assert len(auth_router._pending) == 3


def test_per_ip_limit_returns_429(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_MAX", 100)
    monkeypatch.setattr(auth_router, "_PENDING_MAX_PER_IP", 2)

    codes = [client.get("/api/auth/buyer/start").status_code for _ in range(4)]
    assert codes.count(200) == 2
    assert 429 in codes


def test_expired_entries_are_pruned(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_TTL", 0.01)
    assert client.get("/api/auth/buyer/start").status_code == 200
    time.sleep(0.03)
    assert client.get("/api/auth/buyer/start").status_code == 200
    with auth_router._pending_lock:
        assert len(auth_router._pending) == 1


def test_callback_pop_is_atomic_under_lock(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    with auth_router._pending_lock:
        auth_router._pending["s"] = {
            "verifier": "v", "role_prefix": "buyer", "scopes": [],
            "created_at": time.time(), "browser_nonce": "n", "client_ip": "t",
        }

    got = []

    def _pop():
        with auth_router._pending_lock:
            got.append(auth_router._pending.pop("s", None))

    threads = [threading.Thread(target=_pop) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for g in got if g is not None) == 1
