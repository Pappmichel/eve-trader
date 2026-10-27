# Reaction capacity plan

Status: proposal (2026-10-27). Nothing here is implemented.

Trigger: the user starts reactions (and T2/capital components) once every 14
days, when the component system's cost index is very low (0.14 %). Reactions
run 13 days so there is one day left to haul the output to the manufacturing
system. Components start on the same day but finish within a few days, after
which the manufacturing slots build end products. The question was whether the
reaction slots (about as many as manufacturing slots) cover the demand.

Goal: the Build List uses the reaction slots that exist, buys reaction
products only when those slots are full, and uses alchemy (cheaper per unit,
far slower per slot) only for as much as still fits. Which products are bought
or switched to alchemy follows from ISK saved per slot-day, decided by the
planner instead of by hand.

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
- Nothing in that chain knows how many slots exist. Slots appear only after
  the decision: `plan_asset_optimized` splits ready runs over free slots
  (`_free_slots_by_category`, `_allocate_slots_min_makespan`) and caps runs per
  slot at `asset_plan_slot_days_target`.

### 1.2 Measured on production (Default tenant, 2026-10-27)
Settings: component system M3-KAQ, manufacturing system IAK-JW, reaction and
component cost index override 0.14 %, Tatara T1 for reactions,
`asset_plan_slot_days_target` 13, alchemy on. 21 characters, none excluded:
192 manufacturing and 199 reaction slots.

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

## 2. Decisions to confirm

| # | Question | Proposal |
|---|---|---|
| D1 | Is the capacity per cycle or for the whole backlog? | Per cycle: `reaction slots x reaction_cycle_days`. Everything that does not fit this cycle is bought. A backlog larger than one cycle is bought now rather than waiting for the next window. |
| D2 | Which slots count? | All reaction slots of characters not excluded from planning (`character_slots`, total, not "free now"): at the window every slot is free again. Optional manual override for the slot count. |
| D3 | Runtime per slot? | New setting `reaction_cycle_days`, default 13. |
| D4 | What is optimized? | Minimum total cost of the reaction layer (bought products + bought inputs + job fees), subject to the capacity. This is the same as "build what saves most per slot-day first". |
| D5 | Alchemy | A second recipe per product, mixed freely with the normal one per product ("anteilig"). It only gets slot time that the normal recipes do not need more urgently. |
| D6 | Manual Build/Buy overrides | Respected: a manual Buy is never built; a manual Build is always built, even if that exceeds the capacity (shown as a warning). |
| D7 | Switch | `reaction_capacity_enabled`, default off. Off = exactly today's behaviour. |
| D8 | Components | No capacity constraint now (564 of about 2,500 slot-days). The plan reports component slot-days per cycle so a future constraint can be added. |

## 3. Model

One linear program over the **reaction layer**: every Reaction product the
plan reaches, plus their inputs. Solved with `scipy.optimize.linprog`
(HiGHS), as in `refining/optimizer.py` and `module_reprocessing/
shopping_optimizer.py`.

**Inputs** (all from the existing plan context, no new ESI calls):
- `demand[p]`: units of reaction product `p` the non-reaction part of the plan
  needs. This means components and end products, after stock netting and with the overbuild
  buffer `_expand_all` already applies.
- `stock[m]`: what is left of `_current_stock` for every reaction product and
  input after the manufacturing part of the walk.
- Per recipe `r` (normal and, if enabled, alchemy) of product `p`: seconds per
  run (`_activity_mods` time multiplier x blueprint time), job fee per run
  (EIV x `_job_cost_rate`), inputs per run (`_material_qty` with the reaction
  ME), outputs per run (normal: product quantity; alchemy: the reprocessed
  yield from `_alchemy_yield`, target plus byproducts).
- `price[m]`: `pricing.buy_price` for every material. Inputs without a price
  and without a recipe in the layer (fuel blocks, for example) use `cost_memo`, the
  planner's best buy-or-build unit cost.

**Variables:** `runs[r] >= 0` per recipe, `buy[m] >= 0` per material,
`short[p] >= 0` per demanded product (unmet demand, penalised; keeps the model
feasible when an item has neither a price nor capacity).

**Constraints:**
- Balance per material `m`:
  `sum_r out[r,m] * runs[r] + buy[m] + stock[m] >= demand[m] + sum_r in[r,m] * runs[r] - short[m]`.
