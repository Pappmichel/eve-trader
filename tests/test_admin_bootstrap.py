"""F-07 / F-NEW-02: CLI admin bootstrap, no HTTP backdoor."""
from __future__ import annotations

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from eve_trader import access_gate, admin, storage
from eve_trader.actions import ActionError
from eve_trader.api.app import create_app
from eve_trader.cli import main
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
    pg_helpers.wipe_tables("tenant_registry_entries", "tool_grants", "character_session_revocations")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    yield


def _enable_gate(monkeypatch):
    monkeypatch.setattr(ACCESS_CONFIG, "access_gate_enabled", True)
    monkeypatch.setattr(OAUTH_CONFIG, "session_secret_key", "test-secret-key")


def test_bootstrap_refuses_without_confirm(_apply_admin_schema):
    with pytest.raises(ActionError, match="--confirm"):
        admin.do_bootstrap_admin(42)


def test_bootstrap_grants_admin_on_empty_default_tenant(_apply_admin_schema):
    result = admin.do_bootstrap_admin(42, "Operator", confirm=True)
    assert result["tenant_id"] == storage.DEFAULT_TENANT_ID
    assert "admin" in result["tool_keys"]
    assert result["already_had_admin"] is False
    assert result["created_tenant"] is False


def test_bootstrap_is_idempotent(_apply_admin_schema):
    first = admin.do_bootstrap_admin(42, "Operator", confirm=True)
    second = admin.do_bootstrap_admin(42, "Operator", confirm=True)
    assert first["tenant_id"] == second["tenant_id"]
    assert second["already_had_admin"] is True
    assert second["created_tenant"] is False
    users = [u for u in storage.list_users_with_grants() if u["character_id"] == 42]
    assert len(users) == 1


def test_bootstrap_does_not_reassign_an_existing_character(_apply_admin_schema):
    tenant = storage.create_tenant("Existing")
    storage.add_tenant_registry_entry(tenant, 42, character_name="Existing")
    result = admin.do_bootstrap_admin(42, confirm=True)
    assert result["tenant_id"] == tenant
    assert result["already_registered"] is True
    assert storage.resolve_tenant_id(42) == tenant


def test_bootstrap_creates_a_new_tenant_when_default_is_occupied(_apply_admin_schema):
    storage.add_tenant_registry_entry(storage.DEFAULT_TENANT_ID, 1, character_name="Occupant")
    result = admin.do_bootstrap_admin(99, "Second", confirm=True)
    assert result["tenant_id"] != storage.DEFAULT_TENANT_ID
    assert result["created_tenant"] is True
    assert storage.resolve_tenant_id(1) == storage.DEFAULT_TENANT_ID


def test_cli_bootstrap_requires_confirm(_apply_admin_schema):
    runner = CliRunner()
    token = storage.set_current_tenant(storage.get_current_tenant())
    try:
        result = runner.invoke(main, ["admin", "bootstrap", "--character-id", "42"])
    finally:
        storage.reset_current_tenant(token)
    assert result.exit_code != 0
    assert "--confirm" in result.output


def test_cli_bootstrap_with_confirm(_apply_admin_schema):
    runner = CliRunner()
    token = storage.set_current_tenant(storage.get_current_tenant())
    try:
        result = runner.invoke(
            main, ["admin", "bootstrap", "--character-id", "42", "--character-name", "Op", "--confirm"],
        )
    finally:
        storage.reset_current_tenant(token)
    assert result.exit_code == 0
    assert "Admin bootstrap ok" in result.output
    assert "admin" in storage.list_tool_grants_for_character(42)


def test_fresh_install_no_admin_admin_api_is_401_or_403(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    # Not logged in.
    assert client.get("/api/admin/tenants").status_code == 401
    # Logged in, no grant.
    tenant = storage.create_tenant("User")
    storage.add_tenant_registry_entry(tenant, 7, character_name="User")
    token = access_gate.create_session_token(7, "User", tenant)
    cookies = {access_gate.SESSION_COOKIE_NAME: token}
    assert client.get("/api/admin/tenants", cookies=cookies).status_code == 403


def test_bootstrap_then_login_reaches_admin(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    admin.do_bootstrap_admin(42, "Op", confirm=True, all_tools=True)
    tenant_id = storage.resolve_tenant_id(42)
    token = access_gate.create_session_token(42, "Op", tenant_id)
    cookies = {access_gate.SESSION_COOKIE_NAME: token}
    assert client.get("/api/admin/tenants", cookies=cookies).status_code == 200
    # Restart does not drop grants (they are in Postgres).
    assert "admin" in storage.list_tool_grants_for_character(42)


def test_no_unauthenticated_http_admin_grant(monkeypatch, _apply_admin_schema):
    _enable_gate(monkeypatch)
    # There is no /api/admin/bootstrap (or similar) route.
    assert client.post("/api/admin/bootstrap").status_code in (401, 404, 405)
    assert client.post("/api/admin/users", json={"character_name": "X"}).status_code == 401


def test_repeat_bootstrap_cannot_hijack_another_characters_tenant(_apply_admin_schema):
    first = admin.do_bootstrap_admin(42, "Op", confirm=True)
    second = admin.do_bootstrap_admin(99, "Other", confirm=True)
    assert first["tenant_id"] != second["tenant_id"]
    assert storage.resolve_tenant_id(42) == first["tenant_id"]
    assert storage.resolve_tenant_id(99) == second["tenant_id"]
