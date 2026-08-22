"""Tests for admin.py's do_* actions - see that module's own docstring for
why they're a deliberate cross-tenant superadmin surface (unscoped storage
reads/writes across every tenant, not RLS'd)."""
import requests
import pytest

from eve_trader import admin, storage
from eve_trader.actions import ActionError
from eve_trader.esi_client import ESIClient, ESIError
from eve_trader.production import sde

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


def test_do_add_user_resolves_id_via_esi_search_and_creates_dedicated_tenant(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_search", lambda self, name: 42 if name == "Some Pilot" else None)

    result = admin.do_add_user("Some Pilot")

    assert result["character_id"] == 42
    assert result["character_name"] == "Some Pilot"
    users = {u["character_id"]: u for u in admin.do_list_users()}
    assert users[42]["character_name"] == "Some Pilot"
    assert users[42]["tenant_id"] == result["tenant_id"]
    tenants = {t["tenant_id"]: t for t in admin.do_list_tenants()}
    assert tenants[result["tenant_id"]]["name"] == "Some Pilot"


def test_do_add_user_strips_whitespace_from_name():
    with pytest.raises(ActionError, match="empty"):
        admin.do_add_user("   ")


def test_do_add_user_rejects_a_name_esi_cant_resolve(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_search", lambda self, name: None)

    with pytest.raises(ActionError, match="No character found named"):
        admin.do_add_user("Nobody Real")


def test_do_add_user_never_reuses_an_existing_tenant(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_search", lambda self, name: {"Pilot A": 42, "Pilot B": 43}[name])

    first = admin.do_add_user("Pilot A")
    second = admin.do_add_user("Pilot B")

    assert first["tenant_id"] != second["tenant_id"]


def test_do_add_user_wraps_esi_failure_as_action_error(monkeypatch):
    def _raise(self, name):
        raise ESIError("ESI down")
    monkeypatch.setattr(ESIClient, "character_search", _raise)

    with pytest.raises(ActionError, match="ESI down"):
        admin.do_add_user("Some Pilot")


def test_tenant_registry_entries_tenant_id_is_unique_at_db_level(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_search", lambda self, name: 42)
    result = admin.do_add_user("Some Pilot")

    with pytest.raises(psycopg.errors.UniqueViolation):
        storage.add_tenant_registry_entry(result["tenant_id"], 43, character_name="Someone Else")


def test_do_remove_user_clears_registry_and_grants(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_search", lambda self, name: 42)
    result = admin.do_add_user("Some Pilot")
    tenant_id = result["tenant_id"]
    storage.set_tool_grant(42, "production", tenant_id)

    admin.do_remove_user(42)

    assert 42 not in {u["character_id"] for u in admin.do_list_users()}
    assert storage.list_tool_grants_for_character(42) == []


def test_do_remove_user_on_unknown_character_is_a_no_op():
    admin.do_remove_user(999999)  # doesn't raise


def test_do_set_tool_grants_replaces_not_merges(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_search", lambda self, name: 42)
    result = admin.do_add_user("Some Pilot")
    storage.set_tool_grant(42, "trading", result["tenant_id"])

    admin.do_set_tool_grants(42, ["production", "admin"])

    assert storage.list_tool_grants_for_character(42) == ["admin", "production"]


def test_do_set_tool_grants_rejects_unknown_tool_key(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_search", lambda self, name: 42)
    admin.do_add_user("Some Pilot")

    with pytest.raises(ActionError, match="Unknown tool_key"):
        admin.do_set_tool_grants(42, ["not-a-real-tool"])


def test_do_set_tool_grants_rejects_unregistered_character():
    with pytest.raises(ActionError, match="isn't a registered user"):
        admin.do_set_tool_grants(999999, ["trading"])


def test_do_refresh_sde_downloads_and_invalidates_caches(monkeypatch):
    # GitHub issue #34: moved here from production/actions.py - the SDE
    # cache is global/shared, not per-tenant, so this is a superadmin action.
    #
    # GitHub issue #54's own follow-up bug (found in code review of this PR):
    # invalidate_discover_cache/invalidate_ship_margin_cache became per-tenant
    # by default once their caches were keyed by tenant - a refresh here MUST
    # pass all_tenants=True (every tenant's discover/margin results can be
    # affected by a global SDE change, not just the calling admin's own),
    # or every other tenant keeps serving stale results for up to the full
    # cache TTL. Asserting only that the functions were *called* (the
    # original version of this test) let exactly that regression pass.
    invalidated = []
    monkeypatch.setattr(sde, "refresh_sde", lambda: {"sde_types": 100})
    monkeypatch.setattr(admin, "invalidate_discover_cache",
                         lambda all_tenants=False: invalidated.append(("discover", all_tenants)))
    monkeypatch.setattr(admin, "invalidate_ship_margin_cache",
                         lambda all_tenants=False: invalidated.append(("ship_margin", all_tenants)))

    result = admin.do_refresh_sde()

    assert result == {"sde_types": 100}
    assert invalidated == [("discover", True), ("ship_margin", True)]


def test_do_refresh_sde_wraps_network_error():
    def _raise():
        raise requests.RequestException("connection refused")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sde, "refresh_sde", _raise)
        with pytest.raises(ActionError, match="SDE refresh failed"):
            admin.do_refresh_sde()
