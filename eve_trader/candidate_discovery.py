"""Market-group-based candidate discovery.

Walks the entire ESI market-group tree to build the universe of tradeable
items. Only a small, explicit set of top-level categories is excluded
(TradingConfig.excluded_path_prefixes - confirmed one-by-one with the user);
there's no keyword allowlist/denylist and no per-item volume(m3) cap -
every other item is a candidate and lives or dies on actual profitability +
trading volume, checked later in history_backtest.find_new_import_candidates
(see TradingConfig.min_margin_threshold/min_hit_rate/min_avg_movement).

Run order:
  1. build_candidate_universe()
  2. build_focused_candidate_universe(universe)   # pass-through, see its docstring
  3. eve_trader.history_backtest.find_new_import_candidates(...)
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from . import storage
from .config import TRADING_CONFIG, TradingConfig
from .esi_client import ESIClient, extract_meta_level, resolve_effective_volume
from .models import Candidate

log = logging.getLogger(__name__)

# SDE category_id=7 ("Module") covers both regular modules and rigs - same
# constant production/constants.py uses (MODULE_CATEGORY_ID). Duplicated here
# (rather than importing from production/) to keep the Trading tool free of a
# dependency on the Production tool's module.
MODULE_CATEGORY_ID = 7

# SDE category_id=20 ("Implant") covers both real cyberimplants and Boosters/
# Drugs - CCP doesn't split them into separate categories. group_id=746
# ("Booster") is what actually distinguishes them - confirmed wrong labeling
# (both showing as "Implant") by the user. Confirmed against real live SDE
# data (2026-08-19) - group_id 303 is the real "Booster" group (real drugs
# like AIR Agility Booster II); 746 ("Cyber Missile", an initial wrong guess
# caught during live post-deploy verification) is actually a skill-
# hardwiring implant group, not boosters at all.
IMPLANT_CATEGORY_ID = 20
BOOSTER_GROUP_ID = 303


def is_wanted_market_path(path: str, cfg: TradingConfig = TRADING_CONFIG) -> bool:
    """True unless `path` starts with one of the handful of categorically-
    excluded top-level groups (cfg.excluded_path_prefixes - ships/blueprints/
    apparel/personalization/pilot's services/structures, confirmed one-by-one
    with the user which don't structurally fit the import-arbitrage model).
    No longer requires matching a "wanted keywords" allowlist - confirmed
    with the user that no item should be sorted out just for being in an
    unlisted category; profitability (min_margin_threshold/min_hit_rate) and
    real trading volume (min_avg_movement) are the only gates now."""
    s = path.lower()
    for prefix in cfg.excluded_path_prefixes:
        if s.startswith(prefix):
            return False
    return True


def guess_category(path: str, item_name: str, volume_m3: float, category_id: Optional[int] = None,
                    category_names: Optional[dict[int, str]] = None, group_id: Optional[int] = None) -> str:
    """Display category for the Shortlist's "Kategorie" column - not used for
    any margin/filtering math. Prefers the real SDE category name (e.g.
    "Implant", "Charge", "Drone", "Skill", "Material") via `category_names`
    (storage.load_sde_category_names(), populated from Fuzzwork's
    invCategories.csv - see production/sde.py) when available. Previously this
    only distinguished "Module/Rig" vs a catch-all "Material" bucket, which
    mislabeled e.g. implants (category_id 20) as Material - confirmed wrong by
    the user. Falls back to the old string-matching heuristic ("module"/"rig"
    in the name/path, or a volume >=5m3 guess) only for the rare live-ESI-walk
    path (_build_candidate_universe_from_esi) where fetching each type's real
    category would mean an extra ESI call per type.

    `group_id` (optional - only the SDE-backed path has it) splits Boosters/
    Drugs (BOOSTER_GROUP_ID) out of the "Implant" category they'd otherwise
    share with real cyberimplants - see IMPLANT_CATEGORY_ID's own comment."""
    if category_id is not None:
        if category_id == IMPLANT_CATEGORY_ID and group_id == BOOSTER_GROUP_ID:
            return "Drugs"
        if category_names and category_id in category_names:
            return category_names[category_id]
        return "Module/Rig" if category_id == MODULE_CATEGORY_ID else "Material"
    s = f"{path} {item_name}".lower()
    if "module" in s or "rig" in s or volume_m3 >= 5:
        return "Module/Rig"
    return "Material"


def _market_group_path(group_id: int, names: dict[int, str], parents: dict[int, int]) -> str:
    parts: list[str] = []
    cur = group_id
    guard = 0
    while cur > 0 and guard < 20:
        parts.insert(0, names.get(cur, "?"))
        cur = parents.get(cur, 0)
        guard += 1
    return " > ".join(parts)


def build_candidate_universe(client: ESIClient | None = None,
                              cfg: TradingConfig = TRADING_CONFIG,
                              progress: bool = True) -> list[Candidate]:
    """Market groups and type name/volume/metaLevel are static SDE data (never
    change between CCP patches) - if the Production tool's Fuzzwork SDE cache
    (production/sde.py do_refresh_sde(), tables sde_market_groups/sde_types)
    is populated, build the universe from that local cache instead of
    walking ESI's market-group tree live (~2000+ separate ESI calls -
    list_market_group_ids() + one get_market_group() per group, then one
    get_type_info() per candidate type - why that path is noticeably
    slower). Falls back to the live-ESI walk only if the SDE cache is empty
    (e.g. a fresh install that hasn't run the Production tool's SDE refresh
    yet)."""
    market_groups = storage.load_sde_market_groups()
    sde_types = storage.load_sde_types_with_market_group()
    if market_groups and sde_types:
        category_names = storage.load_sde_category_names()
        type_groups = storage.load_sde_type_groups()
        return _build_candidate_universe_from_sde(market_groups, sde_types, cfg, category_names, type_groups)
    return _build_candidate_universe_from_esi(client, cfg, progress)


def _build_candidate_universe_from_sde(market_groups: list[tuple[int, int, str]],
                                        sde_types: list[tuple[int, str, float, int, Optional[int], Optional[int]]],
                                        cfg: TradingConfig,
                                        category_names: Optional[dict[int, str]] = None,
                                        type_groups: Optional[dict[int, int]] = None) -> list[Candidate]:
    names = {gid: name for gid, _parent, name in market_groups}
    parents = {gid: (parent or 0) for gid, parent, _name in market_groups}

    wanted_paths: dict[int, str] = {
        gid: path for gid in names
        if is_wanted_market_path(path := _market_group_path(gid, names, parents), cfg)
    }

    candidates: list[Candidate] = []
    for type_id, type_name, volume, market_group_id, meta_level, category_id in sde_types:
        path = wanted_paths.get(market_group_id)
        if path is None or not type_name or not volume or volume <= 0:
            continue
        # GitHub issue #73: capital-sized modules (category_id=MODULE_CATEGORY_ID)
        # have a much smaller *packaged* volume than the raw SDE `volume`
        # used above, the same quirk already fixed for Production's own haul
        # cost in issue #11 (production/engine.py's _haul_volume, now a thin
        # wrapper around this same resolve_effective_volume). Ships have the
        # identical quirk but are already excluded via is_wanted_market_path
        # above (excluded_path_prefixes), so they never reach this call -
        # only Module-category types actually trigger the ESI lookup here.
        # Passing category_id (already in hand from this loop's own tuple)
        # skips resolve_effective_volume's own storage.get_type_category
        # lookup.
        effective_volume = resolve_effective_volume(type_id, volume, category_id)
        candidates.append(Candidate(
            item=type_name, type_id=type_id, volume_m3=effective_volume,
            category=guess_category(path, type_name, volume, category_id, category_names,
                                     (type_groups or {}).get(type_id)),
            market_group_path=path, meta_level=meta_level,
        ))
    return candidates


def _build_candidate_universe_from_esi(client: ESIClient | None, cfg: TradingConfig,
                                        progress: bool) -> list[Candidate]:
    client = client or ESIClient(cfg)

    group_ids = client.list_market_group_ids()
    names: dict[int, str] = {}
    parents: dict[int, int] = {}
    group_types: dict[int, list[int]] = {}

    if progress:
        log.info("Loading %d EVE market groups...", len(group_ids))
    for gid in group_ids:
        info = client.get_market_group(gid)
        names[gid] = info.get("name", "")
        parents[gid] = info.get("parent_group_id", 0) or 0
        group_types[gid] = info.get("types", []) or []

    type_paths: dict[int, str] = {}
    for gid in names:
        path = _market_group_path(gid, names, parents)
        if is_wanted_market_path(path, cfg):
            for type_id in group_types.get(gid, []):
                type_paths.setdefault(type_id, path)

    if progress:
        log.info("Loading type names/volumes for %d candidate types...", len(type_paths))
    candidates: list[Candidate] = []
    for i, (type_id, path) in enumerate(type_paths.items()):
        info = client.get_type_info(type_id)
        item_name = info.get("name", "")
        volume = info.get("packaged_volume", info.get("volume", 0)) or 0
        if item_name and volume > 0:
            candidates.append(Candidate(
                item=item_name, type_id=type_id, volume_m3=volume,
                category=guess_category(path, item_name, volume),
                market_group_path=path,
                meta_level=extract_meta_level(info),
            ))
        if progress and i and i % 200 == 0:
            log.info("  ... %d/%d types processed", i, len(type_paths))
    return candidates


def build_focused_candidate_universe(universe: Iterable[Candidate],
                                      cfg: TradingConfig = TRADING_CONFIG) -> list[Candidate]:
    """Used to filter the raw universe down by market-group keyword/per-item
    m3 size (the original BuildFocusedCandidateUniverse/
    IsFocusedImportCandidate port) - confirmed with the user that no item
    should be sorted out by category or physical size before it's even
    price-checked, only by actual profitability (min_margin_threshold/
    min_hit_rate, applied later in history_backtest._score_candidate) and
    real trading volume (min_avg_movement, same place). Now a pass-through
    kept only so the existing "① Marktgruppen laden -> ② Kandidaten filtern
    -> ③ Neue Kandidaten suchen" workflow step numbering/UI doesn't need to
    change - `cfg` is accepted but unused."""
    return list(universe)
