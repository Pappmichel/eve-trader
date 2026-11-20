# Character Management hub and Discord alerts

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Character Management hub

Full phase-by-phase history/decisions live in `docs/CHARACTER_MANAGEMENT_
PLAN.md` (926 lines, all 10 phases done as of 2026-09-29) - this section is
the CLAUDE.md-level summary that section itself flagged as missing in a
2026-10-01 code review (a repo audit that only reads this file would
otherwise never learn this hub exists at all).

**Seven tool_keys, one `char_` prefix, one module.** `char_info`,
`char_skills`, `char_mail`, `char_notifications`, `char_contacts`,
`char_skill_plans` and `char_alerts` (Discord alerts, see "Discord alerts"
below) each gate their own router/page -
decision 8 in the plan: a `char_` prefix specifically so a tool_key never
collides with a data-kind key like `"skills"` (`esi_data/registry.py`'s
`OwnedDataKind.key`). All seven live in `ALL_TOOL_KEYS` (19 grants total
now, with the PI tool's `pi` and the Asset Manager's `asset_manager`) and are consuming tools in `esi_data/registry.py` like any other tool.
Every sub-tool's `do_*` actions live in `eve_trader/character_management/`
(`info_actions.py`, `skills_actions.py`, `mail_actions.py`/`mail_write.py`/
`mail_archive.py`, `notification_actions.py`, `contacts_actions.py`,
`skill_plan_actions.py`/`skill_plan_logic.py`, `skill_check.py`), each with
its own thin router under `api/routers/char_*.py` - same `do_*`/router/RLS
pattern as every other tool, not a special case.

