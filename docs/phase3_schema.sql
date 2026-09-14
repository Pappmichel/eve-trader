-- Phase 3 of the multi-tenant migration (see docs/MULTI_TENANT_PLAN.md) -
-- the tenant registry: `tenants` (the directory of tenants) and
-- `tenant_registry_entries` (character/corp/alliance id -> tenant_id).
--
-- Deliberately NOT RLS-scoped like every other table so far - these two
-- ARE the thing that answers "which tenant is this?", so they have to be
-- queryable *before* any tenant_id is known (see storage.py's
-- connect_unscoped(), the only thing that ever touches these tables).
--
-- Idempotent, same conventions as docs/phase1_schema.sql/phase2_schema.sql.
-- Usage (same local dev Postgres as Phase 0-2):
--   Get-Content docs\phase3_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_registry_entries (
    entry_type TEXT NOT NULL CHECK (entry_type IN ('character', 'corporation', 'alliance')),
    entry_id BIGINT NOT NULL,
    tenant_id UUID NOT NULL,
    PRIMARY KEY (entry_type, entry_id)
);

-- The fixed default tenant - see storage.DEFAULT_TENANT_ID. Covers the
-- access-gate-disabled case (opt-in via config.yaml; the gate is on by
-- default) so AccessGateMiddleware always has *some* tenant to set when
-- there is no session cookie, and the CLI's trusted-operator path.
INSERT INTO tenants (tenant_id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'Default')
ON CONFLICT (tenant_id) DO NOTHING;

GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, tenant_registry_entries TO eve_trader_app;

-- F-01/F-03 session revocation timestamp. Also in admin_schema.sql so
-- existing installs that already applied this file pick it up on the next
-- deploy. NULL = never revoked (no global logout on migrate).
ALTER TABLE tenant_registry_entries
    ADD COLUMN IF NOT EXISTS sessions_valid_after TIMESTAMPTZ;
