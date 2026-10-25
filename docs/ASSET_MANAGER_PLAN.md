# Asset Manager - plan

Status: **phases 0-3b and 5-7 implemented on branch `feature/asset-manager`
(2026-10-25, section 14); phase 4 (container/division names) and the removal
of the old Production tab are open.** Decisions D1-D13 confirmed (section
10), review items decided (sections 11 and 13). Server checks (section 12)
are open: they tune, but do not block, what was built - except S-09/S-10,
which phase 4 waits on.

Goal: turn Production's "Asset Search" tab (one exact item name -> where is
it, how much, who owns it) into a full asset manager: browse every asset by
location and container, filter and search across all of them, see value and
volume, and know how fresh the data is.

## 1. Current state (read from the code, 2026-10-25)

| Piece | Where | What it does |
|---|---|---|
| Page | `frontend/src/pages/production/AssetSearch.tsx` | One `SearchableSelect` over all SDE type names, one Search button, a table Location / Owner / Quantity and a total. |
| Route | `POST /api/production/asset-locations` (`api/routers/production.py`) | Body `{item_name}`; gated by the `production` grant. |
| Action | `production/actions.py::do_search_item_locations` | Exact-name lookup via `storage.search_sde_types`, then the owners shared with **Production** for `assets` (`shared_production_owner_ids("assets")`). |
| Storage | `storage.search_item_stock_locations` | Reads `character_assets` + `corp_assets` for one `type_id`, drops `NON_STOCK_LOCATION_FLAGS`, groups by `(resolved_location_id, owner_name)`. |
| Data | `character_assets` / `corp_assets` (`docs/phase1_schema.sql`) | Full ESI asset list per owner: `item_id`, `type_id`, `location_id` (immediate parent), `location_flag`, `quantity`, `is_blueprint_copy`, `is_singleton`, `assembled_or_fitted`, `resolved_location_id` (outermost station/structure), `resolved_hangar_flag`, owner id/name. Written by `storage.replace_assets` from `esi_data/fetchers.py::fetch_character_assets` / `fetch_corporation_assets`. |

Gaps and defects found while reading:

1. **Location names use only 2 of the 4 tiers.** `search_item_stock_locations`
   looks up `structure_names` then `sde_stations` and skips
   `global_structure_names` and `manual_location_names`. `storage.
   get_location_names` already does all four, batched. The search also runs
   one or two queries per result row (N+1).
2. **Only Production shares count.** A character that shares assets with
   Doctrine, Sorting, Trading or Portfolio but not Production is invisible.
3. **One exact type at a time.** No substring search, no group/category
   search, no "everything at this station" view.
4. **No hierarchy.** Containers, ships and corp hangar divisions are
   flattened into the outermost location. The immediate parent
   (`location_id`) and the flags are stored but never shown.
5. **No names for containers, ships or corp divisions.** ESI
   `POST /characters/{id}/assets/names` / `/corporations/{id}/assets/names`
   (same scope as the asset list) and `GET /corporations/{id}/divisions`
   are not called anywhere.
6. **No value, volume, system or region.** All are derivable: Portfolio's
   price function (`portfolio._priced`, CLAUDE.md "Price sources matrix"),
   `sde_types.volume` / `type_packaged_volume`, `structure_names.
   solar_system_id` / `sde_stations.solar_system_id` -> `sde_solar_systems`
   -> `sde_regions`.
7. **Silent exclusions.** Asset Safety, deliveries and corp market items are
   dropped without a hint; manual stock (`manual_stock`) is not shown;
   assembled ships are counted but not marked.
8. **No freshness.** The hint says "last Sync ESI Data run" but the page
   never shows when that was or offers a sync.
9. **Index.** The asset tables are indexed on `(type_id,
   resolved_location_id)` and on the owner id, not on
   `resolved_location_id` alone, which a "browse by location" view needs.

## 2. Goals and non-goals

Goals (v1 = phases 0-5):

- G1 One page lists every asset of every owner shared with the tool:
  filter by owner, region/system/location, category/group, flag, text.
- G2 Location browser: region -> system -> station/structure -> hangar /
  corp division -> container/ship -> items, with counts, m3 and value at
  every level.
- G3 Real names for containers, ships and corp divisions.
- G4 Value per row and per level with the same price rule as Portfolio
  Total Wealth; unpriced items stay unpriced and are listed, never guessed.
- G5 Freshness per owner and a sync button.
- G6 Export: CSV and an EVE multibuy / appraisal paste list.
- G7 Items in open sell orders and outstanding contracts appear as their
  own sources, so the page shows everything the tenant owns (D13).
- G8 The old Production Asset Search is removed once the new page covers
  it (D12).

Non-goals:

- Moving, repackaging or trashing items. ESI has no endpoint for that.
- Replacing Portfolio Total Wealth. The asset manager shows assets;
  Portfolio stays the place for wealth (incl. orders, contracts, escrow,
  wallets) and its history.
- Changing any demand-side stock reader. The assembled-ships rule and the
  `NON_STOCK_LOCATION_FLAGS` rule stay as they are for planning; the asset
  manager only *shows* those rows, marked.
- A live ESI asset read per page open. The page reads the synced snapshot.

## 3. Decisions (confirmed 2026-10-25)

