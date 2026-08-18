"""One-time ETL: migrates a single-tenant data/eve_trader.db (the SQLite
schema this app used before the multi-tenant migration - see
docs/MULTI_TENANT_PLAN.md) into Postgres for one tenant. Per that plan's
standing constraint, this is written and proven against a *copy* of the
real file, never run against the live deployment's actual database as part
of this migration - that's a separate, later cutover decision.

Only the ~24 per-tenant tables are migrated (the 3 buckets from Phase 1 -
composite-PK, column-only, no-PK-append; see docs/phase1_schema.sql). The
12 shared SDE tables + goonmetrics_history are deliberately NOT migrated -
they're global reference/market data, already reproducible via
production/sde.py's refresh_sde()/the normal daily pipeline, not "this
tenant's data" that would otherwise be lost.

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
