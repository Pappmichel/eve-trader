#!/usr/bin/env bash
# One-time setup for the EVE Trader Cloud Agent dev environment.
#
# Runs after the repository is checked out. Installs native PostgreSQL plus the
# Python/Node dependencies the app needs. Idempotent - safe to re-run. Per-boot
# service startup and database provisioning live in start.sh instead.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- System packages: PostgreSQL (native, no Docker) + Python venv support ---
if ! command -v pg_ctlcluster >/dev/null 2>&1; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    postgresql postgresql-contrib python3-venv
fi

# --- Python virtualenv + project (editable install, with test extras) ---
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -e '.[test]'

# --- Frontend dependencies (uses the committed package-lock.json) ---
( cd frontend && npm ci )

# --- Local config from the committed example files (never clobber an edit) ---
[ -f .env ] || cp .env.example .env
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo "install.sh: dependencies ready"
