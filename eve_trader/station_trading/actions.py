"""Station Trading actions - see CLAUDE.md's "Architecture" section: the
FastAPI router calls these do_* functions, never storage.py/esi_client.py
directly.
"""
from __future__ import annotations

import datetime as dt

from .. import storage
from ..actions import ActionError
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, ConfigError, OAuthConfig, save_tenant_config_overrides
from ..esi_client import ESIClient, ESIError
from . import esi_sync
from .candidate_discovery import confirm_live, discover_candidates
from .config import STATION_TRADING_CONFIG, StationTradingConfig
from .constants import SKILL_LABELS, order_slots_from_skills
from .undercut import check_buy_undercut_pooled, check_undercut_pooled


def now_ts() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def _item_name(type_id: int) -> str:
    sde_row = storage.get_sde_type(type_id)
    return sde_row[2] if sde_row else str(type_id)


def _category_name(type_id: int, category_names: dict[int, str]) -> str:
    category_id = storage.get_type_category(type_id)
    return category_names.get(category_id, "Unknown") if category_id is not None else "Unknown"


def _profit(live_buy: float | None, live_sell: float | None,
            cfg: StationTradingConfig) -> tuple[float | None, float | None]:
    """profit_per_unit/margin from LIVE prices only (never the Goonmetrics
    discovery-time snapshot the shortlist persists) - broker fee is charged
    on both legs (buy placement and sell placement), sales tax only on the
    sell leg, matching real EVE market mechanics (see StationTradingConfig's
    own field comments). None/None when no live price is available (ESI
    outage, or the item never confirmed) - same "don't fabricate a number"
    convention Trading's own shortlist uses for landed_cost/net_sell."""
    if live_buy is None or live_sell is None or live_buy <= 0:
        return None, None
    buy_cost = live_buy * (1 + cfg.broker_fee_rate)
    sell_net = live_sell * (1 - cfg.broker_fee_rate - cfg.sales_tax_rate)
    profit_per_unit = sell_net - buy_cost
    margin = profit_per_unit / buy_cost
    return profit_per_unit, margin


def _build_shortlist_rows(rows: list[tuple[int, float, float, str, bool]],
                           cfg: StationTradingConfig) -> list[dict]:
    """Shared by do_get_shortlist/do_refresh_shortlist - live-confirms every
    row's price in one bounded confirm_live call (never per-row) and derives
    category/profit/margin from that live price, not the persisted
    discovery-time spread (see _profit's own docstring)."""
    type_ids = [type_id for type_id, *_rest in rows]
    live = confirm_live(type_ids)
    category_names = storage.load_sde_category_names()
    result = []
    for type_id, spread_pct, avg_daily_volume, discovered_at, active in rows:
        stats = live.get(type_id)
        live_buy = stats.buy_percentile if stats else None
        live_sell = stats.sell_percentile if stats else None
        profit_per_unit, margin = _profit(live_buy, live_sell, cfg)
        result.append({
            "type_id": type_id, "name": _item_name(type_id), "category": _category_name(type_id, category_names),
            "spread_pct": spread_pct, "avg_daily_volume": avg_daily_volume,
            "discovered_at": discovered_at, "active": active,
            "live_buy": live_buy, "live_sell": live_sell,
            "profit_per_unit": profit_per_unit, "margin": margin,
        })
    return result


# ---------------------------------------------------------- trader characters
def do_list_trader_characters() -> list[tuple[str, int, str]]:
    return esi_sync.list_trader_characters()


def do_remove_trader_character(role_key: str) -> dict:
    TokenManager(OAUTH_CONFIG).remove_token(role_key)
    return {"removed": role_key}


# ---------------------------------------------------------------- shortlist
def do_refresh_shortlist(cfg: StationTradingConfig = STATION_TRADING_CONFIG) -> dict:
    """Re-runs candidate discovery (Goonmetrics-based, see
    candidate_discovery.discover_candidates) and persists the result -
    newly-discovered rows all start active; a previously-deactivated
    type_id stays deactivated (see storage.upsert_station_trading_shortlist).
    Returns the same live-confirmed, profit-annotated shape do_get_shortlist
    does (_build_shortlist_rows), so the "Refresh" button's click updates
    the table immediately without a second round-trip."""
    candidates = discover_candidates(cfg)
    run_ts = now_ts()
    storage.upsert_station_trading_shortlist(
        [(c["type_id"], c["spread_pct"], c["avg_daily_volume"], run_ts) for c in candidates]
    )
    storage.set_esi_sync_time("station_trading", run_ts)
    rows = do_get_shortlist(cfg)
    return {"discovered": len(candidates), "rows": rows}


