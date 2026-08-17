"""Phase 0 acceptance test (see docs/MULTI_TENANT_PLAN.md): proves the core
multi-tenant mechanism - Postgres RLS + composite (tenant_id, type_id) PK on
stock_targets - actually isolates tenants, exercised through the real
eve_trader.pg_tenant module (pool, contextvar, placeholder translation), not
just raw SQL. This is the single most important guarantee of the whole
migration (tenant A never sees tenant B's data) - keep this test even after
Phase 1 moves the rest of storage.py onto the same mechanism.

Requires a running Postgres with the Phase 0 schema (see
docs/MULTI_TENANT_PLAN.md - `docker run` + `_poc_phase0.sql`'s CREATE ROLE/
CREATE TABLE/RLS policy). Skipped automatically if unreachable, so the main
`pytest` run stays green on a machine without this local dev Postgres up -
not a substitute for actually running it locally when working on this
migration.
"""
from __future__ import annotations

import uuid

import pytest

from eve_trader import pg_tenant

psycopg = pytest.importorskip("psycopg")


def _postgres_available() -> bool:
    try:
        with psycopg.connect(pg_tenant.PG_DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="Local Postgres (eve-trader-pg container) not reachable - see docs/MULTI_TENANT_PLAN.md Phase 0",
)


@pytest.fixture(autouse=True)
def _clean_stock_targets():
    """Each test gets fresh rows - deletes as tenant A and B specifically
    (not as the table owner) so this itself exercises normal RLS-scoped
    access, matching how the real app would ever touch this table."""
    for tenant in (_TENANT_A, _TENANT_B):
        with pg_tenant.tenant_context(tenant), pg_tenant.connect() as conn:
            conn.execute("DELETE FROM stock_targets")
    yield


_TENANT_A = str(uuid.uuid4())
_TENANT_B = str(uuid.uuid4())


def test_two_tenants_can_insert_the_same_type_id_without_colliding():
    with pg_tenant.tenant_context(_TENANT_A), pg_tenant.connect() as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name) VALUES (?, ?)",
            (34, "Tritanium (A)"),
        )
    with pg_tenant.tenant_context(_TENANT_B), pg_tenant.connect() as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name) VALUES (?, ?)",
            (34, "Tritanium (B)"),
        )
    # No exception on either INSERT is itself part of what's being proven -
    # a shared/non-composite PK would raise a duplicate-key error on the
    # second insert.


def test_a_tenant_only_ever_sees_its_own_rows():
    with pg_tenant.tenant_context(_TENANT_A), pg_tenant.connect() as conn:
        conn.execute("INSERT INTO stock_targets (type_id, type_name) VALUES (?, ?)", (34, "Tritanium (A)"))
    with pg_tenant.tenant_context(_TENANT_B), pg_tenant.connect() as conn:
        conn.execute("INSERT INTO stock_targets (type_id, type_name) VALUES (?, ?)", (34, "Tritanium (B)"))

    with pg_tenant.tenant_context(_TENANT_A), pg_tenant.connect() as conn:
        rows = conn.execute("SELECT type_name FROM stock_targets").fetchall()
    assert rows == [("Tritanium (A)",)]

    with pg_tenant.tenant_context(_TENANT_B), pg_tenant.connect() as conn:
        rows = conn.execute("SELECT type_name FROM stock_targets").fetchall()
    assert rows == [("Tritanium (B)",)]


def test_upsert_with_widened_conflict_target_stays_tenant_scoped():
    """Mirrors storage.py's real upsert_stock_target (storage.py:937-953)
    almost verbatim - only the ON CONFLICT target is widened from
    `type_id` to `tenant_id, type_id`, matching what Phase 1 needs to do to
    every one of the 10 upsert sites listed in docs/MULTI_TENANT_PLAN.md's
    composite-PK section. Proves both that the widened conflict target
    works at all, and that an UPDATE via ON CONFLICT still can't touch
    another tenant's row with the same type_id."""
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
    with pg_tenant.tenant_context(_TENANT_A), pg_tenant.connect() as conn:
        conn.execute(upsert, (34, "Tritanium (A) v1", 100, None, None, 100))
    with pg_tenant.tenant_context(_TENANT_B), pg_tenant.connect() as conn:
        conn.execute(upsert, (34, "Tritanium (B) v1", 50, None, None, 50))
    # Re-upsert tenant A's row (same type_id) - must UPDATE tenant A's row
    # only, never touch tenant B's.
    with pg_tenant.tenant_context(_TENANT_A), pg_tenant.connect() as conn:
        conn.execute(upsert, (34, "Tritanium (A) v2", None, None, None, None))

    with pg_tenant.tenant_context(_TENANT_A), pg_tenant.connect() as conn:
        row = conn.execute("SELECT type_name, backup_stock FROM stock_targets WHERE type_id = ?", (34,)).fetchone()
    assert row == ("Tritanium (A) v2", 100.0)  # name updated, backup_stock kept (None -> COALESCE kept old)

    with pg_tenant.tenant_context(_TENANT_B), pg_tenant.connect() as conn:
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
