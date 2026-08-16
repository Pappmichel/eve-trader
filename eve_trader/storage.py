"""SQLite persistence layer - the durable store behind the Trading tool.

    shortlist             <- shortlist items (item, category, volume, active flag)
    shortlist_snapshot    <- computed margin/decision per shortlist item, per run
    candidate_universe    <- full market-group-derived candidate universe
    focused_candidates    <- candidate universe after path-prefix filtering
    new_candidates        <- backtested/scored new-candidate recommendations
    goonmetrics_history   <- daily price history from Goonmetrics
    realized_trades       <- matched buy/sell pairs from wallet reconciliation
"""
from __future__ import annotations

import functools
import sqlite3
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .config import DATA_DIR
from .models import Candidate, NewCandidateResult, RealizedTrade, ShortlistItem, ShortlistRow

DB_PATH = DATA_DIR / "eve_trader.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS shortlist (
    item_id INTEGER PRIMARY KEY,
    item TEXT NOT NULL,
    category TEXT,
    volume_m3 REAL,
    active INTEGER DEFAULT 1,
    meta_level INTEGER
);

-- Tracks how long an item has been continuously "No market data / Skip",
-- so actions.do_refresh_and_prune_candidates can apply a grace period
-- (TradingConfig.skip_grace_period_days) before deactivating it - a row
-- exists only while a skip streak is in progress; it's deleted the moment
-- the item is profitable again (see storage.clear_shortlist_skip_streak).
CREATE TABLE IF NOT EXISTS shortlist_skip_streak (
    item_id INTEGER PRIMARY KEY,
    skip_since TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shortlist_snapshot (
    run_ts TEXT,
    item_id INTEGER,
    item TEXT,
    category TEXT,
    landed_cost REAL,
    net_sell REAL,
    sell_volume REAL,
    own_orders_remaining REAL,
    profit_per_unit REAL,
    margin REAL,
    profit_per_m3 REAL,
    decision TEXT,
    active INTEGER,
    volume_m3 REAL,
    jita_sell REAL,
    import_cost REAL,
    meta_level INTEGER
);

CREATE TABLE IF NOT EXISTS candidate_universe (
    run_ts TEXT,
    item TEXT,
    type_id INTEGER,
    volume_m3 REAL,
    category TEXT,
    market_group_path TEXT,
    meta_level INTEGER
);

CREATE TABLE IF NOT EXISTS focused_candidates (
    run_ts TEXT,
    item TEXT,
    type_id INTEGER,
    volume_m3 REAL,
    category TEXT,
    market_group_path TEXT,
    meta_level INTEGER
);

CREATE TABLE IF NOT EXISTS new_candidates (
    run_ts TEXT,
    item TEXT,
    category TEXT,
    type_id INTEGER,
    volume_m3 REAL,
    paired_days INTEGER,
    profitable_days INTEGER,
    hit_rate REAL,
    latest_margin REAL,
    best_margin REAL,
    avg_profit_m3 REAL,
    avg_sell_movement REAL,
    score REAL,
    recommendation TEXT,
    add_flag INTEGER,
    meta_level INTEGER
);

CREATE TABLE IF NOT EXISTS goonmetrics_history (
    region_id INTEGER,
    type_id INTEGER,
    date TEXT,
    min_price REAL,
    max_price REAL,
    avg_price REAL,
    movement REAL,
    num_orders INTEGER,
    PRIMARY KEY (region_id, type_id, date)
);

CREATE TABLE IF NOT EXISTS realized_trades (
    run_ts TEXT,
    type_id INTEGER,
    item TEXT,
    buy_date TEXT,
    buy_qty INTEGER,
    buy_unit_price REAL,
    sell_date TEXT,
    sell_qty INTEGER,
    sell_unit_price REAL,
    matched_qty INTEGER,
    realized_profit REAL,
    margin REAL
);

-- ------------------------------------------------------------- Production tool
-- SDE reference data (cached from Fuzzwork's CSV export, see production/sde.py).
-- Replaced wholesale on every "Refresh SDE" - not upserted row by row.
CREATE TABLE IF NOT EXISTS sde_types (
    type_id INTEGER PRIMARY KEY,
    group_id INTEGER,
    type_name TEXT,
    volume REAL,
    published INTEGER,
    market_group_id INTEGER,
    meta_level INTEGER,
    meta_group_id INTEGER
);

-- Packaged (repackaged/cargo) volume, mainly for ships - can differ hugely
-- from sde_types.volume (the flight/unpackaged volume), which is what haul
-- cost calculations must use. Not published in Fuzzwork's SDE CSVs (only ESI
-- exposes it, per type), so fetched lazily and cached here - a *separate*
-- table from sde_types since it's not wiped by replace_sde_data()'s wholesale
-- SDE refresh (packaged volumes essentially never change for existing types).
CREATE TABLE IF NOT EXISTS type_packaged_volume (
    type_id INTEGER PRIMARY KEY,
    packaged_volume REAL
);

CREATE TABLE IF NOT EXISTS sde_groups (
    group_id INTEGER PRIMARY KEY,
    category_id INTEGER,
    group_name TEXT
);

-- Real SDE category names (e.g. 7 -> "Module", 20 -> "Implant") - lets
-- candidate_discovery.guess_category show the actual EVE category instead of
-- a crude Module-vs-everything-else split.
CREATE TABLE IF NOT EXISTS sde_categories (
    category_id INTEGER PRIMARY KEY,
    category_name TEXT
);

CREATE TABLE IF NOT EXISTS sde_market_groups (
    market_group_id INTEGER PRIMARY KEY,
    parent_group_id INTEGER,
    market_group_name TEXT
);

-- Job time per blueprint+activity (activity 1=Manufacturing, 11=Reaction).
CREATE TABLE IF NOT EXISTS sde_blueprint_time (
    blueprint_type_id INTEGER,
    activity_id INTEGER,
    time REAL,
    PRIMARY KEY (blueprint_type_id, activity_id)
);

CREATE TABLE IF NOT EXISTS sde_blueprint_materials (
    blueprint_type_id INTEGER,
    activity_id INTEGER,
    material_type_id INTEGER,
    quantity REAL
);

-- get_blueprint_materials looks up by (blueprint_type_id, activity_id) - this
-- table has no PRIMARY KEY at all, so without an index every one of the
-- "hundreds of calls per plan" (see engine.py's recursive BOM traversal)
-- fell back to a full table scan (confirmed: ~31.5k rows, measured ~0.63ms/
-- call over a cold lru_cache) before the cache saturated.
CREATE INDEX IF NOT EXISTS idx_sde_blueprint_materials_lookup
    ON sde_blueprint_materials (blueprint_type_id, activity_id);

-- Manufacturing/Reaction blueprints have exactly one product per activity, but
-- Invention (8) can legitimately have more than one (a T1 item that invents
-- into several different T2 variants) - product_type_id is part of the key.
CREATE TABLE IF NOT EXISTS sde_blueprint_products (
    blueprint_type_id INTEGER,
    activity_id INTEGER,
    product_type_id INTEGER,
    quantity REAL,
    PRIMARY KEY (blueprint_type_id, activity_id, product_type_id)
);

-- The PRIMARY KEY above only covers *forward* lookups (by blueprint_type_id).
-- get_blueprint_for_product/find_invention_recipe_by_product_type_id both
-- look up by product_type_id (the reverse direction) - without this index
-- those did a full scan over every row instead of a seek.
CREATE INDEX IF NOT EXISTS idx_sde_blueprint_products_by_product
    ON sde_blueprint_products (product_type_id, activity_id);

-- Invention (activity 8) base success probability: t1_blueprint_type_id -> the
-- resulting T2/T3 blueprint's base chance before skills/decryptor.
CREATE TABLE IF NOT EXISTS sde_invention_probability (
    t1_blueprint_type_id INTEGER,
    product_type_id INTEGER,
    probability REAL,
    PRIMARY KEY (t1_blueprint_type_id, product_type_id)
);

-- Solar system security status + region (static, from Fuzzwork
-- mapSolarSystems.csv) - security scales rig ME/TE bonuses (real EVE
-- mechanic: rigs are 1x in highsec, 1.9x in lowsec, 2.1x in null-sec/
-- wormhole - see constants.py rig_security_multiplier). region_id lets
-- get_station_ids_in_region resolve "any station in The Forge", not just one
-- named system - trade_reconciliation.py's buyer-side location filter needs
-- this (a trader can legitimately buy from any station in the region, not
-- just Jita itself). Fetched from the local SDE cache instead of ESI since
-- this is static, never changes.
CREATE TABLE IF NOT EXISTS sde_solar_systems (
    solar_system_id INTEGER PRIMARY KEY,
    solar_system_name TEXT,
    security REAL,
    region_id INTEGER
);

-- NPC station -> solar system (static, from Fuzzwork staStations.csv) - used
-- to check whether an ESI asset's location_id sits in a specific system
-- (e.g. "is this in Jita") without a live ESI lookup per station.
CREATE TABLE IF NOT EXISTS sde_stations (
    station_id INTEGER PRIMARY KEY,
    solar_system_id INTEGER,
    station_name TEXT
);

-- User-maintained production settings.
-- backup_stock = personal/component buffer target (feeds the Buy/Build engine).
-- home_market_stock/jita_market_stock = how many should be actively listed for
-- sale on each market (informational - see market_status(); not yet fed
-- into the Buy/Build engine).
CREATE TABLE IF NOT EXISTS stock_targets (
    type_id INTEGER PRIMARY KEY,
    type_name TEXT NOT NULL,
    backup_stock REAL DEFAULT 0,
    home_market_stock REAL,
    jita_market_stock REAL
);

CREATE TABLE IF NOT EXISTS manual_stock (
    type_id INTEGER PRIMARY KEY,
    count REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS manual_build_buy (
    type_id INTEGER PRIMARY KEY,
    decision TEXT NOT NULL
);

-- Per-item decryptor override for T2 builds. type_id = the T2 *product*
-- (e.g. Small Shield Booster II, not its blueprint). Absent = "Best"
-- (auto-selected).
CREATE TABLE IF NOT EXISTS selected_decryptors (
    type_id INTEGER PRIMARY KEY,
    decryptor TEXT NOT NULL
);

-- Which structure/station (location_id) each job_category (see
-- production/engine.py job_category) should be built at, for the Logistik
-- tab - user-entered (station names change and player structures aren't in
-- the SDE), not auto-discovered.
CREATE TABLE IF NOT EXISTS job_category_locations (
    category TEXT PRIMARY KEY,
    location_id INTEGER NOT NULL
);

-- Cache for ESIClient.get_structure_name() - a player-owned Upwell
-- structure's name never changes-by-surprise the way its solar system can
-- (see production/actions.py do_resolve_structure_name), and resolving it
-- needs an authenticated ESI call, so cache indefinitely rather than
-- re-fetching on every Logistik page load. name=NULL means "resolution was
-- tried and failed" (e.g. no producer character can see that structure) -
-- distinct from "never tried" (no row at all), so callers know not to
-- silently retry every single load.
-- Known alternative structures for a job_category (see engine.py
-- job_category), for the Logistik tab's per-category dropdown - a category's
-- *active* structure is still the single job_category_locations row; this
-- table is just the set of locations worth offering as quick-switch options
-- (e.g. Reactions/Advanced/Capital Components rotate between a handful of
-- home systems - see production/actions.py do_set_category_location, which
-- adds the newly-active location here too).
CREATE TABLE IF NOT EXISTS category_location_options (
    category TEXT NOT NULL,
    location_id INTEGER NOT NULL,
    PRIMARY KEY (category, location_id)
);

CREATE TABLE IF NOT EXISTS structure_names (
    location_id INTEGER PRIMARY KEY,
    name TEXT
);

-- One row per tool (see get/set_esi_sync_time) recording when that tool's
-- ESI sync/pipeline last completed - "production" for production/actions.py
-- do_sync_esi(), "trading" for actions.py do_pipeline() - shown in each
-- tool's sidebar so a stale sync is obvious at a glance.
CREATE TABLE IF NOT EXISTS esi_sync_state (
    scope TEXT PRIMARY KEY,
    synced_at TEXT NOT NULL
);

-- Single-row cursor for history_backtest.find_new_import_candidates' safe
-- mode: instead of re-testing the same fixed prefix of the candidate list
-- every run (old bug: items past the max_ids cutoff could never be reached)
-- or a random sample every run (no guarantee an item is ever picked), each
-- safe-mode run tests the next max_ids-sized window starting at `offset` and
-- advances it, wrapping at the end of the list - guarantees full coverage of
-- the candidate universe within ceil(len(candidates)/max_ids) runs.
CREATE TABLE IF NOT EXISTS candidate_search_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    offset_value INTEGER NOT NULL
);

-- Single-row record of when production/sde.py's refresh_sde() last completed
-- and the Fuzzwork dump's ETag at that moment - lets a cheap HEAD request
-- (no ~19MB CSV download) detect a newer dump later (see
-- sde.check_for_newer_sde / get_sde_refresh_state).
CREATE TABLE IF NOT EXISTS sde_refresh_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    refreshed_at TEXT NOT NULL,
    dump_etag TEXT
);

-- ESI-derived stock (character + corp assets/industry jobs/blueprints), synced
-- via production/actions.py do_sync_esi(). Replaced wholesale on every sync.
CREATE TABLE IF NOT EXISTS character_assets (
    item_id INTEGER PRIMARY KEY,
    type_id INTEGER,
    location_id INTEGER,
    location_flag TEXT,
    quantity INTEGER,
    is_blueprint_copy INTEGER,
    owner_name TEXT
);

CREATE TABLE IF NOT EXISTS corp_assets (
    item_id INTEGER PRIMARY KEY,
    type_id INTEGER,
    location_id INTEGER,
    location_flag TEXT,
    quantity INTEGER,
    is_blueprint_copy INTEGER,
    owner_name TEXT
);

-- engine.py's _current_stock/_stock_on_hand (esi_stock_at_location) and the
-- Asset Search tab (search_item_stock_locations) both filter these two
-- tables by type_id (sometimes + location_id) on essentially every material
-- in a recursive BOM walk - only item_id (the PRIMARY KEY) was indexed
-- before, so every one of those lookups fell back to a full table scan
-- (confirmed real cost: corp_assets alone holds 9k+ rows for an active
-- account) - same missing-index class already fixed for the SDE tables
-- above (idx_sde_blueprint_materials_lookup).
CREATE INDEX IF NOT EXISTS idx_character_assets_type_location ON character_assets (type_id, location_id);
CREATE INDEX IF NOT EXISTS idx_corp_assets_type_location ON corp_assets (type_id, location_id);

CREATE TABLE IF NOT EXISTS character_industry_jobs (
    job_id INTEGER PRIMARY KEY,
    activity_id INTEGER,
    blueprint_type_id INTEGER,
    product_type_id INTEGER,
    runs INTEGER,
    output_location_id INTEGER,
    status TEXT,
    end_date TEXT,
    start_date TEXT,
    installer_id INTEGER,
    installer_name TEXT
);

CREATE TABLE IF NOT EXISTS corp_industry_jobs (
    job_id INTEGER PRIMARY KEY,
    activity_id INTEGER,
    blueprint_type_id INTEGER,
    product_type_id INTEGER,
    runs INTEGER,
    output_location_id INTEGER,
    status TEXT,
    end_date TEXT,
    start_date TEXT,
    installer_id INTEGER,
    installer_name TEXT
);

-- esi_incoming_industry_qty (engine.py's _current_stock, called on nearly
-- every material) filters both tables by (product_type_id, status) - same
-- missing-index full-scan issue as the assets tables above.
CREATE INDEX IF NOT EXISTS idx_character_industry_jobs_product_status
    ON character_industry_jobs (product_type_id, status);
CREATE INDEX IF NOT EXISTS idx_corp_industry_jobs_product_status
    ON corp_industry_jobs (product_type_id, status);

-- Concurrent industry job-slot totals per registered producer character,
-- derived from skills (see production/constants.py job_slots_from_skills) -
-- refreshed on every sync_esi() call.
CREATE TABLE IF NOT EXISTS character_slots (
    character_name TEXT PRIMARY KEY,
    manufacturing_slots INTEGER,
    reaction_slots INTEGER,
    science_slots INTEGER
);

CREATE TABLE IF NOT EXISTS character_blueprints (
    item_id INTEGER PRIMARY KEY,
    type_id INTEGER,
    location_id INTEGER,
    location_flag TEXT,
    quantity INTEGER,
    material_efficiency INTEGER,
    time_efficiency INTEGER,
    runs INTEGER
);

CREATE TABLE IF NOT EXISTS corp_blueprints (
    item_id INTEGER PRIMARY KEY,
    type_id INTEGER,
    location_id INTEGER,
    location_flag TEXT,
    quantity INTEGER,
    material_efficiency INTEGER,
    time_efficiency INTEGER,
    runs INTEGER
);

-- get_owned_bpo_best_me_te (engine.py's _activity_mods - called for every
-- Tech I item, the large majority of the BOM tree) filters both tables by
-- (type_id, runs) - same missing-index full-scan issue as above (corp_
-- blueprints alone holds 7k+ rows for an active account).
CREATE INDEX IF NOT EXISTS idx_character_blueprints_type_runs ON character_blueprints (type_id, runs);
CREATE INDEX IF NOT EXISTS idx_corp_blueprints_type_runs ON corp_blueprints (type_id, runs);

-- Open SELL orders across all registered producer characters (see Marktstatus
-- tab) - feeds "how many of this do I currently have listed, home vs. Jita".
CREATE TABLE IF NOT EXISTS character_sell_orders (
    order_id INTEGER PRIMARY KEY,
    type_id INTEGER,
    location_id INTEGER,
    region_id INTEGER,
    volume_remain INTEGER,
    character_name TEXT
);

-- sell_order_qty_at_location/sell_order_qty_in_region filter by type_id +
-- location_id or type_id + region_id respectively - smaller table (order
-- counts, not asset counts) but the same fix, cheap to add.
CREATE INDEX IF NOT EXISTS idx_character_sell_orders_type_location ON character_sell_orders (type_id, location_id);
CREATE INDEX IF NOT EXISTS idx_character_sell_orders_type_region ON character_sell_orders (type_id, region_id);
"""


# Columns added after a table's initial CREATE TABLE - "IF NOT EXISTS" won't
# retrofit them onto a database file created by an older version, so add any
# missing ones by hand on every connect().
_ADDED_COLUMNS = {
    "shortlist": ["meta_level INTEGER"],
    "shortlist_snapshot": ["meta_level INTEGER"],
    "candidate_universe": ["meta_level INTEGER"],
    "focused_candidates": ["meta_level INTEGER"],
    "new_candidates": ["meta_level INTEGER"],
    "stock_targets": ["home_market_stock REAL", "jita_market_stock REAL"],
    "character_industry_jobs": ["start_date TEXT", "installer_id INTEGER", "installer_name TEXT"],
    "corp_industry_jobs": ["start_date TEXT", "installer_id INTEGER", "installer_name TEXT"],
    "sde_types": ["meta_group_id INTEGER"],
    "character_assets": ["owner_name TEXT"],
    "corp_assets": ["owner_name TEXT"],
    "sde_stations": ["station_name TEXT"],
}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Two migration strategies, depending on what changed: additive columns
    (_ADDED_COLUMNS - safe, keeps existing rows) via ALTER TABLE ADD COLUMN,
    checked against PRAGMA table_info so it's a no-op on a DB that already
    has the column (idempotent, safe to run on every connect()). A primary-
    key change can't be expressed as ALTER TABLE at all in SQLite, so
    sde_blueprint_products gets a one-off destructive drop-and-recreate
    instead (safe there specifically because it's a wholesale-replaced SDE
    cache, never user-edited data - see replace_sde_data)."""
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for coldef in columns:
            if coldef.split()[0] not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")

    # sde_blueprint_products' primary key grew a column (product_type_id) to
    # support Invention recipes with multiple outputs - "IF NOT EXISTS" won't
    # retrofit that onto a table created under the old schema. Safe to drop:
    # it's a wholesale-replaced SDE cache, never user-edited.
    pk_cols = [row[1] for row in conn.execute("PRAGMA table_info(sde_blueprint_products)") if row[5] > 0]
    if pk_cols and pk_cols != ["blueprint_type_id", "activity_id", "product_type_id"]:
        conn.execute("DROP TABLE sde_blueprint_products")
        conn.executescript(SCHEMA)


_initialized_dbs: set[Path] = set()

# Thread-local active batch connection (see batch_session below) - a plain
# module-level variable would leak one request's connection into a
# concurrently-running request on a different thread; thread-local keeps
# each request's batch isolated the same way nothing here needs a lock for
# the common (no batch active) case.
_batch_local = threading.local()


@contextmanager
def connect(db_path: Path = DB_PATH):
    """Every storage function opens its own connection via this context
    manager - re-running the full CREATE TABLE IF NOT EXISTS script plus
    _apply_migrations' PRAGMA table_info checks on *every single call* (as
    this used to do) is pure overhead once the schema is already in place:
    ~3ms/call measured, which adds up to real seconds across the hundreds of
    calls a single recursive BOM calculation makes. Both are idempotent, so
    running them once per db_path per process is equivalent - just track
    which paths have already been initialized (a set, not a single flag, so
    tests/tools that point at a different db_path still get set up).

    If called from inside an active batch_session() for this same db_path
    (see below), reuses that connection instead of opening a new one - every
    storage function's own `with connect(db_path) as conn:` code needs zero
    changes to benefit; this is the one place that decides whether to share
    or open fresh. Confirmed real cost via cProfile (2026-08-16): a single
    plan_asset_optimized run made ~14,000 calls to connect(), each its own
    sqlite3.connect()+close() cycle (~0.2-0.3ms apiece even with the schema-
    init check already skipped) - several seconds of pure connection-open/
    close overhead on top of (and separate from) actual query time, even
    after the missing indexes elsewhere in this file were added."""
    active = getattr(_batch_local, "conn", None)
    if active is not None and getattr(_batch_local, "db_path", None) == db_path:
        yield active
        return
    conn = sqlite3.connect(db_path)
    try:
        if db_path not in _initialized_dbs:
            conn.executescript(SCHEMA)
            _apply_migrations(conn)
            _initialized_dbs.add(db_path)
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def batch_session(db_path: Path = DB_PATH):
    """Opens ONE connection and makes every connect() call on this thread
    (for this same db_path) reuse it until this `with` block exits, instead
    of each one opening/closing its own - see connect()'s docstring for the
    measured cost this eliminates. Commits once, when this block exits
    (success) - a write made inside a batch is not durable until the whole
    batch completes, same all-or-nothing semantics connect() already had per
    call, just widened to per-batch; on an exception, nothing inside the
    batch is committed (matches a single connect() call's behavior on
    exception too - it only commits after its `yield` returns normally).

    Wrap a read-heavy multi-call operation in this (production/engine.py's
    plan_production/plan_asset_optimized/discover_build_candidates) - every
    storage function called anywhere underneath, directly or indirectly,
    automatically benefits with no changes of its own.

    Reentrant: a nested batch_session() call for the *same* db_path is a
    no-op (reuses the outer batch, doesn't open a second connection or
    double-close) - lets an already-batched caller call another
    batch-wrapped function without either one needing to know about the
    other. A nested call for a *different* db_path raises, since only one
    active batch connection is tracked per thread."""
    active = getattr(_batch_local, "conn", None)
    if active is not None:
        if getattr(_batch_local, "db_path", None) != db_path:
            raise RuntimeError(
                f"batch_session({db_path}) requested while a batch for a different db_path is already active - "
                "only one active batch connection is supported per thread."
            )
        yield  # already inside a batch for this db_path - reuse it, nothing to set up or tear down
        return
    conn = sqlite3.connect(db_path)
    try:
        if db_path not in _initialized_dbs:
            conn.executescript(SCHEMA)
            _apply_migrations(conn)
            _initialized_dbs.add(db_path)
        _batch_local.conn = conn
        _batch_local.db_path = db_path
        yield
        conn.commit()
    finally:
        _batch_local.conn = None
        _batch_local.db_path = None
        conn.close()


def with_batch_session(db_path: Path = DB_PATH):
    """Decorator form of batch_session - wraps a whole function call in one
    shared connection with no changes to the function's own body. Use on any
    top-level entry point that's known to make many storage calls in one go
    (see batch_session's docstring for which ones)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with batch_session(db_path):
                return fn(*args, **kwargs)
        return wrapper
    return decorator


# --------------------------------------------------------------------- writes
def upsert_shortlist(items: Iterable[ShortlistItem], db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO shortlist (item_id, item, category, volume_m3, active, meta_level) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(item_id) DO UPDATE SET item=excluded.item, category=excluded.category, "
            "volume_m3=excluded.volume_m3, active=excluded.active, "
            "meta_level=COALESCE(excluded.meta_level, shortlist.meta_level)",
            [(i.item_id, i.item, i.category, i.volume_m3, int(i.active), i.meta_level) for i in items],
        )


def deactivate_shortlist_items(item_ids: Iterable[int], db_path: Path = DB_PATH) -> None:
    """Sets active=0 for the given item_ids - used by
    actions.do_refresh_and_prune_candidates to drop shortlist items that no
    longer clear the Import/Already-ordered bar, without deleting their
    shortlist_snapshot history."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect(db_path) as conn:
        conn.executemany("UPDATE shortlist SET active = 0 WHERE item_id = ?", [(i,) for i in item_ids])


def get_shortlist_skip_since(db_path: Path = DB_PATH) -> dict[int, str]:
    """Returns {item_id: skip_since (ISO timestamp of when its current
    unbroken Skip streak started)} for every item currently mid-streak."""
    with connect(db_path) as conn:
        rows = conn.execute("SELECT item_id, skip_since FROM shortlist_skip_streak").fetchall()
    return {item_id: skip_since for item_id, skip_since in rows}


def start_shortlist_skip_streak(item_ids: Iterable[int], since: str, db_path: Path = DB_PATH) -> None:
    """Records `since` as the start of a Skip streak for each item_id that
    doesn't already have one in progress (DO NOTHING preserves the original
    streak start - a later Skip evaluation must not keep pushing it out)."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO shortlist_skip_streak (item_id, skip_since) VALUES (?, ?) "
            "ON CONFLICT(item_id) DO NOTHING",
            [(i, since) for i in item_ids],
        )


def clear_shortlist_skip_streak(item_ids: Iterable[int], db_path: Path = DB_PATH) -> None:
    """Removes the in-progress Skip streak for each item_id - called both when
    an item is profitable again (streak broken) and once a streak has actually
    led to deactivation (tracking no longer needed)."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect(db_path) as conn:
        conn.executemany("DELETE FROM shortlist_skip_streak WHERE item_id = ?", [(i,) for i in item_ids])


def update_shortlist_meta_levels(meta_levels: dict[int, int], db_path: Path = DB_PATH) -> None:
    """Backfills meta_level for shortlist items that don't have one cached yet."""
    with connect(db_path) as conn:
        conn.executemany(
            "UPDATE shortlist SET meta_level = ? WHERE item_id = ?",
            [(level, item_id) for item_id, level in meta_levels.items()],
        )


def load_shortlist(db_path: Path = DB_PATH) -> list[ShortlistItem]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT item_id, item, category, volume_m3, active, meta_level FROM shortlist"
        ).fetchall()
    return [ShortlistItem(item=r[1], item_id=r[0], category=r[2], volume_m3=r[3], active=bool(r[4]),
                           meta_level=r[5]) for r in rows]


