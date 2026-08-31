-- Doctrine/Contract tool schema (see the Phase 2/3 architecture + spec docs
-- this was built from). Additive only - never edits phase1/phase2/phase3_
-- schema.sql, which record the multi-tenant migration's own history (see
-- CLAUDE.md's "Adding a new per-tenant table" section for the conventions
-- this file follows). Idempotent - every statement is safe to re-run.
--
-- Usage (same as phase1_schema.sql - owner role only, never the app's own
-- eve_trader_app role):
--   local dev:  Get-Content docs\doctrine_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/doctrine_schema.sql

-- ========================================================= existing-table widening
-- phase2_schema.sql's tenant_settings.scope check constraint only allowed
-- 'trading'/'production' - widened here (additive, doctrine_schema.sql's
-- own file, never editing phase2_schema.sql itself) so
-- save_tenant_config_overrides("doctrine", ...) can persist Doctrine
-- Settings-page saves the same way Trading/Production already do.
--
-- Kept in sync with docs/refining_schema.sql's and docs/
-- station_trading_schema.sql's own copies of this same ALTER (GitHub issue
-- #90) - each new tool's schema file must restate the FULL current scope
-- list, not just its own addition: DROP+ADD CONSTRAINT replaces the whole
-- check, so a re-run of this file *after* a later tool's schema file has
-- already run (all are individually documented as "idempotent, safe to
-- re-run" - deploy/README.md doesn't forbid re-running an earlier one, and
-- deploy.sh's own loop always re-runs every file in chronological order on
-- every deploy) would otherwise silently narrow the constraint back down
-- and CheckViolation on any already-saved later-scope row. Confirmed real
-- twice: once during this feature's own test suite (two schema files'
-- session fixtures reapplying their ALTERs in a different order than the
-- documented deploy sequence), and again live in production (2026-08-31,
-- ON_ERROR_STOP aborting deploy.sh's whole schema loop here, before it
-- ever reached station_trading_schema.sql's own wider ADD CONSTRAINT,
-- against a real already-saved 'station_trading' row) - this file's own
-- list had drifted stale, missing 'station_trading' entirely, exactly the
-- failure mode this comment already warned about. If a sixth tool adds a
-- scope, EVERY existing schema file's copy of this ALTER needs the new
-- scope added too, not just the newest one's.
ALTER TABLE tenant_settings DROP CONSTRAINT IF EXISTS tenant_settings_scope_check;
ALTER TABLE tenant_settings ADD CONSTRAINT tenant_settings_scope_check
    CHECK (scope IN ('trading', 'production', 'doctrine', 'refining', 'station_trading'));

-- ============================================================== shared table
-- typeID -> fitting slot (see production/sde.py's refresh_sde, storage.
-- get_type_slot) - no tenant_id, no RLS, same bucket as every other sde_*
-- reference table (identical for every tenant, refreshed globally).
CREATE TABLE IF NOT EXISTS sde_type_slots (
    type_id INTEGER PRIMARY KEY,
    slot TEXT NOT NULL
);

