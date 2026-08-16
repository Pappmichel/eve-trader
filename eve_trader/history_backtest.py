"""Historical backtest of candidates against Goonmetrics daily price history.

For every candidate not already on the shortlist, pair up daily average
prices in the Jita region vs. the reference region (Insmother by default) and
compute, per day:

    landed  = jita_avg_price + volume_m3 * import_cost_per_m3
    netSell = reference_avg_price * haircut
    profit  = netSell - landed
    margin  = profit / landed
    profitM3 = profit / volume_m3

Then aggregate across days into a hit-rate/score used to recommend additions
to the shortlist (see New Candidates' "Add?" recommendation).
"""
from __future__ import annotations

import logging
import math
from typing import Callable, Iterable, Optional

import pandas as pd

from .config import TRADING_CONFIG, TradingConfig
from .goonmetrics_client import GoonmetricsClient, HistoryPoint
from .models import Candidate, NewCandidateResult

log = logging.getLogger(__name__)

# Minimum paired (jita+reference, same date) history days required before a
# trend is reported at all - below this a "3-day vs 30-day average" split is
# too noisy/coincidental to mean anything (e.g. 4 days total gives a 3-day
# window that's almost the whole sample, not a real "recent" slice).
MIN_TREND_HISTORY_DAYS = 6
RECENT_WINDOW_DAYS = 3
BASELINE_WINDOW_DAYS = 30

# trend_pct divides by baseline_avg_margin - confirmed live (2026-07-15) that
# an exact-zero-only guard isn't enough: items whose baseline margin merely
# hovers near breakeven (e.g. 0.0006, -0.001) produced trend_pct in the
# thousands of percent (one live case: +26,486%) even though both the recent
# and baseline margins individually were perfectly ordinary numbers - the
# ratio itself is what's unstable near zero, not the inputs. 0.02 matches the
# frontend's own "stable" dead-zone (Shortlist.tsx treats |trend_pct| < 0.02
# as noise already) - a baseline inside that band has no meaningful direction
# to measure a percentage change against, so it's omitted rather than shown.
MIN_BASELINE_MARGIN_MAGNITUDE = 0.02


def _index_history(points: Iterable[HistoryPoint]) -> dict[tuple[int, int, str], HistoryPoint]:
    return {(p.region_id, p.type_id, p.date): p for p in points}


def _latest_margin(hist_index: dict, type_id: int, date: str, volume_m3: float,
                    cfg: TradingConfig) -> float:
    """Same landed/net_sell/margin formula as _score_candidate's per-day loop,
    but for a single day only (`date`, normally the most recent one with
    data) - used for the `add` gate below, which cares about *current*
    profitability specifically, not the multi-day average score."""
    jita = hist_index.get((cfg.jita_region_id, type_id, date))
    ref = hist_index.get((cfg.reference_region_id, type_id, date))
    if not jita or not ref:
        return 0.0
    landed = jita.avg_price * (1 + cfg.jita_buy_broker_fee) + volume_m3 * cfg.import_cost_per_m3
    net_sell = ref.avg_price * cfg.structure_sell_haircut
    return (net_sell - landed) / landed if landed > 0 else 0.0


