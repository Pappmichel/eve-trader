"""Drift-guard test for sqlite_migration.py's _PER_TENANT_TABLES list
(GitHub issue #60, found in a full-codebase audit 2026-08-21). CLAUDE.md's
own "Deferred, not rejected" section predicted this would need live
Postgres introspection against pg_policies - it now exists.
"""
import pytest

from eve_trader import sqlite_migration

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, _apply_phase2_schema, _apply_role_consent_schema  # noqa: F401
from .test_doctrine_storage import _apply_doctrine_schema  # noqa: F401
from .test_storage_refining import _apply_refining_schema  # noqa: F401

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()


def test_per_tenant_tables_list_matches_the_real_schema(_apply_doctrine_schema, _apply_refining_schema,
                                                          _apply_role_consent_schema):
    # Every table with a "tenant_isolation" RLS policy is either an
    # actively-migrated table (_PER_TENANT_TABLES) or a documented,
    # deliberate exclusion (KNOWN_NON_MIGRATED_TABLES) - a table falling
    # through both means someone added a new per-tenant table without
    # deciding (and recording) whether SQLite-migration needs to know about
    # it, exactly the drift CLAUDE.md flagged as unverified.
    with psycopg.connect(pg_helpers.OWNER_DSN) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'"
        ).fetchall()
    real_tenant_tables = {r[0] for r in rows}

    migrated = {name for name, _conflict_cols in sqlite_migration._PER_TENANT_TABLES}
    documented_exclusions = set(sqlite_migration.KNOWN_NON_MIGRATED_TABLES)

    undocumented = real_tenant_tables - migrated - documented_exclusions
    assert undocumented == set(), (
        f"New RLS-enabled per-tenant table(s) not in sqlite_migration._PER_TENANT_TABLES "
        f"and not in sqlite_migration.KNOWN_NON_MIGRATED_TABLES: {sorted(undocumented)} - "
        f"decide whether each needs real SQLite-migration support, then record the decision "
        f"in one list or the other."
    )

    # The flip side - a name that's been removed from the real schema (a
    # dropped/renamed table) should also be caught, not silently migrate
    # zero rows forever from a table that no longer exists.
    stale = migrated - real_tenant_tables
    assert stale == set(), f"sqlite_migration._PER_TENANT_TABLES references table(s) no longer in the real schema: {sorted(stale)}"

    stale_exclusions = documented_exclusions - real_tenant_tables
    assert stale_exclusions == set(), (
        f"KNOWN_NON_MIGRATED_TABLES references table(s) no longer in the real schema "
        f"(safe to remove): {sorted(stale_exclusions)}"
    )
