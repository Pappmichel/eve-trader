# Real freight from courier contracts - plan

Status: **Phase 1 built** (2026-10-18). Decisions F1-F7 stay as confirmed.
Phases 2 and 3 are not started.

Phase 1 assigns a shipment rate in the pure costing engine and loads the
couriers that rate comes from. A finished courier that ends at the home
structure becomes a shipment (any start station, so a non-Jita hub works).
Couriers with the same issuer, start and end whose issue times span at most
2 hours from the first courier in the group are one shipment. The rate is
`sum(reward) / sum(volume)`. `shipment_ref` is those contract ids in issue
order, comma-separated. Each market buy and contract buy with frozen m3
takes the first shipment issued at or after it that starts at the purchase
station; anything else keeps the frozen configured rate. A failed courier
warns with its collateral and sets no rate. Expired and deleted couriers
are ignored. Layers and draws carry `freight_source` (`courier` /
`configured`), `shipment_ref` and the applied rate. `CostComponents` splits
`freight` into `freight_courier` and `freight_configured` (the split is not
added into `total`; coverage still excludes freight). ST3 still zeros all
three freight fields on a station-pool unit used at the station. The
archive freight columns are not rewritten. `COSTING_VERSION` stays 4.

Failed couriers are now archived (same row, no items) so the warning can
fire on a rebuild. Expired and deleted couriers are still not archived.
Nothing is persisted on the derived tables yet (Phase 2). The monthly
Forge-to-home courier comparison is unchanged until Phase 2 replaces it.

Tests: `pytest -q` 3304 passed, 4 skipped. New pure cases cover a purchase
before, between and after shipments, two couriers one minute apart, a
purchase by someone other than the issuer, a contract buy, a station no
courier starts from, a failed courier, an expired courier, ST3 on a courier
rate, and a full-chain regression with no couriers (every layer, draw and
result number). Postgres: revoking the contracts share changes the
owner-scope fingerprint and the next rebuild uses the configured rate;
failed couriers are stored and expired ones are not.

Live check (local Postgres, throwaway tenant, seed deleted afterwards):
the six section-2 couriers form five shipments. The 10-03 pair is
`236650353,236650362` at 800.040406 ISK/m3. A purchase after 2026-10-12
06:58 UTC stays at the configured 900. A purchase at a station no courier
starts from stays at its frozen rate. A failed courier warns
`collateral 1500000000` and sets no rate. Log:
`/opt/cursor/artifacts/phase1-courier-live.log`.

### Deviations
- The checkout is LF (`.gitattributes` forces LF only for shell scripts).
  Edited files stay LF so the diff does not rewrite every line.
- `CostComponents.freight_courier` / `freight_configured` are extra fields
  the plan did not name. Phase 2 needs them so a produced sale can split
  mixed freight. They are not part of `total`.
- Assignment uses the home structure as the only end filter, from any start
  station. Section 3 already says that. The old comparison route is still
  Forge-only until Phase 2.
Builds on `docs/REALIZED_PROFIT_UNIFIED_PLAN.md` (U4: freight from the hub
table) and the Production plan's D9 (freight is the configured rate and is
labeled that way).

## 1. Goal

Today every hub purchase freezes `rate x packaged m3` at ingest, with the
rate taken from the hub freight table. The tenant actually pays haulers per
courier contract. Realized profit should carry what the courier contracts
really cost, and fall back to the configured rate (labeled as such) only
where no courier exists yet.

## 2. What the test server shows (Default tenant, 2026-10-18)