- Capacity: `sum_r seconds[r] * runs[r] <= slots * reaction_cycle_days * 86400`.
- Manual Buy: `runs[r] = 0` for that product's recipes. Manual Build:
  `buy[p] = 0` (if capacity cannot cover it, the capacity row gets a slack
  with a high penalty and the result carries a warning).
- No price: `buy[m] = 0`.

**Objective:** minimise `sum_m price[m] * buy[m] + sum_r fee[r] * runs[r] +
penalty * sum_p short[p]`.

Solved as a continuous LP: a reaction run is 1.4-2.8 h against a 13-day slot,
so rounding is negligible. Runs are rounded up per recipe afterwards. If
rounding pushes the total over the capacity, the run with the lowest saving per
slot-day loses one run until it fits. With spare capacity the LP fills it with
alchemy where alchemy is cheaper per unit, so the split per product falls out
of the solve (D5).

**Output:** per product the units built with the normal recipe, built with
alchemy, and bought; plus slot-days used, capacity, and the capacity's shadow
price (ISK one more slot-day would save) for the UI.

## 4. Integration

The LP result is applied as a **share per product** (normal / alchemy / buy),
the same interface for both Build Lists, so they still never disagree:

1. **Collect** (`production/reaction_capacity.py`, new): run the existing walk
   down to the reaction boundary and record `demand`/`stock`. This reuses
   `_expand_all` with a flag that stops at Reaction products and returns their
   pooled demand instead of expanding them.
2. **Solve** once per plan context, cached on `_PlanContext` (both planners and
   the byproduct pre-pass use the same result).
3. **Apply**: `_expand_all` and `plan_asset_optimized`'s `_run_rounds` read
   `ctx.reaction_split[p]` when they reach a Reaction product. Today they make one
   Build/Buy/alchemy choice there; with the split they divide the arriving
   quantity into normal runs, alchemy runs and buy units. The overbuild buffer
   on a product's materials scales with its built share.
   The alchemy byproduct overlay (`_alchemy_byproduct_stock`) keeps working
   unchanged on the resulting alchemy runs.
4. **Inputs outside the layer** (fuel blocks) that the LP's runs consume flow
   back into the normal walk as material demand, exactly as today.

Known approximation: the margin gate (`min_margin`) and the component
build/buy decisions still use the uncapped unit costs. A component whose
reaction inputs end up bought could become cheaper to buy as a whole. A second
solve after re-pricing would fix that; it is left out unless the numbers show
it matters.

## 5. UI

- Production Settings: `reaction_capacity_enabled`, `reaction_cycle_days`,
  optional reaction slot override; the derived slot count and capacity shown
  next to them.
- Build List header: "Reactions: 2,580 of 2,587 slot-days (13 d x 199 slots)",
  and the value of one extra slot-day.
- Build List reaction rows: share built normal / alchemy; Buy List rows that
  exist only because of the capacity get a "capacity" badge with the ISK given
  up.
- Component slot-days per cycle shown next to the reaction figure (D8).

## 6. Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Fix gap 4: alchemy rows get their unit cost (per "Unrefined X" unit, from `_alchemy_unit_cost`) and margin. Small, independent. | Build List shows cost for alchemy rows; test. |
| 1 | `reaction_capacity.py`: data collection and the pure LP (`solve(layer, capacity) -> split`), no planner wiring. Unit tests on small synthetic layers: spare capacity -> all built, alchemy added; tight capacity -> weakest per slot-day bought first; byproduct credit; manual overrides; infeasible -> warning. | Tests green. |
| 2 | Wire the split into `_expand_all` and `plan_asset_optimized` behind `reaction_capacity_enabled`. Test that both Build Lists agree and that the switch off reproduces today's plans byte for byte. | Tests green; switch off unchanged. |
| 3 | Settings, API fields, Build/Buy List UI from section 5. | Browser check. |
| 4 | Run on the test server with a copy of the production stock targets; compare slot-days and total cost against today's plan; then enable on production. | Numbers reported to the user. |

## 7. Open questions
- Should a backlog larger than one cycle be bought now (D1), or should the
  planner offer "spread over N cycles" (capacity x N, nothing bought) as an
  alternative? The LP supports both through the capacity value.
- `component_overbuild` adds a buffer to reaction demand. With a hard capacity
  that buffer costs real slot time. Should the capacity apply to the buffer
  too, or should the buffer be the first thing dropped?
- Reaction skills: most characters have 10 slots, the maximum is 11 (Mass
  Reactions V + Advanced Mass Reactions V). That is up to 32 more slots
  (about 416 slot-days per cycle). It does not affect the plan; the shadow price
  from section 3 shows what those slots would be worth.
