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
            resp = client.get("/api/auth/gate/start")
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

    codes = [client.get("/api/auth/gate/start").status_code for _ in range(5)]
    assert codes.count(200) == 3
    assert codes.count(429) == 2
    with auth_router._pending_lock:
        assert len(auth_router._pending) == 3


def test_per_ip_limit_returns_429(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_MAX", 100)
    monkeypatch.setattr(auth_router, "_PENDING_MAX_PER_IP", 2)

    codes = [client.get("/api/auth/gate/start").status_code for _ in range(4)]
    assert codes.count(200) == 2
    assert 429 in codes


def test_expired_entries_are_pruned(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_TTL", 0.01)
    assert client.get("/api/auth/gate/start").status_code == 200
    time.sleep(0.03)
    assert client.get("/api/auth/gate/start").status_code == 200
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
        assert client.get("/api/auth/gate/start").status_code == 200
    time.sleep(0.03)
    assert client.get("/api/auth/gate/start").status_code == 200
    with auth_router._pending_lock:
        assert len(auth_router._pending) == 1


def test_one_ip_cannot_consume_capacity_from_a_new_source(monkeypatch):
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_MAX", 4)
    monkeypatch.setattr(auth_router, "_PENDING_MAX_PER_IP", 8)
    current = {"ip": "10.0.0.1"}
    monkeypatch.setattr(auth_router, "_client_ip", lambda _req: current["ip"])

    for _ in range(4):
        assert client.get("/api/auth/gate/start").status_code == 200
    assert client.get("/api/auth/gate/start").status_code == 429

    current["ip"] = "10.0.0.2"
    assert client.get("/api/auth/gate/start").status_code == 200
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
    codes = [client.get("/api/auth/gate/start").status_code for _ in range(5)]
    assert codes.count(200) == 3
    assert codes.count(429) == 2
    with auth_router._pending_lock:
        assert len(auth_router._pending) == 3


# ----------------------------------------------------- state / nonce / ip
def _entry(**kw):
    e = {"verifier": "v", "role_prefix": "gate", "scopes": [], "created_at": time.time(),
         "browser_nonce": "n", "client_ip": "t", "tenant_id": None}
    e.update(kw)
    return e


def test_state_is_random_and_not_derived_from_the_role(monkeypatch):
    monkeypatch.setattr(OAUTH_CONFIG, "client_id", "test-client-id")
    monkeypatch.setattr(auth_router, "_PENDING_MAX_PER_IP", 100)
    urls = [client.get("/api/auth/gate/start").json()["url"] for _ in range(2)]
    states = [u.split("state=")[1].split("&")[0] for u in urls]
    assert states[0] != states[1]
    assert all(len(s) >= 40 and not s.startswith("gate") for s in states)


def test_wrong_nonce_keeps_the_entry_so_the_real_callback_still_works(monkeypatch):
    from eve_trader.auth import TokenManager
    auth_router._pending["s1"] = _entry()
    resp = client.get("/api/auth/callback", params={"code": "c", "state": "s1"},
                      cookies={auth_router._OAUTH_NONCE_COOKIE: "wrong"}, follow_redirects=False)
    assert "message=nonce_mismatch" in resp.headers["location"]
    assert "s1" in auth_router._pending

    def _boom(self, code, verifier):
        raise ValueError("stop here")
    monkeypatch.setattr(TokenManager, "_exchange_code", _boom)
    resp = client.get("/api/auth/callback", params={"code": "c", "state": "s1"},
                      cookies={auth_router._OAUTH_NONCE_COOKIE: "n"}, follow_redirects=False)
    assert "message=token_exchange_failed" in resp.headers["location"]
    assert "s1" not in auth_router._pending


def test_expired_unpruned_entry_is_rejected():
    auth_router._pending["old"] = _entry(created_at=time.time() - auth_router._PENDING_TTL - 5)
    resp = client.get("/api/auth/callback", params={"code": "c", "state": "old"},
                      cookies={auth_router._OAUTH_NONCE_COOKIE: "n"}, follow_redirects=False)
    assert "message=state_expired_or_unknown" in resp.headers["location"]
    assert "old" not in auth_router._pending


class _Req:
    def __init__(self, host):
        from types import SimpleNamespace
        self.client = SimpleNamespace(host=host) if host is not None else None


def test_client_ip_groups_ipv6_by_64_and_leaves_ipv4():
    f = auth_router._client_ip
    assert f(_Req("2001:db8:1:2::1")) == f(_Req("2001:db8:1:2:ffff::9"))
    assert f(_Req("2001:db8:1:2::1")) != f(_Req("2001:db8:1:3::1"))
    assert f(_Req("::ffff:203.0.113.5")) == "203.0.113.5"
    assert f(_Req("203.0.113.5")) == "203.0.113.5"
    assert f(_Req("not-an-ip")) == "unknown"
    assert f(_Req(None)) == "unknown"
