-- Recreates the local Phase 0 dev environment (see docs/MULTI_TENANT_PLAN.md).
--
-- 1. Start Postgres:
--      docker run -d --name eve-trader-pg -e POSTGRES_PASSWORD=devpassword \
--        -e POSTGRES_DB=eve_trader -p 5432:5432 postgres:16
-- 2. Apply this file:
--      Get-Content docs\phase0_setup.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
-- 3. Run tests/test_pg_tenant_isolation.py (uses eve_trader.pg_tenant, DSN
--    defaults to this exact setup - see EVE_TRADER_PG_DSN in pg_tenant.py).
--
-- Owner role (postgres, via docker exec above) runs this DDL; the app itself
-- connects as the separate, non-superuser eve_trader_app role below - RLS is
-- silently skipped for a table's owner/superuser otherwise.

CREATE ROLE eve_trader_app WITH LOGIN PASSWORD 'app_devpassword' NOSUPERUSER NOBYPASSRLS;

CREATE TABLE stock_targets (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    type_name TEXT NOT NULL,
    backup_stock REAL DEFAULT 0,
    home_market_stock REAL,
    jita_market_stock REAL,
    PRIMARY KEY (tenant_id, type_id)
);

ALTER TABLE stock_targets ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON stock_targets
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT USAGE ON SCHEMA public TO eve_trader_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON stock_targets TO eve_trader_app;
