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
# naturally idempotent (apt install, pip install -r requirements.lock, npm ci).
#
# NOT tested against a real Oracle Cloud instance (no such environment
# available while writing this) - written from the documented Ubuntu/nginx/
# certbot/systemd mechanics, but treat it as a strong starting point to
# read through and adapt, not a guaranteed one-shot script.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_USER="$(whoami)"
DOMAIN="${1:-}"
UNIT_FILE=/etc/systemd/system/eve-trader.service

# Service user. The repository belongs to the deploy user (the login user that
# runs this script and deploy.sh); the service runs as a separate system user
# without a shell, a home directory or sudo rights. Precedence:
#   1. APP_USER=<name> in the environment,
#   2. the User= of an already installed unit (re-running this script on an
#      existing install must never silently switch the service to a new user),
#   3. "eve-trader" for a fresh install.
if [ -z "${APP_USER:-}" ]; then
    APP_USER=""
    if [ -f "$UNIT_FILE" ]; then
        APP_USER="$(sed -n 's/^User=//p' "$UNIT_FILE" | head -n1)"
        if [ -n "$APP_USER" ]; then
            echo "==> Existing service unit found - keeping User=$APP_USER (set APP_USER=<name> to change it)."
        fi
    fi
    APP_USER="${APP_USER:-eve-trader}"
fi

if [ -z "$DOMAIN" ]; then
    echo "Usage: $0 <your-domain-or-ip>"
    echo "  e.g. $0 eve-trader.example.com"
    echo "  A bare IP works for the nginx/systemd setup below, but Let's"
    echo "  Encrypt (certbot, see deploy/README.md) needs a real domain name."
    exit 1
fi

echo "==> Installing system packages (Python, Node.js, nginx, certbot, Postgres)..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential \
    nginx certbot python3-certbot-nginx curl git postgresql acl

echo "==> Tuning Postgres for a memory-constrained VM..."
# Native install, not Docker (see deploy/README.md's "Set up Postgres"
# section) - the distro defaults (128MB shared_buffers, 100 max_connections)
# assume far more RAM than an Always-Free-tier VM has; the app's own
# connection pool never opens more than 10 (storage._get_pool()). Idempotent
# - re-running just re-applies the same sed substitutions.
PG_CONF=$(sudo -u postgres psql -tAc "SHOW config_file;" 2>/dev/null || true)
if [ -n "$PG_CONF" ]; then
    sudo sed -i \
        -e "s/^#\?shared_buffers.*/shared_buffers = 32MB/" \
        -e "s/^#\?max_connections.*/max_connections = 20/" \
        "$PG_CONF"
    sudo systemctl restart postgresql
fi

NODE_MAJOR=0
if command -v node >/dev/null 2>&1; then
    NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
fi
if [ "$NODE_MAJOR" -lt 22 ]; then
    echo "==> Installing Node.js 22.x (NodeSource; found major version $NODE_MAJOR)..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

echo "==> Checking memory / swap..."
# The Always Free x86 fallback shape (VM.Standard.E2.1.Micro, 1GB RAM) is a
# real risk of an out-of-memory failure during the frontend build below or
# under Trading's "Full Search"/Production's "Build Candidates" scan later -
# a swap file turns that into "slow" instead of "crashed". Skipped entirely
# on the Ampere A1 shape (this script's primary target, 6GB+ RAM) since
# free -m already reports well above the threshold there. Idempotent - safe
# to re-run, only creates /swapfile if it doesn't already exist.
TOTAL_MEM_MB=$(free -m | awk '/^Mem:/{print $2}')
if [ "$TOTAL_MEM_MB" -lt 2048 ] && [ ! -f /swapfile ]; then
    echo "    Low memory (${TOTAL_MEM_MB}MB) - adding a 4GB swap file..."
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
fi

echo "==> Python venv + backend dependencies..."
cd "$APP_DIR"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.lock -q
.venv/bin/pip install -e . --no-deps -q

echo "==> Frontend build..."
if [ -f "$APP_DIR/frontend/dist/index.html" ]; then
    echo "    frontend/dist already exists - skipping build (delete it first to force a rebuild)."
    echo "    On a low-memory VM, building locally and rsyncing frontend/dist/ here first (instead"
    echo "    of letting this script run 'npm run build' on the VM itself) avoids the biggest"
    echo "    out-of-memory risk in this whole setup - see deploy/README.md's x86 fallback section."
else
    cd "$APP_DIR/frontend"
    npm ci
    npm run build
    cd "$APP_DIR"
fi

echo "==> Config files (only if missing - existing config.yaml/.env are never overwritten)..."
NEW_SECRET_FILES=()
if [ ! -f .env ]; then
    cp .env.example .env
    NEW_SECRET_FILES+=(.env)
    echo "    Created .env from .env.example - EDIT IT before starting the service (see deploy/README.md)."
fi
if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml
    NEW_SECRET_FILES+=(config.yaml)
    echo "    Created config.yaml from config.example.yaml - EDIT IT before starting the service."
