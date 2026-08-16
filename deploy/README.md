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
- **A domain name pointed at the VM's public IP**, if you want HTTPS (Let's
  Encrypt/certbot needs a real domain, not a bare IP). A bare IP still works
  for the nginx/systemd setup itself, just without a trusted certificate.

## 1. Get the code onto the server

The GitHub repo is currently **private**, so a plain `git clone` needs
credentials. Easiest options:

- **Deploy key (recommended):** generate an SSH key pair on the VM
  (`ssh-keygen -t ed25519`), add the public key as a **read-only Deploy Key**
  on the GitHub repo (Settings → Deploy keys), then
  `git clone git@github.com:Pappmichel/eve-trader.git`.
- **rsync from your own machine**, excluding everything gitignored:
  `rsync -avz --exclude-from=.gitignore --exclude=.git ./ user@server:/home/user/eve-trader/`

Either way, end up with the repo at some path on the server (`deploy/setup.sh`
below auto-detects it from its own location, so it doesn't matter exactly
where).

## 2. Run the setup script

```bash
cd eve-trader
chmod +x deploy/setup.sh
./deploy/setup.sh your-domain-or-ip
```

Installs Python/Node/nginx/certbot, builds the venv and the frontend, writes
`.env`/`config.yaml` from the `.example` files (only if they don't already
exist - never overwrites a real config), and installs (but does not yet
fully configure) the systemd service and nginx site. See the script itself
for exactly what each step does - it's meant to be read, not just trusted.

## 3. Configure

Edit `.env` (secrets) and `config.yaml` (everything else) with your real
values - same fields as local dev (see the repo's main README), plus these
deployment-specific ones:

**`.env`:**
```
EVE_SSO_CALLBACK_HOST=your-domain-or-ip
FRONTEND_ORIGIN=https://your-domain
EVE_SSO_REDIRECT_URI=https://your-domain/api/auth/callback
SESSION_SECRET_KEY=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
```

**`config.yaml`:**
```yaml
access_gate_enabled: true
allowed_character_ids: [<your character_id>]
allowed_corporation_ids: []
allowed_alliance_ids: []
```

**At https://developers.eveonline.com/applications:** update this app's
registered callback URL to `https://your-domain/api/auth/callback` - it must
match `EVE_SSO_REDIRECT_URI` above *exactly*, or EVE SSO rejects the login
with a generic error.

Then:

```bash
sudo systemctl restart eve-trader
sudo certbot --nginx -d your-domain   # adds HTTPS + http->https redirect
```

## 4. Verify

- `sudo systemctl status eve-trader` - should be `active (running)`.
- `curl -I https://your-domain/api/gate/status` - should return `200` with
  `"enabled":true`.
- Open `https://your-domain` in a browser, click "Login with EVE Online" on
  the landing page, confirm you land back on the app logged in (and that a
  *different*, non-allowlisted character gets denied).

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
