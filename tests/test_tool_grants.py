"""Tests for storage.py's tool_grants functions (set_tool_grant,
revoke_tool_grant, revoke_all_tool_grants, list_tool_grants_for_character,
list_users_with_grants) - see docs/admin_schema.sql. Unscoped like
tenant_registry_entries (storage.connect_unscoped(), no ambient tenant) -
the Admin tool is a deliberate cross-tenant superadmin surface (see
admin.py's own module docstring), so these have to read/write across tenant
boundaries.
"""
import uuid

import pytest

from eve_trader import storage

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


def test_set_tool_grant_then_list_returns_it():
    tenant_id = _new_tenant()
    storage.set_tool_grant(42, "production", tenant_id)

    assert storage.list_tool_grants_for_character(42) == ["production"]


def test_set_tool_grant_is_upsert_not_duplicate():
    tenant_id = _new_tenant()
    storage.set_tool_grant(42, "production", tenant_id)
    storage.set_tool_grant(42, "production", tenant_id)

    assert storage.list_tool_grants_for_character(42) == ["production"]


def test_list_tool_grants_for_character_returns_multiple_sorted():
    tenant_id = _new_tenant()
    storage.set_tool_grant(42, "production", tenant_id)
    storage.set_tool_grant(42, "admin", tenant_id)

    assert storage.list_tool_grants_for_character(42) == ["admin", "production"]


def test_list_tool_grants_for_character_empty_when_none_granted():
    assert storage.list_tool_grants_for_character(999) == []


def test_revoke_tool_grant_removes_only_that_grant():
    tenant_id = _new_tenant()
    storage.set_tool_grant(42, "production", tenant_id)
    storage.set_tool_grant(42, "trading", tenant_id)

    storage.revoke_tool_grant(42, "production")

    assert storage.list_tool_grants_for_character(42) == ["trading"]


def test_revoke_tool_grant_missing_grant_is_a_no_op():
    storage.revoke_tool_grant(999, "production")  # doesn't raise


def test_revoke_all_tool_grants_clears_every_grant_for_character():
    tenant_id = _new_tenant()
    storage.set_tool_grant(42, "production", tenant_id)
    storage.set_tool_grant(42, "trading", tenant_id)
    storage.set_tool_grant(43, "trading", tenant_id)  # a different character - must survive

    storage.revoke_all_tool_grants(42)

    assert storage.list_tool_grants_for_character(42) == []
    assert storage.list_tool_grants_for_character(43) == ["trading"]


def test_list_users_with_grants_includes_registered_characters_with_and_without_grants():
    tenant_id = _new_tenant()
    storage.add_tenant_registry_entry(tenant_id, 1, character_name="Alice")
    storage.add_tenant_registry_entry(tenant_id, 2, character_name="Bob")
    storage.set_tool_grant(1, "production", tenant_id)

    users = storage.list_users_with_grants()

    by_id = {u["character_id"]: u for u in users}
    assert by_id[1]["character_name"] == "Alice"
    assert by_id[1]["tool_keys"] == ["production"]
    assert by_id[2]["character_name"] == "Bob"
    assert by_id[2]["tool_keys"] == []


def test_list_users_with_grants_reports_correct_tenant():
    tenant_a = _new_tenant()
    tenant_b = _new_tenant()
    storage.add_tenant_registry_entry(tenant_a, 1, character_name="Alice")
    storage.add_tenant_registry_entry(tenant_b, 2, character_name="Bob")

    users = storage.list_users_with_grants()

    by_id = {u["character_id"]: u for u in users}
    assert by_id[1]["tenant_id"] == tenant_a
    assert by_id[2]["tenant_id"] == tenant_b
