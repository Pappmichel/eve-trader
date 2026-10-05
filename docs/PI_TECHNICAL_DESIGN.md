# PI tool - technical design

Status: **implemented 2026-10-05 (deviations and findings: `docs/PI_PLAN.md` section 12).** This is the "how"
for `docs/PI_PLAN.md` (the "what" and "why", all decisions D1-D6, O1-O2,
F1-F7 there). Every hook into existing code below was read in the code on
2026-10-04, not assumed. Pitfalls are marked **[P-nn]** where they come up
and collected in section 13 with their mitigation.

## 0. Ground rules carried over from CLAUDE.md

- `do_*` actions in `eve_trader/pi/actions.py` are the only entry point for
  logic; routers stay thin (`_wrap`, `ActionError`); the CLI calls the same
  actions.
- Pure logic (`engine`, `layout`, `decay`, `system_plan`) does no I/O. It gets
  plain dataclasses and returns plain dataclasses. That keeps it testable
  without Postgres and lets the corpus tests run fast.
- Everything committed is in English; Python 3.10 compatible (CI matrix
  3.10/3.11; no 3.11+ syntax or stdlib such as `tomllib`, `ExceptionGroup`,
  `typing.Self`) **[P-01]**.
- No new dependencies. SciPy (pinned `>=1.11,<1.16`) already exists, and the
  frontend editor is plain SVG on Mantine; no canvas/graph library
  **[P-02]**.

## 1. Module layout

```
eve_trader/pi/
  __init__.py
  constants.py      # hardcoded game facts with source comments (P0 table, CC levels, spacing, route limit, zones)
  config.py         # PiConfig + PI_CONFIG proxy + resolve/reset (refining/config.py shape)
  static.py         # loads SDE PI tables into one immutable StaticData (cached, invalidated on SDE apply)
  model.py          # dataclasses: Commodity, Schematic, StructureSpec, PlanetSpec, Design, Fit, Throughput, Layout...
  decay.py          # CCP extractor formula, program cycle time, noise-free ratio
  engine.py         # fit(), throughput(), best_design(), design cache
  economics.py      # taxes, freight, setup, profit, verdict + reason, effort
  chains.py         # planets per stage, value added per tier
  system_plan.py    # system analysis MILP (delegated to an Opus subagent at implementation time)
  demand.py         # Production demand view (reads production_buy_list via storage)
  layout/
    template_io.py  # EVE template JSON <-> Layout, round-trip safe
    geometry.py     # sphere math, lattice, spacing
    topology.py     # chain -> hub/factory/flow graph
    placement.py    # cell providers: standard hex rings + shapes
    routing.py      # link tree, link levels, ordered routes
    validate.py     # the one validator (generator, analyser, editor)
    generate.py     # pipeline + budget feedback loop
    edit.py         # in-place edits ("refusal builds nothing")
    variants.py     # 5c: ways to build this, partial sourcing, mixed P2, storage suggestion, grow
  colonies.py       # ESI snapshot -> Layout, monitor projection, calibration samples
  actions.py        # do_* functions
eve_trader/api/routers/pi.py
eve_trader/alerts/  # + pi alert types (logic.py pure decisions, runner.py hook)
docs/pi_schema.sql
frontend/src/pages/pi/  # Profitability, Planner, System, Chains, Plans, Templates, LayoutEditor, Colonies, Settings
```

`pi/` must not import `production/`, `doctrine/` or other tool packages
(same rule as `skill_check.py`); cross-tool data goes through `storage`
**[P-03]**. Exception: `production.sde` is extended (the SDE importer
lives there), which is fine because SDE is shared infrastructure.

## 2. Data layer

### 2.1 SDE extension (`production/sde.py`, `storage.replace_sde_data`)

New files fetched:

| File | Size | How |
|---|---|---|
| `planetSchematics.csv` (68 rows), `planetSchematicsTypeMap.csv` (203), `planetSchematicsPinMap.csv` (496) | tiny | add to `_SDE_CSV_FILES` |
| `mapDenormalize.csv` | **83 MB**, ~500k rows | **streamed** like `dgmTypeAttributes.csv`, keep only `groupID == 7` and `typeID` in the 8 PI planet types **[P-04]** |
| `dgmTypeAttributes.csv` | already streamed | extend the kept set (below) |

`mapDenormalize.csv` columns (checked): `itemID, typeID, groupID,
solarSystemID, constellationID, regionID, orbitID, x, y, z, radius,
itemName, security, celestialIndex, orbitIndex`. `radius` is in **metres**
**[P-05]**. Planet `typeID` values found: the 8 PI types (11, 12, 13, 2014,
2015, 2016, 2017, 2063) = **67,693 planets** (matches PI Nexus), plus
Shattered (30889, 713 planets, Pochven-style regions) and Scorched Barren
(73911, 1 planet). invTypes also lists 56018-56024 (duplicate Barren/Ice/...)
with **zero** planets in the map. **Filter by the explicit 8-type whitelist,
never by group or name** **[P-06]**. Radius range is extreme: Barren
121 km to Gas 159,270 km, so a single minimum-spacing link on a large gas
giant costs about 397 tf / 297 MW **[P-07]**.

