"""Pure alert decisions - no I/O, no clock, no storage. The scheduler job (later)
feeds these stored data and sends whatever they return."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

SKILLQUEUE_EMPTY = "skillqueue_empty"
MAIL_NEW = "mail_new"
ALERT_TYPES = (SKILLQUEUE_EMPTY, MAIL_NEW)
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
