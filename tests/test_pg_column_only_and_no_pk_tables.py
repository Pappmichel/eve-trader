"""Phase 1 isolation tests for the column-only bucket (8 tables - PK
unchanged, tenant_id + RLS only) and the no-PK/append bucket (5 tables - no
PK at all) from docs/MULTI_TENANT_PLAN.md's "Shared vs. per-tenant tables"
section. Two groups, by the real storage.py write pattern each table uses:

  1. Wholesale-replace tables (11: all 8 column-only-bucket tables, plus
     candidate_universe/focused_candidates/realized_trades from the no-PK
     bucket) - every real replace_*/save_* function for these does an
     **unfiltered** `DELETE FROM {table}` before re-inserting the latest
     ESI sync/run's rows (see e.g. storage.py's replace_assets,
     save_realized_trades). None of these tables got a tenant_id-widened PK
     (the plan's reasoning: column-only-bucket IDs are already globally
     unique per ESI; the no-PK ones have no PK to widen) - so the *entire*
     safety of that unfiltered DELETE depends on RLS alone. Proving that is
     this file's main job.
  2. Append-only tables (2: shortlist_snapshot, new_candidates) - no DELETE,
     no PK, rows just accumulate across runs. Proves multiple appends by one
     tenant never become visible to another.

Uses docs/phase1_schema.sql (applied automatically by tests/pg_helpers.py's
session fixture) - same mechanism as test_pg_tenant_isolation.py and
test_pg_composite_pk_tables.py.
"""
from __future__ import annotations

import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema  # noqa: F401 - scopes the schema-provisioning fixture to this module

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

# (table, insert_columns, row_a, row_b) - minimal columns, just enough to
# insert a distinguishable row per tenant; full column realism isn't needed
# to prove RLS visibility/delete-scoping, which doesn't care about content.
_WHOLESALE_REPLACE_TABLES = [
    ("character_assets", ("item_id", "type_id", "quantity"), (100001, 34, 500), (200001, 35, 300)),
    ("corp_assets", ("item_id", "type_id", "quantity"), (100002, 34, 500), (200002, 35, 300)),
    ("character_industry_jobs", ("job_id", "activity_id", "runs"), (500001, 1, 10), (500002, 1, 5)),
    ("corp_industry_jobs", ("job_id", "activity_id", "runs"), (500003, 1, 10), (500004, 1, 5)),
    ("character_slots", ("character_name", "manufacturing_slots"), ("Alice", 5), ("Bob", 3)),
    ("character_blueprints", ("item_id", "type_id", "runs"), (300001, 34, 1), (300002, 35, 1)),
    ("corp_blueprints", ("item_id", "type_id", "runs"), (300003, 34, 1), (300004, 35, 1)),
    ("character_sell_orders", ("order_id", "type_id", "volume_remain"), (700001, 34, 100), (700002, 35, 50)),
    ("candidate_universe", ("run_ts", "item", "type_id"), ("2026-08-17T00:00:00", "Tritanium (A)", 34), ("2026-08-17T00:00:00", "Tritanium (B)", 34)),
    ("focused_candidates", ("run_ts", "item", "type_id"), ("2026-08-17T00:00:00", "Tritanium (A)", 34), ("2026-08-17T00:00:00", "Tritanium (B)", 34)),
    ("realized_trades", ("run_ts", "type_id", "item"), ("2026-08-17T00:00:00", 34, "Tritanium (A)"), ("2026-08-17T00:00:00", 34, "Tritanium (B)")),
]

# (table, insert_columns, rows_a, rows_b) - tenant A appends 2 rows, tenant B
# appends 1, so equal counts can't hide a cross-tenant leak.
_APPEND_ONLY_TABLES = [
    (
        "shortlist_snapshot",
        ("run_ts", "item_id", "item"),
        [("2026-08-17T00:00:00", 34, "Tritanium"), ("2026-08-17T01:00:00", 34, "Tritanium")],
        [("2026-08-17T00:00:00", 35, "Pyerite")],
    ),
    (
        "new_candidates",
        ("run_ts", "item", "type_id"),
        [("2026-08-17T00:00:00", "Tritanium", 34), ("2026-08-17T01:00:00", "Tritanium", 34)],
        [("2026-08-17T00:00:00", "Pyerite", 35)],
    ),
]

_ALL_TABLES = [t for t, *_ in _WHOLESALE_REPLACE_TABLES] + [t for t, *_ in _APPEND_ONLY_TABLES]


@pytest.fixture(autouse=True)
def _clean(tenant_pair):
    """Cleans up both before *and* after each test (unlike
    test_pg_composite_pk_tables.py's before-only version) - this bucket's
    PKs are NOT tenant_id-widened (by design, per the plan), so a hardcoded
    id like item_id=100001 left behind by a passing test in one pytest
    session would collide with the same literal in a later session's test,
    even though the two runs use different (fresh, random) tenant_pair
    values - the PK alone doesn't know or care which tenant owns a row."""
    pg_helpers.clean_tables(tenant_pair, *_ALL_TABLES)
    yield
    pg_helpers.clean_tables(tenant_pair, *_ALL_TABLES)


@pytest.mark.parametrize("table,cols,row_a,row_b", _WHOLESALE_REPLACE_TABLES)
def test_tenant_scoped_visibility_and_wholesale_delete(tenant_pair, table, cols, row_a, row_b):
    tenant_a, tenant_b = tenant_pair
    col_list = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    insert = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(insert, row_a)
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        conn.execute(insert, row_b)

    with storage.tenant_context(tenant_a), storage.connect() as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (1,)
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (1,)

    # Unfiltered DELETE, as tenant A only - mirrors the real replace_*/save_*
    # functions exactly (see module docstring). Must not touch tenant B's row.
    with storage.tenant_context(tenant_a), storage.connect() as conn:
        conn.execute(f"DELETE FROM {table}")

    with storage.tenant_context(tenant_a), storage.connect() as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (1,)


@pytest.mark.parametrize("table,cols,rows_a,rows_b", _APPEND_ONLY_TABLES)
def test_append_only_tables_stay_tenant_scoped(tenant_pair, table, cols, rows_a, rows_b):
    tenant_a, tenant_b = tenant_pair
    col_list = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    insert = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    with storage.tenant_context(tenant_a), storage.connect() as conn:
        for row in rows_a:
            conn.execute(insert, row)
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        for row in rows_b:
            conn.execute(insert, row)

    with storage.tenant_context(tenant_a), storage.connect() as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (len(rows_a),)
    with storage.tenant_context(tenant_b), storage.connect() as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (len(rows_b),)
