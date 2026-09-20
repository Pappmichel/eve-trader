-- Character-centric ESI access (docs/ESI_ACCESS_PLAN.md Phase 1).
-- Additive only - never edits phase1/phase2/phase3_schema.sql.
-- Idempotent - every statement is safe to re-run.
--
-- New tables: esi_sharing (settled decision 3), esi_freshness
-- (decision 5), esi_character_capabilities (group 3 Access; no
-- tool_key — capabilities are not sharing rows), esi_wallet_
-- transactions / esi_wallet_journal (Phase 8 fetched wallet live and
-- never persisted it; consume shape is trade_reconciliation's).
-- Owner-id columns on ESI snapshot tables and sorting_intake_sources
-- (carry-forward hazard). BIGINT for every EVE id.
--
-- Usage (owner role only, never eve_trader_app):
--   local dev:  Get-Content docs\esi_access_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/esi_access_schema.sql

-- ====================================================== per-tenant: sharing
-- Composite-PK bucket: two tenants can both share character 123's assets
-- with production. Group 3 does NOT live here (no tool dimension).
CREATE TABLE IF NOT EXISTS esi_sharing (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    data_kind TEXT NOT NULL,
    tool_key TEXT NOT NULL,
    PRIMARY KEY (tenant_id, owner_type, owner_id, data_kind, tool_key)
);
ALTER TABLE esi_sharing ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON esi_sharing;
CREATE POLICY tenant_isolation ON esi_sharing
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON esi_sharing TO eve_trader_app;

-- ====================================================== per-tenant: freshness
CREATE TABLE IF NOT EXISTS esi_freshness (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    data_kind TEXT NOT NULL,
    last_success_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    last_error TEXT,
    PRIMARY KEY (tenant_id, owner_type, owner_id, data_kind)
);
ALTER TABLE esi_freshness ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON esi_freshness;
CREATE POLICY tenant_isolation ON esi_freshness
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON esi_freshness TO eve_trader_app;

-- ====================================================== per-tenant: group-3 capabilities
-- On/off per character, no tool_key. Presence of a row means the
-- capability is ticked. Not a sharing row.
CREATE TABLE IF NOT EXISTS esi_character_capabilities (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_id BIGINT NOT NULL,
    capability_key TEXT NOT NULL,
    PRIMARY KEY (tenant_id, character_id, capability_key)
);
ALTER TABLE esi_character_capabilities ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON esi_character_capabilities;
CREATE POLICY tenant_isolation ON esi_character_capabilities
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON esi_character_capabilities TO eve_trader_app;

-- ====================================================== per-tenant: wallet snapshots
-- Phase 8 added ESI client methods and a live fetch inside
-- trade_reconciliation; it did not add a table. Decision 5 and the
-- Phase 0 registry assume a Wallet snapshot (owned, frequent) that
-- do_reconcile_trades will consume instead of paging ESI inline. The
-- consume shape is already determined, so the tables land here rather
-- than waiting on Phase 3:
--   character identity: (character, character_id, transaction_id)
--   corp identity:      (corporation, corporation_id, division, transaction_id)
-- Journal lookup is wallet-local (journal id -> amount). Character
-- wallets have no ESI division; division DEFAULT 0 so the column can
-- sit in the PK (NULL cannot). Corp wallets use ESI divisions 1-7.
-- Empty until Phase 3's fetcher writes them; today's reconcile still
-- pages ESI. Columns are the fields trade_reconciliation already
-- reads, plus owner-id partition keys.

CREATE TABLE IF NOT EXISTS esi_wallet_transactions (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    division INTEGER NOT NULL DEFAULT 0,
    transaction_id BIGINT NOT NULL,
    date TIMESTAMPTZ NOT NULL,
    type_id INTEGER NOT NULL,
    location_id BIGINT NOT NULL,
    unit_price DOUBLE PRECISION NOT NULL,
    quantity INTEGER NOT NULL,
    is_buy BOOLEAN NOT NULL,
    journal_ref_id BIGINT NOT NULL,
    owner_character_id BIGINT,
    owner_corporation_id BIGINT,
    PRIMARY KEY (tenant_id, owner_type, owner_id, division, transaction_id),
    CONSTRAINT esi_wallet_transactions_division_check CHECK (
        (owner_type = 'character' AND division = 0)
        OR (owner_type = 'corporation' AND division BETWEEN 1 AND 7)
    ),
    -- Partition columns stay (Phase 2's delete helper is generic across
    -- snapshot tables). The CHECK ties them to the PK so a character row
    -- cannot carry a different owner_character_id, or a corp id.
    CONSTRAINT esi_wallet_transactions_owner_ids_check CHECK (
        (owner_type = 'character'
         AND owner_character_id = owner_id
         AND owner_corporation_id IS NULL)
        OR (owner_type = 'corporation'
            AND owner_corporation_id = owner_id
            AND owner_character_id IS NULL)
    )
);
ALTER TABLE esi_wallet_transactions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON esi_wallet_transactions;
CREATE POLICY tenant_isolation ON esi_wallet_transactions
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE INDEX IF NOT EXISTS idx_esi_wallet_transactions_owner_character
    ON esi_wallet_transactions (owner_character_id);
CREATE INDEX IF NOT EXISTS idx_esi_wallet_transactions_owner_corporation
    ON esi_wallet_transactions (owner_corporation_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON esi_wallet_transactions TO eve_trader_app;

-- Inline CHECK above is what a fresh CREATE TABLE gets. Postgres has no
-- ADD CONSTRAINT IF NOT EXISTS; DROP/ADD is the idempotent way to attach
-- the same check to a table this file already created before the
-- constraint existed (this PR's first revision).
ALTER TABLE esi_wallet_transactions DROP CONSTRAINT IF EXISTS esi_wallet_transactions_owner_ids_check;
ALTER TABLE esi_wallet_transactions ADD CONSTRAINT esi_wallet_transactions_owner_ids_check CHECK (
    (owner_type = 'character'
     AND owner_character_id = owner_id
     AND owner_corporation_id IS NULL)
    OR (owner_type = 'corporation'
        AND owner_corporation_id = owner_id
        AND owner_character_id IS NULL)
);

CREATE TABLE IF NOT EXISTS esi_wallet_journal (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    division INTEGER NOT NULL DEFAULT 0,
    journal_id BIGINT NOT NULL,
    date TIMESTAMPTZ NOT NULL,
    ref_type TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    owner_character_id BIGINT,
    owner_corporation_id BIGINT,
    PRIMARY KEY (tenant_id, owner_type, owner_id, division, journal_id),
    CONSTRAINT esi_wallet_journal_division_check CHECK (
        (owner_type = 'character' AND division = 0)
        OR (owner_type = 'corporation' AND division BETWEEN 1 AND 7)
    ),
    CONSTRAINT esi_wallet_journal_owner_ids_check CHECK (
        (owner_type = 'character'
         AND owner_character_id = owner_id
         AND owner_corporation_id IS NULL)
        OR (owner_type = 'corporation'
            AND owner_corporation_id = owner_id
            AND owner_character_id IS NULL)
    )
);
ALTER TABLE esi_wallet_journal ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON esi_wallet_journal;
CREATE POLICY tenant_isolation ON esi_wallet_journal
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE INDEX IF NOT EXISTS idx_esi_wallet_journal_owner_character
    ON esi_wallet_journal (owner_character_id);
CREATE INDEX IF NOT EXISTS idx_esi_wallet_journal_owner_corporation
    ON esi_wallet_journal (owner_corporation_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON esi_wallet_journal TO eve_trader_app;

ALTER TABLE esi_wallet_journal DROP CONSTRAINT IF EXISTS esi_wallet_journal_owner_ids_check;
ALTER TABLE esi_wallet_journal ADD CONSTRAINT esi_wallet_journal_owner_ids_check CHECK (
    (owner_type = 'character'
     AND owner_character_id = owner_id
     AND owner_corporation_id IS NULL)
    OR (owner_type = 'corporation'
        AND owner_corporation_id = owner_id
        AND owner_character_id IS NULL)
);

-- ====================================================== owner ids on ESI snapshot tables
-- Nullable for the duration of the backfill (existing rows). Partitioned
-- deletes in Phase 2 key on these, not owner_name. structure_names is
-- tenant-wide reference data and is not listed here. Wallet snapshots
-- above carry the same columns from CREATE (empty table, no backfill).

ALTER TABLE character_assets ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
ALTER TABLE character_assets ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;
ALTER TABLE corp_assets ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
ALTER TABLE corp_assets ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;

ALTER TABLE character_industry_jobs ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
ALTER TABLE character_industry_jobs ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;
ALTER TABLE corp_industry_jobs ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
ALTER TABLE corp_industry_jobs ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;

ALTER TABLE character_blueprints ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
ALTER TABLE character_blueprints ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;
ALTER TABLE corp_blueprints ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
ALTER TABLE corp_blueprints ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;

ALTER TABLE character_sell_orders ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
ALTER TABLE character_sell_orders ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;

ALTER TABLE character_slots ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
ALTER TABLE character_slots ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_character_assets_owner_character
    ON character_assets (owner_character_id);
CREATE INDEX IF NOT EXISTS idx_corp_assets_owner_corporation
    ON corp_assets (owner_corporation_id);
CREATE INDEX IF NOT EXISTS idx_character_industry_jobs_owner_character
    ON character_industry_jobs (owner_character_id);
CREATE INDEX IF NOT EXISTS idx_corp_industry_jobs_owner_corporation
    ON corp_industry_jobs (owner_corporation_id);
CREATE INDEX IF NOT EXISTS idx_character_blueprints_owner_character
    ON character_blueprints (owner_character_id);
CREATE INDEX IF NOT EXISTS idx_corp_blueprints_owner_corporation
    ON corp_blueprints (owner_corporation_id);
CREATE INDEX IF NOT EXISTS idx_character_sell_orders_owner_character
    ON character_sell_orders (owner_character_id);
CREATE INDEX IF NOT EXISTS idx_character_slots_owner_character
    ON character_slots (owner_character_id);

-- Doctrine / Sorting tables may not exist yet if this file is applied
-- before those schemas. Deploy order has esi_access_schema.sql last, so
-- these ALTERs run today; a later reordering of the apply loop produces
-- a silent no-op here, not an error. Re-running this file after them
-- (the deploy loop always re-runs every file) adds the columns. CREATE
-- TABLE IF NOT EXISTS in those files will not add columns to an
-- already-created table.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'doctrine_character_assets'
    ) THEN
        ALTER TABLE doctrine_character_assets ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
        ALTER TABLE doctrine_character_assets ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;
        CREATE INDEX IF NOT EXISTS idx_doctrine_character_assets_owner_character
            ON doctrine_character_assets (owner_character_id);
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'doctrine_corp_assets'
    ) THEN
        ALTER TABLE doctrine_corp_assets ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
        ALTER TABLE doctrine_corp_assets ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;
        CREATE INDEX IF NOT EXISTS idx_doctrine_corp_assets_owner_corporation
            ON doctrine_corp_assets (owner_corporation_id);
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'doctrine_contracts'
    ) THEN
        ALTER TABLE doctrine_contracts ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
        ALTER TABLE doctrine_contracts ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'sorting_intake_sources'
    ) THEN
        ALTER TABLE sorting_intake_sources ADD COLUMN IF NOT EXISTS owner_character_id BIGINT;
        ALTER TABLE sorting_intake_sources ADD COLUMN IF NOT EXISTS owner_corporation_id BIGINT;
    END IF;
END
$$;