-- ============================================================ per-tenant: doctrines/fittings
-- Composite-PK bucket (tenant_id, <app-generated UUID>) - the UUID alone
-- practically never collides across tenants, but every per-tenant table in
-- this app keeps the same composite shape for consistency (see phase1_
-- schema.sql's own "composite-PK bucket" comment banner).

CREATE TABLE IF NOT EXISTS doctrines (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    doctrine_id UUID NOT NULL DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, doctrine_id)
);
ALTER TABLE doctrines ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrines;
CREATE POLICY tenant_isolation ON doctrines
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS doctrine_fittings (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    fitting_id UUID NOT NULL DEFAULT gen_random_uuid(),
    doctrine_id UUID NOT NULL,
    name TEXT NOT NULL,
    variant_label TEXT,
    hull_type_id INTEGER NOT NULL,
    raw_eft TEXT NOT NULL,
    contract_target INTEGER NOT NULL DEFAULT 0,
    stockpile_target INTEGER NOT NULL DEFAULT 0,
    cargo_tolerance_pct REAL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- GitHub issue #18: capital ship contracts also carry a Fuel Bay and a
    -- Ship Maintenance Bay, neither expressible in standard EFT export text
    -- at all (CCP's Fitting Formats spec only covers slots/drones/cargo) -
    -- entered as their own plain-text item lists (doctrine/parser.py's
    -- parse_bay_items), stored verbatim here the same way raw_eft is, so
    -- they can be redisplayed/re-edited. NULL/empty means "not a capital
    -- fit, no bay contents to track".
    fuel_bay_text TEXT,
    ship_maintenance_bay_text TEXT,
    PRIMARY KEY (tenant_id, fitting_id)
);
ALTER TABLE doctrine_fittings ADD COLUMN IF NOT EXISTS fuel_bay_text TEXT;
ALTER TABLE doctrine_fittings ADD COLUMN IF NOT EXISTS ship_maintenance_bay_text TEXT;
CREATE INDEX IF NOT EXISTS idx_doctrine_fittings_doctrine ON doctrine_fittings (tenant_id, doctrine_id);
ALTER TABLE doctrine_fittings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrine_fittings;
CREATE POLICY tenant_isolation ON doctrine_fittings
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS doctrine_fitting_items (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    fitting_id UUID NOT NULL,
    line_no INTEGER NOT NULL,
    -- One EFT line can produce two items (a module + its comma-paired
    -- charge, Phase 3 spec A.5) sharing the same line_no - item_index (0,
    -- then 1) discriminates them; storage.replace_fitting_items assigns it
    -- automatically from input order, never passed in by callers.
    item_index INTEGER NOT NULL DEFAULT 0,
    slot_section TEXT NOT NULL,
    type_id INTEGER NOT NULL,
    quantity REAL NOT NULL DEFAULT 1,
    is_offline BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, fitting_id, line_no, item_index)
);
CREATE INDEX IF NOT EXISTS idx_doctrine_fitting_items_fitting ON doctrine_fitting_items (tenant_id, fitting_id);
ALTER TABLE doctrine_fitting_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrine_fitting_items;
CREATE POLICY tenant_isolation ON doctrine_fitting_items
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS doctrine_fitting_parse_issues (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    fitting_id UUID NOT NULL,
    line_no INTEGER NOT NULL,
    raw_line TEXT NOT NULL,
    issue_kind TEXT NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (tenant_id, fitting_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_doctrine_parse_issues_fitting ON doctrine_fitting_parse_issues (tenant_id, fitting_id);
ALTER TABLE doctrine_fitting_parse_issues ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrine_fitting_parse_issues;
CREATE POLICY tenant_isolation ON doctrine_fitting_parse_issues
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

-- ============================================================ per-tenant: contracts
-- doctrine_contracts' PK is composite for a real reason (not just this
-- app's usual consistency choice): a contract_id from ESI is *not*
-- tenant-unique - two tenants who happen to be in the same corp (or just
-- both see the same public/character contract) would collide on
-- contract_id alone (see Phase 3 spec's Multi-Tenant-Fehler section).

CREATE TABLE IF NOT EXISTS doctrine_contracts (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    contract_id BIGINT NOT NULL,
    source_role TEXT NOT NULL,
    for_corporation BOOLEAN NOT NULL DEFAULT false,
    issuer_id BIGINT,
    start_location_id BIGINT,
    status TEXT NOT NULL,
    title TEXT,
    price DOUBLE PRECISION,
    date_expired TIMESTAMPTZ,
    matched_fitting_id UUID,
    match_score REAL,
    validation_status TEXT NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, contract_id)
);
-- Contract price is an ISK amount, not a ratio/score like match_score -
-- needs double precision, not REAL, same reasoning as phase1_schema.sql's
-- realized_trades comment (found in the same 2026-08-28 audit). Widens an
-- already-provisioned DB's column in place.
ALTER TABLE doctrine_contracts ALTER COLUMN price TYPE DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS idx_doctrine_contracts_fitting ON doctrine_contracts (tenant_id, matched_fitting_id);
CREATE INDEX IF NOT EXISTS idx_doctrine_contracts_status ON doctrine_contracts (tenant_id, validation_status);
ALTER TABLE doctrine_contracts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrine_contracts;
CREATE POLICY tenant_isolation ON doctrine_contracts
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS doctrine_contract_items (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    contract_id BIGINT NOT NULL,
    record_id BIGINT NOT NULL,
    type_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    is_included BOOLEAN NOT NULL DEFAULT true,
    is_singleton BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, contract_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_doctrine_contract_items_contract ON doctrine_contract_items (tenant_id, contract_id);
ALTER TABLE doctrine_contract_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrine_contract_items;
CREATE POLICY tenant_isolation ON doctrine_contract_items
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS doctrine_contract_deviations (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    contract_id BIGINT NOT NULL,
    type_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    expected_qty REAL NOT NULL DEFAULT 0,
    actual_qty REAL NOT NULL DEFAULT 0,
    severity TEXT NOT NULL,
    PRIMARY KEY (tenant_id, contract_id, type_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_doctrine_deviations_contract ON doctrine_contract_deviations (tenant_id, contract_id);
ALTER TABLE doctrine_contract_deviations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrine_contract_deviations;
CREATE POLICY tenant_isolation ON doctrine_contract_deviations
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

-- ==================================================== per-tenant: contract history
-- GitHub issue #19 - "the doctrine tool should have a page giving history:
-- which contracts were sold to who and when". Separate, append-only table
-- from doctrine_contracts above (which stays a wholesale-replaced *active*
-- snapshot, see its own docstring - a finished contract simply isn't in the
-- next sync's list anymore, and used to just vanish with no trace at all).
-- fitting_name/hull_type_id are captured (denormalized) at record time,
-- deliberately not resolved live via matched_fitting_id the way doctrine_
-- contracts' own reads do - a fitting can be edited/deactivated/deleted
-- long after a contract that references it finished, and a *history* row
-- should stay meaningful regardless (unlike the *active* table, where an
-- unmatched/stale fitting_id genuinely should stop resolving to anything).
-- Same composite-PK reasoning as doctrine_contracts - contract_id isn't
-- tenant-unique.
CREATE TABLE IF NOT EXISTS doctrine_contract_history (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    contract_id BIGINT NOT NULL,
    source_role TEXT NOT NULL,
    fitting_id UUID,
    fitting_name TEXT,
    hull_type_id INTEGER,
    title TEXT,
    price DOUBLE PRECISION,
    acceptor_id BIGINT,
    acceptor_name TEXT,
    date_issued TIMESTAMPTZ,
    date_completed TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, contract_id)
);
-- Same ISK-amount reasoning as doctrine_contracts.price above.
ALTER TABLE doctrine_contract_history ALTER COLUMN price TYPE DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS idx_doctrine_contract_history_fitting
    ON doctrine_contract_history (tenant_id, fitting_id);
ALTER TABLE doctrine_contract_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrine_contract_history;
CREATE POLICY tenant_isolation ON doctrine_contract_history
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

-- ================================================== per-tenant: own asset sync
-- Doctrine's own ESI-synced asset cache - deliberately NOT a read of
-- Production's character_assets/corp_assets (an earlier design decision,
-- reversed after real use: Stockpile must work standalone, without
-- requiring Production to have ever been set up/synced). Column-only
-- bucket, same shape/reasoning as character_assets/corp_assets
-- (phase1_schema.sql) - item_id is a real ESI-global identifier, already
-- unique with no risk of cross-tenant collision, so the PK is left
-- unwidened; tenant_id is still a plain column + RLS policy like every
-- other per-tenant table.

CREATE TABLE IF NOT EXISTS doctrine_character_assets (
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
-- Same resolved_location_id shape as phase1_schema.sql's character_assets/
-- corp_assets (GitHub issue #4/#20) - both go through storage.replace_assets,
-- which computes this the same way regardless of which pair of tables it's
-- called for.
ALTER TABLE doctrine_character_assets ADD COLUMN IF NOT EXISTS resolved_location_id BIGINT;
DROP INDEX IF EXISTS idx_doctrine_character_assets_type_location;
CREATE INDEX IF NOT EXISTS idx_doctrine_character_assets_type_resolved_location
    ON doctrine_character_assets (type_id, resolved_location_id);
ALTER TABLE doctrine_character_assets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrine_character_assets;
CREATE POLICY tenant_isolation ON doctrine_character_assets
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS doctrine_corp_assets (
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
ALTER TABLE doctrine_corp_assets ADD COLUMN IF NOT EXISTS resolved_location_id BIGINT;
DROP INDEX IF EXISTS idx_doctrine_corp_assets_type_location;
CREATE INDEX IF NOT EXISTS idx_doctrine_corp_assets_type_resolved_location
    ON doctrine_corp_assets (type_id, resolved_location_id);
ALTER TABLE doctrine_corp_assets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON doctrine_corp_assets;
CREATE POLICY tenant_isolation ON doctrine_corp_assets
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON
    sde_type_slots, doctrines, doctrine_fittings, doctrine_fitting_items,
    doctrine_fitting_parse_issues, doctrine_contracts, doctrine_contract_items,
    doctrine_contract_deviations, doctrine_character_assets, doctrine_corp_assets,
    doctrine_contract_history
    TO eve_trader_app;
