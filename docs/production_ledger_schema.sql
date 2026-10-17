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
    -- NULL: frozen before this column existed (unknown, never guessed).
    -- TRUE/FALSE: whether the structure/rig bonus was applied when inputs
    -- were frozen (plan section 5). Not recomputed later; config can change.
    structure_bonus_applied BOOLEAN,
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

-- ====================================================== archive sync state
-- Last time each (owner, kind, wallet division) was archived. Gap detection
-- for windowed ESI lists (wallet 30 days, order history / jobs 90 days)
-- compares against this, not against the newest archived row, so a quiet
-- period with no new rows is never reported as lost history.
CREATE TABLE IF NOT EXISTS ledger_sync_state (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    data_kind TEXT NOT NULL,
    division INTEGER NOT NULL DEFAULT 0,
    last_synced_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, owner_type, owner_id, data_kind, division)
);
ALTER TABLE ledger_sync_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_sync_state;
CREATE POLICY tenant_isolation ON ledger_sync_state
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_sync_state TO eve_trader_app;

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

-- ====================================================== Phase 3: report columns
-- Flags and cost components the API must not drop. Nullable additions so a
-- re-run of this file against an already-loaded database is a no-op.
ALTER TABLE ledger_job_inputs ADD COLUMN IF NOT EXISTS structure_bonus_applied BOOLEAN;

ALTER TABLE prod_cost_layers ADD COLUMN IF NOT EXISTS quantity_original DOUBLE PRECISION;
ALTER TABLE prod_cost_layers ADD COLUMN IF NOT EXISTS assumed_me BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE prod_cost_layers ADD COLUMN IF NOT EXISTS assumed_decryptor BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE prod_cost_layers ADD COLUMN IF NOT EXISTS estimated_origin BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE prod_cost_layers ADD COLUMN IF NOT EXISTS price_missing BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE prod_cost_layers ADD COLUMN IF NOT EXISTS logical_layer_id INTEGER;

ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS gross DOUBLE PRECISION;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS sales_tax DOUBLE PRECISION;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS broker_fee DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS location_id BIGINT;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS price_missing BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS estimated BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS warnings JSONB;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS cost_materials_bought DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS cost_materials_estimated DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS cost_freight DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS cost_job DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS cost_invention DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS cost_fees DOUBLE PRECISION NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_prod_realized_results_sale_date
    ON prod_realized_results (sale_date);
CREATE INDEX IF NOT EXISTS idx_prod_realized_results_type
    ON prod_realized_results (type_id);

-- Draw edges for the per-sale cost chain. logical_layer_id is the id the
-- costing engine assigned in this rebuild (not the BIGSERIAL layer_id).
CREATE TABLE IF NOT EXISTS prod_cost_draws (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    draw_id BIGSERIAL,
    consumer_kind TEXT NOT NULL CHECK (consumer_kind IN ('sale', 'job')),
    consumer_ref TEXT NOT NULL,
    seq INTEGER NOT NULL,
    logical_layer_id INTEGER NOT NULL,
    type_id INTEGER NOT NULL,
    unit TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    origin TEXT NOT NULL,
    source_ref TEXT,
    cost_materials_bought DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_materials_estimated DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_freight DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_job DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_invention DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_fees DOUBLE PRECISION NOT NULL DEFAULT 0,
    unit_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    estimated_origin BOOLEAN NOT NULL DEFAULT FALSE,
    price_missing BOOLEAN NOT NULL DEFAULT FALSE,
    assumed_me BOOLEAN NOT NULL DEFAULT FALSE,
    assumed_decryptor BOOLEAN NOT NULL DEFAULT FALSE,
    layer_date TIMESTAMPTZ,
    job_id BIGINT,
    location_id BIGINT,
    unit_price DOUBLE PRECISION,
    freight_rate_per_m3 DOUBLE PRECISION,
    PRIMARY KEY (tenant_id, draw_id)
);
ALTER TABLE prod_cost_draws ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_cost_draws;
CREATE POLICY tenant_isolation ON prod_cost_draws
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
CREATE INDEX IF NOT EXISTS idx_prod_cost_draws_consumer
    ON prod_cost_draws (consumer_kind, consumer_ref);