def save_shortlist_snapshot(rows: list[ShortlistRow], run_ts: str, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO shortlist_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, r.item_id, r.item, r.category, r.landed_cost, r.net_sell, r.sell_volume,
              r.own_orders_remaining, r.profit_per_unit, r.margin, r.profit_per_m3, r.decision,
              int(r.active), r.volume_m3, r.jita_sell, r.import_cost, r.meta_level) for r in rows],
        )


def save_candidate_universe(candidates: list[Candidate], run_ts: str, table: str = "candidate_universe",
                             db_path: Path = DB_PATH) -> None:
    # These tables are read in full (storage.read_table, no run_ts filter) as
    # a "current state" snapshot, not as append-only history - replace the
    # contents on every run instead of accumulating duplicates forever.
    assert table in ("candidate_universe", "focused_candidates")
    with connect(db_path) as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?)",
            [(run_ts, c.item, c.type_id, c.volume_m3, c.category, c.market_group_path, c.meta_level)
             for c in candidates],
        )


def save_new_candidates(results: list[NewCandidateResult], run_ts: str, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO new_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, r.item, r.category, r.type_id, r.volume_m3, r.paired_days, r.profitable_days,
              r.hit_rate, r.latest_margin, r.best_margin, r.avg_profit_m3, r.avg_sell_movement,
              r.score, r.recommendation, int(r.add), r.meta_level) for r in results],
        )


