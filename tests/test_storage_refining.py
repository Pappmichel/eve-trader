"""Tests for the SDE storage additions GitHub issue #90 needs: sde_types.
portion_size, sde_type_materials/storage.get_type_materials, and
refining/engine.py's apply_reprocessing_yield (the portion-size-rounding
consumer of both). Mirrors test_storage_sde_queries.py's own pattern for the
existing sde_* shared tables.
"""
from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.refining.engine import apply_reprocessing_yield

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_phase2_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_REFINING_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "refining_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_refining_schema(_apply_phase1_schema, _apply_phase2_schema):
    """docs/refining_schema.sql, applied once per session via the owner role -
    same pattern as test_doctrine_storage.py's own _apply_doctrine_schema.
    Depends on _apply_phase2_schema (not just phase1) since this file ALTERs
    tenant_settings' scope CHECK constraint, which phase2_schema.sql is what
    creates."""
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_REFINING_SCHEMA_SQL.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _wipe():
    # sde_types/sde_type_materials/sde_groups are shared tables (no tenant_id
    # at all) - wipe before each test so a leftover row from an earlier test
    # can never affect this one, regardless of which ids happen to be reused.
    # ore_shortlist/ore_shortlist_snapshot ARE tenant-scoped (composite-PK/
    # no-PK buckets respectively, see refining_schema.sql) - the `tenant`
    # fixture already isolates those per-test via a fresh random tenant_id,
    # no wipe needed for them.
    pg_helpers.wipe_tables("sde_types", "sde_type_materials", "sde_groups")
    storage.get_sde_type.cache_clear()
    storage.get_type_materials.cache_clear()
    yield


def _insert_type(type_id, name="Test Type", portion_size=None, group_id=1):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_types (type_id, group_id, type_name, volume, published, market_group_id, "
            "meta_level, meta_group_id, portion_size) VALUES (?, ?, ?, 1.0, 1, NULL, NULL, NULL, ?)",
            (type_id, group_id, name, portion_size),
        )


def _insert_group(group_id, category_id, group_name):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_groups (group_id, category_id, group_name) VALUES (?, ?, ?)",
            (group_id, category_id, group_name),
        )


def _insert_material(type_id, material_type_id, quantity):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_type_materials (type_id, material_type_id, quantity) VALUES (?, ?, ?)",
            (type_id, material_type_id, quantity),
        )


def test_get_portion_size_reads_the_new_column(tenant):
    _insert_type(34, "Compressed Veldspar", portion_size=100)

    assert storage.get_portion_size(34) == 100


def test_get_portion_size_none_for_unknown_type(tenant):
    assert storage.get_portion_size(999999) is None


def test_get_type_materials_round_trips(tenant):
    _insert_type(34, portion_size=100)
    _insert_material(34, 35, 415.0)   # Tritanium
    _insert_material(34, 36, 41.0)    # Pyerite

    materials = storage.get_type_materials(34)

    assert sorted(materials) == [(35, 415.0), (36, 41.0)]


def test_apply_reprocessing_yield_rounds_down_to_whole_portions(tenant):
    # 250 units at portion_size=100 -> only 2 whole portions count, the
    # leftover 50 yields nothing (exact whole-portion batch rounding, not a
    # continuous 2.5x approximation - confirmed with the user during planning).
    _insert_type(34, portion_size=100)
    _insert_material(34, 35, 415.0)

    output = apply_reprocessing_yield(34, 250, 1.0)

    assert output == {35: 830}  # 2 portions x 415 x 100% yield, floored


def test_apply_reprocessing_yield_floors_each_material_independently(tenant):
    _insert_type(34, portion_size=100)
    _insert_material(34, 35, 415.0)
    _insert_material(34, 36, 41.0)

    output = apply_reprocessing_yield(34, 100, 0.906)

    assert output == {35: int(415 * 0.906), 36: int(41 * 0.906)}


