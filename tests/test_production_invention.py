"""Tests for eve_trader/production/invention.py - EVE's real invention
probability/decryptor mechanic (see this module's own docstring for the
formula and its authoritative source, independently re-confirmed against
wiki.eveuniversity.org in a business-logic audit, 2026-08-29). No existing
test file covered estimate()/compare_decryptors() directly before that same
audit found PB-06 here."""
import pytest

from eve_trader import storage
from eve_trader.production import invention

RECIPE = {
    "base_probability": 0.34,
    "base_runs": 10,
    "datacores": [(20424, 8), (20425, 8)],  # two datacore types, qty 8 each
    "product_type_id": 999,
}


@pytest.fixture(autouse=True)
def _fake_recipe_and_sde(monkeypatch):
    monkeypatch.setattr(storage, "get_invention_recipe", lambda type_id, product_type_id=None: RECIPE)
    monkeypatch.setattr(storage, "get_sde_type",
                         lambda type_id: (type_id, None, f"Type {type_id}", 0.01, None, None, None, None))
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda *a, **k: [])
    # category_id 9 = a genuine T1 blueprint (not a Tech III relic, category
    # 34) - every test in this file exercises the Tech II path, where
    # estimate() must NOT add a relic_cost (see test_production_relic.py for
    # the Tech III relic-pricing behavior this deliberately excludes here).
    monkeypatch.setattr(storage, "get_type_category", lambda type_id: 9)


def test_estimate_computes_cost_per_run_when_every_price_is_known(monkeypatch):
    monkeypatch.setattr(invention.pricing, "buy_price", lambda *a, **k: 100_000.0)

    result = invention.estimate(100, "None", {}, {})

    assert result.datacore_cost == pytest.approx(1_600_000.0)  # 16 datacores x 100k
    assert result.expected_cost_per_success is not None
    assert result.expected_cost_per_run is not None
    assert result.net_cost_per_run is not None


def test_estimate_returns_none_cost_fields_when_a_datacore_is_unpriced(monkeypatch):
    """PB-06 regression (business-logic audit, 2026-08-29): a datacore with
    no sell order anywhere used to be silently treated as free
    (`price or 0.0`), understating the invention attempt's real cost. The
    decision-driving cost fields must become None (unknown) instead of a
    too-low number that could make an unaffordable decryptor look cheap."""
    prices = {20424: 100_000.0, 20425: None}
    monkeypatch.setattr(invention.pricing, "buy_price",
                         lambda material_id, *a, **k: prices.get(material_id, 100_000.0))

    result = invention.estimate(100, "None", {}, {})

    assert result.expected_cost_per_success is None
    assert result.expected_cost_per_run is None
    assert result.net_cost_per_run is None
    # datacore_cost still sums whatever IS known - partial info, not hidden.
    assert result.datacore_cost == pytest.approx(800_000.0)  # only the priced datacore counted


def test_estimate_returns_none_cost_fields_when_the_decryptor_itself_is_unpriced(monkeypatch):
    monkeypatch.setattr(invention.pricing, "buy_price",
                         lambda material_id, *a, **k: None if material_id == invention.DECRYPTORS["Accelerant"].type_id else 50_000.0)

    result = invention.estimate(100, "Accelerant", {}, {})

    assert result.net_cost_per_run is None


def test_reducible_material_cost_returns_none_when_a_material_is_unpriced(monkeypatch):
    """T3-05 (business-logic audit follow-up, 2026-09-26): same anti-pattern
    PB-06 fixed for datacore/decryptor/relic cost above - a reducible
    material with no sell order anywhere used to be silently treated as
    free (`price or 0.0`), understating the real material savings a higher-
    ME decryptor gives you. Must return None (unknown), not a too-low
    partial sum."""
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda *a, **k: [(35, 10), (36, 10)])
    prices = {35: 100.0, 36: None}
    monkeypatch.setattr(invention.pricing, "buy_price",
                         lambda material_id, *a, **k: prices.get(material_id))

    assert invention.reducible_material_cost(1, 1, {}, {}) is None


def test_reducible_material_cost_sums_when_every_material_is_priced(monkeypatch):
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda *a, **k: [(35, 10), (36, 5)])
    monkeypatch.setattr(invention.pricing, "buy_price", lambda material_id, *a, **k: 100.0)

    assert invention.reducible_material_cost(1, 1, {}, {}) == pytest.approx(1500.0)  # 10*100 + 5*100


