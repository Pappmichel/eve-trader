-- Manual-trigger Trading pipeline runs (Search + Add + Clean Up, Refresh
-- Shortlist, Run Complete Pipeline). Status is persisted per tenant so a
-- server restart / second worker process still sees an in-flight job -
-- unlike scheduler.py's in-memory last_run_status, which is fine for a
-- "last tick" readout but would lose a minutes-long cleanup that the UI
-- is polling. The three jobs share one running-row lock: they all mutate
-- the shortlist / snapshot.
--
-- Named (not phase4_schema.sql): after phase3 the repo switched to
-- feature-named schema files (admin/doctrine/refining/...). Numbering
-- those as phase4 would collide with MULTI_TENANT_PLAN.md's own Phase 4.
--
-- Polling, not LISTEN/NOTIFY: this app has no existing NOTIFY usage, the
-- frontend already polls via react-query, and a 4s lag on a minutes-long
-- job is the right complexity tradeoff. Status writes go through
-- storage.connect() like every other per-tenant table.
--
-- Additive only, idempotent - safe to re-run.
--
-- Usage (owner role only, never eve_trader_app):
--   local dev:  Get-Content docs\pipeline_runs_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/pipeline_runs_schema.sql

-- UUID PK is globally unique (generated in Python), so this is column-only
-- bucket: tenant_id is an RLS column, not part of the PK. See
-- phase1_schema.sql's composite-vs-column-only comment banners.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    progress JSONB,
    result JSONB,
    error TEXT
);
ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON pipeline_runs;
CREATE POLICY tenant_isolation ON pipeline_runs
    USING (tenant_id = current_setting('app.tenant_id', false)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', false)::uuid);

CREATE INDEX IF NOT EXISTS pipeline_runs_tenant_started_idx
    ON pipeline_runs (tenant_id, started_at DESC);

-- At most one running job per tenant+job_name. Weaker than the per-tenant
-- lock below; kept so databases that already applied this file don't need
-- a DROP, and so a same-job_name race is still named clearly in Postgres.
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_runs_one_running
    ON pipeline_runs (tenant_id, job_name)
    WHERE status = 'running';

-- At most one running Trading job per tenant, across job_names. Refresh
-- Shortlist / Search+Add+Clean Up / Run Complete Pipeline all mutate the
-- shortlist; overlapping them would race. The application checks first for
-- a friendly ConflictError; this unique index is the race-condition
-- backstop if two POSTs land on different workers in the same instant.
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_runs_one_running_per_tenant
    ON pipeline_runs (tenant_id)
    WHERE status = 'running';

GRANT SELECT, INSERT, UPDATE, DELETE ON pipeline_runs TO eve_trader_app;

-- Cleanup rotation cursor on shortlist (Phase 3 of the Search+Add+Clean Up
-- scale work). NULL = never refreshed, so newly added items are priced first.
-- Also added to phase1_schema.sql's CREATE TABLE for fresh installs; this
-- ALTER covers databases that already applied phase1 before the column existed.
ALTER TABLE shortlist ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMPTZ;
