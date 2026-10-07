"""Chains across planets (docs/PI_PLAN.md 3.4) - pure.

For a target product of tier T, the classic split is one planet type per
stage: P0->P1 extraction planets feed P1->P2 factory planets, which feed
P2->P3, then P3->P4. The top stage is sized to a wanted output per hour
(or to one colony when none is given); each stage below is sized to what
the stage above imports (fractional planet counts, rounded up for display).
Every stage's colony is costed with the same economics as a standalone
colony, except that goods passed between own stages are valued at the
market price on both ends (opportunity cost): a P2 colony fed by own P1 is
only worth what it adds over selling that P1 (jwebbdev's "chains look 2-5x
better than they are" lesson).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import economics as econ
from .model import CHAINS, Evaluation, StaticData

# best(chain, product) -> (evaluation, reason) for the stage colony; the caller
# decides planet type/radius/assumptions (actions.py).
BestFn = Callable[[str, int], tuple[Optional[Evaluation], Optional[str]]]
# extraction(p1) -> (evaluation, reason) for the best P0->P1 colony of that P1.
ExtractFn = Callable[[int], tuple[Optional[Evaluation], Optional[str]]]

_STAGE_CHAIN = {2: "P1-P2", 3: "P2-P3", 4: "P3-P4"}


@dataclass
class StageNode:
    product_type_id: int
    tier: int
    chain: str
    colonies: float                     # fractional planets needed
    evaluation: Optional[Evaluation]
    economics: Optional[econ.Economics]
    reason: Optional[str] = None
    children: list["StageNode"] = field(default_factory=list)

    def to_dict(self, static: StaticData) -> dict:
        ev = self.evaluation
        return {
            "product_type_id": self.product_type_id,
            "product_name": static.name(self.product_type_id),
            "tier": self.tier,
            "chain": self.chain,
            "colonies": self.colonies,
            "colonies_ceil": math.ceil(self.colonies - 1e-9),
            "per_colony_per_hour": ev.effective_product_per_hour if ev else None,
            "profit_per_colony_day": self.economics.profit_per_day if self.economics else None,
            "reason": self.reason,
            "children": [c.to_dict(static) for c in self.children],
        }


def _stage(static, product, needed_per_hour, best: BestFn, extract: ExtractFn, prices, settings) -> StageNode:
    tier = static.tier(product) or 0
    if tier == 1:
        ev, why = extract(product)
        chain = "P0-P1"
    else:
        chain = _STAGE_CHAIN[tier]
        ev, why = best(chain, product)
    if ev is None or ev.effective_product_per_hour <= 0:
        return StageNode(product, tier, chain, math.inf, ev, None, why or "nothing produced")
    colonies = needed_per_hour / ev.effective_product_per_hour if needed_per_hour else 1.0
    node = StageNode(product, tier, chain, colonies, ev, econ.compute(ev, static, prices, settings))
    if tier > 1:
        source_tier, _ = CHAINS[chain]
        for t, rate in ev.imports.items():
            if static.tier(t) == source_tier:
                node.children.append(_stage(static, t, rate * ev.effective_factor * colonies,
                                            best, extract, prices, settings))
    return node


def _walk(node: StageNode):
    yield node
    for c in node.children:
        yield from _walk(c)


def chain_plan(static: StaticData, product: int, best: BestFn, extract: ExtractFn,
               prices: econ.Prices, settings: econ.MarketSettings,
               per_hour: Optional[float] = None) -> dict:
    """The full chain down to extraction, plus "stop at tier k" summaries:
    planets needed and profit per planet per day if you sell at that tier.

    `per_hour` sizes the top stage to that output. None keeps one top colony."""
    root = _stage(static, product, 0.0 if per_hour is None else float(per_hour),
                  best, extract, prices, settings)
    nodes = list(_walk(root))
    feasible = all(math.isfinite(n.colonies) for n in nodes)

    # Value added per stage: profit of the stage colony already pays for its
    # inputs at market price, so summing stage profits x colonies is the
    # chain profit with own intermediates at opportunity cost.
    by_tier: dict[int, list[StageNode]] = {}
    for n in nodes:
        by_tier.setdefault(n.tier, []).append(n)
    summaries = []
    top = root.tier
    for stop in range(1, top + 1):
        included = [n for n in nodes if n.tier <= stop and math.isfinite(n.colonies)]
        if not included or any(not math.isfinite(n.colonies) for n in nodes if n.tier <= stop):
            summaries.append({"tier": stop, "feasible": False})
            continue
        # Scale: the "stop at tier k" variant builds the same lower stages as
        # the full chain and sells the tier-k goods instead of processing them.
        planets = sum(n.colonies for n in included)
        profit = 0.0
        for n in included:
            if n.economics is None:
                continue
            per_colony = n.economics.profit_per_day
            profit += per_colony * n.colonies
        summaries.append({
            "tier": stop,
            "feasible": True,
            "planets": planets,
            "planets_ceil": sum(math.ceil(n.colonies - 1e-9) for n in included),
            "profit_per_day": profit,
            "profit_per_planet_day": profit / planets if planets else None,
        })
    return {
        "product_type_id": product,
        "product_name": static.name(product),
        "per_hour": per_hour,
        "feasible": feasible,
        "tree": root.to_dict(static),
        "stop_at": summaries,
    }