def save_goonmetrics_history(points, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO goonmetrics_history VALUES (?,?,?,?,?,?,?,?)",
            [(p.region_id, p.type_id, p.date, p.min_price, p.max_price, p.avg_price,
              p.movement, p.num_orders) for p in points],
        )


def save_realized_trades(trades: list[RealizedTrade], run_ts: str, db_path: Path = DB_PATH) -> None:
    # Replaced wholesale, not appended - do_reconcile_trades() re-matches the
    # *entire* cfg.lookback_days window from scratch on every run (not
    # incrementally), and the only read site (api/routers/trading.py
    # get_realized_trades) always filters to MAX(run_ts) - so every previous
    # run's rows are both redundant (near-total overlap with the new run's
    # window) and permanently unreachable. Confirmed real bug: a naked INSERT
    # here had bloated the table to ~19.8k rows across only 13 runs, with
    # some exact (type_id, buy_date, sell_date, matched_qty) groups repeated
    # up to 24 times.
    with connect(db_path) as conn:
        conn.execute("DELETE FROM realized_trades")
        conn.executemany(
            "INSERT INTO realized_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, t.type_id, t.item, t.buy_date, t.buy_qty, t.buy_unit_price,
              t.sell_date, t.sell_qty, t.sell_unit_price, t.matched_qty,
              t.realized_profit, t.margin) for t in trades],
        )


