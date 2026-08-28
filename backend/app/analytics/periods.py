from __future__ import annotations

import logging
from datetime import date

from ..models.schemas import Transaction
from .recurring import RecurringSeries

logger = logging.getLogger(__name__)


def _circular_distance(a: int, b: int, period: int = 31) -> int:
    """Distance between two days on a circle."""
    diff = abs(a - b)
    return min(diff, period - diff)


def circular_median_day(dates: list[date]) -> int:
    """Median day-of-month treating days as points on a circle.
    
    A plain median of [31, 1, 30, 2] gives ~16 — wrong. These cluster
    around the month boundary. The circular median finds the angular
    center of mass.
    
    Method: for each candidate day d (1-31), compute the sum of
    circular distances from d to every observed day. The candidate
    with the minimum total distance is the circular median.
    """
    if not dates:
        return 1

    days = [d.day for d in dates]
    min_dist = float("inf")
    best_day = 1

    for candidate in range(1, 32):
        dist = sum(_circular_distance(candidate, day) for day in days)
        if dist < min_dist:
            min_dist = dist
            best_day = candidate

    return best_day


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Shift a (year, month) by delta months. delta can be -1 or +1."""
    m = month + delta
    y = year
    if m < 1:
        m = 12
        y -= 1
    elif m > 12:
        m = 1
        y += 1
    return y, m


def assign_accounting_months(
    transactions: list[Transaction],
    recurring_series: list[RecurringSeries],
) -> None:
    """Set accounting_month on every transaction.
    
    Default: calendar month of txn_date.
    For members of a monthly recurring series: shifted using salary-drift logic.
    One-offs are NEVER moved.
    """
    # 1. Default pass
    for txn in transactions:
        txn.accounting_month = f"{txn.txn_date.year:04d}-{txn.txn_date.month:02d}"

    # Index transactions for quick lookup by ID
    txn_by_id = {t.id: t for t in transactions if t.id}

    # 2. Drift correction
    for series in recurring_series:
        if series.cadence_name != "monthly" or series.occurrences < 3:
            continue

        members = [txn_by_id[tid] for tid in series.transaction_ids if tid in txn_by_id]
        if not members:
            continue

        anchor = circular_median_day([m.txn_date for m in members])
        allocated: dict[str, list[tuple[int, Transaction]]] = {}

        for txn in members:
            y, m = txn.txn_date.year, txn.txn_date.month
            day = txn.txn_date.day
            original_month = txn.accounting_month

            delta = 0
            if anchor >= 24 and day <= 6:
                delta = -1
            elif anchor <= 6 and day >= 25:
                delta = 1

            if delta != 0:
                y, m = _shift_month(y, m, delta)
                new_month = f"{y:04d}-{m:02d}"
                txn.accounting_month = new_month
                logger.info(
                    "Shifted %s on %s from %s to %s (anchor day %s)",
                    series.label,
                    txn.txn_date,
                    original_month,
                    new_month,
                    anchor,
                )

            dist = _circular_distance(day, anchor)
            if txn.accounting_month not in allocated:
                allocated[txn.accounting_month] = []
            allocated[txn.accounting_month].append((dist, txn))

        # 3. Collision guard
        for acc_month, items in allocated.items():
            if len(items) > 1:
                items.sort(key=lambda x: x[0])
                for _, dup_txn in items[1:]:
                    dup_txn.needs_review = True
                    dup_txn.review_reason = "Duplicate in accounting month after drift correction"
