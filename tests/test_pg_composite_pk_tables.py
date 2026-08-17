"""Phase 1 isolation tests for the rest of the composite-PK bucket (see
docs/MULTI_TENANT_PLAN.md's "Composite primary keys" section) - the 10
tables whose PK is an app-level/literal value naturally reused across
tenants (EVE type IDs, literal category/scope strings, the id=1 singleton
row). `stock_targets` itself is already covered by
test_pg_tenant_isolation.py (the original Phase 0 proof); this file covers
the rest, using docs/phase1_schema.sql (already applied automatically by
tests/pg_helpers.py's session fixture).

Three shapes, three groups of tests below:
  1. Simple `ON CONFLICT(tenant_id, <col>) DO UPDATE SET <col>=excluded.<col>`
     upserts (7 tables) - parametrized since the mechanism is identical,
     only column names/types differ. Mirrors the real upsert_* functions in
     storage.py (e.g. upsert_manual_stock, storage.py:969-975).
  2. `ON CONFLICT ... DO NOTHING` upserts (2 tables) - shortlist_skip_streak
     and category_location_options use DO NOTHING (not DO UPDATE) in the
     real storage.py, which needs its own proof that widening the conflict
     target doesn't change that semantic.
  3. `shortlist` - the one table in this bucket with a COALESCE-based
     partial-update upsert (meta_level), mirrored from storage.py:633-641,
     same shape as stock_targets' own COALESCE test in
     test_pg_tenant_isolation.py.
"""
from __future__ import annotations

import pytest

from eve_trader import pg_tenant

from . import pg_helpers

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required

# (table, insert_columns, row_a, row_b, update_col) - `insert_columns[0]` is
# always the PK column that collides across tenants pre-widening.
_SIMPLE_UPDATE_TABLES = [
    ("manual_stock", ("type_id", "count"), (34, 100), (34, 200), "count"),
    ("manual_build_buy", ("type_id", "decision"), (34, "Build"), (34, "Buy"), "decision"),
    ("selected_decryptors", ("type_id", "decryptor"), (34, "Accelerant"), (34, "Attenuation"), "decryptor"),
    ("job_category_locations", ("category", "location_id"), ("Reactions", 1000000000001), ("Reactions", 1000000000002), "location_id"),
    ("structure_names", ("location_id", "name"), (1000000000001, "Structure A"), (1000000000001, "Structure B"), "name"),
    ("esi_sync_state", ("scope", "synced_at"), ("trading", "2026-08-17T00:00:00"), ("trading", "2026-08-17T01:00:00"), "synced_at"),
    ("candidate_search_cursor", ("id", "offset_value"), (1, 10), (1, 20), "offset_value"),
]

_ALL_TABLES = [t for t, *_ in _SIMPLE_UPDATE_TABLES] + ["shortlist_skip_streak", "category_location_options", "shortlist"]


@pytest.fixture(autouse=True)
def _clean(tenant_pair):
    """Cleans up after too, not just before - every PK in this bucket is
    tenant_id-widened so a leftover row can never collide with a later
    session's fresh tenant_pair, but tidying up avoids unbounded garbage in
    the dev DB (see test_pg_column_only_and_no_pk_tables.py's _clean for the
    bucket where this actually matters for correctness, not just hygiene)."""
    pg_helpers.clean_tables(tenant_pair, *_ALL_TABLES)
    yield
    pg_helpers.clean_tables(tenant_pair, *_ALL_TABLES)


@pytest.mark.parametrize("table,cols,row_a,row_b,update_col", _SIMPLE_UPDATE_TABLES)
def test_two_tenants_can_upsert_the_same_key_without_colliding(tenant_pair, table, cols, row_a, row_b, update_col):
    tenant_a, tenant_b = tenant_pair
    key_col = cols[0]
    col_list = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    upsert = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT(tenant_id, {key_col}) DO UPDATE SET {update_col}=excluded.{update_col}"
    )
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute(upsert, row_a)
    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        conn.execute(upsert, row_b)
    # No exception on either INSERT is itself part of what's being proven -
    # a shared/non-composite PK would raise a duplicate-key error on the
    # second insert.

    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        row = conn.execute(f"SELECT {update_col} FROM {table} WHERE {key_col} = ?", (row_a[0],)).fetchone()
    assert row == (row_a[1],)

    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        row = conn.execute(f"SELECT {update_col} FROM {table} WHERE {key_col} = ?", (row_b[0],)).fetchone()
    assert row == (row_b[1],)


