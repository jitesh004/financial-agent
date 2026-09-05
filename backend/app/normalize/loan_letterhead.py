"""Read a loan account statement's own letterhead.

A loan statement is not a balance statement and the difference matters. Its
body runs each instalment as a matched pair - "EMI DUE FOR INST.59" against
the receipt for the same amount - so it opens at zero, closes at zero, and
reconciles perfectly while saying nothing whatever about what is still owed.
Read as a ledger and nothing else, a real loan came out with a balance of
zero and was dropped from the Debt tab entirely.

Its LETTERHEAD says everything the ledger does not:

    Sanction Date  Loan Amount   Rate of Interest  Instl. Paid  Pending  Future  Future Instl.Amt
    26-Sep-21      2,000,000.00  10.25%            59           0 / 0.00  1      42,781.00

    Tenure: 60          Current EMI: 42850.00
    Int. Rate Type: Fixed
    Repayment Mode: AUTO DEBIT A/C No.032101011951

Which gives the outstanding directly - one future instalment of 42,781 - and
the rate that makes an amortisation possible at all. Falling back to the
credit bureau for the same figure gave 84,547, a stale two-instalment
balance from a report pulled a fortnight earlier: the lender's own statement
is both fresher and more precise.

THE REPAYMENT ACCOUNT IS NOT THIS ACCOUNT
-----------------------------------------
"AUTO DEBIT A/C No.032101011951" is the savings account the EMI is collected
from, and its last four digits are the strongest-looking account number on
the page. Taken as the loan's own, it made the loan and the savings account
one and the same - the loan statement's 114 rows landed in the savings
account and the savings balance was overwritten with the loan's zero. The
loan's number is the one it is addressed to: LPPUN00044424899.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .parsers import money

#: The loan's own account number, from the line the statement is headed with.
_ACCOUNT = re.compile(
    r"loan\s+account\s+statement\s+for\s+([A-Z0-9/-]{6,})", re.IGNORECASE)

#: The account the instalment is collected FROM. Captured so it can be
#: excluded, never used as this account's identity.
REPAYMENT_ACCOUNT = re.compile(
    r"(?:auto\s*debit|repayment|debit)\s*(?:mode)?\s*a/?c\.?\s*(?:no\.?)?\s*"
    r"[:\-]?\s*(\d{6,})", re.IGNORECASE)

_RATE = re.compile(r"([\d.]+)\s*%")
_TENURE = re.compile(r"tenure\s*[:\-]?\s*(\d{1,3})", re.IGNORECASE)
_CURRENT_EMI = re.compile(r"current\s*emi\s*[:\-]?\s*([\d,]+(?:\.\d{1,2})?)",
                          re.IGNORECASE)

#: The summary row under the column headings. Reading it positionally is the
#: only way: the headings wrap across three lines and the values land on one.
#:
#:   26-Sep-21 2,000,000.00 0.00 10.25% 5.00% 59 / 2,528,150.00 0 / 0.00 1 42,781.00
_SUMMARY_ROW = re.compile(
    r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+([\d,]+\.\d{2})\s+[\d,]+\.\d{2}\s+"
    r"([\d.]+)%\s+[\d.]+%\s+(\d{1,4})\s*/\s*([\d,]+\.\d{2})\s+"
    r"(\d{1,4})\s*/\s*([\d,]+\.\d{2})\s+(\d{1,4})\s+([\d,]+\.\d{2})")


@dataclass
class LoanFacts:
    """What a loan statement's letterhead states about the loan."""

    account_number: str = ""
    repayment_account: str = ""
    sanctioned: Decimal | None = None
    interest_rate: Decimal | None = None
    emi: Decimal | None = None
    months_total: int | None = None
    instalments_paid: int | None = None
    instalments_overdue: int | None = None
    instalments_future: int | None = None
    future_instalment_amount: Decimal | None = None

    @property
    def outstanding(self) -> Decimal | None:
        """What is still to be paid, from the lender's own count.

        Future instalments times what each one is, plus anything overdue.
        Stated arithmetic on stated figures - not a projection.
        """
        if self.instalments_future is None or \
                self.future_instalment_amount is None:
            return None
        return (self.future_instalment_amount * self.instalments_future)

    @property
    def months_remaining(self) -> int | None:
        if self.instalments_future is None:
            return None
        return self.instalments_future + (self.instalments_overdue or 0)


def _decimal(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def parse(text: str) -> LoanFacts | None:
    """The loan's own terms, or None if this is not a loan statement."""
    facts = LoanFacts()

    account = _ACCOUNT.search(text or "")
    if account:
        facts.account_number = account.group(1).strip()

    repayment = REPAYMENT_ACCOUNT.search(text or "")
    if repayment:
        facts.repayment_account = repayment.group(1).strip()

    row = _SUMMARY_ROW.search(text or "")
    if row:
        facts.sanctioned = _decimal(row.group(2))
        facts.interest_rate = _decimal(row.group(3))
        facts.instalments_paid = int(row.group(4))
        facts.instalments_overdue = int(row.group(6))
        facts.instalments_future = int(row.group(8))
        facts.future_instalment_amount = _decimal(row.group(9))

    emi = _CURRENT_EMI.search(text or "")
    if emi:
        facts.emi = money(emi.group(1))

    tenure = _TENURE.search(text or "")
    if tenure:
        facts.months_total = int(tenure.group(1))

    if facts.interest_rate is None:
        # Some layouts print the rate only in the details block.
        near = re.search(r"rate\s*of\s*interest[^%\n]{0,40}?([\d.]+)\s*%",
                         text or "", re.IGNORECASE)
        if near:
            facts.interest_rate = _decimal(near.group(1))

    known = (facts.account_number or facts.outstanding is not None
             or facts.interest_rate is not None)
    return facts if known else None
