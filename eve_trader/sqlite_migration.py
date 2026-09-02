"""One-time ETL: migrates a single-tenant data/eve_trader.db (the SQLite
schema this app used before the multi-tenant migration - see
docs/MULTI_TENANT_PLAN.md) into Postgres for one tenant. Per that plan's
standing constraint, this is written and proven against a *copy* of the
real file, never run against the live deployment's actual database as part
of this migration - that's a separate, later cutover decision.

Only the 24 tables in _PER_TENANT_TABLES below are migrated (the 3 buckets
from Phase 1 - composite-PK, column-only, no-PK-append; see
docs/phase1_schema.sql). Every other RLS-enabled ("tenant_isolation"
policy) table in the real schema is deliberately excluded, for one of
three reasons (see KNOWN_NON_MIGRATED_TABLES below for the exact list this
maps to - GitHub issue #60, found in a full-codebase audit 2026-08-21,
confirmed this list had drifted from the real schema; the drift itself
turned out harmless - see that constant's own comment - but was previously
undocumented and unverified):
  1. Global reference/market data (the 12 shared SDE tables +
     goonmetrics_history) - not per-tenant at all, no RLS policy, already
     reproducible via production/sde.py's refresh_sde()/the normal daily
     pipeline, not "this tenant's data" that would otherwise be lost.
  2. Genuinely new Postgres-only tables added after the pre-migration
     SQLite schema was retired (every doctrine_* table, and
     manual_blueprint_copy_costs from GitHub issue #40) - nothing to
     migrate *from*, since these features never existed in the old schema
     at all.
  3. Config/token storage that replaced a different pre-migration format
     (tenant_settings replaced config.yaml, tenant_tokens replaced
     tokens.json) - not lossy, just a different source format with its own
     one-time cutover path (see auth.py's import_tokens_file for the
     tokens.json equivalent).

test_sqlite_migration_table_drift.py's test_per_tenant_tables_list_
matches_the_real_schema is the drift-guard test CLAUDE.md's own "Deferred,
not rejected" section predicted would need live Postgres introspection -
it now exists and enforces that every real per-tenant table is either in
_PER_TENANT_TABLES or KNOWN_NON_MIGRATED_TABLES (with a reason), so a
future new table can't silently fall through both again.

Table-driven and generic rather than 24 hand-written per-table functions:
every table's column names/order are identical between the old SQLite
schema and the new Postgres one (docs/phase1_schema.sql was derived
directly from this same pre-migration schema, tenant_id only ever added,
never renaming/reordering an existing column) - cursor.description gives
the real column list at migration time, no need to hand-copy it here.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import storage

# (table_name, conflict_target_columns) - None means "no real PK, plain
# INSERT" (the 5 no-PK append/snapshot tables - duplicates there are
# already normal/expected in this app's own regular operation, matching
# how a fresh pipeline run always adds new rows rather than upserting).
# tenant_id is deliberately never in the SQLite SELECT's column list (the
# old schema predates it entirely) nor in the Postgres INSERT's explicit
# column list - it's populated by each table's own
# `DEFAULT current_setting('app.tenant_id', false)::uuid`, the same way
# every other write in this app already relies on storage.connect()'s
# ambient tenant, not a literal value passed here.
_PER_TENANT_TABLES: list[tuple[str, tuple[str, ...] | None]] = [
    # composite-PK bucket - PK widened to (tenant_id, <original pk>)
    ("stock_targets", ("tenant_id", "type_id")),
    ("manual_stock", ("tenant_id", "type_id")),
    ("manual_build_buy", ("tenant_id", "type_id")),
    ("selected_decryptors", ("tenant_id", "type_id")),
    ("shortlist", ("tenant_id", "item_id")),
    ("shortlist_skip_streak", ("tenant_id", "item_id")),
    ("job_category_locations", ("tenant_id", "category")),
    ("esi_sync_state", ("tenant_id", "scope")),
    ("candidate_search_cursor", ("tenant_id", "id")),
    ("structure_names", ("tenant_id", "location_id")),
    ("category_location_options", ("tenant_id", "category", "location_id")),
    # column-only bucket - PK already globally unique per ESI, unchanged
    ("character_assets", ("item_id",)),
    ("corp_assets", ("item_id",)),
    ("character_industry_jobs", ("job_id",)),
    ("corp_industry_jobs", ("job_id",)),
    ("character_slots", ("character_name",)),
    ("character_blueprints", ("item_id",)),
    ("corp_blueprints", ("item_id",)),
    ("character_sell_orders", ("order_id",)),
    # no-PK append/snapshot bucket
    ("shortlist_snapshot", None),
    ("candidate_universe", None),
    ("focused_candidates", None),
    ("new_candidates", None),
    ("realized_trades", None),
]

# Every real per-tenant (RLS "tenant_isolation" policy) table NOT in
# _PER_TENANT_TABLES above, with why it's deliberately excluded - see this
# module's own docstring for the 3 categories these fall into. Checked
# against the real schema by test_sqlite_migration_table_drift.py's
# drift-guard test (GitHub issue #60) - keep this in sync when adding a new
# per-tenant table that genuinely has no SQLite-era equivalent; a table that
# *does* need real migration (rare, post-cutover) belongs in
# _PER_TENANT_TABLES instead, not here.
KNOWN_NON_MIGRATED_TABLES: dict[str, str] = {
    "tenant_settings": "replaced config.yaml - not a migrated format, see this module's docstring",
    "tenant_tokens": "replaced tokens.json - see auth.py's import_tokens_file for that cutover path instead",
    "manual_blueprint_copy_costs": "added after the pre-migration SQLite schema was retired (issue #40) - nothing to migrate from",
    "doctrines": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "doctrine_fittings": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "doctrine_fitting_items": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "doctrine_fitting_parse_issues": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "doctrine_contracts": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "doctrine_contract_items": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "doctrine_contract_deviations": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "doctrine_contract_history": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "doctrine_character_assets": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "doctrine_corp_assets": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "ore_shortlist": "Postgres-native (GitHub issue #91 - never existed in the pre-migration SQLite schema)",
    "ore_shortlist_snapshot": "Postgres-native (GitHub issue #91 - never existed in the pre-migration SQLite schema)",
    "mineral_requirements": "Postgres-native (GitHub issue #93 - never existed in the pre-migration SQLite schema)",
    "tenant_role_consents": "added after the pre-migration SQLite schema was retired - nothing to migrate from",
    "special_orders": "Postgres-native (never existed in the pre-migration SQLite schema)",
    "special_order_items": "Postgres-native (never existed in the pre-migration SQLite schema)",
}


def migrate_sqlite_to_postgres(sqlite_db_path: Path, tenant_id: str) -> dict[str, int]:
    """Copies every row of every per-tenant table from `sqlite_db_path`
    into Postgres under `tenant_id`. Idempotent - safe to re-run against
    the same source file (ON CONFLICT DO NOTHING for the 19 tables with a
    real PK; the 5 no-PK tables would duplicate on a re-run, same as
    running the app's own pipeline twice would). Returns {table: row_count}
    for the rows read from SQLite (not necessarily all newly inserted, if
    some already existed in Postgres from an earlier run)."""
    counts: dict[str, int] = {}
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    try:
        with storage.tenant_context(tenant_id), storage.connect() as pg_conn:
            for table, conflict_cols in _PER_TENANT_TABLES:
                cur = sqlite_conn.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                counts[table] = len(rows)
                if not rows:
                    continue
                columns = [d[0] for d in cur.description]
                col_list = ", ".join(columns)
                placeholders = ", ".join("?" for _ in columns)
                sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
                if conflict_cols:
                    sql += f" ON CONFLICT({', '.join(conflict_cols)}) DO NOTHING"
                pg_conn.executemany(sql, rows)
    finally:
        sqlite_conn.close()
    return counts
