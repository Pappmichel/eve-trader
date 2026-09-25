"""Read-only views over the ESI-synced industry job cache (see esi_sync.py):
a flat list of currently active jobs, and a per-character job-slot overview
derived from skills (see constants.py job_slots_from_skills)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .. import storage
from . import pricing
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import ACTIVITY_JOB_LABELS, ACTIVITY_SLOT_CATEGORY, SLOT_CATEGORY_LABELS
from .models import CharacterSlotRow, IndustryJobRow


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _shared_job_owner_ids() -> tuple[list[int], list[int]]:
    """(shared character ids, shared corporation ids) - docs/ESI_ACCESS_PLAN.md
    Known gap 3. Lazy import: `.engine` imports `character_slot_overview`
    from this module, so a module-level import back here would be
    circular - this module calls `esi_data.access.shared_owner_ids`
    directly instead of engine.py's cached `shared_production_owner_ids`,
    since both callers below call this once per function, not per type_id
    inside a loop, so there's no repeated-query cost to cache away."""
    from ..esi_data.access import shared_owner_ids
    return (
        shared_owner_ids("industry_jobs", "production", "character"),
        shared_owner_ids("industry_jobs", "production", "corporation"),
    )


def list_current_jobs(cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[IndustryJobRow]:
    """Every active character + corp industry job, one row per job (not
    aggregated by item), sorted by soonest-completing first.

    output_value is quantity x unit price, priced the same way stock_value
    prices owned stock (C-J sell quote, falling back to Jita sell quote) -
    None if the job has no product (research/copying jobs - quantity is
    already None for those, see IndustryJobRow's own docstring) or if
    neither market has a sell quote for it, so a temporary data gap shows as
    "no value" rather than silently as 0."""
    char_ids, corp_ids = _shared_job_owner_ids()
    jobs = storage.list_industry_jobs(owner_character_ids=char_ids, owner_corporation_ids=corp_ids)
    manual_jobs = storage.load_manual_industry_jobs()
    # Only the distinct products these jobs actually output need pricing -
    # see pricing.home_prices/jita_prices' own docstrings for why callers
    # must scope type_ids explicitly now.
    product_type_ids = list({j[3] for j in jobs if j[3] is not None} | {m[1] for m in manual_jobs})
    home = pricing.home_prices(cfg, product_type_ids)
    jita = pricing.jita_prices(product_type_ids)

    now = datetime.now(timezone.utc)
    rows = []
    for (job_id, activity_id, blueprint_type_id, product_type_id, type_name, runs,
         _output_location_id, status, end_date, start_date, installer_name) in jobs:
        quantity = None
        if product_type_id is not None:
            qty_per_run = storage.get_product_quantity(blueprint_type_id, activity_id, product_type_id)
            if qty_per_run is not None:
                quantity = qty_per_run * runs
        output_value = None
        if quantity is not None and product_type_id is not None:
            home_quote = home.get(product_type_id)
            jita_quote = jita.get(product_type_id)
            if home_quote and home_quote.sell > 0:
                output_value = quantity * home_quote.sell
            elif jita_quote and jita_quote.sell > 0:
                output_value = quantity * jita_quote.sell
        end_dt = _parse_iso(end_date)
        remaining = (end_dt - now).total_seconds() if end_dt else None
        rows.append(IndustryJobRow(
            job_id=job_id,
            type_name=type_name or (str(product_type_id) if product_type_id else "?"),
            activity=ACTIVITY_JOB_LABELS.get(activity_id, str(activity_id)),
            runs=runs,
            quantity=quantity,
            output_value=output_value,
            status=status,
            start_date=start_date,
            end_date=end_date,
            remaining_seconds=remaining,
            installer_name=installer_name or "?",
        ))

    # Manual jobs (docs/MANUAL_TRACKING_PLAN.md phase 6) - each its own row,
    # `status` derived from ready_at instead of ESI's own job status field.
    for manual_id, product_type_id, product_name, activity_id, quantity, runs, _location_id, ready_at in manual_jobs:
        output_value = None
        home_quote = home.get(product_type_id)
        jita_quote = jita.get(product_type_id)
        if home_quote and home_quote.sell > 0:
            output_value = quantity * home_quote.sell
        elif jita_quote and jita_quote.sell > 0:
            output_value = quantity * jita_quote.sell
        ready_dt = _parse_iso(ready_at) if isinstance(ready_at, str) else ready_at
        remaining = (ready_dt - now).total_seconds() if ready_dt else None
        rows.append(IndustryJobRow(
            job_id=manual_id,
            type_name=product_name,
            activity=ACTIVITY_JOB_LABELS.get(activity_id, str(activity_id)),
            runs=runs or 0,
            quantity=quantity,
            output_value=output_value,
            status="ready" if ready_dt and ready_dt <= now else "active",
            start_date=None,
            end_date=ready_at if isinstance(ready_at, str) else (ready_at.isoformat() if ready_at else None),
            remaining_seconds=remaining if remaining is None or remaining > 0 else 0.0,
            installer_name="Manual",
            source="manual",
            manual_id=manual_id,
        ))

    rows.sort(key=lambda r: r.remaining_seconds if r.remaining_seconds is not None else float("inf"))
    return rows


def character_slot_overview() -> list[CharacterSlotRow]:
    """Total/used/free industry job slots per registered producer character,
    split by slot category (Manufacturing/Reactions/Science - each governed
    by its own skills, real EVE mechanic). Used = active jobs installed by
    that character across both personal and corp jobs (corp jobs still draw
    on the installing character's own slots)."""
    used: dict[tuple[str, str], int] = {}
    char_ids, corp_ids = _shared_job_owner_ids()
    for (_job_id, activity_id, _bp, _product, _name, _runs, _loc,
         status, _end, _start, installer_name) in storage.list_industry_jobs(
        owner_character_ids=char_ids, owner_corporation_ids=corp_ids,
    ):
        if status not in ("active", "paused", "ready"):
            continue
        category = ACTIVITY_SLOT_CATEGORY.get(activity_id)
        if category is None or not installer_name:
            continue
        key = (installer_name, category)
        used[key] = used.get(key, 0) + 1

    rows = []
    for character_name, manufacturing_slots, reaction_slots, science_slots, excluded in storage.load_character_slots():
        for category, total in (
            ("manufacturing", manufacturing_slots),
            ("reaction", reaction_slots),
            ("science", science_slots),
        ):
            used_count = used.get((character_name, category), 0)
            rows.append(CharacterSlotRow(
                character_name=character_name,
                job_type=SLOT_CATEGORY_LABELS[category],
                total_slots=total,
                used_slots=used_count,
                free_slots=max(0, total - used_count),
                excluded_from_planning=excluded,
            ))
    return rows
