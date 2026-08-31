"""Read a transaction out of a bank's alert email.

Statements are cut 5-15 days after the month ends, so the most recent fortnight
of spending is always missing from a ledger built only from them. The alerts a
bank sends within minutes of a payment close that gap.

They close it at a cost, and the cost is the whole reason this module is
careful. An alert has no opening balance, no closing balance and no siblings to
add up against - none of the arithmetic this project relies on applies to it.
So every row produced here is marked `source='email_alert'`, is kept out of the
reconciliation gate, and is **superseded** the moment the real statement for
that period arrives (see `supersede_matched`). The statement always wins: it is
checked, the alert is not.

Parsing is by per-issuer template rather than by model. An alert is a fixed
sentence a bank's own system generated, so a regex reads it exactly or not at
all - and "not at all" is a far better outcome than a plausible guess at
somebody's rent.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Body text
# --------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS = re.compile(r"[\s ]+")


def to_text(body: str) -> str:
    """Strip an alert email down to a single line of readable text.

    Bank alerts are HTML with the sentence split across table cells, so the
    tags have to go before any pattern can see the sentence as one string.
    """
    if not body:
        return ""
    cleaned = _STYLE.sub(" ", body)
    cleaned = _TAG.sub(" ", cleaned)
    return _WS.sub(" ", html.unescape(cleaned)).strip()


# --------------------------------------------------------------------------
# Field readers
# --------------------------------------------------------------------------

_AMOUNT = r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)"
_DATE_TOKEN = (r"(\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{2,4}"
               r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
               r"|\d{4}-\d{2}-\d{2})")

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_amount(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def parse_date(raw: str | None, fallback: date | None = None) -> date | None:
    """A date out of any of the shapes Indian banks put in an alert."""
    if not raw:
        return fallback
    token = raw.strip().replace("/", "-").replace(" ", "-")
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%B-%Y", "%d-%m-%Y", "%d-%m-%y",
                "%Y-%m-%d", "%d-%b", "%b-%d-%Y"):
        try:
            parsed = datetime.strptime(token, fmt).date()
        except ValueError:
            continue
        if fmt == "%d-%b" and fallback:
            # A day and month with no year: the alert was sent this year, and
            # the fallback (the email's own date) is the only source for it.
            parsed = parsed.replace(year=fallback.year)
        return parsed
    return fallback


#: A date introduced by "on" is the transaction's own; a bare one might be a
#: due date or a card expiry, so it is only used when there is nothing better.
_ON_DATE = re.compile(rf"\bon\s+{_DATE_TOKEN}", re.IGNORECASE)
_ANY_DATE = re.compile(_DATE_TOKEN)


def date_in(text: str) -> str | None:
    """The transaction date out of an alert, wherever the bank put it.

    Read from the whole sentence rather than from a capture group. Banks put
    the date on either side of the payee - "debited from A/c XX1234 on 15-Aug
    to VPA shop@ybl" and "...to VPA shop@ybl on 15-Aug" are both common - and a
    template can only capture one of those orders. The other used to fall back
    to the email's received date, which is a day or more out and moves a
    month-end payment into the following month.
    """
    match = _ON_DATE.search(text) or _ANY_DATE.search(text)
    return match.group(1) if match else None


_MASK = re.compile(r"(?:x{2,}|\*{2,}|XX)?(\d{4})\b", re.IGNORECASE)


def parse_account(raw: str | None) -> str:
    """The last four digits of whatever account an alert names."""
    if not raw:
        return ""
    match = _MASK.search(raw.strip())
    return match.group(1) if match else ""


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    """One issuer's alert sentence.

    `direction` is fixed per template rather than inferred from the wording.
    "credited" and "debited" appear in the same email often enough - "Rs 500
    debited... available balance credited" - that reading whichever verb comes
    first gets the sign wrong, and a sign error is a two-for-one mistake in
    every total.
    """

    name: str
    direction: str            # debit | credit
    pattern: re.Pattern[str]
    kind: str = "other"       # upi | card | ach | atm | other


def _compile(source: str) -> re.Pattern[str]:
    return re.compile(source, re.IGNORECASE)


#: Ordered most-specific first. The first template that matches wins, so a
#: UPI-shaped alert is recognised as UPI before the generic debit pattern gets
#: a chance to read it as a plain withdrawal.
TEMPLATES: list[Template] = [
    Template(
        "upi-debit", "debit", kind="upi",
        pattern=_compile(
            rf"{_AMOUNT}\s*(?:has been |is |was )?debited\s*from\s*"
            rf"(?:your\s*)?(?:a/?c|account|vpa)\s*(?:no\.?\s*)?"
            rf"(?P<account>[\w*x]*\d{{4}})"
            rf".{{0,80}}?(?:to|towards)\s+(?:vpa\s+)?(?P<payee>[\w@.\- ]{{2,60}}?)"
            rf"(?:\s+on\s+{_DATE_TOKEN})?[\s.]")),
    Template(
        "upi-credit", "credit", kind="upi",
        pattern=_compile(
            rf"{_AMOUNT}\s*(?:has been |is |was )?credited\s*to\s*"
            rf"(?:your\s*)?(?:a/?c|account)\s*(?:no\.?\s*)?"
            rf"(?P<account>[\w*x]*\d{{4}})"
            rf".{{0,80}}?(?:from|by)\s+(?P<payee>[\w@.\- ]{{2,60}}?)"
            rf"(?:\s+on\s+{_DATE_TOKEN})?[\s.]")),
    Template(
        "card-spend", "debit", kind="card",
        pattern=_compile(
            rf"{_AMOUNT}\s*(?:was\s+)?(?:spent|used|charged)\s*(?:on|at|using)?\s*"
            rf"(?:your\s*)?(?:.{{0,30}}?card\s*)?(?:ending\s*(?:with\s*)?)?"
            rf"(?P<account>[\w*x]*\d{{4}})?"
            rf".{{0,60}}?(?:at|towards)\s+(?P<payee>[\w@.\-& ]{{2,60}}?)"
            rf"(?:\s+on\s+{_DATE_TOKEN})?[\s.]")),
    Template(
        "atm-withdrawal", "debit", kind="atm",
        pattern=_compile(
            rf"{_AMOUNT}\s*(?:has been |was )?withdrawn\s*(?:from\s*)?"
            rf"(?:your\s*)?(?:a/?c|account|atm)?\s*(?:no\.?\s*)?"
            rf"(?P<account>[\w*x]*\d{{4}})?"
            rf"(?:.{{0,60}}?on\s+{_DATE_TOKEN})?")),
    Template(
        "generic-debit", "debit",
        pattern=_compile(
            rf"{_AMOUNT}\s*(?:has been |is |was )?debited"
            rf"(?:.{{0,40}}?(?:a/?c|account)\s*(?:no\.?\s*)?"
            rf"(?P<account>[\w*x]*\d{{4}}))?"
            rf"(?:.{{0,80}}?on\s+{_DATE_TOKEN})?")),
    Template(
        "generic-credit", "credit",
        pattern=_compile(
            rf"{_AMOUNT}\s*(?:has been |is |was )?credited"
            rf"(?:.{{0,40}}?(?:a/?c|account)\s*(?:no\.?\s*)?"
            rf"(?P<account>[\w*x]*\d{{4}}))?"
            rf"(?:.{{0,80}}?on\s+{_DATE_TOKEN})?")),
]

#: Wording that means the email is telling you about money that has NOT moved.
#: Without this a "your payment is due" reminder becomes a payment, and a
#: declined transaction becomes a real one.
NOT_A_TRANSACTION = re.compile(
    r"\b(?:will be|would be|is due|due on|reminder|scheduled|has failed|"
    r"failed|declined|unsuccessful|not (?:been )?(?:processed|completed)|"
    r"request(?:ed)? (?:for|to)|otp|one[- ]time password|"
    r"do not share|statement is ready|e-?statement|"
    r"auto[- ]?pay (?:is )?(?:set|scheduled))\b", re.IGNORECASE)


@dataclass
class ParsedAlert:
    amount: Decimal
    direction: str
    txn_date: date | None
    account_suffix: str
    counterparty: str
    kind: str
    template: str
    raw_text: str

    @property
    def description(self) -> str:
        """A narration in the shape the statement pipeline expects.

        Prefixed by rail so the existing UPI detection - which keys off a
        leading "UPI" in the narration - classifies these the same way it
        classifies the statement rows they will later be replaced by.
        """
        prefix = "UPI/" if self.kind == "upi" else "POS/" if self.kind == "card" \
            else "ATM/" if self.kind == "atm" else "TXN/"
        return f"{prefix}{self.counterparty or 'UNKNOWN'}"


def parse_alert(body: str, subject: str = "",
                received: date | None = None) -> ParsedAlert | None:
    """Read one alert email, or return None if it is not one.

    None is the common and correct answer: most mail matching the search is a
    reminder, an OTP or a marketing message, and inventing a transaction from
    any of them is far worse than importing nothing.
    """
    text = to_text(f"{subject} {body}")
    if not text:
        return None
    if NOT_A_TRANSACTION.search(text):
        return None

    for template in TEMPLATES:
        match = template.pattern.search(text)
        if not match:
            continue
        amount = parse_amount(match.group(1))
        if amount is None or amount <= 0:
            continue

        groups = match.groupdict()
        raw_date = date_in(text)

        return ParsedAlert(
            amount=amount,
            direction=template.direction,
            txn_date=parse_date(raw_date, received),
            account_suffix=parse_account(groups.get("account")),
            counterparty=_clean_payee(groups.get("payee")),
            kind=template.kind,
            template=template.name,
            raw_text=text[:400],
        )
    return None


_PAYEE_NOISE = re.compile(
    r"\b(?:on|at|your|the|a/?c|account|ref|no|dated|upi|txn|transaction)\b",
    re.IGNORECASE)


def _clean_payee(raw: str | None) -> str:
    if not raw:
        return ""
    cleaned = _PAYEE_NOISE.sub(" ", raw)
    cleaned = re.sub(r"[^\w@.\-& ]+", " ", cleaned)
    return _WS.sub(" ", cleaned).strip(" .-")[:60]


# --------------------------------------------------------------------------
# Superseding
# --------------------------------------------------------------------------

#: How far apart an alert and its statement row may be dated and still be the
#: same payment. Banks post on the value date, alert on the transaction date,
#: and a weekend puts two days between them.
SUPERSEDE_DAY_WINDOW = 3


def supersede_matched(alerts: Iterable, statement_rows: Iterable) -> int:
    """Flag every alert that the arriving statement now accounts for.

    This is the load-bearing half of the feature. Without it, importing a
    statement for a month whose alerts are already in the ledger counts every
    payment in it twice, and the more diligent the user is the more wrong their
    spending becomes - the same failure mode transfer detection exists to stop.

    The alert is flagged rather than deleted: it really did arrive, and being
    able to see that the checked row replaced it is worth a column.
    """
    by_amount: dict[tuple[str, str], list] = {}
    for row in statement_rows:
        direction = getattr(row.direction, "value", row.direction)
        key = (f"{Decimal(str(row.amount)):.2f}", str(direction))
        by_amount.setdefault(key, []).append(row)

    superseded = 0
    for alert in alerts:
        if getattr(alert, "superseded", False):
            continue
        direction = getattr(alert.direction, "value", alert.direction)
        key = (f"{Decimal(str(alert.amount)):.2f}", str(direction))
        for row in by_amount.get(key, []):
            if not alert.txn_date or not row.txn_date:
                continue
            if abs((row.txn_date - alert.txn_date).days) > SUPERSEDE_DAY_WINDOW:
                continue
            if alert.account_id and row.account_id \
                    and alert.account_id != row.account_id:
                continue
            alert.superseded = True
            alert.excluded = True
            alert.note = (alert.note or "") and f"{alert.note} "
            alert.note += "Replaced by the statement row for this payment."
            superseded += 1
            break
    return superseded
