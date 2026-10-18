"""Capital deployed, aggregated from the realized-profit ledger.

Listed quantities are taken from the oldest cost layer of that type
first, then outstanding contract quantities. What remains is stock.
Unrealized profit is market value minus cost for units that have both.
A missing price is left out of both sides. Escrow stays unknown (None)
until every open buy has an archived escrow amount.
"""
from __future__ import annotations

from typing import Optional

_EPS = 1e-9


def _layer_estimated(layer: dict) -> bool:
    return bool(
        layer.get("origin") == "opening"
        or layer.get("estimated_origin")
        or layer.get("price_missing")
        or layer.get("assumed_me")
        or layer.get("assumed_decryptor")
    )


def _queues(layers: list[dict]) -> dict[int, list[dict]]:
    queues: dict[int, list[dict]] = {}
    for layer in layers:
        queues.setdefault(int(layer["type_id"]), []).append({
            "quantity": float(layer["quantity"]),
            "unit_cost": float(layer["unit_cost"]),
            "estimated": _layer_estimated(layer),
        })
    return queues


def _take(queue: list[dict], need: float) -> dict:
    cost = 0.0
    covered = 0.0
    estimated_qty = 0.0
    while need > _EPS and queue:
        layer = queue[0]
        take = min(need, layer["quantity"])
        cost += take * layer["unit_cost"]
        covered += take
        if layer["estimated"]:
            estimated_qty += take
        layer["quantity"] -= take
        need -= take
        if layer["quantity"] <= _EPS:
            queue.pop(0)
    return {"cost": cost, "covered": covered, "estimated_qty": estimated_qty, "uncovered": max(need, 0.0)}


def _contract_unit_prices(items: list[dict], prices: dict) -> list[dict]:
    """Split each contract's price across its items by wealth-price weight.
    A contract with no price uses the wealth price per unit instead."""
    grouped: dict[int, list[dict]] = {}
    for item in items:
        grouped.setdefault(int(item["contract_id"]), []).append(item)
    out = []
    for rows in grouped.values():
        price = rows[0].get("contract_price")
        if price is None:
            for row in rows:
                unit = prices.get(int(row["type_id"]))
                out.append({**row, "unit_price": unit})
            continue
        weights = []
        for row in rows:
            unit = prices.get(int(row["type_id"]))
            weights.append(float(row["quantity"]) * unit if unit is not None else 0.0)
        if sum(weights) <= _EPS:
            weights = [float(row["quantity"]) for row in rows]
        total_weight = sum(weights) or 1.0
        for row, weight in zip(rows, weights):
            quantity = float(row["quantity"])
            share = float(price) * weight / total_weight
            out.append({**row, "unit_price": (share / quantity) if quantity else None})
    return out


def _bucket(key: str, label: str, quantity: float, cost: Optional[float], market: Optional[float],
            estimated_qty: float, uncovered: float, extra: Optional[dict] = None) -> dict:
    if cost is None or market is None:
        unrealized = None
        coverage = None
        estimated = False
    elif quantity is None:
        unrealized = market - cost
        coverage = 1.0
        estimated = False
    else:
        unrealized = market - cost
        coverage = None if quantity <= _EPS else max(quantity - estimated_qty - uncovered, 0.0) / quantity
        estimated = estimated_qty > _EPS or uncovered > _EPS or (coverage is not None and coverage < 1)
    row = {
        "key": key,
        "label": label,
        "quantity": quantity,
        "cost_basis": cost,
        "market_value": market,
        "unrealized": unrealized,
        "coverage": coverage,
        "estimated": estimated,
    }
    if extra:
        row.update(extra)
    return row


def _finish(buckets: list[dict]) -> dict:
    """The total covers the buckets that have both a cost and a market
    value, so market - cost is unrealized for the same goods. A bucket
    without one (running jobs, escrow not stored yet) keeps its own row and
    is named in `excluded`; it does not blank the whole total."""
    included = [
        row for row in buckets
        if row["cost_basis"] is not None and row["market_value"] is not None
    ]
    excluded = [row["key"] for row in buckets if row not in included]
    cost = sum(row["cost_basis"] for row in included) if included else None
    market = sum(row["market_value"] for row in included) if included else None
    unrealized = None if cost is None or market is None else market - cost
    estimated = any(row["estimated"] for row in included)
    coverages = [row["coverage"] for row in included if row["coverage"] is not None]
    coverage = min(coverages) if coverages else None
    return {
        "source": "realized-profit ledger",
        "buckets": buckets,
        "total": {
            "cost_basis": cost,
            "market_value": market,
            "unrealized": unrealized,
            "estimated": estimated,
            "coverage": coverage,
            "excluded": excluded,
        },
    }


