# Asset Manager - plan

Status: **draft, nothing implemented, decisions D1-D12 proposed and not yet
confirmed with the user.** Real-data checks (section 9) are open: this
container has no `config.yaml` and no production database.

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
- G7 The old Production Asset Search keeps working (redirect into the new
  page, prefilled).

Non-goals:

- Moving, repackaging or trashing items. ESI has no endpoint for that.
- Replacing Portfolio Total Wealth. The asset manager shows assets;
  Portfolio stays the place for wealth (incl. orders, contracts, escrow,
  wallets) and its history.
- Changing any demand-side stock reader. The assembled-ships rule and the
  `NON_STOCK_LOCATION_FLAGS` rule stay as they are for planning; the asset
  manager only *shows* those rows, marked.
- A live ESI asset read per page open. The page reads the synced snapshot.

## 3. Decisions (proposed - confirm before phase 1)

- **D1 Own tool, tool key `asset_manager`.** A new tenant-facing tool with
  its own grant, router `/api/asset-manager/`, page `/assets` and its own
  sharing row (added to `consuming_tools` of the `assets` kind in
  `esi_data/registry.py`). Reason: assets are not Production-specific (gap
  2), and a separate opt-in share matches how Portfolio was added. The key
  must **not** be `assets`: that is the data-kind key, and tool keys must
  never collide with data-kind keys (Character Management plan, decision
  8). Alternative: keep it as a Production tab and union all asset shares -
  rejected because it would let a Production grant read assets shared only
  with other tools, which breaks the per-tool sharing model.
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
  location 0 shown as "No location". Toggle, default on.
- **D6 Item names via `/assets/names`, stored on the asset row.** New
  nullable column `item_name` on `character_assets` / `corp_assets`, filled
  by the asset fetcher before `replace_assets` for singleton items whose
  category is Ship or whose group is a container (ESI only names
  singletons; max 1000 ids per call). A name fetch error never fails the
  asset sync (logged, names stay NULL) - same rule as the realized-archive
  hook in `fetchers.py`.
- **D7 Corp division names as a new data kind `corp_divisions`.**
  Corporation-only, scope `esi-corporations.read_divisions.v1`, Director
  role, `TIER_RARE`, consumers `asset_manager` and `sorting`. New scope =
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
- **D10 Reads fail closed through the sharing layer.** Owner filtering uses
  the shared owners of `assets` for `asset_manager` (same mechanism as
  `shared_production_owner_ids`, via `esi_data/access`); revoking the share
  hides the rows immediately.
- **D11 Sync button = `do_sync_for_tool("asset_manager")`** (kinds
  `assets`, `corp_divisions`), with the usual per-owner freshness from
  `esi_freshness`.
- **D12 Production's Asset Search tab** becomes a link/redirect to
  `/assets?type=<id>` once phase 5 ships (only shown if the user holds
  `asset_manager`; otherwise the old tab stays). The old
  `POST /asset-locations` route is removed in the same phase, after a grep
  for other callers.

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
badges (["asset_safety","assembled","fitted","delivery","manual"]),
source ("esi" | "manual")
```

## 5. API (`/api/asset-manager/`, gated by `asset_manager`)

| Method + path | Purpose |
|---|---|
| `GET /items` | Filtered, sorted, paged list. Params: `q` (type name / item name / location substring), `type_ids`, `group_ids`, `category_ids`, `owner` (type:id, repeatable), `region_id`, `system_id`, `location_id`, `container_item_id`, `flags`, `include_manual`, `stock_only`, `group_by` (`none` / `type` / `type_location`), `sort`, `page`, `page_size` (max 500). Returns rows + totals (count, quantity, m3, value, unpriced count) for the whole filter, not the page. |
| `GET /locations` | Tree summary: region -> system -> location with item count, m3, value, owners. |
| `GET /locations/{location_id}/tree` | Hangars/divisions -> containers/ships -> items for one location, with per-node totals. |
| `GET /freshness` | Per owner: last success/attempt/error for `assets` and `corp_divisions`, plus not-shared / reauth-needed owners. |
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
  `_TOOL_PATH_PREFIXES`, registry `consuming_tools` of `assets`, Admin
  auto-tick list, Landing card, sidebar/QuickNav, `App.tsx` route.
- Package `eve_trader/asset_manager/` (`actions.py`, `queries.py`,
  `tree.py`), router `api/routers/asset_manager.py`, empty page.
- `eve-trader admin grant-defaults` note in the release notes (existing
  tenants need the grant; characters need the new share).
- CLAUDE.md: new section + `ALL_TOOL_KEYS` count.
- Tests: gate (401/403), share filter (D10), router monkeypatch target.

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
- Tests: filters, paging totals = sum over filter, badges, manual rows,
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

### Phase 5 - search 2.0, exports, Production hand-over (G6, G7)
- Type picker with multi-select and group/category chips; substring match
  on type names server-side.
- CSV export endpoint; "Copy as EVE list" (`Type Name<TAB>Qty` per line,
  grouped by type, current filter) for multibuy / appraisal paste.
- Row actions: "Add stock target" (opens Production's form, only with the
  `production` grant), "Show in Portfolio".
- Production tab -> redirect (D12); remove `POST /asset-locations`,
  `do_search_item_locations`, `search_item_stock_locations` if no caller
  is left; update tests.

### Phase 6 (optional, decide later) - change history
- Daily snapshot of `(owner, location, type) -> quantity` (new table,
  written after each asset sync, retention e.g. 90 days), diff view "what
  arrived / left since date X". Open question Q3: storage volume.

### Phase 7 (optional, decide later) - insights
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

## 8. Testing and verification

- Unit tests per phase (sections above), `pytest -n auto` green.
- Live check per phase against `localhost:8000` (real response bodies) and
  a Playwright screenshot of the page (throwaway `_verify_*.mjs`, deleted
  afterwards), per CLAUDE.md.
- Performance check: `/items` with the largest real owner, target < 1 s for
  one page and totals.

## 9. Real-data checks (TODO, needs server/DB access)

- [ ] R-01 Asset row count per tenant and per owner (largest owner) -
      confirms paging/aggregation needs (D8).
- [ ] R-02 Distinct `resolved_location_id` count; share without a name in
      any of the four tiers.
- [ ] R-03 Count of singleton ships/containers (size of the `/assets/names`
      calls per sync).
- [ ] R-04 Which shared corps have a Director token (D7 coverage).
- [ ] R-05 Rows with a NULL `resolved_location_id` or a parent missing from
      the same owner's list (orphans for the tree).
- [ ] R-06 Time of the Portfolio price lookup for all owned types today.

## 10. Open questions for the user

- **Q1** Own tool (D1) or stay a Production tab?
- **Q2** Default grant for every tenant (D2)?
- **Q3** Phase 6 (history) and phase 7 (insights) wanted at all, and which
  first?
- **Q4** Should listed items (sell orders) and items in outstanding
  contracts appear as virtual rows, as Portfolio counts them? (Not in v1.)
