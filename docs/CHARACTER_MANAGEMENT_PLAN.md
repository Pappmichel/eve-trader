# Character Management hub (Mail, Skills, Character Info, ...)

Status: **plan, nothing implemented.** Product decisions confirmed with the
user 2026-09-29. Revision 2 (same day) adds a code review of the plan against
the actual `esi_data/` / gate / SDE code: the "Review findings" section lists
what revision 1 got wrong and how each point is resolved; the phase sections
are now worked out file by file. Items marked **[decide]** still need the
user's answer before the phase that depends on them starts.

## Goal

A new Landing card **Character Management** leads to its own landing page
(`/character-management`) hosting several character-centric tools. It extends
`docs/ESI_ACCESS_PLAN.md` (registry, sharing, token selector, orchestrator,
fail-closed accessor) and introduces no new OAuth/token mechanism.

## Settled decisions (product)

1. **One grant per sub-tool.** The hub itself has no grant; its Landing card
   shows when the session holds any sub-tool grant.
2. **The existing Characters page moves into the hub.** `/characters` keeps
   working as a redirect.
3. **Mail is a real mail client: read and send.** Bodies are cached in the DB.
4. **Refresh button per sub-page** plus "last updated". The scheduler stays
   off (CLAUDE.md "Live operational decision"). Mail refreshes on open if its
   snapshot is older than a few minutes.
5. **All extras are in scope as later phases:** skillqueue guard,
   skill-vs-doctrine check, clones & implants, notifications, jump fatigue,
   contacts & calendar, wallet journal per character, skill-plan editor.

## Review findings (revision 1 → revision 2)

Each finding was checked against the code, not assumed.

**R1 - The registry allows exactly one character scope per data kind.**
`OwnedDataKind.character_scope` is a single string. The orchestrator's
`_required_scope()` returns that one scope, `select_auth_role(char, scope)`
picks a token by it, and `_scopes_for_kind_or_cap`, `do_access_preview`,
`fetcher_scope_map` and `frontend/src/esiRegistry.ts` all assume it.
Revision 1 planned `location` with three scopes and `clones` with two, which
does not fit. **Resolution:** keep the invariant "one kind = one scope"
(nothing in the selector/orchestrator/tests changes) and split: `location`,
`ship`, `online`, `clones`, `implants`. Rationale: a multi-scope kind would
need "token holding *all* scopes" selection plus partial-failure semantics,
touching the most load-bearing ESI code for a cosmetic gain. The Characters UI
groups them visually instead (R6).

**R2 - Stale clear would delete the mail archive.** `_record_failure` calls
`clear_stale_owner_kind`, which wipes the owner's partition once
`last_success_at` is older than `tier_hours × esi_stale_clear_multiples`
(frequent 1 h × 3 = **3 h**). With the scheduler off, the normal case is
"last sync yesterday". The first ESI hiccup on opening Mail would delete every
cached header and body. **Resolution:** mail tables are **not** registered in
`stale._KIND_TABLES` (same treatment `skills` already gets via its early
return). The stale-data concern of decision 6 is covered for mail by showing
the age in the UI. Removing cached mail is a separate, explicit user action
(R8).

**R3 - Sending mail must never auto-retry.** `ESIClient._post_response`
retries on timeouts and 500/502/503/504, and treats only `200` as success.
For `POST /characters/{id}/mail/` a retry after a timeout can **send the mail
twice**. ESI answers `201`, and the organize endpoints answer `204`, so every
call would currently be misread as a failure. It is also unauthenticated (no
`auth_role`). **Resolution:** new `ESIClient` write methods: `_write(method,
path, json, auth_role, expect=(201, 204))`. It retries only on 420/429
(request provably not processed) and never on transport errors or 5xx; those
map to an `ESIError` saying "delivery unknown, check Sent before resending".
PUT/DELETE (organize) are idempotent and may use the normal retry.

**R4 - Mail ids are shared across recipients.** One corp mail received by
three alts has the same `mail_id` for all three. Read state and labels
differ per character. A `(character_id, mail_id)` table with the body inline
stores the body three times, and the unified inbox shows the mail three
times. **Resolution:** split schema: `mail_messages` (per mail_id: sender,
subject, timestamp, recipients, body) plus `mail_character_headers`
(per character × mail_id: is_read, labels). Unified inbox groups by mail_id
and shows "received by: A, B".

