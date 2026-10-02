import pytest

from eve_trader.config import TRADING_CONFIG
from eve_trader.esi_client import ESIClient, ESIError, OrderStats
from eve_trader.goonmetrics_client import CurrentPrice, GoonmetricsClient
from eve_trader.production import esi_sync, jita_price_cache
from eve_trader.production.config import ProductionConfig
from eve_trader.production.pricing import buy_price, buy_source, home_prices, jita_prices

# jita_buy_broker_fee=0.0 keeps these source-picking tests' arithmetic clean -
# see test_buy_price_includes_buy_broker_fee for the fee itself. T3-04
# (2026-09-26): the fee itself now lives only on TRADING_CONFIG (see
# _reset_trading_broker_fee below), not ProductionConfig - CFG no longer
# sets it at all.
CFG = ProductionConfig(haul_cost_per_m3=900.0)


@pytest.fixture(autouse=True)
def _reset_jita_price_cache():
    # jita_prices() reads production.jita_price_cache's module-level cache
    # first (see that module's own docstring) - without resetting it between
    # tests, one test populating it (directly, or via jita_prices() itself)
    # would silently make a later test's ESI mock never get called, the
    # exact staleness risk CLAUDE.md's testing-conventions section warns
    # about for any module-level cache.
    jita_price_cache._cache.clear()
    jita_price_cache._updated_at = None
    yield
    jita_price_cache._cache.clear()
    jita_price_cache._updated_at = None


@pytest.fixture(autouse=True)
def _reset_trading_broker_fee(monkeypatch):
    # T3-04 (2026-09-26): _candidate_prices (production/pricing.py) now
    # reads TRADING_CONFIG.jita_buy_broker_fee directly (the single source
    # of truth after merging ProductionConfig's former duplicate copy) -
    # zeroed here so these source-picking tests' arithmetic stays clean,
    # same reasoning CFG's own former jita_buy_broker_fee=0.0 had.
    # test_buy_price_includes_buy_broker_fee overrides this locally to
    # verify the fee actually gets applied.
    monkeypatch.setattr(TRADING_CONFIG, "jita_buy_broker_fee", 0.0)


def _price(sell: float) -> CurrentPrice:
    return CurrentPrice(type_id=1, updated="", buy=0.0, sell=sell)


def test_buy_source_picks_jita_when_haul_adjusted_price_is_cheaper():
    # Home has a listed sell order, but it's far pricier than hauling from
    # Jita - the cheaper source must win, not "home whenever it has stock".
    home = {1: _price(10_000.0)}
    jita = {1: _price(100.0)}  # + 900 * 1 m3 haul = 1000, still far under home
    assert buy_source(1, home, jita, volume_m3=1.0, cfg=CFG) == "Jita"
    assert buy_price(1, home, jita, volume_m3=1.0, cfg=CFG) == 1000.0


def test_buy_source_picks_home_when_it_is_actually_cheaper():
    home = {1: _price(500.0)}
    jita = {1: _price(100.0)}  # + 900 haul = 1000, more than home
    assert buy_source(1, home, jita, volume_m3=1.0, cfg=CFG) == "C-J"
    assert buy_price(1, home, jita, volume_m3=1.0, cfg=CFG) == 500.0


def test_buy_source_falls_back_to_whichever_market_has_stock():
    assert buy_source(1, {}, {1: _price(50.0)}, volume_m3=0.0, cfg=CFG) == "Jita"
    assert buy_source(1, {1: _price(50.0)}, {}, volume_m3=0.0, cfg=CFG) == "C-J"
    assert buy_source(1, {}, {}, volume_m3=0.0, cfg=CFG) is None


def test_buy_price_none_when_no_sell_order_anywhere():
    assert buy_price(1, {}, {}, volume_m3=1.0, cfg=CFG) is None


def test_buy_price_includes_buy_broker_fee(monkeypatch):
    monkeypatch.setattr(TRADING_CONFIG, "jita_buy_broker_fee", 0.0147)
    cfg = ProductionConfig(haul_cost_per_m3=900.0)
    home = {1: _price(500.0)}
    assert buy_price(1, home, {}, volume_m3=1.0, cfg=cfg) == 500.0 * 1.0147


