"""Pricing for the Production tool: current home/Jita buy/sell
(GoonmetricsClient.current_prices), ESI adjusted prices, and ESI system cost
indices. See esi_client.py / goonmetrics_client.py for the actual HTTP calls
this wraps.
"""
from __future__ import annotations

from typing import Optional

from ..esi_client import ESIClient
from ..goonmetrics_client import CurrentPrice, GoonmetricsClient
from .config import PRODUCTION_CONFIG, ProductionConfig

JITA_MARKET = "jita"


def home_prices(gm_client: GoonmetricsClient, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict[int, CurrentPrice]:
    """Returns {} if cfg.home_market is unset, same "not configured yet, don't
    error" fallback pricing.system_cost_indices_for already uses for an unset
    system_id - avoids requesting Goonmetrics' ".../market/None/prices.json"."""
    if not cfg.home_market:
        return {}
    return {p.type_id: p for p in gm_client.current_prices(cfg.home_market)}


def jita_prices(gm_client: GoonmetricsClient) -> dict[int, CurrentPrice]:
    return {p.type_id: p for p in gm_client.current_prices(JITA_MARKET)}


def _candidate_prices(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
                       volume_m3: Optional[float], cfg: ProductionConfig) -> dict[str, float]:
    """Per-source landed unit price for `type_id`, for whichever sources
    actually have a sell order listed. Both are inflated by
    cfg.jita_buy_broker_fee (same buying character, same broker's-fee rate
    regardless of which market they buy in - confirmed against the in-game
    buy screen); Jita's is additionally inflated by haul cost since it still
    has to be moved to the home structure, while home's doesn't need hauling
    by definition."""
    candidates = {}
    home_quote = home.get(type_id)
    if home_quote and home_quote.sell > 0:
        candidates["C-J"] = home_quote.sell * (1 + cfg.jita_buy_broker_fee)
    jita_quote = jita.get(type_id)
    if jita_quote and jita_quote.sell > 0:
        candidates["Jita"] = jita_quote.sell * (1 + cfg.jita_buy_broker_fee) + cfg.haul_cost_per_m3 * (volume_m3 or 0)
    return candidates


def buy_source(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
                volume_m3: Optional[float] = None, cfg: ProductionConfig = PRODUCTION_CONFIG) -> Optional[str]:
    """Which market buy_price() sources `type_id` from - whichever of home
    ("C-J") or haul-adjusted Jita is actually cheaper, else whichever of the
    two has a sell order listed, else None (no sell order anywhere). Single
    source of truth for this decision - buy_price uses it internally so the
    two can never disagree."""
    candidates = _candidate_prices(type_id, home, jita, volume_m3, cfg)
    if not candidates:
        return None
    return min(candidates, key=candidates.get)


def buy_price(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
              volume_m3: Optional[float], cfg: ProductionConfig = PRODUCTION_CONFIG) -> Optional[float]:
    """Cheapest way to acquire one unit of `type_id` right now: home sell
    price, or Jita sell price plus haul cost, whichever is lower. None if
    neither market has a sell order."""
    candidates = _candidate_prices(type_id, home, jita, volume_m3, cfg)
    if not candidates:
        return None
    return min(candidates.values())


def system_cost_indices_for(esi_client: ESIClient, system_id: Optional[int]) -> dict[str, float]:
    """Manufacturing/reaction cost indices for `system_id`. Returns {} if
    `system_id` is None (job-cost modeling falls back to the flat ACTIVITY_MODS
    rate, not guessed, in that case - see engine.py)."""
    if system_id is None:
        return {}
    try:
        return esi_client.get_system_cost_indices(system_id, activities=("manufacturing", "reaction"))
    except Exception:  # noqa: BLE001 - best-effort; a transient ESI hiccup shouldn't block the whole plan
        return {}
