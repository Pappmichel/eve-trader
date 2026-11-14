"""Symmetry Decryptor vs. no decryptor (realized/decryptors.py) and the
loader step that applies the decision to invention jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader.realized import decryptors as d
from eve_trader.realized import loader
from eve_trader.realized.costing import IndustryJob, JobInput, WalletTransaction

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
PRODUCT = 5001       # invented T2 blueprint
OTHER = 5002
T1_BP = 4001
SYMMETRY = 34206
PARITY = 34204


def at(day: int) -> datetime:
    return T0 + timedelta(days=day)


def amb(key: int, runs: int, day: int, product: int = PRODUCT) -> d.AmbiguousJob:
    return d.AmbiguousJob(("corporation", 1, key), product, runs, at(day))


# ---------------------------------------------------------------- pure rules
def test_probability_matches_only_none_or_symmetry():
    factor = d.skill_factor(4, 4, 4)
    assert d.is_none_or_symmetry(0.3 * factor, 0.3, factor)
    assert not d.is_none_or_symmetry(0.3 * factor * 1.5, 0.3, factor)   # Parity
    assert not d.is_none_or_symmetry(1.0, 0.9, factor)                  # capped
    assert not d.is_none_or_symmetry(0.4, 0.3, None)                     # skills unknown


def test_copy_evidence_me3_needs_no_other_me3_decryptor():
    assert d.copy_evidence([(3, 12), (3, 5)], ambiguous_me3=False) == (True, False)
    assert d.copy_evidence([(3, 12)], ambiguous_me3=True) == (False, False)
    assert d.copy_evidence([(2, 10), (3, 12)], ambiguous_me3=False) == (True, True)
    assert d.copy_evidence([(0, 10), (4, 11)], ambiguous_me3=False) == (False, False)


def test_resolve_by_evidence_and_by_bought_symmetry_when_mixed():
    evidence = {PRODUCT: (True, True), OTHER: (True, False), 6000: (False, True)}
    jobs = [amb(1, 100, 2), amb(2, 100, 5), amb(3, 50, 1, OTHER), amb(4, 10, 3, 6000), amb(5, 10, 4, 7000)]
    # 120 bought on day 0, 100 more on day 4.
    out = d.resolve(jobs, evidence, [(at(0), 120), (at(4), 100)])
    assert out[("corporation", 1, 3)] == d.SYMMETRY       # only ME 3 copies (uses 50)
    assert ("corporation", 1, 1) not in out                # mixed: 120 - 50 < 100 on day 2
    assert out[("corporation", 1, 2)] == d.SYMMETRY       # day 5: 220 - 50 >= 100
    assert out[("corporation", 1, 4)] == d.NO_DECRYPTOR   # only ME 2 copies
    assert ("corporation", 1, 5) not in out                # no copies at all


# ---------------------------------------------------------------- loader step
def _job(job_id: int, runs: int, day: int, *, product=PRODUCT, inputs=None, probability=0.4) -> IndustryJob:
    return IndustryJob(
        owner_type="corporation", owner_id=1, job_id=job_id, activity_id=8,
        blueprint_type_id=T1_BP, product_type_id=product, runs=runs, start_date=at(day),
        status="delivered", probability=probability,
        inputs=tuple(inputs or [JobInput(20410, 2.0 * runs, "assumed_decryptor")]),
        output_runs_per_success=10.0,
    )


@pytest.fixture
def patched(monkeypatch):
    factor = d.skill_factor(4, 4, 4)
    monkeypatch.setattr(loader.access, "shared_owners", lambda kind: [("corporation", 1)])
    monkeypatch.setattr(loader.db, "invention_skill_levels_for_owners",
                        lambda owners: {("corporation", 1, j): (4, 4, 4) for j in range(1, 10)})
    monkeypatch.setattr(loader.storage, "get_invention_recipe",
                        lambda bp, product: {"base_probability": 0.4 / factor, "base_runs": 10})
    copies = {PRODUCT: [(3, 12)], OTHER: [(2, 10)]}
    monkeypatch.setattr(loader.db, "blueprint_copies_by_type",
                        lambda owners, types: {t: copies[t] for t in types if t in copies})


def test_loader_adds_symmetry_and_clears_the_assumption(patched):
    jobs = [_job(1, 30, 2), _job(2, 10, 3, product=OTHER), _job(3, 5, 4, probability=0.9)]
    buys = [WalletTransaction("character", 9, 1, at(0), SYMMETRY, 60003760, 200000.0, 100, True)]
    out = loader._resolve_symmetry(jobs, buys, (), t0=T0, chars=frozenset({9}), corps=frozenset({1}),
                                   corp_wallets=frozenset())
    sym, none, unmatched = out
    assert [(i.type_id, i.quantity, i.source) for i in sym.inputs] == [
        (20410, 60.0, "probability_decryptor"), (SYMMETRY, 30.0, "probability_decryptor")]
    assert sym.output_runs_per_success == 12.0
    assert [i.source for i in none.inputs] == ["probability_decryptor"]
    assert not any(i.type_id == SYMMETRY for i in none.inputs)
    assert unmatched is jobs[2]    # probability is not 1.0x: left alone


def test_loader_ignores_me3_copies_when_parity_made_that_product(patched):
    parity_job = _job(4, 10, 1, inputs=[JobInput(PARITY, 10.0, "probability_decryptor")], probability=0.6)
    jobs = [parity_job, _job(1, 30, 2)]
    out = loader._resolve_symmetry(jobs, [], (), t0=T0, chars=frozenset(), corps=frozenset({1}),
                                   corp_wallets=frozenset())
    assert out[1] is jobs[1]
