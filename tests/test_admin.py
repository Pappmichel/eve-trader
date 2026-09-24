"""Tests for admin.py's do_* actions - see that module's own docstring for
why they're a deliberate cross-tenant superadmin surface (unscoped storage
reads/writes across every tenant, not RLS'd)."""
import requests
import pytest

from eve_trader import admin, storage
from eve_trader.actions import ActionError
from eve_trader.esi_client import ESIClient, ESIError
from eve_trader.production import jita_price_cache, sde, sde_diff

from . import pg_helpers
from .pg_helpers import _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(
        "tenant_registry_entries", "tool_grants", "character_session_revocations",
        "access_requests", "access_allowlist",
    )
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    admin._staged_sde = None
    yield
    admin._staged_sde = None


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


def test_do_set_tool_grants_does_not_auto_add_characters(monkeypatch):
    # Decision 11: Admin UI auto-ticks "characters"; the API does not.
    monkeypatch.setattr(ESIClient, "character_search", lambda self, name: 42)
    admin.do_add_user("Some Pilot")

    admin.do_set_tool_grants(42, ["production"])

    assert storage.list_tool_grants_for_character(42) == ["production"]


def test_do_set_tool_grants_rejects_unknown_tool_key(monkeypatch):
    monkeypatch.setattr(ESIClient, "character_search", lambda self, name: 42)
    admin.do_add_user("Some Pilot")

    with pytest.raises(ActionError, match="Unknown tool_key"):
        admin.do_set_tool_grants(42, ["not-a-real-tool"])


def test_do_set_tool_grants_rejects_unregistered_character():
    with pytest.raises(ActionError, match="isn't a registered user"):
        admin.do_set_tool_grants(999999, ["trading"])


def test_do_apply_sde_invalidates_caches(monkeypatch):
    # GitHub issue #34: moved here from production/actions.py - the SDE
    # cache is global/shared, not per-tenant, so this is a superadmin action.
    #
    # GitHub issue #54's own follow-up bug (found in code review of this PR):
    # invalidate_discover_cache/invalidate_ship_margin_cache became per-tenant
    # by default once their caches were keyed by tenant - an apply here MUST
    # pass all_tenants=True (every tenant's discover/margin results can be
    # affected by a global SDE change, not just the calling admin's own),
    # or every other tenant keeps serving stale results for up to the full
    # cache TTL. Asserting only that the functions were *called* (the
    # original version of this test) let exactly that regression pass.
    fetched = sde.FetchedSde(dump_etag="e")
    admin._staged_sde = fetched
    invalidated = []
    monkeypatch.setattr(sde, "apply_sde", lambda staged: {"sde_types": 100})
    monkeypatch.setattr(admin, "invalidate_discover_cache",
                         lambda all_tenants=False: invalidated.append(("discover", all_tenants)))
    monkeypatch.setattr(admin, "invalidate_ship_margin_cache",
                         lambda all_tenants=False: invalidated.append(("ship_margin", all_tenants)))

    result = admin.do_apply_sde()

    assert result == {"sde_types": 100}
    assert invalidated == [("discover", True), ("ship_margin", True)]
    assert admin._staged_sde is None


def test_do_apply_sde_without_preview_raises():
    admin._staged_sde = None
    with pytest.raises(ActionError, match="Keine Preview-Daten vorhanden"):
        admin.do_apply_sde()


def test_do_apply_sde_after_preview_uses_staged_data_without_redownload(monkeypatch):
    fetched = sde.FetchedSde(types=[(1, 1, "Rifter", 1.0, 1, None, None, None, 1)], dump_etag="e1")
    fetch_calls = []

    def fake_fetch(cfg=None, progress_callback=None):
        fetch_calls.append(1)
        return fetched

    applied = []
    monkeypatch.setattr(sde, "fetch_sde", fake_fetch)
    monkeypatch.setattr(storage, "get_sde_snapshot_for_diff", lambda: {
        "sde_types": [], "sde_blueprint_materials": [], "sde_blueprint_products": [],
        "sde_blueprint_time": [], "sde_invention_probability": [], "counts": {},
    })
    monkeypatch.setattr(sde_diff, "build_diff", lambda f, snap: {"new_items": [{"type_id": 1, "name": "Rifter"}]})
    monkeypatch.setattr(sde, "apply_sde", lambda staged: applied.append(staged) or {"sde_types": 1})
    monkeypatch.setattr(admin, "invalidate_discover_cache", lambda all_tenants=False: None)
    monkeypatch.setattr(admin, "invalidate_ship_margin_cache", lambda all_tenants=False: None)

    diff = admin.do_preview_sde()
    assert diff == {"new_items": [{"type_id": 1, "name": "Rifter"}]}
    assert admin._staged_sde is fetched
    assert fetch_calls == [1]

    result = admin.do_apply_sde()
    assert result == {"sde_types": 1}
    assert applied == [fetched]
    assert fetch_calls == [1]
    assert admin._staged_sde is None