CREATE INDEX IF NOT EXISTS idx_prod_cost_draws_layer
    ON prod_cost_draws (logical_layer_id);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_cost_draws TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE prod_cost_draws_draw_id_seq TO eve_trader_app;

CREATE TABLE IF NOT EXISTS prod_job_records (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    job_ref TEXT NOT NULL,
    job_id BIGINT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id BIGINT NOT NULL,
    activity_id INTEGER NOT NULL,
    blueprint_type_id INTEGER NOT NULL,
    product_type_id INTEGER,
    runs INTEGER NOT NULL,
    successful_runs INTEGER,
    start_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    status TEXT,
    state TEXT NOT NULL,
    job_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_materials_bought DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_materials_estimated DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_freight DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_job DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_invention DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_fees DOUBLE PRECISION NOT NULL DEFAULT 0,
    assumed_me BOOLEAN NOT NULL DEFAULT FALSE,
    assumed_decryptor BOOLEAN NOT NULL DEFAULT FALSE,
    price_missing BOOLEAN NOT NULL DEFAULT FALSE,
    no_structure_bonus BOOLEAN,
    output_layer_id INTEGER,
    output_type_id INTEGER,
    output_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    output_quantity_left DOUBLE PRECISION,
    output_unit TEXT,
    facility_id BIGINT,
    PRIMARY KEY (tenant_id, job_ref)
);
ALTER TABLE prod_job_records ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_job_records;
CREATE POLICY tenant_isolation ON prod_job_records
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
CREATE INDEX IF NOT EXISTS idx_prod_job_records_output_layer
    ON prod_job_records (output_layer_id);
CREATE INDEX IF NOT EXISTS idx_prod_job_records_start
    ON prod_job_records (start_date);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_job_records TO eve_trader_app;

CREATE TABLE IF NOT EXISTS prod_fee_lines (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    fee_id BIGSERIAL,
    kind TEXT NOT NULL CHECK (kind IN ('without_sale', 'other_trading')),
    owner_type TEXT NOT NULL,
    owner_id BIGINT NOT NULL,
    fee_date DATE NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    sale_ref TEXT,
    PRIMARY KEY (tenant_id, fee_id)
);
ALTER TABLE prod_fee_lines ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_fee_lines;
CREATE POLICY tenant_isolation ON prod_fee_lines
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
CREATE INDEX IF NOT EXISTS idx_prod_fee_lines_date ON prod_fee_lines (fee_date);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_fee_lines TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE prod_fee_lines_fee_id_seq TO eve_trader_app;

CREATE TABLE IF NOT EXISTS prod_job_losses (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    loss_id BIGSERIAL,
    loss_kind TEXT NOT NULL CHECK (loss_kind IN ('cancelled', 'failed_invention')),
    job_ref TEXT NOT NULL,
    job_id BIGINT NOT NULL,
    loss_date TIMESTAMPTZ NOT NULL,
    cost_materials_bought DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_materials_estimated DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_freight DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_job DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_invention DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_fees DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, loss_id)
);
ALTER TABLE prod_job_losses ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_job_losses;
CREATE POLICY tenant_isolation ON prod_job_losses
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
CREATE INDEX IF NOT EXISTS idx_prod_job_losses_date ON prod_job_losses (loss_date);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_job_losses TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE prod_job_losses_loss_id_seq TO eve_trader_app;

