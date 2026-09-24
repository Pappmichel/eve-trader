"""P5-02: HTTP-level role_key namespace is bound to the caller's tool."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.auth import TOOL_ROLE_PREFIXES, TokenManager

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


def _trading_session():
    tenant_id = storage.create_tenant("T")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="T")
    storage.set_tool_grant(11, "trading", tenant_id)
    return tenant_id, _cookie(11, tenant_id)


def test_trading_endpoint_accepts_seller_role(monkeypatch, _apply_admin_schema):
    called = []
    monkeypatch.setattr(TokenManager, "remove_token", lambda self, role: called.append(role))
    tenant_id, cookies = _trading_session()
    resp = client.delete("/api/trading/auth/character/seller:1", cookies=cookies)
    assert resp.status_code == 200
    assert resp.json() == {"removed": "seller:1"}
    assert called == ["seller:1"]
    del tenant_id


@pytest.mark.parametrize("role_key", [
    "producer:777",
    "doctrine:1",
    "doctrine-assets:1",
    "trader:1",
])
def test_trading_endpoint_rejects_other_tools(monkeypatch, role_key, _apply_admin_schema):
    called = []
    monkeypatch.setattr(TokenManager, "remove_token", lambda self, role: called.append(role))
    _tenant_id, cookies = _trading_session()
    resp = client.delete(f"/api/trading/auth/character/{role_key}", cookies=cookies)
    assert resp.status_code == 400
    assert called == []


@pytest.mark.parametrize("role_key", ["ore:1", "refining:1", "gate:1", "not-a-role", "buyer:0"])
def test_trading_endpoint_rejects_malformed_and_unknown(monkeypatch, role_key, _apply_admin_schema):
    called = []
    monkeypatch.setattr(TokenManager, "remove_token", lambda self, role: called.append(role))
    _tenant_id, cookies = _trading_session()
    resp = client.delete(f"/api/trading/auth/character/{role_key}", cookies=cookies)
    assert resp.status_code == 400
    assert called == []


def test_production_endpoint_rejects_trading_role(monkeypatch, _apply_admin_schema):
    called = []
    monkeypatch.setattr(TokenManager, "remove_token", lambda self, role: called.append(role))
    tenant_id = storage.create_tenant("P")
    storage.add_tenant_registry_entry(tenant_id, 22, character_name="P")
    storage.set_tool_grant(22, "production", tenant_id)
    cookies = _cookie(22, tenant_id)
    resp = client.delete("/api/production/auth/character/buyer:1", cookies=cookies)
    assert resp.status_code == 400
    assert called == []


def test_doctrine_endpoint_rejects_producer_role(monkeypatch, _apply_admin_schema):
    called = []
    monkeypatch.setattr(TokenManager, "remove_token", lambda self, role: called.append(role))
    tenant_id = storage.create_tenant("D")
    storage.add_tenant_registry_entry(tenant_id, 33, character_name="D")
    storage.set_tool_grant(33, "doctrine", tenant_id)
    cookies = _cookie(33, tenant_id)
    resp = client.delete("/api/doctrine/characters/producer:1", cookies=cookies)
    assert resp.status_code == 400
    assert called == []


def test_doctrine_asset_endpoint_rejects_producer_role(monkeypatch, _apply_admin_schema):
    called = []
    monkeypatch.setattr(TokenManager, "remove_token", lambda self, role: called.append(role))
    tenant_id = storage.create_tenant("D")
    storage.add_tenant_registry_entry(tenant_id, 33, character_name="D")
    storage.set_tool_grant(33, "doctrine", tenant_id)
    cookies = _cookie(33, tenant_id)
    resp = client.delete("/api/doctrine/asset-characters/producer:1", cookies=cookies)
    assert resp.status_code == 400
    assert called == []


def test_station_trading_endpoint_rejects_buyer_role(monkeypatch, _apply_admin_schema):
    called = []
    monkeypatch.setattr(TokenManager, "remove_token", lambda self, role: called.append(role))
    tenant_id = storage.create_tenant("S")
    storage.add_tenant_registry_entry(tenant_id, 44, character_name="S")
    storage.set_tool_grant(44, "station_trading", tenant_id)
    cookies = _cookie(44, tenant_id)
    resp = client.delete("/api/station-trading/auth/character/buyer:1", cookies=cookies)
    assert resp.status_code == 400
    assert called == []


def test_trading_wallet_rejects_other_tool_role_without_lookup(monkeypatch, _apply_admin_schema):
    looked = []
    monkeypatch.setattr(TokenManager, "get_record", lambda self, role: looked.append(role) or None)
    _tenant_id, cookies = _trading_session()
    txns = client.get(
        "/api/trading/wallet-transactions",
        params={"role_key": "producer:1"},
        cookies=cookies,
    )
    balance = client.get(
        "/api/trading/wallet-balance",
        params={"role_key": "trader:1"},
        cookies=cookies,
    )
    assert txns.status_code == 400
    assert balance.status_code == 400
    assert looked == []


def test_refining_has_no_esi_role_key_namespace_or_http_parameter():
    from eve_trader.api.routers import refining, sorting

    assert "refining" not in TOOL_ROLE_PREFIXES
    assert "sorting" not in TOOL_ROLE_PREFIXES
    for router in (refining.router, sorting.router):
        for route in router.routes:
            path = getattr(route, "path", "") or ""
            assert "{role_key}" not in path
