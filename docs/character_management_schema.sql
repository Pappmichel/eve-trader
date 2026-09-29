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

-- ------------------------------------------------------------------ phase 3
-- Mail archive (docs/CHARACTER_MANAGEMENT_PLAN.md decision 4). Mail is read
-- LIVE from ESI by default and nothing about it is stored. These tables hold
-- data ONLY for a character whose "Archive mail" checkbox is ticked
-- (char_mail_archive_settings.enabled). The archive has no size limit and is
-- never cleared automatically - not by the ESI stale clear (mail is not an
-- orchestrator kind), not by a failed sync. It is removed only by the explicit
-- "stop archiving and delete" action, which also garbage-collects messages no
-- character header references any more.
--
-- mail_id is shared by every recipient of a message (one corp mail received by
-- three alts is one mail_id), so the message (subject/body/recipients) is
-- stored once per tenant and each character's read state + labels live in
-- mail_character_headers.

CREATE TABLE IF NOT EXISTS char_mail_archive_settings (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_id BIGINT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    -- idle | running | done | error. A 'running' row whose thread died with the
    -- process is reported as interrupted by the app and resumed from the cursor.
    backfill_state TEXT NOT NULL DEFAULT 'idle',
    -- Next `last_mail_id` to request when paging history (NULL = start at newest).
    backfill_cursor BIGINT,
    headers_complete BOOLEAN NOT NULL DEFAULT FALSE,
    backfill_error TEXT,
    last_refresh_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, character_id)
);
ALTER TABLE char_mail_archive_settings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON char_mail_archive_settings;
CREATE POLICY tenant_isolation ON char_mail_archive_settings
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON char_mail_archive_settings TO eve_trader_app;

CREATE TABLE IF NOT EXISTS mail_messages (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    mail_id BIGINT NOT NULL,
    from_id BIGINT,
    subject TEXT NOT NULL DEFAULT '',
    "timestamp" TIMESTAMPTZ,
    body TEXT,
    body_fetched_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, mail_id)
);
ALTER TABLE mail_messages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON mail_messages;
CREATE POLICY tenant_isolation ON mail_messages
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON mail_messages TO eve_trader_app;
CREATE INDEX IF NOT EXISTS mail_messages_timestamp_idx ON mail_messages (tenant_id, "timestamp" DESC);
-- Full-text search over subject + body (archive only).
CREATE INDEX IF NOT EXISTS mail_messages_fts_idx ON mail_messages
    USING GIN (to_tsvector('simple', coalesce(subject, '') || ' ' || coalesce(body, '')));

CREATE TABLE IF NOT EXISTS mail_recipients (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    mail_id BIGINT NOT NULL,
    recipient_id BIGINT NOT NULL,
    recipient_type TEXT NOT NULL,
    PRIMARY KEY (tenant_id, mail_id, recipient_type, recipient_id)
);
ALTER TABLE mail_recipients ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON mail_recipients;
CREATE POLICY tenant_isolation ON mail_recipients
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON mail_recipients TO eve_trader_app;

CREATE TABLE IF NOT EXISTS mail_character_headers (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_id BIGINT NOT NULL,
    mail_id BIGINT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    labels INTEGER[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, character_id, mail_id)
);
ALTER TABLE mail_character_headers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON mail_character_headers;
CREATE POLICY tenant_isolation ON mail_character_headers
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON mail_character_headers TO eve_trader_app;
CREATE INDEX IF NOT EXISTS mail_character_headers_labels_idx
    ON mail_character_headers USING GIN (labels);

CREATE TABLE IF NOT EXISTS mail_labels (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_id BIGINT NOT NULL,
    label_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    color TEXT,
    unread_count INTEGER,
    PRIMARY KEY (tenant_id, character_id, label_id)
);
ALTER TABLE mail_labels ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON mail_labels;
CREATE POLICY tenant_isolation ON mail_labels
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON mail_labels TO eve_trader_app;

CREATE TABLE IF NOT EXISTS mail_lists (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_id BIGINT NOT NULL,
    list_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (tenant_id, character_id, list_id)
);
ALTER TABLE mail_lists ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON mail_lists;
CREATE POLICY tenant_isolation ON mail_lists
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON mail_lists TO eve_trader_app;

-- ------------------------------------------------------------------ phase 5c
-- Clones and implants. `clones` fills the first three tables (home location,
-- jump clones and the implants sitting in each jump clone), `implants` the
-- active implants. Character-partitioned snapshots, replaced per owner on each
-- successful sync. character_clone_meta also carries last_clone_jump_date,
-- which phase 7 (jump timers) reads.

CREATE TABLE IF NOT EXISTS character_clone_meta (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    home_location_id BIGINT,
    home_location_type TEXT,
    last_clone_jump_date TIMESTAMPTZ,
    last_station_change_date TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id)
);
ALTER TABLE character_clone_meta ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_clone_meta;
CREATE POLICY tenant_isolation ON character_clone_meta
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_clone_meta TO eve_trader_app;

CREATE TABLE IF NOT EXISTS character_jump_clones (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    jump_clone_id BIGINT NOT NULL,
    location_id BIGINT,
    location_type TEXT,
    name TEXT,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id, jump_clone_id)
);
ALTER TABLE character_jump_clones ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_jump_clones;
CREATE POLICY tenant_isolation ON character_jump_clones
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_jump_clones TO eve_trader_app;

