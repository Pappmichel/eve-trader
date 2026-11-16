"""Industry activity ids shared by the ESI snapshot fetchers and the realized
ledger (no tool package, so both may import it).

Confirmed CCP data mismatch: the live ESI industry-jobs endpoints report
Reactions as activity_id 9, while the SDE files every Reaction recipe under
11 (`production.constants.ACTIVITY_REACTION`). Jobs are normalized once with
`normalize_activity_id` wherever a live id meets SDE-keyed data.
"""
from __future__ import annotations

LIVE_REACTION_ACTIVITY_ID = 9
SDE_REACTION_ACTIVITY_ID = 11


def normalize_activity_id(activity_id: int) -> int:
    """The SDE activity id for a live ESI job activity id (9 -> 11; every
    other id is returned unchanged)."""
    activity_id = int(activity_id)
    return SDE_REACTION_ACTIVITY_ID if activity_id == LIVE_REACTION_ACTIVITY_ID else activity_id
