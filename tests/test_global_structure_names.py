"""Tests for storage.py's global_structure_names functions
(get_global_structure_names, upsert_global_structure_name) - see
docs/admin_schema.sql / docs/MANUAL_TRACKING_PLAN.md phase 2. Unscoped like
tool_grants (storage.connect_unscoped(), no ambient tenant) - no
storage.tenant_context wrapper appears anywhere in this file, which is
itself the "readable without a tenant set" proof (see that fixture's own
docstring: connect_unscoped never requires or sets app.tenant_id, and
Postgres would raise loudly if these functions accidentally used connect()
instead - see storage.connect_unscoped's own docstring).
"""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("global_structure_names")
    yield


def test_upsert_then_get_roundtrips():
    storage.upsert_global_structure_name(1000000000001, "Test Structure", 30000142)

    result = storage.get_global_structure_names([1000000000001])

    assert result == {1000000000001: ("Test Structure", 30000142)}


def test_get_empty_ids_returns_empty_dict():
    assert storage.get_global_structure_names([]) == {}


def test_get_missing_id_is_absent_not_none():
    result = storage.get_global_structure_names([999])
    assert 999 not in result


def test_upsert_overwrites_name_on_conflict():
    storage.upsert_global_structure_name(1000000000001, "Old Name", 30000142)
    storage.upsert_global_structure_name(1000000000001, "New Name", 30000142)

    result = storage.get_global_structure_names([1000000000001])

    assert result[1000000000001][0] == "New Name"


def test_upsert_coalesces_solar_system_id_when_not_given():
    """A later upsert with solar_system_id=None (a caller that only has the
    name, e.g. a bare re-resolution) must not blow away a system id an
    earlier call already captured - same COALESCE precedent as
    storage.set_cached_structure_name's own docstring."""
    storage.upsert_global_structure_name(1000000000001, "Test Structure", 30000142)
    storage.upsert_global_structure_name(1000000000001, "Test Structure", None)

    result = storage.get_global_structure_names([1000000000001])

    assert result[1000000000001] == ("Test Structure", 30000142)


def test_get_location_names_falls_back_to_global_cache():
    """get_location_names' third tier (after this tenant's own
    structure_names) - a structure another tenant already resolved is
    visible to this tenant too, without any ESI call of its own."""
    storage.upsert_global_structure_name(1000000000001, "Globally Known Structure", 30000142)

    tenant_id = storage.create_tenant("Test Tenant")
    with storage.tenant_context(tenant_id):
        names = storage.get_location_names([1000000000001])

    assert names == {1000000000001: "Globally Known Structure"}


def test_get_location_names_prefers_own_structure_names_over_global():
    storage.upsert_global_structure_name(1000000000001, "Global Name", 30000142)

    tenant_id = storage.create_tenant("Test Tenant")
    with storage.tenant_context(tenant_id):
        storage.set_cached_structure_name(1000000000001, "My Own Name", 30000142)
        names = storage.get_location_names([1000000000001])

    assert names == {1000000000001: "My Own Name"}
