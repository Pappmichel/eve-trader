# Discord alerts - handoff for a later session

Status: **partly implemented** - scheduler-independent parts done (see "Implemented so far"); scheduler job and UI pending. Originally: planned (written 2026-09-29, revised after a critical
review the same day). Temporary note: delete it (and move durable facts into
CLAUDE.md) when the feature lands.

CLAUDE.md lists "Discord alerts" under "Deferred, not rejected - don't start
without asking". The user has now explicitly scheduled it for a later session
and provided the decisions below; still confirm scope at the start of the
session, and update that CLAUDE.md paragraph when work begins.

## Prerequisite

`docs/SCHEDULER_REWORK_PLAN.md` is **implemented** (2026-09-29). This feature
relies on what it provides (names as built):
- **per-job operator switches** (`SCHEDULER_OPERATOR_CONFIG.alerts_job_enabled`,
  reserved, read by nothing yet) and the new thread-start rule (thread runs for
  the master `scheduler_enabled` **or** `alerts_job_enabled`; see
  `scheduler.start`): alerts must work with the scheduler thread
  running for alerts only, and **independent of a tenant's own
  `scheduler_enabled`** (which today defaults to `false` per tenant);
- `schedule_mode = "on_demand"` ESI kinds and
  `orchestrator.pending_due(demand=...)` / `do_sync_due(demand=...)` where
  `demand` is an iterable of `(owner_type, owner_id, kind)` (fill it from opt-in
  subscriptions; wire it into `scheduler._check_and_run_due_jobs_for_tenant`,
  which today passes none). On-demand kinds: `clones`, `implants`, `standings`,
  `loyalty`, `skillqueue`, `notifications`; they are exempt from the stale clear;
  the Character pages also auto-sync when opened with data older than 6 h;
- the tenant activity gate (`tenant_eligibility.is_active`,
  `tenants.last_active_at`). `scheduler._check_and_run_due_jobs` currently does
  nothing when the master switch is off and has a comment marking where the
  alerts job must run regardless of `master`; an inactive tenant currently runs
  **no** `esi_data_sync` at all, so the alerts wiring must feed `demand` for
  inactive tenants too;
- grant filtering and failure backoff in `do_sync_due`;
- 300 s scheduler tick (deliberately kept).

Note the scheduler is currently **off in production by decision** (CLAUDE.md
"Backup"); alerts only work once the operator enables the thread. With the
rework that no longer means enabling every job (backup, pipeline, ...).

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
  Default is metadata only ("2 new mails"; whether sender/subject may appear
  without the content opt-in is open). Content requires one extra ESI call per
  mail body, so that cost only exists with explicit consent. The UI must say
  plainly that content leaves the app and cannot be recalled from Discord.
- **Opt-in does not replace ESI sharing.** The alert needs both the opt-in
  and an `esi_sharing` row for the relevant kind/tool; reads stay fail-closed
  through the accessor (`read_esi` / `is_shared`). Revoking sharing stops the
  alert.
- **Re-check the opt-in (and sharing) at send time**, not only when the job is
  planned. A revoke takes effect immediately, even mid-run.
- **Demand gate vs. tenant gate.** The rework skips inactive tenants
  (14 days). A person who only wants Discord pings and never logs in must not
  lose alerts: alert kinds run for opted-in owners regardless of tenant
  activity and regardless of the tenant's `scheduler_enabled`. Only those
  kinds/jobs; the rest of the tenant's jobs stay gated.

### Skill queue alert

- The snapshot holds the last entry's `finish_date`, so "queue runs empty" is
  predictable. Alert as a warning ("queue ends in < N h", N configurable per
  subscription; a paused queue = missing `finish_date` is detectable).
- **Evaluate the threshold on every scheduler tick from the stored
  `finish_date`** (a local DB read, no ESI call). The 6 h background sync is
  only a safety net; do not rely on it for timing (a 6 h sync would make a
  "< 2 h" warning up to 6 h late).
- When the threshold is crossed, **re-check live once**
  (`ESIClient.character_skillqueue`, ESI caches ~120 s) before sending, to
  avoid false alarms after the user extended the queue. Store the sent key
  (e.g. the `finish_date`) so the same warning is not repeated.

### Mail alert

- `mail` stays `live_only` in the registry (deliberately not an orchestrator
  kind: the stale clear must never delete a mail archive after one failed
  refresh - CHARACTER_MANAGEMENT_PLAN.md R2/R11). Do **not** turn it into a
  tier kind. **Exception to "everything through `do_sync_due` `demand`"**:
  `demand` serves `skillqueue` (and later `notifications`); mail is served by a
  separate opt-in `alerts` job, because `demand` cannot reach a `live_only` kind.
- Poll mail headers only (`character_mail_headers`), interval ~10-15 min (own
  config field), bodies only when `include_content` is on.
- **Baseline on enable**: when a subscription is switched on (or the first run
  has no `last_seen_mail_id`), store the current newest `mail_id` and send
  nothing. Otherwise the first run would announce every old mail.
- Track `last_seen_mail_id` per character in `alert_state`; advance it only
  after a successful send (or after a deliberate skip), so a Discord outage
  does not lose alerts or duplicate them.

