"""Conservative sharing backfill (decision 13) and sorting_intake owner ids."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from eve_trader import storage
from eve_trader.auth import TokenRecord
from eve_trader.esi_data.backfill import backfill_conservative_sharing

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

_SORTING_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "docs" / "sorting_schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_sorting_then_esi(_apply_phase1_schema, _apply_phase2_schema, _apply_esi_access_schema):
    if not pg_helpers._postgres_available():
        return
    with psycopg.connect(pg_helpers.OWNER_DSN, autocommit=True) as conn:
        conn.execute(_SORTING_SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute(pg_helpers._ESI_ACCESS_SCHEMA_SQL.read_text())


@pytest.fixture(autouse=True)
def _wipe(tenant, _apply_sorting_then_esi):
    pg_helpers.wipe_tables(
        "esi_sharing", "esi_character_capabilities", "tenant_tokens",
        "sorting_intake_sources", "character_assets", "character_slots",
    )
    yield


def _token(role: str, character_id: int, name: str) -> None:
    storage.save_tenant_token(role, asdict(TokenRecord(
        role=role, character_id=character_id, character_name=name,
        access_token="a", refresh_token="r", expires_at=9999999999.0, scopes="",
    )))


def _sharing_rows():
    with storage.connect() as conn:
        return conn.execute(
            "SELECT owner_type, owner_id, data_kind, tool_key FROM esi_sharing "
            "ORDER BY owner_type, owner_id, data_kind, tool_key"
        ).fetchall()


def test_producer_and_doctrine_assets_same_character_share_assets_with_both_not_sorting(tenant):
    _token("producer:42", 42, "Pilot")
    _token("doctrine-assets:42", 42, "Pilot")
    result = backfill_conservative_sharing(corporation_ids={42: 99})

    rows = _sharing_rows()
    asset_tools = {tool for owner_type, owner_id, kind, tool in rows
                   if owner_type == "character" and owner_id == 42 and kind == "assets"}
    assert asset_tools == {"production", "doctrine"}
    assert all(tool != "sorting" for *_, tool in rows)
    assert all(tool != "trading" for owner_type, owner_id, kind, tool in rows
               if kind == "assets")
    corp_assets = {tool for owner_type, owner_id, kind, tool in rows
                   if owner_type == "corporation" and owner_id == 99 and kind == "assets"}
    assert corp_assets == {"production", "doctrine"}
    assert result["tokens"] == 2


def test_backfill_is_idempotent(tenant):
    _token("buyer:7", 7, "Buyer")
    first = backfill_conservative_sharing()
    second = backfill_conservative_sharing()
    with storage.connect() as conn:
        count = conn.execute("SELECT count(*) FROM esi_sharing").fetchone()[0]
    assert count == 3  # market_orders, wallet, assets
    assert first["sharing_inserts_attempted"] == second["sharing_inserts_attempted"]


def test_buyer_does_not_write_corp_sharing_or_sorting(tenant):
    _token("buyer:7", 7, "Buyer")
    backfill_conservative_sharing(corporation_ids={7: 100})
    rows = _sharing_rows()
    assert all(owner_type == "character" for owner_type, *_ in rows)
    assert {kind for _, _, kind, _ in rows} == {"market_orders", "wallet", "assets"}
    with storage.connect() as conn:
        caps = conn.execute(
            "SELECT capability_key FROM esi_character_capabilities WHERE character_id = ?",
            (7,),
        ).fetchall()
    assert caps == [("structure_market_book",)]


def test_sorting_intake_matched_character_and_unmatched_logged_not_deleted(tenant, caplog):
    _token("producer:42", 42, "Pilot")
    storage.add_sorting_intake_source("character", "Hangar", owner_name="Pilot", label="matched")
    storage.add_sorting_intake_source("character", "Hangar", owner_name="Stranger", label="orphan")
    with caplog.at_level("WARNING", logger="eve_trader.esi_data.backfill"):
        result = backfill_conservative_sharing()
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT owner_name, owner_character_id FROM sorting_intake_sources ORDER BY label"
        ).fetchall()
    by_name = {name: cid for name, cid in rows}
    assert by_name["Pilot"] == 42
    assert by_name["Stranger"] is None
    assert "Stranger" in caplog.text
    assert "not deleting" in caplog.text
    assert result["unmatched_sorting_intake_ids"]
    # Both rows still present.
    assert len(rows) == 2


def test_buyer_and_seller_same_character_do_not_duplicate_trading_rows(tenant):
    _token("buyer:7", 7, "Buyer")
    _token("seller:7", 7, "Buyer")
    backfill_conservative_sharing()
    rows = _sharing_rows()
    assert len(rows) == 3
    assert {kind for _, _, kind, tool in rows} == {"market_orders", "wallet", "assets"}
    assert {tool for *_, tool in rows} == {"trading"}


def test_access_capabilities_are_flags_not_sharing_rows(tenant):
    _token("buyer:7", 7, "Buyer")
    _token("producer:42", 42, "Pilot")
    backfill_conservative_sharing()
    with storage.connect() as conn:
        kinds = {
            kind for (_, _, kind, _) in conn.execute(
                "SELECT owner_type, owner_id, data_kind, tool_key FROM esi_sharing"
            ).fetchall()
        }
        caps = {
            cap for (cap,) in conn.execute(
                "SELECT capability_key FROM esi_character_capabilities"
            ).fetchall()
        }
    assert "structure_market_book" not in kinds
    assert "structure_name_resolution" not in kinds
    assert caps == {"structure_market_book", "structure_name_resolution"}


def test_character_asset_owner_id_filled_from_token_name(tenant):
    _token("producer:42", 42, "Pilot")
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO character_assets (item_id, type_id, location_id, location_flag, "
            "quantity, is_blueprint_copy, owner_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, 34, 60003760, "Hangar", 1, 0, "Pilot"),
        )
    backfill_conservative_sharing()
    with storage.connect() as conn:
        cid = conn.execute(
            "SELECT owner_character_id FROM character_assets WHERE item_id = ?", (1,),
        ).fetchone()[0]
    assert cid == 42