fi
mkdir -p data
# P5-05: an existing install whose config.yaml still has the old
# access_gate_enabled: false default is rewritten to true unless the
# operator has explicitly set EVE_TRADER_ALLOW_GATE_OFF. Fresh copies of
# config.example.yaml already have true.
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

echo "==> Service user and file access..."
if [ "$APP_USER" = "$DEPLOY_USER" ]; then
    echo "    Service user equals the deploy user ($APP_USER) - no separate account, no ACLs."
    echo "    A separate, sudo-less service user is recommended; see deploy/README.md."
else
    if ! id "$APP_USER" >/dev/null 2>&1; then
        # --system: no aging, low uid; no home directory, no login shell, and
        # useradd adds no supplementary groups, so no sudo/admin membership.
        sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
        echo "    Created system user $APP_USER."
    fi
    # The service user may traverse the directories above the repository
    # (execute bit only), read the code, the venv and the built frontend, and
    # write only data/. Default ACLs make new files under data/ writable by
    # both the service user and the deploy user (backups are created by
    # either).
    _dir="$APP_DIR"
    while [ "$_dir" != "/" ]; do
        sudo setfacl -m "u:$APP_USER:x" "$_dir"
        _dir="$(dirname "$_dir")"
    done
    sudo setfacl -R -m "u:$APP_USER:rX" "$APP_DIR"
    sudo setfacl -R -m "u:$APP_USER:rwX" -m "u:$DEPLOY_USER:rwX" "$APP_DIR/data"
    sudo setfacl -R -d -m "u:$APP_USER:rwX" -m "u:$DEPLOY_USER:rwX" "$APP_DIR/data"
    # .env and config.yaml hold secrets: readable only by the deploy user and
    # the service user. Only files created by this run are touched; an
    # existing file keeps whatever ACL its operator gave it.
    for _f in "${NEW_SECRET_FILES[@]}"; do
        chmod 600 "$APP_DIR/$_f"
        sudo setfacl -m "u:$APP_USER:r" "$APP_DIR/$_f"
    done
    for _f in .env config.yaml; do
        if ! printf '%s\n' "${NEW_SECRET_FILES[@]}" | grep -qx "$_f" && [ -f "$APP_DIR/$_f" ]; then
            echo "    $_f already existed: grant read with  sudo setfacl -m u:$APP_USER:r $APP_DIR/$_f  (not changed here)."
        fi
    done
fi

echo "==> systemd service..."
sed -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__APP_USER__|$APP_USER|g" \
    deploy/eve-trader.service.template | sudo tee /etc/systemd/system/eve-trader.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable eve-trader

echo "==> nginx site..."
NGINX_TEMPLATE=deploy/nginx-eve-trader.conf.template
if sudo test -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem"; then
    echo "    Certificate found for $DOMAIN - using the HTTPS template."
    NGINX_TEMPLATE=deploy/nginx-eve-trader-ssl.conf.template
else
    echo "    No certificate under /etc/letsencrypt/live/$DOMAIN/ - using the HTTP template."
    echo "    After 'sudo certbot certonly --nginx -d $DOMAIN', re-run this script (see deploy/README.md)."
fi
sed -e "s|__DOMAIN__|$DOMAIN|g" "$NGINX_TEMPLATE" \
    | sudo tee /etc/nginx/sites-available/eve-trader > /dev/null
sudo ln -sf /etc/nginx/sites-available/eve-trader /etc/nginx/sites-enabled/eve-trader
sudo nginx -t
sudo systemctl reload nginx

cat <<EOF

==> Done with system setup. Remaining steps (see deploy/README.md "2b. Set
    up Postgres" + "Phase 1" for detail - this assumes no domain yet, plain
    http:// on the bare IP; see "Phase 2" there for adding a domain + HTTPS
    once you have one):

  1. Apply the Postgres schema and set a real EVE_TRADER_PG_DSN password
     (see deploy/README.md's "2b. Set up Postgres" section) - Postgres
     itself is already installed and tuned by this script, but the schema/
     role password are deliberately manual, one-time steps.
  2. Edit $APP_DIR/.env and $APP_DIR/config.yaml with your real values
     (EVE_SSO_CLIENT_ID, structure_id, character names, ...), then provision
     yourself as a tenant (`eve-trader tenant create`/`tenant add-entry` -
     see deploy/README.md, this replaces the old config.yaml allowlist).
  3. Register a new EVE SSO app for this deployment at
     https://developers.eveonline.com/applications with callback URL:
       http://$DOMAIN/api/auth/callback
     and set EVE_SSO_REDIRECT_URI + FRONTEND_ORIGIN in .env to match
     (both to http://$DOMAIN, no https:// yet).
  4. sudo systemctl restart eve-trader
  5. Open http://$DOMAIN in a browser.

  Once you have a real domain pointed at this IP, see deploy/README.md's
  "Phase 2" for adding HTTPS via certbot.

EOF
