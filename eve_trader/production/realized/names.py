"""Display names for the ids the realized-profit report hands out.

Warnings, archive status and jobs carry type ids, owner ids, location ids
and job/sale/contract refs. This module resolves them in bulk: item names
from the SDE, the tenant's own characters from its tokens, every other
character or corporation from ESI's public /universe/names/ (cached for an
hour, best effort), locations through storage's usual lookup chain. A name
that cannot be resolved is simply missing; callers keep showing the id.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from ... import storage

log = logging.getLogger(__name__)

_ACTIVITY = {
    1: "Manufacturing", 3: "Research TE", 4: "Research ME", 5: "Copying",
    8: "Invention", 9: "Reaction", 11: "Reaction",
}

_TYPE_RE = re.compile(r"\b(?:Production )?type (\d+)")
_JOB_RE = re.compile(r"\bjob:(character|corporation):(\d+):(\d+)")
_TX_RE = re.compile(r"\btx:(character|corporation):(\d+):\d+:(\d+)")
_CONTRACT_RE = re.compile(r"\bcontract:(\d+)(?::\d+)?")
_SOLD_AT_RE = re.compile(r"\bsold at (\d+)")


def activity_label(activity_id: Optional[int]) -> Optional[str]:
    if activity_id is None:
        return None
    return _ACTIVITY.get(int(activity_id), f"Activity {int(activity_id)}")


def type_names(type_ids: Iterable[int]) -> dict[int, str]:
    ids = {int(t) for t in type_ids if t is not None}
    return storage.sde_type_names(ids) if ids else {}


def location_names(location_ids: Iterable[int]) -> dict[int, str]:
    ids = sorted({int(i) for i in location_ids if i})
    if not ids:
        return {}
    try:
        return {int(k): v for k, v in storage.get_location_names(ids).items() if v}
    except Exception:  # noqa: BLE001 - a name lookup must not fail the report
        log.warning("realized: location name lookup failed", exc_info=True)
        return {}


def owner_names(owner_ids: Iterable[int]) -> dict[int, str]:
    """Character or corporation id -> name, without ESI where possible: the
    tenant's own characters from its tokens, corporations and job installers
    from names earlier syncs stored (db.stored_owner_names). Only ids found
    nowhere go to ESI's public names endpoint (cached for an hour)."""
    ids = {int(i) for i in owner_ids if i}
    if not ids:
        return {}
    out: dict[int, str] = {}
    try:
        from ...auth import TokenManager
        from ...config import OAUTH_CONFIG
        for rec in TokenManager(OAUTH_CONFIG).list_records():
            if rec.character_id is not None and int(rec.character_id) in ids and rec.character_name:
                out[int(rec.character_id)] = str(rec.character_name)
    except Exception:  # noqa: BLE001
        log.warning("realized: token character names unavailable", exc_info=True)
    missing = sorted(ids - set(out))
    if missing:
        try:
            from . import db
            out.update(db.stored_owner_names(missing))
        except Exception:  # noqa: BLE001
            log.warning("realized: stored owner names unavailable", exc_info=True)
    missing = sorted(ids - set(out))
    if missing:
        try:
            out.update({int(k): str(v) for k, v in _esi_names(missing).items()})
        except Exception:  # noqa: BLE001 - ESI down: keep the ids
            log.warning("realized: ESI name lookup failed", exc_info=True)
    return out


def _esi_names(ids: list[int]) -> dict[int, str]:
    from ...esi_client import ESIClient
    return ESIClient().resolve_names_cached(ids)


def _job_text(ref: str, jobs: dict, types: dict, owners: dict) -> str:
    job = jobs.get(ref)
    job_id = ref.rsplit(":", 1)[-1]
    if job is None:
        owner = owners.get(int(ref.split(":")[2]))
        return f"job #{job_id}" + (f" ({owner})" if owner else "")
    product = types.get(job["type_id"]) if job["type_id"] is not None else None
    parts = [p for p in (product, job["activity"], owners.get(job["owner_id"])) if p]
    return f"job #{job_id}" + (f" ({', '.join(parts)})" if parts else "")


