-- Ore & Minerals ("refining") tool schema, phase 1/5 - GitHub issue #90.
-- Additive only - never edits phase1/phase2/phase3_schema.sql, which record
-- the multi-tenant migration's own history (see CLAUDE.md's "Adding a new
-- per-tenant table" section for the conventions this file follows).
-- Idempotent - every statement is safe to re-run.
--
-- Usage (same as phase1_schema.sql/doctrine_schema.sql - owner role only,
-- never the app's own eve_trader_app role):
--   local dev:  Get-Content docs\refining_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/refining_schema.sql

-- ========================================================= existing-table widening
-- phase2_schema.sql's tenant_settings.scope check constraint only allowed
-- 'trading'/'production'/'doctrine' (doctrine_schema.sql having already
-- widened it once) - widened again here (additive, this file's own, never
-- editing phase2_schema.sql/doctrine_schema.sql themselves) so
-- save_tenant_config_overrides("refining", ...) can persist RefiningConfig
-- Settings-page saves the same way Trading/Production/Doctrine already do.
ALTER TABLE tenant_settings DROP CONSTRAINT IF EXISTS tenant_settings_scope_check;
ALTER TABLE tenant_settings ADD CONSTRAINT tenant_settings_scope_check
    CHECK (scope IN ('trading', 'production', 'doctrine', 'refining'));

-- sde_types (phase1_schema.sql) gets a new trailing column - portionSize
-- (Fuzzwork invTypes.csv), the whole-batch unit reprocessing rounds down to
-- before applying yield% (e.g. Veldspar=100) - see eve_trader/refining/
-- engine.py's apply_reprocessing_yield and storage.get_portion_size. Nullable
-- and additive: existing positional readers of get_sde_type()'s tuple
-- (indices 0-7) are unaffected, and a not-yet-refreshed row simply reads as
-- NULL until the next "Refresh SDE".
ALTER TABLE sde_types ADD COLUMN IF NOT EXISTS portion_size INTEGER;

-- ============================================================== shared table
-- SDE's invTypeMaterials.csv (type_id -> material_type_id/quantity-per-
-- portion) - no tenant_id, no RLS, same bucket as every other sde_* reference
-- table (identical for every tenant, refreshed globally via the existing
-- Admin "Refresh SDE" button, production/sde.py's refresh_sde). Serves both
-- the ore/ice reprocessing path and the scrapmetal path (both are "type ->
-- material yield" lookups against this one table) - see storage.
-- get_type_materials.
CREATE TABLE IF NOT EXISTS sde_type_materials (
    type_id INTEGER NOT NULL,
    material_type_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    PRIMARY KEY (type_id, material_type_id)
);

CREATE INDEX IF NOT EXISTS idx_sde_type_materials_by_type ON sde_type_materials (type_id);

-- eve_trader_app needs read/write on these like every other sde_* table
-- (populated by refresh_sde(), read by every tenant) and on the widened
-- tenant_settings constraint's underlying table.
GRANT SELECT, INSERT, UPDATE, DELETE ON sde_type_materials TO eve_trader_app;