CREATE TABLE IF NOT EXISTS character_jump_clone_implants (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    jump_clone_id BIGINT NOT NULL,
    type_id INTEGER NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id, jump_clone_id, type_id)
);
ALTER TABLE character_jump_clone_implants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_jump_clone_implants;
CREATE POLICY tenant_isolation ON character_jump_clone_implants
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_jump_clone_implants TO eve_trader_app;

CREATE TABLE IF NOT EXISTS character_implants (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    type_id INTEGER NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id, type_id)
);
ALTER TABLE character_implants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_implants;
CREATE POLICY tenant_isolation ON character_implants
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_implants TO eve_trader_app;

-- ------------------------------------------------------------------ phase 6
-- Notifications. character_notifications mirrors what ESI currently returns
-- for the character (replaced per owner on each successful sync). `text` is
-- ESI's raw YAML, parsed on read. character_notification_reads is this app's
-- own read flag (ESI has no write for notifications); it is separate so a
-- snapshot replace never loses a flag, and pruned to ids still in the snapshot.

CREATE TABLE IF NOT EXISTS character_notifications (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    notification_id BIGINT NOT NULL,
    type TEXT NOT NULL,
    sender_id BIGINT,
    sender_type TEXT,
    sent_at TIMESTAMPTZ NOT NULL,
    esi_is_read BOOLEAN NOT NULL DEFAULT FALSE,
    text TEXT,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id, notification_id)
);
ALTER TABLE character_notifications ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_notifications;
CREATE POLICY tenant_isolation ON character_notifications
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_notifications TO eve_trader_app;

CREATE TABLE IF NOT EXISTS character_notification_reads (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    notification_id BIGINT NOT NULL,
    read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_character_id, notification_id)
);
ALTER TABLE character_notification_reads ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_notification_reads;
CREATE POLICY tenant_isolation ON character_notification_reads
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_notification_reads TO eve_trader_app;

-- ------------------------------------------------------------------ phase 9
-- Skill plans (docs/CHARACTER_MANAGEMENT_PLAN.md). Tenant data the user writes
-- themselves, not ESI data: no sharing, no sync, no stale clear. A plan is an
-- ordered list of (skill, level) steps that is kept self-contained - every
-- step's prerequisites (the level below, and the skill's own prerequisites)
-- sit earlier in the same plan (the editor inserts them and prunes dependents).
CREATE TABLE IF NOT EXISTS skill_plans (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS skill_plans_tenant_idx ON skill_plans (tenant_id, id);
ALTER TABLE skill_plans ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON skill_plans;
CREATE POLICY tenant_isolation ON skill_plans
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON skill_plans TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE skill_plans_id_seq TO eve_trader_app;

CREATE TABLE IF NOT EXISTS skill_plan_items (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    plan_id BIGINT NOT NULL REFERENCES skill_plans(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    skill_id INTEGER NOT NULL,
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 5),
    PRIMARY KEY (tenant_id, plan_id, position),
    UNIQUE (tenant_id, plan_id, skill_id, level)
);
ALTER TABLE skill_plan_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON skill_plan_items;
CREATE POLICY tenant_isolation ON skill_plan_items
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON skill_plan_items TO eve_trader_app;

-- ----------------------------------------------------------------- phase 10
-- Discord alerts (docs/DISCORD_ALERTS_HANDOFF.md). One Discord user id per
-- tenant (one character per tenant is a DB guarantee), the bot DMs it. The bot
-- token is an operator env variable, never stored here. Every subscription is
-- opt-in, default off; `alert_state` holds dedupe/baseline state only.
CREATE TABLE IF NOT EXISTS alert_destinations (
    tenant_id UUID PRIMARY KEY DEFAULT current_setting('app.tenant_id', false)::uuid,
    discord_user_id TEXT NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE alert_destinations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON alert_destinations;
CREATE POLICY tenant_isolation ON alert_destinations
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON alert_destinations TO eve_trader_app;

CREATE TABLE IF NOT EXISTS alert_subscriptions (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_id BIGINT NOT NULL,
    alert_type TEXT NOT NULL CHECK (alert_type IN ('skillqueue_empty', 'mail_new')),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    include_content BOOLEAN NOT NULL DEFAULT FALSE,
    lead_hours INTEGER NOT NULL DEFAULT 12 CHECK (lead_hours BETWEEN 1 AND 168),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, character_id, alert_type)
);
ALTER TABLE alert_subscriptions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON alert_subscriptions;
CREATE POLICY tenant_isolation ON alert_subscriptions
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON alert_subscriptions TO eve_trader_app;

CREATE TABLE IF NOT EXISTS alert_state (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_id BIGINT NOT NULL,
    alert_type TEXT NOT NULL CHECK (alert_type IN ('skillqueue_empty', 'mail_new')),
    last_seen_mail_id BIGINT,
    last_key TEXT,
    last_sent_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, character_id, alert_type)
);
ALTER TABLE alert_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON alert_state;
CREATE POLICY tenant_isolation ON alert_state
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON alert_state TO eve_trader_app;
