"""Realized Trade History reconciliation.

Pulls wallet transactions for buyer/seller characters (Jita imports vs
structure sells) over a lookback window, PLUS corporation wallet
transactions for any corp those characters belong to, and matches buys
against sells per item (FIFO) to compute realized profit. The sell side
(T1-01, 2026-09-25 - see the comment above _MARKET_TRANSACTION_REF_TYPE)
uses the REAL per-sale sales tax from ESI's wallet journal when a sell's
`market_transaction` and `transaction_tax` journal entries can both be
confidently located, deducting only cfg.structure_broker_fee (modeled -
broker's fee is charged once per ORDER, not per fill, so it can never be
attributed to one specific sale) on top of that; falling back to the fully
modeled cfg.structure_sell_haircut against sell_unit_price whenever the
real figures can't be confidently found (missing/failed journal fetch, a
snapshot-sourced entry with no linking field, or a sale near the fetch
window's edge).

Character and corp wallets are disjoint ESI streams: a corp-funded market
order is recorded on `/corporations/{id}/wallets/{division}/transactions/`
and is not present in the placing character's personal wallet (the gap this
corp path exists to close). They are still namespaced separately so a
coincidental `transaction_id` collision cannot merge two fills, and so a
corp sale is never attributed to whichever character's token fetched it:

- character txn identity: (character, character_id, transaction_id)
- corp txn identity: (corporation, corporation_id, division, transaction_id)

Each corp is fetched once (not once per member character). Buyer and seller
characters that share a corp therefore cannot double-count the same corp
fill. Journal lookup is wallet-local: a corp transaction's `journal_ref_id`
is resolved only against that division's corp journal, never a character
journal (and vice versa). RealizedTrade has no seller character id; corp
sells join the same structure-sell FIFO pool as personal sells, and corp
buys join the same Jita-buy pool.

This path assumes a single-member corp. Corp fills are counted regardless of
which member placed the order — reconciliation filters only by location
(Jita-region buys, cfg.structure_id sells), never by placing character.
That is correct here; in a shared corp those fills would pull other members'
trades into realized profit and into average_daily_sold_by_type, which
feeds "Profit / Day" on the shortlist.

Corp access follows production/esi_sync.py: the corp is reached through a
member character, retried with each registered character of that corp until
one can read at least one configured wallet division, and a corp no
registered character can serve is skipped non-fatally rather than raising.
A member that can read some but not all configured divisions is used as-is;
unread divisions are not topped up from a later member (that would re-fetch
the readable ones and double-count).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import storage
from .config import TRADING_CONFIG, TradingConfig, WALLET_DIVISION_IDS
from .esi_client import ESIClient, ESIError
from .models import RealizedTrade

log = logging.getLogger(__name__)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


WALLET_TRANSACTIONS_PAGE_SIZE = 2500  # ESI's fixed per-call cap for this endpoint

# Buys are fetched over a longer window than sells - a sale inside
# cfg.lookback_days can legitimately be funded by inventory bought well
# before that window started (an item just sitting at the structure waiting
# to sell), and the FIFO matcher's buy_date <= sell_date rule (PB-02, a
# separate confirmed bug fixed the same day this was found) means a too-old
# buy that was never even fetched looks identical to "no real cost basis" -
# the sale is dropped instead of matched to a fabricated later buy, which is
# safe but an avoidable under-report (PB-05, business-logic audit,
# 2026-08-29). A flat multiplier (not a separate persisted config field)
# scales with however long the user has already configured "recent" to
# mean, while staying bounded - unlike an unbounded/no-cutoff buy fetch,
# which would risk very slow reconciliation for a character with years of
# trading history.
_BUY_LOOKBACK_MULTIPLIER = 3

# PB-03 (business-logic audit, 2026-08-29) originally treated a journal-
# matched sell's `market_transaction` journal-entry `amount` as already NET
# of sales tax. That was wrong (confirmed with the user, 2026-09-25):
# `market_transaction`'s `amount` is the GROSS sale value; ESI deducts sales
# tax via its own separate `transaction_tax` journal entry. A same-day fix
# (still T1-01) then applied the fully modeled cfg.structure_sell_haircut to
# that gross amount instead - correct but conservative, since it left the
# real per-sale tax unused.
#
# T1-01's live verification (2026-09-25, evetrader.duckdns.org, Default
# tenant, read-only) found the REAL tax figure and its linkage, checked
# against all 2447 real structure sells in one seller character's fetched
# wallet journal:
#
#   - The wallet TRANSACTION's own `journal_ref_id` field never equals any
#     journal entry ESI actually returns (confirmed 0/2447) - it is USELESS
#     for finding a sell's `market_transaction` entry and must not be used
#     for that (a stale assumption from before this was live-checked).
#   - The real, 100%-reliable link (2447/2447) is `context_id` on the
#     `market_transaction` entry itself: `context_id == transaction_id` when
#     `context_id_type == "market_transaction_id"`.
#   - Its sales-tax deduction is a separate, adjacent journal entry: id ==
#     (the market_transaction entry's own id) + 1, ref_type
#     "transaction_tax", same `date` - verified this way on 2396/2447 sells
#     (the other 51 simply fell outside the fetched journal window's edge -
#     a safe, honest "can't confidently verify this one", never a wrong
#     guess - see the fallback below).
#   - The observed tax rate was a flat 3.375% of gross on every one of
#     those 2396 sells - a real, currently-live per-character figure, not a
#     value to hardcode; this module still always fetches and uses the
#     actual per-sale entry, never an assumed rate.
#   - "SCC surcharge" (part of structure_sell_haircut's own original
#     derivation) does not apply to a market sell at all - confirmed with
#     the user (2026-09-25) it is an industry-job-only fee - so it is not
#     part of this per-sale deduction (see cfg.structure_broker_fee's own
#     comment in config.py).
#
# Broker's fee still cannot be attributed to one specific FIFO-matched sale
# - it's charged once per ORDER, not per fill (confirmed with the user,
# 2026-08-29) - so cfg.structure_broker_fee stays modeled, applied on top of
# the real gross-minus-tax figure. A sell whose market_transaction/
# transaction_tax pair can't be confidently found this way (fetch failure,
# window-edge sale, or a snapshot-sourced entry - the persisted
# esi_wallet_journal table does not store context_id/context_id_type, so a
# snapshot-only sell can never resolve through this path; extending that
# schema is a separate, deliberately deferred follow-up, not done here)
# falls back to the same fully modeled cfg.structure_sell_haircut x
# sell_unit_price this module always used before PB-03 existed.

_MARKET_TRANSACTION_REF_TYPE = "market_transaction"
_TRANSACTION_TAX_REF_TYPE = "transaction_tax"


def fetch_recent_journal_entries(character_id: int, auth_role: str, client: ESIClient,
                                  lookback_days: int) -> dict[int, dict]:
    """{journal entry id: full entry} for this character's
    `market_transaction` and `transaction_tax` journal entries within
    `lookback_days` - reconcile_realized_trades uses these to find a
    specific sell's real gross amount and its real tax deduction (see
    T1-01's comment above _MARKET_TRANSACTION_REF_TYPE for the linkage -
    NOT the wallet-transaction's own `journal_ref_id`, confirmed live to
    never match). Best-effort: any ESI failure (missing scope, outage, ...)
    returns {} rather than raising, so a wallet-journal problem degrades
    reconciliation to the fully-modeled formula instead of blocking it
    entirely - same spirit as this module's other best-effort fallbacks
    (_type_info below)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        entries = client.character_wallet_journal(character_id, auth_role=auth_role)
    except Exception:  # noqa: BLE001 - best-effort; modeled fallback is always safe
        return {}
    return {
        entry["id"]: entry
        for entry in entries
        if entry.get("ref_type") in (_MARKET_TRANSACTION_REF_TYPE, _TRANSACTION_TAX_REF_TYPE)
        and _parse_iso(entry["date"]) >= cutoff
    }


