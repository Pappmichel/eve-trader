"""do_resolve_structure_name: the tenant's own lookup is the central resolver
(tested in tests/test_location_resolver.py); these tests cover this action's
own logic - what it returns, when it raises, and the operator-fallback tier.
DB-free: the resolver and the Default Tenant switch are stubbed."""
import contextlib

import pytest

from eve_trader import esi_client, storage
from eve_trader.actions import ActionError
from eve_trader.locations import resolver
from eve_trader.production import actions, esi_sync
from eve_trader.production.config import PRODUCTION_CONFIG

LOC = 1049588174021


@contextlib.contextmanager
def _fake_enter_tenant(tenant_id):
    """A DB-free stand-in for tenant_scope.enter_tenant (which also re-resolves
    the per-tenant configs from tenant_settings, a real DB read). Only switches
    storage's own tenant contextvar; a test that wants the Default Tenant's
    fallback switch on patches PRODUCTION_CONFIG directly."""
    token = storage.set_current_tenant(tenant_id)
    try:
        yield
    finally:
        storage.reset_current_tenant(token)


@pytest.fixture(autouse=True)
def _db_free(monkeypatch):
    monkeypatch.setattr(actions.tenant_scope, "enter_tenant", _fake_enter_tenant)
    monkeypatch.setattr(resolver, "record_resolved", lambda *a, **kw: recorded.append(a))
    recorded.clear()


recorded: list = []


def _resolver_says(monkeypatch, name=None, status=None, attempted=False, captured=None):
    def fake(location_ids, **kw):
        if captured is not None:
            captured.update(kw)
        return {"ids": {LOC: {"name": name, "status": status, "attempted": attempted}}}
    monkeypatch.setattr(resolver, "resolve_ids", fake)


def test_a_name_the_resolver_found_is_returned_as_fresh(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda key: [("esi:1", 1, "Alice")])
    _resolver_says(monkeypatch, name="C-J Keepstar", status="resolved", attempted=True)

    assert actions.do_resolve_structure_name(LOC) == {"location_id": LOC, "name": "C-J Keepstar", "cached": False}


def test_a_remembered_name_is_returned_as_cached(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda key: [("esi:1", 1, "Alice")])
    _resolver_says(monkeypatch, name="C-J Keepstar", status="resolved", attempted=False)

    assert actions.do_resolve_structure_name(LOC)["cached"] is True


def test_force_and_the_characters_are_passed_to_the_resolver(monkeypatch):
    chars = [("esi:1", 1, "Alice")]
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda key: chars)
    captured = {}
    _resolver_says(monkeypatch, name="X", status="resolved", attempted=True, captured=captured)

    actions.do_resolve_structure_name(LOC, force=True)

    assert captured["force"] is True and captured["characters"] == chars


def test_a_failure_the_registry_remembers_is_not_retried_or_sent_to_the_fallback(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda key: [("esi:1", 1, "Alice")])
    monkeypatch.setattr(PRODUCTION_CONFIG, "global_structure_resolution_fallback", True)
    monkeypatch.setattr(esi_sync, "resolve_structure_ids",
                        lambda *a, **kw: pytest.fail("a remembered refusal must not reach the fallback"))
    _resolver_says(monkeypatch, name=None, status="forbidden", attempted=False)

    assert actions.do_resolve_structure_name(LOC) == {"location_id": LOC, "name": None, "cached": True}


def test_raises_without_characters_or_fallback(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda key: [])
    _resolver_says(monkeypatch, name=None, status="pending", attempted=False)
    assert PRODUCTION_CONFIG.global_structure_resolution_fallback is False

    with pytest.raises(ActionError):
        actions.do_resolve_structure_name(LOC)


def test_falls_back_to_default_tenant_characters(monkeypatch):
    """The tenant has no characters of its own, but the Default Tenant's
    operator characters can resolve it and the fallback switch is on."""
    monkeypatch.setattr(PRODUCTION_CONFIG, "global_structure_resolution_fallback", True)
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                        lambda key: [("esi:1", 1, "Operator Alice")]
                        if storage.get_current_tenant() == storage.DEFAULT_TENANT_ID else [])
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_client.ESIClient, "corporation_structures", lambda self, corp_id, auth_role: [
        {"structure_id": LOC, "name": "Operator-Resolved Structure", "solar_system_id": 30000142},
    ])
    _resolver_says(monkeypatch, name=None, status="pending", attempted=False)

    result = actions.do_resolve_structure_name(LOC)

    assert result == {"location_id": LOC, "name": "Operator-Resolved Structure", "cached": False}
    assert recorded == [(LOC, "Operator-Resolved Structure", 30000142)]  # stored through the resolver


def test_fallback_restores_tenant_context_on_error(monkeypatch):
    monkeypatch.setattr(PRODUCTION_CONFIG, "global_structure_resolution_fallback", True)

    def _boom(key):
        if storage.get_current_tenant() == storage.DEFAULT_TENANT_ID:
            raise RuntimeError("boom")
        return []
    monkeypatch.setattr(esi_sync, "list_capability_characters", _boom)
    _resolver_says(monkeypatch, name=None, status="pending", attempted=False)

    with storage.tenant_context("caller-tenant"):
        with pytest.raises(RuntimeError):
            actions.do_resolve_structure_name(LOC)
        assert storage.get_current_tenant() == "caller-tenant"


def test_fallback_reads_default_tenant_switch_not_requesting_tenants(monkeypatch):
    """Confirmed real bug (code review 2026-09-25): the switch is
    Default-Tenant-only, so reading the ambient PRODUCTION_CONFIG would read the
    requesting tenant's own copy (always False) and make the fallback inert for
    every tenant but the Default one. This fake enter_tenant swaps the flag per
    tenant so a regression to the ambient value is caught."""
    per_tenant_fallback = {storage.DEFAULT_TENANT_ID: True, "requesting-tenant": False}

    @contextlib.contextmanager
    def _enter_with_per_tenant_config(tenant_id):
        token = storage.set_current_tenant(tenant_id)
        previous = PRODUCTION_CONFIG.global_structure_resolution_fallback
        PRODUCTION_CONFIG.global_structure_resolution_fallback = per_tenant_fallback[tenant_id]
        try:
            yield
        finally:
            PRODUCTION_CONFIG.global_structure_resolution_fallback = previous
            storage.reset_current_tenant(token)

    monkeypatch.setattr(actions.tenant_scope, "enter_tenant", _enter_with_per_tenant_config)
    monkeypatch.setattr(PRODUCTION_CONFIG, "global_structure_resolution_fallback", False)
    monkeypatch.setattr(esi_sync, "list_capability_characters",
                        lambda key: [("esi:1", 1, "Operator Alice")]
                        if storage.get_current_tenant() == storage.DEFAULT_TENANT_ID else [])
    monkeypatch.setattr(esi_client.ESIClient, "character_public_info", lambda self, cid: {"corporation_id": 99})
    monkeypatch.setattr(esi_client.ESIClient, "corporation_structures", lambda self, corp_id, auth_role: [
        {"structure_id": LOC, "name": "Operator-Resolved Structure", "solar_system_id": 30000142},
    ])
    _resolver_says(monkeypatch, name=None, status="pending", attempted=False)

    with storage.tenant_context("requesting-tenant"):
        result = actions.do_resolve_structure_name(LOC)

    assert result["name"] == "Operator-Resolved Structure"
