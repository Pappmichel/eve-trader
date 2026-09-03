-- Special Orders (Production tool): one-off build orders, tracked separately
-- from the permanent stock_targets list - see CLAUDE.md/git history for the
-- feature discussion. Additive only - never edits phase1/phase2/phase3_
-- schema.sql (see CLAUDE.md's "Adding a new per-tenant table" section for
-- the conventions this file follows). Idempotent - every statement is safe
-- to re-run.
--
-- Usage (same as phase1_schema.sql - owner role only, never the app's own
-- eve_trader_app role):
--   local dev:  Get-Content docs\special_orders_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/special_orders_schema.sql

-- ============================================================ per-tenant: special orders
-- Composite-PK bucket (tenant_id, <app-generated UUID>) - same shape as
-- docs/doctrine_schema.sql's doctrines/doctrine_fittings header/child pair.

CREATE TABLE IF NOT EXISTS special_orders (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    order_id UUID NOT NULL DEFAULT gen_random_uuid(),
    note TEXT,
    net_against_stock BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, order_id)
);
ALTER TABLE special_orders ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON special_orders;
CREATE POLICY tenant_isolation ON special_orders
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

-- Natural key (tenant_id, order_id, type_id) rather than its own generated
-- id - a type_id appears at most once per order (quantity is edited via
-- upsert), and this shape lets item writes use the same plain
-- ON CONFLICT DO UPDATE every other composite-PK-keyed table in this app
-- already uses (see tests/test_pg_composite_pk_tables.py).
CREATE TABLE IF NOT EXISTS special_order_items (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    order_id UUID NOT NULL,
    type_id INTEGER NOT NULL,
    type_name TEXT NOT NULL,
    quantity REAL NOT NULL,
    PRIMARY KEY (tenant_id, order_id, type_id)
);
CREATE INDEX IF NOT EXISTS idx_special_order_items_order ON special_order_items (tenant_id, order_id);
ALTER TABLE special_order_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON special_order_items;
CREATE POLICY tenant_isolation ON special_order_items
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON special_orders, special_order_items TO eve_trader_app;
