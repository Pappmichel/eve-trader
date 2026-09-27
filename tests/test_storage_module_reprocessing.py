"""Tests for the Module Reprocessing Import tool's storage additions:
module_reprocessing_candidate_types (the real SQL exclusion filter - category_
id/meta_group_id/rig-slot, see that function's own docstring) and the
shortlist/snapshot round-trip. Mirrors test_storage_refining.py's own pattern
for the Ore Shortlist.

Depends on docs/doctrine_schema.sql too, not just this tool's own schema file
- that's the file that creates the shared sde_type_slots table (typeID ->
fitting slot, no tenant_id, same "shared sde_* reference table" bucket as
sde_types/sde_groups/sde_type_materials - see that table's own comment in
doctrine_schema.sql), which this tool's own candidate query needs to exclude
rigs. A shared reference table's own origin schema file doesn't matter to
anything that reads it - same reasoning storage.py itself never cares which
tool "owns" sde_type_materials.
"""
from pathlib import Path

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_phase2_schema, tenant  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_DOCTRINE_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "doctrine_schema.sql"
_MODULE_REPROCESSING_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "module_reprocessing_schema.sql"

# Real SDE category ids (production/constants.py's MODULE_CATEGORY_ID/
# DRONE_CATEGORY_ID) and meta-group ids (Tech II=2, Storyline=3, Faction=4,
# Officer=5, Deadspace=6 - see storage.get_sde_type's own docstring).
MODULE_CATEGORY_ID = 7
DRONE_CATEGORY_ID = 18
TECH_II_META_GROUP_ID = 2
FACTION_META_GROUP_ID = 4


