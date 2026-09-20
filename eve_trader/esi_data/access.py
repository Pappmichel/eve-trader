"""Fail-closed ESI snapshot accessor (docs/ESI_ACCESS_PLAN.md decision 9).

A consuming `tool_key` is required. Missing or unknown raises — same spirit
as `storage.connect()` refusing to open a connection with no tenant scope.
Rows whose `(owner_type, owner_id, data_kind, tool_key)` has no sharing row
are not returned. No "but Production historically read this" exception.

This module imports no tool package. Isolation is two layers: tenant_id
(RLS, whose data) and the sharing relation (which of this tenant's owners
this tool may read). They must not be conflated.
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from ..access_gate import ALL_TOOL_KEYS
from .registry import OWNED_DATA_KINDS

_KIND_BY_KEY = {k.key: k for k in OWNED_DATA_KINDS}


class AccessorError(RuntimeError):
    """Raised when `read_esi` is called without a usable consuming tool_key."""


def _require_tool_key(tool_key: Optional[str]) -> str:
    if tool_key is None or tool_key == "":
        raise AccessorError(
            "read_esi requires a consuming tool_key - refusing an unfiltered "
            "ESI snapshot read rather than risk returning rows no tool is "
            "shared with"
        )
    if tool_key not in ALL_TOOL_KEYS:
        raise AccessorError(f"unknown tool_key {tool_key!r}")
    return tool_key


def _shared_owner_ids(data_kind: str, tool_key: str, owner_type: str) -> list[int]:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT owner_id FROM esi_sharing "
            "WHERE data_kind = ? AND tool_key = ? AND owner_type = ?",
            (data_kind, tool_key, owner_type),
        ).fetchall()
    return [int(r[0]) for r in rows]


def _select_in(conn, sql: str, ids: list[int], extra: tuple = ()) -> list[tuple]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    return conn.execute(sql.format(placeholders=placeholders), extra + tuple(ids)).fetchall()


def read_esi(data_kind: str, tool_key: str, **filters) -> list[dict]:
    """Return snapshot rows this `tool_key` is shared to read.

    `tool_key` is required (keyword-only in spirit: passing None/omitting
    via a default is a raise, not an unfiltered read). Optional filters:
    `owner_type`, `owner_id`. Unknown `data_kind` raises.
    """
    tool_key = _require_tool_key(tool_key)
    kind = _KIND_BY_KEY.get(data_kind)
    if kind is None:
        raise AccessorError(f"unknown data_kind {data_kind!r}")

    owner_type_filter = filters.get("owner_type")
    owner_id_filter = filters.get("owner_id")

    rows: list[dict] = []
    if data_kind == "assets":
        rows.extend(_read_assets(tool_key, owner_type_filter, owner_id_filter))
    elif data_kind == "industry_jobs":
        rows.extend(_read_jobs(tool_key, owner_type_filter, owner_id_filter))
    elif data_kind == "blueprints":
        rows.extend(_read_blueprints(tool_key, owner_type_filter, owner_id_filter))
    elif data_kind == "market_orders":
        rows.extend(_read_orders(tool_key, owner_type_filter, owner_id_filter))
    elif data_kind == "wallet":
        rows.extend(_read_wallet(tool_key, owner_type_filter, owner_id_filter, filters.get("table")))
    elif data_kind == "contracts":
        rows.extend(_read_contracts(tool_key, owner_type_filter, owner_id_filter))
    elif data_kind == "skills":
        rows.extend(_read_skills(tool_key, owner_id_filter))
    else:
        raise AccessorError(f"unknown data_kind {data_kind!r}")
    return rows


def _filter_ids(ids: list[int], owner_id_filter) -> list[int]:
    if owner_id_filter is None:
        return ids
    oid = int(owner_id_filter)
    return [i for i in ids if i == oid]


def _read_assets(tool_key: str, owner_type, owner_id) -> list[dict]:
    # Until Phase 3b, Doctrine still reads its own tables. Other consumers
    # read the shared character_assets / corp_assets pair.
    if tool_key == "doctrine":
        char_table, corp_table = "doctrine_character_assets", "doctrine_corp_assets"
    else:
        char_table, corp_table = "character_assets", "corp_assets"
    out: list[dict] = []
    cols = (
        "item_id, type_id, location_id, location_flag, quantity, "
        "is_blueprint_copy, owner_name, resolved_location_id, resolved_hangar_flag, "
        "owner_character_id, owner_corporation_id"
    )
    with storage.connect() as conn:
        if owner_type in (None, "character"):
            ids = _filter_ids(_shared_owner_ids("assets", tool_key, "character"), owner_id)
            for r in _select_in(
                conn,
                f"SELECT {cols} FROM {char_table} WHERE owner_character_id IN ({{placeholders}})",
                ids,
            ):
                out.append(_asset_dict(r, "character"))
        if owner_type in (None, "corporation"):
            ids = _filter_ids(_shared_owner_ids("assets", tool_key, "corporation"), owner_id)
            for r in _select_in(
                conn,
                f"SELECT {cols} FROM {corp_table} WHERE owner_corporation_id IN ({{placeholders}})",
                ids,
            ):
                out.append(_asset_dict(r, "corporation"))
    return out


def _asset_dict(r, owner_type: str) -> dict:
    return {
        "item_id": r[0], "type_id": r[1], "location_id": r[2], "location_flag": r[3],
        "quantity": r[4], "is_blueprint_copy": r[5], "owner_name": r[6],
        "resolved_location_id": r[7], "resolved_hangar_flag": r[8],
        "owner_character_id": r[9], "owner_corporation_id": r[10],
        "owner_type": owner_type,
        "owner_id": r[9] if owner_type == "character" else r[10],
    }


def _read_jobs(tool_key: str, owner_type, owner_id) -> list[dict]:
    out: list[dict] = []
    cols = (
        "job_id, activity_id, blueprint_type_id, product_type_id, runs, "
        "output_location_id, status, end_date, start_date, installer_id, installer_name, "
        "owner_character_id, owner_corporation_id"
    )
    with storage.connect() as conn:
        if owner_type in (None, "character"):
            ids = _filter_ids(_shared_owner_ids("industry_jobs", tool_key, "character"), owner_id)
            for r in _select_in(
                conn,
                f"SELECT {cols} FROM character_industry_jobs WHERE owner_character_id IN ({{placeholders}})",
                ids,
            ):
                out.append(_job_dict(r, "character"))
        if owner_type in (None, "corporation"):
            ids = _filter_ids(_shared_owner_ids("industry_jobs", tool_key, "corporation"), owner_id)
            for r in _select_in(
                conn,
                f"SELECT {cols} FROM corp_industry_jobs WHERE owner_corporation_id IN ({{placeholders}})",
                ids,
            ):
                out.append(_job_dict(r, "corporation"))
    return out


def _job_dict(r, owner_type: str) -> dict:
    return {
        "job_id": r[0], "activity_id": r[1], "blueprint_type_id": r[2],
        "product_type_id": r[3], "runs": r[4], "output_location_id": r[5],
        "status": r[6], "end_date": r[7], "start_date": r[8],
        "installer_id": r[9], "installer_name": r[10],
        "owner_character_id": r[11], "owner_corporation_id": r[12],
        "owner_type": owner_type,
        "owner_id": r[11] if owner_type == "character" else r[12],
    }


def _read_blueprints(tool_key: str, owner_type, owner_id) -> list[dict]:
    out: list[dict] = []
    cols = (
        "item_id, type_id, location_id, location_flag, quantity, "
        "material_efficiency, time_efficiency, runs, resolved_location_id, "
        "owner_character_id, owner_corporation_id"
    )
    with storage.connect() as conn:
        if owner_type in (None, "character"):
            ids = _filter_ids(_shared_owner_ids("blueprints", tool_key, "character"), owner_id)
            for r in _select_in(
                conn,
                f"SELECT {cols} FROM character_blueprints WHERE owner_character_id IN ({{placeholders}})",
                ids,
            ):
                out.append(_bp_dict(r, "character"))
        if owner_type in (None, "corporation"):
            ids = _filter_ids(_shared_owner_ids("blueprints", tool_key, "corporation"), owner_id)
            for r in _select_in(
                conn,
                f"SELECT {cols} FROM corp_blueprints WHERE owner_corporation_id IN ({{placeholders}})",
                ids,
            ):
                out.append(_bp_dict(r, "corporation"))
    return out


def _bp_dict(r, owner_type: str) -> dict:
    return {
        "item_id": r[0], "type_id": r[1], "location_id": r[2], "location_flag": r[3],
        "quantity": r[4], "material_efficiency": r[5], "time_efficiency": r[6],
        "runs": r[7], "resolved_location_id": r[8],
        "owner_character_id": r[9], "owner_corporation_id": r[10],
        "owner_type": owner_type,
        "owner_id": r[9] if owner_type == "character" else r[10],
    }


def _read_orders(tool_key: str, owner_type, owner_id) -> list[dict]:
    out: list[dict] = []
    with storage.connect() as conn:
        if owner_type in (None, "character"):
            ids = _filter_ids(_shared_owner_ids("market_orders", tool_key, "character"), owner_id)
            for r in _select_in(
                conn,
                "SELECT order_id, type_id, location_id, region_id, volume_remain, character_name, "
                "owner_character_id, owner_corporation_id FROM character_sell_orders "
                "WHERE owner_character_id IN ({placeholders})",
                ids,
            ):
                out.append(_order_dict(r, "character"))
        if owner_type in (None, "corporation"):
            ids = _filter_ids(_shared_owner_ids("market_orders", tool_key, "corporation"), owner_id)
            for r in _select_in(
                conn,
                "SELECT order_id, type_id, location_id, region_id, volume_remain, character_name, "
                "owner_character_id, owner_corporation_id FROM character_sell_orders "
                "WHERE owner_corporation_id IN ({placeholders})",
                ids,
            ):
                out.append(_order_dict(r, "corporation"))
    return out


def _order_dict(r, owner_type: str) -> dict:
    return {
        "order_id": r[0], "type_id": r[1], "location_id": r[2], "region_id": r[3],
        "volume_remain": r[4], "character_name": r[5],
        "owner_character_id": r[6], "owner_corporation_id": r[7],
        "owner_type": owner_type,
        "owner_id": r[6] if owner_type == "character" else r[7],
    }


def _read_wallet(tool_key: str, owner_type, owner_id, table: Optional[str]) -> list[dict]:
    """`table` is `'transactions'` (default, both tables when None is
    transactions+journal via two calls from reconcile) or `'journal'`."""
    want = table or "transactions"
    out: list[dict] = []
    with storage.connect() as conn:
        for ot in (("character", "corporation") if owner_type is None else (owner_type,)):
            ids = _filter_ids(_shared_owner_ids("wallet", tool_key, ot), owner_id)
            id_col = "owner_character_id" if ot == "character" else "owner_corporation_id"
            if want == "journal":
                for r in _select_in(
                    conn,
                    f"SELECT owner_type, owner_id, division, journal_id, date, ref_type, amount, "
                    f"owner_character_id, owner_corporation_id FROM esi_wallet_journal "
                    f"WHERE {id_col} IN ({{placeholders}})",
                    ids,
                ):
                    out.append({
                        "owner_type": r[0], "owner_id": int(r[1]), "division": r[2],
                        "journal_id": r[3], "date": r[4], "ref_type": r[5], "amount": r[6],
                        "owner_character_id": r[7], "owner_corporation_id": r[8],
                    })
            else:
                for r in _select_in(
                    conn,
                    f"SELECT owner_type, owner_id, division, transaction_id, date, type_id, "
                    f"location_id, unit_price, quantity, is_buy, journal_ref_id, "
                    f"owner_character_id, owner_corporation_id FROM esi_wallet_transactions "
                    f"WHERE {id_col} IN ({{placeholders}})",
                    ids,
                ):
                    out.append({
                        "owner_type": r[0], "owner_id": int(r[1]), "division": r[2],
                        "transaction_id": r[3], "date": r[4], "type_id": r[5],
                        "location_id": r[6], "unit_price": r[7], "quantity": r[8],
                        "is_buy": r[9], "journal_ref_id": r[10],
                        "owner_character_id": r[11], "owner_corporation_id": r[12],
                    })
    return out


def _read_contracts(tool_key: str, owner_type, owner_id) -> list[dict]:
    out: list[dict] = []
    cols = (
        "contract_id, source_role, for_corporation, issuer_id, start_location_id, "
        "status, title, price, date_expired, matched_fitting_id, match_score, "
        "validation_status, synced_at, owner_character_id, owner_corporation_id"
    )
    with storage.connect() as conn:
        if owner_type in (None, "character"):
            ids = _filter_ids(_shared_owner_ids("contracts", tool_key, "character"), owner_id)
            for r in _select_in(
                conn,
                f"SELECT {cols} FROM doctrine_contracts WHERE owner_character_id IN ({{placeholders}})",
                ids,
            ):
                out.append(_contract_dict(r, "character"))
        if owner_type in (None, "corporation"):
            ids = _filter_ids(_shared_owner_ids("contracts", tool_key, "corporation"), owner_id)
            for r in _select_in(
                conn,
                f"SELECT {cols} FROM doctrine_contracts WHERE owner_corporation_id IN ({{placeholders}})",
                ids,
            ):
                out.append(_contract_dict(r, "corporation"))
    return out


def _contract_dict(r, owner_type: str) -> dict:
    return {
        "contract_id": r[0], "source_role": r[1], "for_corporation": r[2],
        "issuer_id": r[3], "start_location_id": r[4], "status": r[5],
        "title": r[6], "price": r[7], "date_expired": r[8],
        "matched_fitting_id": r[9], "match_score": r[10],
        "validation_status": r[11], "synced_at": r[12],
        "owner_character_id": r[13], "owner_corporation_id": r[14],
        "owner_type": owner_type,
        "owner_id": r[13] if owner_type == "character" else r[14],
    }


def _read_skills(tool_key: str, owner_id) -> list[dict]:
    ids = _filter_ids(_shared_owner_ids("skills", tool_key, "character"), owner_id)
    out: list[dict] = []
    with storage.connect() as conn:
        for r in _select_in(
            conn,
            "SELECT character_name, manufacturing_slots, reaction_slots, science_slots, "
            "excluded_from_planning, owner_character_id FROM character_slots "
            "WHERE owner_character_id IN ({placeholders})",
            ids,
        ):
            out.append({
                "character_name": r[0], "manufacturing_slots": r[1],
                "reaction_slots": r[2], "science_slots": r[3],
                "excluded_from_planning": r[4],
                "owner_character_id": r[5], "owner_type": "character",
                "owner_id": r[5],
            })
    return out
