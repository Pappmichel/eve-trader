"""Caller-level sharing gates (decision 9): own_orders and reconciliation
must not live-fetch an owner that is not shared with Trading.

The accessor's own isolation test still stands; these cover the product
paths that previously swallowed AccessorError/RuntimeError and fell back
to unfiltered ESI.
"""
from __future__ import annotations

import datetime as dt

import pytest

from eve_trader import storage
from eve_trader.config import TradingConfig
from eve_trader.esi_data.access import AccessorError
from eve_trader.own_orders import (
    _character_assets,
    fetch_buyer_already_covered,
    fetch_seller_stock_without_order,
)
from eve_trader.trade_reconciliation import (
    collect_trading_wallet_streams,
    reconcile_realized_trades,
)

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_esi_access_schema, _apply_phase1_schema, _apply_phase2_schema, tenant,
)

psycopg = pytest.importorskip("psycopg")

pytestmark = pg_helpers.postgres_required()

ALICE = 1001
BOB = 1002
CORP = 2001
TYPE_ID = 34
JITA_STATION = 60003760


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables(
        "character_assets", "esi_sharing",
        "esi_wallet_transactions", "esi_wallet_journal",
    )
    yield
    pg_helpers.wipe_tables(
        "character_assets", "esi_sharing",
        "esi_wallet_transactions", "esi_wallet_journal",
    )


def _share(owner_type, owner_id, data_kind, tool_key="trading"):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            (owner_type, owner_id, data_kind, tool_key),
        )


