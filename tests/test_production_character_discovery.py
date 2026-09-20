"""Known gap 4 (docs/ESI_ACCESS_PLAN.md), closed: production/esi_sync.py's
sharing/capability-based character discovery - list_shared_producer_
characters and list_capability_characters - against real esi_sharing/
esi_character_capabilities rows, replacing the legacy producer:* prefix
listing (list_producer_characters) those two supersede.
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from eve_trader import storage
from eve_trader.auth import TokenRecord
from eve_trader.production import esi_sync

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 5001  # shares via the modern esi:<id> key - never held a producer:* token
BOB = 5002    # shares but only holds a scope-less token (needs Re-authorize)
CAROL = 5003  # not shared/capable at all


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("tenant_tokens", "esi_sharing", "esi_character_capabilities")
    yield
    pg_helpers.wipe_tables("tenant_tokens", "esi_sharing", "esi_character_capabilities")


def _save_token(role: str, character_id: int, character_name: str, scopes: str) -> None:
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=character_id, character_name=character_name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes=scopes,
    )))


def _share(owner_type: str, owner_id: int, data_kind: str, tool_key: str = "production") -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            (owner_type, owner_id, data_kind, tool_key),
        )


def test_list_shared_producer_characters_includes_esi_prefixed_character(tenant):
    """The exact gap 4 scenario: a character added via the Characters
    page's add-a-character path holds an esi:<id> key, never a producer:*
    one, but shares Assets with production - must still be discovered."""
    _save_token(f"esi:{ALICE}", ALICE, "Alice", "esi-assets.read_assets.v1")
    _share("character", ALICE, "assets")

    result = esi_sync.list_shared_producer_characters()

    assert result == [(f"esi:{ALICE}", ALICE, "Alice")]


def test_list_shared_producer_characters_prefers_assets_scope_over_market_orders(tenant):
    _save_token(f"esi:{ALICE}", ALICE, "Alice",
                "esi-assets.read_assets.v1 esi-markets.read_character_orders.v1")
    _share("character", ALICE, "assets")
    _share("character", ALICE, "market_orders")

    role, character_id, name = esi_sync.list_shared_producer_characters()[0]

    assert (role, character_id, name) == (f"esi:{ALICE}", ALICE, "Alice")


def test_list_shared_producer_characters_falls_back_to_market_orders_scope(tenant):
    _save_token(f"esi:{ALICE}", ALICE, "Alice", "esi-markets.read_character_orders.v1")
    _share("character", ALICE, "market_orders")

    assert esi_sync.list_shared_producer_characters() == [(f"esi:{ALICE}", ALICE, "Alice")]


def test_list_shared_producer_characters_excludes_unshared_character(tenant):
    _save_token(f"esi:{CAROL}", CAROL, "Carol", "esi-assets.read_assets.v1")
    # No _share() call for Carol.

    assert esi_sync.list_shared_producer_characters() == []


def test_list_shared_producer_characters_omits_shared_character_with_no_scope_holding_token(tenant):
    """Shared, but the only token on file doesn't actually carry the
    scope (e.g. an add-round token, identity only) - needs Re-authorize,
    must not be raised as an error here (same "skip, don't abort" shape
    as every other partial-failure path in this module)."""
    _save_token(f"esi:{BOB}", BOB, "Bob", "")  # identity-only, no scopes
    _share("character", BOB, "assets")

    assert esi_sync.list_shared_producer_characters() == []


def test_list_shared_producer_characters_ignores_corporation_only_sharing(tenant):
    """Corp sharing rows key on corporation_id, not character_id - this
    function only resolves character-level sharing (matches
    list_producer_characters itself, which never had a corp-owner variant
    either). A character whose own assets aren't shared must not appear
    just because their corp's are."""
    _save_token(f"esi:{ALICE}", ALICE, "Alice", "esi-assets.read_assets.v1")
    _share("corporation", 9001, "assets")

    assert esi_sync.list_shared_producer_characters() == []


def test_list_capability_characters_includes_ticked_character(tenant):
    _save_token(f"esi:{ALICE}", ALICE, "Alice", "esi-markets.structure_markets.v1")
    storage.upsert_esi_character_capability(ALICE, "structure_market_book")

    result = esi_sync.list_capability_characters("structure_market_book")

    assert result == [(f"esi:{ALICE}", ALICE, "Alice")]


def test_list_capability_characters_excludes_unticked_character(tenant):
    _save_token(f"esi:{CAROL}", CAROL, "Carol", "esi-markets.structure_markets.v1")
    # Capability never ticked for Carol.

    assert esi_sync.list_capability_characters("structure_market_book") == []


def test_list_capability_characters_omits_ticked_character_with_no_scope_holding_token(tenant):
    storage.upsert_esi_character_capability(BOB, "structure_market_book")
    _save_token(f"esi:{BOB}", BOB, "Bob", "")  # ticked, but not yet re-authorized

    assert esi_sync.list_capability_characters("structure_market_book") == []


def test_list_capability_characters_structure_name_resolution(tenant):
    _save_token(f"esi:{ALICE}", ALICE, "Alice", "esi-universe.read_structures.v1")
    storage.upsert_esi_character_capability(ALICE, "structure_name_resolution")

    assert esi_sync.list_capability_characters("structure_name_resolution") == [(f"esi:{ALICE}", ALICE, "Alice")]


def test_list_capability_characters_raises_on_unknown_capability(tenant):
    with pytest.raises(ValueError, match="unknown capability"):
        esi_sync.list_capability_characters("not_a_real_capability")


def test_sync_esi_guard_is_sharing_based_not_prefix_based(tenant, monkeypatch):
    """The exact regression this gap fixed: a producer:*-token-less,
    esi:<id>-shared character must not trip sync_esi's "nothing shared
    yet" guard."""
    _save_token(f"esi:{ALICE}", ALICE, "Alice", "esi-assets.read_assets.v1")
    _share("character", ALICE, "assets")
    monkeypatch.setattr("eve_trader.esi_data.orchestrator.do_sync_for_tool", lambda tool_key: {})
    monkeypatch.setattr(esi_sync, "_discover_structure_names", lambda client, location_ids, corp_roles: {})

    result = esi_sync.sync_esi()  # must not raise ActionError

    assert result == {"structure_names": {}}
