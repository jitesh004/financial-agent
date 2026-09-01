"""Why a row is money in or money out.

Direction is the single most consequential thing read off a statement: get it
wrong and the row is not merely miscategorised, it is counted on the wrong side
of every total, which is a two-for-one error. It is also decided by five
different signals of very different strength, and until this existed the row
carried the answer without the reason.

The codes are set by `normalize.normalizer` as it reads each row. The sentences
are here so the screen and the reader cannot drift apart, and so the ranking
below - which signal beats which - is written down once rather than being
implicit in the order of a function.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Set when a row's direction was never actually decided - an older row from
#: before this was recorded, not a row whose reason is unknown.
UNRECORDED = ""

COLUMN = "column"
CELL_MARKER = "cell_marker"
TYPE_COLUMN = "type_column"
NARRATION = "narration"
BILL_PAYMENT = "bill_payment"
RUNNING_BALANCE = "running_balance"
DEFAULTED = "defaulted"


@dataclass(frozen=True)
class Reason:
    code: str
    #: Shown to the user, in their words.
    label: str
    #: Why this signal is trusted as much as it is.
    detail: str
    #: How much the app trusts it, 1 (strongest) to 5. Not a probability - a
    #: ranking, used to say "this beat that" rather than to compute anything.
    strength: int


REASONS: tuple[Reason, ...] = (
    Reason(
        RUNNING_BALANCE, "The running balance moved this way",
        "The strongest signal there is: the statement's own balance column "
        "went up or down by exactly this amount. It overrides everything "
        "below, because it is the bank's arithmetic rather than a reading of "
        "its wording.",
        1),
    Reason(
        COLUMN, "It was in the debit / credit column",
        "The statement has separate columns for money in and money out, and "
        "this amount was printed in one of them. Nothing needs inferring.",
        2),
    Reason(
        CELL_MARKER, "The amount itself said CR or DR",
        "The statement printed the direction next to the figure. Only one "
        "amount column, but the cell annotates itself.",
        2),
    Reason(
        TYPE_COLUMN, "A type column said so",
        "The statement carries a Dr/Cr or Deposit/Withdrawal column and this "
        "row's said which.",
        3),
    Reason(
        BILL_PAYMENT, "It reads as a credit card bill payment",
        "The one narration whose meaning flips with the account it is on: "
        "money arriving on the card, money leaving the bank account funding "
        "it. Read as an outgoing on both sides, every bill payment would be "
        "counted twice.",
        4),
    Reason(
        NARRATION, "The wording says so",
        "A single-amount-column statement carries no position to read, so the "
        "narration decides. Weakest of the real signals, and the reason "
        "explicit outgoing words are checked before a coincidental credit "
        "word.",
        4),
    Reason(
        DEFAULTED, "Nothing said, so money out was assumed",
        "One amount column, no marker, no type column, and wording that "
        "settles nothing. The row is worth checking - this is the assumption "
        "that, left uncorrected, once booked every salary credit as spending.",
        5),
)

BY_CODE: dict[str, Reason] = {r.code: r for r in REASONS}


def describe(code: str) -> dict[str, object] | None:
    """The reason record for a stored code, or None if it is not one."""
    reason = BY_CODE.get(code or "")
    if reason is None:
        return None
    return {
        "code": reason.code,
        "label": reason.label,
        "detail": reason.detail,
        "strength": reason.strength,
    }
