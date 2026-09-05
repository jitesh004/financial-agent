"""Read a credit card's summary box: limit, what is due, and when.

Everything else in `normalize` is deterministic, and this is not. That is a
deliberate exception and worth stating plainly, because the rest of this app
argues hard against guessing.

A card's summary box resists a reader in a way a transaction table does not:

  * The labels are a HEADER ROW and the figures are somewhere below it, laid
    out by position rather than by adjacency - "Credit Card Number | Credit
    Limit | Available Credit Limit | Available Cash Limit" over a row of
    numbers. Read one line down for the value and Axis hands back the card's
    issuer prefix, so 653047****5207 became a credit limit of 6.5 lakh.
  * The same page is often extracted two or three times over, each pass
    spacing the glyphs differently ("6 53047", "653047**", "Availa ble
    Credit Li mit"), so the label matches on one copy and the value on
    another.
  * Money figures appear in the terms and conditions - the mandated worked
    example of how interest is calculated - in the same words as the real
    summary.

Six issuers, six layouts, and the failure mode is silent: a plausible number
in the right field. The deterministic reader is kept and tried first; it is
right when it fires, and this runs only where it left a gap.

WHAT THIS IS NOT ALLOWED TO DO
------------------------------
It never overrules the WINDOWED deterministic read. That pass looks at the
letterhead and the footer, where a summary belongs, and it is right when it
fires. What this may correct is the wide pass - the one that searches the
whole document, boilerplate included - because that pass has been caught
returning "Total Amount Due 8813.65" from inside Axis's billing-dispute
notice on a statement whose real total is 405.00. Between a figure read out
of the summary box and a figure matched anywhere in twenty thousand
characters, the box wins.

Most of what is read here - the limit, the minimum due, the statement and
due dates, the billing cycle - is not checked by anything and is otherwise
simply absent, so a careful reading is strictly better than nothing.

Answers are cached by a fingerprint of the text sent, so re-parsing the same
statement costs nothing, and a failure anywhere in here returns None rather
than failing the parse around it.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from .parsers import parse_date

log = logging.getLogger(__name__)

#: How much of the document to show the model. The summary box is near the
#: front on every issuer seen, and sending the whole statement would put a
#: year of transaction narrations in front of it for no gain.
_SLICE_CHARS = 3000

#: Anchors that mark where the summary box begins. The slice starts a little
#: before the first one found, so the header row above it is included.
_ANCHORS = (
    r"payment\s*summary", r"statement\s*summary", r"account\s*summary",
    r"total\s*amount\s*due", r"total\s*payment\s*due",
    r"payment\s*due\s*date", r"credit\s*limit",
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "credit_limit": {"type": "string"},
        "total_amount_due": {"type": "string"},
        "minimum_amount_due": {"type": "string"},
        "statement_date": {"type": "string"},
        "payment_due_date": {"type": "string"},
        "billing_cycle_start": {"type": "string"},
        "billing_cycle_end": {"type": "string"},
    },
}

_SYSTEM = (
    "You read Indian credit card statements and return JSON only. "
    "Report only what the statement states outright. Use an empty string "
    "for anything it does not state - never estimate, never carry a figure "
    "over from an example calculation in the terms and conditions, and "
    "never return a card number or any part of one as an amount."
)

_PROMPT = (
    "From this credit card statement summary, report the card's total credit "
    "limit, the total amount due and the minimum amount due this cycle, the "
    "statement date, the "
    "payment due date, and the billing cycle's start and end dates.\n\n"
    "Amounts as plain digits with no currency symbol or grouping. Dates as "
    "YYYY-MM-DD.\n\nStatement:\n"
)

#: A limit below this is not a limit - see `metadata._MIN_PLAUSIBLE_CREDIT_LIMIT`.
_MIN_LIMIT = Decimal("1000")


@dataclass
class CardSummary:
    credit_limit: Decimal | None = None
    total_due: Decimal | None = None
    min_due: Decimal | None = None
    statement_date: date | None = None
    payment_due_date: date | None = None
    cycle_start: date | None = None
    cycle_end: date | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None
                   for f in self.__dataclass_fields__.values())


def summary_slice(text: str) -> str:
    """The part of the statement worth showing a model."""
    lowered = (text or "").lower()
    starts = [m.start() for pattern in _ANCHORS
              for m in [re.search(pattern, lowered)] if m]
    begin = max(0, min(starts) - 400) if starts else 0
    return (text or "")[begin:begin + _SLICE_CHARS]


def _amount(raw: object, bins: set) -> Decimal | None:
    """A money value out of the model's answer, or None.

    `bins` are the issuer prefixes printed on this statement. The system
    prompt forbids returning one and the check stays anyway: the whole reason
    this module exists is that a card number sits where a limit belongs, and
    a rule that matters is enforced rather than requested.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    cleaned = re.sub(r"[^\d.]", "", raw)
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return None if value in bins else value


def _date(raw: object) -> date | None:
    return parse_date(raw) if isinstance(raw, str) and raw.strip() else None


def read(text: str, *, bins: set | None = None) -> CardSummary | None:
    """The card's summary, read by a model. None when it could not be read.

    Never raises: a card with no cycle recorded is a smaller problem than a
    statement that fails to parse.
    """
    from ..llm.client import get_client

    slice_text = summary_slice(text)
    if not slice_text.strip():
        return None
    bins = bins or set()
    fingerprint = hashlib.sha256(slice_text.encode()).hexdigest()

    answer = _cached(fingerprint)
    if answer is None:
        try:
            client = get_client()
            if not client.available:
                return None
            answer = client.complete_json(
                _PROMPT + slice_text, system=_SYSTEM,
                max_tokens=220, schema=_SCHEMA)
        except Exception as exc:
            log.warning("card summary could not be read: %s", exc)
            return None
        if not isinstance(answer, dict):
            return None
        _remember(fingerprint, answer)

    limit = _amount(answer.get("credit_limit"), bins)
    summary = CardSummary(
        credit_limit=limit if limit is not None and limit >= _MIN_LIMIT
        else None,
        total_due=_amount(answer.get("total_amount_due"), bins),
        min_due=_amount(answer.get("minimum_amount_due"), bins),
        statement_date=_date(answer.get("statement_date")),
        payment_due_date=_date(answer.get("payment_due_date")),
        cycle_start=_date(answer.get("billing_cycle_start")),
        cycle_end=_date(answer.get("billing_cycle_end")),
    )
    return None if summary.is_empty() else summary


def _cached(fingerprint: str) -> dict | None:
    """A previous answer for this exact text, if one was stored.

    A broken cache must never cost a parse, so every failure here is a miss.
    """
    try:
        from ..db.database import get_db
        from ..db.repository import get_ai_inference

        return get_ai_inference(get_db(), fingerprint)
    except Exception as exc:
        log.debug("card summary cache unreadable: %s", exc)
        return None


def _remember(fingerprint: str, answer: dict) -> None:
    """Store an answer. Writing needs a signed-in user for row-level
    security, so a parse running outside a request simply does not cache."""
    try:
        from ..db.database import get_db
        from ..db.repository import save_ai_inference

        save_ai_inference(get_db(), fingerprint, answer)
    except Exception as exc:
        log.debug("card summary not cached: %s", exc)
