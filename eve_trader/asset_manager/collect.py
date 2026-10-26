"""Loads every row the Asset Manager shows and enriches it once per request
(docs/ASSET_MANAGER_PLAN.md sections 4 and 13).

Sources: ESI assets (`esi`), manual stock (`manual`, Production data, only
with the `production` grant), open sell orders (`listed`), outstanding
item-exchange contracts (`contract`) and running job outputs
(`in_production`). Listed and contracted items are not in ESI's asset list,
so they are added, never subtracted (P-12).

Rows are plain dicts. `query.py` filters, groups, sorts and pages them; the
browser only ever gets one page (D8, implemented in Python over the loaded
rows instead of SQL - see the plan's review note on R-2).
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import storage
from . import labels
from .scope import ASSET_KINDS

log = logging.getLogger(__name__)

_CORP_SUFFIX = " (corp)"


def _owner_pairs(chars: list[int], corps: list[int]) -> list[tuple[str, int]]:
    return [("character", c) for c in chars] + [("corporation", c) for c in corps]


def _owner_names(pairs: set[tuple[str, int]], known: dict[tuple[str, int], str]) -> dict[tuple[str, int], str]:
    """Names without ESI: asset rows first, then the tenant's tokens, then
    names earlier syncs stored. An id found nowhere shows as the id."""
    out = {pair: name for pair, name in known.items() if name}
    missing = [pair for pair in pairs if pair not in out]
    if missing:
        try:
            from ..auth import TokenManager
            from ..config import OAUTH_CONFIG
            for rec in TokenManager(OAUTH_CONFIG).list_records():
                pair = ("character", int(rec.character_id)) if rec.character_id is not None else None
                if pair in pairs and pair not in out and rec.character_name:
                    out[pair] = str(rec.character_name)
        except Exception:  # noqa: BLE001 - a name lookup must not fail the page
            log.warning("asset manager: token character names unavailable", exc_info=True)
    missing = [pair for pair in pairs if pair not in out]
    if missing:
        try:
            from ..realized.db import stored_owner_names
            stored = stored_owner_names([oid for _t, oid in missing])
            for owner_type, oid in missing:
                if oid in stored:
                    suffix = _CORP_SUFFIX if owner_type == "corporation" else ""
                    out[(owner_type, oid)] = stored[oid] + suffix
        except Exception:  # noqa: BLE001
            log.warning("asset manager: stored owner names unavailable", exc_info=True)
    for owner_type, oid in pairs:
        out.setdefault((owner_type, oid), str(oid))
    return out


def _paths(assets: list[dict]) -> None:
    """Sets `ancestors` (item ids, nearest first), `container_path` (names,
    outermost first) and `hangar_flag` (the flag of the outermost item that
    is not an Office) on every ESI asset row, in place. The parent map is
    per owner: item ids of stacks are not unique across owners (P-02)."""
    index = {(a["owner_type"], a["owner_id"], a["item_id"]): a for a in assets}
    for row in assets:
        chain = [row]
        seen = {row["item_id"]}
        current = row
        while len(chain) <= 10:
            parent = index.get((row["owner_type"], row["owner_id"], current["location_id"]))
            if parent is None or parent["item_id"] in seen:
                break
            chain.append(parent)
            seen.add(parent["item_id"])
            current = parent
        visible = [c for c in chain if c["type_id"] != storage.OFFICE_TYPE_ID]
        row["ancestors"] = [c["item_id"] for c in chain[1:]]
        row["parent_item_id"] = chain[1]["item_id"] if len(chain) > 1 else None
        row["hangar_flag"] = (visible[-1] if visible else row)["location_flag"]
        row["container_chain"] = [c for c in reversed(visible[1:])]


def history_rows(scope: dict[str, tuple[list[int], list[int]]]) -> list[tuple]:
    """What one day of history stores (plan phase 6): the owners' ESI asset
    quantities summed per owner, outermost location (0 = unknown) and type.
    No enrichment and no pricing."""
    chars, corps = scope.get("assets", ([], []))
    sums: dict[tuple, float] = {}
    for a in storage.load_asset_manager_assets(chars, corps):
        if a["type_id"] is None or a["type_id"] == storage.OFFICE_TYPE_ID:
            continue
        key = (a["owner_type"], a["owner_id"], a["resolved_location_id"] or a["location_id"] or 0, a["type_id"])
        sums[key] = sums.get(key, 0.0) + a["quantity"]
    return [(*key, qty) for key, qty in sorted(sums.items())]


def load_base_rows(scope: dict[str, tuple[list[int], list[int]]], *, include_manual: bool,
                   division_names: Optional[dict[tuple[int, int], str]] = None) -> list[dict]:
    """Every row of every source for the owners in `scope`, enriched with
    type, location, owner, path, badges, volume and value."""
    for kind in ASSET_KINDS:
        scope.setdefault(kind, ([], []))
    asset_chars, asset_corps = scope["assets"]
    assets = storage.load_asset_manager_assets(asset_chars, asset_corps)
    blueprints = storage.load_asset_manager_blueprints(*scope["blueprints"])
    if division_names is None:
        division_names = {
            (int(corp), int(division)): name
            for corp, kind, division, name in storage.load_corp_divisions(scope["corp_divisions"][1])
            if kind == "hangar" and name
        }
    _paths(assets)

    rows: list[dict] = []
    for a in assets:
        if a["type_id"] == storage.OFFICE_TYPE_ID:
            # A corp Office is ESI's container for the division hangars, not
            # an item; its contents carry the division as their hangar (P-07).
            continue
        bp = blueprints.get((a["owner_type"], a["owner_id"], a["item_id"]))
        rows.append({
            "source": "esi", "owner_type": a["owner_type"], "owner_id": a["owner_id"],
            "owner_name": a["owner_name"], "item_id": a["item_id"], "type_id": a["type_id"],
            "quantity": float(a["quantity"]),
            "location_id": a["resolved_location_id"] or a["location_id"],
            "flag": a["location_flag"], "hangar_flag": a["hangar_flag"],
            "parent_item_id": a["parent_item_id"], "ancestors": a["ancestors"],
            "container_chain": [(c["item_id"], c["type_id"], c["item_name"]) for c in a["container_chain"]],
            "item_name": a["item_name"], "is_singleton": a["is_singleton"],
            "assembled_or_fitted": a["assembled_or_fitted"],
            "is_copy": bool(bp[2] != -1) if bp else a["is_blueprint_copy"],
            "me": bp[0] if bp else None, "te": bp[1] if bp else None,
            "runs": (bp[2] if bp and bp[2] != -1 else None),
            "has_blueprint_info": bp is not None,
        })

    if include_manual:
        for type_id, _name, location_id, count in storage.load_manual_stock_entries():
            if not count:
                continue
            rows.append(_virtual("manual", "manual", 0, "Manual stock", type_id, count, location_id or None))

    listed = storage.load_sell_orders_grouped(*scope["market_orders"])
    for owner_type, owner_id, location_id, type_id, quantity in listed:
        rows.append(_virtual("listed", owner_type, owner_id, None, type_id, quantity, location_id))

    contract_owners = _owner_pairs(*scope["contracts"])
    if contract_owners:
        from ..realized.db import outstanding_contract_lines
        for owner_type, owner_id, location_id, type_id, quantity in outstanding_contract_lines(
                contract_owners, with_location=True):
            rows.append(_virtual("contract", owner_type, owner_id, None, type_id, quantity, location_id))

    # A corp job's output location is often the corp Office item rather than the
    # structure it stands in, and an Office has no name of its own to resolve.
    office_locations = {
        (a["owner_id"], a["item_id"]): a["location_id"]
        for a in assets if a["type_id"] == storage.OFFICE_TYPE_ID and a["owner_type"] == "corporation"
    }
    for job in storage.load_asset_manager_job_outputs(*scope["industry_jobs"]):
        location_id = job["location_id"]
        if job["owner_type"] == "corporation":
            location_id = office_locations.get((job["owner_id"], location_id), location_id)
        row = _virtual("in_production", job["owner_type"], job["owner_id"], None,
                       job["type_id"], job["quantity"], location_id)
        row["job_end"] = job["end_date"]
        row["job_status"] = job["status"]
        rows.append(row)

    _enrich(rows, division_names or {})
    return rows


def _virtual(source: str, owner_type: str, owner_id: int, owner_name: Optional[str],
             type_id: int, quantity: float, location_id: Optional[int]) -> dict:
    return {
        "source": source, "owner_type": owner_type, "owner_id": owner_id, "owner_name": owner_name,
        "item_id": None, "type_id": int(type_id), "quantity": float(quantity),
        "location_id": location_id, "flag": None, "hangar_flag": None, "parent_item_id": None,
        "ancestors": [], "container_chain": [], "item_name": None, "is_singleton": False,
        "assembled_or_fitted": False, "is_copy": False, "me": None, "te": None, "runs": None,
        "has_blueprint_info": False,
    }


def _enrich(rows: list[dict], division_names: dict[tuple[int, int], str]) -> None:
    from ..portfolio import price_items

    type_ids = {r["type_id"] for r in rows if r["type_id"] is not None}
    for r in rows:
        type_ids.update(t for _i, t, _n in r["container_chain"] if t is not None)
    types = storage.asset_manager_type_info(type_ids)
    locations = storage.asset_manager_location_details(r["location_id"] for r in rows)
    known_names = {
        (r["owner_type"], r["owner_id"]): r["owner_name"]
        for r in rows if r["owner_name"] and r["source"] == "esi"
    }
    pairs = {(r["owner_type"], r["owner_id"]) for r in rows if r["owner_type"] != "manual"}
    owner_names = _owner_names(pairs, known_names)

    divisions_by_corp: dict[int, dict[int, str]] = {}
    for (corp, number), name in division_names.items():
        divisions_by_corp.setdefault(corp, {})[number] = name

    for index, r in enumerate(rows):
        info = types.get(r["type_id"]) or {}
        r["type_name"] = info.get("type_name") or f"Type {r['type_id']}"
        r["group_id"] = info.get("group_id")
        r["group_name"] = info.get("group_name")
        r["category_id"] = info.get("category_id")
        r["category_name"] = info.get("category_name")
        is_blueprint = info.get("category_id") == 9
        r["is_blueprint"] = is_blueprint
        if r["owner_type"] != "manual":
            r["owner_name"] = owner_names[(r["owner_type"], r["owner_id"])]
        loc = locations.get(r["location_id"]) if r["location_id"] else None
        loc = loc or {}
        r["location_name"] = loc.get("name")
        r["solar_system_id"] = loc.get("solar_system_id")
        r["solar_system_name"] = loc.get("solar_system_name")
        r["security"] = _security(loc.get("security"))
        r["region_id"] = loc.get("region_id")
        r["region_name"] = loc.get("region_name")
        r["location_kind"] = labels.location_kind(r["location_id"], bool(loc.get("name")))
        corp_id = r["owner_id"] if r["owner_type"] == "corporation" else None
        divisions = divisions_by_corp.get(corp_id, {})
        r["hangar"] = labels.hangar_label(r["hangar_flag"], divisions) if r["source"] == "esi" else None
        r["container_path"] = [
            name or (types.get(t) or {}).get("type_name") or f"Item {item_id}"
            for item_id, t, name in r["container_chain"]
        ]
        if r["source"] == "esi":
            r["badges"] = labels.badges_for_asset(
                r["flag"], r["hangar_flag"], r["assembled_or_fitted"], is_blueprint, r["is_copy"])
        else:
            r["badges"] = [r["source"]]
        r["stock"] = labels.is_stock(r["source"], r["badges"])
        volume = info.get("volume")
        packaged = info.get("packaged_volume")
        unit_volume = volume if r["is_singleton"] or packaged is None else packaged
        r["unit_volume"] = float(unit_volume) if unit_volume is not None else None
        r["volume"] = r["unit_volume"] * r["quantity"] if r["unit_volume"] is not None else None
        r["key"] = f"{r['source']}:{r['owner_type']}:{r['owner_id']}:{r['item_id'] or index}"
        # Valuation input for portfolio.price_items: a copy is valued only by
        # a manual price or its invention cost (D3).
        r["manual_only"] = bool(is_blueprint and r["is_copy"])

    price_items(rows)
    for r in rows:
        r["unit_price"] = r.pop("price")
        r["value"] = r["unit_price"] * r["quantity"] if r["unit_price"] is not None else None
        del r["manual_only"]


def _security(value) -> Optional[float]:
    if value is None:
        return None
    from ..production.constants import _rounded_security
    return _rounded_security(float(value))
