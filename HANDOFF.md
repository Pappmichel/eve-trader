# HANDOFF — post-merge live verification + deploy (2026-08-22)

Written 2026-08-22, remote sandbox session. All 13 previously-open PRs
(#70-#95) are now merged into `main` - this file is **not** a plan for more
code, it's a checklist of what still needs a real machine (local Windows
dev box + the live Oracle VM) to verify, since this sandbox's network
access to ESI (`esi.evetech.net`) and Goonmetrics (`appraise.gnf.lt`) is
blocked, and it has no SSH access to the deploy target at all. Delete this
file once every section below is checked off and confirmed working, per
this repo's own HANDOFF.md convention (see CLAUDE.md).

## What already happened in the remote session (context, not to redo)

- Merged, in order: #86 → #87 → #71 → #72 → #74 → #75 → #70 → #83 → #82 →
  #81 → #89 → #95. Every merge conflict was resolved and verified with
  `pytest` (843 passed against a real local Postgres), `tsc --noEmit`,
  `vite build`, `vitest`, and `oxlint` before pushing.
- Three real bugs were found and fixed along the way (not just merge
  mechanics):
  1. `error_log` was missing a `DELETE` grant for the app role -
     `storage.log_error`'s own retention pruning (added this session) would
     have failed with `permission denied` on every insert in production.
     Fixed in `docs/observability_schema.sql`.
  2. `test_per_tenant_tables_list_matches_the_real_schema` (the sqlite-
     migration drift guard) was missing a fixture dependency on
     `docs/refining_schema.sql` - would have false-failed on a genuinely
     fresh Postgres. Fixed in `tests/test_sqlite_migration_table_drift.py`.
  3. `test_read_session_token_rejects_tampered_token` failed once in CI,
     confirmed as a real flake on rerun (unrelated to any of this session's
     changes) - no fix needed, just noting it in case it recurs.
- `HANDOFF.md` (the old one, tracking #70-#95) was deleted once all of the
  above merged - this file is a fresh one for the next phase only.

## 1. Live-verify on your local Windows machine, before deploying

None of these were possible from the remote sandbox (network-blocked or
no real browser) - do them locally first:

- **Full browser walkthrough of the merged `main`**, not just the
  individual features in isolation (each was verified during its own PR,
  but never all together post-merge): start backend
  (`uvicorn eve_trader.api.main:app --port 8000`) + `npm run dev` in
  `frontend/`, click through Trading, Production, Doctrine, **Ore &
  Minerals** (new this round), Admin (new "Recent Errors" section, Quick-
  Nav ⌘K), Portfolio. Watch the browser console for errors, especially on
  first load of every page.
- **Real ESI calls** (mocked/monkeypatched everywhere in the sandbox):
  - #74's `esi_client.resolve_effective_volume` - pick a real capital
    module in Trading's Candidate Universe, confirm its landed cost/margin
    now uses packaged (not flight) volume.
  - #57's tool-gating on the real SSO login/callback flow.
  - A real ESI sync (Production "Sync ESI" / Doctrine contract sync).
- **Real Goonmetrics pricing** - Production build costs, Ore & Minerals
  Ore Shortlist pricing, Mineral Shopping List optimizer inputs, all real
  numbers instead of the sandbox's mocked ones.
- **#93's LP solver end-to-end** - run the Mineral Shopping List's
  "Optimize" against real current ore/ice prices, sanity-check the mix it
  picks looks economically reasonable (not just that the LP itself is
  internally consistent, which the unit tests already cover).
- **#92's paste parser against a real "Copy As"** from the actual EVE
  client's inventory window - the sandbox only verified against
  evepraisal's own reference format, never a genuine client clipboard
  paste.
- **`npm run build` on Windows** - the sandbox is Linux; confirm the known
  `rm -rf dist` / `EPERM` (Dropbox sync lock) Windows quirk either doesn't
  recur or is still fixed by the documented workaround (delete `dist`
  manually in Bash, rerun).
- `pytest`/`tsc`/`vitest`/`oxlint` themselves don't need re-running deliberately
  - CI (#87, now live on every push to `main`) already covers those. Worth
  a glance at the Actions tab on the next push regardless, just to confirm
  CI itself is healthy on real `main`.

## 2. Deploy steps (Oracle VM, from your local machine's SSH access)

Full procedure is `deploy/README.md` - this is only the delta for *this*
update on top of an already-running deployment, not a from-scratch setup.

**Schema (owner role, idempotent, safe to re-run) - apply what's missing
before restarting the service:**
```bash
cd ~/eve-trader
sudo -u postgres psql -d eve_trader -f docs/observability_schema.sql
sudo -u postgres psql -d eve_trader -f docs/refining_schema.sql
```
(`admin_schema.sql`/`doctrine_schema.sql` should already be applied from an
earlier deploy - re-run them too if unsure, they're idempotent. Full list
of all seven schema files is in `deploy/README.md`'s own "2b. Set up
Postgres" section.)

**New Python dependency**: `scipy` (for #93's LP solver) - already covered
by the normal `pip install -e .` below, no extra step.

**Normal update sequence** (`deploy/README.md`'s own "Updating later"
section):
```bash
cd eve-trader
git pull
.venv/bin/pip install -e .
cd frontend && npm ci && npm run build && cd ..
sudo systemctl restart eve-trader
```

**Verify after restart:**
```bash
sudo systemctl status eve-trader        # active (running)
curl -I http://<public-ip>/api/gate/status   # 200, "enabled":true
```
Then open the site in a browser and confirm login + the new Ore & Minerals
module both work against the real deployment (not just local dev).

## What's NOT expected to need attention

- No `config.yaml`/`.env` changes needed for this round - every new field
  (refining settings, error tracking) has built-in defaults and is
  configured via the Settings pages, not new required env vars.
- No data migration needed - every new table (`ore_shortlist`,
  `ore_shortlist_snapshot`, `mineral_requirements`, `error_log`) starts
  empty; nothing to backfill from the old schema.
