-- Persistent session revocation (P5-03). Additive only, idempotent.
--
-- sessions_valid_after on tenant_registry_entries is lost when the registry
-- row is DELETE'd. Re-adding the same EVE character then resurrected any
-- still-unexpired cookie. This table is keyed by the stable EVE
-- character_id (CCP does not reuse character IDs) and is deliberately NOT
-- RLS-scoped: revocation must survive registry deletion, tenant
-- reassignment, and admin bootstrap, which all go through
-- storage.connect_unscoped().
--
-- Cookie authorization still requires a matching tenant_registry_entries
-- row (e.tenant_id = cookie tenant). This table only supplies a
-- never-cleared lower bound on sessions_valid_after.
--
-- Usage (owner role only, never eve_trader_app):
--   local dev:  Get-Content docs\session_revocations_schema.sql | docker exec -i eve-trader-pg psql -U postgres -d eve_trader
--   live:       sudo -u postgres psql -d eve_trader -f docs/session_revocations_schema.sql

CREATE TABLE IF NOT EXISTS character_session_revocations (
    character_id BIGINT PRIMARY KEY,
    sessions_valid_after TIMESTAMPTZ NOT NULL
);

GRANT SELECT, INSERT, UPDATE, DELETE ON character_session_revocations TO eve_trader_app;
