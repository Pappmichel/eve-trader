# Location resolution

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Location resolution (names, kinds, parents)

Plan and decisions: `docs/LOCATION_RESOLUTION_PLAN.md`. One package,
`eve_trader/locations/`, answers "what is this location id": it imports
`storage` and the ESI client, never a tool package (same rule as `esi_data/`).

- **One registry.** `location_registry` (per tenant, RLS; `docs/location_
  registry_schema.sql`) has a row for every referenced id that is not an NPC
  station or solar system: `kind` (`structure`, `office`, `container`,
  `other`), the `parent_location_id` an Office or container resolves through,
  the name, and the last lookup's `status` (`pending`, `resolved`, `forbidden`,
  `not_found`, `transient`) with `next_retry_at`. It replaced
  `structure_names` (phase 7): `docs/location_registry_schema.sql` copies any
  old names over and drops that table, so it no longer exists. The
  `storage` helpers `set_cached_structure_name` / `get_cached_structure_name(s)`
  / `list_cached_structure_names` / `list_structure_system_ids` /
  `load_category_system_ids` / `search_locations` keep their signatures and
  work on the registry. A container is any id that is an asset
  `item_id`: an asset's raw `location_id` is its immediate parent, so most
  "huge ids" are containers and ships, which ESI can never name.
- **One reader.** `locations.reader.describe(ids)` (name, kind, system, region,
  parent). `storage.get_location_names` and `asset_manager_location_details`
  delegate to it, and so does the Realized region lookup; an Office or
  container shows its structure. Tiers: registry, shared global names, the
  tenant's manual names, SDE stations. New code reads names through it.
- **One resolver.** `locations.resolver.resolve_pending()` registers what the
  tenant references and looks up only what is due. The ESI orchestrator
  (`esi_data/orchestrator.py` `_sync`) calls it once at the end of every pass
  that fetched a location kind, so idle scheduler ticks stay free and no tool
  has its own resolution step. `resolve_ids` is the on-demand path behind
  `production.actions.do_resolve_structure_name` (the Default-Tenant operator
  fallback stays there). The Admin bulk job calls `resolve_pending(force=...)`.
- **Retry rules.** 403 `forbidden` 7 days (earlier when the set of capable
  characters changes), 404 `not_found` 30 days, anything else `transient` with
  short backoff; a name older than 30 days is refreshed; a failure never hides
  an existing name; Offices and containers never reach ESI; one run spends at
  most a call budget and leaves the rest due. Manual names
  (`manual_location_names`) are never looked up.
- **User actions** (`locations/actions.py`): name a location (tenant-only,
  `manual_location_names`, max 100 chars), remove that name, look one id up
  again now. The Asset Manager exposes them as `PUT/DELETE
  /api/asset-manager/locations/{id}/name` and `POST .../retry`; its rows carry
  `location_status` so the page can say why a place has no name.
- **Do not** add a per-tool "resolve names" step, read `location_registry`
  directly for display, map an Office/container to its
  structure by hand (`storage.asset_item_parents` does that), or treat every
  id above 10^12 as a structure.
- **Consent.** Which characters may look names up is the user's tick of
  "Structure name resolution" (Characters page, Access). The Admin location
  report lists characters that own assets in unnamed structures but have not
  ticked it; it never ticks for them.
