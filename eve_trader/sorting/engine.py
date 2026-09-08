"""Wareneingang/hangar-sorting helper - the Sorting tenant tool
(`eve_trader/sorting/`). EVE has no API to move an item between hangar
divisions: Jita imports for every other tool physically land in one or
more intake hangars first (a character's personal Hangar, a shared corp
division, ...), and a human has to sort them into each tool's own
division by hand. do_sorting_list answers "what's sitting in the
configured intake sources right now, and how much of it does each demand
pot still want" - a read-only aggregation, no writes, no reservation/
allocation logic (EVE itself has no such concept).
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from ..doctrine.config import DOCTRINE_CONFIG, DoctrineConfig
from ..doctrine.engine import stockpile_rows_for_doctrine
from ..production import engine as production_engine
from ..production.config import PRODUCTION_CONFIG, ProductionConfig


def _trading_wanted_by_type() -> dict[int, float]:
    """Approximates each shortlist item's still-open "buy more of this to
    import" quantity from the last Refresh Shortlist snapshot
    (storage.latest_snapshot - a plain read of an already-computed run, no
    fresh ESI/Goonmetrics calls here).

    There is no dedicated "recommended buy quantity" anywhere in Trading -
    ShortlistRow only carries priced/derived columns (landed_cost, margin,
    ...), never a target quantity to actually buy (shortlist.py's own
    "Import"/"Skip"/... decision is a yes/no gate, not a sizing). Rather than
    inventing new sizing logic here, this reuses the one quantity figure
    Trading already computes and treats as a real daily-turnover estimate
    (avg_daily_volume - GitHub issue #100, the same figure CLAUDE.md's
    "Theoretical ceiling" section documents for Profit/Day) as a stand-in for
    "how much of this could plausibly be imported today", net of sell_volume
    (already listed for sale at the structure - what's still covering that
    demand). This is a deliberately simple approximation, not a real Trading
    feature - documented here rather than over-built, since Trading has no
    existing "buy quantity" concept to mirror.

    Deliberately does NOT also subtract own_orders_remaining (the trader's
    own open buy-side coverage): shortlist._decision already routes any row
    with own_orders_remaining > 0 to "Already ordered", never "Import" (see
    shortlist.py), so by the time a row reaches this function - already
    filtered to decision == "Import" below - own_orders_remaining is always
    0 on it. Subtracting it here would be dead code, not a real second
    factor.

    Only rows the last run actually flagged "Import" (shortlist._decision) -
    a Skip/Inactive/No-market-data/Already-ordered row isn't something
    Trading currently wants more of, even if it happens to sit in the shared
    intake hangar right now."""
    df = storage.latest_snapshot()
    if df.empty:
        return {}
    df = df.fillna(0)
    wanted: dict[int, float] = {}
    for _, row in df.iterrows():
        if row.get("decision") != "Import":
            continue
        type_id = int(row["item_id"])
        if not type_id:
            continue
        qty = max(0.0, float(row.get("avg_daily_volume", 0.0)) - float(row.get("sell_volume", 0.0)))
        if qty > 0:
            wanted[type_id] = wanted.get(type_id, 0.0) + qty
    return wanted


def _material_wanted_by_type(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict[int, float]:
    """Production's backup_stock (the internal-reserve Soll - see
    production/engine.py's _total_missing docstring for the full three-pool
    breakdown) minus current Ist, where Ist is manual_stock plus real ESI
    assets restricted to cfg.stock_hangar_flags (Teil A's filtered
    esi_stock_at_location) at cfg.home_location_id - the same structure the
    shared intake hangar physically sits at.

    Deliberately only the backup_stock pool, not the fuller three-pool
    _total_missing calculation (which also nets against home/Jita market-
    listing targets and open sell orders) - this is a "how much material do
    I still need to sort into my own hangar" question. Market-listing
    demand is a separate pot (`markt`, via
    production_engine.market_listing_shortfall_by_type)."""
    manual_stock = storage.load_manual_stock()
    wanted: dict[int, float] = {}
    for type_id, _name, backup_stock, _home, _jita in storage.load_stock_targets():
        current = manual_stock.get(type_id, 0.0) + storage.esi_stock_at_location(
            type_id, cfg.home_location_id, allowed_flags=cfg.stock_hangar_flags)
        missing = max(0.0, backup_stock - current)
        if missing > 0:
            wanted[type_id] = missing
    return wanted


def _doctrine_wanted_by_type(doctrine_cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict[int, float]:
    """Real shortfall per type_id, straight from
    doctrine.engine.stockpile_rows_for_doctrine (the exact same computation
    the Stockpile tab itself shows - validation.stockpile_deviation's own
    `shortfall`), summed across every fitting/doctrine that needs the item
    (a type_id can appear in more than one StockpileRow). Returns {} if no
    doctrine assets have ever been synced (assets_available=False) - nothing
    to compare against yet, not an error."""
    rows, assets_available = stockpile_rows_for_doctrine(cfg=doctrine_cfg)
    if not assets_available:
        return {}
    wanted: dict[int, float] = {}
    for row in rows:
        if row.shortfall > 0:
            wanted[row.type_id] = wanted.get(row.type_id, 0.0) + row.shortfall
    return wanted


def _ore_minerals_wanted_by_type() -> dict[int, float]:
    """Ore & Minerals' own shopping-list demand: mineral_requirements'
    required_qty (eve_trader/refining/ - the user-entered "how much of each
    mineral do I want to have on hand" target), unmodified - unlike
    Production/Doctrine there's no separate Ist/Soll netting step here today
    (Ore & Minerals doesn't track its own ESI stock at all), so the raw
    required_qty is the closest real read available."""
    return {type_id: qty for type_id, _name, qty in storage.load_mineral_requirements() if qty > 0}


def _markt_wanted_by_type(production_cfg: ProductionConfig) -> dict[int, float]:
    """Combined 'this belongs on the market' demand: Trading's open import
    quantity plus Production's home/Jita listing shortfall. A finished
    Production hull waiting to be listed at C-J and a Trading import item
    are the same physical question - both land on the structure market -
    so they share one pot rather than showing as two competing wanted_by
    entries."""
    wanted: dict[int, float] = {}
    for source in (
        _trading_wanted_by_type(),
        production_engine.market_listing_shortfall_by_type(production_cfg),
    ):
        for type_id, qty in source.items():
            wanted[type_id] = wanted.get(type_id, 0.0) + qty
    return wanted


def _source_label(source_kind: str, character_name: Optional[str], hangar_flag: str,
                  label: Optional[str]) -> str:
    if label:
        return label
    if source_kind == "character" and character_name:
        return f"{character_name} ({hangar_flag})"
    return f"Corp ({hangar_flag})"


def _intake_from_sources() -> tuple[dict[int, float], dict[int, list[dict]]]:
    """Sums every configured sorting_intake_sources row, returning
    (totals_by_type, by_source_by_type). Character sources read
    character_assets filtered to owner_name; corp sources read corp_assets
    with no owner filter. Empty sources list -> empty dicts, not an error."""
    totals: dict[int, float] = {}
    by_source: dict[int, list[dict]] = {}
    for source_id, source_kind, character_name, hangar_flag, label in storage.load_sorting_intake_sources():
        if source_kind == "character":
            tables: tuple[str, ...] = ("character_assets",)
            owner_name: Optional[str] = character_name
        else:
            tables = ("corp_assets",)
            owner_name = None
        items = storage.assets_at_flag(hangar_flag, tables=tables, owner_name=owner_name)
        source_label = _source_label(source_kind, character_name, hangar_flag, label)
        for type_id, qty in items:
            totals[type_id] = totals.get(type_id, 0.0) + qty
            by_source.setdefault(type_id, []).append({
                "source_id": source_id,
                "source_label": source_label,
                "qty": qty,
            })
    return totals, by_source


def do_sorting_list(production_cfg: ProductionConfig = PRODUCTION_CONFIG,
                    doctrine_cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Sorting helper for the configured Wareneingang sources: for every
    type_id currently sitting in any of them (storage.assets_at_flag per
    source), how much is there (sum + by_source breakdown) and how much
    each demand pot still wants of it. Purely read-only aggregation - no
    reservation/allocation, EVE itself has no such concept (the player
    still decides by hand where each unit actually goes).

    Returns {"rows": [...]} where each row is
    {type_id, type_name, intake_qty, by_source: [{source_label, qty}],
    wanted_by_tool: [{tool, wanted_qty}], unclaimed}. `unclaimed` is True
    when no pot's own demand covers this type_id at all - still included
    (not filtered out), so a Wareneingang stack nobody currently wants
    shows up as a clear "nothing needs this right now" instead of silently
    vanishing from the list.

    Empty `rows` (not an error) when no sorting_intake_sources are
    configured - the feature is simply off until an operator adds at least
    one character/corp intake."""
    intake, by_source = _intake_from_sources()
    if not intake:
        return {"rows": []}

    markt_wanted = _markt_wanted_by_type(production_cfg)
    material_wanted = _material_wanted_by_type(production_cfg)
    doctrine_wanted = _doctrine_wanted_by_type(doctrine_cfg)
    ore_minerals_wanted = _ore_minerals_wanted_by_type()

    rows = []
    for type_id, intake_qty in sorted(intake.items()):
        wanted_by_tool = []
        for tool, wanted_map in (
            ("markt", markt_wanted), ("material", material_wanted),
            ("doctrine", doctrine_wanted), ("ore_minerals", ore_minerals_wanted),
        ):
            qty = wanted_map.get(type_id)
            if qty:
                wanted_by_tool.append({"tool": tool, "wanted_qty": qty})
        sde_type = storage.get_sde_type(type_id)
        type_name = sde_type[2] if sde_type else str(type_id)
        source_rows = [
            {"source_label": s["source_label"], "qty": s["qty"]}
            for s in by_source.get(type_id, [])
        ]
        rows.append({
            "type_id": type_id,
            "type_name": type_name,
            "intake_qty": intake_qty,
            "by_source": source_rows,
            "wanted_by_tool": wanted_by_tool,
            "unclaimed": len(wanted_by_tool) == 0,
        })
    return {"rows": rows}