def label_warnings(groups: list[dict], jobs: dict[str, dict]) -> None:
    """Add names to warning items in place.

    `jobs`: job_ref -> {"type_id", "activity", "owner_id"} from the latest
    rebuild. Each item gains `type_name`, `owner_name`, `location_name`,
    `job_label` where its ids resolve, `type_id` / `job_ref` when only its
    message names them (so the page can link the row), and `text`: the
    message with ids replaced by names and the kind prefix dropped.
    """
    type_ids: set[int] = set()
    owner_ids: set[int] = set()
    location_ids: set[int] = set()
    parsed: list[tuple[dict, dict]] = []
    for group in groups:
        for item in group["items"]:
            msg = str(item.get("message") or "")
            found = {
                "types": [int(m) for m in _TYPE_RE.findall(msg)],
                "jobs": [f"job:{a}:{b}:{c}" for a, b, c in _JOB_RE.findall(msg)],
                "txs": [(a, int(b), c) for a, b, c in _TX_RE.findall(msg)],
                "sold_at": [int(m) for m in _SOLD_AT_RE.findall(msg)],
            }
            parsed.append((item, found))
            if item.get("type_id") is not None:
                type_ids.add(int(item["type_id"]))
            type_ids.update(found["types"])
            if item.get("owner_id") is not None:
                owner_ids.add(int(item["owner_id"]))
            for ref in found["jobs"]:
                owner_ids.add(int(ref.split(":")[2]))
                job = jobs.get(ref)
                if job is not None:
                    owner_ids.add(int(job["owner_id"]))
                    if job["type_id"] is not None:
                        type_ids.add(int(job["type_id"]))
            for _ot, oid, _tx in found["txs"]:
                owner_ids.add(oid)
            if item.get("job_ref") in jobs:
                job = jobs[item["job_ref"]]
                owner_ids.add(int(job["owner_id"]))
                if job["type_id"] is not None:
                    type_ids.add(int(job["type_id"]))
            if item.get("location_id"):
                location_ids.add(int(item["location_id"]))
            location_ids.update(found["sold_at"])

    types = type_names(type_ids)
    owners = owner_names(owner_ids)
    locations = location_names(location_ids)

    for item, found in parsed:
        if item.get("type_id") is None and len(set(found["types"])) == 1:
            item["type_id"] = found["types"][0]
        if not item.get("job_ref") and len(set(found["jobs"])) == 1:
            item["job_ref"] = found["jobs"][0]
        if item.get("type_id") is not None and int(item["type_id"]) in types:
            item["type_name"] = types[int(item["type_id"])]
        if item.get("owner_id") is not None and int(item["owner_id"]) in owners:
            item["owner_name"] = owners[int(item["owner_id"])]
        if item.get("location_id") and int(item["location_id"]) in locations:
            item["location_name"] = locations[int(item["location_id"])]
        if item.get("job_ref"):
            item["job_label"] = _job_text(str(item["job_ref"]), jobs, types, owners)
        msg = item.get("message")
        if not msg:
            continue
        text = str(msg)
        if ": " in text:
            text = text.split(": ", 1)[1]
        text = _JOB_RE.sub(lambda m: _job_text(m.group(0), jobs, types, owners), text)
        text = _TX_RE.sub(
            lambda m: f"market sale #{m.group(3)}"
            + (f" ({owners[int(m.group(2))]})" if int(m.group(2)) in owners else ""), text)
        text = _CONTRACT_RE.sub(lambda m: f"contract #{m.group(1)}", text)
        text = _TYPE_RE.sub(lambda m: types.get(int(m.group(1)), f"type {m.group(1)}"), text)
        text = _SOLD_AT_RE.sub(lambda m: f"sold at {locations.get(int(m.group(1)), m.group(1))}", text)
        item["text"] = text
