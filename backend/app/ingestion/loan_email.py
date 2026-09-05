"""Read a loan's standing out of the quarterly summary a lender emails.

Some debts never produce a statement. A home loan is repaid by standing
instruction and the lender sends nothing monthly - so the account exists, the
EMI leaves the bank account every month, and the app has no idea what is
owed. On this holder's ledger that was a 67 lakh liability the Overview could
not see, while the Position tab, reading the credit bureau, could.

What the lender does send is a quarterly summary, in the body of an email
with no attachment at all:

    Loan No                                XX4757833
    Interest Rate (p.a)                    7.15%
    Sanctioned Amount (Rs)                 7500000
    Principal amount recovered till date   777221
    Current EMI Amount (Rs)                64032
    Balance Tenure (No of EMI's Left)      165

That is everything the Debt tab needs and rather more than the bureau
carries: a bureau reports a balance, this reports the rate and the remaining
term as well, which is what makes an amortisation possible.

WHY A TEMPLATE AND NOT A MODEL
------------------------------
The same argument `txn_email` makes. This is a sentence a bank's own system
generated from a form, so a regex reads it exactly or not at all - and "not
at all" is the better failure, because the alternative is a plausible wrong
number for somebody's mortgage. Nothing here is inferred: the outstanding
principal is sanctioned minus recovered, both of which the lender printed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..models.schemas import AccountType
from ..normalize import parsers
from ..rules import institutions

log = logging.getLogger(__name__)

#: Subject wording that marks an email as a loan's periodic summary. Matched
#: against the subject so a scan can find it without an attachment to filter
#: on - see `gmail_source.SCAN_INTENTS`.
SUBJECT_MARKERS = (
    "loan summary", "loan account summary", "loan statement summary",
    "quarterly loan", "annual loan summary", "loan details",
)

#: Labels this reader understands, and the field each one fills. Written as
#: they appear once the HTML is flattened: a label, then its value, with
#: whatever punctuation and asterisks the mailer left behind.
#:
#: Each field holds a TUPLE of alternatives, ordered most specific first,
#: and they are tried one search at a time - never joined with "|". See
#: `_number` for the two ways that goes wrong.
_FIELDS: dict[str, tuple[str, ...]] = {
    "sanctioned": (r"sanctioned\s*amount", r"loan\s*amount"),
    "recovered": (r"principal\s*amount\s*recovered\s*till\s*date",
                  r"principal\s*recovered"),
    "outstanding": (r"principal\s*outstanding", r"outstanding\s*principal",
                    r"outstanding\s*(?:amount|balance)",
                    r"balance\s*outstanding"),
    "emi": (r"current\s*emi\s*amount", r"monthly\s*instal?ment\s*amount",
            r"emi\s*amount"),
    "rate": (r"interest\s*rate\s*\(?\s*p\.?\s*a\.?\s*\)?",
             r"rate\s*of\s*interest", r"interest\s*rate"),
    "tenure_left": (r"balance\s*tenure[^0-9\n]{0,40}",
                    r"remaining\s*tenure"),
    "tenure_total": (r"original\s*loan\s*tenure", r"loan\s*tenure"),
}

#: What kind of loan the lender says this is.
_KIND = r"(housing|home|personal|auto|car|vehicle|education|business)\s*loan"

#: A money or numeric value following a label. Deliberately tight: no line
#: break, and at most a short run of punctuation between the two, so a label
#: at the end of one field cannot reach into the next one's value.
_VALUE = r"[^0-9\n]{0,24}([\d,]+(?:\.\d{1,2})?)"

_AS_OF = re.compile(
    r"as\s*(?:of|on)\s*[:\- ]*(\d{1,2}[-/][A-Za-z0-9]{2,9}[-/]\d{2,4})",
    re.IGNORECASE)

_KIND_TO_TYPE = {
    "housing": AccountType.HOME_LOAN, "home": AccountType.HOME_LOAN,
    "personal": AccountType.PERSONAL_LOAN,
    "auto": AccountType.AUTO_LOAN, "car": AccountType.AUTO_LOAN,
    "vehicle": AccountType.AUTO_LOAN,
    "education": AccountType.PERSONAL_LOAN,
    "business": AccountType.PERSONAL_LOAN,
}


@dataclass
class LoanSummary:
    """What a lender says about one loan, on one date."""

    institution: str = ""
    account_number_masked: str = ""
    account_type: AccountType = AccountType.PERSONAL_LOAN
    sanctioned: Decimal | None = None
    outstanding: Decimal | None = None
    emi: Decimal | None = None
    interest_rate: Decimal | None = None
    months_remaining: int | None = None
    months_total: int | None = None
    as_of: date | None = None

    def is_usable(self) -> bool:
        """Whether this says enough to be worth recording.

        An outstanding balance is the whole point; without one there is
        nothing here the app does not already have.
        """
        return self.outstanding is not None and self.outstanding > 0


def looks_like_loan_summary(subject: str, body: str) -> bool:
    """Whether this email is a lender's periodic summary of a loan."""
    haystack = f"{subject} {body[:600]}".lower()
    if not any(marker in haystack for marker in SUBJECT_MARKERS):
        return False
    # A summary states a balance or an EMI. A marketing mail about loans
    # states neither, and this runs over whatever a scan turns up.
    return bool(re.search(r"sanctioned\s*amount|outstanding|emi\s*amount",
                          body, re.IGNORECASE))