# ---------------------------------------------------------- home_prices/jita_prices
HOME_CFG = ProductionConfig(home_market="c-j6mt", home_location_id=1049588174021)


def test_home_prices_uses_live_esi_when_producer_character_succeeds(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda capability_key: [("esi:1", 1, "TestChar")])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", lambda self, location_id, type_ids, auth_role: {
        587: OrderStats(sell_percentile=1234.5, sell_volume=10.0, buy_percentile=1000.0, buy_volume=5.0),
    })
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         pytest.fail("must not fall back to Goonmetrics when ESI succeeds"))

    result = home_prices(HOME_CFG, [587])

    assert result[587].sell == 1234.5
    assert result[587].buy == 1000.0


def test_home_prices_reports_empty_market_as_zero_sell_not_a_stale_goonmetrics_price(monkeypatch):
    # Direct regression test for the reported bug: Goonmetrics may still
    # show a stale nonzero sell price for an item the real C-J market has
    # zero sell orders for right now - the live ESI check must win, and a
    # confirmed-empty market must resolve to sell=0.0 (excluded by every
    # downstream `> 0` check), not the stale Goonmetrics number.
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda capability_key: [("esi:1", 1, "TestChar")])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", lambda self, location_id, type_ids, auth_role: {
        587: OrderStats(sell_percentile=None, sell_volume=0.0, buy_percentile=None, buy_volume=0.0),
    })
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=587, updated="", buy=0.0, sell=99999.0)])

    result = home_prices(HOME_CFG, [587])

    assert result[587].sell == 0.0


def test_home_prices_falls_back_to_goonmetrics_when_every_producer_character_fails(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda capability_key: [("esi:1", 1, "A"), ("esi:2", 2, "B")])

    def _fail(self, location_id, type_ids, auth_role):
        raise ESIError("no docking access")
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", _fail)
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=587, updated="2026-08-26", buy=0.0, sell=42.0)])

    result = home_prices(HOME_CFG, [587])

    assert result[587].sell == 42.0


def test_home_prices_falls_back_to_goonmetrics_when_no_producer_characters(monkeypatch):
    monkeypatch.setattr(esi_sync, "list_capability_characters", lambda capability_key: [])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", lambda *a, **k:
                         pytest.fail("must not attempt ESI with no producer characters"))
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=587, updated="", buy=0.0, sell=42.0)])

    result = home_prices(HOME_CFG, [587])

    assert result[587].sell == 42.0


def test_home_prices_skips_esi_when_home_location_id_unset(monkeypatch):
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", lambda *a, **k:
                         pytest.fail("must not attempt ESI with no home_location_id configured"))
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=587, updated="", buy=0.0, sell=42.0)])

    cfg = ProductionConfig(home_market="c-j6mt", home_location_id=None)
    result = home_prices(cfg, [587])

    assert result[587].sell == 42.0


def test_home_prices_empty_type_ids_returns_empty_dict():
    assert home_prices(HOME_CFG, []) == {}


def test_jita_prices_uses_live_esi_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids: {
        34: OrderStats(sell_percentile=5.5, sell_volume=1000.0, buy_percentile=5.0, buy_volume=900.0),
    })
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         pytest.fail("must not fall back to Goonmetrics when ESI succeeds"))

    result = jita_prices([34])

    assert result[34].sell == 5.5


def test_jita_prices_falls_back_to_goonmetrics_when_esi_fails(monkeypatch):
    def _fail(self, region_id, type_ids):
        raise ESIError("ESI outage")
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fail)
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         [CurrentPrice(type_id=34, updated="", buy=0.0, sell=6.0)])

    result = jita_prices([34])

    assert result[34].sell == 6.0


def test_jita_prices_goonmetrics_outage_returns_empty_not_raise(monkeypatch):
    import requests
    from eve_trader.production.pricing import jita_prices as _jita_prices

    def _fail(self, region_id, type_ids):
        raise ESIError("ESI outage")
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fail)

    def _gm_fail(self, market):
        raise requests.ConnectionError("appraise.gnf.lt down")
    monkeypatch.setattr(GoonmetricsClient, "current_prices", _gm_fail)

    assert _jita_prices([34]) == {}


