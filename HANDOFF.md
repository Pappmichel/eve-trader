# HANDOFF — Issues #45, #46, #51, #52 implementation plan

Written 2026-08-21 (new session, separate remote container — none of the
Tier 1-3 branches described further below exist here; `git branch` in this
session shows only `main` and `claude/issues-plan-8ovgwo`). This section is
the current, active plan. The original "#32–#41 triage plan" content below
is kept for history/cross-machine continuation on that separate, still-open
thread (Tier 3 branches only exist on the original machine) — don't conflate
the two; they're unrelated issue batches.

## Status: all four implemented, committed, and pushed (2026-08-21)

Working branch: `claude/issues-plan-8ovgwo`, pushed to `origin`. Order
agreed with the user: **#45 → #51 → #52 → #46**, implemented one at a time,
each with its own commit (`pytest`/`tsc`/`npm run build` green before every
commit). Plan comments were posted to all four issues before starting code
- see each issue's own comment for the exact write-up.

**#51 update**: the initial commit was a relabel/docs-only fix. The user
then pushed back ("umlabeln reicht nicht, dies ist ein echter bug da es
auch die profit/day berechnung zerschießt") - correct: `sell_volume`
(order-book depth) was feeding the actual "Profit / Day" multiplication,
not just a mislabeled column. Follow-up commit fixed it for real: new
`ShortlistRow.avg_daily_sold`, computed by
`trade_reconciliation.average_daily_sold_by_type` from the last Reconcile
Trades run's `realized_trades.matched_qty` (real matched sales) over
`cfg.lookback_days` - "Profit / Day" now multiplies by *that*, not
`sell_volume`. An item with no real sale ever matched shows "–" instead of
an estimate. **This needs a schema migration applied to the real deployed
Postgres DB before it ships** - `docs/phase1_schema.sql` has a new
`ALTER TABLE shortlist_snapshot ADD COLUMN IF NOT EXISTS avg_daily_sold
REAL;` (idempotent, safe to re-run the whole file) - without it,
`do_refresh_shortlist`'s `save_shortlist_snapshot` INSERT will fail once
deployed. Don't forget this step during deploy.

**Not yet reviewed, merged, or deployed** - per this repo's normal workflow
(see "Standing constraints" below), the next step is showing the user the
branch/diff and getting a go-ahead before push → PR → merge → deploy for
real (with the schema migration applied as part of that, see above). No
live-verify against the real running app was possible this session (no
Postgres available in this sandbox) - `pytest`/`tsc`/build are green, and a
throwaway Playwright check confirmed the #52 landing-page fix visually at a
375px viewport, but the data-populated table pages (#45/#46/#51's new
avg_daily_sold column) were not seen rendering live. Flag this explicitly
when handing back to the user.

Original plan (kept below for reference, all four are now done):

- **#45** "Volume and margin in unlisted stock" — smallest, self-contained.
  Both Trading (`do_check_seller_unlisted_stock` in `actions.py`,
  `UnlistedStockRow` in `models.py`, `pages/trading/UnlistedStock.tsx`) and
  Production (`do_unlisted_stock` in `production/actions.py`,
  `production/models.py`, `pages/production/UnlistedStock.tsx`) unlisted
  stock pages need margin + sell-volume columns added, computed the same way
  `shortlist.evaluate_shortlist_item`/`production/Margin.tsx` already do.

- **#51** "Sell volume in shortlist looks very high" — investigation already
  done: it is NOT a Jita-vs-C-J mixup (already sourced from C-J structure
  stats correctly). The real issue is that `esi_client.py`'s `sell_volume`
  is open-order-book remaining quantity, not actual daily turnover, while
  the UI/CLAUDE.md describe it as a daily figure. Planned fix: relabel/
  redocument only (not a data-source change) — pending confirmation with the
  user on whether the bigger "real turnover from history" rework is wanted
  too (flagged as optional/ask-first in the plan comment).

- **#52** "mobile version" — nav/shell already partially responsive
  (Mantine `AppShell` + `Burger`). Real scope is `DataTable.tsx`'s
  fixed-width columns needing a mobile column-visibility strategy across
  every table page, plus `Container size="xl"` shrinking on small
  viewports.

- **#46** "multiple buyer/seller in trading tool" — largest/architectural,
  done last. Reuse the existing multi-character pattern already used for
  Production (`get_token_interactive_multi`/`list_roles("producer")` in
  `auth.py`) for Trading's buyer/seller roles instead of the current
  single-fixed-role-key assumption; rework every `tm.get_token("seller")`/
  `("buyer")` call site in `actions.py` to iterate across all registered
  characters per role.

Update this section (or delete it once all four issues are implemented,
pushed, and confirmed live by the user) — don't let it go stale the way the
section below did.

---

# HANDOFF — Issue #32–#41 triage plan

Written 2026-08-21. Read this first if you're picking up this repo in a new
session (possibly on a different machine, with none of the previous
session's chat history or local Claude memory available) — it captures a
plan the user asked to have saved for exactly that case. Delete this file
once the plan below is fully executed (merged + deployed) and confirmed with
the user, per this repo's own HANDOFF.md convention (see CLAUDE.md).

## Status (updated 2026-08-21, after Tier 1 + Tier 2 + Tier 3 — ALL MERGED AND DEPLOYED)

**Tier 3 is now also fully merged and deployed** (PRs #47/#34, #48/#39,
#49/#40, #50/#38, in that order - #49 needed a small additive merge-conflict
resolution in `tests/test_storage_stock.py` against #39's own test additions,
same file-both-appended-at-the-end pattern as every other conflict this
session). All four issues are closed. All four feature branches were
deleted (local + remote) after merging - `git branch` should show only
`main`/`dev` now, matching this repo's normal convention.

**Not yet live-verified online by the user** - this was done in one
continuous run per explicit instruction ("weiter bis einschließlich
deploy... sofern keine Fragen oder Probleme aufgetaucht sind"), so unlike
Tier 1/2 there was no per-PR pause for online confirmation. Next step in a
fresh session: ask the user to verify all four online (Admin tool's new SDE
section; Production → Character Slots' new Excluded checkbox; Blueprints
page's new second table; Build List/Build List (Asset-Optimized) both
showing a Margin column).

**All ten original issues (#32-#41) from this triage plan are now closed**
except **#41, deliberately deferred** (see its own section below - stays
open, not being worked on). Once the user confirms Tier 3 live, this
HANDOFF.md's own job is done and it can be deleted per its own instruction
at the top of this file (only delete once the user has actually confirmed,
not preemptively).

**Two new issues appeared during this session, NOT part of this plan**:
#45 ("Volume and margin in unlisted stock") and #46 ("multiple buyer/seller
in trading tool"), both still open/untriaged. Don't confuse them with the
#32-#41 batch this file tracks - if picking this up fresh, ask the user
whether they want a new triage pass for these two before assuming they're
covered by anything above.

## Status (superseded, kept for history — was: "after Tier 1 + Tier 2, both user-verified live")

**Tier 1 is done, deployed, and confirmed working by the user.** #37, #32,
#33 were fixed in one bundled branch/PR (#42, merged to `dev`, fast-forwarded
to `main`, deployed to the real production server, service restarted and
verified via `curl`). The user then found a real follow-up bug in #33 while
verifying live: Market Status still showed items with an explicit `0` market
target (not just `NULL`) - e.g. the Griffin (`home_market_stock=0`,
`jita_market_stock=NULL`). Fixed in a second PR (#44): `market_status()` now
treats `0` the same as `NULL` (`not home_target and not jita_target`
instead of `is None`/`is None`). Live-verified directly against the
production DB afterward (Griffin no longer in the 217 remaining rows).
**Lesson for future similar filters**: a "was this ever configured" check on
a nullable numeric column needs to consider 0 as "not configured" too, not
just NULL - don't assume NULL is the only empty-state sentinel without
checking real data first.

**Tier 2 is done, deployed, and confirmed working by the user** (no
follow-up issues reported for #35/#36).

**Tier 2 is also done and deployed**: the user answered all three questions,
each with real live-data investigation behind it first (not blind
assumptions):
- **#35**: root cause turned out different from the original hypothesis -
  it's not that low-liquidity items are unfairly caught by the skip-streak,
  it's that `shortlist._decision` short-circuits to "Inactive" forever once
  `active=False`, with **no path back to active ever**, even once an item's
  real numbers (still computed regardless of active state, issue #6)
  recover. Confirmed live with concrete examples (Standard Crash Booster:
  132% margin, still Inactive). User confirmed the fix: immediate
  reactivation once an inactive item clears the same Import-bar gate used to
  deactivate it. Implemented in PR #43 (new `_items_to_reactivate` in
  `actions.py`, new `storage.activate_shortlist_items`).
- **#36**: user confirmed additive formula (`stockpile_target + max(0,
  contract_target - valid_contracts)`, not `max()`). Implemented in PR #43
  (`doctrine/validation.py`'s `build_stockpile_soll` gained two new optional
  params; `doctrine/engine.py`'s `stockpile_rows_for_doctrine` computes each
  fitting's `valid_contracts` and passes it through).
- **#41**: user said defer for now - **no code change**, just an
  investigation comment left on the issue (the live Ferox example showing
  `margin_jita` already deducts ~13.5M ISK export haul cost). Issue stays
  open, not being worked on unless the user revisits it.

PR #43 merged to `dev`, fast-forwarded to `main`, deployed to production,
verified via `curl`. Feature branches from both Tier 1 and Tier 2 were
deleted (local + remote) after merging. Issues #37, #32, #33, #35, #36 are
closed; #41 stays open (deferred, see above) - GitHub's closing-keyword
quirk (see below) meant #32/#33/#36 needed a manual `gh issue close` with an
explanatory comment since only the first number in a combined PR title
auto-closes.

**Both tiers are now live-verified by the user** (including the #33
follow-up above). Next step in a fresh session: move straight to Tier 3
(#34 → #39 → #40 → #38) - no outstanding confirmation needed from Tier 1/2.

**Tier 3 is fully implemented (all four issues) and committed - but
deliberately NOT pushed, NOT merged, NOT deployed yet.** The user explicitly
asked (2026-08-21, "nun tier 3 aber diesmal einzeln und jeweils nur bis zum
commit ohne nachfrage" - "now Tier 3 but this time individually and each
time only up to the commit, without asking") for a different workflow than
Tier 1/2: implement each of the four issues on its own separate local
branch, run its own full test suite + `tsc`/`npm run build`, commit locally,
then move to the next one immediately - no push, no PR, no merge, no
deploy, no waiting for a go-ahead between them. All four are done:

- `feature/34-sde-refresh-in-admin` (commit `a8e9d26`) - #34, SDE refresh
  moved from `production/actions.py`/`ProductionLayout.tsx` to
  `admin.py`/`AdminPage.tsx`'s new `SdeDataSection`.
- `feature/39-exclude-character-slots` (commit `b5a8aea`) - #39,
  `excluded_from_planning` column + UPSERT-preserving `replace_character_
  slots` + checkbox on `Slots.tsx`.
- `feature/40-manual-blueprint-copy-costs` (commit `5b3bacb`) - #40, new
  `manual_blueprint_copy_costs` table + second table on `Blueprints.tsx` +
  amortized-cost wiring into `_unit_cost`/`unit_cost_detail`.
- `feature/38-margin-in-buildlists` (commit `47be993`) - #38, `margin` field
  (via `margin_home`, not `margin_jita`) on `BuildJobEntry`/`AssetPlanJob` +
  new column on `BuildList.tsx`/`AssetPlanList.tsx`.

**Important if picking this up on a different machine**: these four
branches only exist in this machine's local git repo - they were never
pushed to `origin`, so a fresh `git clone`/`git pull` elsewhere will NOT
have them. If you're on a different machine, you need either to get back to
this one, or ask the user whether to push the branches over first. If
you're on *this* machine, `git branch` will show all four still present
locally, each with exactly one commit ahead of `dev` at the point it
branched (`4317735`).

**Not yet reviewed by the user at all** - the next step is to show the user
each branch's diff/commit (or however they want to review four
uncommunicated branches at once) and get their go-ahead before any of the
normal push → PR → merge → deploy sequence starts. Don't push/merge/deploy
any of these four without that explicit go-ahead - this is a deliberate
deviation from the Tier 1/2 workflow, not an oversight.

Labels and existing milestones were applied on GitHub to all ten
issues as part of the original triage. The previous
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

### Tier 2 — DONE (see Status section above for the full resolution)

- **#35** "inactive state on shortlist" — **closed, fixed in PR #43.** The
  original hypothesis in this section (thin order books on low-liquidity
  categories) turned out to be wrong - live investigation found the real
  cause was `shortlist._decision` never re-checking an already-inactive
  item's real numbers at all. See Status above for the fix.

- **#36** "stockpile in doctrine" — **closed, fixed in PR #43.** User
  confirmed the additive formula (`stockpile_target + max(0, contract_target
  − valid_contracts)`). See Status above.

- **#41** "no export costs in margin" — **left open, deferred by the user.**
  The finding in this section (margin_jita already subtracts haul cost) was
  confirmed live with a concrete Ferox example and posted as an issue
  comment. No code change - user said to revisit later, not now.

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

The user asked to categorize + plan, then to save this plan for later/
cross-machine continuation, then said "Starte mit tier 1. wenn fertig
deploy" (Tier 1 merged + deployed), then "tier 2 rückfragen" (asked for the
Tier 2 questions). All three Tier 2 questions were answered:
- #35: user pushed back on the original 3 options with a question of their
  own ("was war der Grund sie überhaupt inaktiv zu setzen, und ist das noch
  sinnvoll?") - this led to the real root-cause finding (no reactivation
  path exists at all), which was then confirmed as the fix to implement.
- #36: "Addieren" (additive formula) - confirmed directly.
- #41: "erstmal zurückstellen" (defer for now) - confirmed, no fix.

Tier 2 is fully implemented, merged, deployed, and the user confirmed both
#35 and #36 work online ("Beides funktioniert"). The user then went back to
Tier 1 and reported a real follow-up bug in #33 (Griffin still showing with
a 0-not-NULL target) - fixed in PR #44, deployed, live-verified directly
against the production DB. **Both tiers are now fully done and confirmed.**

The user then said "nun tier 3 aber diesmal einzeln und jeweils nur bis zum
commit ohne nachfrage" - explicitly a different, one-shot workflow from
Tier 1/2 (see the Status section above for the four branch names/commit
hashes). All four Tier 3 issues are implemented and locally committed, each
on its own branch, none pushed/merged/deployed. **The very next action in a
fresh session should be: tell the user all four are committed locally and
ask how they'd like to review them (diffs? one at a time?) before starting
the normal push → PR → merge → deploy sequence for each - do not push or
merge any of them without that explicit go-ahead first.**
