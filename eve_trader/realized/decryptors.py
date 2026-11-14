"""Symmetry Decryptor vs. no decryptor on invention jobs (pure, no I/O).

ESI does not say which decryptor an invention job used; `inputs.py` infers
it from the job's success probability. Symmetry and "no decryptor" share
the probability multiplier 1.0, so the probability cannot tell them apart.
The invented copies can: no decryptor yields ME 2 (no other decryptor
does), Symmetry yields ME 3 (as do Parity and Optimized Attainment, which
the probability does identify on their own jobs).

ESI does not link a job to the copies it made either, but item ids are
handed out in ascending order. Per product, the delivered ambiguous jobs
are walked from the latest delivery back, and each takes as many copies as
it had successful runs, from the highest item id down. The majority ME of
those copies decides: ME 3 -> Symmetry, ME 2 -> no decryptor. ME 3 counts
only when no Parity / Optimized Attainment job made that product; copies
of any other ME are left out (their jobs are not walked either).

Undecided - and kept as the assumed "no decryptor" with its warning - are
jobs not delivered yet (their copies do not exist), jobs without a success,
and jobs the copies run out for. A later rebuild decides them once their
copies are archived.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from ..production.constants import DECRYPTORS

SYMMETRY = "Symmetry"
NO_DECRYPTOR = "None"
# Matching tolerance for the implied probability multiplier, same as
# inputs._DECRYPTOR_MULT_TOLERANCE.
MULT_TOLERANCE = 0.005
# Decryptors other than Symmetry whose invented copies also have ME 3.
OTHER_ME3 = frozenset(
    d.type_id for name, d in DECRYPTORS.items()
    if name != SYMMETRY and d.type_id and d.me_bonus == DECRYPTORS[SYMMETRY].me_bonus
)
_ME_SYMMETRY = DECRYPTORS[SYMMETRY].me_bonus
_ME_NONE = DECRYPTORS[NO_DECRYPTOR].me_bonus


@dataclass(frozen=True)
class AmbiguousJob:
    key: tuple
    product_type_id: int
    runs: int
    start: datetime
    # Delivered jobs only: their copies exist.
    delivered_at: Optional[datetime] = None
    successes: Optional[int] = None


def skill_factor(enc: int, sci1: int, sci2: int) -> float:
    return 1.0 + enc / 40.0 + (sci1 + sci2) / 30.0


def is_none_or_symmetry(probability: Optional[float], base: Optional[float],
                        factor: Optional[float]) -> bool:
    """True when the probability matches only "no decryptor" / Symmetry.
    A probability of 1.0 may be capped and says nothing."""
    if probability is None or not base or not factor or float(probability) >= 1.0:
        return False
    implied = float(probability) / (float(base) * float(factor))
    matches = {
        name for name, d in DECRYPTORS.items()
        if abs(d.probability_multiplier - implied) <= MULT_TOLERANCE
    }
    return bool(matches) and matches <= {NO_DECRYPTOR, SYMMETRY}


def resolve(jobs: Sequence[AmbiguousJob], copies: dict[int, Sequence[tuple[int, int]]],
            other_me3_products: frozenset = frozenset()) -> dict[tuple, str]:
    """Decision per job key: SYMMETRY or NO_DECRYPTOR; undecided jobs are
    left out. `copies`: product -> [(item_id, ME), ...] of archived copies."""
    by_product: dict[int, list[AmbiguousJob]] = {}
    for job in jobs:
        if job.delivered_at is not None and job.successes:
            by_product.setdefault(int(job.product_type_id), []).append(job)
    out: dict[tuple, str] = {}
    for product, group in by_product.items():
        valid = {_ME_NONE} if product in other_me3_products else {_ME_NONE, _ME_SYMMETRY}
        pool = sorted(((int(i), int(me)) for i, me in copies.get(product, ()) if int(me) in valid),
                      reverse=True)
        pos = 0
        for job in sorted(group, key=lambda j: (j.delivered_at, j.key), reverse=True):
            taken = pool[pos:pos + int(job.successes)]
            pos += int(job.successes)
            sym = sum(1 for _i, me in taken if me == _ME_SYMMETRY)
            none = len(taken) - sym
            if sym > none:
                out[job.key] = SYMMETRY
            elif none > sym:
                out[job.key] = NO_DECRYPTOR
    return out
