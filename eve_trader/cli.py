"""Command-line interface - thin wrappers around eve_trader.actions.

Covers the same daily trading workflow as the browser UI, one command per
step, plus a `pipeline` command that runs the whole thing. All actual logic
lives in actions.py so the dashboard (browser UI) uses the exact same code.

Examples:
    eve-trader auth --role buyer
    eve-trader auth --role seller
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

from . import actions, storage

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


@main.group()
def tenant():
    """Tenant provisioning (admin-only, not reachable from the web app)."""


@tenant.command("create")
@click.argument("name")
def tenant_create(name: str):
    """Provisions a new tenant, prints its tenant_id."""
    tenant_id = storage.create_tenant(name)
    click.echo(f"Created tenant '{name}': {tenant_id}")


@tenant.command("add-entry")
@click.argument("tenant_id")
@click.option("--character", type=int, help="EVE character_id to register to this tenant.")
@click.option("--corporation", type=int, help="EVE corporation_id to register to this tenant.")
@click.option("--alliance", type=int, help="EVE alliance_id to register to this tenant.")
def tenant_add_entry(tenant_id: str, character: int | None, corporation: int | None, alliance: int | None):
    """Registers a character/corp/alliance id as belonging to a tenant - a
    login as any registered id resolves to this tenant (see
    access_gate.py/storage.resolve_tenant_id)."""
    entries = [("character", character), ("corporation", corporation), ("alliance", alliance)]
    provided = [(t, i) for t, i in entries if i is not None]
    if not provided:
        raise click.UsageError("Provide at least one of --character/--corporation/--alliance.")
    for entry_type, entry_id in provided:
        storage.add_tenant_registry_entry(tenant_id, entry_type, entry_id)
        click.echo(f"Registered {entry_type} {entry_id} -> tenant {tenant_id}")


@tenant.command("list")
def tenant_list():
    """Lists every provisioned tenant and its registered entries."""
    for tenant_id, name, created_at in storage.list_tenants():
        click.echo(f"{tenant_id}  {name}  (created {created_at})")
        for entry_type, entry_id in storage.list_tenant_registry_entries(str(tenant_id)):
            click.echo(f"    {entry_type}: {entry_id}")


@main.command()
@click.option("--role", type=click.Choice(["buyer", "seller"]), required=True)
def auth(role: str):
    """Authorize a character via EVE SSO (opens a browser)."""
    result = actions.do_auth(role)
    click.echo(f"Authorized [{role}]: {result['character_name']} ({result['character_id']})")


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


if __name__ == "__main__":
    main()
