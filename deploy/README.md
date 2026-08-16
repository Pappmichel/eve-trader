# Deploying EVE Trader (single-user, e.g. Oracle Cloud Always Free)

This walks through the "Option 1" deployment discussed and chosen
2026-08-16: just you, reachable from anywhere, protected by the access gate
(`eve_trader/access_gate.py`) instead of running fully open. Written for a
fresh Ubuntu 22.04/24.04 VM; not tested against a real Oracle Cloud instance
(no such environment was available while writing this) - read through it
before running, and adapt anything that doesn't match your actual VM.

## Before you start

- **A VM with SSH access.** Oracle's Always Free Ampere A1 (ARM) works fine -
  nothing here is architecture-specific.
- **Ports 80 and 443 open in Oracle's Security List / Network Security
  Group**, not just the VM's own firewall (`ufw`/`iptables`). This is the
  most common gotcha: Oracle has its *own* firewall layer in front of the
  VM's network interface, separate from anything you configure on the VM
  itself - forgetting it leaves the server unreachable from outside even
  though everything looks fine locally.
- **No domain yet is fine** (confirmed 2026-08-16 - this deployment starts on
  the bare public IP over plain HTTP, domain/HTTPS added later - see "Phase
  2" below). Let's Encrypt/certbot needs a real domain, not a bare IP, so
  HTTPS just isn't available until you have one - nothing else here depends
  on having a domain from day one.

## 1. Get the code onto the server

The GitHub repo is currently **private**, so a plain `git clone` needs
credentials. A deploy key was already generated and added to the repo
(GitHub → Settings → Deploy keys → "Oracle VM") - the matching **private**
key is `~/.ssh/eve-trader-deploy` on the machine that generated it. Copy it
onto the VM (from that machine, once you have the VM's public IP):

```powershell
scp -i "$env:USERPROFILE\.ssh\eve-trader-oracle" "$env:USERPROFILE\.ssh\eve-trader-deploy" ubuntu@<public-ip>:~/.ssh/eve-trader-deploy
```

Then, **on the VM**:

```bash
chmod 600 ~/.ssh/eve-trader-deploy
GIT_SSH_COMMAND="ssh -i ~/.ssh/eve-trader-deploy" git clone git@github.com:Pappmichel/eve-trader.git
cd eve-trader
```

(Alternative if you'd rather not deal with the deploy key: `rsync -avz
--exclude-from=.gitignore --exclude=.git ./ ubuntu@<public-ip>:~/eve-trader/`
from your own machine instead - skip straight to step 2 on the VM.)

## 2. Run the setup script

```bash
chmod +x deploy/setup.sh
./deploy/setup.sh <public-ip>
```

Installs Python/Node/nginx/certbot, builds the venv and the frontend, writes
`.env`/`config.yaml` from the `.example` files (only if they don't already
exist - never overwrites a real config), and installs (but does not yet
fully configure) the systemd service and nginx site. See the script itself
for exactly what each step does - it's meant to be read, not just trusted.

## 3. Configure (Phase 1: bare IP, HTTP)

Edit `.env` (secrets) and `config.yaml` (everything else) with your real
values - same fields as local dev (see the repo's main README), plus these
deployment-specific ones. No `https://` and no port number in either URL
below - nginx serves on the standard port 80, and there's no certificate yet:

**`.env`:**
```
EVE_SSO_CALLBACK_HOST=<public-ip>
FRONTEND_ORIGIN=http://<public-ip>
EVE_SSO_REDIRECT_URI=http://<public-ip>/api/auth/callback
SESSION_SECRET_KEY=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
```

**`config.yaml`:**
```yaml
access_gate_enabled: true
allowed_character_ids: [<your character_id>]
allowed_corporation_ids: []
allowed_alliance_ids: []
```

**At https://developers.eveonline.com/applications:** register a **new**
EVE SSO application for this deployment (don't reuse the local-dev one - an
app's registered callback URL isn't guaranteed to support having both the
local `http://localhost:8000/...` one and this one at the same time), with
callback URL `http://<public-ip>/api/auth/callback` - must match
`EVE_SSO_REDIRECT_URI` above *exactly*. Put its client ID in `.env`'s
`EVE_SSO_CLIENT_ID`.

Then:

```bash
sudo systemctl restart eve-trader
```

## 4. Verify (Phase 1)

- `sudo systemctl status eve-trader` - should be `active (running)`.
- `curl -I http://<public-ip>/api/gate/status` - should return `200` with
  `"enabled":true`.
- Open `http://<public-ip>` in a browser, click "Login with EVE Online" on
  the landing page, confirm you land back on the app logged in (and that a
  *different*, non-allowlisted character gets denied).

## Phase 2: adding a domain + HTTPS later

Once you have a domain pointed at the VM's public IP (an A record):

1. Update the EVE SSO app's registered callback URL to
   `https://your-domain/api/auth/callback`.
2. In `.env`: `EVE_SSO_CALLBACK_HOST=your-domain`,
   `FRONTEND_ORIGIN=https://your-domain`,
   `EVE_SSO_REDIRECT_URI=https://your-domain/api/auth/callback`.
3. `sudo systemctl restart eve-trader`
4. `sudo certbot --nginx -d your-domain` (adds HTTPS + the http->https
   redirect - also rewrites the nginx site config in place).

`access_gate.py`'s session cookie automatically becomes `Secure` (HTTPS-only)
the moment `FRONTEND_ORIGIN` starts with `https://` - no other change needed
for that part.

## Updating later

```bash
cd eve-trader
git pull                      # or re-rsync
.venv/bin/pip install -e .
cd frontend && npm ci && npm run build && cd ..
sudo systemctl restart eve-trader
```

## Logs

```bash
sudo journalctl -u eve-trader -f
```
