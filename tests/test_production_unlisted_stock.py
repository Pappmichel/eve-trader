import pytest

from eve_trader import storage
from eve_trader.auth import TokenManager
from eve_trader.esi_client import ESIClient
from eve_trader.production import actions
from eve_trader.production.config import ProductionConfig

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

# Only the 2 tests below need `tenant` + postgres_required() - they're the
# ones whose ESI-fetch fixtures actually produce assets that reach
# do_unlisted_stock's real storage.replace_assets()/replace_industry_jobs()
# write path; the other 3 in this file never get far enough to touch real
# storage (empty fixture data, or the early ActionError return).
psycopg = pytest.importorskip("psycopg")

HOME_LOCATION_ID = 1000000000001


def _asset(item_id, type_id, quantity, location_id, location_flag="Hangar"):
    return {"item_id": item_id, "type_id": type_id, "quantity": quantity,
            "location_id": location_id, "location_flag": location_flag}


def _sell_order(type_id, volume_remain, location_id):
    return {"type_id": type_id, "volume_remain": volume_remain, "location_id": location_id, "is_buy_order": False}


def test_returns_empty_when_home_location_not_configured():
    cfg = ProductionConfig(home_location_id=None)
    try:
        actions.do_unlisted_stock(cfg)
        assert False, "expected ActionError"
    except actions.ActionError:
        pass


@pg_helpers.postgres_required()
def test_live_fetches_assets_and_orders_and_flags_unlisted_stock(monkeypatch, tenant):
    cfg = ProductionConfig(home_location_id=HOME_LOCATION_ID)
    # type_id 99 deliberately has no matching entry here - it has stock and no
    # sell order too, but isn't a tracked stock target, so must be excluded.
    # Both configured targets have a home_market_stock target set (a pure
    # backup-only target, home/jita both None, is covered by a separate test
    # below and must never be flagged).
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [
        (1, "No Order At All", 0, 50, None),
        (2, "Fully Listed", 0, 50, None),
    ])
    monkeypatch.setattr(actions.esi_sync, "list_producer_characters",
                         lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(TokenManager, "__init__", lambda self, *a, **kw: None)

    def fake_character_assets(self, character_id, auth_role):
        return [
            _asset(1, 1, 100, HOME_LOCATION_ID),
            _asset(2, 2, 50, HOME_LOCATION_ID),
            _asset(3, 99, 10, HOME_LOCATION_ID),
        ]

    def fake_character_orders(self, character_id, auth_role):
        return [_sell_order(2, 20, HOME_LOCATION_ID)]

    def fake_character_public_info(self, character_id):
        return {"corporation_id": 500}

    def fake_corporation_assets(self, corporation_id, auth_role):
        return []

    def fake_corporation_orders(self, corporation_id, auth_role):
        return []

    monkeypatch.setattr(ESIClient, "character_assets", fake_character_assets)
    monkeypatch.setattr(ESIClient, "character_orders", fake_character_orders)
    monkeypatch.setattr(ESIClient, "character_public_info", fake_character_public_info)
    monkeypatch.setattr(ESIClient, "corporation_assets", fake_corporation_assets)
    monkeypatch.setattr(ESIClient, "corporation_orders", fake_corporation_orders)
    # GitHub issue #45: do_unlisted_stock now also fetches structure order
    # stats/margin for each unlisted row - not this test's concern, so
    # stub both out rather than let them hit the real ESI/Goonmetrics network.
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                         lambda self, structure_id, type_ids, auth_role: {})
    monkeypatch.setattr(actions, "item_margin_detail", lambda type_id, name, cfg: {"margin_home": None})

    result = actions.do_unlisted_stock(cfg)

    assert len(result["rows"]) == 1
    assert result["rows"][0].type_id == 1
    assert result["rows"][0].stock_quantity == 100.0


