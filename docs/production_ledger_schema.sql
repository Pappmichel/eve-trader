-- Production realized-profit ledger (docs/PRODUCTION_REALIZED_PROFIT_PLAN.md
-- Phase 1). Append-only ESI archive plus rebuildable derived tables.
-- Additive only - never edits phase1/phase2/phase3_schema.sql.
-- Idempotent - every statement is safe to re-run.
--
-- Also widens esi_wallet_journal with context_id / party / description /
-- tax columns (plan section 8) so Trading's deferred real-tax linkage can
-- use the snapshot too.
--
-- Usage (owner role only, never eve_trader_app):
--   local dev:  Get-Content docs\production_ledger_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/production_ledger_schema.sql

-- ====================================================== snapshot: journal context
ALTER TABLE esi_wallet_journal ADD COLUMN IF NOT EXISTS context_id BIGINT;
ALTER TABLE esi_wallet_journal ADD COLUMN IF NOT EXISTS context_id_type TEXT;
ALTER TABLE esi_wallet_journal ADD COLUMN IF NOT EXISTS first_party_id BIGINT;
ALTER TABLE esi_wallet_journal ADD COLUMN IF NOT EXISTS second_party_id BIGINT;
ALTER TABLE esi_wallet_journal ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE esi_wallet_journal ADD COLUMN IF NOT EXISTS tax DOUBLE PRECISION;
ALTER TABLE esi_wallet_journal ADD COLUMN IF NOT EXISTS tax_receiver_id BIGINT;

-- ====================================================== archive: wallet transactions
CREATE TABLE IF NOT EXISTS ledger_wallet_transactions (
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
    region_id INTEGER,
    freight_rate_per_m3 DOUBLE PRECISION,
    freight_m3 DOUBLE PRECISION,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_type, owner_id, division, transaction_id),
    CONSTRAINT ledger_wallet_transactions_division_check CHECK (
        (owner_type = 'character' AND division = 0)
        OR (owner_type = 'corporation' AND division BETWEEN 1 AND 7)
    )
);
ALTER TABLE ledger_wallet_transactions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_wallet_transactions;
CREATE POLICY tenant_isolation ON ledger_wallet_transactions
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
CREATE INDEX IF NOT EXISTS idx_ledger_wallet_tx_owner_date
    ON ledger_wallet_transactions (owner_type, owner_id, date);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_wallet_transactions TO eve_trader_app;

-- ====================================================== archive: wallet journal
CREATE TABLE IF NOT EXISTS ledger_wallet_journal (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    division INTEGER NOT NULL DEFAULT 0,
    journal_id BIGINT NOT NULL,
    date TIMESTAMPTZ NOT NULL,
    ref_type TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    context_id BIGINT,
    context_id_type TEXT,
    first_party_id BIGINT,
    second_party_id BIGINT,
    description TEXT,
    tax DOUBLE PRECISION,
    tax_receiver_id BIGINT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_type, owner_id, division, journal_id),
    CONSTRAINT ledger_wallet_journal_division_check CHECK (
        (owner_type = 'character' AND division = 0)
        OR (owner_type = 'corporation' AND division BETWEEN 1 AND 7)
    )
);
ALTER TABLE ledger_wallet_journal ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_wallet_journal;
CREATE POLICY tenant_isolation ON ledger_wallet_journal
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
CREATE INDEX IF NOT EXISTS idx_ledger_wallet_journal_owner_date
    ON ledger_wallet_journal (owner_type, owner_id, date);
CREATE INDEX IF NOT EXISTS idx_ledger_wallet_journal_context
    ON ledger_wallet_journal (context_id_type, context_id);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_wallet_journal TO eve_trader_app;

