"""Symmetry Decryptor vs. no decryptor on invention jobs (pure, no I/O).

ESI does not say which decryptor an invention job used; `inputs.py` infers
it from the job's success probability. Symmetry and "no decryptor" share
the probability multiplier 1.0, so the probability cannot tell them apart.
The invented copies can: no decryptor yields ME 2 (no other decryptor
does), Symmetry yields ME 3 (as do Parity and Optimized Attainment, which
the probability does identify on their own jobs).

Rules per ambiguous job, from the invented copies of its product:
- only ME 3 evidence -> Symmetry;
- only ME 2 evidence -> no decryptor;
- both -> Symmetry while Symmetry bought up to the job's start still
  covers its runs (jobs in start order, one decryptor per run), otherwise
  undecided;
- no evidence -> undecided.
Undecided jobs keep the assumed "no decryptor" and its warning.
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


@dataclass(frozen=True)
class AmbiguousJob:
    key: tuple
    product_type_id: int
    runs: int
    start: datetime


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


def copy_evidence(copies: Sequence[tuple[int, int]], ambiguous_me3: bool) -> tuple[bool, bool]:
    """(Symmetry seen, no decryptor seen) from invented copies (ME, runs) of
    one product. ME 3 counts only when no Parity / Optimized Attainment job
    produced that product (`ambiguous_me3`)."""
    me_sym = DECRYPTORS[SYMMETRY].me_bonus
    me_none = DECRYPTORS[NO_DECRYPTOR].me_bonus
    sym = not ambiguous_me3 and any(int(me) == me_sym for me, _runs in copies)
    none = any(int(me) == me_none for me, _runs in copies)
    return sym, none


def resolve(jobs: Sequence[AmbiguousJob], evidence: dict[int, tuple[bool, bool]],
            purchases: Sequence[tuple[datetime, float]]) -> dict[tuple, str]:
    """Decision per job key: SYMMETRY or NO_DECRYPTOR; undecided jobs are
    left out. `purchases` are (date, quantity) of Symmetry Decryptors the
    tenant acquired."""
    bought = sorted(purchases, key=lambda p: p[0])
    out: dict[tuple, str] = {}
    used = 0.0
    idx = 0
    available = 0.0
    for job in sorted(jobs, key=lambda j: (j.start, j.key)):
        while idx < len(bought) and bought[idx][0] <= job.start:
            available += float(bought[idx][1])
            idx += 1
        sym, none = evidence.get(int(job.product_type_id), (False, False))
        if sym and not none:
            out[job.key] = SYMMETRY
            used += job.runs
        elif none and not sym:
            out[job.key] = NO_DECRYPTOR
        elif sym and none and available - used >= job.runs:
            out[job.key] = SYMMETRY
            used += job.runs
    return out
