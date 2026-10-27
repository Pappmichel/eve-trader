-- Central location resolution (docs/LOCATION_RESOLUTION_PLAN.md phases 2-3):
-- one row per location id this tenant's data references that is not an NPC
-- station or a solar system (those come from the SDE). Records what the id is
-- (kind), the parent an Office or container resolves through, the resolved
-- name and the outcome of the last ESI lookup, so a failure has a reason and a
-- retry date instead of a bare NULL name. It replaces structure_names: the
-- one-time copy at the end of this file moves every name found there and then
-- drops that table (phase 7).
-- Idempotent - every statement is safe to re-run.
--
-- Usage (owner role only, never eve_trader_app):
--   local dev:  Get-Content docs\location_registry_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/location_registry_schema.sql

CREATE TABLE IF NOT EXISTS location_registry (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    location_id BIGINT NOT NULL,
    -- structure: a player structure ESI can name. office / container: an asset
    -- item that has no name of its own and shows its parent. other: an id in
    -- no known range (never sent to ESI).
    kind TEXT NOT NULL CHECK (kind IN ('structure', 'office', 'container', 'other')),
    parent_location_id BIGINT,
    name TEXT,
    solar_system_id INTEGER,
    -- pending: not looked up yet. resolved: name known. forbidden (403),
    -- not_found (404) and transient (network, 5xx, rate limit) are the last
    -- lookup's outcome; next_retry_at says when to try again.
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'resolved', 'forbidden', 'not_found', 'transient')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    next_retry_at TIMESTAMPTZ,
    last_error TEXT,                -- HTTP status text only, never a response body
    resolved_at TIMESTAMPTZ,
    resolved_via TEXT,              -- auth role of the character that last succeeded
    tried_by TEXT,                  -- digest of the capable characters at the last failure
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, location_id)
);
ALTER TABLE location_registry ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON location_registry;
CREATE POLICY tenant_isolation ON location_registry
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON location_registry TO eve_trader_app;
CREATE INDEX IF NOT EXISTS idx_location_registry_due
    ON location_registry (tenant_id, status, next_retry_at);

-- ====================================================== one-time copy (phase 7)
-- Names already resolved into the old per-tenant structure_names table, then
-- the table is dropped. Runs as the owner role, so RLS does not apply and
-- every tenant is copied. An id the registry already has keeps its row,
-- except that a row without a name takes the old name. Once the table is gone
-- the block does nothing, so the file stays safe to re-run.
DO $$
BEGIN
    IF to_regclass('public.structure_names') IS NULL THEN
        RETURN;
    END IF;
    INSERT INTO location_registry (tenant_id, location_id, kind, name, solar_system_id, status, resolved_at)
    SELECT sn.tenant_id, sn.location_id, 'structure', sn.name, sn.solar_system_id, 'resolved', now()
    FROM structure_names sn
    WHERE sn.name IS NOT NULL AND sn.location_id >= 1000000000000
    ON CONFLICT (tenant_id, location_id) DO NOTHING;
    UPDATE location_registry r
    SET name = sn.name, solar_system_id = COALESCE(r.solar_system_id, sn.solar_system_id),
        status = 'resolved', resolved_at = COALESCE(r.resolved_at, now())
    FROM structure_names sn
    WHERE sn.tenant_id = r.tenant_id AND sn.location_id = r.location_id
      AND r.name IS NULL AND sn.name IS NOT NULL;
    DROP TABLE structure_names;
END $$;
