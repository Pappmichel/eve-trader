"""Market fees from character skills and standings (pure, no I/O).

The fee settings (Trading's buy broker fee, Station Trading's broker fee and
sales tax, PI's broker fee and sales tax) are manual values. This module
computes what one character's skills and standings imply, so a Settings page
can show it next to the field with a "Use this value" button. Nothing here
changes a setting: applying is the user's click through the normal save.

Formulas (EVE University wiki, "Trading", checked 2026-11-17):

- Sales tax = 7.5 % x (1 - 0.11 x Accounting level), paid to the SCC:
  7.5 / 6.675 / 5.85 / 5.025 / 4.2 / 3.375 % for Accounting 0-5.
- NPC station broker fee = 3 % - 0.3 % x Broker Relations level
  - 0.03 % x faction standing - 0.02 % x corporation standing, with the
  *unmodified* (raw) standings towards the station owner's faction and
  corporation. The wiki states no percentage floor (only a 100 ISK minimum
  per order); a negative result is clamped to 0. Advanced Broker Relations
  only lowers the relist (order modification) fee, never this base.
- In a player-owned (Upwell) structure the owner sets the broker fee (plus
  an SCC surcharge); skills and standings do not change it, so only the
  sales tax comes from skills there.
"""
from __future__ import annotations

from typing import Iterable, Optional

SKILL_ACCOUNTING = 16622
SKILL_BROKER_RELATIONS = 3446
# Lowers only the relist fee; listed so nobody adds it to the base by mistake.
SKILL_ADVANCED_BROKER_RELATIONS = 16597

BASE_SALES_TAX = 0.075
SALES_TAX_REDUCTION_PER_LEVEL = 0.11

BASE_BROKER_FEE = 0.03
BROKER_RELATIONS_PER_LEVEL = 0.003
FACTION_STANDING_PER_POINT = 0.0003
CORPORATION_STANDING_PER_POINT = 0.0002


def _level(value) -> int:
    return max(0, min(5, int(value or 0)))


def sales_tax_rate(accounting_level: int) -> float:
    """Sales tax as a fraction (0.075 at Accounting 0)."""
    return BASE_SALES_TAX * (1.0 - SALES_TAX_REDUCTION_PER_LEVEL * _level(accounting_level))


def npc_broker_fee_rate(
    broker_relations_level: int,
    faction_standing: float = 0.0,
    corporation_standing: float = 0.0,
) -> float:
    """Broker fee at an NPC station as a fraction. Standings are the raw
    standings towards the station owner's faction and corporation."""
    rate = (
        BASE_BROKER_FEE
        - BROKER_RELATIONS_PER_LEVEL * _level(broker_relations_level)
        - FACTION_STANDING_PER_POINT * float(faction_standing or 0.0)
        - CORPORATION_STANDING_PER_POINT * float(corporation_standing or 0.0)
    )
    return max(0.0, rate)


def skill_levels(rows: Iterable[dict]) -> dict[int, int]:
    """Per-skill rows (`read_esi("skills", ..., table="skills")`) ->
    {skill_id: active level}. A skill without a row is level 0."""
    return {int(r["skill_id"]): int(r.get("active_level") or 0) for r in rows if r.get("skill_id") is not None}


def standing_from(rows: Iterable[dict], from_type: str, from_id: Optional[int]) -> float:
    """The raw standing a character holds from one NPC entity; none is 0."""
    if from_id is None:
        return 0.0
    for r in rows:
        if r.get("from_type") == from_type and int(r.get("from_id") or 0) == int(from_id):
            return float(r.get("standing") or 0.0)
    return 0.0
