"""Hand-curated business constants for the Doctrine tool - same spirit as
production/constants.py's own docstring: small, curated values, not SDE
data (SDE-derived slot classification itself lives in storage.get_type_slot/
sde_type_slots, populated by production/sde.py's refresh_sde - this module
only names the *labels* that data uses, not the effect-ID -> slot mapping
CSV-parsing detail, which stays local to production/sde.py).

Deliberately no imports from eve_trader.production - the two packages'
constants (e.g. SHIP_CATEGORY_ID) happen to share a value but are kept as
separate, small, independently-duplicated constants rather than a
cross-package import, avoiding a doctrine<->production dependency edge that
doesn't otherwise need to exist (doctrine's only real dependency on
Production is storage.py's own character_assets/corp_assets tables, read
directly - see engine.py, not the production package itself).
"""
from __future__ import annotations

# ---------------------------------------------------------------- slot sections
# The nine EFT sections (Phase 3 spec A.2's grammar) - "low"/"med"/"high"/
# "rig"/"subsystem"/"service" match storage.get_type_slot's return values
# 1:1 (both ultimately come from the same dgmTypeEffects.csv-derived slot
# names); "drone"/"cargo"/"charge" have no SDE slot effect at all and are
# classified by SDE *category* instead (see doctrine/parser.py).
SLOT_SECTIONS = ("low", "med", "high", "rig", "subsystem", "service", "drone", "cargo", "charge")

# Exact-match critical positions (Phase 3 spec B.2/D.2) - Hull is handled
# separately as the matching gate (B.2), not part of this set.
EXACT_SECTIONS = frozenset({"low", "med", "high", "rig", "subsystem", "service"})

# Tolerance-eligible consumable positions (Phase 3 spec B.2/D.2).
CONSUME_SECTIONS = frozenset({"drone", "cargo", "charge"})

# ---------------------------------------------------------------- SDE category IDs
# Confirmed against this project's existing production/constants.py
# (SHIP_CATEGORY_ID) and standard EVE SDE invCategories values - duplicated
# here rather than cross-imported, see module docstring.
SHIP_CATEGORY_ID = 6
CHARGE_CATEGORY_ID = 8
DRONE_CATEGORY_ID = 18
STRUCTURE_CATEGORY_ID = 65   # Upwell structures - a valid Doctrine hull, not just ships
FIGHTER_CATEGORY_ID = 87
VALID_HULL_CATEGORY_IDS = frozenset({SHIP_CATEGORY_ID, STRUCTURE_CATEGORY_ID})

# ---------------------------------------------------------------- parse issues
# Phase 3 spec A.7's four issue kinds - a ParseIssue row is always exactly
# one of these.
ISSUE_KIND_UNRESOLVED_NAME = "unresolved_name"
ISSUE_KIND_AMBIGUOUS_SPLIT = "ambiguous_split"
ISSUE_KIND_UNKNOWN_SECTION = "unknown_section"
ISSUE_KIND_MALFORMED = "malformed"
ISSUE_KINDS = (ISSUE_KIND_UNRESOLVED_NAME, ISSUE_KIND_AMBIGUOUS_SPLIT, ISSUE_KIND_UNKNOWN_SECTION, ISSUE_KIND_MALFORMED)

# Levenshtein-suggestion cutoff (Phase 3 spec A.7: "kein Vorschlag, wenn
# Distanz > 1/3 der Namenslänge") - a suggestion further than this from the
# unresolved name is more likely to confuse than help.
LEVENSHTEIN_SUGGESTION_MAX_RATIO = 1 / 3

# ---------------------------------------------------------------- deviations
DEVIATION_KIND_MISSING = "missing"
DEVIATION_KIND_SHORT = "short"
DEVIATION_KIND_EXTRA = "extra"
DEVIATION_KIND_WRONG_VARIANT = "wrong_variant"
DEVIATION_KINDS = (DEVIATION_KIND_MISSING, DEVIATION_KIND_SHORT, DEVIATION_KIND_EXTRA, DEVIATION_KIND_WRONG_VARIANT)

SEVERITY_CRITICAL = "critical"
SEVERITY_TOLERABLE = "tolerable"
SEVERITY_INFO = "info"
SEVERITIES = (SEVERITY_CRITICAL, SEVERITY_TOLERABLE, SEVERITY_INFO)

# ---------------------------------------------------------------- statuses / ampel
VALIDATION_VALID = "valid"
VALIDATION_TOLERABLE = "tolerable"
VALIDATION_INVALID = "invalid"
VALIDATION_UNMATCHED = "unmatched"
VALIDATION_STATUSES = (VALIDATION_VALID, VALIDATION_TOLERABLE, VALIDATION_INVALID, VALIDATION_UNMATCHED)

AMPEL_GREEN = "green"
AMPEL_YELLOW = "yellow"
AMPEL_RED = "red"
AMPEL_GRAY = "gray"
# Ordering for "worst-of" aggregation (Phase 3 spec B.8) - gray is
# deliberately NOT in this tuple, it's handled as a separate case that never
# participates in the red > yellow > green comparison.
AMPEL_SEVERITY_ORDER = (AMPEL_GREEN, AMPEL_YELLOW, AMPEL_RED)

# ---------------------------------------------------------------- ESI contract sync
# Phase 2 E.3's pre-filter, before any per-contract items fetch: only
# item_exchange contracts, only "outstanding" status, are ever candidates
# for Doctrine matching - Auctions/Courier/already-accepted-or-expired
# contracts are never fetched at all.
CONTRACT_TYPE_ITEM_EXCHANGE = "item_exchange"
SYNCABLE_CONTRACT_STATUSES = ("outstanding",)

# ---------------------------------------------------------------- matching (Phase 3 B.5)
MATCH_WEIGHT_EXACT = 0.8
MATCH_WEIGHT_CONSUME = 0.2
MATCH_THRESHOLD = 0.5
