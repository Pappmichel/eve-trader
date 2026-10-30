# Reaction capacity plan

Status: phases 0, 1, 1b, 1c, 2, 3 and 4 done (2026-10-27). The phase 2 run
against production data (section 1.4) found that the single-window model never
builds intermediates; the model is now two windows (D13, D14, section 3).
Phase 1b reworked the solver; the two-window re-run (section 1.5) surfaced five
fixes done in phase 1c; the final re-run (section 1.6) passed. Phase 3 wired
the solver into both Build Lists behind `reaction_capacity_enabled`. Phase 4
added settings, chaining-choice storage, API fields and the Build/Buy List UI.
Phase 4b (2026-10-30, review) fixed an infeasible model on uncoverable W1
demand, tightened the MIP gap, added an LP-rounding fallback and the stock
carry value (D15, `docs/REACTION_SLOT_PROFIT_PLAN.md` section 8).
W2 demand now comes from own sales instead of the backlog (D16).
`reaction_capacity_enabled` is already on for the Default tenant; phase 5
(comparison numbers for the user) is still open.

Trigger: the user starts reactions (and T2/capital components) once every 14
days, when the component system's cost index is very low (0.14 %). Reactions
run 13 days so there is one day left to haul the output to the manufacturing
system. Components start on the same day but finish within a few days, after
which the manufacturing slots build end products. The question was whether the
reaction slots (about as many as manufacturing slots) cover the demand.

Goal: the planner decides what to start at the next **reaction window**. It
uses the reaction slots and formulas that exist, buys reaction products only
when those are not enough, and uses alchemy (cheaper per unit, far slower per
slot) only for as much as still fits. Which products are bought or switched to
alchemy follows from ISK saved per slot-day, decided by the planner instead of
by hand.

## 1. Current state

### 1.1 How the planner decides today
- `_unit_cost` (`production/engine.py`) prices every item as
  `min(buy, build)` per unit. For a Reaction product with an "Unrefined X"
  formula (`alchemy_reactions_enabled`) it also prices the alchemy recipe and
  keeps the cheaper one (`alchemy_memo`).
- `_buy_or_build_decision` turns that into an all-or-nothing Build/Buy per
  type id (or a manual override). `_expand_all` walks the bill of materials
  breadth-first and queues every run the decisions imply. `plan_asset_optimized`
  makes the same calls, so both Build Lists agree.
- Nothing in that chain knows how many slots or formulas exist, or when a job
  can start. Slots appear only after the decision: `plan_asset_optimized`
  splits ready runs over free slots (`_free_slots_by_category`,
  `_allocate_slots_min_makespan`) and caps runs per slot at
  `asset_plan_slot_days_target`.

### 1.2 Measured on production (Default tenant, 2026-10-27)
Settings: component system M3-KAQ, manufacturing system IAK-JW, reaction and
component cost index override 0.14 %, Tatara T1 for reactions,
`asset_plan_slot_days_target` 13, `component_overbuild` 0.7, alchemy on. 21
characters, none excluded: 192 manufacturing and 199 reaction slots.

| Stage | Slot-days in the plan | Capacity per 14-day cycle |
|---|---|---|
| Reactions | 11,104 (7,935 of it alchemy) | 199 x 13 = 2,587 |
| Components | 564 (longest run 1.7 h) | about 3 days on 192 slots |
| End products | 985 | about 1,900 after components |

- Alchemy is 20x slower per unit for the same intermediate: the eight alchemy
  rows need 7,935 slot-days; the normal recipes for the same units need 579.
- Without alchemy the reaction demand is about 3,750 slot-days, still about
  1,160 over one cycle.
- ISK saved versus buying, per reaction slot-day, ranges from 131 M (Ferrogel)
  to 0.4 M (Sulfuric Acid). Buying the nine weakest products frees about 1,200
  slot-days and gives up about 3.1 B of savings.
- **One job per slot per window**: counted per product as
  `ceil(runs / runs that fit in 13 days)`, the plan needs 874 jobs, against
  854 slots from the slot-day total. The difference is small today, but it
  is real.
