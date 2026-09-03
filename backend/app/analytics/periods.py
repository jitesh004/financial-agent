"""Which reporting period a transaction belongs to, and how to select one.

Two halves, and they answer the same question from opposite ends.

`assign_accounting_months` decides, once at import time, which month each row
is COUNTED in - which is not always the calendar month of its date. A salary
paid on the last working day lands on the 31st one month and the 1st two
months later; bucketing by raw date puts two salaries in one month and none in
the next.

Everything above `assign_accounting_months` is the selection side: a preset or
a custom window, resolved to concrete bounds that the API, the query engine
and the analytics engine all filter by. It lives here rather than beside any
one of them because a period means exactly one thing in this app, and three
implementations of "last 3 months" would eventually be three different
answers.

The rule that ties the two halves together: a preset picks whole ACCOUNTING
months, never calendar dates. Ask for August and you get the salary paid on
31 August and the one paid on 1 September if the period engine assigned it to
August - the same rows the Months tab shows, because it is the same question.
A custom window can be either: whole months (the accounting reading) or exact
dates (the literal one), and the UI says which is which.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from ..models.schemas import Transaction
from .recurring import RecurringSeries

logger = logging.getLogger(__name__)


# ==========================================================================
# Selecting a period
# ==========================================================================

#: The Indian financial year runs April to March. Named because the presets
#: below and the labels the UI prints both derive from it.
FY_START_MONTH = 4

#: Every period the app offers, in the order they are shown. One list, read
#: by /api/periods, by the Explore schema and by the frontend's picker, so a
#: preset added here appears everywhere without a second edit.
#:
#: `months` is how many accounting months the window spans when that is
#: fixed; None means "computed" (year-to-date) or "not a window" (all time,
#: custom).
MONTH_PRESETS = {"this_month", "last_month", "last_3m", "last_6m", "last_12m",
                 "recent_2", "recent_3", "recent_4",
                 "ytd", "last_year", "this_fy", "last_fy"}

_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class PeriodError(ValueError):
    """A period spec naming a preset or a month that does not exist."""


def month_key(d: date) -> str:
    """`date(2026, 8, 31)` -> `"2026-08"`."""
    return f"{d.year:04d}-{d.month:02d}"


def parse_month(key: str) -> tuple[int, int]:
    """`"2026-08"` -> `(2026, 8)`, raising rather than guessing."""
    text = str(key or "").strip()
    # A full date is accepted and truncated: a <input type="date"> and a
    # <input type="month"> differ by three characters, and refusing the
    # longer one would be pedantry rather than safety.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        text = text[:7]
    if len(text) != 7 or text[4] != "-":
        raise PeriodError(f"'{key}' is not a month of the form YYYY-MM.")
    try:
        year, month = int(text[:4]), int(text[5:])
    except ValueError as exc:
        raise PeriodError(f"'{key}' is not a month of the form YYYY-MM.") from exc
    if not 1 <= month <= 12:
        raise PeriodError(f"'{key}' names month {month}, which does not exist.")
    return year, month


def shift_month_key(key: str, delta: int) -> str:
    """The month `delta` months after `key` (negative for before)."""
    year, month = parse_month(key)
    total = year * 12 + (month - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_start_date(key: str) -> date:
    year, month = parse_month(key)
    return date(year, month, 1)


def month_end_date(key: str) -> date:
    year, month = parse_month(key)
    return date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)


def month_label(key: str) -> str:
    """`"2026-08"` -> `"Aug 2026"`. The same shape the UI prints."""
    try:
        year, month = parse_month(key)
    except PeriodError:
        return str(key)
    return f"{_MONTH_NAMES[month - 1]} {year}"

def get_period_presets(today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    this_month_key = f"{today.year:04d}-{today.month:02d}"

    out = [
        {"value": "all", "label": "All time", "short": "All", "months": None},
        {"value": "this_month", "label": "This month", "short": "This", "months": 1},
        {"value": "last_month", "label": "Last month", "short": "Last", "months": 1},
    ]
    
    for i in range(2, 5):
        month_str = shift_month_key(this_month_key, -i)
        lbl = month_label(month_str)
        out.append({
            "value": f"recent_{i}", 
            "label": lbl,
            "short": lbl[:3],
            "months": 1
        })

    out.extend([
        {"value": "last_3m", "label": "Last 3 months", "short": "3M", "months": 3},
        {"value": "last_6m", "label": "Last 6 months", "short": "6M", "months": 6},
        {"value": "last_12m", "label": "Last 12 months", "short": "12M", "months": 12},
        {"value": "custom_months", "label": "Custom months…", "short": None, "months": None},
        {"value": "custom", "label": "Custom dates…", "short": None, "months": None},
    ])
    return out

def get_preset_labels() -> dict[str, str]:
    return {p["value"]: p["label"] for p in get_period_presets()}


def months_between(start_month: str, end_month: str) -> list[str]:
    """Every month key from `start_month` to `end_month`, inclusive."""
    if start_month > end_month:
        start_month, end_month = end_month, start_month
    out, cursor = [], start_month
    # Bounded so a malformed pair cannot spin: a century of months is far
    # more than any ledger, and past that the answer is a mistake anyway.
    for _ in range(1200):
        out.append(cursor)
        if cursor >= end_month:
            break
        cursor = shift_month_key(cursor, 1)
    return out


def effective_month(txn: Transaction) -> str:
    """The month a transaction is counted in.

    Its assigned accounting month, or the calendar month of its date for a
    row that predates the period engine. Every bucketing in this app - the
    monthly chart, the Months tab, the SQL below - has to agree on this or
    two screens report different totals for the same August.
    """
    return txn.accounting_month or month_key(txn.txn_date)


def effective_month_sql(prefix: str = "") -> str:
    """`effective_month` as SQL, for the query engine and the repository."""
    return (f"CASE WHEN {prefix}accounting_month IS NOT NULL"
            f" AND {prefix}accounting_month != '' THEN {prefix}accounting_month"
            f" ELSE substr({prefix}txn_date, 1, 7) END")


@dataclass(frozen=True)
class Period:
    """A resolved reporting window.

    Exactly one of two shapes, distinguished by `mode`:

      * `"months"` - whole accounting months, `start_month`..`end_month`.
        What every preset resolves to, and what makes a preset agree with the
        Months tab.
      * `"dates"` - literal dates, `start`..`end`, filtered on the
        transaction date. What a custom day range means.

    ...and `"all"`, which is neither and filters nothing.
    """

    preset: str = "all"
    start_month: str | None = None
    end_month: str | None = None
    start: date | None = None
    end: date | None = None

    @property
    def mode(self) -> str:
        if self.start_month or self.end_month:
            return "months"
        if self.start or self.end:
            return "dates"
        return "all"

    @property
    def is_all(self) -> bool:
        return self.mode == "all"

    @property
    def month_count(self) -> int:
        if self.mode != "months" or not (self.start_month and self.end_month):
            return 0
        return len(months_between(self.start_month, self.end_month))

    def bounds(self) -> tuple[date | None, date | None]:
        """The nominal calendar window, for display and for date arithmetic.

        For a month window these are the first and last day of the months
        named. Deliberately NOT what the rows are filtered by: August's rows
        can begin on 27 July, which is the whole point of an accounting month.
        """
        if self.mode == "months":
            return (month_start_date(self.start_month) if self.start_month else None,
                    month_end_date(self.end_month) if self.end_month else None)
        return self.start, self.end

    def label(self) -> str:
        if self.mode == "all":
            return "All time"
        if self.mode == "months":
            first, last = self.start_month, self.end_month
            if first and last and first != last:
                return f"{month_label(first)} – {month_label(last)}"
            return month_label(first or last or "")
        start, end = self.start, self.end
        if start and end:
            return f"{start.isoformat()} – {end.isoformat()}"
        if start:
            return f"From {start.isoformat()}"
        return f"Until {end.isoformat()}" if end else "All time"

    def contains(self, txn: Transaction) -> bool:
        if self.mode == "all":
            return True
        if self.mode == "months":
            month = effective_month(txn)
            if self.start_month and month < self.start_month:
                return False
            return not (self.end_month and month > self.end_month)
        if self.start and txn.txn_date < self.start:
            return False
        return not (self.end and txn.txn_date > self.end)

    def as_json(self) -> dict[str, Any]:
        start, end = self.bounds()
        return {
            "preset": self.preset,
            "mode": self.mode,
            # "accounting" is the app's own answer to which month a row
            # counts in; "date" is the literal reading. Surfaced so a screen
            # can say which one produced the figures on it.
            "basis": "date" if self.mode == "dates" else "accounting",
            "start_month": self.start_month,
            "end_month": self.end_month,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "months": self.month_count or None,
            "label": self.label(),
        }


ALL_TIME = Period()


def resolve_period(spec: dict[str, Any] | None, today: date | None = None) -> Period:
    """A period spec - preset or custom - to concrete bounds.

    Lenient about which fields a caller sends, because three of them do
    (the dashboard's query string, a saved Explore board, the transactions
    endpoint) and they were written at different times: a month given under
    `start` is read as a month, and a date given under `start_month` is
    truncated to one.
    """
    if not spec:
        return ALL_TIME
    today = today or date.today()
    preset = (spec.get("preset") or "all").strip() or "all"

    if preset in {"custom", "custom_months", "inherit"}:
        return _resolve_custom(spec, preset)
    if preset == "all":
        return ALL_TIME
    if preset not in MONTH_PRESETS:
        raise PeriodError(f"Unknown date preset '{preset}'.")

    this_month = month_key(today)

    if preset == "this_month":
        return Period(preset, this_month, this_month)
    if preset == "last_month":
        previous = shift_month_key(this_month, -1)
        return Period(preset, previous, previous)
    if preset.startswith("recent_"):
        i = int(preset.split("_")[1])
        recent_month = shift_month_key(this_month, -i)
        return Period(preset, recent_month, recent_month)
    if preset in {"last_3m", "last_6m", "last_12m"}:
        span = int(preset.split("_")[1].rstrip("m"))
        return Period(preset, shift_month_key(this_month, -(span - 1)), this_month)
    if preset == "ytd":
        return Period(preset, f"{today.year:04d}-01", this_month)
    if preset == "last_year":
        return Period(preset, f"{today.year - 1:04d}-01", f"{today.year - 1:04d}-12")
    # The financial year the app is for: April to March. "So far" stops at the
    # current month rather than running to a March that has not happened.
    fy_start_year = today.year if today.month >= FY_START_MONTH else today.year - 1
    if preset == "this_fy":
        return Period(preset, f"{fy_start_year:04d}-{FY_START_MONTH:02d}", this_month)
    return Period(preset,
                  f"{fy_start_year - 1:04d}-{FY_START_MONTH:02d}",
                  f"{fy_start_year:04d}-{FY_START_MONTH - 1:02d}")


def _resolve_custom(spec: dict[str, Any], preset: str) -> Period:
    """A user-drawn window, in whichever of the two shapes it arrived in."""
    start_month = spec.get("start_month") or None
    end_month = spec.get("end_month") or None
    start_raw = spec.get("start") or None
    end_raw = spec.get("end") or None

    # A month-shaped value under `start`/`end` is a month. This is how the
    # transactions endpoint can take one pair of parameters and how a board
    # saved with month strings keeps working.
    if not (start_month or end_month) and preset == "custom_months":
        start_month, end_month, start_raw, end_raw = start_raw, end_raw, None, None
    for name, value in (("start", start_raw), ("end", end_raw)):
        if isinstance(value, str) and len(value.strip()) == 7:
            if name == "start":
                start_month, start_raw = value, None
            else:
                end_month, end_raw = value, None

    if start_month or end_month:
        first = _month_or_none(start_month)
        last = _month_or_none(end_month)
        if first and last and first > last:
            first, last = last, first
        return Period("custom_months", first, last)

    first_date = _date_or_none(start_raw)
    last_date = _date_or_none(end_raw)
    if first_date and last_date and first_date > last_date:
        first_date, last_date = last_date, first_date
    if not (first_date or last_date):
        # A half-finished custom range is not a request to show nothing.
        return ALL_TIME
    return Period("custom", start=first_date, end=last_date)


def _month_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    year, month = parse_month(str(value))
    return f"{year:04d}-{month:02d}"


def _date_or_none(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise PeriodError(f"'{value}' is not a date of the form YYYY-MM-DD.") from exc


def previous_period(period: Period) -> Period | None:
    """The equally long window immediately before `period`.

    For period-over-period comparison. `None` for all time, because "the
    period before all of time" is not a thing and comparing against it would
    be misleading rather than empty.
    """
    if period.mode == "months":
        if not (period.start_month and period.end_month):
            return None
        span = period.month_count
        return Period("custom_months",
                      shift_month_key(period.start_month, -span),
                      shift_month_key(period.start_month, -1))
    if period.mode == "dates":
        if not (period.start and period.end):
            return None
        length = (period.end - period.start).days + 1
        return Period("custom",
                      start=period.start - timedelta(days=length),
                      end=period.start - timedelta(days=1))
    return None


def filter_transactions(
    transactions: Iterable[Transaction], period: Period | None,
) -> list[Transaction]:
    """The rows that count in `period`, by the app's own definition of count."""
    rows = list(transactions)
    if period is None or period.is_all:
        return rows
    return [t for t in rows if period.contains(t)]


