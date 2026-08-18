"""Tests for admin.py's do_* actions - see that module's own docstring for
why they're a deliberate cross-tenant superadmin surface (unscoped storage
reads/writes across every tenant, not RLS'd)."""
import uuid

import pytest

from eve_trader import admin, storage
from eve_trader.actions import ActionError
from eve_trader.esi_client import ESIClient, ESIError

from . import pg_helpers
from .pg_helpers import _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("tenant_registry_entries", "tool_grants")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    yield


def _new_tenant() -> str:
    return storage.create_tenant(f"Test Tenant {uuid.uuid4()}")


def test_do_create_tenant_then_appears_in_do_list_tenants():
    created = admin.do_create_tenant("Some Corp")

    tenant_ids = {t["tenant_id"] for t in admin.do_list_tenants()}
    assert created["tenant_id"] in tenant_ids


def test_do_create_tenant_rejects_empty_name():
    with pytest.raises(ActionError, match="empty"):
        admin.do_create_tenant("   ")


def test_do_add_user_resolves_name_via_esi_and_registers(monkeypatch):
    tenant_id = _new_tenant()
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, cid: {"name": "Some Pilot"})

    result = admin.do_add_user(42, tenant_id)

    assert result == {"character_id": 42, "character_name": "Some Pilot", "tenant_id": tenant_id}
    users = {u["character_id"]: u for u in admin.do_list_users()}
    assert users[42]["character_name"] == "Some Pilot"
    assert users[42]["tenant_id"] == tenant_id


def test_do_add_user_rejects_unknown_tenant(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, cid: {"name": "Some Pilot"})

    with pytest.raises(ActionError, match="Unknown tenant_id"):
        admin.do_add_user(42, str(uuid.uuid4()))


def test_do_add_user_wraps_esi_failure_as_action_error(monkeypatch):
    tenant_id = _new_tenant()

    def _raise(self, cid):
        raise ESIError("ESI down")
    monkeypatch.setattr(ESIClient, "character_public_info", _raise)

    with pytest.raises(ActionError, match="ESI down"):
        admin.do_add_user(42, tenant_id)


def test_do_remove_user_clears_registry_and_grants(monkeypatch):
    tenant_id = _new_tenant()
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, cid: {"name": "Some Pilot"})
    admin.do_add_user(42, tenant_id)
    storage.set_tool_grant(42, "production", tenant_id)

    admin.do_remove_user(42)

    assert 42 not in {u["character_id"] for u in admin.do_list_users()}
    assert storage.list_tool_grants_for_character(42) == []


def test_do_remove_user_on_unknown_character_is_a_no_op():
    admin.do_remove_user(999999)  # doesn't raise


def test_do_set_tool_grants_replaces_not_merges(monkeypatch):
    tenant_id = _new_tenant()
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, cid: {"name": "Some Pilot"})
    admin.do_add_user(42, tenant_id)
    storage.set_tool_grant(42, "trading", tenant_id)

    admin.do_set_tool_grants(42, ["production", "admin"])

    assert storage.list_tool_grants_for_character(42) == ["admin", "production"]


def test_do_set_tool_grants_rejects_unknown_tool_key(monkeypatch):
    tenant_id = _new_tenant()
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, cid: {"name": "Some Pilot"})
    admin.do_add_user(42, tenant_id)

    with pytest.raises(ActionError, match="Unknown tool_key"):
        admin.do_set_tool_grants(42, ["not-a-real-tool"])


def test_do_set_tool_grants_rejects_unregistered_character():
    with pytest.raises(ActionError, match="isn't a registered user"):
        admin.do_set_tool_grants(999999, ["trading"])
