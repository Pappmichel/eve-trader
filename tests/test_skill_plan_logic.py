"""Pure skill-plan logic (no database): text format, prerequisite expansion,
pruning, order validation, per-step SP."""
from __future__ import annotations

import pytest

from eve_trader.character_management import skill_plan_logic as logic

# skill ids: 1 Gunnery, 2 Small Projectile (needs Gunnery 2), 3 Rapid Firing (needs Gunnery 3, Small Proj 1)
REQ = {1: [], 2: [(1, 2)], 3: [(1, 3), (2, 1)]}


def req(skill_id):
    return REQ.get(skill_id, [])


def test_parse_accepts_digits_and_roman_numerals_and_reports_the_rest():
    entries, bad = logic.parse_plan_text(
        "Gunnery V\n  small projectile turret 3  \n# a comment\n\nRapid Firing iv\nNo level here\nGunnery 6\n")
    assert entries == [("Gunnery", 5), ("small projectile turret", 3), ("Rapid Firing", 4)]
    assert bad == ["No level here", "Gunnery 6"]
    assert logic.parse_plan_text("") == ([], []) and logic.parse_plan_text(None) == ([], [])       # type: ignore[arg-type]


def test_format_round_trips_through_parse():
    steps = [(1, 1), (1, 2), (2, 1)]
    text = logic.format_plan_text(steps, {1: "Gunnery", 2: "Small Projectile Turret"})
    assert text == "Gunnery I\nGunnery II\nSmall Projectile Turret I"
    assert logic.parse_plan_text(text)[0] == [("Gunnery", 1), ("Gunnery", 2), ("Small Projectile Turret", 1)]
    assert logic.format_plan_text([(9, 3)], {}) == "Skill 9 III"


def test_adding_a_skill_inserts_lower_levels_and_prerequisites_first():
    plan, added = logic.add_with_prerequisites([], [(3, 2)], req)
    assert plan == [(1, 1), (1, 2), (1, 3), (2, 1), (3, 1), (3, 2)]        # Gunnery to III, Small Proj I, then Rapid Firing
    assert added == 6 and logic.is_valid_order(plan, req)


def test_adding_keeps_existing_positions_and_only_appends_what_is_missing():
    start = [(1, 1), (1, 2)]
    plan, added = logic.add_with_prerequisites(start, [(2, 1), (1, 2)], req)
    assert plan == [(1, 1), (1, 2), (2, 1)] and added == 1
    assert logic.add_with_prerequisites(plan, [(2, 1)], req) == (plan, 0)      # idempotent


def test_a_prerequisite_cycle_is_cut_not_looped():
    cyclic = {1: [(2, 1)], 2: [(1, 1)]}
    plan, _ = logic.add_with_prerequisites([], [(1, 1)], lambda s: cyclic.get(s, []))
    assert sorted(plan) == [(1, 1), (2, 1)]


def test_removing_a_step_removes_everything_that_needed_it():
    plan, _ = logic.add_with_prerequisites([], [(3, 1)], req)
    assert plan == [(1, 1), (1, 2), (1, 3), (2, 1), (3, 1)]
    assert logic.prune_after_removal(plan, (1, 3), req) == [(1, 1), (1, 2), (2, 1)]      # Rapid Firing needed Gunnery III
    assert logic.prune_after_removal(plan, (2, 1), req) == [(1, 1), (1, 2), (1, 3)]      # ... and Small Projectile I
    assert logic.prune_after_removal(plan, (3, 1), req) == [(1, 1), (1, 2), (1, 3), (2, 1)]
    assert logic.prune_after_removal(plan, (1, 1), req) == []                            # the root takes everything with it


def test_order_validation():
    ok = [(1, 1), (1, 2), (2, 1)]
    assert logic.is_valid_order(ok, req)
    assert not logic.is_valid_order([(1, 2), (1, 1), (2, 1)], req)                       # level II before level I
    assert not logic.is_valid_order([(2, 1), (1, 1), (1, 2)], req)                       # needs Gunnery II first
    assert not logic.is_valid_order([(1, 2)], req)                                       # a needed step is missing altogether
    assert logic.is_valid_order([], req)


@pytest.mark.parametrize("rank,level,trained,sp_in,expected", [
    (1.0, 3, 3, 8000, 0),                        # already there
    (1.0, 3, 5, 0, 0),                           # above
    (1.0, 1, 0, 0, 250),
    (2.0, 3, 0, 0, 16000 - 2829),                # whole step: SP(3) - SP(2), rank 2 (2829 = ceil(500*32**.5))
    (1.0, 3, 2, 3000, 8000 - 3000),              # part-way through the level below: the rest only
    (1.0, 3, 2, 100, 8000 - 1415),               # SP below the level's start counts as the start
    (None, 3, 0, 0, None),                       # unknown rank
])
def test_step_sp_remaining(rank, level, trained, sp_in, expected):
    assert logic.step_sp_remaining(rank, level, trained, sp_in) == expected