def test_shortlist_skip_streak_do_nothing_stays_tenant_scoped(tenant_pair):
    """Mirrors storage.py's start_shortlist_skip_streak (storage.py:664-676):
    ON CONFLICT DO NOTHING (not DO UPDATE) preserves the original streak
    start. Proves the widened conflict target keeps that semantic per-tenant
    - tenant B starting a streak for the same item_id must not affect tenant
    A's, and a second call for the same tenant must not push A's own
    skip_since out either."""
    tenant_a, tenant_b = tenant_pair
    upsert = (
        "INSERT INTO shortlist_skip_streak (item_id, skip_since) VALUES (?, ?) "
        "ON CONFLICT(tenant_id, item_id) DO NOTHING"
    )
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute(upsert, (501, "2026-08-01T00:00:00"))
    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        conn.execute(upsert, (501, "2026-08-02T00:00:00"))
    # Re-call for tenant A with a later timestamp - DO NOTHING must keep the
    # original skip_since, not push it out.
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute(upsert, (501, "2026-08-03T00:00:00"))

    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        row = conn.execute("SELECT skip_since FROM shortlist_skip_streak WHERE item_id = ?", (501,)).fetchone()
    assert row == ("2026-08-01T00:00:00",)

    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        row = conn.execute("SELECT skip_since FROM shortlist_skip_streak WHERE item_id = ?", (501,)).fetchone()
    assert row == ("2026-08-02T00:00:00",)


def test_category_location_options_do_nothing_stays_tenant_scoped(tenant_pair):
    """Mirrors storage.py's add_category_location_option (storage.py:1048,
    `INSERT OR IGNORE` - the SQLite spelling of `ON CONFLICT DO NOTHING`).
    Its PK is already composite (category, location_id) pre-migration -
    widening to (tenant_id, category, location_id) must still let two
    tenants record the same (category, location_id) pair independently."""
    tenant_a, tenant_b = tenant_pair
    upsert = (
        "INSERT INTO category_location_options (category, location_id) VALUES (?, ?) "
        "ON CONFLICT(tenant_id, category, location_id) DO NOTHING"
    )
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute(upsert, ("Reactions", 1000000000005))
    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        conn.execute(upsert, ("Reactions", 1000000000005))

    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        rows = conn.execute("SELECT category, location_id FROM category_location_options").fetchall()
    assert rows == [("Reactions", 1000000000005)]

    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        rows = conn.execute("SELECT category, location_id FROM category_location_options").fetchall()
    assert rows == [("Reactions", 1000000000005)]


def test_shortlist_coalesce_upsert_stays_tenant_scoped(tenant_pair):
    """Mirrors storage.py's real upsert_shortlist (storage.py:633-641) - the
    other COALESCE-based partial-update upsert in this bucket besides
    stock_targets (see test_pg_tenant_isolation.py's equivalent test).
    meta_level=NULL on a re-upsert must keep the existing value, and must
    never leak across tenants sharing the same item_id."""
    tenant_a, tenant_b = tenant_pair
    upsert = (
        "INSERT INTO shortlist (item_id, item, category, volume_m3, active, meta_level) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(tenant_id, item_id) DO UPDATE SET item=excluded.item, category=excluded.category, "
        "volume_m3=excluded.volume_m3, active=excluded.active, "
        "meta_level=COALESCE(excluded.meta_level, shortlist.meta_level)"
    )
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute(upsert, (1, "Tritanium (A)", "Material", 0.01, 1, 5))
    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        conn.execute(upsert, (1, "Tritanium (B)", "Material", 0.01, 1, 3))
    # Re-upsert tenant A's row with meta_level=None - must keep meta_level=5,
    # must never touch tenant B's row.
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute(upsert, (1, "Tritanium (A) renamed", "Material", 0.01, 1, None))

    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        row = conn.execute("SELECT item, meta_level FROM shortlist WHERE item_id = ?", (1,)).fetchone()
    assert row == ("Tritanium (A) renamed", 5)

    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        row = conn.execute("SELECT item, meta_level FROM shortlist WHERE item_id = ?", (1,)).fetchone()
    assert row == ("Tritanium (B)", 3)