All couriers are issued by **jason Andven** (92267134, buyer). After his
token got the contracts scope and contracts were shared with Production, one
sync archived his contracts (`ledger_contracts`, type `courier`, already
handled by the archive since the Production plan's Phase 5):

| Courier | Issued | Route | Volume m3 | Reward | ISK/m3 | Collateral | Hand-over after delivery |
|---|---|---|---|---|---|---|---|
| 236378813 | 09-25 06:32 | Jita 4-4 -> C-J | 349,971 | 315 M | 900 | 18 bn | 236388749 (same m3) |
| 236650353 | 10-03 12:16 | Jita 4-4 -> C-J | 349,975 | 280 M | 800 | 21 bn | 236658044 (494,975 m3 = both 10-03 couriers) |
| 236650362 | 10-03 12:17 | Jita 4-4 -> C-J | 145,000 | 116 M | 800 | 2 bn | (in 236658044) |
| 236703860 | 10-04 21:10 | Jita 4-4 -> C-J | 69,062 | 56 M | 811 | 13 bn | 236721720 (same m3) |
| 236859676 | 10-09 16:20 | Jita 4-4 -> C-J | 349,895 | 280 M | 800 | 9 bn | 236878551 (same m3) |
| 236959145 | 10-12 06:58 | Jita 4-4 -> C-J | 349,977 | 280 M | 800 | 21 bn | 236966436 (same m3) |

Facts that shape the design:

- **ESI returns no items for a courier contract** (checked live: `[]` for
  two of them). Which purchases a courier carried is not in ESI.
- The reward is also in the issuer's wallet journal as
  `contract_reward_deposited` with `context_id` = contract id (6 entries,
  1.327 bn). That works without the contracts scope, but has no volume.
- **Purchases between two couriers match the shipment.** Jason's Jita 4-4
  buys after the previous courier and up to this courier's issue, per type,
  against the item hand-over that follows the delivery: 19/36, 97/112,
  70/74, 56/60, 58/71 types with exactly the same quantity. The window's
  frozen packaged m3 is close to the courier volume (e.g. 356,940 vs
  349,971 for the first one).
- Two couriers issued a minute apart (10-03) are one shipment: one
  hand-over carries both volumes.
- After delivery Jason hands the goods to pappmichl5 with a 0-ISK item
  exchange at C-J. Both are tenant characters, so costing already skips it
  as an internal transfer. It is useful as a check, not as a cost.
- Today every Jita 4-4 buy has 900 ISK/m3 frozen (4,789 rows, 1.40 bn). The
  couriers cost 1.327 bn for about 1.48 M m3 (~898 ISK/m3 on average), but
  per shipment 800-900. About 80,600 m3 were bought after the last courier
  and have no courier yet.

## 3. Approach

A **shipment** is one or more finished courier contracts that start at a hub
station and end at the home structure, issued by a tenant owner whose
contracts are shared with a realized-profit tool, grouped when issued within
a short gap (F2). A shipment's rate is `sum(reward) / sum(volume)`.

Each hub purchase (market buy or contract buy) at that start station gets
the rate of the **first shipment issued at or after the purchase** (F1).
Purchases after the last shipment keep the configured rate and are labeled
"configured rate (no courier yet)"; the next rebuild after the next courier
moves them over. Freight stays `rate x frozen packaged m3`, so a purchase's
m3 is unchanged; only the rate changes.

This happens in the loader at rebuild time, not at ingest: at ingest the
courier does not exist yet. The ingest keeps freezing the configured rate as
today (the fallback). The archive is not rewritten.

Per shipment the report shows a reconciliation: reward vs freight booked
on the purchases it was assigned (sum of rate x m3). A large gap means
purchases that were not shipped (left at the hub, sold there, station
trading) or a shipment that carried goods not bought in that window.

## 4. Decisions (confirmed)

**F1 Assignment of purchases to a shipment.**
- *Confirmed:* first shipment issued at or after the purchase, from the
  purchase's station. Simple and matches the data (section 2).
- Alternative: allocate each shipment's total reward over its window's
  purchases by m3, so booked freight equals the reward exactly. Wrong
  whenever a window holds goods that stay at the hub or the courier carries
  goods bought earlier; the per-m3 rate does not have that problem.
- Alternative: one rolling average rate per month. Robust but hides that a
  shipment cost 900 instead of 800.

**F2 Grouping couriers into one shipment.** *Confirmed:* same issuer,
same start and end, issued within 2 hours of each other.

**F3 Which purchases a shipment can cover.**
- *Confirmed:* purchases by **any** tenant owner at the courier's start
  station. The buyer and the courier issuer may differ.
- Alternative: only the issuer's own purchases. Exact for today's data
  (Jason buys and ships), wrong for a setup where several buyers share one
  hauler.

**F4 Sharing.** Couriers are read from the `contracts` kind. That kind is
consumed by Production and Doctrine only, so a Trading-only tenant cannot
share couriers. *Confirmed:* add `trading` to the contracts consumers
(`esi_data/registry.py`, `realized/access.KIND_TOOLS`). The Trading report
does not show contract sales of its own from this (they stay as today).

**F5 Couriers that did not finish.** *Confirmed:* only `finished`
couriers make a shipment. A `failed` courier is a warning with its
collateral (the goods are gone, the collateral is income); `expired` /
`deleted` are ignored (the reward was refunded).

**F6 Couriers without a contract row** (older than ESI's contract window, or
issued before the scope was granted), known only from
`contract_reward_deposited`. *Confirmed:* ignore for the rate, list them
in the reconciliation as "reward without courier details". A guessed volume
would be a guessed rate.

**F7 Labels and coverage.** *Confirmed:* freight keeps not lowering
coverage (D9). Each draw/layer carries `freight_source` = `courier` (with
contract id and rate) or `configured`. The UI says "Courier 236959145 · 800
ISK/m3" or "Configured rate (no courier yet)". A courier rate is a real
cost, so the summary's freight line splits into "from couriers" and
"configured".

## 5. Changes

- `realized/db.py`: list finished couriers for the shipment scope (rows are
  already archived: `volume`, `reward`, `collateral`, `start_location_id`,
  `end_location_id`, `date_issued`, `status`).
- `realized/costing.py` (pure): `Shipment` dataclass in `LedgerSnapshot`;
  `assign_shipment_rates(purchases, shipments)` returns per purchase
  `(rate, source, contract_ids)`; `_on_buy` / contract buy use it instead of
  the frozen rate when a shipment covers the purchase. `CostLayer` /
  `LayerDraw` get `freight_source` and `shipment_ref`.
- `realized/loader.py`: load shipments, group (F2), pass them in; persist
  `freight_source` / `shipment_ref` on `prod_cost_layers` and
  `prod_cost_draws` (idempotent `ADD COLUMN IF NOT EXISTS` in
  `docs/production_ledger_schema.sql`). `COSTING_VERSION` stays (the
  opening quantity does not change).
- Reports: freight split on every tool's summary; the Ledger tab's courier
  comparison (`GET /api/production/realized/courier`) becomes a shipment
  list with reward, volume, rate, booked freight and the gap; the cost
  chain shows the shipment on a bought draw.
- `do_reapply_freight_rate` stays for the configured fallback and its
  dialog says that courier-covered purchases are not affected.
- Docs: CLAUDE.md price/cost notes ("Freight is the configured rate"
  becomes "the courier rate, else the configured rate"), the Production
  plan's D9 note, this plan's status.

## 6. Phases

**Phase 1 - shipments and rates (backend).** db read, grouping, pure
assignment with tests: purchase before/after/between shipments, two
couriers one shipment, other buyer (F3), failed/expired courier, a purchase
at a station no courier starts from, contract buy. Regression: without any
courier the results are identical to today.

**Phase 2 - persistence and reports.** Columns, labels, freight split,
shipment list with reconciliation, frontend (Ledger tab, cost chain,
overview freight line). Vitest for the new table.

**Phase 3 - sharing and test server.** F4 registry change, docs. On the
test server: rebuild, then check per shipment that booked freight is close
to the reward (section 2 numbers), total freight drops from 1.40 bn to
about 1.33 bn for the covered purchases, and purchases after 10-12 stay on
the configured rate.

## 7. Risks

- A courier that carries goods bought long before (stock that sat at the
  hub) gives those goods the rate of a later shipment. The rate per m3 is
  still the real price of hauling, so the error is small.
- Goods bought at the hub and never shipped (sold there, station trading,
  used in a hub job) get a courier rate they never paid. With a Station
  Trading wallet share, a station-pool unit sold at the station already
  drops freight (ST3); other cases stay a known limit, visible in the
  reconciliation gap.
- A hauler's contract that is not issued by a tenant owner (someone else
  ships for the tenant) is invisible; those purchases stay on the
  configured rate.
