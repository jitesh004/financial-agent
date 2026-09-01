"""Cashflow forecasting.

The forecast is built from two parts with very different certainty:

  COMMITTED - recurring series already detected (salary in, EMI/rent/subs out).
              These are near-certain: the EMI will leave on the 5th.
  DISCRETIONARY - everything else, modelled as a monthly distribution from
              historical variance.

Presenting one number would imply a precision that doesn't exist, so every
month carries a low/expected/high band derived from actual observed variance.
No model is asked to guess a figure; this is arithmetic over the user's own
history.

Explicit limits: this projects the user's *existing* patterns forward. It knows
nothing about a job change, a bonus, a medical event, or inflation, and says so.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from ..models.schemas import (Category, Direction, INCOME_CATEGORIES,
                              Transaction)
from .engine import MonthlyFlow, q
from .recurring import RecurringSeries

ZERO = Decimal("0")

#: The narrowest a low/high band may be, as a share of the median month.
#:
#: Four near-identical months would otherwise produce a band of almost nothing,
#: which reads as a promise. Next month is not knowable to the rupee, and a
#: forecast that implies it is has told a more damaging lie than one that says
#: "somewhere in this range".
MIN_BAND_SHARE = Decimal("0.15")

#: What makes a forecast trustworthy, in the two dimensions that matter:
#: how much history there is, and how steady the spending in it was.
#: `volatility` is the standard deviation of monthly spend over its mean.
HIGH_CONFIDENCE_MONTHS = 6
HIGH_CONFIDENCE_SERIES = 3
HIGH_CONFIDENCE_VOLATILITY = 0.25
MEDIUM_CONFIDENCE_MONTHS = 3
MEDIUM_CONFIDENCE_VOLATILITY = 0.45

#: A recurring series has to be at least this certain before the forecast
#: treats it as committed money rather than as a pattern it noticed.
COMMITTED_SERIES_CONFIDENCE = 0.6


@dataclass
class ForecastMonth:
    month: str
    committed_income: Decimal
    committed_outflow: Decimal
    discretionary_expected: Decimal
    discretionary_low: Decimal
    discretionary_high: Decimal

    net_expected: Decimal
    net_low: Decimal
    net_high: Decimal

    closing_balance_expected: Decimal
    closing_balance_low: Decimal
    closing_balance_high: Decimal


@dataclass
class ForecastResult:
    months: list[ForecastMonth] = field(default_factory=list)
    opening_balance: Decimal = ZERO
    #: Committed monthly outflow as a share of committed monthly income.
    commitment_ratio: float = 0.0
    #: Months of committed outflow the current balance would cover with no income.
    runway_months: float | None = None
    #: First month the projected balance goes negative, if any.
    first_shortfall_month: str | None = None
    confidence: str = "low"
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def forecast(
    monthly: list[MonthlyFlow],
    series: list[RecurringSeries],
    opening_balance: Decimal,
    horizon_months: int = 6,
    as_of: date | None = None,
) -> ForecastResult:
    """Project cashflow forward from history plus committed recurring items."""
    result = ForecastResult(opening_balance=q(opening_balance))

    if len(monthly) < 2:
        result.warnings.append(
            "At least two complete months of history are needed to forecast. "
            "Upload a longer statement period."
        )
        return result

    # The most recent month is usually partial, which would drag every average
    # down and make the forecast quietly optimistic about spending.
    history = monthly[:-1] if len(monthly) > 2 else monthly

    committed_income, committed_outflow = _committed_flows(series)
    discretionary = _discretionary_history(history, series)

    expected, low, high = _distribution(discretionary)

    result.commitment_ratio = (
        round(float(committed_outflow / committed_income), 3)
        if committed_income else 0.0
    )
    if committed_outflow > 0:
        result.runway_months = round(float(opening_balance / committed_outflow), 1)

    result.confidence = _confidence(history, series)
    result.assumptions = _assumptions(history, series, committed_income, committed_outflow)

    as_of = as_of or date.today()
    year, month = as_of.year, as_of.month
    balance_e = balance_l = balance_h = Decimal(opening_balance)

    for _ in range(horizon_months):
        year, month = _next_month(year, month)
        key = f"{year:04d}-{month:02d}"

        net_e = committed_income - committed_outflow - expected
        net_l = committed_income - committed_outflow - high   # high spend = worst case
        net_h = committed_income - committed_outflow - low

        balance_e = q(balance_e + net_e)
        balance_l = q(balance_l + net_l)
        balance_h = q(balance_h + net_h)

        if result.first_shortfall_month is None and balance_e < 0:
            result.first_shortfall_month = key

        result.months.append(ForecastMonth(
            month=key,
            committed_income=q(committed_income),
            committed_outflow=q(committed_outflow),
            discretionary_expected=q(expected),
            discretionary_low=q(low),
            discretionary_high=q(high),
            net_expected=q(net_e), net_low=q(net_l), net_high=q(net_h),
            closing_balance_expected=balance_e,
            closing_balance_low=balance_l,
            closing_balance_high=balance_h,
        ))

    return result


def _committed_flows(series: list[RecurringSeries]) -> tuple[Decimal, Decimal]:
    """Monthly-equivalent totals of active recurring income and outflow.

    Two exclusions matter:

    - CC_PAYMENT is skipped. The card bill is a real cash outflow, but the
      purchases it settles are already counted individually in the spending
      history. Counting both makes committed outflow exceed committed income
      for anyone who puts daily spending on a card - which then drives
      discretionary spend to zero and produces a nonsense projection.
    - Income series are restricted to genuine income categories, so a refund
      or a mis-paired credit cannot inflate expected salary.
    """
    income = ZERO
    outflow = ZERO
    for s in series:
        if not s.is_active or s.confidence < 0.5:
            continue
        if s.direction == Direction.CREDIT:
            if s.category in INCOME_CATEGORIES:
                income += s.monthly_equivalent
        elif s.category != Category.CC_PAYMENT:
            outflow += s.monthly_equivalent
    return q(income), q(outflow)


def _discretionary_history(
    history: list[MonthlyFlow],
    series: list[RecurringSeries],
) -> list[Decimal]:
    """Per-month spend with committed recurring outflow removed.

    What's left is the variable part - the only part worth modelling as a
    distribution, since the committed part doesn't vary.
    """
    _, committed_outflow = _committed_flows(series)
    out = []
    for m in history:
        # total_outflow, not spend: spend excludes EMIs and SIPs, while
        # committed_outflow includes them, so subtracting one from the other
        # would compare two different things and drive the result negative.
        remainder = m.total_outflow - committed_outflow
        out.append(remainder if remainder > 0 else ZERO)
    return out


def _distribution(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    """Expected / low / high from observed history.

    Median rather than mean, because one holiday month should not become the
    permanent expectation. The band is the observed range, clamped to at least
    +/-15% so a coincidentally stable stretch doesn't imply false precision.
    """
    if not values:
        return ZERO, ZERO, ZERO

    floats = [float(v) for v in values]
    median = Decimal(str(statistics.median(floats)))

    low = min(values)
    high = max(values)

    min_band = median * MIN_BAND_SHARE
    if median - low < min_band:
        low = median - min_band
    if high - median < min_band:
        high = median + min_band

    return (q(median), q(max(low, ZERO)), q(high))


def _confidence(history: list[MonthlyFlow], series: list[RecurringSeries]) -> str:
    """How much the forecast should be trusted, in plain words."""
    months = len(history)
    active = [s for s in series
              if s.is_active and s.confidence >= COMMITTED_SERIES_CONFIDENCE]

    spends = [float(m.spend) for m in history if m.spend > 0]
    volatility = 0.0
    if len(spends) >= 2:
        mean = statistics.mean(spends)
        volatility = statistics.pstdev(spends) / mean if mean else 0.0

    if (months >= HIGH_CONFIDENCE_MONTHS
            and len(active) >= HIGH_CONFIDENCE_SERIES
            and volatility < HIGH_CONFIDENCE_VOLATILITY):
        return "high"
    if (months >= MEDIUM_CONFIDENCE_MONTHS
            and volatility < MEDIUM_CONFIDENCE_VOLATILITY):
        return "medium"
    return "low"


def _assumptions(
    history: list[MonthlyFlow],
    series: list[RecurringSeries],
    committed_income: Decimal,
    committed_outflow: Decimal,
) -> list[str]:
    """State plainly what the projection does and does not account for."""
    active = [s for s in series if s.is_active and s.confidence >= 0.5]
    return [
        f"Based on {len(history)} complete month(s) of history.",
        f"Assumes {len(active)} recurring item(s) continue unchanged: "
        f"{committed_income:,.0f} in and {committed_outflow:,.0f} out per month.",
        "Discretionary spending is projected from your own observed range, not "
        "from a target or a budget.",
        "Does not account for inflation, salary changes, job changes, tax events, "
        "one-off purchases, or market movement in investments.",
        "Loan balances follow their contractual amortization; the projection "
        "assumes no prepayment and no rate change.",
    ]


def savings_goal_projection(
    monthly_surplus: Decimal,
    target_amount: Decimal,
    current_saved: Decimal = ZERO,
) -> dict[str, object]:
    """Months to reach a savings target at the current surplus rate.

    Pure arithmetic, deliberately ignoring investment returns: modelling a
    return would require assuming a rate, and a projection that quietly assumes
    12% annual growth is how these tools mislead people.
    """
    remaining = target_amount - current_saved
    if remaining <= 0:
        return {"already_reached": True, "months": 0}
    if monthly_surplus <= 0:
        return {
            "already_reached": False,
            "months": None,
            "note": "At the current rate nothing is being set aside, so this "
                    "target is not reachable without changing income or spending.",
        }

    months = int((remaining / monthly_surplus).to_integral_value(rounding=ROUND_HALF_UP))
    return {
        "already_reached": False,
        "months": months,
        "target_date": date.today() + timedelta(days=months * 30),
        "monthly_surplus": q(monthly_surplus),
        "note": "Assumes the current surplus continues and excludes any "
                "investment growth.",
    }
