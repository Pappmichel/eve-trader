# Central location resolution plan

Status: proposal (2026-10-26). Nothing here is implemented.

Trigger: the Asset Manager showed `Location 1050055429685` for three corp
Manufacturing jobs. The id was a corp **Office** item, not a structure, so no
amount of ESI name lookups could ever resolve it. Fixing that one case
(PR #310, #312) showed the larger problem: knowing *what a location id is*
and *what it is called* is spread over several tools, each with its own gaps.

Goal: one module owns "what is this location id", every tool reads it, every
ESI sync feeds it, and the cost of resolving stays bounded and predictable.

## 1. Current state

### 1.1 What is already central
- **Storage of names**: `structure_names` (per tenant, RLS; `name NULL` = a
  lookup failed), `global_structure_names` (unscoped, successes only),
  `manual_location_names` (per tenant), `sde_stations` (NPC).
- **Lookup order** in `storage.get_location_names`: tenant cache -> global ->
  manual -> SDE stations. Four sequential queries.
- **The one ESI call chain**: `production/esi_sync.resolve_structure_ids`
  (corporation structure list per corp, then `/universe/structures/{id}` per
  character).
- **Asset parent chain**: `resolved_location_id` is written once per asset /
  blueprint row at sync time (items inside ships, containers and Offices walk
  up to the outermost real location).

### 1.2 What is per tool
| Concern | Where | Problem |
|---|---|---|
| When to resolve | Production `sync_esi` (`_discover_structure_names`), Admin bulk job, `do_resolve_structure_name` (on demand), Asset Manager `do_sync` (added #310) | Four triggers, each with its own candidate set. A tenant that never runs Production sync never resolves anything. |
| Which ids are candidates | `storage.candidate_structure_location_ids` (assets, blueprints, job *output* locations, category locations, manual stock) and Production's own asset-only list | Misses market orders, contracts (`start/end_location_id`), wallet transactions, ledger tables, Doctrine and Station Trading structures, character home/location. |
| Reading names | `get_location_names` (Production, Portfolio, PI, Realized names, Character Info, trading wallet), `asset_manager_location_details` (adds system/region), `realized/db.py` region lookup (own copy of tier 1+2), `production/engine.py` | Three different readers with different tiers; only the Asset Manager returns system and region. |
| Office / container -> structure | `resolved_location_id` for assets and blueprints, `_accumulate_stock_at_location` (live lists), and since #312 a hand-rolled map in Asset Manager `collect.py` for job outputs | Same rule implemented three times; contracts, orders and ledger rows do not apply it. |
| Failure handling | `set_cached_structure_name(id, None)` | A failure has no reason, no time and no retry date (see 1.3). |

### 1.3 Concrete weaknesses
1. **No error classes.** `resolve_structure_ids` swallows every `ESIError`.
   403 (no docking right), 404 (gone), 420/429 (rate limit), 5xx and network
   errors all end as "unresolved". `do_resolve_structure_name` then stores
   `name = NULL` and returns that as "cached" forever unless `force`. One
   transient 502 can hide a structure's name for good on that path.
2. **No negative-cache expiry, so retries are all-or-nothing.** The bulk path
   retries *every* unresolved id on *every* Production sync (observed on the
   test server: 604 candidates, 0 resolved, so each sync repeats the same
   per-id calls for every capable character). The on-demand path never
   retries. Neither adapts to "a character just gained access".
3. **Wrong kind of id is treated as a structure.** Anything >= 10^12 is
   assumed to be a structure. Corp Offices and other item ids are in that range
   too (the Providence case), and are queried against
   `/universe/structures/`, producing guaranteed 403s.
4. **Names never refresh.** A renamed structure keeps its old name.
5. **System and region only for some readers.** Reports that group by region
   (Realized) and the Asset Manager each derive them separately.
6. **Cost.** Each reader does its own 1-4 queries per call; the Asset Manager
   and Portfolio call per request. Resolution runs inside one tool's sync, so
   work is duplicated if several tools run.

### 1.4 Baseline measured on the test server (phase 1, 2026-10-26)
Default tenant, `GET /api/admin/locations/report` logic:

| | count |
|---|---|
| distinct location ids across all tables | 880 |
| NPC stations / solar systems | 251 / 2 |
| corp Offices | 28 |
| **containers and ships** (immediate parent of an asset) | **511** |
| real structures | 88 (73 named, 15 failed) |
| candidates the bulk resolver sends to ESI | 604 |
| estimated ESI calls per bulk run (2 capable characters) | ~1010 |
| the same if only real structures were asked | ~32 |

So 511 of the 577 ids the old logic called "structures" are items that can
never resolve (an asset's `location_id` is its immediate parent, which is often
a container or a ship). About 97% of the ESI calls of a bulk run are wasted, and
they are repeated on every Production sync. The kind classification in 2.1 is
therefore the first and largest efficiency win, before any backoff. Several
sources (`ledger_*`, wallet transactions, sell orders, clones, Doctrine
contracts) are not in the bulk candidate list at all; their structures were
mostly already named here, but that is not guaranteed.

## 2. Target design

New package `eve_trader/locations/` (names are proposals). It imports
`storage` and `esi_client`, and no tool package, like `esi_data/`.

### 2.1 One record per location id
New per-tenant table `location_registry` (RLS, copy the shape of
`structure_names`):

| column | meaning |
|---|---|
| `location_id` | PK with `tenant_id` |
| `kind` | `npc_station`, `structure`, `office`, `container`, `solar_system`, `unknown` |
| `parent_location_id` | for `office` / `container`: the outermost real location (what `resolved_location_id` already holds for assets) |
| `name`, `solar_system_id` | resolved values; a global-cache hit is copied here |
| `status` | `resolved`, `forbidden`, `not_found`, `transient`, `manual`, `pending` |
| `attempts`, `last_attempt_at`, `next_retry_at`, `last_error` | negative-cache bookkeeping (HTTP status only, no body) |
| `resolved_at` | for refresh |

`structure_names` stays for now as a write-through compatibility table and is
dropped in the last phase (see 4). `global_structure_names` is unchanged:
names only, successes only, shared across tenants. `manual_location_names`
stays the tenant's override and wins over everything.

### 2.2 One reader
`locations.describe(ids) -> dict[int, LocationInfo]` with `name`, `kind`,
`status`, `solar_system_id`, `solar_system_name`, `region_id`, `region_name`,
`security`, `effective_id` (the parent for Offices and containers).

- One batched query: registry LEFT JOIN global names LEFT JOIN manual names
  LEFT JOIN `sde_stations` LEFT JOIN `sde_solar_systems`/`sde_regions`.
- Short per-tenant in-process TTL cache, invalidated explicitly by the
  resolver and by manual-name writes (the third caching shape in CLAUDE.md).
- `get_location_names`, `asset_manager_location_details` and the region lookup
  in `realized/db.py` become thin wrappers, then are removed.
- A location without a name never displays as a bare id: the label says why
  (`status`), e.g. "Structure 1049... (no access)".

### 2.3 One feeder
`locations.note_seen(ids, source)` registers ids (kind guessed from the id
range, `pending`) and is called from the places that already write ESI data:
asset, blueprint, job, order, contract and wallet-transaction fetchers, and
the ledger ingest. Cheap: one `INSERT ... ON CONFLICT DO NOTHING` per batch.
Offices and containers get `parent_location_id` here from the asset rows being
written, so the Office rule exists exactly once and every consumer (jobs,
contracts, orders, ledger) uses it.

### 2.4 One resolver
`locations.resolve_pending(budget)` runs as the last step of the existing
orchestrator calls (`do_sync_for_tool`, `do_sync_kinds`, `do_sync_due`), once
per call, never per tool. It is also the body of the Admin bulk job and the
on-demand endpoint.

- Picks rows that are `pending`, or whose `next_retry_at` has passed, or whose
  `resolved_at` is older than the refresh age.
- Skips ids that are not structures (Offices and containers resolve through
  their parent, never through ESI).
- Per run: corporation structure list once per corp (cached for the run),
  then `/universe/structures/{id}` per capable character, trying the
  character that last succeeded for that structure first.
- Error classes decide the outcome: 200 -> `resolved` (also written to the
  global cache); 403 -> `forbidden`, retry in 7 days or sooner when the set of
  capable characters changes or a token is re-authorised; 404 -> `not_found`,
  retry in 30 days; 420/429/5xx/network -> `transient`, short backoff with the
  existing `min(interval, 6 h)` style cap, and never overwrites a good name.
- Bounded: a call budget per run (default a few hundred) so a first run over a
  big backlog cannot starve other jobs. Leftovers stay `pending`.
- Idle ticks stay free: nothing pending means no token or ESI object is built.
- Operator fallback (Default Tenant characters) keeps its current semantics and
  is tried after the tenant's own characters.

### 2.5 Manual naming as the last tier
Some structures stay `forbidden` forever. Any list that shows an unresolved
location offers "Name this location" (writes `manual_location_names`, status
`manual`). Today this only exists on Production pages.

## 3. Candidate sources to cover
Everything that stores a location id feeds `note_seen`, not just assets:
`character_assets` / `corp_assets` (+ resolved), blueprints, industry job
output locations, `character_sell_orders` and ledger market orders, contracts
(`start_location_id`, `end_location_id`), wallet transactions and the ledger
wallet tables, Doctrine and Production config structures, category locations,
manual stock and blueprints, character home and current location.

## 4. Phases
Each phase ships on its own and leaves the app working.

1. **Baseline.** Count per tenant: ids by kind, resolved/failed, ESI calls per
   sync. Add a read-only Admin view listing unresolved ids with reasons. No
   behaviour change. (Gives the before/after numbers for the efficiency claim.)
2. **Registry + reader.** `docs/location_registry_schema.sql` (new schema file,
   so it must be added to `deploy/deploy.sh`, `deploy/README.md`, root
   `README.md`, `.cursor/start.sh` and `tests/pg_helpers.py` order), backfill
   from `structure_names` and the asset tables, `locations.describe`, and
   `get_location_names` delegating to it. Readers unchanged for callers.
3. **Resolver core.** `resolve_pending` with error classes, backoff, budget,
   refresh. Unit-tested with a fake ESI client for every error class, the
   Office/container skip, backoff arithmetic and the global-cache copy.
4. **Hook in.** Call it from the orchestrator; delete
   `_discover_structure_names`, point the Admin bulk job and
   `do_resolve_structure_name` at it, remove the Asset Manager call added in
   #310. `note_seen` in all fetchers.
5. **Migrate readers.** Asset Manager, Portfolio, PI, Realized, Character Info,
   trading wallet, Production engine -> `describe`. Remove the hand-rolled
   Office map in `asset_manager/collect.py` and the region copy in
   `realized/db.py`.
6. **UI.** Unresolved reason in the label, "Name this location" on Asset
   Manager, a "Retry now" for one id.
7. **Cleanup.** Stop writing `structure_names`, drop it after one release.

## 5. Risks and rules
- **Tenant isolation.** Registry is RLS-scoped. Only names that were resolved
  successfully go to the global cache; negative results (`forbidden`) are never
  shared, because access differs per tenant. `describe` reads global names
  only for ids the tenant's own data references (no lookup by arbitrary id
  from the client).
- **ESI load.** Backoff and a per-run budget are the main safeguards; compare
  call counts against the phase-1 baseline before and after phase 4.
- **Migration.** Backfill is idempotent; `structure_names` keeps working until
  phase 7, so a rollback after phase 4 only loses the new bookkeeping.
- **Behaviour change to flag.** Names refresh monthly, so a renamed structure
  shows its new name; a `forbidden` structure is retried weekly instead of
  every sync.
- Live-verify each phase against Server 1 with a full ESI sync first, then
  Server 2, as the project's verification rule requires.

## 6. Decisions (confirmed 2026-10-26)
1. Retry ages: `forbidden` 7 days (earlier when the set of capable characters
   or a token changes), `not_found` 30 days, refresh of resolved names 30 days.
2. "Name this location" is tenant-only (`manual_location_names`), never shared.
3. The resolver runs after syncs and on demand only, not on page open.
4. Scope of the first round: phases 1-4. Phases 5-7 (reader migration, UI,
   cleanup) follow later.