# --------------------------------------------------------- Production: SDE cache
def replace_sde_data(
    types: list[tuple], groups: list[tuple], market_groups: list[tuple],
    blueprint_time: list[tuple], blueprint_materials: list[tuple], blueprint_products: list[tuple],
    invention_probability: list[tuple] = (), solar_systems: list[tuple] = (),
    stations: list[tuple] = (), categories: list[tuple] = (), db_path: Path = DB_PATH,
) -> None:
    """Wholesale-replaces the SDE cache tables (each refresh reflects one Fuzzwork
    dump snapshot, not an incremental merge - stale rows from a previous CCP
    patch would otherwise linger)."""
    with connect(db_path) as conn:
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


def sde_row_counts(db_path: Path = DB_PATH) -> dict[str, int]:
    tables = ["sde_types", "sde_groups", "sde_market_groups", "sde_blueprint_time",
              "sde_blueprint_materials", "sde_blueprint_products", "sde_invention_probability",
              "sde_solar_systems", "sde_stations", "sde_categories"]
    with connect(db_path) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def set_sde_refresh_state(refreshed_at: str, dump_etag: Optional[str], db_path: Path = DB_PATH) -> None:
    """Records when refresh_sde() last completed and the Fuzzwork dump's ETag
    at that moment - see sde_refresh_state's schema comment."""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sde_refresh_state (id, refreshed_at, dump_etag) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET refreshed_at = excluded.refreshed_at, dump_etag = excluded.dump_etag",
            (refreshed_at, dump_etag),
        )


