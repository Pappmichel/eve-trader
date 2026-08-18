# Multi-Tenant SaaS Migration for eve_trader

> Durable, repo-tracked copy of the approved architecture plan (originally drafted in a
> Claude Code planning session, machine-local plan files under `.claude/plans/` don't
> survive a machine switch - this file is the version of record). See `HANDOFF.md` at the
> repo root for the current progress checkpoint if one exists.

## Context

`eve_trader` is currently single-tenant: one SQLite DB (`data/eve_trader.db`), one
`config.yaml`, one `data/tokens.json` per deployment. The goal is to let multiple
independent users each run their own isolated Trading/Production operation (own
buyer/seller/producer characters, own shortlist/stock targets/config) on one shared
deployment, with zero cross-tenant data visibility.

Two decisions are already made with the user (do not re-litigate):
1. **Provisioning is invite-only**, via an admin-run CLI - no public self-service signup.
2. **Data storage moves off SQLite to Postgres**, with tenant isolation enforced via a
   `tenant_id` column - not a SQLite-file-per-tenant-directory approach.

A Plan-agent architecture review (already run) validated the core design, found one
serious flaw the initial proposal missed (see "Composite primary keys" below), and refined
the phasing. This plan reflects that reviewed design, not the original proposal. Every
specific claim below (table/column names, line numbers, upsert-site count) has been
re-verified directly against the current `storage.py`/`config.py`/`access_gate.py` source,
not just taken from the review - one imprecision in the review was caught and corrected
in the process (see "Composite primary keys" below).

**Constraint, explicitly requested**: the currently running deployment (the live Oracle VM,
single-tenant SQLite) must not be touched or put at risk while this is built. All work
happens on a dedicated git branch (`multi-tenant`), developed and verified against a real
Postgres, never against the live VM's data. `main` and the running deployment stay
exactly as they are - no push (of `main`), no deploy, no cutover - until the user separately
decides to go live with the new system. Cutover planning (how/when to actually migrate the
live deployment) is explicitly out of scope for this plan; it's a future decision point once
the parallel system is ready.

## Architecture

### Tenant isolation: Postgres Row-Level Security (RLS), not manual `WHERE` filtering

Every per-tenant table gets a `tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid`
column, `ENABLE ROW LEVEL SECURITY`, and a policy `USING (tenant_id = current_setting('app.tenant_id', false)::uuid)`.
The `false` (not the two-arg `missing_ok=true` form) makes a forgotten `SET LOCAL` a hard
error instead of a silent NULL. The app connects as a non-owner, non-`BYPASSRLS` role;
migrations/DDL run as a separate owner role - this way RLS is never silently skipped.

This means **no individual query in `storage.py` needs a manual tenant filter** - Postgres
enforces it structurally, which is both less work and safer than hand-adding `WHERE
tenant_id = ...` to ~100+ queries (one missed clause there would be a real data leak).

The only place that needs to know about `tenant_id` at all is the existing connection
chokepoint: `connect()`/`batch_session()` in `eve_trader/storage.py:532-615`. Every one of
the ~100 storage functions already funnels through exactly these two context managers
(confirmed: no other `sqlite3.connect()` call exists in the codebase except `backup.py`'s
own online-backup snapshot). Port them to check out from a `psycopg_pool.ConnectionPool`
and run `SET LOCAL app.tenant_id = %s` (sourced from a `contextvars.ContextVar`, set once
per request by `AccessGateMiddleware`) as the first statement of every transaction.

### Composite primary keys - the flaw RLS alone doesn't fix (and a correction to the review)

RLS governs which rows a session can *see*, but Postgres enforces uniqueness at the index
level, below RLS. Directly re-checking every `CREATE TABLE` in `storage.py:29-475` against
what each PK actually identifies found two different situations, not one - the review
treated them as the same risk, which they aren't:

