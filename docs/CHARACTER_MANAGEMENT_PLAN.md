# Character Management hub (Mail, Skills, Character Info, ...)

Status: **plan, nothing implemented.** Decisions below were confirmed with the
user (2026-09-29); the "Open details" section lists what is still to be
settled before/while building.

## Goal

A new Landing card **Character Management** leads to its own landing page
(`/character-management`) that hosts several character-centric tools. It
extends `docs/ESI_ACCESS_PLAN.md` (the `esi_data/` package, registry, sharing,
fail-closed accessor); it does not introduce a new OAuth/token mechanism.

## Settled decisions

1. **One grant per sub-tool.** New `tool_key`s: `skills`, `mail`,
   `character_info` (later phases add more, see below). The existing
   `characters` grant stays and becomes one more sub-tool of the hub. The hub
   card on Landing is shown when the session holds *any* of the hub's
   sub-tool grants; it has no grant of its own (no `character_management`
   key). `ALL_TOOL_KEYS` (`access_gate.py`), the frontend's hand-kept copy
   (`AdminPage.tsx` `UserToolCheckboxes`), `_TOOL_PATH_PREFIXES`
   (`api/app.py`) and `/api/gate/status` `tools` all grow accordingly.
   Admin auto-tick (ESI_ACCESS_PLAN decision 11) generalises: any hub
   sub-tool that consumes ESI data ticks `characters` in the UI only, the
   server stays replace-not-merge.
2. **Characters page moves into the hub.** Route `/characters` stays as a
   redirect to `/character-management/characters`; Landing loses its own
   Characters card. API prefix `/api/characters/` is unchanged (no
   breakage of the OAuth `reauth/start` / `add/start` return URLs - update
   their post-login redirect target though).
3. **Mail is a real mail client: read *and* send**, not a read-only viewer.
   Bodies are cached in the DB (see Mail). Needs the additional scopes
   `esi-mail.send_mail.v1` and `esi-mail.organize_mail.v1`.
4. **Refresh button per sub-page** plus a "last updated" stamp; the scheduler
   stays off (CLAUDE.md "Live operational decision"). Mail additionally
   refreshes on open when its snapshot is older than a few minutes
   (config field, default 5). All refreshes go through the existing
   orchestrator (`do_sync_for_tool` / a per-kind variant), never a
   sub-tool-private fetch loop.
5. **All extras are in scope**, as later phases: skillqueue guard,
   skill-vs-doctrine check, notifications, clones & implants, jump
   fatigue, contacts & calendar, wallet journal per character, skill-plan
   editor.

## Layout (frontend)

- `frontend/src/pages/character_management/` - `CharacterManagementHub.tsx`
  (card grid, same `ToolCard` component pattern as `Landing.tsx`, cards
  filtered by `gateStatus.tools`), plus one folder per sub-tool with its own
  `*Layout.tsx` (sub-nav) like Trading/Production/Doctrine.
- Routes under `/character-management/{characters,skills,mail,info,...}`;
  add QuickNav shortcuts.
- A shared **character switcher** (all of the tenant's registered
  characters, "All" where it makes sense) used by Skills/Mail/Info so the
  three pages behave the same.

## Backend layout

- New package `eve_trader/character_management/` with `actions.py`
  (`do_*` per sub-tool, UI-agnostic, `ActionError` for failures) - the
  usual actions/router/CLI parity. Routers: `api/routers/skills.py`,
  `mail.py`, `character_info.py` (+ existing `characters.py`).
- Raw ESI reads go through `esi_data.read_esi` with the sub-tool's own
  `tool_key` as consumer, so Characters-page sharing rows govern them
  exactly like every other tool (decision 4 of the ESI plan applies:
  derived data is not re-filtered).
