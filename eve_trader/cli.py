"""Command-line interface - thin wrappers around eve_trader.actions.

Covers the same daily trading workflow as the browser UI, one command per
step, plus a `pipeline` command that runs the whole thing. All actual logic
lives in actions.py so the dashboard (browser UI) uses the exact same code.
Character login is web-only (`/api/characters/reauth/start` from the
Characters page); there is no `eve-trader auth --role` command.

Examples:
    eve-trader build-universe
    eve-trader build-focused
    eve-trader find-new-candidates --safe
    eve-trader add-to-shortlist
    eve-trader refresh-shortlist
    eve-trader reconcile-trades
    eve-trader pipeline
"""
from __future__ import annotations

import logging

import click

from . import actions, config, storage, tenant_scope
from .auth import import_tokens_file
from .production import actions as production_actions
from .production import config as production_config
from .sqlite_migration import migrate_sqlite_to_postgres

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("eve_trader.cli")


@click.group()
def main():
    """EVE Trader - C-J import trading toolkit."""
    # Every storage.py query now needs an ambient tenant_id (see
    # storage.connect()'s fail-closed check) - the CLI is a trusted single
    # operator, same reasoning as AccessGateMiddleware's gate-disabled case
    # (api/app.py), so every command gets storage.DEFAULT_TENANT_ID set
    # once here rather than each command setting it individually. No
    # explicit reset needed - the process exits when the command finishes.
    storage.set_current_tenant(storage.DEFAULT_TENANT_ID)
    # Also resolve DEFAULT_TENANT_ID's own TRADING_CONFIG/PRODUCTION_CONFIG
    # (config.yaml + any Settings-page overrides already saved to Postgres's
    # tenant_settings) - confirmed real gap: without this, every CLI command
    # silently ignored a Settings-page save (do_update_settings persists to
    # Postgres only, not config.yaml, since Phase 2), reading only the
    # ContextVar's plain config.yaml-only default instead. Same fix
    # api/app.py's _load_default_tenant_config() already applies for the
    # gate-disabled web path. No reset needed here either - same reasoning.
    config.resolve_and_set_trading_config(storage.DEFAULT_TENANT_ID)
    production_config.resolve_and_set_production_config(storage.DEFAULT_TENANT_ID)


@main.group()
def tenant():
    """Tenant provisioning. `create`/`add-entry`/`list` now also have a web
    equivalent (the Admin tool, eve_trader/admin.py + api/routers/admin.py -
    reachable to characters with an explicit admin tool grant, see
    access_gate.tools_for) - this CLI group stays useful for initial setup
    before a server/session exists at all, and both paths call the exact same
    storage.py functions, so they can never drift apart. `import-tokens`/
    `migrate-sqlite` remain CLI-only - genuinely one-time, operator-run
    commands with no UI equivalent. First-admin recovery is
    `eve-trader admin bootstrap`, not disabling the access gate."""


@tenant.command("create")
@click.argument("name")
def tenant_create(name: str):
    """Provisions a new tenant, prints its tenant_id."""
    tenant_id = storage.create_tenant(name)
    click.echo(f"Created tenant '{name}': {tenant_id}")


@tenant.command("add-entry")
@click.argument("tenant_id")
@click.option("--character", type=int, required=True, help="EVE character_id to register to this tenant.")
def tenant_add_entry(tenant_id: str, character: int):
    """Registers a character id as belonging to a tenant - a login as this
    character resolves to this tenant (see access_gate.py/storage.
    resolve_tenant_id). Character-only - corp/alliance registry entries were
    retired once AccessGate became character-only (see docs/admin_schema.sql)."""
    storage.add_tenant_registry_entry(tenant_id, character)
    click.echo(f"Registered character {character} -> tenant {tenant_id}")


@tenant.command("list")
def tenant_list():
    """Lists every provisioned tenant and its registered entries."""
    for tenant_id, name, created_at in storage.list_tenants():
        click.echo(f"{tenant_id}  {name}  (created {created_at})")
        for entry_type, entry_id in storage.list_tenant_registry_entries(str(tenant_id)):
            click.echo(f"    {entry_type}: {entry_id}")


@tenant.command("import-tokens")
@click.option("--tenant-id", default=None, help="Tenant to import into (default: the fixed default tenant).")
@click.option("--path", type=click.Path(exists=True), default=None,
              help="tokens.json path (default: OAUTH_CONFIG.token_store_path).")
