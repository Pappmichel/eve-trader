# Scheduler and SDE checks

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Scheduler

`eve_trader/scheduler.py` is a stdlib-only (`threading`, no APScheduler)
background daemon thread, started from `api/app.py`'s FastAPI lifespan.
Whether it starts at all is an operator-level decision, read once at boot
from `DEFAULT_TENANT_ID`'s own `TradingConfig.scheduler_enabled` (**off by
default**) - see "Multi-tenant Postgres" below for what `DEFAULT_TENANT_ID`
means. Each tick, `trading_pipeline` / `esi_data_sync` / `portfolio_snapshot`
run once **per tenant** (`storage.list_tenants()`, each fully scoped via
`tenant_scope.enter_tenant`) - a tenant's own `scheduler_enabled`/interval
fields decide independently whether *their* jobs run that tick, via
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
reasoning as the thread's own on/off switch. The Jita price cache is the
other global/unscoped job.

`esi_data_sync` is one orchestrator call (`do_sync_due`) that internally
decides which (owner, kind) pairs are due given `esi_freshness.last_success_at`
and the three freshness-tier intervals (`esi_frequent_interval_hours` /
`esi_normal_interval_hours` / `esi_rare_interval_hours`). A failed fetch
records `last_error` always; it calls the existing
`clear_stale_owner_kind` (with `TradingConfig.esi_stale_clear_multiples` —
do not re-derive that clear) only for a dead owner (HTTP 401/403 or a
rejected SSO refresh), never for a global ESI outage, rate limit, network
error, or non-ESI failure. Manual tool syncs
(`do_sync_for_tool`) stamp freshness and push those pairs past the next
scheduled fetch. `last_run_status` stays job-named (`esi_data_sync`);
per-kind state lives on `esi_freshness`. `get_status()` reports
`esi_data_sync.last_run_at` from `storage.newest_esi_freshness_success_at()`
(`MAX(last_success_at)` for this tenant, `None` if nothing has ever
succeeded) — not `_run_job`'s tick `ran_at`, because that job runs every
five minutes and usually fetches nothing. `last_error` still comes from
`last_run_status`. `get_status()`'s `esi_data_sync` entry reports the
three tier intervals under `tier_interval_hours` (`interval_hours` is
null for this job) - `get_status()` itself is called nowhere in the UI
since the Portfolio rework (`docs/PORTFOLIO_REWORK_PLAN.md`) removed that
page's own "Background Scheduler" card; the function stays for Admin's
possible future use.
After `do_sync_due` the job runs Doctrine's `match_pending_contracts` for a
tenant holding `doctrine` (`scheduler._sync_due_then_match`): the contracts
fetcher stores new or status-changed contracts unmatched, and matching used
to wait for the next Doctrine page read. It then adds finished doctrine
sales the history is missing from the contract archive
(`doctrine.esi_sync.backfill_history_from_archive`: Doctrine-shared
contracts at a Doctrine structure, issued by a tenant character to an
outsider, matched from `ledger_contract_items`). The fetcher logs every contract
that leaves an owner's doctrine snapshot (and why) and every one that loses
its match - a dropped contract comes back unmatched.
`trading_pipeline` is **not** an ESI-owner sync: it still runs
`do_pipeline` (candidate/shortlist + reconcile). Reconcile consumes wallet snapshots when present and only
live-pages ESI for a shared owner whose snapshot is empty.
`production_sync_interval_hours` / `doctrine_sync_interval_hours` are
retired as ESI intervals.

`trading_pipeline` still reuses `storage.esi_sync_state` (written by
`do_pipeline`). The backup job reuses the newest backup file's own mtime
(`backup.list_backups()`). A manual pipeline/backup from the UI correctly
counts either way and pushes back the next scheduled one. `last_run_status`
is `{tenant_id: {job_name: {...}}}` for the three per-tenant jobs; a separate
`_backup_status` (not tenant-keyed) covers the global backup, and
`_jita_price_cache_status` the other. Adding another *per-tenant* scheduled
job that is not ESI-owner sync still means adding one interval field to
`TradingConfig` (plus a `_FIELD_RANGES` entry, `(0, None)`, in `config.py`)
and one `if _hours_since(...) >= cfg.x: _run_job(tenant_id, ...)` line in
`_check_and_run_due_jobs_for_tenant`. Do not add a fourth tool-shaped ESI
job; extend `do_sync_due` / the registry instead.

