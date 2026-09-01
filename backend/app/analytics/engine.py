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
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..models.schemas import (Account, AccountType, CATEGORY_GROUPS, Category,
                              CONTRA_EXPENSE_ROLES, Direction, FlowRole,
                              INCOME_CATEGORIES, LIABILITY_TYPES, Transaction)
from ..rules import formats

#: Shared with the loan calculator and every other place a figure is rounded -
#: see rules.formats. Kept under these names because callers import them here.
CENT = formats.CENT
ZERO = Decimal("0")


def q(value: Decimal) -> Decimal:
    """Round to paise. One implementation, in rules.formats."""
    return formats.to_paise(Decimal(value))


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
    #: Net of anything that came back against it - see AnalysisResult.
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
    #: What was laid out this month before offsets, and what came back.
    gross_spend: Decimal = ZERO
    offsets: Decimal = ZERO


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

@dataclass
class P2PBalance:
    counterparty: str
    sent: Decimal
    received: Decimal
    net_owed_to_me: Decimal
    transaction_count: int
    last_activity: date

@dataclass
class AnalysisResult:
    period_start: date | None = None
    period_end: date | None = None
    months_covered: int = 0

    total_income: Decimal = ZERO
    #: What spending actually cost, after money that came back against it.
    total_spend: Decimal = ZERO
    #: What was laid out before any of it came back. Reported alongside
    #: `total_spend` rather than instead of it: someone who is reimbursed
    #: heavily needs to see both, and either figure alone misleads.
    gross_spend: Decimal = ZERO
    #: Refunds and repayments of expenses that were never the user's.
    total_offsets: Decimal = ZERO
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
    p2p_balances: list[P2PBalance] = field(default_factory=list)

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


def _p2p_balances(txns: list[Transaction]) -> list[P2PBalance]:
    from ..categorize.rules import _payee_field
    from collections import defaultdict
    
    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.category == Category.P2P_TRANSFER:
            # Try to extract exact counterparty name
            name = _payee_field(t)
            # Fallback to normalized description if regex fails
            if not name:
                name = (t.normalized_description or t.raw_description)[:40].strip()
            # Clean up UPI string prefixes if present
            name = re.sub(r'^(UPI[/_]|VPA[/_])', '', name, flags=re.IGNORECASE)
            name = name.split('/')[0].split('@')[0].strip().title()
            if name:
                buckets[name].append(t)
                
    out = []
    for name, members in buckets.items():
        sent = sum((t.amount for t in members if t.direction == Direction.DEBIT), ZERO)
        received = sum((t.amount for t in members if t.direction == Direction.CREDIT), ZERO)
        net = sent - received
        
        out.append(P2PBalance(
            counterparty=name,
            sent=q(sent),
            received=q(received),
            net_owed_to_me=q(net),
            transaction_count=len(members),
            last_activity=max(t.txn_date for t in members)
        ))
        
    # Sort by absolute outstanding balance
    out.sort(key=lambda b: -abs(b.net_owed_to_me))
    return out

