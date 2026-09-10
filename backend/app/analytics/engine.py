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
                              NEUTRAL_ROLES,
                              INCOME_CATEGORIES, LIABILITY_TYPES, LOAN_TYPES,
                              Transaction)
from ..rules import formats
from . import periods

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
    #: What `net_worth` is a position AS AT, and how it was arrived at. A
    #: balance is a fact about a moment, so a figure with no moment attached
    #: is the one number on the screen that cannot be checked.
    net_worth_as_of: date | None = None
    net_worth_basis: str = "latest"
    #: Accounts whose balance could not be established for that moment - a
    #: card statement with no running balance, an account with no rows in the
    #: window. Named, because a total silently missing an account is worse
    #: than a total that says which one it is missing.
    net_worth_missing: list[str] = field(default_factory=list)

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


#: Tokens the payee regex pulls out that are not people. UPI narrations
#: carry scheme markers in the same slot a name occupies, so "P2A" (person
#: to account) was ranked as a counterparty owed 18,000, and a stray "A" as
#: another owed 4,500. A balance sheet of who owes whom is worthless if two
#: of its rows are protocol keywords.
_NOT_A_PAYEE = {
    "P2A", "P2M", "P2P", "UPI", "VPA", "NEFT", "IMPS", "RTGS", "NA", "N/A",
    "SELF", "ATM", "POS", "CASH", "PAYMENT", "TRANSFER", "COLLECT",
}


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
            if name and name.upper() not in _NOT_A_PAYEE and len(name) > 2:
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
    period: "periods.Period | None" = None,
    bureau_accounts: list[dict[str, Any]] | None = None,
    resolved_balances: dict[str, Decimal] | None = None,
    custom_groups: dict[str, str] | None = None,
    adopted_bureau: dict[str, Decimal] | None = None,
) -> AnalysisResult:
    """Compute the full picture from a categorized, reconciled ledger.

    When `start` and/or `end` are provided, only transactions within that
    range are included.  The resulting `period_start` / `period_end` are set
    to the explicit bounds so the caller can distinguish "no data" from
    "data that happens to start later".

    `period` is the richer form of the same idea and the one every screen
    uses: a window of whole ACCOUNTING months (see analytics.periods), so
    asking for August gets the salary the period engine assigned to August
    even though it arrived on 1 September. The dates it reports back are then
    the real first and last dates of the rows that qualified - 27 Jul to
    31 Aug, say - rather than the nominal month boundaries, because those are
    the dates the figures actually cover.
    """
    result = AnalysisResult()
    accounts = accounts or {}

    if not transactions:
        # Two different empties, and they read differently to whoever is
        # looking: a ledger with nothing in it, versus a period with nothing
        # in it. Distinguished here so a caller that filtered the rows in SQL
        # gets the same answer as one that passes the whole ledger and a
        # window - which is what makes that an optimisation rather than a
        # behaviour change.
        if period is not None and not period.is_all:
            result.notes.append(f"No transactions counted in {period.label()}.")
            result.period_start, result.period_end = period.bounds()
        else:
            result.notes.append("No transactions available to analyze.")
        return result

    txns = sorted(_without_lender_ledgers(transactions, accounts),
                  key=lambda t: t.txn_date)

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

    if period is not None and not period.is_all:
        # A no-op when the caller already filtered - `period` still has to be
        # passed so the dates and the month count below describe the window.
        txns = periods.filter_transactions(txns, period)
        if not txns:
            result.notes.append(
                f"No transactions counted in {period.label()}.")
            result.period_start, result.period_end = period.bounds()
            return result
        # Measured over rows that COUNT. An excluded row is not in any
        # total, so letting it set the window's edges describes a period the
        # figures do not cover: one excluded April refund, carrying an
        # August accounting month, reported "Last month" as
        # 27 Apr - 31 Aug.
        dated = [t for t in txns if not t.excluded] or txns
        result.period_start = min(t.txn_date for t in dated)
        result.period_end = max(t.txn_date for t in dated)
    elif start or end:
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

    # Months the rows are COUNTED in, which is the divisor every per-month
    # average below wants. Not the calendar span of the dates: one accounting
    # month's rows can run across three calendar months - August's from 27
    # July to 1 September - and dividing one month of figures by three puts
    # every average on the screen out by a factor of three. It is also the
    # count the monthly table has rows for, so the header and the table agree.
    result.months_covered = len({_effective_month(t) for t in txns})
    if result.months_covered:
        divisor = Decimal(result.months_covered)
        result.average_monthly_income = q(result.total_income / divisor)
        result.average_monthly_spend = q(result.total_spend / divisor)

    result.monthly = _monthly_flows(txns)
    result.monthly_by_category = _monthly_by_category(spend_txns + offset_txns)
    result.by_category = _category_breakdown(
        spend_txns + offset_txns, result.months_covered,
        custom_groups=custom_groups)
    result.by_group = _group_totals(result.by_category)
    result.top_merchants = _merchant_spend(spend_txns + offset_txns)
    result.income_sources = _income_sources(income_txns)
    result.salary_flows = _salary_flows(txns)
    result.p2p_balances = _p2p_balances(txns)

    # The position, as at the end of the window rather than as of whenever the
    # last statement happened to be. "Assets tracked" that does not move when
    # you look at March is not answering a question about March.
    as_at, missing = (None, [])
    if period is not None and not period.is_all:
        as_at, missing = _net_worth_as_at(accounts, txns, transactions)

    if as_at is not None:
        if as_at is not None:
            owed = _unaccounted_debt(bureau_accounts, as_at)
            if owed:
                as_at["_liabilities"] = q(
                    Decimal(str(as_at.get("_liabilities") or 0)) + owed)
                as_at["_net"] = q(
                    Decimal(str(as_at.get("_assets") or 0))
                    - Decimal(str(as_at["_liabilities"])))
        result.net_worth = as_at
        result.net_worth_as_of = result.period_end
        result.net_worth_basis = "period"
        result.net_worth_missing = missing
    else:
        # Either no window, or a window in which nothing printed a balance -
        # a ledger of card statements, which mostly do not carry one. The
        # honest answer there is the latest figure there is, labelled as such.
        # Reporting zero would be a claim that the accounts are empty.
        result.net_worth = _net_worth(accounts, bureau_accounts,
                                      resolved_balances, adopted_bureau)
        result.net_worth_as_of = max(
            (a.balance_as_of for a in accounts.values() if a.balance_as_of),
            default=None)
        result.net_worth_basis = "latest"
        result.net_worth_missing = [
            a.display_name() for a in accounts.values() if a.balance is None]

    result.largest_expenses = sorted(spend_txns, key=lambda t: -t.amount)[:15]
    result.unusual = _find_unusual(spend_txns + offset_txns, result.by_category)

    uncategorized = [t for t in spend_txns if t.category == Category.UNCATEGORIZED]
    result.uncategorized_total = q(sum((t.amount for t in uncategorized), ZERO))
    result.uncategorized_count = len(uncategorized)

    _add_quality_notes(result)
    return result


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

        # Every debit that is really the holder's money leaving, once.
        #
        # `NEUTRAL_ROLES` is the list of roles that are not a flow of their
        # own money in either direction, and naming it here rather than
        # spelling out two or three of its members is what keeps this in step
        # with the rest of the app. Two roles it adds that this used to miss:
        #
        #   LENDER_LEDGER  an EMI on the LOAN's own statement, the far side
        #                  of the payment the bank account already recorded.
        #   TRANSFER_OUT   money moving between the holder's own accounts -
        #                  including a card bill. The purchases that bill
        #                  pays for are already counted from the card's
        #                  statement, so counting the payment too charged
        #                  this holder twice: the Cashflow chart showed 5.5
        #                  lakh of outflow that never left, and the forecast,
        #                  which reads this figure to size discretionary
        #                  spending, projected 3.8 lakh a month of it against
        #                  a real 2.3 - and had the holder 19 lakh overdrawn
        #                  by March on a ledger that saves 16% of income.
        if (t.direction == Direction.DEBIT and not t.is_mirror_leg
                and role not in NEUTRAL_ROLES):
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


