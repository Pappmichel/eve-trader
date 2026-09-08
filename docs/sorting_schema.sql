-- Sorting tool schema. Additive only - never edits phase1/phase2/phase3_
-- schema.sql, which record the multi-tenant migration's own history
-- (see CLAUDE.md's "Adding a new per-tenant table" section for the
-- conventions this file follows, and docs/refining_schema.sql for the
-- worked example this mirrors). Idempotent - every statement is safe to
-- re-run.
--
-- No tenant_settings.scope widening here: the Sorting tool has no scalar
-- SortingConfig to persist (intake sources are this table's own CRUD, not
-- a Settings-page override blob), so save_tenant_config_overrides never
-- writes scope='sorting'. If that changes, EVERY existing schema file's
-- copy of tenant_settings_scope_check needs 'sorting' added too, not just
-- this one (see docs/refining_schema.sql's own comment on that ALTER).
--
-- Usage (same as phase1_schema.sql/refining_schema.sql - owner role only,
-- never the app's own eve_trader_app role):
--   local dev:  Get-Content docs\sorting_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/sorting_schema.sql

-- ====================================================== per-tenant: intake sources
-- One row per configured Wareneingang: a character's personal hangar
-- (source_kind='character', filtered to owner_name=character_name on
-- character_assets) or a shared corp hangar division (source_kind='corp',
-- no owner_name filter, corp_assets only). Surrogate PK - there is no
-- natural key that couldn't collide across tenants in a meaningful way
-- (two tenants can both have a "Hangar" source for a character named
-- "Bob"), so id is globally unique and tenant_id is column-only for RLS
-- (see docs/phase1_schema.sql's column-only bucket).
CREATE TABLE IF NOT EXISTS sorting_intake_sources (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('character', 'corp')),
    -- Required (and non-empty) for character sources; NULL for corp
    -- sources, which aren't owned by one character.
    character_name TEXT,
    hangar_flag TEXT NOT NULL,
    label TEXT,
    CONSTRAINT sorting_intake_sources_character_name_check CHECK (
        (source_kind = 'character' AND character_name IS NOT NULL AND character_name <> '')
        OR (source_kind = 'corp' AND character_name IS NULL)
    )
);
ALTER TABLE sorting_intake_sources ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON sorting_intake_sources;
CREATE POLICY tenant_isolation ON sorting_intake_sources
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE INDEX IF NOT EXISTS idx_sorting_intake_sources_tenant ON sorting_intake_sources (tenant_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON sorting_intake_sources TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE sorting_intake_sources_id_seq TO eve_trader_app;
