"""Tests for storage.py's tenant-registry functions (create_tenant,
add_tenant_registry_entry, resolve_tenant_id, list_tenants,
list_tenant_registry_entries - see docs/phase3_schema.sql) - the
Postgres-backed replacement for the old in-memory AccessConfig allowlist
(allowed_character_ids/allowed_corporation_ids/allowed_alliance_ids) that
access_gate.py's is_allowed() used to check. All of these go through
storage.connect_unscoped() (no ambient tenant needed/used - see its own
docstring), matching how they're actually called (from the OAuth callback,
before any tenant is known, and from the CLI's `tenant` command group).

Character-only since docs/admin_schema.sql retired corp/alliance registry
entries (see the Login/Admin architecture review) - these tests used to
also cover corporation/alliance matching, which no longer exists.
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
    # tenant_registry_entries is keyed by (entry_type, entry_id) - these
    # tests use small hardcoded ids, so wipe (owner-role, no RLS on this
    # table anyway) before each test rather than risk a collision with a
    # leftover row from an earlier run. Never touches the seeded
    # DEFAULT_TENANT_ID row in `tenants` (a different table, only deleting
    # rows this file itself creates via create_tenant).
    pg_helpers.wipe_tables("tenant_registry_entries", "tool_grants")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    yield


def _new_tenant() -> str:
    return storage.create_tenant(f"Test Tenant {uuid.uuid4()}")


def test_create_tenant_returns_a_usable_tenant_id():
    tenant_id = _new_tenant()

    tenant_ids = [str(t[0]) for t in storage.list_tenants()]
    assert tenant_id in tenant_ids


def test_resolve_tenant_id_matches_on_character():
    tenant_id = _new_tenant()
    storage.add_tenant_registry_entry(tenant_id, 42)

    assert storage.resolve_tenant_id(42) == tenant_id
    assert storage.resolve_tenant_id(43) is None


def test_resolve_tenant_id_no_registry_entries_resolves_to_none():
    assert storage.resolve_tenant_id(1) is None


def test_add_tenant_registry_entry_upsert_reassigns_to_a_new_tenant():
    # Re-registering an id to a different tenant is a legitimate
    # re-provisioning operation (e.g. a character moved accounts), not an
    # error - see add_tenant_registry_entry's own docstring.
    tenant_a = _new_tenant()
    tenant_b = _new_tenant()
    storage.add_tenant_registry_entry(tenant_a, 55)
    assert storage.resolve_tenant_id(55) == tenant_a

    storage.add_tenant_registry_entry(tenant_b, 55)
    assert storage.resolve_tenant_id(55) == tenant_b


def test_add_tenant_registry_entry_stores_character_name():
    tenant_id = _new_tenant()
    storage.add_tenant_registry_entry(tenant_id, 55, character_name="Some Pilot")

    users = storage.list_users_with_grants()
    assert next(u for u in users if u["character_id"] == 55)["character_name"] == "Some Pilot"


def test_add_tenant_registry_entry_none_name_leaves_existing_name_untouched():
    tenant_id = _new_tenant()
    storage.add_tenant_registry_entry(tenant_id, 55, character_name="Some Pilot")
    storage.add_tenant_registry_entry(tenant_id, 55, character_name=None)

    users = storage.list_users_with_grants()
    assert next(u for u in users if u["character_id"] == 55)["character_name"] == "Some Pilot"


def test_remove_tenant_registry_entry_deregisters_character():
    tenant_id = _new_tenant()
    storage.add_tenant_registry_entry(tenant_id, 55)
    assert storage.resolve_tenant_id(55) == tenant_id

    storage.remove_tenant_registry_entry(55)
    assert storage.resolve_tenant_id(55) is None


def test_list_tenant_registry_entries_returns_everything_registered_to_a_tenant():
    tenant_id = _new_tenant()
    storage.add_tenant_registry_entry(tenant_id, 1)

    entries = storage.list_tenant_registry_entries(tenant_id)

    assert set(entries) == {("character", 1)}