-- ====================================================== archive: market order history
CREATE TABLE IF NOT EXISTS ledger_market_orders (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    order_id BIGINT NOT NULL,
    type_id INTEGER NOT NULL,
    location_id BIGINT NOT NULL,
    region_id INTEGER,
    is_buy_order BOOLEAN NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    volume_total INTEGER NOT NULL,
    volume_remain INTEGER,
    issued TIMESTAMPTZ NOT NULL,
    state TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_type, owner_id, order_id)
);
ALTER TABLE ledger_market_orders ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_market_orders;
CREATE POLICY tenant_isolation ON ledger_market_orders
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
CREATE INDEX IF NOT EXISTS idx_ledger_market_orders_owner_issued
    ON ledger_market_orders (owner_type, owner_id, issued);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_market_orders TO eve_trader_app;

-- ====================================================== archive: industry jobs
CREATE TABLE IF NOT EXISTS ledger_industry_jobs (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    job_id BIGINT NOT NULL,
    activity_id INTEGER NOT NULL,
    blueprint_id BIGINT,
    blueprint_type_id INTEGER NOT NULL,
    product_type_id INTEGER,
    runs INTEGER NOT NULL,
    licensed_runs INTEGER,
    successful_runs INTEGER,
    cost DOUBLE PRECISION,
    facility_id BIGINT,
    station_id BIGINT,
    output_location_id BIGINT,
    installer_id BIGINT,
    start_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    completed_date TIMESTAMPTZ,
    status TEXT,
    probability DOUBLE PRECISION,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_type, owner_id, job_id)
);
ALTER TABLE ledger_industry_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_industry_jobs;
CREATE POLICY tenant_isolation ON ledger_industry_jobs
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
CREATE INDEX IF NOT EXISTS idx_ledger_industry_jobs_owner_start
    ON ledger_industry_jobs (owner_type, owner_id, start_date);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_industry_jobs TO eve_trader_app;

-- ====================================================== archive: frozen job inputs
-- Written once when a job is first archived; never recomputed on resync.
CREATE TABLE IF NOT EXISTS ledger_job_inputs (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    job_id BIGINT NOT NULL,
    type_id INTEGER NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    blueprint_me INTEGER,
    material_multiplier DOUBLE PRECISION,
    decryptor_type_id INTEGER,
    encryption_skill_level INTEGER,
    science_skill_1_level INTEGER,
    science_skill_2_level INTEGER,
    source TEXT NOT NULL,
    PRIMARY KEY (tenant_id, owner_type, owner_id, job_id, type_id)
);
ALTER TABLE ledger_job_inputs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_job_inputs;
CREATE POLICY tenant_isolation ON ledger_job_inputs
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_job_inputs TO eve_trader_app;

-- ====================================================== archive: last-seen blueprints
CREATE TABLE IF NOT EXISTS ledger_blueprints (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    item_id BIGINT NOT NULL,
    type_id INTEGER NOT NULL,
    location_id BIGINT,
    material_efficiency INTEGER NOT NULL,
    time_efficiency INTEGER NOT NULL,
    runs INTEGER NOT NULL,
    quantity INTEGER,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_type, owner_id, item_id)
);
ALTER TABLE ledger_blueprints ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_blueprints;
CREATE POLICY tenant_isolation ON ledger_blueprints
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_blueprints TO eve_trader_app;

-- ====================================================== archive: contracts
CREATE TABLE IF NOT EXISTS ledger_contracts (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    contract_id BIGINT NOT NULL,
    issuer_id BIGINT,
    issuer_corporation_id BIGINT,
    acceptor_id BIGINT,
    start_location_id BIGINT,
    end_location_id BIGINT,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT,
    price DOUBLE PRECISION,
    reward DOUBLE PRECISION,
    date_issued TIMESTAMPTZ,
    date_completed TIMESTAMPTZ,
    date_expired TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_type, owner_id, contract_id)
);
ALTER TABLE ledger_contracts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_contracts;
CREATE POLICY tenant_isolation ON ledger_contracts
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
CREATE INDEX IF NOT EXISTS idx_ledger_contracts_owner_completed
    ON ledger_contracts (owner_type, owner_id, date_completed);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_contracts TO eve_trader_app;