@pytest.fixture(scope="session", autouse=True)
def _apply_module_reprocessing_schema(_apply_phase1_schema, _apply_phase2_schema):
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_DOCTRINE_SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute(_MODULE_REPROCESSING_SCHEMA_SQL.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _wipe():
    # sde_types/sde_groups/sde_type_slots are shared tables (no tenant_id at
    # all) - wipe before each test so a leftover row from an earlier test
    # can never affect this one. module_reprocessing_shortlist(_snapshot) ARE
    # tenant-scoped - the `tenant` fixture already isolates those per-test.
    pg_helpers.wipe_tables("sde_types", "sde_groups", "sde_type_slots")
    storage.get_sde_type.cache_clear()
    yield


def _insert_group(group_id, category_id, group_name="Test Group"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_groups (group_id, category_id, group_name) VALUES (?, ?, ?)",
            (group_id, category_id, group_name),
        )


def _insert_type(type_id, name, group_id, volume=0.01, published=1, meta_group_id=None):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO sde_types (type_id, group_id, type_name, volume, published, market_group_id, "
            "meta_level, meta_group_id, portion_size) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 1)",
            (type_id, group_id, name, volume, published, meta_group_id),
        )


def _insert_slot(type_id, slot):
    with storage.connect() as conn:
        conn.execute("INSERT INTO sde_type_slots (type_id, slot) VALUES (?, ?)", (type_id, slot))


# --------------------------------------- module_reprocessing_candidate_types
def test_includes_plain_t1_modules_and_drones(tenant):
    _insert_group(1, MODULE_CATEGORY_ID, "Projectile Weapon")
    _insert_group(2, DRONE_CATEGORY_ID, "Combat Drone")
    _insert_type(100, "200mm AutoCannon I", group_id=1)
    _insert_type(200, "Hobgoblin I", group_id=2)

    rows = storage.module_reprocessing_candidate_types()

    assert {r[0] for r in rows} == {100, 200}


def test_excludes_other_categories(tenant):
    _insert_group(1, MODULE_CATEGORY_ID, "Projectile Weapon")
    _insert_group(3, 6, "Frigate")  # category 6 = Ship, not Module/Drone
    _insert_type(100, "200mm AutoCannon I", group_id=1)
    _insert_type(300, "Rifter", group_id=3)

    rows = storage.module_reprocessing_candidate_types()

    assert [r[0] for r in rows] == [100]


def test_excludes_tech_ii_faction_and_other_high_meta_groups(tenant):
    _insert_group(1, MODULE_CATEGORY_ID, "Projectile Weapon")
    _insert_type(100, "200mm AutoCannon I", group_id=1, meta_group_id=None)  # Tech I
    _insert_type(101, "200mm AutoCannon II", group_id=1, meta_group_id=TECH_II_META_GROUP_ID)
    _insert_type(102, "Republic Fleet AutoCannon", group_id=1, meta_group_id=FACTION_META_GROUP_ID)

    rows = storage.module_reprocessing_candidate_types()

    assert [r[0] for r in rows] == [100]


def test_excludes_rigs_via_sde_type_slots(tenant):
    _insert_group(1, MODULE_CATEGORY_ID, "Projectile Weapon")
    _insert_type(100, "200mm AutoCannon I", group_id=1)
    _insert_type(400, "Small Anti-Explosive Pump I", group_id=1)
    _insert_slot(400, "rig")

    rows = storage.module_reprocessing_candidate_types()

    assert [r[0] for r in rows] == [100]


def test_excludes_unpublished(tenant):
    _insert_group(1, MODULE_CATEGORY_ID, "Projectile Weapon")
    _insert_type(100, "200mm AutoCannon I", group_id=1, published=0)

    assert storage.module_reprocessing_candidate_types() == []


def test_type_ids_filter_narrows_the_scan(tenant):
    _insert_group(1, MODULE_CATEGORY_ID, "Projectile Weapon")
    _insert_type(100, "200mm AutoCannon I", group_id=1)
    _insert_type(101, "150mm AutoCannon I", group_id=1)

    rows = storage.module_reprocessing_candidate_types(type_ids=[100])

    assert [r[0] for r in rows] == [100]


def test_type_ids_filter_empty_list_short_circuits(tenant):
    assert storage.module_reprocessing_candidate_types(type_ids=[]) == []


# --------------------------------------------------------------- Shortlist
def test_upsert_and_load_module_reprocessing_shortlist_round_trips(tenant):
    storage.upsert_module_reprocessing_shortlist([(100, "200mm AutoCannon I")])

    rows = storage.load_module_reprocessing_shortlist()

    assert rows == [(100, "200mm AutoCannon I", True)]  # active defaults true on a brand-new row


def test_upsert_module_reprocessing_shortlist_updates_name_on_conflict(tenant):
    storage.upsert_module_reprocessing_shortlist([(100, "200mm AutoCannon I")])
    storage.upsert_module_reprocessing_shortlist([(100, "200mm AutoCannon II")])

    rows = storage.load_module_reprocessing_shortlist()

    assert rows == [(100, "200mm AutoCannon II", True)]


def test_upsert_module_reprocessing_shortlist_never_resets_active_on_conflict(tenant):
    """Confirmed real requirement (2026-09-27): Refresh Shortlist now
    auto-re-discovers and re-upserts every candidate that still clears the
    margin/profit bar on every run - without this, a user's manual
    Deactivate would be silently undone the very next time that same item
    is re-discovered. Mirrors station_trading's own upsert_station_trading_
    shortlist test for the identical requirement."""
    storage.upsert_module_reprocessing_shortlist([(100, "200mm AutoCannon I")])
    storage.deactivate_module_reprocessing_shortlist_items([100])

    storage.upsert_module_reprocessing_shortlist([(100, "200mm AutoCannon I")])  # re-discovered

    assert storage.load_module_reprocessing_shortlist() == [(100, "200mm AutoCannon I", False)]


def test_deactivate_module_reprocessing_shortlist_items(tenant):
    storage.upsert_module_reprocessing_shortlist([(100, "200mm AutoCannon I")])

    storage.deactivate_module_reprocessing_shortlist_items([100])

    assert storage.load_module_reprocessing_shortlist() == [(100, "200mm AutoCannon I", False)]


def test_deactivate_module_reprocessing_shortlist_items_empty_list_is_a_no_op(tenant):
    storage.deactivate_module_reprocessing_shortlist_items([])  # must not raise


def test_activate_module_reprocessing_shortlist_items(tenant):
    storage.upsert_module_reprocessing_shortlist([(100, "200mm AutoCannon I")])
    storage.deactivate_module_reprocessing_shortlist_items([100])

    storage.activate_module_reprocessing_shortlist_items([100])

    assert storage.load_module_reprocessing_shortlist() == [(100, "200mm AutoCannon I", True)]


def test_activate_module_reprocessing_shortlist_items_empty_list_is_a_no_op(tenant):
    storage.activate_module_reprocessing_shortlist_items([])  # must not raise


def test_save_and_read_latest_module_reprocessing_snapshot(tenant):
    row = (100, "200mm AutoCannon I", True, 0.01, 1.9, 0.5, 473.15, 0.0, 473.15, 50.0, 471.24, 247.0, 47124.0,
           "Import")
    storage.save_module_reprocessing_shortlist_snapshot([row], "2026-09-27T00:00:00")

    df = storage.latest_module_reprocessing_snapshot()

    assert len(df) == 1
    assert df.iloc[0]["item"] == "200mm AutoCannon I"
    assert df.iloc[0]["decision"] == "Import"


def test_latest_module_reprocessing_snapshot_only_returns_the_newest_run(tenant):
    old_row = (100, "200mm AutoCannon I", True, 0.01, 1.9, 0.5, 473.15, 0.0, 473.15, 50.0, 471.24, 247.0,
               47124.0, "Skip")
    new_row = (100, "200mm AutoCannon I", True, 0.01, 1.9, 0.5, 473.15, 0.0, 473.15, 50.0, 471.24, 247.0,
               47124.0, "Import")
    storage.save_module_reprocessing_shortlist_snapshot([old_row], "2026-09-27T00:00:00")
    storage.save_module_reprocessing_shortlist_snapshot([new_row], "2026-09-27T01:00:00")

    df = storage.latest_module_reprocessing_snapshot()

    assert len(df) == 1
    assert df.iloc[0]["decision"] == "Import"


def test_latest_module_reprocessing_snapshot_empty_before_any_run(tenant):
    import pandas as pd
    assert storage.latest_module_reprocessing_snapshot().empty
    assert isinstance(storage.latest_module_reprocessing_snapshot(), pd.DataFrame)


def test_module_reprocessing_shortlist_is_tenant_isolated(tenant):
    import uuid
    storage.upsert_module_reprocessing_shortlist([(100, "200mm AutoCannon I")])

    with storage.tenant_context(str(uuid.uuid4())):
        assert storage.load_module_reprocessing_shortlist() == []
        storage.upsert_module_reprocessing_shortlist([(200, "Hobgoblin I")])

    assert storage.load_module_reprocessing_shortlist() == [(100, "200mm AutoCannon I", True)]