def get_sde_refresh_state(db_path: Path = DB_PATH) -> Optional[tuple[str, Optional[str]]]:
    """Returns (refreshed_at, dump_etag) from the last refresh_sde() call, or
    None if the SDE cache has never been refreshed since this field was
    added."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT refreshed_at, dump_etag FROM sde_refresh_state WHERE id = 1").fetchone()
    return tuple(row) if row else None


def get_candidate_universe_built_at(db_path: Path = DB_PATH) -> Optional[str]:
    """Latest run_ts in candidate_universe - when Trading's "Load Market
    Groups" snapshot was last (re)built (see actions.do_build_universe).
    Compared against get_sde_refresh_state() to warn when the SDE cache has
    moved on since - a new item added to the SDE isn't visible to Trading
    until this snapshot is rebuilt, even though Production's own scans
    (which walk the live SDE cache directly, no snapshot) already see it."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT MAX(run_ts) FROM candidate_universe").fetchone()
    return row[0] if row and row[0] else None


def load_sde_category_names(db_path: Path = DB_PATH) -> dict[int, str]:
    """category_id -> real SDE category name (e.g. 7 -> "Module", 20 ->
    "Implant") - lets candidate_discovery.guess_category show the actual EVE
    category instead of a crude Module-vs-everything-else split."""
    with connect(db_path) as conn:
        rows = conn.execute("SELECT category_id, category_name FROM sde_categories").fetchall()
    return {r[0]: r[1] for r in rows}


