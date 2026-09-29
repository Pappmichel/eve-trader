# Discord alerts - handoff for a later session

Status: **planned, not started** (written 2026-09-29). Temporary note: delete
it (and move durable facts into CLAUDE.md) when the feature lands.

CLAUDE.md lists "Discord alerts" under "Deferred, not rejected - don't start
without asking". The user has now explicitly scheduled it for a later session
and provided the decisions below; still confirm scope at the start of the
session, and update that CLAUDE.md paragraph when work begins.

## Prerequisite

`docs/SCHEDULER_REWORK_PLAN.md` must be implemented first. This feature relies
on what it provides:
- `schedule_mode = "on_demand"` ESI kinds and `do_sync_due(..., demand=...)`
  (the demand set is filled from opt-in subscriptions);
- the tenant activity gate with a hook for a **demand gate** (below);
- grant filtering in `do_sync_due`;
- 300 s scheduler tick (deliberately kept; mail alerts need it).

## First alert types

1. **`skillqueue_empty`** (per character)
2. **`mail_new`** (per character) with a second, separate opt-in
   **`include_content`**.

Later candidates (not decided): notifications (e.g. structure attacks; ESI
caches notifications ~10 min, so a ~10-15 min poll at most).

## Settled decisions

- **Every alert is opt-in, default off**, per character x alert type. No
  subscription => nothing runs, zero load.
- **Mail content is an additional opt-in** on the `mail_new` subscription.
  Default is metadata only ("2 new mails", optionally sender - decide).
  Content requires one extra ESI call per mail body, so that cost only exists
  with explicit consent. The UI must say plainly that content leaves the app
  and cannot be recalled from Discord.
- **Opt-in does not replace ESI sharing.** The alert needs both the opt-in
  and an `esi_sharing` row for the relevant kind/tool; reads stay fail-closed
  through the accessor (`read_esi` / `is_shared`). Revoking sharing stops the
  alert.
- **Re-check the opt-in (and sharing) at send time**, not only when the job is
  planned. A revoke takes effect immediately, even mid-run.
- **Demand gate vs. tenant gate.** The scheduler rework skips inactive tenants
  (14 days). A person who only wants Discord pings and never logs in must not
  lose alerts: alert kinds run for opted-in owners regardless of tenant
  activity. Only those kinds/jobs; the rest of that tenant's jobs stay gated.
- **Skill queue**: no high-frequency polling. The snapshot holds the last
  entry's `finish_date`, so "queue runs empty" is predictable. Alert as a
  warning ("queue ends in < N h", N configurable per subscription; a paused
  queue = missing `finish_date` is detectable from the same snapshot). The
  existing 6 h Normal-tier sync is enough; when the threshold is crossed,
  **re-check live once** (`ESIClient.character_skillqueue`, ESI caches ~120 s)
  before sending, to avoid false alarms after the user extended the queue.
- **Mail** stays `live_only` in the registry (`mail` is deliberately not an
  orchestrator kind: the stale clear must never delete a mail archive after
  one failed refresh - CHARACTER_MANAGEMENT_PLAN.md R2/R11). Do **not** turn
  it into a tier kind. Build a separate opt-in poller: mail headers only
  (`character_mail_headers`), remember the last seen `mail_id` per character,
  interval ~10-15 min (own config field), bodies fetched only when
  `include_content` is on.
- Webhook secrets/destinations are per tenant with RLS.

## Open decisions (ask the user)

- Delivery: user-supplied Discord **webhook** per character/tenant vs. a bot
  (DM/channel). Webhook is far simpler; a bot needs a token and linking flow.
- New `tool_key` (e.g. `char_alerts`, would need adding to `ALL_TOOL_KEYS`,
  Admin checkboxes, `_TOOL_PATH_PREFIXES`, Characters page sharing UI) vs.
  reusing `char_skills` / `char_mail` as the consuming tools.
- Default lead time for `skillqueue_empty`; re-alert/cooldown policy.
- Whether the sender/subject may be included without `include_content`.
- Quiet hours, message language.
- How to store the webhook URL at rest (check how `tenant_tokens` handles
  secrets and follow that precedent).

## Suggested data model (sketch)

Put it in an **existing** schema file (e.g. `docs/character_management_schema.sql`)
to avoid the "brand-new schema file" checklist in CLAUDE.md (`deploy.sh`,
both READMEs, `.cursor/start.sh`). If a new file is chosen, all four places
need it. Copy the RLS shape from `docs/phase1_schema.sql` (tenant_id column
with `current_setting('app.tenant_id', false)::uuid` default, RLS, policy);
composite PK with `tenant_id` only if the natural key could collide.

- `alert_destinations(tenant_id, id, kind, url/secret, created_at)`
- `alert_subscriptions(tenant_id, character_id, alert_type, enabled,
  include_content, params JSON, destination_id)`, default `enabled = false`
- `alert_state(tenant_id, character_id, alert_type, last_seen_mail_id,
  last_sent_at, last_key)` for dedupe/cooldown

## Scheduler integration

- New per-tenant job `alerts` in `_check_and_run_due_jobs_for_tenant`
  (one `TradingConfig` interval field + `_FIELD_RANGES` entry `(0, None)` +
  one `if` line, per CLAUDE.md "Scheduler").
- Do **not** add a fourth tool-shaped ESI job; feed opted-in owners/kinds into
  `do_sync_due` through the `demand` parameter.
- Reuse `_run_job`, tenant scope via `tenant_scope.enter_tenant`, and wrap any
  thread-pool callables in `storage.with_current_tenant`.
- Alert sending goes through a `do_*` action (UI/CLI parity); routers stay thin
  with the `_wrap` helper; user-facing failures raise `ActionError`.

## Security / privacy checklist

- Validate webhook URLs: `https` only and Discord webhook hosts only
  (SSRF guard); never fetch arbitrary user URLs.
- Send with `allowed_mentions` disabled/empty so mail or skill names cannot
  ping `@everyone`.
- Truncate content; never log message bodies or webhook URLs.
- Honour Discord `429` / `Retry-After`; a failing destination must not stall
  the tick (jobs already have `JOB_TIMEOUT_SECONDS`).
- Remove/disable subscriptions when a character is removed
  (`admin.do_remove_user`, Characters `remove_owner`) and when the tenant is
  suspended.
- Outbound network access must allow Discord in the deployment environment.

## Read first

`eve_trader/scheduler.py`, `eve_trader/esi_data/{registry,orchestrator,stale}.py`,
`eve_trader/character_management/{mail_*,skill_plan_actions,notification_actions}.py`,
`docs/CHARACTER_MANAGEMENT_PLAN.md` (R2, R9, R10, R11), `docs/ESI_ACCESS_PLAN.md`,
`docs/SCHEDULER_REWORK_PLAN.md`, CLAUDE.md sections "Scheduler",
"Multi-tenant Postgres", "Tool permissions & Admin".

Process rules from CLAUDE.md: live-verify backend and frontend changes, keep
`pytest` green, no Claude/Anthropic attribution in commits or PRs.