#: Categories whose rows REDUCE spending rather than add to it. Kept beside
#: `_category_breakdown` because that is the only place the distinction
#: matters: everywhere else a refund is grouped with income quite correctly.
_CONTRA_CATEGORIES = {Category.REFUND}


def _category_breakdown(
    txns: list[Transaction], months: int,
    custom_groups: dict[str, str] | None = None,
) -> list[CategoryBreakdown]:
    total = sum((_spend_val(t) for t in txns), ZERO)
    # Built-ins first, then whatever the user chose for their own
    # categories. Without the second half every custom category fell to
    # "Other" regardless of the group it was created with, so a category
    # deliberately made an Essential never appeared as one on the chart.
    groups = {c: g for g, cats in CATEGORY_GROUPS.items() for c in cats}
    groups.update(custom_groups or {})
    buckets: dict[Category, list[Transaction]] = defaultdict(list)
    for t in txns:
        buckets[t.category].append(t)

    out: list[CategoryBreakdown] = []
    for category, members in buckets.items():
        subtotal = sum((_spend_val(t) for t in members), ZERO)
        # This is a breakdown of SPENDING, and only spending and its offsets
        # reach it - `analyze` passes `spend_txns + offset_txns`, never an
        # income row. So `CATEGORY_GROUPS["Income"]` can be populated here by
        # exactly one thing: a refund, whose `_spend_val` is negative because
        # it nets against the purchase it reverses.
        #
        # The map is right for what it is mostly used for - filtering the
        # ledger, where "Income" genuinely means salary and interest - but
        # read as a spending group it says a 50,000 credit-balance refund was
        # 50,000 of "Income", and then `_group_totals` renders the group at
        # MINUS 50,000. Both halves are wrong: it is not income, and a
        # negative bar in a spending chart has nothing to be a share of.
        #
        # Grouped by what the row does to the total instead of by the label
        # its category carries elsewhere. Money that comes back is its own
        # group, and it is the only group whose total is meant to be negative.
        group = ("Money back" if category in _CONTRA_CATEGORIES
                 else groups.get(category, "Other"))
        largest = max(members, key=lambda t: t.amount)
        out.append(CategoryBreakdown(
            category=category,
            group=group,
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
    #: A description the parser could not turn into a name. Grouped under one
    #: honest label instead of being ranked as though it were a merchant: the
    #: HDFC card extractor drops the payee on some rows and leaves "+ C", and
    #: that string was appearing as this holder's second-largest merchant at
    #: 1,31,981. It is not a merchant; it is a parse failure, and saying so
    #: is the only way it gets fixed.
    UNREADABLE = "(no merchant on the statement)"

    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        # `merchant` first: it is the normalised identity, so the same payee
        # lands in one bucket even when the reference digits differ row to
        # row. Falling straight to the raw description is what split one
        # home-loan EMI across five "merchants".
        key = (t.merchant or "").strip()
        if not key:
            key = (t.normalized_description or "").strip()
            if not any(ch.isalpha() for ch in key):
                key = UNREADABLE
        buckets[key[:40].strip() or UNREADABLE].append(t)

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
            # The accounting month, not the calendar month the credit landed
            # in. A salary paid on the last working day lands on the 31st one
            # month and the 1st two months later, which is the exact case
            # `assign_accounting_months` exists to absorb - and reading the
            # calendar month here bypassed it. On this ledger the eight
            # salaries dated Feb 1 ... Aug 31 came back labelled
            # 02,03,04,05,05,06,08,08: May and August each appeared twice,
            # July and September not at all. Two rows sharing a month key is
            # not a cosmetic duplicate either; it is the key the Months
            # screen joins on.
            month=salary.accounting_month or _month_key(salary.txn_date),
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

#: Bureau match states that mean a ledger account already covers the line.
#: Mirrors `analytics.position._ATTRIBUTED`, and for the same reason: a line
#: the matcher can plausibly attribute is not a second debt.
_BUREAU_ATTRIBUTED = frozenset({"auto", "confirmed", "suggested"})


def _unaccounted_debt(bureau_accounts, detail: dict,
                      adopted: dict[str, Decimal] | None = None) -> Decimal:
    """Debt a lender reports that no tracked account covers.

    Net worth read the ledger alone, and the ledger only knows about accounts
    whose statements have been imported. This holder's largest liability by
    far - a 66.7 lakh home loan - has never had a statement imported at all;
    it is known only because a lender reported it to a bureau. Left out, the
    Overview said 14.2 lakh to the good while the Position tab, which does
    count it, said 68.4 lakh owed. Two screens, one question, a 67 lakh gap.

    A missing figure is not a zero anywhere else in this app, and it is not
    one here. Each such debt is named in the breakdown alongside the tracked
    accounts, marked as the bureau's word rather than a statement's, because
    it has not been through the reconciliation gate and nothing here should
    imply it has.
    """
    total = ZERO
    for line in bureau_accounts or []:
        if (line.get("status") or "open") != "open":
            continue
        # Adopted onto the balance sheet, so the Position tab already counts
        # it. Counting it here as well is the same card twice.
        if adopted and line.get("id") in adopted:
            continue
        if line.get("account_id") or \
                (line.get("match_status") or "") in _BUREAU_ATTRIBUTED:
            continue
        try:
            owed = Decimal(str(line.get("current_balance") or 0))
        except Exception:
            continue
        if owed <= 0:
            continue
        lender = str(line.get("lender") or "a lender").title()
        kind = str(line.get("account_type") or "account").replace("_", " ")
        detail[f"{lender} {kind} (reported by the bureau)"] = q(-owed)
        total += owed
    return total


def mark_lender_ledgers(
    transactions: list[Transaction],
    accounts: dict[str, Account],
) -> int:
    """Stamp every row that belongs to a LENDER rather than to the holder.

    Public, and called from both places that assemble a ledger - the import
    and the rebuild-from-database - because a fact applied by only one of
    them is a fact the app disagrees with itself about. It did: the import
    knew these rows were the lender's and the rebuild did not, so correcting
    a single transaction anywhere in the app silently put four years of a
    loan account's internal history back into this month's spending.

    Returns how many rows were stamped, so a caller can say so.
    """
    lender_ledgers = {
        account_id for account_id, account in (accounts or {}).items()
        if getattr(account, "account_type", None) in LOAN_TYPES
    }
    if not lender_ledgers:
        return 0
    marked = 0
    for txn in transactions:
        if txn.account_id in lender_ledgers:
            txn.flow_role = FlowRole.LENDER_LEDGER.value
            marked += 1
    return marked


def _without_lender_ledgers(
    transactions: list[Transaction],
    accounts: dict[str, Account],
) -> list[Transaction]:
    """Drop rows that belong to a LOAN account rather than to the holder.

    A loan account statement is the lender's ledger, not the borrower's cash
    flow. Its rows are the disbursement arriving, each EMI being received and
    interest being charged - and every one of those is the far side of a
    movement the funding account has already recorded. The EMI leaving the
    savings account is the holder's outflow; the same EMI arriving at the
    lender is not a second one.

    This is the same double-count the transfer matcher prevents between a
    card and the account that pays its bill, but the matcher cannot see it:
    a loan statement's rows have no counterpart to pair with unless both
    sides happen to have been imported, and the disbursement has no
    counterpart at all - it would have read as 75 lakh of income.

    A loan account is still read, still projected and still counted as debt.
    What it does not do is contribute to income or spending.
    """
    if not accounts:
        return list(transactions)
    lender_ledgers = {
        account_id for account_id, account in accounts.items()
        if getattr(account, "account_type", None) in LOAN_TYPES
    }
    if not lender_ledgers:
        return list(transactions)
    return [t for t in transactions if t.account_id not in lender_ledgers]


def _net_worth(accounts: dict[str, Account],
               bureau_accounts=None,
               resolved: dict[str, Decimal] | None = None,
               adopted_bureau: dict[str, Decimal] | None = None,
               ) -> dict[str, Decimal]:
    """Assets, liabilities and the net of them.

    `resolved` is the Position tab's answer for the accounts it covers, and
    it WINS where it exists. `Account.balance` reads the attested columns
    straight off the row; Position is the layer that ages a loan forward
    through its amortization, prefers holdings over a stale summary column,
    and sets aside a broker statement a CAS already contains. Reading the raw
    columns here is what had this screen and the Position tab quoting net
    worth 3.49 lakh apart.
    """
    assets, liabilities = ZERO, ZERO
    detail: dict[str, Decimal] = {}

    for account in accounts.values():
        signed = account.balance
        if resolved and account.id in resolved:
            signed = resolved[account.id]
        if signed is None:
            continue
        label = account.display_name()
        if signed < 0:
            liabilities += -signed
        else:
            assets += signed
        detail[label] = q(signed)

    # Bureau-only debt the Position tab has adopted. It has no ledger
    # account, so `resolved` cannot carry it and the sweep below skips it as
    # already-counted - without this it falls between the two and vanishes.
    for line in (bureau_accounts or []):
        signed = (adopted_bureau or {}).get(line.get("id"))
        if signed is None:
            continue
        lender = str(line.get("lender") or "a lender").title()
        kind = str(line.get("account_type") or "account").replace("_", " ")
        detail[f"{lender} {kind} (reported by the bureau)"] = q(signed)
        if signed < 0:
            liabilities += -signed
        else:
            assets += signed

    liabilities += _unaccounted_debt(bureau_accounts, detail, adopted_bureau)

    detail["_assets"] = q(assets)
    detail["_liabilities"] = q(liabilities)
    detail["_net"] = q(assets - liabilities)
    return detail


def _net_worth_as_at(
    accounts: dict[str, Account],
    in_window: list[Transaction],
    everything: list[Transaction],
) -> tuple[dict[str, Decimal] | None, list[str]]:
    """The position at the end of a window, from running balances.

    A statement prints a balance after every row, so the balance at any moment
    is the one printed on the last row up to it. That is read here rather than
    recomputed by summing movements: the printed figure is the bank's own, it
    has already been through the reconciliation gate, and a sum of movements
    would silently absorb any row the parse missed.

    Rows OUTSIDE the window are still consulted, deliberately. A window with
    no activity on an account does not mean that account was empty in it - the
    balance simply had not changed since the last row before it. Only an
    account with no priced row at all is unknown, and those are named rather
    than counted as zero.

    Returns `None` when NOT ONE account could be established, which is the
    normal case for a ledger of credit-card statements: no running balance is
    printed on them at all. The caller falls back to the latest known figure
    and says so, because a zero here would read as "you have nothing".
    """
    latest_in_window = max((t.txn_date for t in in_window), default=None)
    assets, liabilities = ZERO, ZERO
    detail: dict[str, Decimal] = {}
    missing: list[str] = []

    for account_id, account in accounts.items():
        label = account.display_name()
        priced = [
            t for t in everything
            if t.account_id == account_id and t.balance_after is not None
            and (latest_in_window is None or t.txn_date <= latest_in_window)
        ]
        if priced:
            # Last by date, then by source row - two rows can share a date and
            # only their order on the statement says which came second.
            last = max(priced, key=lambda t: (t.txn_date, t.source_row or 0))
            signed = -abs(last.balance_after) if account.is_liability \
                else last.balance_after
        elif account.balance is not None and not any(
                t.account_id == account_id for t in everything):
            # Nothing was ever parsed for it - a holdings or loan account
            # whose balance came from the statement header rather than from
            # rows. That figure is as good here as anywhere.
            signed = account.balance
        else:
            missing.append(label)
            continue

        if signed < 0:
            liabilities += -signed
        else:
            assets += signed
        detail[label] = q(signed)

    if not detail:
        return None, missing

    detail["_assets"] = q(assets)
    detail["_liabilities"] = q(liabilities)
    detail["_net"] = q(assets - liabilities)
    return detail, missing


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
            if float(t.amount) <= threshold:
                continue
            # A charge the app itself predicted is not an anomaly. Every one
            # of six identical monthly home-loan EMIs was being flagged as
            # "well above the typical emi spend of ~650" - the 650 being a
            # small insurance instalment that happened to share the
            # category. The most predictable payment in the ledger was the
            # loudest thing on the screen, six times over, which is how a
            # detector trains its reader to ignore it.
            if t.recurring_series_id:
                continue
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