def test_estimate_material_savings_and_net_cost_none_when_reducible_cost_unknown(monkeypatch):
    """None reducible_material_cost_per_run must null out material_savings_
    per_run/net_cost_per_run specifically, without also nulling out
    expected_cost_per_success/expected_cost_per_run - those only depend on
    datacore/decryptor/relic pricing (all known here), not on the build-
    side ME credit."""
    monkeypatch.setattr(invention.pricing, "buy_price", lambda *a, **k: 100_000.0)

    result = invention.estimate(100, "None", {}, {}, reducible_material_cost_per_run=None)

    assert result.expected_cost_per_success is not None
    assert result.expected_cost_per_run is not None
    assert result.material_savings_per_run is None
    assert result.net_cost_per_run is None


def test_compare_decryptors_ranks_an_unpriced_decryptor_last_not_falsely_cheapest(monkeypatch):
    """An unpriced decryptor (e.g. Accelerant with no sell order) must never
    win compare_decryptors' ranking just because its unknown cost silently
    became 0 - it must sort behind every decryptor whose cost IS known
    (compare_decryptors already sorts None as float('inf'), this proves
    estimate() actually produces that None rather than a false 0)."""
    def fake_price(material_id, *a, **k):
        if material_id == invention.DECRYPTORS["Accelerant"].type_id:
            return None  # the one decryptor with no market data
        return 50_000.0
    monkeypatch.setattr(invention.pricing, "buy_price", fake_price)

    results = invention.compare_decryptors(100, {}, {})
    by_name = {r.decryptor: r for r in results}

    assert by_name["Accelerant"].net_cost_per_run is None
    assert results[-1].decryptor == "Accelerant"  # sorted last, not first/cheapest


RATES = invention.FeeRates(adjusted_prices={1: 1_000.0, 2: 10.0}, invention_rate=0.1, copying_rate=0.05)


def _materials_by_blueprint(monkeypatch):
    # 999 = the invented blueprint (RECIPE's product), 100 = the T1 blueprint.
    materials = {999: [(1, 1_000)], 100: [(2, 500)]}
    monkeypatch.setattr(storage, "get_blueprint_materials",
                        lambda bp, activity_id: materials.get(bp, []) if activity_id == 1 else [])


def test_attempt_job_fee_prices_invention_on_the_invented_item_plus_the_t1_copy(monkeypatch):
    _materials_by_blueprint(monkeypatch)
    # Invention: 0.02 x EIV(invented) 1,000,000 x 0.1 = 2,000.
    # Copy: 0.02 x EIV(T1) 5,000 x 0.05 = 5.
    assert invention.attempt_job_fee(100, 999, False, RATES) == pytest.approx(2_005.0)
    # A relic has no copy job.
    assert invention.attempt_job_fee(100, 999, True, RATES) == pytest.approx(2_000.0)
    assert invention.attempt_job_fee(100, 999, False, None) == 0.0


def test_estimate_adds_job_fee_to_the_attempt_cost(monkeypatch):
    _materials_by_blueprint(monkeypatch)
    monkeypatch.setattr(invention.pricing, "buy_price", lambda *a, **k: 100_000.0)

    with_fees = invention.estimate(100, "None", {}, {}, rates=RATES)
    without = invention.estimate(100, "None", {}, {}, rates=None)

    assert with_fees.job_fee == pytest.approx(2_005.0)
    assert with_fees.total_attempt_cost == pytest.approx(without.total_attempt_cost + 2_005.0)
    assert with_fees.expected_cost_per_run > without.expected_cost_per_run


def test_job_fees_weigh_more_on_few_run_decryptors(monkeypatch):
    """Fees are per attempt, so they cost more per BPC run for a decryptor
    with fewer runs and a lower chance (Accelerant) than for Parity."""
    _materials_by_blueprint(monkeypatch)
    monkeypatch.setattr(invention.pricing, "buy_price", lambda *a, **k: 100_000.0)

    def fee_per_run(name):
        with_fees = invention.estimate(100, name, {}, {}, rates=RATES)
        without = invention.estimate(100, name, {}, {}, rates=None)
        return with_fees.expected_cost_per_run - without.expected_cost_per_run

    assert fee_per_run("Accelerant") > fee_per_run("Parity")


def _result(decryptor, runs, net):
    return invention.InventionResult(
        t1_blueprint_type_id=1, t1_blueprint_name="T1", product_type_id=2, product_name="T2",
        decryptor=decryptor, probability=0.5, output_runs=runs, datacore_cost=0.0, decryptor_cost=0.0,
        relic_cost=0.0, total_attempt_cost=0.0, expected_cost_per_success=0.0, expected_cost_per_run=0.0,
        me=0, te=0, material_savings_per_run=0.0, net_cost_per_run=net)