def covered_months(transactions: Sequence[Transaction]) -> list[str]:
    """Every accounting month these rows are counted in, in order."""
    return sorted({effective_month(t) for t in transactions})


# ==========================================================================
# Assigning a period
# ==========================================================================


#: A monthly series has to be seen this many times before its payday can be
#: estimated at all. Two occurrences give a midpoint, not an anchor.
MIN_OCCURRENCES_TO_SHIFT = 3

#: Which days count as "the end of the month" and "the start of it".
#:
#: A salary anchored on or after MONTH_END that arrives on or before
#: MONTH_START belongs to the PREVIOUS month - pay for August that landed on
#: 1 September is August's pay. The mirror case is a salary anchored at the
#: start of the month that arrives on the 25th or later: that is next month's,
#: paid early.
#:
#: Named because they are also what the Rules screen prints. A number typed
#: twice is a number that eventually disagrees with itself.
MONTH_END_ANCHOR = 24
MONTH_START_ANCHOR = 6
ARRIVED_EARLY_FROM = 25
ARRIVED_LATE_UNTIL = 6


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
    statement_periods: dict | None = None,
) -> None:
    """Set accounting_month on every transaction.

    Default: calendar month of txn_date.
    For a row dated outside the statement it arrived on: the cycle it was
    billed in (see below).
    For members of a monthly recurring series: shifted using salary-drift logic.
    One-offs are NEVER moved.
    """
    statement_periods = statement_periods or {}

    # 1. Default pass
    for txn in transactions:
        txn.accounting_month = f"{txn.txn_date.year:04d}-{txn.txn_date.month:02d}"

        # A refund carries the date of the purchase it reverses, which can sit
        # months before the cycle that actually credited it. HSBC printed
        #
        #     30JUL FIRSTCRYBABY PUNE IND                        1,120.05
        #     27APR RAZ*CARS24 SERVICES PR Gurgaon IND     2,242.00 CR
        #     09AUG PTM*RELIANCE RETAIL L NOIDA UTT                 2.00
        #
        # in that order on a statement covering 24 Jul to 23 Aug 2026. The row
        # is real money and must not be dropped, but accounting it to April
        # opened a four-month hole in the ledger's span - one row stretching
        # the dashboard from four months to five, and the "months covered"
        # count with it. The transaction date stays as printed; only the month
        # it is counted in follows the cycle that billed it.
        period = statement_periods.get(txn.statement_id) if txn.statement_id else None
        if not period:
            continue
        start, end = period
        if not end:
            continue
        if start and start <= txn.txn_date <= end:
            continue
        if txn.txn_date > end:
            continue  # after the cycle closed: not this statement's to claim
        txn.accounting_month = f"{end.year:04d}-{end.month:02d}"

    # Index transactions for quick lookup by ID
    txn_by_id = {t.id: t for t in transactions if t.id}

    # 2. Drift correction
    for series in recurring_series:
        if (series.cadence_name != "monthly"
                or series.occurrences < MIN_OCCURRENCES_TO_SHIFT):
            continue

        members = [txn_by_id[tid] for tid in series.transaction_ids if tid in txn_by_id]
        if not members:
            continue

        anchor = circular_median_day([m.txn_date for m in members])
        allocated: dict[str, list[tuple[int, Transaction]]] = {}

        calendar_month: dict[str, str] = {}

        for txn in members:
            y, m = txn.txn_date.year, txn.txn_date.month
            day = txn.txn_date.day
            original_month = txn.accounting_month
            calendar_month[id(txn)] = original_month

            delta = 0
            if anchor >= MONTH_END_ANCHOR and day <= ARRIVED_LATE_UNTIL:
                # Paid at month end, arrived in the first days of the next -
                # it is the previous month's pay.
                delta = -1
            elif anchor <= MONTH_START_ANCHOR and day >= ARRIVED_EARLY_FROM:
                # Paid at month start, arrived at the end of the one before -
                # next month's pay, early.
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

        # 3. Collision guard.
        #
        # A series must never contribute twice to one accounting month - that
        # is the whole point of the exercise, since the failure being
        # prevented is exactly "two salaries in one month and none in the
        # next". Drift correction can CREATE that collision rather than cure
        # it: with pay on 31-Aug and again on 1-Sep, shifting the September
        # payment back lands both in August and empties September, which is
        # worse than leaving them alone.
        #
        # So a losing occurrence is put back in its own calendar month, not
        # merely annotated. Flagging without moving left the double count
        # standing and only described it.
        for acc_month in list(allocated):
            items = allocated[acc_month]
            if len(items) <= 1:
                continue
            # Nearest the anchor keeps the month; it is the one the series
            # genuinely belongs to.
            items.sort(key=lambda pair: pair[0])
            for _, dup_txn in items[1:]:
                reverted = calendar_month[id(dup_txn)]
                if reverted != dup_txn.accounting_month:
                    logger.info(
                        "Reverted %s on %s to %s - %s already held an "
                        "occurrence closer to the anchor",
                        series.label, dup_txn.txn_date, reverted, acc_month,
                    )
                    dup_txn.accounting_month = reverted
                else:
                    # Two genuine payments in the same calendar month, which
                    # no shift can separate. Surfaced rather than hidden.
                    dup_txn.needs_review = True
                    dup_txn.review_reason = (
                        f"Two {series.label} payments fall in {acc_month}; "
                        f"check whether one belongs to another month."
                    )
