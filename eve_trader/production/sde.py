"""Fuzzwork SDE importer.

ESI no longer exposes blueprint materials/products/job-time (CCP removed those
endpoints years ago) - Fuzzwork (fuzzwork.co.uk) republishes CCP's Static Data
Export as CSV after every patch. This downloads the handful of tables this
tool actually needs and caches them in Postgres (see storage.replace_sde_data),
previewed then applied via admin.do_preview_sde / do_apply_sde rather than
baked in once.
"""
from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

from .. import storage
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import ACTIVITY_COPYING, ACTIVITY_INVENTION, ACTIVITY_MANUFACTURING, ACTIVITY_REACTION

log = logging.getLogger("eve_trader.production.sde")

# Any one file from the dump is a fine freshness proxy - Fuzzwork regenerates
# the whole dump directory together, and invTypes.csv is already the first
# file fetch_sde() fetches anyway. Used both to record what we just fetched
# (apply_sde) and to cheaply check what's currently available
# (check_for_newer_sde) without downloading the ~19MB CSV body.
_FRESHNESS_FILE = "invTypes.csv"

USER_AGENT = "eve-trader-python"
# Copying (5) has no material rows (confirmed via wiki.eveuniversity.org/
# Blueprint_copying: "no materials are consumed", just time) - included here
# only for the blueprint_time fetch below, not material_rows/product_rows.
#
# activity_id 7 ("Reverse Engineering", the old Ancient-Relic-based Tech III
# invention mechanic) is deliberately NOT included - confirmed empirically
# (refreshed the SDE with it included and every relevant row count was
# unchanged) that current Fuzzwork/CCP data has zero rows for it: CCP removed
# Reverse Engineering years ago. Tech III hulls/subsystems are produced via
# the exact same activity_id=8 Invention this tool already models for Tech
# II (confirmed against real SDE data: e.g. Loki hull's blueprint - type_id
# 29991 - has a real find_invention_recipe_candidates_by_product_type_id()
# hit, same as any Tech II item, just with a Sleeper relic as the candidate
# instead of a real T1 blueprint) - see engine.py classify_activity's
# is_invented check.
_RELEVANT_ACTIVITIES = {ACTIVITY_MANUFACTURING, ACTIVITY_REACTION, ACTIVITY_INVENTION, ACTIVITY_COPYING}

# Sequential Fuzzwork CSV fetches for fetch_sde - batch/total_batches
# progress uses this tuple's length rather than a magic number. Order is
# the progress-batch order; why each file is needed is commented at the
# unpack site below (meta groups, slot effects, type materials).
_SDE_CSV_FILES = (
    "invTypes.csv",
    "invGroups.csv",
    "invCategories.csv",
    "invMarketGroups.csv",
    "invMetaTypes.csv",
    "industryActivity.csv",
    "industryActivityMaterials.csv",
    "industryActivityProducts.csv",
    "industryActivityProbabilities.csv",
    "mapSolarSystems.csv",
    "mapRegions.csv",
    "staStations.csv",
    "dgmTypeEffects.csv",
    "invTypeMaterials.csv",
    # Which skill(s) a blueprint's activity requires (production/engine.py's
    # job-time skill bonus - see constants.SPECIALIST_TIME_SKILLS, confirmed
    # 2026-09-27 against a real reaction job's Job Duration Modifiers panel).
    "industryActivitySkills.csv",
)


def _fetch_csv(session: requests.Session, base_url: str, filename: str) -> list[dict]:
    """Confirmed real gap: this used to be a bare session.get() with no
    retry at all, unlike every other network client in this codebase
    (esi_client._get_response retries 420/5xx up to 3x; goonmetrics_client's
    current_prices/price_history explicitly retry with backoff for the same
    "a bare timeout with no retry intermittently killed every caller on
    nothing more than normal response-time variance" reason). fetch_sde()
    fetches these sequentially over one session - a single transient
    blip on any one of them used to abort the whole SDE preview."""
    last_exc: Optional[requests.RequestException] = None
    for attempt in range(1, 4):
        try:
            resp = session.get(f"{base_url}{filename}", timeout=120)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_exc = e
            if attempt < 3:
                time.sleep(attempt * 2)
    else:
        raise last_exc
    text = resp.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