- New schema file `docs/character_management_schema.sql`, every table with
  `tenant_id` + RLS + `tenant_isolation` policy (copy `phase1_schema.sql`
  shape). **Register the file everywhere** (CLAUDE.md "A brand-new schema
  file"): `deploy/deploy.sh` loop, `deploy/README.md`, root `README.md`,
  `.cursor/start.sh`. Grep an existing filename (e.g.
  `production_buy_list_schema`) to find them all.

## New / changed ESI data kinds (`esi_data/registry.py`)

Owned data kinds are character-only (group 2) unless noted.

| Kind | Scope(s) | Tier | Consumers |
|---|---|---|---|
| `skills` (exists; consumers extended, full levels now stored) | `esi-skills.read_skills.v1` | rare | + `skills`, `character_info` |
| `skillqueue` | `esi-skills.read_skillqueue.v1` | normal | `skills` |
| `mail` (headers, labels, lists; bodies lazy) | `esi-mail.read_mail.v1` | frequent | `mail` |
| `location` (system/station, ship, online) | `esi-location.read_location.v1`, `read_ship_type.v1`, `read_online.v1` | frequent | `character_info` |
| `clones` (+ implants) | `esi-clones.read_clones.v1`, `esi-clones.read_implants.v1` | rare | `character_info` |
| `standings` | `esi-characters.read_standings.v1` | rare | `character_info` |
| `loyalty` | `esi-characters.read_loyalty.v1` | rare | `character_info` |
| `notifications` (phase 6) | `esi-characters.read_notifications.v1` | frequent | `notifications` |
| `fatigue` (phase 7) | `esi-characters.read_fatigue.v1` | normal | `character_info` |
| `contacts`, `calendar` (phase 8) | `esi-characters.read_contacts.v1`, `esi-calendar.read_calendar_events.v1` | rare | `contacts` |

Public character info (portrait, corp/alliance history, security status,
birthday) needs no scope and no sharing row; it is fetched with the plain
public endpoints and cached with a TTL like `corporation_public_info`.

**Write scopes are not data kinds.** `esi-mail.send_mail.v1` and
`esi-mail.organize_mail.v1` are *action capabilities* (group 3 shape: on/off
per character, no tool dimension, no freshness), added to
`ACCESS_CAPABILITIES`; the re-auth scope union includes them only when
ticked. Sending mail is a user-initiated write to ESI, never done by a
sync.

The wallet-journal view (phase 8) reuses the existing `wallet` kind
(`esi_wallet_journal`); it only adds `character_info`/a `wallet_journal`
consumer, no new scope.

## Sub-tools

### Character Info (`character_info`) - phase 1
Per-character overview page: portrait, corp/alliance + history, security
status, wallet balance (existing `wallet_balance` kind, needs `character_info`
added as consumer), current location/ship/online, standings, LP, clones &
implants (phase 5). All-characters summary table on top, detail card per
character.

### Skills (`skills`) - phase 2
- Fetcher change: `fetch_character_skills` keeps writing job slots
  (Production still needs them) *and* stores every skill row (new table
  `character_skills`: character_id, skill_id, active/trained level, SP)
  plus attributes/unallocated SP/extractable SP if the endpoint provides it.
- Page: skill tree grouped by SDE skill group (names/groups come from
  existing `sde_*` tables - check that `refresh_sde()` already carries skill
  groups; extend it if not, per the "prefer SDE columns" rule), per-character
  or all-characters matrix, total SP, skillqueue with finish times.
- Skill levels for the *Production* time-bonus stay the flat
  `ProductionConfig` fields (CLAUDE.md "Job-time character-skill bonus",
  explicitly declined to change) - this tool does not silently rewire that.

### Mail (`mail`) - phase 3 (read) / phase 4 (write)
Built like a real client:
- **Read (phase 3):** folder list (Inbox, Sent, Corp, Alliance, Mailing
  Lists, custom Labels) with unread counts, message list (paged by ESI's
  `last_mail_id` cursor), reading pane, unified inbox across all
  registered characters plus per-character filter, search (sender, subject,
  and body over the cached mails), attachments-free (EVE mail has none).
- **Storage:** `mail_headers`, `mail_bodies`, `mail_labels`,
  `mail_recipients`, `mail_lists` (per tenant, keyed by
  `(character_id, mail_id)` - mail ids are per character). Bodies are
  fetched lazily on first open (1 ESI call per mail) and cached; a
  retention/size limit (config, default e.g. 5,000 mails per character)
  bounds growth. Failed fetches follow ESI_ACCESS_PLAN decision 6 (keep rows
  until the stale-age limit).
- **Write (phase 4):** compose/reply/reply-all/forward (recipients:
  character, corporation, alliance, mailing list; up to ESI's per-mail
  recipient cap), name -> id resolution via `/universe/ids`, mark
  read/unread, delete, create/delete labels, assign labels. Every write
  goes through a `do_*` action, requires the `send_mail` /
  `organize_mail` capability on the sending character and re-checks it
  server-side (fail closed, clear 400 telling the user to re-authorize with
  that scope). Surface ESI's failure modes plainly (insufficient ISK for
  CSPA charge, rate limit, blocked recipient) as `ActionError`.