- **Real, near-certain-to-guaranteed collision** - the PK is an app-level or literal value
  that's naturally reused across tenants: `stock_targets.type_id`, `manual_stock.type_id`,
  `manual_build_buy.type_id`, `selected_decryptors.type_id`, `shortlist.item_id`,
  `shortlist_skip_streak.item_id` (all EVE item *type* IDs, e.g. Tritanium=34, the same
  value for every tenant who tracks that item), `job_category_locations.category` and
  `esi_sync_state.scope` (literal strings like "Reactions"/"trading" - identical for every
  tenant by construction), `candidate_search_cursor.id` (`CHECK (id = 1)` - every tenant has
  exactly this one row), and `structure_names.location_id`/`category_location_options`'s
  `location_id` (collide whenever two tenants build at the same public NPC station).
  **These 10 tables/upsert-sites genuinely need the PK widened to composite
  `(tenant_id, <original_pk>)`**, confirmed against the actual `ON CONFLICT(...)` call
  sites: `storage.py:637, 674, 948, 973, 989, 1009, 1029, 1111, 1346, 1363`.
- **No real collision risk** - `character_assets`/`corp_assets`/`character_blueprints`/
  `corp_blueprints` (`item_id`), `character_industry_jobs`/`corp_industry_jobs` (`job_id`),
  `character_sell_orders` (`order_id`), and `character_slots` (`character_name`) all key off
  IDs that EVE itself already guarantees are globally unique across the entire game (ESI
  assigns `item_id`/`job_id`/`order_id` server-side per real object instance, never
  per-character-reused; EVE enforces character names unique game-wide - two tenants
  literally cannot both have a character named the same thing). These 8 tables still need
  the `tenant_id` column added (for RLS row-visibility), but **not** composite-PK surgery -
  adding it anyway is a cheap, harmless defensive choice, not a correctness requirement.