`portfolio_snapshot` (`TradingConfig.portfolio_snapshot_interval_hours`,
default 24h) is not an ESI-owner sync (no `esi_freshness` row) and
deliberately does **not** use the `_hours_since` pattern the other two
per-tenant jobs use - `scheduler._portfolio_snapshot_due` compares
`storage.latest_portfolio_snapshot_date()` against the real calendar
date instead (interval_hours rounded to whole days, minimum 1). An
hours-since-last-run check drifts a few minutes later on every successful
run (each run's own timestamp is whenever that tick happened to fire),
and over enough days that drift can push a run past midnight and skip a
calendar day outright - a real bug found in review, not a hypothetical
one, since `portfolio_snapshots`' own primary key is one row per calendar
day, so "due" for this one job is fundamentally a date comparison, not an
elapsed-time one. It calls `portfolio.take_portfolio_snapshot(cfg)`, the
same one write path `GET /api/portfolio/overview`'s lazy-fallback branch
calls when the scheduler is off (the default) - see `docs/PORTFOLIO_
REWORK_PLAN.md` section 5.4. Never call `portfolio_overview()`/
`total_wealth()` directly from a new call site that means to write a
snapshot; go through `take_portfolio_snapshot()` so there is one write
path, not two that could drift.

### Scheduler rework (2026-09-29, `docs/SCHEDULER_REWORK_PLAN.md`)

Goal was limiting background load once the scheduler is on again. What is
true now (the paragraphs above are still right except where this overrides
them):

- **Job switches.** `scheduler_enabled` on `DEFAULT_TENANT_ID` is the master
  switch (saving it through `do_update_settings` under the Default tenant calls
  the idempotent `scheduler.start()`; `_run_job` records `SystemExit`/
  `KeyboardInterrupt` and re-raises; `_loop` always resets `_thread` on exit) (it also gates every other tenant's per-tenant jobs, which still need
  their own `scheduler_enabled` too - now reachable from Trading Settings'
  "Background scheduler" switch, added 2026-10-01 after a code review found it
  was previously writable only via a direct `tenant_settings` SQL update, no UI
  or route at all). New **operator-only** switches live in
  `config.SCHEDULER_OPERATOR_CONFIG` (`SchedulerOperatorConfig`, read from
  `config.yaml` like `AccessConfig`, deliberately *not* on `TradingConfig` and
  not on the Settings page): `backup_job_enabled`, `jita_price_cache_job_enabled`
  (both default on - the global jobs run when the master switch is on and their
  own switch is on), `alerts_job_enabled` (`scheduler._check_and_run_alerts_job`
  reads it every tick - see "Discord alerts" below for the feature this gates, shipped later the same branch) and
  `inactive_tenant_days`. The thread now starts for the master switch **or**
  `alerts_job_enabled` alone, so alerts never require turning the backup,
  pipeline and ESI jobs on. Re-enabling the scheduler therefore no longer has to
  mean re-enabling everything at once (see "Backup" below). Note the master
  switch's own trap: it is a plain `TradingConfig` field, not part of
  `SchedulerOperatorConfig` - a `tenant_settings` override (including one from
  before this UI existed) still shadows a `config.yaml` default the way any
  other `TradingConfig` field would, unlike the operator-only switches above,
  which have no per-tenant override path at all.
- **Transient ESI failures retry sooner (ESI kinds only).** A kind whose last
  failure was transient (5xx, timeout/network `ESIError`, 420/429, `ESIRateLimited`)
  waits 15 min, then 30, 60, ... (`esi_freshness.consecutive_failures`, read via
  `storage.list_esi_failure_counts()`, capped at the long backoff below). Dead
  owners (401/403, SSO rejection), re-auth skips, `suspicious_empty:` rejections
  and non-ESI errors keep the long backoff and reset the count to 0; a success
  resets it too. `list_esi_freshness()` keeps its 7-field tuple.
- **Failure backoff.** A job/kind is due only if the interval since the last
  *success* has elapsed **and** `min(interval, 6 h)` has elapsed since the last
  *attempt* (`scheduler._job_due`, `orchestrator._kind_is_due`). Before this an
  ESI outage re-ran the whole pipeline / a failing kind's owner batch / the
  ~850-call Jita refresh on every 5-minute tick. Pipeline, backup and Jita use
  the in-process `ran_at` from `_run_job` as the attempt (a restart allows one
  immediate retry); ESI kinds use `esi_freshness.last_attempt_at`, and a kind
  skipped as `REAUTH_NEEDED` gets an attempt stamp via
  `storage.record_esi_attempt` (no error text, no stale clear). Manual syncs
  ignore the backoff.
- **ESI `Expires` is a lower bound for "due" (audit package M, fix 8).**
  `esi_client.collect_expires()` collects the `Expires` of every authenticated
  read next to the `Last-Modified` stamps (same call sites, same page
  handling in `_get_all_pages`); `_record_success` stores the latest over all
  pages as `esi_freshness.next_fetch_after` (aware UTC, capped 24 h ahead; a
  success without a usable header clears it, a failure leaves it).
  `_kind_is_due` / `pending_due` never call a pair due before it; the tier
  interval and backoff still apply on top. Manual syncs
  (`do_sync_for_tool` / `do_sync_all` / `do_sync_kinds`) skip a pair before
  its `Expires` (confirmed with the user 2026-11-16) and report `unchanged`
  plus `unchanged_message` ("... unchanged until HH:MM UTC") in the result;
  the frontend's `useAction` / `useCharacterSync` show it as its own notice.
  `live_only` kinds and the Contract Market scan do not use it. The Character
  Management overview responses carry `sync_max_age_hours` (the tenant's normal
  tier), which `useSyncWhenStale` uses instead of a fixed 6 h.
- **Refresh request after a moved item (2026-11-22).** When an asset or
  blueprint sync removes another owner's row for an item it now holds (see
  `production.md` "Items moved between owners"), `storage._request_owner_refresh`
  sets that owner's `esi_freshness.refresh_requested_at`. While it is newer
  than `last_attempt_at`, `_kind_is_due` calls the pair due regardless of tier
  interval and backoff; `Expires` still applies. The next attempt clears it.
