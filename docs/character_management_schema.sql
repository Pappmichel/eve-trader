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

-- ------------------------------------------------------------------ phase 2
-- Skills (docs/CHARACTER_MANAGEMENT_PLAN.md). The skills fetcher keeps writing
-- character_slots (Production's job-slot counts, esi_access_schema.sql /
-- phase1) and now also stores every skill, the attribute block and the SP
-- totals here. Character-partitioned snapshots, replaced per owner on each
-- successful sync; the stale clear (esi_data/stale.py) may remove them after
-- the grace window, but never touches character_slots.

CREATE TABLE IF NOT EXISTS character_skills (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    skill_id INTEGER NOT NULL,
    active_level INTEGER NOT NULL,
    trained_level INTEGER NOT NULL,
    skillpoints_in_skill BIGINT NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id, skill_id)
);
ALTER TABLE character_skills ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_skills;
CREATE POLICY tenant_isolation ON character_skills
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_skills TO eve_trader_app;

-- One row per character. The SP totals come from the /skills/ response, the
-- attribute block from /attributes/ (same scope, esi-skills.read_skills.v1).
-- The two are written independently (a failing /attributes/ call must not
-- lose the totals, and must never break Production's slot sync), so every
-- attribute column is nullable.
CREATE TABLE IF NOT EXISTS character_attributes (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    total_sp BIGINT,
    unallocated_sp BIGINT,
    charisma INTEGER,
    intelligence INTEGER,
    memory INTEGER,
    perception INTEGER,
    willpower INTEGER,
    bonus_remaps INTEGER,
    last_remap_date TIMESTAMPTZ,
    accrued_remap_cooldown_date TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id)
);
ALTER TABLE character_attributes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_attributes;
CREATE POLICY tenant_isolation ON character_attributes
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_attributes TO eve_trader_app;

-- Dates are nullable: a paused queue reports entries without start/finish.
CREATE TABLE IF NOT EXISTS character_skillqueue (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    queue_position INTEGER NOT NULL,
    skill_id INTEGER NOT NULL,
    finished_level INTEGER NOT NULL,
    start_date TIMESTAMPTZ,
    finish_date TIMESTAMPTZ,
    training_start_sp BIGINT,
    level_start_sp BIGINT,
    level_end_sp BIGINT,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id, queue_position)
);
ALTER TABLE character_skillqueue ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_skillqueue;
CREATE POLICY tenant_isolation ON character_skillqueue
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_skillqueue TO eve_trader_app;

-- Global SDE tables (no tenant_id, no RLS - same as every other sde_* table,
-- phase1_schema.sql), wholesale-replaced by storage.replace_sde_data on an SDE
-- apply. Filled from Fuzzwork's dgmTypeAttributes.csv, filtered while parsing
-- to just the attribute ids below (the whole file is far larger than needed):
--   requiredSkill1..6 = 182,183,184,1285,1289,1290 with their levels
--   277,278,279,1286,1287,1288; skillTimeConstant (rank) = 275;
--   primaryAttribute = 180; secondaryAttribute = 181.
-- Empty until the next SDE refresh - a deployment that already has a current
-- SDE must run Admin's SDE preview/apply once to populate them.
CREATE TABLE IF NOT EXISTS sde_skill_requirements (
    type_id INTEGER,
    skill_id INTEGER,
    level INTEGER,
    PRIMARY KEY (type_id, skill_id)
);

CREATE TABLE IF NOT EXISTS sde_skill_meta (
    skill_id INTEGER PRIMARY KEY,
    rank REAL,
    primary_attribute INTEGER,
    secondary_attribute INTEGER
);

GRANT SELECT, INSERT, UPDATE, DELETE ON sde_skill_requirements TO eve_trader_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON sde_skill_meta TO eve_trader_app;
