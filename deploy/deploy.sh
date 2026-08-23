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
for f in phase1_schema.sql phase2_schema.sql phase3_schema.sql admin_schema.sql \
         doctrine_schema.sql observability_schema.sql refining_schema.sql role_consent_schema.sql; do
    sudo -u postgres psql -d eve_trader -f "docs/$f"
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