def report(
    layers: list[dict],
    listed: list[dict],
    contracts: list[dict],
    escrow: Optional[float],
    prices: dict,
    in_production: Optional[dict] = None,
) -> dict:
    """`layers` are oldest-first within each type. `listed` is
    {type_id, quantity, order_price}. `contracts` is
    {contract_id, type_id, quantity, contract_price}. `escrow` None means
    unknown. `in_production` {count, cost} is omitted when count is 0.
    """
    queues = _queues(layers)
    listed_by_type: dict[int, float] = {}
    listed_market = 0.0
    listed_comparison = 0.0
    listed_comparison_known = False
    for row in listed:
        type_id = int(row["type_id"])
        quantity = float(row["quantity"])
        listed_by_type[type_id] = listed_by_type.get(type_id, 0.0) + quantity
        listed_market += quantity * float(row["order_price"])
        wealth = prices.get(type_id)
        if wealth is not None:
            listed_comparison += quantity * float(wealth)
            listed_comparison_known = True

    listed_take = {"cost": 0.0, "covered": 0.0, "estimated_qty": 0.0, "uncovered": 0.0}
    for type_id, quantity in listed_by_type.items():
        taken = _take(queues.setdefault(type_id, []), quantity)
        for key in listed_take:
            listed_take[key] += taken[key]

    priced_contracts = _contract_unit_prices(contracts, prices)
    contract_qty: dict[int, float] = {}
    contract_market = 0.0
    contract_market_known = True
    contract_quantity = 0.0
    for row in priced_contracts:
        type_id = int(row["type_id"])
        quantity = float(row["quantity"])
        contract_qty[type_id] = contract_qty.get(type_id, 0.0) + quantity
        contract_quantity += quantity
        if row["unit_price"] is None:
            contract_market_known = False
        else:
            contract_market += quantity * float(row["unit_price"])

    contract_take = {"cost": 0.0, "covered": 0.0, "estimated_qty": 0.0, "uncovered": 0.0}
    for type_id, quantity in contract_qty.items():
        taken = _take(queues.setdefault(type_id, []), quantity)
        for key in contract_take:
            contract_take[key] += taken[key]

    stock_market = 0.0
    stock_qty = 0.0
    stock_estimated = 0.0
    stock_unpriced = 0.0
    stock_cost_basis = 0.0
    for type_id, queue in queues.items():
        price = prices.get(type_id)
        for layer in queue:
            if layer["quantity"] <= _EPS:
                continue
            stock_qty += layer["quantity"]
            if layer["estimated"]:
                stock_estimated += layer["quantity"]
            if price is None:
                # No price: leave the units out of both market and the cost
                # unrealized subtracts. Coverage falls by the same quantity.
                stock_unpriced += layer["quantity"]
            else:
                stock_cost_basis += layer["quantity"] * layer["unit_cost"]
                stock_market += layer["quantity"] * float(price)

    listed_qty = sum(listed_by_type.values())
    buckets = [
        _bucket("stock", "Stock", stock_qty, stock_cost_basis, stock_market, stock_estimated, stock_unpriced),
        _bucket(
            "listed", "Listed for sale", listed_qty, listed_take["cost"], listed_market,
            listed_take["estimated_qty"], listed_take["uncovered"],
            {"comparison_value": listed_comparison if listed_comparison_known else None},
        ),
        _bucket(
            "contracts", "In outstanding contracts", contract_quantity,
            contract_take["cost"], contract_market if contract_market_known else None,
            contract_take["estimated_qty"], contract_take["uncovered"],
        ),
        _bucket("escrow", "Buy-order escrow", None, escrow, escrow, 0.0, 0.0),
    ]
    if in_production and int(in_production.get("count") or 0) > 0:
        buckets.append(_bucket(
            "in_production", "In production", float(in_production["count"]),
            float(in_production["cost"]), None, 0.0, 0.0,
        ))
    return _finish(buckets)