def analyze(
    transactions: list[Transaction],
    accounts: dict[str, Account] | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> AnalysisResult:
    """Compute the full picture from a categorized, reconciled ledger.

    When `start` and/or `end` are provided, only transactions within that
    range are included.  The resulting `period_start` / `period_end` are set
    to the explicit bounds so the caller can distinguish "no data" from
    "data that happens to start later".
    """
    result = AnalysisResult()
    accounts = accounts or {}

    if not transactions:
        result.notes.append("No transactions available to analyze.")
        return result

    txns = sorted(transactions, key=lambda t: t.txn_date)

    # The span is measured over rows that COUNT. An excluded row is one
    # somebody looked at and rejected - a misread date, a duplicate, a figure
    # lifted out of a terms-and-conditions example - and letting it set the
    # first or last date means rejecting it changes none of the numbers that
    # made it obvious. One row dated 2020 on a ledger of four real months
    # reported seventy-four months covered, and every per-month average
    # divided by that.
    spanning = [t for t in txns if not t.excluded] or txns

    # ...and measured in the months the rows are COUNTED in, which is what
    # every monthly figure below is bucketed by. A refund carrying the date of
    # the purchase it reverses is billed in this cycle and counted in it, so a
    # span read off the raw dates announced "27 Apr - 17 Aug, 5 months" over a
    # table with four month rows in it. The header and the rows have to be
    # answering the same question.
    def _effective_month(txn):
        return txn.accounting_month or _month_key(txn.txn_date)

    if spanning:
        first_month = min(_effective_month(t) for t in spanning)
        last_month = max(_effective_month(t) for t in spanning)
        in_first = [t for t in spanning if _effective_month(t) == first_month]
        in_last = [t for t in spanning if _effective_month(t) == last_month]
        span_start = min(t.txn_date for t in in_first)
        span_end = max(t.txn_date for t in in_last)
    else:  # pragma: no cover - guarded by the empty check above
        span_start = span_end = None

    if start or end:
        s = start or txns[0].txn_date
        e = end or txns[-1].txn_date
        txns = [t for t in txns if s <= t.txn_date <= e]
        if not txns:
            result.notes.append(
                f"No transactions in the requested range "
                f"{s.isoformat()} – {e.isoformat()}."
            )
            return result
        result.period_start = s
        result.period_end = e
    else:
        result.period_start = span_start
        result.period_end = span_end
    result.transaction_count = len(txns)

    # Every total below is a sum over ONE role, so the sets are disjoint by
    # construction and no rupee can land in two of them. The previous version
    # built each figure from its own overlapping predicate, which is how
    # `total_invested` and the monthly `invested` column came to disagree -
    # one excluded internal transfers, the other excluded mirror legs.
    by_role: dict[FlowRole, list[Transaction]] = defaultdict(list)
    for txn in txns:
        by_role[txn.role].append(txn)

    spend_txns = by_role[FlowRole.EXPENSE]
    income_txns = by_role[FlowRole.INCOME]
    invested_txns = by_role[FlowRole.INVESTMENT]
    # Money coming back against an expense already counted as spending: a
    # merchant refund, or someone repaying a purchase that was never the
    # user's. Booking these as income inflates both sides of the ledger for
    # what was really a cancelled or borrowed purchase.
    offset_txns = [t for t in txns if t.role in CONTRA_EXPENSE_ROLES]

    result.total_income = q(sum((_income_val(t) for t in income_txns), ZERO))
    result.gross_spend = q(sum((_spend_val(t) for t in spend_txns), ZERO))
    result.total_offsets = q(sum((_income_val(t) for t in offset_txns), ZERO))
    # Reported net, with the gross figure kept alongside it: a user who is
    # reimbursed heavily needs to see both what they laid out and what it
    # actually cost them, and showing only one of those is misleading either
    # way round.
    result.total_spend = q(result.gross_spend - result.total_offsets)
    result.total_invested = q(sum((_spend_val(t) for t in invested_txns), ZERO))
    result.internal_transfer_total = q(
        sum((t.amount for t in txns
             if t.is_internal_transfer and not t.is_mirror_leg), ZERO)
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
    result.monthly_by_category = _monthly_by_category(spend_txns + offset_txns)
    result.by_category = _category_breakdown(spend_txns + offset_txns, result.months_covered)
    result.by_group = _group_totals(result.by_category)
    result.top_merchants = _merchant_spend(spend_txns + offset_txns)
    result.income_sources = _income_sources(income_txns)
    result.salary_flows = _salary_flows(txns)
    result.p2p_balances = _p2p_balances(txns)
    result.net_worth = _net_worth(accounts)

    result.largest_expenses = sorted(spend_txns, key=lambda t: -t.amount)[:15]
    result.unusual = _find_unusual(spend_txns + offset_txns, result.by_category)

    uncategorized = [t for t in spend_txns if t.category == Category.UNCATEGORIZED]
    result.uncategorized_total = q(sum((t.amount for t in uncategorized), ZERO))
    result.uncategorized_count = len(uncategorized)

    _add_quality_notes(result)
    return result


def _count_months(start: date | None, end: date | None) -> int:
    if not start or not end:
        return 0
    return max(1, (end.year - start.year) * 12 + (end.month - start.month) + 1)


def _income_val(t: Transaction) -> Decimal:
    return t.amount if t.direction == Direction.CREDIT else -t.amount

def _spend_val(t: Transaction) -> Decimal:
    return t.amount if t.direction == Direction.DEBIT else -t.amount

def _monthly_flows(txns: list[Transaction]) -> list[MonthlyFlow]:
    buckets: dict[str, dict[str, Decimal | int]] = defaultdict(
        lambda: {"income": ZERO, "spend": ZERO, "invested": ZERO,
                 "outflow": ZERO, "offsets": ZERO, "count": 0}
    )

    for t in txns:
        # Bucketed by accounting month, which is the calendar month of
        # txn_date except where the period engine moved it - a salary paid on
        # the last working day lands on the 31st one month and the 1st two
        # months later, double-counting one month and emptying another.
        b = buckets[t.accounting_month or _month_key(t.txn_date)]
        b["count"] = int(b["count"]) + 1

        role = t.role
        if role == FlowRole.EXPENSE:
            b["spend"] = b["spend"] + _spend_val(t)
        elif role == FlowRole.INCOME:
            b["income"] = b["income"] + _income_val(t)
        elif role in CONTRA_EXPENSE_ROLES:
            b["offsets"] = b["offsets"] + _income_val(t)
        elif role == FlowRole.INVESTMENT:
            b["invested"] = b["invested"] + _spend_val(t)

        if (t.direction == Direction.DEBIT and not t.is_mirror_leg
                and role not in {FlowRole.EXCLUDED, FlowRole.CARD_SETTLEMENT}):
            b["outflow"] = b["outflow"] + t.amount

    out = []
    for month in sorted(buckets):
        b = buckets[month]
        income = q(b["income"])
        gross = q(b["spend"])
        offsets = q(b["offsets"])
        spend = q(gross - offsets)
        net = q(income - spend)
        out.append(MonthlyFlow(
            month=month, income=income, spend=spend, gross_spend=gross,
            offsets=offsets,
            invested=q(b["invested"]), total_outflow=q(b["outflow"]), net=net,
            savings_rate=_pct(net, income),
            transaction_count=int(b["count"]),
        ))
    return out


def _monthly_by_category(txns: list[Transaction]) -> dict[str, dict[str, Decimal]]:
    """Actual per-month spend per category."""
    out: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for t in txns:
        bucket = out[t.accounting_month or _month_key(t.txn_date)]
        bucket[t.category] = bucket[t.category] + _spend_val(t)
    return {month: {c: q(v) for c, v in cats.items()} for month, cats in out.items()}


def _category_breakdown(
    txns: list[Transaction], months: int
) -> list[CategoryBreakdown]:
    total = sum((_spend_val(t) for t in txns), ZERO)
    groups = {c: g for g, cats in CATEGORY_GROUPS.items() for c in cats}
    buckets: dict[Category, list[Transaction]] = defaultdict(list)
    for t in txns:
        buckets[t.category].append(t)

    out: list[CategoryBreakdown] = []
    for category, members in buckets.items():
        subtotal = sum((_spend_val(t) for t in members), ZERO)
        largest = max(members, key=lambda t: t.amount)
        out.append(CategoryBreakdown(
            category=category,
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


def _merchant_spend(txns: list[Transaction], limit: int = 25) -> list[MerchantSpend]:
    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        key = (t.merchant or t.normalized_description or t.raw_description)[:40].strip()
        if key:
            buckets[key].append(t)

    out = []
    for merchant, members in buckets.items():
        total = sum((_spend_val(m) for m in members), ZERO)
        dates = [m.txn_date for m in members]
        out.append(MerchantSpend(
            merchant=merchant,
            total=q(total),
            count=len(members),
            average=q(total / Decimal(len(members))),
            category=members[0].category,
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
        (key, q(sum((_income_val(m) for m in members), ZERO)), len(members))
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
                      else max((t.txn_date for t in txns if not t.excluded),
                              default=salary.txn_date) + timedelta(days=1))

        in_window = [t for t in outflows + committed
                     if window_start <= t.txn_date < window_end]
        if not in_window:
            continue

        by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for t in in_window:
            by_category[t.category] = by_category[t.category] + t.amount

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
                    f"{category.replace('_', ' ')} spend of ~{median:,.0f}.",
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
