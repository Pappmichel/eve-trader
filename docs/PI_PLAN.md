# Planetary Industry (PI) tool - plan

Status: **planned 2026-10-04, not started.** PI was "deferred, not rejected"
(CLAUDE.md, "Deferred, not rejected"); the user asked for this plan on
2026-10-04 and confirmed decisions D1-D6 one by one the same day (section 11).
One item is still open: the per-security-zone yield defaults (D2), to be set
together with the user in phase 0.

Goal: a PI tool that answers **"which PI is worth doing for me, and which is
not"** from what a planet can *actually* build - real structure counts under
the Command Center's CPU/power budget, real extraction rates, real customs
taxes and real hub prices - plus in-game PI templates (import, analyse,
store, export, later generate) like the PI Nexus web tool
(https://evepinexus.com/).

## 1. Research summary

### 1.1 Sources looked at

| Source | What it is | Usable how |
|---|---|---|
| PI Nexus (evepinexus.com) | Browser-only colony planner, one obfuscated 6 MB HTML file, CSP `connect-src 'self'`, no API, no prices, no taxes | Link target only |
| `psychojf/Eve-PI` (GitHub, **MIT**, updated 2026-09-22) | Python desktop app PI Nexus is built from: colony generator, template analyser, in-game-measured layout constraints | Mechanics constants were checked one by one (below); MIT code may be ported with the license notice. **Its commodity volumes are wrong by a factor of 2** (see 1.3) |
| `jwebbdev/eve-pi` (GitHub, **no LICENSE file**, README claims Apache 2.0) | PI profit optimiser: CCP decay formula, CPU/PG fitting, multi-character allocator, templates | **Ideas only, no code** (licence unclear). Its CLAUDE.md lists in-game-verified values and dead ends, which were useful |
| CCP developer docs, "Planetary Industry" guide (developers.eveonline.com/docs/guides/pi/) | Official extractor yield algorithm | Authoritative formula |
| EVE University wiki (Planetary Industry, Planetary Buildings, Planetary Commodities, PI Templates) | Community docs, flagged as "needs update after Equinox" | Cross-check only; two errors found (1.3) |
| SDE via Fuzzwork CSV (`planetSchematics*.csv`, `invTypes.csv`, `dgmTypeAttributes.csv`, `mapDenormalize.csv`) and ESI `/universe/types`, `/dogma/attributes` | Ground truth | Primary data source |

### 1.2 What was verified against SDE/ESI (2026-10-04)

Everything below was checked live, not copied from a wiki:

- **Recipes**: 68 schematics (15 P1, 24 P2, 21 P3, 8 P4) in
  `planetSchematics`/`planetSchematicsTypeMap`. Inputs, quantities and outputs
  match Eve-PI's tables exactly (scripted diff, 0 mismatches).
  P0->P1: 3000 P0 -> 20 P1; P1->P2: 40+40 -> 5; P2->P3: 10+10(+10) -> 3;
  P3->P4: 6+6+6 (or 6+6 + 40 P1) -> 1.
- **Cycle times** (`planetSchematics.cycleTime`): P1 1800 s, P2/P3/P4 3600 s.
  So per factory and hour: Basic 6000 P0 -> 40 P1; Advanced 80 P1 -> 5 P2
  or 20-30 P2 -> 3 P3; High-Tech 18 P3 (or 12 P3 + 40 P1) -> 1 P4.
- **High-Tech facilities exist only on Barren and Temperate**:
  `planetSchematicsPinMap` lists only pin types 2475 and 2482 for P4 schematics.
- **Commodity volumes** (ESI `volume`): P0 0.005, P1 0.19, P2 0.75, P3 3.0,
  P4 50.0 m3.
- **Structure CPU/power** (dogma 49 `cpuLoad`, 15 `powerLoad`):
  ECU 400 tf / 2600 MW; extractor head 110 tf / 550 MW (ECU attributes
  1690/1691); Basic 200/800; Advanced 500/700; High-Tech 1100/400;
  Storage 500/700; Launchpad 3600/700. Command Center level 0: 1675 tf /
  6000 MW (attributes 48/11).
- **Capacities** (dogma 38): Launchpad 10,000 m3, Storage 12,000 m3,
  Command Center 500 m3.
- **Links** (type 2280 "Link"): 15 tf + 0.2 tf/km, 10 MW + 0.15 MW/km;
  level modifiers 1.4 (CPU) / 1.2 (power); **bandwidth 1250 m3/h at level 0**
  (`logisticalCapacity`, attr 1631).
- **Structure ISK costs** (`invTypes.basePrice`): ECU 45k, Basic 75k,
  Advanced 250k, High-Tech 525k, Storage 250k, Launchpad 900k,
  Command Center 90k (market item).
- **Customs tax base per unit** is in the SDE as dogma attributes
  1640 `importTaxMultiplier` / 1641 `exportTaxMultiplier` on every commodity:
  P0 5, P1 400, P2 7200, P3 60,000, P4 1,200,000 ISK. Launchpad attribute
  1638 `importTax` = 0.5 (import costs half). No hardcoded tier table needed.
- **Extractor decay constants**: attributes 1683 `ecuDecayFactor` 0.012 and
  1687 `ecuNoiseFactor` 0.8 (match CCP's guide). ECU base 1642
  `pinExtractionQuantity` 1000, 1643 `pinCycleTime` 300 s, depletion 1644/1645.
- **In-game template JSON**: a real in-game export (DalShooth templates, via
  jwebbdev) confirms pin `S` is the **product type id** (Coolant = 9832), not
  the schematic id (66).
- **ESI colony endpoints** (current OpenAPI):
  `GET /characters/{id}/planets` and `/characters/{id}/planets/{planet_id}`
  (scope `esi-planets.manage_planets.v1`): pins with lat/lon, `type_id`,
  `schematic_id`, `extractor_details` (`qty_per_cycle`, `cycle_time`,
  `head_radius`, `heads[]`, `product_type_id`), `install_time`/`expiry_time`,
  contents; links with `link_level`; routes with `quantity` and `waypoints`.
  That is everything an in-game template holds, so a real colony can be
  turned into a template. `GET /corporations/{id}/customs_offices`
  (`esi-planets.read_customs_offices.v1`) shows tax rates **only for your own
  corp's offices**. Nothing in ESI tells you another owner's POCO/Skyhook tax.
  `/universe/planets/{id}` has no radius, so radius comes from the SDE.

### 1.3 Errors found in the sources (do not copy blindly)

- **Eve-PI / PI Nexus volumes are 2x too high** (`COMMODITY_SIZE` = 0.01 /
  0.38 / 1.5 / 6 / 100). Every storage-duration and hauling figure there is
  off by 2x. We read volumes from the SDE.
- **EVE Uni "Planetary Buildings"** gives link capacity as "250 m3 (level 0)";
  the SDE says 1250 m3/h, and Eve-PI confirmed 1250 (and 2500 at level 1)
  in game.
- **EVE Uni "Planetary Commodities"** lists incomplete resources per planet
  type (Gas with 3, Barren with Heavy Metals). Eve-PI and jwebbdev agree
  independently on **five P0 per planet type** (table in 3.1). This table is
  **not in the SDE** (planet types have no dogma attributes). It is the only
  hardcoded game table in this plan; verify it once in game before relying on it.

### 1.4 Constraints measured in game by the Eve-PI author (not in the SDE)

- Minimum structure spacing **0.012 rad** (EVE refuses to import a template
  with structures closer). jwebbdev's empirical minimum link length
  `-0.77 + 0.012182 x radius_km` is the same rule. So a link is at least about
  `0.012 x radius` km long, which is why **planet radius drives link cost**.
- **A route may pass at most 7 structures**; longer routes are silently
  dropped on import.
- Link upgrade cost: `base + per_km x km x (level + 1) ^ modifier`, checked at
  levels 1-2 against in-game numbers.
- Templates carry link levels (`Lv`) and import with them.
- (jwebbdev) La/Lo/Diam must serialize as floats; P0 routes must not pass
  through Basic facilities; template JSON is pasted as one compact line.

### 1.5 Command Center levels and skills

CPU/power per level (EVE Uni, Eve-PI and jwebbdev all agree; only level 0 is
in the SDE): L0 1675/6000, L1 7057/9000, L2 12136/12000, L3 17215/15000,
L4 21315/17000, L5 25415/19000. Upgrade ISK: 580k, 930k, 1.2M, 1.5M, 2.1M
(EVE Uni). Command Center Upgrades caps the level, Interplanetary
Consolidation gives +1 planet per level (up to 6). Customs Code Expertise
lowers the high-sec NPC customs rate (10% base, 5% at level V). Planetology,
Advanced Planetology and Remote Sensing only affect scanning, **not yield**.

### 1.6 Extraction: the part no tool gets "exactly right"

CCP's formula (per extractor program; `t` counts 15-minute bars):

```
bar_width   = cycle_time_s / 900
t           = (cycle_index + 0.5) * bar_width
decay       = qty_per_cycle / (1 + t * 0.012)
phase       = qty_per_cycle ** 0.7
noise       = max((cos(phase + t/12) + cos(phase/2 + t*0.2) + cos(t*0.5)) / 3, 0)
cycle_out   = bar_width * decay * (1 + 0.8 * noise)
```

Cycle time follows program length: <=25 h -> 30 min, <=50 h -> 1 h,
<=100 h -> 2 h, <=200 h -> 4 h, longer -> 8 h (max 14 days). Decay is
steep: averaged over a 24 h program, output is ~0.67 of the first bar;
over 7 days ~0.27.

**What stays unknown:** `qty_per_cycle` comes from the deposit under the
heads (planet, spot, head spacing, depletion from earlier programs) and the
chosen program length. No API or SDE gives it before you place heads.
Community planning defaults differ by about 3x (PI Nexus: flat 2000 P0/head/h
with no decay; jwebbdev: about 6000/head/h peak; wormhole estimates of
40-60k/h per colony). So:

- **Planning uses an explicit, user-editable yield assumption**
  (P0/head/hour, averaged over the program), labelled as an assumption in the
  UI, with **one default per security zone** (high-sec, low-sec, null-sec,
  wormhole; D2). The zone comes from the planet's system security
  (`sde_solar_systems.security`, wormhole = J-space region ids). The numbers
  are set together with the user in phase 0.
- **Calibration from the user's own colonies** (ESI, optional): for each real
  extractor, apply the formula to `qty_per_cycle`/`cycle_time`/program length
  to get its true average per head and hour. Store it per P0 type and planet
  type, and use the median as the default once samples exist. This is the
  only way to get a reliable number. It is also something none of the tools
  above does.

## 2. Extractor planets vs factory planets

These are two different economic problems, and the tool keeps them apart.

| | Extractor planet | Factory planet |
|---|---|---|
| Chains | P0->P1 (any type); P0->P2 only if the planet type carries both P0 | P1->P2, P2->P3, P1->P3, P3->P4 (High-Tech: Barren/Temperate only), P2->P4/P1->P4 |
| Binding limit | **Power**: an ECU with 10 heads is 1500 tf / **8100 MW**, and each Basic facility adds 800 MW. Extraction rate decides how many factories are fed | **CPU/power for factories + launchpads**, and the import/export logistics |
| What decides output | Yield per head (assumption/calibration), program length (decay), head count | Factory count that fits, inputs actually hauled in |
| Inputs | Free (P0 from the ground) | Bought or made elsewhere, plus import tax (x0.5) and freight |
| Taxes | Export only (P1 base 400 -> e.g. 40 ISK/unit at 10%), which hurts low-value P1 | Import (inputs) + export (outputs); low base on P1/P2 imports, high on P3/P4 |
| Effort | Restart program every N days; more often means higher average yield | Haul inputs in every collection interval; storage (launchpad 10k m3) sets the max interval |
| Typical failure | Too many factories for the heads (idle Basic facilities) | Storage runs dry before the collection interval; link bandwidth; route over 7 structures |

Example of why this needs an exact fit and not a rule of thumb (CC5, radius
5000 km, minimum link about 60 km, so a link costs about 27 tf / 19 MW):
- *Factory P1->P2, 2 launchpads*: power allows about 24 Advanced facilities
  (CPU would allow 34). Result: 120 P2/h from 1920 P1/h, about 365 m3/h in
  and 90 m3/h out, so two pads (20k m3) hold about 1.5 days of inputs.
- *Extractor P0->P1, 1 ECU x 10 heads + 1 pad*: about 10,200 MW is left, so
  at most about 12 Basic facilities (72,000 P0/h needed). With 10 heads that
  needs 7200 P0/head/h, which few planets give. A second ECU leaves room for
  only 2 Basic facilities. The best design is an integer trade-off between
  heads and factories under the yield assumption, which is exactly what the
  optimiser has to search.

## 3. Calculation model

All in a new pure module (`eve_trader/pi/engine.py`, no I/O, like
`skill_plan_logic.py`), fed by SDE rows and settings.

### 3.1 Static data

- From SDE (new global tables, filled by `refresh_sde()`; see 5):
  schematics, schematic inputs/outputs, schematic->pin-type map, commodity
  volume + tier + tax base (1640/1641), PI structure attributes
  (CPU/power/capacity/planet restriction/link attributes/ECU head costs/decay
  constants), structure ISK cost, PI planets with radius.
- Hardcoded in `pi/constants.py`, each with source comment: P0 per planet type
  (1.3), Command Center level table (1.5), cycle-time thresholds (1.6),
  0.012 rad spacing, 7-structure route limit. Tier is derived from the market
  group/schematic graph, not from names.

### 3.2 Colony design and capacity (the "what can this planet build" step)

A **design** = chain + product + planet type + radius + CC level + counts
(ECUs, heads per ECU, Basic/Advanced/High-Tech facilities, launchpads,
storage, link levels).

- `fit(design)`: CPU and power used, including links. Link count = structures
  - 1 (tree); length = spacing x radius x a per-chain span factor (PI Nexus
  measured extra spacings; start with its values and replace them with exact
  numbers once our own generator exists). A design either fits or doesn't, and
  the UI shows the margin.
- `best_design(chain, product, planet, cc_level, yield, interval)`: enumerate
  the integer space (ECUs 0-2, heads 1-10 per ECU, factories, pads 1-4,
  storage 0-4; a few thousand points, so brute force is fine and exact).
  Objective: **max sellable output** subject to fit, then fewest idle
  factories, then most storage. Never more factories than the supply feeds.
  jwebbdev documents this exact failure (18 factories, 1 head, zero output).
- `throughput(design)`: steady-state rates per commodity (extracted, made,
  consumed, imported, exported) using SDE cycle times; utilisation per factory
  stage; storage duration = buffer m3 / net m3 per hour; link loads against
  1250 x 2^level m3/h.

The template analyser (6) runs the same `throughput`/`fit` on an exact pin
list. Planning estimate and template are two inputs to one model, not two models.

### 3.3 Profitability ("lohnt sich / lohnt sich nicht")

Per design, per day:

```
revenue    = sum(out_units * price_out)                    (hub price, see below)
input_cost = sum(in_units * price_in)                      (0 for P0)
tax        = sum(out_units * export_base * rate)
           + sum(in_units * import_base * rate * 0.5)
freight    = (m3_in + m3_out) * freight_per_m3             (hub freight table)
setup      = (CC upgrades + structures + CC) / amortisation_days
profit     = revenue - input_cost - tax - freight - setup
```

- **Prices**: own `PiConfig.hub_region_id`, `ALL_HUBS` (0) allowed, through
  `hubs.hub_pricing` like Production/Doctrine/Refining (issue #222). Selling
  uses the sell-side valuation minus sales tax + broker fee (tool settings,
  same shape as Refining's). Buying inputs uses the landed cost from
  `hub_pricing`.
- **Tax rate**: one effective rate per planned colony (default from settings,
  e.g. 10% for high-sec NPC customs), entered by the user. No security-based
  guessing, since ESI cannot see foreign POCO/Skyhook rates.
- **Ranking metric: ISK per planet slot per day**, not ISK/m3 (jwebbdev's
  documented dead end: tiny-volume P4 wins ISK/m3 but uses dozens of planets).
  ISK per m3 hauled and haul m3 per week are shown as secondary columns.
- **Opportunity cost for own inputs**: a factory planet fed by your own P1 is
  valued at the P1 market price, so "value added" = factory profit over just
  selling the inputs. Without this a chain looks 2-5x better than it is
  (jwebbdev, Polyaramids example).
- **Market depth**: show average daily traded volume (existing Goonmetrics/ESI
  history plumbing) next to planned daily output, and flag output > X% of
  daily volume (X configurable, default 10%). A P4 plan that would flood the
  market is "not worth it" even with a good margin. This is a figure, not a
  cap (same spirit as CLAUDE.md "Theoretical ceiling").
- **Verdict**: profit > 0 after setup and above a user threshold ISK/planet/day
  -> worth it; otherwise not worth it, with the **reason that dominates**
  (tax, freight, input cost, extraction too low, market too thin). Showing
  the reason is the point of the tool.

### 3.4 Chains across planets

For a target product, compute the planet count per stage from the per-design
rates: e.g. one P1->P2 factory planet (about 1920 P1/h) needs N extractor
planets per P1 input, given the yield assumption. Show total planets,
ISK/day per planet slot for the whole chain vs. selling at each intermediate
tier, and the characters it needs (planets per character and CC level from
ESI skills where shared, else the manual setting; D3). This answers "build up to P2 or sell P1?"
directly. A full multi-character allocator (jwebbdev's 60 KB greedy) is
**not** in scope (see 4).

## 4. What is worth building, and what is not

| Feature | Verdict | Why |
|---|---|---|
| Profit table: every product x chain x planet type, best design, ISK/planet/day, verdict + reason | **Build (core)** | The question the user asked; uses existing pricing/hubs |
| Exact capacity fit + best-design search | **Build (core)** | Without it profit numbers are fiction |
| Chain view (planets per stage, value added per tier) | **Build** | Answers "how far up the chain" |
| Template library: paste/import JSON, analyse (fit, throughput, routes, storage, taxes), store per tenant, export | **Build** | Low risk, matches PI Nexus' useful part, no generator needed |
| ESI colonies: list own colonies, extractor expiry, convert colony -> template, yield calibration | **Build (phase 4)** | Only reliable yield source; colony -> template is cheap once ESI rows exist |
| Template generator (design -> importable layout) | **Build later, carefully** | Highest effort; correctness only provable by in-game import (needs the user); own small generator (D1, scope in 6.3) |
| Saved plans (`pi_plans`) | **Build (phase 2)** | D6; later compared with real colonies via ESI |
| Planet finder (planets within N jumps, by type/radius) | **Optional/later** | Planets come with phase 1 (D5); still needs a jump graph (`mapSolarSystemJumps`); nice but not part of "is it worth it" |
| Discord alert "extractor program expires" | **Later, small** | Natural fit for `alerts/` (like `skillqueue_empty`) once the ESI kind exists |
| Multi-character greedy allocator | **Don't** | Large, opinionated, hard to verify; the chain view + per-character planet count gives 90% |
| Layout eye candy (shapes, 3D planet, themes) | **Don't** | PI Nexus already does this; link to it |
| Modelling deposit maps / head placement / depletion | **Don't** | No data source; calibration covers it |
| Command Center launch tax, POCO standings tiers | **Don't** | Edge cases; SDE CC export multiplier (3.0 vs wiki "x1.5") is unclear anyway. Assume launchpad <-> customs office |

## 5. Data and schema

- New schema file `docs/pi_schema.sql`, which must be added to every place
  that applies schema files (CLAUDE.md "A brand-new schema file":
  `deploy/deploy.sh`, `deploy/README.md`, root `README.md`,
  `.cursor/start.sh`; grep an existing filename to find them all).
- **Global SDE tables** (no `tenant_id`, same pattern as
  `sde_skill_requirements` in `character_management_schema.sql`):
  `sde_pi_schematics` (id, name, cycle_s), `sde_pi_schematic_types`
  (schematic, type, qty, is_input), `sde_pi_schematic_pins`,
  `sde_pi_commodities` (type, tier, volume, import/export tax base),
  `sde_pi_structures` (type, kind, planet_type, cpu, power, capacity,
  isk_cost, plus link/ECU attributes), `sde_pi_planets` (planet_id, name,
  system_id, type_id, radius_km) for **all ~68k PI planets** (D5): streamed
  and filtered out of `mapDenormalize.csv` (83 MB, groupID 7 and a PI
  planet type) during the SDE refresh, the same streaming approach as
  `dgmTypeAttributes.csv`, never held in memory whole. The planner then picks
  a real planet (system search) and gets type, radius and security zone
  from it; a free planet type + radius stays possible for "what if".
- `production/sde.py`: add the three `planetSchematics*.csv` files to the
  fetched list; extend the streamed `dgmTypeAttributes.csv` filter (already
  used for skills) with the PI attribute ids (15, 49, 11, 48, 38, 1631-1636,
  1638-1645, 1683, 1687, 1690, 1691) for PI types only; volumes/base price
  come from `invTypes.csv`, which is already fetched.
- **Per-tenant tables** (RLS shape copied from `phase1_schema.sql`):
  `pi_templates` (id, name, comment, planet_type_id, cc_level, diameter,
  json, source `paste|generated|esi`, created_at),
  `pi_yield_samples` (character_id, planet_id, p0_type_id, planet_type_id,
  per_head_per_hour, program_hours, sampled_at; phase 4),
  `pi_plans` (D6, phase 2): saved designs and chains - name, planet_id (or
  free planet type + radius), character_id (optional), design JSON, customs
  tax rate, yield override, created/updated. Phase 4 compares a plan with
  the matching real colony (same character + planet) via ESI: planned vs.
  actual structures and extraction.
- `PiConfig` dataclass (`eve_trader/pi/config.py`): `hub_region_id`,
  broker fee, sales tax, default customs rate, yield per head **per security
  zone** (four fields, D2), default program length, collection interval,
  amortisation days, market-share warning %, and the manual fallbacks
  planets per character / CC level / Customs Code Expertise level (D3).
- Skills (D3, phase 2): characters shared with `pi` for the existing
  `skills` kind supply Interplanetary Consolidation (planets = 1 + level),
  Command Center Upgrades (max CC level) and Customs Code Expertise per
  character, read via `read_esi` like `char_skills`; characters without a
  share use the manual settings. `pi` is added to the `skills` kind's
  consuming tools. Validated by
  `validate_config_overrides`; enum checks (if any) in a PI-specific
  validator, not in `config.py`.

## 6. Templates

1. **Library + analyser (phase 3)**: paste or upload JSON, parse defensively
   (1-based indices, unknown type ids reported, not crashed), detect planet
   type/chain/product from the pins (as PI Nexus does: never from the name),
   run `fit`/`throughput`, and show CPU/power, idle factories, storage
   duration, link loads/upgrades needed, routes over 7 structures, and the
   profit for this exact layout. Store per tenant; export as compact one-line
   JSON with floats preserved (1.4).
2. **ESI colony -> template (phase 4)**: map ESI pins/links/routes to
   `P`/`L`/`R`. Verify the lat/lon convention against an exported template of
   the same colony before shipping (ESI lat/lon vs template `La` polar angle,
   see Eve-PI `pin_angle`).
3. **Generator (phase 5, D1: own small generator)**: emit a template for a
   `best_design`. "Small" means: one fixed standard layout per chain type
   (P0->P1, P0->P2, P1->P2, P2->P3, P1->P3, P3->P4) - launchpad hub, factory
   rows/tree, ECUs - with exactly the counts the planner computed. It must
   satisfy 0.012 rad spacing, a tree of links within the CPU/power budget,
   routes of at most 7 structures, P0 never routed through Basic facilities,
   link levels for loads over 1250 m3/h, floats for La/Lo/Diam. **Not** in
   scope (PI Nexus does these): alternative shapes, dragging structures,
   per-factory P2 choice, storage-suggestion rebuilds, variant comparison.
   Eve-PI's MIT generator is used only as a local test oracle, not ported.
   Acceptance needs the user to import a fixed set of generated templates in
   game (one per chain type, small and large radius).

## 7. ESI integration (phase 4)

- New data kind `planets` in `esi_data/registry.py`: scope
  `esi-planets.manage_planets.v1`, consuming tool `pi`. Colonies change
  rarely and extractor expiry matters, so a **snapshot kind** (normal tier,
  `schedule_mode` on-demand + page-open sync like Character Info) rather than
  `live_only`. Reads go through `read_esi`/`fields` like every other tool,
  and the `fields.gate()` states apply.
- Re-auth flow is the existing `/api/characters/reauth/start`; no new token
  namespace.
- Character skills come from the existing `skills` kind already in phase 2
  (D3, see 5); phase 4 only adds the `planets` kind.

## 8. API / UI

- Package `eve_trader/pi/` (`engine.py` pure, `constants.py`, `config.py`,
  `actions.py` with `do_*`, `templates.py`), router `api/routers/pi.py`
  using `_wrap`, `ActionError` for user errors. Tool key **`pi`**: add it to
  `ALL_TOOL_KEYS`, `DEFAULT_TOOL_KEYS` (D4: default grant, so it is
  preselected in Add User/access approval and `eve-trader admin
  grant-defaults` backfills it), `frontend/src/toolKeys.ts`,
  `_TOOL_PATH_PREFIXES`, `esi_data/registry.py` consuming tools.
- Pages: **Profitability** (filterable table: product, chain, planet type,
  ISK/planet/day, verdict + reason, click to see the design and the cost
  breakdown); **Planner** (pick planet type/radius/CC level/chain/product,
  edit counts, live fit/throughput/profit); **Chains**; **Templates**;
  **Colonies** (phase 4); **Settings**.
- CLI: `eve-trader pi rank|design|template analyse` calling the same `do_*`.

## 9. Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Set the per-zone yield defaults with the user (D2); one in-game check of the P0 table (1.3) and CC levels | Values recorded here |
| 1 | SDE import (schematics, PI attributes, commodities, structures, all PI planets with radius) + `pi/engine.py` capacity/throughput + unit tests | Golden tests pass (10) |
| 2 | Pricing, taxes, freight, profitability, verdicts, chain view; skills from ESI + manual fallback; saved plans; tool key, router, Profitability/Planner/Chains/Plans/Settings pages | Live-verified against the running API and browser |
| 3 | Template library + analyser + export | Real in-game exports analyse correctly |
| 4 | ESI `planets` kind, colonies page, yield calibration, colony -> template, plan vs. real colony | Calibration matches in-game totals on a real colony |
| 5 | Own small template generator (D1) | User-imported test set builds without errors |
| 6 | Optional: planet finder (jump graph), extractor-expiry Discord alert | - |

Each phase is one or more commits on its own branch, with `pytest` green.

## 10. Testing and verification

- **Data tests**: SDE import yields 68 schematics, 8 P4 schematics only on pin
  types 2475/2482, volumes 0.005/0.19/0.75/3/50, tax bases 5/400/7200/60k/1.2M.
- **Engine golden tests**: hand-computed fits (the two examples in 2), and a
  reference set cross-checked against Eve-PI's MIT generator run locally as an
  oracle (a throwaway script outside the repo; correct for its 2x volume
  error when comparing storage numbers).
- **Decay formula**: unit test against CCP's algorithm; live check against one
  real extractor of the user (ESI values vs in-game shown program total).
- **Template analyser**: the DalShooth/Eve-PI reference templates parse; CPU
  and power match what the game shows for the same colony (the Eve-PI author
  matched in-game usage to the unit, e.g. 23,075 tf / 18,764 MW).
- Live-verify discipline from CLAUDE.md (API with `Invoke-RestMethod`, UI with
  a throwaway Playwright script).

## 11. Decisions (confirmed with the user 2026-10-04)

| # | Question | Decision |
|---|---|---|
| D1 | Template generator: own, port Eve-PI's MIT generator, or none? | **Own small generator** for the standard layouts (scope in 6.3), after phases 1-4; Eve-PI only as local test oracle |
| D2 | Yield default before any calibration | **One default per security zone** (high/low/null/wormhole), editable, labelled as assumption; calibration replaces it. Numbers still open, set together in phase 0 |
| D3 | Planets per character / CC level: manual or ESI skills? | **Both right away** (phase 2): ESI skills per shared character, manual setting as fallback |
| D4 | Is `pi` a default grant for new users? | **Yes**, in `DEFAULT_TOOL_KEYS` |
| D5 | Planet radius source | **Import all ~68k PI planets** with radius from `mapDenormalize` in phase 1 |
| D6 | Save plans per tenant? | **Yes, from the start** (`pi_plans`, phase 2; compared with real colonies in phase 4) |

## 12. Sources

- PI Nexus: https://evepinexus.com/
- Eve-PI (MIT): https://github.com/psychojf/Eve-PI (`src/pi_data.py`,
  `src/services/template_service.py`, `route_limits.py`, `factory_runtime.py`)
- jwebbdev/eve-pi (no licence file): https://github.com/jwebbdev/eve-pi
- CCP extractor formula: https://developers.eveonline.com/docs/guides/pi/
- EVE Uni: https://wiki.eveuniversity.org/Planetary_Industry,
  https://wiki.eveuniversity.org/Planetary_Buildings,
  https://wiki.eveuniversity.org/Planetary_Commodities,
  https://wiki.eveuniversity.org/PI_Templates
- Equinox (Skyhooks replace customs offices in sov null, same tax role):
  https://wiki.eveuniversity.org/Orbital_Skyhook
- SDE: https://www.fuzzwork.co.uk/dump/latest/csv/ (`planetSchematics*.csv`,
  `invTypes.csv`, `dgmTypeAttributes.csv`, `mapDenormalize.csv`); ESI
  `/universe/types/{id}`, `/dogma/attributes/{id}`, OpenAPI at
  https://esi.evetech.net/meta/openapi.json
