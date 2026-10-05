"""PI prices at a player structure (C-J) instead of an NPC hub (pure)."""
from dataclasses import replace

import pytest

from eve_trader import storage
from eve_trader.config import TRADING_CONFIG
from eve_trader.esi_client import OrderStats
from eve_trader.pi import actions, static
from eve_trader.pi.config import PiConfig

from .test_pi_engine import _ROWS


@pytest.fixture
def sd(monkeypatch):
    s = static.build_static({k: [tuple(r) for r in v] for k, v in _ROWS.items()})
    monkeypatch.setattr(actions, "_static", lambda: s)
    monkeypatch.setattr(actions, "_trends", lambda static_, region: {})
    return s


def test_structure_mode_reads_the_structure_book(sd, monkeypatch):
    seen = {}

    def fake(ids, cfg):
        seen["structure"] = cfg.pi_price_structure_id
        return {t: OrderStats(100.0, 1.0, 90.0, 1.0) for t in ids[:5]}, False

    monkeypatch.setattr(actions, "_structure_stats", fake)
    cfg = replace(PiConfig(), pi_price_structure_id=1049588174021)
    prices = actions._prices(sd, cfg)
    first = sorted(sd.commodities)[0]
    assert seen["structure"] == 1049588174021
    assert prices.sell[first] == 100.0 and prices.buy[first] == 90.0
    assert prices.sell[sorted(sd.commodities)[-1]] is None  # not on the structure market
    assert prices.source_note is None


def test_structure_mode_reports_the_goonmetrics_fallback(sd, monkeypatch):
    monkeypatch.setattr(actions, "_structure_stats", lambda ids, cfg: ({}, True))
    prices = actions._prices(sd, replace(PiConfig(), pi_price_structure_id=1))
    assert "Goonmetrics" in prices.source_note


def test_hub_mode_is_unchanged(sd, monkeypatch):
    monkeypatch.setattr(actions, "_structure_stats", lambda ids, cfg: pytest.fail("structure path used"))

    class FakeHub:
        stats, hub_by_type = {}, {}

    monkeypatch.setattr(actions, "hub_pricing", lambda *a, **k: FakeHub())
    actions._prices(sd, PiConfig())


def test_fallback_slug_reuses_trading_or_production(monkeypatch):
    from eve_trader.production.config import PRODUCTION_CONFIG

    monkeypatch.setattr(TRADING_CONFIG, "structure_id", 111)
    monkeypatch.setattr(TRADING_CONFIG, "structure_market_slug", "cj-slug")
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_location_id", 222)
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_market", "home-slug")
    assert actions._structure_fallback_slug(replace(PiConfig(), pi_price_structure_id=111)) == "cj-slug"
    assert actions._structure_fallback_slug(replace(PiConfig(), pi_price_structure_id=222)) == "home-slug"
    assert actions._structure_fallback_slug(replace(PiConfig(), pi_price_structure_id=333)) is None
    assert actions._structure_fallback_slug(
        replace(PiConfig(), pi_price_structure_id=111, pi_price_structure_slug="own")) == "own"


def test_price_structures_and_market_label(monkeypatch):
    from eve_trader.production.config import PRODUCTION_CONFIG

    monkeypatch.setattr(TRADING_CONFIG, "structure_id", 111)
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_location_id", 111)
    monkeypatch.setattr(storage, "get_location_names", lambda ids: {111: "C-J6MT - Home"})
    assert actions.price_structures(PiConfig()) == [{"structure_id": 111, "name": "C-J6MT - Home"}]
    assert actions.market_label(replace(PiConfig(), pi_price_structure_id=111)) == "C-J6MT - Home"
    assert actions.market_label(PiConfig()) == "Jita"


def test_price_structures_works_with_the_live_config_proxy(monkeypatch):
    """do_get_meta passes PI_CONFIG (a ConfigProxy, not the dataclass)."""
    from eve_trader.pi.config import PI_CONFIG
    from eve_trader.production.config import PRODUCTION_CONFIG

    monkeypatch.setattr(TRADING_CONFIG, "structure_id", 111)
    monkeypatch.setattr(TRADING_CONFIG, "structure_market_slug", "C-J6MT")
    monkeypatch.setattr(PRODUCTION_CONFIG, "home_location_id", None)
    monkeypatch.setattr(storage, "get_location_names", lambda ids: {})
    rows = actions.price_structures(PI_CONFIG)
    assert rows[0] == {"structure_id": 111, "name": "C-J6MT (structure)"}
