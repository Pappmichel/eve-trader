"""P5-07 / P5-08: a legacy bare-keyed TokenManager role (predating GitHub
issue #46's multi-character scheme - "buyer"/"seller"/"producer"/...
instead of "buyer:<character_id>") self-heals to canonical form the first
time it's touched (auth._rekey_legacy_bare_roles, called from
TokenManager._load()). Confirms both real-world failure modes this closes:

- P5-07: DELETE .../auth/character/<bare role> used to 400 forever
  (validate_role_key_for_tool's canonical-only grammar, F-05/P5-02) with no
  way to ever reach the token underneath - the real UI flow is always
  list-then-remove (the frontend echoes back GET .../characters's own
  role_key), so once that GET has run once, the DELETE it enables works.
- P5-08: esi_client's per-auth_role structure-book cache (F-02) treats a
  bare "seller" as one principal, but it's only unique WITHIN a tenant -
  two different characters holding a legacy bare "seller" token collide on
  the same class-level cache key until each is migrated to its own
  "seller:<character_id>".
"""
from __future__ import annotations

from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from eve_trader import access_gate, storage
from eve_trader.api.app import create_app
from eve_trader.auth import TokenManager, TokenRecord
from eve_trader.esi_client import ESIClient

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
    client.cookies.clear()
    pg_helpers.wipe_tables("tenant_registry_entries", "tool_grants", "character_session_revocations")
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id != %s", (storage.DEFAULT_TENANT_ID,))
    yield
    client.cookies.clear()


@pytest.fixture(autouse=True)
def _reset_order_book_caches():
    ESIClient.clear_order_book_caches()
    yield
    ESIClient.clear_order_book_caches()


def _cookie(character_id, tenant_id, name="Pilot"):
    token = access_gate.create_session_token(character_id, name, tenant_id)
    return {access_gate.SESSION_COOKIE_NAME: token}


def _seed_legacy_bare_token(tenant_id: str, character_id: int, character_name: str = "Legacy Seller") -> None:
    record = TokenRecord(
        role="seller", character_id=character_id, character_name=character_name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes="",
    )
    with storage.tenant_context(tenant_id):
        storage.save_tenant_token("seller", asdict(record))


def _trading_session(character_id: int, character_name: str = "T"):
    tenant_id = storage.create_tenant(character_name)
    storage.add_tenant_registry_entry(tenant_id, character_id, character_name=character_name)
    storage.set_tool_grant(character_id, "trading", tenant_id)
    return tenant_id, _cookie(character_id, tenant_id)


# --------------------------------------------------------------------- P5-07
def test_bare_role_key_is_still_rejected_directly(_apply_admin_schema):
    """Unchanged, by design: a literal bare "seller" is not, and was never
    meant to be, a valid HTTP role_key - validate_role_key_for_tool's
    canonical-only grammar (F-05) rejects it regardless of what's in
    storage. The fix is that the token underneath is never permanently
    unreachable - see the list-then-remove test below for the real path."""
    tenant_id, cookies = _trading_session(11)
    _seed_legacy_bare_token(tenant_id, character_id=11)

    resp = client.delete("/api/trading/auth/character/seller", cookies=cookies)

    assert resp.status_code == 400


def test_legacy_token_is_removable_after_the_character_list_migrates_it(_apply_admin_schema):
    """The actual P5-07 attack scenario, closed end-to-end: a legacy
    bare-keyed token, reached the exact way a real operator would - load
    the character list (as Trading's own "Seller characters" panel does on
    every page render), then remove using the role_key that list returned."""
    tenant_id, cookies = _trading_session(11)
    _seed_legacy_bare_token(tenant_id, character_id=11, character_name="Legacy Seller")

    listed = client.get("/api/trading/seller-characters", cookies=cookies)
    assert listed.status_code == 200
    rows = listed.json()
    assert rows == [{"role_key": "seller:11", "character_id": 11, "character_name": "Legacy Seller"}]

    removed = client.delete(f"/api/trading/auth/character/{rows[0]['role_key']}", cookies=cookies)
    assert removed.status_code == 200
    assert removed.json() == {"removed": "seller:11"}

    with storage.tenant_context(tenant_id):
        assert storage.load_all_tenant_tokens() == {}

    # And the character list is now correctly empty too, not still showing
    # a phantom entry under either key.
    listed_after = client.get("/api/trading/seller-characters", cookies=cookies)
    assert listed_after.json() == []


