"""Postgres tests for sorting_intake_sources CRUD, RLS isolation, and
storage.load_tenant_character_names.
"""
from pathlib import Path

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import (
    _apply_admin_schema, _apply_phase1_schema, _apply_phase2_schema, _apply_phase3_schema,  # noqa: F401
    tenant, tenant_pair,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_SORTING_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "sorting_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_sorting_schema(_apply_phase1_schema, _apply_phase2_schema):
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_SORTING_SCHEMA_SQL.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _wipe_sorting_sources():
    pg_helpers.wipe_tables("sorting_intake_sources")
    yield


def test_add_and_load_sorting_intake_sources(tenant):
    source_id = storage.add_sorting_intake_source("character", "Hangar", character_name="Alice", label="Alice hangar")
    storage.add_sorting_intake_source("corp", "CorpSAG3", character_name=None, label=None)

    rows = storage.load_sorting_intake_sources()
    by_kind = {r[1]: r for r in rows}
    assert by_kind["character"][0] == source_id
    assert by_kind["character"][2] == "Alice"
    assert by_kind["character"][3] == "Hangar"
    assert by_kind["character"][4] == "Alice hangar"
    assert by_kind["corp"][2] is None
    assert by_kind["corp"][3] == "CorpSAG3"


def test_remove_sorting_intake_source(tenant):
    source_id = storage.add_sorting_intake_source("corp", "CorpSAG1")
    storage.remove_sorting_intake_source(source_id)
    assert storage.load_sorting_intake_sources() == []


def test_sorting_intake_source_of_tenant_a_is_invisible_to_tenant_b(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with storage.tenant_context(tenant_a):
        storage.add_sorting_intake_source("corp", "CorpSAG1", label="A only")
        assert len(storage.load_sorting_intake_sources()) == 1

    with storage.tenant_context(tenant_b):
        assert storage.load_sorting_intake_sources() == []
        storage.add_sorting_intake_source("corp", "CorpSAG2", label="B only")
        rows = storage.load_sorting_intake_sources()
        assert len(rows) == 1
        assert rows[0][4] == "B only"

    with storage.tenant_context(tenant_a):
        rows = storage.load_sorting_intake_sources()
        assert len(rows) == 1
        assert rows[0][4] == "A only"


def test_load_tenant_character_names_returns_only_this_tenants_named_characters(tenant):
    # One character per tenant (tenant_registry_entries.tenant_id UNIQUE -
    # see docs/admin_schema.sql). A nameless registration on a third tenant
    # plus a named character on another tenant must not leak into this one.
    other = storage.create_tenant("Other sorting-names tenant")
    nameless = storage.create_tenant("Nameless sorting-names tenant")
    storage.add_tenant_registry_entry(tenant, 9101, character_name="Alice")
    storage.add_tenant_registry_entry(other, 9201, character_name="Carol")
    storage.add_tenant_registry_entry(nameless, 9301, character_name=None)

    names = storage.load_tenant_character_names()
    assert names == ["Alice"]