- **Owners of a tenant run together (2026-11-22).** Each (owner, kind) used
  to be due on its own clock, so a tenant's snapshots drifted hours apart and
  an item moved between owners counted twice or not at all. When a freshness
  tier is regularly due for one pair, `pending_due` adds every candidate of the
  same tier that is at least `ALIGN_MIN_AGE_SHARE` (0.5) of its interval old
  (`orchestrator._aligned_with`). After one such pass the tier stays together;
  the cost is at most one early fetch per pair. `Expires` and the failure
  backoff still apply, and a refresh request alone pulls nothing along.
  Manual syncs are unchanged.
- **Idle ticks are free.** `esi_data_sync` starts a job thread (and writes
  `last_run_status`) only when `orchestrator.pending_due()` is non-empty;
  `do_sync_due` uses the same list. Due-ness is one `list_esi_freshness()` read;
  live-only kinds are never "due"; `_sync` returns before touching
  `TokenManager`/`ESIClient` when there is nothing to fetch and only runs the
  corp-membership public-info pass when a corporation owner exists.
- **Tenant gating** (`eve_trader/tenant_eligibility.py`): a tenant with no
  authenticated request for `inactive_tenant_days` (14; `0` disables; NULL =
  active; `DEFAULT_TENANT_ID` always active) runs no tenant-level jobs.
  `tenants.last_active_at` is written by `AccessGateMiddleware` via
  `access_gate.note_tenant_activity` (at most one UPDATE per tenant per hour per
  process, never raises). Consequence: inactive tenants get no daily portfolio
  snapshots (gaps in that history). `trading_pipeline` also needs the `trading`
  grant, `do_sync_due(granted_tools=...)` only refreshes kinds of tools the
  tenant still holds, and `jita_price_cache` prices stock targets of
  active tenants holding `production` plus hulls/items (and equivalents)
  of active doctrines whose hub is Jita for active tenants holding
  `doctrine` (also for the manual admin refresh). Gate
  off => `granted_tools()` is `None` (= everything; `DEFAULT_TENANT_ID` has no
  grant rows), gate on => exactly `tool_grants`, for the Default tenant too.
- **`schedule_mode = "on_demand"`** (registry): `clones`, `implants`,
  `standings`, `loyalty`, `skillqueue`, `notifications` are display-only and no
  longer refreshed by the scheduler. `pending_due(demand=...)` /
  `do_sync_due(demand=...)` include one only for an `(owner_type, owner_id,
  kind)` in `demand` - the opt-in Discord alerts job (`alerts/runner.py`)
  supplies this for `skillqueue`, restricted to characters with a
  `skillqueue_empty` subscription and to rows shared with `char_alerts`, so an
  alerts pass never pulls in a tenant's other data. Manual syncs
  (`do_sync_for_tool`/`do_sync_all`) still include them, they are
  exempt from `clear_stale_owner_kind` (a failed sync after a long gap must not
  wipe the snapshot), and the Character Info / Skills / Notifications pages
  sync themselves once per open when their data was last tried more than 6 h
  ago (`frontend/src/hooks/useSyncWhenStale.ts`, fixed 6 h = default normal
  tier). `skills` stays scheduled (Production/Station Trading need it).
