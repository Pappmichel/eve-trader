"""Tests for sqlite_migration.py - the one-time SQLite->Postgres ETL script
(multi-tenant migration Phase 4, see docs/MULTI_TENANT_PLAN.md). Builds a
real SQLite file with all 24 per-tenant tables (the exact pre-migration
schema, from `git show e762245:eve_trader/storage.py`) - empty except for
one representative row per bucket - since migrate_sqlite_to_postgres()
always queries every table in its list; a fixture DB missing a table would
raise sqlite3.OperationalError, not exercise "this table happens to be
empty" (a real copy of data/eve_trader.db always has the full schema, even
for a table with zero rows).
"""
import sqlite3

from eve_trader import storage
from eve_trader.sqlite_migration import migrate_sqlite_to_postgres

import pytest

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401


@pytest.fixture(autouse=True)
def _wipe_character_assets():
    # character_assets' PK is item_id alone (column-only bucket - not
    # tenant-widened, see docs/MULTI_TENANT_PLAN.md's "Composite primary
    # keys" section) - the fixed literal item_id used below could otherwise
    # collide with a leftover row from an earlier run under a *different*
    # tenant, silently no-op'ing this test's INSERT via ON CONFLICT DO
    # NOTHING. Same pattern pg_helpers.wipe_tables's own docstring describes.
    pg_helpers.wipe_tables("character_assets")


# The 24 per-tenant tables' CREATE TABLE statements, byte-for-byte the
# pre-migration schema (minus the 13 shared/SDE tables, which
# sqlite_migration.py deliberately never touches - see its own docstring).
_SCHEMA = """
CREATE TABLE shortlist (
    item_id INTEGER PRIMARY KEY, item TEXT NOT NULL, category TEXT,
    volume_m3 REAL, active INTEGER DEFAULT 1, meta_level INTEGER
);
CREATE TABLE shortlist_skip_streak (
    item_id INTEGER PRIMARY KEY, skip_since TEXT NOT NULL
);
CREATE TABLE shortlist_snapshot (
    run_ts TEXT, item_id INTEGER, item TEXT, category TEXT, landed_cost REAL,
    net_sell REAL, sell_volume REAL, own_orders_remaining REAL,
    profit_per_unit REAL, margin REAL, profit_per_m3 REAL, decision TEXT,
    active INTEGER, volume_m3 REAL, jita_sell REAL, import_cost REAL, meta_level INTEGER
);
CREATE TABLE candidate_universe (
    run_ts TEXT, item TEXT, type_id INTEGER, volume_m3 REAL, category TEXT,
    market_group_path TEXT, meta_level INTEGER
);
CREATE TABLE focused_candidates (
    run_ts TEXT, item TEXT, type_id INTEGER, volume_m3 REAL, category TEXT,
    market_group_path TEXT, meta_level INTEGER
);
CREATE TABLE new_candidates (
    run_ts TEXT, item TEXT, category TEXT, type_id INTEGER, volume_m3 REAL,
    paired_days INTEGER, profitable_days INTEGER, hit_rate REAL, latest_margin REAL,
    best_margin REAL, avg_profit_m3 REAL, avg_sell_movement REAL, score REAL,
    recommendation TEXT, add_flag INTEGER, meta_level INTEGER
);
CREATE TABLE realized_trades (
    run_ts TEXT, type_id INTEGER, item TEXT, buy_date TEXT, buy_qty INTEGER,
    buy_unit_price REAL, sell_date TEXT, sell_qty INTEGER, sell_unit_price REAL,
    matched_qty INTEGER, realized_profit REAL, margin REAL
);
CREATE TABLE stock_targets (
    type_id INTEGER PRIMARY KEY, type_name TEXT NOT NULL, backup_stock REAL DEFAULT 0,
    home_market_stock REAL, jita_market_stock REAL
);
CREATE TABLE manual_stock (type_id INTEGER PRIMARY KEY, count REAL DEFAULT 0);
CREATE TABLE manual_build_buy (type_id INTEGER PRIMARY KEY, decision TEXT NOT NULL);
CREATE TABLE selected_decryptors (type_id INTEGER PRIMARY KEY, decryptor TEXT NOT NULL);
CREATE TABLE job_category_locations (category TEXT PRIMARY KEY, location_id INTEGER NOT NULL);
CREATE TABLE category_location_options (
    category TEXT NOT NULL, location_id INTEGER NOT NULL, PRIMARY KEY (category, location_id)
);
CREATE TABLE structure_names (location_id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE esi_sync_state (scope TEXT PRIMARY KEY, synced_at TEXT NOT NULL);
CREATE TABLE candidate_search_cursor (id INTEGER PRIMARY KEY CHECK (id = 1), offset_value INTEGER NOT NULL);
CREATE TABLE character_assets (
    item_id INTEGER PRIMARY KEY, type_id INTEGER, location_id INTEGER, location_flag TEXT,
    quantity INTEGER, is_blueprint_copy INTEGER, owner_name TEXT
);
CREATE TABLE corp_assets (
    item_id INTEGER PRIMARY KEY, type_id INTEGER, location_id INTEGER, location_flag TEXT,
    quantity INTEGER, is_blueprint_copy INTEGER, owner_name TEXT
);
CREATE TABLE character_industry_jobs (
    job_id INTEGER PRIMARY KEY, activity_id INTEGER, blueprint_type_id INTEGER, product_type_id INTEGER,
    runs INTEGER, output_location_id INTEGER, status TEXT, end_date TEXT, start_date TEXT,
    installer_id INTEGER, installer_name TEXT
);
CREATE TABLE corp_industry_jobs (
    job_id INTEGER PRIMARY KEY, activity_id INTEGER, blueprint_type_id INTEGER, product_type_id INTEGER,
    runs INTEGER, output_location_id INTEGER, status TEXT, end_date TEXT, start_date TEXT,
    installer_id INTEGER, installer_name TEXT
);
CREATE TABLE character_slots (
    character_name TEXT PRIMARY KEY, manufacturing_slots INTEGER, reaction_slots INTEGER, science_slots INTEGER
);
CREATE TABLE character_blueprints (
    item_id INTEGER PRIMARY KEY, type_id INTEGER, location_id INTEGER, location_flag TEXT,
    quantity INTEGER, material_efficiency INTEGER, time_efficiency INTEGER, runs INTEGER
);
CREATE TABLE corp_blueprints (
    item_id INTEGER PRIMARY KEY, type_id INTEGER, location_id INTEGER, location_flag TEXT,
    quantity INTEGER, material_efficiency INTEGER, time_efficiency INTEGER, runs INTEGER
);
CREATE TABLE character_sell_orders (
    order_id INTEGER PRIMARY KEY, type_id INTEGER, location_id INTEGER, region_id INTEGER,
    volume_remain INTEGER, character_name TEXT
);
"""


