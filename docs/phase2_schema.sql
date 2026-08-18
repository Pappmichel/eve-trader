-- Phase 2 of the multi-tenant migration (see docs/MULTI_TENANT_PLAN.md) -
-- config/secrets storage: tenant_settings (TradingConfig/ProductionConfig
-- overrides, replacing config.yaml as the per-tenant persistence backend)
-- and tenant_tokens (OAuth token storage - schema only in this phase,
-- TokenManager itself stays file-based until Phase 3 threads a tenant_id
-- through the OAuth callback flow).
--
-- Idempotent, same conventions as docs/phase1_schema.sql - safe to re-run.
-- Usage (same local dev Postgres as Phase 0/1):
--   Get-Content docs\phase2_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader

CREATE TABLE IF NOT EXISTS tenant_settings (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    scope TEXT NOT NULL CHECK (scope IN ('trading', 'production')),
    overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (tenant_id, scope)
);
ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tenant_settings;
CREATE POLICY tenant_isolation ON tenant_settings
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

-- Schema only for now - see module docstring above. Shape mirrors
-- eve_trader/auth.py's TokenRecord dataclass (role, character_id,
-- character_name, access_token, refresh_token, expires_at, scopes),
-- stored as JSONB the same way asdict(record) already serializes it today.
CREATE TABLE IF NOT EXISTS tenant_tokens (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    role TEXT NOT NULL,
    record JSONB NOT NULL,
    PRIMARY KEY (tenant_id, role)
);
ALTER TABLE tenant_tokens ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tenant_tokens;
CREATE POLICY tenant_isolation ON tenant_tokens
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_settings, tenant_tokens TO eve_trader_app;
