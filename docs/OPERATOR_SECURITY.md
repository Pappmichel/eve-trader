# Operator security: bootstrap, gate, sessions

This is the deployment sequence for the Phase-4/5.1 authorization changes.
Skipping the bootstrap step **before** restarting onto a gate-enabled
build will lock you out of `/api/admin`. There is no HTTP backdoor.

## What changed

- The access gate is **on by default** (`AccessConfig.access_gate_enabled = True`).
- `DEFAULT_TENANT_ID` no longer implies every tool, including admin.
- Admin is a normal `tool_grants` row, created by another admin or by the CLI below.
- Session cookies are re-checked against `tenant_registry_entries` on every
  request. Removing or reassigning a character invalidates the old cookie.
- Revocation is persisted in `character_session_revocations` (see
  `docs/session_revocations_schema.sql`). Removing a character and later
  re-adding them does **not** resurrect an old cookie. Apply that schema
  file on upgrade (`./deploy/deploy.sh` includes it).
- A leftover `access_gate_enabled: false` in an existing `config.yaml`
  **cannot silently stay off** after upgrade (P5-05). At process start,
  `load_access_config` forces the gate on unless `EVE_TRADER_ALLOW_GATE_OFF`
  is `1`/`true`/`yes`. `deploy/setup.sh` and `deploy/deploy.sh` also rewrite
  that yaml key to `true` unless the env var is set.

Trusted local/dev that *intentionally* wants the gate off:

```bash
# .env (filesystem/SSH only — never a request parameter)
EVE_TRADER_ALLOW_GATE_OFF=1
```

and `access_gate_enabled: false` in `config.yaml`. Do **not** set this on a
production host.

## Upgrade from a pre-gate / gate-off install

Exact steps, in order. Bootstrap is CLI, not HTTP, so a forced-on gate
does not lock the operator out of recovery.

1. Deploy the new code (includes `docs/session_revocations_schema.sql` and
   `eve-trader admin bootstrap`).
2. Apply schema. `./deploy/deploy.sh` does this; if updating by hand, also run:

```bash
sudo -u postgres psql -d eve_trader -f docs/session_revocations_schema.sql
```

3. Bootstrap yourself **before or after** restart — the command does not
   need the HTTP API:

```bash
cd ~/eve-trader
.venv/bin/eve-trader admin bootstrap \
  --character-id YOUR_EVE_CHARACTER_ID \
  --character-name "Your Name" \
  --all-tools \
  --confirm
```

`--confirm` is required. Without it the command refuses. Default (omit
`--all-tools`) grants **admin only**; `--all-tools` also grants every other
tool so the operator can use Trading/Production/Portfolio immediately.

4. Confirm the row:

```bash
.venv/bin/eve-trader tenant list
```

5. Restart the app (`sudo systemctl restart eve-trader`), or let
   `./deploy/deploy.sh` restart it. The gate is on. A previous
   `access_gate_enabled: false` is treated as on unless
   `EVE_TRADER_ALLOW_GATE_OFF` is set.
6. Log in through EVE SSO (gate login). `/api/admin/*` should return 200 for
   you and 403 for a normal user.
7. Do **not** set `access_gate_enabled: false` as recovery. If you are locked
   out of admin, re-run the bootstrap command over SSH. Bootstrap does **not**
   clear `character_session_revocations`; an old cookie stays invalid and a
   new SSO login issues a new cookie.

## Fresh install

1. Apply schema (including `docs/admin_schema.sql` / `docs/phase3_schema.sql`
   / `docs/session_revocations_schema.sql`).
2. Install from `requirements.lock` (see `docs/DEPENDENCY_SECURITY.md`).
   Python 3.10–3.12; CI uses 3.11.
3. Copy `config.example.yaml` → `config.yaml` (`access_gate_enabled: true`).
4. Set `SESSION_SECRET_KEY` in `.env`.
5. Bootstrap as above, then start the app and log in.

With the gate on and no admin grant, `/api/admin/*` is 403 (or 401 if you
are not logged in). That is the expected controlled state, not a bug.

## Idempotence / misuse

Re-running bootstrap on the **same** character is a no-op grant (upsert).
It will not move them to another tenant. A new unregistered character is
placed on `DEFAULT_TENANT_ID` only if that tenant has no occupant; otherwise
a dedicated tenant is created. UNIQUE `(tenant_id)` on
`tenant_registry_entries` (one character per tenant) is unchanged.

There is no unauthenticated HTTP path that can grant admin.

## OpenAPI / docs (F-19)

`/docs`, `/redoc`, and `/openapi.json` require a valid session while the
gate is on. They are public while the gate is off (trusted local operator).
The schema must not contain secrets; that is covered by tests.
