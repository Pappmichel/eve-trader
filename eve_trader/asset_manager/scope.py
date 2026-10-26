"""Which owners the Asset Manager may read (docs/ASSET_MANAGER_PLAN.md D10).

The union of shares of every tool the tenant still holds, plus the Asset
Manager's own sharing row. A share with a tool whose grant was removed does
not count: the scheduler stops refreshing it, so it would show a frozen
snapshot as current (review item R-6).
"""
from __future__ import annotations

from typing import Optional

from .. import storage, tenant_eligibility
from ..esi_data.access import owners_shared_with_any

TOOL_KEY = "asset_manager"

# Every kind the page reads. `blueprints` gives ME/TE/runs and the copy
# valuation (R-1), `industry_jobs` the in-production source (F-2).
ASSET_KINDS = ("assets", "blueprints", "market_orders", "contracts", "industry_jobs", "corp_divisions")


def granted_tools() -> Optional[set[str]]:
    """The current tenant's grants, None when the gate is off (everything)."""
    tenant_id = storage.get_current_tenant()
    if tenant_id is None:
        raise RuntimeError("asset manager requires a tenant in scope")
    return tenant_eligibility.granted_tools(tenant_id)


def allowed_tools(grants: Optional[set[str]]) -> Optional[set[str]]:
    """Tools whose sharing rows count for the union. None = any tool."""
    if grants is None:
        return None
    return set(grants) | {TOOL_KEY}


def may_use(tool_key: str, grants: Optional[set[str]]) -> bool:
    """Cross-tool reads (manual stock, demand, Doctrine fittings) need the
    other tool's grant too, same rule as the Doctrine skill check."""
    return grants is None or tool_key in grants


def owner_scope(allowed: Optional[set[str]]) -> dict[str, tuple[list[int], list[int]]]:
    """kind -> (character ids, corporation ids) the page may read."""
    return {kind: owners_shared_with_any(kind, allowed) for kind in ASSET_KINDS}
