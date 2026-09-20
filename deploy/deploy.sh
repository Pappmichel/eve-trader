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

# Self-modifying-script guard (confirmed real bug 2026-09-16, found live: a
# freshly-added schema file - job_category_cost_index_overrides_schema.sql -
# was silently skipped on the very run that pulled the commit adding it to
# the loop below). bash opens this script's file descriptor once at process
# start and keeps reading from it for the rest of the run - the git pull
# above can rewrite this file's own later lines (like the schema-apply loop)
# on disk, but the already-running interpreter doesn't re-open the file to
# pick that up; it only sees the new content on a *separate*, later
# invocation. Re-exec once against the now-current file so the rest of this
# run is guaranteed to read whatever git pull just produced, not whatever
# was on disk when this run started. Uses BASH_SOURCE[0], not $0 - same
# reasoning APP_DIR above already relies on: $0 can be a path relative to
# the caller's original cwd (e.g. invoked as `cd deploy && ./deploy.sh`),
# which the `cd "$APP_DIR"` above would then resolve against the *wrong*
# directory on re-exec; BASH_SOURCE[0] plus APP_DIR (already absolute) stays
# correct regardless of how this script was invoked. The env var guards
# against re-execing forever.
if [ -z "${EVE_TRADER_DEPLOY_REEXECED:-}" ]; then
    export EVE_TRADER_DEPLOY_REEXECED=1
    exec "$APP_DIR/deploy/deploy.sh" "$@"
fi

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
         station_trading_schema.sql role_consent_schema.sql special_orders_schema.sql \
         sorting_schema.sql production_buy_list_schema.sql pipeline_runs_schema.sql \
         session_revocations_schema.sql job_category_cost_index_overrides_schema.sql \
         esi_access_schema.sql; do
    sudo -u postgres psql -d eve_trader -v ON_ERROR_STOP=1 -f "docs/$f"
done

echo "==> Installing backend dependencies..."
.venv/bin/pip install -r requirements.lock -q
.venv/bin/pip install -e . --no-deps -q

echo "==> Migrating legacy access_gate_enabled: false (P5-05)..."
# Runtime load_access_config also forces the gate on unless
# EVE_TRADER_ALLOW_GATE_OFF is set. This rewrite makes the on-disk config
# match that policy so a later reader of config.yaml is not misled.
ALLOW_GATE_OFF="${EVE_TRADER_ALLOW_GATE_OFF:-}"
if [ -f .env ]; then
  _env_allow=$(grep -E '^[[:space:]]*EVE_TRADER_ALLOW_GATE_OFF=' .env | tail -n1 | cut -d= -f2- | tr -d '[:space:]"' | tr '[:upper:]' '[:lower:]' || true)
  if [ -n "$_env_allow" ]; then
    ALLOW_GATE_OFF="$_env_allow"
  fi
fi
case "$(echo "${ALLOW_GATE_OFF:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes) echo "    EVE_TRADER_ALLOW_GATE_OFF set - leaving config.yaml gate setting unchanged." ;;
  *)
    if [ -f config.yaml ] && grep -qE '^[[:space:]]*access_gate_enabled:[[:space:]]*false\b' config.yaml; then
      sed -i 's/^[[:space:]]*access_gate_enabled:[[:space:]]*false\b/access_gate_enabled: true/' config.yaml
      echo "    Rewrote access_gate_enabled: false -> true in config.yaml."
    fi
    ;;
esac

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
