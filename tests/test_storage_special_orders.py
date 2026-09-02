"""Tests for storage.py's Special Orders CRUD (docs/special_orders_schema.sql)
- same pattern as tests/test_doctrine_storage.py's own schema-application
fixture + CRUD test.

special_order_items' PK is (tenant_id, order_id, type_id) - two non-tenant_id
key columns, not the single-key-column shape
tests/test_pg_composite_pk_tables.py's _SIMPLE_UPDATE_TABLES parametrizes
(`ON CONFLICT(tenant_id, <one col>)`) - it doesn't fit that shared list, so
this file has its own bespoke tenant-isolation test instead (mirrors that
file's own test_two_tenants_can_upsert_the_same_key_without_colliding, just
with a two-column conflict target)."""
from pathlib import Path

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant, tenant_pair  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_SPECIAL_ORDERS_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "special_orders_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_special_orders_schema(_apply_phase1_schema):
    """docs/special_orders_schema.sql, applied once per session via the
    owner role - same pattern as test_doctrine_storage.py's own
    _apply_doctrine_schema. Only depends on _apply_phase1_schema (this
    schema doesn't touch tenant_settings, unlike doctrine_schema.sql)."""
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_SPECIAL_ORDERS_SCHEMA_SQL.read_text(encoding="utf-8"))


def test_special_order_and_item_crud(tenant):
    order_id = storage.create_special_order("Customer X", net_against_stock=True)
    row = storage.get_special_order(order_id)
    assert row[1] == "Customer X"  # note
    assert row[2] is True          # net_against_stock
    assert row[3] == "open"        # status

    storage.upsert_special_order_item(order_id, 12058, "Cruise Missile Launcher II", 6000.0)
    storage.upsert_special_order_item(order_id, 638, "Republic Fleet Firetail", 10.0)
    items = storage.list_special_order_items(order_id)
    assert items == [
        (12058, "Cruise Missile Launcher II", 6000.0),
        (638, "Republic Fleet Firetail", 10.0),
    ]

    # Re-upserting the same type_id updates quantity/name, doesn't duplicate.
    storage.upsert_special_order_item(order_id, 12058, "Cruise Missile Launcher II", 6500.0)
    items = storage.list_special_order_items(order_id)
    assert len(items) == 2
    assert dict((t, q) for t, _n, q in items)[12058] == 6500.0

    storage.remove_special_order_item(order_id, 638)
    assert [t for t, _n, _q in storage.list_special_order_items(order_id)] == [12058]

    storage.update_special_order(order_id, {"status": "done"})
    assert storage.get_special_order(order_id)[3] == "done"

    storage.delete_special_order(order_id)
    assert storage.get_special_order(order_id) is None
    assert storage.list_special_order_items(order_id) == []


def test_list_special_orders_most_recent_first(tenant):
    first = storage.create_special_order(None, net_against_stock=False)
    second = storage.create_special_order(None, net_against_stock=False)
    # list_special_orders() returns raw DB types (uuid.UUID for order_id,
    # same "thin storage layer" convention as doctrine's list_doctrines) -
    # create_special_order's own str(...) return needs matching str()
    # here too, not a bug in the storage function itself.
    order_ids = [str(row[0]) for row in storage.list_special_orders()]
    assert order_ids.index(second) < order_ids.index(first)


def test_two_tenants_can_have_items_on_the_same_order_id_type_id_pair_without_colliding(tenant_pair):
    # special_order_items' PK is (tenant_id, order_id, type_id) - a
    # deliberately-colliding fake order_id (in reality always a per-tenant
    # gen_random_uuid(), never actually shared) proves RLS/PK-widening keeps
    # two tenants' rows independent even in this worst case, same spirit as
    # test_pg_composite_pk_tables.py's own upsert-collision tests.
    tenant_a, tenant_b = tenant_pair
    fake_order_id = "99999999-9999-9999-9999-999999999999"

    with storage.tenant_context(tenant_a):
        storage.upsert_special_order_item(fake_order_id, 34, "Tritanium", 100.0)
    with storage.tenant_context(tenant_b):
        storage.upsert_special_order_item(fake_order_id, 34, "Tritanium", 200.0)

    with storage.tenant_context(tenant_a):
        items_a = storage.list_special_order_items(fake_order_id)
    with storage.tenant_context(tenant_b):
        items_b = storage.list_special_order_items(fake_order_id)

    assert items_a == [(34, "Tritanium", 100.0)]
    assert items_b == [(34, "Tritanium", 200.0)]

    pg_helpers.wipe_tables("special_order_items")