(The remaining `ON CONFLICT` sites - `storage.py:746` `goonmetrics_history` and `:828`
`sde_refresh_state` and `:1518` `type_packaged_volume` - are shared tables, see below, and
need no tenant_id/PK change at all. 13 upsert statements total in `storage.py`: 12
`ON CONFLICT` + 1 `INSERT OR REPLACE`, 10 need widening, 3 don't apply.)

Phase 0 must prove out the composite-PK case specifically (pick a table from the first
bucket, not the second) - not discovered later after Phase 1 has already replicated the gap
across every table.

### Shared vs. per-tenant tables

12 tables are EVE static game data (SDE), identical for every tenant, refreshed once
globally - no `tenant_id`, no RLS: `sde_types, type_packaged_volume, sde_groups,
sde_categories, sde_market_groups, sde_blueprint_time, sde_blueprint_materials,
sde_blueprint_products, sde_invention_probability, sde_solar_systems, sde_stations,
sde_refresh_state`.

`goonmetrics_history` also stays **shared**, not per-tenant - its PK is already
`(region_id, type_id, date)`, and duplicating it per tenant would mean every tenant
re-fetching and storing identical public Jita price history for no benefit.

The remaining ~24 tables (`shortlist`, `stock_targets`, `character_assets`,
`character_industry_jobs`, `realized_trades`, etc.) are genuinely per-tenant and get the
`tenant_id`/RLS/composite-PK treatment (see the two buckets above for which ones need the
composite PK vs. just the column).

### Config and secrets move from files into Postgres too

Invite-only tenants have no filesystem/SSH access to hand-edit `config.yaml` the way the
single original operator does today, so `TradingConfig`/`ProductionConfig` values move
into a `tenant_settings` table and OAuth tokens (currently `data/tokens.json`) move into a
`tenant_tokens` table. The existing Settings-page UI (`do_update_settings`) already
provides the edit path - only its persistence backend changes. `AccessConfig` (the
character/corp/alliance allowlist) stays operator-only, **not** in `tenant_settings` -
it becomes the new tenant registry instead (see Phase 3).

`TRADING_CONFIG`/`PRODUCTION_CONFIG` are read as bare module-level globals throughout
`engine.py`/`pricing.py`/`actions.py`/every router (via `cfg: TradingConfig = TRADING_CONFIG`
default parameters - evaluated once at import time, so reassigning the global later would
NOT be seen by already-defined functions). Fix: a `contextvars`-backed proxy object
(Flask `current_app`/`g`-style) that forwards attribute access to the current request's
resolved config instance - existing `from .config import TRADING_CONFIG` imports elsewhere
need zero changes.

**Known break to fix as part of this**: `config.py:172`'s `validate_config_overrides` calls
`typing.get_type_hints(type(cfg))` - under the proxy, `type(cfg)` returns the proxy's own
class, not `TradingConfig`/`ProductionConfig`, silently breaking Settings-page type/range
validation. Fix by having the caller pass the concrete dataclass type explicitly instead of
deriving it from `type(cfg)`.

### OAuth callback has no tenant context - a gap beyond "extend the session cookie"

`/api/auth/callback` (`api/routers/auth.py`) is deliberately exempt from
`AccessGateMiddleware` (it's mid-redirect from EVE SSO, which can't carry your app's
cookie). It handles both the gate identity-login AND the real buyer/seller/producer
authorization. Once tenant-scoped token storage exists, `/callback` has no way to know
which tenant a buyer/seller/producer authorization belongs to. Fix: `/api/auth/{role}/start`
*does* still run behind a normal gated session - read `tenant_id` there and stash it in the
existing `_pending[state]` dict alongside `role_prefix`; `/callback` recovers it from there.

## Phased implementation

**Phase 0 status: done, live-verified against a real local Postgres (Docker
`eve-trader-pg`).** `eve_trader/pg_tenant.py` implements the pool/contextvar/
`SET LOCAL`-equivalent/placeholder-shim; `tests/test_pg_tenant_isolation.py`
(4 tests, all passing) proves cross-tenant isolation, the widened
`ON CONFLICT(tenant_id, type_id)` upsert, and the fail-closed "no tenant set"
error - through the real module, not just raw `psql`. Two real dialect gotchas
were caught by actually running Phase 0 (not discoverable by reading the
code); two more turned up during Phase 1's schema work and cutover (see
below) and are recorded here too, since all four are the same category of
"SQLite never enforced/offered this, Postgres does it differently" surprise:
1. `SET LOCAL x = %s` does not accept a bound parameter - Postgres rejects it
   (`syntax error at or near "$1"`). Fixed by using
   `SELECT set_config('app.tenant_id', %s, true)` instead (a plain function
   call, `is_local=true` is the parameterized equivalent of `SET LOCAL`).
2. `CASE WHEN ? IS NULL THEN ... END` fails with `could not determine data
   type of parameter` whenever the bound value is actually `None` - Postgres
   can't infer a parameter's type from an `IS NULL` check alone (SQLite never
   required this). Fix: cast the parameter explicitly, e.g. `?::real IS NULL`.
   Confirmed only one call site has this exact shape today
   (`storage.py:949`), but watch for the same pattern while porting Phase 1's
   other upserts.
3. Postgres `INTEGER` is a strict 32-bit type (max ~2.1 billion) - SQLite's
   `INTEGER` never enforced any width, so `storage.py`'s schema never needed
   to distinguish "small EVE type/group/category ID" (always well under 2^31)
   from "real ESI object ID" (`item_id`/`job_id`/`order_id`/`location_id` -
   player-structure and modern-asset IDs routinely exceed 2^31). Confirmed
   live: `docs/phase1_schema.sql`'s first draft used `INTEGER` everywhere and
   `NumericValueOutOfRange` immediately failed a `job_category_locations`
   test using a realistic structure ID. Fixed by widening every
   `item_id`/`job_id`/`order_id`/`location_id`/`output_location_id`/
   `installer_id` column (character_assets, corp_assets,
   character/corp_industry_jobs, character/corp_blueprints,
   character_sell_orders, job_category_locations, structure_names,
   category_location_options) to `BIGINT` - watch for the same pattern on any
   column holding a raw ESI object ID rather than an EVE type-scale ID. Same
   gotcha resurfaced later in test *data*, not schema: a test reused a
   BIGINT-scale fake structure ID as `sde_stations.station_id` (a real
   column, correctly INTEGER - real NPC station ids are always small) -
   fixed in the test, not the schema (see Phase 1 below).
