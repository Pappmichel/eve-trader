-- Per-Logistik-category ISK job-cost-index override (GitHub issue,
-- confirmed with the user 2026-09-16). Additive alongside the existing
-- ProductionConfig.reaction_/component_/manufacturing_cost_index_override
-- fields (production/config.py) - those three stay exactly as they are and
-- keep working unchanged, this table just adds a finer-grained layer on
-- top. Each of the JOB_CATEGORIES buckets (production/constants.py) can
-- now get its own override, e.g. "Super Capital Ship" separately from
-- "Capital Ship", or "Capital Components" separately from "Advanced
-- Components" - pairs the flat 3-way split can't distinguish, since both
-- pairs currently share one override field each. See engine.py's
-- _job_cost_rate for the priority this slots into (highest): per-category
-- override (this table) beats the flat *_cost_index_override fields, which
-- still beat the Logistik category-system live lookup (GitHub issue #12,
-- job_category_locations below), which still beats the flat system_id
-- fallback, which still beats the hardcoded ACTIVITY_MODS rate.
--
-- Idempotent - safe to re-run. Same conventions as job_category_locations
-- (docs/phase1_schema.sql) - composite-PK bucket, `category` is a free-text
-- key validated against JOB_CATEGORIES in Python (production/actions.py's
-- do_set_category_cost_index_override), not a DB constraint - same
-- approach job_category_locations already uses.
--
-- Usage (same as phase1_schema.sql/production_buy_list_schema.sql - owner
-- role only, never the app's own eve_trader_app role):
--   local dev:  Get-Content docs\job_category_cost_index_overrides_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/job_category_cost_index_overrides_schema.sql

CREATE TABLE IF NOT EXISTS job_category_cost_index_overrides (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    category TEXT NOT NULL,
    cost_index_override REAL NOT NULL,
    PRIMARY KEY (tenant_id, category)
);
ALTER TABLE job_category_cost_index_overrides ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON job_category_cost_index_overrides;
CREATE POLICY tenant_isolation ON job_category_cost_index_overrides
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON job_category_cost_index_overrides TO eve_trader_app;