def _iso(days_ago: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


class AssetClient:
    def __init__(self, assets_by_char=None):
        self.assets_by_char = assets_by_char or {}
        self.asset_calls: list[int] = []
        self.order_calls: list[int] = []

    def character_assets(self, character_id, auth_role):
        self.asset_calls.append(character_id)
        return list(self.assets_by_char.get(character_id, []))

    def character_orders(self, character_id, auth_role):
        self.order_calls.append(character_id)
        return []


# ------------------------------------------------------------------ own_orders


def test_unshared_character_assets_are_invisible_on_seller_unlisted_path(tenant):
    cfg = TradingConfig()
    live = [{"type_id": TYPE_ID, "quantity": 100, "location_id": cfg.structure_id,
             "location_flag": "Hangar"}]
    client = AssetClient({BOB: live})
    rows = fetch_seller_stock_without_order(BOB, "seller", client, {TYPE_ID}, cfg)
    assert rows == []
    assert client.asset_calls == []


def test_unshared_character_assets_are_invisible_on_buyer_covered_path(tenant, monkeypatch):
    cfg = TradingConfig()
    monkeypatch.setattr(storage, "get_station_ids_in_system", lambda _sid: frozenset({JITA_STATION}))
    live = [{"type_id": TYPE_ID, "quantity": 5, "location_id": JITA_STATION,
             "location_flag": "Hangar"}]
    client = AssetClient({BOB: live})
    covered = fetch_buyer_already_covered(BOB, "buyer", client, cfg)
    assert TYPE_ID not in covered
    assert client.asset_calls == []


def test_shared_character_with_empty_snapshot_live_fetches_assets(tenant):
    cfg = TradingConfig()
    _share("character", ALICE, "assets")
    live = [{"type_id": TYPE_ID, "quantity": 100, "location_id": cfg.structure_id,
             "location_flag": "Hangar"}]
    client = AssetClient({ALICE: live})
    rows = fetch_seller_stock_without_order(ALICE, "seller", client, {TYPE_ID}, cfg)
    assert rows == [{"type_id": TYPE_ID, "asset_quantity": 100,
                     "sell_order_remaining": 0.0, "unlisted_quantity": 100.0}]
    assert client.asset_calls == [ALICE]


def test_character_assets_accessor_raise_propagates(tenant, monkeypatch):
    _share("character", ALICE, "assets")

    def boom(*a, **k):
        raise AccessorError("boom")

    monkeypatch.setattr("eve_trader.esi_data.access.read_esi", boom)
    with pytest.raises(AccessorError, match="boom"):
        _character_assets(ALICE, "seller", AssetClient())


def test_character_assets_missing_tenant_propagates():
    with pytest.raises(RuntimeError, match="no current tenant"):
        _character_assets(ALICE, "seller", AssetClient())


# ---------------------------------------------------------- reconciliation


class WalletClient:
    def __init__(self, txns_by_char=None, character_corps=None, corp_txns=None):
        self.txns_by_char = txns_by_char or {}
        self.character_corps = character_corps or {}
        self.corp_txns = corp_txns or {}
        self.char_txn_calls: list[int] = []
        self.corp_txn_calls: list[tuple] = []

    def character_wallet_transactions(self, character_id, auth_role, from_id=None):
        self.char_txn_calls.append(character_id)
        return self.txns_by_char.get(character_id, []) if from_id is None else []

    def character_wallet_journal(self, character_id, auth_role):
        return []

    def character_public_info(self, character_id):
        corp_id = self.character_corps.get(character_id)
        return {"corporation_id": corp_id} if corp_id else {}

    def corporation_wallet_transactions(self, corporation_id, division, auth_role, from_id=None):
        self.corp_txn_calls.append((corporation_id, division, auth_role, from_id))
        if from_id is not None:
            return []
        return list(self.corp_txns.get((corporation_id, division), []))

    def corporation_wallet_journal(self, corporation_id, division, auth_role):
        return []


def _buy_txn(character_id, txn_id=1):
    return {
        "is_buy": True, "type_id": TYPE_ID, "date": _iso(2), "unit_price": 1000.0,
        "quantity": 10, "location_id": JITA_STATION, "transaction_id": txn_id,
        "journal_ref_id": txn_id,
    }


def _sell_txn(cfg, character_id, txn_id=2):
    return {
        "is_buy": False, "type_id": TYPE_ID, "date": _iso(1), "unit_price": 1200.0,
        "quantity": 10, "location_id": cfg.structure_id, "transaction_id": txn_id,
        "journal_ref_id": txn_id,
    }


def test_reconcile_omits_unshared_character_wallet(tenant, monkeypatch):
    monkeypatch.setattr(
        "eve_trader.trade_reconciliation.storage.get_station_ids_in_region",
        lambda region_id: frozenset({JITA_STATION}),
    )
    cfg = TradingConfig(lookback_days=30)
    _share("character", ALICE, "wallet")
    # Bob is a seller with live sells, but not shared — must not appear.
    client = WalletClient({
        ALICE: [_buy_txn(ALICE)],
        BOB: [_sell_txn(cfg, BOB)],
    })
    txns, journal = collect_trading_wallet_streams(
        [(ALICE, "buyer")], [(BOB, "seller")], client, cfg,
    )
    assert client.char_txn_calls == [ALICE]
    assert all(t.get("_wallet_owner_id") != BOB for t in txns)
    trades = reconcile_realized_trades(
        [(ALICE, "buyer")], [(BOB, "seller")],
        client, {TYPE_ID: "Widget"}, {TYPE_ID: 0.0}, cfg,
        snapshot_txns=txns, snapshot_journal=journal,
    )
    assert trades == []


def test_reconcile_omits_unshared_corporation_wallet(tenant, monkeypatch):
    monkeypatch.setattr(
        "eve_trader.trade_reconciliation.storage.get_station_ids_in_region",
        lambda region_id: frozenset({JITA_STATION}),
    )
    cfg = TradingConfig(lookback_days=30)
    _share("character", ALICE, "wallet")
    _share("character", BOB, "wallet")
    # Corp is discovered via Alice but not shared.
    corp_sell = {
        "is_buy": False, "type_id": TYPE_ID, "date": _iso(1), "unit_price": 1200.0,
        "quantity": 10, "location_id": cfg.structure_id, "transaction_id": 9,
        "journal_ref_id": 9,
    }
    client = WalletClient(
        txns_by_char={ALICE: [_buy_txn(ALICE)], BOB: []},
        character_corps={ALICE: CORP},
        corp_txns={(CORP, 1): [corp_sell]},
    )
    txns, journal = collect_trading_wallet_streams(
        [(ALICE, "buyer")], [(BOB, "seller")], client, cfg,
    )
    assert client.corp_txn_calls == []
    assert all(t.get("_wallet_owner_id") != CORP for t in txns)
    trades = reconcile_realized_trades(
        [(ALICE, "buyer")], [(BOB, "seller")],
        client, {TYPE_ID: "Widget"}, {TYPE_ID: 0.0}, cfg,
        snapshot_txns=txns, snapshot_journal=journal,
    )
    assert trades == []


def test_shared_corp_with_empty_snapshot_live_fetches(tenant, monkeypatch):
    monkeypatch.setattr(
        "eve_trader.trade_reconciliation.storage.get_station_ids_in_region",
        lambda region_id: frozenset({JITA_STATION}),
    )
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0,
                         import_cost_per_m3=0.0)
    _share("character", ALICE, "wallet")
    _share("corporation", CORP, "wallet")
    corp_sell = {
        "is_buy": False, "type_id": TYPE_ID, "date": _iso(1), "unit_price": 1200.0,
        "quantity": 10, "location_id": cfg.structure_id, "transaction_id": 9,
        "journal_ref_id": 9,
    }
    client = WalletClient(
        txns_by_char={ALICE: [_buy_txn(ALICE)]},
        character_corps={ALICE: CORP},
        corp_txns={(CORP, 1): [corp_sell]},
    )
    txns, journal = collect_trading_wallet_streams(
        [(ALICE, "buyer")], [(ALICE, "seller")], client, cfg,
    )
    assert any(c[0] == CORP for c in client.corp_txn_calls)
    trades = reconcile_realized_trades(
        [(ALICE, "buyer")], [(ALICE, "seller")],
        client, {TYPE_ID: "Widget"}, {TYPE_ID: 0.0}, cfg,
        snapshot_txns=txns, snapshot_journal=journal,
    )
    assert len(trades) == 1