def test_two_tenants_each_holding_a_legacy_bare_token_do_not_collide_on_removal(_apply_admin_schema):
    """Confirms the migration is per-tenant, not global: tenant A removing
    their (now-canonical) "seller:11" must never touch tenant B's own
    "seller:22", even though both started out stored under the identical
    bare "seller" key before either was touched."""
    tenant_a, cookies_a = _trading_session(11, "A")
    tenant_b, cookies_b = _trading_session(22, "B")
    _seed_legacy_bare_token(tenant_a, character_id=11, character_name="A's Seller")
    _seed_legacy_bare_token(tenant_b, character_id=22, character_name="B's Seller")

    rows_a = client.get("/api/trading/seller-characters", cookies=cookies_a).json()
    assert rows_a == [{"role_key": "seller:11", "character_id": 11, "character_name": "A's Seller"}]

    removed = client.delete("/api/trading/auth/character/seller:11", cookies=cookies_a)
    assert removed.status_code == 200

    with storage.tenant_context(tenant_a):
        assert storage.load_all_tenant_tokens() == {}
    rows_b = client.get("/api/trading/seller-characters", cookies=cookies_b).json()
    assert rows_b == [{"role_key": "seller:22", "character_id": 22, "character_name": "B's Seller"}]


# --------------------------------------------------------------------- P5-08
def test_legacy_bare_auth_role_no_longer_collides_once_migrated(monkeypatch):
    """Before P5-07/P5-08: two different characters, each still holding a
    legacy bare "seller" token, would resolve to the identical auth_role
    string "seller" and collide on esi_client's (structure_id, auth_role)
    cache key (F-02) - character B's request could silently receive
    character A's cached structure order book. Once each tenant's tokens
    have been loaded at least once (which every real ESI-auth-resolving
    call site already does via list_roles/get_record - see
    actions._list_role_characters, own_orders.check_undercut_pooled),
    their auth_role strings diverge to "seller:11" / "seller:22" and the
    cache keys can never collide again."""
    calls: list[str | None] = []

    def _fake_get_all_pages(self, path, params=None, auth_role=None, max_workers=5):
        calls.append(auth_role)
        return [{"type_id": 34, "is_buy_order": False, "price": 1.0, "volume_remain": 1}]

    monkeypatch.setattr(ESIClient, "_get_all_pages", _fake_get_all_pages)

    tenant_a = storage.create_tenant("A")
    tenant_b = storage.create_tenant("B")
    _seed_legacy_bare_token(tenant_a, character_id=11)
    _seed_legacy_bare_token(tenant_b, character_id=22)

    # What every real caller does before resolving auth_role for an ESI
    # call - see actions.py's _list_role_characters/do_check_undercut and
    # own_orders.check_undercut_pooled, which all go through
    # TokenManager.list_roles/get_record (hence _load()) first.
    with storage.tenant_context(tenant_a):
        auth_role_a = TokenManager().list_roles("seller")[0]
    with storage.tenant_context(tenant_b):
        auth_role_b = TokenManager().list_roles("seller")[0]

    assert auth_role_a == "seller:11"
    assert auth_role_b == "seller:22"
    assert auth_role_a != auth_role_b

    ESIClient().structure_orders_raw(999, auth_role=auth_role_a)
    ESIClient().structure_orders_raw(999, auth_role=auth_role_b)

    # Two distinct principals against the same structure_id -> two real
    # upstream fetches, not a cache hit serving one tenant's book to the
    # other's request.
    assert calls == ["seller:11", "seller:22"]
