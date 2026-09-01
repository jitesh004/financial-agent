"""The shapes Indian financial documents write things in.

Months, payment rails, the tokens that mean "no figure here", and the way an
account number is masked. None of this is logic - it is vocabulary, and it was
being retyped in every module that needed it:

    month names          4 copies (parsers, bureau, txn_email, coverage)
    payment rail names   3 copies (parsers, recurring, settlement)
    "not a number"       2 copies (portfolio, parsers)
    last-four extraction 3 copies (metadata, bureau, txn_email)

Retyped vocabulary drifts. `bureau`'s month map had twelve abbreviations and no
full names, so "December 2025" on a CRIF report parsed as nothing while the
same string on a bank statement parsed fine.

The rail lists below are deliberately kept as separate named subsets rather
than merged into one. They are used for three different jobs and the right set
genuinely differs between them - what should not differ is the spelling of
"NACH", which is why they are all built from RAIL_NAMES.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

# --------------------------------------------------------------------------
# Months
# --------------------------------------------------------------------------

#: Every spelling of a month that appears on these documents -> its number.
#: Full names included: bureau reports print "December 2025" where statements
#: print "Dec-25".
MONTHS: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

#: Regex alternation, longest first so "june" is not consumed as "jun".
MONTH_ALTERNATION: str = "|".join(
    sorted(MONTHS, key=len, reverse=True))

#: A month token as it appears inside a narration, for stripping.
MONTH_TOKEN = re.compile(rf"\b(?:{MONTH_ALTERNATION})\b", re.IGNORECASE)


def month_number(token: str) -> int | None:
    """The month a name or abbreviation refers to, or None."""
    return MONTHS.get((token or "").strip().lower())


# --------------------------------------------------------------------------
# Payment rails
# --------------------------------------------------------------------------

#: Every rail and instrument code seen in a narration on these statements.
#: One spelling each; the subsets below choose from this, and nothing outside
#: this tuple should appear in a rail pattern anywhere.
RAIL_NAMES: tuple[str, ...] = (
    "UPI", "IMPS", "NEFT", "RTGS", "ACH", "ECS", "NACH", "CMS", "MMT",
    "POS", "ATM", "INF", "TRF", "BIL", "CHQ", "CASH", "MPS", "VIN", "SI",
    "NA",
)

#: Rails that appear as a PREFIX on a narration, which `clean_description`
#: strips. Wider than the others because anything leading the string is noise
#: whether or not it is strictly a rail - "CHQ", "CASH" and "SI" are
#: instrument codes, not networks, and they prefix a narration just the same.
PREFIX_RAILS: tuple[str, ...] = (
    "UPI", "IMPS", "NEFT", "RTGS", "POS", "ATM", "ACH", "ECS", "NACH", "MMT",
    "INF", "TRF", "BIL", "CHQ", "CASH", "MPS", "VIN", "SI",
)

#: Rails removed from a recurring-payment signature. NARROWER on purpose: a
#: signature is what makes two months' rows "the same charge", and stripping
#: "CASH" or "POS" from it would merge unrelated cash withdrawals into one
#: series. "NA" is here because several issuers print it as a literal
#: placeholder in the rail position.
SIGNATURE_RAILS: tuple[str, ...] = (
    "UPI", "NEFT", "RTGS", "IMPS", "ACH", "CMS", "MMT", "NA",
)

PREFIX_RAIL_PATTERN = re.compile(
    rf"^\s*({'|'.join(PREFIX_RAILS)})[\s/\-:]+", re.IGNORECASE)

SIGNATURE_RAIL_PATTERN = re.compile(
    rf"\b(?:{'|'.join(SIGNATURE_RAILS)})\b", re.IGNORECASE)


# --------------------------------------------------------------------------
# Card bill payments
# --------------------------------------------------------------------------

#: How a credit card bill being settled is written in a narration - the part
#: all three readers of it agree on.
#:
#: Three modules ask this question for three different reasons:
#:
#:   normalizer  - which DIRECTION is this row? (the one narration whose sign
#:                 flips with the account it is on)
#:   categorize  - which CATEGORY is it?  (CC_PAYMENT, never spending)
#:   settlement  - may a multi-leg group even be attempted for this row?
#:
#: They are NOT the same question, so this is deliberately the shared core and
#: each consumer adds its own. Merging them outright was tried and reverted:
#: "BBPS PAYMENT RECEIVED" is a card bill to the categorizer and to the
#: settlement gate, but on a bank account it is money arriving, and folding it
#: into the direction reader flipped that row's sign.
#:
#: What the core buys is that the seven wordings all three DO share are typed
#: once. Before this, "CRED.CLUB" existed in three places and a fix to one
#: reached neither of the others.
#:
#: `\bcred\b` cannot match inside "credit" - the word boundary requires a
#: non-word character after the d.
BILL_PAYMENT_MARKERS: tuple[str, ...] = (
    r"\bbppy\b",
    r"\bcred\b",
    r"\bcred\.club\b",
    r"\bbillpay\b",
    r"\bcc\s*payment\b",
    r"\bcard\s*payment\b",
    r"\bcredit\s*card\s*payment\b",
)

#: Added by the DIRECTION reader only. On a card, "Payment - Thank You" is the
#: issuer acknowledging a bill; it says nothing about category or settlement.
DIRECTION_ONLY_BILL_MARKERS: tuple[str, ...] = (
    r"\bpayment\s*[-,]?\s*thank\s*you\b",
)

#: Added by the CATEGORIZER only. "BBPS PAYMENT RECEIVED" on a card is a bill;
#: on a bank account it is money in, which is why the direction reader must not
#: see it. HSBC abbreviates it "BBPS PMT" where everyone else writes it out -
#: missing that one spelling booked 83,105 of bill payments as income.
CATEGORY_ONLY_BILL_MARKERS: tuple[str, ...] = (
    r"\bpayment\s+received\b",
    r"\bautopay\b.*\bcard\b",
    r"\bbbps[\s\-]*(?:payment|pmt)\b",
    r"\bamex\b",
    r"\bamerican\s+express\b",
)

#: Added by the SETTLEMENT gate only. Dreamplug is CRED's payment entity, and
#: a bare "BBPS" is enough evidence to attempt a group even though it is not
#: enough to name a category.
SETTLEMENT_ONLY_BILL_MARKERS: tuple[str, ...] = (
    r"\bdreamplug\b",
    r"\bbbps\b",
)

BILL_PAYMENT = re.compile(
    "|".join(BILL_PAYMENT_MARKERS + DIRECTION_ONLY_BILL_MARKERS),
    re.IGNORECASE)


# --------------------------------------------------------------------------
# Blank figures
# --------------------------------------------------------------------------

#: What a document prints where it has no figure. Read as None, never as zero:
#: a bureau printing "-" for a closed account's balance means "nothing
#: reported", and recording that as 0 puts a confident number where there is
#: none. A blank NAV read as zero values a holding at nothing and drags the
#: whole portfolio total down with it.
NO_FIGURE: frozenset[str] = frozenset({
    "", "-", "--", "---", "n/a", "na", "nil", "none", ".", "*",
})


def is_blank_figure(raw: object) -> bool:
    return str(raw if raw is not None else "").strip().lower() in NO_FIGURE


# --------------------------------------------------------------------------
# Masked account numbers
# --------------------------------------------------------------------------

_DIGITS = re.compile(r"\d")

#: How this app writes a masked number once it has one. Only the last four
#: digits are ever stored - enough to tell two accounts apart, useless to
#: anyone who reads the database.
MASK_PREFIX = "XXXX"


def last_four(masked: str | None) -> str:
    """The last four digits of an account number, or "".

    Issuers mask differently - XXXXXX1234, ****1234, xx1234, or the full
    number - and the trailing digits are the only part that survives all of
    them. This is the join key between a statement, an alert and a bureau
    line, so all three must extract it identically; they used to have three
    implementations and the alert one required exactly four trailing digits
    while the bureau one took the last four of any run.
    """
    digits = "".join(_DIGITS.findall(masked or ""))
    return digits[-4:] if len(digits) >= 4 else ""


def masked(account_number: str | None) -> str | None:
    """`account_number` reduced to the stored form, or None."""
    tail = last_four(account_number)
    return f"{MASK_PREFIX}{tail}" if tail else None


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------

#: The smallest unit money is stored in. Every figure the app reports is
#: rounded to it, once, here.
CENT = Decimal("0.01")


def to_paise(value: Decimal) -> Decimal:
    """Round a figure to paise, half away from zero.

    ROUND_HALF_UP rather than Python's banker's rounding, because a statement
    rounds the way a bank does and a ledger that rounds differently from the
    document it came from will fail its own reconciliation on the half-paise.

    One implementation: the totals engine and the loan calculator each had
    their own, identical, and four more places called `.quantize` inline.
    """
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