## Decisions confirmed 2026-09-29 (supersede the open list below)

The scheduler rework is implemented by a separate session; this feature builds
on it afterwards.

1. **Delivery: Discord bot** (not a webhook). The bot token is an
   operator-level env variable, never stored per tenant or in the DB.
2. **Target: DM to a linked Discord account.** The user links their Discord
   account (OAuth2 `identify`) to the character; per tenant only the Discord
   user id is stored. (The webhook SSRF guard in the checklist below then does
   not apply; the bot only calls the fixed Discord API host.)
3. **New tool_key `char_alerts`** (`ALL_TOOL_KEYS`, Admin checkboxes,
   `_TOOL_PATH_PREFIXES`, Characters sharing UI).
4. **`skillqueue_empty`: default lead time 12 h, sent once per `finish_date`**;
   lead time configurable per subscription.
5. **`mail_new` without `include_content`: count only** ("2 new mails"), no
   sender, no subject. Content only with the separate opt-in.
6. **No quiet hours, English messages.** Secrets handling follows
   `tenant_tokens` precedent for anything stored at rest.

## Original open decisions (now answered above)

- Delivery: user-supplied Discord **webhook** per tenant vs. a bot (DM/channel).
  Webhook is far simpler; a bot needs a token and linking flow.
- New `tool_key` (e.g. `char_alerts`; needs `ALL_TOOL_KEYS`, Admin
  checkboxes, `_TOOL_PATH_PREFIXES`, Characters page sharing UI) vs. reusing
  `char_skills` / `char_mail` as consuming tools.
- Default lead time for `skillqueue_empty`; re-alert/cooldown policy.
- Whether sender/subject may be included without `include_content`.
- Quiet hours, message language.
- How to store the webhook URL at rest (check how `tenant_tokens` handles
  secrets and follow that precedent).

## Suggested data model (sketch)

There is exactly one character per tenant (`tenant_registry_entries.tenant_id`
is UNIQUE), so destination per tenant is simplest; subscriptions still carry
`character_id` because the sharing/scope checks are per character.

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
  last_sent_at, last_key)` for baseline/dedupe/cooldown

## Scheduler integration

- New per-tenant job `alerts` in `_check_and_run_due_jobs_for_tenant`, gated by
  the operator switch `alerts_job_enabled` and by the tenant having an enabled
  opt-in - **not** by the tenant's `scheduler_enabled` and **not** by tenant
  activity (see the rework plan's per-job switch design). Add the interval
  field(s) per CLAUDE.md "Scheduler" (`_FIELD_RANGES` entry `(0, None)`).
- Do **not** add a fourth tool-shaped ESI job for ESI-backed kinds; feed
  opted-in owners/kinds into `do_sync_due` through `demand`. Mail is the
  documented exception above.
- Reuse `_run_job` (has the timeout), tenant scope via
  `tenant_scope.enter_tenant`, and wrap any thread-pool callables in
  `storage.with_current_tenant`.
- Apply the same failure-backoff rule as the rework (attempt timestamps) to
  the mail poll and to Discord delivery failures, so an outage cannot cause a
  retry every tick.
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

## Implemented so far (branch `feat/discord-alerts`)

Independent of the scheduler rework:
- Schema (phase 10 in `docs/character_management_schema.sql`): `alert_destinations`,
  `alert_subscriptions`, `alert_state`, RLS like every tenant table.
- `eve_trader/alerts/`: `config.py` (operator env), `discord_client.py` (OAuth2
  identify link + bot DM, mentions disabled, 429 -> `DiscordRateLimited`, DM
  refused -> `DiscordDMBlocked`), `logic.py` (pure `skillqueue_decision` /
  `mail_decision`, incl. baseline and count-only default), `actions.py`
  (settings, subscriptions, link start/finish, unlink, test message,
  `deliver()`).
- Router `/api/char-alerts/` (settings, subscriptions, discord start/callback/
  delete, test), grant `char_alerts` in `ALL_TOOL_KEYS`, and `char_alerts` as a
  consuming tool of `skillqueue` and `mail` (sharing matrix). Frontend mirrors
  updated (`toolKeys.ts`, `esiRegistry.ts`).

Deliberate choices while building:
- Removing a character (`do_remove_token_character`, `admin.do_remove_user`)
  does NOT delete subscriptions, matching those actions' keep-data policy;
  `deliver()` refuses to send without a token holding the scope, so nothing is
  sent for a removed character. The scheduler job must also skip tenants whose
  registry entry is gone or suspended.
- Unlinking Discord switches all subscriptions off; a fresh opt-in resets
  `alert_state` (clean mail baseline, no stale dedupe key).

Still to do (needs the scheduler rework): the `alerts` job (skill queue tick
evaluation with one live re-check before sending, mail header poll with
backoff, `demand` feed into `do_sync_due`), the `alerts_job_enabled` switch and
interval field, and the frontend page `/character-management/alerts` (the
Discord callback already redirects there) plus its hub card and
`CHARACTER_MANAGEMENT_TOOL_KEYS` entry.
