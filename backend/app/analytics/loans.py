"""Loan mathematics: amortization, payoff dates, prepayment impact.

Every number here comes from a closed-form formula, never from a model. A loan's
future is fully determined by principal, rate and EMI - there is nothing to
predict, only to calculate. Asking an LLM for a payoff date would be inventing
uncertainty where none exists.

Where a statement doesn't state the interest rate, we recover it from the
observed interest charges instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from ..models.schemas import Account, Category, Direction, LOAN_TYPES, Transaction

CENT = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class AmortizationRow:
    month: int
    when: date
    opening: Decimal
    emi: Decimal
    interest: Decimal
    principal: Decimal
    closing: Decimal


@dataclass
class LoanProjection:
    account_id: str
    label: str
    outstanding: Decimal
    annual_rate: Decimal
    emi: Decimal
    months_remaining: int
    payoff_date: date | None
    total_interest_remaining: Decimal
    total_payable_remaining: Decimal
    #: Share of the next EMI that is pure interest cost, not debt reduction.
    next_interest_share: float
    schedule: list[AmortizationRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def infer_rate_from_transactions(
    txns: list[Transaction],
    outstanding: Decimal,
) -> Decimal | None:
    """Recover an annual rate from actual interest charges on the statement.

    interest_for_month = balance * annual_rate / 12, so the rate falls out of a
    single month's charge. Uses the most recent interest row, since a floating
    rate makes older rows stale.
    """
    if not outstanding or outstanding <= 0:
        return None

    interest_rows = [
        t for t in txns
        if t.category == Category.LOAN_INTEREST and t.direction == Direction.DEBIT
    ]
    if not interest_rows:
        return None

    latest = max(interest_rows, key=lambda t: t.txn_date)
    monthly_rate = latest.amount / outstanding
    annual = monthly_rate * 12 * 100
    # Sanity-bound it: a recovered rate outside this range means the balance we
    # divided by was wrong, and a wrong rate is worse than no rate.
    if annual <= 0 or annual > 60:
        return None
    return annual.quantize(Decimal("0.01"))


def months_to_payoff(
    outstanding: Decimal,
    annual_rate: Decimal,
    emi: Decimal,
) -> int | None:
    """Number of EMIs left, by iteration rather than logarithms.

    Iterating is a few microseconds and sidesteps the float conversion that a
    log-based formula would force on Decimal money.
    """
    if outstanding <= 0:
        return 0
    if emi <= 0:
        return None

    monthly_rate = annual_rate / Decimal("1200")
    first_interest = _q(outstanding * monthly_rate)
    if emi <= first_interest:
        # The EMI does not even cover the interest: the balance grows forever.
        return None

    balance = outstanding
    months = 0
    while balance > 0 and months < 1200:  # 100 years is the practical ceiling
        interest = _q(balance * monthly_rate)
        principal = emi - interest
        balance = _q(balance - principal)
        months += 1
    return months


def build_schedule(
    outstanding: Decimal,
    annual_rate: Decimal,
    emi: Decimal,
    start: date,
    max_rows: int = 480,
) -> list[AmortizationRow]:
    """Full month-by-month amortization until the loan closes."""
    rows: list[AmortizationRow] = []
    if outstanding <= 0 or emi <= 0:
        return rows

    monthly_rate = annual_rate / Decimal("1200")
    balance = outstanding
    when = start

    for month in range(1, max_rows + 1):
        interest = _q(balance * monthly_rate)
        principal = emi - interest
        if principal <= 0:
            break
        if principal > balance:
            # Final instalment is only what's left plus its interest.
            principal = balance
        closing = _q(balance - principal)
        rows.append(AmortizationRow(
            month=month, when=when, opening=balance,
            emi=_q(principal + interest), interest=interest,
            principal=_q(principal), closing=closing,
        ))
        balance = closing
        when = _add_month(when)
        if balance <= 0:
            break
    return rows


def _add_month(d: date) -> date:
    year, month = divmod(d.month, 12)
    return date(d.year + year, month + 1, min(d.day, 28))


def project_loan(
    account: Account,
    transactions: list[Transaction],
    as_of: date | None = None,
) -> LoanProjection | None:
    """Build a full projection for one loan account."""
    if account.account_type not in LOAN_TYPES:
        return None

    warnings: list[str] = []
    as_of = as_of or max((t.txn_date for t in transactions), default=date.today())

    outstanding = account.principal_outstanding
    if outstanding is None:
        # Fall back to the last balance we saw on the statement.
        with_balance = [t for t in transactions if t.balance_after is not None]
        if with_balance:
            outstanding = max(with_balance, key=lambda t: t.txn_date).balance_after
            warnings.append(
                "Outstanding principal was not stated; taken from the last "
                "running balance on the statement."
            )
    if outstanding is None or outstanding <= 0:
        return None

    emi = account.emi_amount
    if emi is None:
        emi_rows = [t for t in transactions if t.category == Category.EMI]
        if emi_rows:
            emi = max(emi_rows, key=lambda t: t.txn_date).amount
            warnings.append("EMI amount inferred from the most recent EMI transaction.")
    if emi is None or emi <= 0:
        return None

    rate = account.interest_rate or infer_rate_from_transactions(transactions, outstanding)
    if rate is None:
        return None
    if account.interest_rate is None:
        warnings.append(
            f"Interest rate was not stated; recovered {rate}% p.a. from the "
            f"interest charged on the statement."
        )

    months = months_to_payoff(outstanding, rate, emi)
    if months is None:
        warnings.append(
            "The EMI does not cover the monthly interest, so this balance will "
            "grow rather than reduce. Check the figures against the lender."
        )
        return LoanProjection(
            account_id=account.id or "", label=account.display_name(),
            outstanding=outstanding, annual_rate=rate, emi=emi,
            months_remaining=0, payoff_date=None,
            total_interest_remaining=Decimal("0"),
            total_payable_remaining=Decimal("0"),
            next_interest_share=1.0, warnings=warnings,
        )

    schedule = build_schedule(outstanding, rate, emi, _add_month(as_of))
    total_interest = sum((r.interest for r in schedule), Decimal("0"))
    total_payable = sum((r.emi for r in schedule), Decimal("0"))
    next_interest = schedule[0].interest if schedule else Decimal("0")

    return LoanProjection(
        account_id=account.id or "",
        label=account.display_name(),
        outstanding=outstanding,
        annual_rate=rate,
        emi=emi,
        months_remaining=months,
        payoff_date=schedule[-1].when if schedule else None,
        total_interest_remaining=_q(total_interest),
        total_payable_remaining=_q(total_payable),
        next_interest_share=float(next_interest / emi) if emi else 0.0,
        schedule=schedule,
        warnings=warnings,
    )


def prepayment_impact(
    projection: LoanProjection,
    lump_sum: Decimal,
) -> dict[str, object]:
    """What a one-off prepayment today would save.

    Presented as months and rupees saved rather than as a recommendation - the
    trade-off against investing the same money is the user's call, and depends
    on facts this app cannot see.
    """
    if lump_sum <= 0 or projection.outstanding <= 0:
        return {}

    new_balance = projection.outstanding - lump_sum
    if new_balance <= 0:
        return {
            "lump_sum": lump_sum,
            "closes_loan": True,
            "months_saved": projection.months_remaining,
            "interest_saved": projection.total_interest_remaining,
        }

    new_schedule = build_schedule(
        new_balance, projection.annual_rate, projection.emi, date.today()
    )
    new_interest = sum((r.interest for r in new_schedule), Decimal("0"))

    return {
        "lump_sum": lump_sum,
        "closes_loan": False,
        "months_saved": projection.months_remaining - len(new_schedule),
        "interest_saved": _q(projection.total_interest_remaining - new_interest),
        "new_months_remaining": len(new_schedule),
        "new_payoff_date": new_schedule[-1].when if new_schedule else None,
    }