def _score_candidate(candidate: Candidate, hist_index: dict,
                      cfg: TradingConfig) -> NewCandidateResult | None:
    """Aggregates every paired (Jita, reference-region) day of history for
    this candidate into a hit-rate/score, and decides whether to recommend
    it. `score = avg_profit_m3 x log(1+avg_move) x hit_rate`: profit per m3
    rewards import efficiency, log(1+avg_move) rewards liquidity without
    letting a single very-high-volume day dominate (log dampens scale, see
    MIN_BASELINE_MARGIN_MAGNITUDE elsewhere in this module for a related
    "don't let one axis blow up the result" fix), hit_rate rewards
    consistency over a lucky day. `add` (the actual recommendation) requires
    *all* of: at least one profitable day, hit_rate clearing
    cfg.min_hit_rate, the *latest* day's margin (not just the average)
    clearing cfg.min_margin_threshold, a positive score, and average
    liquidity clearing cfg.min_avg_movement - a good historical average
    alone isn't enough if the item isn't profitable or liquid right now."""
    dates = sorted({d for (r, t, d) in hist_index if r == cfg.jita_region_id and t == candidate.type_id})
    if not dates:
        return None

    days = good_days = 0
    best_margin = -999.0
    sum_profit_m3 = sum_move = 0.0
    latest_date = dates[-1]

    for d in dates:
        jita = hist_index.get((cfg.jita_region_id, candidate.type_id, d))
        ref = hist_index.get((cfg.reference_region_id, candidate.type_id, d))
        if not jita or not ref:
            continue
        landed = jita.avg_price * (1 + cfg.jita_buy_broker_fee) + candidate.volume_m3 * cfg.import_cost_per_m3
        net_sell = ref.avg_price * cfg.structure_sell_haircut
        if landed <= 0 or candidate.volume_m3 <= 0:
            continue
        profit = net_sell - landed
        margin = profit / landed
        profit_m3 = profit / candidate.volume_m3
        days += 1
        sum_profit_m3 += profit_m3
        sum_move += ref.movement
        if margin >= cfg.min_margin_threshold:
            good_days += 1
        best_margin = max(best_margin, margin)

    if days == 0:
        return None

    hit_rate = good_days / days
    avg_profit_m3 = sum_profit_m3 / days
    avg_move = sum_move / days
    score = avg_profit_m3 * math.log(1 + avg_move) * hit_rate
    latest_margin = _latest_margin(hist_index, candidate.type_id, latest_date, candidate.volume_m3, cfg)

    add = (good_days > 0 and hit_rate >= cfg.min_hit_rate and latest_margin >= cfg.min_margin_threshold
           and score > 0 and avg_move >= cfg.min_avg_movement)
    return NewCandidateResult(
        item=candidate.item, category=candidate.category, type_id=candidate.type_id,
        volume_m3=candidate.volume_m3, paired_days=days, profitable_days=good_days,
        hit_rate=hit_rate, latest_margin=latest_margin, best_margin=best_margin,
        avg_profit_m3=avg_profit_m3, avg_sell_movement=avg_move, score=score,
        recommendation=("Consider import" if add else "Skip"), add=add,
        meta_level=candidate.meta_level,
    )


