"""F-04 / F-NEW-01 / P5-04: bounded, locked, fair pending OAuth state."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from eve_trader.api.app import create_app
from eve_trader.api.routers import auth as auth_router
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG

pytestmark = pytest.mark.gate_off

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


def test_expired_entries_are_reclaimed_at_capacity(monkeypatch):
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_MAX", 3)
    monkeypatch.setattr(auth_router, "_PENDING_TTL", 0.01)
    for _ in range(3):
        assert client.get("/api/auth/buyer/start").status_code == 200
    time.sleep(0.03)
    assert client.get("/api/auth/buyer/start").status_code == 200
    with auth_router._pending_lock:
        assert len(auth_router._pending) == 1


def test_one_ip_cannot_consume_capacity_from_a_new_source(monkeypatch):
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_MAX", 4)
    monkeypatch.setattr(auth_router, "_PENDING_MAX_PER_IP", 8)
    current = {"ip": "10.0.0.1"}
    monkeypatch.setattr(auth_router, "_client_ip", lambda _req: current["ip"])

    for _ in range(4):
        assert client.get("/api/auth/buyer/start").status_code == 200
    assert client.get("/api/auth/buyer/start").status_code == 429

    current["ip"] = "10.0.0.2"
    assert client.get("/api/auth/buyer/start").status_code == 200
    with auth_router._pending_lock:
        assert len(auth_router._pending) == 4
        ips = [v["client_ip"] for v in auth_router._pending.values()]
        assert ips.count("10.0.0.1") == 3
        assert ips.count("10.0.0.2") == 1


def test_eviction_drops_oldest_from_heaviest_ip():
    with auth_router._pending_lock:
        auth_router._pending.clear()
        auth_router._pending["a1"] = {"created_at": 1.0, "client_ip": "1.1.1.1"}
        auth_router._pending["a2"] = {"created_at": 2.0, "client_ip": "1.1.1.1"}
        auth_router._pending["b1"] = {"created_at": 0.5, "client_ip": "2.2.2.2"}
        evicted = auth_router._evict_from_heaviest_ip_locked()
        assert evicted == "a1"
        assert "a1" not in auth_router._pending
        assert "a2" in auth_router._pending
        assert "b1" in auth_router._pending
        assert len(auth_router._pending) == 2


def test_same_ip_at_global_cap_does_not_evict_others(monkeypatch):
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_MAX", 3)
    monkeypatch.setattr(auth_router, "_PENDING_MAX_PER_IP", 100)
    codes = [client.get("/api/auth/buyer/start").status_code for _ in range(5)]
    assert codes.count(200) == 3
    assert codes.count(429) == 2
    with auth_router._pending_lock:
        assert len(auth_router._pending) == 3
