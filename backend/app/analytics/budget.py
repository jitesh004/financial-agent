"""What a month costs before any choices are made, and what that leaves.

This module exists to answer the questions people actually ask themselves,
which are not the questions a category breakdown answers:

  * Which of my expenses are fixed, and for how long will they be fixed?
  * Which vary, and by how much?
  * What does a month cost me before I decide anything?
  * What is left after that?

None of it is a target the user typed in. Everything here is read off their own
statements: a commitment is a recurring series the detector found (see
analytics.recurring), its end date comes from the loan's own amortization
(analytics.loans), and a variable category's monthly figure is the MEDIAN of
what that category actually cost per month - never the mean, because one
holiday or one hospital bill would otherwise set the expectation for every
month after it.

The one distinction this module insists on: a SIP is not an expense. Money
moving into an investment every month is as committed as an EMI and as
unavailable to spend, but it is still the user's money afterwards. Counting it
as spending makes a diligent saver look reckless. So commitments are reported
in three kinds - debt, spending and saving - and only the first two are
subtracted to reach what a month costs.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..models.schemas import (CONTRA_EXPENSE_ROLES, Category, Direction,
                              FlowRole, Transaction)
from . import periods
from .recurring import RecurringSeries

ZERO = Decimal("0")


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


#: Categories whose recurring charges are debt service rather than spending.
#: An EMI leaves the account every month like rent does, but it is buying down
#: a balance, and it has an end date - which is the whole reason to tell them
#: apart.
DEBT_CATEGORIES = {Category.EMI, Category.LOAN_INTEREST}

#: A series has to look at least roughly monthly to belong in a monthly
#: budget. A yearly insurance premium is a real commitment, so it is included
#: and normalised, but a weekly one is a spending habit rather than a
#: commitment and is left to the variable side.
MIN_CADENCE_DAYS = 20
MAX_CADENCE_DAYS = 400


@dataclass
class Commitment:
    """Something that leaves every month whether or not you decide anything."""

    label: str
    category: str
    #: "debt", "spending" or "saving" - see the module docstring.
    kind: str
    monthly: Decimal
    cadence: str
    cadence_days: int
    occurrences: int
    #: In the window: how many months it actually appeared in, and what it
    #: came to. A commitment the window only caught once is reported as such
    #: rather than presented as an established monthly figure.
    months_seen: int = 0
    charged_in_window: Decimal = ZERO
    last_seen: date | None = None
    next_expected: date | None = None
    #: When it stops. Known for a loan, from its own amortization; a
    #: subscription has no end until somebody cancels it, and saying "unknown"
    #: is the truthful answer rather than picking one.
    ends_on: date | None = None
    months_left: int | None = None
    account: str = ""
    series_id: str = ""
    confidence: float = 0.0


@dataclass
class VariableLine:
    """A category that varies, with what it typically costs and how far it swings."""

    category: str
    group: str
    #: The MEDIAN month, which is what "typically" has to mean here.
    typical_monthly: Decimal
    low_monthly: Decimal
    high_monthly: Decimal
    total: Decimal
    months_seen: int
    transaction_count: int
    #: True when it appeared in every month of the window. A category that
    #: turns up every single month is effectively fixed even though no single
    #: merchant recurs - groceries are the obvious case - and that is a
    #: different kind of thing from one big trip in one month.
    every_month: bool = False


@dataclass
class BudgetResult:
    months: int = 0
    #: Median monthly income, and the total over the window.
    income_typical: Decimal = ZERO
    income_total: Decimal = ZERO

    commitments: list[Commitment] = field(default_factory=list)
    variable: list[VariableLine] = field(default_factory=list)

    committed_debt: Decimal = ZERO
    committed_spending: Decimal = ZERO
    committed_saving: Decimal = ZERO
    variable_typical: Decimal = ZERO

    notes: list[str] = field(default_factory=list)

    @property
    def committed_total(self) -> Decimal:
        """What is spoken for each month, saving included."""
        return _q(self.committed_debt + self.committed_spending
                  + self.committed_saving)

    @property
    def monthly_cost(self) -> Decimal:
        """What a month costs: commitments that leave for good, plus the
        typical variable spend. Investment is excluded - it is still yours."""
        return _q(self.committed_debt + self.committed_spending
                  + self.variable_typical)

    @property
    def headroom(self) -> Decimal:
        """What a typical month leaves after everything above."""
        return _q(self.income_typical - self.monthly_cost - self.committed_saving)

    @property
    def committed_ratio(self) -> float:
        if not self.income_typical:
            return 0.0
        return round(float(self.committed_total / self.income_typical) * 100, 1)


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        return ZERO
    return _q(Decimal(statistics.median([float(v) for v in values])))


def analyse_budget(
    transactions: list[Transaction],
    series: list[RecurringSeries],
    *,
    period: periods.Period | None = None,
    loans: list | None = None,
    accounts: dict | None = None,
    today: date | None = None,
) -> BudgetResult:
    """Split the window's outflow into what is committed and what is chosen."""
    result = BudgetResult()
    accounts = accounts or {}
    today = today or date.today()

    rows = periods.filter_transactions(transactions, period)
    if not rows:
        result.notes.append("No transactions in this period to budget from.")
        return result

    months = periods.covered_months(rows)
    result.months = len(months)

    # ---- income, per month, median ----
    per_month_income: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for txn in rows:
        if txn.role == FlowRole.INCOME:
            value = (txn.amount if txn.direction == Direction.CREDIT
                     else -txn.amount)
            per_month_income[periods.effective_month(txn)] += value
    result.income_total = _q(sum(per_month_income.values(), ZERO))
    result.income_typical = _median(
        [per_month_income.get(m, ZERO) for m in months])

    # ---- commitments, from the detected series ----
    #: Which rows a series accounts for, so the variable side can exclude
    #: them. Read from the series' own membership rather than re-matched, so
    #: the two halves cannot both claim the same rupee.
    committed_ids: set[str] = set()
    by_id = {t.id: t for t in rows if t.id}
    #: ...and from the rows themselves, which the detector stamps. Either
    #: source alone has a gap: a series read back from storage carries no
    #: membership list, and a row imported before the detector ran carries no
    #: series id.
    stamped: dict[str, list[Transaction]] = defaultdict(list)
    for txn in rows:
        if txn.recurring_series_id:
            stamped[txn.recurring_series_id].append(txn)

    for one in series:
        if one.direction != Direction.DEBIT or not one.is_active:
            continue
        if not (MIN_CADENCE_DAYS <= one.cadence_days <= MAX_CADENCE_DAYS):
            continue
        members = [by_id[i] for i in one.transaction_ids if i in by_id]
        if not members:
            members = stamped.get(one.id, [])
        # A series whose payments all fall outside the window is not this
        # period's commitment - it may have ended long ago.
        if not members:
            continue
        # The card bill is not a commitment of its own: the purchases it
        # settles are already counted one by one, and counting both is the
        # double count the whole accounting model exists to remove.
        if one.category == Category.CC_PAYMENT:
            continue

        committed_ids.update(t.id for t in members if t.id)
        kind = ("debt" if one.category in DEBT_CATEGORIES
                else "saving" if one.category == Category.INVESTMENT
                else "spending")
        member_months = {periods.effective_month(t) for t in members}
        account = accounts.get(one.account_id)

        # What it costs a month IN THIS WINDOW, from the charges that fall in
        # it - not the series' lifetime median. Rent that went up in July
        # should read as the new rent when you ask about the last three
        # months, and as the old one when you ask about last year.
        #
        # Two occurrences are needed for a median to mean anything; below
        # that the series-wide figure is the better estimate, and the UI marks
        # those rows as having been seen only once here.
        monthly = (_monthly_from(members, one.cadence_days)
                   if len(members) >= 2 else _q(one.monthly_equivalent))

        commitment = Commitment(
            label=one.label,
            category=one.category,
            kind=kind,
            monthly=monthly,
            cadence=one.cadence_name,
            cadence_days=one.cadence_days,
            occurrences=one.occurrences,
            months_seen=len(member_months),
            charged_in_window=_q(sum((t.amount for t in members), ZERO)),
            last_seen=one.last_seen,
            next_expected=one.next_expected,
            account=account.display_name() if account else "",
            series_id=one.id,
            confidence=one.confidence,
        )
        _attach_end_date(commitment, one, loans or [])
        result.commitments.append(commitment)

    # Biggest first: the question is what dominates the month.
    result.commitments.sort(key=lambda c: -c.monthly)
    for commitment in result.commitments:
        if commitment.kind == "debt":
            result.committed_debt += commitment.monthly
        elif commitment.kind == "saving":
            result.committed_saving += commitment.monthly
        else:
            result.committed_spending += commitment.monthly
    result.committed_debt = _q(result.committed_debt)
    result.committed_spending = _q(result.committed_spending)
    result.committed_saving = _q(result.committed_saving)

    # ---- what is left: the spending that is actually chosen ----
    from ..models.schemas import CATEGORY_GROUPS

    group_of = {c: g for g, cats in CATEGORY_GROUPS.items() for c in cats}
    per_category: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO))
    counts: dict[str, int] = defaultdict(int)

    for txn in rows:
        if txn.id in committed_ids:
            continue
        role = txn.role
        if role == FlowRole.EXPENSE:
            value = (txn.amount if txn.direction == Direction.DEBIT
                     else -txn.amount)
        elif role in CONTRA_EXPENSE_ROLES:
            # Money back reduces the category it came back against, exactly
            # as it does everywhere else in the app.
            value = -txn.amount if txn.direction == Direction.CREDIT else txn.amount
        else:
            continue
        per_category[txn.category][periods.effective_month(txn)] += value
        counts[txn.category] += 1

    for category, per_month in per_category.items():
        amounts = [per_month[m] for m in sorted(per_month)]
        if not amounts:
            continue
        line = VariableLine(
            category=category,
            group=group_of.get(category, "Other"),
            typical_monthly=_median(amounts),
            low_monthly=_q(min(amounts)),
            high_monthly=_q(max(amounts)),
            total=_q(sum(amounts, ZERO)),
            months_seen=len(amounts),
            transaction_count=counts[category],
            every_month=len(amounts) >= result.months > 0,
        )
        result.variable.append(line)

    result.variable.sort(key=lambda v: -v.typical_monthly)
    result.variable_typical = _q(
        sum((v.typical_monthly for v in result.variable), ZERO))

    _add_notes(result)
    return result


