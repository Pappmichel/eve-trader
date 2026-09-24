import contextlib

import pytest

from eve_trader import esi_client, storage
from eve_trader.actions import ActionError
from eve_trader.production import actions, esi_sync
from eve_trader.production.config import PRODUCTION_CONFIG


@pytest.fixture(autouse=True)
def _no_global_cache_hit_by_default(monkeypatch):
    """Phase 2 added a global-cache tier (storage.get_global_structure_names)
    ahead of the live-ESI tiers this file's own tests exercise - every test
    below monkeypatches storage.get_cached_structure_name itself, so default
    this one to "no hit" too, and upsert_global_structure_name to a no-op
    capture, so tests that don't care about the global cache don't need to
    repeat this in every test."""
    monkeypatch.setattr(storage, "get_global_structure_names", lambda location_ids: {})
    monkeypatch.setattr(storage, "upsert_global_structure_name", lambda location_id, name, solar_system_id=None: None)


def test_do_resolve_structure_name_captures_solar_system_id_from_corp_structures(monkeypatch):
    # GitHub issue #12/#21: solar_system_id (needed for per-category cost
    # index lookups) used to be discarded entirely - only `name` was ever
    # read out of either ESI path's response.
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                         lambda capability_key: [("esi:1", 1, "Alice")])
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_client.ESIClient, "corporation_structures", lambda self, corp_id, auth_role: [
        {"structure_id": 60000000001, "name": "C-J Keepstar", "solar_system_id": 30000142},
    ])
    captured = {}
    monkeypatch.setattr(storage, "get_cached_structure_name", lambda loc_id: (False, None))
    monkeypatch.setattr(storage, "set_cached_structure_name",
                         lambda loc_id, name, solar_system_id=None: captured.update(
                             location_id=loc_id, name=name, solar_system_id=solar_system_id))

    result = actions.do_resolve_structure_name(60000000001)

    assert result == {"location_id": 60000000001, "name": "C-J Keepstar", "cached": False}
    assert captured == {"location_id": 60000000001, "name": "C-J Keepstar", "solar_system_id": 30000142}


def test_do_resolve_structure_name_captures_solar_system_id_from_docking_history_fallback(monkeypatch):
    # Path 1 (corp structures) fails to find it - falls back to path 2
    # (ESIClient.get_structure_name, now returning the raw dict instead of
    # just a name string) - solar_system_id must still be captured there.
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                         lambda capability_key: [("esi:1", 1, "Alice")])
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_client.ESIClient, "corporation_structures", lambda self, corp_id, auth_role: [])
    monkeypatch.setattr(esi_client.ESIClient, "get_structure_name", lambda self, loc_id, auth_role: {
        "name": "Some Other Structure", "solar_system_id": 30000144,
    })
    captured = {}
    monkeypatch.setattr(storage, "get_cached_structure_name", lambda loc_id: (False, None))
    monkeypatch.setattr(storage, "set_cached_structure_name",
                         lambda loc_id, name, solar_system_id=None: captured.update(
                             location_id=loc_id, name=name, solar_system_id=solar_system_id))

    result = actions.do_resolve_structure_name(60000000002)

    assert result == {"location_id": 60000000002, "name": "Some Other Structure", "cached": False}
    assert captured["solar_system_id"] == 30000144


def test_do_resolve_structure_name_successful_resolve_writes_global_cache(monkeypatch):
    """Decision Q2 (docs/MANUAL_TRACKING_PLAN.md phase 2): every successful
    resolution becomes globally visible, including a regular tenant
    resolving with its own characters - not just the operator fallback's
    own resolutions."""
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                         lambda capability_key: [("esi:1", 1, "Alice")])
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_client.ESIClient, "corporation_structures", lambda self, corp_id, auth_role: [
        {"structure_id": 60000000001, "name": "C-J Keepstar", "solar_system_id": 30000142},
    ])
    monkeypatch.setattr(storage, "get_cached_structure_name", lambda loc_id: (False, None))
    monkeypatch.setattr(storage, "set_cached_structure_name", lambda *a, **kw: None)
    captured = {}
    monkeypatch.setattr(storage, "upsert_global_structure_name",
                         lambda location_id, name, solar_system_id=None: captured.update(
                             location_id=location_id, name=name, solar_system_id=solar_system_id))

    actions.do_resolve_structure_name(60000000001)

    assert captured == {"location_id": 60000000001, "name": "C-J Keepstar", "solar_system_id": 30000142}


def test_do_resolve_structure_name_uses_global_cache_hit_without_calling_esi(monkeypatch):
    """Tier 2 of the lookup chain - a global-cache hit is copied into this
    tenant's own cache and returned, no ESI call at all."""
    monkeypatch.setattr(storage, "get_cached_structure_name", lambda loc_id: (False, None))
    monkeypatch.setattr(storage, "get_global_structure_names",
                         lambda location_ids: {60000000001: ("Globally Known Structure", 30000142)})
    captured = {}
    monkeypatch.setattr(storage, "set_cached_structure_name",
                         lambda loc_id, name, solar_system_id=None: captured.update(
                             location_id=loc_id, name=name, solar_system_id=solar_system_id))
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info",
                         lambda self, cid: pytest.fail("must not call ESI on a global-cache hit"))

    result = actions.do_resolve_structure_name(60000000001)

    assert result == {"location_id": 60000000001, "name": "Globally Known Structure", "cached": True}
    assert captured == {"location_id": 60000000001, "name": "Globally Known Structure", "solar_system_id": 30000142}


