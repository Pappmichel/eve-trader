"""Postgres (multi-tenant, RLS-scoped) persistence layer - the durable store
behind both the Trading and Production tools.

    shortlist             <- shortlist items (item, category, volume, active flag)
    shortlist_snapshot    <- computed margin/decision per shortlist item, per run
    candidate_universe    <- full market-group-derived candidate universe
    focused_candidates    <- candidate universe after path-prefix filtering
    new_candidates        <- backtested/scored new-candidate recommendations
    goonmetrics_history   <- daily price history from Goonmetrics
    realized_trades       <- matched buy/sell pairs from wallet reconciliation

Tenant isolation is enforced by Postgres Row-Level Security (RLS), not by
filtering queries here - every per-tenant table's RLS policy compares its
tenant_id column against the `app.tenant_id` session setting, set via
`set_config('app.tenant_id', ..., true)` (the parameterized equivalent of
`SET LOCAL` - plain `SET LOCAL x = %s` doesn't accept a bound parameter,
confirmed live) as the first statement of every transaction. If that's ever
skipped, Postgres itself raises `unrecognized configuration parameter
"app.tenant_id"` on the first query - fails loudly, not by silently
returning every tenant's rows.

Schema (all 37 tables, RLS policies, the non-owner `eve_trader_app` role)
lives in docs/phase1_schema.sql, applied separately through the owner role
- this module never creates or migrates schema itself, unlike the SQLite
version it replaced (see docs/MULTI_TENANT_PLAN.md for why: the app
connects as a non-owner, non-BYPASSRLS role specifically so RLS can never
be silently bypassed, and that role has no CREATE TABLE privilege).
"""
from __future__ import annotations

import contextvars
import functools
import os
import re
import threading
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterable, Optional

import pandas as pd
from psycopg_pool import ConnectionPool
from psycopg.types.json import Jsonb

from .models import Candidate, NewCandidateResult, RealizedTrade, ShortlistItem, ShortlistRow

# Dev default matches the local `eve-trader-pg` Docker container (see
# docs/MULTI_TENANT_PLAN.md's Phase 0/1) - override via env var for any
# other environment. Never a hardcoded production credential.
PG_DSN = os.getenv(
    "EVE_TRADER_PG_DSN",
    "host=localhost port=5432 dbname=eve_trader user=eve_trader_app password=app_devpassword",
)

# The fixed tenant used when there's no real tenant-resolution to defer to -
# access-gate disabled (this app's default: trusted single operator, no
# login wall - see docs/phase3_schema.sql) and every CLI command (same
# "trusted single operator" reasoning, see cli.py's main()). Seeded into
# `tenants` by phase3_schema.sql; a real per-tenant login (gate enabled)
# resolves and uses a different, real tenant_id instead.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

_tenant_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tenant_id", default=None)

_pool: Optional[ConnectionPool] = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(PG_DSN, min_size=1, max_size=10, open=True)
    return _pool


def set_current_tenant(tenant_id: Optional[str]) -> contextvars.Token:
    """Sets the tenant for the current context (a request, or a `with` block
    in a test) - returns a Token for `reset_current_tenant`. Call this once,
    e.g. from AccessGateMiddleware after validating the session cookie -
    individual storage functions never need to know about tenant_id
    themselves."""
    return _tenant_id_var.set(tenant_id)


def reset_current_tenant(token: contextvars.Token) -> None:
    _tenant_id_var.reset(token)


@contextmanager
def tenant_context(tenant_id: str):
    """Convenience wrapper for tests/scripts: `with tenant_context(tid): ...`"""
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


def get_current_tenant() -> Optional[str]:
    """Reads (without setting) the ambient tenant_id, or None if none is set
    - e.g. api/routers/auth.py's /start route uses this to stash the
    caller's already-resolved tenant into _pending[state], for /callback
    (an AccessGateMiddleware-exempt path with no automatic tenant of its
    own) to pick back up later."""
    return _tenant_id_var.get()


def with_current_tenant(fn):
    """Wraps fn so that, no matter which thread later executes it, it runs
    with the tenant_id ambient on the *calling* thread right now -
    `concurrent.futures.ThreadPoolExecutor` worker threads do NOT inherit
    contextvars from the thread that submitted the work (unlike asyncio
    tasks/anyio's to_thread.run_sync, which explicitly copy the context;
    confirmed by reading cpython's ThreadPoolExecutor._WorkItem.run, and
    matching this project's own confirmed-live "a raw threading.Thread
    doesn't inherit contextvars" gotcha). Needed anywhere ESI-fetching code
    that might transitively touch storage.py (e.g. TokenManager refreshing
    an expired token mid-fetch) gets parallelized via ThreadPoolExecutor -
    see esi_client.py's _get_all_pages and production/esi_sync.py's
    sync_esi."""
    tenant_id = _tenant_id_var.get()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with tenant_context(tenant_id):
            return fn(*args, **kwargs)
    return wrapper


# Matches a `?` placeholder outside of single-quoted string literals - a
# naive str.replace("?", "%s") would also corrupt a literal "?" appearing
# inside quoted SQL text (e.g. a LIKE pattern). None of this file's queries
# do that (confirmed by inspection), but this regex is defensive rather than
# relying on that staying true forever - it skips over '...'-quoted spans
# (including doubled '' escapes) untouched.
_PLACEHOLDER_RE = re.compile(r"'(?:[^']|'')*'|(\?)")


def translate_placeholders(sql: str) -> str:
    """SQLite's `?` positional placeholder -> psycopg's `%s` - lets every
    query string in this file keep its original `?`-style placeholders."""
    return _PLACEHOLDER_RE.sub(lambda m: "%s" if m.group(1) else m.group(0), sql)