4. psycopg3's `Connection` object has no `executemany()` (only its `Cursor`
   does) - `sqlite3.Connection.executemany()` is a shorthand SQLite offers
   that Postgres's driver doesn't. Confirmed live:
   `AttributeError: 'Connection' object has no attribute 'executemany'`, the
   first time a real `storage.py` write (`upsert_shortlist`, called via
   `conn.executemany(...)` exactly as every other multi-row write in the
   file does) ran against Postgres end to end. Fixed once, centrally: added
   an `executemany` method to `storage.py`'s `_TranslatingConnection`
   wrapper (mirroring its existing `execute`) rather than touching each of
   the ~15 call sites - every one of them keeps calling
   `conn.executemany(...)` unchanged.

**Phase 1 status: done.** `docs/phase1_schema.sql` covers all 37 tables;
every one of the ~24 per-tenant tables has a passing isolation test
(`tests/test_pg_tenant_isolation.py`, `test_pg_composite_pk_tables.py`,
`test_pg_column_only_and_no_pk_tables.py` - 27 tests total). `pg_tenant.py`
has been merged into `storage.py` itself and deleted - `storage.py`'s
`connect()`/`batch_session()` are now the real pool/contextvar/`SET
LOCAL`-equivalent/placeholder-shim mechanism (no more SQLite fallback, no
more `db_path` parameter anywhere in the file - `DB_PATH` itself survives
only as a legacy constant for `backup.py`'s still-unconverted direct-SQLite
online-backup API, explicitly deferred to Phase 4). All ~78 storage
functions had `db_path` stripped from their signatures; the 11
composite-PK-bucket `ON CONFLICT` targets were widened in the real
functions (not just the tests proving the pattern); 2 SQLite-only
`INSERT OR REPLACE`/`INSERT OR IGNORE` statements were rewritten to
`ON CONFLICT ... DO UPDATE`/`DO NOTHING`; 7 bare positional
`INSERT INTO table VALUES (...)` sites for per-tenant tables got explicit
column lists (needed once `tenant_id` became a real leading column); the 2
`pd.read_sql_query(..., conn)` sites were replaced with manual
`cursor.fetchall()` + `pd.DataFrame(...)`.

Two further things learned only by actually running the full test suite
against the real cutover (not discoverable by reading the code):
- Column-only-bucket tables (PK never tenant_id-widened, by design) don't
  get isolation for free from a fresh tenant_id the way composite-PK-bucket
  tables do - a test using a hardcoded id like `item_id=1` can still hit a
  physical PK collision with another test's row under a *different* tenant,
  since RLS hides the row from queries but doesn't relax the PK constraint.
  Fixed with `tests/pg_helpers.wipe_tables()` (an owner-role, cross-tenant
  `DELETE FROM`) as an autouse fixture in the affected test files.
- `@storage.with_batch_session()` (used by `plan_production`,
  `plan_asset_optimized`, `discover_build_candidates`) and any *uncached*
  `@lru_cache`'d SDE lookup (e.g. `get_system_security`, called
  unconditionally by `_security_multiplier_for` for every activity)
  unconditionally open a real pooled connection - even in tests that
  monkeypatch every individual `storage.*` call, if the code path reaches
  one of these it still needs a real tenant + reachable Postgres. This
  pulled several dozen previously Postgres-independent tests (in
  `test_production_engine.py`, `test_production_unlisted_stock.py`) into
  needing `tests/pg_helpers.tenant`/`postgres_required()` too - not
  something the original `db_path`/`tmp_path` text grep could have found,
  since these tests never touched `db_path` at all.