CREATE TABLE IF NOT EXISTS prod_capital_bpos (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE prod_capital_bpos ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_capital_bpos;
CREATE POLICY tenant_isolation ON prod_capital_bpos
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_capital_bpos TO eve_trader_app;

CREATE TABLE IF NOT EXISTS prod_inventory_drift (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    ledger_qty DOUBLE PRECISION NOT NULL,
    esi_qty DOUBLE PRECISION NOT NULL,
    drift DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE prod_inventory_drift ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_inventory_drift;
CREATE POLICY tenant_isolation ON prod_inventory_drift
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_inventory_drift TO eve_trader_app;

-- ====================================================== Phase 3 review fixes
-- Fingerprint of the owners shared with Production (per data kind) when the
-- run was built. The report hides a run whose owners no longer match (D11).
ALTER TABLE prod_ledger_runs ADD COLUMN IF NOT EXISTS owner_scope_hash TEXT;

-- Opening quantity per type, frozen the first time a rebuild sees the type
-- (D8). Not a derived table: a rebuild keeps it and only adds new types. It
-- is cleared when T0 or the shared-owner fingerprint changes, or on an
-- explicit reset. Freezing is what makes the inventory drift check real.
CREATE TABLE IF NOT EXISTS prod_opening_frozen (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    raw_quantity DOUBLE PRECISION NOT NULL,
    cutover_t0 TIMESTAMPTZ NOT NULL,
    owner_scope_hash TEXT NOT NULL,
    frozen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE prod_opening_frozen ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_opening_frozen;
CREATE POLICY tenant_isolation ON prod_opening_frozen
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_opening_frozen TO eve_trader_app;

-- Derived: the opening inventory as the latest rebuild valued it.
CREATE TABLE IF NOT EXISTS prod_opening_lines (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    raw_quantity DOUBLE PRECISION NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    unit_cost DOUBLE PRECISION NOT NULL,
    price_source TEXT NOT NULL,
    reference_price DOUBLE PRECISION,
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE prod_opening_lines ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON prod_opening_lines;
CREATE POLICY tenant_isolation ON prod_opening_lines
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON prod_opening_lines TO eve_trader_app;

-- ====================================================== Phase 5
-- One-time opening reset when a rebuild's costing version is older than
-- the code (NULL counts as older). Written on every successful rebuild.
ALTER TABLE prod_ledger_runs ADD COLUMN IF NOT EXISTS costing_version INTEGER;

-- Sale channel and the channel a draw's layer was bought on (market / contract).
ALTER TABLE prod_realized_results ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE prod_cost_draws ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE prod_cost_layers ADD COLUMN IF NOT EXISTS source TEXT;

-- Courier contracts (comparison only; never costed) plus the start-location
-- region used to recognise a Forge pickup.
ALTER TABLE ledger_contracts ADD COLUMN IF NOT EXISTS volume DOUBLE PRECISION;
ALTER TABLE ledger_contracts ADD COLUMN IF NOT EXISTS collateral DOUBLE PRECISION;
ALTER TABLE ledger_contracts ADD COLUMN IF NOT EXISTS region_id INTEGER;

-- Freight frozen on a contract purchase the first time it can be resolved.
-- A NULL region leaves freight_frozen false so a later rebuild can retry.
ALTER TABLE ledger_contract_items ADD COLUMN IF NOT EXISTS freight_rate_per_m3 DOUBLE PRECISION;
ALTER TABLE ledger_contract_items ADD COLUMN IF NOT EXISTS freight_m3 DOUBLE PRECISION;
ALTER TABLE ledger_contract_items ADD COLUMN IF NOT EXISTS freight_frozen BOOLEAN NOT NULL DEFAULT FALSE;

-- Planner cost/unit frozen the first time a job is archived. Not derived:
-- a rebuild does not delete it, and jobs archived before this table existed
-- stay without a row.
CREATE TABLE IF NOT EXISTS ledger_job_planned_costs (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('character', 'corporation')),
    owner_id BIGINT NOT NULL,
    job_id BIGINT NOT NULL,
    product_type_id INTEGER NOT NULL,
    planned_unit_cost DOUBLE PRECISION,
    source TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_type, owner_id, job_id)
);
ALTER TABLE ledger_job_planned_costs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_job_planned_costs;
CREATE POLICY tenant_isolation ON ledger_job_planned_costs
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_job_planned_costs TO eve_trader_app;

-- ====================================================== valuation prices
-- What the planner would have paid for one unit (cheaper of the buy-list
-- purchase price and its build cost), frozen per (type, day) the first time
-- a rebuild needs it (plan D5/D8). Not derived: there is no price history
-- for the home structure's market, so a dropped row could not be rebuilt.
-- source: buy_home / buy_hub / build, or unavailable (fall back to The
-- Forge daily average in ledger_reference_prices).
CREATE TABLE IF NOT EXISTS ledger_valuation_prices (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    price_date DATE NOT NULL,
    unit_value DOUBLE PRECISION,
    source TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, type_id, price_date)
);
ALTER TABLE ledger_valuation_prices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ledger_valuation_prices;
CREATE POLICY tenant_isolation ON ledger_valuation_prices
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger_valuation_prices TO eve_trader_app;
