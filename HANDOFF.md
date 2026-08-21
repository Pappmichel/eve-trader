# HANDOFF — Issue #32–#41 triage plan

Written 2026-08-21. Read this first if you're picking up this repo in a new
session (possibly on a different machine, with none of the previous
session's chat history or local Claude memory available) — it captures a
plan the user asked to have saved for exactly that case. Delete this file
once the plan below is fully executed (merged + deployed) and confirmed with
the user, per this repo's own HANDOFF.md convention (see CLAUDE.md).

## Status as of writing

Labels and existing milestones have already been applied on GitHub to all
ten issues (see table below) as part of triage. **No code has been changed
yet** — this file is the plan, not a record of work done. The previous
batch (issues #4, #5, #12, #13, #14, #15, #16, #17, #18, #19, #20, #21) is
already fully merged, deployed to the real production server, and closed —
unrelated to this file, don't re-do it.

## Standing constraints for this repo (see CLAUDE.md for full detail)

- Deploy target is the real Oracle Cloud VM (`92.5.11.10`), never localhost.
  SSH keys live in `.ssh-local/` (gitignored): `eve-trader-oracle` (VM
  access), `eve-trader-deploy` (GitHub deploy key on the VM, needs
  `GIT_SSH_COMMAND='ssh -i ~/.ssh/eve-trader-deploy'`). Full procedure in
  `deploy/README.md`.
- Frontend is always built **locally** (`cd frontend && npm run build`),
  then the `dist/` folder is `scp`'d to the VM — never built on the VM
  itself (low-memory box).
- Branch → implement → test (`pytest`, full suite must stay green) →
  commit → push → `gh pr create --base dev` → merge → fast-forward local
  `main`/`dev` to `origin/dev` → push `main` → deploy → live-verify via
  `curl` with the real IP as `Host` → tell the user what to check online →
  **wait for their explicit go-ahead** before the next one.
- Known gotcha: `gh pr merge --merge` titles the merge commit after the PR
  title. If that title contains a GitHub closing keyword + issue number
  (e.g. "Fix #15: ..."), the *later* `git push origin main` (fast-forward)
  auto-closes that issue the moment it lands on the default branch (`main`)
  — even if a same-numbered second issue in a combined title like
  "Fix #16, #17: ..." only closes the first one. Not a bug, just how
  GitHub's keyword parser behaves — expect it, don't be surprised by it.
- Known Windows quirk: `npm run build`'s `rm -rf dist` step sometimes hits
  `EPERM`/`Device or resource busy` (Dropbox sync lock). Just delete `dist`
  manually (`rm -rf dist` in Bash) and rerun `npm run build` — works on retry.
- Delete feature branches (local + `origin`) once merged — this repo has no
  long-lived feature branches by convention, only `main`/`dev` persist.

## The ten issues, categorized

No hard cross-issue dependencies exist in this batch (unlike the previous
one, where #14 depended on #4/#20, etc.) — ordering below is by risk/
confidence, not topology. Two *soft* sequencing notes only: do #40 before
#38 (so the new margin numbers already reflect BPC-copy costs).

### Tier 1 — small, well-scoped bugfixes, ready to implement now

- **#37** "contract history showing wrong contracts" (`bug, data-integrity`,
  milestone "Doctrine Tool"). `eve_trader/doctrine/esi_sync.py`'s
  `_passes_history_filter` / the `history_rows` loop (~line 313-329)
  requires only finished + ITEM_EXCHANGE + own structure — **no fitting-
  match requirement at all**. Any finished contract at the structure lands
  in `doctrine_contract_history`, even ones with `fitting_id=None`
  (never matched to any doctrine fitting). Fix: skip building a history row
  when `fitting_id is None`.

- **#32** "Assets in asset safety" (`bug, data-integrity`, milestone
  "Logistics & Asset Engine"). `storage.available_blueprint_copies` (added
  for issue #14, Invention Logistics) does **not** filter
  `NON_STOCK_LOCATION_FLAGS` (`storage.py` ~line 1503-1525), unlike
  `esi_stock_at_location`/`search_item_stock_locations` which both do. A
  blueprint copy sitting in Asset Safety counts as available for invention
  when it shouldn't. Fix: add the same `location_flag NOT IN (...)` filter
  used by `esi_stock_at_location` (requires adding `location_flag` to the
  SELECT/WHERE — check whether `character_blueprints`/`corp_blueprints`
  already store it; they do, per `docs/phase1_schema.sql`).

- **#33** "not wanted items on market status" (`bug, data-integrity`,
  milestone "Logistics & Asset Engine"). `production/engine.py`'s
  `market_status()` (~line 1614) iterates every row of
  `storage.load_stock_targets()` unconditionally — items with
  `home_target`/`jita_target` both null/0 (i.e. no actual market target,
  just an internal backup-stock item) still show up. Fix: only include rows
  where `home_target` or `jita_target` is actually set.

### Tier 2 — need a quick clarification/live-check before writing code

- **#35** "inactive state on shortlist" (`bug, data-integrity`). Likely
  cause: the 30-day skip-grace-period auto-deactivation
  (`SKIP_STREAK_DECISIONS` in `eve_trader/actions.py`, ~line 409-514) —
  low-liquidity categories (drugs/boosters) may repeatedly read "No market
  data"/"Skip" from the live ESI order-book stats (thin books, not the same
  thing as "not profitable") and get auto-deactivated even though they're
  genuinely still worth trading less frequently. **Before writing any
  fix**: query `shortlist_skip_streak` + `shortlist` for a concrete affected
  item (e.g. a specific booster/drug the user named) to confirm this really
  is the mechanism, then decide with the user whether the fix is a longer
  grace period for certain categories, a different threshold, or excluding
  some categories from the cap entirely.