- **Formulas**: each running job locks one Reaction Formula original. Owned
  originals (character + corp blueprints) limit parallel jobs. Examples:
  Sylramic Fibers needs 26 jobs and has 20 formulas, Titanium Carbide 23 and
  15, Caesarium Cadmide 8 and 5. **No alchemy formula is owned**, yet the plan
  queues 316 Unrefined Hexite jobs.
- Formulas are cheap: 10-20 M in Jita (alchemy formulas 10 M). One formula
  carries up to 13 slot-days per cycle, so it pays back within one cycle for
  every product that saves more than about 1.5 M per slot-day.
- **No chaining inside a window**: the composite reactions' intermediate
  inputs on hand cover only 63 % of Photonic Metamaterials, 71 % of Ferrogel,
  85 % of Phenolic Composites and 87 % of Sylramic Fibers. The plan assumes
  the rest is reacted in the same window, which is impossible: an intermediate
  started at the window is ready at day 13.
- The plan is a backlog (183 of 371 targets below target), not a per-cycle
  rate.

### 1.4 Phase 2 prototype run (production, 2026-10-27, PR #330 solver)
The reactions started on 2026-10-24 (ending 2026-11-03 to 11-07) already cover
today's shortfall and alchemy has been switched off, so the run used the full
steady-state demand of the stock targets (`_base_runs`), with the alchemy
recipes offered anyway: 60 products, 77 recipes (17 alchemy), 199 slots.

| | A: real stock | B: empty reaction stock |
|---|---|---|
| One MIP | 1.2 s | 1.3 s |
| Full `solve` (shadow prices + chaining probes) | 101 s | 144 s |
| Jobs used | 138 / 199 | 199 / 199 |
| Bought | 0.8 B | 110 B |
| Intermediate jobs (Hexite, Ferrofluid, ...) | **0** | **0** |
| Slot shadow price | 0 | 20.7 M |

Findings:
1. **Intermediates are never reacted.** With no chaining inside a window,
   nothing in the single-window model needs an intermediate made now: in A,
   61 slots stay idle; in B, about 60 B of intermediates are bought (Neo
   Mercurite 15.9 B, Ferrofluid 11.4 B, ...) while no intermediate job runs.
   The model would buy every intermediate forever. Fixed by D13.
2. **"Wait" is not waiting.** Fixing the input buy to 0 makes the solver buy
   the composite itself, so the comparison always shows "buy". Fixed by D13
   (wait = run in the next window) and D14.
3. **Too slow for a plan refresh**: one probe MIP per recipe for the formula
   shadow prices plus one per chaining comparison.
4. **Formula shadow prices are always empty**: the base cost uses the trimmed
   formula purchases, the probes the untrimmed ones, so a probe never looks
   cheaper.
5. Busy slots also count jobs of characters excluded from planning.

### 1.5 Phase 2 re-run (two-window solver, 2026-10-27)
Same production layer as 1.4 (60 products, 77 recipes, 199 slots), after the
phase 1b two-window rewrite. Intermediates are now reacted in W1 for W2
composites, as intended. The demand is the full stock-target backlog at empty
stock (e.g. 48.6 M Sylramic Fibers) — a stress test for the optimizer, not a
realistic cycle; the real comparison waits on phase 5 when the solver is wired
into the planner.

| | A: real stock | B: empty reaction stock |
|---|---|---|
| Full `solve` | (see notes) | 11.9 s |
| W1 jobs | intermediates (Hexite, Neo Mercurite, Ferrofluid, Titanium Chromide, …) and composites | 199 / 199 |
| Bought now | 19.8 B | 135 B |
| W2 preview | uses all 199 jobs | (full) |
| Slot shadow price | — | 76.2 M |
| MIP status | HiGHS Status 13: Time limit reached | same |

Findings (fixed in phase 1c):
1. **MIP hits the 10 s time limit** — `jobs2` was integer, doubling the
   integer variables. The plan says W2 is a continuous preview; only `jobs1`
   and `new_formulas` stay integer. Also set `mip_rel_gap=0.005` and report
   time-limit / gap status in warnings.
