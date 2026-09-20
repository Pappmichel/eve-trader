"""Phase 2 partitioned writes: NULL-id transition, A-does-not-delete-B,
failed-fetch skip, and the age-limit clear (docs/ESI_ACCESS_PLAN.md).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import storage
from eve_trader.esi_data.stale import (
    DEFAULT_STALE_CLEAR_MULTIPLES,
    clear_stale_owner_kind,
)

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

TYPE_ID = 34
LOCATION_ID = 1000000000001
ALICE_ID = 1001
BOB_ID = 1002


@pytest.fixture(autouse=True)
def _wipe(_apply_doctrine_schema, _apply_esi_access_schema):
    pg_helpers.wipe_tables(
        "character_assets", "corp_assets",
        "character_industry_jobs", "corp_industry_jobs",
        "character_blueprints", "corp_blueprints",
        "character_sell_orders",
        "doctrine_contracts", "doctrine_contract_items", "doctrine_contract_deviations",
        "esi_freshness",
        "character_slots",
    )
    yield


def _asset(item_id, qty, owner_name, type_id=TYPE_ID, location_id=LOCATION_ID):
    return (item_id, type_id, location_id, "Hangar", qty, 0, owner_name)


def _owner_ids(table, item_id):
    with storage.connect() as conn:
        return conn.execute(
            f"SELECT owner_name, quantity, owner_character_id, owner_corporation_id "
            f"FROM {table} WHERE item_id = ?",
            (item_id,),
        ).fetchall()


def test_null_owner_id_partitioned_replace_does_not_pk_collide_or_touch_other_owner(tenant):
    # Realistic pre-deploy state: Phase 1 backfill left unmatched (and all
    # pre-column) rows with NULL owner ids and only owner_name. A naive
    # DELETE WHERE owner_character_id = Alice would strand Alice's NULL-id
    # row; the insert then UniqueViolation on (item_id, owner_name).
    storage.replace_assets("character_assets", [
        _asset(1, 10, "Alice"),
        _asset(2, 20, "Bob"),
    ])
    with storage.connect() as conn:
        nulls = conn.execute(
            "SELECT count(*) FROM character_assets WHERE owner_character_id IS NULL"
        ).fetchone()[0]
    assert nulls == 2

    storage.replace_assets(
        "character_assets",
        [_asset(1, 99, "Alice")],
        owner_character_id=ALICE_ID, owner_name="Alice",
    )

    alice = _owner_ids("character_assets", 1)
    bob = _owner_ids("character_assets", 2)
    assert len(alice) == 1
    assert alice[0][0] == "Alice" and alice[0][1] == 99 and alice[0][2] == ALICE_ID
    assert len(bob) == 1
    assert bob[0][0] == "Bob" and bob[0][1] == 20 and bob[0][2] is None


def test_production_replace_of_a_does_not_delete_b(tenant):
    # Done-when: Production-shaped rows for A and Doctrine-shaped rows for B
    # in the shared table; a Production-only replace of A leaves B.
    storage.replace_assets(
        "character_assets", [_asset(1, 10, "Alice")],
        owner_character_id=ALICE_ID, owner_name="Alice",
    )
    storage.replace_assets(
        "character_assets", [_asset(2, 20, "Bob")],
        owner_character_id=BOB_ID, owner_name="Bob",
    )
    storage.replace_assets(
        "character_assets", [_asset(1, 50, "Alice")],
        owner_character_id=ALICE_ID, owner_name="Alice",
    )
    names = {r[0]: r[1] for r in _owner_ids("character_assets", 1) + _owner_ids("character_assets", 2)}
    assert names == {"Alice": 50, "Bob": 20}


def test_failed_replace_leaves_owner_rows_intact(tenant):
    # Callers skip replace on a failed fetch (decision 6). Not calling
    # replace for Alice must leave her rows; Bob's successful replace must
    # not take them with it.
    storage.replace_assets(
        "character_assets", [_asset(1, 10, "Alice")],
        owner_character_id=ALICE_ID, owner_name="Alice",
    )
    storage.replace_assets(
        "character_assets", [_asset(2, 20, "Bob")],
        owner_character_id=BOB_ID, owner_name="Bob",
    )
    storage.replace_assets(
        "character_assets", [_asset(2, 8, "Bob")],
        owner_character_id=BOB_ID, owner_name="Bob",
    )
    assert _owner_ids("character_assets", 1)[0][1] == 10
    assert _owner_ids("character_assets", 2)[0][1] == 8


def test_successful_empty_fetch_clears_only_that_owner(tenant):
    storage.replace_assets(
        "character_assets", [_asset(1, 10, "Alice")],
        owner_character_id=ALICE_ID, owner_name="Alice",
    )
    storage.replace_assets(
        "character_assets", [_asset(2, 20, "Bob")],
        owner_character_id=BOB_ID, owner_name="Bob",
    )
    storage.replace_assets(
        "character_assets", [],
        owner_character_id=ALICE_ID, owner_name="Alice",
    )
    assert _owner_ids("character_assets", 1) == []
    assert _owner_ids("character_assets", 2)[0][1] == 20


def test_age_limit_clear_removes_stale_owner_only(tenant):
    storage.replace_assets(
        "character_assets", [_asset(1, 10, "Alice")],
        owner_character_id=ALICE_ID, owner_name="Alice",
    )
    storage.replace_assets(
        "character_assets", [_asset(2, 20, "Bob")],
        owner_character_id=BOB_ID, owner_name="Bob",
    )
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    stale_at = now - timedelta(hours=DEFAULT_STALE_CLEAR_MULTIPLES * 4 + 1)
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_freshness (owner_type, owner_id, data_kind, last_success_at) "
            "VALUES ('character', ?, 'assets', ?)",
            (ALICE_ID, stale_at),
        )
        conn.execute(
            "INSERT INTO esi_freshness (owner_type, owner_id, data_kind, last_success_at) "
            "VALUES ('character', ?, 'assets', ?)",
            (BOB_ID, now),
        )
    cleared = clear_stale_owner_kind(
        "character", ALICE_ID, "assets",
        tier_interval_hours=4, now=now, owner_name="Alice",
    )
    assert cleared is True
    assert _owner_ids("character_assets", 1) == []
    assert _owner_ids("character_assets", 2)[0][1] == 20


def test_age_limit_clear_noops_without_freshness_or_inside_grace(tenant):
    storage.replace_assets(
        "character_assets", [_asset(1, 10, "Alice")],
        owner_character_id=ALICE_ID, owner_name="Alice",
    )
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert clear_stale_owner_kind(
        "character", ALICE_ID, "assets", tier_interval_hours=4, now=now,
    ) is False
    assert _owner_ids("character_assets", 1)[0][1] == 10

    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_freshness (owner_type, owner_id, data_kind, last_success_at) "
            "VALUES ('character', ?, 'assets', ?)",
            (ALICE_ID, now - timedelta(hours=1)),
        )
    assert clear_stale_owner_kind(
        "character", ALICE_ID, "assets", tier_interval_hours=4, now=now,
    ) is False
    assert _owner_ids("character_assets", 1)[0][1] == 10


def test_age_limit_clear_does_not_touch_character_slots(tenant):
    storage.replace_character_slots([("Alice", 5, 3, 2)])
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_freshness (owner_type, owner_id, data_kind, last_success_at) "
            "VALUES ('character', ?, 'skills', ?)",
            (ALICE_ID, now - timedelta(hours=1000)),
        )
    assert clear_stale_owner_kind(
        "character", ALICE_ID, "skills", tier_interval_hours=4, now=now,
    ) is False
    rows = storage.load_character_slots()
    assert rows[0][0] == "Alice"


def test_null_owner_id_jobs_use_installer_id_without_touching_other_owner(tenant):
    # replace_industry_jobs without owner ids groups by installer_id and
    # stamps the id — so the pre-deploy NULL-id state has to be seeded
    # with a raw INSERT, not via the write path.
    with storage.connect() as conn:
        conn.executemany(
            "INSERT INTO character_industry_jobs (job_id, activity_id, blueprint_type_id, "
            "product_type_id, runs, output_location_id, status, end_date, start_date, "
            "installer_id, installer_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, 1, 34, 35, 1, LOCATION_ID, "active", "", "", ALICE_ID, "Alice"),
                (2, 1, 34, 35, 1, LOCATION_ID, "active", "", "", BOB_ID, "Bob"),
            ],
        )
    storage.replace_industry_jobs(
        "character_industry_jobs",
        [(1, 1, 34, 35, 9, LOCATION_ID, "active", "", "", ALICE_ID, "Alice")],
        owner_character_id=ALICE_ID,
    )
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT job_id, runs, installer_id, owner_character_id FROM character_industry_jobs "
            "ORDER BY job_id"
        ).fetchall()
    by_id = {r[0]: r for r in rows}
    assert by_id[1][1] == 9 and by_id[1][3] == ALICE_ID
    assert by_id[2][1] == 1 and by_id[2][2] == BOB_ID and by_id[2][3] is None


def test_unqualified_asset_delete_is_refused(tenant):
    with pytest.raises(ValueError, match="unqualified DELETE"):
        storage.replace_assets("character_assets", [])
    with pytest.raises(ValueError, match="unqualified DELETE"):
        storage.delete_owner_snapshot_rows("character_assets")