`storage.py` now requires an ambient tenant_id (fail-closed) for every real
query - **the live app itself is deliberately left non-functional by this**
(confirmed live: hitting a real endpoint returns HTTP 500 with
`RuntimeError: ... no current tenant set`, not silently wrong data) until
Phase 3 wires up real tenant resolution. Agreed explicitly with the user
before starting Phase 1's cutover: no placeholder default-tenant stopgap -
only the test suite (which sets its own tenant per test) needs to stay
green in the meantime.

**Phase 2 status: done.** `docs/phase2_schema.sql` adds `tenant_settings`
(`tenant_id`, `scope` - `'trading'`/`'production'`, `overrides JSONB`) and
`tenant_tokens` (schema only - see below). `eve_trader/config.py` gained a
`ConfigProxy` class (`__getattr__`/`__setattr__` forwarding to a
`contextvars.ContextVar`'s current value) - `TRADING_CONFIG`/
`PRODUCTION_CONFIG` are now proxies, not plain dataclass instances, so
every one of the 92 `cfg: TradingConfig = TRADING_CONFIG`-style call sites
across 20 files needed **zero changes** (confirmed live: both files import
cleanly, `TRADING_CONFIG.jita_region_id` reads correctly, and a real GET
`/api/trading/settings` returns real values with **no tenant context set
at all** - the `ContextVar`'s `default=` is exactly the single shared
instance the app used before this phase, so nothing broke for the
still-single-tenant-in-practice app). `validate_config_overrides` gained an
optional `cfg_type` parameter (defaults to `type(cfg)`, correct for every
existing caller - only the one call site now passing a proxy needs it
explicit) - fixes the `type(cfg)` break this plan flagged in advance.
`save_config_overrides` (YAML-writing) is retired, replaced by
`save_tenant_config_overrides` (validates, then `storage.
save_tenant_settings`/`apply_config_overrides`) - `do_update_settings` in
both `actions.py` and `production/actions.py`, plus `production/actions.py`'s
`do_set_system`, all swapped over. Confirmed live: a real POST to
`/api/trading/settings` now fails with the same "no tenant set" `RuntimeError`
every other write does post-Phase-1 - expected, not a regression, matching
the trade-off already accepted.

**Decided with the user before starting Phase 2**: `tenant_tokens` gets its
table (schema only) but `TokenManager` itself stays file-based
(`data/tokens.json`) - wiring it to Postgres needs a known tenant_id at the
OAuth-callback point, which doesn't exist until Phase 3 threads one
through. Building both together in Phase 3 avoids a half-working
stopgap now.

**Phase 3a status: done.** `docs/phase3_schema.sql` adds `tenants`
(`tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `name`,
`created_at`) and `tenant_registry_entries` (`entry_type` - `'character'`/
`'corporation'`/`'alliance'`, `entry_id BIGINT`, `tenant_id` - PK
`(entry_type, entry_id)`) - deliberately **not** RLS-scoped, since they're
the directory used to resolve *which* tenant a visitor is, before any
tenant context exists; seeds the fixed `DEFAULT_TENANT_ID` row
(`00000000-0000-0000-0000-000000000001`, name `'Default'`) idempotently.

`storage.py` gained `DEFAULT_TENANT_ID`, `connect_unscoped()` (a `connect()`
sibling that checks out a pooled connection without requiring/setting an
ambient tenant_id - Postgres itself would fail loudly if this were misused
against a real RLS-scoped table, same fail-closed spirit as `connect()`),
and 5 tenant-registry functions built on it: `create_tenant`,
`add_tenant_registry_entry` (upsert - re-registering an id to a different
tenant is a legitimate re-provisioning operation, not an error),
`resolve_tenant_id(character_id, corporation_id, alliance_id)` (character
checked first, then corp, then alliance - same "any wins" order the old
`AccessConfig` allowlist used), `list_tenants`, `list_tenant_registry_entries`.

`access_gate.py`'s session cookie now signs `tenant_id` alongside
`character_id`/`character_name`; `is_allowed()` and `AccessConfig`'s three
`allowed_*_ids` list fields are retired entirely, replaced by
`storage.resolve_tenant_id(...)`. `AccessGateMiddleware` (`api/app.py`) now
**unconditionally** sets storage's ambient tenant contextvar for every
request, not just when the gate is enabled: `DEFAULT_TENANT_ID` when the
gate is off (this app's default - a trusted single operator, no login
wall) or the path is exempt, the session cookie's resolved `tenant_id`
otherwise. `auth.py`'s `/callback` route's `role_prefix == "gate"` branch
calls `storage.resolve_tenant_id(...)` instead of the old `is_allowed`
check. `cli.py` gained a `tenant` command group (`create`/`add-entry`/
`list`) for admin provisioning, and `main()`'s group callback sets
`storage.DEFAULT_TENANT_ID` once for every command (same "trusted single
operator" reasoning as the gate-disabled web case).

Confirmed live: with the gate disabled (today's default), a real CLI
command (`eve-trader refresh-shortlist`) and a real HTTP request
(`GET /api/portfolio/overview`, which queries Postgres) both now succeed
end-to-end - this is the headline proof Phase 3a was worth doing (neither
worked after Phase 1's cutover, by design, until this phase). Full
`pytest` suite: 334 passed with Postgres up, 233 passed / 101 skipped
(clean skips, no failures) with Postgres stopped.

**Decided with the user before starting Phase 3a**: `TokenManager`'s actual
switch to `tenant_tokens` (14 call sites), and threading `tenant_id`
through `/start`'s `_pending` dict and `/callback`'s buyer/seller/producer
branches, are deferred to their own future session - nothing would consume
a `tenant_id` there yet (matches the project's "don't add code for a need
that doesn't exist yet" convention).

**Phase 0 - Postgres + RLS proof of concept on `stock_targets`**
Chosen specifically because its `type_id` PK has the collision problem, not because it's
easy. Stand up Postgres, add `psycopg[binary]` + `psycopg_pool` dependencies (no ORM -
matches this project's existing minimal-dependency stance). Port `connect()`/
`batch_session()` to pool-checkout + `SET LOCAL app.tenant_id`. Add the non-owner app role
vs. owner/migration role. Widen `stock_targets` to composite PK, widen its `ON CONFLICT`
target. Add a `?`→`%s` placeholder-translation shim in the connection wrapper (confirm no
query embeds a literal `?` inside a string literal first) so individual query strings
elsewhere barely need to change later. **Acceptance**: an isolation test - two tenants
insert a `stock_targets` row for the same `type_id`, verify neither can read the other's
row and neither INSERT errors/overwrites the other.

**Phase 1 - Port the remaining ~24 per-tenant tables**
Same pattern as Phase 0 per table: add `tenant_id`, widen PK where needed, enable RLS,
widen conflict targets, translate placeholders. Port the 12 shared SDE tables with no
`tenant_id` at all. Move `goonmetrics_history` to the shared bucket (no `tenant_id`,
unchanged PK). Replace the 2 `pd.read_sql_query(..., conn)` call sites (`storage.py:1444`,
`1452`) with manual `cursor.fetchall()` + `pd.DataFrame(...)`, since pandas' SQL layer
doesn't reliably support raw `psycopg` connections. Rework the test suite's isolation
model - currently ~136 `db_path=`/`tmp_path` occurrences across `tests/` rely on per-test
SQLite files, which has no Postgres equivalent; needs a dedicated test Postgres instance
with per-test tenant_id + transaction rollback. Size this as its own multi-day sub-effort,
not a footnote.

**Phase 2 - Config/secrets into Postgres + the config proxy**
Create `tenant_settings`/`tenant_tokens` tables. Build the `TRADING_CONFIG`/`PRODUCTION_CONFIG`
proxy objects. Fix `config.py:172`'s `type(cfg)` call site. Swap `do_update_settings`'s
persistence backend from YAML-file write to a `tenant_settings` UPDATE (function
signature/callers unchanged). **Acceptance**: explicit regression test that a Settings-page
save with a bad value (wrong type / out of range) still fails with `ConfigError` through
the proxy, exactly as it does today.

**Phase 3 - Tenant resolution, access-gate, OAuth callback fix, admin CLI**
Extend the gate session cookie (`access_gate.py`) to carry `tenant_id`, resolved at login
against a new tenant registry (character/corp/alliance ID → tenant_id) instead of one
global `AccessConfig`. `AccessGateMiddleware` sets the request's tenant contextvar right
after validating the cookie. Fix the `/callback` tenant-context gap above. Build the admin
CLI (`eve-trader tenant create ...`) that provisions a tenant's registry entry only - actual
character authorization still happens through the tenant's own browser via the existing web
OAuth flow, not through the admin's machine.

**Phase 4 - Scheduler multi-tenant loop + migration *tooling* (not a live migration)**
`scheduler.py`'s job loop currently runs once against one global config/DB - make it
iterate all tenants, setting both contextvars before each tenant's job run, with per-tenant
job-status tracking (today's single global status dict becomes tenant-keyed). Build (and
test against a *copy* of the live SQLite file, never the live VM itself) a one-time ETL
script: `data/eve_trader.db` → Postgres "tenant #1" row, `config.yaml`/`tokens.json` → first
rows of `tenant_settings`/`tenant_tokens`. Per the constraint above, this script is written
and proven here but **not run against the actual live deployment** as part of this plan -
that's the separate, later cutover decision. Rework `backup.py` (it currently does its own
direct `sqlite3.connect(storage.DB_PATH)` for the online-backup API, which has no Postgres
equivalent - becomes `pg_dump`-based).

**Phase 5 - Deploy docs**
Document the owner/app-role separation, connection pool sizing, and the `SET LOCAL`
discipline required of any *new* storage function added after this migration (a future
contributor forgetting this is the main long-term risk of this design).

## Critical files

- `eve_trader/storage.py` - schema (37 `CREATE TABLE`s), `connect()`/`batch_session()`
  chokepoints (lines 532-615), ~116 query call sites
- `eve_trader/config.py` - `TradingConfig`/`ProductionConfig`/`AccessConfig`,
  `ConfigProxy`, `save_tenant_config_overrides` (Phase 2, done)
- `eve_trader/access_gate.py` - session cookie contents, `is_allowed`
- `eve_trader/api/app.py` - `AccessGateMiddleware`
- `eve_trader/api/routers/auth.py` - `/start`/`/callback`, the `_pending` dict
- `eve_trader/scheduler.py` - job loop, per-job status tracking
- `eve_trader/backup.py` - direct SQLite connection, needs a Postgres-native rework

## Verification

All work happens on the `multi-tenant` branch and is verified against a real Postgres
instance - **never against the live Oracle VM or its data**, per the constraint above. Each
phase has its own acceptance test (stated above). Phase 0's cross-tenant isolation test in
particular should be a permanent addition to the test suite, not a one-off manual check,
since it's the test that would catch a regression in the single most important guarantee of
this whole migration (tenant A never sees tenant B's data). Run the full existing `pytest`
suite after each phase - expect Phase 1 to require the test-harness rework before it can
pass again. The live deployment keeps running unmodified throughout all 5 phases; nothing
here is pushed to `main` or deployed until a separate, explicit go-ahead.
