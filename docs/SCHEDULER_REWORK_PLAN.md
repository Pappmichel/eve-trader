# Scheduler rework - load reduction (plan)

Status: **implemented 2026-09-29** (phases A-F, one commit each on
`ccr-bfaa809d-aluzun`); written and revised the same day after a critical
review. Every decision below was confirmed one by one with the user. The
durable summary is CLAUDE.md, "Scheduler rework"; "Deviations from the plan as
written" at the end lists where the implementation differs. Goal: limit background load (ESI calls, DB queries, threads)
once the scheduler is switched on again. It is currently off in production
(CLAUDE.md, "Backup").

Follow-up feature that built on this: Discord alerts (see CLAUDE.md, "Discord alerts").

## What the scheduler runs today (baseline)

Tick every `CHECK_INTERVAL_SECONDS` (300 s), per tenant with
`scheduler_enabled`, plus two global jobs:

| Job | Scope | Default interval | Cost driver |
|---|---|---|---|
| `trading_pipeline` (`do_pipeline(safe=True)`) | per tenant | 24 h | shortlist size (order-book stats) |
| `esi_data_sync` (`do_sync_due`) | per tenant | every tick, per-kind tiers 1 h / 6 h / 24 h | owners x kinds |
| `portfolio_snapshot` | per tenant | calendar day | cheap |
| `backup` | global | 24 h | one `pg_dump` |
| `jita_price_cache` | global | 1 h | ~850 ESI calls per run (module docstring) |

Load is not measured, only reasoned from code. Measure before/after if it
matters (ESI call counts, tick duration in the log).

## Findings from the review that changed this plan

1. **No failure backoff anywhere** (largest single lever). Due-ness looks at
   `last_success_at` only, never at the last *attempt*:
   - ESI sync: a kind that keeps failing (403, missing scope) rolls the whole
     owner batch back (`_run_character_owner`), so *every* due kind of that
     owner is refetched on every tick. `REAUTH_NEEDED` kinds never get a
     freshness stamp and stay due forever, so the "nothing due, skip" path of
     5A/5C never triggers for that tenant.
   - `trading_pipeline`: `set_esi_sync_time("trading")` is only written after
     a successful refresh (`actions.py` ~853/~1199). `do_pipeline` swallows
     step errors, so an ESI outage re-runs the whole pipeline every 5 min.
   - `jita_price_cache`: `_updated_at` is not set when ESI returns no quotes,
     so ~850 calls repeat every 5 min.
2. `config.example.yaml` pins `trading_pipeline_interval_hours: 24.0`. A
   production `config.yaml` copied from it overrides the dataclass default, so
   changing only `config.py` may have no effect. (The real `config.yaml` is
   hand-maintained; never overwritten by code - tell the user to adjust it.)
3. The tenant gate must not stop alert kinds (see "Gate split" below).
4. Phase order: the activity signal must exist before the scheduler uses it.
5. The inactivity threshold is an operator setting; it must not be a
   `TradingConfig` field that shows up in every tenant's Settings page while
   only the Default tenant's value counts (the same trap CLAUDE.md documents
   for `scheduler_enabled`).

## Decisions (all confirmed)

| # | Decision |
|---|---|
| 1 | Tenants not active for 14 days are skipped by the tenant-level jobs. Signal: `tenants.last_active_at`, written by `AccessGateMiddleware` (throttled, max once/hour/tenant). `NULL` = active (rollout). `DEFAULT_TENANT_ID` is always active. Consequence to accept: inactive tenants get no daily portfolio snapshots (gaps in Portfolio history). |
| 2a | `do_sync_due` only refreshes sharing rows whose `tool_key` the tenant still holds as a grant. Gate off: all keys (same rule as `_call_with_default_tenant`). |
| 2c | `wallet_balance` moves from `TIER_FREQUENT` to `TIER_NORMAL` (6 h). |
| 2b (char sheets) | Display-only kinds leave the scheduler and become `schedule_mode = "on_demand"`: `clones`, `implants`, `standings`, `loyalty`, `skillqueue`, `notifications`. Refreshed on page open (auto-sync when older than the tier interval) or via the existing sync button. Background refresh only for owners with an active alert opt-in (Discord session). `skills` stays scheduled. `mail` stays `live_only`. |
| 3 | `trading_pipeline` only for tenants holding the `trading` grant. Default 24 h -> **48 h**. |
| 4 | `jita_price_cache` only prices stock targets of tenants that are active **and** hold the `production` grant (gate off: all). Default 1 h -> **3 h**. Also applies to the manual admin refresh (same function). |
| 5A | `do_sync_due` returns immediately when nothing is due (no `TokenManager`, no `ESIClient`, no public-info loop); the corp-membership pass only runs with a corp owner. |
| 5B | Due-ness from one `storage.list_esi_freshness()` read instead of one query per sharing row. |
| 5C | The scheduler checks due-ness inline and only starts a job thread / writes `last_run_status` when something is due. |
| 5D | **Rejected**: tick stays 300 s (mail alerts and returning tenants need the short latency; after 5A-5C an idle tick is cheap). |
| 6 (new) | **Failure backoff** for all three: a job/kind is due only if `hours_since(last_success) >= interval` **and** `hours_since(last_attempt) >= min(interval, 6 h)`. A failed attempt therefore retries at most every `min(interval, 6 h)`. Manual syncs ignore the backoff. `REAUTH_NEEDED` counts as an attempt (record it) so it does not stay due. |
| 7 (new) | **Per-job switches** instead of one global on/off. Alerts run independently of a tenant's `scheduler_enabled` (see below). |
| Gate split | Tenant activity gate (decision 1) covers `trading_pipeline`, `portfolio_snapshot`, the `jita_price_cache` universe and the kinds consumed by Production/Trading/Doctrine/Portfolio. Alert kinds are demand-driven (an opt-in subscription is the demand) and ignore tenant activity: for an inactive tenant `esi_data_sync` still runs, but only for `demand` rows. The demand source lands with the Discord feature; this rework provides the hook. |