**`fields.py` is the shared gate/state vocabulary every sub-tool builds
on.** Three states cover every value a page can show: `not_shared` (the
kind isn't shared with this tool for this character - nothing was read),
`reauth_needed` (shared, but no stored token holds the kind's scope), and
either `ok`/`not_synced`/`error` for the actual data. `fields.gate()` is the
one function that decides which of the first two applies; `fields.live()`
wraps a live ESI call in that gate plus ESI-failure redaction
(`esi_failure()` - only the HTTP status or "network error" ever surfaces,
never the response body, since some of this is mail); `fields.snapshot()`
does the same for a kind read through `read_esi()`. One dead token or one
missing share never blanks a whole page - only that one field, on that one
character.

**`OwnedDataKind.live_only`** (registry.py): a kind with a scope and
sharing rows (so the Characters page can grant it) but deliberately no
snapshot table, no freshness row, no stale clear - `location`, `ship`,
`online`, `fatigue`, `contacts`, `calendar` and `mail` are all `live_only`.
`read_esi()` raises `AccessorError` for one of these (never returns `[]`,
which would read as "shared with nobody" instead of "there is no snapshot
to read at all") - the caller must gate on `is_shared()`/`fields.gate()`
and read ESI directly instead. The orchestrator drops `live_only` sharing
rows before deriving owner tasks entirely, so a character sharing only a
live kind never gets an owner task, never counts as "attempted", and never
triggers the NULL-owner-id sweep. `ESIClient`'s own live-read cache
(`_live_character_read`, 60s-10min TTL depending on the kind) keys on
`(tenant_id, what, character_id, *extra)` - the tenant_id is load-bearing:
two tenants can each hold a token for the same real EVE character, and an
unscoped cache key would leak one tenant's private read (and its sharing
decision) to the other.

**Mail (phase 3-4) is live-by-default, archive is opt-in.** Every character
shared with `char_mail` is read straight from ESI on every list/open,
nothing stored, unless that character's own "Archive mail" checkbox is on
(`char_mail_archive_settings`) - then `mail_archive.py` runs a resumable
background backfill (own daemon thread per character, not `pipeline_
runner`, since a first backfill of an old mailbox is thousands of pages/
bodies and must not hold one pooled DB connection for the whole run) and
mirrors reads/writes into the archive going forward. Turning the archive
off **deletes** it (the one destructive path here, confirmed-count-then-
confirm gated). Mail bodies are HTML-ish text written by any player who can
mail you - `frontend/src/mailHtml.ts` (DOMPurify, strict tag allowlist,
non-http(s) hrefs stripped, `rel="noopener noreferrer nofollow"`) is the
one place a mail body becomes DOM markup; nothing else in the app may
`dangerouslySetInnerHTML` a mail body. Writes (phase 4: send/mark-read/
labels/delete) are gated by a *capability* tick (`mail_send`/
`mail_organize` on the Characters page - explicit consent this app may act
for the character, separate from and in addition to the `char_mail` share)
plus a token holding the write scope (`fields.capability_ready`).
`ESIClient._write` splits write failures by whether a retry is safe:
420/429 always retries (ESI never processed the request), a transport
error/5xx retries only for an idempotent PUT/DELETE, and a non-idempotent
POST (sending a mail, creating a label) raises `ESIDeliveryUnknown` instead
- surfaced as "check the Sent folder before sending it again", never
silently retried (a retry after a timeout could double-send).

**Skills (phase 2/5b/9)** adds per-skill rows, attributes and SP totals
(`character_skills`/`character_attributes`, `char_skills`/
`char_skill_plans` tool_keys) next to - never instead of - the
`character_slots` row Production/Station Trading already read from the
same `skills` data kind; same kind, two tools, two row shapes, each under
its own sharing row (`esi_data/access.py`'s `_read_character_skills` vs.
`_read_skills`). `pi` always gets per-skill rows; Production, Trading and Station
Trading get them with `table="skills"` (Trading/Station Trading for the fee
hints, which also make `trading`, `station_trading` and `pi` consumers of the
on-demand `standings` kind). Needs two new global SDE tables (`sde_skill_requirements`/
`sde_skill_meta`, filled by streaming-filtering `dgmTypeAttributes.csv`
during an SDE refresh - see `production/sde.py`'s `skill_rows_from_
attributes`) for skill prerequisites/rank/attributes, used by both Skill
Plans' prerequisite expansion (`skill_plan_logic.py`, pure, no I/O) and the
Doctrine skill check (`skill_check.py`: which of my characters can fly
which doctrine fitting - reads Doctrine's stored fittings via `storage`
directly, never importing the doctrine package, and its route additionally
requires the `doctrine` grant checked against `request.state.tool_keys`,
since a `char_skills` grant alone must not expose another tool's fittings).

**Discord alerts.** Implemented and live in production since 2026-10-02 (test DM confirmed;
scheduled 2026-09-29): bot DMs
to an OAuth-linked Discord account, tool_key `char_alerts`, opt-in per
character x alert type (`skillqueue_empty`, `mail_new`, `contract_status`, and
the PI types). Code lives in
`eve_trader/alerts/` (`discord_client.py`, pure `logic.py`, `actions.py` with
`deliver()` as the one send path that re-checks opt-in/sharing/token at send
time), router `/api/char-alerts/`. Bot token and OAuth client are operator env
variables (`DISCORD_*`, see `.env.example`), never stored per tenant. The
`alerts` job (`alerts/runner.py`, hooked in `scheduler._check_and_run_alerts_job`)
runs only with the operator switch `alerts_job_enabled`, independent of every
tenant's `scheduler_enabled` and of tenant inactivity
(`tenant_eligibility.alerts_allowed`: gate on -> registered, not suspended,
holds `char_alerts`). Frontend: `/character-management/alerts`.
Failed attempts are stamped with the failure time (`runner._failure_time()`), not
the run's start `now`; three ESI failures in a row (`_RunHealth`) abort the
subscription loop (`{"ran": True, "aborted": "esi_failures"}`, the rest stays
unstamped). The alerts job runs via `_run_job(..., wait=False)`: the tick never
waits for it, the worker writes the status itself, and the per-(tenant, job)
guard holds until the thread ends.
`runner.run_for_tenant` guards itself per tenant (`_try_begin_tenant`/
`_end_tenant`, same shape as `esi_data.orchestrator`'s own per-owner
`_try_begin_owner`/`_end_owner`, one level up) - confirmed real gap fixed
2026-10-01: `scheduler._run_job` abandons a job thread after
`JOB_TIMEOUT_SECONDS` rather than waiting for it, so a slow run could still be
mid-flight when the next 5-minute tick started a second one for the same
tenant, and both would read `alert_state` before either wrote it - a real
double-DM risk, not just a wasted ESI call.

Link, logout and text safety. Linking a Discord account needs a login younger
than 10 minutes (`DISCORD_LINK_FRESH_LOGIN_SECONDS`, gate on only; the gate puts
`session_issued_at`, `character_id` and `character_name` on `request.state`);
otherwise `fresh_login_required` (start 400, callback redirect
`?discord=fresh_login_required`). Moving the link to another Discord account
first DMs the old one (failure only logged). After a logout/revocation newer
than `alert_destinations.linked_at` (`storage.latest_session_revocation`) mail
text is left out of DMs - sender and subject still go out - until Discord is
linked again; `deliver(..., has_content=True)` refuses content while paused and
the settings carry `mail_content_paused`. Every third-party string (names,
subjects, titles, places, mail text) goes through `logic.md_escape` after
shortening and before composing; `send_dm` sets `flags: 4` (no embeds).

Operator setup: `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`
and optionally `DISCORD_REDIRECT_URI` (see `.env.example`); the redirect URI
(default `FRONTEND_ORIGIN` + `/api/char-alerts/discord/callback`) must be
registered in the Discord developer portal. The bot is one shared application,
so only one server should run the alerts job (`alerts_job_enabled`) or two
servers would send duplicate DMs for the same subscriptions. Alert design
rules: opt-in per character x alert type, default off; opt-in never replaces ESI
sharing (re-checked at send time); `skillqueue_empty` default lead time 12 h,
sent once per `finish_date`, with one live re-check before sending;
`mail_new` sends count, sender and subject, the mail text only with the separate
`include_content` opt-in, and takes a baseline on first poll so old mail is never
announced; `mail` stays `live_only` (polled by the alerts job, not
`do_sync_due`); `contract_status` polls the character's contract list live every
`alerts_contract_poll_minutes` (15) and announces each contract the character
issued once when it finishes or passes its expiry (`contract_alert_state`,
first poll = baseline); its "Doctrine only" filter matches a contract the
Doctrine snapshot holds no decision for live from its ESI items, and leaves
it unseen when they cannot be read (a dropped event is marked seen for good); messages disable mentions and are English-only, no quiet hours.
Contract alert filters (GH #382) are opt-in columns on the `contract_status`
subscription row (`contract_*`, defaults = unfiltered): finished / expired
switches, a minimum interval between DMs (15-1440 min), "only valid Doctrine
fits" (`doctrine.engine.contract_doctrine_matches`, valid/tolerable in the
`doctrine_contracts` snapshot) and "only when a sale empties a fitting"
(`sold_out_by_contract`). Both Doctrine filters need the `doctrine` grant
(checked on save and at send time; without it they let nothing through). A
filtered-out event is marked seen and never announced; an event held back by
the interval stays unseen and goes out with the next DM; a failed Doctrine
lookup with a Doctrine filter on marks nothing and retries next poll.

**Scheduler integration** is covered by the Scheduler section above
(`schedule_mode = "on_demand"`, `useSyncWhenStale.ts`'s page-open sync) -
not repeated here.