- **Sensitivity:** mail is the most private kind here. Sharing row
  (`mail`, consumer `mail`) is opt-in per character on the Characters page
  like any other kind; the Characters UI gets a short note that
  the mail sync stores message bodies in the tenant's database. No mail
  content in logs or error messages (`error_log.py`).

### Skillqueue guard - phase 5a
Warn on the Skills page/hub when a character's queue ends within X hours
(config, default 24) or is empty. UI-only at first (no push); Discord/e-mail
delivery stays under "Deferred, not rejected" in CLAUDE.md - do not build it
without asking.

### Skill check vs. Doctrine - phase 5b
"Which of my characters can fly fitting X?" Cross-tool: Doctrine owns the
fittings, Skills owns the levels. Required skills per fitting come from the
SDE (`dgmTypeAttributes` skill-requirement attributes of ship + every fitted
module/charge/drone); compare against `character_skills`. Lives in the
Skills tool, reads Doctrine's stored fittings through storage (no import of
the doctrine package's internals beyond a storage reader). Output: per
fitting a table of characters with "can fly / missing skills + training
time".

### Clones & implants - phase 5c
Part of Character Info: home station, jump clones with locations and
implants, active implants, jump-clone cooldown. Location names go through the
existing structure-name resolution chain.

### Notifications - phase 6
New sub-tool `notifications` (own grant): structure attacks, war decs,
etc., with type filter and read/unread; snapshot-based, no push.

### Jump fatigue / timers - phase 7
Part of Character Info (jump fatigue expiry, jump-activation cooldown, clone
cooldown) as countdowns.

### Contacts & calendar - phase 8 (`contacts`, own grant)
Read-only contact list with standings/labels and calendar events.
Write actions (add/delete contacts) are explicitly *not* included.

### Wallet journal per character - phase 8
Filterable journal view per character (ref types, date range, running
balance) on Character Info; read from `esi_wallet_journal`.

### Skill-plan editor - phase 9
Named plans per tenant (ordered skill@level targets), import/export in the
in-game plan text format, training time from real attributes + implants
(needs the attribute/implant data from phases 2/5c), per-character progress
and remaining time, "add to queue order" suggestion. Largest item; can be
split further when reached.

## Phases (each independently shippable, live-verified per CLAUDE.md)

0. **Hub shell:** grants, `ALL_TOOL_KEYS`/prefixes/gate status, Landing card,
   hub page, move Characters, redirect, Admin checkboxes, tests
   (`Landing.ui.test.tsx`, router grant tests).
1. **Character Info** (public info + location/wallet/standings/LP).
2. **Skills** (+ skillqueue, attributes).
3. **Mail read** (sync, folders, reader, search).
4. **Mail write** (capabilities, compose, organize).
5. **5a** queue guard, **5b** doctrine skill check, **5c** clones/implants.
6. **Notifications.**
7. **Jump fatigue/timers.**
8. **Contacts/calendar, wallet journal.**
9. **Skill-plan editor.**

Each phase that adds a kind also: registry entry (+ `fetcher_scope_map`),
fetcher + orchestrator wiring, schema, `do_*` actions, router, frontend page,
Characters-page scope/sharing rows (`esiRegistry.ts` mirror), tests, and a
post-deploy note on which characters must re-authorize (new scopes always
require a re-auth; list them in a deployment checklist like the ESI plan's).

## Open details (settle while building, not blockers)

- Exact mail retention default and whether body prefetch (vs. lazy only) is
  worth the ESI call volume for the unified inbox search.
- Whether `character_info` and `skills` sync share one "Refresh" that
  fans out per owner (likely yes: one `do_sync_for_tool(tool_key)` call).
- Naming of the hub in the UI (German/English: "Character Management").
- Whether Production's slots (from the skills fetcher) should read the new
  `character_skills` table instead of being written directly (nice-to-have
  cleanup, not needed for phase 2).