@lru_cache(maxsize=8)
def get_station_ids_in_system(system_id: int, db_path: Path = DB_PATH) -> frozenset:
    """Static SDE lookup (see get_sde_type) - station_ids for `system_id`, used
    to check whether an ESI asset's location_id sits in that system (e.g. "is
    this asset in Jita") without a live ESI lookup per station."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT station_id FROM sde_stations WHERE solar_system_id = ?", (system_id,)
        ).fetchall()
    return frozenset(r[0] for r in rows)


@lru_cache(maxsize=8)
def get_station_ids_in_region(region_id: int, db_path: Path = DB_PATH) -> frozenset:
    """Static SDE lookup - every NPC station_id in `region_id` (e.g. every
    station in The Forge, not just Jita itself). Confirmed with the user:
    trade_reconciliation.py's buyer-side location filter should accept a buy
    anywhere in the region, not just Jita's own solar system - a trader can
    legitimately buy from other Forge stations too."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT s.station_id FROM sde_stations s "
            "JOIN sde_solar_systems sys ON sys.solar_system_id = s.solar_system_id "
            "WHERE sys.region_id = ?", (region_id,),
        ).fetchall()
    return frozenset(r[0] for r in rows)


def load_sde_market_groups(db_path: Path = DB_PATH) -> list[tuple[int, Optional[int], str]]:
    """Returns (market_group_id, parent_group_id, market_group_name) for every
    cached market group - static data (see production/sde.py), used by
    candidate_discovery.py to build the candidate universe locally instead of
    walking ESI's market-group tree live."""
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT market_group_id, parent_group_id, market_group_name FROM sde_market_groups"
        ).fetchall()


def load_sde_types_with_market_group(db_path: Path = DB_PATH) -> list[tuple[int, str, float, int, Optional[int], Optional[int]]]:
    """Returns (type_id, type_name, volume, market_group_id, meta_level,
    category_id) for every published, market-grouped type - static data, same
    use as load_sde_market_groups(). category_id (via sde_groups) lets
    candidate_discovery.guess_category classify by real SDE data
    (category_id == MODULE_CATEGORY_ID) instead of string-matching "module"/
    "rig" in the item name/market-group path."""
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT t.type_id, t.type_name, t.volume, t.market_group_id, t.meta_level, g.category_id "
            "FROM sde_types t JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE t.published = 1 AND t.market_group_id IS NOT NULL"
        ).fetchall()


@lru_cache(maxsize=32)
def get_system_security(system_id: Optional[int], db_path: Path = DB_PATH) -> Optional[float]:
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
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT security FROM sde_solar_systems WHERE solar_system_id = ?", (system_id,)
        ).fetchone()
    return row[0] if row else None


# ----------------------------------------------------- Production: user settings
def upsert_stock_target(type_id: int, type_name: str, backup_stock: Optional[float] = None,
                         home_market_stock: Optional[float] = None, jita_market_stock: Optional[float] = None,
                         db_path: Path = DB_PATH) -> None:
    """Any of the three stock values left as None keeps whatever is already
    stored (falling back to 0 only for a brand-new row) - same COALESCE
    pattern as shortlist.meta_level - so e.g. editing just the home-market
    target doesn't silently reset backup stock to 0."""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name, backup_stock, home_market_stock, jita_market_stock) "
            "VALUES (?,?,COALESCE(?, 0),?,?) "
            "ON CONFLICT(type_id) DO UPDATE SET type_name=excluded.type_name, "
            "backup_stock=CASE WHEN ? IS NULL THEN stock_targets.backup_stock ELSE excluded.backup_stock END, "
            "home_market_stock=COALESCE(excluded.home_market_stock, stock_targets.home_market_stock), "
            "jita_market_stock=COALESCE(excluded.jita_market_stock, stock_targets.jita_market_stock)",
            (type_id, type_name, backup_stock, home_market_stock, jita_market_stock, backup_stock),
        )


def delete_stock_target(type_id: int, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM stock_targets WHERE type_id = ?", (type_id,))


def load_stock_targets(db_path: Path = DB_PATH) -> list[tuple[int, str, float, Optional[float], Optional[float]]]:
    """Returns (type_id, type_name, backup_stock, home_market_stock, jita_market_stock)."""
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT type_id, type_name, backup_stock, home_market_stock, jita_market_stock FROM stock_targets"
        ).fetchall()


def upsert_manual_stock(type_id: int, count: float, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO manual_stock (type_id, count) VALUES (?,?) "
            "ON CONFLICT(type_id) DO UPDATE SET count=excluded.count",
            (type_id, count),
        )


def load_manual_stock(db_path: Path = DB_PATH) -> dict[int, float]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT type_id, count FROM manual_stock").fetchall()
    return {r[0]: r[1] for r in rows}


def upsert_manual_build_buy(type_id: int, decision: str, db_path: Path = DB_PATH) -> None:
    assert decision in ("Build", "Buy")
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO manual_build_buy (type_id, decision) VALUES (?,?) "
            "ON CONFLICT(type_id) DO UPDATE SET decision=excluded.decision",
            (type_id, decision),
        )