Defaults (48 h, 3 h) only change tenants without a saved override in
`tenant_settings`; `config.example.yaml` must change too (see finding 2).
`jita_price_cache_interval_hours` and `backup_interval_hours` are read from
`DEFAULT_TENANT_ID` only.

### Per-job switches (decision 7) - design

- `scheduler_enabled` keeps its meaning: operator-level "does the thread start
  at all" (read from `DEFAULT_TENANT_ID`) and, per tenant, the switch for the
  three legacy tenant jobs (`trading_pipeline`, `esi_data_sync`,
  `portfolio_snapshot`) - unchanged behaviour.
- New **operator-only** job switches so the operator can run e.g. only alerts
  without automatically enabling the backup: `backup_job_enabled`,
  `jita_price_cache_job_enabled`, `alerts_job_enabled` (the last one is
  reserved for the Discord session). Defaults preserve today's behaviour
  (`True` for backup/jita, alerts `False` until built).
- These are operator-only: config.yaml/file-based, **not** part of the
  Settings page (mechanism to be chosen when implementing; `AccessConfig`
  is the precedent for "filesystem/SSH only"). Do not put them on
  `TradingConfig`.
- The `alerts` job (Discord session) runs per tenant that has an enabled
  opt-in, **regardless of that tenant's `scheduler_enabled`** and regardless of
  tenant activity. Consequence: the thread must start when *any* job is
  enabled at operator level, not only when the Default tenant's
  `scheduler_enabled` is on. Rule: thread starts if `scheduler_enabled` or any
  operator job switch (backup/jita/alerts) is on; per-tenant jobs still need
  the tenant's own `scheduler_enabled`.
