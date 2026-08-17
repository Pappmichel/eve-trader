"""Reusable Postgres test fixtures for the multi-tenant migration (see
docs/MULTI_TENANT_PLAN.md) - factored out of test_pg_tenant_isolation.py's
original Phase 0 helpers so each subsequent per-table isolation test
(Phase 1) doesn't reimplement tenant-pair setup, schema provisioning, or
RLS-scoped cleanup from scratch.
"""
from __future__ import annotations

import functools
import os
import uuid
from pathlib import Path
from typing import Iterable

import pytest

from eve_trader import storage

psycopg = pytest.importorskip("psycopg")

# Owner/superuser DSN - only used to apply docs/phase1_schema.sql (DDL, role
# creation, RLS policies). The app itself never connects with this - see
# storage.PG_DSN for the non-owner eve_trader_app role it uses instead.
OWNER_DSN = os.getenv(
    "EVE_TRADER_PG_OWNER_DSN",
    "host=localhost port=5432 dbname=eve_trader user=postgres password=devpassword",
)

_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
_PHASE1_SCHEMA_SQL = _DOCS_DIR / "phase1_schema.sql"
_PHASE2_SCHEMA_SQL = _DOCS_DIR / "phase2_schema.sql"


@functools.lru_cache(maxsize=1)
def _postgres_available() -> bool:
    """Checks reachability via OWNER_DSN, not pg_tenant.PG_DSN - the
    eve_trader_app role only exists *after* phase1_schema.sql has been
    applied, but this skip check runs at collection time, before the
    _apply_phase1_schema fixture below has had a chance to run. On a freshly
    (re)created container (role not provisioned yet), checking the app role
    here would wrongly skip every Postgres test - confirmed live. The owner
    role (postgres) always exists on any Postgres server, schema applied or
    not, so it's the right thing to probe for "is a server here at all".

    Cached (single process, never invalidated - a test session doesn't
    expect Postgres to appear/disappear mid-run) since this is otherwise
    called twice per session (once for the `postgres_required` marker at
    import time, once from `_apply_phase1_schema` at fixture setup) - each
    call is a real network round-trip with a multi-second timeout when
    unreachable, not worth paying twice."""
    try:
        with psycopg.connect(OWNER_DSN, connect_timeout=2):
            return True
    except Exception:
        return False


def postgres_required() -> pytest.MarkDecorator:
    """Call this in any test module that needs a real Postgres -
    `pytestmark = pg_helpers.postgres_required()` (mirrors the original
    test_pg_tenant_isolation.py skip, just reusable).

    A *function*, not a precomputed module-level constant - `import`
    executes a module's entire body regardless of which name you actually
    reference, so a module-level `postgres_required = pytest.mark.skipif(not
    _postgres_available(), ...)` would call `_postgres_available()` (a real
    network round-trip) the moment *anything* imports this module - which
    conftest.py does, for `tenant_pair`, on every single `pytest` run.
    Confirmed live: that cost even a Postgres-unrelated single-file run
    several seconds of pure connection-timeout overhead. A function defers
    the check to only the test modules that actually call it."""
    return pytest.mark.skipif(
        not _postgres_available(),
        reason="Local Postgres (eve-trader-pg container) not reachable - see docs/MULTI_TENANT_PLAN.md Phase 0",
    )


@pytest.fixture(scope="session", autouse=True)
def _apply_phase1_schema() -> None:
    """Applies docs/phase1_schema.sql once per test session, via the owner
    role - the script is idempotent (see its own header), so this replaces
    the manual `Get-Content | docker exec ... psql` step as a prerequisite
    for running these tests. No-ops if Postgres isn't reachable (the
    `postgres_required` skip on each test module handles that case) - only
    takes effect in test modules that actually import this fixture."""
    if not _postgres_available():
        return
    with psycopg.connect(OWNER_DSN, autocommit=True) as conn:
        conn.execute(_PHASE1_SCHEMA_SQL.read_text())


@pytest.fixture(scope="session", autouse=True)
def _apply_phase2_schema(_apply_phase1_schema) -> None:
    """Same idea as _apply_phase1_schema, for docs/phase2_schema.sql
    (tenant_settings/tenant_tokens) - a separate fixture/file rather than
    folding into phase1_schema.sql, matching how each phase got its own
    schema file so far. Explicitly depends on _apply_phase1_schema (not
    just relying on fixture-collection order) - phase2_schema.sql's GRANT
    targets the eve_trader_app role, which phase1_schema.sql is what
    creates."""
    if not _postgres_available():
        return
    with psycopg.connect(OWNER_DSN, autocommit=True) as conn:
        conn.execute(_PHASE2_SCHEMA_SQL.read_text())


@pytest.fixture
def tenant_pair() -> tuple[str, str]:
    """Two fresh tenant ids per test - replaces each test file's own
    module-level _TENANT_A/_TENANT_B pair, so a leftover row from one test
    can never be mistaken for another test's fixture data."""
    return str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def tenant() -> str:
    """One fresh tenant id per test, set as the current tenant for the whole
    test body via storage.tenant_context - for the general storage.py test
    files (test_storage_*.py etc.) that only need *an* isolated tenant, not
    a pair to prove cross-tenant isolation between.

    A fresh tenant_id alone only guarantees isolation for composite-PK-bucket
    tables (tenant_id is part of the real PK there). For column-only-bucket
    tables (character_assets, character_blueprints, character_sell_orders,
    ...) the PK was deliberately left un-widened (the plan's reasoning:
    those ids are already globally unique per ESI in real usage) - a test
    using a small hardcoded id like item_id=1 can still collide with another
    test's row at the physical PK level even though RLS hides one tenant's
    rows from the other's queries. Use wipe_tables() for those."""
    tenant_id = str(uuid.uuid4())
    with storage.tenant_context(tenant_id):
        yield tenant_id


def clean_tables(tenant_ids: Iterable[str], *table_names: str) -> None:
    """Deletes all rows from `table_names` for each id in `tenant_ids` -
    generalizes test_pg_tenant_isolation.py's original _clean_stock_targets
    fixture to any table list. Runs as each tenant specifically (not as the
    table owner), so this itself exercises normal RLS-scoped access,
    matching how the real app would ever touch these tables. Only isolates
    composite-PK-bucket tables - see wipe_tables() for the column-only
    bucket, whose PK isn't tenant-scoped."""
    for tenant_id in tenant_ids:
        with storage.tenant_context(tenant_id), storage.connect() as conn:
            for table in table_names:
                conn.execute(f"DELETE FROM {table}")


def wipe_tables(*table_names: str) -> None:
    """Deletes ALL rows from `table_names`, across every tenant - connects
    as the owner role (bypassing RLS) specifically to do this. For
    column-only-bucket tables, a fresh tenant_id doesn't prevent a small
    hardcoded test id (item_id=1, 2, 3...) from colliding with the same
    literal id inserted by an earlier test under a *different* tenant - the
    PK collision happens regardless of which tenant can see the row. Call
    this as an autouse fixture in any test file that seeds those tables with
    hardcoded ids."""
    with psycopg.connect(OWNER_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            for table in table_names:
                cur.execute(f"DELETE FROM {table}")