def tenant_import_tokens(tenant_id: str | None, path: str | None):
    """One-time import of a file-based tokens.json (TokenManager's format
    before its Postgres cutover) into tenant_tokens. Safe to re-run - every
    record is upserted."""
    from pathlib import Path
    count = import_tokens_file(
        tenant_id or storage.DEFAULT_TENANT_ID, Path(path) if path else None
    )
    click.echo(f"Imported {count} token(s) into tenant {tenant_id or storage.DEFAULT_TENANT_ID}.")


@tenant.command("backfill-esi-sharing")
@click.option("--tenant-id", default=None, help="Tenant to backfill (default: the fixed default tenant).")
def tenant_backfill_esi_sharing(tenant_id: str | None):
    """One-time conservative ESI sharing backfill (docs/ESI_ACCESS_PLAN.md
    decision 13). Idempotent and per-tenant. No web equivalent — same
    operator-run shape as `tenant import-tokens`."""
    from .esi_data.backfill import backfill_conservative_sharing
    tid = tenant_id or storage.DEFAULT_TENANT_ID
    with storage.tenant_context(tid):
        result = backfill_conservative_sharing()
    click.echo(
        f"Backfilled tenant {tid}: {result['sharing_inserts_attempted']} sharing "
        f"insert(s) attempted from {result['tokens']} token(s)."
    )
    missing = result["characters_missing_corporation_id"]
    if missing:
        click.echo(
            f"  character-only (public-info lookup failed): {missing}"
        )


@main.command("migrate-sqlite")
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--tenant-id", default=None, help="Tenant to migrate into (default: the fixed default tenant).")
def migrate_sqlite(db_path: str, tenant_id: str | None):
    """One-time ETL: migrates a single-tenant data/eve_trader.db (the
    pre-multi-tenant-migration SQLite schema) into Postgres for one tenant.
    Run this against a COPY of the real file, never the live one directly -
    see eve_trader/sqlite_migration.py's own docstring."""
    from pathlib import Path
    counts = migrate_sqlite_to_postgres(Path(db_path), tenant_id or storage.DEFAULT_TENANT_ID)
    for table, count in counts.items():
        click.echo(f"  {table}: {count} row(s)")
    click.echo(f"Migrated {sum(counts.values())} total row(s) into tenant {tenant_id or storage.DEFAULT_TENANT_ID}.")


@main.command("build-universe")
def build_universe():
    """Builds the full candidate universe from ESI/SDE market groups."""
    result = actions.do_build_universe()
    click.echo(f"{result['count']} market-group candidates created. Now run build-focused / find-new-candidates.")


@main.command("build-focused")
def build_focused():
    """Filters the candidate universe down to the focused set."""
    try:
        result = actions.do_build_focused()
    except actions.ActionError as e:
        click.echo(str(e))
        return
    click.echo(f"{result['count']} focused candidates created. Run find-new-candidates next.")


@main.command("find-new-candidates")
@click.option("--safe/--full", default=True, help="Safe mode caps at safe_mode_max_ids (recommended).")
def find_new_candidates(safe: bool):
    """Backtests focused candidates against price history and scores them."""
    try:
        result = actions.do_find_new_candidates(safe=safe)
    except actions.ActionError as e:
        click.echo(str(e))
        return
    click.echo(f"{result['evaluated']} new candidates evaluated. {result['recommended']} recommended (Add=True).")


@main.command("add-to-shortlist")
def add_to_shortlist():
    """Adds recommended new candidates to the shortlist."""
    try:
        result = actions.do_add_to_shortlist()
    except actions.ActionError as e:
        click.echo(str(e))
        return
    click.echo(f"{result['added']} selected candidates added to the shortlist. Run refresh-shortlist next.")


@main.command("refresh-shortlist")
def refresh_shortlist():
    """Recomputes landed cost / margin / decision for every active shortlist item."""
    try:
        result = actions.do_refresh_shortlist()
    except actions.ActionError as e:
        click.echo(str(e))
        return
    click.echo(f"{result['own_sell_orders_found']} own sell orders found in the structure.")
    click.echo(f"Summary: {result['summary']}")
    click.echo("Top imports by max daily profit:")
    for row in result["top_imports"]:
        click.echo(f"  {row['item']:<45} margin={row['margin']:.1%}  "
                    f"max_profit/day={row['max_profit_per_day']:,.0f} ISK")
    if any(result["audit"].values()):
        click.echo(f"Audit warnings: {result['audit']}")


