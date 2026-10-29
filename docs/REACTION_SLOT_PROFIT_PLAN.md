# Reaction slot profit

Status: proposal, not implemented (2026-10-29). Measurements below are
read-only runs against the Default tenant
(`00000000-0000-0000-0000-000000000001`). Nothing was written to the buy
list or to dynamic stock targets. This note sits beside
`docs/REACTION_CAPACITY_PLAN.md` and does not change that model.

Question: a slot is awarded by minimising purchases over two windows, and
on-hand inputs cost 0 in that sum. Is a job's profit better read as
"what the next stage would have paid for the output, minus what the inputs
would cost to buy"? When is that the right slot rule?

## 1. How a slot is awarded today

`reaction_capacity._solve_mip` minimises, over two windows, the sum of

- buy price times units bought,
- job fee per run,
- formula buy price divided by `reaction_formula_payback_cycles` (1 on this
  tenant, so the full price in the first window),
- a penalty large enough that unmet demand is a last resort.

`jobs1` and `new_formulas` are integer. `jobs2` stays continuous because
window 2 is a preview. Each job carries an extra 0.001 ISK so an empty job
is never free. There is no revenue term. A slot has no value of its own.
The slot-slack penalty (`1e15`) applies only when the plan exceeds the
slot cap.

The rules that shape the comparison, all from the capacity plan:

- Window 1 is executed. Window 2 demand equals window 1 demand times
  `reaction_next_window_demand_factor` (1 on this tenant). Output of a
  window feeds the next window only.
- A job holds at most `fit` runs. On this tenant's 13-day cycle that is
  222 runs for a normal reaction and 111 for alchemy.
- Jobs of one recipe cannot exceed owned formulas plus formulas bought
  now. A formula that only window 2 needs is bought now.
- An unpriced material or formula cannot be bought.
- On-hand stock, including output of jobs that finish before the window,
  enters as a quantity limit priced at 0. Owned formulas are free the
  same way.
- Alchemy is a second recipe in the same objective. It takes a slot when
  its output saves more ISK per slot than the recipe that would otherwise
  have the slot.

A product is bought when a slot on its recipe saves less than that slot
saves somewhere else, or when the recipe's own margin does not cover its
fee and formula. Demand the next stage does not have is not credited:
units beyond what the two windows need do not reduce the objective.

The reported slot shadow price is the continuous model's objective drop
from `slots` to `slots + 1`. It prices one more slot beyond a plan that
already fills the cap. It does not price an idle slot inside the cap.

HiGHS may stop at a feasible incumbent within `mip_rel_gap` 0.005
(0.5 percent) or after 10 seconds, and still report the solution as
optimal. On an objective of about 514 billion ISK, 0.5 percent is about
2.6 billion ISK.

## 2. What the runs showed

Settings on the tenant at the time: alchemy on, reaction capacity on,
cycle 13 days, window interval 14 days, next window 2026-11-07 21:00 UTC,
demand factor 1, formula payback 1, component overbuild 0.7, 199 reaction
slots. The 87 active reaction jobs all end before the window, so busy
slots were 0. Their output counts as on hand. Home structure
`1049588174021`. Chaining comparisons were stubbed in the diagnostic
scripts; that does not change the MIP allocation.

### 2.1 Real hangar

The solver filled 199 of 199 slots. One decision run (solver optimal,
slot shadow about 95.0 million ISK, window-1 cost about 24.1 billion)
gave Hexite 8 jobs. A later recompute of the same tenant gave Hexite 7
jobs (310,800 units). The count moves with the hangar. The 8-job run is
the one the balance below was taken from.

Hexite (type 16665) had no direct demand. Hangar 640,438. Eight full jobs
produce 355,200 (8 × 222 × 200). Window-1 consumers eat the hangar, not
that output: Sylramic Fibers 347,385.6 from 16 jobs and Ferrogel 86,846.4
from 4 jobs, together 434,232. Left for window 2: 206,206. Window 2 draws
561,406, which is 206,206 + 355,200 exactly. The 8 jobs exist to stop
window 2 buying Hexite.

Inputs of those Hexite jobs, and what else wanted them:

