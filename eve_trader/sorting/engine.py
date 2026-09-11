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

import pandas as pd

from .. import storage
from ..doctrine.config import DOCTRINE_CONFIG, DoctrineConfig
from ..doctrine.engine import stockpile_rows_for_doctrine
from ..production import engine as production_engine
from ..production.config import PRODUCTION_CONFIG, ProductionConfig


# Snapshot decisions that mean "this is a live Trading SKU". The Shortlist
# page renders latest_snapshot(), not the live shortlist.active flag, so
# Import/Already-ordered on the snapshot is the same "on the import list"
# signal the operator sees. Skip is not one of these: a brand-new import
# with no C-J listings yet is Skip (shortlist._decision requires
# sell_volume > 0 for Import) and is claimed via the active shortlist
# instead.
_TRADING_SNAPSHOT_DECISIONS = frozenset({"Import", "Already ordered"})


def _as_type_id(value) -> Optional[int]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        type_id = int(value)
    except (TypeError, ValueError):
        return None
    return type_id or None


def _as_qty(value) -> float:
    if value is None:
        return 1.0
    try:
        if pd.isna(value):
            return 1.0
        qty = float(value)
    except (TypeError, ValueError):
        return 1.0
    if qty != qty:
        return 1.0
    return max(1.0, qty)


