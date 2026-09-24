"""Login callback, middleware re-check, and /api/gate/status for the allowlist."""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, access_policy, storage
from eve_trader.access_policy import Verdict
from eve_trader.api.app import create_app
from eve_trader.api.routers import auth as auth_router
from eve_trader.auth import TokenManager
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG
from eve_trader.esi_client import ESIClient

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

client = TestClient(create_app())


@pytest.fixture(autouse=True)
def _wipe():
    client.cookies.clear()
    pg_helpers.wipe_tables(
        "tool_grants", "tenant_registry_entries", "character_session_revocations",
        "access_requests", "access_allowlist",
    )
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    yield
    client.cookies.clear()


def _enable_gate(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")


def _callback(monkeypatch, character_id, name, *, affiliation):
    state = f"gate-{character_id}-{time.time()}"
    auth_router._pending[state] = {
        "verifier": "v", "role_prefix": "gate", "scopes": [],
        "created_at": time.time(), "browser_nonce": "n",
    }
    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (character_id, name)))
    monkeypatch.setattr(access_policy, "fetch_affiliation", lambda _cid: affiliation)
    monkeypatch.setattr(ESIClient, "resolve_names", lambda self, ids: {i: f"Name {i}" for i in ids})
    return client.get(
        "/api/auth/callback", params={"code": "abc", "state": state},
        cookies={auth_router._OAUTH_NONCE_COOKIE: "n"}, follow_redirects=False,
    )


def _has_session_cookie(resp) -> bool:
    return access_gate.SESSION_COOKIE_NAME in resp.headers.get("set-cookie", "")


def _age_affiliation(character_id: int, interval: str) -> None:
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE tenant_registry_entries SET affiliation_checked_at = now() - %s::interval "
            "WHERE entry_type = 'character' AND entry_id = %s",
            (interval, character_id),
        )


def _register(character_id: int, tools=("trading",), name="Pilot"):
    tenant_id = storage.create_tenant(f"{name}-{uuid.uuid4()}")
    storage.add_tenant_registry_entry(tenant_id, character_id, character_name=name)
    for tool in tools:
        storage.set_tool_grant(character_id, tool, tenant_id)
    return tenant_id


# ------------------------------------------------------------------- callback
def test_unregistered_not_allowlisted_is_denied(monkeypatch, _apply_admin_schema, _apply_phase3_schema):
    _enable_gate(monkeypatch)
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    resp = _callback(monkeypatch, 999, "Nope", affiliation=(2, None))
    assert "gate=denied" in resp.headers["location"]
    assert not _has_session_cookie(resp)
    assert storage.get_access_request(999) is None


def test_unregistered_allowlisted_creates_a_pending_request(monkeypatch, _apply_admin_schema, _apply_phase3_schema):
    _enable_gate(monkeypatch)
    storage.add_allowlist_entry("alliance", 8, "Ally")
    resp = _callback(monkeypatch, 999, "Newbie", affiliation=(2, 8))
    assert "gate=pending" in resp.headers["location"]
    assert not _has_session_cookie(resp)
    row = storage.get_access_request(999)
    assert row["status"] == "pending"
    assert row["character_name"] == "Newbie"
    assert row["corporation_id"] == 2
    assert row["alliance_id"] == 8
    assert row["corporation_name"] == "Name 2"


def test_repeat_login_refreshes_a_pending_request(monkeypatch, _apply_admin_schema, _apply_phase3_schema):
    _enable_gate(monkeypatch)
    storage.add_allowlist_entry("corporation", 2, "Corp")
    storage.upsert_pending_access_request(999, "Old", 2, "Corp", None, None)
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE access_requests SET last_login_at = now() - interval '2 days' WHERE character_id = 999"
        )
    before = storage.get_access_request(999)["last_login_at"]
    resp = _callback(monkeypatch, 999, "New Name", affiliation=(2, None))
    assert "gate=pending" in resp.headers["location"]
    assert not _has_session_cookie(resp)
    row = storage.get_access_request(999)
    assert row["status"] == "pending"
    assert row["character_name"] == "New Name"
    assert row["last_login_at"] != before