def test_rank_prefers_more_bpc_runs_within_one_percent_of_material_cost():
    # Material cost 100M: 1% = 1M. Parity is 0.6M dearer but has twice the runs.
    accelerant, parity = _result("Accelerant", 2, -4.0e6), _result("Parity", 4, -3.4e6)

    ranked = invention.rank([accelerant, parity], 100e6)

    assert [r.decryptor for r in ranked] == ["Parity", "Accelerant"]
    assert ranked[0].preferred_for_runs is True
    assert ranked[1].preferred_for_runs is False


def test_rank_keeps_the_cheapest_when_the_gap_is_one_percent_or_more():
    accelerant, parity = _result("Accelerant", 2, -4.0e6), _result("Parity", 4, -2.9e6)

    ranked = invention.rank([parity, accelerant], 100e6)

    assert [r.decryptor for r in ranked] == ["Accelerant", "Parity"]
    assert not ranked[0].preferred_for_runs


def test_rank_picks_the_most_runs_among_all_near_ties_and_ignores_unpriced():
    rows = [_result("Accelerant", 2, -4.0e6), _result("Parity", 4, -3.5e6),
            _result("Attainment", 5, -3.2e6), _result("Augmentation", 10, None)]

    ranked = invention.rank(rows, 100e6)

    assert ranked[0].decryptor == "Attainment"


def test_rank_without_a_material_cost_is_plain_cheapest_first():
    accelerant, parity = _result("Accelerant", 2, -4.0e6), _result("Parity", 4, -3.99e6)

    assert invention.rank([parity, accelerant], None)[0].decryptor == "Accelerant"
    assert invention.rank([parity, accelerant], 0.0)[0].decryptor == "Accelerant"


# --- fee_rates: invention system vs manufacturing fallback -------------------
# Opt out of conftest's fee_rates stub via @pytest.mark.live_fee_rates.


@pytest.fixture(autouse=True)
def _clear_invention_system_cache():
    invention.clear_invention_system_cache()
    yield
    invention.clear_invention_system_cache()


def _fee_client(monkeypatch, *, adjusted=None, indices_by_system=None):
    """Stub ESIClient so fee_rates never hits the network. indices_by_system
    maps solar_system_id -> activity-index dict."""
    from eve_trader import esi_client as esi_client_module

    adjusted = adjusted if adjusted is not None else {1: 1.0}
    indices_by_system = indices_by_system or {}
    seen_systems: list[int] = []

    monkeypatch.setattr(esi_client_module.ESIClient, "__init__", lambda self: None)
    monkeypatch.setattr(esi_client_module.ESIClient, "get_adjusted_prices",
                        lambda self: adjusted)

    def fake_indices(self, system_id, activities=()):
        seen_systems.append(system_id)
        return dict(indices_by_system.get(system_id, {}))

    monkeypatch.setattr(esi_client_module.ESIClient, "get_system_cost_indices", fake_indices)
    return seen_systems


@pytest.mark.live_fee_rates
def test_fee_rates_uses_invention_location_system(monkeypatch):
    from eve_trader.locations.reader import LocationInfo
    from eve_trader.production.config import ProductionConfig
    from eve_trader.production.constants import ACTIVITY_MODS, SCC_SURCHARGE_RATE

    invention_sys, mfg_sys = 30_000_142, 30_000_144
    location_id = 102_000_000_001
    describe_calls: list[list[int]] = []

    def fake_describe(ids):
        describe_calls.append(list(ids))
        return {location_id: LocationInfo(location_id=location_id, solar_system_id=invention_sys)}

    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-a")
    monkeypatch.setattr("eve_trader.locations.reader.describe", fake_describe)
    seen = _fee_client(monkeypatch, indices_by_system={
        invention_sys: {"invention": 0.5, "copying": 0.25},
        mfg_sys: {"invention": 0.1, "copying": 0.05},
    })
    cfg = ProductionConfig(
        invention_location_id=location_id,
        manufacturing_system_id=mfg_sys,
        facility_tax_rate=0.0,
    )
    rates = invention.fee_rates(cfg)
    assert rates is not None
    assert seen == [invention_sys]
    assert rates.invention_rate == pytest.approx(0.5 + SCC_SURCHARGE_RATE)
    assert rates.copying_rate == pytest.approx(0.25 + SCC_SURCHARGE_RATE)
    assert ACTIVITY_MODS["Tech II"].job_cost_rate != 0.5  # not the flat fallback