def test_shared_wallet_with_empty_snapshot_live_fetches(tenant, monkeypatch):
    monkeypatch.setattr(
        "eve_trader.trade_reconciliation.storage.get_station_ids_in_region",
        lambda region_id: frozenset({JITA_STATION}),
    )
    cfg = TradingConfig(lookback_days=30, structure_sell_haircut=1.0, jita_buy_broker_fee=0.0,
                         import_cost_per_m3=0.0)
    _share("character", ALICE, "wallet")
    _share("character", BOB, "wallet")
    client = WalletClient({
        ALICE: [_buy_txn(ALICE)],
        BOB: [_sell_txn(cfg, BOB)],
    })
    txns, journal = collect_trading_wallet_streams(
        [(ALICE, "buyer")], [(BOB, "seller")], client, cfg,
    )
    assert sorted(client.char_txn_calls) == [ALICE, BOB]
    trades = reconcile_realized_trades(
        [(ALICE, "buyer")], [(BOB, "seller")],
        client, {TYPE_ID: "Widget"}, {TYPE_ID: 0.0}, cfg,
        snapshot_txns=txns, snapshot_journal=journal,
    )
    assert len(trades) == 1


def test_collect_wallet_accessor_raise_propagates(tenant, monkeypatch):
    _share("character", ALICE, "wallet")

    def boom(*a, **k):
        raise AccessorError("wallet boom")

    monkeypatch.setattr("eve_trader.esi_data.access.read_esi", boom)
    with pytest.raises(AccessorError, match="wallet boom"):
        collect_trading_wallet_streams(
            [(ALICE, "buyer")], [(ALICE, "seller")],
            WalletClient(), TradingConfig(),
        )


def test_collect_wallet_missing_tenant_propagates():
    with pytest.raises(RuntimeError, match="no current tenant"):
        collect_trading_wallet_streams(
            [(ALICE, "buyer")], [(BOB, "seller")],
            WalletClient(), TradingConfig(),
        )


def test_shared_wallet_snapshot_skips_live_esi(tenant, monkeypatch):
    """Phase 3a made reconcile a snapshot consumer; Phase 7 keeps do_pipeline
    off the live wallet page when rows exist.
    """
    monkeypatch.setattr(
        "eve_trader.trade_reconciliation.storage.get_station_ids_in_region",
        lambda region_id: frozenset({JITA_STATION}),
    )
    cfg = TradingConfig(lookback_days=30)
    _share("character", ALICE, "wallet")
    _share("character", BOB, "wallet")
    storage.replace_wallet_transactions(
        [(0, 1, _iso(2), TYPE_ID, JITA_STATION, 1000.0, 10, True, 1)],
        owner_type="character", owner_id=ALICE,
    )
    storage.replace_wallet_transactions(
        [(0, 2, _iso(1), TYPE_ID, JITA_STATION, 1200.0, 10, False, 2)],
        owner_type="character", owner_id=BOB,
    )
    storage.replace_wallet_journal(
        [(0, 2, _iso(1), "market_transaction", -12000.0)],
        owner_type="character", owner_id=BOB,
    )
    client = WalletClient({
        ALICE: [_buy_txn(ALICE)],
        BOB: [_sell_txn(cfg, BOB)],
    })
    txns, _journal = collect_trading_wallet_streams(
        [(ALICE, "buyer")], [(BOB, "seller")], client, cfg,
    )
    assert client.char_txn_calls == []
    assert client.corp_txn_calls == []
    assert {t["_wallet_owner_id"] for t in txns} == {ALICE, BOB}