def test_rejected_request_stays_blocked(monkeypatch, _apply_admin_schema, _apply_phase3_schema):
    _enable_gate(monkeypatch)
    storage.add_allowlist_entry("corporation", 2, "Corp")
    storage.upsert_pending_access_request(999, "Nope", 2, "Corp", None, None)
    storage.reject_access_request(999, 1)
    resp = _callback(monkeypatch, 999, "Nope", affiliation=(2, None))
    assert "gate=rejected" in resp.headers["location"]
    assert not _has_session_cookie(resp)
    assert storage.get_access_request(999)["status"] == "rejected"


def test_unregistered_affiliation_failure_is_an_error(monkeypatch, _apply_admin_schema, _apply_phase3_schema):
    _enable_gate(monkeypatch)
    storage.add_allowlist_entry("corporation", 2, "Corp")
    resp = _callback(monkeypatch, 999, "Nope", affiliation=None)
    assert "gate=error" in resp.headers["location"]
    assert "affiliation_unavailable" in resp.headers["location"]
    assert not _has_session_cookie(resp)
    assert storage.get_access_request(999) is None


def test_registered_character_on_empty_allowlist_logs_in(monkeypatch, _apply_admin_schema, _apply_phase3_schema):
    _enable_gate(monkeypatch)
    tenant_id = _register(999)
    resp = _callback(monkeypatch, 999, "Pilot", affiliation=(2, None))
    assert "gate=success" in resp.headers["location"]
    assert _has_session_cookie(resp)
    assert storage.resolve_tenant_id(999) == tenant_id


def test_registered_character_leaving_the_allowlist_is_suspended(
    monkeypatch, _apply_admin_schema, _apply_phase3_schema,
):
    _enable_gate(monkeypatch)
    tenant_id = _register(999, name="Pilot")
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    resp = _callback(monkeypatch, 999, "Pilot", affiliation=(2, None))
    assert "gate=suspended" in resp.headers["location"]
    assert not _has_session_cookie(resp)
    assert storage.resolve_tenant_id(999) == tenant_id
    assert storage.get_registry_affiliation(999)["access_suspended"] is True


def test_registered_esi_failure_uses_a_recent_stored_affiliation(
    monkeypatch, _apply_admin_schema, _apply_phase3_schema,
):
    _enable_gate(monkeypatch)
    _register(999)
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    storage.update_registry_affiliation(999, 1, None, False)
    _age_affiliation(999, "1 day")
    resp = _callback(monkeypatch, 999, "Pilot", affiliation=None)
    assert "gate=success" in resp.headers["location"]
    assert _has_session_cookie(resp)


def test_registered_esi_failure_with_a_stale_affiliation_is_an_error(
    monkeypatch, _apply_admin_schema, _apply_phase3_schema,
):
    _enable_gate(monkeypatch)
    _register(999)
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    storage.update_registry_affiliation(999, 1, None, False)
    _age_affiliation(999, "8 days")
    resp = _callback(monkeypatch, 999, "Pilot", affiliation=None)
    assert "gate=error" in resp.headers["location"]
    assert "affiliation_unavailable" in resp.headers["location"]
    assert not _has_session_cookie(resp)


def test_admin_is_not_rechecked_at_login(monkeypatch, _apply_admin_schema, _apply_phase3_schema):
    _enable_gate(monkeypatch)
    _register(999, tools=("admin",))
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    calls = {"n": 0}

    def fetch(_cid):
        calls["n"] += 1
        return (2, None)

    monkeypatch.setattr(TokenManager, "_exchange_code", lambda self, code, verifier: {"access_token": "tok"})
    monkeypatch.setattr(TokenManager, "_verify", staticmethod(lambda token: (999, "Admin")))
    monkeypatch.setattr(access_policy, "fetch_affiliation", fetch)
    state = "admin-not-rechecked"
    auth_router._pending[state] = {
        "verifier": "v", "role_prefix": "gate", "scopes": [],
        "created_at": time.time(), "browser_nonce": "n",
    }
    resp = client.get(
        "/api/auth/callback", params={"code": "abc", "state": state},
        cookies={auth_router._OAUTH_NONCE_COOKIE: "n"}, follow_redirects=False,
    )
    assert "gate=success" in resp.headers["location"]
    assert calls["n"] == 0