2. **W1 slot slack used** on the time-limited incumbent ("Reaction jobs
   exceed W1 slot capacity") although slots are a hard limit. Without Manual
   Build, slack is treated as failure and the model is re-solved with slack
   bound at 0.
3. **Chaining not like for like** — `buy_now_cost` was the rounded W1
   objective, `wait_cost` a continuous LP objective, so "ISK saved by
   waiting" was largely negative (impossible: defer only relaxes). Both sides
   now use the continuous LP; `isk_saved_by_waiting = max(0, base - wait)`.
4. **`total_cost` dropped W2-feeding purchases** — `_recompute_buy1` rebuilt
   W1 needs only. Report `w1_cost` (fees, buy1 incl. W2 feeds, formulas) and
   `w2_preview_cost`; MIP `buy1` that feeds W2 stays on the W1 buy list.
   *Superseded 2026-10-28:* buying in W1 or W2 costs the same, so the MIP put
   purchases only W2 needs onto today's Buy List (test server: 0 % on hand
   for Photonic Metamaterials). Only W1's own needs are bought now; the rest
   moves to the W2 preview purchases and `w2_preview_cost`.
5. **Formula dual** — an extra owned formula relaxes both window rows, so
   the dual is `d1 + d2`, not `max(d1, d2)`.

### 1.6 Final re-run after phase 1c (2026-10-27)
Same layer and scenarios as 1.5.

| | A: real stock | B: empty reaction stock |
|---|---|---|
| Full `solve` (incl. shadow prices and chaining) | 4.1 s | 3.7 s |
| W1 jobs | 199 / 199 | 199 / 199 |
| W1 cost (fees, purchases, formulas) | 40.2 B | 159.5 B |
| W2 preview cost | 15.2 B | 32.0 B |
| Slot shadow price | 104.8 M | 77.8 M |
| Warnings | none | none |

- No time limit hit, no slot slack, `isk_saved_by_waiting` never negative.
  The largest saving from waiting is Ferrogel (0.14 B in A, 1.6 B in B);
  everything else is a few hundred million or less, so the buy default (D10)
  is reasonable.
- W1 runs intermediates (Neo Mercurite, Crystallite Alloy, Platinum
  Technite, ...) next to composites; the W2 preview runs the composites.
- Formula shadow prices equal the formula price / payback (about 20 M) for
  every bought formula: correct by design, but not very informative in the UI.
- Two runs on the same data differ slightly in purchases (36.8 B vs 37.2 B)
  because of `mip_rel_gap` 0.005.

### 1.3 Gaps
1. **No capacity constraint.** The planner builds every reaction whose unit
   cost beats the buy price, however many slot-days that takes.
2. **Alchemy is chosen per unit cost only.** It wins on materials, so it takes
   the whole product even when the slots are full.
3. **All or nothing per product.** A product is either fully built or fully
   bought; "build what fits, buy the rest" is not expressible.
