-- Self-hosted error tracking (GitHub issue #88) - frontend errors (a render
-- crash caught by ErrorBoundary, an uncaught exception, an unhandled
-- promise rejection) previously only ever reached the browser's own
-- console.error, with nothing durable recording that a real user actually
-- hit a failure. Additive only, idempotent - safe to re-run.
--
-- Usage (same as admin_schema.sql/doctrine_schema.sql - owner role only,
-- never the app's own eve_trader_app role):
--   local dev:  Get-Content docs\observability_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/observability_schema.sql

-- error_log: deliberately NOT RLS-scoped, same reasoning as tool_grants/
-- tenants (docs/admin_schema.sql's own comment) - this is inherently a
-- cross-tenant operator concern (the Admin tool's "Recent Errors" section
-- reads across every tenant), not per-tenant data. tenant_id is a plain
-- informational column (which tenant's request this happened during), not
-- an RLS-scoping one, and stays nullable - a truly tenant-independent
-- failure (or one outside any resolvable request context) still needs to
-- be recordable.
CREATE TABLE IF NOT EXISTS error_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    detail TEXT,
    path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS error_log_created_at_idx ON error_log (created_at DESC);

-- DELETE is needed by storage.log_error's own retention pruning (caps
-- error_log at MAX_ERROR_LOG_ROWS after every insert) - SELECT/INSERT
-- alone were sufficient before that existed.
GRANT SELECT, INSERT, DELETE ON error_log TO eve_trader_app;
GRANT USAGE, SELECT ON SEQUENCE error_log_id_seq TO eve_trader_app;
