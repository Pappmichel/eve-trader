#!/usr/bin/env bash
# Per-boot startup for the EVE Trader Cloud Agent dev environment.
#
# Brings up PostgreSQL and guarantees the databases + schema exist. Idempotent,
# so it is safe to run on every boot (including a fresh pod with no warm data
# directory). Dependency installation lives in install.sh, not here.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Keep idempotent schema re-application quiet (suppress "already exists" NOTICEs).
export PGOPTIONS='-c client_min_messages=warning'

# --- Start the PostgreSQL cluster (no-op if it is already running) ---
sudo pg_ctlcluster 16 main start 2>/dev/null || true
for _ in $(seq 1 30); do
  pg_isready -h localhost -p 5432 -q && break
  sleep 1
done

# --- Owner role password (matches tests/pg_helpers.py OWNER_DSN + .env.example) ---
sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
  "ALTER USER postgres WITH PASSWORD 'devpassword';" >/dev/null

# --- Databases -------------------------------------------------------------
#   eve_trader     -> test database. Left empty here; the pytest fixtures apply
#                     schema themselves, exactly like CI. This keeps `pytest`
#                     reproducing CI behaviour with zero extra configuration.
#   eve_trader_dev -> application database. Gets every schema file so all five
#                     tools work in the running app. The backend targets it via
#                     EVE_TRADER_PG_DSN (see .cursor/environment.json).
for db in eve_trader eve_trader_dev; do
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
    || sudo -u postgres createdb "$db"
done

# --- Apply every schema file to the application database (idempotent) ---
export PGPASSWORD=devpassword
APP_DSN="host=localhost port=5432 user=postgres dbname=eve_trader_dev"
for f in phase1_schema phase2_schema phase3_schema admin_schema doctrine_schema \
         observability_schema refining_schema role_consent_schema \
         special_orders_schema station_trading_schema sorting_schema \
         production_buy_list_schema pipeline_runs_schema; do
  psql "$APP_DSN" -v ON_ERROR_STOP=1 -q -f "docs/$f.sql" >/dev/null
done

echo "start.sh: PostgreSQL ready (test DB: eve_trader, app DB: eve_trader_dev)"
