"""F-01 + F-03: session cookie is only valid while the character still
belongs to the cookie's tenant; grants are only valid for that tenant.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG
from itsdangerous.timed import TimestampSigner

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

client = TestClient(create_app())


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("tenant_registry_entries", "tool_grants")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    yield


def _enable_gate(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")


def _cookie(character_id, tenant_id, name="Pilot", *, issued_unix: int | None = None):
    """If issued_unix is set, the cookie’s itsdangerous timestamp uses that
    whole second (needed because the signer has 1s resolution)."""
    if issued_unix is None:
        token = access_gate.create_session_token(character_id, name, tenant_id)
    else:
        orig = TimestampSigner.get_timestamp
        TimestampSigner.get_timestamp = lambda self: issued_unix  # type: ignore[method-assign]
        try:
            token = access_gate.create_session_token(character_id, name, tenant_id)
        finally:
            TimestampSigner.get_timestamp = orig  # type: ignore[method-assign]
    return {access_gate.SESSION_COOKIE_NAME: token}


def test_valid_character_tenant_grant_is_allowed(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_id)

    resp = client.get("/api/trading/settings", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 200


def test_unknown_character_is_401(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")

    resp = client.get("/api/trading/settings", cookies=_cookie(99, tenant_id))
    assert resp.status_code == 401


def test_character_not_in_session_tenant_is_401(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_a = storage.create_tenant("A")
    tenant_b = storage.create_tenant("B")
    storage.add_tenant_registry_entry(tenant_a, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_a)

    resp = client.get("/api/trading/settings", cookies=_cookie(11, tenant_b))
    assert resp.status_code == 401


def test_registered_character_without_grant_is_403(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")

    resp = client.get("/api/trading/settings", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 403


def test_grant_for_a_different_tenant_is_403(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_a = storage.create_tenant("A")
    tenant_b = storage.create_tenant("B")
    storage.add_tenant_registry_entry(tenant_a, 11, character_name="A")
    # Grant exists, but for the wrong tenant — JOIN requires g.tenant_id = e.tenant_id.
    storage.set_tool_grant(11, "trading", tenant_b)

    resp = client.get("/api/trading/settings", cookies=_cookie(11, tenant_a))
    assert resp.status_code == 403


def test_revoked_grant_is_rejected_on_the_next_request(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_id)
    cookies = _cookie(11, tenant_id)
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 200

    storage.revoke_tool_grant(11, "trading")
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 403


def test_removed_character_old_cookie_is_401(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_id)
    cookies = _cookie(11, tenant_id)
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 200

    storage.remove_tenant_registry_entry(11)
    # Physical grant may still exist.
    assert storage.list_tool_grants_for_character(11) == ["trading"]
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 401


def test_reassigned_character_old_cookie_is_401(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_a = storage.create_tenant("A")
    tenant_c = storage.create_tenant("C")
    storage.add_tenant_registry_entry(tenant_a, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_a)
    cookies = _cookie(11, tenant_a, issued_unix=int(time.time()) - 2)
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 200

    storage.add_tenant_registry_entry(tenant_c, 11, character_name="A")
    # Physical grant for tenant A may still exist; JOIN requires g.tenant_id = e.tenant_id.
    assert "trading" in storage.list_tool_grants_for_character(11)
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 401
    # Cookie for the new tenant still has no grant there.
    assert client.get("/api/trading/settings", cookies=_cookie(11, tenant_c)).status_code == 403


def test_sessions_valid_after_invalidates_old_cookie(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_id)
    cookies = _cookie(11, tenant_id, issued_unix=int(time.time()) - 2)
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 200

    storage.revoke_sessions_for_character(11)
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 401

    # New login (fresh cookie) still works.
    new_cookies = _cookie(11, tenant_id)
    assert client.get("/api/trading/settings", cookies=new_cookies).status_code == 200


def test_logout_revokes_server_side(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_id)
    cookies = _cookie(11, tenant_id, issued_unix=int(time.time()) - 2)

    assert client.post("/api/gate/logout", cookies=cookies).status_code == 200
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 401


def test_db_error_fail_closed(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_id)

    def boom(*_a, **_k):
        raise RuntimeError("postgres down")
    monkeypatch.setattr(storage, "session_authorization", boom)

    resp = client.get("/api/trading/settings", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 401


def test_admin_grant_still_reaches_admin_api(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    storage.set_tool_grant(11, "admin", tenant_id)

    resp = client.get("/api/admin/tenants", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 200


def test_normal_user_cannot_reach_admin_api(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_id)

    resp = client.get("/api/admin/tenants", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 403


def test_session_authorization_none_vs_empty_grants(_apply_admin_schema):
    tenant_id = storage.create_tenant("A")
    assert storage.session_authorization(11, tenant_id) is None
    assert access_gate.tools_for(tenant_id, 11) is None

    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    result = storage.session_authorization(11, tenant_id)
    assert result is not None
    tools, sva = result
    assert tools == []
    assert sva is None
    assert access_gate.tools_for(tenant_id, 11) == []


def test_cross_finding_grant_survives_physically_after_removal(monkeypatch, _apply_admin_schema):
    """Character A / Tenant A / Grant A / Session A → remove → old cookie
    and leftover grant cannot access Tenant A."""
    _enable_gate(monkeypatch)
    tenant_a = storage.create_tenant("A")
    storage.add_tenant_registry_entry(tenant_a, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_a)
    cookies = _cookie(11, tenant_a)

    storage.remove_tenant_registry_entry(11)
    assert storage.list_tool_grants_for_character(11) == ["trading"]
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 401


def test_gate_cannot_be_disabled_via_request_params(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    resp = client.get("/api/trading/settings", params={"access_gate_enabled": "false"})
    assert resp.status_code == 401


def test_access_gate_defaults_to_enabled():
    from eve_trader.config import AccessConfig
    assert AccessConfig().access_gate_enabled is True
