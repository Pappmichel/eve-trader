"""Data version stamps for the "New data available" notice (FRONTEND_PLAN.md
B.12). Session-only in api/app.py: shown in every tool's header, so no
single tool grant fits; the values are this tenant's own (RLS-scoped) plus
the global Jita price cache time."""
from __future__ import annotations

from fastapi import APIRouter

from ... import data_versions

router = APIRouter()


@router.get("/versions")
def get_data_versions():
    return data_versions.do_data_versions()