class _TranslatingCursor:
    """Wraps a psycopg cursor so `execute`/`executemany` transparently
    translate `?`-style SQL."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=None):
        return self._cursor.execute(translate_placeholders(sql), params)

    def executemany(self, sql, params_seq):
        return self._cursor.executemany(translate_placeholders(sql), params_seq)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __iter__(self):
        return iter(self._cursor)


class _TranslatingConnection:
    """Same idea as _TranslatingCursor, one level up - most of this file
    calls `conn.execute(...)`/`conn.executemany(...)` directly on the
    connection (sqlite3.Connection's own shorthand for both), not just
    `conn.cursor().execute(...)`. psycopg3's Connection has neither method
    natively (only its Cursor does) - confirmed live:
    `AttributeError: 'Connection' object has no attribute 'executemany'`,
    the first time a real storage.py write ran against Postgres end to end -
    both are implemented here to keep every existing call site unchanged."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(translate_placeholders(sql), params)
        return _TranslatingCursor(cur)

    def executemany(self, sql, params_seq):
        cur = self._conn.cursor()
        cur.executemany(translate_placeholders(sql), params_seq)
        return _TranslatingCursor(cur)

    def cursor(self):
        return _TranslatingCursor(self._conn.cursor())

    def __getattr__(self, name):
        return getattr(self._conn, name)


# Thread-local active batch connection (see batch_session below) - a plain
# module-level variable would leak one request's connection into a
# concurrently-running request on a different thread; thread-local keeps
# each request's batch isolated the same way nothing here needs a lock for
# the common (no batch active) case.
_batch_local = threading.local()


@contextmanager
def connect():
    """Every storage function opens its own connection via this context
    manager, checked out from a pooled `psycopg_pool.ConnectionPool` and
    scoped to the current tenant (from a contextvar, set once per request by
    AccessGateMiddleware - see set_current_tenant). Raises RuntimeError if no
    tenant is set, rather than risk a query silently running un-scoped.

    If called from inside an active batch_session() (see below), reuses that
    connection instead of checking out a new one - every storage function's
    own `with connect() as conn:` code needs zero changes to benefit; this is
    the one place that decides whether to share or open fresh."""
    active = getattr(_batch_local, "conn", None)
    if active is not None:
        yield active
        return
    tenant_id = _tenant_id_var.get()
    if tenant_id is None:
        raise RuntimeError(
            "storage.connect() called with no current tenant set - "
            "call set_current_tenant()/tenant_context() first. Refusing to "
            "open a connection with no tenant scope rather than risk it "
            "defaulting to an un-scoped query."
        )
    pool = _get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            # `SET LOCAL x = %s` doesn't accept a bound parameter for the
            # value - Postgres's SET syntax isn't a regular query, psycopg
            # sends it as a prepared-statement parameter regardless and
            # Postgres rejects it (confirmed live: `syntax error at or near
            # "$1"`). set_config(name, value, is_local) is a plain function
            # call, takes a normal parameter, and with is_local=true is the
            # functional equivalent of SET LOCAL (reverts at COMMIT/ROLLBACK).
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        try:
            yield _TranslatingConnection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


@contextmanager
def batch_session():
    """Opens ONE pooled connection and makes every connect() call on this
    thread reuse it until this `with` block exits, instead of each one
    checking out its own - see connect()'s docstring for the pattern this
    mirrors. Commits once, when this block exits (success) - a write made
    inside a batch is not durable until the whole batch completes, same
    all-or-nothing semantics connect() already has per call, just widened to
    per-batch; on an exception, nothing inside the batch is committed.

    Wrap a read-heavy multi-call operation in this (production/engine.py's
    plan_production/plan_asset_optimized/discover_build_candidates) - every
    storage function called anywhere underneath, directly or indirectly,
    automatically benefits with no changes of its own.

    Reentrant: a nested batch_session() call is a no-op (reuses the outer
    batch) - lets an already-batched caller call another batch-wrapped
    function without either one needing to know about the other."""
    active = getattr(_batch_local, "conn", None)
    if active is not None:
        yield  # already inside a batch - reuse it, nothing to set up or tear down
        return
    tenant_id = _tenant_id_var.get()
    if tenant_id is None:
        raise RuntimeError(
            "storage.batch_session() called with no current tenant set - "
            "call set_current_tenant()/tenant_context() first."
        )
    pool = _get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        _batch_local.conn = _TranslatingConnection(conn)
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _batch_local.conn = None


def with_batch_session():
    """Decorator form of batch_session - wraps a whole function call in one
    shared connection with no changes to the function's own body. Use on any
    top-level entry point that's known to make many storage calls in one go
    (see batch_session's docstring for which ones)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with batch_session():
                return fn(*args, **kwargs)
        return wrapper
    return decorator


@contextmanager
def connect_unscoped():
    """A `connect()` sibling for the handful of genuinely tenant-independent
    tables (`tenants`, `tenant_registry_entries` - see docs/phase3_schema.sql)
    - checks out a pooled connection *without* requiring or setting an
    ambient tenant_id. Needed because resolving *which* tenant a visitor
    belongs to (storage.resolve_tenant_id, called from the OAuth callback's
    identity-login branch) necessarily happens before any tenant_id exists
    for that request - the normal connect() would fail-closed right there.

    Deliberately narrow: using this against a real per-tenant RLS-enabled
    table doesn't silently bypass RLS - Postgres itself raises
    `unrecognized configuration parameter "app.tenant_id"` immediately,
    since nothing here ever calls `set_config`, same fail-loud spirit as
    connect()'s own check, just enforced by Postgres instead of Python."""
    pool = _get_pool()
    with pool.connection() as conn:
        try:
            yield _TranslatingConnection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ------------------------------------------------------------- tenant registry
def create_tenant(name: str) -> str:
    """Provisions a new tenant, returns its tenant_id. Admin-CLI-only (see
    cli.py's `tenant create`) - not reachable from the web app itself."""
    with connect_unscoped() as conn:
        row = conn.execute(
            "INSERT INTO tenants (name) VALUES (?) RETURNING tenant_id", (name,)
        ).fetchone()
    return str(row[0])


def add_tenant_registry_entry(tenant_id: str, entry_type: str, entry_id: int) -> None:
    """Registers `entry_id` (a character/corp/alliance id) as belonging to
    `tenant_id` - upsert, since re-registering an id to a different tenant
    is a legitimate re-provisioning operation (e.g. a character moved
    accounts), not an error to reject."""
    assert entry_type in ("character", "corporation", "alliance")
    with connect_unscoped() as conn:
        conn.execute(
            "INSERT INTO tenant_registry_entries (entry_type, entry_id, tenant_id) VALUES (?, ?, ?) "
            "ON CONFLICT(entry_type, entry_id) DO UPDATE SET tenant_id = excluded.tenant_id",
            (entry_type, entry_id, tenant_id),
        )


def resolve_tenant_id(character_id: int, corporation_id: Optional[int],
                       alliance_id: Optional[int]) -> Optional[str]:
    """Returns the tenant_id registered for `character_id`, else
    `corporation_id`, else `alliance_id` (same "any wins", character-first
    order as access_gate.py's old is_allowed), or None if none of the three
    match any registry entry."""
    with connect_unscoped() as conn:
        for entry_type, entry_id in (
            ("character", character_id), ("corporation", corporation_id), ("alliance", alliance_id),
        ):
            if entry_id is None:
                continue
            row = conn.execute(
                "SELECT tenant_id FROM tenant_registry_entries WHERE entry_type = ? AND entry_id = ?",
                (entry_type, entry_id),
            ).fetchone()
            if row is not None:
                return str(row[0])
    return None


def list_tenants() -> list[tuple]:
    """Returns (tenant_id, name, created_at) for every provisioned tenant -
    admin-CLI-only (`tenant list`)."""
    with connect_unscoped() as conn:
        return conn.execute("SELECT tenant_id, name, created_at FROM tenants ORDER BY created_at").fetchall()


def list_tenant_registry_entries(tenant_id: str) -> list[tuple]:
    """Returns (entry_type, entry_id) for every id registered to `tenant_id`
    - admin-CLI-only (`tenant list`)."""
    with connect_unscoped() as conn:
        return conn.execute(
            "SELECT entry_type, entry_id FROM tenant_registry_entries WHERE tenant_id = ? "
            "ORDER BY entry_type, entry_id",
            (tenant_id,),
        ).fetchall()


# ------------------------------------------------------------ tenant settings
def save_tenant_settings(scope: str, updates: dict) -> None:
    """Merges `updates` into the current tenant's stored overrides for
    `scope` ('trading'/'production') - same "later save wins per-key,
    doesn't clobber unrelated keys" semantics config.yaml's old
    data[key] = value loop had, now via Postgres's `||` JSONB merge
    operator. Caller (do_update_settings) is responsible for validating
    `updates` first - this never raises on a bad value, it assumes that's
    already been checked."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO tenant_settings (scope, overrides) VALUES (?, ?) "
            "ON CONFLICT(tenant_id, scope) DO UPDATE SET overrides = tenant_settings.overrides || excluded.overrides",
            (scope, Jsonb(updates)),
        )


def load_tenant_settings(scope: str) -> dict:
    """Returns the current tenant's stored overrides dict for `scope`, or
    {} if nothing has been saved yet."""
    with connect() as conn:
        row = conn.execute("SELECT overrides FROM tenant_settings WHERE scope = ?", (scope,)).fetchone()
    return row[0] if row else {}


# -------------------------------------------------------------- tenant tokens
def save_tenant_token(role: str, record: dict) -> None:
    """Upserts one OAuth token record (see auth.py's TokenRecord/asdict) for
    `role` under the current tenant."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO tenant_tokens (role, record) VALUES (?, ?) "
            "ON CONFLICT(tenant_id, role) DO UPDATE SET record = excluded.record",
            (role, Jsonb(record)),
        )


def load_all_tenant_tokens() -> dict[str, dict]:
    """Returns {role: record} for every token stored under the current
    tenant - TokenManager always operates on its whole store at once (same
    "read everything in one go" shape its old file-based _load() had), not
    a single role in isolation."""
    with connect() as conn:
        rows = conn.execute("SELECT role, record FROM tenant_tokens").fetchall()
    return {role: record for role, record in rows}


def delete_tenant_token(role: str) -> None:
    """Idempotent - deleting a role with no stored row is a no-op, not an
    error, so callers never need to check existence first."""
    with connect() as conn:
        conn.execute("DELETE FROM tenant_tokens WHERE role = ?", (role,))


# --------------------------------------------------------------------- writes
def upsert_shortlist(items: Iterable[ShortlistItem]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO shortlist (item_id, item, category, volume_m3, active, meta_level) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, item_id) DO UPDATE SET item=excluded.item, category=excluded.category, "
            "volume_m3=excluded.volume_m3, active=excluded.active, "
            "meta_level=COALESCE(excluded.meta_level, shortlist.meta_level)",
            [(i.item_id, i.item, i.category, i.volume_m3, int(i.active), i.meta_level) for i in items],
        )


