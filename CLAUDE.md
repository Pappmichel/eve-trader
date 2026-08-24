# CLAUDE.md

Conventions and settled decisions for working in this repo. README.md covers
setup/usage; this file covers *how the code is organized and why*, so you
don't have to re-derive it (or accidentally re-litigate a decision that was
already made deliberately) from scratch.

**If `HANDOFF.md` exists at the repo root, read it first** - it's a
temporary, self-deleting note left when a session ends mid-task (e.g.
continuing on a different computer with no access to this machine's Claude
memory) and takes priority over re-deriving current status from scratch.

## Two tools, one backend (plus Doctrine, Ore & Minerals, and Admin)

- **Trading**: buys in Jita, sells at a private player structure ("C-J").
- **Production**: Tech I/II/Reaction manufacturing planning for the same C-J
  structure - buy-vs-build, stock targets, invention.

These two are the original pair this section's title/history refers to -
`eve_trader/portfolio.py` and `eve_trader/scheduler.py` are the only modules
that deliberately span *just* them. The app has since grown two more
tenant-facing tools that follow the exact same `do_*`-actions/router/RLS
pattern described in the rest of this file: **Doctrine** (fitted-ship
contract/stockpile tracking against EFT fittings, `eve_trader/doctrine/`)
and **Ore & Minerals** (ore/ice import-refine-sell, reprocessing quotes,
mineral shopping list, `eve_trader/refining/`, GitHub issue #90) - plus the
cross-tenant **Admin** tool (`eve_trader/admin.py`, see "Tool permissions &
Admin" below), which isn't tenant-facing at all.

All four tenant-facing tools share one FastAPI backend (`eve_trader/api/`),
one Postgres store (`eve_trader/storage.py`, multi-tenant - see
"Multi-tenant Postgres" below), and one React/TypeScript frontend
(`frontend/src/`).

**Production sells only at C-J, never Jita** - this was implemented once
(a Jita-comparison feature) and explicitly reverted after confirming with the
user that freighting finished Production goods to Jita isn't part of this
tool's business model. Don't reintroduce Jita as a Production sales channel
without asking first.

**Price sources matrix** - three different price sources answer three
different questions, deliberately, not by accident, but nowhere else are
they laid out side by side:
| Question | Source | Where |
|---|---|---|
| Is a *newly discovered* candidate historically worth importing? | Goonmetrics region-average history for `reference_region_id` (Insmother) | `history_backtest.py` |
| What can I actually buy/sell *right now* on the live Trading shortlist? | Real ESI order-book stats (5th-percentile) for `jita_region_id`/C-J's own structure | `esi_client.region_order_stats`/`structure_order_stats`, via `shortlist.py` |
| What's a Production build/buy decision worth? | Goonmetrics current-price quotes (`appraise.gnf.lt`) for the configured home/Jita markets | `production/pricing.py` |
Each is the right tool for its own job (a historical region average smooths
out noise for "is this worth tracking at all", a live order-book percentile
is what you'd actually pay/receive right now) - don't assume a number from
one is interchangeable with a same-named-sounding number from another
(see Finding 2.5's discovery/live-shortlist mismatch for a concrete case
this caused).

## Architecture: actions.py is the one entry point

`eve_trader/actions.py` and `eve_trader/production/actions.py` hold every
`do_*` function - UI-agnostic, no framework imports, return plain
dicts/dataclasses. Both `cli.py` and the FastAPI routers
(`api/routers/*.py`) call the *same* `do_*` functions, so the CLI and the web
app can never drift apart. If you add a feature, the real logic goes in
`engine.py`/`pricing.py`/`storage.py`; the `do_*` action is a thin
orchestration wrapper; the router is thinner still.

Routers use a `_wrap(fn, **kwargs)` helper that catches `ActionError` and
converts it to an HTTP 400 - `ActionError` is the one user-facing error type
across the whole app. A new failure mode should raise `ActionError` (or a
narrower exception the caller converts to `ActionError`), not a raw
exception that would otherwise surface as a bare 500.

Two narrow, deliberate exceptions call something other than a `do_*`
function directly - not places where the rule was missed, but don't extend
either without the same reasoning: `api/routers/portfolio.py` calls
`portfolio.portfolio_overview()`/`scheduler.get_status()` directly, since
`portfolio.py`/`scheduler.py` are the cross-cutting modules that
deliberately span both tools (see "Two tools, one backend" above) - there's
no natural `do_*` home for either without picking one tool arbitrarily.
`cli.py`'s `tenant import-tokens`/`migrate-sqlite` commands call `storage`/
`sqlite_migration` directly - both are genuinely one-time, operator-run
commands with no web/API equivalent at all, so there's no router on the
other side of them to keep in sync with. `tenant create`/`add-entry`/`list`
are *not* this exception anymore - they call `storage.create_tenant`/
`add_tenant_registry_entry`/`list_tenants` too, but the Admin tool
(`eve_trader/admin.py`'s `do_*` functions, see "Tool permissions & Admin"
below) calls the exact same `storage.py` functions, so both paths stay in
sync by construction.

## Config: dataclasses + config.yaml, validated before applied, resolved per-tenant

`TradingConfig`/`ProductionConfig` (`eve_trader/config.py` /
`eve_trader/production/config.py`) are dataclasses with built-in defaults,
overridden by `config.yaml` at load time (the base values every tenant
starts from) and by Settings-page saves at runtime, persisted per-tenant to
Postgres's `tenant_settings` table (`save_tenant_config_overrides` -
`save_config_overrides`/YAML-writing was retired in the multi-tenant
migration's Phase 2). Both paths run `validate_config_overrides`
(type-checks every field against its declared type) - and Production
additionally runs `validate_production_overrides` (enum-checks
`*_structure_type`/`*_rig_tier` against `production/constants.py`) -
*before* anything is written or applied to the live config object, so a bad
value never lands half-applied. Raises `ConfigError`; `do_update_settings`
in both actions modules catches it and re-raises as `ActionError` (kept
separate from `ActionError` itself to avoid a circular import - `config.py`
is imported *by* `actions.py`, not the other way around).

`TRADING_CONFIG`/`PRODUCTION_CONFIG` (the names everything else imports and
reads) are `ConfigProxy` objects, not plain dataclass instances - they
forward every attribute read/write to whichever instance a `contextvars.
ContextVar` currently resolves to. `tenant_scope.enter_tenant(tenant_id)`
is what actually resolves and `.set()`s that per-request/per-job-tick (see
"Multi-tenant Postgres" below) - a bare dataclass instance would leak one
tenant's Settings-page save into every other tenant's live config, since
they'd all be reading/writing the exact same object.

If you add a new config field, it's automatically type-checked - no extra
work needed unless it's an enum-style string field, in which case add it to
`validate_production_overrides` (Production-specific checks stay out of the
shared `config.py` - that module is imported *by* `production/config.py`,
reaching back into `production/constants.py` from the shared module would be
a layering violation).

## Multi-tenant Postgres: rules for new storage/schema code

Full design history and rationale lives in `docs/MULTI_TENANT_PLAN.md`
(read this if you need the "why", not just the "what" below) - this section
is the durable, quick-reference version for anyone adding a new storage
function or table after that migration (all 5 of its phases are done).

- **`storage.DEFAULT_TENANT_ID`** (`00000000-0000-0000-0000-000000000001`,
  seeded into `tenants` by `docs/phase3_schema.sql`) is the fixed tenant
  used whenever there's no real per-request tenant to resolve: the CLI
  (`cli.py`'s `main()` sets it once per process - a trusted single
  operator, no login wall there at all) and every web request when
  `AccessConfig.access_gate_enabled` is `False` (this app's default - it
  ran for months as a single-operator tool with no login wall before the
  multi-tenant migration). A real per-tenant login (gate enabled) resolves
  and uses a different, real `tenant_id` from the registry instead.
- **One tenant per character, enforced at the DB level.**
  `tenant_registry_entries.tenant_id` has a `UNIQUE` constraint
  (`docs/admin_schema.sql`) - no two characters can ever share a tenant, by
  DB guarantee, not just UI convention. The Admin tool's "Add User"
  (`admin.do_add_user`) reflects this: it always creates a brand-new
  tenant (named after the character's own ESI-resolved name) as part of
  adding a user, there's no "assign to an existing tenant" flow anymore -
  `admin.do_create_tenant`/the standalone "Create Tenant" UI were removed
  for the same reason. `admin.do_remove_user` deliberately does *not*
  delete the now-permanently-orphaned tenant or its data - it just
  deregisters the character, leaving the tenant's data intact/recoverable
  (matches this app's general preference for reversible over destructive
  admin operations).
- **Two Postgres roles, never conflate them.** `eve_trader_app`
  (`storage.PG_DSN`) is what the app itself connects as for every real
  query - `NOSUPERUSER NOBYPASSRLS`, so it can *never* accidentally see
  another tenant's rows even from a bug, only from Postgres itself
  correctly refusing. `postgres` (the owner role) is used *only* for schema
  DDL (`docs/phase*_schema.sql`, applied by hand/CI, never by the running
  app) and `backup.py`'s `pg_dump` (a whole-database dump necessarily
  bypasses RLS - see "Backup" above). Never give the app role `BYPASSRLS`
  or run app queries as the owner "to make something easier" - that defeats
  the entire isolation guarantee this migration exists for.
- **Every real query goes through `storage.connect()`/`storage.
  batch_session()`.** These are the only two places that check out a pooled
  connection and set `app.tenant_id` (via `SELECT set_config(...)`, the
  parameterized equivalent of `SET LOCAL` - see `connect()`'s own
  docstring for why not literal `SET LOCAL`) from the ambient
  `contextvars.ContextVar`, fail-closed (`RuntimeError`) if none is set.
  Never open a raw `psycopg`/`psycopg_pool` connection anywhere else in
  `storage.py` or elsewhere in the app - doing so would silently bypass
  this entirely, and nothing would catch it (Postgres itself only enforces
  RLS based on `app.tenant_id` actually being set on *that* connection).
  `storage.connect_unscoped()` is a narrow, deliberate exception for the
  handful of genuinely tenant-independent tables (`tenants`,
  `tenant_registry_entries`) - don't reach for it for anything else.
- **Adding a new per-tenant table** needs, in its `CREATE TABLE`: a
  `tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id',
  false)::uuid` column, `ENABLE ROW LEVEL SECURITY`, and a
  `tenant_isolation` policy (`USING`/`WITH CHECK` both comparing
  `tenant_id = current_setting('app.tenant_id', false)::uuid`) - copy the
  shape of any existing table in `docs/phase1_schema.sql`, don't write it
  from scratch. Decide whether its primary key needs widening to
  `(tenant_id, ...)` - only if the "natural" key (without tenant_id) could
  plausibly collide across tenants (an EVE item type ID, a literal
  scope/category string) - see that same file's "composite-PK bucket" vs.
  "column-only bucket" comment banners for real examples of each.
- **`tenant_scope.enter_tenant(tenant_id)`** is the one place that resolves
  a tenant fully - storage's own ambient tenant *and* `TRADING_CONFIG`/
  `PRODUCTION_CONFIG`'s per-tenant instance together. Used by
  `AccessGateMiddleware` (only its gate-*enabled* branch - see
  `api/app.py`'s own comment for why the gate-disabled default path
  deliberately stays on the cheaper `storage.set_current_tenant`-only path)
  and `scheduler.py`'s per-tenant job iteration. If you add a new place
  that needs to "become" a specific tenant for a stretch of code (a new
  background job, a new admin script), use this, not a bare
  `storage.set_current_tenant` - a table added in the future might need its
  config resolved too, and this is the one chokepoint that stays correct
  automatically.
- **Connection pool sizing**: `storage._get_pool()` opens one
  `psycopg_pool.ConnectionPool(PG_DSN, min_size=1, max_size=10)` per
  process, shared across every request/job. 10 is a Phase-1-era default,
  not the result of load testing - this app's traffic today is a handful of
  invite-only tenants, well under that. If concurrent load ever routinely
  saturates it (connections queuing, visible as request latency spikes
  under concurrent Trading/Production dashboard use), raise `max_size`
  first before anything more invasive - don't raise `min_size` preemptively,
  it only trades connection-pool cold-start latency for idle-connection
  overhead on Postgres's side, not something to guess at without a measured
  problem.
- **`ThreadPoolExecutor`/raw `threading.Thread` don't inherit contextvars**
  from the thread that spawned them (confirmed live, twice, in this
  migration - `production/esi_sync.py`'s `sync_esi` and `esi_client.py`'s
  `_get_all_pages`, both fixed via `storage.with_current_tenant(fn)`). If
  you parallelize anything that might transitively touch `storage.py`
  (directly or via `TokenManager`'s lazy-refresh path), wrap the submitted
  callable in `storage.with_current_tenant(...)` - don't assume the ambient
  tenant "just carries over" into a worker thread.

## Tool permissions & Admin

Two independent authorization layers, don't conflate them: **tenant_id**
(RLS, "whose data") and **tool grants** (`tool_grants` table, "which tools
can this specific character see/use"). A valid access-gate session only
proves *who* - `AccessGateMiddleware` (`api/app.py`) is what actually
enforces the second layer, via `access_gate.tools_for(tenant_id,
character_id)` checked against a path-prefix-to-`tool_key` map
(`_TOOL_PATH_PREFIXES`) before `call_next`. `/api/gate/status`'s own
`tools` field (via the same `tools_for`, one chokepoint so the two can never
disagree) is *informational only* - it's what `Landing.tsx` uses to decide
which cards to render, but hiding a card there does not, by itself, block a
direct API call; the middleware is the actual enforcement point. Both are
no-ops while `AccessConfig.access_gate_enabled` is `False` (this app's
default) - every tool is visible/usable, matching the pre-tool-grants
behavior for local/trusted-single-operator installs.

`tool_grants` (`character_id, tool_key, tenant_id`) is deliberately **not
RLS-scoped**, same reasoning as `tenants`/`tenant_registry_entries`
(`docs/phase3_schema.sql`) - queried via `storage.connect_unscoped()`. The
Admin tool (`eve_trader/admin.py`'s `do_*` functions, `api/routers/
admin.py`, tool_key `"admin"`) is a deliberate **cross-tenant superadmin**
surface, not a per-tenant self-service page: `storage.DEFAULT_TENANT_ID`'s
own users get every tool (including `"admin"`) automatically, with no
`tool_grants` row needed (`access_gate.tools_for`'s own bypass) - the same
"the operator is special, not just another tenant" pattern already used for
the scheduler/backup job. No other tenant can reach `/admin` at all unless
explicitly granted that tool_key by a Default-tenant admin.

`AccessGate` is character-only (`tenant_registry_entries.entry_type`
CHECK-constrained to `'character'`, `docs/admin_schema.sql`) - corp/alliance
registry entries were retired once tool-level permissions made "any
character in this corp/alliance gets full access" too coarse. Before
narrowing that constraint on a real deployment, check for existing
corp/alliance rows first (`SELECT entry_type, count(*) FROM
tenant_registry_entries GROUP BY entry_type`) - narrowing without migrating
them first locks those characters out.

## Testing conventions

- Router tests (`tests/test_api_routers.py`) monkeypatch the already-imported
  `actions`/`production_actions`/`portfolio`/`scheduler` **module objects**,
  not individual functions - this only works because every router does
  `from ... import actions` (module-level import), never
  `from .actions import do_thing`. If you add a router that imports
  differently, its tests need a different monkeypatch target.
- Any function with a module-level cache (see `discover_build_candidates`
  below) needs an autouse fixture resetting it between tests, or later tests
  will silently reuse an earlier test's monkeypatched result.
- Full suite: `pytest` from the repo root. Keep it green before calling
  anything done - it currently runs in a few seconds, there's no excuse to
  skip it.

## "Live-verify before declaring done" discipline

Passing unit tests is necessary but not sufficient. Before calling a
backend change done, hit the real running endpoint (`Invoke-RestMethod`/
`Invoke-WebRequest` against `localhost:8000`) and read the actual response -
not just the mocked unit-test path. Before calling a frontend change done,
load it in a real browser (Playwright via a throwaway `_verify_*.mjs`
script - screenshot it, check console/network errors, then delete the
script and screenshot afterward; don't leave verification artifacts in the
repo). This caught real bugs during development (e.g. a settings save that
looked fine in isolated unit tests but needed checking against the actual
Pydantic request-validation layer to know whether the new backend
validation was even reachable via HTTP).

## Caching pattern

The shared idea everywhere that caches an expensive call: a plain
`time.time()`-based TTL, no external caching library - but it's grown three
slightly different shapes as new needs came up, not one uniform pattern:
- `esi_client.py`'s `ESIClient` uses **class-level** attributes
  (`_adjusted_prices_cache`/`_adjusted_prices_cache_at`, etc, `clear_price_caches()`
  to reset for tests) - deliberately class-wide, not per-instance, since a
  fresh `ESIClient()` is constructed per call/request elsewhere in this
  codebase, so an instance-level cache would never actually hit.
- `goonmetrics_client.py`'s `current_prices` uses **module-level dicts**
  (`_prices_cache`/`_prices_cache_at`, keyed by market) plus a **per-market**
  lock (`_prices_locks`) - narrower than a single shared lock so concurrent
  requests for two different markets don't serialize behind one one lock held
  for the whole (multi-second) fetch.
- `discover_build_candidates` (`production/engine.py`) uses a module-level
  value (`_discover_cache`/`_discover_cache_at`) behind **one single lock**
  covering the whole cached computation, plus explicit invalidation
  (`invalidate_discover_cache()`) from every action that actually changes its
  result set (Settings save, stock target add/remove, decryptor change, SDE
  refresh) - TTL alone would let a just-changed Setting serve stale results
  for the rest of the TTL window, a real correctness problem, not just a
  performance one.

Each shape exists for a reason specific to its own caller (see above) - when
adding a new cache, match whichever of these three actually fits your
situation (per-instance-never-hits -> class-level; multiple independent
cache keys -> per-key lock; one whole-computation cache that must never
serve stale-after-a-write results -> single lock + explicit invalidation)
rather than assuming there's one canonical "the" pattern to copy.

## Scheduler

`eve_trader/scheduler.py` is a stdlib-only (`threading`, no APScheduler)
background daemon thread, started from `api/app.py`'s FastAPI lifespan.
Whether it starts at all is an operator-level decision, read once at boot
from `DEFAULT_TENANT_ID`'s own `TradingConfig.scheduler_enabled` (**off by
default**) - see "Multi-tenant Postgres" below for what `DEFAULT_TENANT_ID`
means. Each tick, `trading_pipeline`/`production_sync` run once **per
tenant** (`storage.list_tenants()`, each fully scoped via `tenant_scope.
enter_tenant`) - a tenant's own `scheduler_enabled`/interval fields decide
independently whether *their* jobs run that tick, via
`_check_and_run_due_jobs_for_tenant`. This does mean a real second tenant
who flips their own `scheduler_enabled` on (Settings page) is silently a
no-op the entire background thread never even starts, and so never reaches
that tenant's per-tick check, unless `DEFAULT_TENANT_ID` (the operator)
*also* has theirs on. Semantically this field is closer to a global
deploy-level switch than a genuine per-tenant setting - worth knowing before
inviting a second tenant. `backup` stays a single **global**,
unscoped job (`_check_and_run_backup_job`) - one `pg_dump` already covers
every tenant's data in one shot, nothing to iterate; its own interval/
enabled check reads `DEFAULT_TENANT_ID`'s config, same operator-level
reasoning as the thread's own on/off switch.

Both per-tenant jobs reuse an existing "when did this last happen" source
instead of separate scheduler-specific persistence: `storage.esi_sync_state`
(already written by `do_pipeline`/`do_sync_esi`). The backup job reuses the
newest backup file's own mtime (`backup.list_backups()`). A manual run/
backup from the UI correctly counts either way and pushes back the next
scheduled one. `last_run_status` is `{tenant_id: {job_name: {...}}}` for the
two per-tenant jobs; a separate `_backup_status` (not tenant-keyed) covers
the global one. Adding a fourth *per-tenant* scheduled job means adding one
interval field to `TradingConfig` (plus a `_FIELD_RANGES` entry, `(0, None)`,
in `config.py`) and one `if _hours_since(...) >= cfg.x: _run_job(tenant_id,
...)` line in `_check_and_run_due_jobs_for_tenant` - no other wiring needed.

## Backup

`eve_trader/backup.py`'s `create_backup()` zips a `pg_dump` (`-Fc`, custom/
compressed format - restorable via `pg_restore`) of the whole Postgres
database plus `config.yaml` into a timestamped `.zip` under `data/backups/`,
pruning down to `MAX_BACKUPS` (14) automatically. `data/tokens.json` is
**not** included - `TokenManager` persists to Postgres's `tenant_tokens`
table now, so the live tokens are already inside the dump; a separate,
possibly-stale file copy would be actively misleading on a restore. Shells
out to `docker exec <container> pg_dump -U postgres ...` - must run as the
Postgres *owner* role (`postgres`), not the app's own `eve_trader_app` role,
since RLS raises on a missing tenant setting rather than silently returning
zero rows (see "Multi-tenant Postgres" below) - a non-bypassing role
couldn't dump per-tenant tables at all. `EVE_TRADER_PG_CONTAINER`/
`EVE_TRADER_DOCKER_BIN` env vars override the container name/`docker`
binary path (default `"eve-trader-pg"`/`"docker"`). Reachable two ways: the
"Backup Now" button on the Portfolio page (always available), and the
scheduler's own global backup job (see above, opt-in via
`DEFAULT_TENANT_ID`'s `backup_interval_hours`).

## "Theoretical ceiling" figures - not bugs

`potential_daily_profit` (Production's Build Candidates) and "Profit / Day"
(Trading's Shortlist) are deliberately `profit_per_unit x <a volume figure>`
- not a claim about what one seller could personally capture in a day. A
tiny-volume, huge-per-unit item (a capital hull, a faction module) can show
an enormous number - that's mathematically correct for "what's the whole
market worth," confirmed deliberate with the user after live-testing
surfaced exactly this case. Don't cap, filter, or "fix" the multiplication
itself without asking first.

**The volume figure must be a real turnover estimate, never order-book
depth, and never scoped to just one trader's own sales** - two real bugs,
not just a labeling issue:
- GitHub issue #51 (2026-08-21): Trading's Shortlist "Profit / Day" used to
  be `profit_per_unit x sell_volume`, where `sell_volume`
  (`esi_client._summarize_orders`) is the sum of `volume_remain` across
  every currently open sell order at the structure - i.e. "how much is
  listed for sale right now," a live order-book-depth snapshot, not actual
  daily traded volume. A single seller parking a large batch of a
  never-actually-sold item produced a wildly inflated "Profit / Day" purely
  from that listed quantity. #51 first fixed this by switching to the
  trader's own realized-sales average (`trade_reconciliation.
  average_daily_sold_by_type`, from the last Reconcile Trades run's
  matched sales) - a real fix for the order-book-depth bug, but it
  overcorrected: it left "Profit / Day" empty for every not-yet-sold-by-me
  candidate, which is most of what a shortlist exists to evaluate.
- GitHub issue #100 (2026-08-23): replaced that with `ShortlistRow.
  avg_daily_volume` - real average daily *market-wide* traded quantity,
  from Goonmetrics region history for `cfg.reference_region_id` (the real
  region C-J's own solar system sits in, confirmed live via
  `sde_solar_systems` - there's no ESI/Goonmetrics history endpoint for a
  player structure's own market at all, so region-wide is the closest real
  signal available), computed by `shortlist.average_market_daily_volume`
  (averages `HistoryPoint.movement` over Goonmetrics' own ~28-day return
  window) - `None` (shown as "–", excluded from Top Imports) until
  Goonmetrics has history for that item in that region, rather than
  estimated from something else. Exactly mirrors Production's own
  `potential_daily_profit`/`daily_movement` (`production/engine.py`),
  which already used this same `reference_region_id` + `price_history_
  chunked` pattern and was never affected by either bug. The `sell_volume`
  field itself is unchanged and still legitimately shown as "Listed Qty"
  (own column) - it's just never used for the Profit/Day multiplication.

## Real SDE data drives classification, not heuristics

Item categorization (`classify_activity` in `production/engine.py`) uses
real SDE fields (`meta_group_id` for the "Faction"/"Officer"/"Storyline"/
"Deadspace" categories - meta_group_id 4/5/3/6 respectively, all mapped via
one `meta_group_labels` dict at the end of `classify_activity` - and
invention-recipe lookups for Tech II) rather than guessing from name
patterns or `metaLevel` alone - a past bug (Machariel/Nestor miscategorized
as Tech II) came from exactly that kind of heuristic (`metaLevel >= 2`
catching Faction ships too). All four of these meta-group labels share the
same ME0/TE0, non-researchable treatment (`constants.ACTIVITY_MODS`) - none
of them get Tech I's owned-BPO-preference/research-baseline treatment (see
`_activity_mods`). When adding another classification category, prefer an
SDE column already fetched by `production/sde.py`'s `refresh_sde()` over a
new heuristic; if the SDE doesn't already carry the field you need, extend
`refresh_sde()` to fetch it (see the `invMetaTypes.csv` merge that added
`meta_group_id` for precedent) rather than approximating.

## Environment specifics

- **Git repository, GitHub remote.** `git init` + first commit + a GitHub
  remote (`origin`, https://github.com/Pappmichel/eve-trader, currently
  private) were set up 2026-08-16 as part of preparing the project for
  publication - this repo is no longer "no VCS safety net." Workflow: commit
  locally after completed work without asking (cheap, reversible, purely
  local); never `git push` without the user explicitly asking for it in that
  turn, since that's what actually publishes to the shared remote.
- Windows 11 / PowerShell. Backend: `uvicorn eve_trader.api.main:app --port
  8000` (no `--reload` in the usual dev setup here - restart manually after
  backend changes, e.g. `Stop-Process -Id <pid> -Force` then relaunch).
  Frontend: Vite dev server on `:5173`.
- `config.yaml` is hand-maintained and not under version control - never
  overwrite it with synthetic test data; tests use their own
  `TradingConfig()`/`ProductionConfig()` instances, not the real file, and
  any live HTTP verification against `/settings` should restore/no-op the
  real values afterward.
- **No Claude/Anthropic attribution anywhere (confirmed 2026-08-24).** Never
  add a `Co-Authored-By: Claude ...` or `Claude-Session: ...` line to a
  commit message, and never add a "Generated by/with Claude Code" footer (or
  any session link) to a PR description, issue, or comment - regardless of
  what a tool's own default template suggests appending. This applies on
  every machine/session, not just the one this was confirmed on. The repo's
  full history and every PR description were already scrubbed of these once
  (2026-08-24, ahead of making the repo public) - don't reintroduce the
  pattern going forward.
- **User preference (confirmed 2026-08-22): delegate to an Opus subagent
  without asking first** whenever a task/sub-task is algorithmically heavy
  (e.g. a real optimization/LP formulation, a tricky correctness-critical
  calculation) rather than defaulting to whatever model the session itself
  is running on - report the delegation after the fact, don't ask
  permission each time. First applied to GitHub issue #93 (the Mineral
  Shopping List's `scipy.optimize.linprog` solver). This is a
  cross-session behavioral preference, not something specific to this
  repo's own conventions - it's recorded here only because no
  guaranteed-persistent cross-session memory mechanism was available
  when this was confirmed; if a real one exists by the time you're
  reading this, prefer that.

## Deferred, not rejected

Contract-Scanner, Discord alerts, and a PI (Planetary Interaction) calculator
were explicitly discussed and deferred (not rejected) as of 2026-07-14 -
they're legitimate future scope, just not started. Don't start on these
without asking first.

A full codebase audit (2026-08-18) turned up four more low-priority items,
deliberately left unfixed at the time (everything else the audit found -
critical/important bugs, nice-to-haves, architecture docs, EVE-mechanic
corrections, README - was fixed and deployed the same day). All four were
later closed out: `candidate_discovery.py`'s SDE-crawl path using raw flight
volume for capital-sized modules turned out to be a real bug, not
"practically harmless" (fixed, GitHub issue #73); the `sqlite_migration.py`
drift-guard test was implemented as part of GitHub issue #60; the other two
(a Goonmetrics full-market-dump re-fetch in `portfolio.py`'s
`portfolio_overview`, and `production/invention.py` assuming one global
datacore-skill pair instead of per-blueprint pairs) were reviewed again and
confirmed to have no actionable fix worth tracking here - the former has no
alternative Goonmetrics endpoint to switch to, the latter would need new SDE
skill-requirement data plus a live ESI character-skills pull, real new
feature scope rather than a bug.

## Windows packaging lives in a sibling repo

`../eve_trader_electron` (a sibling of this directory, **not** a
subdirectory of it) is a separate, already-packaged copy of this app -
PyInstaller-bundled backend (`backend_entry.py`, `eve-trader-backend.spec`,
`build_backend/`, `dist_backend/`) plus an Electron shell, last touched
2026-07-14. It is not kept in continuous sync with this repo - treat it as
its own project, not a build target of this one, unless the user says
otherwise. Do not assume packaging work is unstarted or "deferred until
feature-complete" - that framing was true earlier in this project's history
but is now stale; the packaging already happened once, over there.
