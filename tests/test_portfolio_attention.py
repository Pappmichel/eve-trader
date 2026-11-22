"""Portfolio attention lists only what the viewer can act on."""
from datetime import datetime, timedelta, timezone

from eve_trader import portfolio, storage
from eve_trader.config import TradingConfig


def _wealth(**overrides):
    data = {
        "characters_missing_wallet_scope": [],
        "wealth_unpriced_items": 0,
    }
    data.update(overrides)
    return data


def _no_rebuild(**_kw):
    return {"status": {"empty": True, "needs_rebuild": False}}


def _quiet(monkeypatch, wealth=None, targets=None, shares=None, freshness=None):
    monkeypatch.setattr(portfolio, "total_wealth", lambda cfg=None: wealth or _wealth())
    monkeypatch.setattr(
        storage, "load_stock_targets",
        lambda: [] if targets is None else targets,
    )
    monkeypatch.setattr(storage, "list_esi_sharing", lambda tool_key=None: shares or [])
    monkeypatch.setattr(storage, "list_esi_freshness", lambda: freshness or [])
    for target in (
        "eve_trader.production.realized.report.summary",
        "eve_trader.trade_realized.report.summary",
        "eve_trader.doctrine.realized.summary",
        "eve_trader.station_trading.realized.summary",
    ):
        monkeypatch.setattr(target, _no_rebuild)


def test_attention_wallet_scope_unpriced_and_recompute_only(monkeypatch):
    _quiet(monkeypatch, wealth=_wealth(
        characters_missing_wallet_scope=[{"character_id": 1, "character_name": "Alice"}],
        wealth_unpriced_items=2,
    ), targets=[(34,)])

    def production(**_kw):
        return {"status": {"empty": True, "needs_rebuild": False}}

    def trading(**_kw):
        return {"status": {"empty": True, "needs_rebuild": True}}

    monkeypatch.setattr("eve_trader.production.realized.report.summary", production)
    monkeypatch.setattr("eve_trader.trade_realized.report.summary", trading)

    items = portfolio.attention(["production", "trading"])["items"]
    by_key = {item["key"]: item for item in items}
    assert by_key["wallet_scope"]["link"] == "/character-management/characters"
    assert "Alice has no wallet scope" in by_key["wallet_scope"]["text"]
    assert "recompute_production" not in by_key
    assert by_key["recompute_trading"]["link"] == "/trading/trades"
    assert by_key["unpriced"]["text"] == "2 unpriced items are excluded from Total Wealth."
    assert by_key["unpriced"]["link"] == "#unpriced"
    assert "stock_targets" not in by_key
    assert [item["key"] for item in items] == ["wallet_scope", "recompute_trading", "unpriced"]


def test_attention_stock_targets_only_for_a_production_grant(monkeypatch):
    _quiet(monkeypatch, targets=[])
    without = portfolio.attention(["trading"])["items"]
    assert without == []
    with_production = portfolio.attention(["production"])["items"]
    assert with_production == [{
        "key": "stock_targets",
        "text": "Production has no stock targets configured.",
        "link": "/production/stock-targets",
    }]


def test_attention_stale_esi_names_only_overdue_portfolio_kinds(monkeypatch):
    now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    shares = [
        ("character", 1, "assets", "portfolio"),
        ("character", 2, "assets", "portfolio"),
        ("character", 1, "market_orders", "portfolio"),
        ("character", 1, "blueprints", "portfolio"),
        ("character", 1, "contracts", "portfolio"),
        ("character", 1, "skills", "portfolio"),
    ]
    freshness = [
        ("character", 1, "assets", (now - timedelta(hours=7)).isoformat(), None, None),
        ("character", 2, "assets", (now - timedelta(hours=1)).isoformat(), None, None),
        ("character", 1, "market_orders", (now - timedelta(minutes=30)).isoformat(), None, None),
        ("character", 1, "blueprints", (now - timedelta(hours=20)).isoformat(), None, None),
        ("character", 1, "skills", (now - timedelta(days=9)).isoformat(), None, None),
    ]
    _quiet(monkeypatch, targets=[(1,)], shares=shares, freshness=freshness)
    items = portfolio.attention([], cfg=TradingConfig(), now=now)["items"]
    assert len(items) == 1
    assert items[0]["key"] == "stale_esi"
    assert items[0]["link"] == "/character-management/characters"
    assert items[0]["text"] == (
        "ESI data shared with Portfolio is stale: Assets, Contracts."
    )


def test_attention_wallet_scope_line_names_five_characters_then_counts_the_rest(monkeypatch):
    missing = [{"character_id": i, "character_name": f"Pilot {i}"} for i in range(1, 24)]
    _quiet(monkeypatch, wealth=_wealth(characters_missing_wallet_scope=missing), targets=[(34,)])

    items = portfolio.attention([])["items"]
    text = {item["key"]: item for item in items}["wallet_scope"]["text"]

    assert text == (
        "Pilot 1, Pilot 2, Pilot 3, Pilot 4, Pilot 5, ... and 18 more have no wallet scope, "
        "so their balance is left out of Total Wealth."
    )


def test_attention_names_owners_whose_copies_have_no_run_data(monkeypatch):
    copies = [
        {"owner_type": "corporation", "owner_id": 98000001, "copies": 12254},
        {"owner_type": "character", "owner_id": 7, "copies": 3},
    ]
    _quiet(monkeypatch, wealth=_wealth(blueprint_copies_without_runs=copies), targets=[(34,)])
    monkeypatch.setattr("eve_trader.realized.names.owner_names", lambda ids: {98000001: "Builders Corp"})

    item = {item["key"]: item for item in portfolio.attention([])["items"]}["copies_without_runs"]

    assert item["text"] == (
        "12,257 blueprint copies of Builders Corp, 7 are known only from assets and left out of "
        "Total Wealth. Share Blueprints with Portfolio to value them by runs."
    )
    assert item["link"] == "/character-management/characters"


def test_attention_wallet_scope_line_lists_exactly_five_without_a_tail(monkeypatch):
    missing = [{"character_id": i, "character_name": f"Pilot {i}"} for i in range(1, 6)]
    _quiet(monkeypatch, wealth=_wealth(characters_missing_wallet_scope=missing), targets=[(34,)])

    text = {item["key"]: item for item in portfolio.attention([])["items"]}["wallet_scope"]["text"]

    assert text.startswith("Pilot 1, Pilot 2, Pilot 3, Pilot 4, Pilot 5 have no wallet scope")
    assert "more" not in text