def test_apply_reprocessing_yield_below_one_portion_yields_nothing(tenant):
    _insert_type(34, portion_size=100)
    _insert_material(34, 35, 415.0)

    assert apply_reprocessing_yield(34, 99, 1.0) == {}


def test_apply_reprocessing_yield_unknown_type_yields_nothing(tenant):
    assert apply_reprocessing_yield(999999, 1000, 1.0) == {}


def test_replace_sde_data_clears_and_reloads_type_materials(tenant):
    storage.replace_sde_data(
        types=[(34, 1, "Compressed Veldspar", 0.01, 1, None, None, None, 100)],
        groups=[], market_groups=[], blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        type_materials=[(34, 35, 415.0), (34, 36, 41.0)],
    )

    assert storage.get_portion_size(34) == 100
    assert sorted(storage.get_type_materials(34)) == [(35, 415.0), (36, 41.0)]

    # A second refresh with a different snapshot wholesale-replaces, not merges.
    storage.replace_sde_data(
        types=[(34, 1, "Compressed Veldspar", 0.01, 1, None, None, None, 100)],
        groups=[], market_groups=[], blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        type_materials=[(34, 35, 420.0)],
    )
    assert storage.get_type_materials(34) == [(35, 420.0)]


def test_sde_row_counts_includes_type_materials(tenant):
    _insert_type(34, portion_size=100)
    _insert_material(34, 35, 415.0)

    counts = storage.sde_row_counts()

    assert counts["sde_type_materials"] == 1


# ------------------------------------------------ Ore Shortlist (GitHub issue #91)
def test_load_ore_ice_candidate_types_filters_to_compressed_ore_groups(tenant):
    _insert_group(1000, 25, "Compressed Veldspar")
    _insert_group(1001, 25, "Veldspar")  # raw ore - must NOT be included
    _insert_group(1002, 7, "Compressed Something")  # wrong category (Module, not Ore) - must NOT be included
    _insert_type(34, "Compressed Veldspar", portion_size=100, group_id=1000)
    _insert_type(1230, "Veldspar", portion_size=100, group_id=1001)
    _insert_type(9999, "Compressed Something", portion_size=1, group_id=1002)

    rows = storage.load_ore_ice_candidate_types()

    assert [r[0] for r in rows] == [34]
    assert rows[0][1] == "Compressed Veldspar"
    assert rows[0][3] == "Compressed Veldspar"


def test_load_ore_ice_candidate_types_excludes_unpublished(tenant):
    _insert_group(1000, 25, "Compressed Veldspar")
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_types (type_id, group_id, type_name, volume, published, market_group_id, "
            "meta_level, meta_group_id, portion_size) VALUES (34, 1000, 'Compressed Veldspar', 0.01, 0, "
            "NULL, NULL, NULL, 100)",
        )
    assert storage.load_ore_ice_candidate_types() == []


def test_upsert_and_load_ore_shortlist_round_trips(tenant):
    storage.upsert_ore_shortlist([(34, "Compressed Veldspar", "Veldspar", False, True)])

    rows = storage.load_ore_shortlist()

    assert rows == [(34, "Compressed Veldspar", "Veldspar", False, True)]


def test_upsert_ore_shortlist_updates_existing_row_on_conflict(tenant):
    storage.upsert_ore_shortlist([(34, "Compressed Veldspar", "Veldspar", False, True)])
    storage.upsert_ore_shortlist([(34, "Compressed Veldspar", "Veldspar", False, False)])

    rows = storage.load_ore_shortlist()

    assert rows == [(34, "Compressed Veldspar", "Veldspar", False, False)]


def test_deactivate_ore_shortlist_items(tenant):
    storage.upsert_ore_shortlist([(34, "Compressed Veldspar", "Veldspar", False, True)])

    storage.deactivate_ore_shortlist_items([34])

    assert storage.load_ore_shortlist() == [(34, "Compressed Veldspar", "Veldspar", False, False)]