`dgmTypeAttributes.csv`: today the stream keeps a fixed attribute set for
skills. Add PI attributes, but **only for PI types** for the generic ones
(15 powerLoad, 49 cpuLoad, 11 powerOutput, 48 cpuOutput, 38 capacity), since
those exist on thousands of ships/modules **[P-08]**. The PI type set is
derived **before** the stream from the already-fetched `invTypes.csv` +
`invGroups.csv`: categories **41** (Planetary Industry), **42** (Planetary
Resources), **43** (Planetary Commodities), checked against invGroups on
2026-10-04. PI-only attributes (1631-1645, 1683, 1687, 1690, 1691) can be
kept unconditionally. Order inside `fetch_sde`: CSV loop -> compute PI type
set -> stream attributes with `kept = skill attrs | (pi attrs if type in pi
set)`.

New global tables in `docs/pi_schema.sql` (no `tenant_id`, no RLS, `GRANT
SELECT, INSERT, UPDATE, DELETE ... TO eve_trader_app`, same as
`sde_skill_requirements`):

```sql
CREATE TABLE IF NOT EXISTS sde_pi_schematics (schematic_id INTEGER PRIMARY KEY, name TEXT NOT NULL, cycle_seconds INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sde_pi_schematic_types (schematic_id INTEGER, type_id INTEGER, quantity INTEGER NOT NULL, is_input BOOLEAN NOT NULL, PRIMARY KEY (schematic_id, type_id));
CREATE TABLE IF NOT EXISTS sde_pi_schematic_pins (schematic_id INTEGER, pin_type_id INTEGER, PRIMARY KEY (schematic_id, pin_type_id));
CREATE TABLE IF NOT EXISTS sde_pi_type_attributes (type_id INTEGER, attribute_id INTEGER, value DOUBLE PRECISION NOT NULL, PRIMARY KEY (type_id, attribute_id));
CREATE TABLE IF NOT EXISTS sde_pi_planets (planet_id INTEGER PRIMARY KEY, planet_name TEXT, solar_system_id INTEGER NOT NULL, type_id INTEGER NOT NULL, radius_km DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS sde_pi_planets_system ON sde_pi_planets (solar_system_id);
```

- **One generic attribute table** instead of a wide structure table: the
  engine maps attributes to meaning in `static.py`. Adding a later attribute
  is then a data change, not a schema change.
- `DOUBLE PRECISION`, not `REAL`, for new numeric columns. Existing
  `sde_types.volume` is `REAL` (float4), so 0.19 comes back as
  0.1899999976 **[P-09]**. `static.py` rounds volumes to 4 decimals on load.
  Commodity volume, tier and **structure ISK cost** need data `sde_types`
  doesn't hold (`basePrice` is not stored today) **[P-10]**. Store
  `basePrice` for PI types as pseudo-attribute `-1` in
  `sde_pi_type_attributes`. That needs no change to `sde_types` and no 9-
  to 10-column insert rewrite.
- `replace_sde_data` gets five new keyword arguments (default `()`), DELETE +
  INSERT inside the existing single transaction, and new `cache_clear()`
  calls plus `pi.static.invalidate()` **[P-11]**.
- **SDE preview diff**: `SDE_TABLES` drives both `sde_row_counts()` and the
  row-by-row diff (`_SDE_DIFF_FULL_TABLES = SDE_TABLES`). Adding
  `sde_pi_planets` there would make the first preview after deployment list
  67,693 "new" rows in the Admin UI and the job result JSON **[P-12]**. Add
  the four small PI tables to the row diff; give `sde_pi_planets` a
  **count-only** entry (`new_count/removed_count/changed_count`, sample of
  20). The staged `FetchedSde` holds ~68k small tuples in memory between
  preview and apply (a few MB). Acceptable, but note it in the docstring.
- **Tier is derived, not hardcoded**: P0 = types that are schematic inputs
  and never outputs; tier(x) = 1 + max(tier(inputs)). The max matters: P4
  recipes with a P1 input (Nano-Factory: Reactive Metals) must still come
  out as P4 **[P-13]**. A unit test pins 15/15/24/21/8 per tier.

### 2.2 Per-tenant tables (`docs/pi_schema.sql`)

All with `tenant_id ... DEFAULT current_setting('app.tenant_id', false)::uuid`,
RLS `tenant_isolation`, grants. Copy the shape of an existing table.

```sql
pi_plans          (tenant_id, plan_id BIGSERIAL, name, planet_id NULL, planet_type_id, radius_km, character_id NULL,
                   design JSONB, owner_tax_rate, freight_per_m3 NULL, yield_override NULL, created_at, updated_at,
                   PRIMARY KEY (tenant_id, plan_id))
pi_templates      (tenant_id, template_id BIGSERIAL, name, comment, planet_type_id, cc_level, diameter_km,
                   template JSONB, source TEXT CHECK (source IN ('paste','generated','esi')), created_at,
                   PRIMARY KEY (tenant_id, template_id))
pi_yield_samples  (tenant_id, character_id, planet_id, pin_id, install_time, p0_type_id, planet_type_id, security,
                   heads, program_hours, per_head_per_hour, sampled_at, PRIMARY KEY (tenant_id, character_id, pin_id, install_time))
character_pi_colonies (tenant_id, owner_character_id, planet_id, planet_type, solar_system_id, upgrade_level,
                   num_pins, last_update, layout JSONB, PRIMARY KEY (tenant_id, owner_character_id, planet_id))
pi_alert_state    (tenant_id, character_id, planet_id, alert_type, last_key, sent_at,
                   PRIMARY KEY (tenant_id, character_id, planet_id, alert_type))
```