# Character Management (docs/CHARACTER_MANAGEMENT_PLAN.md R7): the skill
# catalogue needs a handful of dogma attributes out of dgmTypeAttributes.csv.
# That file has a row per (type, attribute) for the *whole* SDE - millions of
# rows - so it is streamed and filtered while parsing instead of going through
# _fetch_csv (which holds the whole body and a dict per row in memory).
_SKILL_ATTRIBUTES_FILE = "dgmTypeAttributes.csv"
_REQUIRED_SKILL_ATTRS = (182, 183, 184, 1285, 1289, 1290)        # requiredSkill1..6
_REQUIRED_SKILL_LEVEL_ATTRS = (277, 278, 279, 1286, 1287, 1288)  # requiredSkill1..6Level
_ATTR_SKILL_TIME_CONSTANT = 275   # skill rank
_ATTR_PRIMARY = 180
_ATTR_SECONDARY = 181
_KEPT_SKILL_ATTRS = frozenset(
    _REQUIRED_SKILL_ATTRS + _REQUIRED_SKILL_LEVEL_ATTRS
    + (_ATTR_SKILL_TIME_CONSTANT, _ATTR_PRIMARY, _ATTR_SECONDARY)
)


def _attr_value(row: dict) -> Optional[float]:
    for col in ("valueFloat", "valueInt"):
        v = row.get(col)
        if v not in (None, ""):
            return float(v)
    return None


def _fetch_skill_attributes(session: requests.Session, base_url: str) -> dict[int, dict[int, float]]:
    """{typeID: {attributeID: value}} for `_KEPT_SKILL_ATTRS` only, read as a
    stream. Same retry policy as _fetch_csv, wrapped around the *whole*
    stream - a connection reset half-way through restarts the download
    rather than yielding a silently truncated table."""
    last_exc: Optional[requests.RequestException] = None
    for attempt in range(1, 4):
        try:
            with session.get(f"{base_url}{_SKILL_ATTRIBUTES_FILE}", timeout=120, stream=True) as resp:
                resp.raise_for_status()
                lines = _decode_lines(resp.iter_lines())
                kept: dict[int, dict[int, float]] = {}
                for row in csv.DictReader(lines):
                    attr_id = int(row["attributeID"])
                    if attr_id not in _KEPT_SKILL_ATTRS:
                        continue
                    value = _attr_value(row)
                    if value is not None:
                        kept.setdefault(int(row["typeID"]), {})[attr_id] = value
                return kept
        except requests.RequestException as e:
            last_exc = e
            if attempt < 3:
                time.sleep(attempt * 2)
    raise last_exc


def _decode_lines(raw_lines):
    """Streamed byte lines -> str lines. The UTF-8 BOM (Fuzzwork's files
    carry one, which is why _fetch_csv decodes as utf-8-sig) can only sit on
    the very first line and would otherwise glue itself onto the first CSV
    column name."""
    for i, raw in enumerate(raw_lines):
        line = raw.decode("utf-8")
        yield line.lstrip("\ufeff") if i == 0 else line


def skill_rows_from_attributes(attrs_by_type: dict[int, dict[int, float]]) -> tuple[list[tuple], list[tuple]]:
    """(skill_requirements, skill_meta) rows from the filtered attribute map.

    requirements: (type_id, skill_id, level) for every requiredSkillN whose
    matching level attribute is present (a skill id without a level is
    skipped rather than guessed). meta: (skill_id, rank, primary, secondary)
    for every type that has a skillTimeConstant, i.e. every skill."""
    requirements: list[tuple] = []
    meta: list[tuple] = []
    for type_id, attrs in attrs_by_type.items():
        for skill_attr, level_attr in zip(_REQUIRED_SKILL_ATTRS, _REQUIRED_SKILL_LEVEL_ATTRS):
            skill_id = attrs.get(skill_attr)
            level = attrs.get(level_attr)
            if skill_id is None or level is None:
                continue
            requirements.append((type_id, int(skill_id), int(level)))
        rank = attrs.get(_ATTR_SKILL_TIME_CONSTANT)
        if rank is not None:
            primary = attrs.get(_ATTR_PRIMARY)
            secondary = attrs.get(_ATTR_SECONDARY)
            meta.append((
                type_id, rank,
                int(primary) if primary is not None else None,
                int(secondary) if secondary is not None else None,
            ))
    return requirements, meta