- `get_status()` gains the switch state (kept for Admin's possible future use).

## Implementation order

One commit per phase, suite green after each (`pytest` from repo root).

### Phase A - defaults and registry tier (trivial)
- `eve_trader/config.py`: `trading_pipeline_interval_hours` 24.0 -> 48.0 (~566), `jita_price_cache_interval_hours` 1.0 -> 3.0 (~582); fix the inline comments.
- `config.example.yaml`: `trading_pipeline_interval_hours: 48.0` (line ~54). Mention to the user that the real `config.yaml` must be adjusted by hand.
- `esi_data/registry.py`: `wallet_balance` -> `TIER_NORMAL`.
- Tests: `tests/test_esi_data_registry.py` (tier), any default assertions.

### Phase B - orchestrator (5A, 5B, 2a, backoff for ESI kinds)
- `esi_data/orchestrator.py`:
  - `do_sync_due(..., granted_tools: Optional[set[str]] = None)`; `None` = no filter (CLI / gate off), otherwise drop sharing rows with `tool_key` not in the set. Keep `esi_data` free of tool-package imports.
  - One `list_esi_freshness()` read builds `{(owner_type, owner_id, kind): (last_success_at, last_attempt_at)}`; due = decision 6.
  - Extract a shared `pending_due(...)` used by both `do_sync_due` and the scheduler's inline check - no duplicated due logic.
  - `_sync`: return early on empty `due`; run the `character_public_info` / corp pass only when `corp_owners` is non-empty.
  - Record an attempt for `REAUTH_NEEDED` kinds (upsert freshness with `success=False`, error `reauth_needed`). Check that this does **not** trigger `clear_stale_owner_kind` for them (it currently runs from `_record_failure`; use a separate path or a flag).
  - Verify how a batch rollback interacts with the backoff: the failed kind gets an attempt stamp outside the rolled-back batch, the other kinds keep their old stamps and are retried next tick without the failing kind - expected to self-heal; add a test.
- `storage.py`: `list_tool_grants_for_tenant(tenant_id)` via `connect_unscoped()` (`tool_grants` is unscoped, has `tenant_id`).
- Tests: `tests/test_esi_orchestrator.py` - nothing due => no `TokenManager`/`ESIClient` constructed and no ESI call; grant filter; one freshness read; backoff after a failed kind; REAUTH_NEEDED not perpetually due.

### Phase C - activity tracking (decision 1) - before the scheduler uses it
- Schema: `ALTER TABLE tenants ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;` appended to **`docs/admin_schema.sql`** (existing file => the four-place "new schema file" list is not triggered; `deploy.sh` already applies it idempotently). `tenants` already has `GRANT ... UPDATE` for `eve_trader_app` (phase3_schema.sql:35).
- `storage.touch_tenant_active(tenant_id)` (`connect_unscoped`, one `UPDATE`); a bulk read for the scheduler.
- `api/app.py` `AccessGateMiddleware.dispatch`, gate-on branch after `authorize_session_cookie` succeeds: in-process throttle dict (`tenant_id -> monotonic ts`, 1 h). One small synchronous UPDATE per hour per tenant is fine (the same code path already does a synchronous DB read in `authorize_session_cookie`); a failed touch must never fail the request.
- Threshold `inactive_tenant_days` (default 14, `0` disables) is an **operator-only** field (see per-job switches for where such fields live), not a `TradingConfig` field.
- Tests: `tests/test_access_gate*.py` (touch throttled, never breaks a request), a storage test.

### Phase D - scheduler gating, backoff, per-job switches
- `scheduler.py`, `_check_and_run_due_jobs_for_tenant`:
  - `_tenant_is_active(tenant_id)` (NULL active, Default tenant always active) gates `trading_pipeline`, `portfolio_snapshot`; for `esi_data_sync` an inactive tenant runs demand-only (empty until Discord ships => effectively skipped);
  - `trading_pipeline` only with the `trading` grant; due = decision 6, so the pipeline records an *attempt* timestamp at start (e.g. `set_esi_sync_time("trading_pipeline_attempt", ...)`, reusing the existing `esi_sync_state` key/value table) next to the success stamp it already writes;
  - `esi_data_sync`: compute `granted_tools`, ask `pending_due`; only `_run_job` when non-empty;
  - grants from `storage.list_tool_grants_for_tenant`; gate off (`ACCESS_CONFIG.access_gate_enabled` False) => all keys.
  - Note: with the gate **on** the Default tenant can hold real grants (`admin bootstrap` may register a character there); the "all keys" shortcut is only for gate off.
- `production/jita_price_cache.py`: track `_attempted_at`; restrict the tenant loop to active tenants holding `production` (gate off: all); no such tenant => return 0 without ESI calls; the scheduler applies decision 6. Keep `_structural_material_closure` inside the `enter_tenant` block (documented cold-cache bug).
- Per-job switches and thread-start rule as in "Per-job switches - design".
- Tests: `tests/test_scheduler.py` (inactive tenant skipped, NULL active, default tenant always active, backoff for pipeline, thread starts on operator job switch alone, per-tenant jobs still need the tenant's own flag), `tests/test_production_jita_price_cache.py` (no ESI call when no eligible tenant, backoff after empty result).

### Phase E - `schedule_mode = "on_demand"` (char sheets)
- `esi_data/registry.py`: new field `schedule_mode: str = "always"`; `"on_demand"` for the six kinds above. `test_esi_data_registry.py` guards import purity (no tool packages) - keep it.
- `do_sync_due(..., demand: frozenset[tuple[str,int,str]] = frozenset())`: an `on_demand` kind is only due for `(owner_type, owner_id, kind)` in `demand`. Empty for now; the Discord session fills it.
- **Verify first, likely fix**: `_record_failure` calls `clear_stale_owner_kind` on *any* failed fetch (also manual). For an `on_demand` kind the snapshot is expected to be much older than `tier x esi_stale_clear_multiples`, so one failed page-open sync would wipe it. Exempt `on_demand` kinds from the stale clear and add a test.
- Page-open auto-sync: frontend on the char_info / char_skills / char_notifications pages checks `GET /api/characters/freshness` and, when a kind is older than its tier interval, calls the existing sync endpoint; existing `in_flight` handling (R10) covers double triggers. No new backend route expected - confirm against `frontend/src/pages/characters` and `character_management/*_actions.py` first.
- Frontend live-verify per CLAUDE.md (Playwright throwaway script, delete afterwards).

### Phase F - docs
- CLAUDE.md "Scheduler": new defaults, tenant gate, grant filter, backoff, per-job switches and the new thread-start rule, on-demand kinds, demand-gate hook, tick stays 300 s. Update "Deferred, not rejected" when Discord alerts start.
- `config.example.yaml` comments for the new operator fields; README / `docs/OPERATOR_SECURITY.md` only if they list these fields (grep first).

## Risks / things to verify while implementing
- Stale clear on `on_demand` kinds (Phase E) - real data-loss path.
- Backoff must not affect manual syncs (`do_sync_for_tool` / `do_sync_all`).
- Changing the thread-start rule changes what "scheduler off" means: today one flag turns *everything* on; afterwards the operator must consciously pick the jobs. Document it prominently, and keep defaults behaviour-preserving.
- The activity touch sits on the hot request path; keep it throttled and failure-tolerant.
- A returning tenant is refreshed on the next tick (<= 5 min) after its first request; make sure the first request itself is not blocked.
- Gate-off installs: `DEFAULT_TENANT_ID` has no grants in the DB; the grant filter must treat gate-off as "all tools", or every sync stops - also in the manual Jita refresh.
- Router tests monkeypatch module objects (`from ... import actions`); new router code must keep that import style.
- Live-verify each phase against the real endpoint / a real browser, not only unit tests (CLAUDE.md).
- No Claude/Anthropic attribution in commits or PRs (CLAUDE.md, "Environment specifics").

## Deviations from the plan as written (what was actually built)

- **Attempt timestamps for pipeline/backup/Jita are the in-process `ran_at`
  that `_run_job` already records**, not new persisted keys
  (`scheduler._job_due` / `_last_attempt`). A restart therefore allows one
  immediate retry, which is acceptable. ESI kinds use the persisted
  `esi_freshness.last_attempt_at` as planned; `REAUTH_NEEDED` kinds get an
  attempt-only stamp (`storage.record_esi_attempt`), which leaves `last_error`
  and the success time untouched and never triggers the stale clear.
- **Operator config** is its own dataclass, `config.SchedulerOperatorConfig` /
  `SCHEDULER_OPERATOR_CONFIG` (read from `config.yaml`, not on the Settings page,
  not on `TradingConfig`, not in `AccessConfig`).
- **Shared eligibility logic** lives in `eve_trader/tenant_eligibility.py`
  (`is_active`, `granted_tools`, `may_use`) because `production/jita_price_cache.py`
  needs it and `scheduler.py` imports that module.
- **Thread-start rule and master switch**: the thread starts for the Default
  tenant's `scheduler_enabled` **or** `alerts_job_enabled`. Tenant-level jobs
  and the global backup/Jita jobs additionally require the master switch
  (`scheduler._master_enabled()`, DEFAULT tenant's `scheduler_enabled`), which
  is what makes an alerts-only run truly alerts-only. The backup/Jita switches
  default to on but only matter while the master switch is on - a default of
  "on" that started the thread by itself would have broken "off by default".
- **Backup got the same backoff** as the pipeline and Jita jobs (a failing
  `pg_dump` would otherwise be retried every tick).
- `pending_due` also drops sharing rows for unknown kinds and corporation rows
  whose kind has no corporation scope, besides live-only kinds.
- Page-open auto-sync needed no new backend route: `useSyncWhenStale` calls the
  existing sync endpoints. It looks at the newest *attempt* time
  (`freshness.last_attempt_at`; for Notifications `synced_at`, which is all that
  endpoint returns), so a failing kind retries once per 6 h window per open,
  not on every render.
- Not done / left for later: the demand source for `on_demand` kinds and the
  alerts job (Discord session); a second thread-safety look at the throttle
  dict in `access_gate` is not needed (guarded by a lock).

## Verification done

- Backend suite green after every phase (2654 passed, 3 skipped at the end; run
  against a freshly recreated test database - see below), frontend `vitest` (203
  passed), `tsc -b` clean.
- Live against the dev Postgres (RLS on, app role): a seeded character with a
  failing ESI (sandbox proxy 403) produced exactly one attempt per kind; the
  second tick did not re-run the pipeline, the failed kind or the snapshot job,
  and the on-demand `skillqueue` was never scheduled. A real browser on the Skills
  page issued exactly one `POST /api/char-skills/sync` on open.
- Test-suite gotcha found on the way: `tests/test_sqlite_migration_table_drift.py`
  reads `pg_policies` of the shared test database, so it fails on a database that
  earlier full runs already filled with `station_trading_*` / `module_reprocessing_*`
  tables (unrelated to this change). Recreate `eve_trader` before a full run.

