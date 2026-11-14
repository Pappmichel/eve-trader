"""Symmetry Decryptor vs. no decryptor (realized/decryptors.py) and the
loader step that applies the decision to invention jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader.realized import decryptors as d
from eve_trader.realized import loader
from eve_trader.realized.costing import IndustryJob, JobInput

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
PRODUCT = 5001       # invented T2 blueprint
OTHER = 5002
T1_BP = 4001
SYMMETRY = 34206
PARITY = 34204


def at(day: int) -> datetime:
    return T0 + timedelta(days=day)


def amb(key: int, day: int, successes: int, *, product: int = PRODUCT, delivered: bool = True) -> d.AmbiguousJob:
    return d.AmbiguousJob(("corporation", 1, key), product, 10, at(day),
                          delivered_at=at(day + 1) if delivered else None, successes=successes)


# ---------------------------------------------------------------- pure rules
def test_probability_matches_only_none_or_symmetry():
    factor = d.skill_factor(4, 4, 4)
    assert d.is_none_or_symmetry(0.3 * factor, 0.3, factor)
    assert not d.is_none_or_symmetry(0.3 * factor * 1.5, 0.3, factor)   # Parity
    assert not d.is_none_or_symmetry(1.0, 0.9, factor)                  # capped
    assert not d.is_none_or_symmetry(0.4, 0.3, None)                     # skills unknown


def test_latest_delivery_takes_the_highest_item_ids():
    # Older ME 2 copies (ids 1-4), then a Symmetry batch (ids 10-12).
    copies = {PRODUCT: [(1, 2), (2, 2), (3, 2), (4, 2), (10, 3), (11, 3), (12, 3)]}
    jobs = [amb(1, 1, 2), amb(2, 3, 2), amb(3, 6, 3)]
    out = d.resolve(jobs, copies)
    assert out[("corporation", 1, 3)] == d.SYMMETRY       # ids 12, 11, 10
    assert out[("corporation", 1, 2)] == d.NO_DECRYPTOR   # ids 4, 3
    assert out[("corporation", 1, 1)] == d.NO_DECRYPTOR   # ids 2, 1


def test_running_failed_and_uncovered_jobs_stay_undecided():
    copies = {PRODUCT: [(1, 2), (2, 2)], OTHER: [(5, 3)]}
    jobs = [amb(1, 1, 2), amb(2, 5, 0), amb(3, 8, 4, delivered=False),
            amb(4, 0, 3, product=OTHER), amb(5, 9, 1, product=7000)]
    out = d.resolve(jobs, copies)
    assert out == {("corporation", 1, 1): d.NO_DECRYPTOR, ("corporation", 1, 4): d.SYMMETRY}


def test_me3_copies_do_not_count_when_parity_made_the_product():
    copies = {PRODUCT: [(1, 3), (2, 3)]}
    assert d.resolve([amb(1, 1, 2)], copies, frozenset({PRODUCT})) == {}
    # Copies of other decryptors (ME 4) are skipped, not taken.
    copies = {PRODUCT: [(1, 3), (2, 3), (9, 4)]}
    assert d.resolve([amb(1, 1, 2)], copies) == {("corporation", 1, 1): d.SYMMETRY}


# ---------------------------------------------------------------- loader step
def _job(job_id: int, runs: int, day: int, *, product=PRODUCT, inputs=None, probability=0.4,
         status="delivered", successes=None) -> IndustryJob:
    return IndustryJob(
        owner_type="corporation", owner_id=1, job_id=job_id, activity_id=8,
        blueprint_type_id=T1_BP, product_type_id=product, runs=runs, start_date=at(day),
        end_date=at(day + 1), status=status, probability=probability,
        successful_runs=runs if successes is None else successes,
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
    copies = {PRODUCT: [(100 + i, 3) for i in range(30)], OTHER: [(200 + i, 2) for i in range(10)]}
    monkeypatch.setattr(loader.db, "blueprint_copies_by_type",
                        lambda owners, types: {t: copies[t] for t in types if t in copies})


def test_loader_adds_symmetry_and_clears_the_assumption(patched):
    jobs = [_job(1, 30, 2), _job(2, 10, 3, product=OTHER), _job(3, 5, 4, probability=0.9),
            _job(5, 20, 6, status="active")]
    sym, none, unmatched, running = loader._resolve_symmetry(jobs)
    assert [(i.type_id, i.quantity, i.source) for i in sym.inputs] == [
        (20410, 60.0, "probability_decryptor"), (SYMMETRY, 30.0, "probability_decryptor")]
    assert sym.output_runs_per_success == 12.0
    assert [i.source for i in none.inputs] == ["probability_decryptor"]
    assert not any(i.type_id == SYMMETRY for i in none.inputs)
    assert unmatched is jobs[2]    # probability is not 1.0x: left alone
    assert running is jobs[3]      # no copies yet: stays assumed


def test_loader_ignores_me3_copies_when_parity_made_that_product(patched):
    parity_job = _job(4, 10, 1, inputs=[JobInput(PARITY, 10.0, "probability_decryptor")], probability=0.6)
    jobs = [parity_job, _job(1, 30, 2)]
    out = loader._resolve_symmetry(jobs)
    assert out[1] is jobs[1]


def test_loader_falls_back_to_current_skills_when_none_were_frozen(patched, monkeypatch):
    monkeypatch.setattr(loader.db, "invention_skill_levels_for_owners", lambda owners: {})
    monkeypatch.setattr(loader, "invention_skill_factor",
                        lambda bp, installer: (d.skill_factor(4, 4, 4), 4, 4, 4))
    [sym] = loader._resolve_symmetry([_job(1, 30, 2)])
    assert any(i.type_id == SYMMETRY for i in sym.inputs)
