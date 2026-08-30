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
    monkeypatch.setattr(storage, "get_invention_recipe", lambda type_id: RECIPE)
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
