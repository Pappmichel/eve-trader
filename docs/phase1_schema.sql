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
    location_id BIGINT NOT NULL DEFAULT 0,   -- 0 = "no location"
    count REAL DEFAULT 0,
    PRIMARY KEY (tenant_id, type_id, location_id)
);
-- docs/MANUAL_TRACKING_PLAN.md phase 3 (decision 15): widened from
-- (tenant_id, type_id) so the same type can have separate manual-stock
-- entries per location. Idempotent for an already-provisioned DB - existing
-- rows end up at location_id = 0 ("no location"), same total as before
-- (load_manual_stock still SUMs across locations, see storage.py).
ALTER TABLE manual_stock ADD COLUMN IF NOT EXISTS location_id BIGINT NOT NULL DEFAULT 0;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
    WHERE c.conrelid = 'manual_stock'::regclass AND c.contype = 'p' AND a.attname = 'location_id'
  ) THEN
    ALTER TABLE manual_stock DROP CONSTRAINT manual_stock_pkey;
    ALTER TABLE manual_stock ADD PRIMARY KEY (tenant_id, type_id, location_id);
  END IF;
END $$;
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

-- GitHub issue #40: manually-registered purchase cost + included run count
-- for a blueprint *copy* that must be bought outright (never owned as a
-- BPO, not inventable) - e.g. a faction/officer BPC only obtainable from an
-- LP store or the market. type_id is the *product* built from that BPC
-- (matches how the Blueprints page's second table lets a user pick it), not
-- the blueprint's own type_id - a blueprint's product is a stable 1:1
-- lookup (storage.get_blueprint_for_product) so this is unambiguous either
-- way, and product_type_id is what _unit_cost already keys everything else
-- on.
CREATE TABLE IF NOT EXISTS manual_blueprint_copy_costs (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    type_name TEXT NOT NULL,
    purchase_cost DOUBLE PRECISION NOT NULL,
    runs INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, type_id)
);
-- ISK values need double precision, not REAL (single-precision/~7 sig
-- digits) - a 500M+ ISK price silently rounds to the nearest ~32-256 ISK in
-- REAL. Widens an already-provisioned DB's column in place (CREATE TABLE IF
-- NOT EXISTS above is a no-op there); a fresh DB gets DOUBLE PRECISION
-- directly. Found in a full-codebase audit (2026-08-28), confirmed live:
-- REAL rounding was already visible in realized_trades' stored prices (see
-- that table's own comment below).
ALTER TABLE manual_blueprint_copy_costs ALTER COLUMN purchase_cost TYPE DOUBLE PRECISION;
ALTER TABLE manual_blueprint_copy_costs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON manual_blueprint_copy_costs;
CREATE POLICY tenant_isolation ON manual_blueprint_copy_costs
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

-- Manual per-blueprint ME/TE override (confirmed with the user 2026-09-16) -
-- lets a user enter a fixed ME/TE for a blueprint's product, regardless of
-- whether they own a real BPO with that research level, or whether the
-- blueprint is one of the four non-researchable categories engine.py's own
-- classify_activity/ACTIVITY_MODS otherwise pins at ME0/TE0 (Faction/
-- Storyline/Officer/Deadspace) - the top-priority entry in _activity_mods'
-- own ME/TE resolution chain, same "an explicit per-item entry always wins
-- over the app's inferred/default value" precedent as
-- manual_blueprint_copy_costs (GitHub issue #40) directly above. type_id is
-- the *product* built from the blueprint, matching that same table's own
-- convention - a blueprint's product is a stable 1:1 lookup
-- (storage.get_blueprint_for_product) either way. material_efficiency/
-- time_efficiency are stored as the raw 0-10/0-20 ME/TE levels (not
-- pre-converted multipliers) so the Blueprints page can display/edit them
-- in the same units EVE itself uses - engine.py converts to a multiplier
-- (1 - level/100) at read time, same formula _owned_bpo_mods already uses
-- for a real owned BPO's ME/TE.
CREATE TABLE IF NOT EXISTS manual_blueprint_me_te_overrides (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    type_name TEXT NOT NULL,
    material_efficiency INTEGER NOT NULL,
    time_efficiency INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE manual_blueprint_me_te_overrides ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON manual_blueprint_me_te_overrides;
CREATE POLICY tenant_isolation ON manual_blueprint_me_te_overrides
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
    refreshed_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, item_id)
);
ALTER TABLE shortlist ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON shortlist;
CREATE POLICY tenant_isolation ON shortlist
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
ALTER TABLE shortlist ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMPTZ;

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

