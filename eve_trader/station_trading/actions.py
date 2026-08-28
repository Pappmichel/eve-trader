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


# ---------------------------------------------------------- trader characters
def do_list_trader_characters() -> list[tuple[str, int, str]]:
    return esi_sync.list_trader_characters()


def do_remove_trader_character(role_key: str) -> dict:
    TokenManager(OAUTH_CONFIG).remove_token(role_key)
    return {"removed": role_key}


# ---------------------------------------------------------------- shortlist
def do_refresh_shortlist(cfg: StationTradingConfig = STATION_TRADING_CONFIG) -> dict:
    """Re-runs candidate discovery (Goonmetrics-based, see
    candidate_discovery.discover_candidates), persists the result, then
    live-confirms every discovered candidate against Jita's real order book
    in the same pass (candidate_discovery.confirm_live) - the "Refresh"
    button's click gets the fully live-confirmed table back immediately,
    without a second round-trip."""
    candidates = discover_candidates(cfg)
    run_ts = now_ts()
    storage.upsert_station_trading_shortlist(
        [(c["type_id"], c["spread_pct"], c["avg_daily_volume"], run_ts) for c in candidates]
    )
    storage.set_esi_sync_time("station_trading", run_ts)

    live = confirm_live([c["type_id"] for c in candidates])
    rows = []
    for c in candidates:
        stats = live.get(c["type_id"])
        rows.append({
            "type_id": c["type_id"], "name": _item_name(c["type_id"]),
            "spread_pct": c["spread_pct"], "avg_daily_volume": c["avg_daily_volume"],
            "discovered_at": run_ts, "active": True,
            "live_buy": stats.buy_percentile if stats else None,
            "live_sell": stats.sell_percentile if stats else None,
        })
    return {"discovered": len(candidates), "rows": rows}


def do_get_shortlist() -> list[dict]:
    """Persisted shortlist rows only, no live ESI call - a fast read for
    page load, refreshed to live prices only when the user actually clicks
    Refresh (do_refresh_shortlist)."""
    rows = storage.load_station_trading_shortlist()
    return [
        {"type_id": type_id, "name": _item_name(type_id), "spread_pct": spread_pct,
         "avg_daily_volume": avg_daily_volume, "discovered_at": discovered_at, "active": active}
        for type_id, spread_pct, avg_daily_volume, discovered_at, active in rows
    ]


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