- **D1 Own tool, tool key `asset_manager`.** A new tenant-facing tool with
  its own grant, router `/api/asset-manager/`, page `/assets` and its own
  sharing row (added to `consuming_tools` of `assets`, `corp_divisions`,
  `market_orders` and `contracts` in `esi_data/registry.py`). Reason: assets
  are not Production-specific (gap 2). The key must **not** be `assets`:
  that is the data-kind key, and tool keys must never collide with
  data-kind keys (Character Management plan, decision 8).
- **D2 Default grant.** `asset_manager` joins `DEFAULT_TOOL_KEYS`, and
  Admin's checkbox auto-tick for `characters` treats it as ESI-consuming.
- **D3 Price rule = Portfolio's.** Values come from the same function that
  prices Total Wealth (manual price, else cheaper of home sell and Jita sell
  plus haul cost; BPCs via `_bpc_run_values`; BPOs per Portfolio's
  blueprint rule). Refactor it into a shared call if needed, never copy it.
  Unpriced rows show "-" and are counted in a footer.
- **D4 Everything is shown, nothing is silently dropped.** Asset Safety,
  deliveries, corp market and assembled/fitted rows appear with a badge and
  have filter toggles (default: all on, except a "stock only" quick filter
  that applies exactly the planners' rules).
- **D5 Manual stock is a separate source.** `manual_stock` rows appear with
  `source: "manual"` (same convention as the Manual tracking plan),
  location 0 shown as "No location". Toggle, default on. Only for a
  session that also holds `production` (manual stock is Production data;
  same cross-tool rule as PI's demand view, review R-8).
- **D6 Item names via `/assets/names`, stored on the asset row.** New
  nullable column `item_name` on `character_assets` / `corp_assets`, filled
  by the asset fetcher before `replace_assets` for singleton items whose
  category is Ship or whose group is a container (ESI only names
  singletons; max 1000 ids per call). A name fetch error never fails the
  asset sync (logged, names stay NULL) - same rule as the realized-archive
  hook in `fetchers.py`.
- **D7 Corp division names as a new data kind `corp_divisions`.**
  Corporation-only, scope `esi-corporations.read_divisions.v1`, Director
  role, `TIER_RARE`, consumer `asset_manager` only (Sorting is added when
  its code actually reads the names - the registry lists real readers
  only, review R-9). New scope =
  re-auth; until then labels fall back to "Division 1".."Division 7".
  Alternative (fetch inside the assets fetcher) rejected: it would tie a new
  scope to an existing kind and break the scope/reauth bookkeeping.
- **D8 Server-side filtering and paging.** A large corp can have tens of
  thousands of asset rows. The list endpoint filters, sorts and pages in
  SQL; aggregates (per location / per level) are computed in SQL; only the
  container tree of one selected location is built in Python.
- **D9 Hierarchy from stored columns, no new parent table.** `location_id`
  is the immediate parent and `(item_id, owner_name)` the key, exactly what
  `_resolve_locations` already walks. The tree for one station is built
  from that station's rows on request.
- **D10 Union of the shares of granted tools, still fail closed.** For
  each kind it reads (`assets`, `blueprints`, `corp_divisions`,
  `market_orders`, `contracts`, `industry_jobs`; `blueprints` added in the
  review, R-1; `industry_jobs` for F-2) the tool sees every owner that
  shares that kind with a consuming tool **the tenant still holds**, or
  with the Asset Manager itself (R-6, confirmed). A share with a tool whose
  grant was removed does not count: the scheduler no longer refreshes it
  (`orchestrator.pending_due` filters by granted tools), so it would show a
  frozen snapshot - the same union rule the realized-profit archive uses
  (`realized/access.py`, unified plan U3a). A character can also share a
  kind with the Asset Manager alone. Revoking the last share of a kind
  hides that owner's rows immediately; an owner with no share is never
  read. This is acceptable because a tenant is one character (DB-enforced),
  so the union never crosses people. Implement it as one helper in
  `esi_data/access.py` (`owners_shared_with_any(kind, granted_tools)`;
  `granted_tools` None = gate off = every tool, same convention as
  `tenant_eligibility.granted_tools()`), not as a loop over tool names in
  the asset package.
- **D11 Sync button = `do_sync_for_tool("asset_manager")`** (kinds
  `assets`, `blueprints`, `corp_divisions`, `market_orders`, `contracts`,
  `industry_jobs`), with the usual per-owner freshness from
  `esi_freshness`.
- **D12 Production's Asset Search tab is removed without a replacement
  link** in phase 5: the tab, its route in `App.tsx`, the
  `ProductionLayout`/QuickNav entries, `POST /api/production/asset-locations`,
  `do_search_item_locations`, `search_item_stock_locations` (after a grep
  for other callers) and their tests. Phase 0 still fixes it, because it
  stays live until then.
- **D13 Listed and contracted items as virtual rows.** Items in the
  owners' open sell orders (`market_orders` snapshot) and outstanding
  item-exchange contracts the owner issued (`contracts` snapshot, items
  with `is_included`) appear with `source: "listed"` / `"contract"`, a
  badge and a toggle (default on). Same definitions as Portfolio Total
  Wealth's `listed` / `contracts` components, so the two pages agree;
  reuse Portfolio's loaders, do not re-derive them. Location = the order's
  station/structure, or the contract's start location. Buy orders and
  escrow are not items and are not shown.

## 4. Data model

Schema changes (all `ADD COLUMN IF NOT EXISTS` / new table in an existing
file, so no new schema file and no deploy-list changes - see CLAUDE.md
"A brand-new schema file"):

```sql
-- docs/phase1_schema.sql, next to the asset tables
ALTER TABLE character_assets ADD COLUMN IF NOT EXISTS item_name TEXT;
ALTER TABLE corp_assets      ADD COLUMN IF NOT EXISTS item_name TEXT;
CREATE INDEX IF NOT EXISTS idx_character_assets_resolved_location
    ON character_assets (tenant_id, resolved_location_id);
CREATE INDEX IF NOT EXISTS idx_corp_assets_resolved_location
    ON corp_assets (tenant_id, resolved_location_id);

-- docs/esi_access_schema.sql (snapshot table for D7)
CREATE TABLE IF NOT EXISTS corp_divisions (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    owner_corporation_id BIGINT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('hangar', 'wallet')),
    division INTEGER NOT NULL CHECK (division BETWEEN 1 AND 7),
    name TEXT,
    PRIMARY KEY (tenant_id, owner_corporation_id, kind, division)
);
-- + ENABLE ROW LEVEL SECURITY, tenant_isolation policy, GRANT (copy shape)
```

`replace_assets` row tuples gain an optional `item_name` slot (like
`is_singleton` did). The PL/pgSQL helpers in `docs/esi_access_schema.sql`
that copy asset rows (lines ~320-370) must carry the new column too.

Row shape returned by the API (one row per stored asset, or per stack
group in grouped mode):

```
item_id, owner_type, owner_id, owner_name, type_id, type_name,
group_name, category_name, quantity, is_blueprint_copy, is_singleton,
assembled_or_fitted, location_flag, hangar_label, parent_item_id,
parent_name, path (["Jita IV-4", "Corp Hangar 3 'Fuel'", "Fuel Box"]),
location_id, location_name, solar_system_name, region_name, security,
unit_volume_m3, total_volume_m3, unit_price, total_value, price_source,
badges (["asset_safety","assembled","fitted","delivery","manual",
         "listed","contract","in_production","bpo","bpc"]),
source ("esi" | "manual" | "listed" | "contract" | "in_production"),
me, te, runs (blueprints only), wanted_by ([{tool, qty}], F-4)
```

## 5. API (`/api/asset-manager/`, gated by `asset_manager`)

| Method + path | Purpose |
|---|---|
| `GET /items` | Filtered, sorted, paged list. Params: `q` (type name / item name / location substring), `type_ids`, `group_ids`, `category_ids`, `owner` (type:id, repeatable), `region_id`, `system_id`, `location_id`, `container_item_id`, `flags`, `sources` (esi/manual/listed/contract), `stock_only`, `group_by` (`none` / `type` / `type_location`), `sort`, `page`, `page_size` (max 500). Returns rows + totals (count, quantity, m3, value, unpriced count) for the whole filter, not the page. |
| `GET /locations` | Tree summary: region -> system -> location with item count, m3, value, owners. |
| `GET /locations/{location_id}/tree` | Hangars/divisions -> containers/ships -> items for one location, with per-node totals. |
| `GET /freshness` | Per owner: last success/attempt/error for `assets`, `corp_divisions`, `market_orders` and `contracts`, plus not-shared / reauth-needed owners. |
| `POST /sync` | `do_sync_for_tool("asset_manager")` as a `pipeline_runner` job. |
| `GET /export.csv` | Same params as `/items`, unpaged, streamed. |

All read routes are GET-with-logic, so they go through `do_*` actions in
`eve_trader/asset_manager/actions.py` (CLAUDE.md: a read that needs a
decision belongs in `do_*`). CLI: `eve-trader assets list|locations|export`
calling the same actions.

## 6. Phases

### Phase 0 - fix the existing search (small, independent, can ship first)
- `search_item_stock_locations`: one grouped SQL query, names via
  `get_location_names` (all four tiers), no per-row queries.
- Show the last asset sync time (from `esi_freshness`) on the page.
- Tests: name tiers (global / manual name now appear), grouping unchanged.

### Phase 1 - tool scaffolding
- `asset_manager` in `ALL_TOOL_KEYS`, `DEFAULT_TOOL_KEYS`,
  `_TOOL_PATH_PREFIXES`, registry `consuming_tools` of `assets`,
  `blueprints`, `market_orders`, `contracts` and `industry_jobs`
  (`corp_divisions` comes in phase 4), Admin
  auto-tick list, Landing card, sidebar/QuickNav, `App.tsx` route.
- Package `eve_trader/asset_manager/` (`actions.py`, `queries.py`,
  `tree.py`), router `api/routers/asset_manager.py`, empty page.
- `eve-trader admin grant-defaults` note in the release notes (existing
  tenants need the grant; characters need the new share).
- CLAUDE.md: new section + `ALL_TOOL_KEYS` count.
- `esi_data/access.owners_shared_with_any(kind)` (D10).
- Tests: gate (401/403); union filter - an owner sharing assets only with
  Doctrine is visible, an owner with no share is not, revoking the last
  share hides it; router monkeypatch target.

### Phase 2 - inventory list (G1, G4, G5)
- `queries.py`: one SQL over both asset tables (UNION ALL) joined to
  `sde_types` / `sde_groups` / `sde_categories`, location -> system ->
  region via the four name tiers + `structure_names.solar_system_id` /
  `sde_stations`; filters, sort, paging, totals.
- Value via the Portfolio price function (D3), applied to the page's
  type_ids plus totals over the whole filter (price map cached per request).
- Volume: packaged volume unless `is_singleton` (assembled ship /
  container: SDE volume).
- Frontend: filter bar (text, owner multi-select, region/system/location,
  category/group, badge toggles, "stock only"), `DataTable` with server
  paging, totals footer, unpriced counter, freshness strip + sync button,
  filters in the URL query (shareable, back button works).
- Virtual rows for listed / contracted items (D13) through Portfolio's
  loaders, in the same UNION and the same totals.
- Tests: filters, paging totals = sum over filter, badges, manual rows,
  listed / contract rows match Portfolio's components for the same data,
  unpriced handling, RLS (second tenant sees nothing).

### Phase 3 - location browser and container tree (G2)
- `GET /locations` and `/locations/{id}/tree`; `tree.py` builds the
  hierarchy from `location_id` within `(owner, item_id)` (D9), hangar
  labels from flags (`Hangar`, `CorpSAG1..7`, `ShipHangar`, `Cargo`, slot
  flags grouped as "Fitting").
- Frontend: two-pane layout - left tree (region/system/location), right
  the location's hangars -> containers -> items with totals; clicking a
  node sets the list filter (shared state with phase 2's table).
- Tests: nested container in ship in corp office, cycles capped, an item
  whose parent is missing (partial sync) lands under "Unknown container".

### Phase 4 - names (G3)
- ESI client: `character_asset_names` / `corporation_asset_names` (POST,
  chunks of 1000), `corporation_divisions`.
- Fetcher: names for singleton ships/containers before `replace_assets`
  (D6); new `corp_divisions` kind + fetcher + snapshot table (D7),
  registry entry, Characters page shows the new share/scope.
- Search `q` also matches `item_name` and division names.
- Tests: name fetch failure keeps the sync green; "None"/empty names stored
  as NULL; division fallback labels.

### Phase 5 - search 2.0, exports, old tab removed (G6, G8)
- Type picker with multi-select and group/category chips; substring match
  on type names server-side.
- CSV export endpoint; "Copy as EVE list" (`Type Name<TAB>Qty` per line,
  grouped by type, current filter) for multibuy / appraisal paste.
- Row actions: "Add stock target" (opens Production's form, only with the
  `production` grant), "Show in Portfolio".
- Remove Production's Asset Search tab completely (D12): page,
  `App.tsx` route, `ProductionLayout` and QuickNav entries,
  `POST /asset-locations`, `do_search_item_locations`,
  `search_item_stock_locations` (if no caller is left) and their tests.

### Phase 6 - change history (confirmed)
- Daily snapshot of `(owner, location, type) -> quantity` (new table,
  written after each asset sync, retention e.g. 90 days), diff view "what
  arrived / left since date X". Size the retention after S-12.

### Phase 7 - insights (confirmed)
- Consolidation helper: a type spread over many locations, with m3 to haul
  to the home structure and the freight cost at `haul_cost_per_m3`.
- Forgotten assets: value above a threshold at locations no tool uses,
  not demanded by Production/Doctrine/Sorting.
- Asset Safety list with the system and a reminder of the timer (the
  ESI asset list carries no wrap date; only "in safety since first seen").

## 7. Pitfalls

- **P-01** Tool key `assets` would collide with the data kind (D1).
- **P-02** `item_id` is not unique across owners for stacks; always key on
  `(item_id, owner_name)` (see `_resolve_locations` docstring).
- **P-03** A shared corp mixes other members' items into `corp_assets`;
  label corp rows with the corp name, not as the user's own character.
- **P-04** Two tenants holding the same corp: RLS already isolates rows;
  never cache asset query results across tenants (key any cache on
  tenant_id).
- **P-05** `/assets/names` returns 404/error for ids that are not
  nameable; only send singleton ships/containers, and treat ESI's literal
  `"None"` as no name.
- **P-06** Corp divisions need the Director role; a non-director token
  gets 403 - map to `reauth_needed`/role-missing, not to an error banner.
- **P-07** Office (`OfficeFolder`) and corp hangar items: the office is a
  container whose children carry `CorpSAG*` flags; the tree must show the
  divisions, not "Office".
- **P-08** Pricing every owned type is a large Goonmetrics call; reuse the
  cached price plumbing (`_goonmetrics_prices`) and price only the types in
  the current filter.
- **P-09** `float4` security values: round with `production.constants.
  _rounded_security` before showing high/low/null (PI section of CLAUDE.md).
- **P-10** Rows synced before `assembled_or_fitted` / `item_name` existed
  read as false/NULL until the next asset sync; show "Sync to refresh" when
  the newest asset sync predates the deploy.
- **P-11** Removing the old route: router tests monkeypatch module objects;
  delete the tests together with the route.
- **P-12** No double counting with D13: ESI's asset list already omits
  listed and contracted items (CLAUDE.md, Total Wealth), so virtual rows are
  added, never subtracted. Courier contracts are not item rows.
- **P-13** D10 union: the union helper must exclude `live_only` kinds and
  must not read a kind whose last share was revoked from a stale cache.

## 8. Testing and verification

- Unit tests per phase (sections above), `pytest -n auto` green.
- Live check per phase against `localhost:8000` (real response bodies) and
  a Playwright screenshot of the page (throwaway `_verify_*.mjs`, deleted
  afterwards), per CLAUDE.md.
- Performance check: `/items` with the largest real owner, target < 1 s for
  one page and totals.

## 9. Real-data checks

Merged into section 12 (S-01..S-13) during the review: R-01 -> S-01,
R-02 -> S-02, R-03 -> S-08, R-04 -> S-10, R-05 -> S-03, R-06 -> S-07,
R-07 -> S-12.

## 10. Decision log (asked one by one, 2026-10-25)

| # | Question | Answer |
|---|---|---|
| D1 | Own tool or Production tab? | Own tool `asset_manager` |
| D2 | Default grant? | Yes, in `DEFAULT_TOOL_KEYS` |
| D3 | Price rule? | Portfolio's (Total Wealth) |
| D4 | Asset Safety, deliveries, assembled ships? | Show all, badged, filterable |
| D5 | Manual stock? | Yes, own source |
| D6 | Container/ship names? | `/assets/names` during the asset sync |
| D7 | Corp division names? | New data kind `corp_divisions` |
| D8 | Filtering/paging? | Server-side |
| D9 | Container tree source? | Existing columns, built per location |
| D10 | Which owners? | Union of all shares per kind; own sharing row too; also for orders/contracts |
| D11 | Sync button? | Yes, `do_sync_for_tool` |
| D12 | Old Production tab? | Removed in phase 5, no redirect |
| D13 (Q4) | Listed / contracted items? | Yes, own sources |
| Q3 | Phases 6 and 7? | Both in the plan |

## 11. Review (2026-10-25)

Second pass over the whole plan against the code. Each item says what is
wrong or missing and what changes. Items marked **(open)** need a user
decision; the rest are folded into the plan above or are implementation
rules for the phase named.

### 11.1 Defects in the plan

- **R-1 Blueprints were missing (fixed in D10).** The asset list contains
  every blueprint item, but ME/TE/runs live in `character_blueprints` /
  `corp_blueprints` (`blueprints` kind). Portfolio excludes those item_ids
  from assets and values them from the blueprint table
  (`storage.load_assets_grouped`, `portfolio._collect_wealth`): BPO via the
  manual price, BPC via `_bpc_run_values` x runs. Without the `blueprints`
  share the asset manager can neither show ME/TE/runs nor apply D3, and
  would value a BPC at the BPO market price. Phase 2: join the blueprint
  table on `(item_id, owner)`, show ME/TE/runs columns, BPO/BPC badge.
- **R-2 Price sorting contradicts server-side paging (D8).** Prices come
  from Python (Goonmetrics, manual prices, BPC run values), not SQL. Rule
  for phase 2: compute the price map for the distinct type_ids of the whole
  filter (usually a few thousand; `current_prices` is one cached full-market
  call per market, 60 s TTL), pass it to SQL as a `VALUES` CTE / temp table,
  then sort, page and total in SQL. Never page first and price after -
  totals and "sort by value" would be wrong.
- **R-3 Do not copy Portfolio's collector, extract it.** `_collect_wealth`
  already builds exactly the rows D13 needs (sell orders from the
  `market_orders` snapshot, outstanding contract items from the ledger
  archive, de-duplicated per contract) and prices them. Phase 2 first
  extracts a shared, owner-parameterised loader + pricer (e.g.
  `portfolio_items.py`) that both Portfolio and the asset manager call;
  Portfolio's numbers must not change (snapshot test before/after).
- **R-4 Contracts for an asset-manager-only share are never archived.**
  Outstanding contract items and sell orders come from the ledger archive,
  and `esi_data/fetchers.py` archives an owner only while a realized-profit
  tool or Portfolio shares the kind (`_archive_for_production` /
  `_shared_with_portfolio`). An owner sharing contracts only with the asset
  manager would show no contract rows. Phase 1: generalise
  `_shared_with_portfolio` to "shared with Portfolio or the asset manager"
  (still not part of the costing union) - stays inside `esi_data`, no tool
  import.
- **R-5 Contract rows have no location in Portfolio.** `outstanding_
  contract_lines` drops `start_location_id`. D13 promises a location, so
  the extracted loader must return `ledger_contracts.start_location_id`
  (Portfolio may ignore it).
- **R-6 Stale data from the union (decided: granted tools + own share,
  folded into D10).** The scheduler refreshes only
  sharing rows of tools the tenant still holds (`orchestrator.pending_due`,
  `granted_tools` filter). An owner shared with assets only for Doctrine,
  in a tenant that lost the Doctrine grant, stays readable by D10 but is
  never refreshed - the page would show a frozen snapshot as current.
  Proposal: union over the sharing rows of **granted** tools plus
  `asset_manager` itself; every row still shows its own age (G5).
- **R-7 Phase 6 must not write from the asset package inside the
  fetcher.** CLAUDE.md allows exactly one tool import in
  `esi_data/fetchers.py` (realized). The history snapshot is either a
  storage function called from `replace_assets`, or - preferred - a daily
  per-tenant scheduler job that copies the current grouped totals
  (one row per owner/location/type/day, same calendar-date "due" rule as
  `portfolio_snapshot`). Inactive tenants then get gaps, as in Portfolio.
- **R-8 Manual stock is Production data (fixed in D5).** Show it only when
  the session also holds `production`, the same rule as the PI demand view
  and the Doctrine skill check.
- **R-9 Registry consumers must be real readers (fixed in D7).** Sorting
  removed from `corp_divisions` until it reads the names.
- **R-10 Locations that are not stations or structures.** The fetcher does
  not store ESI's `location_type`. Items in space (deployed mobile depots,
  POCOs, structures themselves in corp assets) or whose parent is missing
  end with a solar system id or an unknown id as `resolved_location_id`.
  `get_location_names` resolves neither solar systems nor regions. Phase 2:
  classify ids by range (60M stations, 30M systems, 1.0e12+ structures /
  items), resolve system names from `sde_solar_systems`, and group
  everything else under "In space / unknown"; store `location_type` from
  the fetcher (new nullable column, same migration as `item_name`).
  Check the real distribution first (S-03).
- **R-11 Unresolved structure names.** A player structure without docking
  access stays unnamed and without a system. Add a "Resolve names" action
  that calls the existing `do_resolve_structure_name` path for the
  location ids on the page, and show unnamed ones with the plain id plus a
  manual-name link (Manual tracking's `manual_location_names`).
- **R-12 `DataTable` has no server-side mode.** `frontend/src/components/
  DataTable.tsx` pages and sorts in the browser. Phase 2 needs a manual
  pagination/sorting mode (or a separate component); size it as its own
  task.
- **R-13 Removing the Production tab (D12) needs the grant first.**
  `DEFAULT_TOOL_KEYS` only affects new users and approvals. Order for
  phase 5: deploy phase 1-4, run `eve-trader admin grant-defaults`, then
  remove the tab. Otherwise existing Production users lose asset search.
- **R-14 Names refetch must not wipe names.** `replace_assets` deletes and
  reinserts the owner partition. If `/assets/names` fails, carry the
  previous `item_name` over by `(item_id, owner)` instead of writing NULL.
- **R-15 CSV injection.** Container and ship names are free text set by
  any corp member. Prefix cells starting with `= + - @` (and tab/CR) with
  `'` in the CSV export.
- **R-16 Freshness per kind, not per page.** Listed orders refresh on a
  different tier than assets; totals that mix sources must show the oldest
  source age, not the newest.
- **R-17 CLAUDE.md updates.** New section for the tool, the `ALL_TOOL_KEYS`
  count, the price-sources matrix row "What is an owned item worth" gains
  the asset manager, and the "Assembled ships are not stock" section names
  the asset manager next to Portfolio as a deliberate non-filter.

### 11.2 Improvements

- **I-1 Phase order.** Phase 3 (location browser) gives the most value per
  effort after phase 2 and needs no new ESI calls; keep it before names.
  Phase 0 can ship alone this week.
- **I-2 Grouped view as default.** Raw asset rows (one per stack) are
  noisy; default to `group_by=type_location` with a toggle to item level.
- **I-3 URL state.** Filters, selected node and grouping in the query
  string (already in phase 2). A link only works for the same tenant;
  tenants are isolated, so it is a bookmark, not a share link.
- **I-4 Performance budget.** One page incl. totals < 1 s for the largest
  owner (S-01); location summary cached per tenant until the next asset
  sync (key on tenant_id + newest `esi_freshness` stamp - explicit
  invalidation for free).

### 11.3 Missing features (all five confirmed 2026-10-25, see section 13)

- **F-1 Ship view (yes, with Doctrine match).** For an assembled ship: its fitting, cargo and bays as
  a tree, an EFT export, and - if the tenant holds `doctrine` - which
  doctrine fitting it matches (reuse Doctrine's matcher through storage,
  no doctrine import into the asset package).
- **F-2 In-production items as a source (yes).** Outputs of running industry jobs
  (`industry_jobs` kind) as virtual rows "in production" at the output
  location; Production already computes incoming quantities.
- **F-3 Saved views (yes, server-side).** Named filter presets per tenant (e.g. "Fuel at
  all structures", "Ships in Jita").
- **F-4 Compare to demand (yes, per grant).** A column "wanted by" (Production stock target,
  Doctrine stockpile, Sorting pot) - the same data Sorting already
  aggregates; also the base for phase 7's "forgotten assets".
- **F-5 Type detail (yes, with price history).** "Where is all my Tritanium":
  one row per type with locations expandable - mostly I-2 plus a type
  detail drawer with a mini price history.

Not proposed: PI storage contents (separate ESI endpoint per planet, not
assets), "open in game" (new `esi-ui.open_window` scope for little value).

## 12. Server checks before implementation

The plan's design depends on facts only the production server has. Run
these read-only on the server (SQL as the app role inside a tenant, or
the owner role for counts across tenants) **before the phase named**;
record each result here. Phase 0 and phase 1 need none of them.

| # | Check | Why / decides | Before |
|---|---|---|---|
| S-01 | Asset rows per tenant and largest owner (`count(*)` per owner in both tables) | Paging, cache, performance budget (D8, I-4) | Phase 2 |
| S-02 | Distinct `resolved_location_id`; share without a name in all four tiers | Size of R-11, UI for unnamed places | Phase 2 |
| S-03 | `resolved_location_id` by id range (station / structure / system / other) and rows whose parent is missing | R-10 grouping, orphan handling in the tree | Phase 2 |
| S-04 | Distinct `location_flag` and `resolved_hangar_flag` values with counts | Hangar labels in phase 3 (P-07) | Phase 3 |
| S-05 | Blueprint items: assets rows whose item_id is not in the blueprint tables (unshared or stale blueprints) | R-1 fallback valuation | Phase 2 |
| S-06 | Sharing matrix: owners x tools for `assets`, `blueprints`, `market_orders`, `contracts`; owners whose only share is an ungranted tool | R-6 decision, real effect of D10 | Phase 1 |
| S-07 | Distinct owned type_ids and time of `portfolio._priced` for them on the server | R-2 price map cost | Phase 2 |
| S-08 | Count of singleton ships/containers per owner | `/assets/names` call volume (D6) | Phase 4 |
| S-09 | One live `POST /characters/{id}/assets/names` with a real token: response for an unnamed container, a ship, a non-singleton id | P-05 behaviour (ESI docs are vague) | Phase 4 |
| S-10 | Which shared corps have a Director token; one live `GET /corporations/{id}/divisions` | D7 coverage, 403 mapping (P-06) | Phase 4 |
| S-11 | Outstanding contracts with `start_location_id` NULL; listed orders per owner | R-5, D13 | Phase 2 |
| S-12 | Distinct (owner, location, type) per tenant | Phase 6 storage per day and retention | Phase 6 |
| S-13 | Current Portfolio breakdown JSON saved | Before/after check for the R-3 extraction | Phase 2 |

S-01..S-07, S-11 and S-13 can be one throwaway read-only script in the
scratch directory (not committed), same method as the dynamic stock
targets plan. S-09 and S-10 make one live ESI call each with an existing
token; nothing is written.

## 13. Decisions on the review items (asked one by one, 2026-10-25)

| # | Question | Answer | Where it goes |
|---|---|---|---|
| R-6 | Which shares count for the union? | Shares of granted tools + the Asset Manager's own | D10 |
| F-1 | Ship view? | Yes: fitting/cargo/bay tree, EFT export, Doctrine match with the `doctrine` grant | Phase 3b |
| F-2 | Running job outputs as a source? | Yes, source `in_production` | Phase 2 |
| F-3 | Saved views? | Yes, stored server-side per tenant | Phase 5 |
| F-4 | "Wanted by" column? | Yes, only for tools the session holds | Phase 5, basis for phase 7 |
| F-5 | Type detail? | Yes, with price history | Phase 5 |

Implementation rules for these:

- **F-1** Phase 3b, after the location tree. Slot flags grouped as High /
  Mid / Low / Rig / Subsystem / Drone bay / Fighter bay / Cargo / other
  holds. EFT export built in the backend from SDE names (same reason as
  PI templates: one serializer, no client-side formatting). Doctrine match
  reuses the pure scorer `doctrine/validation.match_score` on fittings read
  through storage; the route additionally requires the `doctrine` grant
  checked against `request.state.tool_keys` (same pattern as the Doctrine
  skill check). Importing the pure validation module is acceptable
  (Sorting already imports Doctrine's engine); importing anything that
  writes Doctrine state is not.
- **F-2** Rows from the `industry_jobs` snapshot with status active/ready:
  product type, runs x product quantity per run (SDE), location =
  `output_location_id` resolved like assets; invention jobs count expected
  output only if Production already has a rule for it, otherwise show runs
  without quantity. Same numbers as Production's incoming figure
  (`esi_incoming_industry_qty`) - reuse it, do not recompute. Default
  toggle on, badge "in production", excluded from the "stock only" filter.
- **F-3** New per-tenant table `asset_manager_views (id, tenant_id, name,
  query JSONB, created_at, updated_at)`, unique `(tenant_id, name)`, RLS
  like any tenant table. The asset manager gets its own schema file
  `docs/asset_manager_schema.sql` (also used by phase 6's history table) -
  a new schema file must be added to every place that applies schema
  files: `deploy/deploy.sh`, `deploy/README.md`, root `README.md`,
  `.cursor/start.sh`, `tests/pg_helpers.py`'s order (grep for
  `production_buy_list_schema` to find them all). `query` is validated
  against the `/items` parameter model on save and on load (a stored view
  from an older version must not 500).
- **F-4** `wanted_by` reads demand through storage only: Production stock
  targets (with `production`), Doctrine stockpile targets (with
  `doctrine`), Sorting pots (with `sorting`). Sorting's
  `engine.do_sorting_list` already aggregates the three; prefer extracting
  its demand part into a shared read function over a third copy. Only
  computed for the types on the current page (not for totals), so it does
  not slow down paging.
- **F-5** Drawer opened from a type cell: all owners/locations/sources for
  that type, value, and a 30/90-day price chart from the existing
  `price_history` path for the tenant's home region (Goonmetrics where
  covered, ESI daily history otherwise - CLAUDE.md market hubs section).
  Read-only; uses the dataviz conventions of the existing charts.

Phase plan after these decisions: 0 fix search -> 1 scaffolding -> 2 list
(incl. D13, F-2, R-1..R-5, R-10, R-12) -> 3 location tree -> 3b ship view
(F-1) -> 4 names -> 5 search 2.0, export, saved views, wanted-by, type
detail, old tab removed (F-3, F-4, F-5, D12, R-13) -> 6 history -> 7
insights.

## 14. Implementation notes (2026-10-25)

Built without server access, against unit tests (Postgres test database)
and a live run on the dev database with seeded assets. What differs from the
plan text above, and why:

- **D8 / R-2 paging in Python, not SQL.** `collect.load_base_rows` loads the
  owners' rows once (SQL filtered by owner), prices them with
  `portfolio.price_items` and caches them per tenant for 120 s;
  `query.run` filters, groups, sorts and pages them. Totals and "sort by
  value" are correct by construction, so the `VALUES` CTE of R-2 and the new
  `(tenant_id, resolved_location_id)` indexes of section 4 were not needed.
  S-01/S-07 decide whether this is fast enough for the largest owner; if
  not, the cache key already isolates the place to add SQL pushdown.
- **R-3** became `portfolio.price_items` (the pricing loop of
  `_collect_wealth`, unchanged behaviour); the loaders (`load_sell_orders_grouped`,
  `realized.db.outstanding_contract_lines(with_location=True)`) are reused
  directly instead of a new shared module.
- **Section 4 schema.** Only `item_name` was added to the asset tables; the
  PL/pgSQL blocks in `esi_access_schema.sql` are a one-off legacy copy of the
  retired Doctrine asset tables and need no new column. `location_type`
  (R-10) was not added: ids are classified by range in `labels.location_kind`
  and S-03 decides whether the stored type is still needed.
- **D11** uses a new `orchestrator.do_sync_kinds(kinds, tool_keys=...)`, so
  the button refreshes the same union the page reads.
- **F-2** covers Manufacturing and Reaction jobs only (runs x SDE product
  quantity); invention and copying produce blueprints, not items.
- **F-4** reads configured demand through storage (stock targets, the last
  buy list, active doctrine stockpile sets, the active Trading shortlist,
  mineral targets) instead of extracting Sorting's shortfall code: the
  question here is "does any tool want this type", not "how much is
  missing", and it stays cheap enough to run per page.
- **Phase 6** stores ESI assets only (owner, location, type, quantity per
  day); manual, listed, contracted and in-production rows are not history.
  Retention is `TradingConfig.asset_history_retention_days` (default 90),
  not yet on a Settings page.
- **Phase 7** "used locations" are the Production home structure and the
  Trading structure; the Asset Safety list has no timer (ESI has no date).
- **Live check findings fixed:** a corp Office row is no longer listed as an
  item; CSV columns depend on the grouping; the freshness panel shows owner
  names. Goonmetrics is unreachable from the dev container, so the live run
  used manual prices; the price chart showed its "no history" state.

Second review (2026-10-25), fixed:

- The sync button called the contracts fetcher without Doctrine's
  `structure_id`, so it would have written every item exchange anywhere into
  Doctrine's contract snapshot and fetched all their items. It now passes
  the same `extra` as the scheduler (`do_sync_kinds(..., extra=...)`).
- The daily history snapshot priced every row (Goonmetrics and the BPC
  invention path) only to count items; `collect.history_rows` now reads
  quantities only.
- The history due check ran `SELECT DISTINCT snapshot_date` per tenant on
  every 5-minute tick; it is a `MAX()` now.
- The row cache never evicted superseded or expired keys (a new share or
  grant made a new key); it keeps one entry per tenant. "Wanted by" (one
  read per doctrine fitting) shares the same TTL instead of running per
  page request.
- The CLI promised in section 5 was missing: `eve-trader assets list`,
  `export-csv`, `snapshot`.
- Tests used SDE ids other tests also seed (Jita, Tritanium) - possible
  xdist-order flakes; they use their own ids now. The new CLI test stubs
  `cli.main()`'s process-wide tenant/config setup so it cannot leak.
- UI: the search box follows the URL on back/forward and saved views; the
  Value total says that listed, contracted and in-production items count
  while their source is selected.

Known limits, not changed:

- The value total includes in-production and listed/contracted items while
  those sources are ticked (default on). Portfolio counts listed and
  contracted items but not in-production ones.
- History compares owner sets as they are each day: a newly shared
  character shows everything it holds as "arrived" on its first day.
- With a text filter the location tree only shows matching rows; a matching
  item whose container does not match hangs directly under its hangar.

Still open, in order:

1. Server checks S-01..S-13 (section 12), then tune caching/paging.
2. Phase 4: `/assets/names` and the `corp_divisions` kind, after S-09/S-10.
   `hangar_label` already takes division names; `collect.load_base_rows`
   has the `division_names` parameter waiting for them.
3. Deploy, run `eve-trader admin grant-defaults`, then remove the Production
   Asset Search tab (D12, R-13).
