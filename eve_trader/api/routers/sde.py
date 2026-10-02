"""Read-only global SDE reference data for the frontend pickers (issue #223).

Session-only in api/app.py (`_SESSION_ONLY_API_PREFIXES`): region names are
public SDE data used by several tools' Settings pages (Trading, Module
Reprocessing), so no single tool grant fits - any authenticated character may
read them. Same bucket as /api/errors."""
from __future__ import annotations

from fastapi import APIRouter

from ... import storage

router = APIRouter()


@router.get("/regions")
def list_regions() -> list[dict]:
    return [{"region_id": rid, "region_name": name} for rid, name in storage.list_all_regions()]
