# Reaction capacity plan

Status: decisions confirmed and reviewed against production data
(2026-10-27), not implemented yet.

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
| D10 | Chaining (gap 7) | Shown per case: for every reaction whose inputs are short at the window the planner computes both options, "buy the missing inputs now and run it fully" and "run what the stock allows, the rest waits one cycle", with the extra ISK of buying next to the delay. The user picks per product; the default is the cheaper option. |
| D11 | Formulas (gap 5) | Owned originals limit jobs per recipe. The optimizer may also buy formulas at their buy price when that pays back within `reaction_formula_payback_cycles` cycles (setting, default 1). Bought formulas appear on the Buy List. |
| D12 | Window date (gap 8) | New settings `reaction_window_next` (date and time of the next window) and `reaction_window_interval_days` (default 14). Slots whose reaction jobs end after the window start are not free; output of jobs ending before it counts as on hand. Without a date set, all slots count as free. |

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
- `formulas[r]`: owned originals of the recipe's formula (`character_blueprints`
  + `corp_blueprints`, `quantity = -1` rows plus the stack size of rows with a
  positive quantity; copies, `-2`, never count).
- `price[m]`: `pricing.buy_price` for every material and formula. Inputs
  without a price and without a recipe in the layer (fuel blocks) use
  `cost_memo`, the planner's best buy-or-build unit cost.
- `slots`: D2 minus busy slots (D12).

**Variables:** `runs[r] >= 0` (continuous), `jobs[r]` (integer >= 0),
`new_formulas[r]` (integer >= 0), `buy[m] >= 0`, `short[p] >= 0` (unmet
demand, penalised; keeps the model feasible).

**Constraints:**
- Inputs at the window: `sum_r in[r,m] * runs[r] <= on_hand[m] + buy[m]`.
  Output of this window cannot feed this window (gap 7).
- Coverage per reaction product `p`:
  `sum_r out[r,p] * runs[r] + on_hand[p] + incoming[p] + buy[p] + short[p]
  >= demand[p] + sum_r in[r,p] * runs[r]`.
  Units consumed by this window come out of `on_hand`; units produced refill
  stock for the next window and for components.
- Job size: `runs[r] <= fit[r] * jobs[r]`.
- Slots: `sum_r jobs[r] <= slots`.
- Formulas: `jobs[r] <= formulas[r] + new_formulas[r]`.
- Manual Buy: `jobs[r] = 0` for that product's recipes. Manual Build:
  `buy[p] = 0`; if the slots cannot cover it, a penalised slack on the slot
  row keeps the model feasible and the result carries a warning.
- No price: `buy[m] = 0`.

**Objective:** minimise `sum_m price[m] * buy[m] + sum_r fee[r] * runs[r] +
sum_r price[formula r] * new_formulas[r] / reaction_formula_payback_cycles +
penalty * sum_p short[p]`.

With spare slots the solver fills them with alchemy where alchemy is cheaper
per unit, so the per-product split falls out of the solve (D5). Runs are
rounded up per recipe afterwards; if that breaks a job's `fit`, the job count
grows by one only when a slot is free, otherwise the run is dropped and the
unit bought.

**Chaining comparison (D10).** For every reaction whose input constraint is
binding, a second solve fixes `buy[m] = 0` for its short reaction inputs
("wait"). The result shows both totals; the user's choice per product is
stored (`reaction_chain_choice`, per tenant and type, `buy` / `wait`, missing
= the cheaper one) and fed back as that fixed bound.

**Output:** per recipe the jobs, runs per job, units and formulas to buy;
per product the units bought; plus the slot and formula shadow prices (ISK
one more slot or one more formula of that kind would save per window).

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
  `reaction_window_interval_days`, `reaction_formula_payback_cycles`; next to
  them the derived slots, the slots busy at the window and the resulting
  capacity.
- Build List header: "Reactions: 197 of 199 jobs, window 2026-11-03 18:00",
  the value of one more slot, and the formulas worth buying.
- Build List reaction rows: jobs x runs per job, recipe (normal / alchemy),
  formulas owned / needed.
- Buy List rows that exist only because of the capacity get a "capacity" badge
  with the ISK given up; formula purchases get their own badge.
- Chaining table (D10): per affected reaction the two options with ISK and
  delay, and the choice.
- Component slot-days per cycle shown next to the reaction figure (D8).

## 6. Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Fix gap 4: alchemy rows get their unit cost (per "Unrefined X" unit, from `_alchemy_unit_cost`) and margin. Independent of the switch. | Build List shows cost for alchemy rows; test. |
| 1 | `reaction_capacity.py`: formula counting, window inputs and the pure solver (`solve(layer, window) -> plan`), no planner wiring. Unit tests on small synthetic layers: spare slots -> all built, alchemy added; tight slots -> weakest per slot-day bought first; formula limit and formula purchase; job size `fit`; no chaining inside a window; byproduct credit; manual overrides; busy slots; infeasible -> warning. | Tests green. |
| 2 | Solver prototype run on the server against the real layer (read-only script) before wiring, to check runtime and that the result is plausible (jobs, purchases, shadow prices). | Numbers reported to the user. |
| 3 | Wire the solution into `_expand_all` and `plan_asset_optimized` behind `reaction_capacity_enabled`. Tests: both lists agree; switch off reproduces today's plans exactly. | Tests green; switch off unchanged. |
| 4 | Settings, chaining choice storage (schema), API fields, Build/Buy List UI from section 5. | Browser check. |
| 5 | Test server with a copy of the production stock targets: compare jobs, purchases and total cost with today's plan; then enable on production. | Numbers reported to the user. |

## 7. Notes
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
