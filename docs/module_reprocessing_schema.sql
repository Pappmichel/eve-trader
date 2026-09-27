-- Module Reprocessing Import tool schema. Additive only - never edits
-- phase1/phase2/phase3_schema.sql, which record the multi-tenant
-- migration's own history (see CLAUDE.md's "Adding a new per-tenant table"
-- section for the conventions this file follows, and docs/refining_schema.sql
-- for the worked example this mirrors - Ore Shortlist's own shortlist/
-- shortlist_snapshot pair). Idempotent - every statement is safe to re-run.
--
-- Usage (same as phase1_schema.sql/refining_schema.sql - owner role only,
-- never the app's own eve_trader_app role):
--   local dev:  Get-Content docs\module_reprocessing_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/module_reprocessing_schema.sql

-- ========================================================= existing-table widening
-- tenant_settings.scope's check constraint (last widened by
-- station_trading_schema.sql) needs 'module_reprocessing' added so
-- save_tenant_config_overrides("module_reprocessing", ...) can persist
-- ModuleReprocessingConfig Settings-page saves the same way every other
-- tool already does. Restate the FULL current scope list here too, not
-- just this file's own addition - see docs/refining_schema.sql's own
-- comment on this exact ALTER for the repeated-bug history
-- (deploy/deploy.sh's migration loop re-runs every schema file, every
-- deploy, so a narrower list here would win and drop every other tool's
-- already-saved settings rows into a CheckViolation on their next save).
-- If a tenth tool adds a scope, EVERY existing schema file's copy of this
-- ALTER needs the new scope added too, not just this one.
ALTER TABLE tenant_settings DROP CONSTRAINT IF EXISTS tenant_settings_scope_check;
ALTER TABLE tenant_settings ADD CONSTRAINT tenant_settings_scope_check
    CHECK (scope IN ('trading', 'production', 'doctrine', 'refining', 'station_trading', 'module_reprocessing'));

-- ============================================== per-tenant: Module Shortlist
-- Same two-table shape as Ore & Minerals' own ore_shortlist/
-- ore_shortlist_snapshot (docs/refining_schema.sql) - composite-PK "live
-- list" bucket + no-PK append/history snapshot bucket (see
-- docs/phase1_schema.sql's own section banners for the reasoning behind
-- each shape). Unlike ore_shortlist (every candidate in a tiny, fixed
-- universe is added automatically), this tool's own candidate universe
-- (T1/Meta modules and drones, category_id 7/18) is large - items land
-- here only when the user picks them from a Discover run's results, never
-- all at once (see module_reprocessing/candidate_discovery.py).

CREATE TABLE IF NOT EXISTS module_reprocessing_shortlist (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    item_id INTEGER NOT NULL,
    item TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, item_id)
);
ALTER TABLE module_reprocessing_shortlist ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON module_reprocessing_shortlist;
CREATE POLICY tenant_isolation ON module_reprocessing_shortlist
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE TABLE IF NOT EXISTS module_reprocessing_shortlist_snapshot (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    run_ts TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    item TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    volume_m3 REAL,
    landed_cost DOUBLE PRECISION,
    yield_pct DOUBLE PRECISION,
    mineral_value DOUBLE PRECISION,
    refining_tax DOUBLE PRECISION,
    net_sell DOUBLE PRECISION,
    sell_listed_qty DOUBLE PRECISION,
    profit_per_unit DOUBLE PRECISION,
    margin DOUBLE PRECISION,
    profit_per_m3 DOUBLE PRECISION,
    decision TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_module_reprocessing_shortlist_snapshot_tenant
    ON module_reprocessing_shortlist_snapshot (tenant_id);
ALTER TABLE module_reprocessing_shortlist_snapshot ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON module_reprocessing_shortlist_snapshot;
CREATE POLICY tenant_isolation ON module_reprocessing_shortlist_snapshot
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON module_reprocessing_shortlist, module_reprocessing_shortlist_snapshot
    TO eve_trader_app;
