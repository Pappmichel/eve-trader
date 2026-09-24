# Manual tracking for Production – implementation plan

Status: phase 0, 2, 3, 4, 5, and 6 done · 2026-09-24 (phase 1 landed
separately, see PR #196)

Goal: make the Production tool fully usable without an ESI login. Manual data
(stock, blueprints, running jobs, listed quantities, locations) takes effect
everywhere ESI data does today – **additively**, exactly like the existing
`manual_stock`. No mode switch and no separate page: input lives on the
existing Stock Targets, Blueprints and Industry Jobs pages.

---

## 0. Verification before building (no code)

Checked 2026-09-24 against [evepraisal/evepaste](https://github.com/evepraisal/evepaste) `master` (`17df80ef`, library version 0.9). That is the same reference `eve_trader/paste_parser.py` used for the inventory parser (issue #92). Parser modules on that tree: `assets`, `cargo_scan`, `chat`, `contract`, `dscan`, `eft`, `fitting`, `industry`, `killmail`, `listing`, `loot_history`, `pi`, `survey_scanner`, `view_contents`, `wallet`. There is no blueprint parser and no market-order parser.

| Check | Result |
|---|---|
| EVE Blueprints window copy format (ME/TE/Runs) | **Negative.** No usable format. Blueprint paste is dropped; the form is the only input (decision 5). |
| Market window "My Orders" copy format | **Negative.** No usable format. Only the manual listed-quantity fields are built (decision 7). |

**Blueprints.** `assets.py` is the inventory list: Name, Quantity, Group, Category, Size, Slot, Volume, Meta Level, Tech Level. A blueprint copied from inventory is a named stack in that list. Those columns do not carry ME, TE, or runs, so those lines stay skipped in the asset paste (decision 10). `industry.py` parses an industry bill of materials (`Name (N Units)`); its test fixture is minerals, not the Blueprints window. `contract.py` is name, quantity, type, category, and a details string (fitted or not). Forum posts from 2012–2013 describe a Science & Industry list copy that included ML/PL/Runs. That window predates the current industry UI, and evepaste never implemented it. It is not a reliable format.

**My Orders.** `wallet.py` parses the wallet journal and completed transactions, not open orders. `listing.py` parses human item lists (`10x Name`), not the market window. The market window's export writes a file under the client's Marketlogs directory. That is not a clipboard grammar in the reference library, and this plan does not take a file-export parser in its place.

Section 12 item 10 is cancelled.

---

## 1. Schema

All per-tenant tables go into the existing `docs/phase1_schema.sql`; the global
table goes into `docs/admin_schema.sql`. Both files are already in the loops in
`deploy/deploy.sh`, `README.md`, `deploy/README.md` and `.cursor/start.sh` –
**no new schema file**, so nothing needs registering in multiple places.

**Grants:** the blanket grant at `phase1_schema.sql:740`
(`ON ALL TABLES IN SCHEMA public`) does **not** cover sequences. New
`BIGSERIAL` tables also need
`GRANT USAGE, SELECT ON SEQUENCE … TO eve_trader_app` (precedent:
`sorting_schema.sql:54`). Every new table also gets its own explicit grant, to
be safe.

### 1.1 Rebuild `manual_stock` (phase1)

Done in phase 3 (of this plan - "phase1" in the heading refers to
`docs/phase1_schema.sql`, the multi-tenant migration file this table's
`CREATE TABLE` already lives in, not this plan's own phase numbering).

The `CREATE TABLE` is updated for fresh databases. Existing databases get an
idempotent migration:

```sql
CREATE TABLE IF NOT EXISTS manual_stock (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    location_id BIGINT NOT NULL DEFAULT 0,   -- 0 = "no location"
    count REAL DEFAULT 0,
    PRIMARY KEY (tenant_id, type_id, location_id)
);
ALTER TABLE manual_stock ADD COLUMN IF NOT EXISTS location_id BIGINT NOT NULL DEFAULT 0;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
    WHERE c.conrelid = 'manual_stock'::regclass AND c.contype = 'p' AND a.attname = 'location_id'
  ) THEN
    ALTER TABLE manual_stock DROP CONSTRAINT manual_stock_pkey;
    ALTER TABLE manual_stock ADD PRIMARY KEY (tenant_id, type_id, location_id);
  END IF;
END $$;
```

Existing rows end up at `location_id = 0`. RLS and the policy are unchanged.

### 1.2 New per-tenant tables (phase1, each with RLS and a `tenant_isolation` policy following the existing pattern)

```sql
CREATE TABLE IF NOT EXISTS manual_owned_blueprints (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    blueprint_type_id INTEGER NOT NULL,          -- blueprint type, not product (the engine's key)
    is_original BOOLEAN NOT NULL,
    material_efficiency INTEGER NOT NULL CHECK (material_efficiency BETWEEN 0 AND 10),
    time_efficiency INTEGER NOT NULL CHECK (time_efficiency BETWEEN 0 AND 20),
    runs INTEGER CHECK (runs IS NULL OR runs > 0),   -- NULL for a BPO
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    location_id BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((is_original AND runs IS NULL) OR (NOT is_original AND runs IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS manual_industry_jobs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    product_type_id INTEGER NOT NULL,
    activity_id INTEGER NOT NULL CHECK (activity_id IN (1, 11)),  -- derived from the SDE
    quantity DOUBLE PRECISION NOT NULL CHECK (quantity > 0),      -- the value used in calculations
    runs INTEGER CHECK (runs IS NULL OR runs > 0),                -- display only, if entered as runs
    location_id BIGINT NOT NULL DEFAULT 0,                        -- output location, target for "Complete"
    ready_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS manual_listed_stock (
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    type_id INTEGER NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('home', 'jita')),
    quantity DOUBLE PRECISION NOT NULL CHECK (quantity >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, type_id, market)
);

CREATE TABLE IF NOT EXISTS manual_location_names (   -- decision 8: visible to the own tenant only
    tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', false)::uuid,
    location_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (tenant_id, location_id)
);
```

Indexes: `manual_owned_blueprints (tenant_id, blueprint_type_id)` and
`manual_industry_jobs (tenant_id, product_type_id)`.

### 1.3 Global table (admin_schema.sql, **no** RLS)

```sql
CREATE TABLE IF NOT EXISTS global_structure_names (
    location_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,                 -- successful resolutions only
    solar_system_id INTEGER,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON global_structure_names TO eve_trader_app;
```

Accessed only through `storage.connect_unscoped()`. This is a deliberate new
exception; CLAUDE.md's list of exceptions gets updated accordingly.

---

## 2. Shared paste parser module

Done in phase 1.

- `eve_trader/refining/paste_parser.py` now lives at `eve_trader/paste_parser.py`.
  Imports updated in `refining/actions.py`, `refining/reprocessing.py`,
  `tests/test_refining_paste_parser.py` and `tests/test_refining_reprocessing.py`.
  SYNC.md names the new path.
- The logic stays the same. `ParsedPasteLine.category` already carries the
  category, which is how `Blueprint` lines are detected (decision 10).
- Phase 0 was negative, so this module does not gain `parse_blueprint_paste()`
  or `parse_market_orders_paste()`.

---

## 3. Storage (`eve_trader/storage.py`)

### 3.1 Manual stock

The first five rows are done in phase 3, `apply_manual_stock_paste` in
phase 4.

| Function | Behaviour |
|---|---|
| `load_manual_stock() -> dict[int, float]` | **Signature unchanged**, now `SUM(count) GROUP BY type_id` across all locations (decision 16) |
| `load_manual_stock_entries() -> list[tuple]` | `(type_id, type_name, location_id, count)`, via a JOIN on `sde_types` |
| `upsert_manual_stock(type_id, count, location_id=0)` | `ON CONFLICT(tenant_id, type_id, location_id)` |
| `delete_manual_stock(type_id, location_id)` | |
| `manual_stock_at_location(type_id, location_id) -> float` | for the engine |
| `apply_manual_stock_paste(location_id, rows, mode)` | in **one** transaction: `replace` first deletes every row *at this location* (decision 11); `merge` adds via `count = manual_stock.count + excluded.count` |

### 3.2 Manual blueprints

Done in phase 5.

- CRUD: `load_manual_owned_blueprints()`, `insert_…`, `update_…(id, …)`, `delete_…(id)`
- `manual_bpo_best_me_te(bp_type_id) -> Optional[(me, te)]`
- `manual_bpc_runs(bp_type_id, location_id | None) -> float` – `SUM(runs * quantity)`
- `manual_has_bpo_at_location(bp_type_id, location_id) -> bool`

### 3.3 Manual jobs

Done in phase 6.

- CRUD
- `manual_incoming_qty(product_type_id) -> float` – `SUM(quantity)`
- `complete_manual_job(job_id, location_id)` – delete the job and add its
  quantity to `manual_stock` via upsert, **in one transaction**

### 3.4 Listed quantities
- `upsert_manual_listed_stock(type_id, market, qty)`, `delete_…`,
  `load_manual_listed_stock() -> {(type_id, market): (qty, updated_at)}`

### 3.5 Locations

Done in phase 2.

- `manual_location_names`: CRUD
- `get_global_structure_names(ids)` and `upsert_global_structure_name(location_id, name, system_id)`
  (unscoped)
- `get_location_names()`: extend the lookup chain to `structure_names` →
  `global_structure_names` → `manual_location_names` → `sde_stations`
- **new** `search_locations(query, limit)`: NPC stations (`sde_stations`), the
  tenant's own `structure_names` and its own manual names. The global cache is
  **not** searched, so it can't be listed.

### 3.6 Batch name resolution (decision 18)

Done in phase 1.

- `resolve_type_names_exact(names) -> dict[str_lower, (type_id, name, category_id)]` –
  one query on `lower(type_name) = ANY(?)`, published types only.
- Names that aren't found each get a suggestion through `suggest_type_names`,
  which calls the existing `search_sde_types(name, limit=1)` once per name.

### 3.7 Fix 19

Done in phase 1. `esi_incoming_industry_qty()` takes
`owner_character_ids`/`owner_corporation_ids` (filtered through the existing
`_owner_id_clause`). `_current_stock` calls `_esi_incoming_industry_qty`, which
passes `shared_production_owner_ids("industry_jobs")`. `manual_incoming_qty`
is still phase 6.

---

## 4. Engine (`production/engine.py`)

| Location | Change |
|---|---|
| `_current_stock` (1465) | replace the direct call to `storage.esi_incoming_industry_qty` with a new wrapper `_esi_incoming_industry_qty` that uses `shared_production_owner_ids("industry_jobs")` (**fix 19**, done in phase 1); additionally `+ storage.manual_incoming_qty(type_id)`, **not** multiplied by the product quantity (decision 2, done in phase 6) |
| `_stock_on_hand` (1508) | unchanged – manual jobs are not physical stock |
| `_stock_at_location` (1429) | done in phase 3. when `location_id` is set, add `+ storage.manual_stock_at_location(...)`; when `location_id is None`, add **nothing**, because `_current_stock`/`_stock_on_hand` already include manual stock through the dict (otherwise it would be double-counted; record this in a comment). This lets Logistics and Invention see manual stock (decision 6) |
| `_owned_bpo_best_me_te` (1447) | done, phase 5. ME and TE each as the maximum of ESI and manual |
| `_available_blueprint_copies` (1453) | done, phase 5. `+ storage.manual_bpc_runs(type_id, location_id)` |
| `_has_bpo_at_location` (1459) | done, phase 5. `or storage.manual_has_bpo_at_location(...)` |
| Home/Jita listing (1579/1587, 2625/2628) | add the manual value (home/jita) at both places (decision 7) |

Performance: `_stock_at_location` runs per material and location in Logistics.
The extra query costs about 3 ms. Measure with real data; only if it's
noticeable, preload manual stock once per call.

---

## 5. Actions (`production/actions.py`, all raise `ActionError`)

### Stock

The first four bullets are done in phase 3, asset paste (the rest of this
section) in phase 4.

- `do_list_manual_stock_entries()`
- `do_set_manual_stock(type_id, count, location_id=0)` – existing action,
  extended with `location_id`; the `count >= 0` check stays
- `do_add_manual_stock_entry(item_name, count, location_id)` – resolve the name via the SDE
- `do_remove_manual_stock_entry(type_id, location_id)`
- `do_preview_asset_paste(text, location_id, mode)` → `{rows:[{type_id, name, old, new, status: new|changed|unchanged|removed}], skipped_blueprints:[…], unresolved:[{line, suggestion}], errors:[…]}` (`removed` only exists in `replace` mode)
- `do_commit_asset_paste(text, location_id, mode)` – **re-parses the text on the
  server** instead of taking rows from the client, so the frontend can't
  alter the result
- **No** cache invalidation (decision 4)

### Blueprints

Done in phase 5.

- `do_list_owned_blueprints()` – adds the manual rows; `OwnedBlueprintRow` gets
  `source: "esi" | "manual"`, `manual_id` and `location_id`
- `do_add_manual_owned_blueprint(item_name, is_original, me, te, runs, quantity, location_id)`
  – accepts the blueprint name **or** the product name (the latter is mapped
  via `get_blueprint_for_product`); ME must be 0–10, TE 0–20 in steps of two;
  a BPC needs runs > 0, a BPO has no runs
- `do_update_manual_owned_blueprint(id, …)`, `do_remove_manual_owned_blueprint(id)`
- **After every change:** `invalidate_discover_cache()` and
  `invalidate_ship_margin_cache()` (decision 4)

### Jobs

Done in phase 6.

- `do_add_manual_industry_job(item_name, quantity=None, runs=None, location_id=0, ready_at=None)`
  – exactly one of `quantity` or `runs`; activity and quantity per run come from
  `get_blueprint_for_product`; rejected if there's no blueprint (decision 13)
- `do_update_…`, `do_remove_…`
- `do_complete_manual_industry_job(id, location_id=None)` – defaults to the
  job's output location (decision 12)
- `production/jobs.py list_current_jobs()` appends the manual jobs.
  `IndustryJobRow` gets `source` and `manual_id`; `status` is `ready` once
  `ready_at` has passed, otherwise `active`. The slot overview is untouched.

### Listed
- `do_set_manual_listed_stock(type_id, market, quantity)`, `do_clear_manual_listed_stock(type_id, market)`

### Locations

Done in phase 2 (the admin job's own use of `resolve_structure_ids` is still
phase 8).

- `do_search_locations(query)`
- `do_set_manual_location_name(location_id, name)`, `do_remove_manual_location_name(location_id)`
- `do_resolve_structure_name()` – new chain:
  1. own cache
  2. **global cache** (hits are copied into the own cache)
  3. own characters
  4. **operator fallback**, only when the switch in section 6 is on: use the
     Default Tenant's characters inside `with enter_tenant(DEFAULT_TENANT_ID)`.
     The token never leaves the server; only `{name, solar_system_id}` is
     returned.

  Every successful resolution also writes `upsert_global_structure_name` –
  **including a regular tenant resolving with its own characters** (question 2).
  `_discover_structure_names` also writes to the global cache during the ESI
  sync. `force=True` updates the global entry too (decision 6d).
- The two-tier resolution logic (corp structure list first, then per-character
  docking) is extracted into one function, `esi_sync.resolve_structure_ids(...)`,
  shared by `_discover_structure_names`, `do_resolve_structure_name` and the
  admin job.

---

## 6. Admin (`eve_trader/admin.py`, `api/routers/admin.py`)

- `do_start_structure_name_resolve(force: bool)` starts a background job with
  progress reporting through
  `pipeline_runner.start_job(TOOL_ADMIN, JOB_STRUCTURE_RESOLVE, …)`.
- **Candidates:** every `location_id >= STRUCTURE_ID_MIN` from the admin
  tenant's own data – `character_assets`, `corp_assets`,
  `character_blueprints`, `corp_blueprints`, the jobs' `output_location_id`,
  the category locations, the config locations, and the locations from manual
  stock and manual blueprints.
- `force=False` resolves only what isn't in the global cache yet;
  `force=True` re-resolves everything.
- Resolution uses the admin tenant's characters (the
  `structure_name_resolution` capability). The result goes into the own cache
  **and** the global cache. The click counts as consent (decision 6b).
- **Small required change:** `pipeline_runner.job_status(tool)` gets an
  optional `job_name` parameter. Otherwise the SDE preview and structure
  resolution share the same admin status slot and each page would show the
  other's run. `storage.get_latest_pipeline_run` can already filter by
  `job_name` + `tool`.
- Endpoints: `POST /api/admin/structures/resolve` (`{force}`) and
  `GET /api/admin/structures/resolve/status`.
- **"Operator fallback for structure names" switch** (question 1, done in
  phase 2 - everything else on this page is still phase 8): a Default
  Tenant settings field, e.g.
  `ProductionConfig.global_structure_resolution_fallback: bool = False`,
  read/written via `admin.do_get/set_structure_resolution_fallback`, read
  inside `enter_tenant(DEFAULT_TENANT_ID)`, so it always holds the Default
  Tenant's value. Other tenants can neither see nor set it. The bulk
  resolution via the admin click does not depend on this switch, and its own
  UI section (with the fallback switch's own toggle/warning text) is bundled
  into phase 8, not built yet.
  Endpoints: `GET/PUT /api/admin/structures/fallback` (done).

---

## 7. API (`api/routers/production.py`, everything through `_wrap`)

| Method | Path | Action |
|---|---|---|
| GET | `/manual-stock` | stays as is (totals per type, backwards compatible) (done, phase 3) |
| POST | `/manual-stock` | `do_set_manual_stock` (new: optional `location_id`) (done, phase 3) |
| GET | `/manual-stock/entries` | `do_list_manual_stock_entries` (done, phase 3) |
| POST | `/manual-stock/entries` | `do_add_manual_stock_entry` (done, phase 3) |
| DELETE | `/manual-stock/entries/{type_id}/{location_id}` | `do_remove_manual_stock_entry` (done, phase 3) |
| POST | `/manual-stock/paste/preview` | `do_preview_asset_paste` (done, phase 4) |
| POST | `/manual-stock/paste/commit` | `do_commit_asset_paste` (done, phase 4) |
| POST | `/manual-blueprints` · PATCH/DELETE `/manual-blueprints/{id}` | blueprint CRUD (done, phase 5) |
| POST | `/manual-jobs` · PATCH/DELETE `/manual-jobs/{id}` | job CRUD (done, phase 6) |
| POST | `/manual-jobs/{id}/complete` | `do_complete_manual_industry_job` (done, phase 6) |
| GET/POST | `/manual-listed-stock` · DELETE `/manual-listed-stock/{type_id}/{market}` | listed quantities |
| GET | `/locations/search?q=` | `do_search_locations` (done, phase 2) |
| POST `/locations/manual-names` · DELETE `/locations/manual-names/{location_id}` | manual names (done, phase 2) |

Size limit on the paste text in the request model (e.g. 500 KB or 10,000
lines), so a huge paste can't block the server.
Everything lives under `/api/production/…` and is therefore automatically
covered by the existing `production` grant.

---

## 8. CLI (`cli.py`, decision 17)

New group `eve-trader production manual …`. Every command takes
`--tenant-id` (default: the Default Tenant) and runs inside
`with tenant_scope.enter_tenant(tid):`, **not** a bare `set_current_tenant`.

- `stock list|add|remove`, `stock import FILE|- --location ID --mode replace|merge [--dry-run]`
  (`--dry-run` runs the preview)
- `blueprints list|add|remove`
- `jobs list|add|complete|remove`
- `listed set|clear`
- `locations name ID NAME` / `locations unname ID`

---

## 9. Frontend

| File | Change |
|---|---|
| `api/client.ts`, `api/types.ts` | new endpoints and types; `source`/`manual_id` on blueprint and job rows (locations endpoints/types done, phase 2; manual-stock-entries endpoints/types done, phase 3 - the blueprint/job `source`/`manual_id` fields are still phase 5/6) |
| **new** `components/LocationPicker.tsx` (done, phase 2) | search across NPC stations, own structures and own manual names; direct entry of a structure ID with "Resolve" (if it stays unresolved: "Give it your own name"); a "No location" option. Wired into StockTargets.tsx's own Manual stock add form as of phase 3. |
| `pages/production/StockTargets.tsx` | new **"Manual stock"** section (done, phase 3): table (item, location, quantity, edit/delete), add form; the existing column shows the total and is only directly editable with at most one entry (done, phase 3). Paste panel (done, phase 4): location, text area, replace/merge mode, preview as a diff marking skipped blueprints and "Did you mean…?", apply - `autosize` dropped from the paste `Textarea` (a jsdom/Mantine incompatibility broke StockTargets.ui.test.tsx, not worth chasing for a cosmetic auto-grow). Still phase 7: new "Listed Home/Jita (manual)" columns with "as of". **corrected delete-dialog text** (decision 20, done in phase 1) |
| `pages/production/Blueprints.tsx` (done, phase 5) | new **"Manual blueprints"** section (form with the hint texts from decision 14); source badge in the owned table; edit/delete only for manual rows |
| `pages/production/Jobs.tsx` (done, phase 6) | form (item, runs/units toggle, quantity, location, ready at); source badge; "done" marker; "Complete" button with a confirmable target location; row keys from `source:id` |
| Logistics page | names through the extended lookup chain (global cache and manual names) |
| `pages/admin/AdminPage.tsx` | "Resolve structure names" section with "Resolve new" and "Re-resolve all" buttons plus progress; also the "Operator fallback" switch with a warning text |

---

## 10. Tests

- **Schema/PG:** update `test_pg_composite_pk_tables.py` (manual_stock with a
  3-column key), `test_sqlite_migration.py`, and
  `sqlite_migration.py:66` → `("tenant_id", "type_id", "location_id")`
  (decision 3). New: RLS isolation for every new table, an idempotency test
  (apply the migration twice), and a test that `global_structure_names` is
  readable without a tenant set.
- **Storage:** totals in `load_manual_stock`; replace and merge per location,
  other locations untouched; Complete in one transaction; batch name
  resolution.
- **Engine:** manual BPOs affect Tech I ME/TE; manual BPC runs at the
  invention location; manual stock at a location shows up in Logistics;
  **no double counting** at `location_id=None`; manual jobs count in
  `_current_stock` but not in `_stock_on_hand`; listed quantities reduce
  `_total_missing`; **fix 19:** jobs of unshared characters no longer count.
- **Actions:** validation (ME/TE, runs/quantity, unknown names); blueprint
  lines in a paste are skipped; commit re-parses; cache invalidation only for
  blueprints.
- **Router:** module-level monkeypatch per project convention; paste size limit.
- **Admin:** candidate collection, force vs. not force, writes to the global
  cache, `job_status` with the `job_name` filter.
- **Resolution chain:** fallback only with the switch on; tenant B's context
  is restored correctly after the fallback (also on error); no token appears
  in the response; a regular tenant's resolution lands in the global cache; a
  failure does not.
- **CLI:** `--tenant-id` uses `enter_tenant`.
- The existing tests that monkeypatch `load_manual_stock` keep working
  unchanged (the signature stays).

---

## 11. Docs

- **CLAUDE.md:** new "Manual tracking" section (additive, no modes, where
  input lives, `location_id = 0`, engine touch points), plus
  `global_structure_names` in the `connect_unscoped` exception list
- `docs/ESI_ACCESS_PLAN.md`: global structure cache alongside the tenant
  cache. **State explicitly:** every successful resolution by any tenant
  becomes globally visible, so private structure names are deliberately
  shared across tenants (question 2); the operator fallback is opt-in
  (question 1).
- `docs/OPERATOR_SECURITY.md`: note on the fallback switch and what it means
  (operator characters resolve for all tenants)
- `SYNC.md`: new paste parser path and `manual_stock`
- Live verification per CLAUDE.md: endpoints against `localhost:8000`, UI via a
  Playwright script (delete the script and screenshots afterwards)

---

## 12. Order / PR split

| # | Scope | Depends on |
|---|---|---|
| 1 | Done. Groundwork: parser move, batch name resolution, fix 19, delete-dialog text | – |
| 2 | Done. Locations: `manual_location_names`, `global_structure_names`, lookup chain, shared resolution function, `search_locations`, LocationPicker, fallback switch | – |
| 3 | Done. `manual_stock` with locations: schema migration, storage, engine (`_stock_at_location`), SQLite migration, "Manual stock" UI table | 2 |
| 4 | Done. Asset paste: preview/commit, paste panel | 1, 3 |
| 5 | Done. Manual blueprints | 2 |
| 6 | Done. Manual jobs incl. Complete | 3 |
| 7 | Listed quantities | – |
| 8 | Admin bulk resolution | 2 |
| 9 | CLI | 3–7 |
| 10 | Cancelled. Phase 0 was negative: no blueprint paste and no My Orders paste. | – |

---

## Decision log

| # | Decision |
|---|---|
| 1 | Manual blueprints feed all three engine read points (Tech I ME/TE, BPC runs, BPO on site) and the blueprint list |
| 2 | Jobs can be entered as runs or units; stored and calculated as units; count in `_current_stock`, not in `_stock_on_hand` |
| 3 | SQLite migration and its tests follow the new `manual_stock` key |
| 4 | Targeted cache invalidation: only manual blueprints clear the discover and ship-margin caches |
| 5 | Blueprint paste is dropped. The form is the only input. Phase 0 found no reliable ME/TE/Runs clipboard format. |
| 6 | Real EVE `location_id`s only (NPC station or structure ID), no invented locations; global structure cache; operator fallback behind a Default Tenant switch; synchronous; `force` refreshes globally |
| 6b | Admin bulk resolution: the click is the consent; candidates from the admin tenant's own data only; new or all selectable; background job |
| 7 | `manual_listed_stock` for home/Jita with "as of". My Orders paste is dropped. Phase 0 found no reliable clipboard format. |
| 8 | Manual location names per tenant, not global |
| 9 | Separate "Manual stock" table on Stock Targets; target column shows the total |
| 10 | Blueprint lines in the inventory paste are skipped and flagged |
| 11 | Paste needs a target location; "replace" affects only that location |
| 12 | "Complete" action on jobs, books into the job's output location |
| 13 | Job activity derived from the SDE; keys from source + ID |
| 14 | Same location picker for blueprints; ME/TE fields kept with hint texts |
| 15 | `manual_stock` key `(tenant_id, type_id, location_id)`, `location_id = 0` for no location; no note field |
| 16 | `load_manual_stock()` keeps returning totals per type |
| 17 | CLI commands with `--tenant-id` and `enter_tenant` |
| 18 | Batch name resolution with "Did you mean…?" |
| 19 | Fix the missing sharing filter on incoming industry jobs in this project |
| 20 | Stock Targets delete dialog: only the backup/home/Jita targets are deleted. Manual stock and the build/buy override stay. |
| Q2 | Every successful resolution by any tenant is written to the global cache |
