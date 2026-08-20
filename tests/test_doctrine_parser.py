import pytest

from eve_trader.doctrine.parser import FittingParseError, ResolvedType, parse_bay_items, parse_fitting

# Fake SDE: name (lowercase) -> ResolvedType. Category IDs match
# doctrine/constants.py: 6=Ship, 7=Module (arbitrary "not special" stand-in
# here), 8=Charge, 18=Drone.
_SHIP = 6
_MODULE = 7
_CHARGE = 8
_DRONE = 18

_TYPES = {
    "rifter": ResolvedType(1, 100, _SHIP, None, None, "Rifter"),
    "damage control ii": ResolvedType(2, 200, _MODULE, 2, 5, "Damage Control II"),
    "200mm autocannon ii": ResolvedType(3, 300, _MODULE, 2, 5, "200mm AutoCannon II"),
    "antimatter charge s": ResolvedType(4, 400, _CHARGE, None, None, "Antimatter Charge S"),
    "nanofiber internal structure ii": ResolvedType(5, 500, _MODULE, 2, 5, "Nanofiber Internal Structure II"),
    "small ancillary current router i": ResolvedType(6, 600, _MODULE, None, None, "Small Ancillary Current Router I"),
    "warrior ii": ResolvedType(7, 700, _DRONE, 2, 5, "Warrior II"),
    "tritanium": ResolvedType(8, 800, 4, None, None, "Tritanium"),  # category 4 = Material, no slot
}
_SLOTS = {2: "low", 3: "high", 5: "low", 6: "rig"}


def _resolve_name(name: str):
    return _TYPES.get(name.strip().lower())


def _resolve_slot(type_id: int):
    return _SLOTS.get(type_id)


def _parse(text, candidates=()):
    return parse_fitting(text, _resolve_name, _resolve_slot, hull_name_candidates=candidates)


# --------------------------------------------------------------- hard failures
def test_missing_header_is_hard_error():
    with pytest.raises(FittingParseError):
        _parse("just some text\nno header here\n")


def test_header_without_comma_is_hard_error():
    with pytest.raises(FittingParseError):
        _parse("[Rifter]\nDamage Control II\n")


def test_unresolvable_hull_is_hard_error_with_suggestion():
    with pytest.raises(FittingParseError, match="Rifer"):
        _parse("[Rifer, Typo Fit]\n", candidates=["Rifter"])


def test_hull_wrong_category_is_hard_error():
    with pytest.raises(FittingParseError):
        _parse("[Tritanium, Not A Ship]\n")


# --------------------------------------------------------------- basic parsing
def test_hull_only_fitting_is_valid_with_no_items_or_issues():
    result = _parse("[Rifter, Empty Hull]\n")
    assert result.hull_type_id == 1
    assert result.items == []
    assert result.issues == []


def test_leading_blank_lines_before_header_are_tolerated():
    result = _parse("\n\n[Rifter, Padded]\nDamage Control II\n")
    assert result.hull_type_id == 1
    assert len(result.items) == 1


def test_module_classified_by_sde_slot():
    result = _parse("[Rifter, Fit]\nDamage Control II\n")
    assert len(result.items) == 1
    item = result.items[0]
    assert item.type_id == 2
    assert item.slot_section == "low"
    assert item.quantity == 1
    assert item.is_offline is False


def test_offline_suffix_sets_flag_and_still_resolves():
    result = _parse("[Rifter, Fit]\nDamage Control II /offline\n")
    assert result.items[0].is_offline is True
    assert result.items[0].type_id == 2


def test_offline_suffix_case_insensitive():
    result = _parse("[Rifter, Fit]\nDamage Control II /OFFLINE\n")
    assert result.items[0].is_offline is True


def test_empty_marker_produces_no_item_and_no_issue():
    result = _parse("[Rifter, Fit]\n[Empty Low slot]\nDamage Control II\n")
    assert len(result.items) == 1
    assert result.issues == []


# --------------------------------------------------------------- comma / charges
def test_module_charge_comma_line_produces_two_items():
    result = _parse("[Rifter, Fit]\n200mm AutoCannon II, Antimatter Charge S\n")
    assert len(result.items) == 2
    module, charge = result.items
    assert module.type_id == 3 and module.slot_section == "high"
    assert charge.type_id == 4 and charge.slot_section == "charge"
    assert result.issues == []


def test_bare_charge_line_without_module_gets_charge_section_and_issue():
    result = _parse("[Rifter, Fit]\nAntimatter Charge S\n")
    assert len(result.items) == 1
    assert result.items[0].slot_section == "charge"
    assert result.items[0].type_id == 4
    assert len(result.issues) == 1
    assert result.issues[0].issue_kind == "unknown_section"


def test_ambiguous_comma_split_produces_ambiguous_split_issue_no_item():
    # Both "Damage Control II" and "Nanofiber Internal Structure II" resolve,
    # but the right-hand side isn't a Charge - genuinely ambiguous per A.5.
    result = _parse("[Rifter, Fit]\nDamage Control II, Nanofiber Internal Structure II\n")
    assert result.items == []
    assert len(result.issues) == 1
    assert result.issues[0].issue_kind == "ambiguous_split"


# --------------------------------------------------------------- quantities
def test_drone_with_quantity_suffix():
    result = _parse("[Rifter, Fit]\nWarrior II x5\n")
    assert len(result.items) == 1
    item = result.items[0]
    assert item.type_id == 7 and item.slot_section == "drone" and item.quantity == 5


def test_cargo_item_with_quantity_suffix_ignores_its_own_slot():
    # Antimatter Charge S has no slot of its own (category Charge), so this
    # is already unambiguously cargo - confirms the qty-suffix path.
    result = _parse("[Rifter, Fit]\nAntimatter Charge S x50\n")
    assert len(result.items) == 1
    assert result.items[0].slot_section == "cargo"
    assert result.items[0].quantity == 50