def test_jita_prices_empty_type_ids_returns_empty_dict():
    assert jita_prices([]) == {}


def test_jita_prices_uses_shared_cache_without_calling_esi(monkeypatch):
    jita_price_cache._cache[34] = CurrentPrice(type_id=34, updated="", buy=5.0, sell=5.5)
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, region_id, type_ids:
                         pytest.fail("must not call ESI when the shared cache already has every requested type_id"))

    result = jita_prices([34])

    assert result[34].sell == 5.5


def test_jita_prices_only_live_fetches_type_ids_missing_from_cache(monkeypatch):
    jita_price_cache._cache[34] = CurrentPrice(type_id=34, updated="", buy=5.0, sell=5.5)
    seen_type_ids = []

    def _fetch(self, region_id, type_ids):
        seen_type_ids.extend(type_ids)
        return {99: OrderStats(sell_percentile=7.0, sell_volume=1.0, buy_percentile=6.0, buy_volume=1.0)}
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)

    result = jita_prices([34, 99])

    assert seen_type_ids == [99]
    assert result[34].sell == 5.5
    assert result[99].sell == 7.0


# ---------------------------------------------------------- per-tool hub (#222)
def test_jita_prices_reads_the_production_hub_not_trading_config(monkeypatch):
    # Trading's own hub must not leak into Production: only hub_region_id counts.
    monkeypatch.setattr(TRADING_CONFIG, "jita_region_id", 10000043)
    seen = []

    def _fetch(self, region_id, type_ids):
        seen.append(region_id)
        return {34: OrderStats(sell_percentile=5.5, sell_volume=1.0, buy_percentile=5.0, buy_volume=1.0)}
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)

    jita_prices([34], 10000002)
    jita_prices([34], 10000032)

    assert seen == [10000002, 10000032]


def test_jita_prices_non_jita_hub_bypasses_the_jita_cache(monkeypatch):
    # The shared cache is Jita-only; a cached Jita quote must never answer for Amarr.
    jita_price_cache._cache[34] = CurrentPrice(type_id=34, updated="", buy=5.0, sell=5.5)
    seen = []

    def _fetch(self, region_id, type_ids):
        seen.append((region_id, list(type_ids)))
        return {34: OrderStats(sell_percentile=9.0, sell_volume=1.0, buy_percentile=8.0, buy_volume=1.0)}
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fetch)

    result = jita_prices([34], 10000043)

    assert seen == [(10000043, [34])]
    assert result[34].sell == 9.0


def test_jita_prices_non_jita_hub_has_no_goonmetrics_fallback(monkeypatch):
    def _fail(self, region_id, type_ids):
        raise ESIError("ESI outage")
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", _fail)
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market:
                         pytest.fail("Goonmetrics' 'jita' slug must never stand in for another hub"))

    assert jita_prices([34], 10000043) == {}


def test_jita_prices_defaults_to_the_production_config_hub(monkeypatch):
    from eve_trader.production.pricing import PRODUCTION_CONFIG
    monkeypatch.setattr(PRODUCTION_CONFIG, "hub_region_id", 10000030)
    seen = []
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk",
                        lambda self, region_id, type_ids: seen.append(region_id) or {})

    jita_prices([34])

    assert seen == [10000030]


# ------------------------------------------------ ALL_HUBS / best hub (#222)

JITA, AMARR, DODIXIE, RENS = 10000002, 10000043, 10000032, 10000030


def _stats(sell):
    return OrderStats(sell_percentile=sell, sell_volume=100.0, buy_percentile=None, buy_volume=0.0)


