"""Storage for the corp/alliance allowlist and access requests."""
from __future__ import annotations

import uuid

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase3_schema,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(
        "tool_grants", "tenant_registry_entries", "character_session_revocations",
        "access_requests", "access_allowlist",
    )
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    yield


def test_allowlist_crud_and_contains():
    assert storage.allowlist_is_empty()
    assert storage.allowlist_contains(1, 2) is False
    storage.add_allowlist_entry("corporation", 10, "Corp", added_by_character_id=1)
    storage.add_allowlist_entry("alliance", 20, "Ally")
    assert storage.allowlist_is_empty() is False
    assert storage.allowlist_contains(10, None) is True
    assert storage.allowlist_contains(None, 20) is True
    assert storage.allowlist_contains(99, 98) is False
    names = {row["entry_id"]: row["name"] for row in storage.list_allowlist()}
    assert names == {10: "Corp", 20: "Ally"}
    storage.add_allowlist_entry("corporation", 10, "Corp Renamed")
    assert storage.list_allowlist()[0]["name"] == "Corp Renamed" or any(
        row["name"] == "Corp Renamed" for row in storage.list_allowlist()
    )
    assert storage.remove_allowlist_entry("corporation", 10) is True
    assert storage.allowlist_contains(10, None) is False
    assert storage.remove_allowlist_entry("corporation", 10) is False


def test_upsert_keeps_rejected_status():
    storage.upsert_pending_access_request(7, "Old", 1, "Corp", None, None)
    assert storage.reject_access_request(7, decided_by_character_id=3) is True
    storage.upsert_pending_access_request(7, "New", 2, "Other", 9, "Ally")
    row = storage.get_access_request(7)
    assert row["status"] == "rejected"
    assert row["character_name"] == "New"
    assert row["corporation_id"] == 2
    assert row["alliance_id"] == 9
    assert row["decided_by_character_id"] == 3


def test_approve_is_atomic_when_grant_insert_fails():
    storage.upsert_pending_access_request(77, "Pilot", 5, "Corp", None, None)
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(
            "ALTER TABLE tool_grants DROP CONSTRAINT IF EXISTS tool_grants_test_no_boom"
        )
        conn.execute(
            "ALTER TABLE tool_grants ADD CONSTRAINT tool_grants_test_no_boom "
            "CHECK (tool_key <> 'boom')"
        )
    try:
        with pytest.raises(psycopg.Error):
            storage.approve_access_request(77, ["trading", "boom"], decided_by_character_id=1)
    finally:
        with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
            conn.execute(
                "ALTER TABLE tool_grants DROP CONSTRAINT IF EXISTS tool_grants_test_no_boom"
            )
    assert storage.resolve_tenant_id(77) is None
    assert storage.get_access_request(77)["status"] == "pending"
    assert storage.list_tool_grants_for_character(77) == []


def test_approve_refuses_an_already_registered_character():
    tenant_id = storage.create_tenant("Existing")
    storage.add_tenant_registry_entry(tenant_id, 77, character_name="Pilot")
    storage.upsert_pending_access_request(77, "Pilot", 5, "Corp", None, None)
    with pytest.raises(storage.RegistryConflict):
        storage.approve_access_request(77, ["trading"], decided_by_character_id=1)
    assert storage.resolve_tenant_id(77) == tenant_id
    assert storage.get_access_request(77)["status"] == "pending"


def test_approve_creates_tenant_registry_and_marks_approved():
    storage.upsert_pending_access_request(77, "Pilot", 5, "Corp", 8, "Ally")
    tenant_id = storage.approve_access_request(77, ["trading", "trading"], decided_by_character_id=4)
    assert storage.resolve_tenant_id(77) == tenant_id
    assert storage.list_tool_grants_for_character(77) == ["trading"]
    assert storage.get_access_request(77)["status"] == "approved"
    assert storage.get_access_request(77)["decided_by_character_id"] == 4
    stored = storage.get_registry_affiliation(77)
    assert stored["corporation_id"] == 5
    assert stored["alliance_id"] == 8
    assert stored["access_suspended"] is False
    assert stored["affiliation_checked_at"] is not None
    users = {u["character_id"]: u for u in storage.list_users_with_grants()}
    assert users[77]["tenant_name"] == "Pilot"


def test_session_authorization_returns_affiliation_in_one_read():
    tenant_id = storage.create_tenant(f"A {uuid.uuid4()}")
    storage.add_tenant_registry_entry(tenant_id, 11, character_name="A")
    storage.set_tool_grant(11, "trading", tenant_id)
    storage.update_registry_affiliation(11, 10, 20, False)
    storage.add_allowlist_entry("corporation", 10, "Corp")
    authz = storage.session_authorization(11, tenant_id)
    assert authz is not None
    assert authz.tool_keys == ["trading"]
    assert authz.corporation_id == 10
    assert authz.alliance_id == 20
    assert authz.affiliation_checked_at is not None
    assert authz.access_suspended is False
    assert authz.allowlist_active is True


def test_removing_allowlist_entry_flags_open_requests():
    storage.add_allowlist_entry("corporation", 10, "Corp")
    storage.upsert_pending_access_request(7, "Pilot", 10, "Corp", None, None)
    assert storage.list_access_requests("pending")[0]["no_longer_allowlisted"] is False
    storage.remove_allowlist_entry("corporation", 10)
    row = storage.list_access_requests("pending")[0]
    assert row["status"] == "pending"
    assert row["no_longer_allowlisted"] is True
    assert storage.count_pending_access_requests() == 1
