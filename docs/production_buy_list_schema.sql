-- Latest Production buy-list snapshot. Additive only - never edits
-- phase1/phase2/phase3_schema.sql, which record the multi-tenant
-- migration's own history (see CLAUDE.md's "Adding a new per-tenant
-- table" section for the conventions this file follows, and
-- docs/sorting_schema.sql for the worked example this mirrors).
-- Idempotent - every statement is safe to re-run.
--
-- Wholesale current-state table, not a growing history like
-- shortlist_snapshot: Sorting's material pot only needs the latest
-- plan_production buy_list (the real expanded, margin-gated material
-- demand), not trend data. Written by plan_production after it builds
-- buy_list; read by sorting/engine._material_wanted_by_type.
--
-- Usage (same as phase1_schema.sql/sorting_schema.sql - owner role only,
-- never the app's own eve_trader_app role):
--   local dev:  Get-Content docs\production_buy_list_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/production_buy_list_schema.sql

-- ====================================================== per-tenant: latest buy list
-- Composite-PK bucket, same shape as stock_targets/manual_stock: type_id
-- is an EVE item type and would collide across tenants without tenant_id
-- in the PK. No surrogate id, no history rows.
CREATE TABLE IF NOT EXISTS production_buy_list (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE production_buy_list ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON production_buy_list;
CREATE POLICY tenant_isolation ON production_buy_list
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON production_buy_list TO eve_trader_app;
