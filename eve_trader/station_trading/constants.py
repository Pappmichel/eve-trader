"""Static EVE-mechanic tables for the Station Trading tool - real skill
type_ids and the order-slot-count formula, not heuristics (see CLAUDE.md's
"Real SDE data drives classification" section).

Confirmed directly against the live sde_types cache (2026-08-27), not
trusted from memory or the initial web research alone - that research also
named a "Margin Trading" skill (buy-order escrow reduction), which does
not exist anywhere in the SDE's full "Trade Skills" group (26 real
members checked) and has evidently been removed from the game at some
point - dropped entirely here, along with any escrow-reduction modeling.
Several of the remaining IDs also differ from what memory alone would have
guessed (e.g. Tycoon is 18580, not 16598 - that's actually Marketing).
"""
from __future__ import annotations

SKILL_TRADE = 3443
SKILL_RETAIL = 3444
SKILL_WHOLESALE = 16596
SKILL_TYCOON = 18580
SKILL_ACCOUNTING = 16622
SKILL_BROKER_RELATIONS = 3446
SKILL_ADVANCED_BROKER_RELATIONS = 16597

# Every skill type_id worth pulling a level for, name-labeled in the order
# the Settings page's Skills panel should show them.
SKILL_LABELS: dict[int, str] = {
    SKILL_TRADE: "Trade",
    SKILL_RETAIL: "Retail",
    SKILL_WHOLESALE: "Wholesale",
    SKILL_TYCOON: "Tycoon",
    SKILL_ACCOUNTING: "Accounting",
    SKILL_BROKER_RELATIONS: "Broker Relations",
    SKILL_ADVANCED_BROKER_RELATIONS: "Advanced Broker Relations",
}

# Base 5 concurrent orders + 4/level Trade + 8/level Retail + 16/level
# Wholesale + 32/level Tycoon - the well-established, stable EVE order-slot
# mechanic (unlike the fee/tax discount skills below, this one has no
# standings component, so it's safe to model exactly).
_BASE_ORDER_SLOTS = 5


def order_slots_from_skills(skill_levels: dict[int, int]) -> int:
    """skill_levels: {skill_type_id: active_skill_level}. Returns the total
    number of concurrent market orders the pulled skill levels allow."""
    return (_BASE_ORDER_SLOTS
            + 4 * skill_levels.get(SKILL_TRADE, 0)
            + 8 * skill_levels.get(SKILL_RETAIL, 0)
            + 16 * skill_levels.get(SKILL_WHOLESALE, 0)
            + 32 * skill_levels.get(SKILL_TYCOON, 0))
