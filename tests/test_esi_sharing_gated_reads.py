"""Caller-level sharing gates (decision 9): own_orders and reconciliation
must not live-fetch an owner that is not shared with Trading. Known gap 3
(docs/ESI_ACCESS_PLAN.md) is the same class of bug on the Production/
Sorting side - storage.py's own readers now take an owner-id filter (see
storage._owner_id_clause) and production/engine.py resolves it via
shared_production_owner_ids before calling them.

The accessor's own isolation test still stands; these cover the product
paths that previously swallowed AccessorError/RuntimeError and fell back
to unfiltered ESI, plus (Gap 3 section below) the storage-level readers
that never went through the accessor at all.
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
from eve_trader.production import engine as production_engine
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


# ---------------------------------------------------------------- gap 3
# Known gap 3 (docs/ESI_ACCESS_PLAN.md): Production's own storage.py readers
# never went through the accessor at all - storage.esi_stock_at_location/
# assets_at_flag/list_industry_jobs/load_owned_blueprints/sell_order_qty_*/
# get_owned_bpo_best_me_te/available_blueprint_copies/has_bpo_at_location/
# search_item_stock_locations now take an owner-id filter
# (storage._owner_id_clause), and production/engine.py resolves it via
# shared_production_owner_ids (esi_data.access.shared_owner_ids) before
# calling them - the same shape as own_orders/trade_reconciliation's own
# is_shared/read_esi gate above, just at the SQL level instead of the
# accessor's Python-level row filter (a per-type_id demand loop calling the
# accessor's read_esi would load every shared row on every iteration).
GAP3_ALICE = 3001
GAP3_BOB = 3002
GAP3_CORP = 4001


@pytest.fixture(autouse=True)
def _wipe_gap3_tables():
    pg_helpers.wipe_tables(
        "character_blueprints", "corp_blueprints",
        "character_industry_jobs", "corp_industry_jobs",
        "character_sell_orders", "corp_assets",
    )
    production_engine.invalidate_shared_production_owner_ids_cache(all_tenants=True)
    yield
    pg_helpers.wipe_tables(
        "character_blueprints", "corp_blueprints",
        "character_industry_jobs", "corp_industry_jobs",
        "character_sell_orders", "corp_assets",
    )
    production_engine.invalidate_shared_production_owner_ids_cache(all_tenants=True)


def test_esi_stock_at_location_owner_filter_excludes_unshared_character(tenant):
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, JITA_STATION, "Hangar", 5, 0, "Alice"),
    ], owner_character_id=GAP3_ALICE)
    storage.replace_assets("character_assets", [
        (2, TYPE_ID, JITA_STATION, "Hangar", 7, 0, "Bob"),
    ], owner_character_id=GAP3_BOB)

    unfiltered = storage.esi_stock_at_location(TYPE_ID, None)
    assert unfiltered == 12.0

    only_alice = storage.esi_stock_at_location(
        TYPE_ID, None, owner_character_ids=[GAP3_ALICE], owner_corporation_ids=[],
    )
    assert only_alice == 5.0

    nobody_shared = storage.esi_stock_at_location(
        TYPE_ID, None, owner_character_ids=[], owner_corporation_ids=[],
    )
    assert nobody_shared == 0.0


def test_assets_at_flag_owner_filter_excludes_unshared_corp(tenant):
    storage.replace_assets("corp_assets", [
        (1, TYPE_ID, JITA_STATION, "Hangar", 3, 0, "Test Corp"),
    ], owner_corporation_id=GAP3_CORP)

    unfiltered = storage.assets_at_flag("Hangar", tables=("corp_assets",))
    assert unfiltered == [(TYPE_ID, 3.0)]

    filtered_out = storage.assets_at_flag(
        "Hangar", tables=("corp_assets",),
        owner_character_ids=[], owner_corporation_ids=[],
    )
    assert filtered_out == []

    kept = storage.assets_at_flag(
        "Hangar", tables=("corp_assets",),
        owner_character_ids=[], owner_corporation_ids=[GAP3_CORP],
    )
    assert kept == [(TYPE_ID, 3.0)]


def test_list_industry_jobs_owner_filter_excludes_unshared_character(tenant):
    storage.replace_industry_jobs("character_industry_jobs", [
        (900, 1, 100, TYPE_ID, 1, None, "active", "2026-01-01T00:00:00Z",
         "2026-01-01T00:00:00Z", GAP3_ALICE, "Alice"),
    ], owner_character_id=GAP3_ALICE)
    storage.replace_industry_jobs("character_industry_jobs", [
        (901, 1, 100, TYPE_ID, 1, None, "active", "2026-01-01T00:00:00Z",
         "2026-01-01T00:00:00Z", GAP3_BOB, "Bob"),
    ], owner_character_id=GAP3_BOB)

    assert len(storage.list_industry_jobs()) == 2
    only_alice = storage.list_industry_jobs(
        owner_character_ids=[GAP3_ALICE], owner_corporation_ids=[],
    )
    assert [j[0] for j in only_alice] == [900]


def test_load_owned_blueprints_owner_filter_excludes_unshared_character(tenant):
    storage.replace_blueprints("character_blueprints", [
        (10, TYPE_ID, JITA_STATION, "Hangar", -1, 10, 20, -1),
    ], owner_character_id=GAP3_ALICE)
    storage.replace_blueprints("character_blueprints", [
        (11, TYPE_ID, JITA_STATION, "Hangar", -1, 0, 0, -1),
    ], owner_character_id=GAP3_BOB)

    assert len(storage.load_owned_blueprints()) == 2
    only_alice = storage.load_owned_blueprints(
        owner_character_ids=[GAP3_ALICE], owner_corporation_ids=[],
    )
    assert only_alice == [(TYPE_ID, -1, 10, 20, -1)]


def test_get_owned_bpo_best_me_te_owner_filter(tenant):
    storage.replace_blueprints("character_blueprints", [
        (10, TYPE_ID, JITA_STATION, "Hangar", -1, 10, 20, -1),
    ], owner_character_id=GAP3_ALICE)

    assert storage.get_owned_bpo_best_me_te(TYPE_ID) == (10, 20)
    assert storage.get_owned_bpo_best_me_te(
        TYPE_ID, owner_character_ids=[], owner_corporation_ids=[],
    ) is None
    assert storage.get_owned_bpo_best_me_te(
        TYPE_ID, owner_character_ids=[GAP3_ALICE], owner_corporation_ids=[],
    ) == (10, 20)


def test_available_blueprint_copies_and_has_bpo_owner_filter(tenant):
    storage.replace_blueprints("character_blueprints", [
        (10, TYPE_ID, JITA_STATION, "Hangar", -1, 0, 0, -1),   # BPO
        (11, TYPE_ID, JITA_STATION, "Hangar", -1, 4, 8, 5),    # BPC, 5 runs
    ], owner_character_id=GAP3_ALICE)

    assert storage.available_blueprint_copies(TYPE_ID, None) == 5.0
    assert storage.available_blueprint_copies(
        TYPE_ID, None, owner_character_ids=[], owner_corporation_ids=[],
    ) == 0.0

    assert storage.has_bpo_at_location(TYPE_ID, JITA_STATION) is True
    assert storage.has_bpo_at_location(
        TYPE_ID, JITA_STATION, owner_character_ids=[], owner_corporation_ids=[],
    ) is False


def test_sell_order_qty_owner_filter_excludes_unshared_corp(tenant):
    storage.replace_sell_orders([
        (5001, TYPE_ID, JITA_STATION, 10000002, 25.0, "Test Corp (corp)"),
    ], owner_corporation_id=GAP3_CORP, owner_name="Test Corp (corp)")

    assert storage.sell_order_qty_at_location(TYPE_ID, JITA_STATION) == 25.0
    assert storage.sell_order_qty_at_location(
        TYPE_ID, JITA_STATION, owner_character_ids=[], owner_corporation_ids=[],
    ) == 0.0
    assert storage.sell_order_qty_in_region(TYPE_ID, 10000002) == 25.0
    assert storage.sell_order_qty_in_region(
        TYPE_ID, 10000002, owner_character_ids=[], owner_corporation_ids=[GAP3_CORP],
    ) == 25.0


def test_shared_production_owner_ids_resolves_sharing_and_caches(tenant, monkeypatch):
    """production/engine.py's own resolver - real sharing rows in, and the
    process-wide cache (invalidate_shared_production_owner_ids_cache) must
    not serve a stale answer across a sharing toggle."""
    assert production_engine.shared_production_owner_ids("assets") == ([], [])

    _share("character", GAP3_ALICE, "assets", tool_key="production")
    _share("corporation", GAP3_CORP, "assets", tool_key="production")
    # Still cached from the call above - a toggle mid-request must not
    # silently apply until the cache is invalidated.
    assert production_engine.shared_production_owner_ids("assets") == ([], [])

    production_engine.invalidate_shared_production_owner_ids_cache()
    char_ids, corp_ids = production_engine.shared_production_owner_ids("assets")
    assert char_ids == [GAP3_ALICE]
    assert corp_ids == [GAP3_CORP]

    # A different data_kind is unaffected either way.
    assert production_engine.shared_production_owner_ids("blueprints") == ([], [])


def test_production_stock_helpers_respect_sharing_end_to_end(tenant):
    """The actual wiring (_stock_at_location -> shared_production_owner_ids
    -> esi_sharing), not just the storage-level filter in isolation."""
    production_engine.invalidate_shared_production_owner_ids_cache()
    storage.replace_assets("character_assets", [
        (1, TYPE_ID, JITA_STATION, "Hangar", 9, 0, "Alice"),
    ], owner_character_id=GAP3_ALICE)

    assert production_engine._stock_at_location(TYPE_ID, None) == 0.0

    _share("character", GAP3_ALICE, "assets", tool_key="production")
    production_engine.invalidate_shared_production_owner_ids_cache()

    assert production_engine._stock_at_location(TYPE_ID, None) == 9.0
