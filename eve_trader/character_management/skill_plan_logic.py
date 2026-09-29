"""Pure skill-plan logic (docs/CHARACTER_MANAGEMENT_PLAN.md phase 9): the plan
text format, prerequisite expansion, pruning, order validation and per-character
progress. The only storage it touches is the SDE skill-requirement lookup,
passed in as `requirements` so tests need no database.

A plan is an ordered list of steps `(skill_id, level)`. It is *self-contained*:
a step at level L > 1 needs `(skill, L-1)` earlier in the plan, and a step at
level 1 needs each of the skill's own prerequisites `(prereq_skill, level)`
earlier. (In EVE a skill's prerequisites gate training it at all, i.e. its
first level; the later levels only need the level below.)
"""
from __future__ import annotations

import math
import re
from typing import Callable, Iterable, Optional

Step = tuple[int, int]
Requirements = Callable[[int], list[Step]]     # skill_id -> [(prereq_skill_id, level), ...]

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_ROMAN_BY_LEVEL = {v: k for k, v in _ROMAN.items()}
_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<level>[1-5]|I{1,3}|IV|V)$", re.IGNORECASE)

MAX_STEPS = 600          # a plan of every skill at every level is ~ 500 steps


def level_label(level: int) -> str:
    return _ROMAN_BY_LEVEL.get(level, str(level))


def parse_plan_text(text: str) -> tuple[list[tuple[str, int]], list[str]]:
    """`(entries, unparsed)`: `entries` are `(skill name, level)` in text order
    (level as digit 1-5 or roman I-V, case-insensitive); `unparsed` are the
    non-empty, non-comment lines that did not look like `<name> <level>`."""
    entries: list[tuple[str, int]] = []
    unparsed: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            unparsed.append(line[:120])
            continue
        token = match.group("level").upper()
        entries.append((match.group("name").strip(), int(token) if token.isdigit() else _ROMAN[token]))
    return entries, unparsed


def format_plan_text(steps: Iterable[Step], names: dict[int, str]) -> str:
    """One `<name> <roman level>` line per step, in plan order."""
    return "\n".join(f"{names.get(skill_id, f'Skill {skill_id}')} {level_label(level)}" for skill_id, level in steps)


def requirements_of(step: Step, requirements: Requirements) -> list[Step]:
    skill_id, level = step
    if level > 1:
        return [(skill_id, level - 1)]
    return [tuple(r) for r in requirements(skill_id)]      # type: ignore[misc]


def add_with_prerequisites(
    plan: list[Step], additions: Iterable[Step], requirements: Requirements,
) -> tuple[list[Step], int]:
    """`plan` with each addition appended after everything it needs (which is
    appended too if missing), and how many steps were added in total. Steps
    already in the plan keep their position. A prerequisite cycle in the data
    is cut rather than looped on."""
    result = list(plan)
    present = set(result)
    visiting: set[Step] = set()

    def add(step: Step) -> None:
        if step in present or step in visiting:
            return
        visiting.add(step)
        for needed in requirements_of(step, requirements):
            add(needed)
        visiting.discard(step)
        if step not in present:
            present.add(step)
            result.append(step)

    for step in additions:
        add(step)
    return result, len(result) - len(plan)


def prune_after_removal(plan: list[Step], removed: Step, requirements: Requirements) -> list[Step]:
    """`plan` without `removed` and without every step that (transitively) needed
    it, so the plan stays self-contained."""
    remaining = [s for s in plan if s != removed]
    while True:
        present = set(remaining)
        kept = [s for s in remaining if all(n in present for n in requirements_of(s, requirements))]
        if len(kept) == len(remaining):
            return kept
        remaining = kept


def is_valid_order(plan: list[Step], requirements: Requirements) -> bool:
    """Every step comes after each step it needs (and the needed steps exist)."""
    seen: set[Step] = set()
    for step in plan:
        if not all(n in seen for n in requirements_of(step, requirements)):
            return False
        seen.add(step)
    return True


def step_sp_remaining(rank: Optional[float], level: int, trained_level: int, sp_in_skill: int) -> Optional[int]:
    """SP still to train for this step for a character with `trained_level`
    and `sp_in_skill` in the skill: 0 if already at/above the level, the whole
    step (SP(level) - SP(level-1)) if lower levels are still missing, and only
    the rest of the level if the character is part-way through the level below.
    None when the rank is unknown."""
    if trained_level >= level:
        return 0
    if rank is None:
        return None
    base = _sp(rank, level - 1)
    have = max(base, sp_in_skill) if trained_level == level - 1 else base
    return max(0, _sp(rank, level) - have)


def _sp(rank: float, level: int) -> int:
    return 0 if level <= 0 else math.ceil(250 * rank * 32 ** ((level - 1) / 2))
