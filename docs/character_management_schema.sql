-- Character Management hub (docs/CHARACTER_MANAGEMENT_PLAN.md), phase 1.
--
-- Snapshot tables for the Character Info sub-tool. Only data kinds that are
-- synced live here: standings and loyalty points. Location / ship / online
-- status are live ESI reads (registry `live_only`) and deliberately have no
-- table at all. Later phases append their own tables to this file (skills,
-- skillqueue, mail archive, ...) - keep it one file so deploy.sh, both READMEs
-- and .cursor/start.sh only ever need this one name.
--
-- Both tables are character-partitioned snapshots replaced per owner on each
-- successful sync (same shape as character_wallet_balances in
-- esi_access_schema.sql); a failed sync keeps the old rows until the stale
-- clear (esi_data/stale.py) removes them. Composite PK leads with tenant_id:
-- two tenants can hold a token for the same EVE character id.
--
-- Idempotent - safe to re-run.
--   local dev:  Get-Content docs\character_management_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/character_management_schema.sql

CREATE TABLE IF NOT EXISTS character_standings (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    from_id BIGINT NOT NULL,
    from_type TEXT NOT NULL CHECK (from_type IN ('agent', 'npc_corp', 'faction')),
    standing DOUBLE PRECISION NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id, from_type, from_id)
);
ALTER TABLE character_standings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_standings;
CREATE POLICY tenant_isolation ON character_standings
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON character_standings TO eve_trader_app;

CREATE TABLE IF NOT EXISTS character_loyalty_points (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    corporation_id BIGINT NOT NULL,
    loyalty_points BIGINT NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id, corporation_id)
);
ALTER TABLE character_loyalty_points ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_loyalty_points;
CREATE POLICY tenant_isolation ON character_loyalty_points
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON character_loyalty_points TO eve_trader_app;
