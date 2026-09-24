"""F-06: backup listing/creation is admin-only while the gate is on.

Moved from /api/portfolio/backups to /api/admin/backups (confirmed real
misplacement 2026-09-21, see admin.do_create_backup's own docstring) - a
plain "portfolio" grant no longer sees backups at all, listing included,
since the whole feature is now behind the /api/admin/ prefix like every
other cross-tenant-impacting action. Before this move, GET was a portfolio
read and only POST needed "admin" via a one-off exception in
api/app.py's _required_tool_for_path (F-06); that exception is gone.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, admin, storage
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


def _enable_gate(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")


def _cookie(character_id, tenant_id, name="Pilot"):
    token = access_gate.create_session_token(character_id, name, tenant_id)
    return {access_gate.SESSION_COOKIE_NAME: token}


def test_portfolio_user_cannot_list_or_create_backups(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("User")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="User")
    storage.set_tool_grant(11, "portfolio", tenant_id)
    cookies = _cookie(11, tenant_id)

    created = {"n": 0}

    def _create():
        created["n"] += 1
        return {"name": "x.zip", "created_at": "t", "size_bytes": 1}

    monkeypatch.setattr(admin, "do_create_backup", _create)
    monkeypatch.setattr(admin, "do_list_backups", lambda: {"rows": []})

    assert client.get("/api/admin/backups", cookies=cookies).status_code == 403
    for _ in range(20):
        resp = client.post("/api/admin/backups", cookies=cookies)
        assert resp.status_code == 403
    assert created["n"] == 0


def test_admin_can_list_and_create_backups(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    tenant_id = storage.create_tenant("Op")
    storage.add_tenant_registry_entry(tenant_id, 42, character_name="Op")
    storage.set_tool_grant(42, "admin", tenant_id)
    cookies = _cookie(42, tenant_id)

    monkeypatch.setattr(admin, "do_list_backups", lambda: {"rows": []})
    monkeypatch.setattr(
        admin, "do_create_backup",
        lambda: {"name": "x.zip", "created_at": "t", "size_bytes": 1},
    )

    assert client.get("/api/admin/backups", cookies=cookies).status_code == 200
    resp = client.post("/api/admin/backups", cookies=cookies)
    assert resp.status_code == 200
    assert resp.json()["name"] == "x.zip"


def test_unauthenticated_cannot_access_backups(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    created = {"n": 0}
    monkeypatch.setattr(admin, "do_create_backup", lambda: created.__setitem__("n", 1))
    monkeypatch.setattr(admin, "do_list_backups", lambda: {"rows": []})

    assert client.get("/api/admin/backups").status_code == 401
    assert client.post("/api/admin/backups").status_code == 401
    assert created["n"] == 0