def do_get_shortlist(cfg: StationTradingConfig = STATION_TRADING_CONFIG) -> list[dict]:
    """Persisted shortlist rows, live-price-confirmed and profit-annotated on
    every read (see _build_shortlist_rows) - bounded to at most top_n=200
    rows (candidate_discovery.discover_candidates' own cap), so the live ESI
    call this makes is cheap and stays within ESIClient's existing 30s
    class-wide order-book cache."""
    rows = storage.load_station_trading_shortlist()
    return _build_shortlist_rows(rows, cfg)


def do_deactivate_shortlist_items(type_ids: list[int]) -> dict:
    storage.deactivate_station_trading_shortlist_items(type_ids)
    return {"deactivated": len(type_ids)}


def do_activate_shortlist_items(type_ids: list[int]) -> dict:
    storage.activate_station_trading_shortlist_items(type_ids)
    return {"activated": len(type_ids)}


# ---------------------------------------------------------------- undercut
def _undercut_row_to_dict(r: dict) -> dict:
    return {"type_id": r["type_id"], "name": _item_name(r["type_id"]), "my_price": r["my_price"],
            "competitor_price": r["competitor_price"], "difference": r["difference"]}


def do_check_undercut(cfg: StationTradingConfig = STATION_TRADING_CONFIG,
                       oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Bidirectional: flags any of the trader's own Jita trade-hub orders -
    buy or sell - that a genuinely different market participant now beats.
    See undercut.py's own docstring for why both sides need their own live
    ESI order-book fetch rather than a Goonmetrics snapshot."""
    tm = TokenManager(oauth_cfg)
    traders = [(character_id, role) for role, character_id, _name in esi_sync.list_trader_characters(tm)]
    if not traders:
        raise ActionError("No trader characters registered yet - add one first.")
    client = ESIClient(tokens=tm)
    try:
        sell_rows = check_undercut_pooled(traders, client, cfg)
        buy_rows = check_buy_undercut_pooled(traders, client, cfg)
    except ESIError as e:
        raise ActionError(f"Could not fetch order-book data ({e}).") from e
    return {
        "sell": [_undercut_row_to_dict(r) for r in sell_rows],
        "buy": [_undercut_row_to_dict(r) for r in buy_rows],
    }


# ------------------------------------------------------------------ skills
def do_get_skill_summary(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> list[dict]:
    """Live-pulled trade-skill levels per registered trader character, plus
    the derived order-slot count - informational only (see constants.py's
    own docstring for why fee/tax discounts are deliberately NOT derived
    from these levels)."""
    tm = TokenManager(oauth_cfg)
    client = ESIClient(tokens=tm)
    summaries = []
    for role, character_id, character_name in esi_sync.list_trader_characters(tm):
        try:
            skills = client.character_skills(character_id, auth_role=role)
        except ESIError as e:
            summaries.append({"character_name": character_name, "error": f"skipped (re-add character? {e})"})
            continue
        levels = {s["skill_id"]: s["active_skill_level"] for s in skills.get("skills", [])}
        summaries.append({
            "character_name": character_name,
            "levels": {label: levels.get(skill_id, 0) for skill_id, label in SKILL_LABELS.items()},
            "order_slots": order_slots_from_skills(levels),
        })
    return summaries


# ----------------------------------------------------------------- settings
def do_update_settings(updates: dict, cfg: StationTradingConfig = STATION_TRADING_CONFIG) -> dict:
    """Persists `updates` to tenant_settings and applies them to the live
    STATION_TRADING_CONFIG immediately (see Station Trading Settings tab)."""
    try:
        save_tenant_config_overrides("station_trading", updates, cfg, cfg_type=StationTradingConfig)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return updates