def test_deactivate_ore_shortlist_items_empty_list_is_a_no_op(tenant):
    storage.deactivate_ore_shortlist_items([])  # must not raise


def test_activate_ore_shortlist_items(tenant):
    storage.upsert_ore_shortlist([(34, "Compressed Veldspar", "Veldspar", False, False)])

    storage.activate_ore_shortlist_items([34])

    assert storage.load_ore_shortlist() == [(34, "Compressed Veldspar", "Veldspar", False, True)]


def test_activate_ore_shortlist_items_empty_list_is_a_no_op(tenant):
    storage.activate_ore_shortlist_items([])  # must not raise


def test_save_and_read_latest_ore_snapshot(tenant):
    row = (34, "Compressed Veldspar", "Veldspar", False, True, 0.01, 1.9, 0.5, 1958.8, 0.0, 1958.8, 5000.0,
           17.67, 9.23, 1767.0, "Import")
    storage.save_ore_shortlist_snapshot([row], "2026-08-22T00:00:00")

    df = storage.latest_ore_snapshot()

    assert len(df) == 1
    assert df.iloc[0]["item"] == "Compressed Veldspar"
    assert df.iloc[0]["decision"] == "Import"


def test_latest_ore_snapshot_only_returns_the_newest_run(tenant):
    old_row = (34, "Compressed Veldspar", "Veldspar", False, True, 0.01, 1.9, 0.5, 1958.8, 0.0, 1958.8, 5000.0,
               17.67, 9.23, 1767.0, "Skip")
    new_row = (34, "Compressed Veldspar", "Veldspar", False, True, 0.01, 1.9, 0.5, 1958.8, 0.0, 1958.8, 5000.0,
               17.67, 9.23, 1767.0, "Import")
    storage.save_ore_shortlist_snapshot([old_row], "2026-08-22T00:00:00")
    storage.save_ore_shortlist_snapshot([new_row], "2026-08-22T01:00:00")

    df = storage.latest_ore_snapshot()

    assert len(df) == 1
    assert df.iloc[0]["decision"] == "Import"


def test_latest_ore_snapshot_empty_before_any_run(tenant):
    import pandas as pd
    assert storage.latest_ore_snapshot().empty
    assert isinstance(storage.latest_ore_snapshot(), pd.DataFrame)


# --------------------------------------- Mineral Shopping List (GitHub issue #93)
def test_mineral_requirements_round_trip(tenant):
    storage.replace_mineral_requirements([(34, "Tritanium", 1_000_000.0), (35, "Pyerite", 250_000.5)])

    rows = storage.load_mineral_requirements()

    # Name-ordered, so Pyerite comes first regardless of insert order.
    assert rows == [(35, "Pyerite", 250_000.5), (34, "Tritanium", 1_000_000.0)]


def test_replace_mineral_requirements_drops_rows_no_longer_in_the_list(tenant):
    storage.replace_mineral_requirements([(34, "Tritanium", 100.0), (35, "Pyerite", 200.0)])

    storage.replace_mineral_requirements([(34, "Tritanium", 150.0)])

    assert storage.load_mineral_requirements() == [(34, "Tritanium", 150.0)]


def test_replace_mineral_requirements_with_an_empty_list_clears_everything(tenant):
    storage.replace_mineral_requirements([(34, "Tritanium", 100.0)])

    storage.replace_mineral_requirements([])

    assert storage.load_mineral_requirements() == []


def test_load_mineral_requirements_empty_before_anything_is_saved(tenant):
    assert storage.load_mineral_requirements() == []


def test_mineral_requirements_are_tenant_isolated(tenant):
    import uuid
    storage.replace_mineral_requirements([(34, "Tritanium", 100.0)])

    with storage.tenant_context(str(uuid.uuid4())):
        assert storage.load_mineral_requirements() == []
        storage.replace_mineral_requirements([(35, "Pyerite", 7.0)])

    assert storage.load_mineral_requirements() == [(34, "Tritanium", 100.0)]
