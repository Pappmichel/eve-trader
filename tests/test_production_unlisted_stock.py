import pytest

from eve_trader import storage
from eve_trader.auth import TokenManager
from eve_trader.esi_client import ESIClient
from eve_trader.production import actions
from eve_trader.production.config import ProductionConfig

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401


def _stub_unlisted_margin(monkeypatch, raise_exc=None):
    """do_unlisted_stock now builds one _PlanContext then calls
    _item_margin_detail_with_context per row - stub both so tests that
    only care about the ESI asset/order path never hit Goonmetrics."""
    class _FakeCtx:
        def __init__(self, cfg, extra_type_ids=()):
            pass
    monkeypatch.setattr(actions, "_PlanContext", _FakeCtx)
    if raise_exc is None:
        monkeypatch.setattr(actions, "_item_margin_detail_with_context",
                            lambda *a, **k: {"margin_home": None})
    else:
        def _raise(*a, **k):
            raise raise_exc
        monkeypatch.setattr(actions, "_item_margin_detail_with_context", _raise)


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
    _stub_unlisted_margin(monkeypatch)

    result = actions.do_unlisted_stock(cfg)

    assert len(result["rows"]) == 1
    assert result["rows"][0].type_id == 1
    assert result["rows"][0].stock_quantity == 100.0


@pg_helpers.postgres_required()
def test_margin_lookup_goonmetrics_failure_degrades_to_none_not_500(monkeypatch, tenant):
    # Found in code review of PR #71: item_margin_detail's _PlanContext build
    # also calls Goonmetrics directly (unlike every other ESI call in this
    # function, whose own client wraps transport failures into ESIError) - a
    # requests.RequestException from Goonmetrics used to propagate uncaught
    # past this best-effort degrade, turning "margin unknown for this one
    # row" into a 500 for the whole page.
    import requests

    cfg = ProductionConfig(home_location_id=HOME_LOCATION_ID)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(1, "No Order At All", 0, 50, None)])
    monkeypatch.setattr(actions.esi_sync, "list_producer_characters",
                         lambda: [("producer:1", 1, "TestChar")])
    monkeypatch.setattr(TokenManager, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(ESIClient, "character_assets", lambda self, character_id, auth_role: [
        _asset(1, 1, 100, HOME_LOCATION_ID),
    ])
    monkeypatch.setattr(ESIClient, "character_orders", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_public_info",
                         lambda self, character_id: {"corporation_id": 500})
    monkeypatch.setattr(ESIClient, "corporation_assets", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_orders", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                         lambda self, structure_id, type_ids, auth_role: {})
    _stub_unlisted_margin(monkeypatch, raise_exc=requests.exceptions.ConnectionError("Goonmetrics unreachable"))

    result = actions.do_unlisted_stock(cfg)  # must not raise

    assert len(result["rows"]) == 1
    assert result["rows"][0].margin is None


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
    _stub_unlisted_margin(monkeypatch)

    result = actions.do_unlisted_stock(cfg)

    # Corp assets were still found (Director role present) even though corp
    # orders failed (Accountant/Trader role missing) - correctly flagged as
    # unlisted since no order (personal or corp) could be found for it.
    assert len(result["rows"]) == 1
    assert result["rows"][0].type_id == 1


def _stub_unlisted_esi(monkeypatch, characters, assets_by_char, orders_by_char=None):
    monkeypatch.setattr(actions.esi_sync, "list_producer_characters", lambda: characters)
    monkeypatch.setattr(TokenManager, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 0, f"Item {type_id}"))
    orders_by_char = orders_by_char or {}
    monkeypatch.setattr(
        ESIClient, "character_assets",
        lambda self, character_id, auth_role: assets_by_char.get(character_id, []))
    monkeypatch.setattr(
        ESIClient, "character_orders",
        lambda self, character_id, auth_role: orders_by_char.get(character_id, []))
    monkeypatch.setattr(
        ESIClient, "character_public_info",
        lambda self, character_id: {"corporation_id": 500 + character_id})
    monkeypatch.setattr(ESIClient, "corporation_assets", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_orders", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                         lambda self, structure_id, type_ids, auth_role: {})


def test_character_esi_fetches_run_in_parallel(monkeypatch):
    import threading
    import time

    cfg = ProductionConfig(home_location_id=HOME_LOCATION_ID)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(1, "Item", 0, 50, None)])
    _stub_unlisted_esi(monkeypatch, [
        ("producer:1", 1, "A"),
        ("producer:2", 2, "B"),
        ("producer:3", 3, "C"),
    ], {})
    _stub_unlisted_margin(monkeypatch)

    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()
    seen_chars = []

    def fake_character_assets(self, character_id, auth_role):
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            seen_chars.append(character_id)
        time.sleep(0.04)
        with lock:
            in_flight -= 1
        return [_asset(character_id, 1, 10, HOME_LOCATION_ID)]

    monkeypatch.setattr(ESIClient, "character_assets", fake_character_assets)

    t0 = time.monotonic()
    result = actions.do_unlisted_stock(cfg)
    elapsed = time.monotonic() - t0

    assert sorted(seen_chars) == [1, 2, 3]
    assert max_in_flight >= 2  # sequential loop would stay at 1
    assert elapsed < 0.10  # sequential would be ~0.12s
    assert result["rows"][0].stock_quantity == 30.0


def test_unlisted_stock_builds_plan_context_once_for_all_rows(monkeypatch):
    cfg = ProductionConfig(home_location_id=HOME_LOCATION_ID)
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [
        (1, "A", 0, 50, None),
        (2, "B", 0, 50, None),
    ])
    _stub_unlisted_esi(monkeypatch, [("producer:1", 1, "A")], {
        1: [_asset(1, 1, 10, HOME_LOCATION_ID), _asset(2, 2, 20, HOME_LOCATION_ID)],
    })

    ctx_calls = {"n": 0}

    class CountingCtx:
        def __init__(self, cfg, extra_type_ids=()):
            ctx_calls["n"] += 1

    helper_calls = {"n": 0}

    def fake_helper(*a, **k):
        helper_calls["n"] += 1
        return {"margin_home": 0.1}

    monkeypatch.setattr(actions, "_PlanContext", CountingCtx)
    monkeypatch.setattr(actions, "_item_margin_detail_with_context", fake_helper)

    result = actions.do_unlisted_stock(cfg)

    assert ctx_calls["n"] == 1
    assert helper_calls["n"] == 2
    assert {r.type_id for r in result["rows"]} == {1, 2}