| Input | On hand | Window-1 use | Buy price used |
|---|---:|---:|---:|
| Nitrogen Fuel Block | 164,989 | 54,822 | ~17,605 |
| Chromium | 4,250,752 | 705,627 | ~5,721 |
| Platinum | 567,153 | 564,502 | ~8,249 |

Platinum is shared with Platinum Technite and Rolled Tungsten and is
essentially gone after window 1 (window-2 platinum use on those recipes
was 0). Chromium and fuel stay in surplus for several further cycles.
Hexite's own buy price was about 8,783 ISK. The fee was about 45,145 ISK
per run. Owned Hexite formulas: 25, so this run bought none.

Per Hexite job (44,400 units), under three ways of pricing the inputs:

| Inputs charged | Output at buy price | Fee | Inputs | Left |
|---|---:|---:|---:|---:|
| None (today's objective) | ~390 M | ~10 M | 0 | ~380 M |
| Platinum only | ~390 M | ~10 M | ~179 M | ~201 M |
| Fuel, chromium and platinum | ~390 M | ~10 M | ~322 M | ~58 M |

Fuel is about 19 million of that 322, chromium about 124 million,
platinum about 179 million.

Moving one of those Hexite jobs onto a product the plan was buying, with
window-2 jobs held fixed, raised the objective by about 300 to 390
million ISK (Nonlinear, Crystalline Carbonide, Promethium Mercurite,
Photonic, Ferrogel, Fernite, and others in that band). Taking one more
Hexite job from Titanium Carbide, the largest holder, raised it by about
161 million. Under stock-at-zero, that run's Hexite count was the cost
minimum, not a preference for Hexite as a top-ranked reaction. The same
run's "saved if inputs are free" ranking put Ferrogel near 2.7 billion
per job and Neo Mercurite near 1.6 billion, with Hexite near 380 million.
That ranking overstates any product whose inputs are shared or whose
output is not all needed.

Alchemy did take slots on this hangar: Hyperflurite alchemy, on the order
of 16 to 19 jobs, because the stock made it cheaper than buying
Hyperflurite. With the hangar emptied it took none (section 2.2).

### 2.2 Empty hangar

Counterfactual on the same tenant, same targets, same owned formulas.
Physical hangar and manual stock forced to 0. Industry jobs that finish
before the window, and manual incoming, still count. Running jobs
therefore still contribute their output. The buy list was not saved.

| | Real hangar | Empty hangar |
|---|---:|---:|
| Window-1 jobs | 199 / 199 | 197 / 199 |
| Alchemy jobs | 16–19 | 0 |
| Slot shadow (continuous, slots to slots+1) | ~95.0 M | ~55.2 M |
| Window-1 purchases | ~24 billion | ~264 billion |
| Formulas bought | ~61 (~1.0 billion) | 151 (~2.5 billion) |

Products that dropped to 0 jobs without the hangar include Titanium
Carbide (30), Hyperflurite alchemy (16), Titanium Chromide (15),
Sylramic Fibers (14), Rolled Tungsten (9), Fullerides, Vanadium Hafnite
and several smaller holders. The empty plan's largest holders were Neo
Mercurite 29, Caesarium Cadmide 27, Terahertz 24, Platinum Technite 23,
Plasmonic Metamaterials 19.

Hexite without the hangar: on hand 166,400 from jobs already finishing,
demand 0, 6 jobs producing 266,400, buy 72,428. The direct demand was
still 0. The jobs still exist for window 2, and part of the Hexite is
bought because six jobs do not cover it once the hangar is gone.

### 2.3 Why two slots stayed empty

On the empty-hangar window the continuous relaxation uses 199.000 jobs
worth of runs. The integer model returned 197, and rounding did not drop
any of them: every MIP job count matched the published plan. Inside those
197 jobs the only partial fills were Neo Mercurite (0.26 of a job short
of full) and Caesarium Cadmide (0.11). The two missing slots were unused,
not half-filled jobs. Slot slack was 0. The solver reported HiGHS status
7, optimal. The time limit was not hit.

Objectives on that same captured window:

| Solve | Jobs | Objective |
|---|---:|---:|
| Continuous, 199 slots | 199.00 runs/fit | 512.448 billion |
| Integer, gap 0.005 (what the app keeps) | 197 | 513.883 billion |
| Integer, gap 1e-8, same window | 199 | 512.756 billion |

The loose integer solution is 1.43 billion above the continuous bound and
1.13 billion above the tight integer solution. 1.13 / 514 is 0.22 percent,
inside the 0.5 percent stop. The shadow of about 55.2 million is the
value of a 200th slot in the continuous model, which already fills 199.
It is not evidence that slot 198 was worth leaving empty.

The tight solution's 1.13 billion comes from a different mix, not from
two extra full batches. It uses 199 integer jobs and only 196.2 job-equivalents
of runs, so several of those jobs are partial. Jobs that moved:

| Recipe | Loose gap | Tight gap |
|---|---:|---:|
| Phenolic Composites | 6 | 0 |
| Crystallite Alloy | 6 | 4 |
| Caesarium Cadmide | 27 | 30 |
| Neo Mercurite | 29 | 28 |
| Hexite | 6 | 5 |
| Photonic Metamaterials | 6 | 5 |
| Prometium | 5 | 6 |
| Hyperflurite (normal) | 5 | 6 |
| Plasmonic Metamaterials | 19 | 20 |
| Fullerene Intercalated Graphite, Graphene Nanoribbons, Hypnagogic Neurolink Enhancer, Axosomatic Neurolink Enhancer, Isotropic Neofullerene Alpha-3 | 1 or 2 | +1 |
| Meta-Operant Neurolink Enhancer, Sense-Heuristic Neurolink Enhancer | 0 | 1 |

The products that gain a job are the ones the continuous model wanted at
a fraction of a job more (about +0.2 to +0.6). The loose solve had spent
those slots on Phenolic, Crystallite, Hexite, Photonic and Neo Mercurite.

With the real hangar the same 0.5 percent rule already filled 199 of 199.
The idle slots showed up when almost everything was bought and the
objective sat near 514 billion, so 0.5 percent became an allowance of
about 2.6 billion ISK.

## 3. Input against output

Credit a job with the buy price of its output, in the quantity the next
stage would otherwise buy, and charge its inputs at buy price, plus the
fee, plus a formula if one must be bought. That difference is the job's
profit against buying the product.

For the Hexite job in section 2.1 the three rows are that comparison:

- Stock priced at 0, which is today's objective, leaves about 380 million.
- Every input at its buy price leaves about 58 million.
- The slot shadow next to those figures was about 95 million on the real
  hangar and about 55 million on the empty hangar.

A free slot should take the job with the highest profit per slot, and
only for units the next stage would buy. Above that quantity the profit
is 0. Alchemy uses the same comparison with half as many runs per job
(111 against 222), so the per-slot figure is what competes, not the
per-run figure. A full slot list still has to be solved together wherever
two recipes want the same input: platinum can sit in only one job's
58 million.

Along a chain the sum is the right total when every stage charges its
inputs at buy price. Hexite's profit plus Ferrogel's profit, with Hexite
inside Ferrogel priced at the buy price, equals the profit from raw
materials to the composite. Charging the produced Hexite at 0 inside
Ferrogel and also keeping Hexite's own profit counts the same saving
twice.

This is already what the solver does on an empty hangar, apart from the
integer gap in section 2.3. The gap between this comparison and today's
objective is the on-hand stock priced at 0.

## 4. When the buy price is the right input cost

The buy price of an input is the right opportunity cost when this job
forces the input to be bought again, or when the input would be sold
instead. It is too high when the stock would otherwise sit in the hangar.

Hexite on the real hangar splits cleanly:

- Platinum is gone after window 1, and window 2 does not use it on these
  recipes. Charging it at the buy price is right. The job's profit falls
  from about 380 million to about 201 million, which is still above the
  95 million slot shadow, so the slot stays.
- Fuel and chromium are not used up (165 thousand against about 55
  thousand used, 4.25 million against about 706 thousand used). Charging
  them as well drops the job to about 58 million, under the shadow. The
  solver would give the slot away even though this window neither sells
  that fuel and chromium nor replaces them. The avoided Hexite purchase
  is then lost.

A blanket "every input at buy price" is the right profit figure for a
reaction as a process, and the right ranking signal for an input that
will be replaced or sold. Applied to a surplus that sits, it undervalues
the job. A blanket 0, which is today, overvalues a job that consumes
something the next plan must buy back. Platinum is that case: the
380 million treats a repurchase as free.

The resale price, when it applies, is what the material would actually
sell for, net of hauling it back out. Materials already at the home
structure are not worth a full Jita buy price just because a buy price
exists.

## 5. Alternatives considered

**Tighter solver gap.** Same objective as today. On the empty hangar it
is worth about 1.13 billion ISK and it is what fills the two idle slots.
It does not change which economic trade the solver is making. It is
independent of the input-price rule. The 10-second limit was not the
reason on these runs; the relative gap was.

**Longer horizon.** A third window, with the same demand factor, lets an
intermediate compete when its buyer sits one cycle further out than
window 2. Hexite gets its slots today only because window 2 buys it.
Raising `reaction_next_window_demand_factor` above 1 invents demand the
build plan does not have. Decision D1 of the capacity plan still stands
for the backlog: what does not fit this window is bought now, not spread
over extra cycles. A third window would be a preview, the way window 2
already is.

**Formula payback above 1.** A 10 to 20 million formula must earn itself
inside the first window at payback 1. Recipes the tenant will keep
running, and does not yet own, lose slots they would win over a longer
payback. This only affects recipes that need a new formula. Hexite, with
25 owned, is unaffected.

**Second pass that manufactures for sale.** After the build plan is
covered, leftover slots ranked by sale price minus inputs, fee and
formula, above a minimum margin. The current objective cannot do this:
output nobody was going to buy has no credit. That pass is a different
business from covering the build plan. Sales of finished goods stay at
the home structure. It is out of scope for this note.

**Minimum value per slot.** Drop any job that saves less than the shadow
of the last filled slot, and buy the product. That uses fewer slots, each
with a higher saving. It is the wrong lever while 199 slots are free and
the plan can already fill them. It does not fix stock priced at 0.

**Greedy rank by ISK saved per slot.** The joint solve exists because
recipes share fuel, minerals and intermediates. Sorting recipes
independently double-counts a pile that only one job can consume.
Section 2.1's "saved if inputs are free" list is that mistake: it ranks
Hexite as though its platinum were not also Platinum Technite's.

## 6. Recommended rule

Keep the two-window minimisation. Change the cost of using on-hand
stock, not the meaning of a slot.

1. Output credit stays the avoided buy, and only up to the quantity the
   two windows would otherwise buy. Extra units stay worth 0.
2. Each input used from stock is charged one of three prices:
   - the buy price (landed replacement), when the two windows exhaust it
     and a later need would repurchase it;
   - the resale proceeds net of haul-back, when selling it is the
     alternative;
   - 0, when it would sit unused either way.
3. Purchases the plan already makes stay charged once, at the buy price.
   The stock charge is the opportunity cost of units already owned, not
   a second bill for units the plan buys.
4. Shared inputs stay inside the one solve. The per-job difference in
   section 3 is the figure the solve is maximising per slot, not a
   separate sort.
5. The 0.5 percent gap is a separate defect. Tightening it recovers the
   idle slots and the about 1.13 billion on the empty-hangar window
   without adopting this price rule. Both are worth doing. Neither
   replaces the other.

On the Hexite job that rule charges platinum and leaves fuel and chromium
at 0, so the job shows about 201 million against a 95 million slot
shadow and keeps the slot. Charging all three inputs would drop it below
the shadow and give the slot away.

## 7. Out of scope

- No sale-revenue term, and no second pass that reacts for the market.
- No change to decision D1: unmet demand of this window is still bought
  now.
- Alchemy stays a second recipe in the same objective.
- The buy list still must not ask, two weeks early, for a purchase that
  only window 2 needs. That split is independent of the stock price.
- This document does not retune `mip_rel_gap` and does not implement the
  three input prices.
