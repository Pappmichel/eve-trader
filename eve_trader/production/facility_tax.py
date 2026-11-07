"""Facility tax observed on the tenant's own recent jobs.

A structure owner can change the facility tax at any time, so no rate is
bound to a station or a job category. The rate in use is the last one seen
on our own jobs: per (facility_id, activity) the median of the newest
OBSERVATION_JOBS usable jobs started within OBSERVATION_WINDOW_DAYS, with
the date of the newest job so a page can show how old it is. The job-cost
formula takes it ahead of `ProductionConfig.facility_tax_rate`, which is
only the fallback (resolution order in `engine.facility_resolution`:
category override, observed rate, Settings).

One job's rate is its `industry_job_tax` journal entry (context id = job id)
divided by the job's estimated item value (EIV). The archive does not keep
the EIV CCP used, so it is an APPROXIMATION: the blueprint's base (ME 0)
materials at *today's* ESI adjusted prices, like `engine._build_job_cost`
does for planning. Copy and invention jobs are 2% of the manufacturing EIV
of their blueprint / invented product (`invention.JOB_BASE_EIV_FRACTION`).
A job without an EIV (blueprint unknown, a material without an adjusted
price) is skipped, never guessed; so is a job without a tax entry.

Only jobs of owners shared with a realized-profit tool count, and their tax
entries only from shared wallets (`realized.access`, D11). One query reads
every job; the result is cached per tenant for a minute and dropped by
`engine.invalidate_facility_cache`.
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

from .. import storage
from ..realized import access, db

logger = logging.getLogger(__name__)

ACTIVITY_MANUFACTURING = 1
ACTIVITY_COPYING = 5
ACTIVITY_INVENTION = 8
ACTIVITY_REACTION = 11
OBSERVED_ACTIVITIES = (ACTIVITY_MANUFACTURING, ACTIVITY_COPYING, ACTIVITY_INVENTION, ACTIVITY_REACTION)

OBSERVATION_WINDOW_DAYS = 30
OBSERVATION_JOBS = 3

# Copy and invention jobs are based on 2% of the manufacturing EIV
# (same figure as invention.JOB_BASE_EIV_FRACTION; kept here because
# invention imports this module).
COPY_INVENTION_EIV_FRACTION = 0.02

# A rate above the Settings field's own range (config._FIELD_RANGES: 0..1)
# means the approximate EIV is off by orders of magnitude: not an observation.
MAX_RATE = 1.0


@dataclass(frozen=True)
class ObservedTax:
    rate: float            # median of `jobs` job rates (a fraction, 0.0025 = 0.25%)
    observed_at: datetime  # start of the newest job used (UTC)
    jobs: int              # how many jobs the median was taken over

    def age_days(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        return max((now - self.observed_at).total_seconds() / 86400.0, 0.0)


def _materials_eiv(blueprint_type_id: Optional[int], activity_id: int,
                   adjusted: Mapping[int, float]) -> Optional[float]:
    """Base (ME 0, one run) materials at adjusted prices; None when the
    recipe is unknown or any material has no positive adjusted price."""
    if blueprint_type_id is None:
        return None
    materials = storage.get_blueprint_materials(int(blueprint_type_id), activity_id)
    if not materials:
        return None
    total = 0.0
    for material_id, quantity in materials:
        price = adjusted.get(int(material_id))
        if not price or price <= 0:
            return None
        total += float(quantity) * float(price)
    return total if total > 0 else None


def approximate_job_eiv(activity_id: int, blueprint_type_id: Optional[int],
                        product_type_id: Optional[int], runs: Optional[int],
                        licensed_runs: Optional[int],
                        adjusted: Mapping[int, float]) -> Optional[float]:
    """APPROXIMATION of the EIV a job was charged on (see module docstring):
    today's adjusted prices stand in for the ones on the job's day."""
    if not runs or runs <= 0:
        return None
    if activity_id == ACTIVITY_MANUFACTURING:
        base = _materials_eiv(blueprint_type_id, ACTIVITY_MANUFACTURING, adjusted)
        return None if base is None else base * runs
    if activity_id == ACTIVITY_REACTION:
        base = _materials_eiv(blueprint_type_id, ACTIVITY_REACTION, adjusted)
        return None if base is None else base * runs
    if activity_id == ACTIVITY_INVENTION:
        # The product of an invention job is the invented blueprint.
        base = _materials_eiv(product_type_id, ACTIVITY_MANUFACTURING, adjusted)
        return None if base is None else COPY_INVENTION_EIV_FRACTION * base * runs
    if activity_id == ACTIVITY_COPYING:
        base = _materials_eiv(blueprint_type_id, ACTIVITY_MANUFACTURING, adjusted)
        if base is None:
            return None
        return COPY_INVENTION_EIV_FRACTION * base * runs * (licensed_runs or 1)
    return None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def observe(rows: list[tuple], adjusted: Mapping[int, float]) -> dict[tuple[int, int], ObservedTax]:
    """Pure core: rows are `db.industry_job_tax_rows` tuples. Unusable jobs
    (no EIV, no positive tax, implausible rate) are dropped first, then the
    newest OBSERVATION_JOBS per (facility_id, activity_id) are medianed."""
    per_key: dict[tuple[int, int], list[tuple[datetime, float]]] = {}
    for _job_id, activity_id, blueprint_type_id, product_type_id, runs, licensed_runs, \
            facility_id, start_date, tax in rows:
        if facility_id is None or start_date is None or tax is None or float(tax) <= 0:
            continue
        eiv = approximate_job_eiv(int(activity_id), blueprint_type_id, product_type_id, runs,
                                  licensed_runs, adjusted)
        if eiv is None:
            continue
        rate = float(tax) / eiv
        if not 0 < rate <= MAX_RATE:
            continue
        per_key.setdefault((int(facility_id), int(activity_id)), []).append((_as_utc(start_date), rate))
    out: dict[tuple[int, int], ObservedTax] = {}
    for key, jobs in per_key.items():
        newest = sorted(jobs, key=lambda j: j[0], reverse=True)[:OBSERVATION_JOBS]
        out[key] = ObservedTax(
            rate=float(statistics.median(rate for _, rate in newest)),
            observed_at=newest[0][0], jobs=len(newest),
        )
    return out


def _adjusted_prices() -> dict[int, float]:
    from ..esi_client import ESIClient
    return ESIClient().get_adjusted_prices()


def _load(now: datetime) -> dict[tuple[int, int], ObservedTax]:
    job_owners = access.shared_owners("industry_jobs")
    wallet_owners = access.shared_owners("wallet")
    rows = db.industry_job_tax_rows(
        job_owners, wallet_owners, now - timedelta(days=OBSERVATION_WINDOW_DAYS), OBSERVED_ACTIVITIES,
    )
    if not rows:
        return {}  # nothing to price: no ESI call
    try:
        adjusted = _adjusted_prices()
    except Exception as e:  # noqa: BLE001 - best effort; the Settings rate applies
        logger.warning("Observed facility tax left out: ESI adjusted prices unavailable (%s)", e)
        return {}
    return observe(rows, adjusted)


_CACHE_TTL_SECONDS = 60.0
_cache: dict[Optional[str], tuple[float, dict[tuple[int, int], ObservedTax]]] = {}
_cache_lock = threading.Lock()


def invalidate(all_tenants: bool = False) -> None:
    with _cache_lock:
        if all_tenants:
            _cache.clear()
        else:
            _cache.pop(storage.get_current_tenant(), None)


def observed_rates() -> dict[tuple[int, int], ObservedTax]:
    """{(facility_id, activity_id): ObservedTax} for the current tenant.
    Empty without a tenant (pure unit tests)."""
    tenant_id = storage.get_current_tenant()
    if tenant_id is None:
        return {}
    now = time.time()
    with _cache_lock:
        cached = _cache.get(tenant_id)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    try:
        result = _load(datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001 - best effort: the Settings rate applies; not cached, retried next call
        logger.warning("Observed facility tax left out: archive read failed", exc_info=True)
        return {}
    with _cache_lock:
        _cache[tenant_id] = (now, result)
    return result


def observed_for_location(location_id: Optional[int], activity_id: int) -> Optional[ObservedTax]:
    """The observation for one structure (an Office or container resolves to
    the structure it sits in, like the invention system lookup)."""
    if location_id is None:
        return None
    rates = observed_rates()
    if not rates:
        return None
    hit = rates.get((int(location_id), activity_id))
    if hit is not None:
        return hit
    try:
        from ..locations import reader as locations_reader
        described = locations_reader.describe([int(location_id)]).get(int(location_id))
    except Exception:  # noqa: BLE001 - no parent known, no observation
        return None
    if described is None or described.parent_id is None:
        return None
    return rates.get((int(described.parent_id), activity_id))
