"""Phase 0: ESI data-kind registry is vocabulary, no Postgres, no tool packages."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from eve_trader.access_gate import ALL_TOOL_KEYS
from eve_trader.esi_data.registry import (
    ACCESS_CAPABILITIES,
    GROUP_1,
    GROUP_2,
    OWNED_DATA_KINDS,
    AccessCapability,
    all_registry_scopes,
    consuming_tool_keys,
    fetcher_scope_map,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ESI_DATA_DIR = _REPO_ROOT / "eve_trader" / "esi_data"

_TOOL_PACKAGES = (
    "eve_trader.production",
    "eve_trader.doctrine",
    "eve_trader.sorting",
    "eve_trader.refining",
    "eve_trader.station_trading",
    "eve_trader.admin",
    "eve_trader.actions",
)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_registry_source_does_not_import_tool_packages():
    for path in sorted(_ESI_DATA_DIR.glob("*.py")):
        imported = _imported_modules(path)
        for name in imported:
            for pkg in _TOOL_PACKAGES:
                assert name != pkg and not name.startswith(pkg + "."), (
                    f"{path.name} imports {name}"
                )


def test_importing_registry_does_not_load_tool_packages():
    code = (
        "import sys\n"
        "import eve_trader.esi_data.registry  # noqa: F401\n"
        "forbidden = %r\n"
        "loaded = [m for m in forbidden if m in sys.modules]\n"
        "assert loaded == [], loaded\n" % (list(_TOOL_PACKAGES),)
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_group_1_has_seven_owned_kinds_with_corp_variant():
    group_1 = [k for k in OWNED_DATA_KINDS if k.group == GROUP_1]
    assert len(group_1) == 7
    assert {k.key for k in group_1} == {
        "assets", "industry_jobs", "blueprints", "market_orders", "contracts", "wallet",
        "wallet_balance",
    }
    for kind in group_1:
        assert kind.corporation_scope
        assert kind.character_scope


def test_group_2_is_character_only_kinds_with_no_corp_variant():
    group_2 = {k.key: k for k in OWNED_DATA_KINDS if k.group == GROUP_2}
    # skills + Character Management phase 1 (docs/CHARACTER_MANAGEMENT_PLAN.md).
    assert set(group_2) == {
        "skills", "skillqueue", "standings", "loyalty", "location", "ship", "online", "mail",
        "clones", "implants", "notifications", "fatigue", "contacts", "calendar",
    }
    for kind in group_2.values():
        assert kind.corporation_scope is None
        assert kind.corp_roles == ()
    skills = group_2["skills"]
    assert skills.label == "Skills"
    assert skills.character_scope == "esi-skills.read_skills.v1"


def test_every_kind_has_exactly_one_character_scope_and_scopes_are_unique():
    # R1: one scope per kind is an invariant the selector, orchestrator and
    # Characters page all assume. Two kinds sharing a scope would silently
    # double-book a token.
    scopes = [k.character_scope for k in OWNED_DATA_KINDS
              if k.key not in ("wallet_balance",)]  # wallet_balance shares wallet's scope by design
    assert all(isinstance(s, str) and s for s in scopes)
    assert len(scopes) == len(set(scopes))


def test_character_management_kinds():
    by_key = {k.key: k for k in OWNED_DATA_KINDS}
    assert by_key["standings"].character_scope == "esi-characters.read_standings.v1"
    assert by_key["loyalty"].character_scope == "esi-characters.read_loyalty.v1"
    assert by_key["location"].character_scope == "esi-location.read_location.v1"
    assert by_key["ship"].character_scope == "esi-location.read_ship_type.v1"
    assert by_key["online"].character_scope == "esi-location.read_online.v1"
    for key in ("standings", "loyalty", "location", "ship", "online"):
        assert by_key[key].consuming_tools == ("char_info",)
    # Live-only kinds: the three location kinds, plus mail (own code path,
    # phase 3 - never an orchestrator kind, so the stale clear cannot reach it).
    assert {k.key for k in OWNED_DATA_KINDS if k.live_only} == {"location", "ship", "online", "mail", "fatigue", "contacts", "calendar"}
    assert by_key["mail"].character_scope == "esi-mail.read_mail.v1"
    assert by_key["mail"].consuming_tools == ("char_mail",)
    # char_info also reads the wallet balance (Character Info's ISK column).
    assert "char_info" in by_key["wallet_balance"].consuming_tools
    assert "portfolio" in by_key["wallet_balance"].consuming_tools
    assert "char_info" in consuming_tool_keys()


def test_group_3_capabilities_and_no_consuming_tool_list():
    # corporation_roles added for docs/ESI_ACCESS_PLAN.md Known gap 2 (the
    # Corporations table's role warning); mail_send / mail_organize for the
    # Character Management mail write actions (phase 4).
    assert len(ACCESS_CAPABILITIES) == 5
    assert {c.key for c in ACCESS_CAPABILITIES} == {
        "structure_name_resolution", "structure_market_book", "corporation_roles",
        "mail_send", "mail_organize",
    }
    by_key = {c.key: c for c in ACCESS_CAPABILITIES}
    assert by_key["mail_send"].character_scope == "esi-mail.send_mail.v1"
    assert by_key["mail_organize"].character_scope == "esi-mail.organize_mail.v1"
    for key in ("mail_send", "mail_organize"):
        assert by_key[key].corporation_scope is None and by_key[key].corp_roles == ()
    for cap in ACCESS_CAPABILITIES:
        assert isinstance(cap, AccessCapability)
        assert not hasattr(cap, "consuming_tools")
        assert not hasattr(cap, "freshness_tier")


def test_structure_name_resolution_is_one_capability_carrying_both_scopes():
    cap = next(c for c in ACCESS_CAPABILITIES if c.key == "structure_name_resolution")
    assert cap.character_scope == "esi-universe.read_structures.v1"
    assert cap.corporation_scope == "esi-corporations.read_structures.v1"
    assert cap.corp_roles == ("Station_Manager",)
    # Not split into two kinds.
    assert sum(1 for c in ACCESS_CAPABILITIES if "structure_name" in c.key) == 1


def test_every_consuming_tool_key_is_in_all_tool_keys():
    allowed = set(ALL_TOOL_KEYS)
    for key in consuming_tool_keys():
        assert key in allowed, key
    # Management / non-consumers stay out of the consuming set.
    assert "characters" in ALL_TOOL_KEYS
    assert "admin" not in consuming_tool_keys()
    assert "characters" not in consuming_tool_keys()
    assert "refining" not in consuming_tool_keys()
    # Portfolio rework: "portfolio" is a real consumer now, of exactly
    # "wallet_balance" (Total Wealth) - not "wallet" (Trading's own
    # reconciliation keeps owning that one).
    assert "portfolio" in consuming_tool_keys()


def test_every_scope_appears_in_fetcher_facing_mapping():
    mapped = {scope for scopes in fetcher_scope_map().values() for scope in scopes}
    assert mapped == all_registry_scopes()
    assert "esi-universe.read_structures.v1" in mapped
    assert "esi-corporations.read_structures.v1" in mapped
    assert "esi-markets.structure_markets.v1" in mapped
    # Skills has no corporation entry.
    assert ("skills", "corporation") not in fetcher_scope_map()
    assert ("skills", "character") in fetcher_scope_map()


def test_default_freshness_tiers_match_the_plan():
    by_key = {k.key: k.freshness_tier for k in OWNED_DATA_KINDS}
    assert by_key["market_orders"] == "frequent"
    assert by_key["wallet"] == "frequent"
    assert by_key["wallet_balance"] == "frequent"
    assert by_key["assets"] == "normal"
    assert by_key["industry_jobs"] == "normal"
    assert by_key["contracts"] == "normal"
    assert by_key["blueprints"] == "rare"
    assert by_key["skills"] == "rare"