def _monthly_from(members: list[Transaction], cadence_days: int) -> Decimal:
    """A commitment's monthly cost, from the charges in the window.

    The median charge, normalised by its cadence - so a quarterly premium is
    reported as a third of itself per month, which is what a monthly budget
    needs it to be. Median rather than mean for the usual reason: one
    part-payment or one missed month should not move the figure.
    """
    if not members:
        return ZERO
    typical = _median([t.amount for t in members])
    if cadence_days <= 0:
        return typical
    return _q(typical * Decimal("30.44") / Decimal(cadence_days))


def _attach_end_date(commitment: Commitment, series: RecurringSeries,
                     loans: list) -> None:
    """When a commitment stops, where that is knowable.

    Only a loan knows: its amortization says how many payments are left and
    what date the last one falls on. Matched on the account first and the EMI
    amount second, because one account can carry more than one loan.
    """
    if commitment.kind != "debt":
        return
    candidates = [p for p in loans if p.account_id == series.account_id]
    if not candidates:
        candidates = list(loans)
    if not candidates:
        return
    best = min(candidates, key=lambda p: abs(
        float(p.emi or 0) - float(series.median_amount)))
    # A loan whose EMI is nothing like this charge is not this charge's loan.
    if best.emi and abs(float(best.emi) - float(series.median_amount)) \
            > 0.25 * float(series.median_amount):
        return
    commitment.ends_on = best.payoff_date
    commitment.months_left = best.months_remaining


def _add_notes(result: BudgetResult) -> None:
    """Caveats that have to travel with these figures."""
    if result.months < 3:
        result.notes.append(
            f"Only {result.months} month(s) in this period. A typical month "
            f"cannot be established from this little - widen the period.")
    if not result.income_typical:
        result.notes.append(
            "No income was found in this period, so there is nothing to "
            "measure the month's cost against.")
    thin = [c.label for c in result.commitments if c.months_seen < 2]
    if thin:
        result.notes.append(
            f"{len(thin)} commitment(s) appeared only once in this period, so "
            f"their monthly figure comes from the wider history rather than "
            f"from this window: {', '.join(thin[:4])}"
            + ("…" if len(thin) > 4 else ""))
    if result.headroom < 0:
        result.notes.append(
            "A typical month costs more than a typical month brings in. The "
            "gap is being covered by savings, by credit, or by income that "
            "is not in these statements.")
