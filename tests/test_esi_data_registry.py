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


def test_group_1_has_six_owned_kinds_with_corp_variant():
    group_1 = [k for k in OWNED_DATA_KINDS if k.group == GROUP_1]
    assert len(group_1) == 6
    assert {k.key for k in group_1} == {
        "assets", "industry_jobs", "blueprints", "market_orders", "contracts", "wallet",
    }
    for kind in group_1:
        assert kind.corporation_scope
        assert kind.character_scope


def test_group_2_is_skills_with_no_corp_variant():
    group_2 = [k for k in OWNED_DATA_KINDS if k.group == GROUP_2]
    assert len(group_2) == 1
    skills = group_2[0]
    assert skills.key == "skills"
    assert skills.label == "Skills"
    assert skills.corporation_scope is None
    assert skills.corp_roles == ()
    assert skills.character_scope == "esi-skills.read_skills.v1"


def test_group_3_has_two_capabilities_and_no_consuming_tool_list():
    assert len(ACCESS_CAPABILITIES) == 2
    assert {c.key for c in ACCESS_CAPABILITIES} == {
        "structure_name_resolution", "structure_market_book",
    }
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
    assert "admin" not in consuming_tool_keys()
    assert "characters" not in consuming_tool_keys()
    assert "refining" not in consuming_tool_keys()
    assert "portfolio" not in consuming_tool_keys()


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
    assert by_key["assets"] == "normal"
    assert by_key["industry_jobs"] == "normal"
    assert by_key["contracts"] == "normal"
    assert by_key["blueprints"] == "rare"
    assert by_key["skills"] == "rare"