def deactivate_shortlist_items(item_ids: Iterable[int]) -> None:
    """Sets active=0 for the given item_ids - used by
    actions.do_refresh_and_prune_candidates to drop shortlist items that no
    longer clear the Import/Already-ordered bar, without deleting their
    shortlist_snapshot history."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect() as conn:
        conn.executemany("UPDATE shortlist SET active = 0 WHERE item_id = ?", [(i,) for i in item_ids])


def get_shortlist_skip_since() -> dict[int, str]:
    """Returns {item_id: skip_since (ISO timestamp of when its current
    unbroken Skip streak started)} for every item currently mid-streak."""
    with connect() as conn:
        rows = conn.execute("SELECT item_id, skip_since FROM shortlist_skip_streak").fetchall()
    return {item_id: skip_since for item_id, skip_since in rows}


def start_shortlist_skip_streak(item_ids: Iterable[int], since: str) -> None:
    """Records `since` as the start of a Skip streak for each item_id that
    doesn't already have one in progress (DO NOTHING preserves the original
    streak start - a later Skip evaluation must not keep pushing it out)."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect() as conn:
        conn.executemany(
            "INSERT INTO shortlist_skip_streak (item_id, skip_since) VALUES (?, ?) "
            "ON CONFLICT(tenant_id, item_id) DO NOTHING",
            [(i, since) for i in item_ids],
        )


def clear_shortlist_skip_streak(item_ids: Iterable[int]) -> None:
    """Removes the in-progress Skip streak for each item_id - called both when
    an item is profitable again (streak broken) and once a streak has actually
    led to deactivation (tracking no longer needed)."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect() as conn:
        conn.executemany("DELETE FROM shortlist_skip_streak WHERE item_id = ?", [(i,) for i in item_ids])


def update_shortlist_meta_levels(meta_levels: dict[int, int]) -> None:
    """Backfills meta_level for shortlist items that don't have one cached yet."""
    with connect() as conn:
        conn.executemany(
            "UPDATE shortlist SET meta_level = ? WHERE item_id = ?",
            [(level, item_id) for item_id, level in meta_levels.items()],
        )


def load_shortlist() -> list[ShortlistItem]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT item_id, item, category, volume_m3, active, meta_level FROM shortlist"
        ).fetchall()
    return [ShortlistItem(item=r[1], item_id=r[0], category=r[2], volume_m3=r[3], active=bool(r[4]),
                           meta_level=r[5]) for r in rows]


def save_shortlist_snapshot(rows: list[ShortlistRow], run_ts: str) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO shortlist_snapshot (run_ts, item_id, item, category, landed_cost, net_sell, "
            "sell_volume, own_orders_remaining, profit_per_unit, margin, profit_per_m3, decision, active, "
            "volume_m3, jita_sell, import_cost, meta_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, r.item_id, r.item, r.category, r.landed_cost, r.net_sell, r.sell_volume,
              r.own_orders_remaining, r.profit_per_unit, r.margin, r.profit_per_m3, r.decision,
              int(r.active), r.volume_m3, r.jita_sell, r.import_cost, r.meta_level) for r in rows],
        )


def save_candidate_universe(candidates: list[Candidate], run_ts: str, table: str = "candidate_universe") -> None:
    # These tables are read in full (storage.read_table, no run_ts filter) as
    # a "current state" snapshot, not as append-only history - replace the
    # contents on every run instead of accumulating duplicates forever.
    assert table in ("candidate_universe", "focused_candidates")
    with connect() as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (run_ts, item, type_id, volume_m3, category, market_group_path, meta_level) "
            "VALUES (?,?,?,?,?,?,?)",
            [(run_ts, c.item, c.type_id, c.volume_m3, c.category, c.market_group_path, c.meta_level)
             for c in candidates],
        )


def save_new_candidates(results: list[NewCandidateResult], run_ts: str) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO new_candidates (run_ts, item, category, type_id, volume_m3, paired_days, "
            "profitable_days, hit_rate, latest_margin, best_margin, avg_profit_m3, avg_sell_movement, "
            "score, recommendation, add_flag, meta_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, r.item, r.category, r.type_id, r.volume_m3, r.paired_days, r.profitable_days,
              r.hit_rate, r.latest_margin, r.best_margin, r.avg_profit_m3, r.avg_sell_movement,
              r.score, r.recommendation, int(r.add), r.meta_level) for r in results],
        )


def save_goonmetrics_history(points) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO goonmetrics_history VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(region_id, type_id, date) DO UPDATE SET "
            "min_price=excluded.min_price, max_price=excluded.max_price, avg_price=excluded.avg_price, "
            "movement=excluded.movement, num_orders=excluded.num_orders",
            [(p.region_id, p.type_id, p.date, p.min_price, p.max_price, p.avg_price,
              p.movement, p.num_orders) for p in points],
        )


def save_realized_trades(trades: list[RealizedTrade], run_ts: str) -> None:
    # Replaced wholesale, not appended - do_reconcile_trades() re-matches the
    # *entire* cfg.lookback_days window from scratch on every run (not
    # incrementally), and the only read site (api/routers/trading.py
    # get_realized_trades) always filters to MAX(run_ts) - so every previous
    # run's rows are both redundant (near-total overlap with the new run's
    # window) and permanently unreachable. Confirmed real bug: a naked INSERT
    # here had bloated the table to ~19.8k rows across only 13 runs, with
    # some exact (type_id, buy_date, sell_date, matched_qty) groups repeated
    # up to 24 times.
    with connect() as conn:
        conn.execute("DELETE FROM realized_trades")
        conn.executemany(
            "INSERT INTO realized_trades (run_ts, type_id, item, buy_date, buy_qty, buy_unit_price, "
            "sell_date, sell_qty, sell_unit_price, matched_qty, realized_profit, margin) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, t.type_id, t.item, t.buy_date, t.buy_qty, t.buy_unit_price,
              t.sell_date, t.sell_qty, t.sell_unit_price, t.matched_qty,
              t.realized_profit, t.margin) for t in trades],
        )


