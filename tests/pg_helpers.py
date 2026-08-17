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

from eve_trader import pg_tenant

psycopg = pytest.importorskip("psycopg")

# Owner/superuser DSN - only used to apply docs/phase1_schema.sql (DDL, role
# creation, RLS policies). The app itself never connects with this - see
# pg_tenant.PG_DSN for the non-owner eve_trader_app role it uses instead.
OWNER_DSN = os.getenv(
    "EVE_TRADER_PG_OWNER_DSN",
    "host=localhost port=5432 dbname=eve_trader user=postgres password=devpassword",
)

_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "phase1_schema.sql"


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
        conn.execute(_SCHEMA_SQL.read_text())


@pytest.fixture
def tenant_pair() -> tuple[str, str]:
    """Two fresh tenant ids per test - replaces each test file's own
    module-level _TENANT_A/_TENANT_B pair, so a leftover row from one test
    can never be mistaken for another test's fixture data."""
    return str(uuid.uuid4()), str(uuid.uuid4())


def clean_tables(tenant_ids: Iterable[str], *table_names: str) -> None:
    """Deletes all rows from `table_names` for each id in `tenant_ids` -
    generalizes test_pg_tenant_isolation.py's original _clean_stock_targets
    fixture to any table list. Runs as each tenant specifically (not as the
    table owner), so this itself exercises normal RLS-scoped access,
    matching how the real app would ever touch these tables."""
    for tenant_id in tenant_ids:
        with pg_tenant.tenant_context(tenant_id), pg_tenant.connect() as conn:
            for table in table_names:
                conn.execute(f"DELETE FROM {table}")