def fetch_recent_transactions(character_id: int, auth_role: str, client: ESIClient,
                               lookback_days: int) -> list[dict]:
    """Pages through character_wallet_transactions via `from_id` (cursor
    pagination, oldest transaction_id of the previous page) until either a
    page's oldest transaction is older than the lookback cutoff, or a
    short page (< WALLET_TRANSACTIONS_PAGE_SIZE) signals there's nothing
    older left at all - a single un-paginated call only ever sees the most
    recent 2500 transactions, which silently dropped older-but-still-in-
    window trades for any character with more transaction volume than that
    within `lookback_days` (confirmed real-world symptom: a frequently-traded
    item like Oxygen Isotopes missing from Realized Trades even though it
    was clearly sold within the lookback window).

    T1-01 follow-up (independent challenge pass, 2026-09-26): dedupes by
    transaction_id across pages - live production logs show recurring
    duplicate-key errors on this same boundary-transaction shape in the
    persisted-snapshot sync path (esi_data/fetchers.py's own
    _page_wallet_transactions, fixed the same way), and this live-fetch
    path shares the identical from_id-cursor pattern - without this, a
    boundary transaction entering FIFO reconciliation twice would
    double-count its quantity and profit whenever it's a structure sale."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    all_txns: list[dict] = []
    seen_ids: set[int] = set()
    from_id: Optional[int] = None
    while True:
        page = client.character_wallet_transactions(character_id, auth_role=auth_role, from_id=from_id)
        if not page:
            break
        new_on_this_page = [t for t in page if t["transaction_id"] not in seen_ids]
        all_txns.extend(new_on_this_page)
        seen_ids.update(t["transaction_id"] for t in new_on_this_page)
        oldest = min(page, key=lambda t: t["transaction_id"])
        # len(page) < WALLET_TRANSACTIONS_PAGE_SIZE as the "no more pages"
        # signal relies on ESI's per-call cap staying fixed at 2500 - correct
        # today, but would silently under-page (looking identical to "no
        # older transactions left") if CCP ever lowered it.
        if _parse_iso(oldest["date"]) < cutoff or len(page) < WALLET_TRANSACTIONS_PAGE_SIZE:
            break
        from_id = oldest["transaction_id"]
    in_window = [t for t in all_txns if _parse_iso(t["date"]) >= cutoff]
    for t in in_window:
        t["_wallet_kind"] = "character"
        t["_wallet_owner_id"] = character_id
        t["_wallet_division"] = None
    return in_window


def _fmt_divisions(ids: list[int]) -> str:
    return ", ".join(str(i) for i in ids)


def _wallet_divisions(cfg: TradingConfig) -> tuple[int, ...]:
    """Empty wallet_division_ids means all seven ESI divisions, matching
    ProductionConfig.stock_hangar_flags' empty-means-all behaviour."""
    if not cfg.wallet_division_ids:
        return WALLET_DIVISION_IDS
    return tuple(cfg.wallet_division_ids)