-- docs/MANUAL_TRACKING_PLAN.md phase 2 (decision 8): manual location names
-- are per-tenant, not global - unlike global_structure_names
-- (docs/admin_schema.sql), a name given here is only ever this tenant's own
-- opinion of what to call a location, never shared or copied into the
-- global cache.
CREATE TABLE IF NOT EXISTS manual_location_names (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    location_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (tenant_id, location_id)
);
ALTER TABLE manual_location_names ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON manual_location_names;
CREATE POLICY tenant_isolation ON manual_location_names
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON manual_location_names TO eve_trader_app;

-- docs/MANUAL_TRACKING_PLAN.md phase 5 - manually-tracked owned blueprints
-- (BPOs/BPCs), additive alongside ESI-synced character_blueprints/
-- corp_blueprints (see production/engine.py's _owned_bpo_best_me_te/
-- _available_blueprint_copies/_has_bpo_at_location). blueprint_type_id is
-- the blueprint's own type, not the product it builds (the engine's key).
CREATE TABLE IF NOT EXISTS manual_owned_blueprints (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    blueprint_type_id INTEGER NOT NULL,
    is_original BOOLEAN NOT NULL,
    material_efficiency INTEGER NOT NULL CHECK (material_efficiency BETWEEN 0 AND 10),
    time_efficiency INTEGER NOT NULL CHECK (time_efficiency BETWEEN 0 AND 20),
    runs INTEGER CHECK (runs IS NULL OR runs > 0),   -- NULL for a BPO
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    location_id BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((is_original AND runs IS NULL) OR (NOT is_original AND runs IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS manual_owned_blueprints_tenant_bp_idx
    ON manual_owned_blueprints (tenant_id, blueprint_type_id);
ALTER TABLE manual_owned_blueprints ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON manual_owned_blueprints;
CREATE POLICY tenant_isolation ON manual_owned_blueprints
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON manual_owned_blueprints TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE manual_owned_blueprints_id_seq TO eve_trader_app;

-- docs/MANUAL_TRACKING_PLAN.md phase 6 - manually-tracked running industry
-- jobs, additive alongside ESI-synced character_industry_jobs/
-- corp_industry_jobs. quantity is the value production/engine.py's
-- _current_stock actually adds (decision 2); runs is display-only, set
-- only when the job was entered as runs rather than a raw quantity.
-- location_id is the job's output location - also the default target for
-- "Complete" (decision 12).
CREATE TABLE IF NOT EXISTS manual_industry_jobs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    product_type_id INTEGER NOT NULL,
    activity_id INTEGER NOT NULL CHECK (activity_id IN (1, 11)),
    quantity DOUBLE PRECISION NOT NULL CHECK (quantity > 0),
    runs INTEGER CHECK (runs IS NULL OR runs > 0),
    location_id BIGINT NOT NULL DEFAULT 0,
    ready_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS manual_industry_jobs_tenant_product_idx
    ON manual_industry_jobs (tenant_id, product_type_id);
ALTER TABLE manual_industry_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON manual_industry_jobs;
CREATE POLICY tenant_isolation ON manual_industry_jobs
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON manual_industry_jobs TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE manual_industry_jobs_id_seq TO eve_trader_app;

-- docs/MANUAL_TRACKING_PLAN.md phase 7 (decision 7) - manually-tracked
-- quantities already listed for sale at home/Jita, additive alongside the
-- ESI-derived open-sell-order volume production/engine.py's
-- _total_missing/market_status already compute.
CREATE TABLE IF NOT EXISTS manual_listed_stock (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('home', 'jita')),
    quantity DOUBLE PRECISION NOT NULL CHECK (quantity >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, type_id, market)
);
ALTER TABLE manual_listed_stock ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON manual_listed_stock;
CREATE POLICY tenant_isolation ON manual_listed_stock
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON manual_listed_stock TO eve_trader_app;

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
-- PK (job_id/order_id/character_name) is already globally unique per
-- EVE/ESI's own guarantees - tenant_id added for RLS row-visibility only, PK
-- left unchanged.
--
-- character_assets/corp_assets are the one exception, PK (item_id,
-- owner_name) rather than item_id alone: confirmed live (2026-09-08,
-- production/esi_sync.py's sync_esi 500ing with a real
-- character_assets_pkey UniqueViolation) - CCP's item_id for a
-- non-singleton (stackable) asset is NOT guaranteed globally unique across
-- different owners at the same location, only that this file originally
-- assumed it was. Two real characters both had a "Hangar" stack of the
-- same type_id at the same structure sharing one item_id (different
-- quantities - genuinely two different stacks, not a duplicate fetch, and
-- reproduced deterministically on every retry, not a one-off race). See
-- storage._resolve_locations/_resolve_hangar_flags, which key their own
-- parent-of-container lookups the same way for the same reason.

CREATE TABLE IF NOT EXISTS character_assets (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    item_id BIGINT NOT NULL,
    type_id INTEGER,
    location_id BIGINT,
    location_flag TEXT,
    quantity INTEGER,
    is_blueprint_copy INTEGER,
    owner_name TEXT NOT NULL,
    resolved_location_id BIGINT,
    resolved_hangar_flag TEXT,
    PRIMARY KEY (item_id, owner_name)
);
-- Live migration: a table created before 2026-09-08 still has the old
-- item_id-only PK - widen it in place. owner_name has no legacy NULLs to
-- worry about (every existing row was already populated by replace_assets),
-- so SET NOT NULL is safe unconditionally.
ALTER TABLE character_assets ALTER COLUMN owner_name SET NOT NULL;
ALTER TABLE character_assets DROP CONSTRAINT IF EXISTS character_assets_pkey;
-- T1-04 (2026-09-26): widened again to include tenant_id - (item_id,
-- owner_name) alone was still cross-tenant-unsafe for a shared corp (see
-- corp_assets' own T1-04 comment below; this table shares the same
-- collision shape for a character whose name coincidentally matches
-- another tenant's - low real risk but cheap to close for consistency).
ALTER TABLE character_assets ADD CONSTRAINT character_assets_pkey PRIMARY KEY (tenant_id, item_id, owner_name);
-- GitHub issue #4/#20: location_id is the item's *immediate* parent (a ship,
-- a container, a corp Office, ...), which can be several containers deep -
-- resolved_location_id is that chain walked all the way up to the outermost
-- station/structure (see storage.replace_assets), computed once at sync
-- time so every query can filter on it directly instead of re-walking the
-- chain (or missing anything past one level, the original bug) per query.
ALTER TABLE character_assets ADD COLUMN IF NOT EXISTS resolved_location_id BIGINT;
-- Hangar-sorting feature: resolved_hangar_flag is the corp-hangar-division
-- flag (e.g. "CorpSAG1") an item should be counted under, walked up through
-- nested containers/ships the same way resolved_location_id is - see
-- storage._resolve_hangar_flags. Filtering allowed_flags/assets_at_flag on
-- the raw location_flag column instead (an earlier version of this feature)
-- reintroduced the exact #4/#20 bug class: an item nested one level inside a
-- flagged container read as invisible to that container's own division.
ALTER TABLE character_assets ADD COLUMN IF NOT EXISTS resolved_hangar_flag TEXT;
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
    item_id BIGINT NOT NULL,
    type_id INTEGER,
    location_id BIGINT,
    location_flag TEXT,
    quantity INTEGER,
    is_blueprint_copy INTEGER,
    owner_name TEXT NOT NULL,
    resolved_location_id BIGINT,
    resolved_hangar_flag TEXT,
    PRIMARY KEY (item_id, owner_name)
);
ALTER TABLE corp_assets ALTER COLUMN owner_name SET NOT NULL;
ALTER TABLE corp_assets DROP CONSTRAINT IF EXISTS corp_assets_pkey;
-- T1-04 (business-logic audit 2026-08-28/30, live-confirmed 2026-09-26):
-- (item_id, owner_name) alone is NOT tenant-safe for a corp table, even
-- though MULTI_TENANT_PLAN.md originally classified corp_assets/corp_
-- industry_jobs/corp_blueprints as "no real collision risk" alongside the
-- character-owned tables in the same bucket - that reasoning ("EVE
-- guarantees the id is globally unique") is true but doesn't matter here:
-- collision isn't about id reuse, it's that owner_name is the SAME real
-- corp's name for every tenant that has a character in it (one corp is not
-- 1:1 with a tenant the way one character is - director-level characters
-- from multiple tenants can legitimately belong to the same corp). Live-
-- confirmed real, not just theoretical: corporation_id 98370861 has
-- registered characters under 5 different tenants right now, and the
-- Default/Hari Lindberg tenants' corp_assets/corp_blueprints/corp_
-- industry_jobs rows for that corp have ZERO overlapping ids despite both
-- syncing the same real corp - exactly the silent-partial-sync symptom
-- this bug produces (whichever tenant's INSERT loses the bare-PK race for
-- a given id never gets that row). No existing duplicate rows are possible
-- under the current bare PK (Postgres already enforced global uniqueness
-- on it), so widening can only be safe - never conflicts with existing data.
ALTER TABLE corp_assets ADD CONSTRAINT corp_assets_pkey PRIMARY KEY (tenant_id, item_id, owner_name);
ALTER TABLE corp_assets ADD COLUMN IF NOT EXISTS resolved_location_id BIGINT;
ALTER TABLE corp_assets ADD COLUMN IF NOT EXISTS resolved_hangar_flag TEXT;
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
-- T1-04 (2026-09-26): bare job_id alone is not tenant-safe (see corp_
-- industry_jobs' own T1-04 comment below) - low real risk here specifically
-- (one tenant per character), widened for consistency with the corp table.
ALTER TABLE character_industry_jobs DROP CONSTRAINT IF EXISTS character_industry_jobs_pkey;
ALTER TABLE character_industry_jobs ADD CONSTRAINT character_industry_jobs_pkey PRIMARY KEY (tenant_id, job_id);
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
-- T1-04 (business-logic audit 2026-08-28/30, live-confirmed 2026-09-26):
-- bare job_id is not tenant-safe - same corp-is-not-1:1-with-a-tenant gap
-- as corp_assets' own T1-04 comment above (see that comment for the full
-- reasoning and the live evidence: corp 98370861 shared across 5 tenants,
-- zero id overlap in what each tenant actually managed to store).
ALTER TABLE corp_industry_jobs DROP CONSTRAINT IF EXISTS corp_industry_jobs_pkey;
ALTER TABLE corp_industry_jobs ADD CONSTRAINT corp_industry_jobs_pkey PRIMARY KEY (tenant_id, job_id);
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
-- T1-04 (2026-09-26): bare character_name alone is not tenant-safe in
-- principle (low real risk - EVE enforces character names unique game-wide -
-- but cheap to close for consistency with the corp tables' own real fix).
-- storage.replace_character_slots' own ON CONFLICT target was widened to
-- match (tenant_id, character_name) in the same change.
ALTER TABLE character_slots DROP CONSTRAINT IF EXISTS character_slots_pkey;
ALTER TABLE character_slots ADD CONSTRAINT character_slots_pkey PRIMARY KEY (tenant_id, character_name);
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
-- T1-04 (2026-09-26): bare item_id alone is not tenant-safe (see corp_
-- blueprints' own T1-04 comment below) - low real risk here specifically
-- (one tenant per character), widened for consistency with the corp table.
ALTER TABLE character_blueprints DROP CONSTRAINT IF EXISTS character_blueprints_pkey;
ALTER TABLE character_blueprints ADD CONSTRAINT character_blueprints_pkey PRIMARY KEY (tenant_id, item_id);
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
-- T1-04 (business-logic audit 2026-08-28/30, live-confirmed 2026-09-26):
-- bare item_id is not tenant-safe - same corp-is-not-1:1-with-a-tenant gap
-- as corp_assets' own T1-04 comment above (see that comment for the full
-- reasoning and the live evidence: corp 98370861 shared across 5 tenants,
-- zero id overlap in what each tenant actually managed to store).
ALTER TABLE corp_blueprints DROP CONSTRAINT IF EXISTS corp_blueprints_pkey;
ALTER TABLE corp_blueprints ADD CONSTRAINT corp_blueprints_pkey PRIMARY KEY (tenant_id, item_id);
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
-- T1-04 (2026-09-26): bare order_id alone is not tenant-safe in principle
-- (low real risk - EVE's own order_id is globally unique and there is no
-- corp-shared counterpart table for sell orders - but cheap to close for
-- consistency with the corp tables' own real fix).
ALTER TABLE character_sell_orders DROP CONSTRAINT IF EXISTS character_sell_orders_pkey;
ALTER TABLE character_sell_orders ADD CONSTRAINT character_sell_orders_pkey PRIMARY KEY (tenant_id, order_id);
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
    landed_cost DOUBLE PRECISION,
    net_sell DOUBLE PRECISION,
    sell_volume REAL,
    own_orders_remaining REAL,
    profit_per_unit DOUBLE PRECISION,
    margin REAL,
    profit_per_m3 DOUBLE PRECISION,
    decision TEXT,
    active INTEGER,
    volume_m3 REAL,
    jita_sell DOUBLE PRECISION,
    import_cost DOUBLE PRECISION,
    meta_level INTEGER
);
-- The six ISK-denominated columns above (landed_cost/net_sell/profit_per_unit/
-- profit_per_m3/jita_sell/import_cost) need double precision, not REAL - see
-- manual_blueprint_copy_costs' own comment above for why. sell_volume/
-- own_orders_remaining/volume_m3 (unit counts/m3) and margin (a ratio, not
-- an ISK amount) don't have this problem - REAL's ~7 significant digits is
-- already far more precision than either needs, so they're deliberately
-- left as-is. Widens an already-provisioned DB's columns in place.
ALTER TABLE shortlist_snapshot ALTER COLUMN landed_cost TYPE DOUBLE PRECISION;
ALTER TABLE shortlist_snapshot ALTER COLUMN net_sell TYPE DOUBLE PRECISION;
ALTER TABLE shortlist_snapshot ALTER COLUMN profit_per_unit TYPE DOUBLE PRECISION;
ALTER TABLE shortlist_snapshot ALTER COLUMN profit_per_m3 TYPE DOUBLE PRECISION;
ALTER TABLE shortlist_snapshot ALTER COLUMN jita_sell TYPE DOUBLE PRECISION;
ALTER TABLE shortlist_snapshot ALTER COLUMN import_cost TYPE DOUBLE PRECISION;
-- GitHub issue #51: "Profit / Day" used to be computed from sell_volume
-- (order-book depth, "how much is listed right now") - a never-actually-sold
-- item with a large order book produced a wildly inflated number. #51 first
-- replaced it with the trader's own realized-sales average (avg_daily_sold,
-- trade_reconciliation.average_daily_sold_by_type) - GitHub issue #100 then
-- found that overcorrected: it left "Profit / Day" empty for every
-- not-yet-sold-by-me candidate, the exact rows a shortlist exists to
-- evaluate. Renamed avg_daily_sold -> avg_daily_volume and re-sourced from
-- Goonmetrics region history (shortlist.average_market_daily_volume) - real
-- market-wide traded quantity in C-J's own home region, not order-book
-- depth and not one trader's own sales. The rename is itself idempotent: a
-- DB that already ran the old avg_daily_sold migration gets it renamed in
-- place (its historical values carry over, not dropped/reset); a fresh DB
-- just gets avg_daily_volume created directly.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'shortlist_snapshot' AND column_name = 'avg_daily_sold') THEN
        ALTER TABLE shortlist_snapshot RENAME COLUMN avg_daily_sold TO avg_daily_volume;
    END IF;