@main.command("reconcile-trades")
def reconcile_trades():
    """Matches buyer/seller wallet transactions into realized trades."""
    try:
        result = actions.do_reconcile_trades()
    except actions.ActionError as e:
        click.echo(str(e))
        return
    click.echo(f"{result['matched_trades']} matched trades. "
               f"Total profit={result['total_realized_profit']:,.0f} ISK, "
               f"Avg margin={result['average_margin']:.1%}")
    for item, profit in result["top3_items_by_profit"]:
        click.echo(f"  Top: {item:<40} {profit:,.0f} ISK")


@main.command()
@click.option("--safe/--full", default=True)
@click.option("--rebuild-universe", is_flag=True, default=False,
              help="Also re-crawl the full ESI market-group tree (slow; occasional step, not daily).")
def pipeline(safe: bool, rebuild_universe: bool):
    """Runs the whole daily workflow: refresh -> new candidates -> shortlist -> trades."""
    results = actions.do_pipeline(safe=safe, rebuild_universe=rebuild_universe)
    for step, result in results.items():
        click.echo(f"[{step}] {result}")
    click.echo("Pipeline complete. Review 'New Candidates' (add-to-shortlist) and the dashboard.")


def _tenant_id_option(fn):
    return click.option(
        "--tenant-id", default=None,
        help="Tenant to operate on (default: the fixed default tenant).",
    )(fn)


@main.group("production")
def production_group():
    """Production tool: manual (ESI-free) data entry - docs/
    MANUAL_TRACKING_PLAN.md phase 9. Same do_* actions the web UI's Stock
    Targets/Blueprints/Jobs pages call, so this CLI can never drift from
    what the browser does."""


@production_group.group("manual")
def manual_group():
    """Manual (non-ESI) Production data: stock, blueprints, jobs, listed
    quantities, location names. Every command takes --tenant-id (default:
    the fixed default tenant) and runs inside tenant_scope.enter_tenant -
    not a bare storage.set_current_tenant - so TRADING_CONFIG/
    PRODUCTION_CONFIG are resolved for that tenant too (decision 17)."""


@manual_group.group("stock")
def manual_stock_group():
    """Manual stock entries (docs/MANUAL_TRACKING_PLAN.md phase 3/4)."""


@manual_stock_group.command("list")
@_tenant_id_option
def manual_stock_list(tenant_id: str | None):
    """Lists every manual stock entry (type, location, count)."""
    with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
        rows = production_actions.do_list_manual_stock_entries()["rows"]
    for row in rows:
        click.echo(f"{row['type_name']:<40} location={row['location_id']:<20} count={row['count']}")


@manual_stock_group.command("add")
@click.argument("item_name")
@click.argument("count", type=float)
@click.option("--location", "location_id", type=int, default=0, help="location_id (default: 0, unspecified).")
@_tenant_id_option
def manual_stock_add(item_name: str, count: float, location_id: int, tenant_id: str | None):
    """Adds or overwrites a manual stock entry for ITEM_NAME at --location."""
    try:
        with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
            result = production_actions.do_add_manual_stock_entry(item_name, count, location_id)
    except actions.ActionError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    click.echo(f"{result['type_name']} @ location {result['location_id']}: {result['count']}")


@manual_stock_group.command("remove")
@click.argument("type_id", type=int)
@click.option("--location", "location_id", type=int, default=0, help="location_id (default: 0, unspecified).")
@_tenant_id_option
def manual_stock_remove(type_id: int, location_id: int, tenant_id: str | None):
    """Removes the manual stock entry for TYPE_ID at --location."""
    with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
        production_actions.do_remove_manual_stock_entry(type_id, location_id)
    click.echo(f"Removed type {type_id} @ location {location_id}.")


@manual_stock_group.command("import")
@click.argument("source", type=click.File("r"))
@click.option("--location", "location_id", type=int, required=True, help="location_id to import into.")
@click.option("--mode", type=click.Choice(["replace", "merge"]), required=True)
@click.option("--dry-run", is_flag=True, default=False, help="Preview the diff without writing anything.")
@_tenant_id_option
def manual_stock_import(source, location_id: int, mode: str, dry_run: bool, tenant_id: str | None):
    """Imports an EVE inventory "Copy As" paste from SOURCE (a file path, or
    "-" for stdin) into manual stock at --location. --dry-run runs the same
    preview the Stock Targets page's paste panel shows, without writing."""
    text = source.read()
    try:
        with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
            if dry_run:
                result = production_actions.do_preview_asset_paste(text, location_id, mode)
            else:
                result = production_actions.do_commit_asset_paste(text, location_id, mode)
    except actions.ActionError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    if dry_run:
        for row in result["rows"]:
            click.echo(f"  [{row['status']}] {row['name']:<40} {row['old']} -> {row['new']}")
    else:
        click.echo(f"Applied: {result}")
    if result.get("unresolved"):
        click.echo(f"Unresolved lines: {result['unresolved']}", err=True)
    if result.get("skipped_blueprints"):
        click.echo(f"Skipped blueprint lines: {result['skipped_blueprints']}", err=True)