@pytest.mark.live_fee_rates
def test_fee_rates_falls_back_to_manufacturing_system_when_location_unset(monkeypatch):
    from eve_trader.production.config import ProductionConfig
    from eve_trader.production.constants import SCC_SURCHARGE_RATE

    mfg_sys = 30_000_144
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-a")

    def boom(_ids):
        raise AssertionError("describe must not run without invention_location_id")

    monkeypatch.setattr("eve_trader.locations.reader.describe", boom)
    seen = _fee_client(monkeypatch, indices_by_system={
        mfg_sys: {"invention": 0.2, "copying": 0.1},
    })
    cfg = ProductionConfig(invention_location_id=None, manufacturing_system_id=mfg_sys,
                           facility_tax_rate=0.0)
    rates = invention.fee_rates(cfg)
    assert rates is not None
    assert seen == [mfg_sys]
    assert rates.invention_rate == pytest.approx(0.2 + SCC_SURCHARGE_RATE)


@pytest.mark.live_fee_rates
def test_fee_rates_falls_back_when_location_unknown(monkeypatch):
    from eve_trader.production.config import ProductionConfig
    from eve_trader.production.constants import SCC_SURCHARGE_RATE

    mfg_sys = 30_000_144
    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-a")
    monkeypatch.setattr("eve_trader.locations.reader.describe", lambda ids: {})
    seen = _fee_client(monkeypatch, indices_by_system={
        mfg_sys: {"invention": 0.3, "copying": 0.15},
    })
    cfg = ProductionConfig(invention_location_id=999, manufacturing_system_id=mfg_sys,
                           facility_tax_rate=0.0)
    rates = invention.fee_rates(cfg)
    assert rates is not None
    assert seen == [mfg_sys]
    assert rates.invention_rate == pytest.approx(0.3 + SCC_SURCHARGE_RATE)


@pytest.mark.live_fee_rates
def test_fee_rates_uses_flat_tech_ii_rate_when_no_index(monkeypatch):
    from eve_trader.production.config import ProductionConfig
    from eve_trader.production.constants import ACTIVITY_MODS, SCC_SURCHARGE_RATE

    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-a")
    monkeypatch.setattr("eve_trader.locations.reader.describe", lambda ids: {})
    _fee_client(monkeypatch, indices_by_system={})  # system known, empty indices
    cfg = ProductionConfig(invention_location_id=None, manufacturing_system_id=30_000_144,
                           facility_tax_rate=0.0)
    rates = invention.fee_rates(cfg)
    flat = ACTIVITY_MODS["Tech II"].job_cost_rate
    assert rates is not None
    assert rates.invention_rate == pytest.approx(flat + SCC_SURCHARGE_RATE)
    assert rates.copying_rate == pytest.approx(flat + SCC_SURCHARGE_RATE)


@pytest.mark.live_fee_rates
def test_fee_rates_caches_location_describe(monkeypatch):
    from eve_trader.locations.reader import LocationInfo
    from eve_trader.production.config import ProductionConfig

    location_id = 102_000_000_001
    invention_sys = 30_000_142
    describe_calls: list[int] = []

    def fake_describe(ids):
        describe_calls.append(1)
        return {location_id: LocationInfo(location_id=location_id, solar_system_id=invention_sys)}

    monkeypatch.setattr(storage, "get_current_tenant", lambda: "tenant-a")
    monkeypatch.setattr("eve_trader.locations.reader.describe", fake_describe)
    _fee_client(monkeypatch, indices_by_system={
        invention_sys: {"invention": 0.4, "copying": 0.2},
    })
    cfg = ProductionConfig(invention_location_id=location_id, manufacturing_system_id=1,
                           facility_tax_rate=0.0)
    assert invention.fee_rates(cfg) is not None
    assert invention.fee_rates(cfg) is not None
    assert len(describe_calls) == 1


@pytest.mark.live_fee_rates
def test_fee_rates_falls_back_without_tenant(monkeypatch):
    from eve_trader.production.config import ProductionConfig
    from eve_trader.production.constants import SCC_SURCHARGE_RATE

    mfg_sys = 30_000_144
    monkeypatch.setattr(storage, "get_current_tenant", lambda: None)

    def boom(_ids):
        raise AssertionError("describe requires a tenant; must not be called")

    monkeypatch.setattr("eve_trader.locations.reader.describe", boom)
    seen = _fee_client(monkeypatch, indices_by_system={
        mfg_sys: {"invention": 0.2, "copying": 0.1},
    })
    cfg = ProductionConfig(invention_location_id=102_000_000_001,
                           manufacturing_system_id=mfg_sys, facility_tax_rate=0.0)
    rates = invention.fee_rates(cfg)
    assert rates is not None
    assert seen == [mfg_sys]
    assert rates.invention_rate == pytest.approx(0.2 + SCC_SURCHARGE_RATE)