# --------------------------------------------------------- Production: SDE cache
def replace_sde_data(
    types: list[tuple], groups: list[tuple], market_groups: list[tuple],
    blueprint_time: list[tuple], blueprint_materials: list[tuple], blueprint_products: list[tuple],
    invention_probability: list[tuple] = (), solar_systems: list[tuple] = (),
    stations: list[tuple] = (), categories: list[tuple] = (),
) -> None:
    """Wholesale-replaces the SDE cache tables (each refresh reflects one Fuzzwork
    dump snapshot, not an incremental merge - stale rows from a previous CCP
    patch would otherwise linger)."""
    with connect() as conn:
        conn.execute("DELETE FROM sde_types")
        conn.execute("DELETE FROM sde_groups")
        conn.execute("DELETE FROM sde_market_groups")
        conn.execute("DELETE FROM sde_blueprint_time")
        conn.execute("DELETE FROM sde_blueprint_materials")
        conn.execute("DELETE FROM sde_blueprint_products")
        conn.execute("DELETE FROM sde_invention_probability")
        conn.execute("DELETE FROM sde_solar_systems")
        conn.execute("DELETE FROM sde_stations")
        conn.execute("DELETE FROM sde_categories")
        conn.executemany("INSERT INTO sde_types VALUES (?,?,?,?,?,?,?,?)", types)
        conn.executemany("INSERT INTO sde_groups VALUES (?,?,?)", groups)
        conn.executemany("INSERT INTO sde_market_groups VALUES (?,?,?)", market_groups)
        conn.executemany("INSERT INTO sde_blueprint_time VALUES (?,?,?)", blueprint_time)
        conn.executemany("INSERT INTO sde_blueprint_materials VALUES (?,?,?,?)", blueprint_materials)
        conn.executemany("INSERT INTO sde_blueprint_products VALUES (?,?,?,?)", blueprint_products)
        conn.executemany("INSERT INTO sde_invention_probability VALUES (?,?,?)", invention_probability)
        conn.executemany("INSERT INTO sde_solar_systems VALUES (?,?,?,?)", solar_systems)
        conn.executemany("INSERT INTO sde_stations VALUES (?,?,?)", stations)
        conn.executemany("INSERT INTO sde_categories VALUES (?,?)", categories)
    get_system_security.cache_clear()
    get_sde_type.cache_clear()
    get_type_category.cache_clear()
    get_blueprint_for_product.cache_clear()
    get_blueprint_materials.cache_clear()
    get_blueprint_time.cache_clear()
    find_invention_recipe_by_product_type_id.cache_clear()
    get_station_ids_in_system.cache_clear()
    get_station_ids_in_region.cache_clear()


def sde_row_counts() -> dict[str, int]:
    tables = ["sde_types", "sde_groups", "sde_market_groups", "sde_blueprint_time",
              "sde_blueprint_materials", "sde_blueprint_products", "sde_invention_probability",
              "sde_solar_systems", "sde_stations", "sde_categories"]
    with connect() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def set_sde_refresh_state(refreshed_at: str, dump_etag: Optional[str]) -> None:
    """Records when refresh_sde() last completed and the Fuzzwork dump's ETag
    at that moment - see sde_refresh_state's schema comment."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO sde_refresh_state (id, refreshed_at, dump_etag) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET refreshed_at = excluded.refreshed_at, dump_etag = excluded.dump_etag",
            (refreshed_at, dump_etag),
        )


def get_sde_refresh_state() -> Optional[tuple[str, Optional[str]]]:
    """Returns (refreshed_at, dump_etag) from the last refresh_sde() call, or
    None if the SDE cache has never been refreshed since this field was
    added."""
    with connect() as conn:
        row = conn.execute("SELECT refreshed_at, dump_etag FROM sde_refresh_state WHERE id = 1").fetchone()
    return tuple(row) if row else None


def get_candidate_universe_built_at() -> Optional[str]:
    """Latest run_ts in candidate_universe - when Trading's "Load Market
    Groups" snapshot was last (re)built (see actions.do_build_universe).
    Compared against get_sde_refresh_state() to warn when the SDE cache has
    moved on since - a new item added to the SDE isn't visible to Trading
    until this snapshot is rebuilt, even though Production's own scans
    (which walk the live SDE cache directly, no snapshot) already see it."""
    with connect() as conn:
        row = conn.execute("SELECT MAX(run_ts) FROM candidate_universe").fetchone()
    return row[0] if row and row[0] else None


def load_sde_category_names() -> dict[int, str]:
    """category_id -> real SDE category name (e.g. 7 -> "Module", 20 ->
    "Implant") - lets candidate_discovery.guess_category show the actual EVE
    category instead of a crude Module-vs-everything-else split."""
    with connect() as conn:
        rows = conn.execute("SELECT category_id, category_name FROM sde_categories").fetchall()
    return {r[0]: r[1] for r in rows}


@lru_cache(maxsize=8)
def get_station_ids_in_system(system_id: int) -> frozenset:
    """Static SDE lookup (see get_sde_type) - station_ids for `system_id`, used
    to check whether an ESI asset's location_id sits in that system (e.g. "is
    this asset in Jita") without a live ESI lookup per station."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT station_id FROM sde_stations WHERE solar_system_id = ?", (system_id,)
        ).fetchall()
    return frozenset(r[0] for r in rows)


@lru_cache(maxsize=8)
def get_station_ids_in_region(region_id: int) -> frozenset:
    """Static SDE lookup - every NPC station_id in `region_id` (e.g. every
    station in The Forge, not just Jita itself). Confirmed with the user:
    trade_reconciliation.py's buyer-side location filter should accept a buy
    anywhere in the region, not just Jita's own solar system - a trader can
    legitimately buy from other Forge stations too."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.station_id FROM sde_stations s "
            "JOIN sde_solar_systems sys ON sys.solar_system_id = s.solar_system_id "
            "WHERE sys.region_id = ?", (region_id,),
        ).fetchall()
    return frozenset(r[0] for r in rows)


def load_sde_market_groups() -> list[tuple[int, Optional[int], str]]:
    """Returns (market_group_id, parent_group_id, market_group_name) for every
    cached market group - static data (see production/sde.py), used by
    candidate_discovery.py to build the candidate universe locally instead of
    walking ESI's market-group tree live."""
    with connect() as conn:
        return conn.execute(
            "SELECT market_group_id, parent_group_id, market_group_name FROM sde_market_groups"
        ).fetchall()


def load_sde_types_with_market_group() -> list[tuple[int, str, float, int, Optional[int], Optional[int]]]:
    """Returns (type_id, type_name, volume, market_group_id, meta_level,
    category_id) for every published, market-grouped type - static data, same
    use as load_sde_market_groups(). category_id (via sde_groups) lets
    candidate_discovery.guess_category classify by real SDE data
    (category_id == MODULE_CATEGORY_ID) instead of string-matching "module"/
    "rig" in the item name/market-group path."""
    with connect() as conn:
        return conn.execute(
            "SELECT t.type_id, t.type_name, t.volume, t.market_group_id, t.meta_level, g.category_id "
            "FROM sde_types t JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE t.published = 1 AND t.market_group_id IS NOT NULL"
        ).fetchall()


@lru_cache(maxsize=32)
def get_system_security(system_id: Optional[int]) -> Optional[float]:
    """Static per-system security status from the local SDE cache (never
    changes in-game, so no need to hit ESI for this) - used to scale rig
    ME/TE bonuses (see production/constants.py rig_security_multiplier).
    Cached: engine.py's recursive build-tree traversal calls this once per
    node (thousands of times for a large plan), but there are only ever 2-3
    distinct system_ids in play per run and the value never changes -
    invalidated by replace_sde_data() in case a fresh SDE import ever did
    change it (extremely rare in practice)."""
    if system_id is None:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT security FROM sde_solar_systems WHERE solar_system_id = ?", (system_id,)
        ).fetchone()
    return row[0] if row else None


