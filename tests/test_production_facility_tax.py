"""Facility tax observed on the tenant's own last jobs (production/facility_tax.py).

The archive reads, the one-query cache and the resolution order run for real
against Postgres; blueprint materials and ESI adjusted prices are stubbed (SDE
tables and HTTP). EIV in these tests: one run of blueprint BP consumes
100 x Tritanium at 10 ISK = 1,000 ISK, so a job's tax is rate x 1,000 x runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader import storage
from eve_trader.production import actions as production_actions
from eve_trader.production import engine, facility_tax, invention
from eve_trader.production.config import ProductionConfig
from eve_trader.production.constants import ACTIVITY_MODS, SCC_SURCHARGE_RATE
from eve_trader.realized import db as ledger_db

from . import pg_helpers
from .pg_helpers import (  # noqa: F401
    _apply_job_category_cost_index_overrides_schema, _apply_phase1_schema,
    _apply_production_ledger_schema, tenant,
)

pytestmark = pg_helpers.postgres_required()

ALICE = 920001
BOB = 920002
BP = 1001            # manufacturing blueprint: 100 Tritanium per run
BP_INVENTED = 1002   # blueprint an invention job makes: 200 Tritanium per run
BP_NO_RECIPE = 1003  # no materials known
BP_UNPRICED = 1004   # needs a material with no adjusted price
TRIT = 34
UNPRICED = 99999
FAC_A = 1_049_588_174_021
FAC_B = 1_049_588_174_022
NOW = datetime.now(timezone.utc)

MATERIALS = {
    (BP, 1): [(TRIT, 100)],
    (BP_INVENTED, 1): [(TRIT, 200)],
    (BP_UNPRICED, 1): [(TRIT, 100), (UNPRICED, 1)],
    (BP, 11): [(TRIT, 100)],   # reaction formula of the same size
}


@pytest.fixture(autouse=True)
def _setup(monkeypatch, _apply_production_ledger_schema,
           _apply_job_category_cost_index_overrides_schema):
    pg_helpers.wipe_tables(
        "ledger_industry_jobs", "ledger_wallet_journal", "esi_sharing",
        "job_category_facilities", "job_category_locations",
    )
    monkeypatch.setattr(storage, "get_blueprint_materials",
                        lambda blueprint, activity: MATERIALS.get((blueprint, activity), []))
    monkeypatch.setattr(facility_tax, "_adjusted_prices", lambda: {TRIT: 10.0})
    # The category/profile lookups read the SDE; this file is about the tax.
    monkeypatch.setattr(engine, "job_category", lambda type_id: "Large Ships")
    monkeypatch.setattr(engine, "_structure_profile", lambda activity, type_id: "manufacturing")
    monkeypatch.setattr(engine, "_security_multiplier_for", lambda profile, cfg: 1.0)
    engine.invalidate_facility_cache(all_tenants=True)
    facility_tax.invalidate(all_tenants=True)
    facility_tax.clear_parent_cache()
    invention.clear_invention_system_cache()
    yield
    engine.invalidate_facility_cache(all_tenants=True)
    facility_tax.invalidate(all_tenants=True)
    facility_tax.clear_parent_cache()
    invention.clear_invention_system_cache()


def _share(owner_id: int, data_kind: str):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO esi_sharing (owner_type, owner_id, data_kind, tool_key) "
            "VALUES ('character', ?, ?, 'production') ON CONFLICT DO NOTHING",
            (owner_id, data_kind),
        )


def _share_all(owner_id: int = ALICE):
    _share(owner_id, "industry_jobs")
    _share(owner_id, "wallet")


_ids = iter(range(5000, 10**6))


def _job(days_ago: float, facility: int, rate: float, *, activity: int = 1, runs: int = 1,
         blueprint: int = BP, product: int | None = None, licensed_runs: int | None = None,
         owner: int = ALICE, wallet_owner: int | None = None, tax: float | None = None,
         eiv: float = 1000.0, cost: float | None = None, with_tax: bool = True) -> int:
    """Archives a job and optionally its industry_job_tax entry; the tax
    defaults to `rate` x `eiv` (the job's EIV in this file's stub prices).
    `cost` is ESI's facility fee + tax; leave None so tax-only observation
    tests do not also produce a total-rate observation."""
    job_id = next(_ids)
    start = NOW - timedelta(days=days_ago)
    ledger_db.upsert_ledger_industry_jobs([(
        "character", owner, job_id, activity, None, blueprint, product, runs, licensed_runs, None,
        cost, facility, None, None, owner, start, start + timedelta(hours=2), None, "active", None,
    )])
    if with_tax:
        isk = (rate * eiv) if tax is None else tax
        ledger_db.upsert_ledger_wallet_journal([(
            "character", wallet_owner or owner, 0, job_id + 10**7, start, "industry_job_tax", -isk,
            job_id, "industry_job_id", None, None, None, None, None,
        )])
    return job_id


def _cfg(**kwargs) -> ProductionConfig:
    return ProductionConfig(facility_tax_rate=0.0025, **kwargs)


def _resolve(category="Large Ships", activity="Tech I", profile="manufacturing", cfg=None) -> dict:
    return engine.facility_resolution(category, activity, profile, cfg or _cfg())


def _locate(category: str, facility: int):
    storage.upsert_category_location(category, facility)
    engine.invalidate_facility_cache()


# ------------------------------------------------------------------ observation

def test_rate_is_the_job_tax_over_the_estimated_item_value(tenant):
    _share_all()
    _job(1, FAC_A, 0.0, runs=2, tax=5.0)  # EIV 2,000 -> 0.25%
    seen = facility_tax.observed_rates()[(FAC_A, 1)]
    assert seen.rate == pytest.approx(0.0025)
    assert seen.jobs == 1


def test_value_is_the_median_of_the_three_newest_jobs(tenant):
    _share_all()
    _job(2, FAC_A, 0.010)
    _job(3, FAC_A, 0.002)
    _job(4, FAC_A, 0.004)
    _job(12, FAC_A, 0.500)  # older than the newest three: not used
    seen = facility_tax.observed_rates()[(FAC_A, 1)]
    assert seen.rate == pytest.approx(0.004)
    assert seen.jobs == 3


def test_rate_follows_the_newest_jobs_when_the_owner_changes_the_tax(tenant):
    _share_all()
    for days in (5, 6, 7):
        _job(days, FAC_A, 0.0025)
    assert facility_tax.observed_rates()[(FAC_A, 1)].rate == pytest.approx(0.0025)

    _job(1, FAC_A, 0.0100)
    _job(0.5, FAC_A, 0.0100)
    facility_tax.invalidate()

    seen = facility_tax.observed_rates()[(FAC_A, 1)]
    assert seen.rate == pytest.approx(0.0100)


def test_observation_carries_the_age_of_its_newest_job(tenant):
    _share_all()
    _job(4, FAC_A, 0.005)
    _job(9, FAC_A, 0.005)
    seen = facility_tax.observed_rates()[(FAC_A, 1)]
    assert seen.age_days() == pytest.approx(4, abs=0.1)


def test_jobs_older_than_thirty_days_are_not_observed(tenant):
    _share_all()
    _job(31, FAC_A, 0.005)
    assert facility_tax.observed_rates() == {}


def test_jobs_without_eiv_or_adjusted_prices_are_skipped(tenant):
    _share_all()
    _job(1, FAC_A, 0.0, blueprint=BP_NO_RECIPE, tax=9.0)
    _job(2, FAC_A, 0.0, blueprint=BP_UNPRICED, tax=9.0)
    _job(3, FAC_A, 0.005)  # the only usable one, although it is the oldest
    seen = facility_tax.observed_rates()[(FAC_A, 1)]
    assert seen.rate == pytest.approx(0.005)
    assert seen.jobs == 1


def test_a_job_without_a_tax_entry_is_not_an_observation(tenant):
    _share_all()
    job_id = _job(1, FAC_A, 0.005)
    with storage.connect() as conn:
        conn.execute("DELETE FROM ledger_wallet_journal WHERE context_id = ?", (job_id,))
    assert facility_tax.observed_rates() == {}


def test_copy_and_invention_jobs_use_two_percent_of_the_manufacturing_eiv(tenant):
    _share_all()
    # Copy: 2% of BP's EIV (1,000) x 1 run x 10 licensed runs = 200 ISK EIV.
    _job(1, FAC_A, 0.0, activity=5, runs=1, licensed_runs=10, tax=0.5, blueprint=BP)
    # Invention: 2% of the invented blueprint's EIV (2,000) x 1 run = 40 ISK EIV.
    _job(1, FAC_A, 0.0, activity=8, runs=1, blueprint=BP, product=BP_INVENTED, tax=0.1)
    rates = facility_tax.observed_rates()
    assert rates[(FAC_A, 5)].rate == pytest.approx(0.0025)
    assert rates[(FAC_A, 8)].rate == pytest.approx(0.0025)


def test_reaction_job_is_observed_under_activity_eleven(tenant):
    _share_all()
    _job(1, FAC_A, 0.0, activity=11, runs=3, tax=7.5)  # EIV 3,000
    assert facility_tax.observed_rates()[(FAC_A, 11)].rate == pytest.approx(0.0025)


def test_a_job_counted_under_two_owners_is_one_job(tenant):
    _share_all()
    _share_all(BOB)
    job_id = _job(1, FAC_A, 0.005)
    # The same job also archived from the corporation's side.
    ledger_db.upsert_ledger_industry_jobs([(
        "character", BOB, job_id, 1, None, BP, None, 1, None, None, 1.0, FAC_A, None, None, BOB,
        NOW - timedelta(days=1), NOW, None, "active", None,
    )])
    seen = facility_tax.observed_rates()[(FAC_A, 1)]
    assert seen.jobs == 1
    assert seen.rate == pytest.approx(0.005)  # the tax is not summed twice


# ------------------------------------------------------------------ D11 scope

def test_jobs_of_an_unshared_owner_are_ignored(tenant):
    _share_all(ALICE)
    _job(1, FAC_A, 0.020, owner=BOB)  # BOB shares nothing
    assert facility_tax.observed_rates() == {}


def test_a_tax_entry_from_an_unshared_wallet_is_ignored(tenant):
    _share(ALICE, "industry_jobs")
    _share(BOB, "wallet")
    _job(1, FAC_A, 0.020, owner=ALICE, wallet_owner=ALICE)  # ALICE's wallet is not shared
    assert facility_tax.observed_rates() == {}


def test_revoking_the_share_hides_the_observation_again(tenant):
    _share_all()
    _job(1, FAC_A, 0.005)
    assert (FAC_A, 1) in facility_tax.observed_rates()
    with storage.connect() as conn:
        conn.execute("DELETE FROM esi_sharing")
    facility_tax.invalidate()
    assert facility_tax.observed_rates() == {}


# ------------------------------------------------------------------ resolution

def test_the_observed_rate_replaces_the_settings_rate_for_the_category_structure(tenant):
    _share_all()
    _job(1, FAC_A, 0.0050)
    _locate("Large Ships", FAC_A)
    resolved = _resolve()
    assert resolved["tax_rate"] == pytest.approx(0.0050)
    assert resolved["tax_source"] == "observed"
    assert resolved["tax_jobs"] == 1
    assert abs(resolved["tax_observed_at"] - (NOW - timedelta(days=1))) < timedelta(minutes=1)


def test_two_structures_do_not_mix_their_rates(tenant):
    _share_all()
    _job(1, FAC_A, 0.0050)
    _job(1, FAC_B, 0.0100)
    _locate("Large Ships", FAC_A)
    assert _resolve()["tax_rate"] == pytest.approx(0.0050)

    _locate("Large Ships", FAC_B)
    assert _resolve()["tax_rate"] == pytest.approx(0.0100)


def test_a_category_resolves_only_its_own_structure(tenant):
    _share_all()
    _job(1, FAC_A, 0.0050)
    _job(1, FAC_B, 0.0100)
    _locate("Large Ships", FAC_A)
    _locate("Medium Ships", FAC_B)
    assert _resolve("Large Ships")["tax_rate"] == pytest.approx(0.0050)
    assert _resolve("Medium Ships")["tax_rate"] == pytest.approx(0.0100)


def test_a_manufacturing_job_does_not_set_the_reaction_rate(tenant):
    _share_all()
    _job(1, FAC_A, 0.0500)  # manufacturing only
    _locate("Reactions", FAC_A)
    resolved = _resolve("Reactions", "Reaction", "reaction")
    assert (resolved["tax_rate"], resolved["tax_source"]) == (0.0025, "settings")

    _job(1, FAC_A, 0.0300, activity=11)
    engine.invalidate_facility_cache()
    resolved = _resolve("Reactions", "Reaction", "reaction")
    assert (resolved["tax_rate"], resolved["tax_source"]) == (pytest.approx(0.03), "observed")


def test_the_category_override_wins_over_the_observation(tenant):
    _share_all()
    _job(1, FAC_A, 0.0050)
    _locate("Large Ships", FAC_A)
    production_actions.do_set_category_facility("Large Ships", facility_tax_rate=0.0200)
    resolved = _resolve()
    assert resolved["tax_rate"] == pytest.approx(0.0200)
    assert resolved["tax_source"] == "override"

    listed = next(r for r in production_actions.do_list_category_facilities(_cfg())["categories"]
                  if r["category"] == "Large Ships")
    assert listed["tax"]["source"] == "override"
    assert listed["tax"]["saved"] == pytest.approx(0.0200)
    # What was seen stays visible next to the override.
    assert listed["tax"]["observed"]["rate"] == pytest.approx(0.0050)
    assert listed["saved"]["facility_tax_rate"] == pytest.approx(0.0200)


def test_clearing_the_override_returns_to_the_observed_rate(tenant):
    _share_all()
    _job(1, FAC_A, 0.0050)
    _locate("Large Ships", FAC_A)
    production_actions.do_set_category_facility("Large Ships", facility_tax_rate=0.0200)
    production_actions.do_clear_category_facility("Large Ships")
    assert _resolve()["tax_source"] == "observed"


def test_without_jobs_the_settings_rate_applies(tenant):
    _share_all()
    _locate("Large Ships", FAC_A)
    resolved = _resolve(cfg=_cfg())
    assert (resolved["tax_rate"], resolved["tax_source"]) == (0.0025, "settings")
    assert resolved["tax_observed_at"] is None


def test_a_category_without_a_structure_uses_the_settings_rate(tenant):
    _share_all()
    _job(1, FAC_A, 0.0500)
    resolved = _resolve()
    assert (resolved["tax_rate"], resolved["tax_source"]) == (0.0025, "settings")


def test_a_tax_override_alone_is_stored_and_validated(tenant):
    production_actions.do_set_category_facility("Large Ships", facility_tax_rate=0.01)
    assert storage.load_category_facilities()["Large Ships"]["facility_tax_rate"] == pytest.approx(0.01)
    with pytest.raises(production_actions.ActionError):
        production_actions.do_set_category_facility("Large Ships", facility_tax_rate=1.5)
    with pytest.raises(production_actions.ActionError):
        production_actions.do_set_category_facility("Large Ships", facility_tax_rate=-0.1)
    production_actions.do_set_category_facility("Large Ships")  # all fields empty: row removed
    assert "Large Ships" not in storage.load_category_facilities()


# ------------------------------------------------------------------ job cost

def test_job_cost_rate_uses_the_observed_tax(tenant):
    _share_all()
    _job(1, FAC_A, 0.0120)
    _locate("Large Ships", FAC_A)
    cfg = _cfg()
    rate = engine._job_cost_rate("Tech I", 5, cfg, {})
    assert rate == pytest.approx(ACTIVITY_MODS["Tech I"].job_cost_rate + 0.0120 + SCC_SURCHARGE_RATE)


def test_job_cost_rate_falls_back_to_settings_without_observation(tenant):
    _share_all()
    _locate("Large Ships", FAC_A)
    cfg = _cfg()
    rate = engine._job_cost_rate("Tech I", 5, cfg, {})
    assert rate == pytest.approx(ACTIVITY_MODS["Tech I"].job_cost_rate + 0.0025 + SCC_SURCHARGE_RATE)


def test_many_items_cost_one_archive_query_and_one_price_read(tenant, monkeypatch):
    _share_all()
    _job(1, FAC_A, 0.0120)
    _locate("Large Ships", FAC_A)
    calls = {"archive": 0, "prices": 0}
    real_rows = ledger_db.industry_job_tax_rows

    def counting_rows(*args, **kwargs):
        calls["archive"] += 1
        return real_rows(*args, **kwargs)

    def counting_prices():
        calls["prices"] += 1
        return {TRIT: 10.0}

    monkeypatch.setattr(ledger_db, "industry_job_tax_rows", counting_rows)
    monkeypatch.setattr(facility_tax, "_adjusted_prices", counting_prices)
    cfg = _cfg()
    for type_id in range(1, 61):
        engine._job_cost_rate("Tech I", type_id, cfg, {})
    assert calls == {"archive": 1, "prices": 1}


# ------------------------------------------------------------------ invention

class _NoNetworkClient:
    def __init__(self):
        pass

    def get_adjusted_prices(self):
        return {TRIT: 10.0}

    def get_system_cost_indices(self, system_id, activities=()):
        return {}


@pytest.mark.live_fee_rates
def test_invention_fees_use_the_rates_observed_at_the_invention_location(tenant, monkeypatch):
    from eve_trader import esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _NoNetworkClient)
    invention.clear_invention_system_cache()
    _share_all()
    _job(1, FAC_A, 0.0, activity=8, product=BP_INVENTED, tax=0.2)   # EIV 40 -> 0.5%
    _job(1, FAC_A, 0.0, activity=5, licensed_runs=1, tax=0.3)       # EIV 20 -> 1.5%
    cfg = _cfg(invention_location_id=FAC_A, manufacturing_system_id=None)

    rates = invention.fee_rates(cfg)

    assert rates is not None
    flat = ACTIVITY_MODS["Tech II"].job_cost_rate
    cost_mult, _, _ = invention.structure_rig_multiplier(cfg.manufacturing_structure_type,
                                                         cfg.manufacturing_rig_tier)
    assert rates.invention_rate == pytest.approx(flat * cost_mult + 0.005 + SCC_SURCHARGE_RATE)
    assert rates.copying_rate == pytest.approx(flat * cost_mult + 0.015 + SCC_SURCHARGE_RATE)


@pytest.mark.live_fee_rates
def test_invention_fees_fall_back_to_settings_without_observation(tenant, monkeypatch):
    from eve_trader import esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _NoNetworkClient)
    invention.clear_invention_system_cache()
    _share_all()
    cfg = _cfg(invention_location_id=FAC_A, manufacturing_system_id=None)

    rates = invention.fee_rates(cfg)

    flat = ACTIVITY_MODS["Tech II"].job_cost_rate
    cost_mult, _, _ = invention.structure_rig_multiplier(cfg.manufacturing_structure_type,
                                                         cfg.manufacturing_rig_tier)
    assert rates.invention_rate == pytest.approx(flat * cost_mult + 0.0025 + SCC_SURCHARGE_RATE)


@pytest.mark.live_fee_rates
def test_invention_location_inside_a_structure_uses_that_structures_rates(tenant, monkeypatch):
    from eve_trader import esi_client as esi_client_module
    from eve_trader.locations.reader import LocationInfo
    monkeypatch.setattr(esi_client_module, "ESIClient", _NoNetworkClient)
    invention.clear_invention_system_cache()
    office = 1_040_000_000_001
    monkeypatch.setattr("eve_trader.locations.reader.describe",
                        lambda ids: {office: LocationInfo(location_id=office, kind="office", parent_id=FAC_A)})
    _share_all()
    _job(1, FAC_A, 0.0, activity=8, product=BP_INVENTED, tax=0.2)
    cfg = _cfg(invention_location_id=office, manufacturing_system_id=None)

    rates = invention.fee_rates(cfg)

    flat = ACTIVITY_MODS["Tech II"].job_cost_rate
    cost_mult, _, _ = invention.structure_rig_multiplier(cfg.manufacturing_structure_type,
                                                         cfg.manufacturing_rig_tier)
    assert rates.invention_rate == pytest.approx(flat * cost_mult + 0.005 + SCC_SURCHARGE_RATE)


# ------------------------------------------------ observed total fee (invention/copy)


@pytest.mark.live_fee_rates
def test_invention_fees_use_the_observed_total_rate_directly(tenant, monkeypatch):
    # Measured shape: cost/EIV replaces the whole formula; tax and SCC are
    # not added again (would double-count). Invention EIV here: 200 Trit x 10
    # x 0.02 = 40; cost 6.152 -> 15.38%.
    from eve_trader import esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _NoNetworkClient)
    _share_all()
    _job(1, FAC_A, 0.0, activity=8, product=BP_INVENTED, cost=6.152, with_tax=False, eiv=40.0)
    cfg = _cfg(invention_location_id=FAC_A, manufacturing_system_id=None)

    rates = invention.fee_rates(cfg)

    assert rates is not None
    assert rates.invention_rate == pytest.approx(6.152 / 40.0)
    # Formula with Settings tax would be much higher (~20%); prove no add-on.
    flat = ACTIVITY_MODS["Tech II"].job_cost_rate
    cost_mult, _, _ = invention.structure_rig_multiplier(cfg.manufacturing_structure_type,
                                                         cfg.manufacturing_rig_tier)
    modelled = flat * cost_mult + 0.0025 + SCC_SURCHARGE_RATE
    assert rates.invention_rate == pytest.approx(0.1538)
    assert rates.invention_rate != pytest.approx(modelled)
    assert rates.invention_rate != pytest.approx(0.1538 + 0.0025 + SCC_SURCHARGE_RATE)


@pytest.mark.live_fee_rates
def test_copy_job_without_a_tax_journal_still_sets_the_total_rate(tenant, monkeypatch):
    from eve_trader import esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _NoNetworkClient)
    _share_all()
    # Copy EIV: 100 Trit x 10 x 0.02 x 1 licensed run = 20; cost 2.716 -> 13.58%.
    _job(1, FAC_A, 0.0, activity=5, licensed_runs=1, cost=2.716, with_tax=False, eiv=20.0)
    cfg = _cfg(invention_location_id=FAC_A, manufacturing_system_id=None)

    rates = invention.fee_rates(cfg)

    assert rates.copying_rate == pytest.approx(2.716 / 20.0)
    assert facility_tax.observed_rates() == {}  # no tax journal -> no tax observation


@pytest.mark.live_fee_rates
def test_invention_fees_fall_back_to_the_formula_without_a_total_observation(tenant, monkeypatch):
    from eve_trader import esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _NoNetworkClient)
    _share_all()
    # Tax-only job (cost=None): total observation stays empty, formula + tax.
    _job(1, FAC_A, 0.0, activity=8, product=BP_INVENTED, tax=0.2, cost=None)
    cfg = _cfg(invention_location_id=FAC_A, manufacturing_system_id=None)

    rates = invention.fee_rates(cfg)

    flat = ACTIVITY_MODS["Tech II"].job_cost_rate
    cost_mult, _, _ = invention.structure_rig_multiplier(cfg.manufacturing_structure_type,
                                                         cfg.manufacturing_rig_tier)
    assert rates.invention_rate == pytest.approx(flat * cost_mult + 0.005 + SCC_SURCHARGE_RATE)


@pytest.mark.live_fee_rates
def test_unshared_job_owner_does_not_set_the_total_rate(tenant, monkeypatch):
    from eve_trader import esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _NoNetworkClient)
    _share_all(ALICE)
    _job(1, FAC_A, 0.0, activity=8, product=BP_INVENTED, owner=BOB, cost=6.152, with_tax=False)
    cfg = _cfg(invention_location_id=FAC_A, manufacturing_system_id=None)

    rates = invention.fee_rates(cfg)

    flat = ACTIVITY_MODS["Tech II"].job_cost_rate
    cost_mult, _, _ = invention.structure_rig_multiplier(cfg.manufacturing_structure_type,
                                                         cfg.manufacturing_rig_tier)
    assert rates.invention_rate == pytest.approx(flat * cost_mult + 0.0025 + SCC_SURCHARGE_RATE)
    assert facility_tax.observed_total_rates() == {}


def test_manufacturing_stays_on_tax_observation_not_the_total_rate(tenant):
    # Total-rate query only covers invention/copy; manufacturing keeps the
    # tax observation that _job_cost_rate reads through facility_resolution.
    _share_all()
    _locate("Large Ships", FAC_A)
    _job(1, FAC_A, 0.01, activity=1, cost=50.0)
    cfg = _cfg()

    resolved = _resolve(cfg=cfg)
    assert resolved["tax_rate"] == pytest.approx(0.01)
    assert resolved["tax_source"] == "observed"
    assert (FAC_A, 1) not in facility_tax.observed_total_rates()
    assert (FAC_A, 1) in facility_tax.observed_rates()



# ------------------------------------------------ parent-resolve cache (Fix 2)


@pytest.mark.live_fee_rates
def test_fee_rates_resolve_an_office_parent_at_most_once(tenant, monkeypatch):
    # Observation lives on the structure; fee_rates is called with an Office
    # inside it. Without a parent cache that would re-describe every call.
    from eve_trader import esi_client as esi_client_module
    from eve_trader.locations.reader import LocationInfo
    monkeypatch.setattr(esi_client_module, "ESIClient", _NoNetworkClient)
    office = 1_040_000_000_002
    calls = {"n": 0}

    def describe(ids):
        calls["n"] += 1
        return {office: LocationInfo(
            location_id=office, kind="office", parent_id=FAC_A, solar_system_id=30000142)}

    monkeypatch.setattr("eve_trader.locations.reader.describe", describe)
    _share_all()
    _job(1, FAC_A, 0.0, activity=8, product=BP_INVENTED, cost=6.152, with_tax=False, eiv=40.0)
    cfg = _cfg(invention_location_id=office, manufacturing_system_id=None)

    for _ in range(20):
        rates = invention.fee_rates(cfg)
        assert rates.invention_rate == pytest.approx(6.152 / 40.0)

    # Parent cache + invention-system cache share the same describe stub;
    # both cache a hit, so many fee_rates calls stay at most 2 describes.
    assert 1 <= calls["n"] <= 2
