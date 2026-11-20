# Doctrine

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Equivalent module variants (Doctrine, confirmed with the user 2026-11-04)

Items with the same group, dogma effects and dogma attributes (fitting cost
included; only metaLevel/metaGroupID ignored) are interchangeable, e.g. True
Sansha / Dark Blood Thermal Armor Hardener. `eve_trader/sde_equivalence.py`
computes the classes from the SDE on every fetch (modules, charges, drones,
fighters, subsystems; published and market-listed; abyssal templates and
fireworks excluded) into the global `sde_type_equivalents`
(`docs/doctrine_schema.sql`); class id = smallest member, so it changes when
the SDE changes membership. Tenant edits from Doctrine's Equivalents tab
(`doctrine/equivalents.py`: exclude an item, add an item of the same group,
switch a group off) are stored **per item, never per class id**
(`doctrine_equivalent_overrides`, `doctrine_equivalent_disabled_groups`), so
they survive SDE updates. With `DoctrineConfig.accept_equivalent_variants`
(default on): a contract with the twin stays valid (info row kind
`equivalent_variant`), stockpile and Max assemblable pool the twins' stock
(exact stock first, then twins; one unit never serves two lines), and the
Shopping List buys the cheapest twin (`buy_type_id`). Every edit, the
settings switch, and an SDE apply that changes the classes re-validate the
stored contracts (no ESI call).

## Max assemblable fits (Doctrine)