def test_fitted_module_type_with_quantity_suffix_forces_cargo_with_issue():
    # A real slot-fitted type (Damage Control II, slot "low") carrying an
    # xN suffix is never fitted - Phase 3 A.4 point 2: qty-suffix always
    # wins over the slot, and it's surprising enough to flag.
    result = _parse("[Rifter, Fit]\nDamage Control II x3\n")
    assert len(result.items) == 1
    item = result.items[0]
    assert item.slot_section == "cargo"
    assert item.quantity == 3
    assert any(i.issue_kind == "unknown_section" for i in result.issues)


def test_unknown_type_with_quantity_suffix_is_cargo_not_drone():
    result = _parse("[Rifter, Fit]\nTritanium x1000\n")
    assert len(result.items) == 1
    assert result.items[0].slot_section == "cargo"
    assert result.items[0].quantity == 1000


# --------------------------------------------------------------- resolution failures
def test_unresolved_module_name_is_soft_issue_not_hard_error():
    result = _parse("[Rifter, Fit]\nSome Totally Fake Module\n")
    assert result.items == []
    assert len(result.issues) == 1
    assert result.issues[0].issue_kind == "unresolved_name"


def test_unresolved_name_mentions_abyssal_when_no_suggestion_found():
    result = _parse("[Rifter, Fit]\nCompletely Unknown Thing With No Similar Name\n")
    assert "Abyssal" in result.issues[0].message


# --------------------------------------------------------------- normalization
def test_crlf_normalization_preserves_line_numbering():
    text = "[Rifter, Fit]\r\nDamage Control II\r\n200mm AutoCannon II, Antimatter Charge S\r\n"
    result = _parse(text)
    line_nos = [i.line_no for i in result.items]
    assert line_nos == [2, 3, 3]  # module+charge share line 3


def test_case_insensitive_and_whitespace_tolerant_name_resolution():
    result = _parse("[rifter, Fit]\n  damage    control   ii  \n")
    assert result.hull_type_id == 1
    assert result.items[0].type_id == 2


# --------------------------------------------------------------- marker-position gegenprobe
def test_marker_export_position_conflict_emits_unknown_section_issue():
    # Damage Control II (real slot "low") placed under a High-slot marker
    # block - SDE still wins (item classified "low"), but the export claims
    # position via markers, so the mismatch is flagged.
    text = (
        "[Rifter, Fit]\n"
        "[Empty Low slot]\n\n"
        "[Empty Med slot]\n\n"
        "Damage Control II\n"
    )
    result = _parse(text)
    assert len(result.items) == 1
    assert result.items[0].slot_section == "low"
    assert any(i.issue_kind == "unknown_section" for i in result.issues)


def test_no_markers_no_position_conflict_issue_even_when_reordered():
    # Same reordering, but no markers anywhere - the normal in-game-export
    # case, must stay silent (A.4: gegenprobe only fires with markers).
    text = "[Rifter, Fit]\n\n\nDamage Control II\n"
    result = _parse(text)
    assert len(result.items) == 1
    assert result.issues == []


# --------------------------------------------------- parse_bay_items (GitHub issue #18)
def test_parse_bay_items_plain_name_defaults_to_qty_one():
    items, issues = parse_bay_items("Tritanium", _resolve_name, "fuelbay")
    assert issues == []
    assert len(items) == 1
    assert items[0].type_id == 8
    assert items[0].quantity == 1.0
    assert items[0].slot_section == "fuelbay"


def test_parse_bay_items_qty_suffix_sets_quantity():
    items, issues = parse_bay_items("Tritanium x50", _resolve_name, "fuelbay")
    assert issues == []
    assert items[0].quantity == 50.0


def test_parse_bay_items_every_resolved_line_uses_the_passed_section():
    # Same list of resolved names, different slot_section per call - proves
    # the caller's `slot_section` wins over anything the item's own SDE
    # category/slot would normally classify it as (no slot classification
    # happens here at all, unlike parse_fitting's own _classify_section).
    items, _issues = parse_bay_items("Warrior II\nRifter", _resolve_name, "shipmaintenancebay")
    assert all(i.slot_section == "shipmaintenancebay" for i in items)
    assert [i.type_id for i in items] == [7, 1]


def test_parse_bay_items_unresolved_name_becomes_issue_not_item():
    items, issues = parse_bay_items("Some Unknown Thing", _resolve_name, "fuelbay")
    assert items == []
    assert len(issues) == 1
    assert issues[0].issue_kind == "unresolved_name"


def test_parse_bay_items_blank_lines_are_skipped():
    items, issues = parse_bay_items("Tritanium\n\n\nWarrior II\n", _resolve_name, "fuelbay")
    assert len(items) == 2
    assert issues == []


def test_parse_bay_items_empty_text_returns_nothing():
    items, issues = parse_bay_items("", _resolve_name, "fuelbay")
    assert items == []
    assert issues == []


def test_parse_bay_items_line_no_starts_from_given_offset():
    items, _issues = parse_bay_items("Tritanium\nWarrior II", _resolve_name, "fuelbay", line_no_start=42)
    assert [i.line_no for i in items] == [42, 43]


def test_parse_bay_items_ignores_offline_and_comma_charge_pairing():
    # Bay contents never carry an /offline suffix or a comma-paired charge
    # the way a fitted module can (GitHub #18's own scope: plain item lists
    # only) - a comma-containing line simply fails to resolve as a whole
    # name, becoming an unresolved-name issue rather than being split.
    items, issues = parse_bay_items("Damage Control II /offline", _resolve_name, "fuelbay")
    assert items == []
    assert issues[0].issue_kind == "unresolved_name"
