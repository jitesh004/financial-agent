"""The analytics engine: every number the user sees originates here.

Hard rule for this module: all arithmetic is done in Python over Decimals. No
language model participates in producing a figure. The LLM's job, later, is to
read these computed results and write prose about them - it never adds, divides
or estimates. That separation is what makes the output trustworthy.

Every aggregate excludes internal transfers. `Transaction.is_spend` is the
single definition of "money that actually left", and everything here defers to
it rather than re-deriving the rule.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from ..models.schemas import (Account, AccountType, CATEGORY_GROUPS, Category,
                              Direction, INCOME_CATEGORIES, LIABILITY_TYPES,
                              Transaction)

CENT = Decimal("0.01")
ZERO = Decimal("0")


def q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _pct(part: Decimal, whole: Decimal) -> float:
    if not whole:
        return 0.0
    return round(float(part / whole) * 100, 2)


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass
class MonthlyFlow:
    month: str
    income: Decimal
    spend: Decimal
    invested: Decimal
    #: Every rupee of cash that actually left this month: spending PLUS
    #: committed transfers out (EMI, SIP). Excludes mirror legs and the credit
    #: card bill, whose underlying purchases are already counted in `spend`.
    #: `spend` alone understates outflow badly for anyone with loans.
    total_outflow: Decimal
    net: Decimal
    savings_rate: float
    transaction_count: int


@dataclass
class CategoryBreakdown:
    category: str
    group: str
    total: Decimal
    share_pct: float
    transaction_count: int
    monthly_average: Decimal
    largest_single: Decimal
    largest_description: str


@dataclass
class MerchantSpend:
    merchant: str
    total: Decimal
    count: int
    average: Decimal
    category: str
    first_seen: date
    last_seen: date


@dataclass
class SalaryFlow:
    """Where each salary went in the days after it landed."""
    month: str
    salary_date: date
    salary_amount: Decimal
    allocations: list[tuple[str, Decimal, float]]
    #: What remained un-spent by the time the next salary arrived.
    left_over: Decimal
    days_to_next_salary: int
    #: Day count until half the salary had been spent - a burn-speed measure.
    days_to_half_spent: int | None


@dataclass
class AnalysisResult:
    period_start: date | None = None
    period_end: date | None = None
    months_covered: int = 0

    total_income: Decimal = ZERO
    total_spend: Decimal = ZERO
    total_invested: Decimal = ZERO
    net_savings: Decimal = ZERO
    savings_rate: float = 0.0

    average_monthly_income: Decimal = ZERO
    average_monthly_spend: Decimal = ZERO

    monthly: list[MonthlyFlow] = field(default_factory=list)
    #: month -> {category: total}. Computed rather than approximated from
    #: period-wide shares, so the trend chart shows real figures.
    monthly_by_category: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    by_category: list[CategoryBreakdown] = field(default_factory=list)
    by_group: dict[str, Decimal] = field(default_factory=dict)
    top_merchants: list[MerchantSpend] = field(default_factory=list)
    salary_flows: list[SalaryFlow] = field(default_factory=list)

    income_sources: list[tuple[str, Decimal, int]] = field(default_factory=list)
    net_worth: dict[str, Decimal] = field(default_factory=dict)

    largest_expenses: list[Transaction] = field(default_factory=list)
    unusual: list[tuple[Transaction, str]] = field(default_factory=list)

    transaction_count: int = 0
    internal_transfer_total: Decimal = ZERO
    uncategorized_total: Decimal = ZERO
    uncategorized_count: int = 0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Core analysis
# --------------------------------------------------------------------------

def analyze(
    transactions: list[Transaction],
    accounts: dict[str, Account] | None = None,
) -> AnalysisResult:
    """Compute the full picture from a categorized, reconciled ledger."""
    result = AnalysisResult()
    accounts = accounts or {}

    if not transactions:
        result.notes.append("No transactions available to analyze.")
        return result

    txns = sorted(transactions, key=lambda t: t.txn_date)
    result.period_start = txns[0].txn_date
    result.period_end = txns[-1].txn_date
    result.transaction_count = len(txns)

    spend_txns = [t for t in txns if t.is_spend]
    income_txns = [
        t for t in txns
        if t.direction == Direction.CREDIT
        and not t.is_internal_transfer
        and t.category in INCOME_CATEGORIES
    ]
    invested_txns = [
        t for t in txns
        if t.category == Category.INVESTMENT
        and t.direction == Direction.DEBIT
        and not t.is_internal_transfer
    ]

    result.total_income = q(sum((t.amount for t in income_txns), ZERO))
    result.total_spend = q(sum((t.amount for t in spend_txns), ZERO))
    result.total_invested = q(sum((t.amount for t in invested_txns), ZERO))
    result.internal_transfer_total = q(
        sum((t.amount for t in txns if t.is_internal_transfer), ZERO)
    )

    # Savings = what came in, minus what was consumed. Money moved into
    # investments is still the user's, so it counts as saved, not spent.
    result.net_savings = q(result.total_income - result.total_spend)
    result.savings_rate = _pct(result.net_savings, result.total_income)

    result.months_covered = _count_months(result.period_start, result.period_end)
    if result.months_covered:
        divisor = Decimal(result.months_covered)
        result.average_monthly_income = q(result.total_income / divisor)
        result.average_monthly_spend = q(result.total_spend / divisor)

    result.monthly = _monthly_flows(txns)
    result.monthly_by_category = _monthly_by_category(spend_txns)
    result.by_category = _category_breakdown(spend_txns, result.months_covered)
    result.by_group = _group_totals(result.by_category)
    result.top_merchants = _merchant_spend(spend_txns)
    result.income_sources = _income_sources(income_txns)
    result.salary_flows = _salary_flows(txns)
    result.net_worth = _net_worth(accounts)

    result.largest_expenses = sorted(spend_txns, key=lambda t: -t.amount)[:15]
    result.unusual = _find_unusual(spend_txns, result.by_category)

    uncategorized = [t for t in spend_txns if t.category == Category.UNCATEGORIZED]
    result.uncategorized_total = q(sum((t.amount for t in uncategorized), ZERO))
    result.uncategorized_count = len(uncategorized)

    _add_quality_notes(result)
    return result


def _count_months(start: date | None, end: date | None) -> int:
    if not start or not end:
        return 0
    return max(1, (end.year - start.year) * 12 + (end.month - start.month) + 1)


def _monthly_flows(txns: list[Transaction]) -> list[MonthlyFlow]:
    buckets: dict[str, dict[str, Decimal | int]] = defaultdict(
        lambda: {"income": ZERO, "spend": ZERO, "invested": ZERO,
                 "outflow": ZERO, "count": 0}
    )

    for t in txns:
        b = buckets[_month_key(t.txn_date)]
        b["count"] = int(b["count"]) + 1
        if t.is_spend:
            b["spend"] = b["spend"] + t.amount
        elif (t.direction == Direction.CREDIT and not t.is_internal_transfer
              and t.category in INCOME_CATEGORIES):
            b["income"] = b["income"] + t.amount
        if (t.category == Category.INVESTMENT and t.direction == Direction.DEBIT
                and not t.is_mirror_leg):
            b["invested"] = b["invested"] + t.amount
        if (t.direction == Direction.DEBIT and not t.is_mirror_leg
                and t.category != Category.CC_PAYMENT):
            b["outflow"] = b["outflow"] + t.amount

    out = []
    for month in sorted(buckets):
        b = buckets[month]
        income, spend = q(b["income"]), q(b["spend"])
        net = q(income - spend)
        out.append(MonthlyFlow(
            month=month, income=income, spend=spend,
            invested=q(b["invested"]), total_outflow=q(b["outflow"]), net=net,
            savings_rate=_pct(net, income),
            transaction_count=int(b["count"]),
        ))
    return out


def _monthly_by_category(spend_txns: list[Transaction]) -> dict[str, dict[str, Decimal]]:
    """Actual per-month spend per category."""
    out: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for t in spend_txns:
        bucket = out[_month_key(t.txn_date)]
        bucket[t.category.value] = bucket[t.category.value] + t.amount
    return {month: {c: q(v) for c, v in cats.items()} for month, cats in out.items()}


def _category_breakdown(
    spend_txns: list[Transaction], months: int
) -> list[CategoryBreakdown]:
    total = sum((t.amount for t in spend_txns), ZERO)
    groups = {c: g for g, cats in CATEGORY_GROUPS.items() for c in cats}
    buckets: dict[Category, list[Transaction]] = defaultdict(list)
    for t in spend_txns:
        buckets[t.category].append(t)

    out: list[CategoryBreakdown] = []
    for category, members in buckets.items():
        subtotal = sum((t.amount for t in members), ZERO)
        largest = max(members, key=lambda t: t.amount)
        out.append(CategoryBreakdown(
            category=category.value,
            group=groups.get(category, "Other"),
            total=q(subtotal),
            share_pct=_pct(subtotal, total),
            transaction_count=len(members),
            monthly_average=q(subtotal / Decimal(months)) if months else ZERO,
            largest_single=q(largest.amount),
            largest_description=largest.raw_description[:80],
        ))

    out.sort(key=lambda c: -c.total)
    return out


def _group_totals(breakdown: list[CategoryBreakdown]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in breakdown:
        totals[row.group] = totals[row.group] + row.total
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def _merchant_spend(spend_txns: list[Transaction], limit: int = 25) -> list[MerchantSpend]:
    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for t in spend_txns:
        key = (t.merchant or t.normalized_description or t.raw_description)[:40].strip()
        if key:
            buckets[key].append(t)

    out = []
    for merchant, members in buckets.items():
        total = sum((m.amount for m in members), ZERO)
        dates = [m.txn_date for m in members]
        out.append(MerchantSpend(
            merchant=merchant,
            total=q(total),
            count=len(members),
            average=q(total / Decimal(len(members))),
            category=members[0].category.value,
            first_seen=min(dates),
            last_seen=max(dates),
        ))

    out.sort(key=lambda m: -m.total)
    return out[:limit]


def _income_sources(income_txns: list[Transaction]) -> list[tuple[str, Decimal, int]]:
    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for t in income_txns:
        key = (t.normalized_description or t.raw_description)[:45].strip()
        buckets[key].append(t)

    out = [
        (key, q(sum((m.amount for m in members), ZERO)), len(members))
        for key, members in buckets.items()
    ]
    out.sort(key=lambda row: -row[1])
    return out[:15]


# --------------------------------------------------------------------------
# "After the salary arrived, where did it go?"
# --------------------------------------------------------------------------

def _salary_flows(txns: list[Transaction]) -> list[SalaryFlow]:
    """Trace each salary credit through to the next one.

    This answers the question people actually ask - not "what did I spend on
    food this year" but "the money landed on the 1st, and by the 20th it was
    gone; where did it go?". Windowing between consecutive salary credits is
    what makes the answer concrete rather than an average.
    """
    salaries = sorted(
        (t for t in txns if t.category == Category.SALARY
         and t.direction == Direction.CREDIT),
        key=lambda t: t.txn_date,
    )
    if not salaries:
        return []

    # Collapse same-day splits (some employers pay in two instalments).
    merged: list[Transaction] = []
    for s in salaries:
        if merged and (s.txn_date - merged[-1].txn_date).days <= 2:
            continue
        merged.append(s)

    # What counts as "where the salary went":
    #   - every real outflow (rent, groceries, card purchases, fees)
    #   - plus committed transfers out: an EMI or SIP leaving the account is
    #     absolutely part of the answer
    #   - MINUS mirror legs, which are another account's copy of the same money
    #   - MINUS the credit-card bill payment, because the individual card
    #     purchases it settles are already counted line by line. Counting both
    #     is how these views end up claiming someone spent more than they earned.
    def _counts(t: Transaction) -> bool:
        if t.direction != Direction.DEBIT or t.is_mirror_leg:
            return False
        return t.category != Category.CC_PAYMENT

    outflows = sorted((t for t in txns if _counts(t)), key=lambda t: t.txn_date)
    committed: list[Transaction] = []

    flows: list[SalaryFlow] = []
    for i, salary in enumerate(merged):
        window_start = salary.txn_date
        window_end = (merged[i + 1].txn_date if i + 1 < len(merged)
                      else max(t.txn_date for t in txns) + timedelta(days=1))

        in_window = [t for t in outflows + committed
                     if window_start <= t.txn_date < window_end]
        if not in_window:
            continue

        by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for t in in_window:
            by_category[t.category.value] = by_category[t.category.value] + t.amount

        spent = sum(by_category.values(), ZERO)
        allocations = sorted(
            ((cat, q(amt), _pct(amt, salary.amount)) for cat, amt in by_category.items()),
            key=lambda row: -row[1],
        )

        flows.append(SalaryFlow(
            month=_month_key(salary.txn_date),
            salary_date=salary.txn_date,
            salary_amount=q(salary.amount),
            allocations=allocations,
            left_over=q(salary.amount - spent),
            days_to_next_salary=(window_end - window_start).days,
            days_to_half_spent=_days_to_half(salary, in_window),
        ))

    return flows


def _days_to_half(salary: Transaction, outflows: list[Transaction]) -> int | None:
    """How many days until half the salary had been spent."""
    half = salary.amount / 2
    running = ZERO
    for t in sorted(outflows, key=lambda t: t.txn_date):
        running += t.amount
        if running >= half:
            return (t.txn_date - salary.txn_date).days
    return None


# --------------------------------------------------------------------------
# Net worth and anomalies
# --------------------------------------------------------------------------

def _net_worth(accounts: dict[str, Account]) -> dict[str, Decimal]:
    assets, liabilities = ZERO, ZERO
    detail: dict[str, Decimal] = {}

    for account in accounts.values():
        signed = account.balance
        if signed is None:
            continue
        label = account.display_name()
        if signed < 0:
            liabilities += -signed
        else:
            assets += signed
        detail[label] = q(signed)

    detail["_assets"] = q(assets)
    detail["_liabilities"] = q(liabilities)
    detail["_net"] = q(assets - liabilities)
    return detail


def _find_unusual(
    spend_txns: list[Transaction],
    breakdown: list[CategoryBreakdown],
) -> list[tuple[Transaction, str]]:
    """Flag transactions that are large relative to their own category.

    Uses median + MAD rather than mean + standard deviation: spending is heavily
    right-skewed, and a single large outlier inflates a standard deviation
    enough to hide itself.
    """
    by_category: dict[Category, list[Transaction]] = defaultdict(list)
    for t in spend_txns:
        by_category[t.category].append(t)

    flagged: list[tuple[Transaction, str]] = []
    for category, members in by_category.items():
        if len(members) < 6:
            continue
        amounts = [float(t.amount) for t in members]
        median = statistics.median(amounts)
        mad = statistics.median([abs(a - median) for a in amounts])
        if mad <= 0:
            continue
        threshold = median + 6 * mad

        for t in members:
            if float(t.amount) > threshold:
                flagged.append((
                    t,
                    f"{t.amount:,.0f} is well above the typical "
                    f"{category.value.replace('_', ' ')} spend of ~{median:,.0f}.",
                ))

    flagged.sort(key=lambda pair: -pair[0].amount)
    return flagged[:12]


def _add_quality_notes(result: AnalysisResult) -> None:
    """Surface caveats that should travel with the numbers."""
    if result.uncategorized_count:
        result.notes.append(
            f"{result.uncategorized_count} transactions totalling "
            f"{result.uncategorized_total:,.2f} could not be categorized and sit "
            f"outside the category breakdown."
        )
    if result.months_covered < 3:
        result.notes.append(
            f"Only {result.months_covered} month(s) of data. Averages and trends "
            f"from this little history are indicative, not reliable."
        )
    if result.total_income == 0 and result.total_spend > 0:
        result.notes.append(
            "No income transactions were found, so the savings rate cannot be "
            "computed. If your salary account statement is missing, upload it."
        )