def test_corp_hangar_stock_with_only_a_corp_order_is_not_flagged_unlisted(monkeypatch):
    # Confirmed real bug: stock sitting in a corp hangar is often listed via
    # a *corp* sell order (funded by the corp wallet), not a personal one -
    # this must count as "listed", not get flagged as unlisted stock.
    cfg = ProductionConfig(home_location_id=HOME_LOCATION_ID)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(1, "Corp Hangar Item", 0, 50, None)])
    monkeypatch.setattr(actions.esi_sync, "list_producer_characters",
                         lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(TokenManager, "__init__", lambda self, *a, **kw: None)

    monkeypatch.setattr(ESIClient, "character_assets", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_orders", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_public_info",
                         lambda self, character_id: {"corporation_id": 500})
    monkeypatch.setattr(ESIClient, "corporation_assets",
                         lambda self, corporation_id, auth_role: [_asset(1, 1, 100, HOME_LOCATION_ID)])
    monkeypatch.setattr(ESIClient, "corporation_orders",
                         lambda self, corporation_id, auth_role: [_sell_order(1, 20, HOME_LOCATION_ID)])

    result = actions.do_unlisted_stock(cfg)

    assert result["rows"] == []


def test_backup_only_stock_target_is_never_flagged_as_unlisted(monkeypatch):
    # Confirmed real bug: a stock target with only backup_stock set (no
    # home_market_stock/jita_market_stock) is a personal/component buffer -
    # the user never intends to list it for sale, so physically having it
    # with no sell order is expected, not a problem to flag. E.g. every
    # Decryptor in the live config is backup-only and was wrongly showing up
    # here before this fix.
    cfg = ProductionConfig(home_location_id=HOME_LOCATION_ID)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [
        (1, "Augmentation Decryptor", 300, None, None),
    ])
    monkeypatch.setattr(actions.esi_sync, "list_producer_characters",
                         lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(TokenManager, "__init__", lambda self, *a, **kw: None)

    monkeypatch.setattr(ESIClient, "character_assets",
                         lambda self, character_id, auth_role: [_asset(1, 1, 100, HOME_LOCATION_ID)])
    monkeypatch.setattr(ESIClient, "character_orders", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_public_info",
                         lambda self, character_id: {"corporation_id": 500})
    monkeypatch.setattr(ESIClient, "corporation_assets", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_orders", lambda self, corporation_id, auth_role: [])

    result = actions.do_unlisted_stock(cfg)

    assert result["rows"] == []


@pg_helpers.postgres_required()
def test_corp_order_role_failure_does_not_block_corp_asset_role_success(monkeypatch, tenant):
    # Assets need Director, orders need Accountant/Trader - a character (or
    # every registered character) missing one of the two roles must not
    # prevent the other from being fetched and used.
    cfg = ProductionConfig(home_location_id=HOME_LOCATION_ID)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(1, "Corp Hangar Item", 0, 50, None)])
    monkeypatch.setattr(actions.esi_sync, "list_producer_characters",
                         lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(TokenManager, "__init__", lambda self, *a, **kw: None)

    from eve_trader.esi_client import ESIError

    monkeypatch.setattr(ESIClient, "character_assets", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_orders", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_public_info",
                         lambda self, character_id: {"corporation_id": 500})
    monkeypatch.setattr(ESIClient, "corporation_assets",
                         lambda self, corporation_id, auth_role: [_asset(1, 1, 100, HOME_LOCATION_ID)])

    def fail_corporation_orders(self, corporation_id, auth_role):
        raise ESIError("missing Accountant/Trader role")
    monkeypatch.setattr(ESIClient, "corporation_orders", fail_corporation_orders)
    # GitHub issue #45: same stub as the other live-fetch test above - not
    # this test's concern.
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                         lambda self, structure_id, type_ids, auth_role: {})
    monkeypatch.setattr(actions, "item_margin_detail", lambda type_id, name, cfg: {"margin_home": None})

    result = actions.do_unlisted_stock(cfg)

    # Corp assets were still found (Director role present) even though corp
    # orders failed (Accountant/Trader role missing) - correctly flagged as
    # unlisted since no order (personal or corp) could be found for it.
    assert len(result["rows"]) == 1
    assert result["rows"][0].type_id == 1
