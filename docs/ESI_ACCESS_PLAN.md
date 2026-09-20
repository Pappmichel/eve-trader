# Character-Centric ESI Access for eve_trader

> Durable, repo-tracked copy of the approved architecture plan. Same role as
> `docs/MULTI_TENANT_PLAN.md`: the "why" of a multi-phase change that will
> otherwise get re-derived (and accidentally re-litigated) from scratch.
> `CLAUDE.md` stays the terse day-to-day reference and will be updated as
> part of the implementation (Phase 0 / module layout), pointing back here
> for anything non-obvious. See `HANDOFF.md` at the repo root for a current
> progress checkpoint if one exists.

This document is the plan, not the implementation. No code lands because of
this file; each phase below is a later session's work.

## Context

`role_prefix` (`buyer` / `seller` / `producer` / `doctrine` /
`doctrine-assets` / `trader`, plus identity-only `gate`) currently answers
three independent questions with one string:

1. **Which character** is this token for?
2. **Which ESI scopes** does the login request?
3. **Which tool** may use the resulting data?

`eve_trader/auth.py`'s `ROLE_PREFIX_TOOL` / `TOOL_ROLE_PREFIXES` and
`api/routers/auth.py`'s `_scopes_for` are the live encoding of that
collapse: a prefix is a fixed scope bundle *and* a tool namespace
(`"producer:2112625428"` is both "this character" and "Production's
token"). `TokenManager` persists one row per prefix-key in `tenant_tokens`
(`docs/phase2_schema.sql`: PK `(tenant_id, role)`). The frontend repeats
the same idea per tool: `useRoleCharacters.ts` + each layout's "Add
Character" button starts `/api/auth/{role_prefix}/start`, which always
requests that prefix's entire bundle.

This was the right shape when each tool owned a disjoint, frozen set of
endpoints. It is the wrong shape now. Four concrete consequences, all
visible in the current code, not hypothetical:

- **Scopes are all-or-nothing per tool.** Logging in a producer requests
  every entry of `PRODUCTION_SCOPES` (`production/esi_sync.py`) even if the
  tenant only needed skills. Logging in a doctrine-assets character
  requests only assets (`DOCTRINE_ASSET_SCOPES`) and can never grow
  contracts without a second login under a different prefix. There is no
  way to grant Production a character's assets without also granting it
  that character's jobs, blueprints, orders, skills, and structure-market
  access.
- **One character must log in once per tool.** The same EVE character
  that is `producer:<id>` for Production must also become
  `doctrine-assets:<id>` for Doctrine's stockpile and `seller:<id>` for
  Trading, each a separate SSO round, each a separate `tenant_tokens`
  row. GitHub issue #46 made buyer/seller multi-character; it did not
  make a character multi-tool.
- **Production and Doctrine fetch and store the same asset data twice.**
  `production/esi_sync.py`'s `sync_esi` writes `character_assets` /
  `corp_assets` via `storage.replace_assets`. `doctrine/esi_sync.py`'s
  `sync_assets` writes `doctrine_character_assets` / `doctrine_corp_assets`
  through the same function (`replace_assets` asserts all four table
  names). The doctrine tables are column- and PK-identical to the
  production ones (`docs/phase1_schema.sql` vs `docs/doctrine_schema.sql`:
  both `(item_id, owner_name)`, same `resolved_location_id` /
  `resolved_hangar_flag` columns). Doctrine's own module docstring records
  this as deliberate — a Doctrine-only tenant must not depend on
  Production ever being set up — which was the right call under
  prefix-equals-tool and is exactly the duplication character-centric
  sharing removes.
- **Sorting silently reads Production's tables with no configuration.**
  `sorting/engine.py` calls `storage.assets_at_flag` against
  `character_assets` / `corp_assets` (defaults in `storage.py`). There is
  no `role_prefix` for Sorting, no grant that "this character's assets may
  be used here", and no way for a Doctrine-only asset scan to feed it.
  Sorting works iff Production has already synced. That is an implicit
  coupling, not a sharing setting.

The access-gate identity login (`role_prefix="gate"`, `access_gate.py`) is
out of scope for this change. It remains a scope-less SSO round that
issues a session cookie and never lands in `tenant_tokens`. Nothing here
reopens tenant isolation, tool grants as an authorization layer, or the
`storage.connect()` RLS chokepoint — those stay as `MULTI_TENANT_PLAN.md`
and `CLAUDE.md` already describe them. This plan is about the *data-access*
characters sitting *inside* a tenant.

Two further facts about today's writes that this plan has to replace
rather than paper over:

- `storage.replace_assets` / `replace_industry_jobs` / the blueprint and
  sell-order siblings are **wholesale `DELETE FROM {table}` then insert**.
  A failed character in a multi-character sync is already dropped from the
  merged list (each fetch is try/except'd), so a character that errors
  *disappears from the table* rather than keeping yesterday's rows. Missing
  data is visible; the failure mode this plan must not "fix" into is silent
  staleness that looks current (see settled decision 6).
- `scheduler.py`'s per-tenant tick runs **three tool-shaped jobs**
  (`trading_pipeline` / `production_sync` / `doctrine_contract_sync`),
  gated on `esi_sync_state.scope IN ('trading','production','doctrine')`
  and three interval fields on `TradingConfig`. Station Trading has no
  scheduled sync at all (live reads). Sorting has none. Wallet fetches
  happen inside Trading's `do_reconcile_trades`, not as their own job.

## Target model

**One character = one login = one row.** Scopes and tool-sharing become
settings on that row, not separate logins. A new tenant-facing tool
**Characters** (`tool_key "characters"`, the ninth entry in
`access_gate.ALL_TOOL_KEYS` — currently the eight-tuple `trading`,
`production`, `doctrine`, `refining`, `station_trading`, `sorting`,
`portfolio`, `admin`) owns the UI and the `do_*` actions. `"characters"`
is a normal tool grant (see settled decision 11), not an admin surface
and not implied by `DEFAULT_TENANT_ID`.

Corp data is still reached only through a member character. EVE SSO has
no "authorize a corporation" flow (`production/esi_sync.py`'s module
docstring already states this; nothing here changes it). The Corporations
section of the UI is a view over corp-scoped data kinds, each accessed
via a registered character, not a second login type.

### Data kinds

Three groups (6 + 1 + 2 = 9). The registry (Phase 0) is the source of truth for names,
scopes, consuming tools, corp in-game roles, and freshness-tier defaults.
This section is the vocabulary that registry encodes.

**Group 1 — owned data, character *and* corporation variant (6):**

| Data kind        | Character scope                                      | Corporation scope                                        | Corp in-game role                          |
|------------------|------------------------------------------------------|----------------------------------------------------------|--------------------------------------------|
| Assets           | `esi-assets.read_assets.v1`                          | `esi-assets.read_corporation_assets.v1`                  | Director                                   |
| Industry Jobs    | `esi-industry.read_character_jobs.v1`                | `esi-industry.read_corporation_jobs.v1`                  | Director                                   |
| Blueprints       | `esi-characters.read_blueprints.v1`                  | `esi-corporations.read_blueprints.v1`                    | Director                                   |
| Market Orders    | `esi-markets.read_character_orders.v1`               | `esi-markets.read_corporation_orders.v1`                 | Accountant or Trader                       |
| Contracts        | `esi-contracts.read_character_contracts.v1`          | `esi-contracts.read_corporation_contracts.v1`            | (whatever ESI already requires today)      |
| Wallet           | `esi-wallet.read_character_wallet.v1`                | `esi-wallet.read_corporation_wallets.v1`                 | Accountant                                 |

The corp-role column is what the Corporations UI warns on, not a new
ESI check this app invents. `ESIClient.corporation_assets` /
`corporation_industry_jobs` / `corporation_blueprints` already document
Director; `corporation_orders` already documents Accountant or Trader.
Wallet's Accountant requirement matches ESI's corporation-wallets
endpoint (Phase 8 lands that endpoint; it does not exist in
`esi_client.py` today). Contracts keep whatever
`ESIClient.corporation_contracts` already requires — do not invent a
Director-or-otherwise role for it here just to fill the table.
Station Manager lives with structure name resolution in group 3, not
here: a resolved name is not a per-character snapshot (see below).

**Group 2 — owned data, character only (1):**

| Data kind | Character scope                 |
|-----------|---------------------------------|
| Skills    | `esi-skills.read_skills.v1`     |

Used today for Production's `character_slots` (via
`job_slots_from_skills`) and Station Trading's live order-slot display.
No corporation variant.

**Group 3 — access capabilities (2), not owned data:**

| Capability                 | Character scope                         | Corporation scope                         | Corp in-game role |
|----------------------------|-----------------------------------------|-------------------------------------------|-------------------|
| Structure name resolution  | `esi-universe.read_structures.v1`       | `esi-corporations.read_structures.v1`     | Station Manager   |
| Structure market book      | `esi-markets.structure_markets.v1`      | —                                         | —                 |

These are on or off for a character. They have **no freshness, no
scheduling, no per-tool sharing**. Structure name resolution is one
capability carrying both scopes, not two kinds and not a group-1
snapshot. A resolved name is tenant-wide reference data
(`storage.structure_names`), cached indefinitely once resolved and
populated opportunistically from asset location ids
(`production/esi_sync.py`'s `_discover_structure_names`). It is not a
per-character snapshot. Purging it when a character's sharing is
removed would destroy knowledge that is still correct and that other
tools legitimately display. Structure market book is the same shape:
it authorizes a live ESI call, it does not own rows.
`ESIClient.corporation_structures` already documents Station Manager;
the Access UI is where that warning lives.

Consuming tools, as the registry's starting set (strings only — see
settled decision 8). This is derived from what the code actually reads
today, not from what a tool's sidebar happens to offer:

- **Assets:** `production`, `doctrine`, `sorting`, `trading` (Trading
  currently live-fetches via `own_orders.py`, not the cache).
- **Industry Jobs:** `production`.
- **Blueprints:** `production`.
- **Market Orders:** `trading`, `production`, `station_trading`.
- **Contracts:** `doctrine`.
- **Wallet:** `trading`.
- **Skills:** `production`, `station_trading`.
- **Group 3 (both capabilities):** no tool dimension — any tool that
  needs a structure name or a structure book asks the Access layer
  "which characters can provide this", not "is this shared with me".

`refining` and `portfolio` do not consume raw ESI character/corp data
today (`portfolio` reads derived tables; Ore & Minerals is Goonmetrics +
SDE + its own shortlist). `admin` never does. They are not consuming
tools in the registry. Adding a consumer later is a registry edit, not
a new login prefix.

### UI — four sections, this order

A single Characters page (`/characters`, gated on `tool_key "characters"`),
not a sidebar copy-pasted into every tool layout.

1. **Characters.** Rows = registered characters. Columns = the seven
   owned data kinds (the six group-1 kinds plus Skills). Each cell is
   five-state:
   - not shared
   - shared with all capable tools
   - shared with some (badge `"2/4"` — shared-count / capable-count)
   - pending re-auth (this character has ticked a data kind whose
     scope is not yet on any of its tokens)
   - error (last fetch for this owner × data kind failed)
   Clicking a cell opens a popover with **one toggle per tool that can
   consume that data kind** (from the registry). Toggling writes or
   deletes one sharing row; it does not itself call ESI.
2. **Corporations.** The same six group-1 columns (Skills has no corp
   variant). Corp data is reached through a member character, so an extra
   **"access via \<character\>"** column names which registered character
   is currently providing that corp, plus a **warning when no registered
   character holds the needed in-game role** (Director / Accountant or
   Trader / Accountant, per the group-1 table). Station Manager is an
   Access warning, not a Corporations-table column. Same five-state
   cells, same per-tool popover, same sharing table with
   `owner_type='corporation'`.
3. **Access.** Rows = capability (structure name resolution, structure
   market book). Columns show which characters can provide it. No
   freshness, no scheduling, no per-tool sharing; on or off.
4. **Tool view.** Read-only. Per tool: what it currently receives (which
   owners × data kinds have a sharing row for that `tool_key`) and what
   has no source. This is how a tenant notices "Sorting has no Assets
   source" after a conservative migration, rather than Sorting silently
   reading Production's cache.

The page also states, in plain language, settled decision 4: sharing
governs raw ESI snapshots only; derived tables (realized trades,
shortlists, production plans) are not filtered by it. That sentence is
load-bearing — without it a later session will "fix" derived reads.

One **re-authorize** button per character applies every pending scope
change for that character in a single SSO round. One **sync everything**
button on this page refreshes every owned data kind for every owner.
Each existing tool's own sync button stays and means "refresh what I
need" (the owners × kinds shared with that tool).

## Settled decisions (do not re-litigate)

These were decided with the user before this file was written. A later
session that "improves" any of them is reopening a closed call, not
fixing a gap.

### 1. Scope acquisition: only ticked scopes are requested

The SSO authorize URL's `scope=` query is the union of scopes for data
kinds that currently have at least one sharing row (plus ticked group-3
capabilities), **not** a tool's entire historical bundle. Ticking a
kind whose scope is not yet on any of this character's tokens marks the
character **re-auth needed**. One button per character applies all
pending changes in a single SSO round. Unticking a kind does not
require re-auth (ESI cannot shrink a granted token; the sharing row
just goes away and the next fetch for that kind is skipped).

### 2. Token model: no token re-keying migration

A character may hold several `tenant_tokens` rows (a **pool**). Existing
keys (`producer:<id>`, `doctrine-assets:<id>`, `seller:<id>`, leftover
legacy bare `"buyer"` / `"seller"` / `"producer"` from the pre-#46 era
that `auth._rekey_legacy_bare_roles` already handles) keep those keys
on upgrade. There is no rewrite of `tenant_tokens.role` from
`prefix:<id>` to a canonical `character:<id>`.

The fetcher picks one token that carries the required scope,
**deterministically**: largest scope set (cardinality of the normalized
scope set), then lexically first role key. Determinism is load-bearing
for `ESIClient`'s per-`auth_role` caches (`_structure_orders_raw_cache`
is keyed by `(structure_id, auth_role)` in `esi_client.py`) — picking a
different role key for the same character on adjacent calls would split
that cache and look like a miss.