def fetch_recent_corporation_transactions(corporation_id: int, division: int, auth_role: str,
                                           client: ESIClient, lookback_days: int) -> list[dict]:
    """Pages corporation_wallet_transactions via `from_id` (same cursor
    scheme as fetch_recent_transactions). ESI's per-call cap is 2500 for
    this endpoint too (swagger maxItems, confirmed 2026-09-20) so
    WALLET_TRANSACTIONS_PAGE_SIZE is shared. Raises ESIError —
    fetch_corporation_wallet_streams catches that per division so one
    unread wallet does not discard the others. Dedupes by transaction_id
    across pages - see fetch_recent_transactions' own T1-01 follow-up
    comment for why."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    all_txns: list[dict] = []
    seen_ids: set[int] = set()
    from_id: Optional[int] = None
    while True:
        page = client.corporation_wallet_transactions(
            corporation_id, division, auth_role=auth_role, from_id=from_id)
        if not page:
            break
        new_on_this_page = [t for t in page if t["transaction_id"] not in seen_ids]
        all_txns.extend(new_on_this_page)
        seen_ids.update(t["transaction_id"] for t in new_on_this_page)
        oldest = min(page, key=lambda t: t["transaction_id"])
        if _parse_iso(oldest["date"]) < cutoff or len(page) < WALLET_TRANSACTIONS_PAGE_SIZE:
            break
        from_id = oldest["transaction_id"]
    in_window = [t for t in all_txns if _parse_iso(t["date"]) >= cutoff]
    for t in in_window:
        t["_wallet_kind"] = "corporation"
        t["_wallet_owner_id"] = corporation_id
        t["_wallet_division"] = division
    return in_window


def fetch_recent_corporation_journal_entries(corporation_id: int, division: int, auth_role: str,
                                              client: ESIClient, lookback_days: int) -> dict[int, dict]:
    """{journal entry id: full entry} for one corp wallet division's
    `market_transaction` and `transaction_tax` entries within
    `lookback_days`, same as fetch_recent_journal_entries (see T1-01's
    comment above _MARKET_TRANSACTION_REF_TYPE). Best-effort, same
    contract as fetch_recent_journal_entries: any ESI failure returns {}
    rather than raising, so a journal problem degrades that wallet to the
    modeled haircut instead of blocking reconciliation. Role/scope failure
    for the corp as a whole is detected on the transactions fetch, not here.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        entries = client.corporation_wallet_journal(
            corporation_id, division, auth_role=auth_role)
    except Exception:  # noqa: BLE001 - best-effort; modeled fallback is always safe
        return {}
    return {
        entry["id"]: entry
        for entry in entries
        if entry.get("ref_type") in (_MARKET_TRANSACTION_REF_TYPE, _TRANSACTION_TAX_REF_TYPE)
        and _parse_iso(entry["date"]) >= cutoff
    }


def _corps_for_characters(characters: list[tuple[int, str]],
                           client: ESIClient) -> dict[int, list[tuple[int, str]]]:
    """corporation_id -> member (character_id, auth_role) pairs, in the
    original character-list order so the first-registered character is
    tried first (same as production/esi_sync.py). A character listed as both
    buyer and seller contributes both role keys — they are different tokens,
    and only one of them may hold the corp-wallets scope after a partial
    re-auth."""
    by_corp: dict[int, list[tuple[int, str]]] = {}
    seen: set[tuple[int, int, str]] = set()
    for character_id, role in characters:
        try:
            info = client.character_public_info(character_id)
        except Exception:  # noqa: BLE001 - skip this character's corp, don't abort reconcile
            log.warning("Skipping corp discovery for character %s: public-info fetch failed",
                        character_id, exc_info=True)
            continue
        corporation_id = info.get("corporation_id") if isinstance(info, dict) else None
        if not corporation_id:
            continue
        key = (corporation_id, character_id, role)
        if key in seen:
            continue
        seen.add(key)
        by_corp.setdefault(corporation_id, []).append((character_id, role))
    return by_corp