# ----------------------------------------------------- Production: user settings
def upsert_stock_target(type_id: int, type_name: str, backup_stock: Optional[float] = None,
                         home_market_stock: Optional[float] = None,
                         jita_market_stock: Optional[float] = None) -> None:
    """Any of the three stock values left as None keeps whatever is already
    stored (falling back to 0 only for a brand-new row) - same COALESCE
    pattern as shortlist.meta_level - so e.g. editing just the home-market
    target doesn't silently reset backup stock to 0."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name, backup_stock, home_market_stock, jita_market_stock) "
            "VALUES (?,?,COALESCE(?, 0),?,?) "
            "ON CONFLICT(tenant_id, type_id) DO UPDATE SET type_name=excluded.type_name, "
            "backup_stock=CASE WHEN ?::real IS NULL THEN stock_targets.backup_stock ELSE excluded.backup_stock END, "
            "home_market_stock=COALESCE(excluded.home_market_stock, stock_targets.home_market_stock), "
            "jita_market_stock=COALESCE(excluded.jita_market_stock, stock_targets.jita_market_stock)",
            (type_id, type_name, backup_stock, home_market_stock, jita_market_stock, backup_stock),
        )


def delete_stock_target(type_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM stock_targets WHERE type_id = ?", (type_id,))


def load_stock_targets() -> list[tuple[int, str, float, Optional[float], Optional[float]]]:
    """Returns (type_id, type_name, backup_stock, home_market_stock, jita_market_stock)."""
    with connect() as conn:
        return conn.execute(
            "SELECT type_id, type_name, backup_stock, home_market_stock, jita_market_stock FROM stock_targets"
        ).fetchall()


def upsert_manual_stock(type_id: int, count: float) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO manual_stock (type_id, count) VALUES (?,?) "
            "ON CONFLICT(tenant_id, type_id) DO UPDATE SET count=excluded.count",
            (type_id, count),
        )


def load_manual_stock() -> dict[int, float]:
    with connect() as conn:
        rows = conn.execute("SELECT type_id, count FROM manual_stock").fetchall()
    return {r[0]: r[1] for r in rows}


def upsert_manual_build_buy(type_id: int, decision: str) -> None:
    assert decision in ("Build", "Buy")
    with connect() as conn:
        conn.execute(
            "INSERT INTO manual_build_buy (type_id, decision) VALUES (?,?) "
            "ON CONFLICT(tenant_id, type_id) DO UPDATE SET decision=excluded.decision",
            (type_id, decision),
        )


def load_manual_build_buy() -> dict[int, str]:
    with connect() as conn:
        rows = conn.execute("SELECT type_id, decision FROM manual_build_buy").fetchall()
    return {r[0]: r[1] for r in rows}


def delete_manual_build_buy(type_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM manual_build_buy WHERE type_id = ?", (type_id,))


def upsert_selected_decryptor(type_id: int, decryptor: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO selected_decryptors (type_id, decryptor) VALUES (?,?) "
            "ON CONFLICT(tenant_id, type_id) DO UPDATE SET decryptor=excluded.decryptor",
            (type_id, decryptor),
        )


def load_selected_decryptors() -> dict[int, str]:
    with connect() as conn:
        rows = conn.execute("SELECT type_id, decryptor FROM selected_decryptors").fetchall()
    return {r[0]: r[1] for r in rows}


def delete_selected_decryptor(type_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM selected_decryptors WHERE type_id = ?", (type_id,))


def upsert_category_location(category: str, location_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO job_category_locations (category, location_id) VALUES (?,?) "
            "ON CONFLICT(tenant_id, category) DO UPDATE SET location_id=excluded.location_id",
            (category, location_id),
        )


def load_category_locations() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute("SELECT category, location_id FROM job_category_locations").fetchall()
    return {r[0]: r[1] for r in rows}


def delete_category_location(category: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM job_category_locations WHERE category = ?", (category,))


def add_category_location_option(category: str, location_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO category_location_options (category, location_id) VALUES (?,?) "
            "ON CONFLICT(tenant_id, category, location_id) DO NOTHING",
            (category, location_id),
        )


def load_category_location_options() -> dict[str, list[int]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT category, location_id FROM category_location_options ORDER BY category, location_id"
        ).fetchall()
    result: dict[str, list[int]] = {}
    for category, location_id in rows:
        result.setdefault(category, []).append(location_id)
    return result


def delete_category_location_option(category: str, location_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM category_location_options WHERE category = ? AND location_id = ?",
            (category, location_id),
        )


def get_cached_structure_name(location_id: int) -> tuple[bool, Optional[str]]:
    """Returns (was_cached, name). was_cached=False means resolution has
    never been attempted for this location_id - was_cached=True with
    name=None means it *was* attempted and failed (no producer character
    could see that structure), so callers know not to silently keep retrying
    every page load."""
    with connect() as conn:
        row = conn.execute(
            "SELECT name FROM structure_names WHERE location_id = ?", (location_id,),
        ).fetchone()
    if row is None:
        return False, None
    return True, row[0]


def get_cached_structure_names(location_ids: list[int]) -> dict[int, tuple[bool, Optional[str]]]:
    """Batched form of get_cached_structure_name above - one query instead of
    N. Confirmed real (if minor - storage.py's own connect() docstring notes
    ~3ms/call) N+1-connection gap: GET /logistics/structure-names used to
    call get_cached_structure_name once per location_id in a Python loop.
    Returns {location_id: (was_cached, name)} for every id in location_ids,
    same was_cached semantics as the single-id version."""
    if not location_ids:
        return {}
    placeholders = ",".join("?" * len(location_ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT location_id, name FROM structure_names WHERE location_id IN ({placeholders})",
            location_ids,
        ).fetchall()
    found = dict(rows)
    return {loc_id: (loc_id in found, found.get(loc_id)) for loc_id in location_ids}
    return True, row[0]


def set_cached_structure_name(location_id: int, name: Optional[str]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO structure_names (location_id, name) VALUES (?, ?) "
            "ON CONFLICT(tenant_id, location_id) DO UPDATE SET name=excluded.name",
            (location_id, name),
        )


# --------------------------------------------------- Production: ESI-derived stock
def replace_assets(table: str, rows: list[tuple]) -> None:
    assert table in ("character_assets", "corp_assets")
    with connect() as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (item_id, type_id, location_id, location_flag, quantity, "
            "is_blueprint_copy, owner_name) VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def replace_industry_jobs(table: str, rows: list[tuple]) -> None:
    assert table in ("character_industry_jobs", "corp_industry_jobs")
    with connect() as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (job_id, activity_id, blueprint_type_id, product_type_id, runs, "
            "output_location_id, status, end_date, start_date, installer_id, installer_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def replace_character_slots(rows: list[tuple]) -> None:
    """rows: [(character_name, manufacturing_slots, reaction_slots, science_slots), ...]."""
    with connect() as conn:
        conn.execute("DELETE FROM character_slots")
        conn.executemany(
            "INSERT INTO character_slots (character_name, manufacturing_slots, reaction_slots, science_slots) "
            "VALUES (?,?,?,?)",
            rows,
        )


def load_character_slots() -> list[tuple]:
    """Returns [(character_name, manufacturing_slots, reaction_slots, science_slots), ...]."""
    with connect() as conn:
        return conn.execute(
            "SELECT character_name, manufacturing_slots, reaction_slots, science_slots "
            "FROM character_slots ORDER BY character_name"
        ).fetchall()


def get_product_quantity(blueprint_type_id: int, activity_id: int, product_type_id: int) -> Optional[float]:
    with connect() as conn:
        row = conn.execute(
            "SELECT quantity FROM sde_blueprint_products "
            "WHERE blueprint_type_id = ? AND activity_id = ? AND product_type_id = ?",
            (blueprint_type_id, activity_id, product_type_id),
        ).fetchone()
    return row[0] if row else None


def list_industry_jobs() -> list[tuple]:
    """Returns every active character + corp industry job, each with the
    product's type_name joined in: (job_id, activity_id, blueprint_type_id,
    product_type_id, type_name, runs, output_location_id, status, end_date,
    start_date, installer_name)."""
    with connect() as conn:
        rows = []
        for table in ("character_industry_jobs", "corp_industry_jobs"):
            rows.extend(conn.execute(
                f"SELECT j.job_id, j.activity_id, j.blueprint_type_id, j.product_type_id, "
                f"t.type_name, j.runs, j.output_location_id, j.status, j.end_date, "
                f"j.start_date, j.installer_name "
                f"FROM {table} j LEFT JOIN sde_types t ON t.type_id = j.product_type_id"
            ).fetchall())
    return rows


def replace_blueprints(table: str, rows: list[tuple]) -> None:
    assert table in ("character_blueprints", "corp_blueprints")
    with connect() as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (item_id, type_id, location_id, location_flag, quantity, "
            "material_efficiency, time_efficiency, runs) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )


def replace_sell_orders(rows: list[tuple]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM character_sell_orders")
        conn.executemany(
            "INSERT INTO character_sell_orders (order_id, type_id, location_id, region_id, "
            "volume_remain, character_name) VALUES (?,?,?,?,?,?)",
            rows,
        )


def sell_order_qty_at_location(type_id: int, location_id: int) -> float:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(volume_remain), 0) FROM character_sell_orders "
            "WHERE type_id = ? AND location_id = ?",
            (type_id, location_id),
        ).fetchone()
    return row[0]


def sell_order_qty_in_region(type_id: int, region_id: int) -> float:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(volume_remain), 0) FROM character_sell_orders "
            "WHERE type_id = ? AND region_id = ?",
            (type_id, region_id),
        ).fetchone()
    return row[0]


OFFICE_TYPE_ID = 27  # generic "Office" item - see esi_stock_at_location

# location_flag values that share a hangar/office's location_id in ESI's
# asset model but are NOT usable stock sitting in that hangar:
# - AssetSafety: recovered after a structure was unanchored/lost - requires a
#   separate retrieval trip/fee, not physically at this location right now.
# - Deliveries / CorpDeliveries: tied up in a pending contract, not free stock.
# - CorpMarket: listed on the corp's buyback/market system, not hangar stock.
# Confirmed as a real, previously-hit bug class for ESI asset tools (jeveassets
# changelog: "Asset safety is not an unknown location" needed its own fix).
# Without this filter, esi_stock_at_location silently over-counts "current
# stock" by including quantities that aren't actually available to build with.
NON_STOCK_LOCATION_FLAGS = ("AssetSafety", "Deliveries", "CorpDeliveries", "CorpMarket")


def esi_stock_at_location(type_id: int, location_id: Optional[int]) -> float:
    """Sums character + corp asset quantities for `type_id`, optionally filtered
    to `location_id` (None = all locations - useful when the home structure's
    numeric ID isn't configured). Excludes NON_STOCK_LOCATION_FLAGS (see above).

    Corp hangar contents are *not* flat under the station/structure's own
    location_id in ESI's asset model - they sit one level deeper, nested
    under the corp's rented Office item (type_id 27) at that location, which
    itself has location_id = the station/structure (confirmed empirically:
    a structure holding 151M+ units of real corp hangar stock showed only
    the Office item itself at the structure's own location_id - the actual
    stock was all nested under the Office's own item_id). So a location_id
    lookup also has to include whatever's nested under any Office item(s)
    the corp holds *at* that location, or corp hangar stock silently reads
    as zero everywhere."""
    flag_placeholders = ",".join("?" * len(NON_STOCK_LOCATION_FLAGS))
    with connect() as conn:
        total = 0.0
        for table in ("character_assets", "corp_assets"):
            if location_id is None:
                row = conn.execute(
                    f"SELECT COALESCE(SUM(quantity), 0) FROM {table} "
                    f"WHERE type_id = ? AND (location_flag IS NULL OR location_flag NOT IN ({flag_placeholders}))",
                    (type_id, *NON_STOCK_LOCATION_FLAGS),
                ).fetchone()
                total += row[0]
                continue
            row = conn.execute(
                f"SELECT COALESCE(SUM(quantity), 0) FROM {table} WHERE type_id = ? AND location_id = ? "
                f"AND (location_flag IS NULL OR location_flag NOT IN ({flag_placeholders}))",
                (type_id, location_id, *NON_STOCK_LOCATION_FLAGS),
            ).fetchone()
            total += row[0]
            office_item_ids = conn.execute(
                f"SELECT item_id FROM {table} WHERE type_id = ? AND location_id = ?",
                (OFFICE_TYPE_ID, location_id),
            ).fetchall()
            for (office_item_id,) in office_item_ids:
                row = conn.execute(
                    f"SELECT COALESCE(SUM(quantity), 0) FROM {table} WHERE type_id = ? AND location_id = ? "
                    f"AND (location_flag IS NULL OR location_flag NOT IN ({flag_placeholders}))",
                    (type_id, office_item_id, *NON_STOCK_LOCATION_FLAGS),
                ).fetchone()
                total += row[0]
    return total


def search_item_stock_locations(type_id: int) -> list[tuple[int, Optional[str], str, float]]:
    """For `type_id`, every character/corp asset (excluding
    NON_STOCK_LOCATION_FLAGS - see esi_stock_at_location above), resolved up
    through any nested containers (ship cargo, corp Office, personal
    container, ...) to the outermost station/structure location_id -
    generalizes esi_stock_at_location's single-level Office-nesting case into
    an arbitrary-depth walk (item_id -> location_id, capped at 10 hops as a
    defensive bound against a cyclical edge case, not a realistic EVE nesting
    depth) - then grouped by (resolved location, owner) summing quantity.

    Returns [(location_id, location_name, owner_name, quantity), ...] sorted
    by quantity descending. location_name comes from the structure_names
    cache (player structures - populated via logistics/resolve-structure-
    name) first, then sde_stations.station_name (NPC stations); None if
    neither has it yet. owner_name is whichever character or "<corp> (corp)"
    the sync attributed this asset to (see esi_sync.py) - "?" for data synced
    before owner_name existed (Sync ESI Data again to backfill)."""
    flag_placeholders = ",".join("?" * len(NON_STOCK_LOCATION_FLAGS))
    with connect() as conn:
        # item_id -> location_id for *every* asset (any type_id) - needed to
        # walk a matching item's own location_id up through nested
        # containers, regardless of what the container itself is.
        parent_of: dict[int, int] = {}
        for table in ("character_assets", "corp_assets"):
            for item_id, location_id in conn.execute(f"SELECT item_id, location_id FROM {table}"):
                parent_of[item_id] = location_id

        raw_rows: list[tuple[int, str, float]] = []  # (location_id, owner_name, quantity)
        for table in ("character_assets", "corp_assets"):
            raw_rows.extend(conn.execute(
                f"SELECT location_id, owner_name, quantity FROM {table} "
                f"WHERE type_id = ? AND (location_flag IS NULL OR location_flag NOT IN ({flag_placeholders}))",
                (type_id, *NON_STOCK_LOCATION_FLAGS),
            ).fetchall())

        grouped: dict[tuple[int, str], float] = {}
        for location_id, owner_name, quantity in raw_rows:
            root = location_id
            hops = 0
            while root in parent_of and hops < 10:
                root = parent_of[root]
                hops += 1
            key = (root, owner_name or "?")
            grouped[key] = grouped.get(key, 0.0) + quantity

        results: list[tuple[int, Optional[str], str, float]] = []
        for (location_id, owner_name), quantity in grouped.items():
            name_row = conn.execute(
                "SELECT name FROM structure_names WHERE location_id = ?", (location_id,)
            ).fetchone()
            name = name_row[0] if name_row else None
            if name is None:
                station_row = conn.execute(
                    "SELECT station_name FROM sde_stations WHERE station_id = ?", (location_id,)
                ).fetchone()
                name = station_row[0] if station_row else None
            results.append((location_id, name, owner_name, quantity))

    results.sort(key=lambda r: r[3], reverse=True)
    return results


def set_esi_sync_time(scope: str, synced_at: str) -> None:
    """`scope`: "production" or "trading". `synced_at`: ISO-8601 UTC timestamp."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO esi_sync_state (scope, synced_at) VALUES (?, ?) "
            "ON CONFLICT(tenant_id, scope) DO UPDATE SET synced_at=excluded.synced_at",
            (scope, synced_at),
        )


def get_candidate_search_offset() -> int:
    """See candidate_search_cursor - defaults to 0 (start of the list) if no
    safe-mode search has run yet."""
    with connect() as conn:
        row = conn.execute("SELECT offset_value FROM candidate_search_cursor WHERE id = 1").fetchone()
    return row[0] if row else 0


def set_candidate_search_offset(offset: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO candidate_search_cursor (id, offset_value) VALUES (1, ?) "
            "ON CONFLICT(tenant_id, id) DO UPDATE SET offset_value=excluded.offset_value",
            (offset,),
        )


def get_esi_sync_time(scope: str) -> Optional[str]:
    with connect() as conn:
        row = conn.execute("SELECT synced_at FROM esi_sync_state WHERE scope = ?", (scope,)).fetchone()
    return row[0] if row else None


def esi_incoming_industry_qty(product_type_id: int) -> dict[str, float]:
    """Returns {'runs': total outstanding job runs, 'jobs': job count} for
    `product_type_id` across character + corp industry jobs. Converting runs
    to output quantity needs the blueprint's product qty/run (see
    get_blueprint_for_product) - done in engine.py, not here.

    Counts active/paused/ready jobs - same "still outstanding" status set
    jobs.py character_slot_overview() already uses. Confirmed real bug: this
    used to only count 'active', so a job that finished running and is just
    sitting there as 'ready' (completed, waiting to be picked up/delivered -
    its output already exists) wasn't counted as incoming stock at all,
    understating current+incoming supply and causing the Bauliste to plan to
    build/buy more of something that's already sitting there completed."""
    with connect() as conn:
        runs = 0
        jobs = 0
        for table in ("character_industry_jobs", "corp_industry_jobs"):
            row = conn.execute(
                f"SELECT COALESCE(SUM(runs), 0), COUNT(*) FROM {table} "
                "WHERE product_type_id = ? AND status IN ('active', 'paused', 'ready')",
                (product_type_id,),
            ).fetchone()
            runs += row[0]
            jobs += row[1]
    return {"runs": runs, "jobs": jobs}


def load_owned_blueprints() -> list[tuple]:
    """Returns (type_id, quantity, material_efficiency, time_efficiency, runs)
    across character + corp blueprints, for informational display."""
    with connect() as conn:
        rows = []
        for table in ("character_blueprints", "corp_blueprints"):
            rows.extend(conn.execute(
                f"SELECT type_id, quantity, material_efficiency, time_efficiency, runs FROM {table}"
            ).fetchall())
    return rows


def get_owned_bpo_best_me_te(blueprint_type_id: int) -> Optional[tuple[int, int]]:
    """Best (highest) ME and TE independently across every owned *Original*
    (runs = -1 in ESI's blueprint model - a BPC's ME/TE was fixed by whoever
    copied it, not your own research, so only BPOs count here) of
    `blueprint_type_id`, across character + corp blueprints. None if you
    don't own this BPO. Used by production/engine.py's _activity_mods to use
    your actual research level for Tech I items instead of the flat "perfect
    research" (ME10/TE20) assumption, when you actually own that BPO.
    Not cached (unlike SDE reads) - this is ESI-synced data that changes on
    every do_sync_esi() run, not static per-patch data."""
    best_me: Optional[int] = None
    best_te: Optional[int] = None
    with connect() as conn:
        for table in ("character_blueprints", "corp_blueprints"):
            row = conn.execute(
                f"SELECT MAX(material_efficiency), MAX(time_efficiency) FROM {table} "
                "WHERE type_id = ? AND runs = -1",
                (blueprint_type_id,),
            ).fetchone()
            if row and row[0] is not None:
                best_me = row[0] if best_me is None else max(best_me, row[0])
            if row and row[1] is not None:
                best_te = row[1] if best_te is None else max(best_te, row[1])
    if best_me is None or best_te is None:
        return None
    return (best_me, best_te)


# ---------------------------------------------------------------------- reads
def read_table(table: str) -> pd.DataFrame:
    with connect() as conn:
        cur = conn.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def latest_snapshot() -> pd.DataFrame:
    with connect() as conn:
        run_ts = conn.execute("SELECT MAX(run_ts) FROM shortlist_snapshot").fetchone()[0]
        if not run_ts:
            return pd.DataFrame()
        cur = conn.execute("SELECT * FROM shortlist_snapshot WHERE run_ts = ?", (run_ts,))
        columns = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


# ------------------------------------------------------------- Production: SDE reads
def search_sde_types(query: str, limit: int = 20) -> list[tuple[int, str]]:
    """Type-ahead lookup for the Stock Targets editor. An exact (case-insensitive)
    match always sorts first, regardless of `limit` - otherwise e.g. "Vexor"
    could get pushed out of a small limit by the many longer names ("Guardian-
    Vexor", ...) that also contain it as a substring and sort earlier."""
    query = query.strip()
    with connect() as conn:
        rows = conn.execute(
            "SELECT type_id, type_name FROM sde_types "
            "WHERE published = 1 AND type_name LIKE ? "
            "ORDER BY (LOWER(type_name) <> LOWER(?)), type_name LIMIT ?",
            (f"%{query}%", query, limit),
        ).fetchall()
    return rows


@lru_cache(maxsize=None)
def get_sde_type(type_id: int) -> Optional[tuple]:
    """Returns (type_id, group_id, type_name, volume, published, market_group_id,
    meta_level, meta_group_id). meta_group_id is the real SDE metaGroupID
    (Fuzzwork invMetaTypes.csv - see production/sde.py): 1=Tech I, 2=Tech II,
    3=Storyline, 4=Faction, 5=Officer, 6=Deadspace, etc. - the authoritative
    field for "is this genuinely Tech II" questions (meta_level alone is not,
    see production/engine.py classify_activity's docstring for why). None for
    a type_id with no entry in invMetaTypes.csv (e.g. most Tech I items -
    only meta-variant types get a row at all) or an SDE cached before this
    field was added (Refresh SDE to populate it). Cached: static SDE data,
    called for the same type_ids repeatedly across a recursive BOM traversal
    (production/engine.py) - invalidated on replace_sde_data()
    (do_refresh_sde())."""
    with connect() as conn:
        return conn.execute("SELECT * FROM sde_types WHERE type_id = ?", (type_id,)).fetchone()


@lru_cache(maxsize=None)
def get_type_category(type_id: int) -> Optional[int]:
    """Returns the type's SDE category_id (e.g. 6 = Ship) via its group, or
    None if the type/group isn't in the SDE cache. Cached - see get_sde_type."""
    with connect() as conn:
        row = conn.execute(
            "SELECT g.category_id FROM sde_types t JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE t.type_id = ?", (type_id,),
        ).fetchone()
    return row[0] if row else None


def get_cached_packaged_volume(type_id: int) -> Optional[float]:
    """None means "never looked up yet" - distinct from a cached value that
    happens to equal the flight volume, so callers know whether to fetch."""
    with connect() as conn:
        row = conn.execute(
            "SELECT packaged_volume FROM type_packaged_volume WHERE type_id = ?", (type_id,),
        ).fetchone()
    return row[0] if row else None


def set_cached_packaged_volume(type_id: int, packaged_volume: float) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO type_packaged_volume (type_id, packaged_volume) VALUES (?, ?) "
            "ON CONFLICT(type_id) DO UPDATE SET packaged_volume=excluded.packaged_volume",
            (type_id, packaged_volume),
        )


