# Scheduler rework - load reduction (plan)

Status: **planned, nothing implemented** (written 2026-09-29). Every decision
below was confirmed one by one with the user; see "Decisions". Goal: limit
background load (ESI calls, DB queries, threads) once the scheduler is
switched on again. It is currently off in production (CLAUDE.md, "Backup").

Follow-up feature that builds on this: `docs/DISCORD_ALERTS_HANDOFF.md`.

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

## Decisions (all confirmed)

| # | Decision |
|---|---|
| 1 | Tenants that were not active for 14 days are skipped by the tenant-level jobs. Signal: `tenants.last_active_at`, written by `AccessGateMiddleware` (throttled, max once/hour/tenant). `NULL` = active (rollout). `DEFAULT_TENANT_ID` is always active. |
| 2a | `do_sync_due` only refreshes sharing rows whose `tool_key` the tenant still holds as a grant. Gate off: all keys (same rule as `_call_with_default_tenant`). |
| 2c | `wallet_balance` moves from `TIER_FREQUENT` to `TIER_NORMAL` (6 h). |
| 2b (via char sheets) | Display-only kinds leave the scheduler and become `schedule_mode = "on_demand"`: `clones`, `implants`, `standings`, `loyalty`, `skillqueue`, `notifications`. Refreshed on page open (auto-sync when older than the tier interval) or via the existing sync button. Background refresh only for owners with an active alert opt-in (Discord session). `skills` stays scheduled (Production/Station Trading/Skill plans use it). `mail` stays `live_only`. |
| 3 | `trading_pipeline` only runs for tenants holding the `trading` grant. Default interval 24 h -> **48 h**. |
| 4 | `jita_price_cache` only prices stock targets of tenants that are active **and** hold the `production` grant. Default interval 1 h -> **3 h**. |
| 5A | `do_sync_due` returns immediately when nothing is due (no `TokenManager`, no `ESIClient`, no public-info loop); the corp-membership pass only runs when there is a corp owner. |
| 5B | Due-ness from one `storage.list_esi_freshness()` read instead of one query per sharing row. |
| 5C | The scheduler checks due-ness inline and only starts a job thread / writes `last_run_status` when something is due. |
| 5D | **Rejected**: tick stays 300 s (mail alerts and returning tenants need the short latency). |
| Gate split | Tenant gate (decision 1) covers `trading_pipeline`, `portfolio_snapshot`, `jita_price_cache` universe and the Production/Trading/Doctrine/Portfolio kinds. A demand gate for alert kinds (opt-in subscription = demand) ignores tenant activity; it lands with the Discord feature, this rework only provides the hook. |

Defaults (48 h, 3 h) only change tenants without a saved override in
`tenant_settings`; a stored value keeps winning. `jita_price_cache_interval_hours`
is read from `DEFAULT_TENANT_ID` only.

## Implementation order

One commit per phase, suite green after each (`pytest` from repo root).

### Phase A - defaults and registry tier (trivial)
- `eve_trader/config.py`: `trading_pipeline_interval_hours` 24.0 -> 48.0 (line ~566), `jita_price_cache_interval_hours` 1.0 -> 3.0 (~582). Fix the inline comments.
- `eve_trader/esi_data/registry.py`: `wallet_balance` -> `TIER_NORMAL`.
- Tests: `tests/test_esi_data_registry.py` (tier), any default assertions.

### Phase B - orchestrator (5A, 5B, 2a)
- `esi_data/orchestrator.py`:
  - `do_sync_due(..., granted_tools: Optional[set[str]] = None)`; `None` = no filter (CLI / gate off), otherwise drop sharing rows with `tool_key` not in the set. Keep `esi_data` free of tool-package imports (it only names tools as strings).
  - One `list_esi_freshness()` read builds a `{(owner_type, owner_id, kind): last_success_at}` map; `_kind_is_due` takes it instead of querying. Keep the single-row helper for manual callers if any.
  - Extract a shared `pending_due(...)` used by both `do_sync_due` and the scheduler's inline check (decision 5C) - no duplicated due logic.
  - `_sync`: return early on empty `due`; run the `character_public_info` / corp pass only when `corp_owners` is non-empty.
- `storage.py`: `list_tool_grants_for_tenant(tenant_id)` via `connect_unscoped()` (`tool_grants` is unscoped, `tenant_id` column exists).
- Tests: `tests/test_esi_orchestrator.py` - nothing due => no `TokenManager`/`ESIClient` constructed, no ESI call; grant filter; one freshness query.