END
$$;
ALTER TABLE shortlist_snapshot ADD COLUMN IF NOT EXISTS avg_daily_volume REAL;
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
    avg_profit_m3 DOUBLE PRECISION,
    avg_sell_movement REAL,
    score REAL,
    recommendation TEXT,
    add_flag INTEGER,
    meta_level INTEGER
);
-- avg_profit_m3 is ISK/m3 - needs double precision, not REAL, same reasoning
-- as manual_blueprint_copy_costs' own comment above. hit_rate/latest_margin/
-- best_margin/score are ratios/scores, not ISK amounts - left as REAL.
ALTER TABLE new_candidates ALTER COLUMN avg_profit_m3 TYPE DOUBLE PRECISION;
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
    buy_unit_price DOUBLE PRECISION,
    sell_date TEXT,
    sell_qty INTEGER,
    sell_unit_price DOUBLE PRECISION,
    matched_qty INTEGER,
    realized_profit DOUBLE PRECISION,
    margin REAL
);
-- This is the realized-P&L ledger - a record of what actually happened, not
-- just a decision-support estimate, so REAL's silent rounding (confirmed
-- live 2026-08-28: stored buy/sell prices above ~16.7M ISK were provably not
-- the exact fill price, e.g. a 535,1xx,xxx ISK fill stored back as an
-- exactly-round 535,100,000) is a real correctness defect here, not just an
-- immaterial rounding difference the way it is for a margin percentage.
-- margin stays REAL - it's a ratio, not an ISK amount, unaffected by this.
-- Widening the type does NOT recover precision already lost on existing
-- rows (float32 -> float64 preserves the already-rounded number); only a
-- fresh Reconcile Trades run re-derives exact values from ESI wallet
-- transactions.
ALTER TABLE realized_trades ALTER COLUMN buy_unit_price TYPE DOUBLE PRECISION;
ALTER TABLE realized_trades ALTER COLUMN sell_unit_price TYPE DOUBLE PRECISION;
ALTER TABLE realized_trades ALTER COLUMN realized_profit TYPE DOUBLE PRECISION;
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