# ------------------------------------------------------------------ middleware
def _cookie(character_id, tenant_id, name="Pilot"):
    token = access_gate.create_session_token(character_id, name, tenant_id)
    return {access_gate.SESSION_COOKIE_NAME: token}


def test_middleware_suspended_flag_is_403(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema,
):
    _enable_gate(monkeypatch)
    tenant_id = _register(11)
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    storage.update_registry_affiliation(11, 2, None, True)
    resp = client.get("/api/trading/settings", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "access_suspended"


def test_middleware_stale_check_refreshes_in_a_threadpool(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema,
):
    _enable_gate(monkeypatch)
    tenant_id = _register(11)
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    storage.update_registry_affiliation(11, 1, None, False)
    _age_affiliation(11, "7 hours")
    calls = {"n": 0, "pooled": False}

    def refresh(character_id, tool_keys, force=False):
        calls["n"] += 1
        return Verdict.SUSPENDED

    from eve_trader.api import app as app_module
    real_pool = app_module.run_in_threadpool

    async def pool(fn, *args, **kwargs):
        calls["pooled"] = True
        return await real_pool(fn, *args, **kwargs)

    monkeypatch.setattr(access_policy, "refresh_registered", refresh)
    monkeypatch.setattr(app_module, "run_in_threadpool", pool)
    resp = client.get("/api/trading/settings", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "access_suspended"
    assert calls["n"] == 1
    assert calls["pooled"] is True


def test_middleware_unknown_affiliation_is_403(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema,
):
    _enable_gate(monkeypatch)
    tenant_id = _register(11)
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    storage.update_registry_affiliation(11, 1, None, False)
    _age_affiliation(11, "7 hours")
    monkeypatch.setattr(access_policy, "refresh_registered", lambda *a, **k: Verdict.UNKNOWN)
    resp = client.get("/api/trading/settings", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "access_unverifiable"


def test_middleware_does_not_recheck_an_admin(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema,
):
    _enable_gate(monkeypatch)
    tenant_id = _register(11, tools=("admin",))
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    storage.update_registry_affiliation(11, 1, None, False)
    _age_affiliation(11, "7 hours")
    calls = {"n": 0}
    monkeypatch.setattr(
        access_policy, "refresh_registered",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1),
    )
    resp = client.get("/api/admin/tenants", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 200
    assert calls["n"] == 0


def test_middleware_gate_off_ignores_suspension(
    monkeypatch, _apply_phase1_schema, _apply_phase2_schema, _apply_admin_schema,
):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", False)
    _register(11)
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    storage.update_registry_affiliation(11, 2, None, True)
    assert client.get("/api/trading/settings").status_code == 200


# ---------------------------------------------------------------- gate status
def test_gate_status_suspended_flag_and_admin_only_count(
    monkeypatch, _apply_phase1_schema, _apply_admin_schema,
):
    _enable_gate(monkeypatch)
    tenant_id = _register(11)
    storage.add_allowlist_entry("corporation", 1, "Allowed")
    storage.update_registry_affiliation(11, 2, None, True)
    storage.upsert_pending_access_request(50, "Asker", 1, "Allowed", None, None)
    resp = client.get("/api/gate/status", cookies=_cookie(11, tenant_id))
    body = resp.json()
    assert body["logged_in"] is True
    assert body["suspended"] is True
    assert body["pending_access_requests"] is None

    admin_tenant = _register(12, tools=("admin",), name="Admin")
    resp = client.get("/api/gate/status", cookies=_cookie(12, admin_tenant, name="Admin"))
    body = resp.json()
    assert body["suspended"] is False
    assert body["pending_access_requests"] == 1
