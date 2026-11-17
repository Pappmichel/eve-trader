"""Shared trade-hub settings (GitHub issue #222): the per-hub freight table
every tool uses in "All hubs" mode.

Session-only in api/app.py (`_SESSION_ONLY_API_PREFIXES`): Doctrine,
Production, Ore & Minerals and Module Reprocessing all edit the same table,
so no single tool grant fits. The table is this tenant's own setting
(tenant_settings, RLS-scoped), nothing cross-tenant."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import hubs
from ...actions import ActionError

router = APIRouter()


class HubFreightRow(BaseModel):
    region_id: int
    freight_cost_per_m3: Optional[float] = None


@router.get("")
def list_hubs():
    """The four trade hubs (region, name, label, system, main station) - the
    one list the pickers and labels read. Static, tenant-independent."""
    return hubs.do_list_hubs()


@router.get("/freight")
def get_hub_freight():
    return hubs.do_get_hub_freight()


@router.get("/observed-freight")
def get_observed_freight():
    """Suggested freight rate per hub from finished courier contracts
    (aggregates only; never applied automatically)."""
    return hubs.do_get_observed_freight()


@router.post("/freight")
def update_hub_freight(rows: Annotated[list[HubFreightRow], Field(max_length=1_000)]):
    try:
        return hubs.do_update_hub_freight({r.region_id: r.freight_cost_per_m3 for r in rows})
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))