@manual_group.group("blueprints")
def manual_blueprints_group():
    """Manual owned blueprints (docs/MANUAL_TRACKING_PLAN.md phase 5)."""


@manual_blueprints_group.command("list")
@_tenant_id_option
def manual_blueprints_list(tenant_id: str | None):
    """Lists every owned blueprint (ESI-synced and manual)."""
    with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
        rows = production_actions.do_list_owned_blueprints()["rows"]
    for row in rows:
        kind = "BPO" if row.is_original else "BPC"
        manual = f" manual_id={row.manual_id}" if row.source == "manual" else ""
        click.echo(f"[{row.source:<6}] {row.type_name:<40} {kind} ME={row.material_efficiency} "
                   f"TE={row.time_efficiency} runs={row.runs} qty={row.quantity}{manual}")


@manual_blueprints_group.command("add")
@click.argument("item_name")
@click.option("--original/--copy", "is_original", default=True, help="BPO (default) or BPC.")
@click.option("--me", "material_efficiency", type=int, default=0, help="Material efficiency, 0-10.")
@click.option("--te", "time_efficiency", type=int, default=0, help="Time efficiency, 0-20 (even).")
@click.option("--runs", type=int, default=None, help="Runs remaining - required for a BPC, omit for a BPO.")
@click.option("--quantity", type=int, default=1, help="How many identical stacks (default: 1).")
@click.option("--location", "location_id", type=int, default=0, help="location_id (default: 0, unspecified).")
@_tenant_id_option
def manual_blueprints_add(item_name: str, is_original: bool, material_efficiency: int, time_efficiency: int,
                           runs: int | None, quantity: int, location_id: int, tenant_id: str | None):
    """Adds a manual owned blueprint for ITEM_NAME (blueprint or product name)."""
    try:
        with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
            result = production_actions.do_add_manual_owned_blueprint(
                item_name, is_original, material_efficiency, time_efficiency, runs, quantity, location_id,
            )
    except actions.ActionError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    click.echo(f"Added manual blueprint id={result['manual_id']}: {result}")


@manual_blueprints_group.command("remove")
@click.argument("manual_id", type=int)
@_tenant_id_option
def manual_blueprints_remove(manual_id: int, tenant_id: str | None):
    """Removes manual owned blueprint MANUAL_ID."""
    with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
        production_actions.do_remove_manual_owned_blueprint(manual_id)
    click.echo(f"Removed manual blueprint {manual_id}.")


@manual_group.group("jobs")
def manual_jobs_group():
    """Manual industry jobs (docs/MANUAL_TRACKING_PLAN.md phase 6)."""


@manual_jobs_group.command("list")
@_tenant_id_option
def manual_jobs_list(tenant_id: str | None):
    """Lists every current industry job (ESI-synced and manual)."""
    with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
        rows = production_actions.do_list_current_jobs()["rows"]
    for row in rows:
        manual = f" manual_id={row.manual_id}" if row.source == "manual" else ""
        click.echo(f"[{row.source:<6}] {row.type_name:<40} {row.activity} runs={row.runs} "
                   f"qty={row.quantity} status={row.status}{manual}")


@manual_jobs_group.command("add")
@click.argument("item_name")
@click.option("--quantity", type=float, default=None, help="Exactly one of --quantity/--runs must be given.")
@click.option("--runs", type=int, default=None, help="Exactly one of --quantity/--runs must be given.")
@click.option("--location", "location_id", type=int, default=0, help="location_id (default: 0, unspecified).")
@click.option("--ready-at", default=None, help="ISO timestamp the job finishes (default: now).")
@_tenant_id_option
def manual_jobs_add(item_name: str, quantity: float | None, runs: int | None, location_id: int,
                     ready_at: str | None, tenant_id: str | None):
    """Adds a manual industry job producing ITEM_NAME."""
    try:
        with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
            result = production_actions.do_add_manual_industry_job(
                item_name, quantity=quantity, runs=runs, location_id=location_id, ready_at=ready_at,
            )
    except actions.ActionError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    click.echo(f"Added manual job id={result['manual_id']}: {result}")


@manual_jobs_group.command("complete")
@click.argument("manual_id", type=int)
@click.option("--location", "location_id", type=int, default=None,
              help="Override location_id to book the stock into (default: the job's own location).")