4. **Alchemy rows have no unit cost** in the Build List (`unit_build_cost` is
   read from `cost_memo` under the "Unrefined X" id, which only holds the
   target's entry), so the list shows no cost or margin for them.
5. **Formulas are ignored.** Jobs per recipe are not limited by owned
   formulas, and recipes without any formula are planned.
6. **Slot-days are not jobs.** A slot runs one job per window, so capacity is
   whole jobs of at most `reaction_cycle_days`, not a pool of slot-days.
7. **No timing.** Reaction inputs are treated as available whenever they are
   produced; within one window they are only available if on hand at the start.
8. **Busy slots are counted as free.** Reaction jobs still running at the
   next window block their slots.

## 2. Decisions (confirmed with the user 2026-10-27)

| # | Question | Decision |
|---|---|---|
| D1 | Is the capacity per window or for the whole backlog? | Per window. Everything that does not fit this window is bought now, also when the backlog is larger than one cycle (no spreading over several cycles). |
| D2 | Which slots count? | Reaction slots of characters not excluded from planning (`character_slots`), minus slots still busy at the window (D12). Optional manual override for the slot count (`reaction_slot_override`, empty = derived). |
| D3 | Runtime per slot? | Adjustable, not fixed: Production setting `reaction_cycle_days` (default 13, range 1-30), edited on the Settings page. Every capacity figure in this plan uses it; 13 days is only today's value. |
| D4 | What is optimized? | Minimum total cost of the reaction layer (bought products, bought inputs, job fees, formula purchases) subject to slots and formulas. This is the same as "build what saves most per slot-day first". |
| D5 | Alchemy | A second recipe per product, mixed freely with the normal one per product ("anteilig"). It only gets slot time that the normal recipes do not need more urgently. |
| D6 | Manual Build/Buy overrides | Respected: a manual Buy is never built; a manual Build is always built, even if that exceeds the capacity (shown as a warning). |
| D7 | Switch | `reaction_capacity_enabled`, default off. Off = exactly today's behaviour. |
| D8 | Components | Shown, not constrained (564 of about 2,500 slot-days today). The plan reports component slot-days per cycle so a constraint can be added later. |
| D9 | Overbuild buffer (`component_overbuild`) | Counts as real demand: the buffer on reaction products competes for slot time like any other demand and is bought when it does not fit. |
| D10 | Chaining (gap 7) | Shown per case: for every product whose demand this window needs bought reaction inputs (or the product itself), the planner shows "buy now" against "wait": the demand moves to the next window and is made there from this window's intermediates. Shown: ISK saved by waiting and the units delayed by one cycle. The user picks per product. **Default is buy (D1)**; revised 2026-10-27, because waiting has no cost of its own and "the cheaper option" would always wait. |
| D11 | Formulas (gap 5) | Owned originals limit jobs per recipe. The optimizer may also buy formulas at their buy price when that pays back within `reaction_formula_payback_cycles` cycles (setting, default 1). Bought formulas appear on the Buy List. Formulas needed only by W2 jobs are **bought now** together with W1's (confirmed 2026-10-27). |
| D12 | Window date (gap 8) | New settings `reaction_window_next` (date and time of the next window) and `reaction_window_interval_days` (default 14). Slots whose reaction jobs end after the window start are not free; output of jobs ending before it counts as on hand. Without a date set, all slots count as free. |
| D13 | Intermediates for the next window (finding 1.4.1) | The solver plans two windows together: the next one (W1, executed and shown as the plan) and the one after (W2, a preview). Intermediates reacted in W1 can feed composites in W2. Only W1's jobs and purchases are acted on; the next plan run starts again from the stock it finds. |
| D14 | Demand of the second window | W2 demand = W1 demand x `reaction_next_window_demand_factor` (setting, default 1.0 = the next cycle needs the same again). What is built too much stays in stock and is netted by the next plan. |
| D15 | Cost of using owned stock (2026-10-30, `docs/REACTION_SLOT_PROFIT_PLAN.md`) | Owned raw inputs (moon materials, fuel blocks) left after W2 are credited at 99 % of their buy price up to what one more cycle needs (`carry_need`), so using stock the next cycle needs costs its replacement price; stock above that need is free. Reaction products get no credit (refilling them would earn credit for output the slot rule values at 0). Setting `reaction_stock_carry_value`, default on. |

## 3. Model

One mixed-integer program per window over the **reaction layer**: every
Reaction product the plan reaches, plus their inputs. Solved with
`scipy.optimize.linprog(integrality=...)` (HiGHS), as in `pi/chain_plan.py`
and `refining/optimizer.py`. The layer has about 35 products and 45 recipes,
so the integer part is small.

**Inputs** (all from the existing plan context, no new ESI calls):
- `demand[p]`: units of reaction product `p` the non-reaction part of the plan
  needs, after stock netting and with the overbuild buffer (D9). This covers
  components and end products.
- `on_hand[m]`: physical stock at the window per reaction product and input
  (`_stock_on_hand` plus reaction jobs that end before the window, D12).
  This is what reactions of this window can consume.
- `incoming[m]`: everything `_current_stock` counts beyond `on_hand`
  (jobs ending after the window). It covers demand, never inputs of this window.
- Per recipe `r` (normal and, if enabled, alchemy) of product `p`: seconds per
  run (`_activity_mods` time multiplier x blueprint time), job fee per run
  (EIV x `_job_cost_rate`), inputs per run (`_material_qty` with the reaction
  ME), outputs per run (normal: product quantity; alchemy: the reprocessed
  yield from `_alchemy_yield`, target plus byproducts),
  `fit[r] = floor(reaction_cycle_days x 86400 / seconds[r])` runs per job.
- `carry_need[m]` (D15): one more cycle's use of every raw input, the W2
  product demand expanded through the normal recipes.
- `formulas[r]`: owned originals of the recipe's formula (`character_blueprints`
  + `corp_blueprints`, `quantity = -1` rows plus the stack size of rows with a
  positive quantity; copies, `-2`, never count).