def fetch_corporation_wallet_streams(characters: list[tuple[int, str]], client: ESIClient,
                                      txn_lookback_days: int, journal_lookback_days: int,
                                      cfg: TradingConfig,
                                      corps: Optional[dict[int, list[tuple[int, str]]]] = None,
                                      ) -> tuple[list[dict], dict[tuple, dict]]:
    """Corp wallet transactions + namespaced journal entries for every corp
    a registered buyer/seller belongs to.

    Each corp is fetched once. Member characters are tried in list order.
    A member is usable if it can read any configured division
    (Accountant / Junior_Accountant + the corp-wallets scope, and any
    in-game per-division wallet grant); remaining unread divisions are
    logged as a partial miss and are not topped up from a later member
    (that would re-fetch the readable divisions and double-count). A
    member that cannot read any configured division is skipped and the
    next member is tried. A corp no registered character can serve at all
    is skipped non-fatally — character-wallet matching still runs.
    """
    divisions = _wallet_divisions(cfg)
    txns: list[dict] = []
    journal: dict[tuple, dict] = {}
    if corps is None:
        corps = _corps_for_characters(characters, client)
    for corporation_id, members in corps.items():
        fetched_any = False
        member_failures: list[str] = []
        for character_id, role in members:
            readable: list[int] = []
            unread: list[int] = []
            corp_txns: list[dict] = []
            last_error: Optional[BaseException] = None
            for division in divisions:
                try:
                    corp_txns.extend(fetch_recent_corporation_transactions(
                        corporation_id, division, role, client, txn_lookback_days))
                except ESIError as e:
                    last_error = e
                    unread.append(division)
                    continue
                readable.append(division)
            if not readable:
                member_failures.append(
                    f"character {character_id} ({role}): {last_error}")
                continue
            for division in readable:
                for jid, entry in fetch_recent_corporation_journal_entries(
                    corporation_id, division, role, client, journal_lookback_days,
                ).items():
                    journal[("corporation", corporation_id, division, jid)] = entry
            txns.extend(corp_txns)
            fetched_any = True
            if unread:
                log.warning(
                    "Corporation %s wallet: character %s (%s) could read "
                    "divisions %s but not %s; using the readable divisions "
                    "only (not retrying another member — that would "
                    "double-count). Last unread error: %s",
                    corporation_id, character_id, role,
                    _fmt_divisions(readable), _fmt_divisions(unread),
                    last_error,
                )
            break
        if not fetched_any:
            log.warning(
                "Skipping corporation %s wallet: no registered character "
                "could read any of configured divisions %s "
                "(Accountant/Junior_Accountant plus corp-wallets scope, "
                "and per-division wallet access). Tried: %s",
                corporation_id, _fmt_divisions(list(divisions)),
                "; ".join(member_failures) or "no members",
            )
    return txns, journal