@lru_cache(maxsize=None)
def get_blueprint_for_product(product_type_id: int) -> Optional[tuple[int, int, float]]:
    """Returns (blueprint_type_id, activity_id, product_qty) for the blueprint/formula
    that produces `product_type_id` via Manufacturing (1) or Reaction (11), preferring
    Manufacturing if (implausibly) both exist. None if the type isn't producible.

    Confirmed real bug: some products (e.g. Tungsten Carbide, type 16672) have
    a leftover *unpublished* blueprint row in the SDE (CCP test/legacy data,
    e.g. "Test Reaction Blueprint") alongside the real published one, with
    wildly different quantity/materials - without an explicit published-first
    tiebreak, SQLite's unordered scan could return either one. `t.published
    DESC` prefers the real, currently-buildable blueprint; the extra
    `blueprint_type_id` tiebreak keeps the choice deterministic if several
    published blueprints somehow tie. Cached - see get_sde_type."""
    with connect() as conn:
        row = conn.execute(
            "SELECT p.blueprint_type_id, p.activity_id, p.quantity FROM sde_blueprint_products p "
            "JOIN sde_types t ON t.type_id = p.blueprint_type_id "
            "WHERE p.product_type_id = ? AND p.activity_id IN (1, 11) "
            "ORDER BY t.published DESC, p.activity_id, p.blueprint_type_id LIMIT 1",
            (product_type_id,),
        ).fetchone()
    return row


