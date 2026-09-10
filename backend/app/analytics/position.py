"""The position: what the user attested, aged to today, checked against the ledger.

Three records can describe the same debt and they routinely disagree:

  the statements   what has been imported, which may be nothing at all
  the bureau       every credit account a lender reported, balances months old
  the user         what they know, including the loan neither of the above
                   can see

None of the three is the truth on its own. This module is where they are put
beside each other, and the rule it follows is the same one the rest of the app
follows: state each figure, say where it came from, and report a disagreement
rather than picking a winner quietly.

Why an attested figure is not a stale form
------------------------------------------

The obvious objection to letting somebody type "outstanding: 42,00,000" is
that it is true for one day. It is - and that is a property of the number, not
a reason to refuse it. A loan outstanding on 4 September is a fact about 4
September; by 4 December three EMIs have been paid and some known amount of
principal has come off.

So nothing attested is displayed as attested. It is rolled forward from its
review date through the same closed-form amortization the Debt tab uses, and
both figures are shown: the baseline the user confirmed, and what it must be
today if the EMIs went out as scheduled. An attested position therefore gets
MORE accurate as the user keeps reviewing it, and never silently less.

A card is the exception, and treating it as one is the whole point. A card
balance does not amortize - it is whatever was spent and whatever was paid -
so rolling one forward would be inventing a number. What a card DOES have is a
cycle, and that is arithmetic: the next statement date and the next due date
are computable, and the balance is marked stale the moment a cycle has closed
since it was last reviewed.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..models.schemas import LOAN_TYPES, AccountType
from . import loans as loans_mod

ZERO = Decimal("0")

#: Kinds an item can be. Deliberately few: the distinction that matters is
#: how a figure AGES, and there are only three answers - it amortizes, it
#: cycles, or it just sits there.
KINDS = ("loan", "card", "account", "investment", "other")

#: How far past its review a non-amortizing figure is still worth quoting.
#: A savings balance a fortnight old is a reasonable answer; one from March
#: is a number pretending to be current.
STALE_AFTER_DAYS = 45

#: How far apart the rolled-forward figure and the statements have to be
#: before the difference is worth reporting. Both are rounded to the rupee
#: somewhere along the way, and an EMI landing a day either side of a
#: statement boundary moves a balance legitimately.
DRIFT_TOLERANCE = Decimal("0.02")


# ---------------------------------------------------------------------------
# Solving for the term nobody wrote down
# ---------------------------------------------------------------------------

def infer_rate(outstanding: Decimal, emi: Decimal,
               months_remaining: int) -> Decimal | None:
    """The annual rate implied by a balance, an instalment and a term.

    People know three of the four numbers on a loan and rarely all four - the
    rate is the one nobody remembers, and it is also the one every projection
    needs. It is recoverable: there is exactly one rate at which this balance
    takes this many instalments to clear, and it is found by bisection because
    the closed form needs a logarithm and money here is Decimal.

    Returns None when the three given numbers do not describe a loan - an EMI
    that cannot even cover the first month's interest at any plausible rate,
    or a term that no rate produces.
    """
    if outstanding <= 0 or emi <= 0 or months_remaining <= 0:
        return None
    # A zero-interest loan clears in ceil(P / E) instalments. Anything shorter
    # than that is not a loan, it is an arithmetic mistake.
    zero_rate_months = -(-outstanding // emi)
    if months_remaining < zero_rate_months:
        return None

    low, high = Decimal("0"), Decimal("60")
    for _ in range(60):
        mid = (low + high) / 2
        months = loans_mod.months_to_payoff(outstanding, mid, emi)
        if months is None or months > months_remaining:
            # Too expensive: this rate needs more instalments than there are.
            high = mid
        else:
            low = mid

    # Rounded to something a person would recognise as a rate - and then
    # checked, because rounding can push the answer over a month boundary.
    # A rate that does not reproduce the term it was derived from is not the
    # rate: it would make every projection built on it a month out.
    exact = (low + high) / 2
    for candidate in _nearby(exact):
        if candidate <= 0:
            continue
        if loans_mod.months_to_payoff(outstanding, candidate,
                                      emi) == months_remaining:
            return candidate
    return None


def _nearby(value: Decimal) -> list[Decimal]:
    """The two-decimal rates either side of this one, nearest first."""
    step = Decimal("0.01")
    rounded = value.quantize(step)
    return [rounded, rounded - step, rounded + step]


def complete_loan_terms(outstanding: Decimal | None, emi: Decimal | None,
                        rate: Decimal | None,
                        months_remaining: int | None) -> dict[str, Any]:
    """Fill in whichever one of the four terms is missing.

    A loan has four numbers and any three of them determine the fourth, so a
    user who knows the balance, the instalment and the term should not have to
    go and find the rate before this screen can project anything. What is
    derived is labelled as derived - see `derived` in the return - because a
    figure the app worked out and a figure the user confirmed are not the same
    kind of fact.
    """
    derived: list[str] = []
    if (outstanding and emi and months_remaining and rate is None):
        rate = infer_rate(outstanding, emi, months_remaining)
        if rate is not None:
            derived.append("interest_rate")
    if (outstanding and emi and rate is not None and not months_remaining):
        months_remaining = loans_mod.months_to_payoff(outstanding, rate, emi)
        if months_remaining:
            derived.append("months_remaining")
    return {"outstanding": outstanding, "emi": emi, "interest_rate": rate,
            "months_remaining": months_remaining, "derived": derived}


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------

def _on_day(year: int, month: int, day: int) -> date:
    """A day of the month, clamped to months that are shorter.

    A statement dated the 31st is dated the 28th in February, and every
    cardholder knows that; the arithmetic has to as well.
    """
    return date(year, month, min(max(day, 1), calendar.monthrange(year, month)[1]))


def next_on_day(day: int | None, as_of: date) -> date | None:
    """The next occurrence of this day of the month, on or after `as_of`."""
    if not day:
        return None
    this_month = _on_day(as_of.year, as_of.month, day)
    if this_month >= as_of:
        return this_month
    year, month = (as_of.year + 1, 1) if as_of.month == 12 else (as_of.year,
                                                                 as_of.month + 1)
    return _on_day(year, month, day)


def cycles_between(day: int | None, start: date, end: date) -> int:
    """How many times this day of the month has come round in a window."""
    if not day or end <= start:
        return 0
    count, cursor = 0, next_on_day(day, start + timedelta(days=1))
    while cursor is not None and cursor <= end and count < 600:
        count += 1
        nxt = cursor + timedelta(days=1)
        cursor = next_on_day(day, nxt)
    return count


# ---------------------------------------------------------------------------
# Ageing one item
# ---------------------------------------------------------------------------

@dataclass
class AgedItem:
    """One attested item, as it stands today.

    Deliberately flat rather than nested. Every field here is a column the
    screen can sort by, and "sort my cards by utilisation" should not require
    the client to reach into a sub-object to do it.
    """

    id: str
    kind: str
    label: str
    institution: str
    account_id: str | None
    bureau_account_id: str | None
    reviewed_on: date
    days_since_review: int

    # ---- what the user attested ----
    attested_outstanding: Decimal | None = None
    attested_months_remaining: int | None = None

    # ---- what it must be now ----
    outstanding: Decimal | None = None
    months_remaining: int | None = None
    payoff_date: date | None = None
    emi: Decimal | None = None
    interest_rate: Decimal | None = None
    original_amount: Decimal | None = None
    #: Instalments that have fallen due since the review.
    emis_since_review: int = 0
    #: Principal cleared in that time - the reason the figure moved.
    principal_paid_since_review: Decimal | None = None
    interest_paid_since_review: Decimal | None = None
    total_interest_remaining: Decimal | None = None

    # ---- cards ----
    credit_limit: Decimal | None = None
    utilisation_pct: float | None = None
    min_due: Decimal | None = None
    statement_day: int | None = None
    due_day: int | None = None
    next_statement_on: date | None = None
    next_due_on: date | None = None
    days_to_due: int | None = None
    cycles_since_review: int = 0

    # ---- what the documents say ----
    observed_outstanding: Decimal | None = None
    observed_as_of: date | None = None
    bureau_outstanding: Decimal | None = None
    drift: Decimal | None = None
    drift_pct: float | None = None
    #: The documents cover this holding, but under a statement that already
    #: contains it. Counting the attestation as well would double it.
    superseded: bool = False

    # ---- how to read all of the above ----
    #: One sentence saying where `outstanding` came from.
    basis: str = ""
    #: True when the figure is being quoted past the point it can be trusted.
    stale: bool = False
    #: Fields this app worked out rather than the user confirming.
    derived: list[str] = field(default_factory=list)
    notes: str = ""
    archived: bool = False
    sort_order: int = 0
    warnings: list[str] = field(default_factory=list)


def age_item(item: dict[str, Any], *, as_of: date,
             observed: dict[str, Any] | None = None,
             bureau: dict[str, Any] | None = None,
             payments: list[date] | None = None) -> AgedItem:
    """Roll one attested item forward to `as_of` and check it.

    `observed` is what the ledger currently says about the mapped account, and
    `bureau` what the credit report says. Both optional: an item mapped to
    nothing is still a real position, which is the reason this table exists.
    """
    reviewed_on = _as_date(item.get("reviewed_on")) or as_of
    aged = AgedItem(
        id=item["id"],
        kind=item.get("kind") or "other",
        label=item.get("label") or "",
        institution=item.get("institution") or "",
        account_id=item.get("account_id"),
        bureau_account_id=item.get("bureau_account_id"),
        reviewed_on=reviewed_on,
        days_since_review=max(0, (as_of - reviewed_on).days),
        attested_outstanding=_dec(item.get("outstanding")),
        attested_months_remaining=_int(item.get("months_remaining")),
        emi=_dec(item.get("emi")),
        interest_rate=_dec(item.get("interest_rate")),
        original_amount=_dec(item.get("original_amount")),
        credit_limit=_dec(item.get("credit_limit")),
        min_due=_dec(item.get("min_due")),
        statement_day=_int(item.get("statement_day")),
        due_day=_int(item.get("due_day")),
        notes=item.get("notes") or "",
        archived=bool(item.get("archived")),
        sort_order=_int(item.get("sort_order")) or 0,
    )

    if aged.kind == "loan":
        _age_loan(aged, as_of, payments)
    elif aged.kind == "card":
        _age_card(aged, as_of)
    else:
        _age_holding(aged, as_of)

    _attach_observed(aged, observed, bureau)

    # Attestation is an OVERRIDE, not a parallel store. Where nothing has
    # been asserted, the document answers - which is what lets an item stop
    # carrying a second copy of a figure a statement already provides, and
    # lets that copy stop going stale on its own. A loan is the exception
    # handled above: its attested balance IS the root fact, because a loan
    # serviced from an account nobody uploaded has no document to fall back
    # on.
    if aged.outstanding is None and aged.observed_outstanding is not None:
        aged.outstanding = aged.observed_outstanding
        aged.basis = ("from your statement"
                      + (f" of {aged.observed_as_of.isoformat()}"
                         if aged.observed_as_of else ""))
    return aged


def _age_loan(aged: AgedItem, as_of: date,
              payments: list[date] | None = None) -> None:
    """Walk the amortization forward from the review to today."""
    terms = complete_loan_terms(aged.attested_outstanding, aged.emi,
                                aged.interest_rate,
                                aged.attested_months_remaining)
    aged.interest_rate = terms["interest_rate"]
    aged.derived = list(terms["derived"])
    if terms["months_remaining"] and not aged.attested_months_remaining:
        aged.attested_months_remaining = terms["months_remaining"]

    # A loan has four numbers and any three fix the fourth, so all four given
    # can contradict each other - and usually do because one was typed wrong
    # or remembered from a different year. Caught here, at the moment it is
    # entered, rather than surfacing later as a payoff date that is quietly
    # four years out. The schedule is what gets walked either way: it is the
    # only reading of the figures that is internally consistent.
    _check_terms_agree(aged)

    outstanding = aged.attested_outstanding
    if outstanding is None:
        aged.basis = "no outstanding recorded"
        aged.warnings.append(
            "Nothing can be projected without an outstanding balance.")
        return

    # Instalments are counted on the day they actually leave, where that is
    # known. Counting whole calendar months instead is wrong by one for half
    # of every month - which on a 20-year loan is the difference between the
    # right payoff date and one a month out.
    #
    # Better still: count the payments the LEDGER actually recorded. The
    # calendar can only ever be a model of when money moved; the debits are
    # the money moving. This loan is why - `due_day` was never captured, so
    # the fallback used the review date's own day (the 30th) while every EMI
    # actually left on the 5th. Two cycles were counted where three payments
    # had been made, and the balance read 24,262 too high.
    if payments:
        paid = sum(1 for when in payments if when > aged.reviewed_on)
    else:
        emi_day = aged.due_day or aged.reviewed_on.day
        paid = cycles_between(emi_day, aged.reviewed_on, as_of)
    aged.emis_since_review = paid
    aged.outstanding = outstanding
    aged.months_remaining = aged.attested_months_remaining

    if aged.emi is None or aged.emi <= 0 or aged.interest_rate is None:
        aged.basis = (f"as you reviewed it on {aged.reviewed_on.isoformat()}")
        aged.stale = paid > 0
        if paid:
            aged.warnings.append(
                f"{paid} instalment(s) have fallen due since you reviewed "
                f"this, but without both an EMI and a rate the balance "
                f"cannot be rolled forward. Add either and it will age "
                f"itself.")
        return

    schedule = loans_mod.build_schedule(
        outstanding, aged.interest_rate, aged.emi, aged.reviewed_on)
    if not schedule:
        aged.basis = "the EMI does not cover the interest on this balance"
        aged.warnings.append(
            "At this rate the instalment does not cover a month's interest, "
            "so the balance grows rather than reduces. Check the figures "
            "against the lender.")
        return

    if paid >= len(schedule):
        aged.outstanding = ZERO
        aged.months_remaining = 0
        aged.payoff_date = schedule[-1].when
        aged.basis = (f"cleared - the last instalment fell due "
                      f"{schedule[-1].when.isoformat()}")
        aged.principal_paid_since_review = outstanding
        return

    walked = schedule[:paid]
    aged.principal_paid_since_review = sum(
        (row.principal for row in walked), ZERO)
    aged.interest_paid_since_review = sum((row.interest for row in walked), ZERO)
    aged.outstanding = walked[-1].closing if walked else outstanding
    remaining = schedule[paid:]
    aged.months_remaining = len(remaining)
    aged.payoff_date = remaining[-1].when if remaining else None
    aged.total_interest_remaining = sum((row.interest for row in remaining), ZERO)
    aged.basis = (
        f"as you reviewed it on {aged.reviewed_on.isoformat()}, rolled "
        f"forward {paid} instalment(s)" if paid else
        f"as you reviewed it on {aged.reviewed_on.isoformat()}")


#: How far the stated term can be from the one the other three terms imply
#: before it is worth saying something. One month either side is rounding on
#: a part-paid first instalment; a year is a typo.
TERM_TOLERANCE_MONTHS = 2


def _check_terms_agree(aged: AgedItem) -> None:
    """Say so when the four numbers on a loan cannot all be true."""
    if not (aged.attested_outstanding and aged.emi and aged.interest_rate
            and aged.attested_months_remaining):
        return
    implied = loans_mod.months_to_payoff(
        aged.attested_outstanding, aged.interest_rate, aged.emi)
    if implied is None:
        return
    gap = implied - aged.attested_months_remaining
    if abs(gap) <= TERM_TOLERANCE_MONTHS:
        return
    aged.warnings.append(
        f"These four figures do not describe one loan. At "
        f"{aged.interest_rate}% a balance of {aged.attested_outstanding:,.0f} "
        f"takes {implied} instalments of {aged.emi:,.0f}, not "
        f"{aged.attested_months_remaining}. The schedule below follows the "
        f"balance, rate and EMI - check whichever of the four is wrong.")


def _age_card(aged: AgedItem, as_of: date) -> None:
    """A card's cycle rolls; its balance does not.

    Projecting a card balance would mean guessing what was spent, and a
    guessed liability is the one number on this screen that must not exist.
    So the balance stays exactly as attested, the cycle dates are computed,
    and the moment a statement has been generated since the review the figure
    is marked stale and says why.
    """
    aged.outstanding = aged.attested_outstanding
    aged.next_statement_on = next_on_day(aged.statement_day, as_of)
    aged.next_due_on = next_on_day(aged.due_day, as_of)
    if aged.next_due_on:
        aged.days_to_due = (aged.next_due_on - as_of).days
    if aged.credit_limit and aged.credit_limit > 0 and aged.outstanding is not None:
        aged.utilisation_pct = round(
            float(aged.outstanding / aged.credit_limit) * 100, 1)

    aged.cycles_since_review = cycles_between(
        aged.statement_day, aged.reviewed_on, as_of)
    if aged.cycles_since_review:
        aged.stale = True
        aged.basis = (
            f"as you reviewed it on {aged.reviewed_on.isoformat()} - "
            f"{aged.cycles_since_review} statement(s) have been generated "
            f"since, so this balance has moved")
    elif aged.days_since_review > STALE_AFTER_DAYS:
        aged.stale = True
        aged.basis = (f"as you reviewed it on {aged.reviewed_on.isoformat()}, "
                      f"{aged.days_since_review} days ago")
    else:
        aged.basis = f"as you reviewed it on {aged.reviewed_on.isoformat()}"


def _age_holding(aged: AgedItem, as_of: date) -> None:
    """A balance that neither amortizes nor cycles. It simply gets old."""
    aged.outstanding = aged.attested_outstanding
    aged.stale = aged.days_since_review > STALE_AFTER_DAYS
    aged.basis = (f"as you reviewed it on {aged.reviewed_on.isoformat()}"
                  + (f", {aged.days_since_review} days ago" if aged.stale else ""))


def _attach_observed(aged: AgedItem, observed: dict[str, Any] | None,
                     bureau: dict[str, Any] | None) -> None:
    """Put the documents beside the attestation and measure the gap.

    Neither side wins. A statement is checked and an attestation is not, so
    the statement is usually right - but the whole reason this table exists is
    that the statements are sometimes absent or months behind, and a screen
    that silently overwrote the user's own figure with a stale one would be
    the same mistake in the other direction. The difference is reported; the
    person reading it decides.
    """
    if bureau:
        aged.bureau_outstanding = _dec(bureau.get("current_balance"))
    if not observed:
        return
    if observed.get("superseded"):
        # Not drift, and not a missing figure either: a consolidated
        # statement already reports these holdings, so the attested copy is
        # the SAME money a second time. Say so, and leave it out of the
        # totals rather than reporting the whole balance as a discrepancy.
        aged.superseded = True
        aged.warnings.append(
            "Already counted: a consolidated statement reports these "
            "holdings, so this attested figure is the same money twice. "
            "It is shown here but left out of the totals.")
        return
    aged.observed_outstanding = _dec(observed.get("outstanding"))
    aged.observed_as_of = _as_date(observed.get("as_of"))
    if observed.get("credit_limit") and aged.credit_limit is None:
        aged.credit_limit = _dec(observed.get("credit_limit"))

    if aged.observed_outstanding is None or aged.outstanding is None:
        return
    gap = aged.observed_outstanding - aged.outstanding
    if aged.outstanding == 0:
        aged.drift = gap
        return
    relative = abs(gap / aged.outstanding)
    if relative <= DRIFT_TOLERANCE:
        return
    aged.drift = gap
    aged.drift_pct = round(float(relative) * 100, 1)
    aged.warnings.append(
        f"Your figure rolls forward to {aged.outstanding:,.0f}; the "
        f"statements say {aged.observed_outstanding:,.0f}"
        + (f" as at {aged.observed_as_of.isoformat()}"
           if aged.observed_as_of else "")
        + f". Difference {gap:,.0f}.")


# ---------------------------------------------------------------------------
# The whole position
# ---------------------------------------------------------------------------

def build(items: list[dict[str, Any]], accounts: list[Any],
          bureau_accounts: list[dict[str, Any]], *,
          as_of: date | None = None,
          include_archived: bool = False,
          holdings: list[dict[str, Any]] | None = None,
          holdings_any: list[dict[str, Any]] | None = None,
          emi_payments: dict[str, list[date]] | None = None) -> dict[str, Any]:
    """Every attested item aged to today, plus what nothing accounts for."""
    as_of = as_of or date.today()
    accounts_by_id = {a.id: a for a in accounts if getattr(a, "id", None)}
    resolve = _account_resolver(accounts)
    resolve_bureau = _bureau_resolver(bureau_accounts)
    held = _holdings_by_account(holdings)
    superseded = _superseded_accounts(accounts, held, holdings_any)

    aged: list[AgedItem] = []
    for item in items:
        if item.get("archived") and not include_archived:
            continue
        account = resolve(item)
        line = resolve_bureau(item)
        if line is not None and line.get("id") != item.get("bureau_account_id"):
            item = {**item, "bureau_account_id": line["id"]}
        if account is not None and getattr(account, "id", None):
            # Report the account this item RESOLVED to, not the uuid stored
            # on it. They differ whenever a rebuild renumbered the ledger,
            # and anything downstream keying on the stored one would file
            # this balance against whatever now holds that uuid - which is
            # how both loans came to be filed against a credit card.
            item = {**item, "account_id": account.id}
        aged.append(age_item(
            item, as_of=as_of,
            observed=_observed_for(account, held, superseded),
            bureau=line,
            payments=_payments_for(item, emi_payments),
        ))
    aged.sort(key=lambda a: (a.sort_order, a.label.lower()))

    claimed_accounts = {a.account_id for a in aged if a.account_id}
    claimed_bureau = {a.bureau_account_id for a in aged if a.bureau_account_id}

    unconfirmed = ZERO
    for line in bureau_accounts:
        if line["id"] in claimed_bureau or _is_attributed(line):
            continue
        if (line.get("status") or "open") != "open":
            continue
        try:
            unconfirmed += Decimal(str(line.get("current_balance") or 0))
        except (ArithmeticError, ValueError):
            continue

    return {
        "as_of": as_of.isoformat(),
        "items": [_item_json(a) for a in aged],
        "totals": _totals(aged, unconfirmed),
        # The three ways this picture can be incomplete, each named rather
        # than folded into a confidence score. Only the first is alarming.
        "unaccounted": {
            "bureau": [
                {"id": b["id"], "lender": b.get("lender"),
                 "type": b.get("account_type"), "status": b.get("status"),
                 "balance": b.get("current_balance"),
                 "emi": b.get("emi_amount"),
                 "masked": b.get("account_number_masked")}
                for b in bureau_accounts
                if b["id"] not in claimed_bureau
                and (b.get("status") or "open") == "open"
                and not _is_attributed(b)
            ],
            "accounts": [
                {"id": a.id, "name": a.display_name(),
                 "type": a.account_type.value}
                for account_id, a in accounts_by_id.items()
                if account_id not in claimed_accounts
            ],
        },
        "needs_attention": [
            {"id": a.id, "label": a.label, "why": w}
            for a in aged for w in a.warnings
        ],
    }


def _account_resolver(accounts: list[Any]):
    """Find the account an attested item is about, by IDENTITY not by uuid.

    Account uuids do not survive a ledger rebuild - `staging/process` mints
    new ones - so an item created before a rebuild holds an id that now names
    nothing, or worse, names a different account entirely. Both happened on
    the ledger this was written against: 25 of 28 links dangled, and the two
    loans had both come to point at the same credit card. A card carries a
    zero balance, so a 66.7 lakh home loan was compared against 0 and the
    entire balance was reported as "drift".

    So identity is tried before the uuid, in the order it can be trusted:

      1. `account_key` - INSTITUTION|TYPE|MASKED|PRODUCT, the same key
         `user_overrides` has always used, which is why a correction survives
         a reprocess when an attestation did not.
      2. The label, which was rendered from the account's own fields when the
         item was created and still names the real-world account.
      3. The stored uuid, last, because it is the thing that goes stale.

    Returning None is a real answer and a different one from "balance zero":
    it means no ledger account covers this item, which is the whole reason
    the item was typed in by hand.
    """
    from ..pipeline.fingerprint import account_key

    by_id: dict[str, Any] = {}
    by_key: dict[str, Any] = {}
    by_label: dict[str, Any] = {}
    for account in accounts:
        if getattr(account, "id", None):
            by_id[account.id] = account
        key = account_key(account)
        if key:
            by_key.setdefault(key, account)
        try:
            by_label.setdefault(account.display_name(), account)
        except Exception:            # pragma: no cover - defensive
            pass

    def resolve(item: dict[str, Any]) -> Any:
        key = (item.get("account_key") or "").strip()
        if key and key in by_key:
            return by_key[key]
        labelled = by_label.get(item.get("label") or "")
        if labelled is not None:
            return labelled
        return by_id.get(item.get("account_id"))

    return resolve


def _holdings_by_account(
    holdings: list[dict[str, Any]] | None,
) -> dict[str, tuple[Decimal, str]]:
    """Latest valued holdings, summed per account.

    For an investment account this - not `accounts.current_balance` - is what
    the documents actually say. That column is a summary written alongside the
    holdings, and it can go stale on its own: on the ledger this was written
    against, the Broker account carried `current_balance = 0` with a
    2026-07-31 as-of stamp while its own holdings for that same date summed to
    301,409.33. Reading the copy reported the whole balance as drift.
    """
    index: dict[str, tuple[Decimal, str]] = {}
    for row in holdings or []:
        account_id = row.get("account_id")
        if not account_id:
            continue
        try:
            value = Decimal(str(row.get("value") or "0"))
        except (ArithmeticError, ValueError):
            continue
        total, seen = index.get(account_id, (Decimal("0"), ""))
        index[account_id] = (total + value,
                             max(seen, str(row.get("as_of") or "")))
    return index


def balances_by_account(built: dict[str, Any]) -> dict[str, Decimal]:
    """Signed resolved balance per linked account, from a built position.

    This is the hand-off that makes the Position tab the reader-of-record.
    Every other screen used to read `accounts.principal_outstanding` and
    `accounts.current_balance` directly, which is why four screens quoted
    four different figures for one home loan: those columns hold the balance
    as ATTESTED, and only this module ages it forward, checks it against the
    ledger and drops what a consolidated statement already counted.

    Liabilities come back negative and assets positive, matching
    `Account.balance`, so a caller can substitute one for the other.
    Superseded items are omitted rather than zeroed: the caller must not
    count them, and must not read a zero as "this is empty" either.
    """
    out: dict[str, Decimal] = {}
    for item in built.get("items", []):
        account_id = item.get("account_id")
        if not account_id or item.get("superseded"):
            continue
        kind = item.get("kind")
        if kind == "investment":
            value = item.get("observed_outstanding")
            if value is None:
                value = item.get("outstanding")
        else:
            value = item.get("outstanding")
        if value is None:
            continue
        amount = Decimal(str(value))
        out[account_id] = -amount if kind in {"loan", "card"} else amount
    return out


def adopted_bureau(built: dict[str, Any]) -> dict[str, Decimal]:
    """Bureau-only debt this position has taken responsibility for.

    A bureau line adopted onto the balance sheet is counted HERE, so whoever
    also totals "debt the bureau reports that nothing covers" has to skip it
    or the same card is counted twice. Keyed by bureau id for exactly that
    hand-off. Signed like `balances_by_account`: liabilities negative.
    """
    out: dict[str, Decimal] = {}
    for item in built.get("items", []):
        bureau_id = item.get("bureau_account_id")
        if not bureau_id or item.get("superseded"):
            continue
        # Only items the bureau is the SOLE source for. One that also maps to
        # a ledger account is already counted through that account, and
        # returning it here would add the same debt a second time.
        if item.get("account_id"):
            continue
        value = item.get("outstanding")
        if value is None:
            continue
        amount = Decimal(str(value))
        out[bureau_id] = (-amount if item.get("kind") in {"loan", "card"}
                          else amount)
    return out


def _payments_for(item: dict[str, Any],
                  emi_payments: dict[str, list[date]] | None) -> list[date]:
    """The EMI debits the ledger recorded for this item's loan.

    Keyed by the item id, because the caller is the only layer that can see
    both the attestation and the transactions and decide which debits belong
    to which loan.
    """
    if not emi_payments:
        return []
    return emi_payments.get(item.get("id") or "", [])


def _bureau_resolver(bureau_accounts: list[dict[str, Any]]):
    """Find the bureau line an item is about, by identity not by uuid.

    Exactly the same failure as `_account_resolver` guards against, one table
    over: `bureau_accounts` rows are replaced wholesale each time a report is
    ingested, so a `bureau_account_id` captured against an older pull names a
    row that no longer exists - or, once the uuids are reused, somebody
    else's. On this ledger three unrelated items - a credit card, the home
    loan and the personal loan - had all come to share one bureau id.

    Identity here is the lender plus the masked number, which is what the
    label was built from when the item was adopted.
    """
    by_id = {b["id"]: b for b in bureau_accounts if b.get("id")}
    by_identity: dict[str, dict[str, Any]] = {}
    for line in bureau_accounts:
        ident = " ".join(x for x in (
            str(line.get("lender") or "").strip(),
            str(line.get("account_number_masked") or "").strip(),
        ) if x)
        if ident:
            by_identity.setdefault(ident.upper(), line)

    def resolve(item: dict[str, Any]) -> dict[str, Any] | None:
        label = str(item.get("label") or "").strip().upper()
        if label and label in by_identity:
            return by_identity[label]
        return by_id.get(item.get("bureau_account_id"))

    return resolve


def _superseded_accounts(accounts: list[Any],
                         held: dict[str, tuple[Decimal, str]],
                         holdings_any: list[dict[str, Any]] | None) -> set[str]:
    """Investment accounts a consolidated statement has already absorbed.

    `repository.get_holdings(latest_only=True)` deliberately drops a broker's
    own holdings when a CAS covers the same securities - "consolidated" means
    the CAS is a superset, and adding the broker's copy counts those shares
    twice.

    So an investment account that HAS holdings on file yet contributes none
    of them is not empty: it has been superseded. An account with no holdings
    at all is a different thing again - simply unknown - and must not be
    confused with either. The three states are why this returns a set rather
    than a boolean per account.
    """
    with_any = {row.get("account_id") for row in (holdings_any or [])
                if row.get("account_id")}
    return {a.id for a in accounts
            if getattr(a, "id", None)
            and a.account_type == AccountType.INVESTMENT
            and a.id in with_any
            and a.id not in held}


def _observed_for(account: Any,
                  held: dict[str, tuple[Decimal, str]] | None = None,
                  superseded: set[str] | None = None,
                  ) -> dict[str, Any] | None:
    """What the DOCUMENTS say about this account, or None for "nothing does".

    Returning None is a real answer and a different one from zero. An account
    nobody has uploaded a statement for has an UNKNOWN balance; reporting it
    as 0 turns "I do not know" into "you owe nothing", and then measures the
    user's own attested figure against that invented zero. That is how a 66.7
    lakh home loan came to be reported as 66.7 lakh of drift.
    """
    if account is None:
        return None

    if account.account_type in LOAN_TYPES or             account.account_type == AccountType.CREDIT_CARD:
        # Same fallback as `Account.balance`: a zero principal beside a real
        # current balance is which column the extractor happened to fill,
        # not a settled account.
        outstanding = account.principal_outstanding
        if not outstanding and account.current_balance:
            outstanding = account.current_balance
        return {"outstanding": outstanding, "as_of": account.balance_as_of,
                "credit_limit": account.credit_limit}

    if account.account_type == AccountType.INVESTMENT:
        # Holdings are canonical for an investment; `current_balance` is a
        # summary written beside them that can go stale on its own.
        if held and account.id in held:
            total, as_of = held[account.id]
            return {"outstanding": total,
                    "as_of": as_of or account.balance_as_of,
                    "credit_limit": account.credit_limit}
        if superseded and account.id in superseded:
            return {"superseded": True}
        return None

    # A balance nobody ever read is unknown, not zero. `balance_as_of` is the
    # marker for "a document stated this", so its absence is the marker for
    # "no document did".
    if not account.balance_as_of:
        return None
    return {"outstanding": account.current_balance,
            "as_of": account.balance_as_of,
            "credit_limit": account.credit_limit}


def _sum_known(values: list[Decimal | None]) -> tuple[Decimal | None, int]:
    """Total what is known, and count what is not.

    Summing with a zero default is the single most dangerous shortcut on this
    screen. A savings account whose balance nobody has recorded is not an
    account holding nothing, and adding it in as zero produced "assets: 0" and
    a net worth stated as if the person owned nothing at all - from a position
    that simply had one blank field in it. A card with no balance recorded
    likewise came out as "0% utilisation", which reads as a compliment.

    So: None when nothing is known, and the count of blanks travels with the
    total so the screen can say what the figure is missing.
    """
    known = [v for v in values if v is not None]
    return (sum(known, ZERO) if known else None, len(values) - len(known))


def _totals(aged: list[AgedItem],
            unconfirmed: Decimal | None = None) -> dict[str, Any]:
    loans = [a for a in aged if a.kind == "loan"]
    cards = [a for a in aged if a.kind == "card"]
    # An investment the documents already report under a consolidated
    # statement is the same money twice, so it is named and set aside rather
    # than added. And where holdings DO cover an investment, they are the
    # canonical figure - the attestation is a copy that can go stale.
    assets = [a for a in aged
              if a.kind in {"account", "investment"} and not a.superseded]
    superseded = [a for a in aged if a.superseded]

    loan_outstanding, loans_unknown = _sum_known([a.outstanding for a in loans])
    card_outstanding, cards_unknown = _sum_known([a.outstanding for a in cards])
    limit, _ = _sum_known([a.credit_limit for a in cards])
    asset_total, assets_unknown = _sum_known([
        a.observed_outstanding
        if (a.kind == "investment" and a.observed_outstanding is not None)
        else a.outstanding
        for a in assets])
    monthly_emi, _ = _sum_known([a.emi for a in loans])
    interest_left, _ = _sum_known(
        [a.total_interest_remaining for a in loans])

    owed = _add(loan_outstanding, card_outstanding)
    # Utilisation over the cards whose balance AND limit are both known.
    # Averaging a known balance against a total limit that includes cards
    # with no balance would understate it by however many are blank.
    priced = [a for a in cards
              if a.outstanding is not None and a.credit_limit]
    priced_out = sum((a.outstanding for a in priced), ZERO)
    priced_limit = sum((a.credit_limit for a in priced), ZERO)

    return {
        "loan_count": len(loans),
        "card_count": len(cards),
        "superseded_count": len(superseded),
        # Debt the bureau reports that nothing here has adopted. Named
        # rather than folded into `total_owed`, because it is a different
        # KIND of claim - a third party's word, unconfirmed - and because
        # the Overview adds exactly this figure on top of the accounts it
        # knows. Both screens can now quote the same composition instead of
        # silently disagreeing about scope.
        "unconfirmed_bureau_debt": _money(unconfirmed),
        "total_owed_including_unconfirmed": _money(_add(owed, unconfirmed)),
        "loan_outstanding": _money(loan_outstanding),
        "card_outstanding": _money(card_outstanding),
        "total_owed": _money(owed),
        "credit_limit": _money(limit),
        "card_utilisation_pct": (
            round(float(priced_out / priced_limit) * 100, 1)
            if priced_limit > 0 else None),
        "monthly_emi": _money(monthly_emi),
        "interest_remaining": _money(interest_left),
        "assets": _money(asset_total),
        "net": (None if asset_total is None and owed is None
                else _money((asset_total or ZERO) - (owed or ZERO))),
        # The same subtraction over the INCLUSIVE debt, because that is what
        # the Overview's net worth uses.
        #
        # `total_owed_including_unconfirmed` was added above so the two
        # screens could "quote the same composition instead of silently
        # disagreeing about scope" - but `net` was left over the exclusive
        # figure, so the disagreement moved from the debt line to the
        # headline: Position said -48,63,484 and Net Worth -48,91,143, a
        # 27,659 gap that is exactly the unconfirmed bureau debt and that
        # neither screen accounted for. Publishing both makes the
        # composition legible instead of leaving a reader to find the
        # difference by subtraction.
        "net_including_unconfirmed": (
            None if asset_total is None and owed is None
            else _money((asset_total or ZERO)
                        - (_add(owed, unconfirmed) or ZERO))),
        # Every figure above is only as complete as what has been filled in,
        # and a blank is not a zero. Counted here so the screen can say which
        # totals are short rather than presenting them as final.
        "unknown": {"loans": loans_unknown, "cards": cards_unknown,
                    "assets": assets_unknown},
        "is_complete": not (loans_unknown or cards_unknown or assets_unknown),
        # The furthest-out payoff across every loan: when this person stops
        # owing anybody anything at the current instalments.
        #
        # ISO strings, like every date `_item_json` emits. `build` returns
        # JSON and these three were the only raw `date`s left in it - which
        # is invisible over HTTP, because FastAPI encodes them on the way
        # out, and fatal anywhere else: an agent reading position() carried
        # them into its transcript and the whole run failed to store.
        "debt_free_on": _iso(max(
            (a.payoff_date for a in loans if a.payoff_date), default=None)),
        "next_due_on": _iso(min(
            (a.next_due_on for a in cards if a.next_due_on), default=None)),
        "reviewed_oldest": _iso(min((a.reviewed_on for a in aged),
                                    default=None)),
        "stale_count": sum(1 for a in aged if a.stale),
        "drifting_count": sum(1 for a in aged if a.drift is not None),
    }


def _add(*values: Decimal | None) -> Decimal | None:
    known = [v for v in values if v is not None]
    return sum(known, ZERO) if known else None


def _item_json(aged: AgedItem) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in vars(aged).items():
        if isinstance(value, Decimal):
            out[key] = _money(value)
        elif isinstance(value, date):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------

def _money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(round(Decimal(value), 2))


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# The first draft
# ---------------------------------------------------------------------------

#: Bureau match states that mean something in the ledger probably already
#: covers this line.
#:
#: `account_id` alone is not that question. It is set only for an AUTOMATIC
#: link, and an automatic link needs digits that agree - which for a credit
#: card they never do: CRIF reports an internal account reference
#: ("0000000014274199") where the statement reports the card's last four
#: ("XXXX5001"), so the strongest evidence available is that the lender and
#: the account type agree. The matcher scores that 0.55 and offers it rather
#: than applying it, because a holder with four Axis cards would otherwise
#: get them silently swapped.
#:
#: Treating "offered" as "unaccounted for" is what filled this screen with
#: sixteen bureau cards beside the fifteen ledger cards they describe, and
#: added 81 lakh of debt that is mostly one set of cards counted twice.
#: A maybe belongs on the suggestions list, not in the totals.
_ATTRIBUTED = frozenset({"auto", "confirmed", "suggested"})


def _is_attributed(bureau: dict[str, Any]) -> bool:
    """Whether the ledger already appears to account for this bureau line."""
    return bool(bureau.get("account_id")) or \
        (bureau.get("match_status") or "") in _ATTRIBUTED


def seed(accounts: list[Any], bureau_accounts: list[dict[str, Any]],
         projections: list[Any], *, as_of: date | None = None,
         bureau_as_of: date | None = None,
         last_activity: dict[str, date] | None = None,
         existing: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """A first position, drafted from everything already known.

    Nobody is going to type twelve accounts in from memory, and asking them to
    is how a screen like this ends up empty. Every figure the statements and
    the bureau already carry is filled in; what the user does is CORRECT it,
    which is a five-minute job rather than an afternoon, and correcting is
    also what makes the review mean something.

    Drafted, not attested. Each row comes back with the review date set to the
    day the figure is actually as-of - not today - because saying "I confirmed
    this today" on somebody's behalf is exactly the lie this whole design is
    built to avoid. It becomes attested when they press the button.

    Anything already in the position is left alone: this can be re-run after
    importing a new statement to pick up an account that did not exist before,
    without touching a single figure the user has since corrected.
    """
    as_of = as_of or date.today()
    taken_accounts = {e.get("account_id") for e in (existing or [])
                      if e.get("account_id")}
    taken_bureau = {e.get("bureau_account_id") for e in (existing or [])
                    if e.get("bureau_account_id")}
    by_account = {p.account_id: p for p in projections
                  if getattr(p, "account_id", None)}

    drafts: list[dict[str, Any]] = []
    order = 0

    for account in accounts:
        if not account.id or account.id in taken_accounts:
            continue
        order += 1
        common = {
            "account_id": account.id,
            "label": account.display_name(),
            "institution": account.institution or "",
            "sort_order": order,
            # The date the figure is true as of, in descending order of how
            # much it is worth: the statement period the balance was read
            # from, then the last transaction on the account, then today.
            # Today is the answer of last resort because dating a figure now
            # says it was confirmed now, and nothing confirmed it.
            "reviewed_on": (account.balance_as_of
                            or (last_activity or {}).get(account.id)
                            or as_of).isoformat(),
        }
        if account.account_type in LOAN_TYPES:
            projection = by_account.get(account.id)
            drafts.append({
                **common, "kind": "loan",
                "outstanding": _txt(account.principal_outstanding),
                "emi": _txt(account.emi_amount),
                "interest_rate": _txt(
                    account.interest_rate
                    or (projection.annual_rate if projection else None)),
                "months_remaining": (account.tenure_months_remaining
                                     or (projection.months_remaining
                                         if projection else None)),
            })
        elif account.account_type == AccountType.CREDIT_CARD:
            drafts.append({
                **common, "kind": "card",
                "outstanding": _txt(account.principal_outstanding),
                "credit_limit": _txt(account.credit_limit),
            })
        elif account.account_type == AccountType.INVESTMENT:
            drafts.append({**common, "kind": "investment",
                           "outstanding": _txt(account.current_balance)})
        else:
            drafts.append({**common, "kind": "account",
                           "outstanding": _txt(account.current_balance)})

    # Then the credit accounts the statements have never seen. These are the
    # rows that make the position worth having: a loan nothing else in this
    # app knows about, named by the lender who reported it.
    for bureau in bureau_accounts:
        if bureau.get("id") in taken_bureau:
            continue
        if (bureau.get("status") or "open") != "open":
            continue
        if _is_attributed(bureau):
            continue  # the ledger already covers this - see _ATTRIBUTED
        order += 1
        kind = "card" if "card" in (bureau.get("account_type") or "").lower() \
            else "loan"
        drafts.append({
            "kind": kind,
            "bureau_account_id": bureau["id"],
            "label": f"{bureau.get('lender') or 'Unknown lender'}"
                     f"{' ' + bureau['account_number_masked'] if bureau.get('account_number_masked') else ''}",
            "institution": bureau.get("lender") or "",
            "outstanding": bureau.get("current_balance"),
            "emi": bureau.get("emi_amount"),
            "credit_limit": bureau.get("credit_limit") if kind == "card" else None,
            "original_amount": bureau.get("sanctioned") if kind == "loan" else None,
            "sort_order": order,
            # The date the BUREAU pulled the report, not today. A bureau
            # balance is routinely 30-60 days old, and dating it now would
            # make a stale figure look freshly confirmed.
            "reviewed_on": (bureau_as_of or as_of).isoformat(),
            "notes": "From your credit report - no statement has been "
                     "imported for this account.",
        })

    return drafts


def _txt(value: Any) -> str | None:
    return None if value is None else str(value)