def load_manual_build_buy(db_path: Path = DB_PATH) -> dict[int, str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT type_id, decision FROM manual_build_buy").fetchall()
    return {r[0]: r[1] for r in rows}


def delete_manual_build_buy(type_id: int, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM manual_build_buy WHERE type_id = ?", (type_id,))


def upsert_selected_decryptor(type_id: int, decryptor: str, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO selected_decryptors (type_id, decryptor) VALUES (?,?) "
            "ON CONFLICT(type_id) DO UPDATE SET decryptor=excluded.decryptor",
            (type_id, decryptor),
        )


def load_selected_decryptors(db_path: Path = DB_PATH) -> dict[int, str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT type_id, decryptor FROM selected_decryptors").fetchall()
    return {r[0]: r[1] for r in rows}


def delete_selected_decryptor(type_id: int, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM selected_decryptors WHERE type_id = ?", (type_id,))


def upsert_category_location(category: str, location_id: int, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO job_category_locations (category, location_id) VALUES (?,?) "
            "ON CONFLICT(category) DO UPDATE SET location_id=excluded.location_id",
            (category, location_id),
        )


def load_category_locations(db_path: Path = DB_PATH) -> dict[str, int]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT category, location_id FROM job_category_locations").fetchall()
    return {r[0]: r[1] for r in rows}


def delete_category_location(category: str, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM job_category_locations WHERE category = ?", (category,))


def add_category_location_option(category: str, location_id: int, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO category_location_options (category, location_id) VALUES (?,?)",
            (category, location_id),
        )


def load_category_location_options(db_path: Path = DB_PATH) -> dict[str, list[int]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT category, location_id FROM category_location_options ORDER BY category, location_id"
        ).fetchall()
    result: dict[str, list[int]] = {}
    for category, location_id in rows:
        result.setdefault(category, []).append(location_id)
    return result


def delete_category_location_option(category: str, location_id: int, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM category_location_options WHERE category = ? AND location_id = ?",
            (category, location_id),
        )


def get_cached_structure_name(location_id: int, db_path: Path = DB_PATH) -> tuple[bool, Optional[str]]:
    """Returns (was_cached, name). was_cached=False means resolution has
    never been attempted for this location_id - was_cached=True with
    name=None means it *was* attempted and failed (no producer character
    could see that structure), so callers know not to silently keep retrying
    every page load."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM structure_names WHERE location_id = ?", (location_id,),
        ).fetchone()
    if row is None:
        return False, None
    return True, row[0]


def get_cached_structure_names(location_ids: list[int], db_path: Path = DB_PATH) -> dict[int, tuple[bool, Optional[str]]]:
    """Batched form of get_cached_structure_name above - one query instead of
    N. Confirmed real (if minor - storage.py's own connect() docstring notes
    ~3ms/call) N+1-connection gap: GET /logistics/structure-names used to
    call get_cached_structure_name once per location_id in a Python loop.
    Returns {location_id: (was_cached, name)} for every id in location_ids,
    same was_cached semantics as the single-id version."""
    if not location_ids:
        return {}
    placeholders = ",".join("?" * len(location_ids))
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT location_id, name FROM structure_names WHERE location_id IN ({placeholders})",
            location_ids,
        ).fetchall()
    found = dict(rows)
    return {loc_id: (loc_id in found, found.get(loc_id)) for loc_id in location_ids}
    return True, row[0]


def set_cached_structure_name(location_id: int, name: Optional[str], db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO structure_names (location_id, name) VALUES (?, ?) "
            "ON CONFLICT(location_id) DO UPDATE SET name=excluded.name",
            (location_id, name),
        )


# --------------------------------------------------- Production: ESI-derived stock
def replace_assets(table: str, rows: list[tuple], db_path: Path = DB_PATH) -> None:
    assert table in ("character_assets", "corp_assets")
    with connect(db_path) as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (item_id, type_id, location_id, location_flag, quantity, "
            "is_blueprint_copy, owner_name) VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def replace_industry_jobs(table: str, rows: list[tuple], db_path: Path = DB_PATH) -> None:
    assert table in ("character_industry_jobs", "corp_industry_jobs")
    with connect(db_path) as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (job_id, activity_id, blueprint_type_id, product_type_id, runs, "
            "output_location_id, status, end_date, start_date, installer_id, installer_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def replace_character_slots(rows: list[tuple], db_path: Path = DB_PATH) -> None:
    """rows: [(character_name, manufacturing_slots, reaction_slots, science_slots), ...]."""
    with connect(db_path) as conn:
        conn.execute("DELETE FROM character_slots")
        conn.executemany("INSERT INTO character_slots VALUES (?,?,?,?)", rows)


def load_character_slots(db_path: Path = DB_PATH) -> list[tuple]:
    """Returns [(character_name, manufacturing_slots, reaction_slots, science_slots), ...]."""
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT character_name, manufacturing_slots, reaction_slots, science_slots "
            "FROM character_slots ORDER BY character_name"
        ).fetchall()


def get_product_quantity(blueprint_type_id: int, activity_id: int, product_type_id: int,
                          db_path: Path = DB_PATH) -> Optional[float]:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT quantity FROM sde_blueprint_products "
            "WHERE blueprint_type_id = ? AND activity_id = ? AND product_type_id = ?",
            (blueprint_type_id, activity_id, product_type_id),
        ).fetchone()
    return row[0] if row else None


def list_industry_jobs(db_path: Path = DB_PATH) -> list[tuple]:
    """Returns every active character + corp industry job, each with the
    product's type_name joined in: (job_id, activity_id, blueprint_type_id,
    product_type_id, type_name, runs, output_location_id, status, end_date,
    start_date, installer_name)."""
    with connect(db_path) as conn:
        rows = []
        for table in ("character_industry_jobs", "corp_industry_jobs"):
            rows.extend(conn.execute(
                f"SELECT j.job_id, j.activity_id, j.blueprint_type_id, j.product_type_id, "
                f"t.type_name, j.runs, j.output_location_id, j.status, j.end_date, "
                f"j.start_date, j.installer_name "
                f"FROM {table} j LEFT JOIN sde_types t ON t.type_id = j.product_type_id"
            ).fetchall())
    return rows


def replace_blueprints(table: str, rows: list[tuple], db_path: Path = DB_PATH) -> None:
    assert table in ("character_blueprints", "corp_blueprints")
    with connect(db_path) as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?)", rows)


def replace_sell_orders(rows: list[tuple], db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM character_sell_orders")
        conn.executemany("INSERT INTO character_sell_orders VALUES (?,?,?,?,?,?)", rows)


def sell_order_qty_at_location(type_id: int, location_id: int, db_path: Path = DB_PATH) -> float:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(volume_remain), 0) FROM character_sell_orders "
            "WHERE type_id = ? AND location_id = ?",
            (type_id, location_id),
        ).fetchone()
    return row[0]


def sell_order_qty_in_region(type_id: int, region_id: int, db_path: Path = DB_PATH) -> float:
    with connect(db_path) as conn:
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


def esi_stock_at_location(type_id: int, location_id: Optional[int], db_path: Path = DB_PATH) -> float:
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
    with connect(db_path) as conn:
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


def search_item_stock_locations(type_id: int, db_path: Path = DB_PATH) -> list[tuple[int, Optional[str], str, float]]:
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
    with connect(db_path) as conn:
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


def set_esi_sync_time(scope: str, synced_at: str, db_path: Path = DB_PATH) -> None:
    """`scope`: "production" or "trading". `synced_at`: ISO-8601 UTC timestamp."""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO esi_sync_state (scope, synced_at) VALUES (?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET synced_at=excluded.synced_at",
            (scope, synced_at),
        )


def get_candidate_search_offset(db_path: Path = DB_PATH) -> int:
    """See candidate_search_cursor - defaults to 0 (start of the list) if no
    safe-mode search has run yet."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT offset_value FROM candidate_search_cursor WHERE id = 1").fetchone()
    return row[0] if row else 0


def set_candidate_search_offset(offset: int, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO candidate_search_cursor (id, offset_value) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET offset_value=excluded.offset_value",
            (offset,),
        )


def get_esi_sync_time(scope: str, db_path: Path = DB_PATH) -> Optional[str]:
    with connect(db_path) as conn:
        row = conn.execute("SELECT synced_at FROM esi_sync_state WHERE scope = ?", (scope,)).fetchone()
    return row[0] if row else None


def esi_incoming_industry_qty(product_type_id: int, db_path: Path = DB_PATH) -> dict[str, float]:
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
    with connect(db_path) as conn:
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


def load_owned_blueprints(db_path: Path = DB_PATH) -> list[tuple]:
    """Returns (type_id, quantity, material_efficiency, time_efficiency, runs)
    across character + corp blueprints, for informational display."""
    with connect(db_path) as conn:
        rows = []
        for table in ("character_blueprints", "corp_blueprints"):
            rows.extend(conn.execute(
                f"SELECT type_id, quantity, material_efficiency, time_efficiency, runs FROM {table}"
            ).fetchall())
    return rows


def get_owned_bpo_best_me_te(blueprint_type_id: int, db_path: Path = DB_PATH) -> Optional[tuple[int, int]]:
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
    with connect(db_path) as conn:
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
def read_table(table: str, db_path: Path = DB_PATH) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)


def latest_snapshot(db_path: Path = DB_PATH) -> pd.DataFrame:
    with connect(db_path) as conn:
        run_ts = conn.execute("SELECT MAX(run_ts) FROM shortlist_snapshot").fetchone()[0]
        if not run_ts:
            return pd.DataFrame()
        return pd.read_sql_query(
            "SELECT * FROM shortlist_snapshot WHERE run_ts = ?", conn, params=(run_ts,)
        )


# ------------------------------------------------------------- Production: SDE reads
def search_sde_types(query: str, limit: int = 20, db_path: Path = DB_PATH) -> list[tuple[int, str]]:
    """Type-ahead lookup for the Stock Targets editor. An exact (case-insensitive)
    match always sorts first, regardless of `limit` - otherwise e.g. "Vexor"
    could get pushed out of a small limit by the many longer names ("Guardian-
    Vexor", ...) that also contain it as a substring and sort earlier."""
    query = query.strip()
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT type_id, type_name FROM sde_types "
            "WHERE published = 1 AND type_name LIKE ? "
            "ORDER BY (LOWER(type_name) <> LOWER(?)), type_name LIMIT ?",
            (f"%{query}%", query, limit),
        ).fetchall()
    return rows


@lru_cache(maxsize=None)
def get_sde_type(type_id: int, db_path: Path = DB_PATH) -> Optional[tuple]:
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
    with connect(db_path) as conn:
        return conn.execute("SELECT * FROM sde_types WHERE type_id = ?", (type_id,)).fetchone()


@lru_cache(maxsize=None)
def get_type_category(type_id: int, db_path: Path = DB_PATH) -> Optional[int]:
    """Returns the type's SDE category_id (e.g. 6 = Ship) via its group, or
    None if the type/group isn't in the SDE cache. Cached - see get_sde_type."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT g.category_id FROM sde_types t JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE t.type_id = ?", (type_id,),
        ).fetchone()
    return row[0] if row else None


def get_cached_packaged_volume(type_id: int, db_path: Path = DB_PATH) -> Optional[float]:
    """None means "never looked up yet" - distinct from a cached value that
    happens to equal the flight volume, so callers know whether to fetch."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT packaged_volume FROM type_packaged_volume WHERE type_id = ?", (type_id,),
        ).fetchone()
    return row[0] if row else None


