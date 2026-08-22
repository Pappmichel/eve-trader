"""Tests for eve_trader/refining/paste_parser.py - GitHub issue #92. Real
EVE "Copy As" clipboard format confirmed against evepraisal.com's own
open-source evepaste library (evepaste/parsers/assets.py) during
implementation - 9 tab-separated columns: Name, Quantity, Group, Category,
Size, Slot, Volume, Meta Level, Tech Level, no header row."""
from eve_trader.refining.paste_parser import merge_duplicate_stacks, parse_paste

# A real Tech I ammo line - Size/Slot/Meta Level/Tech Level empty (common for
# non-fitted items), still real trailing tab characters in an actual paste.
_AMMO_LINE = "Antimatter Charge S\t1000\tProjectile Ammo\tCharge\t\t\t5.0 m3\t\t"
_MODULE_LINE = "Damage Control II\t3\tDamage Controls\tModule\tSmall\tLow\t5.0 m3\t5\t2"


def test_parses_a_full_9_column_line():
    [line] = parse_paste(_AMMO_LINE)
    assert line.name == "Antimatter Charge S"
    assert line.quantity == 1000
    assert line.category == "Charge"
    assert line.volume_m3 == 5.0
    assert line.error is None


def test_parses_quantity_with_thousands_separator():
    [line] = parse_paste("Tritanium\t1,234,567\tMineral\tMaterial\t\t\t0.01 m3\t\t")
    assert line.quantity == 1234567


def test_parses_meta_and_tech_level_columns_present():
    [line] = parse_paste(_MODULE_LINE)
    assert line.name == "Damage Control II"
    assert line.quantity == 3
    assert line.category == "Module"
    assert line.volume_m3 == 5.0


def test_skips_blank_lines():
    lines = parse_paste(f"{_AMMO_LINE}\n\n\n{_MODULE_LINE}\n")
    assert len(lines) == 2


def test_flags_non_tab_separated_line_as_error_not_dropped():
    lines = parse_paste("just some free text, not a real paste")
    assert len(lines) == 1
    assert lines[0].error is not None


def test_flags_line_missing_quantity_as_error():
    lines = parse_paste("Tritanium\t\tMineral\tMaterial\t\t\t0.01 m3\t\t")
    assert len(lines) == 1
    assert lines[0].error is not None


def test_merge_duplicate_stacks_sums_quantities_case_insensitively():
    text = "Antimatter Charge S\t500\tProjectile Ammo\tCharge\t\t\t5.0 m3\t\t\n" \
           "antimatter charge s\t500\tProjectile Ammo\tCharge\t\t\t5.0 m3\t\t"
    merged = merge_duplicate_stacks(parse_paste(text))
    assert len(merged) == 1
    assert merged[0].quantity == 1000


def test_merge_duplicate_stacks_keeps_distinct_items_separate():
    merged = merge_duplicate_stacks(parse_paste(f"{_AMMO_LINE}\n{_MODULE_LINE}"))
    assert len(merged) == 2


def test_merge_duplicate_stacks_excludes_error_lines():
    merged = merge_duplicate_stacks(parse_paste(f"{_AMMO_LINE}\nnot a real line"))
    assert len(merged) == 1
    assert merged[0].name == "Antimatter Charge S"
