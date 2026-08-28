-- Station Trading tool schema. Additive only - never edits phase1/phase2/
-- phase3_schema.sql, which record the multi-tenant migration's own history
-- (see CLAUDE.md's "Adding a new per-tenant table" section for the
-- conventions this file follows, and docs/refining_schema.sql for the
-- worked example this mirrors). Idempotent - every statement is safe to
-- re-run.
--
-- Usage (same as phase1_schema.sql/refining_schema.sql - owner role only,
-- never the app's own eve_trader_app role):
--   local dev:  Get-Content docs\station_trading_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/station_trading_schema.sql

-- ========================================================= existing-table widening
-- phase2_schema.sql's tenant_settings.scope check constraint doesn't know
-- about 'station_trading' yet - widened here (additive, never editing
-- phase2_schema.sql/doctrine_schema.sql/refining_schema.sql themselves) so
-- save_tenant_config_overrides("station_trading", ...) can persist
-- StationTradingConfig Settings-page saves the same way every other tool
-- already does.
ALTER TABLE tenant_settings DROP CONSTRAINT IF EXISTS tenant_settings_scope_check;
ALTER TABLE tenant_settings ADD CONSTRAINT tenant_settings_scope_check
    CHECK (scope IN ('trading', 'production', 'doctrine', 'refining', 'station_trading'));

-- ================================================== per-tenant: candidate shortlist
-- Persisted spread/volume candidates from candidate_discovery.
-- discover_candidates - same composite-PK "live list" shape as ore_shortlist
-- (docs/refining_schema.sql) - the natural key is an EVE type_id, reused
-- verbatim across every tenant, so the PK is widened to (tenant_id, type_id).
-- No "own orders" table alongside this - undercut checks (station_trading/
-- undercut.py) stay live/one-shot, no persistence, matching Trading's own
-- check_undercut precedent and avoiding a second source of staleness.
CREATE TABLE IF NOT EXISTS station_trading_shortlist (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    spread_pct DOUBLE PRECISION NOT NULL,
    avg_daily_volume DOUBLE PRECISION NOT NULL,
    discovered_at TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE station_trading_shortlist ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON station_trading_shortlist;
CREATE POLICY tenant_isolation ON station_trading_shortlist
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON station_trading_shortlist TO eve_trader_app;
