"""Tests for manual_stock's phase-3 location dimension (docs/
MANUAL_TRACKING_PLAN.md phase 3, decision 15) - storage.py's per-location
functions, Postgres-backed. See test_manual_stock_actions.py for
production/actions.py's do_* wrappers (unit-level, no Postgres needed)."""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

TYPE_ID = 34  # Tritanium
LOCATION_A = 1000000000001
LOCATION_B = 1000000000002


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("sde_types")
    yield


def _insert_sde_type(type_id: int, name: str) -> None:
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_types (type_id, type_name, published) VALUES (?, ?, 1) "
            "ON CONFLICT (type_id) DO UPDATE SET type_name = excluded.type_name",
            (type_id, name),
        )


def test_load_manual_stock_sums_across_locations(tenant):
    with storage.tenant_context(tenant):
        storage.upsert_manual_stock(TYPE_ID, 100, LOCATION_A)
        storage.upsert_manual_stock(TYPE_ID, 50, LOCATION_B)
        storage.upsert_manual_stock(TYPE_ID, 25, 0)

        assert storage.load_manual_stock() == {TYPE_ID: 175}


def test_upsert_manual_stock_different_locations_do_not_collide(tenant):
    with storage.tenant_context(tenant):
        storage.upsert_manual_stock(TYPE_ID, 100, LOCATION_A)
        storage.upsert_manual_stock(TYPE_ID, 50, LOCATION_B)
        storage.upsert_manual_stock(TYPE_ID, 150, LOCATION_A)  # updates location A only

        assert storage.manual_stock_at_location(TYPE_ID, LOCATION_A) == 150
        assert storage.manual_stock_at_location(TYPE_ID, LOCATION_B) == 50


def test_upsert_manual_stock_defaults_to_location_zero(tenant):
    with storage.tenant_context(tenant):
        storage.upsert_manual_stock(TYPE_ID, 42)  # no location_id passed
        assert storage.manual_stock_at_location(TYPE_ID, 0) == 42


def test_manual_stock_at_location_returns_zero_for_no_row(tenant):
    with storage.tenant_context(tenant):
        assert storage.manual_stock_at_location(TYPE_ID, LOCATION_A) == 0.0


def test_delete_manual_stock_removes_only_that_location(tenant):
    with storage.tenant_context(tenant):
        storage.upsert_manual_stock(TYPE_ID, 100, LOCATION_A)
        storage.upsert_manual_stock(TYPE_ID, 50, LOCATION_B)

        storage.delete_manual_stock(TYPE_ID, LOCATION_A)

        assert storage.manual_stock_at_location(TYPE_ID, LOCATION_A) == 0.0
        assert storage.manual_stock_at_location(TYPE_ID, LOCATION_B) == 50
        assert storage.load_manual_stock() == {TYPE_ID: 50}


def test_load_manual_stock_entries_returns_one_row_per_type_and_location(tenant):
    _insert_sde_type(TYPE_ID, "Tritanium")
    with storage.tenant_context(tenant):
        storage.upsert_manual_stock(TYPE_ID, 100, LOCATION_A)
        storage.upsert_manual_stock(TYPE_ID, 50, LOCATION_B)

        entries = storage.load_manual_stock_entries()

    assert sorted(entries, key=lambda e: e[2]) == [
        (TYPE_ID, "Tritanium", LOCATION_A, 100),
        (TYPE_ID, "Tritanium", LOCATION_B, 50),
    ]
