"""Phase 0 acceptance test (see docs/MULTI_TENANT_PLAN.md): proves the core
multi-tenant mechanism - Postgres RLS + composite (tenant_id, type_id) PK on
stock_targets - actually isolates tenants, exercised through the real
eve_trader.pg_tenant module (pool, contextvar, placeholder translation), not
just raw SQL. This is the single most important guarantee of the whole
migration (tenant A never sees tenant B's data) - keep this test even after
Phase 1 moves the rest of storage.py onto the same mechanism.

Requires a running local Postgres (`eve-trader-pg` container - see
docs/MULTI_TENANT_PLAN.md). Schema is applied automatically per test session
by tests/pg_helpers.py's `_apply_phase1_schema` fixture (docs/
phase1_schema.sql, idempotent) - no manual `psql` step needed. Skipped
automatically if Postgres isn't reachable, so the main `pytest` run stays
green on a machine without this local dev Postgres up - not a substitute for
actually running it locally when working on this migration.
"""
from __future__ import annotations

import pytest

from eve_trader import pg_tenant

from . import pg_helpers  # tenant_pair fixture comes from conftest.py

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required


@pytest.fixture(autouse=True)
def _clean_stock_targets(tenant_pair):
    """Each test gets fresh rows - see pg_helpers.clean_tables for why this
    runs as tenant A/B specifically rather than as the table owner. Cleans
    up after too, not just before - stock_targets' PK is tenant_id-widened
    so a leftover row can never collide with a later session's fresh
    tenant_pair, but tidying up avoids unbounded garbage in the dev DB."""
    pg_helpers.clean_tables(tenant_pair, "stock_targets")
    yield
    pg_helpers.clean_tables(tenant_pair, "stock_targets")


def test_two_tenants_can_insert_the_same_type_id_without_colliding(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name) VALUES (?, ?)",
            (34, "Tritanium (A)"),
        )
    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name) VALUES (?, ?)",
            (34, "Tritanium (B)"),
        )
    # No exception on either INSERT is itself part of what's being proven -
    # a shared/non-composite PK would raise a duplicate-key error on the
    # second insert.


def test_a_tenant_only_ever_sees_its_own_rows(tenant_pair):
    tenant_a, tenant_b = tenant_pair
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute("INSERT INTO stock_targets (type_id, type_name) VALUES (?, ?)", (34, "Tritanium (A)"))
    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        conn.execute("INSERT INTO stock_targets (type_id, type_name) VALUES (?, ?)", (34, "Tritanium (B)"))

    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        rows = conn.execute("SELECT type_name FROM stock_targets").fetchall()
    assert rows == [("Tritanium (A)",)]

    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        rows = conn.execute("SELECT type_name FROM stock_targets").fetchall()
    assert rows == [("Tritanium (B)",)]


def test_upsert_with_widened_conflict_target_stays_tenant_scoped(tenant_pair):
    """Mirrors storage.py's real upsert_stock_target (storage.py:937-953)
    almost verbatim - only the ON CONFLICT target is widened from
    `type_id` to `tenant_id, type_id`, matching what Phase 1 needs to do to
    every one of the 10 upsert sites listed in docs/MULTI_TENANT_PLAN.md's
    composite-PK section. Proves both that the widened conflict target
    works at all, and that an UPDATE via ON CONFLICT still can't touch
    another tenant's row with the same type_id."""
    tenant_a, tenant_b = tenant_pair
    # `?::real IS NULL` (not bare `? IS NULL`) - confirmed live: Postgres's
    # parameter-typing is stricter than SQLite's here. A parameter used only
    # in an `IS NULL` check has no type context to infer from, and errors
    # with "could not determine data type of parameter" when the caller
    # happens to pass None - SQLite never required this cast. Real, confirmed
    # instance of the dialect-porting work docs/MULTI_TENANT_PLAN.md Phase 1
    # anticipated in general terms - this is the concrete pattern to apply
    # to every other CASE-WHEN-?-IS-NULL upsert in storage.py.
    upsert = (
        "INSERT INTO stock_targets (type_id, type_name, backup_stock, home_market_stock, jita_market_stock) "
        "VALUES (?,?,COALESCE(?, 0),?,?) "
        "ON CONFLICT(tenant_id, type_id) DO UPDATE SET type_name=excluded.type_name, "
        "backup_stock=CASE WHEN ?::real IS NULL THEN stock_targets.backup_stock ELSE excluded.backup_stock END, "
        "home_market_stock=COALESCE(excluded.home_market_stock, stock_targets.home_market_stock), "
        "jita_market_stock=COALESCE(excluded.jita_market_stock, stock_targets.jita_market_stock)"
    )
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute(upsert, (34, "Tritanium (A) v1", 100, None, None, 100))
    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        conn.execute(upsert, (34, "Tritanium (B) v1", 50, None, None, 50))
    # Re-upsert tenant A's row (same type_id) - must UPDATE tenant A's row
    # only, never touch tenant B's.
    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        conn.execute(upsert, (34, "Tritanium (A) v2", None, None, None, None))

    with pg_tenant.tenant_context(tenant_a), pg_tenant.connect() as conn:
        row = conn.execute("SELECT type_name, backup_stock FROM stock_targets WHERE type_id = ?", (34,)).fetchone()
    assert row == ("Tritanium (A) v2", 100.0)  # name updated, backup_stock kept (None -> COALESCE kept old)

    with pg_tenant.tenant_context(tenant_b), pg_tenant.connect() as conn:
        row = conn.execute("SELECT type_name, backup_stock FROM stock_targets WHERE type_id = ?", (34,)).fetchone()
    assert row == ("Tritanium (B) v1", 50.0)  # untouched by tenant A's upsert


def test_connect_without_a_current_tenant_refuses_to_run_unscoped():
    token = pg_tenant._tenant_id_var.set(None)
    try:
        with pytest.raises(RuntimeError, match="no current tenant set"):
            with pg_tenant.connect():
                pass
    finally:
        pg_tenant._tenant_id_var.reset(token)