Because re-auth always requests the union of ticked scopes, a new token
is always a **superset** of every previous token of that character and
safely replaces the one it is written over. After that write, any other
token of the same character whose normalized scope set is a **strict
subset** of another token of that character may be deleted. Normalize
before comparing: split on whitespace, drop empties, dedupe, sort, then
compare. Order, duplicates, and whitespace in `TokenRecord.scopes` must
not keep a redundant row alive. Equal (non-strict) scope sets on two
keys of the same character are left alone by the subset rule; the next
re-auth still collapses the pool because the new superset makes both
old rows strict subsets.

The pool therefore **only ever arises from legacy multi-prefix logins**
and self-heals on the next re-auth. The Characters UI shows a hint on
such characters ("this character still has more than one token from the
old per-tool logins; re-authorize once to merge them"). Do not build a
batch migration that rewrites keys to make the hint go away.

Where the new token is written: reuse an existing role key for that
`character_id` if any (the same lexically-first tie-break the selector
uses), else insert under a new key. Do not invent a re-keying pass that
renames surviving rows.

### 3. Sharing is stored as one relation

```
(tenant_id, owner_type, owner_id, data_kind, tool_key)
```

`owner_type` is `'character'` or `'corporation'`. `owner_id` is the EVE
id (character_id / corporation_id), not a name. `"Enabled"` for a
(owner, data kind) cell is **derived** (`EXISTS` a row for that pair),
not a second flag. The re-auth scope union is the `DISTINCT data_kind`
values that have at least one row for that character (plus ticked
group-3 capabilities, which are not sharing rows — they have no
`tool_key`). Corp sharing uses this same table via `owner_type`, not a
parallel `corp_sharing` table.

Group 3 (Access) deliberately has **no tool dimension** and therefore
does **not** live in this table. Capabilities are settings on the
character (on/off), not sharing rows.

RLS shape is the standard per-tenant one (`docs/phase1_schema.sql`):
`tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid`,
`ENABLE ROW LEVEL SECURITY`, `tenant_isolation` policy on both `USING`
and `WITH CHECK`. The natural key can collide across tenants (two
tenants can both share character 123's assets with `production`), so
this is a **composite-PK bucket** table: PK
`(tenant_id, owner_type, owner_id, data_kind, tool_key)`.

### 4. Sharing governs raw ESI data only

Derived tables — `realized_trades`, shortlists (`shortlist`,
`ore_shortlist`, station-trading's own), production plans, doctrine
contract snapshots *after* matching, sorting lists, mineral shopping
lists — are **not** filtered by sharing. A realized trade that was
matched last week does not vanish from Trading's history because the
tenant later unticked Wallet for that character. A production plan
already computed from assets does not re-filter its BOM against the
new sharing matrix.

This is a deliberate boundary, not an incomplete implementation. State
it in this file (here) **and** in the Characters UI (Phase 9), so a
later session does not "fix" it. The fail-closed accessor (decision 9)
applies to raw ESI snapshot tables and live ESI fetches of those data
kinds. It does not wrap `storage.read_realized_trades` or
`storage.get_shortlist`.

### 5. Sync: freshness is per (owner, data kind)

Replace today's three tool-shaped ESI jobs with jobs cut **per data
kind**. Freshness is tracked per `(owner_type, owner_id, data_kind)`,
not per `esi_sync_state.scope` of `'trading'` / `'production'` /
`'doctrine'`.

Three configurable **freshness tiers** (`frequent` / `normal` / `rare`)
replace seven per-kind interval fields. The registry assigns each owned
data kind a default tier; `TradingConfig` (or a small dedicated config
surface — one place, not seven) exposes three interval hours, one per
tier. Group 3 capabilities are not scheduled.

Suggested default mapping (registry data, adjustable without a code
change to the orchestrator):

- **frequent:** Market Orders, Wallet (the inputs that go stale inside
  a session)
- **normal:** Assets, Industry Jobs, Contracts
- **rare:** Blueprints, Skills

Tool sync buttons stay. They mean "refresh every (owner, kind) shared
with this tool", not "run this tool's old `sync_esi`". The Characters
page gets a global "sync everything". Both paths call the same
orchestrator.

`do_pipeline` (`actions.py`) is **not** an ESI-owner sync. It refreshes
candidates, prunes the shortlist, and reconciles trades. Phase 7 does
not delete it. What changes is that `do_reconcile_trades` becomes a
*consumer* of already-fetched Wallet snapshots (via the accessor, as
Trading) rather than the thing that pages ESI wallet transactions
inline. Candidate/shortlist work stays a Trading-shaped scheduled job
if it still needs one; that is outside this plan's "three tool-shaped
ESI jobs" rewrite. `do_sync_contracts` similarly shrinks to "match
already-fetched Contracts against fittings" once the fetch lives in
`eve_trader/esi_data/`.

### 6. Failed fetch keeps existing rows until an age limit, then clears

Today's wholesale `DELETE FROM {table}` plus insert means a failed
character loses its rows: missing data is visible. Naive partitioned
writes (`DELETE FROM character_assets WHERE owner_character_id = %s`
then insert, skipped on exception) would keep yesterday's rows with
today's timestamp-or-lack-thereof, i.e. **stale data that looks
current**.

Rule: on a failed fetch, keep the owner's existing rows. Track
`last_success_at` / `last_error` on the freshness row. When
`now - last_success_at` exceeds **one config knob** — a multiple of
that kind's tier interval — **then** clear that owner's partition.
The multiple is the same knob for every tier (e.g. `esi_stale_clear_multiples`,
default something like 3: frequent data that has failed for 3× its
interval is dropped; rare data gets a proportionally longer grace).
Do not add seven per-kind timeouts.

The Characters cell's **error** state is this freshness row's
`last_error` while rows still exist; after the clear, the cell reads
as empty/error with no data behind it — missing again, which is the
visible failure mode we are keeping.

### 7. Parallelism is one task per owner, data kinds sequential inside it

Not the owner × data-kind cross product. `storage._get_pool()` is
`max_size=10` (Phase-1-era default, `CLAUDE.md`). An owner × kind
cross product with a dozen characters and seven owned kinds would queue on
the pool and look like a hang; one task per owner with kinds run
sequentially inside it stays well under that cap even with a handful
of concurrent owners.

Each owner task runs inside `storage.batch_session()`. That is the
same connection-reuse context manager `plan_production` already uses:
every `connect()` on that thread reuses one pooled connection, commits
once at the end, rolls back on exception. Per-owner atomicity comes
for free — a character whose Jobs fetch fails mid-task does not
commit a half-written Assets partition from the same task. (Assets
that succeeded in a *previous* task stay, per decision 6; this is
atomicity of one owner-run, not of history.)

`storage.with_current_tenant(fn)` still wraps anything submitted to a
`ThreadPoolExecutor`. Worker threads do not inherit contextvars
(`CLAUDE.md`, found twice already in `esi_sync.py` and `esi_client.py`).
Do not rediscover this a third time.

A cheap **per-owner guard** (in-process lock/set keyed by
`(tenant_id, owner_type, owner_id)`) prevents a manual tool sync and
the scheduler from processing the same owner concurrently. Same race
exists today; it becomes cheaper to close once there is one
orchestrator instead of three `sync_esi` functions.

### 8. Module layout: `eve_trader/esi_data/` is a third cross-cutting package

New top-level package `eve_trader/esi_data/` holding:

- the data-kind **registry** (pure data: kind names, scopes, consuming
  **tool keys as strings**, corp roles, default freshness tier; **imports
  no tool package** — not `eve_trader.production`, not
  `eve_trader.doctrine`, not `eve_trader.sorting`. A registry that
  `import`s Production to ask "do you consume assets?" reintroduces the
  coupling this package exists to break)
- the **fetchers** (thin wrappers around existing `ESIClient` methods,
  one per owned data kind × owner_type; group 3 is not a fetcher)
- the **orchestrator** (owner tasks, freshness, partitioned writes,
  per-owner guard, "sync what this tool needs" / "sync everything")
- the Characters **`do_*` actions** (list owners, toggle sharing, start
  re-auth, sync). Same `do_*` / router / CLI rule as everything else:
  the router is a thin `_wrap`; `cli.py` does not grow a parallel
  implementation

`production/esi_sync.py` and `doctrine/esi_sync.py` shrink to whatever
is truly tool-specific (Production's slot-row derivation from skills;
Doctrine's contract-to-fitting match). They do not fetch or
`replace_assets` themselves.

This package is the **third cross-cutting module** alongside
`portfolio.py` and `scheduler.py`. Updating `CLAUDE.md`'s opening
"Two tools, one backend" / module-layout description is part of the
implementation of this phase, not a later docs sweep: the current text
says `portfolio.py` and `scheduler.py` are the only modules that
deliberately span just Trading and Production, and lists four
tenant-facing tools. After this lands there are more tenant-facing
tools than that paragraph names, and a third cross-cutting package.
Leaving `CLAUDE.md` stale is how the next session re-derives a
prefix-per-tool layout from the file that was supposed to prevent
exactly that.

### 9. All reads of ESI data go through one fail-closed accessor

Same shape as `storage.connect()` raising `RuntimeError` on a missing
tenant (`storage.py`: "Refusing to open a connection with no tenant
scope rather than risk it defaulting to an un-scoped query"). The
accessor requires a consuming `tool_key` and **raises when none is
given**. An optional filter that callers might forget is not
enforcement — sharing can only be enforced at read time, and a
scattered `WHERE` / `if shared:` would be forgotten the same way a
scattered `WHERE tenant_id = ...` would have been (the reason RLS
exists).

Live ESI fetches of a data kind go through the orchestrator/selector
with the same `tool_key` requirement (Trading's current
`own_orders.py` live asset pull is a read of Assets, not an exception).
Derived-table reads do not (decision 4).

Add an isolation test modelled on `tests/test_pg_tenant_isolation.py`:
a data kind **not shared** with a tool must be unreachable through that
tool's read path, exercised through the real accessor (not a raw SQL
stand-in), and a missing `tool_key` must raise rather than return rows.
Keep that test permanently; it is this migration's equivalent of
"tenant A never sees tenant B's data".

### 10. `tenant_role_consents` is retired

Table, `docs/role_consent_schema.sql`, the two `/api/auth/{role_prefix}/consent`
endpoints, `storage.has_role_consent` / `record_role_consent`, the
frontend's "you'll only see this once per role" copy, and the
`KNOWN_NON_MIGRATED_TABLES` entry in `sqlite_migration.py` all go.
`tests/pg_helpers.py` currently applies `role_consent_schema.sql` as
part of schema setup — that application site is removed with the file.

Instead, the confirmation dialog **before each SSO redirect** shows
exactly what is being requested **right now**, derived from the
registry, with **newly added** items highlighted (scopes that were not
on any of this character's existing tokens). Reason: "once per
`role_prefix`" only worked because a prefix was a fixed scope bundle;
the new bundle is variable. A stored acknowledgement of "producer"
would either lie (the next tick adds Wallet) or need a bundle-hash
anyway, at which point showing the live list is simpler and honest.
Identity-only `gate` keeps its existing `localStorage` acknowledgement
on Landing — it still requests no game data and still has no tenant at
the moment the dialog is shown.

### 11. `"characters"` is a normal tool grant; Admin auto-ticks it in the UI only

The Admin UI (`frontend/src/pages/admin/AdminPage.tsx`'s
`UserToolCheckboxes`, which already mirrors `ALL_TOOL_KEYS` by hand)
auto-ticks `"characters"` when any ESI-consuming tool is ticked
(`trading`, `production`, `doctrine`, `station_trading`, `sorting` —
the tools in the registry's consuming set; not `refining` / `portfolio`
/ `admin` unless those later become consumers), with an explanatory
note, and the checkbox stays **deliberately un-tickable** (checked +
disabled while an ESI tool is selected).

Enforce this **in the frontend only**. `admin.do_set_tool_grants` keeps
its replace semantics (`storage.revoke_all_tool_grants` then insert
exactly `tool_keys`). A client that omits `"characters"` while sending
`"production"` is accepted at the API; the Characters page then 403s
for that user until an admin ticks it. Do not special-case
`"characters"` inside `do_set_tool_grants` — that function's replace
contract is load-bearing for "unchecked checkboxes actually revoke"
and is tested that way.

`ALL_TOOL_KEYS` grows to nine entries; the frontend's hand-kept copy
grows with it, same as today. Landing gets a ToolCard. QuickNav gets
a shortcut. `api/app.py`'s `_TOOL_PATH_PREFIXES` maps `/api/characters/`
to `"characters"`. Gate-disabled installs already receive every
tool_key from `/api/gate/status` and will show the card automatically.

### 12. `eve-trader auth --role buyer|seller` is removed

`cli.py`'s `auth` command (`click.Choice(["buyer", "seller"])` calling
`actions.do_auth`) is the CLI face of prefix-equals-tool. It goes.
No other CLI command **signature** changes: `pipeline`,
`refresh-shortlist`, `reconcile-trades`, the production/doctrine
commands, `tenant …`, `admin bootstrap` all keep their names and
flags. They already go through `do_*` functions; only the **source of
the character list** underneath those functions changes (from
`tm.list_roles("producer")` / `list_producer_characters()` to "owners
shared with this tool for the data kinds it is about to read").

`actions.do_auth` itself is a wrapper around
`TokenManager.get_token_interactive*` — the blocking local-HTTP-server
flow that the web app already replaced with `/start`+`/callback`.
Removing the CLI command is what removes the last caller that still
thinks in `buyer`/`seller` prefixes. The web re-auth path (Phase 6)
is a Characters-scoped `/start` that does not take a tool prefix.

### 13. Migration is conservative

On upgrade:

- Existing tokens keep working. Nobody must re-authorize.
- A migrated character is shared **only** with the tool its old
  `role_prefix` belonged to, **never auto-widened**.

Prefix → tool, from `auth.ROLE_PREFIX_TOOL` as it exists today:

| Old prefix         | Shared with        | Data kinds implied by that prefix's current scope bundle |
|--------------------|--------------------|----------------------------------------------------------|
| `buyer`, `seller`  | `trading`          | Market Orders, Wallet, Assets; Access: structure market book |
| `producer`         | `production`       | Assets, Industry Jobs, Blueprints, Market Orders, Skills; Access: structure name resolution, structure market book |
| `doctrine`         | `doctrine`         | Contracts; Access: structure name resolution             |
| `doctrine-assets`  | `doctrine`         | Assets                                                   |
| `trader`           | `station_trading`  | Market Orders, Skills                                    |
| `gate`             | (not a token)      | nothing                                                  |

A character that is both `producer:<id>` and `doctrine-assets:<id>`
becomes one character row with Assets shared with **both**
`production` and `doctrine` (each prefix contributes its own rows;
that is not auto-widening, it is preserving both existing grants).
Assets are **not** auto-shared with `sorting` or `trading` even if
Production has been feeding Sorting by accident. After migration,
Sorting's Tool-view row reads "no source" until a human shares Assets
with it. That is the conservative outcome, not a bug. Do not "fix"
it by noticing Sorting used to read `character_assets` and writing
those sharing rows.

Corp sharing is inferred the same way: if a prefix's bundle included
the corp variant (producer, doctrine, doctrine-assets), the corp the
character belongs to gets the corresponding corp sharing rows for
that tool, still not widened to other tools. Access capabilities
implied by a prefix are ticked on for that character, not written as
sharing rows.

## Carry-forward hazards

These are not open design questions. They are landmines a later phase
will hit if it only implements the happy path. List them here so they
are in the same file as the plan, not rediscovered from `git log`.

- **`replace_character_slots` must stay an UPSERT.** GitHub issue #39:
  the old delete-then-reinsert blew away `excluded_from_planning` on
  every ESI re-sync (the same bug class `resolved_location_id` already
  hit). `storage.replace_character_slots` now UPSERTs slot counts and
  deletes only names no longer in the payload. A partitioned rewrite
  that goes back to delete+insert "because every other ESI table does
  that now, per owner" re-breaks #39. Skills still update slot *counts*;
  they must not recreate the row.
- **`sorting_intake_sources` identifies sources by `owner_name`.**
  `docs/sorting_schema.sql` / `storage.add_sorting_intake_source` store
  `owner_name` (character name or `"RichlTech (corp)"`-style corp
  label), and `sorting/engine.py` passes that into
  `assets_at_flag(..., owner_name=...)`. Adding `owner_character_id` /
  `owner_corporation_id` to the asset tables (Phase 1) is not enough
  on its own: the config rows that *point at* those tables also have
  to learn ids, or a rename (or the `" (corp)"` suffix convention)
  silently detaches a tenant's intake sources from their data. Phase 1
  or 2 must migrate those config rows, not only the data tables.
- **Error strings reference the old UI.**
  `production/esi_sync.py` raises `ActionError("No producer character
  logged in yet. Use 'Add Character' in the sidebar.")`;
  `production/actions.py` has the shorter `"No producer character
  logged in yet."`; Trading's Transactions page tells the user to log
  in a character on the Overview page. After Phase 9 those strings
  send people to a sidebar that no longer adds characters. Grep for
  `Add Character`, `producer character`, `buyer/seller`,
  `doctrine-assets` in user-facing text as part of Phase 9, not as an
  afterthought.
- **Manual and scheduled sync can process the same owner concurrently.**
  True today (`do_sync_esi` from the Production button vs
  `production_sync` from `scheduler.py`). A per-owner guard on the new
  orchestrator is cheap (decision 7) and should land with Phase 3, not
  after the first duplicated-write incident.
- **A new schema file must be registered in four places**, not just
  created. Confirmed real, repeated bug class (`docs/pipeline_runs_schema.sql`
  2026-09-11; `station_trading_schema.sql` as documented inline in
  `deploy/deploy.sh`): forgetting one is a silent gap, not a loud
  error — the app runs until the first request that touches the new
  table, then 500s with `UndefinedTable`. The four:
  `deploy/deploy.sh`'s migration for-loop, `deploy/README.md`'s
  `psql`/`docker exec` instructions, the root `README.md`'s same
  list, `.cursor/start.sh`'s dev-environment loop. Grep for an
  existing filename (e.g. `production_buy_list_schema`) to find every
  site. A new per-tenant table also needs an entry in
  `sqlite_migration.KNOWN_NON_MIGRATED_TABLES` (with a reason) or
  `tests/test_sqlite_migration_table_drift.py` fails. Retiring
  `tenant_role_consents` is the inverse: remove it from those lists
  and from `KNOWN_NON_MIGRATED_TABLES` in the same change, or the
  drift-guard fails the other way ("references table(s) no longer in
  the real schema").

## Architecture notes the phases depend on

### Partition key is an immutable EVE id, not a name

`character_assets` / `corp_assets` (and the doctrine twins) currently
PK on `(item_id, owner_name)`. EVE character names are unique while
held, but they **rename**; corp names rename; Doctrine writes corp
owners as `f"{corp_name} (corp)"` (`doctrine/esi_sync.py`). Partitioned
deletes keyed on `owner_name` would strand rows across a rename and
would make "clear this corp's assets" a string-equality against a
display convention.

Phase 1 adds `owner_character_id` / `owner_corporation_id` (BIGINT,
nullable only for the duration of the backfill) and switches the
partition `DELETE` to those columns. Display names stay on the row for
UI convenience but are not the delete key. `character_slots` is already
PK'd on `character_name` — same trap; it needs the id column too even
though its write path stays an UPSERT (#39).

### Token selector vs. `auth_role`

Callers of `ESIClient` today pass an `auth_role` they obtained from
`list_producer_characters()` / `list_roles("seller")` / etc. After
Phase 4 they ask the selector for `(auth_role, character_id)` given
`(owner, data_kind)` (and, for live fetches, `tool_key`). They do not
pick a prefix. The selector's deterministic choice is what keeps
structure-market caches stable.

### Fail-closed accessor shape

Sketch, not an implementation — names can move, the contract cannot:

```
def read_esi(data_kind: str, tool_key: str, **filters) -> list: ...
```

- `tool_key` is required. `None` / omitted raises, same spirit as
  `connect()`'s missing tenant.
- `tool_key` not in `access_gate.ALL_TOOL_KEYS` raises (`ActionError`
  at the `do_*` boundary, `RuntimeError` inside storage if a caller
  bypassed `do_*` — match whatever the surrounding module already
  does).
- Rows whose `(owner_type, owner_id, data_kind, tool_key)` is not in
  the sharing table are not returned. No "also return unshared if the
  caller is production, because historically…".
- Isolation test: tenant-scoped (RLS still applies), then sharing-scoped
  on top. Two layers, do not conflate them (`CLAUDE.md`: tenant_id is
  "whose data", tool grants are "which tools", sharing is "which of
  this tenant's owners a given tool may read").

### Conservative migration is a data transform, not a re-auth

A one-shot function (CLI is fine; no web equivalent required, same
reasoning as `eve-trader tenant import-tokens`) walks `tenant_tokens`,
groups by `character_id`, inserts sharing rows from the prefix table
in decision 13, and leaves tokens in place. Idempotent
(`ON CONFLICT DO NOTHING` on the sharing PK). Run once per tenant as
part of the schema phase's deploy, not as a "please click through
every character" UI. Characters with multiple prefixes get multiple
tools' rows and the pool-hint (decision 2).

## Phased implementation

Each phase has a rationale (why it is a phase, not a bullet on another
phase) and a definition of done. Status lines get added here as work
lands, the same way `MULTI_TENANT_PLAN.md` accumulated them; they are
absent on purpose at the start.

**Recommended delivery order** (not the same as the numbered sequence;
the numbers are dependencies, the delivery order is what actually
ships):

1. **Phase 8 first, standalone.** Corp wallet is a real missing fetch
   (see that phase) and depends on none of the new model. Shipping it
   first means Trading benefits even if the rest of this plan pauses.
2. **Phases 0–4 as one block.** Registry without schema is a dictionary;
   schema without partitioned writes still wipes whole tables; writes
   without fetchers have no producer; fetchers without the selector
   still think in prefixes. The block has to land together to be
   meaningfully testable. Internal PRs inside the block are fine;
   exposing a half-migrated read path to the UI is not.
3. **Phases 5–7.** Consent, API/grant/gate, scheduler. These are the
   "the rest of the app now talks to `esi_data`" cutover. Tokens still
   work; conservative sharing is already in place from the 0–4 block.
4. **Phase 9 last.** Frontend. Backend can be live-verified with curl /
   the existing layouts' leftover "Add Character" buttons until the
   Characters page replaces them.

### Phase 0 — Registry / vocabulary

**Rationale.** Every later phase names data kinds, scopes, consuming
tools, and corp roles. If those strings are invented independently in
the schema file, the fetcher, the accessor, and the UI, they will
drift — the exact failure `useRoleCharacters.ts` was written to stop
at the *role* layer (GitHub issue #69: three copies of the same login
pattern). The registry is pure data and imports no tool package so the
dependency arrow is one-way: tools name themselves as strings, the
registry does not import them to ask.

**Done when:**

- `eve_trader/esi_data/` exists with a registry module that lists every
  data kind in the three groups above, character/corp scopes, corp
  in-game roles, consuming tool keys as strings, default freshness
  tier, and group-3 capabilities.
- A unit test (no Postgres) asserts: every consuming tool_key is in
  `ALL_TOOL_KEYS`; every scope string appears in some fetcher-facing
  mapping; group 1 has six kinds, group 2 has Skills, group 3 has two
  capabilities and no consuming-tool list; Skills has no corp
  variant; structure name resolution is one capability carrying both
  `esi-universe.read_structures.v1` and
  `esi-corporations.read_structures.v1`.
- `CLAUDE.md`'s module-layout paragraph names `eve_trader/esi_data/` as
  the third cross-cutting package and names Characters as a
  tenant-facing tool. This file stays the design history.

### Phase 1 — Schema

**Rationale.** Partitioned deletes (Phase 2) cannot key on
`owner_name`. Sharing (decision 3) and freshness (decision 5) have no
table yet. Putting columns on the live ESI tables before changing
write shape lets the backfill run while today's wholesale replace is
still what writes them.

**Includes:**

- New schema file (name like `docs/esi_access_schema.sql` — pick one
  and grep it into `deploy/deploy.sh`, `deploy/README.md`, root
  `README.md`, `.cursor/start.sh` in the same change).
- Sharing table as specified in decision 3, composite PK, RLS, `GRANT`
  to `eve_trader_app`.
- Freshness table keyed `(tenant_id, owner_type, owner_id, data_kind)`
  with `last_success_at`, `last_attempt_at`, `last_error`, also
  composite-PK + RLS.
- `owner_character_id` / `owner_corporation_id` on every ESI snapshot
  table that will be partitioned (assets, jobs, blueprints, orders,
  contracts, wallet once Phase 8 has added it, `character_slots` —
  not `structure_names`, which is tenant-wide reference data).
  BIGINT, matching the ESI-object-id width lesson from
  `MULTI_TENANT_PLAN.md` Phase 1 (Postgres `INTEGER` is 32-bit; EVE
  character ids fit, but *do not* use `INTEGER` for "any ESI id"
  out of habit — `item_id` already had to become `BIGINT`).
- Character-capability storage for group 3 (on/off per character, no
  `tool_key`). Could be columns on a small `esi_characters` overlay
  or a two-row-per-character table; either is fine so long as it is
  not shoehorned into the sharing relation.
- `KNOWN_NON_MIGRATED_TABLES` entries for every new per-tenant table.
- Conservative sharing backfill (decision 13) as an idempotent
  function, tested with fixture tokens under more than one prefix for
  the same `character_id`.
- `sorting_intake_sources` grows owner-id columns (carry-forward
  hazard). Existing rows backfilled from current `owner_name` where
  a matching asset owner still exists; rows that cannot be matched
  are left with `owner_name` only and logged, not deleted.

**Done when:** schema applies idempotently against the real local
Postgres (`eve-trader-pg`), isolation tests cover the new tables
(composite PK + RLS, modelled on `test_pg_tenant_isolation.py`),
drift-guard is green, and a dry-run backfill against a copy of real
`tenant_tokens` produces sharing rows that match decision 13
(including "producer + doctrine-assets of the same id ⇒ Assets shared
with both tools, not with Sorting").

### Phase 2 — Partitioned writes, merge the doctrine asset tables

**Rationale.** Doctrine's asset tables are a second copy of the same
rows. Merging them after partitioned writes exist means Doctrine and
Production can both write an owner's partition without a wholesale
wipe of the other tool's characters. Merging them *before* partitioned
writes would make `sync_esi`'s `DELETE FROM character_assets` destroy
Doctrine-only characters the first time Production syncs.

**Includes:**

- `replace_assets` (and the jobs/blueprints/orders/contracts siblings)
  delete by `owner_character_id` / `owner_corporation_id`, then insert
  that owner's rows. Never `DELETE FROM {table}` with no owner
  predicate.
- Failed fetch: skip the delete (decision 6). Age-limit clear is a
  separate pass using the freshness row and the one multiple-knob.
- Copy `doctrine_character_assets` / `doctrine_corp_assets` into
  `character_assets` / `corp_assets` (`ON CONFLICT DO NOTHING` or
  equivalent; they are PK-identical on `(item_id, owner_name)` today,
  and after Phase 1 should conflict on the id-aware key). Doctrine
  read paths (`doctrine/engine.py`'s
  `tables=("doctrine_character_assets", "doctrine_corp_assets")`)
  switch to the shared tables **through the accessor** once Phase 0–4's
  accessor exists; until then, a temporary read of the shared tables
  with Doctrine's current unfiltered call is acceptable inside this
  block but must not ship past Phase 4.
- Drop the doctrine asset tables (and their `KNOWN_NON_MIGRATED_TABLES`
  entries) in the same schema change that completes the copy, so there
  is no window where two writers target different tables.
- `replace_character_slots` stays an UPSERT (issue #39).

**Done when:** a test writes Production-shaped asset rows for character
A and Doctrine-shaped rows for character B into the shared table via
the partitioned replace, then a Production-only replace of A does not
delete B; a failed replace of A leaves A's previous rows; an aged-out
failed A is cleared; doctrine tables are gone; `#39` slot-exclusion
test still passes.

### Phase 3 — Fetch layer + orchestrator

**Rationale.** Today's fetch lives in three tool modules with three
ideas of "for every character in `list_roles(prefix)`". The
orchestrator is what makes sharing, freshness, partitioned writes, and
the per-owner guard actually run. Fetchers stay thin so adding Wallet
(Phase 8, if it hasn't already) or a future data kind is a registry
row plus one `ESIClient` wrapper, not a fourth `esi_sync.py`.

**Includes:**

- Fetchers for every group-1 and group-2 kind, character and corp
  variants, calling the existing `ESIClient` methods (Wallet corp
  variant exists only after Phase 8 — if Phase 8 already shipped,
  just consume it). Group 3 is not an orchestrator kind: name
  resolution stays opportunistic (`_discover_structure_names`
  filling `structure_names`).
- Orchestrator: one task per owner, kinds sequential, each owner
  wrapped in `batch_session()` + `with_current_tenant` for pool
  workers, per-owner guard, freshness update, age-limit clear.
- `do_sync_for_tool(tool_key)` and `do_sync_all()`. Tool `do_sync_esi`
  / `do_sync_contracts` / `do_sync_assets` become wrappers that call
  `do_sync_for_tool` with their own key (and then run tool-specific
  post-processing: slots, contract matching).
- Error strings that still say "Add Character in the sidebar" can wait
  for Phase 9, but new orchestrator errors must not add more of them.

**Done when:** unit tests drive the orchestrator with a fake `ESIClient`
across two owners and two kinds — success, mid-kind failure (decision
6), overlapping manual+scheduled call (guard), and
`tool_key="production"` refreshing only what Production is shared.
`production/esi_sync.py` / `doctrine/esi_sync.py` no longer call
`replace_assets` / `character_assets()` themselves. Full `pytest`
green.

### Phase 4 — Token selector

**Rationale.** Without this, the orchestrator still asks
`list_roles("producer")`. The selector is what makes a pool of
legacy keys usable as one character, and what makes `auth_role`
stable for `ESIClient` caches.

**Includes:**

- Given `(character_id, required_scope)` (and optionally a set of
  scopes), pick a token by largest normalized scope set, then
  lexically first role key.
- Strict-subset token deletion after a successful re-auth write.
- The Characters-UI hint is a backend flag ("this character_id has
  more than one `tenant_tokens` row"), not a frontend guess.
- No re-keying migration (decision 2). Tests cover: two keys for one
  id with overlapping scopes pick the larger; equal size picks
  lexical; a new superset write deletes strict subsets; equal-scope
  duplicates survive until a superset write; normalization treats
  `"b a"` and `"a  b a"` as the same set.

**Done when:** those tests pass, and a live `GET` of whatever status
endpoint Phase 6 will own (or a temporary debug helper, deleted
before merge) shows a real multi-prefix character as one row with the
hint set. `ESIClient.structure_orders_raw` still caches by the
selected `auth_role` — selector stability is covered by asserting the
same inputs return the same role key across 100 calls.

### Phase 5 — Retire consent

**Rationale.** `/consent` is "once per prefix". Prefixes stop being
the unit of login in Phase 6. Retiring consent *after* the new
confirm dialog exists would mean two dialogs; retiring it *before*
the Characters re-auth path exists would mean SSO with no
explanation. Phase 5 sits on the 5–7 cutover so the new dialog
(decision 10) and the new `/start` (Phase 6) land together from the
user's point of view; internally it can merge in the same PR as
Phase 6.

**Includes:**

- Delete `docs/role_consent_schema.sql` and drop the table in the
  esi-access schema file (idempotent `DROP TABLE IF EXISTS
  tenant_role_consents`). Remove from deploy lists, `.cursor/start.sh`,
  both READMEs, `KNOWN_NON_MIGRATED_TABLES`, `pg_helpers` schema
  application.
- Delete `storage.has_role_consent` / `record_role_consent` and
  `api/routers/auth.py`'s GET/POST `/{role_prefix}/consent`.
- `frontend/src/roleAccessDescriptions.tsx` stops being a
  per-prefix static bundle. The confirm dialog's body is derived
  from the registry (or from a small backend payload of "scopes
  about to be requested, with `added: true/false`"). Newly added
  items highlighted. `gate` keeps Landing `localStorage`.
- `useRoleCharacters.ts`'s consent-check-then-redirect path dies
  with the per-tool Add Character buttons in Phase 9; until then it
  must not 404 on `/consent`. Sequence with Phase 6/9 so the frontend
  never calls a deleted endpoint.

**Done when:** grep for `tenant_role_consents`, `has_role_consent`,
`role_consent_schema`, `consentStatus`, `acknowledgeConsent` is empty
outside this plan file and git history; drift-guard green; a re-auth
of a character that is adding Wallet shows Wallet highlighted and
does not show a "you'll only see this once per role" line.

### Phase 6 — API, router, grant, gate mapping

**Rationale.** This is the cutover the rest of the app sees: a
Characters router, a grant, a gate mapping, and `/start` that no
longer takes `producer`. Doing it before 0–4 would expose empty
endpoints; doing it after 9 would make the UI talk to routes that
don't exist.

**Includes:**

- `eve_trader/api/routers/characters.py` (name flexible) calling
  `esi_data` `do_*` functions via `_wrap`. Reads of sharing /
  freshness / owners are allowed to be thin storage reads if they
  truly have no decision in them (`CLAUDE.md`'s GET-only exception);
  toggles, re-auth start, and sync go through `do_*`.
- `access_gate.ALL_TOOL_KEYS` gains `"characters"`. `_TOOL_PATH_PREFIXES`
  maps `/api/characters/` to it. `/api/auth/.../start` for the new
  re-auth path is gated on `"characters"` (the Characters tool is
  who is adding/changing ESI access), not on `ROLE_PREFIX_TOOL`.
- Existing `/api/auth/{role_prefix}/start` for `buyer` / `seller` /
  `producer` / `doctrine` / `doctrine-assets` / `trader` is removed
  or hard-410'd once Phase 9 has removed the callers. Do not leave
  a working prefix `/start` "just in case" — it would mint new
  prefix-keys and fight the selector.
- `ROLE_PREFIX_TOOL` / `_ROLE_KEY_RE` / `validate_role_key_for_tool`
  shrink as callers disappear. Token *storage* still has prefix
  keys (decision 2); the HTTP API no longer accepts them as a way
  to log in.
- `do_set_tool_grants` unchanged in semantics (decision 11). Tests
  still prove replace, not merge.
- Isolation test (decision 9) in the permanent suite.
- Remove `eve-trader auth --role buyer|seller` (decision 12).
- Per-tool character-list endpoints (`/api/production/producer-characters`,
  `/api/trading/buyer-characters`, …) either keep working as
  "owners shared with this tool" (so Phase 9 can delete the sidebar
  without a flag day) or are deleted in the same PR as Phase 9.
  Prefer the first: signature unchanged, source underneath changes
  (decision 12 applied to the web).

**Done when:** a real HTTP session with `"production"` but not
`"characters"` gets 403 on `/api/characters/*` and still 200s
Production reads of data that *is* shared; a session with
`"characters"` can toggle sharing and get a confirm-dialog payload;
`/api/auth/producer/start` is gone; `pytest` including the new
isolation test is green; live curl against localhost:8000 matches
the unit tests (this repo's "live-verify before declaring done"
rule — a settings-save-shaped bug was already caught only at this
layer once).

### Phase 7 — Scheduler

**Rationale.** Freshness tiers do nothing if the tick still runs
`do_sync_esi` / `do_sync_contracts` / `do_pipeline`'s wallet fetch
on three tool-shaped intervals. This phase is small on purpose
(`CLAUDE.md` scheduler note: adding a per-tenant job is "one
interval field plus one `if _hours_since` line") but the *shape*
changes from three jobs to one orchestrator call that internally
decides which (owner, kind) pairs are due given the three tier
intervals.

**Includes:**

- Three config fields (frequent / normal / rare hours) + the one
  stale-clear multiple (decision 6). `_FIELD_RANGES` entries
  `(0, None)`. Retire
  `production_sync_interval_hours` and
  `doctrine_sync_interval_hours` as *ESI* intervals. Keep
  `trading_pipeline_interval_hours` if `do_pipeline`'s
  candidate/shortlist work still needs its own cadence; that job
  must not fetch Wallet itself after this phase.
- `_check_and_run_due_jobs_for_tenant` calls the orchestrator's
  "run whatever is due" once per tenant, not three tool jobs.
  `last_run_status` can stay job-named (`esi_data_sync`) — do not
  explode it into seven per-kind status dicts in the portfolio
  readout; per-kind state lives on the freshness table and the
  Characters UI.
- Backup and Jita price cache stay global/unscoped, unchanged.

**Done when:** with the scheduler enabled against a test tenant,
only due kinds fetch (fake clock / monkeypatched `last_success_at`);
a manual tool sync updates freshness and pushes back the next
scheduled fetch for those pairs; `do_pipeline` no longer pages ESI
wallet; full `pytest` including `tests/test_scheduler.py` rewritten
for the new job name.

### Phase 8 — Corp wallet

**Rationale.** Ship this **first and standalone** (see delivery
order). It fixes a real bug and depends on nothing in Phases 0–7.

The bug is the other half of a gap `ESIClient.corporation_orders`
already documents: stock in a corp hangar is often listed via a
**corp** order funded by the **corp wallet**, not a personal one.
Corporation orders were added so `do_unlisted_stock` / Production
unlisted-stock stop flagging that stock as unsold. Realized-trade
reconciliation (`trade_reconciliation.py`) still pages only
`character_wallet_transactions` / `character_wallet_journal`. Those
endpoints do not contain corp-wallet fills, so a corp-funded sell
is invisible to Trading's history the same way it used to be
invisible to unlisted-stock. There is no
`esi-wallet.read_corporation_wallets.v1` on `OAUTH_CONFIG.scopes` or
`PRODUCTION_SCOPES`, and no `ESIClient` method for it.

Phase 8 adds the client methods, the scope on the **current**
buyer/seller (and producer, if Production grows a wallet consumer)
bundles, the fetch, and the reconcile path. Under the old prefix
model that means a one-time re-auth of seller/buyer characters to
pick up the new scope — acceptable because this phase ships before
the variable-bundle re-auth UI, and it is the same "character added
before this scope existed needs to be re-added" pattern
`PRODUCTION_SCOPES` already documents for `structure_markets`.
Under the new model (once 0–9 land) Wallet's corp variant is just
another group-1 kind with Accountant as the in-game role.

**Done when:** `ESIClient` has corporation wallet transaction/journal
(division-aware; ESI exposes per-division wallets),
`trade_reconciliation` matches corp-wallet sells the same way it
matches character-wallet sells (including the real-tax journal
path), tests cover a corp-funded fill that is absent from the
character wallet, and a live reconcile against a character that has
re-authed with the new scope sees a known corp sell that previously
did not appear. No Characters UI, no sharing table, no registry
required for this phase.

### Phase 9 — Frontend

**Rationale.** Last because it is the only phase that cannot be
live-verified with curl alone, and because every backend contract
it needs (registry payload, five-state cells, confirm-dialog
highlights, tool view, admin auto-tick) is a lie until 0–7 exist.

**Includes:**

- Characters page, four sections in the specified order, five-state
  cells, per-tool popover, re-auth button per character, sync
  everything, pool hint, decision-4 boundary sentence.
- Landing ToolCard, QuickNav entry, `App.tsx` route, Admin
  `ALL_TOOL_KEYS` copy, auto-tick + disabled checkbox + note
  (decision 11).
- Remove per-tool "Add Character" sidebars
  (`TradingLayout.tsx`, `ProductionLayout.tsx`,
  `DoctrineLayout.tsx`'s two CharacterGroups,
  Station Trading's equivalent). Tool sync buttons stay, labelled
  as "refresh what I need".
- `useRoleCharacters.ts` either dies or shrinks to "list + remove
  for this tool's shared owners" without an SSO start. Do not leave
  a second login path.
- Grep-clean user-facing strings (carry-forward hazard): no "Add
  Character in the sidebar", no "once per role".
- Browser verification of the actual flows, not a single screenshot
  (`CLAUDE.md` live-verify discipline, and this repo's UI rule):
  toggle a cell, see pending re-auth, run the confirm dialog with a
  highlighted new kind, confirm Tool view updates, confirm a
  Production page still sees shared assets and does not see
  unshared ones, confirm Admin auto-ticks Characters, confirm a
  user without the grant does not see the card.

**Done when:** those flows have been exercised in a real browser
against the real backend; Playwright or a throwaway `_verify_*.mjs`
is deleted after, not left in the repo; full `pytest` still green.

## Critical files

- `eve_trader/auth.py` — `TokenRecord`, `ROLE_PREFIX_TOOL`,
  `TOOL_ROLE_PREFIXES`, `_ROLE_KEY_RE`, `TokenManager` persistence
  to `tenant_tokens`, legacy re-key of bare `"buyer"`/`"seller"`.
  Selector (Phase 4) lives next to this, not in a tool package.
- `eve_trader/api/routers/auth.py` — `_scopes_for`, `/start` /
  `/callback` / `/consent`, `_pending` dict. Consent and prefix
  `/start` die here.
- `eve_trader/access_gate.py` — `ALL_TOOL_KEYS` (grows to nine).
- `eve_trader/api/app.py` — `_TOOL_PATH_PREFIXES`,
  `_required_tool_for_path` (today maps `{role_prefix}/start` via
  `ROLE_PREFIX_TOOL`).
- `eve_trader/storage.py` — `replace_assets` (four-table assert,
  wholesale delete), `replace_character_slots` (UPSERT, #39),
  `assets_at_flag`, `has_role_consent`, `esi_sync_state`,
  `sorting_intake_sources`. Accessor chokepoint lands here or in
  `esi_data/` calling into here — one place, not both.
- `eve_trader/production/esi_sync.py` / `eve_trader/doctrine/esi_sync.py`
  / `eve_trader/station_trading/esi_sync.py` — today's prefix-scoped
  fetchers and scope lists. Shrink in Phase 3.
- `eve_trader/scheduler.py` — three tool-shaped jobs, three interval
  fields.
- `eve_trader/trade_reconciliation.py` / `eve_trader/esi_client.py` —
  Phase 8 corp wallet; `corporation_orders` already documents the
  sibling bug.
- `eve_trader/cli.py` — `auth --role buyer|seller` (decision 12).
- `eve_trader/admin.py` — `do_set_tool_grants` replace semantics
  (decision 11: do not change).
- `eve_trader/sqlite_migration.py` — `KNOWN_NON_MIGRATED_TABLES`
  (add sharing/freshness; remove `tenant_role_consents` and the
  doctrine asset tables when they go).
- `frontend/src/hooks/useRoleCharacters.ts`,
  `frontend/src/roleAccessDescriptions.tsx`,
  `frontend/src/pages/admin/AdminPage.tsx` (`ALL_TOOL_KEYS` copy,
  `UserToolCheckboxes`),
  `frontend/src/pages/Landing.tsx`,
  `frontend/src/App.tsx`,
  `frontend/src/components/QuickNav.tsx`,
  per-tool layouts' character sidebars.
- `docs/role_consent_schema.sql` — deleted in Phase 5.
- `CLAUDE.md` — module-layout paragraph, updated as part of Phase 0,
  not as a cleanup PR afterwards.

## Verification

All work happens on a feature branch, verified against the real local
Postgres (`eve-trader-pg`) the same way `MULTI_TENANT_PLAN.md` required
— never against a live deployment's data as a development scratchpad.
Each phase's "done when" is the acceptance test for that phase.

The sharing isolation test (decision 9) is a **permanent** suite
member, not a one-off manual check, for the same reason
`tests/test_pg_tenant_isolation.py` still exists after that migration
finished: it is the test that would catch a regression in the single
most important new guarantee (tool A never reads owner data that was
not shared with it).

Run the full existing `pytest` suite after each phase. Live-verify
mutating HTTP against `localhost:8000` and frontend changes in a real
browser before calling the phase done (`CLAUDE.md`). Phase 8 in
particular is only done when a known corp-wallet fill that
reconciliation currently misses appears after the new fetch, not when
the client method has a unit test with a fake payload.

Do not push to `main` or deploy from this plan. Cutover of a live
tenant (when to run the conservative backfill against real
`tenant_tokens`) is a later operator decision, as it was for the
multi-tenant migration. The backfill itself is written and proven
against a copy first.

## Explicitly out of scope

- Reopening tenant isolation, RLS, `storage.connect()`'s fail-closed
  tenant check, or the app-role vs owner-role split.
- Token re-keying to a canonical `character:<id>` key (decision 2).
- Auto-widening sharing to Sorting (or anyone else) because they
  used to read a table (decision 13).
- Filtering derived tables by sharing (decision 4).
- Making `"characters"` required at the `do_set_tool_grants` layer
  (decision 11).
- Changing CLI signatures other than deleting `eve-trader auth`
  (decision 12).
- Contract-Scanner, Discord alerts, PI calculator (still deferred,
  `CLAUDE.md`).
- Reintroducing Jita as a Production sales channel (`CLAUDE.md`).
- Parallelism beyond one task per owner (decision 7), including a
  job queue. This app's invite-only scale does not justify one.
- Giving the Characters grant any `DEFAULT_TENANT_ID` bypass. First
  admin remains `eve-trader admin bootstrap`.