- **#36** "stockpile in doctrine" (`bug, data-integrity`, milestone
  "Doctrine Tool"). `doctrine/validation.py`'s `build_stockpile_soll`
  (~line 56-70) multiplies required quantities only by `stockpile_target`
  — it has no awareness of `contract_target`/`valid_contracts` at all, so
  items needed to *create more outstanding contracts* (not just top up the
  spare-stock buffer) never show as required in the Stockpile page. **Ask
  the user for the exact desired formula** before implementing — plausible
  candidate: `required = stockpile_target + max(0, contract_target −
  valid_contracts)`, but confirm rather than assume.

- **#41** "no export costs in margin" (`bug, data-integrity`). Surprising
  finding: `production/engine.py`'s `margin_jita` (~line 595-605) **already**
  subtracts `cfg.haul_cost_per_m3 × packaged_volume` as an export cost, and
  both `discover_ship_margins`/`_scan_ship_margins` (list view) and
  `item_margin_detail` (single-item search) call it — so the obvious fix
  already appears to be implemented in current code. Before touching
  anything: sit down with the user and a concrete item (ship name + the
  number they see on the Margin page vs. what they expect), to figure out
  whether this is (a) already fixed and the issue is stale, (b) a different
  cost component actually meant (e.g. a Jita broker fee, not haul cost), or
  (c) some other gap not yet found by reading the code alone.

### Tier 3 — larger, independent feature/architecture work

- **#34** "sde data refresh in admin tool" (`architecture, feature`). SDE
  data is global/shared across all tenants, but `do_refresh_sde` /
  `POST /production/sde/refresh` (`production/actions.py` line ~71,
  `api/routers/production.py` line ~248) is exposed per-tenant in
  Production, with the trigger button in `ProductionLayout.tsx`'s sidebar
  (~line 111-116). Move the action + endpoint + button to
  `eve_trader/admin.py` / `api/routers/admin.py` / `AdminPage.tsx` (matches
  this repo's existing cross-tenant-superadmin pattern, see CLAUDE.md's
  "Tool permissions & Admin" section). While in there, do a quick audit for
  any other action that's similarly global-but-currently-per-tenant-exposed.

- **#39** "Unwanted character slots" (`enhancement`, milestone "Logistics &
  Asset Engine"). `character_slots` table (`docs/phase1_schema.sql`) has no
  exclusion flag today. Needs: (1) a migration adding an
  `excluded_from_planning BOOLEAN NOT NULL DEFAULT FALSE` column, (2)
  `storage.replace_character_slots` changed from delete+reinsert to an
  UPSERT that preserves the flag across every ESI re-sync (same
  COALESCE-preserving pattern `set_cached_structure_name` already uses for
  `solar_system_id` — copy that shape), (3) a new storage toggle function,
  (4) `production/jobs.py`'s `character_slot_overview`/
  `production/engine.py`'s `_free_slots_by_category` filtering excluded
  characters out of the slot pool, (5) the same exclusion applied wherever
  `plan_asset_optimized`'s slot-splitting reads free slots, (6) a checkbox
  added to `frontend/src/pages/production/Slots.tsx`'s pivoted per-character
  row (that page currently renders a raw Mantine `<Table>` with a two-row
  grouped header, deliberately left off the DataTable migration in issue
  #15's follow-up PR #31 — adding a checkbox column doesn't require
  reversing that decision, just adding one more `<Table.Td>`).

- **#40** "second table on blueprints" (`feature`). New functionality, the
  most novel item in this batch: some buildable items require a blueprint
  *copy* that isn't owned/inventable and must be bought outright (e.g. a
  faction/officer BPC from an LP store or the market) — its purchase cost
  should amortize per run into that item's build cost. Needs: a new
  tenant-scoped storage table (type_id, purchase cost, included run count),
  CRUD storage functions + `do_*` actions + router endpoints, a second table
  on the Blueprints page (`frontend/src/pages/production/Blueprints.tsx`,
  currently a single `DataTable` per Blueprints.tsx read during this
  session's investigation) to add/edit/remove entries, and
  `production/engine.py`'s `_unit_cost` (~line 472) incorporating
  `purchase_cost / included_runs` into the modeled cost for any type_id with
  a registered manual entry.

- **#38** "Margin in buildlists" (`enhancement`). Add a `margin` field
  (reuse the existing `margin_home` from `production/engine.py` ~line 584 —
  **not** `margin_jita`; per CLAUDE.md, "Production sells only at C-J, never
  Jita", so the buildlists' own margin must be the C-J one, not the
  informational Jita comparison the standalone Margin page also shows) to
  both `BuildJobEntry` (constructed at `engine.py` ~line 1219,
  `frontend/src/api/types.ts` ~line 161) and `AssetPlanJob` (constructed at
  `engine.py` ~line 1570, `types.ts` ~line 225), plus a new column in both
  `frontend/src/pages/production/BuildList.tsx` and `AssetPlanList.tsx`. Do
  this *after* #40 so the margin numbers already reflect any registered
  BPC-copy costs, not before.

## Recommended order

1. Tier 1 (#37, #32, #33) — bundle into one branch or three small PRs, low
   risk, no clarification needed, same deploy workflow as every previous
   issue this session.
2. Tier 2 (#35, #36, #41) — resolve the open question with the user for
   each (live-data check, formula confirmation, live-check respectively)
   *before* writing code for any of them.
3. Tier 3, in this order: #34 → #39 → #40 → #38.

## What's already been asked of the user / answered

Nothing yet — the user asked to categorize + plan, then asked to save this
plan for later/cross-machine continuation. No Tier 2 answers have been
collected yet. No Tier 1 branch has been started yet. The very next action
in a fresh session should be: confirm with the user whether to start Tier 1
immediately, or address the Tier 2 questions first.