- `price[m]`: `pricing.buy_price` for every material and formula. Inputs
  without a price and without a recipe in the layer (fuel blocks) use
  `cost_memo`, the planner's best buy-or-build unit cost.
- `slots`: D2 minus busy slots (D12).

**Two windows (D13).** `w = 1` is the next window, `w = 2` the one after.
`incoming1[m]` is output of running jobs that ends after W1 starts but before
W2 starts; `incoming2[m]` ends later. `demand1[p]` is `demand[p]`;
`demand2[p] = demand[p] x reaction_next_window_demand_factor` (D14).

**Variables** per window `w`: `runs[r,w] >= 0` (continuous), `buy[m,w] >= 0`;
`jobs[r,1]` integer >= 0 (executed); `jobs[r,2] >= 0` continuous (W2 preview);
once: `new_formulas[r]` (integer >= 0, owned from W1 on, also covers W2 jobs,
D11); per product `defer[p] >= 0` (W1 demand moved to W2, only for products
set to "wait", D10) and `short[p,w] >= 0` (penalised, keeps the model
feasible).

Stock at the start of W2, per material (a linear expression, not a variable):
`S2[m] = on_hand[m] + buy[m,1] + incoming1[m] + sum_r (out[r,m] - in[r,m]) * runs[r,1]
- (demand1[m] - defer[m])`.

**Constraints:**
- Inputs at W1: `sum_r in[r,m] * runs[r,1] <= on_hand[m] + buy[m,1]`.
  Output of a window cannot feed the same window (gap 7).
- Coverage of W1 demand: `S2[p] + short[p,1] >= 0` for every reaction product,
  i.e. the W1 demand (minus deferred units) is covered by stock, W1 output,
  output of jobs ending before W2, and purchases.
- Inputs at W2: `sum_r in[r,m] * runs[r,2] <= S2[m] + buy[m,2]`. This is
  where W1 intermediates feed W2 composites.
- Coverage of W2 demand: `S2[p] + incoming2[p] + buy[p,2] + sum_r (out[r,p]
  - in[r,p]) * runs[r,2] + short[p,2] >= demand2[p] + defer[p]`.
- `defer[p] <= demand1[p]`, and `defer[p] = 0` unless the product's choice is
  "wait" (D10 default buy).
- Job size: `runs[r,w] <= fit[r] * jobs[r,w]`.
- Slots: `sum_r jobs[r,1] <= slots1`, `sum_r jobs[r,2] <= slots2`
  (`slots2` = all slots of non-excluded characters minus jobs still running
  at W2 start, or the override).
- Formulas: `jobs[r,w] <= formulas[r] + new_formulas[r]` for both windows.
- Manual Buy: `jobs[r,w] = 0` for that product's recipes. Manual Build:
  `buy[p,w] = 0`; if the slots cannot cover it, a penalised slack on the slot
  row keeps the model feasible and the result carries a warning.
- No price: `buy[m,w] = 0`.
- Carry value (D15): `keep[m] <= min(carry_need[m], on_hand + incoming1 +
  incoming2)` and `keep[m] <=` stock left after W2 without shorts.
- The W2 input row includes `short[p,1]` (S2 contains it); without it an
  uncoverable W1 demand made the model infeasible.

**Objective:** minimise, over both windows,
`sum_{m,w} price[m] * buy[m,w] + sum_{r,w} fee[r] * runs[r,w] +
sum_r price[formula r] * new_formulas[r] / reaction_formula_payback_cycles +
penalty * sum short - 0.99 * sum_m price[m] * keep[m]`. W2 is costed in full: W1 runs whose output only pays off
in W2 (intermediates) compete fairly with buying in W2.