**R5 - The sharing/grant naming collides.** A tool_key `skills` next to the
data kind `skills` makes `esi_sharing` rows read `('skills', 'skills')` and the
Characters matrix ambiguous. **Resolution:** tool keys get a prefix:
`char_skills`, `char_mail`, `char_info`, later `char_notifications`,
`char_contacts`, `char_skill_plans`. Data kinds keep their plain names.
**[decide]** exact names; this is the proposal.

**R6 - The Characters sharing table does not scale.** It renders one *column*
per data kind (`CharactersPage.tsx`, `CHARACTER_KINDS.map`). Going from 7 to
roughly 18 kinds makes it unusable. **Resolution:** Phase 0 reworks the table
into column groups ("Industry & Trading" and "Character") with collapsible
groups. `esiRegistry.ts` gets a `section` field; the registry mirror is still
hand-kept.

**R7 - The SDE has no dogma attributes.** `refresh_sde()` loads
`dgmTypeEffects.csv`, not `dgmTypeAttributes.csv` (CLAUDE.md's "verified
against dgmTypeAttributes.csv" was a manual check). The following need skill
requirements (attributes 182/183/184/1285/1289/1290 with levels
277/278/279/1286/1287/1288), skill rank (275) and primary/secondary
attributes (180/181):
- the doctrine skill check,
- the skill planner,
- prerequisite chains,
- training-time maths.

**Resolution:** extend `refresh_sde()` with `dgmTypeAttributes.csv`, filtered
*at import* to those attribute ids. The whole file is large and not needed.
It lands in two new global SDE tables, `sde_skill_requirements (type_id,
skill_id, level)` and `sde_skill_meta (skill_id, rank, primary_attr,
secondary_attr)`. Skill groups already work: `sde_types.group_id`, then
`sde_groups` with category 16.

**R8 - Removing a character does not remove data.** `do_remove_token_character`
keeps snapshots and sharing by design (decision 4 / reversible admin
operations). For mail that is surprising privacy-wise. **Resolution:** keep
the general rule, and add an explicit "Delete cached mail for this character"
action on the Mail settings (and offered in the remove-character confirm
dialog). It deletes `mail_character_headers` for that character, then
garbage-collects `mail_messages` no longer referenced by any header.

**R9 - Location/ship/online are not snapshot data.** Syncing them on a tier is
pointless (they are stale within minutes) and needs three tables.
**Resolution:** a *live* read on page open with a short class-level TTL cache
in `ESIClient` (the caching shape for per-call-constructed clients). It is
still gated by `is_shared(kind, 'char_info', 'character', id)` first, as
decision 9 covers live fetches too. There is no table, no freshness row and no
stale clear. The kinds exist in the registry only for scope and sharing; they
are flagged `live_only=True` so the orchestrator skips them (new field,
default `False`).

**R10 - One owner guard for everything.** The orchestrator's `_in_flight`
guard is per owner, not per (owner, kind). A Mail auto-refresh on open makes
a concurrent Skills refresh of the same character return `skipped:
in_flight`. **Resolution:** acceptable. The UI must render `in_flight` as
"sync already running", not as an error or a silent success. Mail's
refresh-on-open is debounced client-side and never fires while one is pending.

**R11 - Mail sync cost inside a batch session.** Each owner task holds one
pooled connection for its whole run (pool max 10, 4 workers). A first mail
backfill (for example 5,000 mails, 100 header pages) and per-mail body calls
must not run inside that. **Resolution:**
- The mail fetcher syncs **headers only**, incrementally. It pages newest to
  oldest until it hits a known `mail_id` older than the refresh window, and
  re-fetches a fixed window (the newest 500) for read and label changes.
- It stops at the retention cap.
- Bodies are fetched **lazily** in the `do_open_mail` action, outside the
  orchestrator: one call, cached.
- An optional "prefetch bodies" background job (`pipeline_runner`) is a later
  nice-to-have.

**R12 - Admin's ESI tool list is already out of sync.**
`AdminPage.tsx` `ESI_CONSUMING_TOOLS` lacks `portfolio`, which is a real
consumer since the Portfolio rework. Fix it in Phase 0 while adding the new
keys, and add a test that compares it against `consuming_tool_keys()` via a
small constant list.

**R13 - Read state vs. the game.** Opening a mail in the app does not mark it
read in EVE. That needs `PUT .../mail/{id}` with `organize_mail`.
**Resolution:** with the `mail_organize` capability, opening marks read in
EVE too. Without it, the local `is_read` is overwritten by the next sync.
The UI says so once.

**R14 - Attributes, extractable SP and training time.**
- `GET /characters/{id}/attributes/` uses the **same** scope as skills
  (`read_skills`), so the skills fetcher makes two calls and needs no new
  kind.
- Extractable SP is not an ESI field. It is computed:
  `max(0, (total_sp − 5,000,000) // 500,000)` extractors.
- Whether ESI's attribute values include implant bonuses must be
  **live-verified** before the planner computes training times from them.
- For queued skills, always show ESI's own `finish_date`. That covers
  Alpha/Omega speed. Planner estimates are labelled as estimates.

**R15 - The wallet journal only holds 30 days.** `esi_wallet_journal` is
replaced per owner on each sync, so it only ever holds ESI's 30-day window.
The per-character journal view says so. Long history would need an
accumulating table, which is out of scope unless asked.

## Grants and tool keys (proposal, R5)

| Sub-tool | tool_key | Consumes kinds | Phase |
|---|---|---|---|
| Characters (exists) | `characters` | none (manages access) | 0 |
| Character Info | `char_info` | `location`, `ship`, `online`, `standings`, `loyalty`, `wallet_balance`, `clones`, `implants`, `fatigue`, `wallet` (journal view) | 1, 5c, 7, 8 |
| Skills | `char_skills` | `skills`, `skillqueue` | 2, 5a, 5b |
| Mail | `char_mail` | `mail` | 3, 4 |
| Notifications | `char_notifications` | `notifications` | 6 |
| Contacts & Calendar | `char_contacts` | `contacts`, `calendar` | 8 |
| Skill plans | `char_skill_plans` | `skills`, `skillqueue` | 9 |

Only add a key in the phase that ships its router. A dead grant in Admin
confuses admins.

## Registry additions (R1, R9)

| Kind | Scope | Tier | live_only | Phase |
|---|---|---|---|---|
| `skills` (consumers extended) | `esi-skills.read_skills.v1` | rare | no | 2 |
| `skillqueue` | `esi-skills.read_skillqueue.v1` | normal | no | 2 |
| `mail` | `esi-mail.read_mail.v1` | frequent | no | 3 |
| `location` | `esi-location.read_location.v1` | - | yes | 1 |
| `ship` | `esi-location.read_ship_type.v1` | - | yes | 1 |
| `online` | `esi-location.read_online.v1` | - | yes | 1 |
| `standings` | `esi-characters.read_standings.v1` | rare | no | 1 |
| `loyalty` | `esi-characters.read_loyalty.v1` | rare | no | 1 |
| `clones` | `esi-clones.read_clones.v1` | rare | no | 5c |
| `implants` | `esi-clones.read_implants.v1` | rare | no | 5c |
| `notifications` | `esi-characters.read_notifications.v1` | frequent | no | 6 |
| `fatigue` | `esi-characters.read_fatigue.v1` | - | yes | 7 |
| `contacts` | `esi-characters.read_contacts.v1` | rare | no | 8 |
| `calendar` | `esi-calendar.read_calendar_events.v1` | normal | no | 8 |

New access capabilities (group 3: on/off per character, no tool, no
freshness), phase 4:
- `mail_send`: `esi-mail.send_mail.v1`
- `mail_organize`: `esi-mail.organize_mail.v1`

All kinds are group 2 (character only). No new DB constraint is needed:
`esi_sharing.data_kind`/`tool_key` have no CHECK, which was verified.

Public character info (portrait, corporation/alliance history, security
status, birthday) needs no scope. `character_public_info` already exists with
a class-level TTL cache. Corporation history is a new public call with the
same cache shape.

## Implementation, phase by phase

Every phase must satisfy the following before it counts as done:
- `pytest` green, frontend tests green;
- live-verified against the running backend, and in a browser for UI
  (CLAUDE.md);
- a deployment note listing which characters must re-authorize for the new
  scopes;
- any new `docs/*_schema.sql` registered in `deploy/deploy.sh`,
  `deploy/README.md`, root `README.md` and `.cursor/start.sh`. To find every
  place, grep `production_buy_list_schema`.

### Phase 0 - hub shell (no new ESI data)
Backend:
- `access_gate.ALL_TOOL_KEYS`: no new keys yet (R5). Keys arrive with their
  routers.
- No backend change for the move: `/api/characters/` stays and is still
  mapped in `_TOOL_PATH_PREFIXES`.

Frontend:
- `pages/character_management/CharacterManagementHub.tsx`: extract `ToolCard`
  from `Landing.tsx` into `components/ToolCard.tsx` and reuse it. Show only
  the cards the session holds.
- `Landing.tsx`: replace the Characters card with a **Character Management**
  card. Show it if `tools` is undefined (still loading or gate off) or
  contains any key of a shared `CHARACTER_MANAGEMENT_TOOL_KEYS` constant.
- `App.tsx`:
  - add routes `/character-management` (hub) and
    `/character-management/characters`;
  - make `/characters` a `<Navigate replace>`, so old bookmarks and the
    `?auth=success` handling keep working (the OAuth callback redirects to `/`
    with query params, which is unaffected);
  - update `Portfolio.tsx`'s link and `QuickNav.tsx` `PATHS`.
- `CharactersPage.tsx`:
  - implement the column groups (R6);
  - add `section` to `esiRegistry.ts`;
  - render `skipped: in_flight` properly (R10).
- `AdminPage.tsx`: fix `ESI_CONSUMING_TOOLS` (R12) and move
  `ALL_TOOL_KEYS` / `ESI_CONSUMING_TOOLS` into one shared TS module that the
  hub also reads.

Tests:
- `Landing.ui.test.tsx`: hub card visibility for any, none and undefined
  tools.
- A redirect test.
- A CharactersPage grouping test.
- Admin auto-tick including `portfolio`.

### Phase 1 - Character Info (`char_info`)
Backend:
- `access_gate.ALL_TOOL_KEYS` gains `char_info`. `_TOOL_PATH_PREFIXES` gains
  `/api/char-info/`.
- `esi_data/registry.py`:
  - add `location`, `ship`, `online`, `standings` and `loyalty`;
  - add the `live_only` field to `OwnedDataKind`, and have
    `_kinds_for_owner` skip `live_only` kinds;
  - add `char_info` to `wallet_balance`'s consumers.
- `esi_client.py`:
  - add `character_location`, `character_ship`, `character_online`
    (class-level 60 s TTL cache, keyed per character);
  - add `character_standings`, `character_loyalty` and
    `character_corporation_history` (public, TTL cache).
- `esi_data/fetchers.py`: `fetch_character_standings` and
  `fetch_character_loyalty` write to new tables. Register both in `FETCHERS`
  and in `stale._KIND_TABLES`; these are fine to clear.
- `esi_data/access.py`: add `_read_standings` and `_read_loyalty` branches in
  `read_esi`.
- New `docs/character_management_schema.sql` with `character_standings`
  (`owner_character_id, from_id, from_type, standing`) and
  `character_loyalty` (`owner_character_id, corporation_id, loyalty_points`).
  Both have `tenant_id` and RLS, and PKs `(tenant_id, owner_character_id,
  ...)`, because two tenants can hold the same character.
- New package `eve_trader/character_management/` with `__init__.py` and
  `info_actions.py`:
  - `do_list_character_overview()` combines public info, wallet balance (via
    `read_esi('wallet_balance', 'char_info')`) and shared live location.
  - `do_character_detail(character_id)`.
  - Every live call is preceded by `is_shared`. If it is not shared, the
    field is `None` plus a `"not_shared"` marker, never a silent fetch.
- The same package must not be imported by `esi_data`: the
  `test_importing_registry_does_not_load_tool_packages` rule.
- `api/routers/char_info.py` uses the `_wrap` helper and imports the module
  object: `from ...character_management import info_actions` (CLAUDE.md
  testing convention).
- `POST /api/char-info/sync` calls `esi_data.do_sync_for_tool('char_info')`.

Frontend:
- `pages/character_management/info/`:
  - an overview table of all characters;
  - a detail drawer;
  - a Refresh button with "last updated" from `/api/characters/freshness`.

Tests:
- registry (live_only, consumers);
- the accessor fails closed for the new kinds;
- a router grant test (403 without `char_info`);
- the fetcher writes and stale-clear behaviour.

### Phase 2 - Skills (`char_skills`)
- The registry gets `skillqueue` and adds `char_skills` to the `skills`
  consumers.
- `fetch_character_skills` writes job slots unchanged (Production depends on
  them) and additionally:
  - replaces the character's rows in the new `character_skills` table
    (`skill_id`, `active_level`, `trained_level`, `sp`);
  - calls `/attributes/` (same scope, R14) and stores the result in
    `character_attributes`, along with `total_sp` and `unallocated_sp`.
- Stale clear: `skills` currently returns early. Keep that for
  `character_slots` (issue #39), but let the new tables be cleared by
  splitting the early return per table.
- `fetch_character_skillqueue` writes `character_skillqueue`.
- SDE extension (R7): `dgmTypeAttributes.csv` is fetched in `refresh_sde()`
  and filtered while parsing. It feeds `sde_skill_requirements` and
  `sde_skill_meta` in the same schema file. These are global SDE tables, not
  RLS, following the `sde_*` precedent in `phase1_schema.sql`.
  `storage.replace_sde` gains them, and `get_sde_*` caches are cleared the
  same way.
- `character_management/skills_actions.py`:
  - skill tree grouped by SDE group (category 16);
  - per-character and all-characters matrix;
  - total SP;
  - extractable SP (R14);
  - queue with ESI finish dates.
- Production's time-bonus config stays manual (CLAUDE.md, explicitly
  declined).

### Phase 3 - Mail read (`char_mail`)
- Schema (R4): see the table sketch below.
  - `mail_messages` (`tenant_id, mail_id, from_id, subject, timestamp,
    body NULL, body_fetched_at`)
  - `mail_recipients` (`tenant_id, mail_id, recipient_id, recipient_type`)
  - `mail_character_headers` (`tenant_id, character_id, mail_id, is_read,
    labels INT[]`)
  - `mail_labels` (`tenant_id, character_id, label_id, name, color,
    unread_count`)
  - `mail_lists` (`tenant_id, character_id, list_id, name`)
  - Indexes on `(tenant_id, character_id, timestamp desc)`, plus a
    `to_tsvector('simple', subject || body)` GIN index for search.
- The fetcher (R11) runs incrementally and writes headers, labels and lists
  inside the owner batch. There is **no** `stale._KIND_TABLES` entry (R2).
- Retention config: `CharMailConfig.max_mails_per_character`, default 5000,
  mapped in `_FIELD_RANGES`. Mails older than the cap are pruned after the
  sync.
- Actions (`mail_actions.py`):
  - `do_list_folders` (Inbox, Sent, Corp, Alliance, mailing lists and labels,
    with unread counts);
  - `do_list_mails(folder, character_id|None, cursor, q)`;
  - `do_open_mail(character_id, mail_id)`: lazy body fetch outside any batch
    session;
  - `do_delete_cached_mail(character_id)` (R8).
- Every read uses `shared_owner_ids('mail', 'char_mail', 'character')`.
  Mails visible only through an unshared character are hidden. A message
  shared through at least one shared recipient is visible, with only that
  character's header.
- Names come from `resolve_names` (existing, batched). The cache is
  class-level, the same shape as structure names, or a small
  `eve_names` table.
- Privacy: no subjects or bodies in logs or `error_log.py` (review this
  explicitly). The Characters page note on the `mail` row says: "stores
  message bodies in this tenant's database".
- Frontend: a three-pane layout (folders, list, reader).
  - Unified inbox with a character filter chip.
  - Search box.
  - Refresh button, plus auto-refresh on open when the last sync is older
    than 5 minutes (config), debounced (R10).
  - EVE mail bodies are HTML-ish (`<font>`, `<a href="showinfo:...">`), so
    render them through a whitelist sanitizer (for example DOMPurify with
    allowed tags). `showinfo:` links become plain text or an internal link.
    **Never** use `dangerouslySetInnerHTML` unsanitized. This is XSS from any
    EVE player who can send you mail.

### Phase 4 - Mail write
- Capabilities `mail_send` and `mail_organize` go into
  `ACCESS_CAPABILITIES`, the `esiRegistry.ts` mirror, and the capabilities
  table on the Characters page.
- `ESIClient._write` (R3), plus:
  - `send_mail` (never retried);
  - `update_mail(read, labels)`;
  - `delete_mail`;
  - `create_label` and `delete_label`.
- Actions:
  - `do_send_mail(from_character_id, recipients, subject, body,
    reply_to_mail_id?)`;
  - `do_mark_read`;
  - `do_set_labels`;
  - `do_delete_mail`;
  - `do_create_label` and `do_delete_label`.
- Each action checks:
  - the character is shared with `char_mail`;
  - the capability flag is set;
  - `select_auth_role(char, scope)` is not `None`, otherwise an
    `ActionError` telling the user to re-authorize with Send or Organize.
- Recipient resolution uses `/universe/ids` (`_post_universe_ids` exists).
  Mailing-list recipients must be in the sender's own `mail_lists`.
- Validate ESI's limits up front with a clear error:
  - at most 50 recipients;
  - subject at most 1,000 characters;
  - body at most 10,000 characters.
- Surface these ESI errors as `ActionError`:
  - CSPA charge (`approved_cost`): the UI asks for confirmation and resends
    with the cost;
  - rate limits;
  - blocked recipients.
- After a successful send, insert the mail into the local Sent folder right
  away (ESI returns the new `mail_id`).
- Compose UI:
  - reply, reply-all and forward (quote the original body);
  - a recipient autocomplete with character, corporation, alliance and list
    chips;
  - a sender selector limited to characters with `mail_send`.
- CSRF: the existing unsafe-method protection in `api/app.py` applies. Check
  the new POST, PUT and DELETE routes against it.

### Phase 5
- **5a Queue guard:** config `queue_warning_hours`, default 24. Warnings go
  to the Skills page and as a hub card badge (same mechanism as Admin's
  pending badge; the count is computed server-side). Push, Discord or email
  stays deferred (CLAUDE.md).
- **5b Doctrine skill check:** read fittings via storage (a doctrine storage
  reader, no import of `eve_trader.doctrine` internals). Collect the required
  skills of the ship and every fitted type from `sde_skill_requirements`,
  expanded recursively through skill prerequisites. Compare them against
  `character_skills`. Output per fitting: characters who can fly it, and for
  the others the missing skills with an estimated training time (R14 caveat).
- **5c Clones & implants:** add the `clones` and `implants` kinds plus
  tables. Show them on Character Info. Location names come from the existing
  resolution chain.

### Phase 6 - Notifications (`char_notifications`)
- Add the `notifications` kind plus a table.
- The ESI `text` is YAML: parse the known types (structure attacked or
  reinforced, war declared, sov) into readable lines, and fall back to the
  raw type name.
- Filter by type. A read/unread flag is local only (ESI has no write).

### Phase 7 - Jump fatigue / timers
- Add `fatigue` (`live_only`).
- Show countdowns on Character Info. The jump clone cooldown comes from
  `clones.last_clone_jump_date`.

### Phase 8 - Contacts & calendar (`char_contacts`), wallet journal
- Contacts and calendar are read-only. Calendar event details are one call
  per event, fetched lazily like mail bodies.
- The wallet journal view goes into Character Info through the existing
  `wallet` kind (`char_info` added to its consumers). It shows the 30-day
  window only (R15).

### Phase 9 - Skill-plan editor (`char_skill_plans`)
- Tenant-scoped `skill_plans` and `skill_plan_items` (`plan_id, position,
  skill_id, level`).
- Import and export in the EVE client skill plan text format.
- Adding a skill auto-inserts its missing prerequisites (R7 tables).
- Training time per character from attributes and SDE rank (R14:
  live-verified attribute semantics first).
- Progress per character, plus the next step as a suggested queue order.

## Open items [decide]

1. **Tool-key names** (R5): `char_info`, `char_skills`, `char_mail`, and so
   on, or others.
2. **Split scopes into single-scope kinds** (R1): the recommendation. The
   alternative is multi-scope kinds, which means reworking the selector.
3. **Mail archive never auto-cleared** (R2), with only explicit deletion
   (R8): the recommendation.
4. **Location/ship/online as live reads without storage** (R9): the
   recommendation.
5. **Mail retention default:** 5,000 per character, or unlimited.
