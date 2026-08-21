"""Doctrine tool orchestration - the only doctrine/*.py module that touches
storage.py directly (Phase 2 A.1: parser.py/validation.py stay pure).
Contract<->Fitting matching, status/ampel aggregation, and Stockpile Soll/Ist
all live here, on top of storage.py's plain-tuple reads and validation.py's
pure scoring/deviation functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import storage
from ..config import TRADING_CONFIG
from ..esi_client import ESIClient, ESIError, OrderStats
from . import parser, validation
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import AMPEL_GRAY, EXACT_SECTIONS
from .models import (
    AggregatedStockpileRow, ContractItemRow, ContractRow, DeviationRow, Doctrine, DoctrineStatus, Fitting,
    FittingItem, FittingStatus, ParsedFitting, ParsedIssue, ParsedItem, ParseIssue, ShoppingListRow, StockpileRow,
)
from .parser import FittingParseError, ResolvedType

# Deliberate, confirmed exception to constants.py's own "no imports from
# eve_trader.production" policy (see that module's docstring) - the
# Shopping List's Build cost (shopping_list_rows below) reuses Production's
# real build-cost engine directly rather than a second, possibly-divergent
# estimate, so a user's owned-BPO/ME/TE/structure config gives the exact
# same number here as it would in the Production tool itself. Only pure,
# stateless price/cost computation is imported - no shared mutable state or
# tables, so this doesn't reintroduce the kind of coupling that policy was
# actually protecting against.
from ..production.config import PRODUCTION_CONFIG
from ..production import pricing as production_pricing
from ..production.engine import _PlanContext, _haul_volume, unit_cost_detail


# ---------------------------------------------------------------- parsing entry point
def _resolve_name(name: str) -> Optional[ResolvedType]:
    row = storage.resolve_sde_type_by_name(name)
    if row is None:
        return None
    type_id, group_id, category_id, meta_group_id, meta_level, type_name = row
    return ResolvedType(type_id=type_id, group_id=group_id, category_id=category_id,
                         meta_group_id=meta_group_id, meta_level=meta_level, type_name=type_name)


def _resolve_slot(type_id: int) -> Optional[str]:
    return storage.get_type_slot(type_id)


def parse_fitting_text(raw_eft: str) -> ParsedFitting:
    """The one place doctrine/parser.py's storage-free resolvers get wired
    up to real SDE data - see parser.parse_fitting's own docstring for why
    the parser itself never imports storage.py."""
    candidates = storage.list_hull_type_names()
    return parser.parse_fitting(raw_eft, _resolve_name, _resolve_slot, hull_name_candidates=candidates)


def parse_bay_items_text(text: str, slot_section: str,
                          line_no_start: int = 1) -> tuple[list[ParsedItem], list[ParsedIssue]]:
    """Same real-SDE wiring as parse_fitting_text above, for parser.
    parse_bay_items (GitHub issue #18 - Fuel Bay / Ship Maintenance Bay
    content lists, entered separately from the main EFT paste since that
    format has no syntax for either)."""
    items, issues = parser.parse_bay_items(text, _resolve_name, slot_section, line_no_start=line_no_start)
    return items, issues


# ---------------------------------------------------------------- row <-> dataclass mapping
def fitting_from_row(row: tuple) -> Fitting:
    (fitting_id, doctrine_id, name, variant_label, hull_type_id, raw_eft, contract_target,
     stockpile_target, cargo_tolerance_pct, active, created_at, updated_at, fuel_bay_text,
     ship_maintenance_bay_text) = row
    return Fitting(fitting_id=str(fitting_id), doctrine_id=str(doctrine_id), name=name,
                    variant_label=variant_label, hull_type_id=hull_type_id, raw_eft=raw_eft,
                    contract_target=contract_target, stockpile_target=stockpile_target,
                    cargo_tolerance_pct=cargo_tolerance_pct, active=active,
                    created_at=str(created_at) if created_at else None,
                    updated_at=str(updated_at) if updated_at else None,
                    fuel_bay_text=fuel_bay_text, ship_maintenance_bay_text=ship_maintenance_bay_text)


def doctrine_from_row(row: tuple) -> Doctrine:
    doctrine_id, name, description, active, created_at = row
    return Doctrine(doctrine_id=str(doctrine_id), name=name, description=description, active=active,
                     created_at=str(created_at) if created_at else None)


def items_from_rows(fitting_id: str, rows: list[tuple]) -> list[FittingItem]:
    return [FittingItem(fitting_id=fitting_id, line_no=line_no, slot_section=slot_section,
                         type_id=type_id, quantity=quantity, is_offline=is_offline)
            for line_no, slot_section, type_id, quantity, is_offline in rows]


def load_fitting_with_items(fitting_id: str) -> tuple[Fitting, list[FittingItem]]:
    row = storage.get_fitting(fitting_id)
    if row is None:
        raise LookupError(f"Fitting {fitting_id} not found.")
    fitting = fitting_from_row(row)
    items = items_from_rows(fitting_id, storage.load_fitting_items(fitting_id))
    return fitting, items


def effective_cargo_tolerance(fitting: Fitting, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> float:
    return fitting.cargo_tolerance_pct if fitting.cargo_tolerance_pct is not None else cfg.cargo_tolerance_pct


# ---------------------------------------------------------------- matching candidates
@dataclass
class _Candidate:
    fitting: Fitting
    exact_soll: dict[int, float]
    consume_soll: dict[int, float]
    items: list[FittingItem]


def load_match_candidates() -> list[_Candidate]:
    """Every active fitting (Phase 3 B.5 step 1's candidate pool), in
    doctrine-then-fitting creation order - the same order Phase 3 C.2's
    stockpile allocation treats as priority, and the fallback tiebreak for
    Phase 3 B.5 step 3 when neither the title hint nor a strict score
    difference decides a match."""
    candidates = []
    for row in storage.list_active_fittings():
        fitting = fitting_from_row(row)
        items = items_from_rows(fitting.fitting_id, storage.load_fitting_items(fitting.fitting_id))
        exact_soll, consume_soll = validation.build_contract_soll(items)
        candidates.append(_Candidate(fitting=fitting, exact_soll=exact_soll, consume_soll=consume_soll, items=items))
    return candidates


#: Internal-only status sentinel - never added to constants.VALIDATION_STATUSES
#: and never persisted (esi_sync.sync_contracts drops any contract that gets
#: this status before it ever reaches storage.replace_doctrine_sync_snapshot,
#: see that function's own docstring). Distinct from the ordinary "unmatched"
#: status (hull present, but score below threshold - a genuine near-miss
#: worth keeping visible) - this one means no candidate fitting's hull was
#: found in the contract at all, i.e. it isn't a Doctrine ship sale.
NO_HULL_MATCH = "no_relevant_hull"


def match_and_validate_contract(contract_id: int, contract_title: Optional[str],
                                 contract_items: list[ContractItemRow], candidates: list[_Candidate],
                                 cfg: DoctrineConfig = DOCTRINE_CONFIG) -> tuple[Optional[str], float, list[DeviationRow], str]:
    """Phase 3 spec B.5 (matching) + B.4/B.6 (deviations) + B.7 (status),
    combined into the one call esi_sync.py needs per contract. Returns
    (matched_fitting_id, match_score, deviations, validation_status) -
    validation_status can be NO_HULL_MATCH (see its own docstring), never
    persisted as such."""
    scored: list[tuple[_Candidate, float]] = []
    for c in candidates:
        if not validation.hull_gate_satisfied(contract_items, c.fitting.hull_type_id):
            continue
        ist = validation.build_contract_ist(contract_items, c.fitting.hull_type_id)
        score = validation.match_score(c.exact_soll, c.consume_soll, ist)
        scored.append((c, score))

    if not scored:
        return None, 0.0, [], NO_HULL_MATCH

    max_score = max(s for _c, s in scored)
    if not validation.clears_match_threshold(max_score):
        return None, max_score, [], "unmatched"

    tied = [c for c, s in scored if s == max_score]
    winner = tied[0]
    if len(tied) > 1 and contract_title:
        title_matches = [c for c in tied if c.fitting.name.lower() in contract_title.lower()]
        if len(title_matches) == 1:
            winner = title_matches[0]

    tolerance = effective_cargo_tolerance(winner.fitting, cfg)
    deviations = validation.compute_deviations(contract_id, winner.exact_soll, winner.consume_soll,
                                                contract_items, winner.fitting.hull_type_id,
                                                tolerance, cfg.strict_extras)
    deviations = validation.pair_wrong_variants(deviations, _group_id_of, _slot_of)
    status = validation.contract_status(deviations, matched=True)
    return winner.fitting.fitting_id, max_score, deviations, status


def _group_id_of(type_id: int) -> Optional[int]:
    row = storage.get_sde_type(type_id)
    return row[1] if row else None


def _slot_of(type_id: int) -> Optional[str]:
    return storage.get_type_slot(type_id)


# ---------------------------------------------------------------- stockpile
def stockpile_rows_for_doctrine(doctrine_id: Optional[str] = None,
                                 cfg: DoctrineConfig = DOCTRINE_CONFIG) -> tuple[list[StockpileRow], bool]:
    """Phase 3 spec C - returns (rows, assets_available). Only ever computed
    live (never persisted, Phase 2 B.2's own note on StockpileRow)."""
    assets_available = storage.has_any_doctrine_synced_assets()
    location_id = cfg.effective_stockpile_location_id

    candidates = load_match_candidates()
    if doctrine_id is not None:
        candidates = [c for c in candidates if c.fitting.doctrine_id == doctrine_id]
    if not candidates or not assets_available:
        return [], assets_available

    # {doctrine_id: name} - used below so each row carries its doctrine's own
    # name (not a fitting's, which Stockpile.tsx used as a stand-in for the
    # doctrine section heading until this was fixed - GitHub issue #1).
    doctrine_name_by_id = {str(row[0]): row[1] for row in storage.list_doctrines()}

    ordered_soll: list[tuple[str, dict[int, tuple[float, str]]]] = []
    for c in candidates:
        # GitHub issue #36: same valid-contract count fitting_status computes
        # (contract_ampel's own "valid" count) - a fitting still short of its
        # contract_target needs the materials for those extra contracts too,
        # not just its separate stockpile_target buffer.
        contracts = contract_rows_from_db(storage.list_doctrine_contracts(fitting_id=c.fitting.fitting_id))
        valid_contracts = sum(1 for ct in contracts if ct.validation_status == "valid" and ct.status != "expired")
        soll = validation.build_stockpile_soll(c.items, c.fitting.hull_type_id, c.fitting.stockpile_target,
                                                contract_target=c.fitting.contract_target,
                                                valid_contracts=valid_contracts)
        ordered_soll.append((c.fitting.fitting_id, soll))

    type_ids = {t for _fid, soll in ordered_soll for t in soll}
    available_by_type = {
        t: storage.esi_stock_at_location(t, location_id, tables=("doctrine_character_assets", "doctrine_corp_assets"))
        for t in type_ids
    }
    allocation = validation.allocate_stockpile(ordered_soll, available_by_type)

    rows: list[StockpileRow] = []
    for c in candidates:
        soll = dict(next(s for fid, s in ordered_soll if fid == c.fitting.fitting_id))
        alloc = allocation[c.fitting.fitting_id]
        for type_id, (required, item_class) in soll.items():
            allocated = alloc.get(type_id, 0.0)
            tolerance = effective_cargo_tolerance(c.fitting, cfg)
            shortfall, severity = validation.stockpile_deviation(type_id, required, allocated, item_class, tolerance)
            type_row = storage.get_sde_type(type_id)
            type_name = type_row[2] if type_row else str(type_id)
            slot_section = "hull" if type_id == c.fitting.hull_type_id else (
                "low/med/high/rig/subsystem/service" if item_class == "exact" else "drone/cargo/charge")
            rows.append(StockpileRow(
                fitting_id=c.fitting.fitting_id, fitting_name=c.fitting.name,
                doctrine_id=c.fitting.doctrine_id,
                doctrine_name=doctrine_name_by_id.get(c.fitting.doctrine_id, c.fitting.doctrine_id),
                type_id=type_id, type_name=type_name, slot_section=slot_section, required_total=required,
                available=allocated, shortfall=shortfall, severity=severity,
            ))
    return rows, assets_available


def aggregate_stockpile_rows(rows: list[StockpileRow]) -> list[AggregatedStockpileRow]:
    """Combines the same item across every fitting/doctrine that needs it
    into one row (GitHub issue #1) - "how much of X do I need in total"
    without manually adding up several StockpileRows. Summing
    required_total/available/shortfall across rows for the same type_id is
    mathematically safe: validation.allocate_stockpile already partitions
    the physical stock across fittings by priority, so no row's `available`
    double-counts another's. severity is the worst among contributing rows
    (validation.worst_severity) - two fittings can legitimately disagree
    (different cargo_tolerance_pct), so there's no single "correct" tolerance
    to recompute a combined severity from directly."""
    by_type: dict[int, list[StockpileRow]] = {}
    for r in rows:
        by_type.setdefault(r.type_id, []).append(r)

    aggregated = [
        AggregatedStockpileRow(
            type_id=type_id, type_name=group[0].type_name,
            required_total=sum(r.required_total for r in group),
            available=sum(r.available for r in group),
            shortfall=sum(r.shortfall for r in group),
            severity=validation.worst_severity([r.severity for r in group]),
            fitting_count=len(group),
        )
        for type_id, group in by_type.items()
    ]
    aggregated.sort(key=lambda r: r.shortfall, reverse=True)
    return aggregated


# ------------------------------------------------------------- shopping list
def _shopping_prices(type_id: int, home: dict, jita: dict, volume: Optional[float],
                      cfg: DoctrineConfig, home_stats: Optional[OrderStats] = None,
                      jita_stats: Optional[OrderStats] = None) -> tuple[Optional[float], Optional[float]]:
    """(cj_price, jita_landed_price) for the Shopping List's own Buy columns -
    mirrors production.pricing's own buy-candidate formula, but uses
    Doctrine's own cfg.import_cost_per_m3 for the Jita leg instead of
    Production's haul_cost_per_m3 (a deliberately separate, independently-
    configurable value - see DoctrineConfig.import_cost_per_m3's own
    comment). The broker fee is the same real per-character fee either way,
    so that part is still read off PRODUCTION_CONFIG - no reason to
    duplicate it as a second Doctrine setting nobody asked for.

    `home_stats`/`jita_stats` (shopping_list_rows' own live ESI order-book
    check, GitHub issue #10) gate each price on real listed sell volume, not
    just Goonmetrics' quote existing - confirmed real bug: Goonmetrics can
    return a nonzero "sell" price for a market with zero actual sell orders
    right now (confirmed live 2026-08-19 for Federation Navy Iridium Charge
    M at C-J - Goonmetrics' own C-J and Jita quotes for it came back under
    the same "updated" timestamp despite Jita's real ESI order book showing
    over a million units listed and C-J's structure having none). None (not
    an OrderStats) means "couldn't verify" (no seller token / structure
    docking access for the C-J leg) - degrades to trusting the Goonmetrics
    quote as before rather than blocking the page on a fixable auth gap."""
    home_quote, jita_quote = home.get(type_id), jita.get(type_id)
    broker_fee = PRODUCTION_CONFIG.jita_buy_broker_fee
    cj = None
    if home_quote and home_quote.sell > 0 and (home_stats is None or home_stats.sell_volume > 0):
        cj = home_quote.sell * (1 + broker_fee)
    jita_landed = None
    if jita_quote and jita_quote.sell > 0 and (jita_stats is None or jita_stats.sell_volume > 0):
        jita_landed = jita_quote.sell * (1 + broker_fee) + cfg.import_cost_per_m3 * (volume or 0)
    return cj, jita_landed


def shopping_list_rows(doctrine_id: Optional[str] = None, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> list[ShoppingListRow]:
    """Every item with a real stockpile shortfall (across every doctrine,
    same aggregation as the Stockpile page's own combined view - GitHub
    issue #1's aggregate_stockpile_rows), each with a Build-vs-Buy-C-J-vs-
    Buy-Jita comparison. Build cost reuses Production's real cost engine
    directly (_PlanContext/unit_cost_detail) - see this module's own import
    comment for why - so it reflects the user's actual owned-BPO/ME/TE/
    structure config, not a second, possibly-divergent estimate."""
    rows, _ = stockpile_rows_for_doctrine(doctrine_id, cfg)
    aggregated = [r for r in aggregate_stockpile_rows(rows) if r.shortfall > 0]
    if not aggregated:
        return []

    ctx = _PlanContext(PRODUCTION_CONFIG)
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, tuple[float, float, Optional[str]]] = {}

    # Live order-book presence check (GitHub issue #10) - fetched once for
    # every shortfall item up front (the structure endpoint has no type_id
    # filter, so a per-item call would re-download the whole order book
    # every time, same reasoning actions.py's own shortlist refresh already
    # documents for structure_order_stats_bulk). Jita is public/no-auth, so
    # it's always attempted; the C-J leg reuses Trading's own "seller" token
    # (esi-markets.structure_markets.v1 isn't a scope Doctrine/Production
    # characters have) since Doctrine's structure defaults to Trading's own
    # sell structure anyway (DoctrineConfig.effective_structure_id) - best
    # effort, degrades to trusting Goonmetrics for whichever leg it can't
    # verify rather than failing the whole page.
    esi = ESIClient()
    type_ids = [row.type_id for row in aggregated]
    try:
        jita_stats = esi.region_order_stats_bulk(TRADING_CONFIG.jita_region_id, type_ids)
    except Exception:  # noqa: BLE001 - best-effort; falls back to trusting Goonmetrics for every row
        jita_stats = {}
    home_stats: dict[int, OrderStats] = {}
    structure_id = cfg.effective_structure_id
    if structure_id is not None and esi.tokens.has_token("seller"):
        try:
            home_stats = esi.structure_order_stats_bulk(structure_id, type_ids, auth_role="seller")
        except ESIError:
            home_stats = {}  # no docking access right now - degrade, don't break the page

    result = []
    for row in aggregated:
        _, build_cost, _ = unit_cost_detail(row.type_id, PRODUCTION_CONFIG, ctx.home, ctx.jita, cost_memo,
                                             ctx.selected_decryptors, t2_memo, ctx.cost_indices, ctx.adjusted_prices)
        # _haul_volume (not raw sde_type volume) so ships/capital modules use
        # their packaged volume for the Jita haul leg, not the much larger
        # flight/assembled volume - GitHub issue #11.
        volume = _haul_volume(row.type_id, PRODUCTION_CONFIG)
        cj_price, jita_landed_price = _shopping_prices(
            row.type_id, ctx.home, ctx.jita, volume, cfg,
            home_stats.get(row.type_id), jita_stats.get(row.type_id))

        candidates = {"Build": build_cost, "C-J": cj_price, "Jita": jita_landed_price}
        priced = {k: v for k, v in candidates.items() if v is not None}
        recommended_source = min(priced, key=priced.get) if priced else None
        total_cost = priced[recommended_source] * row.shortfall if recommended_source else None

        result.append(ShoppingListRow(
            type_id=row.type_id, type_name=row.type_name, shortfall=row.shortfall,
            build_cost=build_cost, cj_price=cj_price, jita_landed_price=jita_landed_price,
            recommended_source=recommended_source, total_cost=total_cost,
        ))
    return result


# ---------------------------------------------------------------- status / ampel
def contract_rows_from_db(rows: list[tuple]) -> list[ContractRow]:
    result = []
    for (contract_id, source_role, for_corporation, issuer_id, start_location_id, status, title, price,
         date_expired, matched_fitting_id, match_score, validation_status, synced_at) in rows:
        result.append(ContractRow(contract_id=contract_id, source_role=source_role,
                                   for_corporation=for_corporation, issuer_id=issuer_id,
                                   start_location_id=start_location_id, status=status, title=title,
                                   price=price, date_expired=str(date_expired) if date_expired else None,
                                   matched_fitting_id=str(matched_fitting_id) if matched_fitting_id else None,
                                   match_score=match_score, validation_status=validation_status,
                                   synced_at=str(synced_at) if synced_at else None))
    return result


def fitting_status(fitting: Fitting, cfg: DoctrineConfig = DOCTRINE_CONFIG,
                    stockpile_rows: Optional[list[StockpileRow]] = None,
                    assets_available: bool = True, home: Optional[dict] = None) -> FittingStatus:
    """`home` (Goonmetrics current-price dict, production_pricing.home_prices'
    own return shape) is an optional passthrough for multibuy_cost, same
    "compute once in doctrine_status' loop, don't refetch per fitting"
    reasoning as `stockpile_rows` above - None (the default, e.g. a lone
    do_get_fitting_detail call) just means multibuy_cost comes back None."""
    contracts = contract_rows_from_db(storage.list_doctrine_contracts(fitting_id=fitting.fitting_id))
    last_synced_at = storage.get_esi_sync_time("doctrine")
    # status == "expired" (the raw ESI contract status, separate from
    # validation_status) is excluded here even though SYNCABLE_CONTRACT_
    # STATUSES now lets expired contracts stay synced/visible (see that
    # constant's own comment) - an expired contract must stop counting
    # toward a fitting's target ampel the moment it expires, it just
    # doesn't disappear from the Contracts page without explanation anymore.
    valid = sum(1 for c in contracts if c.validation_status == "valid" and c.status != "expired")
    tolerable = sum(1 for c in contracts if c.validation_status == "tolerable" and c.status != "expired")
    contract_ampel = validation.contract_ampel(last_synced_at, fitting.contract_target, valid, tolerable)

    if stockpile_rows is None:
        all_rows, assets_available = stockpile_rows_for_doctrine(fitting.doctrine_id, cfg)
        stockpile_rows = [r for r in all_rows if r.fitting_id == fitting.fitting_id]

    worst_shortfall_pct = 0.0
    worst_severity = None
    for r in stockpile_rows:
        if r.severity is None:
            continue
        pct = (r.shortfall / r.required_total) if r.required_total else 0.0
        worst_shortfall_pct = max(worst_shortfall_pct, pct)
        if r.severity == "critical" or worst_severity is None:
            worst_severity = r.severity
    stockpile_amp = validation.stockpile_ampel(assets_available, fitting.stockpile_target, worst_severity)

    hull_sde = storage.get_sde_type(fitting.hull_type_id)
    hull_name = hull_sde[2] if hull_sde else str(fitting.hull_type_id)

    multibuy_cost = None
    if home is not None:
        items = storage.load_fitting_items(fitting.fitting_id)
        quantities: dict[int, float] = {fitting.hull_type_id: 1.0}
        for _line_no, _slot_section, type_id, quantity, _is_offline in items:
            quantities[type_id] = quantities.get(type_id, 0.0) + quantity
        total = 0.0
        for type_id, quantity in quantities.items():
            quote = home.get(type_id)
            if quote is None or quote.sell <= 0:
                total = None
                break
            total += quote.sell * quantity
        multibuy_cost = total

    return FittingStatus(fitting_id=fitting.fitting_id, fitting_name=fitting.name, doctrine_id=fitting.doctrine_id,
                          contract_status=contract_ampel, valid_contracts=valid, tolerable_contracts=tolerable,
                          contract_target=fitting.contract_target, stockpile_status=stockpile_amp,
                          stockpile_target=fitting.stockpile_target,
                          worst_stockpile_shortfall_pct=worst_shortfall_pct, last_synced_at=last_synced_at,
                          assets_available=assets_available, hull_type_id=fitting.hull_type_id,
                          hull_name=hull_name, multibuy_cost=multibuy_cost)


def doctrine_status(doctrine_row: tuple, cfg: DoctrineConfig = DOCTRINE_CONFIG) -> DoctrineStatus:
    doctrine = doctrine_from_row(doctrine_row)
    fitting_rows = storage.list_fittings_for_doctrine(doctrine.doctrine_id)
    stockpile_rows, assets_available = stockpile_rows_for_doctrine(doctrine.doctrine_id, cfg)

    # Fetched once for every fitting's multibuy_cost below, not per-fitting -
    # GoonmetricsClient's own 60s cache would mostly cover repeat calls
    # anyway, but explicit-once matches this function's existing
    # stockpile_rows-computed-once convention.
    from ..goonmetrics_client import GoonmetricsClient
    home = production_pricing.home_prices(GoonmetricsClient(), PRODUCTION_CONFIG)

    statuses = []
    for row in fitting_rows:
        fitting = fitting_from_row(row)
        if not fitting.active:
            continue
        own_rows = [r for r in stockpile_rows if r.fitting_id == fitting.fitting_id]
        statuses.append(fitting_status(fitting, cfg, stockpile_rows=own_rows, assets_available=assets_available,
                                        home=home))

    contract_rollup = validation.worst_ampel([s.contract_status for s in statuses]) if statuses else AMPEL_GRAY
    stockpile_rollup = validation.worst_ampel([s.stockpile_status for s in statuses]) if statuses else AMPEL_GRAY
    overall = validation.worst_ampel([contract_rollup, stockpile_rollup])

    return DoctrineStatus(doctrine_id=doctrine.doctrine_id, doctrine_name=doctrine.name, overall=overall,
                           contract_rollup=contract_rollup, stockpile_rollup=stockpile_rollup, fittings=statuses)
