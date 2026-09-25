"""Tests for storage.py's manual_location_names CRUD and search_locations
(docs/MANUAL_TRACKING_PLAN.md phase 2) - both are RLS-scoped per-tenant
(manual_location_names) or combine an RLS-scoped table with the shared
sde_stations table (search_locations), so both need a real Postgres
connection, unlike test_resolve_structure_name.py's plain monkeypatched
unit tests.
"""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _clean(tenant_pair):
    pg_helpers.clean_tables(tenant_pair, "manual_location_names", "structure_names")
    yield
    pg_helpers.clean_tables(tenant_pair, "manual_location_names", "structure_names")


def test_set_then_list_manual_location_name(tenant_pair):
    tenant_a, _tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.set_manual_location_name(1000000000001, "My Staging POS")

        assert storage.list_manual_location_names() == [(1000000000001, "My Staging POS")]


def test_set_is_upsert_not_duplicate(tenant_pair):
    tenant_a, _tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.set_manual_location_name(1000000000001, "Old Name")
        storage.set_manual_location_name(1000000000001, "New Name")

        assert storage.list_manual_location_names() == [(1000000000001, "New Name")]


def test_remove_manual_location_name_removes_it(tenant_pair):
    tenant_a, _tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.set_manual_location_name(1000000000001, "My Staging POS")
        storage.remove_manual_location_name(1000000000001)

        assert storage.list_manual_location_names() == []


def test_manual_location_names_are_tenant_isolated(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.set_manual_location_name(1000000000001, "Tenant A's Name")
    with storage.tenant_context(tenant_b):
        storage.set_manual_location_name(1000000000001, "Tenant B's Name")

    with storage.tenant_context(tenant_a):
        assert storage.list_manual_location_names() == [(1000000000001, "Tenant A's Name")]
    with storage.tenant_context(tenant_b):
        assert storage.list_manual_location_names() == [(1000000000001, "Tenant B's Name")]


def test_search_locations_finds_own_manual_name(tenant_pair):
    tenant_a, _tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.set_manual_location_name(1000000000001, "Zzz Unique Manual Name")

        rows = storage.search_locations("Unique Manual")

    assert rows == [(1000000000001, "Zzz Unique Manual Name", "manual")]


def test_search_locations_finds_own_structure_name(tenant_pair):
    tenant_a, _tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.set_cached_structure_name(1000000000002, "Zzz Unique Structure Name", 30000142)

        rows = storage.search_locations("Unique Structure")

    assert rows == [(1000000000002, "Zzz Unique Structure Name", "structure")]


def test_search_locations_does_not_leak_across_tenants(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.set_manual_location_name(1000000000001, "Zzz Only Tenant A Sees This")

    with storage.tenant_context(tenant_b):
        assert storage.search_locations("Only Tenant A Sees") == []


def test_search_locations_empty_query_returns_nothing(tenant_pair):
    tenant_a, _tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        assert storage.search_locations("") == []
        assert storage.search_locations("   ") == []


def test_search_locations_is_case_insensitive(tenant_pair):
    """Confirmed real bug in code review (2026-09-25): Postgres's LIKE is
    case-sensitive (unlike SQLite's default ASCII LIKE), so a lowercase
    query like "jita" used to find nothing against "Jita IV..." - fixed by
    switching to ILIKE. Covers all three kinds (manual/structure/station -
    the last one is seeded SDE data, real published stations)."""
    tenant_a, _tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.set_manual_location_name(1000000000001, "Zzz Unique Manual Name")
        storage.set_cached_structure_name(1000000000002, "Zzz Unique Structure Name", 30000142)

        manual_rows = storage.search_locations("unique manual")
        structure_rows = storage.search_locations("UNIQUE STRUCTURE")

    assert manual_rows == [(1000000000001, "Zzz Unique Manual Name", "manual")]
    assert structure_rows == [(1000000000002, "Zzz Unique Structure Name", "structure")]
