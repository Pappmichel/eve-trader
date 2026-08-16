# Deploying EVE Trader (single-user, e.g. Oracle Cloud Always Free)

This walks through the "Option 1" deployment discussed and chosen
2026-08-16: just you, reachable from anywhere, protected by the access gate
(`eve_trader/access_gate.py`) instead of running fully open. **Confirmed
working end-to-end against a real Oracle Cloud instance, 2026-08-16**
(VM.Standard.E2.1.Micro, eu-frankfurt-1) - the steps and gotchas below are
from that actual run, not just theory.

## Before you start

- **A VM with SSH access.** Oracle's Always Free Ampere A1 (ARM) or the
  x86 E2.1.Micro fallback both work - nothing here is architecture-specific.
- **TWO separate firewall layers need port 80/443 open, not just one** -
  confirmed real, both bit us during the first real deployment:
  1. **Oracle's Security List** (cloud-level, in front of the VM's network
     interface) - Networking → Virtual Cloud Networks → your VCN → Security
     Lists → add Ingress Rules for TCP 80 and 443, source `0.0.0.0/0`.
  2. **The VM's own local `iptables`**, *separately* - Oracle's stock Ubuntu
     images ship with a default `iptables` ruleset that only allows SSH (22)
     inbound and rejects everything else, regardless of what the Security
     List allows. Symptom: `sudo systemctl status nginx`/`eve-trader` both
     show "active (running)", `curl http://localhost/...` works *on the VM*,
     but the public IP is unreachable from outside. Fix (also persists
     across reboots):
     ```bash
     sudo iptables -L INPUT -n --line-numbers   # find the REJECT rule's line number
     sudo iptables -I INPUT <line-before-REJECT> -p tcp -m state --state NEW -m tcp --dport 80 -j ACCEPT
     sudo iptables -I INPUT <line-before-REJECT> -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT
     sudo netfilter-persistent save
     ```
- **If you create the VCN/subnet manually** (the Console's "Create new public
  subnet" wizard inside instance-creation didn't reliably auto-create
  everything for us) - **also confirm an Internet Gateway exists and the
  subnet's route table has a `0.0.0.0/0` route to it**, or the instance gets
  a public IP with no actual path in/out (same symptom as the iptables issue
  above - SSH/HTTP just time out, nothing gets rejected outright). Check:
  ```bash
  oci network internet-gateway list --compartment-id $TENANCY_ID --vcn-id $VCN_ID
  oci network route-table get --rt-id $RT_ID --query "data.\"route-rules\""
  ```
  If either is empty: create the gateway
  (`oci network internet-gateway create --compartment-id $TENANCY_ID --vcn-id $VCN_ID --is-enabled true --display-name "eve-trader-igw"`)
  and add the route
  (`oci network route-table update --rt-id $RT_ID --route-rules '[{"destination":"0.0.0.0/0","destination-type":"CIDR_BLOCK","network-entity-id":"<igw-id>"}]' --force`).
- **No domain yet is fine** (confirmed 2026-08-16 - this deployment starts on
  the bare public IP over plain HTTP, domain/HTTPS added later - see "Phase
  2" below). Let's Encrypt/certbot needs a real domain, not a bare IP, so
  HTTPS just isn't available until you have one - nothing else here depends
  on having a domain from day one.

## Fallback: x86 shape instead of Ampere A1

If Always Free A1 capacity ("Out of host capacity") never frees up, the
x86 **VM.Standard.E2.1.Micro** shape is almost never capacity-constrained -
trade-off is 1GB RAM instead of A1's several GB, a real risk for this app
(`npm run build` alone can exceed 1GB; Trading's "Full Search" and
Production's "Build Candidates" scan are also memory-hungry). Two
mitigations, both already handled:

- **`deploy/setup.sh` now adds a 4GB swap file automatically** on any VM
  with under 2GB RAM (idempotent, safe to re-run) - turns a likely crash
  into "slow" instead.
- **Build the frontend locally first, don't build it on the VM.** `setup.sh`
  already skips its own `npm run build` step if `frontend/dist/index.html`
  already exists. On your own machine:
  ```powershell
  cd frontend; npm run build; cd ..
  scp -i .ssh-local\eve-trader-oracle -r frontend\dist ubuntu@<public-ip>:~/eve-trader/frontend/dist
  ```
  (run this *before* `./deploy/setup.sh` on the VM, or re-run setup.sh
  afterward - it only skips the build step if `dist/` is already there).

To launch the x86 shape instead of A1: drop `--shape-config` entirely
(E2.1.Micro is a fixed size, not `.Flex`), change `--shape` to
`VM.Standard.E2.1.Micro`, and look up its own image ID first (x86 images
have a different OCID than the ARM one used for A1). **Confirmed real
2026-08-16: E2.1.Micro isn't available in every Availability Domain within
a region** - `oci compute instance launch` fails with a vague
`NotAuthorizedOrNotFound`/404 in an AD that doesn't have it. Check first:
```bash
oci compute shape list --compartment-id $TENANCY_ID --availability-domain "<AD>" --query "data[?contains(shape,'Micro')].shape"
```
and try each AD in the region until one returns `VM.Standard.E2.1.Micro`.
```bash
IMAGE_ID=$(oci compute image list --compartment-id $TENANCY_ID \
  --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
  --shape "VM.Standard.E2.1.Micro" --query "data[0].id" --raw-output)
oci compute instance launch \
  --compartment-id $TENANCY_ID --availability-domain "<any AD>" \
  --shape "VM.Standard.E2.1.Micro" \
  --image-id "$IMAGE_ID" --subnet-id "$SUBNET_ID" --assign-public-ip true \
  --ssh-authorized-keys-file ~/eve-trader-oracle.pub \
  --display-name "eve-trader" --wait-for-state RUNNING
```
E2.1.Micro capacity is available immediately in practice - no retry loop
needed, a single attempt should succeed.

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
Easy to hand-edit `.env` in `nano` and accidentally skip a line (confirmed
real, 2026-08-16 - `SESSION_SECRET_KEY` got missed this way, which doesn't
fail until the *first* login attempt, with a raw Internal Server Error and
`RuntimeError: SESSION_SECRET_KEY is not set` in `sudo journalctl -u
eve-trader -n 60`). Safer to append it directly instead of relying on the
editor:
```bash
python3 -c "import secrets; print('SESSION_SECRET_KEY=' + secrets.token_hex(32))" >> ~/eve-trader/.env
```
Then verify every deployment-specific `.env` value actually landed before
moving on:
```bash
grep -E "EVE_SSO_CALLBACK_HOST|FRONTEND_ORIGIN|EVE_SSO_REDIRECT_URI|SESSION_SECRET_KEY|EVE_SSO_CLIENT_ID" ~/eve-trader/.env
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
