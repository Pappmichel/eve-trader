# Operator security: bootstrap, gate, sessions

This is the deployment sequence for the Phase-4 authorization changes.
Skipping the bootstrap step **before** restarting onto a gate-enabled
build will lock you out of `/api/admin`. There is no HTTP backdoor.

## What changed

- The access gate is **on by default** (`AccessConfig.access_gate_enabled = True`).
- `DEFAULT_TENANT_ID` no longer implies every tool, including admin.
- Admin is a normal `tool_grants` row, created by another admin or by the CLI below.
- Session cookies are re-checked against `tenant_registry_entries` on every
  request. Removing or reassigning a character invalidates the old cookie.

You can still set `access_gate_enabled: false` in `config.yaml` over SSH.
That is a filesystem trust channel, not a request parameter.

## What an operator must do before deploying the gate change

On the **already-running** host, while you can still reach the app (or via
SSH/systemd on the box):

1. Deploy the new code (includes `eve-trader admin bootstrap`) **but do not
   restart yet** if the running process still has the old DEFAULT-tenant
   bypass. Prefer: deploy, bootstrap, then restart. If you already restarted,
   bootstrap still works over SSH — it does not need HTTP.
2. Bootstrap yourself:

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

3. Confirm the row:

```bash
.venv/bin/eve-trader tenant list
```

4. Restart the app (`sudo systemctl restart eve-trader`). The gate stays on.
5. Log in through EVE SSO (gate login). `/api/admin/*` should return 200 for
   you and 403 for a normal user.
6. Do **not** set `access_gate_enabled: false` as recovery. If you are locked
   out of admin, re-run the bootstrap command over SSH.

## Fresh install

1. Apply schema (including `docs/admin_schema.sql` / `docs/phase3_schema.sql`).
2. Install from `requirements.lock` (see `docs/DEPENDENCY_SECURITY.md`).
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