def set_cached_packaged_volume(type_id: int, packaged_volume: float, db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO type_packaged_volume (type_id, packaged_volume) VALUES (?, ?) "
            "ON CONFLICT(type_id) DO UPDATE SET packaged_volume=excluded.packaged_volume",
            (type_id, packaged_volume),
        )


@lru_cache(maxsize=None)
def get_blueprint_for_product(product_type_id: int, db_path: Path = DB_PATH) -> Optional[tuple[int, int, float]]:
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
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT p.blueprint_type_id, p.activity_id, p.quantity FROM sde_blueprint_products p "
            "JOIN sde_types t ON t.type_id = p.blueprint_type_id "
            "WHERE p.product_type_id = ? AND p.activity_id IN (1, 11) "
            "ORDER BY t.published DESC, p.activity_id, p.blueprint_type_id LIMIT 1",
            (product_type_id,),
        ).fetchone()
    return row


@lru_cache(maxsize=None)
def get_blueprint_materials(blueprint_type_id: int, activity_id: int,
                             db_path: Path = DB_PATH) -> list[tuple[int, float]]:
    """Returns [(material_type_id, quantity), ...] for one run at ME 0 (before any reduction).
    Cached - see get_sde_type. The returned list is shared across callers
    (lru_cache returns the same object every time) - callers must treat it as
    read-only (all current callers only iterate it, never mutate)."""
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT material_type_id, quantity FROM sde_blueprint_materials "
            "WHERE blueprint_type_id = ? AND activity_id = ?",
            (blueprint_type_id, activity_id),
        ).fetchall()


@lru_cache(maxsize=None)
def get_blueprint_time(blueprint_type_id: int, activity_id: int, db_path: Path = DB_PATH) -> Optional[float]:
    """Cached - see get_sde_type."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT time FROM sde_blueprint_time WHERE blueprint_type_id = ? AND activity_id = ?",
            (blueprint_type_id, activity_id),
        ).fetchone()
    return row[0] if row else None


def find_invention_recipe_by_product_name(product_name: str, db_path: Path = DB_PATH) -> Optional[tuple[int, int]]:
    """Given a T2/T3 blueprint's name (the thing you want to invent), returns
    (t1_blueprint_type_id, product_type_id) - the invention recipe that
    produces it. None if `product_name` isn't an invented type.

    Case-insensitive (confirmed real bug: "loki blueprint" - any case other
    than the SDE's exact stored casing - returned None even though
    search_sde_types elsewhere in this file already handles this correctly
    with LOWER()), and tolerant of incidental leading/trailing whitespace
    (matches search_sde_types' own .strip(), same fix)."""
    product_name = product_name.strip()
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT p.blueprint_type_id, p.product_type_id FROM sde_blueprint_products p "
            "JOIN sde_types t ON t.type_id = p.product_type_id "
            "WHERE p.activity_id = 8 AND LOWER(t.type_name) = LOWER(?)",
            (product_name,),
        ).fetchone()


@lru_cache(maxsize=None)
def find_invention_recipe_by_product_type_id(product_blueprint_type_id: int,
                                              db_path: Path = DB_PATH) -> Optional[int]:
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
    with connect(db_path) as conn:
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


def get_invention_recipe(t1_blueprint_type_id: int, db_path: Path = DB_PATH) -> Optional[dict]:
    """Returns the full invention job definition for `t1_blueprint_type_id`:
    product_type_id, base_runs (the invented BPC's run count before decryptor
    bonus), base_probability, job time, and datacore requirements."""
    with connect(db_path) as conn:
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