@lru_cache(maxsize=None)
def get_blueprint_materials(blueprint_type_id: int, activity_id: int) -> list[tuple[int, float]]:
    """Returns [(material_type_id, quantity), ...] for one run at ME 0 (before any reduction).
    Cached - see get_sde_type. The returned list is shared across callers
    (lru_cache returns the same object every time) - callers must treat it as
    read-only (all current callers only iterate it, never mutate)."""
    with connect() as conn:
        return conn.execute(
            "SELECT material_type_id, quantity FROM sde_blueprint_materials "
            "WHERE blueprint_type_id = ? AND activity_id = ?",
            (blueprint_type_id, activity_id),
        ).fetchall()


@lru_cache(maxsize=None)
def get_blueprint_time(blueprint_type_id: int, activity_id: int) -> Optional[float]:
    """Cached - see get_sde_type."""
    with connect() as conn:
        row = conn.execute(
            "SELECT time FROM sde_blueprint_time WHERE blueprint_type_id = ? AND activity_id = ?",
            (blueprint_type_id, activity_id),
        ).fetchone()
    return row[0] if row else None


def find_invention_recipe_by_product_name(product_name: str) -> Optional[tuple[int, int]]:
    """Given a T2/T3 blueprint's name (the thing you want to invent), returns
    (t1_blueprint_type_id, product_type_id) - the invention recipe that
    produces it. None if `product_name` isn't an invented type.

    Case-insensitive (confirmed real bug: "loki blueprint" - any case other
    than the SDE's exact stored casing - returned None even though
    search_sde_types elsewhere in this file already handles this correctly
    with LOWER()), and tolerant of incidental leading/trailing whitespace
    (matches search_sde_types' own .strip(), same fix)."""
    product_name = product_name.strip()
    with connect() as conn:
        return conn.execute(
            "SELECT p.blueprint_type_id, p.product_type_id FROM sde_blueprint_products p "
            "JOIN sde_types t ON t.type_id = p.product_type_id "
            "WHERE p.activity_id = 8 AND LOWER(t.type_name) = LOWER(?)",
            (product_name,),
        ).fetchone()