def test_do_resolve_structure_name_force_skips_own_and_global_cache(monkeypatch):
    was_cache_checked = {"value": False}

    def _fail_if_checked(loc_id):
        was_cache_checked["value"] = True
        return (True, "Should Not Be Used")

    monkeypatch.setattr(storage, "get_cached_structure_name", _fail_if_checked)
    monkeypatch.setattr(storage, "get_global_structure_names",
                         lambda location_ids: pytest.fail("must not consult the global cache when force=True"))
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                         lambda capability_key: [("esi:1", 1, "Alice")])
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_client.ESIClient, "corporation_structures", lambda self, corp_id, auth_role: [
        {"structure_id": 60000000001, "name": "Fresh Name", "solar_system_id": 30000142},
    ])
    monkeypatch.setattr(storage, "set_cached_structure_name", lambda *a, **kw: None)

    result = actions.do_resolve_structure_name(60000000001, force=True)

    assert result == {"location_id": 60000000001, "name": "Fresh Name", "cached": False}
    assert was_cache_checked["value"] is False


def test_do_resolve_structure_name_raises_without_characters_or_fallback(monkeypatch):
    monkeypatch.setattr(storage, "get_cached_structure_name", lambda loc_id: (False, None))
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda capability_key: [])
    assert PRODUCTION_CONFIG.global_structure_resolution_fallback is False

    with pytest.raises(ActionError):
        actions.do_resolve_structure_name(60000000001)


@contextlib.contextmanager
def _fake_enter_tenant(tenant_id):
    """A DB-free stand-in for tenant_scope.enter_tenant (which also
    re-resolves TRADING_CONFIG/PRODUCTION_CONFIG/etc from tenant_settings,
    a real DB read - see storage.load_tenant_settings) - this module's other
    tests deliberately don't need Postgres, and enter_tenant's own generic
    "restores every contextvar, including on error" guarantee is already
    covered by test_tenant_scope.py. These two tests only need to prove
    do_resolve_structure_name's own fallback tier actually switches the
    ambient tenant to DEFAULT_TENANT_ID for its own capability lookup, and
    switches it back afterward - same shape as the real thing, just without
    the config-resolution DB read."""
    token = storage.set_current_tenant(tenant_id)
    try:
        yield
    finally:
        storage.reset_current_tenant(token)


def test_do_resolve_structure_name_falls_back_to_default_tenant_characters(monkeypatch):
    """Tier 4 (docs/MANUAL_TRACKING_PLAN.md phase 2, question 1): this
    tenant has no structure_name_resolution characters of its own, but the
    Default Tenant's operator characters can resolve it, and the fallback
    switch is on."""
    monkeypatch.setattr(storage, "get_cached_structure_name", lambda loc_id: (False, None))
    monkeypatch.setattr(storage, "set_cached_structure_name", lambda *a, **kw: None)
    monkeypatch.setattr(PRODUCTION_CONFIG, "global_structure_resolution_fallback", True)
    monkeypatch.setattr(actions.tenant_scope, "enter_tenant", _fake_enter_tenant)

    def _characters(capability_key):
        current_tenant = storage.get_current_tenant()
        if current_tenant == storage.DEFAULT_TENANT_ID:
            return [("esi:1", 1, "Operator Alice")]
        return []

    monkeypatch.setattr(esi_sync, "list_capability_characters", _characters)
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_client.ESIClient, "corporation_structures", lambda self, corp_id, auth_role: [
        {"structure_id": 60000000001, "name": "Operator-Resolved Structure", "solar_system_id": 30000142},
    ])

    result = actions.do_resolve_structure_name(60000000001)

    assert result == {"location_id": 60000000001, "name": "Operator-Resolved Structure", "cached": False}


def test_do_resolve_structure_name_fallback_restores_tenant_context_on_error(monkeypatch):
    """The fallback tier's own tenant_scope.enter_tenant use must still
    restore the caller's ambient tenant even when the fallback tier itself
    raises."""
    monkeypatch.setattr(storage, "get_cached_structure_name", lambda loc_id: (False, None))
    monkeypatch.setattr(PRODUCTION_CONFIG, "global_structure_resolution_fallback", True)
    monkeypatch.setattr(actions.tenant_scope, "enter_tenant", _fake_enter_tenant)

    def _boom(capability_key):
        if storage.get_current_tenant() == storage.DEFAULT_TENANT_ID:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(esi_sync, "list_capability_characters", _boom)

    with storage.tenant_context("caller-tenant"):
        with pytest.raises(RuntimeError):
            actions.do_resolve_structure_name(60000000001)
        assert storage.get_current_tenant() == "caller-tenant"