With spare slots the solver fills them with alchemy where alchemy is cheaper
per unit, so the per-product split falls out of the solve (D5). Runs are
rounded up per recipe for W1 only (W2 stays a continuous preview); if that
breaks a job's `fit`, the job count grows by one only when a slot is free,
otherwise the run is dropped and the unit bought.

**Chaining comparison (D10).** For each product whose W1 demand needs bought
reaction inputs or a bought product, compare the continuous LP objective with
no defer (same basis as the shadow-price LP) against one with `defer[p] =
demand1[p]`. ISK saved by waiting is `max(0, base - wait)`; the deferred units
are the delay. The user's choice per product is stored
(`reaction_chain_choice`, per tenant and type, `buy` / `wait`, missing = buy)
and sets the `defer` bound in the main solve.

**Shadow prices** come from the duals of the continuous LP relaxation (not one
probe MIP per recipe): the slot value is the objective drop at `slots1 + 1`;
each formula's value is the sum of its W1 and W2 formula-row duals (one owned
formula relaxes both), or the formula price / payback when buying is cheaper.
Runtime target: the whole `solve` under 5 s on the production layer
(`mip_rel_gap` 1e-4 and `mip_abs_gap` 100,000 ISK since 2026-10-30, time
limit 10 s; time-limit status is reported). A solve with shorts or slot
slack is repeated with those fixed and priced at 0, so penalties do not
widen the gap. When the MIP stops early, the LP rounded to whole jobs
replaces the incumbent if it is cheaper.

**Output:** W1 per recipe the jobs, runs per job, units and formulas to buy;
per product the units bought now (including purchases that feed W2);
`w1_cost` and `w2_preview_cost`; the W2 preview (jobs per recipe, purchases);
the slot and formula shadow prices; the chaining table.

## 4. Integration

1. **Collect** (`production/reaction_capacity.py`, new): run the existing walk
   down to the reaction boundary and record `demand`. `_expand_all` gets a flag
   that stops at Reaction products and returns their pooled demand instead of
   expanding them; stock netting above the boundary is unchanged.
2. **Solve** once per plan context, cached on `_PlanContext`, so
   `plan_production`, `plan_asset_optimized` and the byproduct pre-pass all
   use the same result.
3. **Apply in the Build List**: the reaction rows come straight from the
   solution (jobs, runs, recipe), the bought reaction products and formulas go
   to `buy_totals`, and the inputs the runs consume (moon materials, fuel
   blocks) go back into the walk as material demand, so fuel blocks are still
   planned as manufacturing jobs. The alchemy byproduct overlay
   (`_alchemy_byproduct_stock`) uses the solved alchemy runs.
4. **Apply in the Asset-Optimized list**: its reaction jobs take the solved
   jobs and runs per job directly (`recommended_slots` = jobs,
   `runs_per_slot` = runs per job) instead of `_allocate_slots_min_makespan`.
   Readiness stays as today: inputs not on hand and not on the Buy List are
   blockers.
5. **Window settings** (D12) feed `on_hand`, `incoming` and `slots` from the
   industry jobs already synced (`_esi_incoming_industry_qty`, job end dates).

Known approximation: the margin gate (`min_margin`) and the component
build/buy decisions still use the uncapped unit costs. A component whose
reaction inputs end up bought could become cheaper to buy as a whole. A second
solve after re-pricing would fix that; it is left out unless phase 4 shows it
matters.

## 5. UI

- Production Settings: `reaction_capacity_enabled`, `reaction_cycle_days`,
  `reaction_slot_override`, `reaction_window_next`,
  `reaction_window_interval_days`, `reaction_formula_payback_cycles`,
  `reaction_next_window_demand_factor`; next to
  them the derived slots, the slots busy at the window and the resulting
  capacity.
- Build List header: "Reactions: 197 of 199 jobs, window 2026-11-03 18:00",
  the value of one more slot, and the formulas worth buying.
- Build List reaction rows: jobs x runs per job, recipe (normal / alchemy),
  formulas owned / needed.
- Buy List rows that exist only because of the capacity get a "capacity" badge
  with the ISK given up; formula purchases get their own badge. A reaction
  product bought while W1 slots are still free gets "inputs" instead: its
  reaction inputs were not on hand at the window (D10), not the slots.