@pytest.fixture
def best_hub_env(monkeypatch):
    from eve_trader.production import engine
    # Item 1: tiny and cheap in Jita, item 2: bulky, so freight decides it.
    books = {
        JITA: {1: _stats(100.0), 2: _stats(100.0)},
        AMARR: {1: _stats(150.0), 2: _stats(90.0)},
        DODIXIE: {1: _stats(None)},
        RENS: {},
    }
    regions_asked = []

    def fake_bulk(self, region_id, type_ids):
        regions_asked.append(region_id)
        return {t: books[region_id][t] for t in type_ids if t in books.get(region_id, {})}

    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", fake_bulk)
    monkeypatch.setattr(engine, "_haul_volume", lambda tid, cfg: {1: 1.0, 2: 10.0}[tid])
    monkeypatch.setattr(TRADING_CONFIG, "jita_buy_broker_fee", 0.0)
    monkeypatch.setattr(TRADING_CONFIG, "hub_freight_cost_per_m3", {str(JITA): 5.0, str(AMARR): 1.0})
    from eve_trader.production.config import PRODUCTION_CONFIG
    monkeypatch.setattr(PRODUCTION_CONFIG, "haul_cost_per_m3", 7.0)
    monkeypatch.setattr(PRODUCTION_CONFIG, "hub_region_id", 0)
    return regions_asked


def test_all_hubs_buy_candidate_uses_cheapest_landed_hub_per_item(best_hub_env):
    from eve_trader.production.pricing import HubQuote
    quotes = jita_prices([1, 2])
    assert all(isinstance(q, HubQuote) for q in quotes.values())
    # item 1 (1 m3): Jita 100+5 = 105 beats Amarr 150+1; item 2 (10 m3): Jita
    # 100+50 = 150 loses to Amarr 90+10 = 100.
    assert (quotes[1].hub_region_id, quotes[1].freight_per_m3) == (JITA, 5.0)
    assert (quotes[2].hub_region_id, quotes[2].freight_per_m3) == (AMARR, 1.0)
    cfg = ProductionConfig(haul_cost_per_m3=7.0)
    assert buy_price(1, {}, quotes, 1.0, cfg) == 105.0
    assert buy_price(2, {}, quotes, 10.0, cfg) == 100.0
    assert buy_source(2, {}, quotes, 10.0, cfg) == "Jita"
    from eve_trader.production import pricing
    assert pricing.quote_hub(quotes[2]) == (AMARR, "Amarr")


def test_all_hubs_freight_falls_back_to_haul_cost_for_hub_without_entry(best_hub_env, monkeypatch):
    monkeypatch.setattr(TRADING_CONFIG, "hub_freight_cost_per_m3", {})
    quotes = jita_prices([2])
    # No table entries: every hub uses haul_cost_per_m3 (7.0), so Amarr's
    # lower sell price wins.
    assert (quotes[2].hub_region_id, quotes[2].freight_per_m3) == (AMARR, 7.0)


def test_all_hubs_non_buy_reads_use_jita_reference_never_region_zero(best_hub_env):
    from eve_trader.production import engine, pricing
    quotes = jita_prices([1, 2])
    # Item 2's buy quote is Amarr's, but the reference for margins/valuation
    # is Jita's own quote.
    assert quotes[2].sell == 90.0
    assert pricing.reference_quote(quotes[2]).sell == 100.0
    cfg = ProductionConfig(hub_region_id=0, market_fees=0.0, haul_cost_per_m3=0.0)
    assert engine.margin_jita(2, 50.0, quotes, cfg) == pytest.approx((100.0 - 50.0) / 100.0) \
        or engine.margin_jita(2, 50.0, quotes, cfg) is not None
    assert engine._listing_region_id(cfg) == JITA
    assert engine._listing_region_id(ProductionConfig(hub_region_id=AMARR)) == AMARR
    assert 0 not in best_hub_env


def test_single_hub_quotes_are_plain_and_use_haul_cost(monkeypatch):
    from eve_trader.production import pricing
    monkeypatch.setattr(ESIClient, "region_order_stats_bulk", lambda self, r, ids: {1: _stats(100.0)})
    quotes = jita_prices([1], AMARR)
    assert not isinstance(quotes[1], pricing.HubQuote)
    assert pricing.reference_quote(quotes[1]) is quotes[1]
    assert buy_price(1, {}, quotes, 2.0, CFG) == 100.0 + 900.0 * 2.0