def _make_sqlite_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    # One representative row per bucket - proves real data round-trips, not
    # just that empty tables don't error.
    conn.execute("INSERT INTO stock_targets VALUES (34, 'Tritanium', 1000.0, 500.0, 200.0)")  # composite-PK bucket
    conn.execute("INSERT INTO character_assets VALUES (123456789, 34, 60003760, 'Hangar', 100, 0, 'Some Char')")  # column-only bucket
    conn.execute("INSERT INTO realized_trades VALUES ('2026-01-01T00:00:00', 34, 'Tritanium', "
                 "'2026-01-01', 100, 4.5, '2026-01-02', 100, 5.5, 100, 100.0, 0.18)")  # no-PK bucket
    conn.commit()
    conn.close()


@pg_helpers.postgres_required()
def test_migrate_sqlite_to_postgres_copies_rows_from_each_bucket(tmp_path, tenant):
    db_path = tmp_path / "eve_trader.db"
    _make_sqlite_db(db_path)

    counts = migrate_sqlite_to_postgres(db_path, tenant)

    assert counts["stock_targets"] == 1
    assert counts["character_assets"] == 1
    assert counts["realized_trades"] == 1
    assert counts["manual_stock"] == 0  # empty table - still queried, still reports 0, no error

    with storage.tenant_context(tenant), storage.connect() as conn:
        row = conn.execute("SELECT type_name, backup_stock FROM stock_targets WHERE type_id = 34").fetchone()
        assert row == ("Tritanium", 1000.0)
        row = conn.execute("SELECT owner_name FROM character_assets WHERE item_id = 123456789").fetchone()
        assert row == ("Some Char",)
        row = conn.execute("SELECT item FROM realized_trades WHERE type_id = 34").fetchone()
        assert row == ("Tritanium",)


@pg_helpers.postgres_required()
def test_migrate_sqlite_to_postgres_is_idempotent_for_pk_tables(tmp_path, tenant):
    db_path = tmp_path / "eve_trader.db"
    _make_sqlite_db(db_path)

    migrate_sqlite_to_postgres(db_path, tenant)
    migrate_sqlite_to_postgres(db_path, tenant)  # must not raise a duplicate-key error

    with storage.tenant_context(tenant), storage.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM stock_targets WHERE type_id = 34").fetchone()[0]
        assert count == 1  # ON CONFLICT DO NOTHING - not duplicated
        count = conn.execute("SELECT COUNT(*) FROM realized_trades WHERE type_id = 34").fetchone()[0]
        assert count == 2  # no-PK bucket - a re-run duplicates, same as a real pipeline re-run would
