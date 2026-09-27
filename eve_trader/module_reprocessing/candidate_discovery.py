"""Builds the Module Reprocessing Import tool's candidate universe.

Unlike Ore & Minerals' own fixed, tiny compressed-ore/ice universe (~80
types, every one added to the shortlist automatically - see refining/
candidate_discovery.py), the T1/Meta module+drone universe is large
(thousands of published types). This module only does the cheap part - one
SDE query (storage.module_reprocessing_candidate_types, already filtered to
real category_id/meta_group_id/rig-slot fields, see that function's own
docstring) - never a per-candidate network call. The Discover pipeline job
(module_reprocessing/actions.py's do_discover_candidates, run via
pipeline_runner since a Goonmetrics full-market-dump download alone
measures ~30s - see goonmetrics_client.py's current_prices docstring) is
what actually prices this universe, using one bulk current-price dump per
market rather than one ESI call per candidate.
"""
from __future__ import annotations

from .. import storage
from .models import ModuleCandidate


def build_module_candidate_universe() -> list[ModuleCandidate]:
    """Every published T1/Meta module or drone type - see storage.
    module_reprocessing_candidate_types's own docstring for the exact
    SDE-driven inclusion/exclusion rules (category, meta-group, rig-slot)."""
    rows = storage.module_reprocessing_candidate_types()
    return [
        ModuleCandidate(type_id=type_id, item=type_name, volume_m3=volume)
        for type_id, type_name, volume in rows
        if type_name and volume
    ]
