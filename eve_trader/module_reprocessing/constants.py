"""Candidate-universe filter constants for the Module Reprocessing Import
tool. Reuses production/constants.py's real SDE category ids rather than
re-deriving them (that module is already this repo's single source of
truth for MODULE_CATEGORY_ID/DRONE_CATEGORY_ID - refining/actions.py
already imports across the same tool boundary for the same reason).

TECH_II_META_GROUP_ID (2) is the one meta-group value none of this repo's
existing tools have needed before now: production/engine.py's
classify_activity deliberately does NOT use meta_group_id to detect Tech
II (it uses invention-recipe presence instead - see that function's own
docstring for why metaLevel/meta_group heuristics misfired for Tech
III/Faction items). This tool has no such conflict: a module/drone that
survives every other exclusion below and would otherwise be treated as
"Tech I" for reprocessing purposes only needs a plain, correct Tech II tag,
which storage.get_sde_type's own docstring already documents as the real,
authoritative SDE value ("1=Tech I, 2=Tech II, 3=Storyline, 4=Faction,
5=Officer, 6=Deadspace") - the same source table eve_trader/production/
constants.py's own FACTION/STORYLINE/OFFICER/DEADSPACE values (3/4/5/6) are
drawn from.
"""
from __future__ import annotations

from ..production.constants import (
    DEADSPACE_META_GROUP_ID,
    DRONE_CATEGORY_ID,
    FACTION_META_GROUP_ID,
    MODULE_CATEGORY_ID,
    OFFICER_META_GROUP_ID,
    STORYLINE_META_GROUP_ID,
)

TECH_II_META_GROUP_ID = 2

CANDIDATE_CATEGORY_IDS = (MODULE_CATEGORY_ID, DRONE_CATEGORY_ID)

# Excluded from this tool's candidate universe entirely (confirmed with the
# user during planning): Tech II, Storyline, Faction, Officer, Deadspace are
# almost always worth more sold intact than scrapped. A plain Tech I item
# (meta_group_id NULL, or any value not in this set) passes through.
EXCLUDED_META_GROUP_IDS = frozenset({
    TECH_II_META_GROUP_ID, STORYLINE_META_GROUP_ID, FACTION_META_GROUP_ID,
    OFFICER_META_GROUP_ID, DEADSPACE_META_GROUP_ID,
})