@lru_cache(maxsize=None)
def find_invention_recipe_by_product_type_id(product_blueprint_type_id: int) -> Optional[int]:
    """Given a T2/T3 *blueprint*'s type_id (e.g. from get_blueprint_for_product),
    returns the T1 blueprint that invents it, or None if it isn't an invented
    type (e.g. a T1 item, or a BPO that was never invention-sourced).

    Confirmed real bug: 79 T2/T3 products have *more than one* valid
    invention source in the SDE - most visibly every Tech III hull/subsystem,
    which has 3 relic BPCs (Intact/Malfunctioning/Wrecked Hull Section) with
    materially different success probability (0.26/0.21/0.14) but identical
    materials/time. Without an explicit tiebreak this picked whichever row
    SQLite's unordered scan happened to return first - it silently returned
    the best (Intact) option today only by accident of row-insertion order,
    not by design. `ORDER BY probability DESC` makes "always prefer the
    highest-probability recipe" an explicit, deterministic choice instead of
    a coincidence that could flip on a future SDE refresh. Cached - see
    get_sde_type."""
    with connect() as conn:
        row = conn.execute(
            "SELECT p.blueprint_type_id FROM sde_blueprint_products p "
            "LEFT JOIN sde_invention_probability prob "
            "  ON prob.t1_blueprint_type_id = p.blueprint_type_id "
            "  AND prob.product_type_id = p.product_type_id "
            "WHERE p.activity_id = 8 AND p.product_type_id = ? "
            "ORDER BY prob.probability DESC, p.blueprint_type_id LIMIT 1",
            (product_blueprint_type_id,),
        ).fetchone()
    return row[0] if row else None


def get_invention_recipe(t1_blueprint_type_id: int) -> Optional[dict]:
    """Returns the full invention job definition for `t1_blueprint_type_id`:
    product_type_id, base_runs (the invented BPC's run count before decryptor
    bonus), base_probability, job time, and datacore requirements."""
    with connect() as conn:
        product = conn.execute(
            "SELECT product_type_id, quantity FROM sde_blueprint_products "
            "WHERE blueprint_type_id = ? AND activity_id = 8",
            (t1_blueprint_type_id,),
        ).fetchone()
        if product is None:
            return None
        product_type_id, base_runs = product
        prob = conn.execute(
            "SELECT probability FROM sde_invention_probability "
            "WHERE t1_blueprint_type_id = ? AND product_type_id = ?",
            (t1_blueprint_type_id, product_type_id),
        ).fetchone()
        time_row = conn.execute(
            "SELECT time FROM sde_blueprint_time WHERE blueprint_type_id = ? AND activity_id = 8",
            (t1_blueprint_type_id,),
        ).fetchone()
        materials = conn.execute(
            "SELECT material_type_id, quantity FROM sde_blueprint_materials "
            "WHERE blueprint_type_id = ? AND activity_id = 8",
            (t1_blueprint_type_id,),
        ).fetchall()
    return {
        "product_type_id": product_type_id,
        "base_runs": base_runs,
        "base_probability": prob[0] if prob else None,
        "job_time": time_row[0] if time_row else None,
        "datacores": materials,
    }