- **ESI read integrity.** `ESIClient._get_all_pages` checks that every page
  of one list agrees on `X-Pages` and `Last-Modified` (never `ETag`) and, when
  the last page is as long as page 1 and page 1 has 1,000 or more rows, probes
  page N+1 once (404/400 or `[]` is the end). A mismatch reloads the whole list
  once; a second one raises `ESIError`, so the owner batch rolls back and the
  old snapshot stays. The collector gets the `Last-Modified` stamps of the
  accepted pass only. Fetchers (`esi_data/fetchers.py`) additionally reload an
  empty `assets`/`blueprints` list once when 50 or more rows are stored, and
  reject it with a `suspicious_empty:` error; the next attempt, whose
  `esi_freshness.last_error` still starts with that prefix, accepts it (other
  kinds never do this: 0 after many rows is normal for jobs, orders, wallet).
  `is_buy` and `is_singleton` are required fields (indexed, a missing one fails
  the sync); `is_buy_order` and journal `amount` are optional in ESI's spec.
  `ESI_COMPATIBILITY_DATE` (`config.py`) is None until a date is pinned after
  diffing the public spec; setting it sends `X-Compatibility-Date` and drops
  `/latest` from the default `esi_base`. The ESI, Goonmetrics and SDE clients
  share `esi_client.USER_AGENT`. Goonmetrics `current_prices` remembers a
  failed market for 45 s (no network for callers in that window) and does not
  cache an empty list.
- Defaults: `trading_pipeline_interval_hours` 48 (was 24),
  `jita_price_cache_interval_hours` 3 (was 1), `wallet_balance` in the normal
  tier (was frequent). They only change tenants without a saved override; a
  `config.yaml` copied from `config.example.yaml` overrides the dataclass
  default, so check the real one.
- Tick stays 300 s on purpose (returning tenants and the future mail alerts need
  short latency; after the above an idle tick is a few small queries).

### SDE guards and the daily SDE check

- **Import checks** (`production/sde.py`): `_fetch_csv` and the two streamed
  files reject a file with missing columns, a cut-off or over-long row, or fewer
  rows than `_CSV_SPEC` / `_MIN_STREAM_ROWS` (about half of the real size; invTypes
  26,000) with `SdeDumpInvalid`, which the preview turns into an `ActionError`.
  `sde_diff.build_diff` adds `shrink_warnings` (a table losing more than 20 %,
  `SHRINK_LIMIT_PERCENT`); `admin.do_apply_sde(confirm_shrink=False)` refuses while
  there are warnings, recomputed from the staged dump (which stays) against
  `storage.sde_row_counts()`. The first fill of an empty table is no shrink.
- **Constant drift**: the preview also reports `constant_drift` (`production/
  constant_drift.py`): skill time bonuses, decryptor stats and example rig bonuses
  against the dump's dogma. Attribute ids are looked up by name in
  `dgmAttributeTypes.csv` at fetch time, never hard-coded; a name the dump lacks is
  listed as not compared. Structure bonuses are not compared. Nothing is changed
  automatically.
- **Daily check job**: global job `sde_freshness_check` (switch
  `SchedulerOperatorConfig.sde_check_job_enabled`, default on; needs the master
  switch like backup and the Jita cache) stores the remote ETag and time in
  `sde_refresh_state.remote_etag` / `remote_checked_at` through one HEAD request,
  once per 24 h with the usual failure backoff, and only after the SDE was applied
  once. `sde.check_for_newer_sde` (the manual route) falls back to the stored ETag
  when its live HEAD fails; the Admin page shows "New SDE available".
- **Jita price cache age**: `jita_price_cache.get_cached_prices` still serves a
  snapshot older than twice `jita_price_cache_interval_hours` but
  `stale_age_seconds()` feeds it into `price_data_stale_hours`; older than 24 h it
  serves nothing and `pricing.jita_prices` falls back to live ESI.
- **Missing adjusted prices**: plans carry `adjusted_prices_missing` (recipe
  materials of the priced types that ESI's adjusted-price list lacks; their EIV
  share reads 0). Informational only, numbers and `job_costs_reliable` stay.
