"""Which tenants the scheduler's (and the shared Jita cache's) background work
is worth spending on - docs/SCHEDULER_REWORK_PLAN.md decisions 1, 2a, 3, 4.

Two independent questions:
- `is_active`: did this tenant make an authenticated request recently
  (`tenants.last_active_at`, written by AccessGateMiddleware)? Threshold is the
  operator-only `SchedulerOperatorConfig.inactive_tenant_days`.
- `granted_tools`: which tool grants does the tenant currently hold? A tenant
  that lost a tool must stop costing ESI calls for it.

Kept out of scheduler.py because production/jita_price_cache.py needs the same
answers and scheduler.py imports that module.
"""
from __future__ import annotations

import datetime as dt
from typing import Mapping, Optional

from . import storage
from .config import ACCESS_CONFIG, SCHEDULER_OPERATOR_CONFIG


def is_active(
    tenant_id: str,
    last_active: Optional[Mapping[str, Optional[dt.datetime]]] = None,
    *,
    now: Optional[dt.datetime] = None,
) -> bool:
    """False only when the tenant has a recorded `last_active_at` older than
    `inactive_tenant_days`. NULL/unknown counts as active (a fresh rollout must
    not switch anyone off), `0` disables the check, and the Default tenant (the
    CLI/operator, no login) is always active. `last_active` is the
    `storage.list_tenant_last_active()` map (pass it in to avoid a query per
    tenant); None reads it here."""
    days = SCHEDULER_OPERATOR_CONFIG.inactive_tenant_days
    if days <= 0 or str(tenant_id) == storage.DEFAULT_TENANT_ID:
        return True
    if last_active is None:
        last_active = storage.list_tenant_last_active()
    last = last_active.get(str(tenant_id))
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    clock = now or dt.datetime.now(dt.timezone.utc)
    return (clock - last).total_seconds() < days * 86400


def granted_tools(tenant_id: str) -> Optional[set[str]]:
    """The tenant's current tool grants, or None meaning "no restriction".

    Gate off (trusted local operator): everything is allowed, and
    DEFAULT_TENANT_ID has no rows in tool_grants at all - returning an empty
    set there would stop every sync. Gate on: exactly what `tool_grants` says,
    for the Default tenant too (`admin bootstrap` can register a character
    there with real grants)."""
    if not ACCESS_CONFIG.access_gate_enabled:
        return None
    return set(storage.list_tool_grants_for_tenant(str(tenant_id)))


def may_use(tool_key: str, grants: Optional[set[str]]) -> bool:
    return grants is None or tool_key in grants


def alerts_allowed(tenant_id: str) -> bool:
    """May this tenant's Discord alerts run? Deliberately NOT gated on
    `is_active` (someone who only wants pings and never logs in must keep
    them) nor on the tenant's own `scheduler_enabled`. Gate on: the tenant
    must still have a registered, non-suspended character and hold the
    `char_alerts` grant. Gate off: always."""
    if not ACCESS_CONFIG.access_gate_enabled:
        return True
    suspended = storage.tenant_registry_suspension(str(tenant_id))
    if suspended is None or suspended:
        return False
    return may_use("char_alerts", granted_tools(tenant_id))
