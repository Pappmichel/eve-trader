"""Admin allowlist and access-request actions."""
from __future__ import annotations

import uuid

import pytest

from eve_trader import admin, storage
from eve_trader.actions import ActionError
from eve_trader.esi_client import ESIClient

from . import pg_helpers
from .pg_helpers import _apply_admin_schema, _apply_phase1_schema, _apply_phase3_schema  # noqa: F401

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


def _user(character_id, name, tools, corp=None, alliance=None):
    tenant_id = storage.create_tenant(f"{name}-{uuid.uuid4()}")
    storage.add_tenant_registry_entry(tenant_id, character_id, character_name=name)
    for tool in tools:
        storage.set_tool_grant(character_id, tool, tenant_id)
    if corp is not None:
        storage.update_registry_affiliation(character_id, corp, alliance, False)
    return tenant_id


def test_approve_creates_a_tenant_with_exactly_the_chosen_grants(monkeypatch):
    storage.upsert_pending_access_request(77, "Newbie", 5, "Corp", None, None)
    result = admin.do_approve_access_request(77, ["production", "sorting"], decided_by_character_id=1)
    users = {u["character_id"]: u for u in admin.do_list_users()}
    assert users[77]["tenant_name"] == "Newbie"
    assert users[77]["tenant_id"] == result["tenant_id"]
    assert users[77]["tool_keys"] == ["production", "sorting"]
    assert "characters" not in users[77]["tool_keys"]


def test_approve_rejects_an_unknown_tool_key():
    storage.upsert_pending_access_request(77, "Newbie", 5, "Corp", None, None)
    with pytest.raises(ActionError):
        admin.do_approve_access_request(77, ["not-a-tool"])
    assert storage.resolve_tenant_id(77) is None
    assert storage.get_access_request(77)["status"] == "pending"


def test_reject_then_delete_lets_a_new_request_through():
    storage.upsert_pending_access_request(77, "Newbie", 5, "Corp", None, None)
    admin.do_reject_access_request(77, decided_by_character_id=1)
    storage.upsert_pending_access_request(77, "Newbie", 5, "Corp", None, None)
    assert storage.get_access_request(77)["status"] == "rejected"
    admin.do_delete_access_request(77)
    assert storage.get_access_request(77) is None
    storage.upsert_pending_access_request(77, "Newbie", 5, "Corp", None, None)
    assert storage.get_access_request(77)["status"] == "pending"


def test_impact_preview_lists_users_the_first_entry_would_suspend():
    _user(1, "In", ["trading"], corp=10)
    _user(2, "Out", ["trading"], corp=99)
    _user(3, "Admin", ["admin"], corp=99)
    impact = admin.do_allowlist_impact("corporation", 10, "add", actor_character_id=3)
    assert impact["activates_recheck"] is True
    assert impact["actor_exempt_but_affected"] is True
    ids = {row["character_id"] for row in impact["would_suspend"]}
    assert ids == {2}


def test_impact_preview_for_removing_an_entry():
    storage.add_allowlist_entry("corporation", 10, "Corp")
    storage.add_allowlist_entry("alliance", 20, "Ally")
    _user(1, "Only corp", ["trading"], corp=10, alliance=None)
    _user(2, "Still allied", ["trading"], corp=10, alliance=20)
    impact = admin.do_allowlist_impact("corporation", 10, "remove", actor_character_id=None)
    ids = {row["character_id"] for row in impact["would_suspend"]}
    assert ids == {1}
    assert impact["disables_recheck"] is False


def test_removing_the_last_entry_suspends_nobody():
    storage.add_allowlist_entry("corporation", 10, "Corp")
    _user(1, "Pilot", ["trading"], corp=99)
    impact = admin.do_allowlist_impact("corporation", 10, "remove")
    assert impact["disables_recheck"] is True
    assert impact["would_suspend"] == []


def test_list_requests_flags_rows_no_longer_allowlisted():
    storage.add_allowlist_entry("corporation", 10, "Corp")
    storage.upsert_pending_access_request(7, "Pilot", 10, "Corp", None, None)
    storage.remove_allowlist_entry("corporation", 10)
    rows = admin.do_list_access_requests("pending")
    assert rows[0]["no_longer_allowlisted"] is True
    assert rows[0]["status"] == "pending"


def test_add_allowlist_entry_caches_the_resolved_name(monkeypatch):
    monkeypatch.setattr(ESIClient, "resolve_names", lambda self, ids: {10: "Pandemic Horde"})
    row = admin.do_add_allowlist_entry("alliance", 10, added_by_character_id=4)
    assert row["name"] == "Pandemic Horde"
    stored = storage.list_allowlist()
    assert stored[0]["entry_type"] == "alliance"
    assert stored[0]["added_by_character_id"] == 4


def test_refresh_affiliations_updates_the_registry(monkeypatch):
    _user(1, "Pilot", ["trading"])
    monkeypatch.setattr(
        ESIClient, "character_affiliation",
        lambda self, ids, timeout=30, retries=3: {1: (10, 20)},
    )
    storage.add_allowlist_entry("corporation", 99, "Other")
    result = admin.do_refresh_user_affiliations()
    assert result["updated"] == 1
    stored = storage.get_registry_affiliation(1)
    assert stored["corporation_id"] == 10
    assert stored["alliance_id"] == 20
    assert stored["access_suspended"] is True
