# Planetary Industry

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Planetary Industry (PI) tool

Plan and decisions: `docs/PI_PLAN.md`; technical design with the pitfall
register (P-01..P-64) and in-game verification items: `docs/PI_TECHNICAL_
DESIGN.md`. This section is the durable summary. Tool key `pi` (default
grant), package `eve_trader/pi/`, router `/api/pi/`, schema
`docs/pi_schema.sql` (applied after `character_management_schema.sql`: it
widens the alert type CHECK constraints).

- **SDE first, constants second.** Recipes, cycle times, structure CPU/power/
  capacity, link costs, tax bases (dogma 1640/1641) and volumes come from the
  SDE refresh (`sde_pi_*` tables; `mapDenormalize.csv` is *streamed* for the
  67.7k PI planets; `invTypes.basePrice`/`capacity` are stored as pseudo
  attributes -1/-2 because `sde_types` has no such columns). Only the P0 per
  planet type table, Command Center levels and layout rules are hardcoded
  (`pi/constants.py`), each checked in game. Eve-PI/PI Nexus volumes are 2x
  too high - never copy data from there. The SDE also lists one unpublished
  Command Center type per upgrade level; the placeable one is level 0.
- **Extractor formula is CCP's, verified exactly in game** (`pi/decay.py`;
  golden test: q=5903, 50 h -> 23,058 / 10,654 / 682,147). `q` is a base per
  15-minute bar; the noise term only *adds* yield (use the noise-free curve
  for ratios only). Planning yields are per security zone (D2) until real
  colonies calibrate them.
- **Engine** (`pi/engine.py`, pure): a design is structure counts; what it
  makes is exactly the set of factories it has, everything else is extracted
  (with an ECU) or hauled in. `best_design` maximises *effective* output
  (raw output x min(1, storage hours / collection interval)); designs are
  cached without prices (single lock, cleared on PI settings save and SDE
  apply via `pi.static.invalidate()`).
- **Economics** (`pi/economics.py`): customs = (high-sec NPC 10% - 1% per
  Customs Code Expertise) + owner rate, import x0.5; PI has its own freight
  rate - `hubs.hub_pricing(freight_override=...)`, never the shared hub ->
  home-structure table. With "All hubs" inputs are bought at the cheapest hub
  (`hub_pricing`) but outputs are valued at the hub with the highest net
  revenue for `pi_valuation` (`economics.best_output_hubs`, `Prices.output_*`);
  the planner response names each import's and export's hub. Verdict reasons
  are a fixed vocabulary.
- **Skills drive tax and demand (D3, GH #47).** `actions._skill_defaults(cfg)`
  gives `cc_level` and `customs_code_expertise` from `skills.load_overview`
  (highest level over the characters sharing skills with PI, `skill_source`
  `esi`), else the manual `pi_cc_level` / `pi_customs_code_expertise_level`
  (`manual`). A request calls it once and passes it on (`_cc_level`, `_market`,
  `_tax_parts`, `skills=`); an explicit parameter wins. Profitability, planner,
  chain, ways-to-build, chain plan, system analysis and Production demand all
  use it and return `skill_source`. The design cache key holds the CC level
  actually used. Applied automatically (not a fee suggestion).
- **Layouts** (`pi/layout/`): `template_io` round-trips EVE templates byte
  for byte (alphabetical keys, La/Lo/Diam floats; pin `S` = product type id,
  `H` = head count, no Command Center pin). Templates are serialized only in
  the backend - JavaScript drops `.0` and the game rejects the paste.
  `validate.analyse` is the one validator (spacing 0.012 rad after 5-decimal
  rounding, <= 7 structures per route, per-link `ceil` costs - confirmed in
  game, link loads, throughput LP). The generator never routes P0 through a
  Basic facility (Basic facilities and extractors hang directly off a hub);
  real in-game exports sometimes do, so the validator only notes it. The
  spacing check allows 2e-5 rad below 0.012: in-game exports sit at 0.01199x.
- **Reference layouts** (`pi/layout/reference.py`, `reference_templates.json`):
  `do_generate_layout` (standard shape, `use_references`) also adapts public
  community templates from planetsin.space (comments/authors stripped, only
  validator-clean ones) to the design - recipe tree mapped by shape, planet
  structure ids and route quantities rewritten, factories rebalanced to the
  design's stage ratio, unneeded factories/hubs pruned, then grown while the
  budget allows - and uses it when its *effective* output (interval-aware,
  colony's own products and P0 barred from import) is >= 98% of the
  generator's. Otherwise the generator's layout stays. Not an import feature:
  the site is only a source of examples; its API is unofficial.
- **Security zone**: use `economics.security_zone` (wormhole by region id
  first, Pochven = null-sec); it relies on `production.constants.
  _rounded_security`, which rounds to 4 decimals first because
  `sde_solar_systems.security` is float4 (0.45 used to come back as low-sec).
- **Config fields are `pi_`-prefixed** (except `hub_region_id`): config.yaml
  and `_FIELD_RANGES` are flat across all tool dataclasses.
- **Cross-tool reads**: the Production demand view reads
  `production_buy_list` through storage and its route additionally requires
  the `production` grant (same pattern as the Doctrine skill check).
- **Tests and corpus**: `tests/test_pi_*.py` use a real SDE snapshot
  (`tests/fixtures/pi_static_rows.json`). The full generator corpus (every
  product x chain x planet type x CC level x radius) runs only with
  `PI_CORPUS=1` (~3 min).
