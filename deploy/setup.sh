#!/usr/bin/env bash
# One-time provisioning for a fresh Ubuntu 22.04/24.04 VM (written for Oracle
# Cloud's Always Free Ampere A1, but doesn't assume ARM specifically - plain
# apt/pip/npm, no architecture-specific steps).
#
# Run this FROM the repo root, after getting the code onto the server
# yourself (git clone with a deploy key, or rsync from your own machine -
# see deploy/README.md) - this script does not fetch the code, only sets up
# everything around it (system packages, venv, built frontend, systemd
# service, nginx). Safe to re-run: every step either checks first or is
# naturally idempotent (apt install, pip install -e ., npm ci).
#
# NOT tested against a real Oracle Cloud instance (no such environment
# available while writing this) - written from the documented Ubuntu/nginx/
# certbot/systemd mechanics, but treat it as a strong starting point to
# read through and adapt, not a guaranteed one-shot script.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${APP_USER:-$(whoami)}"
DOMAIN="${1:-}"

if [ -z "$DOMAIN" ]; then
    echo "Usage: $0 <your-domain-or-ip>"
    echo "  e.g. $0 eve-trader.example.com"
    echo "  A bare IP works for the nginx/systemd setup below, but Let's"
    echo "  Encrypt (certbot, see deploy/README.md) needs a real domain name."
    exit 1
fi

echo "==> Installing system packages (Python, Node.js, nginx, certbot)..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential \
    nginx certbot python3-certbot-nginx curl git

if ! command -v node >/dev/null 2>&1; then
    echo "==> Installing Node.js 20.x (NodeSource)..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

echo "==> Python venv + backend dependencies..."
cd "$APP_DIR"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -e . -q

echo "==> Frontend build..."
cd "$APP_DIR/frontend"
npm ci
npm run build
cd "$APP_DIR"

echo "==> Config files (only if missing - existing config.yaml/.env are never overwritten)..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "    Created .env from .env.example - EDIT IT before starting the service (see deploy/README.md)."
fi
if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml
    echo "    Created config.yaml from config.example.yaml - EDIT IT before starting the service."
fi

echo "==> systemd service..."
sed -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__APP_USER__|$APP_USER|g" \
    deploy/eve-trader.service.template | sudo tee /etc/systemd/system/eve-trader.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable eve-trader

echo "==> nginx site..."
sed -e "s|__DOMAIN__|$DOMAIN|g" deploy/nginx-eve-trader.conf.template \
    | sudo tee /etc/nginx/sites-available/eve-trader > /dev/null
sudo ln -sf /etc/nginx/sites-available/eve-trader /etc/nginx/sites-enabled/eve-trader
sudo nginx -t
sudo systemctl reload nginx

cat <<EOF

==> Done with system setup. Remaining steps (see deploy/README.md "Phase 1"
    for detail - this assumes no domain yet, plain http:// on the bare IP;
    see "Phase 2" there for adding a domain + HTTPS once you have one):

  1. Edit $APP_DIR/.env and $APP_DIR/config.yaml with your real values
     (EVE_SSO_CLIENT_ID, structure_id, character names, access-gate
     allowlists, ...).
  2. Register a new EVE SSO app for this deployment at
     https://developers.eveonline.com/applications with callback URL:
       http://$DOMAIN/api/auth/callback
     and set EVE_SSO_REDIRECT_URI + FRONTEND_ORIGIN in .env to match
     (both to http://$DOMAIN, no https:// yet).
  3. sudo systemctl restart eve-trader
  4. Open http://$DOMAIN in a browser.

  Once you have a real domain pointed at this IP, see deploy/README.md's
  "Phase 2" for adding HTTPS via certbot.

EOF
