"""Client for the Goonmetrics price-history API - used by history_backtest.py's
candidate discovery/scoring (find_new_import_candidates).

The endpoint returns XML like:
    <evec_api><result><rowset name="history">
      <type id="...">
        <history date="..." avgPrice="..." maxPrice="..." minPrice="..."
                 movement="..." numOrders="..."/>
      </type>
    </rowset></result></evec_api>
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable, Optional
from xml.etree import ElementTree as ET

import requests

from .config import TRADING_CONFIG, TradingConfig

log = logging.getLogger(__name__)

USER_AGENT = "eve-trader-python"

APPRAISE_BASE = "https://appraise.gnf.lt"

# Module-level (not per-instance) TTL cache for current_prices, keyed by
# `market` - a GoonmetricsClient is instantiated fresh on every _PlanContext
# (production/engine.py) and every plan_production/plan_asset_optimized
# call, so an instance-level cache (the pattern esi_client.py uses for its
# own ESI caches) would never actually hit; this has to live at module scope
# to survive across those fresh instantiations. Confirmed real cost via
# cProfile: two calls (home + Jita) took ~4.3s combined out of a ~14s
# plan_asset_optimized run - this payload is a multi-megabyte JSON dump (see
# current_prices' own docstring), not something worth re-downloading twice
# in the same minute just because the user clicked "Compute Buy/Build List"
# and then "Recompute" on the Asset-Optimized tab. 60s (short relative to
# discover_build_candidates' 600s _DISCOVER_CACHE_TTL in production/engine.py)
# keeps this fresh enough for actual trading decisions while still
# eliminating that extremely common back-to-back double-fetch. Locked per
# market (see _lock_for_market), not with one lock shared across every
# market - held for the whole fetch (same "two callers racing on a cold
# cache should serialize, not both pay the full multi-MB download" reasoning
# _DISCOVER_CACHE_TTL's lock in production/engine.py uses), so a *single*
# shared lock would otherwise make an in-flight Jita fetch block a concurrent
# home-market fetch too, even though they don't share a cache entry at all.
_PRICES_CACHE_TTL = 60  # seconds
_prices_cache: dict[str, list["CurrentPrice"]] = {}
_prices_cache_at: dict[str, float] = {}
_prices_locks: dict[str, threading.Lock] = {}
_prices_locks_guard = threading.Lock()  # protects creation of a new per-market lock only, never held during a fetch


def _lock_for_market(market: str) -> threading.Lock:
    with _prices_locks_guard:
        if market not in _prices_locks:
            _prices_locks[market] = threading.Lock()
        return _prices_locks[market]


def clear_prices_cache() -> None:
    """Forces the next current_prices call (for every market) to re-fetch -
    exists for tests that hit the real cache path (none do yet - every
    current test either monkeypatches current_prices itself, wholesale, or
    never reaches it via a mocked _PlanContext) rather than relying on
    module-level state resetting itself between them."""
    with _prices_locks_guard:
        _prices_cache.clear()
        _prices_cache_at.clear()


@dataclass(frozen=True)
class HistoryPoint:
    region_id: int
    type_id: int
    date: str
    min_price: float
    max_price: float
    avg_price: float
    movement: float
    num_orders: int


@dataclass(frozen=True)
class CurrentPrice:
    """Current best buy/sell for one type in a market, as served by appraise.gnf.lt."""
    type_id: int
    updated: str
    buy: float
    sell: float


class GoonmetricsClient:
    def __init__(self, cfg: TradingConfig = TRADING_CONFIG):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def current_prices(self, market: str) -> list[CurrentPrice]:
        """Current best buy (max)/sell (min) for every type in `market` (e.g.
        "jita", or a player structure's market slug), sorted by type_id.

        Confirmed real-world: this payload is a multi-megabyte JSON dump (the
        full market, ~11MB for Jita) that can legitimately take over 30s to
        fully download from this third-party, no-SLA server (measured 32.4s
        for the home market on an otherwise-idle connection) - a bare
        timeout=30 with no retry intermittently killed every caller
        (stock_value, plan_production, market-status) on nothing more than
        normal response-time variance, not an actual outage. Retries a couple
        of times with backoff before giving up, same shape as esi_client's
        _get_response.

        Cached module-wide for _PRICES_CACHE_TTL seconds (see that constant's
        comment) - a fresh download every call was measured costing ~4.3s
        combined (home + Jita) out of a ~14s plan_asset_optimized run, purely
        from back-to-back re-fetches of the same still-fresh data.
        """
        with _lock_for_market(market):
            cached_at = _prices_cache_at.get(market, 0.0)
            if market in _prices_cache and (time.time() - cached_at) < _PRICES_CACHE_TTL:
                return list(_prices_cache[market])

            url = f"{APPRAISE_BASE}/market/{market}/prices.json"
            last_exc: Optional[requests.RequestException] = None
            for attempt in range(1, 4):
                try:
                    resp = self.session.get(url, timeout=60)
                    resp.raise_for_status()
                    break
                except requests.RequestException as e:
                    last_exc = e
                    if attempt < 3:
                        time.sleep(attempt * 2)
            else:
                raise last_exc
            results = resp.json()
            prices = [
                CurrentPrice(
                    type_id=item["typeID"],
                    updated=item["prices"]["updated"],
                    buy=item["prices"]["buy"]["max"],
                    sell=item["prices"]["sell"]["min"],
                )
                for item in results
            ]
            prices.sort(key=lambda p: p.type_id)
            _prices_cache[market] = prices
            _prices_cache_at[market] = time.time()
            return list(prices)

    def price_history(self, region_id: int, type_ids: Iterable[int]) -> list[HistoryPoint]:
        """Never raises on a Goonmetrics failure - silently falls back to
        the slower per-type_id ESI history endpoint instead (see except
        clause below), so callers don't need their own fallback handling."""
        type_ids = list(type_ids)
        ids = ",".join(str(t) for t in type_ids)
        url = f"{self.cfg.goonmetrics_history_base}?region_id={region_id}&type_id={ids}"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            # Goonmetrics (appraise.gnf.lt) is a third-party community API with
            # no SLA - fall back to ESI's own (slower, one-call-per-type_id,
            # but always-available) daily history rather than letting a
            # Goonmetrics outage take down candidate discovery entirely.
            log.warning("Goonmetrics price history unavailable (%s) - falling back to ESI per-type history.", e)
            return self._esi_price_history_fallback(region_id, type_ids)
        return _parse_history_xml(resp.text, region_id)

    def _esi_price_history_fallback(self, region_id: int, type_ids: list[int]) -> list[HistoryPoint]:
        """Refetches via ESI's /markets/{region_id}/history/ (esi_client.py's
        region_market_history), one call per type_id since ESI has no batch
        endpoint for this. Maps ESI's schema onto HistoryPoint: `movement` is
        ESI's own `volume` field (units traded that day, not an ISK value -
        confirmed live 2026-07-15 against ESI's own history endpoint, see
        production/engine.py's discover_build_candidates docstring), so this
        passes it straight through rather than multiplying by average_price -
        that multiplication used to happen here and silently mixed
        unit-count and ISK-value figures in the same HistoryPoint.movement
        field depending on whether Goonmetrics or this fallback served the
        request."""
        from .esi_client import ESIClient, ESIError  # local import: avoids a hard esi_client<->goonmetrics_client coupling for callers that never hit this fallback

        esi = ESIClient(self.cfg)
        points: list[HistoryPoint] = []
        for type_id in type_ids:
            try:
                rows = esi.region_market_history(region_id, type_id)
            except ESIError as e:
                log.warning("ESI price history fallback also failed for type_id %d (%s) - skipping it.", type_id, e)
                continue
            for r in rows:
                points.append(HistoryPoint(
                    region_id=region_id, type_id=type_id, date=r["date"],
                    min_price=r["lowest"], max_price=r["highest"], avg_price=r["average"],
                    movement=r["volume"], num_orders=r["order_count"],
                ))
        return points

    def price_history_chunked(self, region_id: int, type_ids: list[int],
                               chunk_size: int | None = None, max_workers: int = 6) -> list[HistoryPoint]:
        """Chunks are independent requests (each already batches chunk_size
        type_ids into one call) - fetching them concurrently cuts wall-clock
        time roughly by max_workers vs. one-by-one, since this is
        network-latency-bound, not local-computation-bound.

        Each chunk is isolated in its own try/except (on top of price_history's
        own Goonmetrics->ESI fallback for plain request failures) so a single
        chunk hitting something neither of those handles - a malformed
        response, a parsing bug, anything unexpected - just loses that one
        chunk's history points instead of aborting the whole call and
        everything after it (matters most for a full, non-safe-mode candidate
        search spanning many chunks over several minutes)."""
        chunk_size = chunk_size or self.cfg.chunk_size
        chunks = [type_ids[i:i + chunk_size] for i in range(0, len(type_ids), chunk_size)]
        if not chunks:
            return []

        def _fetch(chunk: list[int]) -> list[HistoryPoint]:
            try:
                return self.price_history(region_id, chunk)
            except Exception:  # noqa: BLE001 - one bad chunk must not lose the rest
                log.exception("Price history chunk failed for region %d (%d ids) - skipping it.",
                              region_id, len(chunk))
                return []

        out: list[HistoryPoint] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for points in pool.map(_fetch, chunks):
                out.extend(points)
        return out


def _parse_history_xml(xml_text: str, region_id: int) -> list[HistoryPoint]:
    root = ET.fromstring(xml_text)
    points: list[HistoryPoint] = []
    for type_el in root.iter("type"):
        type_id = int(type_el.attrib["id"])
        for hist_el in type_el.findall("history"):
            points.append(HistoryPoint(
                region_id=region_id,
                type_id=type_id,
                date=hist_el.attrib["date"],
                min_price=float(hist_el.attrib["minPrice"]),
                max_price=float(hist_el.attrib["maxPrice"]),
                avg_price=float(hist_el.attrib["avgPrice"]),
                movement=float(hist_el.attrib["movement"]),
                num_orders=int(hist_el.attrib["numOrders"]),
            ))
    return points