def _dump_etag(session: requests.Session, base_url: str) -> Optional[str]:
    """A plain HEAD request's ETag (no CSV body downloaded) - best-effort,
    returns None on any failure (a third-party server's freshness metadata
    being briefly unavailable shouldn't block a real refresh, and a staleness
    check that can't reach the server just reports "unknown", not an error)."""
    try:
        resp = session.head(f"{base_url}{_FRESHNESS_FILE}", timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return resp.headers.get("ETag")


def region_rows_from_csv(rows: list[dict]) -> list[tuple]:
    """mapRegions.csv -> (region_id, region_name) rows for sde_regions. Every
    region is kept (no filtering); only rows missing an id or a name are
    skipped, since region_name is NOT NULL."""
    return [
        (int(r["regionID"]), r["regionName"])
        for r in rows
        if r.get("regionID") not in (None, "") and r.get("regionName") not in (None, "")
    ]


def _int_or_none(v: str):
    return int(v) if v not in (None, "") else None


def _float_or_none(v: str):
    return float(v) if v not in (None, "") else None


def _emit_progress(progress_callback, payload: dict) -> None:
    """Best-effort: a status-write failure must never abort the real work.
    Copied rather than imported from actions._emit_progress - this module
    is imported by production/actions.py, so reaching back would cycle."""
    if progress_callback is None:
        return
    try:
        progress_callback(payload)
    except Exception:  # noqa: BLE001
        log.exception("progress_callback failed")


@dataclass
class FetchedSde:
    """Parsed Fuzzwork dump, not yet written to storage. Staged in
    admin._staged_sde between preview and apply so Apply does not re-fetch."""
    types: list[tuple] = field(default_factory=list)
    groups: list[tuple] = field(default_factory=list)
    market_groups: list[tuple] = field(default_factory=list)
    blueprint_time: list[tuple] = field(default_factory=list)
    blueprint_materials: list[tuple] = field(default_factory=list)
    blueprint_products: list[tuple] = field(default_factory=list)
    invention_probability: list[tuple] = field(default_factory=list)
    solar_systems: list[tuple] = field(default_factory=list)
    regions: list[tuple] = field(default_factory=list)
    stations: list[tuple] = field(default_factory=list)
    categories: list[tuple] = field(default_factory=list)
    type_slots: list[tuple] = field(default_factory=list)
    type_materials: list[tuple] = field(default_factory=list)
    blueprint_skills: list[tuple] = field(default_factory=list)
    # From dgmTypeAttributes.csv (Character Management) - see skill_rows_from_attributes.
    skill_requirements: list[tuple] = field(default_factory=list)
    skill_meta: list[tuple] = field(default_factory=list)
    dump_etag: Optional[str] = None


def fetch_sde(cfg: ProductionConfig = PRODUCTION_CONFIG, progress_callback=None) -> FetchedSde:
    """Downloads and parses the current Fuzzwork SDE export. Does not write
    the local cache - apply_sde() is the write half.

    progress_callback is optional so in-process callers stay unchanged; the
    HTTP background job passes pipeline_runner's writer. Emits phase=run +
    batch/total_batches (same vocabulary as Trading and Doctrine sync - not
    a per-file schema) before each CSV fetch."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    base = cfg.fuzzwork_csv_base
    dump_etag = _dump_etag(session, base)  # captured before the real fetches - see check_for_newer_sde

    fetched: dict[str, list[dict]] = {}
    total = len(_SDE_CSV_FILES) + 1  # + the streamed dgmTypeAttributes.csv below
    for i, filename in enumerate(_SDE_CSV_FILES, start=1):
        _emit_progress(progress_callback, {
            "phase": "run",
            "batch": i,
            "total_batches": total,
            "message": f"Fetching {filename}",
        })
        fetched[filename] = _fetch_csv(session, base, filename)
    _emit_progress(progress_callback, {
        "phase": "run",
        "batch": total,
        "total_batches": total,
        "message": f"Fetching {_SKILL_ATTRIBUTES_FILE} (filtered to skill attributes)",
    })
    skill_requirement_rows, skill_meta_rows = skill_rows_from_attributes(
        _fetch_skill_attributes(session, base)
    )

    inv_types = fetched["invTypes.csv"]
    inv_groups = fetched["invGroups.csv"]
    inv_categories = fetched["invCategories.csv"]
    inv_market_groups = fetched["invMarketGroups.csv"]
    # typeID -> metaGroupID (1=Tech I, 2=Tech II, 3=Storyline, 4=Faction,
    # 5=Officer, 6=Deadspace, ...) - a separate CSV from invTypes.csv (only
    # meta-variant types get a row at all), needed to tell a genuinely
    # invented Tech II item apart from a Faction/Officer/Deadspace one that
    # merely has an elevated metaLevel (see engine.classify_activity).
    inv_meta_types = fetched["invMetaTypes.csv"]
    activity_time = fetched["industryActivity.csv"]
    activity_materials = fetched["industryActivityMaterials.csv"]
    activity_products = fetched["industryActivityProducts.csv"]
    activity_probabilities = fetched["industryActivityProbabilities.csv"]
    solar_systems = fetched["mapSolarSystems.csv"]
    regions = fetched["mapRegions.csv"]
    stations = fetched["staStations.csv"]
    # typeID -> fitting slot, for the Doctrine tool's EFT parser (see
    # doctrine/parser.py's SDE-verification step). dgmTypeEffects.csv is a
    # typeID/effectID/isDefault table (every dogma effect a type has, not
    # just slot-related ones) - filtered here to just the 6 slot-defining
    # effect IDs (confirmed against EVE Ref dogma attribute references):
    # 11=loPower, 13=medPower, 12=hiPower, 2663=rigSlot, 3772=subSystem,
    # 6306=serviceSlot. A type has at most one of these in practice (a
    # module fits exactly one slot kind) - the dict comprehension below
    # keeps the last match per typeID if that assumption is ever wrong for
    # some edge-case type, rather than crashing the whole refresh.
    type_effects = fetched["dgmTypeEffects.csv"]
    # GitHub issue #90 ("Ore & Minerals"): reprocessing/manufacturing material
    # yields - a table this codebase has never loaded before. Both the ore/ice
    # and scrapmetal reprocessing paths are "type -> material yield" lookups
    # against this one SDE table (see refining/engine.py, storage.
    # get_type_materials).
    inv_type_materials = fetched["invTypeMaterials.csv"]
    activity_skills = fetched["industryActivitySkills.csv"]

    meta_group_by_type = {int(r["typeID"]): _int_or_none(r["metaGroupID"]) for r in inv_meta_types}
    types_rows = [
        (
            int(r["typeID"]), _int_or_none(r["groupID"]), r["typeName"],
            _float_or_none(r["volume"]), int(r["published"] == "1"),
            _int_or_none(r["marketGroupID"]), _int_or_none(r["metaLevel"]),
            meta_group_by_type.get(int(r["typeID"])),
            # portionSize (GitHub issue #90): the whole-batch unit
            # reprocessing rounds down to before applying yield% (e.g.
            # Veldspar=100) - confirmed present in invTypes.csv already, no
            # separate fetch needed.
            _int_or_none(r["portionSize"]),
        )
        for r in inv_types
    ]
    type_materials_rows = [
        (int(r["typeID"]), int(r["materialTypeID"]), float(r["quantity"]))
        for r in inv_type_materials
    ]
    groups_rows = [
        (int(r["groupID"]), _int_or_none(r["categoryID"]), r["groupName"])
        for r in inv_groups
    ]
    category_rows = [
        (int(r["categoryID"]), r["categoryName"])
        for r in inv_categories
    ]
    market_groups_rows = [
        (int(r["marketGroupID"]), _int_or_none(r["parentGroupID"]), r["marketGroupName"])
        for r in inv_market_groups
    ]
    time_rows = [
        (int(r["typeID"]), int(r["activityID"]), float(r["time"]))
        for r in activity_time if int(r["activityID"]) in _RELEVANT_ACTIVITIES
    ]
    material_rows = [
        (int(r["typeID"]), int(r["activityID"]), int(r["materialTypeID"]), float(r["quantity"]))
        for r in activity_materials if int(r["activityID"]) in _RELEVANT_ACTIVITIES
    ]
    product_rows = [
        (int(r["typeID"]), int(r["activityID"]), int(r["productTypeID"]), float(r["quantity"]))
        for r in activity_products if int(r["activityID"]) in _RELEVANT_ACTIVITIES
    ]
    probability_rows = [
        (int(r["typeID"]), int(r["productTypeID"]), float(r["probability"]))
        for r in activity_probabilities if int(r["activityID"]) == ACTIVITY_INVENTION
    ]
    blueprint_skill_rows = [
        (int(r["typeID"]), int(r["activityID"]), int(r["skillID"]), int(r["level"]))
        for r in activity_skills if int(r["activityID"]) in _RELEVANT_ACTIVITIES
    ]
    solar_system_rows = [
        (int(r["solarSystemID"]), r["solarSystemName"], float(r["security"]), _int_or_none(r.get("regionID")))
        for r in solar_systems if r.get("security") not in (None, "")
    ]
    region_rows = region_rows_from_csv(regions)
    station_rows = [
        (int(r["stationID"]), int(r["solarSystemID"]), r.get("stationName"))
        for r in stations if r.get("solarSystemID") not in (None, "")
    ]
    _SLOT_EFFECT_IDS = {11: "low", 13: "med", 12: "high", 2663: "rig", 3772: "subsystem", 6306: "service"}
    type_slot_by_id: dict[int, str] = {}
    for r in type_effects:
        slot = _SLOT_EFFECT_IDS.get(int(r["effectID"]))
        if slot is not None:
            type_slot_by_id[int(r["typeID"])] = slot
    type_slot_rows = list(type_slot_by_id.items())

    return FetchedSde(
        types=types_rows, groups=groups_rows, market_groups=market_groups_rows,
        blueprint_time=time_rows, blueprint_materials=material_rows,
        blueprint_products=product_rows, invention_probability=probability_rows,
        solar_systems=solar_system_rows, regions=region_rows, stations=station_rows,
        categories=category_rows, type_slots=type_slot_rows,
        type_materials=type_materials_rows, blueprint_skills=blueprint_skill_rows,
        skill_requirements=skill_requirement_rows, skill_meta=skill_meta_rows,
        dump_etag=dump_etag,
    )


def apply_sde(fetched: FetchedSde) -> dict:
    """Writes a previously fetched dump into the local SDE cache and records
    freshness. Safe to re-run any time (e.g. after a CCP balance patch)."""
    storage.replace_sde_data(
        types=fetched.types, groups=fetched.groups, market_groups=fetched.market_groups,
        blueprint_time=fetched.blueprint_time, blueprint_materials=fetched.blueprint_materials,
        blueprint_products=fetched.blueprint_products, stations=fetched.stations,
        invention_probability=fetched.invention_probability, solar_systems=fetched.solar_systems,
        regions=fetched.regions,
        categories=fetched.categories, type_slots=fetched.type_slots,
        type_materials=fetched.type_materials, blueprint_skills=fetched.blueprint_skills,
        skill_requirements=fetched.skill_requirements, skill_meta=fetched.skill_meta,
    )
    storage.set_sde_refresh_state(datetime.now(timezone.utc).isoformat(), fetched.dump_etag)
    return storage.sde_row_counts()


def check_for_newer_sde(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Cheap staleness check - one HEAD request (no CSV download) compared
    against the ETag recorded at the last successful apply_sde() - tells
    the caller whether it's worth clicking "SDE-Update prüfen" without
    actually doing the ~19MB, several-file download just to find out.
    Doesn't auto-refresh anything.

    newer_sde_available is only ever True on a genuine ETag *mismatch* - both
    "never refreshed yet" and "remote temporarily unreachable" report False
    rather than nagging on a guess (see _dump_etag's best-effort None)."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    state = storage.get_sde_refresh_state()
    local_refreshed_at, local_etag = state if state else (None, None)
    remote_etag = _dump_etag(session, cfg.fuzzwork_csv_base)
    newer_available = bool(remote_etag and local_etag and remote_etag != local_etag)
    return {
        "local_refreshed_at": local_refreshed_at,
        "remote_check_succeeded": remote_etag is not None,
        "newer_sde_available": newer_available,
    }
