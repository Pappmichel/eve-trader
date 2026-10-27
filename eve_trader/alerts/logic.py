"""Pure alert decisions - no I/O, no clock, no storage. The scheduler job (later)
feeds these stored data and sends whatever they return."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

SKILLQUEUE_EMPTY = "skillqueue_empty"
MAIL_NEW = "mail_new"
PI_EXTRACTOR_EXPIRY = "pi_extractor_expiry"
PI_PAD_FULL = "pi_pad_full"
PI_INPUTS_EMPTY = "pi_inputs_empty"
PI_ALERT_TYPES = (PI_EXTRACTOR_EXPIRY, PI_PAD_FULL, PI_INPUTS_EMPTY)
CONTRACT_STATUS = "contract_status"
ALERT_TYPES = (SKILLQUEUE_EMPTY, MAIL_NEW) + PI_ALERT_TYPES + (CONTRACT_STATUS,)
DEFAULT_LEAD_HOURS = 12
MIN_LEAD_HOURS, MAX_LEAD_HOURS = 1, 168
MAX_SUBJECT_CHARS = 100
MAX_MAILS_LISTED = 5


@dataclass(frozen=True)
class Decision:
    """`key` is the dedupe key to store once the message went out."""
    key: str
    message: str


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours(delta_seconds: float) -> str:
    hours = delta_seconds / 3600
    return f"{hours:.0f} h" if hours >= 2 else f"{max(1, round(delta_seconds / 60))} min"


def skillqueue_decision(
    finish_dates: Sequence[Optional[str]], now: datetime, lead_hours: int, last_key: Optional[str],
    character_name: str,
) -> Optional[Decision]:
    """`finish_dates` is one entry per queue row (None = paused row). Empty
    sequence = an empty queue. Returns None while the queue is comfortably
    long or this exact situation was already announced (`last_key`)."""
    if not finish_dates:
        decision = Decision("empty", f"{character_name}: the skill queue is empty.")
    elif any(f is None or parse_iso(f) is None for f in finish_dates):
        decision = Decision("paused", f"{character_name}: the skill queue is paused.")
    else:
        end = max(parse_iso(f) for f in finish_dates)  # type: ignore[type-var]
        remaining = (end - now).total_seconds()
        if remaining <= 0:
            decision = Decision(f"ended:{end.isoformat()}", f"{character_name}: the skill queue has run empty.")
        elif remaining <= lead_hours * 3600:
            decision = Decision(
                f"ending:{end.isoformat()}",
                f"{character_name}: the skill queue ends in {_hours(remaining)}.",
            )
        else:
            return None
    return None if decision.key == last_key else decision


@dataclass(frozen=True)
class MailDecision:
    """`baseline` = first run: remember `newest_id`, send nothing. Otherwise
    `decision` is the message (None = nothing new); `newest_id` is the cursor
    to store after a successful send."""
    newest_id: Optional[int]
    decision: Optional[Decision]
    baseline: bool = False


def mail_decision(
    headers: Sequence[dict], last_seen_mail_id: Optional[int], include_content: bool, character_name: str,
    bodies: Optional[dict[int, str]] = None, senders: Optional[dict[int, str]] = None,
) -> MailDecision:
    """`headers` are ESI mail headers. Only unread mails newer than the
    cursor count. The message always lists sender and subject of the newest
    mails (`senders` maps a header's `from` id to a name; an unresolved one
    reads "unknown sender"); the mail text is added only with `include_content`
    - a settled decision (sender and subject are metadata, the text is not)."""
    ids = [int(h["mail_id"]) for h in headers if "mail_id" in h]
    newest = max(ids) if ids else last_seen_mail_id
    if last_seen_mail_id is None:
        return MailDecision(newest, None, baseline=True)
    fresh = [h for h in headers if int(h["mail_id"]) > last_seen_mail_id and not h.get("is_read")]
    if not fresh:
        # Read mails still advance the cursor so they never come back as new.
        return MailDecision(newest, None)
    n = len(fresh)
    lines = []
    fresh.sort(key=lambda h: int(h["mail_id"]))
    for h in fresh[:MAX_MAILS_LISTED]:
        sender = (senders or {}).get(int(h.get("from") or 0)) or "unknown sender"
        subject = str(h.get("subject") or "(no subject)")[:MAX_SUBJECT_CHARS]
        line = f"- {sender[:MAX_SUBJECT_CHARS]}: {subject}"
        body = (bodies or {}).get(int(h["mail_id"])) if include_content else None
        lines.append(line + (f"\n  {body}" if body else ""))
    if n > MAX_MAILS_LISTED:
        lines.append(f"... and {n - MAX_MAILS_LISTED} more")
    text = f"{character_name}: {n} new EVE mail" + ("s" if n != 1 else "") + ".\n" + "\n".join(lines)
    return MailDecision(newest, Decision(f"mail:{newest}", text))


# ------------------------------------------------------------------- contracts
FINISHED_CONTRACT_STATUSES = frozenset({"finished", "finished_issuer", "finished_contractor"})
CONTRACT_FINISHED, CONTRACT_EXPIRED = "finished", "expired"
CONTRACT_TYPE_LABELS = {"item_exchange": "Item exchange", "courier": "Courier", "auction": "Auction", "loan": "Loan"}
MAX_CONTRACTS_LISTED = 10


def contract_events(contracts: Sequence[dict], character_id: int, now: datetime) -> list[tuple[int, str, dict]]:
    """`(contract_id, event, contract)` for every contract the character
    issued (also on behalf of the corporation) that is finished, or still
    outstanding past its expiry date (ESI has no "expired" status)."""
    out = []
    for c in contracts:
        if int(c.get("issuer_id") or 0) != int(character_id) or "contract_id" not in c:
            continue
        status = c.get("status")
        if status in FINISHED_CONTRACT_STATUSES:
            event = CONTRACT_FINISHED
        elif status == "outstanding" and (parse_iso(c.get("date_expired")) or now) < now:
            event = CONTRACT_EXPIRED
        else:
            continue
        out.append((int(c["contract_id"]), event, c))
    return out


def _isk(value: float) -> str:
    for size, unit, digits in ((1e9, "B", 2), (1e6, "M", 1), (1e3, "k", 0)):
        if abs(value) >= size:
            return f"{value / size:.{digits}f}{unit} ISK"
    return f"{value:.0f} ISK"


def contract_message(events: Sequence[tuple[int, str, dict]], character_name: str,
                     items: Optional[dict[int, str]] = None, places: Optional[dict[int, str]] = None) -> str:
    """One message for the given new events. `items` maps a contract id to a
    short item summary (used when the contract has no title), `places` a
    location id to its name; both are optional."""
    finished = sum(1 for _i, e, _c in events if e == CONTRACT_FINISHED)
    expired = len(events) - finished
    parts = [f"{n} contract{'s' if n != 1 else ''} {what}"
             for n, what in ((finished, "finished"), (expired, "expired")) if n]
    lines = []
    for contract_id, event, c in sorted(events, key=lambda e: e[0])[:MAX_CONTRACTS_LISTED]:
        kind = CONTRACT_TYPE_LABELS.get(c.get("type"), "Contract")
        what = str(c.get("title") or "").strip()[:MAX_SUBJECT_CHARS] or (items or {}).get(contract_id)
        value = float((c.get("reward") if c.get("type") == "courier" else c.get("price")) or 0)
        place = (places or {}).get(int(c.get("start_location_id") or 0))
        line = f"- {event.capitalize()}: {kind}" + (f" \"{what}\"" if what else "")
        line += (f", {_isk(value)}" if value else "") + (f" at {place}" if place else "")
        lines.append(line)
    if len(events) > MAX_CONTRACTS_LISTED:
        lines.append(f"... and {len(events) - MAX_CONTRACTS_LISTED} more")
    return f"{character_name}: " + ", ".join(parts) + ".\n" + "\n".join(lines)


# ------------------------------------------------------------ Planetary Industry
@dataclass(frozen=True)
class PiDecision:
    """One message for one colony and alert type. `key` is stored per
    (character, planet, type) in `pi_alert_state` after a successful send."""
    planet_id: int
    alert_type: str
    key: str
    message: str


def _floor_hour(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()


def _in_hours(hours: float) -> str:
    return "now" if hours <= 0 else "in " + _hours(hours * 3600)


def pi_decisions(
    alert_type: str, colonies: Sequence[dict], now: datetime, lead_hours: int,
    last_keys: dict[tuple[int, str], Optional[str]], character_name: str,
) -> list[PiDecision]:
    """`colonies`: [{"planet_id", "planet_name", "projection"}] where
    `projection` is `pi.colonies.project`'s dict. Nothing is sent for a
    situation whose key equals the last sent key. The expiry decision of a
    colony covers all of its due extractors; its key joins their per-pin keys
    (`expiry:{pin_id}:{expiry}`), so a newly due extractor re-announces.
    Pad-full and inputs-empty are estimates from the colony state at
    `last_update` (ESI does not give the live state), and say so."""
    out: list[PiDecision] = []
    for c in colonies:
        pid = int(c["planet_id"])
        label = c.get("planet_name") or f"planet {pid}"
        proj = c.get("projection") or {}
        key = message = None
        if alert_type == PI_EXTRACTOR_EXPIRY:
            due = []
            for e in proj.get("extractors") or []:
                expiry = parse_iso(e.get("expiry_time"))
                if expiry is None:
                    continue
                hours_left = (expiry - now).total_seconds() / 3600
                if hours_left <= lead_hours:
                    due.append((int(e["pin_id"]), expiry, hours_left, e.get("product_name")))
            if due:
                due.sort(key=lambda d: d[1])
                key = "|".join(f"expiry:{pin}:{exp.isoformat()}" for pin, exp, _h, _p in due)
                lines = [
                    f"- {product or 'extractor'}: " + ("expired" if h <= 0 else f"expires {_in_hours(h)}")
                    for _pin, _exp, h, product in due
                ]
                message = f"{character_name}: extractors on {label} need attention.\n" + "\n".join(lines)
        elif alert_type in (PI_PAD_FULL, PI_INPUTS_EMPTY):
            full = alert_type == PI_PAD_FULL
            at = parse_iso(proj.get("full_at" if full else "inputs_empty_at"))
            if at is not None and (at - now).total_seconds() / 3600 <= lead_hours:
                key = f"{'padfull' if full else 'inputs'}:{pid}:{_floor_hour(at)}"
                what = "storage is estimated to be full" if full else "imported inputs are estimated to run out"
                if not full and proj.get("inputs_empty_type"):
                    what += f" ({proj['inputs_empty_type']})"
                when = _in_hours((at - now).total_seconds() / 3600)
                message = (f"{character_name}: on {label} {what} {when}. "
                           f"Estimate from the colony state of {proj.get('last_update') or 'unknown'}.")
        if key is not None and key != last_keys.get((pid, alert_type)):
            out.append(PiDecision(pid, alert_type, key, message))
    return out
