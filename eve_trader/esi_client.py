"""Thin wrapper around EVE Online's ESI API.

Covers market structure/order-book stats and stats, character orders/wallet
transactions/assets, character search, adjusted prices, and system cost
indices - the ESI surface both the Trading and Production tools need.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

import requests

from . import storage
from .auth import TokenManager
from .config import TRADING_CONFIG, TradingConfig

USER_AGENT = "eve-trader-python (contact: set EVE_CONTACT_EMAIL in .env)"

METALEVEL_ATTRIBUTE_ID = 633  # EVE SDE dogma attribute "metaLevel" (0=Tech I, 5=Tech II, ...)


class ESIError(RuntimeError):
    pass


def extract_meta_level(type_info: dict) -> Optional[int]:
    """Pulls the metaLevel dogma attribute out of a /universe/types/{id}/
    response, if present (unpublished/no-attribute types return None)."""
    for attr in type_info.get("dogma_attributes") or []:
        if attr.get("attribute_id") == METALEVEL_ATTRIBUTE_ID:
            try:
                return int(attr["value"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


@dataclass
class OrderStats:
    """Summary stats for one side (buy/sell) of an order book - a robust
    price percentile plus total listed volume, see _summarize_orders."""
    sell_percentile: Optional[float]
    sell_volume: float
    buy_percentile: Optional[float]
    buy_volume: float


class ESIClient:
    # Class-level (not per-instance): ESI's error limit is enforced per
    # calling IP by CCP, not per ESIClient object, and this app creates a
    # fresh ESIClient() at many call sites - an instance-level counter
    # wouldn't see requests made through a different instance. Confirmed real
    # gap: the old retry loop only *reacted* to a 420 after it already
    # happened (blind exponential backoff, never reading the budget ESI
    # actually tells you about) instead of proactively slowing down before
    # hitting zero - ESI's own best-practices doc says a well-behaved client
    # should watch X-Esi-Error-Limit-Remain and back off before it runs out,
    # since "once you reach the error limit, all your requests are
    # automatically discarded until the end of the time frame."
    _error_limit_lock = threading.Lock()
    _error_limit_remain: int = 100
    _error_limit_reset_at: float = 0.0  # unix timestamp the current window ends

    # Class-level (not per-instance), same reasoning as _error_limit_* above -
    # confirmed real gap (2026-08-16): these used to be set in __init__, but
    # production/engine.py's _PlanContext creates a *fresh* ESIClient() on
    # every plan_production/plan_asset_optimized call, so an instance-level
    # cache never actually survived past one call despite cache_seconds
    # implying it should - every "Compute Buy/Build List"/"Recompute" click
    # re-downloaded ESI's *entire* adjusted-prices catalog (every published
    # type) and *entire* system cost-indices catalog (every solar system)
    # from scratch, even seconds apart. Same fix goonmetrics_client.py's
    # current_prices got for the identical problem (a fresh GoonmetricsClient
    # per plan call), just via a class attribute (this codebase's own
    # existing pattern for cross-instance ESI state, see _error_limit_lock)
    # instead of a bare module-level dict, since ESIClient already has the
    # precedent.
    _prices_indices_lock = threading.Lock()
    _adjusted_prices_cache: Optional[dict[int, float]] = None
    _adjusted_prices_cache_at: float = 0.0
    _cost_indices_cache: Optional[dict[int, dict[str, float]]] = None
    _cost_indices_cache_at: float = 0.0

    def __init__(self, cfg: TradingConfig = TRADING_CONFIG, tokens: Optional[TokenManager] = None):
        self.cfg = cfg
        self.tokens = tokens or TokenManager()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    @classmethod
    def clear_price_caches(cls) -> None:
        """Forces the next get_adjusted_prices/get_system_cost_indices call
        (on *any* instance - these caches are class-wide) to re-fetch -
        exists for tests, same reason goonmetrics_client.clear_prices_cache
        does."""
        with cls._prices_indices_lock:
            cls._adjusted_prices_cache = None
            cls._adjusted_prices_cache_at = 0.0
            cls._cost_indices_cache = None
            cls._cost_indices_cache_at = 0.0

    # ------------------------------------------------------------- low level
    @classmethod
    def _await_error_budget(cls) -> None:
        """Proactively sleeps if the last-observed error-limit window is
        nearly exhausted, instead of waiting to get 420'd first."""
        with cls._error_limit_lock:
            remaining_window = cls._error_limit_reset_at - time.time()
            if cls._error_limit_remain <= 2 and remaining_window > 0:
                wait = remaining_window
            else:
                wait = 0.0
        if wait > 0:
            time.sleep(wait)

    @classmethod
    def _record_error_budget(cls, resp: requests.Response) -> None:
        remain = resp.headers.get("X-Esi-Error-Limit-Remain")
        reset = resp.headers.get("X-Esi-Error-Limit-Reset")
        if remain is None or reset is None:
            return
        with cls._error_limit_lock:
            cls._error_limit_remain = int(remain)
            cls._error_limit_reset_at = time.time() + int(reset)

    def _get_response(self, path_or_url: str, params: Optional[dict] = None,
                       auth_role: Optional[str] = None, retries: int = 3) -> requests.Response:
        """Shared retry/backoff core for both _get and _get_all_pages - returns
        the raw Response (status 200 guaranteed) so callers needing headers
        (X-Pages, for pagination) aren't stuck re-implementing retry logic
        themselves. Confirmed real bug: _get_all_pages used to do a bare
        self.session.get() with no retry at all, so a single transient
        502/503 or ESI error-limit (420) hit on any page - not just the
        first - aborted the *entire* paginated fetch (e.g. one character's
        whole asset sync) instead of retrying like a single-page _get call
        would."""
        url = path_or_url if path_or_url.startswith("http") else f"{self.cfg.esi_base}{path_or_url}"
        headers = {}
        if auth_role:
            headers.update(self.tokens.auth_header(auth_role))
        for attempt in range(1, retries + 1):
            self._await_error_budget()
            resp = self.session.get(url, params=params, headers=headers, timeout=30)
            self._record_error_budget(resp)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 420:  # ESI error-limited
                time.sleep(min(2 ** attempt, 20))
                continue
            if resp.status_code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(attempt * 1.5)
                continue
            raise ESIError(f"HTTP {resp.status_code} for {url}: {resp.text[:300]}")
        raise ESIError(f"Exhausted retries for {url}")

    def _get(self, path_or_url: str, params: Optional[dict] = None,
             auth_role: Optional[str] = None, retries: int = 3) -> Any:
        return self._get_response(path_or_url, params, auth_role, retries).json()

    def _post_response(self, path: str, json_body: Any, params: Optional[dict] = None,
                        retries: int = 3) -> requests.Response:
        """POST counterpart to _get_response - same retry/backoff behavior
        (420 error-limit, 500/502/503/504) and same ESIError on exhaustion.
        Confirmed real bug: _post_universe_ids/resolve_names used to do a bare
        self.session.post() with no retry at all and raised requests.HTTPError
        (not this codebase's ESIError) on failure - a transient error hit
        while resolving a solar system name (do_set_system) or a batch of
        industry-job installer ids (production/esi_sync.py) produced either an
        unhandled 500 (do_set_system, since api routers only catch
        ActionError) or a silently-dropped chunk (resolve_names used to
        `continue` past any non-200 with no retry and no error at all)."""
        url = f"{self.cfg.esi_base}{path}"
        for attempt in range(1, retries + 1):
            self._await_error_budget()
            resp = self.session.post(url, json=json_body, params=params, timeout=30)
            self._record_error_budget(resp)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 420:
                time.sleep(min(2 ** attempt, 20))
                continue
            if resp.status_code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(attempt * 1.5)
                continue
            raise ESIError(f"HTTP {resp.status_code} for {url}: {resp.text[:300]}")
        raise ESIError(f"Exhausted retries for {url}")

    def _get_all_pages(self, path: str, params: Optional[dict] = None, auth_role: Optional[str] = None,
                        max_workers: int = 6) -> list:
        """Confirmed real gap (2026-08-16): pages used to be fetched one at a
        time, strictly sequentially - purely network-latency-bound (each
        page is an independent request, nothing about page N depends on page
        N-1's *content*, only on knowing page 1's X-Pages header to know how
        many more exist). A corp with 9k+ assets (confirmed real account
        data) pages at ESI's standard 1000-items/page into ~10 sequential
        round-trips for that one call alone - during a full ESI sync this
        happens for assets/industry-jobs/blueprints, per character *and* per
        corp. Page 1 still has to be fetched alone first (it's the only way
        to learn X-Pages) - pages 2..N are then fetched concurrently, same
        ThreadPoolExecutor shape goonmetrics_client.py's price_history_
        chunked already uses for the equivalent "many independent chunks,
        latency-bound" situation. Results are reassembled in page order
        before returning, matching the old sequential call's ordering."""
        base_params = dict(params or {})
        first_params = dict(base_params, page=1)
        resp = self._get_response(path, first_params, auth_role)
        first_chunk = resp.json()
        if not first_chunk:
            return []
        total_pages = int(resp.headers.get("X-Pages", "1"))
        if total_pages <= 1:
            return first_chunk

        def _fetch_page(page: int) -> list:
            page_params = dict(base_params, page=page)
            return self._get_response(path, page_params, auth_role).json()

        pages: dict[int, list] = {1: first_chunk}
        # storage.with_current_tenant: ThreadPoolExecutor workers don't
        # inherit contextvars from the submitting thread - if auth_role's
        # token happens to be expired right now, _fetch_page's auth_header()
        # call would otherwise refresh it from inside a worker thread with no
        # ambient tenant set, 500ing with "no current tenant set".
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(storage.with_current_tenant(_fetch_page), page): page
                       for page in range(2, total_pages + 1)}
            for future in as_completed(futures):
                pages[futures[future]] = future.result()

        out: list = []
        for page in range(1, total_pages + 1):
            out.extend(pages[page])
        return out

    # -------------------------------------------------- market groups / types
    def list_market_group_ids(self) -> list[int]:
        return self._get("/markets/groups/", params={"datasource": "tranquility"})

    def get_market_group(self, group_id: int) -> dict:
        return self._get(f"/markets/groups/{group_id}/",
                          params={"datasource": "tranquility", "language": "en"})

    def get_type_info(self, type_id: int) -> dict:
        return self._get(f"/universe/types/{type_id}/",
                          params={"datasource": "tranquility", "language": "en"})

    def get_meta_level(self, type_id: int) -> Optional[int]:
        """Fetches just the metaLevel for a type (extra request - prefer
        extract_meta_level() on a get_type_info() response you already have,
        e.g. while walking market groups in candidate_discovery)."""
        return extract_meta_level(self.get_type_info(type_id))

    def get_packaged_volume(self, type_id: int) -> Optional[float]:
        """Packaged (repackaged/cargo) volume for `type_id`, mainly relevant
        for ships - not in Fuzzwork's SDE CSVs, only ESI exposes it per type,
        and only for types where it differs from the flight volume (absent
        otherwise, callers should fall back to the SDE `volume` field)."""
        info = self.get_type_info(type_id)
        value = info.get("packaged_volume")
        return float(value) if value is not None else None

    def corporation_structures(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Lists every Upwell structure the corp owns (name included directly
        in the response) - requires esi-corporations.read_structures.v1 and
        the Station_Manager role in that corporation, but crucially does NOT
        need the querying character to have personally docked at/visited any
        of them, unlike get_structure_name below. Prefer this as the primary
        structure-name-resolution path (see production/actions.py
        do_resolve_structure_name) since it resolves every corp-owned
        structure in one call instead of depending on one character's docking
        history per structure."""
        return self._get_all_pages(f"/corporations/{corporation_id}/structures/",
                                    params={"datasource": "tranquility"}, auth_role=auth_role)

    def get_structure_name(self, structure_id: int, auth_role: str) -> dict:
        """Resolves a player-owned Upwell structure's info (never in the SDE,
        unlike NPC stations) - requires esi-universe.read_structures.v1 and a
        character that can actually "see" the structure (has docking rights/
        has been there), so a 403 from a character without access isn't
        necessarily fatal - caller should retry with a different producer
        character before giving up (see production/actions.py
        do_resolve_structure_name, same fallback pattern esi_sync.py uses for
        corp-level calls). Returns the raw ESI dict (at least `name`, usually
        also `solar_system_id` - GitHub issue #12 needs the latter too, not
        just the name this used to return alone) rather than unpacking it
        here, so a caller needing more of the response later doesn't need
        another round trip."""
        return self._get(f"/universe/structures/{structure_id}/",
                          params={"datasource": "tranquility"}, auth_role=auth_role)

    # -------------------------------------------------------------- markets
    def region_order_stats(self, region_id: int, type_id: int) -> OrderStats:
        """Regional order-book stats for one type_id.
        Uses _get_all_pages defensively: /markets/{region_id}/orders/ is a
        genuinely paginated endpoint per the ESI spec even with a type_id
        filter applied - a single type_id in a normal region is very unlikely
        to exceed one page in practice, but nothing in the spec guarantees
        that, and every other pagination-capable endpoint in this file
        already gets the same treatment."""
        orders = self._get_all_pages(f"/markets/{region_id}/orders/",
                                      params={"datasource": "tranquility", "type_id": type_id, "order_type": "all"})
        return _summarize_orders(orders)

    def region_order_stats_bulk(self, region_id: int, type_ids: list[int], max_workers: int = 10) -> dict[int, OrderStats]:
        """Same as region_order_stats, but for many type_ids concurrently. ESI
        has no multi-type_id batch endpoint for regional orders (each type_id
        needs its own call), but the calls are independent and each already
        server-side filtered (small payload) - running them on a small thread
        pool cuts wall-clock time roughly by max_workers vs. one-by-one, since
        the cost here is almost entirely network round-trip latency, not
        local computation. A failed lookup for one type_id doesn't affect the
        others (falls back to an empty OrderStats)."""
        results: dict[int, OrderStats] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self.region_order_stats, region_id, tid): tid for tid in type_ids}
            for future in as_completed(futures):
                tid = futures[future]
                try:
                    results[tid] = future.result()
                except ESIError:
                    results[tid] = OrderStats(None, 0.0, None, 0.0)
        return results

    def structure_order_stats(self, structure_id: int, type_id: int, auth_role: str) -> OrderStats:
        """Player-structure order-book stats for one type_id.
        Requires a token with esi-markets.structure_markets.v1 for a character
        docked at / with access to that structure. Prefer
        structure_order_stats_bulk() when you need this for many type_ids -
        ESI's structure-orders endpoint has no type_id filter, so this method
        downloads the *entire* structure order book on every call; calling it
        once per shortlist item repeatedly re-downloads the same book from
        scratch (this was the single biggest cause of a slow shortlist
        refresh - see structure_order_stats_bulk)."""
        orders = self._get_all_pages(f"/markets/structures/{structure_id}/",
                                      params={"datasource": "tranquility"}, auth_role=auth_role)
        orders = [o for o in orders if o.get("type_id") == type_id]
        return _summarize_orders(orders)

    def structure_orders_raw(self, structure_id: int, auth_role: str) -> list[dict]:
        """The structure's full, unsummarized order book (every order, every
        type_id) - shared raw fetch behind structure_order_stats_bulk and
        own_orders.check_undercut, both of which need the whole book anyway
        (ESI's /markets/structures/{id}/ endpoint has no type_id filter) and
        would otherwise each re-download it separately."""
        return self._get_all_pages(f"/markets/structures/{structure_id}/",
                                    params={"datasource": "tranquility"}, auth_role=auth_role)

    def structure_order_stats_bulk(self, structure_id: int, type_ids: list[int],
                                    auth_role: str) -> dict[int, OrderStats]:
        """Downloads the structure's full order book exactly once (ESI's
        /markets/structures/{id}/ endpoint has no type_id filter, unlike
        regional orders), then groups locally per type_id - replaces what used
        to be one full-book re-download *per shortlist item*."""
        orders = self.structure_orders_raw(structure_id, auth_role)
        by_type: dict[int, list[dict]] = {}
        for o in orders:
            by_type.setdefault(o.get("type_id"), []).append(o)
        return {tid: _summarize_orders(by_type.get(tid, [])) for tid in type_ids}

    def region_market_history(self, region_id: int, type_id: int) -> list[dict]:
        """Official ESI daily history (used as a fallback / cross-check to Goonmetrics)."""
        return self._get(f"/markets/{region_id}/history/",
                          params={"datasource": "tranquility", "type_id": type_id})

    # ------------------------------------------------------------ industry
    def get_adjusted_prices(self, type_ids: Optional[list[int]] = None,
                             cache_seconds: float = 3600) -> dict[int, float]:
        """ESI has no per-type lookup, it returns every published type's
        adjusted price in one call - so fetch the full list (cached
        class-wide for `cache_seconds`, see _adjusted_prices_cache's own
        comment) and filter locally.
        """
        with ESIClient._prices_indices_lock:
            now = time.time()
            if (ESIClient._adjusted_prices_cache is None
                    or (now - ESIClient._adjusted_prices_cache_at) > cache_seconds):
                rows = self._get("/markets/prices/", params={"datasource": "tranquility"})
                ESIClient._adjusted_prices_cache = {
                    row["type_id"]: row["adjusted_price"] for row in rows if "adjusted_price" in row
                }
                ESIClient._adjusted_prices_cache_at = now
            cache = ESIClient._adjusted_prices_cache
        if type_ids is None:
            return dict(cache)
        return {t: cache.get(t, 0) for t in type_ids}

    def get_system_cost_indices(self, system_id: int,
                                 activities: tuple = ("manufacturing", "reaction", "invention"),
                                 cache_seconds: float = 21600) -> dict[str, float]:
        """ESI returns every solar system's cost indices in one call - fetch
        once (cached class-wide for `cache_seconds`, see
        _adjusted_prices_cache's own comment), then look up `system_id` and
        filter to `activities`. Missing activities are omitted.
        """
        with ESIClient._prices_indices_lock:
            now = time.time()
            if ESIClient._cost_indices_cache is None or (now - ESIClient._cost_indices_cache_at) > cache_seconds:
                rows = self._get("/industry/systems/", params={"datasource": "tranquility"})
                ESIClient._cost_indices_cache = {
                    row["solar_system_id"]: {
                        idx["activity"]: idx["cost_index"] for idx in row.get("cost_indices", [])
                    }
                    for row in rows
                }
                ESIClient._cost_indices_cache_at = now
            system_indices = ESIClient._cost_indices_cache.get(system_id)
        if system_indices is None:
            raise ESIError(f"No cost indices found for solar system {system_id}.")
        return {a: system_indices[a] for a in activities if a in system_indices}

    # ------------------------------------------------------------ characters
    def character_search(self, name: str) -> Optional[int]:
        """Resolves a character name to its ID via a strict (exact, case-
        insensitive) match against /universe/ids/."""
        result = self._post_universe_ids([name])
        chars = result.get("characters") or []
        for c in chars:
            if c["name"].lower() == name.lower():
                return c["id"]
        return chars[0]["id"] if chars else None

    def resolve_system_id(self, name: str) -> Optional[int]:
        """Resolves a solar system name (e.g. "Jita", "Amarr") to its ID via
        the same /universe/ids/ endpoint character_search uses. Solar system
        names are unique and never change, so callers are expected to resolve
        once (e.g. when a Settings form is saved) and store the ID, not
        re-resolve on every calculation."""
        result = self._post_universe_ids([name])
        systems = result.get("systems") or []
        for s in systems:
            if s["name"].lower() == name.strip().lower():
                return s["id"]
        return None

    def _post_universe_ids(self, names: list[str]) -> dict:
        return self._post_response("/universe/ids/", names, params={"datasource": "tranquility"}).json()

    def character_orders(self, character_id: int, auth_role: str) -> list[dict]:
        """This character's open market orders (buy and sell)."""
        return self._get(f"/characters/{character_id}/orders/",
                          params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_orders(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-markets.read_corporation_orders.v1 + the character
        holding the Accountant or Trader role in that corporation. Stock
        sitting in a corp hangar (see corporation_assets) is often listed for
        sale via a *corp* order funded by the corp wallet, not a personal one
        - without this, do_unlisted_stock/sync_esi would only ever see
        personal orders and wrongly flag corp-hangar stock as unlisted even
        when it's actually for sale (confirmed bug: a real corp sell order
        for stock physically in the corp hangar was invisible to both)."""
        return self._get_all_pages(f"/corporations/{corporation_id}/orders/",
                                    params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_public_info(self, character_id: int) -> dict:
        """Public endpoint, no auth - used to resolve corporation_id for corp-level calls."""
        return self._get(f"/characters/{character_id}/", params={"datasource": "tranquility"})

    def corporation_public_info(self, corporation_id: int) -> dict:
        """Public endpoint, no auth - used to resolve a corp's name for display."""
        return self._get(f"/corporations/{corporation_id}/", params={"datasource": "tranquility"})

    # ------------------------------------------------- assets / industry / BPs
    def character_assets(self, character_id: int, auth_role: str) -> list[dict]:
        """Requires esi-assets.read_assets.v1."""
        return self._get_all_pages(f"/characters/{character_id}/assets/",
                                    params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_assets(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-assets.read_corporation_assets.v1 + the character holding
        the Director role in that corporation."""
        return self._get_all_pages(f"/corporations/{corporation_id}/assets/",
                                    params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_industry_jobs(self, character_id: int, auth_role: str) -> list[dict]:
        """Requires esi-industry.read_character_jobs.v1. Confirmed against the
        ESI spec: unlike the corporation variant below, this endpoint has no
        `page` param / X-Pages header at all - plain _get (not _get_all_pages,
        which would still work by accident since it defaults to one page, but
        implies pagination capability this endpoint doesn't have)."""
        return self._get(f"/characters/{character_id}/industry/jobs/",
                          params={"datasource": "tranquility", "include_completed": "false"}, auth_role=auth_role)

    def corporation_industry_jobs(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-industry.read_corporation_jobs.v1 + Director role."""
        return self._get_all_pages(f"/corporations/{corporation_id}/industry/jobs/",
                                    params={"datasource": "tranquility", "include_completed": "false"},
                                    auth_role=auth_role)

    def character_blueprints(self, character_id: int, auth_role: str) -> list[dict]:
        """Requires esi-characters.read_blueprints.v1."""
        return self._get_all_pages(f"/characters/{character_id}/blueprints/",
                                    params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_blueprints(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-corporations.read_blueprints.v1 + Director role."""
        return self._get_all_pages(f"/corporations/{corporation_id}/blueprints/",
                                    params={"datasource": "tranquility"}, auth_role=auth_role)

    # ------------------------------------------------------------- contracts
    def character_contracts(self, character_id: int, auth_role: str) -> list[dict]:
        """Requires esi-contracts.read_character_contracts.v1. Every contract
        the character is issuer, acceptor, or assignee of, only up to 30
        days old or still `in_progress`/`outstanding` (ESI's own retention
        rule, not filtered here) - doctrine/esi_sync.py narrows this further
        to item_exchange + outstanding + this app's own structure before
        ever fetching a single contract's items."""
        return self._get_all_pages(f"/characters/{character_id}/contracts/",
                                    params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_contracts(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-contracts.read_corporation_contracts.v1. Same
        retention/shape as character_contracts, corp-wide."""
        return self._get_all_pages(f"/corporations/{corporation_id}/contracts/",
                                    params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_contract_items(self, character_id: int, contract_id: int, auth_role: str) -> list[dict]:
        """Requires esi-contracts.read_character_contracts.v1 (same scope as
        character_contracts, not a separate one). A contract's item list
        never changes after creation (ESI guarantee) - doctrine/esi_sync.py
        relies on this to only re-fetch items for contracts it hasn't
        already stored."""
        return self._get(f"/characters/{character_id}/contracts/{contract_id}/items/",
                          params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_contract_items(self, corporation_id: int, contract_id: int, auth_role: str) -> list[dict]:
        """Requires esi-contracts.read_corporation_contracts.v1."""
        return self._get(f"/corporations/{corporation_id}/contracts/{contract_id}/items/",
                          params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_wallet_transactions(self, character_id: int, auth_role: str,
                                       from_id: Optional[int] = None) -> list[dict]:
        """This character's wallet transaction history.
        Returns up to 2500 transactions (ESI's fixed per-call cap for this
        endpoint), most recent first. This endpoint uses cursor pagination via
        `from_id` (pass the oldest transaction_id from a previous call to page
        further back in time) rather than the page/X-Pages scheme
        _get_all_pages handles, so it can't use that helper - trade_reconciliation.
        fetch_recent_transactions loops this until it's covered the requested
        lookback window."""
        params = {"datasource": "tranquility"}
        if from_id is not None:
            params["from_id"] = from_id
        return self._get(f"/characters/{character_id}/wallet/transactions/",
                          params=params, auth_role=auth_role)

    def character_skills(self, character_id: int, auth_role: str) -> dict:
        """Requires esi-skills.read_skills.v1. Returns {"skills": [{"skill_id",
        "active_skill_level", ...}], "total_sp", ...} - used to derive industry
        job-slot counts (see production/constants.py job_slots_from_skills)."""
        return self._get(f"/characters/{character_id}/skills/",
                          params={"datasource": "tranquility"}, auth_role=auth_role)

    def resolve_names(self, ids: list[int]) -> dict[int, str]:
        """Batch id->name resolution via POST /universe/names/ (public, no
        auth, up to 1000 ids/call) - used to resolve industry job installer_id
        to a display name. Unknown/invalid ids are silently omitted (ESI
        itself omits them from the response body, not this method's doing).
        A transient error retries (via _post_response) instead of the old
        bare "any non-200 -> silently drop this whole chunk of up to 1000
        names, no retry, no error" behavior - a real error-limit hit while
        resolving a large batch of industry-job installer ids used to
        silently produce permanent str(installer_id) fallbacks with no
        indication it was ever fixable."""
        if not ids:
            return {}
        out: dict[int, str] = {}
        unique_ids = list(dict.fromkeys(ids))
        for i in range(0, len(unique_ids), 1000):
            chunk = unique_ids[i:i + 1000]
            resp = self._post_response("/universe/names/", chunk, params={"datasource": "tranquility"})
            for entry in resp.json():
                out[entry["id"]] = entry["name"]
        return out


def _summarize_orders(orders: list[dict]) -> OrderStats:
    """percentile = 5th percentile sell price / 95th percentile buy price (a
    robust proxy for "realistic" best price, ignoring outlier orders), volume
    = sum of volume_remain across all orders on that side.
    """
    sells = sorted((o["price"] for o in orders if not o["is_buy_order"]))
    buys = sorted((o["price"] for o in orders if o["is_buy_order"]), reverse=True)
    sell_vol = sum(o["volume_remain"] for o in orders if not o["is_buy_order"])
    buy_vol = sum(o["volume_remain"] for o in orders if o["is_buy_order"])
    return OrderStats(
        sell_percentile=_percentile(sells, 0.05),
        sell_volume=sell_vol,
        buy_percentile=_percentile(buys, 0.05),
        buy_volume=buy_vol,
    )


def _percentile(sorted_values: list[float], pct: float) -> Optional[float]:
    """`sorted_values` must already be sorted ascending (both call sites in
    _summarize_orders sort before calling) - not re-sorted here since both
    callers already have the sort order they need for their own side (sells
    ascending for a low percentile, buys descending so the same `pct` picks
    from the top). Nearest-rank, not interpolated: index = pct fraction into
    the list, clamped to the last element."""
    if not sorted_values:
        return None
    idx = min(int(len(sorted_values) * pct), len(sorted_values) - 1)
    return sorted_values[idx]
