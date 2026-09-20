"""Permanent accessor isolation test (docs/ESI_ACCESS_PLAN.md decision 9).

Modelled on tests/test_pg_tenant_isolation.py: exercised through the real
`read_esi` accessor, not a raw SQL stand-in. Two layers, kept distinct:
tenant_id is "whose data"; the sharing relation is "which of this tenant's
owners this tool may read".
"""
from __future__ import annotations

import pytest

from eve_trader import storage
from eve_trader.esi_data.access import AccessorError, read_esi

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
    tenant_pair,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

TYPE_ID = 34
LOCATION_ID = 1000000000001
ALICE = 1001
BOB = 1002


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("character_assets", "esi_sharing")
    yield
    pg_helpers.wipe_tables("character_assets", "esi_sharing")


def _asset_row(item_id, qty, name):
    return (item_id, TYPE_ID, LOCATION_ID, "Hangar", qty, 0, name)


def _share(owner_id, tool_key, data_kind="assets"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, ?)",
            (owner_id, data_kind, tool_key),
        )


def test_read_esi_requires_a_consuming_tool_key(tenant):
    with pytest.raises(AccessorError, match="tool_key"):
        read_esi("assets", None)
    with pytest.raises(AccessorError, match="tool_key"):
        read_esi("assets", "")
    with pytest.raises(AccessorError, match="unknown tool_key"):
        read_esi("assets", "not_a_tool")
    with pytest.raises(AccessorError, match="unknown data_kind"):
        read_esi("not_a_kind", "production")


def test_unshared_owner_is_unreachable_through_the_accessor(tenant):
    storage.replace_assets(
        "character_assets",
        [_asset_row(1, 10, "Alice")],
        owner_character_id=ALICE, owner_name="Alice",
    )
    storage.replace_assets(
        "character_assets",
        [_asset_row(2, 20, "Bob")],
        owner_character_id=BOB, owner_name="Bob",
    )
    _share(ALICE, "production")
    _share(BOB, "sorting")

    prod = read_esi("assets", "production")
    assert {r["owner_character_id"] for r in prod} == {ALICE}
    sort = read_esi("assets", "sorting")
    assert {r["owner_character_id"] for r in sort} == {BOB}
    trade = read_esi("assets", "trading")
    assert trade == []
    # A data kind not shared with production is empty even though rows exist.
    assert read_esi("blueprints", "production") == []


def test_accessor_is_also_tenant_scoped(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.replace_assets(
            "character_assets",
            [_asset_row(1, 10, "Alice")],
            owner_character_id=ALICE, owner_name="Alice",
        )
        _share(ALICE, "production")
    with storage.tenant_context(tenant_b):
        storage.replace_assets(
            "character_assets",
            [_asset_row(2, 99, "Alice")],
            owner_character_id=ALICE, owner_name="Alice",
        )
        _share(ALICE, "production")

    with storage.tenant_context(tenant_a):
        rows = read_esi("assets", "production")
    assert len(rows) == 1
    assert rows[0]["quantity"] == 10

    with storage.tenant_context(tenant_b):
        rows = read_esi("assets", "production")
    assert len(rows) == 1
    assert rows[0]["quantity"] == 99