- `template` is stored as JSONB **plus** the original text is not kept: the
  export re-serializes. That is safe only if `template_io` round-trips
  byte-for-byte for unchanged templates (Eve-PI's invariant). JSONB
  reorders keys, so serialization must emit keys in EVE's order (`CmdCtrLv,
  Cmt, Diam, L, P, Pln, R`, inner keys sorted as in real exports)
  **[P-14]**.
- `character_pi_colonies.layout` keeps the ESI pins/links/routes as JSONB
  (one row per colony) rather than four normalised tables. Nothing queries
  inside it in SQL; the engine reads it whole.
- **New schema file checklist** (CLAUDE.md "A brand-new schema file", a
  repeated real bug class) **[P-15]**: `deploy/deploy.sh` migration loop,
  `deploy/README.md`, root `README.md`, `.cursor/start.sh`. Grep for
  `production_buy_list_schema` to find all of them.
- **sqlite_migration drift guard**: every new Postgres table must be listed
  in `sqlite_migration.py`'s Postgres-native dict or
  `tests/test_sqlite_migration.py` fails **[P-16]**.
- **Alert CHECK constraints**: `alert_subscriptions.alert_type` and
  `alert_state.alert_type` have `CHECK (alert_type IN ('skillqueue_empty',
  'mail_new'))`, and `CREATE TABLE IF NOT EXISTS` will not change an existing
  table **[P-17]**. `character_management_schema.sql` gets an idempotent
  `ALTER TABLE ... DROP CONSTRAINT IF EXISTS alert_subscriptions_alert_type_check;
  ALTER TABLE ... ADD CONSTRAINT ... CHECK (alert_type IN (... 'pi_extractor_expiry',
  'pi_pad_full', 'pi_inputs_empty'))` (check the real constraint name with
  `\d alert_subscriptions` first). `alert_state`'s PK is (character,
  alert_type), one row per type, but PI alerts are per colony, hence
  `pi_alert_state` **[P-18]**.

## 3. Static data and engine

### 3.1 `static.py`

`StaticData` (frozen): commodities `{type_id: Commodity(name, tier, volume,
tax_base)}`, schematics (inputs, output, qty, cycle), pin map, structures
`{type_id: StructureSpec(kind, planet_type_id, cpu, power, capacity,
isk_cost)}`, link spec, ECU head cost, decay constants, CC levels (from
`constants.py`). Loaded with one query per table, cached module-level
behind a lock, `invalidate()` on SDE apply (CLAUDE.md caching shape 3)
**[P-11]**.

- **Structure kind by group** (1026-1030, 1063, 1036), **not** by name or by
  planet. Real in-game templates mix planet types: a Barren factory
  template contains an **Ice** launchpad (2552), and the game accepts it.
  The analyser must resolve any PI structure id to its kind, whatever the
  template's planet **[P-19]**. The generator still emits the target
  planet's own ids.
- **Tax base** from attribute 1641/1640 per commodity; a commodity without
  it is a data error and is logged, not defaulted **[P-20]**.
- A fresh deployment before the first SDE refresh has empty PI tables. Every
  action must raise a clear `ActionError("PI data missing - run the SDE
  refresh in Admin")` rather than a KeyError/500 **[P-21]**.

### 3.2 Geometry and link cost (`layout/geometry.py`, used by engine)

- Pins are (La = polar angle, Lo). Distance = great-circle central angle
  `acos(cos La1 cos La2 + sin La1 sin La2 cos(Lo1 - Lo2))`, clamped to
  [-1, 1] **[P-22]**. Link km = angle x **radius_km of the target planet**.
- `Diam` in a template only describes the planet it was made on. Analysing a
  stored template **for a given planet** uses that planet's radius. Without a
  planet, it uses `Diam / 2` and says so **[P-23]**.
- Link cost at level L: `cpu = 15 + 0.2 * km * (L+1) ** 1.4`,
  `power = 10 + 0.15 * km * (L+1) ** 1.2`, capacity `1250 * 2 ** L`
  (constants from the Link type's attributes). Eve-PI rounds **each link up**
  (`ceil`) and matched in-game totals to the unit. **Confirmed in game
  2026-10-04 (V-5)**: a 26 km link shows 21 tf / 14 MW (20.2 / 13.9), a
  114 km link 38 tf / 28 MW (37.8 / 27.1; plain rounding would give 27 MW).
  Level-0 capacity shows 1250 m3/h. Golden tests pin both links **[P-24]**.
- Spacing rule: central angle >= 0.012 rad. Check it **after** rounding
  La/Lo to the 5 decimals templates use; rounding can push two pins under
  the limit **[P-25]**.

### 3.3 Throughput (`engine.throughput`)

Two callers with different inputs: designs (counts, regular structure) and
arbitrary layouts (analyser/editor/ESI colonies).

- **Designs**: closed form. Per factory per hour = recipe qty x 3600 /
  cycle_seconds; extraction = heads x yield_per_head x decay ratio
  (program length); utilisation = min(1, supply / demand) per stage.
- **Layouts**: a small LP (`scipy.optimize.linprog`, continuous): variables
  = factory run rates (<= 1 cycle per cycle time), flows on routes; supply
  = extractor output + launchpad imports (unbounded or capped by user
  "haul-in"); maximise exports. Factory inputs come only over that factory's
  routes, each route carries at most `Q` per destination cycle. This catches
  starving factories, unrouted factories and routes for the wrong
  commodity, which closed form cannot **[P-26]**. Route order (EVE drains a
  factory's input routes in creation order) matters for buffers, not steady
  state, so it is checked by the validator as a warning, not modelled in the LP.
- Storage duration = buffer m3 / net m3/h, per hub; with SDE volumes, never
  Eve-PI's doubled ones **[P-27]**.

### 3.4 `best_design`

- Search space per chain: ECUs 0-2 (P0->P2 needs exactly 2 distinct P0, one
  per ECU; an ECU is locked to one P0) **[P-28]**, heads 1-10 per ECU, factories
  per stage, pads 1-4, storage 0-4.
- Link cost estimate before a layout exists = `structures - 1` links x
  (0.012 x radius x span factor). The span factor starts at Eve-PI's measured
  extra spacings. Once the generator exists, `generate()` feeds back the
  **exact** link cost and `best_design` re-solves (6A pipeline step 6). The
  planner shows the estimate, the template the exact figure.
- Objective order: max sellable output -> fewest idle factories -> most
  buffer hours -> fewest structures (stable tie-break, so cached results are
  deterministic) **[P-29]**.
- Dominance pruning: once supply >= demand, more heads never help; once all
  factories are fed, more pads only help buffer hours. That brings a few
  thousand points down to hundreds.
- **Cache**: key = (chain, product, planet_type_id, radius bucket, cc_level,
  yield_per_head, program_hours, interval_hours, sourcing). Radius buckets
  (e.g. 250 km steps up to 10k km, then 2.5k) bound the key space for the
  Profitability table. A real planet in Planner/System analysis bypasses the
  bucket and uses its exact radius. Single lock + explicit invalidation on PI
  settings save and SDE apply **[P-30]**. The key includes everything the
  design depends on and **no prices**.

### 3.5 Decay (`decay.py`)

- `cycle_outputs(qty_per_cycle, cycle_seconds, cycles, noise=True)` =
  CCP's algorithm with integer truncation per cycle, as in CCP's sample.
- **Cycle-time thresholds** **[P-31]**, **settled in game 2026-10-04
  (V-1)**: EVE Uni is right and jwebbdev is wrong. Below 25 h: 15 min;
  from 25 h: 30 min; 50 h: 1 h; 100 h: 2 h; 200 h up to 14 days: 4 h (no
  8 h step). Real data still uses ESI's `cycle_time`; the table is for the
  Planner.
- **Formula verified exactly in game (V-2, 2026-10-04)**: a real Lava
  extractor (Heavy Metals, 4 heads, program 2d 2h = 50 h, 1 h cycles, 50
  cycles) showed cycle 1 = 23,058, cycle 50 = 10,654, total 682,147
  (avg 13,643/h). CCP's algorithm with **integer truncation per cycle** and
  base `q = 5903` reproduces all three numbers exactly, and the peak at
  cycle 10 (~28,000) as well. Golden test: `cycle_outputs(5903, 3600, 50)`
  must give exactly those values. **`qty_per_cycle` semantics [P-32]**: a
  cycle yields about `(cycle_seconds / 900) x q`, i.e. `q` is the base per
  15-minute bar, not per cycle. Phase 4 only needs to confirm that ESI's
  `qty_per_cycle` for that extractor is 5903 (one comparison).
- **Noise only adds yield** **[P-64]**: `max(..., 0)` means the noise term
  is never negative. In the verified example it adds +13% over the
  noise-free curve (13,643/h vs 12,039/h). So the planning **ratio**
  between program lengths may use the noise-free curve, but calibration
  must use the full formula, or it would overstate the base yield by ~10-15%.
- Planning ratio: noise-free average for program length P divided by the
  same for 72 h (the D2 reference). Pure function, memoised. Data point from
  the same extractor: 3,411 P0/head/h at 50 h, about 2,890 at 72 h
  (noise-free ratio 0.848). That sits between the low-sec (2000) and
  null-sec (4000) defaults.

### 3.6 Economics (`economics.py`)

- Taxes per unit: export `tax_base x rate`; import `tax_base x rate x 0.5`.
  Rate = NPC part + owner part (PI_PLAN 3.3). NPC part: high-sec 10% minus
  1% per Customs Code Expertise level; 0 elsewhere.
- **Security zone**: reuse `production.constants._rounded_security`'s rule
  (true sec 0.45-0.4999 counts as 0.5 = high-sec) via a shared helper, not a
  second classifier. `sde_solar_systems.security` is float4, so 0.45 is
  stored as 0.449999988 **[P-33]**. Wormhole = region id 11000001-11000033,
  checked before security, since J-space has security -1.0. Pochven (region
  10000070, 220 PI planets) counts as null-sec. Jove regions are unreachable
  **[P-63]**.
- Prices: `hubs.hub_pricing` with PI's own `freight_per_m3` passed as
  `freight_fallback`. In `ALL_HUBS` mode `hub_pricing` reads the shared
  hub->home freight table internally, which is **wrong for PI** (O1)
  **[P-34]**. Add a `freight_override: Optional[float]` parameter to
  `hub_pricing` that, when set, replaces the per-hub table lookup. That is a
  small, backwards-compatible change; the existing callers don't pass it.
- ESI calls: ~83 PI types (68 products + 15 P0). In `ALL_HUBS` that is
  4 x 83 = 332 `region_orders_raw` calls on a cold cache. They are cached
  class-level already (`_region_order_stats_cache`), but the first
  Profitability load after a restart takes a while. Show a loading state and
  never block the design computation on prices (compute designs first,
  attach prices second) **[P-35]**.
- Verdict reasons are a fixed enum (`tax`, `freight`, `input_cost`,
  `extraction_low`, `market_thin`, `setup`) computed as the largest cost
  share relative to revenue, so the frontend can translate and filter.
- Effort = restarts/week + hauls/week (PI_PLAN 3.3). Price trend from
  `goonmetrics_client.price_history_chunked` for the PI hub region (ESI
  daily history fallback is built in). The 30-day window needs ESI history
  where Goonmetrics returns ~28 days, so take what comes and show the
  actual window length **[P-36]**.

### 3.7 Chains and system analysis

- `chains.py`: integer planet counts per stage from the per-design rates;
  rounding is **up** for feeders (a factory planet needs whole extractor
  planets) and the surplus is shown, not hidden.
- `system_plan.py` (Opus subagent at implementation, per CLAUDE.md):
  `linprog(..., integrality=...)` as in the Mineral Shopping List (stay
  consistent with that existing use, not `milp`). Variables: number of
  colonies per (planet, option); constraints: total colonies <= slots; per
  planet <= number of characters (one CC per character per planet)
  **[P-37]**; flows of intermediates balanced inside the system; repeated
  extraction on one planet = separate options "k-th extractor colony" with
  yield x penalty^(k-1) and ordering constraints (k-th only if (k-1)-th),
  which keeps the program linear. Planets per system are <= ~25, options
  per planet ~10-40, so the problem is small. Still give HiGHS a time limit
  (5 s) and fall back to the greedy best-single-use list with a note
  **[P-38]**.

### 3.8 Production demand (`demand.py`)

- Source: `production_buy_list` (latest plan's material quantities, read via
  `storage` like `sorting/engine` does) intersected with PI commodity types.
  There is **no daily demand figure** in Production, and the in-process
  `_last_plan` cache in the Production router is per process and lost on
  restart **[P-39]**. So the view shows quantity needed (from the last plan
  run), PI cost/unit vs buy price, saving for that quantity, and "colonies
  needed to cover it in N days" with N as a setting. It never invents a
  daily rate.
- The route additionally requires the `production` grant
  (`request.state.tool_keys`), like the Doctrine skill check **[P-40]**.

## 4. Layout generator (6A) - technical notes

- **Coordinates**: build the lattice in a local tangent plane around
  (La = pi/2, Lo = 0), x = longitude offset, y = polar offset; at the equator
  `sin(La) = 1`, so 1 unit = 1 radian both ways. Larger layouts drift off
  the equator; validate with the true great-circle distance, never the flat
  x/y (jwebbdev's mistake) **[P-41]**.
- **Route length by construction**: tree depth = ring index from the
  factory's own hub. A hub->factory route passes ring+1 structures;
  hub-to-hub transfers pass through the centre. Cap factory rings so the
  worst route is <= 7, then still validate (Eve-PI found 8-structure routes
  in its own "standard" layouts) **[P-42]**.
- **Budget feedback loop**: generate -> exact cost -> if over budget,
  `best_design` again with the measured link cost and **one fewer
  structure-unit** as an upper bound. It strictly decreases, so it
  terminates; cap at 10 iterations and fail with a reason otherwise
  **[P-43]**.
- **Large planets**: on gas giants the minimum-spacing link alone costs
  hundreds of tf/MW **[P-07]**. Compact placement (rings, not arms) is the
  default for that reason; the Planner shows the link share of the budget
  so the user sees why a big planet fits fewer factories.
- **Template content rules** (checked against real exports):
  `P[].S` = product type id (factory) or P0 type id (ECU); `H` = head count
  (head positions are not part of a template); no Command Center pin;
  `Pln` = planet **type** id; 1-based indices; La/Lo/Diam floats
  **[P-44]**. Serialization happens **only on the backend**: Python's json
  keeps `3.0`, JavaScript's `JSON.stringify` drops it and the game then
  rejects the paste. The frontend copies the backend's string verbatim
  **[P-45]**.
- **Determinism**: same design -> same template (sorted iteration, no set
  ordering), so golden tests and "Reset to generated" work.
- **Unknown game limits**: max pins per colony and max `Cmt` length are not
  documented; the in-game name field showed no visible length limit (V-4,
  2026-10-04). Keep `Cmt` <= 60 ASCII-safe chars (real exports contain an
  en dash in UTF-8; parse UTF-8, emit ASCII) and record the in-game
  acceptance results in the plan (V-4) **[P-46]**.

## 5. Layout editor (frontend, phase 5b)

- SVG, equirectangular projection around the layout's centre (which stays
  near the equator), zoom/pan, Mantine controls. Lazy-loaded route
  (`React.lazy`) so the main bundle doesn't grow; the test-server build
  already needs `NODE_OPTIONS` heap 2048 **[P-47]**.
- Drag: spacing check locally (pure geometry, same formula as the backend)
  for instant red rings; everything else via debounced (250 ms)
  `POST /api/pi/layouts/validate`. Stale responses are dropped by request
  sequence number **[P-48]**.
- Undo/redo: client-side stack of layout snapshots; a server-side edit
  (`/layouts/edit`) returns the full new layout, never a patch, so client
  and server can't drift.
- State in the URL/plan: unsaved edits warn on navigation (the app already
  uses this pattern for Settings).

## 6. ESI colonies (phase 4)

- Registry: `OwnedDataKind(key="planets", label="Planetary Industry",
  group=GROUP_2, character_scope="esi-planets.manage_planets.v1",
  corporation_scope=None, corp_roles=(), consuming_tools=("pi",
  "char_alerts"), freshness_tier=TIER_NORMAL, schedule_mode=ON_DEMAND)`.
- **Every place a snapshot kind touches** (a real repeated sync risk)
  **[P-49]**: `esi_data/registry.py`; `esi_data/fetchers.py` (`FETCHERS`);
  `esi_data/access.py` (`read_esi` dispatch + reader); `esi_data/stale.py`
  (`_KIND_TABLES`); `storage.py` per-owner clear allowlist (**two** lists in
  `clear_owner_partition`); `frontend/src/esiRegistry.ts` +
  `toolKeys.ts` `ESI_CONSUMING_TOOLS` (+ their drift tests);
  `sqlite_migration.py` list. Write a test that iterates the registry and
  asserts each non-live kind has a fetcher, a stale entry and an access
  reader.
- Fetcher: `GET /characters/{id}/planets` then one
  `/characters/{id}/planets/{planet_id}` per colony (<= 6). One planet
  failing fails the character's batch (consistent snapshot, the
  orchestrator's existing rollback semantics); a 404 for a just-abandoned
  colony is skipped, not an error **[P-50]**.
- Mapping ESI -> Layout: `factory_details.schematic_id` / pin
  `schematic_id` is a **schematic id**, while templates use the **product
  type id**, so convert through `sde_pi_schematic_types` **[P-51]**. Command
  Center pin is dropped for templates. ESI `latitude`/`longitude` convention
  vs template `La`/`Lo` must be checked against one in-game export of the
  same colony before shipping colony->template (V-3) **[P-52]**. Route
  `quantity` is a float in ESI and an int in templates.
- **Freshness**: colony data reflects `last_update` (last in-game
  interaction). Monitor and alerts project from it and say so; the
  projection gets "uncertain" after a configurable age **[P-53]**.
- Calibration samples: per extractor pin with `install_time`/`expiry_time`,
  `qty_per_cycle`, `cycle_time`, head count. Sample key = (character, pin,
  install_time), so re-syncs don't duplicate. Skip programs shorter than 1 h
  and those with 0 heads. Median per (P0, planet type, zone) with a minimum
  sample count (3) before it replaces the D2 default **[P-54]**.

## 7. Alerts (phase 4)

- `alerts/logic.py`: pure `pi_decisions(colony_projection, now, lead_hours,
  last_keys)` returning per-planet decisions. Keys:
  `expiry:{pin_id}:{expiry_iso}`, `padfull:{pin_id}:{bucket}`,
  `inputs:{pin_id}:{bucket}`, so the same situation is announced once.
- `alerts/runner.py`: add `planets` to the `demand` set for characters with
  a PI subscription (same as `skillqueue`); `deliver()` re-checks
  subscription, `char_alerts` sharing of `planets` and the token at send time.
  Messages are English, mentions disabled. "Pad full" / "inputs empty" say
  "estimated from the colony state of {last_update}" **[P-53]**.
- The per-tenant guard (`_try_begin_tenant`) already prevents double DMs
  across overlapping ticks; PI alerts must run inside it, not in a new job
  **[P-55]**.

## 8. API, auth, limits

- Router prefix `/api/pi/`; add `"/api/pi/": "pi"` to `_TOOL_PATH_PREFIXES`;
  `"pi"` to `ALL_TOOL_KEYS`, `DEFAULT_TOOL_KEYS` (automatic via the filter)
  and the frontend mirrors. Existing tenants need `eve-trader admin
  grant-defaults` after deploy, or nobody sees the tool **[P-56]**.
- POST endpoints are subject to the existing same-origin CSRF check. Fine
  for the SPA, but the CLI must not go through HTTP.
- **Input limits** **[P-57]**: template paste <= 256 KB, <= 300 pins, <= 600
  routes, route paths <= 50 entries, numbers finite. Reject before parsing
  deeper. System analysis: slots <= 60. Validation requests are cheap but
  debounced; still rate-limit per session (in-process token bucket) so a
  stuck editor can't hammer the backend.
- Responses never include other tenants' data. All PI per-tenant reads go
  through `storage.connect()` (RLS); `sde_pi_*` are global by design.

## 9. Config

- `PiConfig` in `pi/config.py`, wired like `RefiningConfig`:
  `load_pi_config` (yaml cache + deepcopy), `_pi_config_var`, `PI_CONFIG =
  ConfigProxy(...)`, `resolve_and_set_pi_config`, `reset_pi_config`, scope
  `"pi"` in `tenant_settings`.
- **Add it to `tenant_scope.enter_tenant`** (another nested try/finally,
  following that function's own reasoning) **[P-58]**. Check
  `api/app.py`'s gate-disabled path the same way the other tool configs
  are handled there.
- **Field names must be PI-prefixed** **[P-59]**: `config.yaml` is one flat
  mapping applied to *every* config dataclass (`apply_config_overrides`
  sets any key the dataclass has), and `_FIELD_RANGES` is keyed by field
  name across all dataclasses. A PI field called `broker_fee_rate` or
  `freight_cost_per_m3` would silently take Station Trading's / Module
  Reprocessing's config.yaml value and share their bounds. Use
  `pi_broker_fee_rate`, `pi_sales_tax_rate`, `pi_freight_per_m3`,
  `pi_yield_highsec` ... Exception: `hub_region_id`, kept for consistency
  with the other tools' hub pickers (#222); the shared yaml behaviour is
  already accepted there.
- Every numeric field gets a `_FIELD_RANGES` entry (rates 0-1, levels 0-5,
  yields >= 0, days >= 1). `ALL_HUBS` (0) must stay valid for
  `hub_region_id`, as in the other tools.

## 10. Performance budget

| Operation | Target | How |
|---|---|---|
| Profitability table (all rows) | < 2 s warm, design part | design cache [P-30], prices attached after |
| Planner recompute on edit | < 200 ms | single `best_design` + throughput |
| System analysis | < 3 s | per-planet designs from cache + small MILP with time limit [P-38] |
| Validate (editor) | < 50 ms | pure geometry + LP on <= 300 pins |
| SDE refresh | + a few seconds | streamed planets, one executemany of 68k rows |

## 11. Testing

- Default `pytest` must stay "a few seconds" (CLAUDE.md) **[P-60]**. The full
  corpus (every product x chain x planet type x CC x radius bucket, several
  thousand generations) runs under a `pi_corpus` marker in CI only. The
  default run takes a fixed, representative sample (one per chain x 2 radii).
- Data tests on the real SDE files are network-bound. Store small fixture
  CSVs (a handful of schematics, planets, attributes) under `tests/fixtures/`;
  never hit Fuzzwork from tests.
- Golden tests: the two hand calculations in PI_PLAN 2; tax bases; tier
  counts; decay formula against CCP's sample; template round-trip on the
  real reference templates (both Eve-PI's MIT ones and a few user exports;
  **not** the unlicensed DalShooth files committed to the repo) **[P-61]**.
- Router tests monkeypatch the module object (`from ...pi import actions`),
  per CLAUDE.md testing conventions. Caches get an autouse reset fixture
  (`pi.static`, design cache) **[P-62]**.
- Live-verify each phase against the running API and in a browser
  (throwaway Playwright script, deleted afterwards).

## 12. Phase checklists (files touched)

- **Phase 1**: `production/sde.py`, `storage.py` (replace_sde_data, SDE_TABLES,
  counts, readers), `production/sde_diff.py` (PI tables, planets count-only),
  `docs/pi_schema.sql` + deploy lists, `sqlite_migration.py`,
  `pi/{constants,static,model,decay,engine}.py`, tests.
- **Phase 2**: `pi/{config,economics,chains,demand,actions}.py`,
  `tenant_scope.py`, `config.py` `_FIELD_RANGES`, `hubs.py`
  (`freight_override`), `access_gate.py`, `api/app.py`, `api/routers/pi.py`,
  `api/schemas.py`, `cli.py`, `esi_data/registry.py` (`pi` on `skills`),
  frontend pages + `toolKeys.ts` + `esiRegistry.ts`, Landing card.
- **Phase 2b**: `pi/system_plan.py` (Opus subagent), System page.
- **Phase 3**: `pi/layout/{template_io,geometry,validate}.py`, Templates page.
- **Phase 4**: snapshot kind (all places in 6), `pi/colonies.py`, alerts
  (logic, runner, schema CHECK change, `pi_alert_state`), Colonies page.
- **Phase 5a-5c**: `pi/layout/*`, LayoutEditor page.

## 13. Pitfall register

| # | Pitfall | Mitigation |
|---|---|---|
| P-01 | 3.11+ syntax breaks the 3.10 CI/deploy | no `tomllib`/`Self`/`ExceptionGroup`; CI matrix catches it |
| P-02 | New libs bloat bundle/lockfile | SVG editor, SciPy already present |
| P-03 | `pi` importing tool packages creates cycles/coupling | read other tools' data via `storage` only |
| P-04 | 83 MB mapDenormalize loaded into memory | stream + filter like dgmTypeAttributes |
| P-05 | radius in metres | divide by 1000 on import; test |
| P-06 | non-PI planet types (Shattered, Scorched, 56018-56024) | explicit 8-type whitelist |
| P-07 | huge gas giants make links very expensive | compact placement; show link budget share |
| P-08 | generic dogma attrs on thousands of non-PI types | filter generic attrs to PI categories 41/42/43 |
| P-09 | float4 REAL distorts volumes | DOUBLE PRECISION for new columns; round on load |
| P-10 | basePrice not stored in sde_types | store as pseudo-attribute -1 for PI types |
| P-11 | stale caches after SDE refresh | `pi.static.invalidate()` + design cache clear in `replace_sde_data` path |
| P-12 | 68k-row SDE diff floods Admin preview | planets count-only in diff |
| P-13 | P4 with P1 input mis-tiered | tier = 1 + max(input tiers); test counts |
| P-14 | JSONB key reordering breaks round-trip | serialize in EVE key order; round-trip test |
| P-15 | new schema file missing from deploy lists | grep checklist; first-request 500 otherwise |
| P-16 | sqlite_migration drift test fails | add new tables to its list |
| P-17 | alert CHECK constraint rejects new types | idempotent DROP/ADD CONSTRAINT |
| P-18 | alert_state is per type, PI needs per colony | `pi_alert_state` |
| P-19 | templates mix planet-type structure ids | resolve kind by group, not planet |
| P-20 | missing tax base silently 0 | log + exclude with reason |
| P-21 | empty PI tables before first SDE refresh | clear ActionError |
| P-22 | acos domain errors | clamp |
| P-23 | Diam != target planet | analyse with target radius |
| P-24 | link cost rounding vs game | ceil per link, confirmed in game (V-5) |
| P-25 | rounding La/Lo breaks spacing | validate after rounding |
| P-26 | closed form misses unrouted/starved factories | LP for arbitrary layouts |
| P-27 | doubled volumes from Eve-PI | SDE volumes only |
| P-28 | ECU locked to one P0 | P0->P2 = 2 ECUs |
| P-29 | non-deterministic tie-breaks | full ordering key |
| P-30 | design cache stale or unbounded | radius buckets, explicit invalidation |
| P-31 | cycle-time table conflict | settled: EVE Uni table (V-1); ESI cycle_time for real data |
| P-32 | qty_per_cycle semantics | settled: base per 15-min bar, formula exact (V-2) |
| P-33 | 0.45 sec boundary + float4 | shared rounded-security helper |
| P-34 | ALL_HUBS uses hub->home freight | `freight_override` in `hub_pricing` |
| P-35 | 332 cold ESI calls | existing cache; designs first, prices after |
| P-36 | history window shorter than 30 days | show actual window |
| P-37 | one CC per character per planet | MILP constraint |
| P-38 | MILP runtime | 5 s limit + greedy fallback |
| P-39 | no daily demand in Production; `_last_plan` volatile | use `production_buy_list`, quantity-based view |
| P-40 | demand view leaks Production data | extra `production` grant check |
| P-41 | flat La/Lo math | great-circle checks |
| P-42 | routes > 7 structures | depth by construction + validate |
| P-43 | feedback loop not terminating | strictly decreasing bound, 10 iterations |
| P-44 | wrong template field semantics | rules from real exports, tests |
| P-45 | JS drops `.0` floats | backend-only serialization |
| P-46 | unknown pin limit; comment field has no visible limit (V-4) | `Cmt` <= 60 chars anyway (our choice); pin limit checked by acceptance import |
| P-47 | bundle size / build memory | lazy route |
| P-48 | out-of-order validate responses | request sequence numbers |
| P-49 | snapshot kind touches 7+ places | registry consistency test |
| P-50 | abandoned colony 404 | skip, not error |
| P-51 | schematic id vs product type id | map via SDE |
| P-52 | ESI lat/lon convention unknown | verify (V-3) |
| P-53 | ESI colony state is old | project + label + age threshold |
| P-54 | calibration from too few/odd samples | min 3 samples, skip short programs |
| P-55 | double DMs | run inside existing per-tenant guard |
| P-56 | existing tenants lack the new grant | `admin grant-defaults` in deploy notes |
| P-57 | oversized/malicious template input | size/count caps, rate limit |
| P-58 | PiConfig not resolved per tenant | add to `enter_tenant` |
| P-59 | flat config.yaml / `_FIELD_RANGES` name collisions | `pi_` field prefix |
| P-60 | slow test suite | corpus behind marker |
| P-61 | unlicensed reference templates in repo | use MIT/user exports only |
| P-62 | caches leak between tests | autouse reset fixtures |
| P-64 | CCP noise term only adds yield | noise-free only for ratios; full formula for calibration |
| P-63 | the SDE holds 1,976 PI planets in the unreachable Jove regions (UUA-F4, J7HZ-F, A821-A) and 21,084 in J-space | System analysis says "region not reachable" for Jove regions; the later planet finder excludes them; J-space handled as wormhole zone |

## 14. Verification items (need real data or the game)

| # | What | When | How |
|---|---|---|---|
| V-1 | Program length -> cycle time thresholds | **done 2026-10-04** | in game: EVE Uni table confirmed |
| V-2 | Extractor formula and `qty_per_cycle` semantics | **done 2026-10-04** (ESI value check in phase 4) | in game: formula exact with q = 5903 |
| V-3 | ESI pin lat/lon vs template La/Lo | phase 4 | export the same colony in game and compare |
| V-4 | Pin count limit (comment: no visible limit, done) | phase 5a | in-game import of the acceptance set (PI_PLAN 6A.3) |
| V-5 | Link cost rounding (per link ceil) | **done 2026-10-04** | in game: 26 km and 114 km links |