def test_do_preview_sde_wraps_network_error():
    def _raise(cfg=None, progress_callback=None):
        raise requests.RequestException("connection refused")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sde, "fetch_sde", _raise)
        with pytest.raises(ActionError, match="SDE refresh failed"):
            admin.do_preview_sde()


def test_do_preview_sde_emits_increasing_batch_progress(monkeypatch):
    """Track A: each of the 13 sequential CSV fetches reports batch/total_batches."""
    fetched = []

    def fake_fetch(session, base, filename):
        fetched.append(filename)
        return []

    monkeypatch.setattr(sde, "_fetch_csv", fake_fetch)
    monkeypatch.setattr(sde, "_dump_etag", lambda *a, **k: "etag")
    monkeypatch.setattr(storage, "get_sde_snapshot_for_diff", lambda: {
        "sde_types": [], "sde_blueprint_materials": [], "sde_blueprint_products": [],
        "sde_blueprint_time": [], "sde_invention_probability": [], "counts": {},
    })
    monkeypatch.setattr(sde_diff, "build_diff", lambda f, snap: {"new_items": []})
    monkeypatch.setattr(storage, "replace_sde_data", lambda **k: (_ for _ in ()).throw(
        AssertionError("preview must not write the SDE cache")))

    seen = []
    result = admin.do_preview_sde(progress_callback=seen.append)

    assert len(sde._SDE_CSV_FILES) == 13
    assert fetched == list(sde._SDE_CSV_FILES)
    assert [p["batch"] for p in seen] == list(range(1, 14))
    assert all(p["phase"] == "run" for p in seen)
    assert all(p["total_batches"] == 13 for p in seen)
    assert seen[0]["message"] == "Fetching invTypes.csv"
    assert seen[-1]["message"] == "Fetching invTypeMaterials.csv"
    assert result == {"new_items": []}
    assert admin._staged_sde is not None
    assert admin._staged_sde.dump_etag == "etag"


def test_do_refresh_jita_price_cache_returns_count_and_timestamp(monkeypatch):
    # Standalone manual trigger for production/jita_price_cache.py's shared
    # cache - same cross-tenant-impacting-cache reasoning as do_apply_sde
    # above, thin wrapper only (the real refresh logic is tested in
    # tests/test_production_jita_price_cache.py).
    monkeypatch.setattr(jita_price_cache, "refresh_jita_price_cache", lambda: 42)
    monkeypatch.setattr(jita_price_cache, "last_updated_at", lambda: "2026-09-01T00:00:00+00:00")

    result = admin.do_refresh_jita_price_cache()

    assert result == {"cached_type_ids": 42, "updated_at": "2026-09-01T00:00:00+00:00"}


def test_do_refresh_jita_price_cache_wraps_network_error(monkeypatch):
    def _raise():
        raise ESIError("ESI down")
    monkeypatch.setattr(jita_price_cache, "refresh_jita_price_cache", _raise)
    with pytest.raises(ActionError, match="Could not refresh Jita price cache"):
        admin.do_refresh_jita_price_cache()


# docs/MANUAL_TRACKING_PLAN.md phase 2, question 1 - Default-Tenant-only
# operator switch.
def test_do_get_structure_resolution_fallback_defaults_to_false():
    with storage.tenant_context(storage.DEFAULT_TENANT_ID), storage.connect() as conn:
        conn.execute("DELETE FROM tenant_settings WHERE scope = 'production'")

    assert admin.do_get_structure_resolution_fallback() == {"global_structure_resolution_fallback": False}


def test_do_set_structure_resolution_fallback_persists_and_reads_back():
    admin.do_set_structure_resolution_fallback(True)
    try:
        assert admin.do_get_structure_resolution_fallback() == {"global_structure_resolution_fallback": True}
        with storage.tenant_context(storage.DEFAULT_TENANT_ID):
            assert storage.load_tenant_settings("production") == {"global_structure_resolution_fallback": True}
    finally:
        admin.do_set_structure_resolution_fallback(False)  # don't leak into other tests


def test_do_set_structure_resolution_fallback_is_scoped_to_default_tenant_only():
    other_tenant = storage.create_tenant("Some Other Tenant")
    admin.do_set_structure_resolution_fallback(True)
    try:
        with storage.tenant_context(other_tenant):
            assert storage.load_tenant_settings("production").get("global_structure_resolution_fallback") is None
    finally:
        admin.do_set_structure_resolution_fallback(False)
