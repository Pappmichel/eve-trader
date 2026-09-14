"""P5-06: HTTP-level gate enforcement actually runs with the gate on."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.config import ACCESS_CONFIG

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
    pg_helpers.wipe_tables("tenant_registry_entries", "tool_grants", "character_session_revocations")
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
