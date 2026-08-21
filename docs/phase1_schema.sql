-- Phase 1 of the multi-tenant migration (see docs/MULTI_TENANT_PLAN.md) - the
-- complete Postgres schema for every table currently in eve_trader/storage.py's
-- SCHEMA string, split into the three buckets worked out in that plan's
-- "Shared vs. per-tenant tables" / "Composite primary keys" sections:
--   1. Shared SDE/reference tables - no tenant_id, no RLS.
--   2. Composite-PK bucket - tenant_id added, PK widened to (tenant_id, ...).
--   3. Column-only + no-PK bucket - tenant_id added, PK (if any) unchanged.
--
-- Supersedes docs/phase0_setup.sql for schema purposes (that file stays as
-- the historical Phase-0 record - stock_targets here is byte-for-byte the
-- same shape it created). Idempotent - every statement is safe to re-run
-- against an already-provisioned dev DB (CREATE TABLE/INDEX IF NOT EXISTS,
-- role creation guarded against duplicate_object, policies dropped and
-- recreated rather than assumed absent).
--
-- Usage (same local dev Postgres as Phase 0 - see docs/MULTI_TENANT_PLAN.md):
--   Get-Content docs\phase1_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--
-- Owner role (postgres) runs this DDL; the app connects as the separate,
-- non-superuser eve_trader_app role - RLS is silently skipped for a table's
-- owner/superuser otherwise.

DO $$
BEGIN
    CREATE ROLE eve_trader_app WITH LOGIN PASSWORD 'app_devpassword' NOSUPERUSER NOBYPASSRLS;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END
$$;

GRANT USAGE ON SCHEMA public TO eve_trader_app;

-- ============================================================== shared tables
-- 12 SDE reference tables + goonmetrics_history - identical for every tenant,
-- refreshed globally. No tenant_id, no RLS.

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

CREATE TABLE IF NOT EXISTS type_packaged_volume (
    type_id INTEGER PRIMARY KEY,
    packaged_volume REAL
);

CREATE TABLE IF NOT EXISTS sde_groups (
    group_id INTEGER PRIMARY KEY,
    category_id INTEGER,
    group_name TEXT
);

CREATE TABLE IF NOT EXISTS sde_categories (
    category_id INTEGER PRIMARY KEY,
    category_name TEXT
);

CREATE TABLE IF NOT EXISTS sde_market_groups (
    market_group_id INTEGER PRIMARY KEY,
    parent_group_id INTEGER,
    market_group_name TEXT
);

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

CREATE INDEX IF NOT EXISTS idx_sde_blueprint_materials_lookup
    ON sde_blueprint_materials (blueprint_type_id, activity_id);

CREATE TABLE IF NOT EXISTS sde_blueprint_products (
    blueprint_type_id INTEGER,
    activity_id INTEGER,
    product_type_id INTEGER,
    quantity REAL,
    PRIMARY KEY (blueprint_type_id, activity_id, product_type_id)
);

CREATE INDEX IF NOT EXISTS idx_sde_blueprint_products_by_product
    ON sde_blueprint_products (product_type_id, activity_id);

CREATE TABLE IF NOT EXISTS sde_invention_probability (
    t1_blueprint_type_id INTEGER,
    product_type_id INTEGER,
    probability REAL,
    PRIMARY KEY (t1_blueprint_type_id, product_type_id)
);

CREATE TABLE IF NOT EXISTS sde_solar_systems (
    solar_system_id INTEGER PRIMARY KEY,
    solar_system_name TEXT,
    security REAL,
    region_id INTEGER
);

CREATE TABLE IF NOT EXISTS sde_stations (
    station_id INTEGER PRIMARY KEY,
    solar_system_id INTEGER,
    station_name TEXT
);

CREATE TABLE IF NOT EXISTS sde_refresh_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    refreshed_at TEXT NOT NULL,
    dump_etag TEXT
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

