"""Detect recurring transactions: salary, EMIs, rent, subscriptions.

Recurring series are the backbone of any honest forecast. Most of a person's
future cashflow is already determined - the salary arrives, the EMI leaves, the
rent leaves - and the discretionary remainder is what actually varies. Finding
the fixed skeleton first means the forecast only has to model the small,
genuinely uncertain part.

Detection is statistical, not model-based: group by merchant signature, then
test whether the dates form a regular cadence and the amounts cluster. This is
fully deterministic and re-runs to the same answer every time.
"""

from __future__ import annotations

import re
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..models.schemas import Category, Direction, Transaction

#: Cadences we recognise, in days, with a tolerance for weekends/holidays.
CADENCES: list[tuple[str, int, int]] = [
    ("weekly", 7, 2),
    ("fortnightly", 14, 3),
    ("monthly", 30, 6),
    ("bi-monthly", 61, 8),
    ("quarterly", 91, 12),
    ("half-yearly", 182, 20),
    ("yearly", 365, 30),
]

MIN_OCCURRENCES = 3
MIN_CONFIDENCE = 0.25
#: How much the amount may vary and still count as "the same" recurring charge.
#: Utilities drift a lot month to month; a subscription barely moves.
AMOUNT_VARIANCE_TOLERANCE = 0.35

_SIGNATURE_NOISE = re.compile(r"\b\d+\b|\b[A-Z]{2}\d+\b")


@dataclass
class RecurringSeries:
    id: str
    account_id: str | None
    label: str
    category: Category
    direction: Direction
    median_amount: Decimal
    cadence_days: int
    cadence_name: str
    occurrences: int
    first_seen: date | None
    last_seen: date | None
    next_expected: date | None
    is_active: bool
    confidence: float
    amount_variance: float = 0.0
    transaction_ids: list[str] = field(default_factory=list)

    @property
    def monthly_equivalent(self) -> Decimal:
        """Normalize any cadence to a monthly figure for budgeting."""
        if self.cadence_days <= 0:
            return Decimal("0")
        return (self.median_amount * Decimal("30.44") / Decimal(self.cadence_days)
                ).quantize(Decimal("0.01"))


def _signature(txn: Transaction) -> str:
    """A stable key for 'the same charge, recurring'.

    Reference numbers and dates change every month, so they are stripped. What
    remains - the merchant words - is what makes NETFLIX in January the same
    series as NETFLIX in February.
    """
    base = txn.normalized_description or txn.raw_description or ""
    base = _SIGNATURE_NOISE.sub("", base.upper())
    words = [w for w in re.split(r"[^A-Z]+", base) if len(w) > 2]
    return " ".join(words[:4])


def _classify_cadence(gaps: list[int]) -> tuple[str, int, float] | None:
    """Match observed date gaps to a known cadence.

    Uses the median gap so one missed month (a failed autopay, a holiday) does
    not disqualify an otherwise obvious series.
    """
    if not gaps:
        return None
    median_gap = statistics.median(gaps)

    for name, days, tolerance in CADENCES:
        if abs(median_gap - days) <= tolerance:
            # How tightly the individual gaps cluster around the cadence.
            within = sum(1 for g in gaps if abs(g - days) <= tolerance * 2)
            confidence = within / len(gaps)
            return name, days, confidence
    return None


def detect_recurring(transactions: list[Transaction]) -> list[RecurringSeries]:
    """Group transactions into recurring series."""
    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        # Mirror legs would otherwise produce a phantom second series for every
        # EMI and card payment - and, worse, an income series for money that is
        # really the user paying their own card.
        if txn.is_mirror_leg:
            continue
        sig = _signature(txn)
        if not sig:
            continue
        groups[(txn.account_id, txn.direction, sig)].append(txn)

    series: list[RecurringSeries] = []
    today = max((t.txn_date for t in transactions), default=date.today())

    for (account_id, direction, sig), members in groups.items():
        if len(members) < MIN_OCCURRENCES:
            continue

        members.sort(key=lambda t: t.txn_date)
        dates = [t.txn_date for t in members]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]

        cadence = _classify_cadence(gaps)
        if cadence is None:
            continue
        cadence_name, cadence_days, cadence_confidence = cadence

        amounts = [t.amount for t in members]
        median_amount = Decimal(str(statistics.median(amounts)))
        variance = _relative_variance(amounts, median_amount)
        if variance > AMOUNT_VARIANCE_TOLERANCE:
            continue  # same payee, wildly different amounts - not a fixed charge

        last_seen = dates[-1]
        next_expected = last_seen + timedelta(days=cadence_days)
        # Active if we would not yet have expected another one to be missing.
        is_active = (today - last_seen).days <= cadence_days * 1.6

        # Amount stability and cadence regularity both feed confidence; a series
        # is only as trustworthy as its least regular dimension.
        confidence = round(
            min(1.0, cadence_confidence * (1 - variance) * min(1.0, len(members) / 6)), 3
        )

        series.append(RecurringSeries(
            id=str(uuid.uuid4()),
            account_id=account_id,
            label=_label_for(members, sig),
            category=_dominant_category(members),
            direction=direction,
            median_amount=median_amount,
            cadence_days=cadence_days,
            cadence_name=cadence_name,
            occurrences=len(members),
            first_seen=dates[0],
            last_seen=last_seen,
            next_expected=next_expected,
            is_active=is_active,
            confidence=confidence,
            amount_variance=round(variance, 3),
            transaction_ids=[t.id for t in members if t.id],
        ))

    # Stamp the series id back onto its members so the UI can link them.
    by_id = {s.id: s for s in series}
    for s in series:
        for txn in transactions:
            if txn.id in s.transaction_ids:
                txn.recurring_series_id = s.id

    # Below this, "recurring" is coincidence - a few fuel stops that happened
    # to fall a month apart. Surfacing those as commitments makes the whole
    # list untrustworthy.
    series = [s for s in series if s.confidence >= MIN_CONFIDENCE]
    series.sort(key=lambda s: -s.monthly_equivalent)
    return series


def _relative_variance(amounts: list[Decimal], median: Decimal) -> float:
    if median == 0:
        return 1.0
    deviations = [abs(float(a - median) / float(median)) for a in amounts]
    return sum(deviations) / len(deviations)


def _label_for(members: list[Transaction], fallback: str) -> str:
    """Human-readable name, preferring the shortest real description.

    Shortest tends to be the cleanest - longer variants carry extra reference
    junk that survived normalization.
    """
    descriptions = [t.raw_description for t in members if t.raw_description]
    if not descriptions:
        return fallback.title()
    shortest = min(descriptions, key=len)
    return shortest[:70].strip()


def _dominant_category(members: list[Transaction]) -> Category:
    counts: dict[Category, int] = defaultdict(int)
    for t in members:
        counts[t.category] += 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def upcoming(series: list[RecurringSeries], horizon_days: int = 30,
             as_of: date | None = None) -> list[tuple[date, RecurringSeries]]:
    """Project the next occurrence of every active series within a horizon."""
    today = as_of or date.today()
    horizon = today + timedelta(days=horizon_days)
    out: list[tuple[date, RecurringSeries]] = []

    for s in series:
        if not s.is_active or not s.next_expected:
            continue
        when = s.next_expected
        # A series whose next date has already slipped past gets rolled forward.
        while when < today:
            when += timedelta(days=s.cadence_days)
        while when <= horizon:
            out.append((when, s))
            when += timedelta(days=s.cadence_days)

    out.sort(key=lambda pair: pair[0])
    return out
