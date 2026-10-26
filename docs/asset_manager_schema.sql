-- Asset Manager tool (docs/ASSET_MANAGER_PLAN.md): saved views (F-3) and the
-- daily quantity history (phase 6). Asset rows themselves live in
-- character_assets / corp_assets (phase1_schema.sql).
-- Idempotent - every statement is safe to re-run.
--
-- Usage (owner role only, never eve_trader_app):
--   local dev:  Get-Content docs\asset_manager_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/asset_manager_schema.sql

-- ====================================================== saved views (F-3)
-- `query` is the /api/asset-manager/items parameter set; it is validated
-- again on load, so a view saved by an older version never 500s.
CREATE TABLE IF NOT EXISTS asset_manager_views (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    query JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);
ALTER TABLE asset_manager_views ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON asset_manager_views;
CREATE POLICY tenant_isolation ON asset_manager_views
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON asset_manager_views TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE asset_manager_views_id_seq TO eve_trader_app;

-- ====================================================== daily history (phase 6)
-- One row per calendar day, owner, location and type: the summed quantity
-- of that owner's ESI assets there (location 0 = unknown). Written once per
-- day by the scheduler's asset_history job or lazily on the first history
-- read of the day; pruned to the tenant's retention setting. Composite PK
-- with tenant_id: type and location ids collide across tenants.
CREATE TABLE IF NOT EXISTS asset_manager_history (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    snapshot_date DATE NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    location_id BIGINT NOT NULL DEFAULT 0,
    type_id INTEGER NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (tenant_id, snapshot_date, owner_type, owner_id, location_id, type_id)
);
ALTER TABLE asset_manager_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON asset_manager_history;
CREATE POLICY tenant_isolation ON asset_manager_history
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON asset_manager_history TO eve_trader_app;
