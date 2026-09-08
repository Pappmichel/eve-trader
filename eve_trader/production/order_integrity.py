"""Read-only integrity scan for special_orders / special_order_items.

Phase E.2. Does not repair rows, does not call the planner, and does not
change SpecialOrder. Callers (do_audit_special_orders) only report.
Tenant-scoped via storage.connect() RLS — never crosses tenants.
"""
from __future__ import annotations

from .. import storage


def audit() -> list[dict]:
    """Every integrity issue currently visible to the current tenant.

    Each issue is ``{"kind": str, "order_id": str, "detail": str}``.
    ``kind`` is one of ``orphan_item``, ``empty_order``, ``nonpositive_quantity``.
    """
    issues: list[dict] = []
    headers = {str(row[0]) for row in storage.list_special_orders()}
    for order_id, _note, _net, _status, _created in storage.list_special_orders():
        oid = str(order_id)
        items = storage.list_special_order_items(oid)
        if not items:
            issues.append({
                "kind": "empty_order",
                "order_id": oid,
                "detail": "header has no line items",
            })
        for type_id, type_name, quantity in items:
            if quantity <= 0:
                issues.append({
                    "kind": "nonpositive_quantity",
                    "order_id": oid,
                    "detail": f"{type_name} ({type_id}) quantity={quantity}",
                })
    for order_id, type_id, type_name, quantity in storage.list_all_special_order_item_rows():
        oid = str(order_id)
        if oid not in headers:
            issues.append({
                "kind": "orphan_item",
                "order_id": oid,
                "detail": f"{type_name} ({type_id}) quantity={quantity}",
            })
    return issues