-- ===================================================== composite-PK bucket
-- PK is an app-level/literal value naturally reused across tenants (EVE type
-- IDs, literal category/scope strings, the id=1 singleton row) - PK widened
-- to (tenant_id, <original pk>).

CREATE TABLE IF NOT EXISTS stock_targets (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    type_name TEXT NOT NULL,
    backup_stock REAL DEFAULT 0,
    home_market_stock REAL,
    jita_market_stock REAL,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE stock_targets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON stock_targets;
CREATE POLICY tenant_isolation ON stock_targets
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS manual_stock (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    count REAL DEFAULT 0,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE manual_stock ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON manual_stock;
CREATE POLICY tenant_isolation ON manual_stock
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS manual_build_buy (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE manual_build_buy ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON manual_build_buy;
CREATE POLICY tenant_isolation ON manual_build_buy
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS selected_decryptors (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    decryptor TEXT NOT NULL,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE selected_decryptors ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON selected_decryptors;
CREATE POLICY tenant_isolation ON selected_decryptors
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS shortlist (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    item_id INTEGER NOT NULL,
    item TEXT NOT NULL,
    category TEXT,
    volume_m3 REAL,
    active INTEGER DEFAULT 1,
    meta_level INTEGER,
    PRIMARY KEY (tenant_id, item_id)
);
ALTER TABLE shortlist ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON shortlist;
CREATE POLICY tenant_isolation ON shortlist
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS shortlist_skip_streak (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    item_id INTEGER NOT NULL,
    skip_since TEXT NOT NULL,
    PRIMARY KEY (tenant_id, item_id)
);
ALTER TABLE shortlist_skip_streak ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON shortlist_skip_streak;
CREATE POLICY tenant_isolation ON shortlist_skip_streak
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS job_category_locations (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    category TEXT NOT NULL,
    location_id BIGINT NOT NULL,
    PRIMARY KEY (tenant_id, category)
);
ALTER TABLE job_category_locations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON job_category_locations;
CREATE POLICY tenant_isolation ON job_category_locations
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS esi_sync_state (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    scope TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, scope)
);
ALTER TABLE esi_sync_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON esi_sync_state;
CREATE POLICY tenant_isolation ON esi_sync_state
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS candidate_search_cursor (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    id INTEGER NOT NULL CHECK (id = 1),
    offset_value INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
ALTER TABLE candidate_search_cursor ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON candidate_search_cursor;
CREATE POLICY tenant_isolation ON candidate_search_cursor
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS structure_names (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    location_id BIGINT NOT NULL,
    name TEXT,
    solar_system_id INTEGER,
    PRIMARY KEY (tenant_id, location_id)
);
-- GitHub issue #12: lets production/engine.py's cost-index calculation look
-- up the actual system a Logistik category's assigned structure sits in,
-- instead of always using the flat component/manufacturing 2-way split -
-- see storage.load_category_system_ids.
ALTER TABLE structure_names ADD COLUMN IF NOT EXISTS solar_system_id INTEGER;
ALTER TABLE structure_names ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON structure_names;
CREATE POLICY tenant_isolation ON structure_names
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS category_location_options (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    category TEXT NOT NULL,
    location_id BIGINT NOT NULL,
    PRIMARY KEY (tenant_id, category, location_id)
);
ALTER TABLE category_location_options ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON category_location_options;
CREATE POLICY tenant_isolation ON category_location_options
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

-- ======================================================== column-only bucket
-- PK (item_id/job_id/order_id/character_name) is already globally unique per
-- EVE/ESI's own guarantees - tenant_id added for RLS row-visibility only, PK
-- left unchanged.

CREATE TABLE IF NOT EXISTS character_assets (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    item_id BIGINT PRIMARY KEY,
    type_id INTEGER,
    location_id BIGINT,
    location_flag TEXT,
    quantity INTEGER,
    is_blueprint_copy INTEGER,
    owner_name TEXT,
    resolved_location_id BIGINT
);
-- GitHub issue #4/#20: location_id is the item's *immediate* parent (a ship,
-- a container, a corp Office, ...), which can be several containers deep -
-- resolved_location_id is that chain walked all the way up to the outermost
-- station/structure (see storage.replace_assets), computed once at sync
-- time so every query can filter on it directly instead of re-walking the
-- chain (or missing anything past one level, the original bug) per query.
ALTER TABLE character_assets ADD COLUMN IF NOT EXISTS resolved_location_id BIGINT;
DROP INDEX IF EXISTS idx_character_assets_type_location;
CREATE INDEX IF NOT EXISTS idx_character_assets_type_resolved_location
    ON character_assets (type_id, resolved_location_id);
ALTER TABLE character_assets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_assets;
CREATE POLICY tenant_isolation ON character_assets
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS corp_assets (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    item_id BIGINT PRIMARY KEY,
    type_id INTEGER,
    location_id BIGINT,
    location_flag TEXT,
    quantity INTEGER,
    is_blueprint_copy INTEGER,
    owner_name TEXT,
    resolved_location_id BIGINT
);
ALTER TABLE corp_assets ADD COLUMN IF NOT EXISTS resolved_location_id BIGINT;
DROP INDEX IF EXISTS idx_corp_assets_type_location;
CREATE INDEX IF NOT EXISTS idx_corp_assets_type_resolved_location ON corp_assets (type_id, resolved_location_id);
ALTER TABLE corp_assets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON corp_assets;
CREATE POLICY tenant_isolation ON corp_assets
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS character_industry_jobs (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    job_id BIGINT PRIMARY KEY,
    activity_id INTEGER,
    blueprint_type_id INTEGER,
    product_type_id INTEGER,
    runs INTEGER,
    output_location_id BIGINT,
    status TEXT,
    end_date TEXT,
    start_date TEXT,
    installer_id BIGINT,
    installer_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_character_industry_jobs_product_status
    ON character_industry_jobs (product_type_id, status);
ALTER TABLE character_industry_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_industry_jobs;
CREATE POLICY tenant_isolation ON character_industry_jobs
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS corp_industry_jobs (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    job_id BIGINT PRIMARY KEY,
    activity_id INTEGER,
    blueprint_type_id INTEGER,
    product_type_id INTEGER,
    runs INTEGER,
    output_location_id BIGINT,
    status TEXT,
    end_date TEXT,
    start_date TEXT,
    installer_id BIGINT,
    installer_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_corp_industry_jobs_product_status
    ON corp_industry_jobs (product_type_id, status);
ALTER TABLE corp_industry_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON corp_industry_jobs;
CREATE POLICY tenant_isolation ON corp_industry_jobs
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS character_slots (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_name TEXT PRIMARY KEY,
    manufacturing_slots INTEGER,
    reaction_slots INTEGER,
    science_slots INTEGER
);
-- GitHub issue #39: lets a character be excluded from the free-slot pool
-- (character_slot_overview/_free_slots_by_category) and the asset-optimized
-- build list's slot-splitting, without un-registering their ESI sync
-- entirely. Not wiped by replace_character_slots' own re-sync (see that
-- function's own comment for why it's an UPSERT, not delete+reinsert).
ALTER TABLE character_slots ADD COLUMN IF NOT EXISTS excluded_from_planning BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE character_slots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_slots;
CREATE POLICY tenant_isolation ON character_slots
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS character_blueprints (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    item_id BIGINT PRIMARY KEY,
    type_id INTEGER,
    location_id BIGINT,
    location_flag TEXT,
    quantity INTEGER,
    material_efficiency INTEGER,
    time_efficiency INTEGER,
    runs INTEGER,
    resolved_location_id BIGINT
);
-- Same resolved_location_id idea as character_assets above (GitHub issue
-- #20: a BPC sitting inside a container read as "missing" since only its
-- container's own item_id, not the outer station/structure, was stored) -
-- resolved against character_assets/corp_assets by storage.replace_blueprints
-- (a blueprint's immediate container is always a regular asset).
ALTER TABLE character_blueprints ADD COLUMN IF NOT EXISTS resolved_location_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_character_blueprints_type_runs ON character_blueprints (type_id, runs);
CREATE INDEX IF NOT EXISTS idx_character_blueprints_type_resolved_location
    ON character_blueprints (type_id, resolved_location_id);
ALTER TABLE character_blueprints ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_blueprints;
CREATE POLICY tenant_isolation ON character_blueprints
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS corp_blueprints (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    item_id BIGINT PRIMARY KEY,
    type_id INTEGER,
    location_id BIGINT,
    location_flag TEXT,
    quantity INTEGER,
    material_efficiency INTEGER,
    time_efficiency INTEGER,
    runs INTEGER,
    resolved_location_id BIGINT
);
ALTER TABLE corp_blueprints ADD COLUMN IF NOT EXISTS resolved_location_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_corp_blueprints_type_runs ON corp_blueprints (type_id, runs);
CREATE INDEX IF NOT EXISTS idx_corp_blueprints_type_resolved_location
    ON corp_blueprints (type_id, resolved_location_id);
ALTER TABLE corp_blueprints ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON corp_blueprints;
CREATE POLICY tenant_isolation ON corp_blueprints
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS character_sell_orders (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    order_id BIGINT PRIMARY KEY,
    type_id INTEGER,
    location_id BIGINT,
    region_id INTEGER,
    volume_remain INTEGER,
    character_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_character_sell_orders_type_location ON character_sell_orders (type_id, location_id);
CREATE INDEX IF NOT EXISTS idx_character_sell_orders_type_region ON character_sell_orders (type_id, region_id);
ALTER TABLE character_sell_orders ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_sell_orders;
CREATE POLICY tenant_isolation ON character_sell_orders
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

-- ==================================================== no-PK append/snapshot
-- Run-history tables with no natural PK at all - tenant_id + RLS only. An
-- index on tenant_id alone (nothing else to lean on) keeps the RLS filter a
-- seek rather than a per-query full scan.

CREATE TABLE IF NOT EXISTS shortlist_snapshot (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
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
CREATE INDEX IF NOT EXISTS idx_shortlist_snapshot_tenant ON shortlist_snapshot (tenant_id);
ALTER TABLE shortlist_snapshot ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON shortlist_snapshot;
CREATE POLICY tenant_isolation ON shortlist_snapshot
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS candidate_universe (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    run_ts TEXT,
    item TEXT,
    type_id INTEGER,
    volume_m3 REAL,
    category TEXT,
    market_group_path TEXT,
    meta_level INTEGER
);
CREATE INDEX IF NOT EXISTS idx_candidate_universe_tenant ON candidate_universe (tenant_id);
ALTER TABLE candidate_universe ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON candidate_universe;
CREATE POLICY tenant_isolation ON candidate_universe
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS focused_candidates (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    run_ts TEXT,
    item TEXT,
    type_id INTEGER,
    volume_m3 REAL,
    category TEXT,
    market_group_path TEXT,
    meta_level INTEGER
);
CREATE INDEX IF NOT EXISTS idx_focused_candidates_tenant ON focused_candidates (tenant_id);
ALTER TABLE focused_candidates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON focused_candidates;
CREATE POLICY tenant_isolation ON focused_candidates
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS new_candidates (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
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
CREATE INDEX IF NOT EXISTS idx_new_candidates_tenant ON new_candidates (tenant_id);
ALTER TABLE new_candidates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON new_candidates;
CREATE POLICY tenant_isolation ON new_candidates
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS realized_trades (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
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
CREATE INDEX IF NOT EXISTS idx_realized_trades_tenant ON realized_trades (tenant_id);
ALTER TABLE realized_trades ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON realized_trades;
CREATE POLICY tenant_isolation ON realized_trades
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

-- ===================================================================== grants
-- One blanket grant covers shared tables too - production/sde.py's
-- refresh_sde() writes SDE tables through the same app connection today,
-- there is no separate owner-only write path.

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eve_trader_app;
