# Handoff note (2026-08-16 → continuing on a different computer)

Temporary file, not a permanent project doc - delete once the Oracle VM is
up and deployed (see "What's left" below). Written so a fresh Claude Code
session on a different machine can pick up exactly where this one left off:
that new session has **no memory of this conversation** (Claude's session
memory lives outside git, tied to this specific machine) - everything it
needs to know is either in git already (code, `CLAUDE.md`) or spelled out
here.

## What's done (all committed + pushed to `origin/main`)

- **ESI-based access gate** (`eve_trader/access_gate.py`) - EVE SSO login
  gates the whole app once `access_gate_enabled: true`, checked against
  character/corp/alliance allowlists. Off by default, fully tested (301+
  tests), not yet turned on anywhere real.
- **Deployment prep** (`deploy/` folder) - `setup.sh`, systemd/nginx
  templates, `deploy/README.md` walkthrough for a single-user deployment on
  Oracle Cloud's Always Free tier, written for "Phase 1: bare IP + plain
  HTTP first, add a domain/HTTPS later" (that's the path chosen, not
  HTTPS-from-day-one).
- **Industry Jobs tab**: Activity filter, per-job Output Value, and
  Manufacturing/Reactions/Combined value KPI cards.
- GitHub repo (private): `https://github.com/Pappmichel/eve-trader`.

## What's in progress: the Oracle Cloud VM

**Not created yet** - blocked on Always Free Ampere A1 capacity in
`eu-frankfurt-1` ("Out of host capacity", a well-known recurring Oracle
issue, not a configuration problem). Decided against the 1GB x86 fallback
shape (too small for this app's real usage - see reasoning in git history /
ask a fresh Claude session to re-derive it if needed, it's straightforward:
`npm run build` alone can exceed 1GB, plus Trading's "Full Search" and
Production's "Build Candidates" scan are genuinely memory-hungry).

**Already prepared in the OCI Console** (Frankfurt region) - reachable via
Cloud Shell, do **not** redo these:
- VCN `eve-trader-vcn` with a manually-added public subnet `public-subnet`
  (CIDR `10.0.0.0/24`) - the wizard's own auto-subnet creation didn't take,
  had to be added by hand.
- Tenancy/compartment ID, the Ubuntu 24.04 ARM image ID, and the subnet ID
  were all looked up once already - a fresh Cloud Shell session needs to
  re-fetch them (Cloud Shell itself does **not** persist between sessions),
  but the *resources* (VCN/subnet/tenancy) themselves persist fine:
  ```bash
  TENANCY_ID="<get from Console profile menu -> Tenancy -> copy OCID>"
  IMAGE_ID=$(oci compute image list --compartment-id $TENANCY_ID \
    --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
    --shape "VM.Standard.A1.Flex" --query "data[0].id" --raw-output)
  oci network subnet list --compartment-id $TENANCY_ID --query "data[].{name:\"display-name\",id:id}"
  # ^ copy the public-subnet's id into SUBNET_ID
  ```

**The retry-until-capacity-frees-up loop** (see this conversation's history
for how we got here) needs to be **started fresh** in a new Cloud Shell
session - it does not survive a closed browser tab either:
```bash
while true; do
  for AD in $(oci iam availability-domain list --compartment-id $TENANCY_ID --query "data[].name" --raw-output | jq -r '.[]'); do
    echo "Versuche $AD..."
    oci compute instance launch \
      --compartment-id $TENANCY_ID --availability-domain "$AD" \
      --shape "VM.Standard.A1.Flex" --shape-config '{"ocpus":2,"memoryInGBs":12}' \
      --image-id "$IMAGE_ID" --subnet-id "$SUBNET_ID" --assign-public-ip true \
      --ssh-authorized-keys-file ~/eve-trader-oracle.pub \
      --display-name "eve-trader" --wait-for-state RUNNING && break 2
    sleep 5
  done
  echo "Alle ADs fehlgeschlagen, warte 60s..."
  sleep 60
done
```
(needs `~/eve-trader-oracle.pub` re-uploaded to Cloud Shell first - see
"SSH keys" below. Cloud Shell also disconnects after ~15-20 min idle, so
this needs occasional attention, not truly unattended.)

## SSH keys - in the project folder, NOT in git

Two ed25519 keypairs, originally generated in `%USERPROFILE%\.ssh\` on this
machine, are copied into **`.ssh-local/`** at the repo root (this exact
folder name is gitignored - `git check-ignore -v .ssh-local/*` confirmed
none of the 4 files are tracked or stageable):
- `eve-trader-oracle` (+ `.pub`) - for logging into the VM itself once it
  exists.
- `eve-trader-deploy` (+ `.pub`) - a **read-only GitHub deploy key**,
  already added at github.com/Pappmichel/eve-trader/settings/keys
  ("Oracle VM") - lets the VM `git clone` the private repo.

Since this project folder is Dropbox-synced to the new computer (same
account, confirmed), **`.ssh-local/` arrives there automatically** - no
manual transfer needed. On the new machine, either use the keys straight
from `.ssh-local/` (e.g. `ssh -i .ssh-local/eve-trader-oracle ...`,
`GIT_SSH_COMMAND="ssh -i .ssh-local/eve-trader-deploy" git clone ...`), or
copy them into that machine's own `~/.ssh/` first if you'd rather not
reference a Dropbox path directly - either way the keys themselves are
unchanged, no need to regenerate or update the GitHub deploy key.

## Also not in git (by design) - confirmed handled by Dropbox here

The new computer syncs the **same Dropbox account**, so this whole project
folder - `config.yaml`, `.env`, `data/` (the real DB, OAuth tokens,
backups), and now `.ssh-local/` too - arrives there automatically, unlike a
from-scratch git clone. Nothing manual needed for any of it.

## What's left once the VM exists

Follow `deploy/README.md` starting at "1. Get the code onto the server" -
it's already written for exactly this state (Phase 1: bare IP + HTTP,
deploy key, `setup.sh`, then Phase 2 later for a domain + HTTPS). Nothing
else needs deciding - every open question from this session is already
resolved and written down there or above.
