# Ore & Minerals, Module Reprocessing and Sorting

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Sorting paste

`sorting.actions.do_sorting_paste` (`POST /api/sorting/sorting-paste`, tab
`/sorting/paste`) sorts a pasted inventory window for stock the last asset sync
has not seen yet. It stores nothing (POST only for the text body) and needs no
intake source. Only the intake comes from the paste
(`engine.rows_for_intake`): Doctrine and market-listing demand still use the
last synced assets. Blueprints count, unlike the manual-stock paste, which
needs ME/TE/runs (`paste_parser.resolve_paste(skip_blueprints=False)`). A line
without a quantity stays a reported error and is never guessed as 1.

## Mineral shopping lists respect market depth

The Coverage Check's `landed_price_per_unit` (GitHub issue #420) is the
effective cost per delivered unit, so price x delivered adds up to the plan's
total cost (`mineral_supply_lp.landed_price_by_mineral`). The issue asked for a
median; confirmed with the user 2026-11-05 that a weighted mean is what they
want, since a median is just the largest source's price. A source yielding
several listed minerals splits its cost by quantity x the mineral's cheapest
direct tier; minerals not on the list take no share.

Both mineral shopping lists (Ore & Minerals `refining/optimizer.py`, Module
Reprocessing `module_reprocessing/shopping_optimizer.py`) solve through one
shared integer program, `eve_trader/mineral_supply_lp.py`. Every ore, module
and directly bought mineral is split into supply tiers
(`eve_trader/market_depth.py`): the hub's real sell orders up to the reference
price (5th percentile weighted by listed volume) plus `shopping_price_tolerance` (default 5%, separate
field in RefiningConfig and ModuleReprocessingConfig), in price bands starting
+1%, +2%, +4%... above the cheapest order, each at its volume-weighted price.
Deeper bands are bought only while they still beat every other source; when a
cheap source runs out the solver switches to other ores/modules, and what is
still missing is reported as `MineralCoverage.short` / plan `shortfall`
instead of an error (confirmed with the user 2026-11-04). The home-market
quote (Goonmetrics) has no volume: it stays one uncapped tier and its lines
carry `depth_checked: false` ("qty unchecked" in the UI). Order books come
from the same cached `region_orders_raw` the prices came from, so depth costs
no extra ESI calls.

**By-products (confirmed with the user 2026-11-10).** Materials a bought source
yields that are not on the list (Zeolites -> Atmospheric Gases) are always
returned as `byproducts` with `byproduct_value` and `net_cost`, valued at the
home-market Goonmetrics sell quote x `structure_sell_haircut` (no quote = unpriced,
never guessed; depth not checked). Their value enters the objective only with the
per-tool switch `credit_byproducts` (RefiningConfig / ModuleReprocessingConfig,
default off): a source's portion cost becomes `max(cost - credit, 1 ISK)`
(`mineral_supply_lp.net_portion_cost`), so a source is never bought beyond what
the list needs, and `savings_vs_all_direct` is then on `net_cost`. Surplus of a
*requested* mineral is still never credited.

**Import tab (confirmed with the user 2026-11-10).** Ore & Minerals and Module
Reprocessing each have an Import tab (`POST /api/refining/import/plan`,
`POST /api/module-reprocessing/import/plan`). It returns the most profitable buy
list for an ISK budget (landed cost) and/or a haul volume, solved by the shared,
pure `eve_trader/import_planner.py`. Ore & Minerals buys from the active Ore
Shortlist. Module Reprocessing buys from its active module shortlist plus the
compressed ore universe, each kind priced like its own Shortlist. A hub sell
order is bought only while its landed price still meets the tool's min margin and
min profit (`cap_hub_price`, passed as `market_depth`'s `max_price`), not up to
`shopping_price_tolerance`. The solve is a continuous LP rounded down to whole
portions and topped up greedily. It never uses integer columns, because of the
HiGHS hang below. Mineral value is the Shortlist's C-J figure. The minerals table
shows output against the quantity listed at C-J as a hint only; that does not
change the selection. Nothing is stored.

**One solver process at a time.** `mineral_supply_lp._solve_mip_in_child` holds a
module-wide `BoundedSemaphore(1)` around the worker process; a caller that does not
get the slot within `MIP_QUEUE_WAIT_SECONDS` (25 s) gets `None` and the usual
rounded, `approximate` LP plan. Do not put the semaphore in `_run_mip` (tests
replace it).

**HiGHS can ignore its time limit (found 2026-11-05).** Production is aarch64
with one CPU. With integer column bounds in the billions (3.0B of listed
Tritanium), HiGHS sat in `HighsRedcostFixing::addRootRedcost` for minutes on an
input x86 solves in 0.3 s: nginx returned 504, and the thread kept a CPU until
the service restarted. Two guards, keep both: `mineral_supply_lp.
_useful_portions` lowers each bound to what the list needs, and the integer solve
runs in a killable process (`eve_trader/mip_worker.py`,
`MIP_PROCESS_DEADLINE_SECONDS`). A list needing over about 2B of one mineral can
still get a bound that large; the killable process covers that. A killed or
failed solve returns the LP solution rounded up, flagged `approximate` and shown
as such. A solve that stops at its time limit with a whole-unit solution
(worker `status` 1) keeps that solution, also flagged `approximate`. `tests/conftest.py` runs this solve in-process,
`tests/test_mineral_supply_lp.py` tests the real worker.

**The trigger is a large finite integer bound, not a missing one.** On emulated
arm64 one real reaction model solved with no bound and with bounds up to 2.0B,
and hung at 2,147,483,646 and above. Whether this is a 32-bit limit in HiGHS is
not proven, and a large bound alone does not always hang. Do not add finite bounds
to integer columns as a precaution: bounds derived from demand made `production/
reaction_capacity.py` hang and slowed its solves, so its integer columns stay
unbounded on purpose. The integer bounds in `pi/` are small. Keep any finite integer
bound under 2B, and replay a bound change in a `linux/arm64` Python container
before shipping it; x86 does not show the hang.