### Phase C - scheduler gating (3, 5C, tenant gate hook)
- `scheduler.py`, `_check_and_run_due_jobs_for_tenant`:
  - tenant gate `_tenant_is_active(tenant_id)` first (decision 1);
  - `trading_pipeline` only with `trading` grant;
  - `esi_data_sync`: compute `granted_tools`, ask `pending_due`; only `_run_job` when non-empty;
  - `portfolio_snapshot` behind the tenant gate.
  - Grants come from `storage.list_tool_grants_for_tenant`; gate off (`ACCESS_CONFIG.access_gate_enabled` False) => all keys.
- `production/jita_price_cache.py::refresh_jita_price_cache`: restrict the tenant loop to active tenants holding `production`; no such tenant => return 0 with no ESI call. Keep `_structural_material_closure` inside the `enter_tenant` block (documented cold-cache bug).
- Tests: `tests/test_scheduler.py`, `tests/test_production_jita_price_cache.py`.

### Phase D - activity tracking (1)
- Schema: `ALTER TABLE tenants ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;` appended to **`docs/admin_schema.sql`** (existing file => the four-place "new schema file" list is not triggered; `deploy.sh` already applies it idempotently). `tenants` already has `GRANT ... UPDATE` for `eve_trader_app` (phase3_schema.sql:35).
- `storage.touch_tenant_active(tenant_id)` (`connect_unscoped`, one `UPDATE`); `storage.tenant_last_active_at` / bulk read for the scheduler.
- `api/app.py` `AccessGateMiddleware.dispatch`, gate-on branch after `authorize_session_cookie` succeeds: in-process throttle dict (`tenant_id -> monotonic ts`, 1 h). Do the write off the event loop (thread) or accept one tiny sync UPDATE per hour per tenant - decide when implementing, keep the request path failure-tolerant (a failed touch must never fail the request).
- Threshold: operator field `inactive_tenant_days` (default 14, `0` disables) on `TradingConfig`, read from `DEFAULT_TENANT_ID` like `backup_interval_hours`; add a `_FIELD_RANGES` entry `(0, None)`.
- Tests: `tests/test_access_gate*.py` (touch is throttled, never breaks a request), storage test, scheduler test (inactive tenant skipped, NULL active, default tenant always active).

### Phase E - `schedule_mode = "on_demand"` (2b)
- `esi_data/registry.py`: new field `schedule_mode: str = "always"`; set `"on_demand"` for the six kinds above. `test_esi_data_registry.py` guards import purity (no tool packages) - keep it.
- `do_sync_due(..., demand: frozenset[tuple[str,int,str]] = frozenset())`: an `on_demand` kind is only due for `(owner_type, owner_id, kind)` in `demand`. Empty for now (no opt-in source exists yet); the Discord session fills it.
- **Verify first, likely fix**: `_record_failure` calls `clear_stale_owner_kind` on *any* failed fetch (also manual). For an `on_demand` kind the snapshot is expected to be much older than `tier x esi_stale_clear_multiples`, so a single failed page-open sync would wipe it. Exempt `on_demand` kinds from the stale clear (or clear based on a much larger horizon) and add a test.
- Page-open auto-sync (decision 2b): frontend on the char_info / char_skills / char_notifications pages checks `GET /api/characters/freshness` and, when a kind is older than its tier interval, calls the existing sync endpoint (`do_sync_char_info` etc.); the existing `in_flight` handling (R10) covers double triggers. No new backend route expected - confirm against `frontend/src/pages/characters` and `character_management/*_actions.py` before building.
- Frontend live-verify per CLAUDE.md (Playwright throwaway script, delete afterwards).

### Phase F - docs
- CLAUDE.md "Scheduler": new defaults, tenant gate, grant filter, on-demand kinds, the demand-gate hook, tick stays 300 s. Update the "Deferred, not rejected" paragraph when Discord alerts start.
- README config table / `docs/OPERATOR_SECURITY.md` only if they list these fields (grep before editing).

## Risks / things to verify while implementing
- Stale clear on `on_demand` kinds (Phase E) - real data-loss path.
- The activity touch sits on the hot request path; keep it throttled and failure-tolerant.
- Inactive-tenant skip changes `esi_freshness` cadence: a returning tenant is refreshed on the next tick (<= 5 min) after its first request; make sure the first request itself is not blocked by it.
- Gate-off installs: `DEFAULT_TENANT_ID` has no grants in the DB; the grant filter must treat gate-off as "all tools", or every sync stops.
- Router tests monkeypatch module objects (`from ... import actions`); new router code must keep that import style.
- Live-verify each phase against the real endpoint / a real browser, not only unit tests (CLAUDE.md).
- No Claude/Anthropic attribution in commits or PRs (CLAUDE.md, "Environment specifics").
