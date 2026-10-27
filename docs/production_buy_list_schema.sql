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

-- ================================== per-tenant: dynamic stock target values
-- docs/DYNAMIC_STOCK_TARGETS_PLAN.md section 4.2. One frozen row per
-- (type, UTC day): the effective home-market target the planners and the
-- cheap pages read, plus every input that produced it (so the page can
-- explain it). Placed here, in an existing apply-list file, so the "schema
-- file missing from one apply list" bug class cannot happen. Rows older than
-- 90 days are deleted on write (storage.freeze_dynamic_values).
-- effective_qty is REAL like stock_targets.home_market_stock; the smoothing
-- state and the inputs are DOUBLE PRECISION so the dead-band arithmetic is
-- not disturbed by float4 rounding (P-30).
CREATE TABLE IF NOT EXISTS stock_target_dynamic_values (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    day DATE NOT NULL,
    effective_qty REAL NOT NULL,
    smoothed_qty DOUBLE PRECISION NOT NULL,
    raw_qty DOUBLE PRECISION,
    velocity_own DOUBLE PRECISION,
    velocity_market DOUBLE PRECISION,
    velocity_used DOUBLE PRECISION,
    velocity_source TEXT,
    margin DOUBLE PRECISION,
    margin_source TEXT,
    factor DOUBLE PRECISION,
    reason TEXT NOT NULL,
    gate_margin DOUBLE PRECISION,
    gate_blocked BOOLEAN,
    max_day_share DOUBLE PRECISION,
    region_id INTEGER,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, type_id, day)
);
ALTER TABLE stock_target_dynamic_values ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON stock_target_dynamic_values;
CREATE POLICY tenant_isolation ON stock_target_dynamic_values
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON stock_target_dynamic_values TO eve_trader_app;

-- ============================== per-tenant: reaction chaining choice (D10)
-- docs/REACTION_CAPACITY_PLAN.md phase 4. Per reaction product: "buy" (default
-- when missing) or "wait" (defer W1 demand to W2). Missing row = buy.
CREATE TABLE IF NOT EXISTS reaction_chain_choice (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    choice TEXT NOT NULL CHECK (choice IN ('buy', 'wait')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE reaction_chain_choice ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON reaction_chain_choice;
CREATE POLICY tenant_isolation ON reaction_chain_choice
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON reaction_chain_choice TO eve_trader_app;