def _as_item_name(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    name = value.strip().lower()
    return name or None


def _trading_wanted() -> tuple[dict[int, float], dict[str, float]]:
    """Hangar-staging demand for Trading's import list, claimed as the
    `trading` pot (separate from Production's `markt` listing shortfall).

    Returns (by_type_id, by_lower_item_name). Name matching covers the
    case where the snapshot/shortlist row's item_id doesn't equal the
    intake stack's type_id but both are the same SDE name (Navy Cap
    Booster 3200 vs a drifted id) - the Shortlist page is name-first.

    Membership is the last Refresh Shortlist snapshot's Import/Already-
    ordered rows (storage.latest_snapshot - the same freeze the Shortlist
    page shows) plus every active live shortlist row
    (storage.load_shortlist). Snapshot Import wins even if the live
    shortlist later marked the row inactive: GitHub issue #35's booster
    deactivations left high-margin Boosters/Drugs inactive while the
    still-displayed snapshot said Import, which dropped them as nobody.

    Quantity reuses avg_daily_volume (GitHub issue #100), floored at 1.
    Deliberately does NOT subtract sell_volume: listings covering ADV
    means "don't import more today", not "this doesn't belong in the
    Trading hangar"."""
    snapshot_qty_by_id: dict[int, float] = {}
    snapshot_qty_by_name: dict[str, float] = {}
    snapshot_live_ids: set[int] = set()
    snapshot_live_names: set[str] = set()

    df = storage.latest_snapshot()
    if not df.empty:
        for _, row in df.iterrows():
            type_id = _as_type_id(row.get("item_id"))
            name = _as_item_name(row.get("item"))
            qty = _as_qty(row.get("avg_daily_volume"))
            if type_id:
                snapshot_qty_by_id[type_id] = max(snapshot_qty_by_id.get(type_id, 0.0), qty)
            if name:
                snapshot_qty_by_name[name] = max(snapshot_qty_by_name.get(name, 0.0), qty)
            decision = row.get("decision")
            if isinstance(decision, str):
                decision = decision.strip()
            if decision in _TRADING_SNAPSHOT_DECISIONS:
                if type_id:
                    snapshot_live_ids.add(type_id)
                if name:
                    snapshot_live_names.add(name)

    active_ids: set[int] = set()
    active_names: set[str] = set()
    for item in storage.load_shortlist():
        if not item.active:
            continue
        if item.item_id:
            active_ids.add(item.item_id)
        name = _as_item_name(item.item)
        if name:
            active_names.add(name)

    by_id = {
        type_id: snapshot_qty_by_id.get(type_id, 1.0)
        for type_id in active_ids | snapshot_live_ids
    }
    by_name = {
        name: snapshot_qty_by_name.get(name, 1.0)
        for name in active_names | snapshot_live_names
    }
    return by_id, by_name


def _material_wanted_by_type() -> dict[int, float]:
    """Production's real material demand, from the last Refresh Production
    buy list (storage.load_latest_buy_list - a plain read of an already-
    computed plan_production run, no fresh ESI/Goonmetrics calls here).

    That list is the fully expanded, margin-gated bill of materials (raw
    minerals, reaction inputs, ...), not the stock_targets rows themselves
    (those are almost always finished ships/modules). Reading stock_targets
    shortfall for the exact type_id sitting in intake therefore missed
    every real buy-list material that was never itself a stock target.

    Empty dict (not an error) if Production has never been refreshed this
    tenant - the same accepted staleness/emptiness Trading's
    `_trading_wanted` already has via load_shortlist()/
    latest_snapshot(). Market-listing demand stays a separate pot
    (`markt`, via production_engine.market_listing_shortfall_by_type)."""
    return storage.load_latest_buy_list()


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
    """Production's home/Jita listing shortfall - finished goods that
    belong on the C-J market hangar. Trading import-list items are a
    separate `trading` pot: same physical 'list this at C-J' idea, but a
    different hangar destination in practice, so they must not merge."""
    return production_engine.market_listing_shortfall_by_type(production_cfg)


def _source_label(source_kind: str, owner_name: Optional[str], hangar_flag: str,
                  label: Optional[str]) -> str:
    if label:
        return label
    if source_kind == "character" and owner_name:
        return f"{owner_name} ({hangar_flag})"
    if owner_name:
        return f"{owner_name} (Corp, {hangar_flag})"
    return f"Corp ({hangar_flag})"


def _intake_from_sources(production_cfg: ProductionConfig = PRODUCTION_CONFIG,
                         ) -> tuple[dict[int, float], dict[int, list[dict]]]:
    """Sums every configured sorting_intake_sources row, returning
    (totals_by_type, by_source_by_type). Character sources read
    character_assets filtered to that character's owner_name; corp sources
    read corp_assets filtered to that corp's owner_name. Both are also
    filtered to production_cfg.home_location_id (the C-J structure) when
    set - a hangar-division flag like "Hangar" exists at every station a
    character has ever had cargo in, and a Wareneingang source means the
    copy of it sitting at C-J specifically, not a character's Jita "Hangar"
    or any other station's. Empty sources list -> empty dicts, not an
    error."""
    totals: dict[int, float] = {}
    by_source: dict[int, list[dict]] = {}
    for source_id, source_kind, owner_name, hangar_flag, label in storage.load_sorting_intake_sources():
        if source_kind == "character":
            tables: tuple[str, ...] = ("character_assets",)
        else:
            tables = ("corp_assets",)
        items = storage.assets_at_flag(hangar_flag, tables=tables, owner_name=owner_name,
                                        location_id=production_cfg.home_location_id)
        source_label = _source_label(source_kind, owner_name, hangar_flag, label)
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
    intake, by_source = _intake_from_sources(production_cfg)
    if not intake:
        return {"rows": []}

    trading_by_id, trading_by_name = _trading_wanted()
    markt_wanted = _markt_wanted_by_type(production_cfg)
    material_wanted = _material_wanted_by_type()
    doctrine_wanted = _doctrine_wanted_by_type(doctrine_cfg)
    ore_minerals_wanted = _ore_minerals_wanted_by_type()

    rows = []
    sde_by_id = storage.get_sde_types_bulk(list(intake))
    for type_id, intake_qty in sorted(intake.items()):
        sde_type = sde_by_id.get(type_id)
        type_name = sde_type[2] if sde_type else str(type_id)
        trading_qty = trading_by_id.get(_as_type_id(type_id) or type_id)
        if not trading_qty:
            trading_qty = trading_by_name.get(_as_item_name(type_name) or "")
        wanted_by_tool = []
        for tool, qty in (
            ("trading", trading_qty),
            ("markt", markt_wanted.get(type_id)),
            ("material", material_wanted.get(type_id)),
            ("doctrine", doctrine_wanted.get(type_id)),
            ("ore_minerals", ore_minerals_wanted.get(type_id)),
        ):
            if qty:
                wanted_by_tool.append({"tool": tool, "wanted_qty": qty})
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