def _iso_date(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _txn_from_snapshot(row: dict) -> dict:
    owner_type = row["owner_type"]
    division = row["division"]
    return {
        "transaction_id": row["transaction_id"],
        "date": _iso_date(row["date"]),
        "type_id": row["type_id"],
        "location_id": row["location_id"],
        "unit_price": row["unit_price"],
        "quantity": row["quantity"],
        "is_buy": bool(row["is_buy"]),
        "journal_ref_id": row["journal_ref_id"],
        "_wallet_kind": owner_type,
        "_wallet_owner_id": int(row["owner_id"]),
        "_wallet_division": None if owner_type == "character" else int(division),
    }


def _journal_from_snapshot(rows: list[dict]) -> dict[tuple, dict]:
    """The persisted `esi_wallet_journal` table (docs/esi_access_schema.sql)
    stores only (division, journal_id, date, ref_type, amount) - it has no
    context_id/context_id_type column, so a snapshot-sourced entry can never
    resolve through T1-01's real-tax linkage (see the comment above
    _MARKET_TRANSACTION_REF_TYPE) no matter what's returned here - it will
    always safely fall back to the modeled cfg.structure_sell_haircut
    formula. Extending that schema to carry context_id/context_id_type is a
    deliberately deferred, separate follow-up (a live schema migration),
    not part of this fix. Kept shape-compatible (dict[tuple, dict], one
    entry per key) with the live-fetch paths regardless, so callers don't
    need to know which source a given entry came from."""
    journal: dict[tuple, dict] = {}
    for entry in rows:
        if entry.get("ref_type") != _MARKET_TRANSACTION_REF_TYPE:
            continue
        owner_type = entry["owner_type"]
        division = None if owner_type == "character" else entry["division"]
        journal[(owner_type, int(entry["owner_id"]), division, entry["journal_id"])] = {
            "id": entry["journal_id"],
            "date": _iso_date(entry["date"]),
            "ref_type": entry["ref_type"],
            "amount": entry["amount"],
        }
    return journal


def collect_trading_wallet_streams(
    buyer_characters: list[tuple[int, str]],
    seller_characters: list[tuple[int, str]],
    client: ESIClient,
    cfg: TradingConfig = TRADING_CONFIG,
) -> tuple[list[dict], dict[tuple, dict], int]:
    """Per-owner wallet rows for Trading, sharing-gated (decision 9).

    For each character and each corporation derived from them:
    - no sharing row → omit that owner, do not live-fetch
    - sharing row, snapshot present → use the snapshot
    - sharing row, snapshot empty → live-fetch that owner only

    AccessorError and storage.connect()'s missing-tenant RuntimeError
    propagate. Always returns lists (possibly empty), never `(None, None)`.
    `reconcile_realized_trades(..., snapshot_txns=None)` stays the
    unrestricted live path used by Phase 8 tests.

    The third element is how many owners (characters + corporations) were
    actually readable, i.e. passed the sharing gate. Callers need this to
    tell "read every shared wallet, there were no trades in the window"
    (empty result is the truth) from "every owner was skipped because
    nothing shares Wallet with Trading" (empty result is an access
    problem). Confirmed real, destructive bug 2026-09-21: the two were
    indistinguishable, and do_reconcile_trades feeds the result straight
    into storage.save_realized_trades, which DELETEs the whole table
    before inserting - so a tenant who shared only Market Orders (not
    Wallet) silently wiped their entire realized-trade history the next
    time Reconcile Trades ran, and Portfolio/Profit-per-Day went to
    zero with `matched_trades: 0` looking like "nothing to match".
    """
    from .esi_data.access import is_shared, read_esi

    buy_lookback = cfg.lookback_days * _BUY_LOOKBACK_MULTIPLIER
    sell_lookback = cfg.lookback_days
    txns: list[dict] = []
    journal: dict[tuple, dict] = {}

    roles_by_id: dict[int, str] = {}
    buyer_ids = {cid for cid, _role in buyer_characters}
    seller_ids = {cid for cid, _role in seller_characters}
    for cid, role in list(buyer_characters) + list(seller_characters):
        roles_by_id.setdefault(cid, role)

    readable_owners = 0
    for character_id, role in roles_by_id.items():
        if not is_shared("wallet", "trading", "character", character_id):
            continue
        readable_owners += 1
        snap = read_esi(
            "wallet", "trading", owner_type="character", owner_id=character_id,
            table="transactions",
        )
        journal_rows = read_esi(
            "wallet", "trading", owner_type="character", owner_id=character_id,
            table="journal",
        )
        if snap:
            txns.extend(_txn_from_snapshot(t) for t in snap)
            journal.update(_journal_from_snapshot(journal_rows))
            continue
        lookback = buy_lookback if character_id in buyer_ids else sell_lookback
        txns.extend(fetch_recent_transactions(character_id, role, client, lookback))
        if character_id in seller_ids:
            for jid, entry in fetch_recent_journal_entries(
                character_id, role, client, sell_lookback,
            ).items():
                journal[("character", character_id, None, jid)] = entry

    seen_pairs: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for pair in list(buyer_characters) + list(seller_characters):
        if pair not in seen:
            seen.add(pair)
            seen_pairs.append(pair)
    discovered = _corps_for_characters(seen_pairs, client)
    live_corps: dict[int, list[tuple[int, str]]] = {}
    for corporation_id, members in discovered.items():
        if not is_shared("wallet", "trading", "corporation", corporation_id):
            continue
        readable_owners += 1
        snap = read_esi(
            "wallet", "trading", owner_type="corporation", owner_id=corporation_id,
            table="transactions",
        )
        journal_rows = read_esi(
            "wallet", "trading", owner_type="corporation", owner_id=corporation_id,
            table="journal",
        )
        if snap:
            txns.extend(_txn_from_snapshot(t) for t in snap)
            journal.update(_journal_from_snapshot(journal_rows))
            continue
        live_corps[corporation_id] = members
    if live_corps:
        corp_txns, corp_journal = fetch_corporation_wallet_streams(
            seen_pairs, client, buy_lookback, sell_lookback, cfg, corps=live_corps,
        )
        txns.extend(corp_txns)
        journal.update(corp_journal)
    return txns, journal, readable_owners


def reconcile_realized_trades(buyer_characters: list[tuple[int, str]], seller_characters: list[tuple[int, str]],
                               client: ESIClient, item_names: dict[int, str],
                               item_volumes: dict[int, float],
                               cfg: TradingConfig = TRADING_CONFIG,
                               snapshot_txns: Optional[list[dict]] = None,
                               snapshot_journal: Optional[dict[tuple, dict]] = None) -> list[RealizedTrade]:
    """Matches every buyer character's Jita buy transactions against every
    seller character's structure sell transactions per type_id, FIFO, within
    cfg.lookback_days, then the same for corporation-wallet fills of any corp
    those characters belong to. `buyer_characters`/`seller_characters` are
    lists of (character_id, auth_role) pairs - GitHub issue #46: multiple
    buyer/seller characters are pooled together (every buyer's buys vs. every
    seller's sells, not paired 1:1 by character), matching how the shortlist's
    own "own orders remaining"/undercut checks already pool across characters.
    Corp fills join those same pools; they are not paired to the token
    character that fetched them. See this module's docstring for how the two
    streams stay distinct.
    """
    buy_lookback = cfg.lookback_days * _BUY_LOOKBACK_MULTIPLIER
    sell_lookback = cfg.lookback_days
    sell_cutoff = datetime.now(timezone.utc) - timedelta(days=sell_lookback)

    buys: list[dict] = []
    sells: list[dict] = []
    journal_entries_by_key: dict[tuple, dict] = {}

    if snapshot_txns is not None:
        buyer_ids = {cid for cid, _role in buyer_characters}
        seller_ids = {cid for cid, _role in seller_characters}
        buy_cutoff = datetime.now(timezone.utc) - timedelta(days=buy_lookback)
        for t in snapshot_txns:
            kind = t.get("_wallet_kind", "character")
            owner_id = t.get("_wallet_owner_id")
            if kind == "character":
                if owner_id in buyer_ids and t.get("is_buy") and _parse_iso(t["date"]) >= buy_cutoff:
                    buys.append(t)
                if owner_id in seller_ids and not t.get("is_buy") and _parse_iso(t["date"]) >= sell_cutoff:
                    sells.append(t)
            else:
                if t.get("is_buy") and _parse_iso(t["date"]) >= buy_cutoff:
                    buys.append(t)
                elif not t.get("is_buy") and _parse_iso(t["date"]) >= sell_cutoff:
                    sells.append(t)
        if snapshot_journal:
            journal_entries_by_key.update(snapshot_journal)
    else:
        for character_id, role in buyer_characters:
            buys.extend(fetch_recent_transactions(character_id, role, client, buy_lookback))
        for character_id, role in seller_characters:
            sells.extend(fetch_recent_transactions(character_id, role, client, sell_lookback))

        # PB-03/T1-01: real per-fill journal entries, namespaced by wallet
        # so a character journal id cannot satisfy a corp sell (or vice
        # versa). See fetch_recent_journal_entries and the comment above
        # _MARKET_TRANSACTION_REF_TYPE for the linkage/formula.
        for character_id, role in seller_characters:
            for jid, entry in fetch_recent_journal_entries(
                character_id, role, client, sell_lookback,
            ).items():
                journal_entries_by_key[("character", character_id, None, jid)] = entry

        seen_pairs: list[tuple[int, str]] = []
        seen: set[tuple[int, str]] = set()
        for pair in list(buyer_characters) + list(seller_characters):
            if pair not in seen:
                seen.add(pair)
                seen_pairs.append(pair)
        corp_txns, corp_journal = fetch_corporation_wallet_streams(
            seen_pairs, client, buy_lookback, sell_lookback, cfg)
        journal_entries_by_key.update(corp_journal)
        for t in corp_txns:
            if t.get("is_buy"):
                buys.append(t)
            elif _parse_iso(t["date"]) >= sell_cutoff:
                sells.append(t)

    # Confirmed real bug: unlike `sells` (correctly scoped to cfg.structure_id
    # below), `buys` had no location filter at all - any wallet transaction
    # by the buyer character anywhere, not just The Forge (matching this
    # module's own "buyer imports in Jita" docstring - confirmed with the
    # user that this means the whole region, not just Jita's own solar
    # system, since a trader can legitimately buy from any station in The
    # Forge), could enter the FIFO match and get paired against an unrelated
    # structure sale, producing a wrong landed/profit/margin for that trade.
    # Wallet transactions only carry a station/structure location_id (no
    # region_id), so resolve every NPC station in cfg.jita_region_id from the
    # local SDE cache instead. Corp buys/sells go through the same filters.
    jita_region_stations = storage.get_station_ids_in_region(cfg.jita_region_id)
    buys = [t for t in buys if t.get("is_buy") and t.get("location_id") in jita_region_stations]
    sells = [t for t in sells if not t.get("is_buy") and t.get("location_id") == cfg.structure_id]

    # T1-01: index market_transaction entries by the wallet transaction they
    # actually belong to (context_id/context_id_type - see the comment above
    # _MARKET_TRANSACTION_REF_TYPE), NOT by journal_ref_id. Built once here
    # rather than per-sell for efficiency.
    market_transaction_by_key: dict[tuple, dict] = {}
    for (kind, owner_id, division, _entry_id), entry in journal_entries_by_key.items():
        if (entry.get("ref_type") == _MARKET_TRANSACTION_REF_TYPE
                and entry.get("context_id_type") == "market_transaction_id"
                and entry.get("context_id") is not None):
            market_transaction_by_key[(kind, owner_id, division, entry["context_id"])] = entry

    # T1-01 follow-up (independent challenge pass, 2026-09-26): the id+1
    # adjacency check alone doesn't verify the tax entry actually belongs
    # to THIS sale - it only checks ref_type and a matching date string.
    # Demonstrated real failure: two sells whose market_transaction/
    # transaction_tax journal entries interleave within the same second
    # (id order MT_A, MT_B, TAX_A, TAX_B rather than the usual MT_A, TAX_A,
    # MT_B, TAX_B) makes sell B's id+1 land on TAX_A instead of its own -
    # same date, right ref_type, wrong sale - producing a wildly wrong
    # profit (a small sale inheriting a much larger sale's tax deduction).
    # A real EVE sales tax is a small, bounded fraction of gross (T1-01's
    # own live-verified figure was 3.375%; the true base rate is 8% at
    # Accounting 0) - reject a pairing whose implied rate falls outside a
    # generous but finite band instead of trusting id+1 blindly. This does
    # not fully solve misattribution between two same-second sales of
    # similar size (a plausible-looking wrong pairing wouldn't trip this
    # guard). Definitively checked live, 2026-09-26 (4130 real
    # transaction_tax entries from this same seller character, every one
    # inspected): ESI never puts a context_id/context_id_type on this
    # ref_type at all - exactly one key-set (amount/balance/date/
    # description/first_party_id/id/reason/ref_type/second_party_id) across
    # all 4130. There is no field ESI provides that would close this gap -
    # the plausibility-ratio guard above is the ceiling of what this
    # linkage mechanism can verify, not a stopgap pending more data.
    _MAX_PLAUSIBLE_TAX_RATE = 0.15

    def _real_net_sell_per_unit(sell: dict) -> Optional[float]:
        """The real (gross - sales tax) per-unit proceeds for `sell`, or
        None if the market_transaction/transaction_tax pair can't be
        confidently located - callers must fall back to the fully modeled
        cfg.structure_sell_haircut formula in that case, never guess."""
        if not sell.get("quantity"):
            return None
        key = (sell.get("_wallet_kind", "character"), sell.get("_wallet_owner_id"),
               sell.get("_wallet_division"), sell.get("transaction_id"))
        mt = market_transaction_by_key.get(key)
        if mt is None:
            return None
        tax_key = (key[0], key[1], key[2], mt["id"] + 1)
        tax_entry = journal_entries_by_key.get(tax_key)
        if (tax_entry is None or tax_entry.get("ref_type") != _TRANSACTION_TAX_REF_TYPE
                or tax_entry.get("date") != mt.get("date")):
            return None
        real_tax = -tax_entry["amount"]
        gross = mt["amount"]
        if not gross or not (0 <= real_tax / gross <= _MAX_PLAUSIBLE_TAX_RATE):
            return None
        return (gross - real_tax) / sell["quantity"]

    buys_by_type: dict[int, list[dict]] = defaultdict(list)
    for t in buys:
        buys_by_type[t["type_id"]].append(t)
    for lst in buys_by_type.values():
        lst.sort(key=lambda t: t["date"])

    sells_by_type: dict[int, list[dict]] = defaultdict(list)
    for t in sells:
        sells_by_type[t["type_id"]].append(t)
    for lst in sells_by_type.values():
        lst.sort(key=lambda t: t["date"])

    # item_names/item_volumes only cover the *current* shortlist - a type traded
    # historically but since removed (or never added, e.g. incidental moon/ice
    # product income) falls through both. Backfill those on demand from ESI's
    # public /universe/types/ endpoint instead of guessing, and cache per type_id
    # so each one is only fetched once regardless of how many trades match it.
    names = dict(item_names)
    volumes = dict(item_volumes)

    def _type_info(type_id: int) -> None:
        if type_id in volumes:
            return
        try:
            info = client.get_type_info(type_id)
        except Exception:  # noqa: BLE001 - best-effort backfill, never block reconciliation
            volumes[type_id] = 0.0
            return
        volumes[type_id] = info.get("volume") or 0.0
        names.setdefault(type_id, info.get("name", str(type_id)))

    results: list[RealizedTrade] = []
    for type_id, sell_txns in sells_by_type.items():
        buy_queue = [dict(t) for t in buys_by_type.get(type_id, [])]
        buy_idx = 0
        _type_info(type_id)
        for sell in sell_txns:
            remaining_to_match = sell["quantity"]
            while remaining_to_match > 0 and buy_idx < len(buy_queue):
                buy = buy_queue[buy_idx]
                if buy["date"] > sell["date"]:
                    # Confirmed real bug (business-logic audit, 2026-08-29):
                    # a sale can't be funded by inventory bought *after* it
                    # sold - but FIFO here only ordered buys chronologically,
                    # never checked a matched buy actually predates its sell.
                    # Live evidence: 324 of 1640 realized_trades rows (20%)
                    # had buy_date > sell_date, accounting for 21.8% of the
                    # reported net realized profit - happens whenever a sell
                    # has no in-window buy old enough to be its real cost
                    # basis (most commonly: pre-window inventory, bought
                    # before cfg.lookback_days even started) and FIFO reached
                    # for the next available buy regardless of its date.
                    # buy_queue is sorted ascending and buy_idx never
                    # rewinds, so every later buy is >= this one's date too -
                    # break (not skip past it) leaves it for a later,
                    # actually-later-dated sell to still reach; the
                    # unmatched remainder of *this* sell is simply dropped,
                    # same "no real data yet" honesty as elsewhere in this
                    # module (see average_daily_sold_by_type's own docstring)
                    # rather than fabricating a cost basis. Doesn't recover a
                    # sell's true pre-window cost basis (a separate, larger
                    # fix - seed the FIFO queue with real pre-window
                    # inventory) - this only stops it from silently
                    # substituting a wrong, later one.
                    break
                matched = min(remaining_to_match, buy["quantity"])
                if matched <= 0:
                    buy_idx += 1
                    continue
                # cfg.import_cost_per_m3 is an ISK-per-m3 *rate*, never a flat
                # per-unit fee - freight must always scale with the item's own
                # per-unit volume, or cheap/small/bulk-traded items (ammo, ice
                # products, ...) get a wildly overstated landed cost.
                freight = volumes[type_id] * cfg.import_cost_per_m3
                # jita_buy_broker_fee is still fully *modeled* (config.py) -
                # broker's fee is charged once per order, not per fill, so it
                # can't be attributed to this specific matched buy the way
                # sales tax can be (see PB-03's comment above) - real
                # skill/standing changes over the lookback window could still
                # make this side of Realized Trades drift from what actually
                # landed in the wallet.
                landed = buy["unit_price"] * (1 + cfg.jita_buy_broker_fee) + freight
                real_net_sell = _real_net_sell_per_unit(sell)
                if real_net_sell is not None:
                    # T1-01: real gross sale amount minus the real per-sale
                    # sales tax (both from ESI's wallet journal - see the
                    # comment above _MARKET_TRANSACTION_REF_TYPE), minus the
                    # modeled broker's-fee-only estimate (it can't be
                    # attributed to one specific fill the way tax now can).
                    net_sell = real_net_sell * (1 - cfg.structure_broker_fee)
                else:
                    net_sell = sell["unit_price"] * cfg.structure_sell_haircut
                profit_per_unit = net_sell - landed
                results.append(RealizedTrade(
                    type_id=type_id,
                    item=names.get(type_id, str(type_id)),
                    buy_date=buy["date"], buy_qty=buy["quantity"], buy_unit_price=buy["unit_price"],
                    sell_date=sell["date"], sell_qty=sell["quantity"], sell_unit_price=sell["unit_price"],
                    matched_qty=matched,
                    realized_profit=profit_per_unit * matched,
                    margin=(profit_per_unit / landed) if landed else 0.0,
                ))
                buy["quantity"] -= matched
                remaining_to_match -= matched
                if buy["quantity"] <= 0:
                    buy_idx += 1
    results.sort(key=lambda r: r.sell_date)
    return results


def summarize_realized(trades: list[RealizedTrade]) -> dict:
    """Equivalent of the 'Gesamtgewinn' / 'Durchschnittsmarge' / Top-3 block."""
    total_profit = sum(t.realized_profit for t in trades)
    weighted_denom = sum(t.buy_unit_price * t.matched_qty for t in trades)
    avg_margin = (total_profit / weighted_denom) if weighted_denom else 0.0

    by_item: dict[str, float] = defaultdict(float)
    for t in trades:
        by_item[t.item] += t.realized_profit
    top3 = sorted(by_item.items(), key=lambda kv: kv[1], reverse=True)[:3]

    return {
        "total_realized_profit": total_profit,
        "average_margin": avg_margin,
        "top3_items_by_profit": top3,
    }


def average_daily_sold_by_type(cfg: TradingConfig = TRADING_CONFIG) -> dict[int, float]:
    """Real average daily *sold* quantity per type_id, computed from the last
    Reconcile Trades run's realized_trades rows (storage.save_realized_trades
    wholesale-replaces that table every run with exactly one
    cfg.lookback_days window's worth of matched sells, not an accumulating
    log - see that function's own comment). Sums `matched_qty` (the actual
    FIFO-matched sale amount, not the original transaction's full
    buy_qty/sell_qty, which can span multiple matches) per type_id and
    divides by cfg.lookback_days.

    GitHub issue #51: this - not the structure's live order-book remaining
    quantity (esi_client.OrderStats.sell_volume, "how much is listed right
    now") - is what the Shortlist's "Profit / Day" figure is computed from.
    An item never actually sold (e.g. a fresh candidate with a large order
    book from a single seller) is simply absent here, not estimated from
    listed quantity - see shortlist.evaluate_shortlist_item's
    avg_daily_sold parameter.

    Returns {} if Reconcile Trades has never been run (or found nothing to
    match) - every item's avg_daily_sold then stays None, an honest "no real
    sales data yet" rather than a number derived from something else."""
    df = storage.read_table("realized_trades")
    if df.empty or cfg.lookback_days <= 0:
        return {}
    latest = df[df["run_ts"] == df["run_ts"].max()]
    sold = latest.groupby("type_id")["matched_qty"].sum()
    return {int(type_id): float(qty) / cfg.lookback_days for type_id, qty in sold.items()}