def compute_margin_trends(history_df: pd.DataFrame, volumes: dict[int, float],
                           cfg: TradingConfig = TRADING_CONFIG) -> dict[int, dict]:
    """Momentum signal inspired by comparable EVE trading tools (3-day vs
    30-day VWAP trend detection): for every type_id in `volumes`, compares
    the average landed-cost margin over the most recent RECENT_WINDOW_DAYS
    against the average over the last BASELINE_WINDOW_DAYS (which includes
    those same recent days, same "rolling window" convention the inspiring
    tools use - not a disjoint "recent vs everything before it" split).

    `history_df` is the already-persisted goonmetrics_history table
    (storage.read_table("goonmetrics_history"), populated by
    find_new_import_candidates' history_sink) - deliberately passed in rather
    than read from storage here, same "no storage coupling, caller wires data
    in" shape every other function in this module already has. This is a
    zero-network-cost signal layered on data the app fetches anyway, not a
    new data source. Returns {} (or omits a type_id) wherever there isn't
    enough paired history yet - a brand-new shortlist item with only a day or
    two of history simply has no trend to report, not a fabricated one. Also
    omits a type_id whose baseline margin sits within
    MIN_BASELINE_MARGIN_MAGNITUDE of zero - dividing by a near-breakeven
    baseline produces a huge, meaningless trend_pct even when neither the
    recent nor baseline margin is itself unusual (confirmed live).

    Returns {type_id: {"recent_avg_margin", "baseline_avg_margin",
    "trend_pct"}} - trend_pct = (recent - baseline) / abs(baseline), positive
    means the margin is improving, negative means it's eroding.
    """
    df = history_df
    if df.empty:
        return {}

    jita = df[df["region_id"] == cfg.jita_region_id][["type_id", "date", "avg_price"]] \
        .rename(columns={"avg_price": "jita_price"})
    ref = df[df["region_id"] == cfg.reference_region_id][["type_id", "date", "avg_price"]] \
        .rename(columns={"avg_price": "ref_price"})
    merged = jita.merge(ref, on=["type_id", "date"])
    if merged.empty:
        return {}

    merged = merged[merged["type_id"].isin(volumes)]
    merged["volume_m3"] = merged["type_id"].map(volumes)
    merged["landed"] = merged["jita_price"] * (1 + cfg.jita_buy_broker_fee) + \
        merged["volume_m3"] * cfg.import_cost_per_m3
    merged = merged[merged["landed"] > 0]
    merged["net_sell"] = merged["ref_price"] * cfg.structure_sell_haircut
    merged["margin"] = (merged["net_sell"] - merged["landed"]) / merged["landed"]
    merged = merged.sort_values(["type_id", "date"])

    results: dict[int, dict] = {}
    for type_id, group in merged.groupby("type_id"):
        if len(group) < MIN_TREND_HISTORY_DAYS:
            continue
        recent_avg = group["margin"].tail(RECENT_WINDOW_DAYS).mean()
        baseline_avg = group["margin"].tail(BASELINE_WINDOW_DAYS).mean()
        if pd.isna(baseline_avg) or pd.isna(recent_avg) or abs(baseline_avg) < MIN_BASELINE_MARGIN_MAGNITUDE:
            continue
        results[int(type_id)] = {
            "recent_avg_margin": float(recent_avg),
            "baseline_avg_margin": float(baseline_avg),
            "trend_pct": float((recent_avg - baseline_avg) / abs(baseline_avg)),
        }
    return results


def select_candidate_window(candidates: list[Candidate], max_ids: int, offset: int) -> tuple[list[Candidate], int]:
    """Returns (window, next_offset): the next `max_ids`-sized slice of
    `candidates` starting at `offset`, wrapping around the end of the list,
    plus the offset the *next* call should start at.

    Used by safe-mode search so repeated runs eventually cover every
    candidate instead of either (a) a fixed prefix, where anything past
    max_ids is never reachable, or (b) a random sample each run, which only
    gives every candidate a *chance* of being picked, never a guarantee.
    Rotating the window guarantees full coverage within
    ceil(len(candidates)/max_ids) runs - see storage.candidate_search_cursor.
    """
    n = len(candidates)
    if n == 0:
        return [], 0
    if n <= max_ids:
        return list(candidates), 0
    start = offset % n
    end = start + max_ids
    window = candidates[start:end] if end <= n else candidates[start:] + candidates[:end - n]
    return window, end % n