def _number(text: str, alternatives: tuple[str, ...]) -> Decimal | None:
    """The figure one of these labels carries, or None.

    Each alternative is bracketed and tried as its own search, most specific
    first. Two separate traps this avoids:

    - An alternation binds looser than concatenation, so `a|b|c<value>`
      attaches the value to `c` alone and matches the bare words of `a`
      and `b`.
    - `re.search` returns the LEFTMOST match whichever branch found it, and
      the specific label is not always leftmost. HDFC prints "Total
      Pre-EMI/EMI amount due (Rs) 0" above "Current EMI Amount (Rs) 64032",
      so a generic "emi amount" branch took the zero and the real
      instalment was never reached.
    """
    for alternative in alternatives:
        match = re.search(f"(?:{alternative})" + _VALUE, text, re.IGNORECASE)
        if match:
            value = parsers.money(match.group(1))
            if value is not None:
                return value
    return None


def parse(subject: str, body: str, sender: str = "") -> LoanSummary | None:
    """One loan's standing, or None if the email does not carry one."""
    # Flattened here rather than by the caller. A mailer sends HTML, and a
    # reader that assumes plain text works perfectly against a test string
    # and returns nothing at all on the real mail - the same shape of
    # mistake as reading a PDF's tables and forgetting its prose.
    # `txn_email.parse_alert` owns this for its own input for the same
    # reason.
    from .txn_email import to_text

    text = re.sub(r"[*_]+", " ", to_text(body or ""))
    text = re.sub(r"[ \t]{2,}", " ", text)
    if not looks_like_loan_summary(subject, text):
        return None

    summary = LoanSummary()
    summary.institution = (institutions.name_for(f"{sender} {subject} {text[:400]}")
                           or "").strip() or "Unknown"

    kind = re.search(_KIND, f"{subject} {text[:800]}", re.IGNORECASE)
    if kind:
        summary.account_type = _KIND_TO_TYPE.get(kind.group(1).lower(),
                                                 AccountType.PERSONAL_LOAN)

    number = re.search(r"loan\s*(?:no|number)[^A-Za-z0-9]{0,6}"
                       r"([A-Za-z]{0,4}[Xx*]{0,8}\d{4,})", text, re.IGNORECASE)
    if number:
        summary.account_number_masked = number.group(1).strip()

    summary.sanctioned = _number(text, _FIELDS["sanctioned"])
    summary.emi = _number(text, _FIELDS["emi"])

    # The balance, stated or derived. A lender that prints "outstanding"
    # says it outright; HDFC prints what it has RECOVERED instead, and
    # sanctioned minus recovered is arithmetic on two figures it printed -
    # not an estimate.
    summary.outstanding = _number(text, _FIELDS["outstanding"])
    if summary.outstanding is None:
        recovered = _number(text, _FIELDS["recovered"])
        if summary.sanctioned is not None and recovered is not None:
            summary.outstanding = summary.sanctioned - recovered

    for alternative in _FIELDS["rate"]:
        rate = re.search(f"(?:{alternative})" + r"[^0-9\n]{0,20}([\d.]+)\s*%?",
                         text, re.IGNORECASE)
        if rate:
            try:
                summary.interest_rate = Decimal(rate.group(1))
            except Exception:
                summary.interest_rate = None
            if summary.interest_rate:
                break

    left = _number(text, _FIELDS["tenure_left"])
    if left is not None:
        summary.months_remaining = int(left)
    total = _number(text, _FIELDS["tenure_total"])
    if total is not None:
        summary.months_total = int(total)

    stated = _AS_OF.search(subject) or _AS_OF.search(text)
    if stated:
        summary.as_of = parsers.parse_date(stated.group(1))

    return summary if summary.is_usable() else None
