-- Planetary Industry (PI) tool - docs/PI_PLAN.md, docs/PI_TECHNICAL_DESIGN.md.
-- Additive only, idempotent: every statement is safe to re-run.
--
-- Usage (owner role only, never the app's own eve_trader_app role):
--   local dev:  Get-Content docs\pi_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/pi_schema.sql
--
-- Must run after character_management_schema.sql (it widens the alert_type
-- CHECK constraints created there).

-- ================================================ global: SDE (no tenant_id)
-- Filled by the SDE refresh (production/sde.py), empty until the next Admin
-- SDE preview/apply. Same pattern as sde_skill_requirements.

CREATE TABLE IF NOT EXISTS sde_pi_schematics (
    schematic_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    cycle_seconds INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sde_pi_schematic_types (
    schematic_id INTEGER NOT NULL,
    type_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    is_input BOOLEAN NOT NULL,
    PRIMARY KEY (schematic_id, type_id)
);

CREATE TABLE IF NOT EXISTS sde_pi_schematic_pins (
    schematic_id INTEGER NOT NULL,
    pin_type_id INTEGER NOT NULL,
    PRIMARY KEY (schematic_id, pin_type_id)
);

-- Dogma attributes of PI types (structures, links, commodities), plus the
-- invTypes basePrice stored as pseudo attribute -1 (sde_types has no
-- base price column). DOUBLE PRECISION on purpose: REAL (float4) turns
-- 0.19 into 0.1899999976.
CREATE TABLE IF NOT EXISTS sde_pi_type_attributes (
    type_id INTEGER NOT NULL,
    attribute_id INTEGER NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (type_id, attribute_id)
);

-- Every planet of the 8 PI planet types (~67.7k rows), from mapDenormalize.
CREATE TABLE IF NOT EXISTS sde_pi_planets (
    planet_id INTEGER PRIMARY KEY,
    planet_name TEXT,
    solar_system_id INTEGER NOT NULL,
    type_id INTEGER NOT NULL,
    radius_km DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS sde_pi_planets_system ON sde_pi_planets (solar_system_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON sde_pi_schematics TO eve_trader_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON sde_pi_schematic_types TO eve_trader_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON sde_pi_schematic_pins TO eve_trader_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON sde_pi_type_attributes TO eve_trader_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON sde_pi_planets TO eve_trader_app;

-- ===================================================== per-tenant tables

-- Saved plans (D6): a design or chain for a real planet or a free planet type.
CREATE TABLE IF NOT EXISTS pi_plans (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    plan_id BIGSERIAL,
    name TEXT NOT NULL,
    planet_id INTEGER,
    planet_type_id INTEGER NOT NULL,
    radius_km DOUBLE PRECISION NOT NULL,
    character_id BIGINT,
    design JSONB NOT NULL,
    owner_tax_rate DOUBLE PRECISION,
    freight_per_m3 DOUBLE PRECISION,
    yield_override DOUBLE PRECISION,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, plan_id)
);
ALTER TABLE pi_plans ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON pi_plans;
CREATE POLICY tenant_isolation ON pi_plans
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON pi_plans TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE pi_plans_plan_id_seq TO eve_trader_app;

-- Template library. `template` is the EVE template JSON; export
-- re-serializes it in EVE's key order (pi/layout/template_io.py).
CREATE TABLE IF NOT EXISTS pi_templates (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    template_id BIGSERIAL,
    name TEXT NOT NULL,
    comment TEXT,
    planet_type_id INTEGER,
    cc_level INTEGER,
    diameter_km DOUBLE PRECISION,
    template JSONB NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('paste', 'generated', 'esi')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, template_id)
);
ALTER TABLE pi_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON pi_templates;
CREATE POLICY tenant_isolation ON pi_templates
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON pi_templates TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE pi_templates_template_id_seq TO eve_trader_app;

-- Extraction calibration samples from real extractors (ESI `planets` kind).
CREATE TABLE IF NOT EXISTS pi_yield_samples (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_id BIGINT NOT NULL,
    planet_id INTEGER NOT NULL,
    pin_id BIGINT NOT NULL,
    install_time TIMESTAMPTZ NOT NULL,
    p0_type_id INTEGER NOT NULL,
    planet_type_id INTEGER NOT NULL,
    security DOUBLE PRECISION,
    heads INTEGER NOT NULL,
    program_hours DOUBLE PRECISION NOT NULL,
    per_head_per_hour DOUBLE PRECISION NOT NULL,
    sampled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, character_id, pin_id, install_time)
);
ALTER TABLE pi_yield_samples ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON pi_yield_samples;
CREATE POLICY tenant_isolation ON pi_yield_samples
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON pi_yield_samples TO eve_trader_app;

-- ESI `planets` snapshot: one row per colony, ESI pins/links/routes kept
-- whole as JSONB (the engine reads it whole, nothing queries inside it).
CREATE TABLE IF NOT EXISTS character_pi_colonies (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_character_id BIGINT NOT NULL,
    planet_id INTEGER NOT NULL,
    planet_type TEXT,
    solar_system_id INTEGER,
    upgrade_level INTEGER,
    num_pins INTEGER,
    last_update TIMESTAMPTZ,
    layout JSONB NOT NULL,
    PRIMARY KEY (tenant_id, owner_character_id, planet_id)
);
ALTER TABLE character_pi_colonies ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON character_pi_colonies;
CREATE POLICY tenant_isolation ON character_pi_colonies
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON character_pi_colonies TO eve_trader_app;

-- PI alert dedupe state, per colony (alert_state is per character x type).
CREATE TABLE IF NOT EXISTS pi_alert_state (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    character_id BIGINT NOT NULL,
    planet_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    last_key TEXT,
    last_sent_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, character_id, planet_id, alert_type)
);
ALTER TABLE pi_alert_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON pi_alert_state;
CREATE POLICY tenant_isolation ON pi_alert_state
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON pi_alert_state TO eve_trader_app;

-- ============================ alert types: widen the CHECK constraints
-- created inline in character_management_schema.sql. CREATE TABLE IF NOT
-- EXISTS never changes an existing table, so the new PI alert types need an
-- explicit DROP/ADD here.
ALTER TABLE alert_subscriptions DROP CONSTRAINT IF EXISTS alert_subscriptions_alert_type_check;
ALTER TABLE alert_subscriptions ADD CONSTRAINT alert_subscriptions_alert_type_check
    CHECK (alert_type IN ('skillqueue_empty', 'mail_new',
                          'pi_extractor_expiry', 'pi_pad_full', 'pi_inputs_empty'));
ALTER TABLE alert_state DROP CONSTRAINT IF EXISTS alert_state_alert_type_check;
ALTER TABLE alert_state ADD CONSTRAINT alert_state_alert_type_check
    CHECK (alert_type IN ('skillqueue_empty', 'mail_new',
                          'pi_extractor_expiry', 'pi_pad_full', 'pi_inputs_empty'));

-- ======================================= tenant_settings scope 'pi'
-- PiConfig Settings saves use tenant_settings scope 'pi'. Restates the FULL
-- scope list like every other schema file's copy of this ALTER (see
-- docs/module_reprocessing_schema.sql's comment): deploy.sh re-runs every
-- file, so all copies must carry the same complete list.
ALTER TABLE tenant_settings DROP CONSTRAINT IF EXISTS tenant_settings_scope_check;
ALTER TABLE tenant_settings ADD CONSTRAINT tenant_settings_scope_check
    CHECK (scope IN ('trading', 'production', 'doctrine', 'refining', 'station_trading', 'module_reprocessing', 'pi'));