def find_new_import_candidates(candidates: list[Candidate], existing_item_ids: set[int],
                                client: GoonmetricsClient | None = None,
                                cfg: TradingConfig = TRADING_CONFIG,
                                max_ids: int | None = None,
                                offset: int = 0,
                                history_sink=None,
                                results_sink: Optional[Callable[[list[NewCandidateResult]], None]] = None,
                                ) -> tuple[list[NewCandidateResult], int]:
    """Full scan, or a "safe" (rate-limited) one if `max_ids` is set - see
    select_candidate_window for how safe mode rotates through the candidate
    universe across repeated calls.

    `existing_item_ids` should be the set of item IDs already active on the
    shortlist - candidates already tracked there are skipped.

    `offset`/the returned next-offset drive select_candidate_window (see its
    docstring) - callers that care about full eventual coverage (safe mode)
    should persist the returned offset and pass it back in next time (see
    actions.py do_find_new_candidates / storage.candidate_search_cursor).

    `history_sink`, if given, is called once per internal batch with the
    HistoryPoint objects fetched for that batch (e.g.
    storage.save_goonmetrics_history) so 'Goonmetrics History' data is
    persisted, not just used in-memory for scoring.

    `results_sink`, if given, is called once per internal batch with that
    batch's NewCandidateResults - lets a caller persist results
    incrementally (e.g. storage.save_new_candidates) instead of only after
    every candidate has been scored. This matters for a full (max_ids=None)
    run over the whole candidate universe (thousands of items, many
    Goonmetrics/ESI round-trips, can run for minutes) - without incremental
    saving, a crash/restart partway through would lose everything scored so
    far instead of keeping the results already computed.

    Processes candidates in batches of up to cfg.safe_mode_max_ids each - for
    'safe' mode (max_ids set) that's a single batch (the rotated window from
    select_candidate_window); for a full run it's every batch in sequence.
    Each batch is isolated in its own try/except so one bad batch (a
    Goonmetrics/ESI hiccup that price_history_chunked's own per-chunk
    fallback couldn't route around, an unexpected parsing error, etc.)
    doesn't lose the results already computed for every other batch - it's
    logged and skipped instead of aborting the whole run.
    """
    client = client or GoonmetricsClient(cfg)
    seen_ids: set[int] = set()
    new_candidates = []
    for c in candidates:
        if c.type_id in existing_item_ids or c.type_id in seen_ids:
            continue
        seen_ids.add(c.type_id)
        new_candidates.append(c)

    batch_size = max_ids or cfg.safe_mode_max_ids
    next_offset = 0
    if max_ids and len(new_candidates) > max_ids:
        new_candidates, next_offset = select_candidate_window(new_candidates, max_ids, offset)
        batches = [new_candidates]
    else:
        batches = [new_candidates[i:i + batch_size] for i in range(0, len(new_candidates), batch_size)]

    all_results: list[NewCandidateResult] = []
    total_batches = len(batches)
    for batch_num, batch in enumerate(batches, start=1):
        if not batch:
            continue
        try:
            type_ids = [c.type_id for c in batch]
            points: list[HistoryPoint] = []
            points.extend(client.price_history_chunked(cfg.jita_region_id, type_ids))
            points.extend(client.price_history_chunked(cfg.reference_region_id, type_ids))

            if history_sink is not None and points:
                history_sink(points)

            hist_index = _index_history(points)
            batch_results = [r for r in (_score_candidate(c, hist_index, cfg) for c in batch) if r]
        except Exception:  # noqa: BLE001 - one bad batch must not lose every other batch's results
            log.exception("Candidate batch %d/%d (%d items) failed - skipping it, continuing with the rest.",
                           batch_num, total_batches, len(batch))
            continue

        batch_results.sort(key=lambda r: (r.score, r.latest_margin), reverse=True)
        if results_sink is not None and batch_results:
            results_sink(batch_results)
        all_results.extend(batch_results)
        if total_batches > 1:
            log.info("Candidate search: batch %d/%d done (%d evaluated so far).",
                      batch_num, total_batches, len(all_results))

    all_results.sort(key=lambda r: (r.score, r.latest_margin), reverse=True)
    return all_results, next_offset


def find_new_import_candidates_safe(candidates: list[Candidate], existing_item_ids: set[int],
                                     client: GoonmetricsClient | None = None,
                                     cfg: TradingConfig = TRADING_CONFIG,
                                     offset: int = 0,
                                     history_sink=None,
                                     results_sink: Optional[Callable[[list[NewCandidateResult]], None]] = None,
                                     ) -> tuple[list[NewCandidateResult], int]:
    """Rate-limited variant: caps at cfg.safe_mode_max_ids and uses
    cfg.chunk_size-sized Goonmetrics requests so large candidate universes
    stay responsive instead of one huge blocking scan.
    """
    return find_new_import_candidates(candidates, existing_item_ids, client, cfg,
                                       max_ids=cfg.safe_mode_max_ids, offset=offset,
                                       history_sink=history_sink, results_sink=results_sink)
