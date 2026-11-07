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

-- 2026-10-31: the "Drones & Ammunition" job category was split into
-- "Drones & Fighters" and "Ammunition" (production/constants.py
-- JOB_CATEGORIES). The structure, structure history and cost index override
-- a tenant had set for the old bucket move to "Drones & Fighters";
-- "Ammunition" starts unset. Lives here rather than in phase1_schema.sql
-- because this file runs after phase1 and owns the third table involved.
-- Idempotent: once no "Drones & Ammunition" rows are left, every statement
-- is a no-op.
INSERT INTO job_category_locations (tenant_id, category, location_id)
    SELECT tenant_id, 'Drones & Fighters', location_id
    FROM job_category_locations WHERE category = 'Drones & Ammunition'
    ON CONFLICT (tenant_id, category) DO NOTHING;
DELETE FROM job_category_locations WHERE category = 'Drones & Ammunition';

INSERT INTO category_location_options (tenant_id, category, location_id)
    SELECT tenant_id, 'Drones & Fighters', location_id
    FROM category_location_options WHERE category = 'Drones & Ammunition'
    ON CONFLICT (tenant_id, category, location_id) DO NOTHING;
DELETE FROM category_location_options WHERE category = 'Drones & Ammunition';

INSERT INTO job_category_cost_index_overrides (tenant_id, category, cost_index_override)
    SELECT tenant_id, 'Drones & Fighters', cost_index_override
    FROM job_category_cost_index_overrides WHERE category = 'Drones & Ammunition'
    ON CONFLICT (tenant_id, category) DO NOTHING;
DELETE FROM job_category_cost_index_overrides WHERE category = 'Drones & Ammunition';

-- 2026-11-04: per-category structure type and rig tiers. Each job category
-- is built in its own structure, often with two ME rigs (one for Tech I,
-- one for Tech II/III items, e.g. "Basic" vs "Advanced Large Ship" rigs).
-- NULL means "not set": production/engine.py's _facility_mults then reads
-- the tier from the category structure's name, and falls back to the global
-- ProductionConfig profile when the name says nothing.
CREATE TABLE IF NOT EXISTS job_category_facilities (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    category TEXT NOT NULL,
    structure_type TEXT,
    rig_tier_basic TEXT,
    rig_tier_advanced TEXT,
    PRIMARY KEY (tenant_id, category)
);
ALTER TABLE job_category_facilities ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON job_category_facilities;
CREATE POLICY tenant_isolation ON job_category_facilities
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON job_category_facilities TO eve_trader_app;

-- Manual facility tax override per category (a fraction, 0.0025 = 0.25%).
-- NULL means "not set": the engine then uses the rate observed on the last
-- own jobs at the category's structure, and the Settings rate when there is
-- none (production/facility_tax.py, engine.facility_resolution).
ALTER TABLE job_category_facilities ADD COLUMN IF NOT EXISTS facility_tax_rate DOUBLE PRECISION;
