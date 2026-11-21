# Production

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## "Theoretical ceiling" figures - not bugs

`potential_daily_profit` (Production's Build Candidates) and "Profit / Day"
(Trading's Shortlist) are deliberately `profit_per_unit x <a volume figure>`
- not a claim about what one seller could personally capture in a day. A
tiny-volume, huge-per-unit item (a capital hull, a faction module) can show
an enormous number - that's mathematically correct for "what's the whole
market worth," confirmed deliberate with the user after live-testing
surfaced exactly this case. Don't cap, filter, or "fix" the multiplication
itself without asking first.

**The volume figure must be a real turnover estimate, never order-book
depth, and never scoped to just one trader's own sales** - two real bugs,
not just a labeling issue:
- GitHub issue #51 (2026-08-21): Trading's Shortlist "Profit / Day" used to
  be `profit_per_unit x sell_volume`, where `sell_volume`
  (`esi_client._summarize_orders`) is the sum of `volume_remain` across
  every currently open sell order at the structure - i.e. "how much is
  listed for sale right now," a live order-book-depth snapshot, not actual
  daily traded volume. A single seller parking a large batch of a
  never-actually-sold item produced a wildly inflated "Profit / Day" purely
  from that listed quantity. #51 first fixed this by switching to the
  trader's own realized-sales average (`trade_reconciliation.
  average_daily_sold_by_type`, from the last Reconcile Trades run's
  matched sales) - a real fix for the order-book-depth bug, but it
  overcorrected: it left "Profit / Day" empty for every not-yet-sold-by-me
  candidate, which is most of what a shortlist exists to evaluate.
- GitHub issue #100 (2026-08-23): replaced that with `ShortlistRow.
  avg_daily_volume` - real average daily *market-wide* traded quantity,
  from Goonmetrics region history for `cfg.reference_region_id` (the real
  region C-J's own solar system sits in, confirmed live via
  `sde_solar_systems` - there's no ESI/Goonmetrics history endpoint for a
  player structure's own market at all, so region-wide is the closest real
  signal available), computed by `shortlist.average_market_daily_volume`
  (averages `HistoryPoint.movement` over Goonmetrics' own ~28-day return
  window) - `None` (shown as "–", excluded from Top Imports) until
  Goonmetrics has history for that item in that region, rather than
  estimated from something else. Exactly mirrors Production's own
  `potential_daily_profit`/`daily_movement` (`production/engine.py`),
  which already used this same `reference_region_id` + `price_history_
  chunked` pattern and was never affected by either bug. The `sell_volume`
  field itself is unchanged and still legitimately shown as "Listed Qty"
  (own column) - it's just never used for the Profit/Day multiplication.
  Corp wallet rows still enter the realized-profit archive. A shared corp mixes other members' fills into that archive and therefore into ledger profit. Profit / Day does not read them: since #100 it uses market-wide volume. The old FIFO matcher was removed (2026-10-25; `trade_reconciliation.py` keeps only the live wallet-transaction paging). The `realized_trades` table and its last rows stay only so `eve-trader realized compare-trading` can print them beside the Trading channel; nothing writes it anymore.

## Real SDE data drives classification, not heuristics

Item categorization (`classify_activity` in `production/engine.py`) uses
real SDE fields (`meta_group_id` for the "Faction"/"Officer"/"Storyline"/
"Deadspace" categories - meta_group_id 4/5/3/6 respectively, all mapped via
one `meta_group_labels` dict at the end of `classify_activity` - and
invention-recipe lookups for Tech II) rather than guessing from name
patterns or `metaLevel` alone - a past bug (Machariel/Nestor miscategorized
as Tech II) came from exactly that kind of heuristic (`metaLevel >= 2`
catching Faction ships too). All four of these meta-group labels share the
same ME0/TE0, non-researchable treatment (`constants.ACTIVITY_MODS`) - none
of them get Tech I's owned-BPO-preference/research-baseline treatment (see
`_activity_mods`). When adding another classification category, prefer an
SDE column already fetched by `production/sde.py`'s `refresh_sde()` over a
new heuristic; if the SDE doesn't already carry the field you need, extend
`refresh_sde()` to fetch it (see the `invMetaTypes.csv` merge that added
`meta_group_id` for precedent) rather than approximating.

## Assembled ships are not stock (confirmed with the user 2026-10-03)

Every demand calculation ignores assembled ships *and their fitting*
(slot modules, loaded charges, drone bay, fighter bay/tubes); cargo and
other holds inside an assembled ship still count. ESI's `is_singleton`
plus the SDE Ship category identify an assembled hull; a fitting/bay
`location_flag` identifies a fitted item (`storage.is_fitted_location_flag`).
`replace_assets` stores the result once at sync time in
`character_assets`/`corp_assets.assembled_or_fitted` (rows synced before
the column existed count until their next asset sync); the SQL stock
readers (`esi_stock_at_location_bulk`, `esi_stock_by_location_bulk`,
`assets_at_flag`), Doctrine's `esi_stock_from_asset_rows` and Trading's
`own_orders.py` skip it, and live ESI lists go through
`storage.mark_assembled_or_fitted`. Deliberately *not* applied to valuation
(Portfolio's `load_all_assets`) or the Asset
Manager (which badges such rows instead) - an assembled ship still has
value and still exists. A new stock reader must apply the
same filter.

**Asset lag (confirmed with the user 2026-11-04).** ESI caches industry
jobs ~5 min but assets ~60 min, so the job snapshot can be newer than the
asset snapshot. Per owner (jobs and assets both shared with Production),
`engine._asset_lag_adjustments` reads the job archive against
`esi_freshness.data_as_of`: output of a job delivered after the asset
snapshot still counts (`storage.delivered_job_outputs_after_assets`), and
materials of a job started after it are taken out
(`started_job_inputs_after_assets`, frozen `ledger_job_inputs`). Invented
copies delivered after the blueprint snapshot still count as BPC runs in
progress. `_current_stock` / `_stock_on_hand` apply it; a new stock reader
should go through them.

**Items moved between owners (2026-11-22).** Each owner's assets and
blueprints are a snapshot of their own time, so an item moved from A to B in
one tenant sat in both snapshots until A synced again: Total Wealth and the
Asset Manager counted it twice (seen live: two freighters moved from
characters into a corp hangar), and in the blueprint tables, whose PK is
`(tenant_id, item_id)`, the insert failed and two stale snapshots holding
each other's items blocked both owners for good. `replace_assets` and
`replace_blueprints` now remove the other owners' rows for an incoming item,
across the character and corp table (`storage._remove_moved_items`; within
one blueprint table always, since the PK requires it). Same item = same
item_id and type_id, and a singleton (ship, fitted module, blueprint
original/copy) or the same quantity (a stack moved whole, e.g. a moved
ship's cargo). Stacks with one item_id but different quantities stay, the
case c25e265 widened the asset PK for. The displaced owner is then synced at
the next tick (`scheduler.md`). The other direction cannot be detected: when
the old owner syncs first the item is missing until the new owner syncs, and
looks the same as one sold or destroyed.

**Ready at the build location (Asset-Optimized list, 2026-11-04).**
`ProductionConfig.asset_plan_ready_at_build_location` (switch on that page)
makes "Ready Now" count only stock at the structure set for the job's
category on the Logistics page (`engine._ReadinessLedger`, capped by the
type's total so a unit is never promised twice). A category without a
structure counts every location. Sizing does not change. Blockers report
`elsewhere`: the shortfall that lies free at other locations.

**Excluded systems (Production Settings, confirmed with the user
2026-11-04).** `ProductionConfig.excluded_system_ids` lists solar systems
Production ignores completely. `engine.production_excluded_location_ids`
resolves them once (60 s cache, cleared on a Settings save) to every location
id inside: SDE stations, registry structures with that system, registry
structures without a known system whose name starts with "<system> - ",
global structure names, and Offices/containers parented there
(`storage.location_ids_in_systems`). Production's asset, blueprint, formula
and running-job readers pass it as `exclude_location_ids`; a new Production
stock or blueprint reader must pass it too. That includes manual stock,
manual blueprints and manual jobs (`engine.production_excluded_kwargs()`;
`location_id = 0` is in no system and always counts), invention jobs by output
location, and the reaction window: a reaction job delivering into an excluded
system still takes its slot but adds no stock or incoming. The storage
parameter defaults to unfiltered, so the manual-stock page, the ledger,
valuation, the Asset Manager and other tools are not affected.

## Job-time character-skill bonus (confirmed real gap, fixed 2026-09-27)

`_activity_mods`/`_tech_ii_mods` (`production/engine.py`) used to combine
only Blueprint Time Efficiency with the structure/rig bonus for a job's
predicted duration - no character-skill (or implant) time bonus at all,
confirmed by live-comparing a real reaction job's "Job Duration Modifiers"
panel against this tool's own computed duration for the same blueprint/
quantity (100,000 runs of Crystalline Carbonide Armor Plate): the game
showed "Skills and Implants -35.4%", this tool showed nothing. `_skill_time_
mult` (engine.py) now fixes this with two independently-stacking layers, both
sourced from each skill's own dogma attribute (not just its free-text
description - `manufacturingTimeBonus`/`advancedIndustrySkillIndustryJobTime
Bonus`/`reactionTimeBonus`/`manufactureTimePerLevel`, verified against
`dgmTypeAttributes.csv`/`dgmAttributeTypes.csv`, cross-checked live against
the same real reaction job: Industry L5 (-20%) x Advanced Industry L5 (-15%)
x Molecular Engineering L5 (-5%) = 0.646 = exactly the shown -35.4%):
1. **Universal**: Industry + Advanced Industry for every Manufacturing job
   (also covers Tech I/II/III and Faction/Storyline/Officer/Deadspace, all
   of which route through this same activity_id=1 mechanic), or Reactions
   alone for a Reaction job - confirmed no "Advanced Reactions" time skill
   exists (Mass Reactions/Advanced Mass Reactions are job-slot skills only,
   see `constants.job_slots_from_skills`).
2. **Blueprint-specific "specialist" skill** (`constants.SPECIALIST_TIME_
   SKILLS`, ~26 skills, e.g. Molecular Engineering, each -1%/level except
   Mutagenic Stabilization at -2%/level) - only applies if the blueprint's
   own `industryActivitySkills.csv` row (`storage.get_blueprint_skills`,
   `sde_blueprint_skills` table) actually requires it; a required skill with
   no such dogma attribute (e.g. Capital Ship Construction - a pure gate,
   confirmed via its own SDE description) correctly contributes nothing.

Every level is a **flat, manually-set `ProductionConfig` field**
(`industry_skill_level`/`advanced_industry_skill_level`/`reactions_skill_
level`/`specialist_skill_level`, default 5) - the exact same "one tenant-
wide number, no per-character selection" design this tool already used for
Invention's `encryption_skill_level`/`datacore_skill_*_level`, deliberately
kept that way here too (confirmed with the user 2026-09-27): real ESI
character skills already exist for job-slot counts
(`esi_data/fetchers.py::fetch_character_skills`), but are not reused for
this, since a tenant can have several producer characters and there is no
"whose skills count" selection logic anywhere in this tool to reach for
instead - re-opening that question was explicitly declined in favor of
staying consistent with the existing Invention pattern.

## Special orders in the regular build workflow

Plan and freeze: `docs/SPECIAL_ORDERS_INTEGRATION_PLAN.md` (done),
`docs/PRODUCTION_SEMANTICS.md` SF-1..SF-10, `docs/KNOWN_LIMITATIONS.md`.
An open order with `include_in_production` feeds `plan_production` and
`plan_asset_optimized` next to stock targets. Turning that flag on also
forces `net_against_stock` (Prefer-stock stays locked while integrated).
Both planners run an **order pass then a stock-target pass** on one shared
stock ledger; the order pass uses `base_runs = {}` (no overbuild) and
ignores `min_margin`. Integrated top-level qty is netted against
`_current_stock` (incl. running jobs), oldest order first — SF-1 still
applies to the order's own Compute / Combined preview. Asset-Optimized
gives order runs readiness and slots first (with a days target, all order
ready runs are queued before stock-target runs wait). The Special Orders
page shows `order_progress` from the cached asset plan and a "Ready to
deliver" hint; marking `done` stays manual. Cached Build / Asset plans
are kept but flagged `stale` when integrated demand changes (D9). Schema change is
`ADD COLUMN IF NOT EXISTS` on the existing `docs/special_orders_schema.sql`.

## Max buildable now (Special Orders page)

`production/max_buildable.py` (`do_max_buildable`, `POST /api/production/
max-buildable`, CLI `eve-trader production max-build ITEM [--mode full|direct]
[--target N]`, card on the Special Orders page) answers "how many of X can I
build right now". Confirmed with the user 2026-11-03: the item gets top
priority, so nothing other stock targets or orders would reserve is
subtracted; only physically on-hand stock counts (`_stock_on_hand`, no
running jobs); nothing is bought. `full` builds missing components and
reactions from owned inputs, `direct` counts only the item's own inputs.
Blueprints limit the result: an owned original is unlimited, otherwise only
owned copies (`storage.list_blueprint_copies`), each copy its own job with its
own ME and rounding. The result is a binary search over a pooled,
`_expand_all`-shaped walk; the bottleneck is what is short at max + 1. When no
original is owned it also reports what materials alone would allow.

## Structure rigs per job category (Logistics page)

Every structure/rig bonus goes through `engine._facility_mults` /
`facility_resolution` (ME, TE and job cost; also Realized and Max buildable).
For an item's job category (`job_category`) the order is: the value saved in
`job_category_facilities` (Logistics page, CLI `eve-trader production
facilities list|set|clear`), then the tier a **rig sheet** states for the
category structure's solar system (below), then the rig tier the category
structure's name states (`production/facility_names.py`, e.g. "IAK-JW - CEZ T2 Adv L T1 Basic
L"), then the global Settings profile (`*_structure_type` / `*_rig_tier`).
Rig security comes from the category structure's system when known. Only the
three ship-size categories have a separate rig for Tech II/III hulls
("Advanced" rigs); every other category uses one rig. Confirmed with the user
2026-11-04: rigs are set per category and filled from the structure name
where it says so; the CEZ/KFEZ/GEZ prefixes are owner tags, not structure
types. Structure rigs are not in ESI assets unless the corp owns the
structure, so names are the only automatic source. Data is cached per tenant
(60 s TTL plus `invalidate_facility_cache()` on every write path).

**Rig sheet source (decided with the user 2026-11-16).** An alliance may keep
a shared Google sheet with the rig tier per solar system and job category.
`ProductionConfig.rig_sheet_url` (per tenant, Production settings, validated
and length-limited) names it; the link is user input and never part of the
repository, tests or docs. `production/rig_sheet.py` holds a pure parser
(`parse_csv`, `lookup`) and the only network code (`fetch_csv`: the export
address is built from the link's sheet id and `gid`, host `docs.google.com`
only, redirects only to `*.googleusercontent.com`, 10 s, 1 MiB, error texts
never carry the address or the server's answer). The parser finds each block
by its `Location` cell, reads the group row above it, identifies a column by
group plus name (one table, `_COLUMN_TABLE`), follows arrows to the
neighbouring column inside the same group (a loop or the group's end means no
value), and treats `F`, empty and unknown codes as no value; only `T1`/`T2`/`N`
count, a qualifier in brackets counts only as `All` (a category is not one
reaction kind). A column that depends on the structure type (Raitaru/Azbel/
Sotiyo, Athanor/Tatara) is read at the type of the category's structure; an
unknown type gives no value. The last good parse is stored per tenant in
`production_rig_sheet` (with the source key of the link it came from, so a
changed link never reads the old sheet). **Planners and `facility_resolution`
never touch the network**: only `actions.do_reload_rig_sheet` does (saving a
new link, the Logistics page's "Reload rig sheet" button, and the page's
automatic reload when the stored copy is older than 24 h). A failed reload
keeps the stored rows and shows its error. The sheet applies only when the
category structure's system is known and listed and the cell resolves to
`T1`/`T2`/`N`; `N` ("No Rig") beats the name. TE follows the ME tier as before.
The Research/Copy/Invention blocks are not read.

**ME and TE rigs are separate tiers (decided with the user 2026-11-16).** The
ME tier follows the order above (`rig_tier`, `rig_source`). The time-efficiency
tier (`te_rig_tier`, `te_rig_source`) is the category's saved
`te_rig_tier_basic`/`_advanced`, else the profile's global
`<profile>_te_rig_tier`, else **the same tier as ME** ("same as ME"), so nothing
changes until someone sets a TE tier; the structure name is an ME source only.
Each tier may carry its own security table (`RigTier.security`; the Thukker
tier uses 0.1 / 1.9 / 0.1 and has no TE bonus, never offered for reactions), and
`facility_resolution` returns the ME and TE security multipliers separately
(`security_multiplier` stays the ME one). The invention cost rig
(`invention_rig_tier`, `constants.INVENTION_RIG_TIERS`, kept out of the
per-category dropdowns) is resolved by `facility_resolution(None, "Invention",
"invention", cfg)` with the invention system's security and only lowers the
modelled invention/copy fee in `invention.fee_rates`; an observed total rate
stays authoritative. Rig values follow the SDE dogma: the Thukker material rig
is the T1 base (2 %) scaled by its own table (so 3.8 % in low sec); its extra
`attributeThukkerEngRigMatBonus` (-3.7) is not applied until its effect is
confirmed. Invention cost rigs are -10 % / -12 % with the standard table.

**Facility tax is observed, never bound to a station or category (confirmed
with the user 2026-11-07).** A structure owner changes the rate at any time, so
no fixed rate is stored for a station or category. `engine.facility_resolution`
resolves it (`tax_rate`, `tax_source`) in this order: the category's manual
override (`job_category_facilities.facility_tax_rate`, Logistics page / CLI
`facilities set --tax-percent`), then the rate observed on the tenant's own last
jobs at the category's structure for that job activity (1 manufacturing, 11
reaction), then `ProductionConfig.facility_tax_rate`, which is now only the
fallback (its Settings label says so). `engine._job_cost_rate` reads it there;
nothing else may add `cfg.facility_tax_rate` to a manufacturing/reaction job
cost. Observation lives in `production/facility_tax.py`: per
`(facility_id, activity)` the median of the 3 newest usable jobs started within
30 days, with the date of the newest job so the Logistics page shows the age
(it also shows the observed rate while an override wins). One job's *tax* rate
is its `industry_job_tax` journal entry (`context_id` = job id) divided by the
job's EIV. The archive has no EIV, so it is an approximation: base ME 0
materials at today's ESI adjusted prices (copy and invention x 0.02). A job
without a tax entry, a recipe, or an adjusted price for every material is
skipped, never guessed; a missing observation falls back to Settings. Only
owners shared with a realized-profit tool count, jobs via
`access.shared_owners("industry_jobs")` and tax entries via `"wallet"` (D11);
tax rows are one query (`db.industry_job_tax_rows`), cached per tenant with the
facility cache and dropped by `invalidate_facility_cache()`. Do not read the
observation per item, and do not bind a rate to a station.

**Invention and copy fees use an observed total rate when available
(confirmed with the user 2026-11-07).** ESI's job `cost` is facility fee plus
tax. `facility_tax.observed_total_for_location` takes the median of
`job.cost / approximate_job_eiv` over the 3 newest invention (activity 8) or
copy (activity 5) jobs in 30 days at that structure — no journal tax entry
required (copy jobs often have none). `invention.fee_rates` uses that fraction
directly as `invention_rate` / `copying_rate` and does **not** add tax or SCC
on top. Without an observation it falls back to the modelled formula (system
index × manufacturing structure bonus + observed/Settings tax + SCC). The EIV
approximation uses today's adjusted prices. Manufacturing and reaction stay on
`_job_cost_rate` (real per-category rigs). Cost rows are a second scoped query
(`db.industry_job_cost_rows`); parent resolution for Office/container location
ids is cached 60 s per tenant+location (including a None miss) so a miss does
not re-hit `locations.reader` on every item.

## Manual tracking for Production

Full history/decision log/phase breakdown lives in `docs/MANUAL_TRACKING_PLAN.md`
(read this if you need the "why", not just the "what" below) - all 10 phases
(0-9) are done. Goal: Production is fully usable without an ESI login.
Manual data (stock, blueprints, running jobs, listed quantities, location
names) takes effect everywhere ESI data already does, **additively** -
there is no mode switch and no separate page. Input lives on the existing
Stock Targets, Blueprints and Industry Jobs pages, each with its own
manual-entry section/form alongside the ESI-synced rows (`source: "esi" |
"manual"` on the row shape, `manual_id` set only for a manual row).

- **`location_id = 0`** is the "no location" convention throughout
  (`manual_stock`/`manual_owned_blueprints`/`manual_industry_jobs` all
  default to it) - a manual entry doesn't have to specify a real structure/
  station id, since not every user tracks that level of detail.
- **Engine touch points** (`production/engine.py`) - manual data is added
  on top of the ESI-derived figure, never replacing it: `_current_stock`/
  `_stock_at_location` add `manual_stock`; `_owned_bpo_best_me_te` takes
  `max(esi, manual)` independently for ME and TE; `_available_blueprint_
  copies` adds manual BPC runs; `_has_bpo_at_location` also checks manual
  BPOs; `_current_stock` also adds `manual_incoming_qty` (a flat add, not
  runs × product qty like ESI's own incoming-jobs figure - decision 2);
  `_total_missing`/`market_status` add manual listed quantities to the
  ESI-derived home/Jita listed figures.
- **Asset paste** (Stock Targets page): pastes an EVE inventory "Copy As"
  list straight into manual stock at a chosen location, `replace` or
  `merge` mode, previewed as a diff before committing - the same
  `paste_parser.py` grammar the Ore & Minerals import already uses.
  Blueprint-category lines are always skipped (no reliable ME/TE/runs
  clipboard format exists - confirmed against `evepraisal/evepaste`,
  see the plan's phase 0). The server re-parses the pasted text itself on
  commit, never trusting rows the client sends back.
- **Location names**: `manual_location_names` (per-tenant, this tenant's
  own opinion, never shared) is the lowest-priority tier in the location
  lookup chain, below `global_structure_names` (see "Tool permissions &
  Admin" above for why that one has no `tenant_id` at all) and this
  tenant's own ESI-resolved cache.
- **Operator fallback** (`ProductionConfig.global_structure_resolution_
  fallback`, Default-Tenant-only, see "Tool permissions & Admin" above for
  the general Default-Tenant-only pattern): when on, a tenant whose own
  `structure_name_resolution` characters can't resolve a given structure -
  whether because it has none at all, or because the ones it has simply
  can't see that particular structure - falls back to the Default Tenant's
  own operator characters. Not limited to "no characters of its own": a
  tenant with working characters still hits the fallback for any structure
  its own characters happen not to be able to resolve. The
  switch itself is always read inside `tenant_scope.enter_tenant(storage.
  DEFAULT_TENANT_ID)` in `do_resolve_structure_name`, never off the
  ambient/requesting tenant's own `PRODUCTION_CONFIG` - that ambient copy
  is always `False` for every tenant except the Default one, since
  `admin.do_set_structure_resolution_fallback` only ever saves it there.
  Confirmed real bug fixed in code review (2026-09-25): the fallback was
  reading the requesting tenant's own copy, making it silently inert for
  every tenant but the operator's own.
- **Admin bulk resolution** (`admin.do_resolve_structure_names`, a
  `pipeline_runner` background job): resolves every candidate location the
  *admin's own* tenant's data references (decision 6b - not every tenant's
  data; `storage.candidate_structure_location_ids` is a normal RLS-scoped
  read) in one pass, instead of relying on that tenant's own on-demand
  resolution - see that function/`admin._structure_resolve_candidates` for
  what counts as a candidate. Every successful resolution still lands in
  the global cache (decision Q2), so it does end up helping every other
  tenant that later asks about the same structure - just not because this
  job itself looked at their data.
- **CLI**: `eve-trader production manual stock|blueprints|jobs|listed|
  locations ...` (`cli.py`) - every command takes `--tenant-id` and runs
  inside `tenant_scope.enter_tenant`, same `do_*` actions the web UI calls.
