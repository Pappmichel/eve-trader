"""Fuzzwork SDE importer.

ESI no longer exposes blueprint materials/products/job-time (CCP removed those
endpoints years ago) - Fuzzwork (fuzzwork.co.uk) republishes CCP's Static Data
Export as CSV after every patch. This downloads the handful of tables this
tool actually needs and caches them in SQLite (see storage.replace_sde_data),
refreshed on demand via do_refresh_sde() (production/actions.py) rather than
baked in once.
"""
from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from .. import storage
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import ACTIVITY_COPYING, ACTIVITY_INVENTION, ACTIVITY_MANUFACTURING, ACTIVITY_REACTION

# Any one file from the dump is a fine freshness proxy - Fuzzwork regenerates
# the whole dump directory together, and invTypes.csv is already the first
# file refresh_sde() fetches anyway. Used both to record what we just fetched
# (refresh_sde) and to cheaply check what's currently available
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
# 29991 - has a real find_invention_recipe_by_product_type_id() hit, same as
# any Tech II item) - see engine.py classify_activity's is_invented check.
_RELEVANT_ACTIVITIES = {ACTIVITY_MANUFACTURING, ACTIVITY_REACTION, ACTIVITY_INVENTION, ACTIVITY_COPYING}


def _fetch_csv(session: requests.Session, base_url: str, filename: str) -> list[dict]:
    """Confirmed real gap: this used to be a bare session.get() with no
    retry at all, unlike every other network client in this codebase
    (esi_client._get_response retries 420/5xx up to 3x; goonmetrics_client's
    current_prices/price_history explicitly retry with backoff for the same
    "a bare timeout with no retry intermittently killed every caller on
    nothing more than normal response-time variance" reason). refresh_sde()
    fetches 11 of these sequentially over one session - a single transient
    blip on any one of them used to abort the whole SDE refresh."""
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


def _int_or_none(v: str):
    return int(v) if v not in (None, "") else None


def _float_or_none(v: str):
    return float(v) if v not in (None, "") else None


def refresh_sde(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Downloads the current Fuzzwork SDE export and replaces the local cache
    wholesale. Safe to re-run any time (e.g. after a CCP balance patch)."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    base = cfg.fuzzwork_csv_base
    dump_etag = _dump_etag(session, base)  # captured before the real fetches - see check_for_newer_sde

    inv_types = _fetch_csv(session, base, "invTypes.csv")
    inv_groups = _fetch_csv(session, base, "invGroups.csv")
    inv_categories = _fetch_csv(session, base, "invCategories.csv")
    inv_market_groups = _fetch_csv(session, base, "invMarketGroups.csv")
    # typeID -> metaGroupID (1=Tech I, 2=Tech II, 3=Storyline, 4=Faction,
    # 5=Officer, 6=Deadspace, ...) - a separate CSV from invTypes.csv (only
    # meta-variant types get a row at all), needed to tell a genuinely
    # invented Tech II item apart from a Faction/Officer/Deadspace one that
    # merely has an elevated metaLevel (see engine.classify_activity).
    inv_meta_types = _fetch_csv(session, base, "invMetaTypes.csv")
    activity_time = _fetch_csv(session, base, "industryActivity.csv")
    activity_materials = _fetch_csv(session, base, "industryActivityMaterials.csv")
    activity_products = _fetch_csv(session, base, "industryActivityProducts.csv")
    activity_probabilities = _fetch_csv(session, base, "industryActivityProbabilities.csv")
    solar_systems = _fetch_csv(session, base, "mapSolarSystems.csv")
    stations = _fetch_csv(session, base, "staStations.csv")
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
    type_effects = _fetch_csv(session, base, "dgmTypeEffects.csv")

    meta_group_by_type = {int(r["typeID"]): _int_or_none(r["metaGroupID"]) for r in inv_meta_types}
    types_rows = [
        (
            int(r["typeID"]), _int_or_none(r["groupID"]), r["typeName"],
            _float_or_none(r["volume"]), int(r["published"] == "1"),
            _int_or_none(r["marketGroupID"]), _int_or_none(r["metaLevel"]),
            meta_group_by_type.get(int(r["typeID"])),
        )
        for r in inv_types
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
    solar_system_rows = [
        (int(r["solarSystemID"]), r["solarSystemName"], float(r["security"]), _int_or_none(r.get("regionID")))
        for r in solar_systems if r.get("security") not in (None, "")
    ]
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

    storage.replace_sde_data(
        types=types_rows, groups=groups_rows, market_groups=market_groups_rows,
        blueprint_time=time_rows, blueprint_materials=material_rows, blueprint_products=product_rows,
        stations=station_rows,
        invention_probability=probability_rows, solar_systems=solar_system_rows,
        categories=category_rows, type_slots=type_slot_rows,
    )
    storage.set_sde_refresh_state(datetime.now(timezone.utc).isoformat(), dump_etag)

    return storage.sde_row_counts()


def check_for_newer_sde(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Cheap staleness check - one HEAD request (no CSV download) compared
    against the ETag recorded at the last successful refresh_sde() - tells
    the caller whether it's worth clicking "Refresh SDE" without actually
    doing the ~19MB, several-file download just to find out. Doesn't
    auto-refresh anything.

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
