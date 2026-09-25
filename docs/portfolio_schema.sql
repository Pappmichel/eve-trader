-- Portfolio rework (docs/PORTFOLIO_REWORK_PLAN.md): daily history for the
-- existing overview figures. One row per tenant per day - a same-day second
-- snapshot (scheduler tick + lazy page-load fallback both firing) upserts
-- the same row rather than creating a duplicate. No retention pruning
-- (decision: unbounded) - one row/day is a handful of doubles, immaterial
-- even after years.
--
-- total_wealth/wealth_assets_value/wealth_wallet_balance stay NULL until any
-- owner shares assets/blueprints/wallet_balance with the "portfolio" tool_key
-- (see section 4 of the plan) - combined_value (Trading + Production, this
-- app's original figure) and total_wealth (the new, broader, opt-in figure)
-- are deliberately two separate columns, never conflated.
--
-- Idempotent - safe to re-run.
--   local dev:  Get-Content docs\portfolio_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/portfolio_schema.sql

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    snapshot_date DATE NOT NULL,
    trading_realized_profit DOUBLE PRECISION NOT NULL,
    trading_average_margin DOUBLE PRECISION NOT NULL,
    trading_daily_profit_volatility DOUBLE PRECISION,
    trading_trade_count INTEGER NOT NULL,
    production_stock_value DOUBLE PRECISION NOT NULL,
    production_stock_targets_configured BOOLEAN NOT NULL,
    combined_value DOUBLE PRECISION NOT NULL,
    total_wealth DOUBLE PRECISION,
    wealth_assets_value DOUBLE PRECISION,
    wealth_wallet_balance DOUBLE PRECISION,
    taken_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, snapshot_date)
);
ALTER TABLE portfolio_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON portfolio_snapshots;
CREATE POLICY tenant_isolation ON portfolio_snapshots
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio_snapshots TO eve_trader_app;

-- Manual item prices (plan section 7) - used only to value items
-- Goonmetrics has no quote for in Total Wealth. One price per type_id,
-- tenant-wide, no quantity/location tiers - scoped to Portfolio's own
-- wealth calculation only, not a shared pricing fallback for Production/
-- Trading (same "an explicit per-item entry always wins" precedent as
-- manual_blueprint_copy_costs/manual_blueprint_me_te_overrides in
-- phase1_schema.sql, but deliberately its own table since it's a
-- different tool's override, not a blueprint-cost one).
CREATE TABLE IF NOT EXISTS manual_item_prices (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    type_name TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL CHECK (price >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, type_id)
);
ALTER TABLE manual_item_prices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON manual_item_prices;
CREATE POLICY tenant_isolation ON manual_item_prices
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON manual_item_prices TO eve_trader_app;