- Chaining table (D10): per affected product "buy now" vs "wait", the ISK
  waiting saves, the units delayed by one cycle, and the choice (default buy).
- Next-window preview (D13): the W2 jobs and purchases, marked as a forecast.
- Component slot-days per cycle shown next to the reaction figure (D8).

## 6. Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Fix gap 4: alchemy rows get their unit cost (per "Unrefined X" unit, from `_alchemy_unit_cost`) and margin. Independent of the switch. | Build List shows cost for alchemy rows; test. **Done (PR #330).** |
| 1 | `reaction_capacity.py`: formula counting, window inputs and the pure solver (`solve(layer, window) -> plan`), no planner wiring. Unit tests on small synthetic layers: spare slots -> all built, alchemy added; tight slots -> weakest per slot-day bought first; formula limit and formula purchase; job size `fit`; no chaining inside a window; byproduct credit; manual overrides; busy slots; infeasible -> warning. | Tests green. **Done (PR #330), single-window model.** |
| 2 | Solver prototype run on the server against the real layer (read-only script) before wiring, to check runtime and that the result is plausible (jobs, purchases, shadow prices). | **Done 2026-10-27, section 1.4.** |
| 1b | Rework the solver to the two-window model (D13, D14) and the revised D10; shadow prices from LP duals; formula shadow fix; busy slots skip excluded characters; `reaction_next_window_demand_factor` setting. Tests: an intermediate with spare W1 slots is reacted for W2 composites; "wait" runs the composite in W2 instead of buying; default buy; shadow prices non-empty and match a brute-force probe on a small layer; runtime guard on a 60-product synthetic layer. Then re-run the phase 2 script. | **Tests green (PR #330).** Two-window production re-run done (section 1.5). |
| 1c | Fixes from section 1.5: W2 continuous (no `jobs2` integrality) + `mip_rel_gap`; hard W1 slots; like-for-like LP chaining with `isk_saved >= 0`; `w1_cost` / `w2_preview_cost` keeping W2-feeding `buy1`; formula dual = `d1 + d2`; D11 W2 formulas bought now. | **Done; production re-run passed (section 1.6).** |
| 3 | Wire the solution into `_expand_all` and `plan_asset_optimized` behind `reaction_capacity_enabled`. Tests: both lists agree; switch off reproduces today's plans exactly. | Tests green; switch off unchanged. **Done.** |
| 4 | Settings, chaining choice storage (schema), API fields, Build/Buy List UI from section 5. | Browser check; frontend CI green. **Done.** |
| 4b | Review fixes (2026-10-30): W1 short in the W2 input row; gap 1e-4 + abs 100k ISK and penalty-free repeat; LP-rounding fallback; carry value D15; non-reaction incoming counted with a window date; asset-plan readiness claims shared inputs once. | Tests green. **Done.** |
| 5 | Test server with a copy of the production stock targets: compare jobs, purchases and total cost with today's plan; then enable on production. | Numbers reported to the user. |

## 7. Notes
- **Open: W2 demand is the backlog again.** `demand1` is the whole
  stock-target shortfall, not one cycle's use; after W1 the backlog is bought
  (D1), so W2 should only carry one cycle's consumption. With a large backlog
  W2 and `carry_need` are too high (see `docs/REACTION_SLOT_PROFIT_PLAN.md`
  8.3). Lower `reaction_next_window_demand_factor` until this is decided.
- Reaction skills: most characters have 10 slots, the maximum is 11 (Mass
  Reactions V + Advanced Mass Reactions V). That is up to 32 more slots. It does
  not affect the plan; the slot shadow price from section 3 shows what those
  slots would be worth.
- Job time uses the flat skill levels from `ProductionConfig` (Reactions 5).
  A character with a lower Reactions skill runs slower than planned.
- Formulas are counted wherever they are; whether they sit where the jobs are
  started (the Tatara, or a hangar the starting character can use) is not
  checked.
- Slots are pooled across characters. Personal formulas can only be used by
  their owner, so a large pool on one character can be less usable than the
  plan assumes.
