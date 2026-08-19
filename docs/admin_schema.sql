-- Tool-permission system (see the Login/Admin architecture review this was
-- built from). Additive only - never edits phase1/phase2/phase3_schema.sql,
-- which record the multi-tenant migration's own history. Idempotent - every
-- statement is safe to re-run.
--
-- Usage (same as doctrine_schema.sql - owner role only, never the app's own
-- eve_trader_app role):
--   local dev:  Get-Content docs\admin_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/admin_schema.sql

-- ========================================================= existing-table widening
-- Character name, cached once at "add user"/first-login time (see
-- admin.do_add_user / auth.py's /callback gate branch) - avoids a live ESI
-- call every time the Admin page's user list is rendered.
ALTER TABLE tenant_registry_entries ADD COLUMN IF NOT EXISTS character_name TEXT;

-- AccessGate is character-only now (corp/alliance registry entries retired -
-- see the Login/Admin architecture review; live-checked before this change
-- that no corp/alliance rows existed). Narrows the CHECK constraint that
-- used to allow all three entry_type values.
ALTER TABLE tenant_registry_entries DROP CONSTRAINT IF EXISTS tenant_registry_entries_entry_type_check;
ALTER TABLE tenant_registry_entries ADD CONSTRAINT tenant_registry_entries_entry_type_check
    CHECK (entry_type = 'character');

-- ============================================================== shared table
-- tool_grants: bewusst NICHT RLS-gescoped, same reasoning as tenants/
-- tenant_registry_entries in phase3_schema.sql - these ARE the thing that
-- answers "which tools can this character see", and the Admin tool is a
-- deliberate cross-tenant superadmin surface (only DEFAULT_TENANT_ID's own
-- users ever reach it - see admin.py's own module docstring), so it has to
-- read/write across tenant boundaries, which per-tenant RLS would block.
CREATE TABLE IF NOT EXISTS tool_grants (
    character_id BIGINT NOT NULL,
    tool_key TEXT NOT NULL,
    tenant_id UUID NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (character_id, tool_key)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON tool_grants TO eve_trader_app;

-- ================================================== one tenant per character
-- Every logged-in character gets its own dedicated tenant now - no sharing,
-- not even by an admin's mistake (see CLAUDE.md's "Multi-tenant Postgres").
-- admin.do_add_user always creates a brand-new tenant as part of adding a
-- user (there's no more "assign to an existing tenant" flow), so this is a
-- DB-level backstop for that invariant, not just the UI/action-layer logic.
ALTER TABLE tenant_registry_entries DROP CONSTRAINT IF EXISTS tenant_registry_entries_tenant_id_unique;
ALTER TABLE tenant_registry_entries ADD CONSTRAINT tenant_registry_entries_tenant_id_unique UNIQUE (tenant_id);
