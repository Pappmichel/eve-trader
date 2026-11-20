# Asset Manager

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Asset Manager tool

Plan, decisions (D1-D13, R-*, F-*) and the server checks still open:
`docs/ASSET_MANAGER_PLAN.md`. Tool key `asset_manager` (default grant),
package `eve_trader/asset_manager/`, router `/api/asset-manager/`, page
`/assets`, schema `docs/asset_manager_schema.sql` (saved views, daily
history).

- **Owner union (D10).** The page reads every owner that shares a kind with
  any tool the tenant still holds, plus its own sharing row
  (`esi_data.access.owners_shared_with_any`, `asset_manager/scope.py`). A
  share with an ungranted tool does not count: the scheduler stops
  refreshing it. Its sync button refreshes that same union
  (`orchestrator.do_sync_kinds`). Kinds: assets, blueprints, market_orders,
  contracts, industry_jobs. `fetchers._shared_with_portfolio` also archives
  orders/contracts for an Asset-Manager-only share.
- **Sources.** ESI assets, manual stock (only with the `production` grant),
  listed sell orders and outstanding contract items (Portfolio's
  definitions; never subtracted from assets), running Manufacturing/Reaction
  outputs. Everything is shown and badged (Asset Safety, deliveries,
  assembled, fitted, BPO/BPC); "stock only" applies the planners' rules.
- **Value** is `portfolio.price_items`, the one valuation shared with Total
  Wealth. Unpriced stays unpriced.
- **Paging.** Rows are loaded, priced and cached per tenant for 120 s
  (`actions._rows`, per-key lock), then filtered/sorted/paged in Python
  (`query.py`); totals always cover the whole filter. `invalidate_cache`
  after a sync.
- **Hierarchy** comes from `location_id` within `(owner, item_id)`; a corp
  Office is skipped and its contents sit under their division.
  `character_assets`/`corp_assets.item_name` holds ESI `/assets/names`
  names - nothing fills it yet (plan phase 4, waits on server check S-09).
- **History.** Per-tenant `asset_history` scheduler job (calendar-date due
  rule like `portfolio_snapshot`, needs the `asset_manager` grant), lazy
  fallback on the first history read of the day, retention
  `TradingConfig.asset_history_retention_days` (90).
- **Cross-tool reads** (manual stock, "wanted by", Doctrine match in the ship
  view) check the other tool's grant; the Doctrine match uses the pure
  `doctrine.validation` scorer on fittings read through storage.
- Production's old Asset Search tab is removed (commit `58c23df1`); the Asset
  Manager replaces it.
