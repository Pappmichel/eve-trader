"""Optional preview refresh after a successful special-order item mutation.

Phase E.4 wrapper: Set/Remove stay pure (no planner). This module calls those
actions, then `do_compute_special_order`. It must not import `engine`.
"""
from __future__ import annotations

from . import actions
from .config import PRODUCTION_CONFIG, ProductionConfig


def set_item_and_preview(order_id: str, type_id: int | str, quantity: float,
                         cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    detail = actions.do_set_special_order_item(order_id, type_id, quantity)
    plan = actions.do_compute_special_order(order_id, cfg)
    return {"order": detail["order"], "items": detail["items"], "plan": plan}


def remove_item_and_preview(order_id: str, type_id: int | str,
                            cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    detail = actions.do_remove_special_order_item(order_id, type_id)
    plan = actions.do_compute_special_order(order_id, cfg)
    return {"order": detail["order"], "items": detail["items"], "plan": plan}
