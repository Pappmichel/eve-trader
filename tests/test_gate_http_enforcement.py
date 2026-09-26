"""P5-06: HTTP-level gate enforcement actually runs with the gate on."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.config import ACCESS_CONFIG, OAUTH_CONFIG

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,
    _apply_session_revocations_schema,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = [pg_helpers.postgres_required(), pytest.mark.gate_enforced]

client = TestClient(create_app())


@pytest.fixture(autouse=True)
def _wipe():
    client.cookies.clear()
    pg_helpers.wipe_tables(
        "tenant_registry_entries", "tool_grants", "character_session_revocations",
        "access_requests", "access_allowlist",
    )
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    yield


def _cookie(character_id, tenant_id, name="Pilot"):
    token = access_gate.create_session_token(character_id, name, tenant_id)
    return {access_gate.SESSION_COOKIE_NAME: token}


def test_gate_enforced_marker_enables_the_gate_without_a_local_override():
    """Would fail if conftest still globally forced the gate off."""
    assert ACCESS_CONFIG.access_gate_enabled is True


def test_unauthenticated_gated_get_is_401():
    assert client.get("/api/trading/settings").status_code == 401


def test_unauthenticated_gated_post_is_401():
    assert client.post("/api/trading/settings", json={}).status_code == 401


def test_unauthenticated_gated_put_is_401():
    assert client.put("/api/admin/users/1/tools", json={"tool_keys": ["trading"]}).status_code == 401


def test_authorized_admin_reaches_admin_api(_apply_admin_schema):
    tenant_id = storage.create_tenant("Admin")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="Admin")
    storage.set_tool_grant(11, "admin", tenant_id)
    resp = client.get("/api/admin/tenants", cookies=_cookie(11, tenant_id))
    assert resp.status_code == 200


def test_authorized_trading_user_reaches_trading_not_production(_apply_admin_schema):
    tenant_id = storage.create_tenant("T")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="T")
    storage.set_tool_grant(11, "trading", tenant_id)
    cookies = _cookie(11, tenant_id)
    assert client.get("/api/trading/settings", cookies=cookies).status_code == 200
    assert client.get("/api/production/settings", cookies=cookies).status_code == 403
    assert client.get("/api/admin/tenants", cookies=cookies).status_code == 403


@pytest.mark.gate_off
def test_gate_off_allows_unauthenticated_settings():
    assert ACCESS_CONFIG.access_gate_enabled is False
    assert client.get("/api/trading/settings").status_code == 200


# ------------------------------------------------------------- CSRF Origin check
# 2026-09-26 pentest follow-up: a state-changing /api/ request carrying an
# Origin header that matches neither this server's own origin nor the
# configured frontend_origin is rejected outright, before auth/tenant
# resolution - see app.py's own _csrf_check docstring for why this exists
# (CORS alone never protected a simple-content-type form POST, which skips
# preflight entirely).

def test_mutating_request_with_disallowed_origin_is_rejected_before_auth():
    resp = client.post("/api/trading/settings", json={}, headers={"Origin": "https://evil-attacker.example"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden - disallowed origin"


def test_mutating_request_with_disallowed_origin_rejected_even_with_valid_auth(_apply_admin_schema):
    # Ordering check: a hostile Origin must be rejected *before* a valid
    # session cookie would otherwise let the request through.
    tenant_id = storage.create_tenant("T")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="T")
    storage.set_tool_grant(11, "trading", tenant_id)
    resp = client.post("/api/trading/settings", json={}, headers={"Origin": "https://evil-attacker.example"},
                        cookies=_cookie(11, tenant_id))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden - disallowed origin"


def test_mutating_request_with_no_origin_header_reaches_normal_auth_check():
    # A non-browser API client (no Origin header at all) was never a CSRF
    # vector - only rejected here for the usual reason (no session cookie).
    assert client.post("/api/trading/settings", json={}).status_code == 401


def test_mutating_request_with_matching_frontend_origin_reaches_normal_auth_check():
    resp = client.post("/api/trading/settings", json={}, headers={"Origin": OAUTH_CONFIG.frontend_origin})
    assert resp.status_code == 401


def test_mutating_request_with_matching_self_origin_reaches_normal_auth_check():
    self_origin = str(client.base_url).rstrip("/")
    resp = client.post("/api/trading/settings", json={}, headers={"Origin": self_origin})
    assert resp.status_code == 401


def test_safe_get_method_is_never_origin_checked():
    # GET can't carry a CSRF side effect - a hostile Origin here is simply
    # irrelevant, request is only ever blocked by the normal auth check.
    resp = client.get("/api/trading/settings", headers={"Origin": "https://evil-attacker.example"})
    assert resp.status_code == 401
