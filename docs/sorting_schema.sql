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
-- (source_kind='character', filtered to owner_name on character_assets)
-- or a corp hangar division (source_kind='corp', filtered to owner_name on
-- corp_assets). Both kinds store which owner to count - a tenant can have
-- more than one corp's assets synced, so a corp source is not "all corps
-- together". Surrogate PK - there is no natural key that couldn't collide
-- across tenants in a meaningful way (two tenants can both have a
-- "Hangar" source for a character named "Bob"), so id is globally unique
-- and tenant_id is column-only for RLS (see docs/phase1_schema.sql's
-- column-only bucket).
CREATE TABLE IF NOT EXISTS sorting_intake_sources (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('character', 'corp')),
    -- Required (and non-empty) for both kinds: the character or corp whose
    -- assets this source should count (matches character_assets/corp_assets
    -- owner_name).
    owner_name TEXT,
    hangar_flag TEXT NOT NULL,
    label TEXT,
    CONSTRAINT sorting_intake_sources_owner_name_check CHECK (
        owner_name IS NOT NULL AND owner_name <> ''
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

-- character_name -> owner_name: corp sources used to be forced NULL (one
-- unnamed shared division). They now name the corp, same as a character
-- source names the character. Idempotent: CREATE TABLE IF NOT EXISTS
-- above is a no-op on a live DB that already has the old column; a fresh
-- DB already has owner_name and skips the rename. Existing corp rows with
-- a NULL name cannot be mapped to one of several synced corps, so they
-- are dropped rather than left to fail the new CHECK (re-add them from
-- Settings with a concrete corp).
ALTER TABLE sorting_intake_sources DROP CONSTRAINT IF EXISTS sorting_intake_sources_character_name_check;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public'
                 AND table_name = 'sorting_intake_sources'
                 AND column_name = 'character_name') THEN
        ALTER TABLE sorting_intake_sources RENAME COLUMN character_name TO owner_name;
    END IF;
END
$$;
DELETE FROM sorting_intake_sources WHERE owner_name IS NULL OR owner_name = '';
ALTER TABLE sorting_intake_sources DROP CONSTRAINT IF EXISTS sorting_intake_sources_owner_name_check;
ALTER TABLE sorting_intake_sources ADD CONSTRAINT sorting_intake_sources_owner_name_check
    CHECK (owner_name IS NOT NULL AND owner_name <> '');