@_tenant_id_option
def manual_jobs_complete(manual_id: int, location_id: int | None, tenant_id: str | None):
    """Completes manual job MANUAL_ID: deletes it and books its quantity into manual stock."""
    try:
        with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
            result = production_actions.do_complete_manual_industry_job(manual_id, location_id)
    except actions.ActionError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    click.echo(f"Completed manual job {manual_id}: {result}")


@manual_jobs_group.command("remove")
@click.argument("manual_id", type=int)
@_tenant_id_option
def manual_jobs_remove(manual_id: int, tenant_id: str | None):
    """Removes manual job MANUAL_ID without booking any stock (use `complete` for that)."""
    with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
        production_actions.do_remove_manual_industry_job(manual_id)
    click.echo(f"Removed manual job {manual_id}.")


@manual_group.group("listed")
def manual_listed_group():
    """Manual listed quantities (docs/MANUAL_TRACKING_PLAN.md phase 7)."""


@manual_listed_group.command("set")
@click.argument("type_id", type=int)
@click.argument("market", type=click.Choice(["home", "jita"]))
@click.argument("quantity", type=float)
@_tenant_id_option
def manual_listed_set(type_id: int, market: str, quantity: float, tenant_id: str | None):
    """Sets the manually-tracked listed quantity for TYPE_ID on MARKET."""
    try:
        with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
            production_actions.do_set_manual_listed_stock(type_id, market, quantity)
    except actions.ActionError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    click.echo(f"Set type {type_id} listed on {market}: {quantity}")


@manual_listed_group.command("clear")
@click.argument("type_id", type=int)
@click.argument("market", type=click.Choice(["home", "jita"]))
@_tenant_id_option
def manual_listed_clear(type_id: int, market: str, tenant_id: str | None):
    """Clears the manually-tracked listed quantity for TYPE_ID on MARKET."""
    with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
        production_actions.do_clear_manual_listed_stock(type_id, market)
    click.echo(f"Cleared type {type_id} listed on {market}.")


@manual_group.group("locations")
def manual_locations_group():
    """Manual location names (docs/MANUAL_TRACKING_PLAN.md phase 2)."""


@manual_locations_group.command("name")
@click.argument("location_id", type=int)
@click.argument("name")
@_tenant_id_option
def manual_locations_name(location_id: int, name: str, tenant_id: str | None):
    """Gives LOCATION_ID a tenant-own display NAME."""
    try:
        with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
            production_actions.do_set_manual_location_name(location_id, name)
    except actions.ActionError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    click.echo(f"Named location {location_id}: {name}")


@manual_locations_group.command("unname")
@click.argument("location_id", type=int)
@_tenant_id_option
def manual_locations_unname(location_id: int, tenant_id: str | None):
    """Removes this tenant's own manual name for LOCATION_ID."""
    with tenant_scope.enter_tenant(tenant_id or storage.DEFAULT_TENANT_ID):
        production_actions.do_remove_manual_location_name(location_id)
    click.echo(f"Unnamed location {location_id}.")


@main.group("admin")
def admin_group():
    """Operator-only admin recovery. There is no HTTP equivalent for
    bootstrap — that is the point (F-07 / F-NEW-02)."""


@admin_group.command("bootstrap")
@click.option("--character-id", type=int, required=True, help="EVE character_id to grant admin.")
@click.option("--character-name", default=None, help="Optional cached name for the Admin UI.")
@click.option("--all-tools", is_flag=True, default=False,
              help="Also grant every tool, not just admin. Default is admin only.")
@click.option("--confirm", is_flag=True, default=False, help="Required. Prevents accidental grants.")
def admin_bootstrap(character_id: int, character_name: str | None, all_tools: bool, confirm: bool):
    """Grant the admin tool to a character over a local/SSH trust channel.

    Run this BEFORE deploying a gate-enabled build onto a host that currently
    relies on the DEFAULT_TENANT_ID bypass, or you will lock yourself out of
    /api/admin. See docs/OPERATOR_SECURITY.md.
    """
    from . import admin as admin_mod
    try:
        result = admin_mod.do_bootstrap_admin(
            character_id, character_name, confirm=confirm, all_tools=all_tools,
        )
    except actions.ActionError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    click.echo(
        f"Admin bootstrap ok: character {result['character_id']} "
        f"tenant {result['tenant_id']} tools={result['tool_keys']} "
        f"created_tenant={result['created_tenant']} "
        f"already_had_admin={result['already_had_admin']}"
    )


if __name__ == "__main__":
    main()
