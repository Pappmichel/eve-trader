# Logistics scope toggle plan

Status: planned, not started (2026-10-24).

## Goal

The Production Logistics tab today answers one question: "what has to be at
each category station so that the **whole Build List** can be built". It reads
only the Build List cache (`_last_plan`, filled by `POST /plan/refresh`) and
never the Asset-Optimized plan (`_last_asset_plan`).

Add a toggle that switches the material tables to a second question: "what
has to be hauled to each category station to start the jobs that are
**ready now** in the Asset-Optimized plan". Default stays the current
behavior.

## Current state (for reference)

| Table | Endpoint | Input | Engine |
|---|---|---|---|
| Material per category | `GET /logistics` | `_last_plan.build_list` | `engine.logistics_status` |
| Distribution | `GET /logistics/distribution` | `_last_plan.build_list` | `engine.distribution_recommendations` |
| Market Restock | `GET /logistics/market-restock` | none (stock targets, assets, orders) | `engine.market_restock` |
| Invention | `GET /logistics/invention` | `_last_plan.invention_list` | `engine.invention_logistics` |

`logistics_status` and `distribution_recommendations` both go through
`_category_material_demand`, which only reads `type_id`, `job_category`,
`decryptor` and `job_runs` of each entry. `AssetPlanJob` carries the same
four fields, so the engine functions work on asset-plan jobs once the run
count is set to the ready-now value.

## Decisions

- **D1 - Two scopes, default unchanged.** `scope = "build_list"` (default,
  today's behavior) and `scope = "asset_ready"`.
- **D2 - Which runs count as "ready now".** Per `AssetPlanJob`:
  - `recommended_slots` is set (Reactions / Advanced Components / Capital
    Components): `recommended_slots x runs_per_slot`. That is what the asset
    plan actually tells you to queue on the free slots now. Ready runs that
    do not fit on free slots wait for the next round, like on the Asset plan
    page.
  - otherwise: `runs_ready_now`.
  - Jobs with 0 resulting runs are dropped.
  - Open question for the user, see below.
- **D3 - Only the two material tables follow the toggle.** Material per
  category and Distribution switch. Market Restock is plan-independent and
  stays as is. Invention stays on the Build List's `invention_list`: the
  asset plan has no invention list, and invention demand is a buffer target,
  not a "ready now" job set. The page says so next to the toggle.
- **D4 - Stock netting stays the same.** Station stock is still read with
  `_stock_at_location(..., exclude_intake_at_location_id=home)`. The asset
  plan's readiness uses `_stock_on_hand` across all locations, so in
  `asset_ready` mode every listed material exists *somewhere*, and the
  table shows what still has to be moved to the station. The pull-from hint
  and the distribution phases are unchanged.
- **D5 - Stale plan.** If `_last_asset_plan` is flagged `stale` (D9 of the
  special orders plan), the page shows the same recompute hint as the Asset
  plan page. The data still renders.
- **D6 - Logic placement.** The run selection (D2) is business logic, so it
  lives in the engine (`engine.asset_plan_ready_jobs(jobs)`), called from the
  `do_*` actions. The router only picks the cache and passes the scope.

## Backend changes

1. `eve_trader/production/engine.py`
   - New `asset_plan_ready_jobs(jobs: list[AssetPlanJob]) -> list[AssetPlanJob]`:
     returns `dataclasses.replace(job, job_runs=ready)` per D2, drops zero.
   - Widen the type hints of `_category_material_demand`,
     `logistics_status` and `distribution_recommendations` to
     `Sequence[BuildJobEntry | AssetPlanJob]`. No behavior change.
2. `eve_trader/production/actions.py`
   - `do_get_logistics_status(build_list=None, asset_jobs=None)` and
     `do_get_distribution_recommendations(...)` the same way: exactly one of
     the two is passed; with `asset_jobs` the action calls
     `asset_plan_ready_jobs` first. Raise `ActionError` if neither is given.
3. `eve_trader/api/routers/production.py`
   - `GET /logistics` and `GET /logistics/distribution` take
     `scope: Literal["build_list", "asset_ready"] = "build_list"`.
   - `asset_ready`: read `_last_asset_plan`; if it is missing, HTTP 400
     "No asset-optimized build list computed yet. Run 'Refresh Asset Build
     List' first." An empty `jobs` list is a valid state (nothing ready), not
     an error - return `[]`.
   - Response models unchanged (`LogisticsRow`, `DistributionRow`).
4. CLI: there is no Logistics command today. None is added.

## Frontend changes

1. `frontend/src/api/client.ts`: `logisticsStatus(scope)` and
   `distributionRecommendations(scope)` pass `?scope=`.
2. `frontend/src/pages/production/Logistics.tsx`
   - `SegmentedControl` at the top: "Full build list" / "Ready now
     (Asset-Optimized)". Persist the choice in `localStorage` with try/catch,
     same pattern as `Portfolio.tsx`'s period/tab keys.
   - Query keys get the scope: `['production', 'logistics', scope]` and
     `['production', 'logistics', 'distribution', scope]`, so both caches
     coexist and the prefix-based invalidations in this file keep working.
   - Empty/error hints per scope: no Build List vs. no asset plan (link to
     the Asset plan tab), and "No jobs are ready to start right now" for an
     empty `asset_ready` result.
   - Stale hint from `GET /asset-plan`'s `stale` flag in `asset_ready` mode.
   - Small note under the toggle: Market Restock and Invention do not depend
     on it.
3. `frontend/src/pages/production/AssetPlanList.tsx`: add
   `['production', 'logistics']` to `refreshAssetPlan`'s invalidation list,
   so the Logistics tab does not show an old asset plan after a refresh.

## Tests

- `tests/test_production_engine.py`
  - `asset_plan_ready_jobs`: slot-capped job, job without slot
    recommendation, job with 0 ready runs dropped, `recommended_slots = 0`.
  - `logistics_status` with asset-plan jobs: needed scales with the ready
    runs, not `job_runs`.
- `tests/test_api_routers.py`
  - default scope still reads `_last_plan` (regression).
  - `scope=asset_ready` without an asset plan -> 400; with an asset plan with
    no jobs -> `[]`; with jobs -> the action receives `asset_jobs`.
  - invalid scope -> 422.
- Full `pytest` green.

## Live verification

Per CLAUDE.md: hit `GET /api/production/logistics?scope=asset_ready` and
`.../distribution?scope=asset_ready` on the running backend after
refreshing both plans, and compare a handful of rows against the Asset plan
page's ready runs. Load the tab in Playwright, switch the toggle both ways,
check console/network errors, then delete the throwaway script and
screenshot.

## Open question for the user

D2: should "ready now" for Reactions / Advanced Components / Capital
Components be capped to the free-slot recommendation
(`recommended_slots x runs_per_slot`, recommended), or use all
`runs_ready_now` regardless of free slots?
