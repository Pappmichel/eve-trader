#!/usr/bin/env bash
# Bundles the "Updating later" sequence from deploy/README.md into one
# command, run FROM THE VM after the code is already cloned and set up once
# via setup.sh (this script never does initial provisioning - see setup.sh
# for that). Idempotent - every schema file is safe to re-run, matching
# deploy/README.md's own "always re-run every schema file" guidance, so
# there's no harm running this even when a given update didn't touch the
# schema or the frontend.
#
# Usage (on the VM, from the repo root):
#   ./deploy/deploy.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

echo "==> Pulling latest code..."
git pull

echo "==> Applying Postgres schema (all files, idempotent)..."
# Confirmed real bug (2026-08-30): this list was missing station_trading_
# schema.sql - each schema file's own tenant_settings_scope_check widening
# (DROP CONSTRAINT then ADD CONSTRAINT with that file's own, then-current
# scope list) is only self-consistent in the original chronological order
# these files were written in. Without station_trading_schema.sql in this
# loop, doctrine_schema.sql's and refining_schema.sql's own (older,
# narrower) ADD CONSTRAINT statements fail against a live 'station_trading'
# row - and since their own DROP CONSTRAINT already succeeded first, this
# SILENTLY LEFT THE CONSTRAINT MISSING ENTIRELY rather than merely stale
# (psql's default no-ON_ERROR_STOP behavior doesn't abort the file or this
# loop on that error either, so this was easy to miss - see ON_ERROR_STOP
# below, added for the same reason).
#
# Adding station_trading_schema.sql to the loop and ON_ERROR_STOP together
# only turned that silent failure into a loud one, though - they didn't fix
# the actual root cause: confirmed real again live (2026-08-31),
# doctrine_schema.sql's and refining_schema.sql's own scope lists had
# independently drifted stale (missing 'station_trading'), so
# ON_ERROR_STOP correctly aborted the whole deploy right there, before the
# loop ever reached station_trading_schema.sql's wider ADD CONSTRAINT that
# would've put things right again. The actual fix is keeping every schema
# file's copy of this ALTER's scope list equal to the full, current set
# (see docs/doctrine_schema.sql's own comment on this same ALTER) - this
# loop's chronological ordering only matters for tables/columns being
# introduced, never for this particular shared CHECK constraint.
for f in phase1_schema.sql phase2_schema.sql phase3_schema.sql admin_schema.sql \
         doctrine_schema.sql observability_schema.sql refining_schema.sql \
         station_trading_schema.sql role_consent_schema.sql special_orders_schema.sql; do
    sudo -u postgres psql -d eve_trader -v ON_ERROR_STOP=1 -f "docs/$f"
done

echo "==> Installing backend dependencies..."
.venv/bin/pip install -e . -q

echo "==> Building frontend..."
cd "$APP_DIR/frontend"
npm ci
npm run build
cd "$APP_DIR"

echo "==> Restarting eve-trader..."
sudo systemctl restart eve-trader

echo "==> Verifying..."
sleep 2
sudo systemctl is-active --quiet eve-trader && echo "    eve-trader: active" \
    || { echo "    eve-trader failed to start - see: sudo journalctl -u eve-trader -n 60"; exit 1; }
git log -1 --format='    Deployed commit: %H (%ci)'

echo "==> Done."