`doctrine/engine.py` `fitting_assemblable` / `assemblable_for_fitting`
(`GET /api/doctrine/fittings/{id}/assemblable`, CLI `eve-trader doctrine
max-fit FITTING`, card on Fitting Detail, "Assemblable" column in the
doctrine table via `FittingStatus.assemblable_now`) answers "how many
complete fits can I put together right now". Confirmed with the user
2026-11-04: only the Doctrine stockpile counts (`engine.stockpile_available`,
the same filter path as the Stockpile page, summed over every stockpile
location; assembled ships are not stock),
consumables need their full quantity (no cargo tolerance; charges keep
their floor of 1), and the checked fitting gets top priority - stock other
fittings' stockpile allocation would claim still counts, and rows list
those fittings as `also_needed_by`. Nothing is built or bought. For a
bottleneck with a Production recipe the page asks Production's own
`/api/production/max-buildable` on demand (button only with the
`production` grant); there is no Doctrine-side cross-tool build path.
The math is pure (`validation.max_assemblable`): per item
`floor(on_hand / per_fit)`, the minimum is the answer. An optional target
(`?target_sets=N`, `--target N`, GitHub issue #416) adds `short_for_target`
per row under the same rules (`validation.short_for_sets`).

## Strategic reserve (Doctrine, GitHub issue #418)

`doctrine/reserve.py`, tab `/doctrine/strategic-reserve`, per-fitting
`doctrine_fittings.reserve_target`. One hangar, identified by owner, structure
and division together (`DoctrineConfig.strategic_reserve_*`,
`storage.ExcludedHangar`): a division flag alone repeats at every structure and
for every owner. A container in that hangar can narrow it
(`strategic_reserve_container_id`, added on the user's request 2026-11-05): then
only that container and everything nested in it is the reserve, matched by item
id (`storage.asset_subtree_item_ids`, 60 s cache), and the rest of the division
stays visible. A container that no longer exists hides nothing and the tab says
so (`container_missing`). The tab allocates the hangar's stock in Doctrine priority order
(`allocate_stockpile_with_equivalents`), so a shared module counts once; the
hull switch is global. Only loose items and packaged hulls count: assembled
ships and their fitting are not stock here either, and the tab says how many it
skipped.

Confirmed with the user 2026-11-05:
- **Production and Trading** treat the contents as nonexistent. A new stock or
  blueprint reader of theirs must pass `exclude_hangar`
  (`production_excluded_hangar()`); a live ESI path uses
  `storage.is_in_hangar` / `hangar_flag_for`. Trading reads character assets
  only, so a corp hangar changes nothing there.
- **Doctrine** does not count it as stockpile, even at a stockpile location, so
  shortfalls stay and the Shopping List still buys them. `reserve_on_hand` on a
  shortfall row is a hint only and enters no calculation. The tab warns when a
  fitting is below its target.
- **Realized and Portfolio deliberately do not filter.** The items still exist:
  hiding them would read a move into the reserve as reprocessing or inventory
  drift. The Asset Manager shows them with a `strategic_reserve` badge, and its
  "stock only" filter leaves them out.

A tenant without the `doctrine` grant has an inert reserve (`active_hangar`
returns None, 60 s grant cache), so stock cannot stay hidden from a tool nobody
can reach the setting of. Asset rows with a NULL flag stay counted until the next
sync: `_exclude_hangar_clause` needs its `COALESCE`, a plain `NOT (a AND b)`
would drop them.

## Ready to list (Doctrine, GitHub issue #429)

`doctrine/ready_to_list.py`: one hangar of assembled ships, per owner, structure
and division (`DoctrineConfig.ready_to_list_*`). The Doctrine Detail column counts
ships whose fit validates as valid or tolerable, pooled by fit identity. The hull
must be the ship itself, and a ship inside another ship's bay does not count. No
container option: assembled ships cannot sit in a container. An owner with no asset
rows at all is unknown (`None`), not 0. Not an exclude hangar: assembled ships are
never stock.

## Contract Market (Doctrine, GitHub issue #428)

`doctrine/contract_market.py`, tab `/doctrine/contract-market`, tables
`doctrine_market_*` (never `doctrine_contracts`). Scans one solar system as a
`pipeline_runner` job, on demand only. Scan and display cover every configured
doctrine and fitting, including inactive ones and those with no targets; other
Doctrine calculations stay on active fittings only.

ESI facts, checked live 2026-11-06. Do not re-derive these from the docs, which
say otherwise:
- `GET /contracts/public/{region}/` has no `status` or `availability`. Everything
  on it is open until `date_expired`.
- There is no alliance endpoint (esi/esi-issues#1218). `GET /corporations/{id}/
  contracts/` also returns contracts assigned to the corporation's alliance, from
  any member corp, with readable items; member corps get the same list. Those rows
  say `availability: personal` (esi/esi-issues#1447), so only `assignee_id`
  identifies them. The character list does not have them.
- A corp list therefore holds about 15,000 rows of mostly other people's contracts.
  Anything treating a row as the corporation's own must check `for_corporation` as
  well as `issuer_corporation_id` (`esi_data/fetchers.py`, `realized.ingest.
  contract_issuer_matches`): the latter names the corp on every member's personal
  contract too. Checked live: 1 of 1,734 such rows in a 12,000-member corp is a real
  corp contract. A member's personal contracts come through their character share.
  Corp archive rows stored before this rule stay in `ledger_contracts`.

Rules:
- Public locations are read, not looked up: resolving every structure of a region
  fills `location_registry` with strangers' structures and burns the ESI error
  budget. What cannot be placed is counted and shown.
- A failed list read or too many failed item reads keep the last result.
- A contract smaller than the smallest doctrine hull (packaged) cannot hold a fit
  and is never read: 413 of 34,459 in The Forge pass. If a hull's volume is
  unknown, the filter is off.
- The corporation contract routes are group `corp-contract`: 600 tokens per 15 min,
  2 per read, **per character** (checked live with 18 members), and any member can
  read the corp list without a corp role. A 16-page list costs 32. So item reads
  spread over every member's token, at most `MAX_READS_PER_CHARACTER` each, moving on
  when a bucket is empty; the next scan continues what is left.
- `ESIClient` never sleeps out a `Retry-After` over `MAX_RETRY_AFTER_WAIT_SECONDS`;
  it raises `ESIRateLimited`. Waiting holds the job slot, and the bearer token in
  the prepared request expires meanwhile, which then fails as a 401.
- Items of every open contract read so far are stored, in any system, because a
  contract's list never changes.
  Cap per scan `MAX_NEW_ITEM_FETCHES`, minimum interval `MIN_SECONDS_BETWEEN_SCANS`.
- A contract asking the buyer for items is not listed: its price says nothing
  about the fit. Invalid fits are not listed either.
- A contract shows under every fitting with the same identity.
- When `for_corporation` is true and `issuer_corporation_id` is set, the Seller
  is that corporation; the character who created the contract is never resolved
  or stored on `doctrine_market_contracts`. Missing `for_corporation` (common on
  the public list) stays personal. Own Contracts (`doctrine_contracts`) are
  unchanged.
- Without a readable alliance source the last result is never replaced: an
  alliance-only scan raises, and with Public on the public half continues with
  `alliance_skipped` (shown on the page). Previously stored item lists stay
  when an enabled source was not actually read.
- If several corporations of the same alliance are shared, the one with the most
  capable readers runs the list and item reads (lowest corp id on a tie). Their
  readers are not pooled: a character may only call its own corporation endpoint.
- Character contract-item calls are paced in `ESIClient` at 15 per 10 s per
  character (below ESI's ConStopSpamming wall of 20 / 10 s). A 520 body with
  `ConStopSpamming` waits `remainingTime / 1e7` seconds (100-ns ticks) or raises
  `ESIRateLimited` when the wait is too long; other 520s stay ordinary errors.
- Items already fetched survive a scan abort (`upsert_doctrine_market_contract_items`
  before the `ActionError`); the next scan skips those contract ids.
