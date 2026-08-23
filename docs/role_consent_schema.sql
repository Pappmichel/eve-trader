-- Login-role data-access consent tracking - a tenant confirms, once per
-- role_prefix, that they understand what ESI data a given character-login
-- role reads before EVE SSO ever redirects them (see api/routers/auth.py's
-- new /{role_prefix}/consent GET/POST endpoints and the frontend's shared
-- role-login confirm modal). Additive only, idempotent - safe to re-run.
--
-- Usage (same as phase1_schema.sql/doctrine_schema.sql - owner role only,
-- never the app's own eve_trader_app role):
--   local dev:  Get-Content docs\role_consent_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/role_consent_schema.sql

-- role_prefix is a fixed, small literal set ("buyer"/"seller"/"producer"/
-- "doctrine"/"doctrine-assets"/"gate" - see auth.py's own ROLE_PREFIX_TOOL)
-- reused across every tenant, not a per-tenant-unique value - composite PK
-- bucket, same shape as manual_blueprint_copy_costs/stock_targets
-- (phase1_schema.sql's own "composite-PK bucket" section). "gate" is
-- deliberately NOT written here pre-login (no tenant exists yet at that
-- point - the frontend uses localStorage for that one role instead, see
-- Landing.tsx) - only recorded here after a successful gate login
-- completes, informational, not yet gating anything.
CREATE TABLE IF NOT EXISTS tenant_role_consents (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    role_prefix TEXT NOT NULL,
    acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, role_prefix)
);
ALTER TABLE tenant_role_consents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tenant_role_consents;
CREATE POLICY tenant_isolation ON tenant_role_consents
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT ON tenant_role_consents TO eve_trader_app;