CREATE TABLE IF NOT EXISTS ledger_contract_items (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    contract_id BIGINT NOT NULL,
    record_id BIGINT NOT NULL,
    type_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    is_included BOOLEAN NOT NULL DEFAULT TRUE,
    is_singleton BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (tenant_id, owner_type, owner_id, contract_id, record_id)
);
ALTER TABLE ledger_contract_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_contract_items;
CREATE POLICY tenant_isolation ON ledger_contract_items
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_contract_items TO eve_trader_app;

-- ====================================================== archive: reference prices (Phase 2+)
CREATE TABLE IF NOT EXISTS ledger_reference_prices (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    price_date DATE NOT NULL,
    avg_price DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, type_id, price_date)
);
ALTER TABLE ledger_reference_prices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_reference_prices;
CREATE POLICY tenant_isolation ON ledger_reference_prices
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_reference_prices TO eve_trader_app;

-- ====================================================== archive: manual entries (Phase 3+)
CREATE TABLE IF NOT EXISTS ledger_manual_entries (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    entry_id BIGSERIAL,
    entry_kind TEXT NOT NULL,
    type_id INTEGER,
    quantity DOUBLE PRECISION,
    amount DOUBLE PRECISION NOT NULL,
    entry_date TIMESTAMPTZ NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, entry_id)
);
ALTER TABLE ledger_manual_entries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_manual_entries;
CREATE POLICY tenant_isolation ON ledger_manual_entries
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_manual_entries TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE ledger_manual_entries_entry_id_seq TO eve_trader_app;

-- ====================================================== archive gaps (Phase 0 hard caps)
CREATE TABLE IF NOT EXISTS ledger_archive_gaps (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    data_kind TEXT NOT NULL,
    gap_start TIMESTAMPTZ NOT NULL,
    gap_end TIMESTAMPTZ NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_type, owner_id, data_kind, gap_start, gap_end)
);
ALTER TABLE ledger_archive_gaps ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_archive_gaps;
CREATE POLICY tenant_isolation ON ledger_archive_gaps
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_archive_gaps TO eve_trader_app;

-- ====================================================== derived (rebuildable; Phase 2+)
CREATE TABLE IF NOT EXISTS prod_cost_layers (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    layer_id BIGSERIAL,
    type_id INTEGER NOT NULL,
    unit TEXT NOT NULL CHECK (unit IN ('item', 'bpc_run')),
    quantity_left DOUBLE PRECISION NOT NULL,
    unit_cost DOUBLE PRECISION NOT NULL,
    origin TEXT NOT NULL,
    source_ref TEXT,
    cost_materials_bought DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_materials_estimated DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_freight DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_job DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_invention DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_fees DOUBLE PRECISION NOT NULL DEFAULT 0,
    layer_date TIMESTAMPTZ,
    job_id BIGINT,
    PRIMARY KEY (tenant_id, layer_id)
);
ALTER TABLE prod_cost_layers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_cost_layers;
CREATE POLICY tenant_isolation ON prod_cost_layers
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_cost_layers TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE prod_cost_layers_layer_id_seq TO eve_trader_app;

CREATE TABLE IF NOT EXISTS prod_realized_results (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    result_id BIGSERIAL,
    sale_ref TEXT NOT NULL,
    type_id INTEGER NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    revenue DOUBLE PRECISION NOT NULL,
    cost DOUBLE PRECISION NOT NULL,
    profit DOUBLE PRECISION NOT NULL,
    coverage_share DOUBLE PRECISION NOT NULL,
    sale_date TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, result_id)
);
ALTER TABLE prod_realized_results ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_realized_results;
CREATE POLICY tenant_isolation ON prod_realized_results
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_realized_results TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE prod_realized_results_result_id_seq TO eve_trader_app;

CREATE TABLE IF NOT EXISTS prod_ledger_runs (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    run_id BIGSERIAL,
    ran_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cutover_t0 TIMESTAMPTZ,
    warnings JSONB,
    PRIMARY KEY (tenant_id, run_id)
);
ALTER TABLE prod_ledger_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_ledger_runs;
CREATE POLICY tenant_isolation ON prod_ledger_runs
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_ledger_runs TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE prod_ledger_runs_run_id_seq TO eve_trader_app;
