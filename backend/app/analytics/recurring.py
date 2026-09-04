"""Detect recurring transactions: salary, EMIs, rent, subscriptions.

Recurring series are the backbone of any honest forecast. Most of a person's
future cashflow is already determined - the salary arrives, the EMI leaves, the
rent leaves - and the discretionary remainder is what actually varies. Finding
the fixed skeleton first means the forecast only has to model the small,
genuinely uncertain part.

Detection is statistical, not model-based: group by merchant signature, then
test whether the dates form a regular cadence and the amounts cluster. Fully
deterministic, and it re-runs to the same answer every time.

How the dates are tested is the part worth reading.
---------------------------------------------------

The obvious method - take the median gap between charges and look it up in a
table of cadences - is what this module used to do, and it is wrong in three
ways that matter on real statements:

  * A missed month destroys it. Rent paid in January, February, April and May
    has gaps of 31, 59 and 30 days; the median is 30.5, which happens to still
    read as monthly, but Jan/Mar/May gives 59 and 61 and the answer comes back
    "bi-monthly" for a charge that is plainly monthly with one month absent.

  * Months are not 30 days. A bill paid on the 31st has gaps of 28, 31, 30 and
    31, so a method measuring days has to allow ±6 - and that slack is then
    available to any pair of unrelated charges that happen to fall a month or
    so apart.

  * The median gap throws away the strongest signal there is. "The 5th of
    every month" is not a fact about gaps at all; it is a fact about the day
    of the month, and two charges on the 5th are far better evidence of one
    standing instruction than two charges 30 days apart on the 8th and the
    7th.

So instead every candidate cadence is FITTED, the way you would fit a lattice
to a set of points. Each date is assigned to the period it lands in - counted
in whole months for the monthly family, in days for the weekly one - and the
fit is scored on four things at once:

  regularity   how far the dates sit from the period boundaries they claim
  day anchor   how tightly they cluster on one day of the month
  coverage     how many of the periods between the first and the last
               actually have a charge in them
  collisions   two charges in one period, which is proof the period is wrong

The winner is the highest-scoring cadence, and the score becomes the series'
confidence. A missed month costs coverage and nothing else; a charge on the
last day of every month fits perfectly whether that day is the 28th or the
31st; and five shop visits scattered over fourteen months score low on every
one of the four and do not survive.

Amounts get the same treatment. A flat variance gate rejected two real things
outright - a subscription whose price went up, and a loan's interest component,
which falls a little every month for twenty years - so the amount model looks
for a LEVEL SHIFT and for a MONOTONE DRIFT before it gives up on a series.
When it finds one, the going-forward figure is the current level rather than
the lifetime median, which is what a budget needs: rent that went from 41,500
to 45,000 costs 45,000 next month.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..models.schemas import Category, Direction, Transaction
from ..rules import formats, instalments

# ---------------------------------------------------------------------------
# Cadences
# ---------------------------------------------------------------------------

#: Cadences we recognise, in days, with a tolerance for weekends/holidays.
#: Kept in this shape because the Rules screen publishes it - see
#: api/rules_routes. `CANDIDATES` below is what detection actually fits.
CADENCES: list[tuple[str, int, int]] = [
    ("weekly", 7, 2),
    ("fortnightly", 14, 3),
    ("four-weekly", 28, 3),
    ("monthly", 30, 6),
    ("bi-monthly", 61, 8),
    ("quarterly", 91, 12),
    ("half-yearly", 182, 20),
    ("yearly", 365, 30),
]

#: How many times a year each cadence actually happens.
#:
#: Deliberately not 365.25/days. A monthly bill is paid twelve times a year -
#: once per calendar month - whether the gap between charges reads 28, 30 or
#: 31 days, so its monthly cost is the charge itself. Scaling a nominal
#: 30-day cadence by 30.44 days instead reported every monthly commitment
#: 1.5% high: rent of a flat 41,500 was published as "42,109 a month", and the
#: error compounded across a Budget tab that adds fourteen of them up. The
#: day-rate answer is right only for cadences that really are counted in days,
#: and for those three this table agrees with it to three decimal places.
PER_YEAR: dict[str, Decimal] = {
    "weekly": Decimal("52.18"),
    "fortnightly": Decimal("26.09"),
    "four-weekly": Decimal("13.04"),
    "monthly": Decimal("12"),
    "bi-monthly": Decimal("6"),
    "quarterly": Decimal("4"),
    "half-yearly": Decimal("2"),
    "yearly": Decimal("1"),
}


@dataclass(frozen=True)
class Candidate:
    """One cadence to fit, and the unit it is honestly measured in.

    `unit` is the whole point of the split. A monthly charge is regular in
    MONTHS and irregular in days - 28 to 31 of them - so fitting it in days
    needs a tolerance wide enough to also admit coincidences. Fitting it in
    months makes "the 5th of every month" a perfect fit and "the 5th, then 47
    days later" plainly not one.
    """

    name: str
    unit: str  # "month" or "day"
    step: int  # how many units between occurrences
    days: int  # the nominal length, for callers that need one number


#: Tried in this order, and a tie goes to whichever comes first - so the
#: common shapes win a coin toss against the exotic ones. Four-weekly sits
#: after monthly for exactly that reason: it only wins when the dates really
#: do drift a couple of days earlier every time, which is what a 28-day
#: subscription does and what a monthly one never does.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("monthly", "month", 1, 30),
    Candidate("weekly", "day", 7, 7),
    Candidate("fortnightly", "day", 14, 14),
    Candidate("quarterly", "month", 3, 91),
    Candidate("yearly", "month", 12, 365),
    Candidate("half-yearly", "month", 6, 182),
    Candidate("four-weekly", "day", 28, 28),
    Candidate("bi-monthly", "month", 2, 61),
)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

MIN_OCCURRENCES = 3

#: Below this, "recurring" is coincidence - a few fuel stops that happened to
#: fall a month apart. Surfacing those as commitments makes the whole list
#: untrustworthy. Deliberately lower than what the Budget tab will call a
#: commitment (see analytics.budget.MIN_COMMITMENT_CONFIDENCE): "you seem to
#: buy fuel about monthly" is worth SHOWING and is not a fixed cost.
MIN_CONFIDENCE = 0.25

#: A cadence that fits worse than this is not a cadence. Checked before the
#: amount is even looked at, because a charge with no rhythm is not recurring
#: however stable its price.
MIN_CADENCE_FIT = 0.2

#: How much the amount may vary and still count as "the same" recurring
#: charge, when the category says nothing more specific.
AMOUNT_VARIANCE_TOLERANCE = 0.35

#: Per-category amount tolerance, because "the same charge" means different
#: things for different charges. An EMI is the same to the paisa; an
#: electricity bill triples between March and June and is no less a fixed
#: monthly obligation for it. One flat number could only ever be wrong in one
#: direction or the other, and it was wrong in both: it admitted scattered
#: shop visits and rejected genuine utility bills.
AMOUNT_TOLERANCE: dict[str, float] = {
    Category.EMI: 0.10,
    Category.RENT: 0.12,
    Category.SUBSCRIPTIONS: 0.15,
    Category.INVESTMENT: 0.12,
    Category.INSURANCE: 0.20,
    Category.EDUCATION: 0.25,
    Category.LOAN_INTEREST: 0.30,
    Category.SALARY: 0.30,
    Category.UTILITIES: 0.60,
}

#: A level change smaller than this is noise, not a price rise.
MIN_LEVEL_SHIFT = 0.15

#: How much of a sequence has to move the same way before it counts as a
#: drift rather than as noise. A loan's interest component falls every single
#: month; a shop's bill does not.
MIN_MONOTONE_RATIO = 0.8

#: ...and how much of it has to move at all. Without this a price list of two
#: levels is "perfectly monotone" - it makes exactly one move, upward - and
#: gets averaged into a figure that was never charged. See `_looks_like_drift`.
MIN_CHANGING_RATIO = 0.6


def to_monthly(amount: Decimal, cadence_name: str,
               cadence_days: int) -> Decimal:
    """What one charge at this cadence costs per month.

    The single place this conversion happens. It used to be written out twice
    - here and in analytics.budget - and the two copies carried the same
    1.5% error on every monthly commitment, which is the argument for there
    being one of it.
    """
    per_year = PER_YEAR.get(cadence_name)
    if per_year is None:
        # A cadence the table does not name: fall back to the day rate, which
        # is the best available answer when the shape is unknown.
        if cadence_days <= 0:
            return Decimal("0")
        per_year = Decimal("365.25") / Decimal(cadence_days)
    return (amount * per_year / Decimal("12")).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# The series
# ---------------------------------------------------------------------------

@dataclass
class RecurringSeries:
    id: str
    account_id: str | None
    label: str
    category: str
    direction: Direction
    #: The going-forward figure: what the NEXT charge is expected to be. For a
    #: charge that has never changed this is simply the median; for one whose
    #: price rose it is the median SINCE the rise, because a budget that
    #: reports last year's rent is reporting a number that will not be paid.
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

    # ---- what the fit found, kept so the UI can show its working ----

    #: "active" (a charge is not yet due), "overdue" (one has been missed but
    #: not enough to call it finished) or "ended".
    status: str = "active"
    #: Of the periods between the first and last charge, the share that
    #: actually have one. A commitment covers nearly all of them.
    coverage: float = 1.0
    #: How many periods in that span have no charge.
    missed: int = 0
    #: The day of the month it lands on, for a monthly-family cadence.
    day_of_month: int | None = None
    #: The whole-ledger median, as opposed to the going-forward figure above.
    lifetime_median: Decimal | None = None
    #: The most recent charge.
    last_amount: Decimal | None = None
    #: "flat", "rose", "fell" or "drifting".
    amount_trend: str = "flat"
    #: When the level changed, if it did.
    changed_on: date | None = None
    #: Every account this series has been charged to, oldest first. More than
    #: one means the charge followed the user to a replacement card.
    account_ids: list[str] = field(default_factory=list)
    #: Plain-language reasons this was called recurring, for the UI. A series
    #: is an inference, and an inference the user cannot interrogate is one
    #: they have to take on faith.
    evidence: list[str] = field(default_factory=list)

    @property
    def monthly_equivalent(self) -> Decimal:
        """Normalize any cadence to a monthly figure for budgeting."""
        return to_monthly(self.median_amount, self.cadence_name,
                          self.cadence_days)


# ---------------------------------------------------------------------------
# Signatures: what makes two rows "the same charge"
# ---------------------------------------------------------------------------

#: Month names and rail codes both live in rules.formats - four modules used
#: to carry their own copy of the month list, and one of them was missing the
#: full names.
_MONTHS = formats.MONTH_TOKEN.pattern
_RAILS = formats.SIGNATURE_RAIL_PATTERN.pattern
_SIGNATURE_NOISE = re.compile(
    rf'\b\d+\b|\b[A-Z0-9]*\d[A-Z0-9]*\b|{_MONTHS}|{_RAILS}', re.IGNORECASE)

#: A UPI virtual payment address. The handle changes when somebody switches
#: app - the same landlord is "@okhdfcbank" one month and "@ybl" the next -
#: so it cannot be part of what identifies the payee.
_VPA = re.compile(r"@[A-Z0-9.\-]+", re.IGNORECASE)

#: Words that appear in half of all narrations and identify nobody. Left in,
#: they crowd out the merchant: "RENT PAYMENT TO NEFT ONLINE" keeps four
#: tokens and only one of them says who was paid.
_FILLER = frozenset({
    "PAYMENT", "PAYMENTS", "PMT", "PAY", "PAID", "TRANSFER", "TRF", "TXN",
    "TRANSACTION", "ONLINE", "AUTO", "DEBIT", "CREDIT", "THE", "FOR", "FROM",
    "AND", "WITH", "VIA", "REF", "REFNO", "PVT", "PRIVATE", "LTD", "LIMITED",
    "LIMITE", "LLP", "INC", "COM", "WWW", "IND", "INR", "INDIA", "BANK",
    "ACCOUNT", "ACC", "CHARGES", "CHARGE", "MONTHLY", "DUE", "BILL", "BILLS",
})


def _tokens(text: str) -> list[str]:
    """The words in a narration that could name a payee."""
    cleaned = _SIGNATURE_NOISE.sub(" ", _VPA.sub(" ", text.upper()))
    return [w for w in re.split(r"[^A-Z]+", cleaned)
            if len(w) > 2 and w not in _FILLER]


def _signature(txn: Transaction) -> str:
    """A stable key for 'the same charge, recurring'.

    Reference numbers and dates change every month, so they are stripped. What
    remains - the merchant words - is what makes NETFLIX in January the same
    series as NETFLIX in February.
    """
    if txn.category == Category.SALARY:
        # Payroll narrations change every month - "NEFT-CMS1812612535608-
        # ACME TECHNOLOGIES" one month, "TECHNOLOGIES PRIVATELIMI-
        # PANKAJSALJUL26CMS2" the next - so the words cannot key the series
        # and the category has to.
        #
        # But the category alone put every salary-labelled row in ONE group,
        # including two 12,000 person-to-person transfers the user had
        # themselves marked as salary. Median gap across the merged set was
        # 20 days, which is not monthly, so no series was found at all - and
        # with no series there is no drift correction, so a payroll credit
        # landing on 1 August stayed in August instead of counting as July's
        # pay. August showed two salaries and July showed none.
        #
        # Order of magnitude separates them and nothing else has to: 12,000
        # and 167,489 are plainly not the same charge, while a raise from
        # 167,489 to 185,000 stays in one series where an exact-amount key
        # would split it.
        return f"SALARY/{_magnitude(txn.amount)}"

    # The issuer's EMI offer marker comes off first. It is printed on some
    # months' charges and not others depending on whether the issuer felt
    # like advertising, so leaving it in split one merchant into two series -
    # see rules.instalments for what the marker actually means.
    base = instalments.strip_offer_marker(
        txn.normalized_description or txn.raw_description or "")
    words = _tokens(base)
    if not words:
        # Nothing but numbers and rails. The merchant field is a second
        # chance - `extract_merchant` reads a different part of the string.
        words = _tokens(txn.merchant or "")
    return " ".join(words[:4])


def _loose_signature(sig: str) -> str:
    """A shorter key, for joining a series that changed its wording.

    Only ever used to RESCUE a group too small to qualify on its own, never
    to merge two groups that both already look like series - see
    `_merge_variants`. Two tokens is enough to keep "AXIS MUTUAL FUND SIP"
    and "AXIS MUTUAL FUND" together without also collecting every other
    charge that happens to start with the same word.
    """
    return " ".join(sig.split()[:2])


def _magnitude(amount: Decimal) -> int:
    """How many digits the amount has, as a coarse size band.

    Coarse on purpose. It has to separate charges that are plainly different
    things while keeping one that merely changed - a raise, a rent increase -
    in a single series.
    """
    return len(str(int(abs(amount))))


# ---------------------------------------------------------------------------
# Fitting a cadence
# ---------------------------------------------------------------------------

@dataclass
class CadenceFit:
    candidate: Candidate
    #: How close the dates sit to the lattice they claim, 0..1.
    regularity: float
    #: How tightly they cluster on one day of the month, 0..1. Only
    #: meaningful for the monthly family; 1.0 for the others, which have no
    #: day of the month to be regular about.
    day_fit: float
    #: Share of the periods in the span that hold a charge, 0..1.
    coverage: float
    #: Periods in the span with no charge at all.
    missed: int
    #: Periods holding more than one charge - proof this period is too long.
    collisions: int
    anchor_day: int | None
    #: Set instead of a fixed day when the series is anchored to the END of
    #: the month - "the last working day" - and says how many days back.
    anchor_from_end: int | None = None

    @property
    def score(self) -> float:
        """One number for "is this the rhythm?".

        Coverage is raised to a power so that a series which skips half its
        periods is punished much harder than one that skips a tenth: five
        visits to a shop over fourteen months and eleven rent payments out of
        twelve are both "not every period", and they are not remotely the
        same claim.

        The day anchor is a HALF weight rather than a full one. A charge on
        the same day every month is the strongest evidence there is, but a
        bill whose date wanders - an electricity bill lands whenever the
        meter is read - is still monthly, and a full-weight anchor threw
        those away.
        """
        collision_rate = self.collisions / max(1, self.collisions + self.missed
                                               + 1)
        return round(
            self.regularity
            * (0.5 + 0.5 * self.day_fit)
            * (self.coverage ** 1.5)
            * (1 - collision_rate),
            4,
        )


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _circular_day_distance(a: int, b: int) -> int:
    """Distance between two days of the month, around the boundary.

    The 1st and the 31st are one day apart in practice - a standing
    instruction dated the last of the month lands on the 1st when the last
    is a Sunday - and treating them as thirty days apart is what makes a
    perfectly regular charge look erratic.
    """
    gap = abs(a - b)
    return min(gap, 31 - gap)


def _days_from_month_end(when: date) -> int:
    import calendar
    return calendar.monthrange(when.year, when.month)[1] - when.day


def _day_anchor_fit(dates: list[date]) -> tuple[float, int, int | None]:
    """How tightly these dates cluster on one day of the month.

    Measured two ways and scored on the better of them, because "the 1st of
    the month" and "the last day of the month" are both perfectly regular and
    only the first has a constant day number. February is what forces it: the
    31st, the 28th and the 30th are one instruction, and counting BACK from
    the end of the month is what makes that visible.

    Returns the fit, a representative day of the month, and - when counting
    back won - how many days back it is, so the next date can be projected
    onto the right day of a month of a different length.
    """
    from .periods import circular_median_day

    anchor = circular_median_day(dates)
    from_start = statistics.fmean(
        _circular_day_distance(anchor, d.day) for d in dates)

    ends = [_days_from_month_end(d) for d in dates]
    end_anchor = int(statistics.median(ends))
    from_end = statistics.fmean(abs(e - end_anchor) for e in ends)

    #: Eight days is a third of a month: past that the date carries no
    #: information about which day the charge lands on.
    spread = min(from_start, from_end)
    fit = max(0.0, 1.0 - spread / 8.0)
    return fit, anchor, (end_anchor if from_end < from_start else None)


def fit_cadence(dates: list[date]) -> CadenceFit | None:
    """The rhythm these dates keep, or None if they keep none.

    Every candidate is fitted and the best-scoring one wins - as opposed to
    taking the median gap and looking it up, which cannot tell a monthly
    charge with a month missing from a bi-monthly one.
    """
    if len(dates) < 2:
        return None

    dates = sorted(dates)
    best: CadenceFit | None = None

    for candidate in CANDIDATES:
        fit = _fit_one(dates, candidate)
        if fit is None:
            continue
        if best is None or fit.score > best.score:
            best = fit
    return best


#: A day of the month this close to either end counts as "on the boundary",
#: and a charge dated there can legitimately appear in the month on either
#: side of the one it belongs to.
_BOUNDARY_DAYS = 7


def _anchored_month(when: date, anchor: int | None) -> int:
    """Which month this charge BELONGS to, as an absolute month number.

    The calendar month is the obvious answer and it is wrong for the most
    important series there is. A salary paid on the last working day lands on
    31 May one year and on 2 June the next, because 31 May was a Saturday -
    two calendar months apart for two consecutive payments of the same
    salary. Slotting by calendar month then puts two charges in June, none in
    May, and the whole series reads as irregular: on the demo ledger the
    monthly fit scored 0.31 that way and lost outright to a four-weekly
    reading of a plainly monthly salary.

    So a date on the far side of a month boundary FROM THE ANCHOR is counted
    as the anchor's month. This is the same correction
    `analytics.periods.assign_accounting_months` applies when it decides
    which month a payment is reported in, for the same reason - and the two
    agreeing is what stops a salary being monthly for reporting and
    four-weekly for forecasting.
    """
    index = when.year * 12 + when.month
    if anchor is None:
        return index
    if anchor >= 31 - _BOUNDARY_DAYS and when.day <= _BOUNDARY_DAYS:
        return index - 1  # paid late in the month, slipped into the next one
    if anchor <= _BOUNDARY_DAYS and when.day >= 31 - _BOUNDARY_DAYS:
        return index + 1  # paid early in the month, landed in the previous one
    return index


def _fit_one(dates: list[date], candidate: Candidate) -> CadenceFit | None:
    if candidate.unit == "month":
        day_fit, anchor, from_end = _day_anchor_fit(dates)
        indices = [_anchored_month(d, anchor) for d in dates]
        base = min(indices)
        elapsed = [i - base for i in indices]
    else:
        day_fit, anchor, from_end = 1.0, None, None
        origin = dates[0]
        elapsed = [(d - origin).days for d in dates]

    step = candidate.step
    # int(x + 0.5) rather than round(), which rounds halves to even and would
    # send an exactly-halfway charge to a different slot depending on whether
    # the slot number happened to be odd.
    slots = [int(value / step + 0.5) for value in elapsed]
    # min/max rather than first/last: the straddle correction above can move
    # a boundary-dated charge into the month before the one it was dated in,
    # so a date-sorted list is not necessarily slot-sorted.
    span = max(slots) - min(slots) + 1
    if span <= 0:
        return None

    counts = Counter(slots)
    occupied = len(counts)
    if occupied < 2:
        # Every date landed in one period. That is not a rhythm, it is a
        # cadence far too long for this data.
        return None
    collisions = sum(n - 1 for n in counts.values() if n > 1)
    missed = span - occupied
    coverage = occupied / span

    residuals = [abs(value - slot * step) for value, slot in zip(elapsed, slots)]
    # A residual of half a step is the worst possible: any further and the
    # date would have been assigned to the next period instead.
    regularity = max(0.0, 1.0 - statistics.fmean(residuals) / (step / 2))

    return CadenceFit(candidate=candidate, regularity=regularity,
                      day_fit=day_fit, coverage=coverage, missed=missed,
                      collisions=collisions, anchor_day=anchor,
                      anchor_from_end=from_end)


# ---------------------------------------------------------------------------
# Modelling the amount
# ---------------------------------------------------------------------------

@dataclass
class AmountModel:
    #: What the next charge is expected to be.
    typical: Decimal
    lifetime_median: Decimal
    last: Decimal
    #: Robust relative dispersion within the current level.
    variance: float
    stable: bool
    trend: str  # "flat" | "rose" | "fell" | "drifting"
    changed_on: date | None = None
    #: What it used to cost, when the level changed.
    previous: Decimal | None = None


def _median(values: list[Decimal]) -> Decimal:
    return Decimal(str(statistics.median(values)))


def _dispersion(values: list[Decimal], centre: Decimal) -> float:
    """Median absolute deviation, relative to the centre.

    Median rather than mean, so one part-payment or one double-charge does
    not by itself disqualify a series that is otherwise identical every
    month. That single outlier is exactly what the mean version punished, and
    a genuine standing instruction with one duplicated month was being thrown
    away for it.
    """
    if centre == 0:
        return 1.0
    deviations = [abs(float(v - centre) / float(centre)) for v in values]
    return float(statistics.median(deviations))


def _within(value: Decimal, centre: Decimal, tolerance: float) -> bool:
    if centre == 0:
        return value == 0
    return abs(float(value - centre) / float(centre)) <= tolerance


def _looks_like_drift(values: list[Decimal]) -> bool:
    """Whether this sequence creeps rather than steps.

    Three conditions, and the middle one is what separates a drift from a
    price rise. A subscription that went 199, 199, 199, 199, 649, 649, 649,
    649 is perfectly monotone - the single move it makes is upward - so
    monotonicity alone would call it a drift and report the average of the
    two prices as next month's bill. What a drift actually looks like is that
    NEARLY EVERY charge differs from the one before it, which is true of a
    loan's interest component and false of a price list.
    """
    if len(values) < 4:
        return False

    pairs = list(zip(values, values[1:]))
    steps = [1 if b > a else -1 if b < a else 0 for a, b in pairs]
    moves = [step for step in steps if step]
    if len(moves) / len(pairs) < MIN_CHANGING_RATIO:
        return False  # a couple of jumps in an otherwise flat list
    up = sum(1 for step in moves if step > 0)
    if max(up, len(moves) - up) / len(moves) < MIN_MONOTONE_RATIO:
        return False  # it wanders up and down: noise, not a trend

    ends = (values[0], values[-1])
    larger = max(abs(ends[0]), abs(ends[1]))
    if larger == 0:
        return False
    return abs(float(ends[1] - ends[0]) / float(larger)) >= MIN_LEVEL_SHIFT


def model_amounts(amounts: list[Decimal], dates: list[date],
                  tolerance: float) -> AmountModel:
    """What this charge costs, and whether "the same charge" is honest.

    Three shapes count as one charge, and only the first of them used to:

      flat       every charge within tolerance of the median
      a step     a price rise or a rent increase - tight before, tight after,
                 and the two levels far enough apart to be a decision rather
                 than noise
      a drift    a loan's interest component, which falls a little every
                 month for twenty years, or a rent with an annual escalation

    The step and the drift both used to fail the flat test, so a subscription
    that went up in price stopped being a subscription and a loan's interest
    leg was never a series at all.
    """
    lifetime = _median(amounts)
    last = amounts[-1]
    flat_variance = _dispersion(amounts, lifetime)

    # ---- a steady drift ----
    # Checked FIRST, because a drifting series can sit well inside its
    # tolerance over a short window and still not be flat: a loan's interest
    # leg falls a little every month, so the median of the window is behind
    # the trend from the moment it is taken. Reporting the median as next
    # month's figure is a small error that compounds over a twenty-year
    # projection, and the recent charges are the honest answer.
    if _looks_like_drift(amounts):
        recent = amounts[-3:]
        return AmountModel(
            typical=_median(recent), lifetime_median=lifetime, last=last,
            variance=_dispersion(recent, _median(recent)), stable=True,
            trend="drifting",
        )

    # ---- flat ----
    # The median has to be usable as a forecast, which is more than it being
    # in the middle: a series charged at one level for a year and a higher one
    # since has a median absolute deviation of ZERO if most of the charges sit
    # at the old level, and the figure carried forward would be a price that
    # is no longer charged. So the latest charge has to agree with the median
    # too, and a series where it does not falls through to the level-shift
    # test below, which is what such a series actually is.
    if flat_variance <= tolerance and _within(last, lifetime, tolerance):
        return AmountModel(typical=lifetime, lifetime_median=lifetime,
                           last=last, variance=flat_variance, stable=True,
                           trend="flat")

    # ---- a level shift ----
    step = _best_level_shift(amounts, tolerance)
    if step is not None:
        at, before, after = step
        med_before, med_after = _median(before), _median(after)
        return AmountModel(
            typical=med_after, lifetime_median=lifetime, last=last,
            variance=_dispersion(after, med_after), stable=True,
            trend="rose" if med_after > med_before else "fell",
            changed_on=dates[at], previous=med_before,
        )

    return AmountModel(typical=lifetime, lifetime_median=lifetime, last=last,
                       variance=flat_variance, stable=False, trend="flat")


def _best_level_shift(
    amounts: list[Decimal], tolerance: float
) -> tuple[int, list[Decimal], list[Decimal]] | None:
    """The point where the price changed, if there is one.

    Three conditions, and the third is the one that makes this safe. Both
    sides have to be tight on their own, the two levels have to be far enough
    apart to be a decision rather than noise, and **every charge has to be on
    the right side of the cut** - nearer its own side's level than the other
    one's.

    Without that last test the cut lands in the wrong place. A subscription
    that went 199, 199, 199, 199, 649, 649, 649, 649 can be cut after the
    second charge and still pass the first two conditions, because the median
    absolute deviation of [199, 199, 649, 649, 649, 649] around 649 is zero -
    four of the six sit exactly on it. The reported change date was then two
    months before the price actually moved. Requiring clean separation is
    what a level shift really means, and it picks the only cut that has it.
    """
    if len(amounts) < 4:
        return None

    best: tuple[float, int] | None = None
    for at in range(2, len(amounts) - 1):
        before, after = amounts[:at], amounts[at:]
        med_before, med_after = _median(before), _median(after)
        larger = max(med_before, med_after)
        if larger == 0:
            continue
        jump = abs(float(med_after - med_before) / float(larger))
        if jump < MIN_LEVEL_SHIFT:
            continue
        if _dispersion(before, med_before) > tolerance:
            continue
        if _dispersion(after, med_after) > tolerance:
            continue
        if any(abs(v - med_before) < abs(v - med_after) for v in after):
            continue
        if any(abs(v - med_after) < abs(v - med_before) for v in before):
            continue
        if best is None or jump > best[0]:
            best = (jump, at)

    if best is None:
        return None
    at = best[1]
    return at, amounts[:at], amounts[at:]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_recurring(transactions: list[Transaction]) -> list[RecurringSeries]:
    """Group transactions into recurring series."""
    eligible = [t for t in transactions if _countable(t)]

    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for txn in eligible:
        sig = _signature(txn)
        if not sig:
            continue
        groups[(txn.account_id, txn.direction, sig)].append(txn)

    groups = _merge_variants(groups)

    # Excluded rows specifically must not set this: a single mis-parsed row -
    # a credit card statement's own column-header line ("PaymentDueDate
    # Min.AmountDue ChequeNo Date Bank Amount") landing in the ledger with a
    # garbage date, already excluded from every total for exactly that
    # reason - still counted here as "the most recent activity in the whole
    # ledger" and pushed `today` weeks past every real transaction. Every
    # series measured its own last occurrence against that phantom date, so
    # a salary that was two or three weeks overdue read as two or three
    # MONTHS overdue and dropped out of "active" entirely.
    today = max((t.txn_date for t in eligible), default=date.today())
    #: ...and per account, because a statement that stops in March does not
    #: mean the standing instruction on that account stopped in March. Judged
    #: against the whole ledger's end date, every series on an account whose
    #: statements are a few months behind reads as abandoned.
    account_end: dict[str | None, date] = {}
    for txn in eligible:
        current = account_end.get(txn.account_id)
        if current is None or txn.txn_date > current:
            account_end[txn.account_id] = txn.txn_date

    series: list[RecurringSeries] = []
    for (account_id, direction, sig), members in groups.items():
        one = _build_series(account_id, direction, sig, members,
                            as_of=account_end.get(account_id, today))
        if one is not None:
            series.append(one)

    # Stamp the series id back onto its members so the UI can link them.
    for one in series:
        wanted = set(one.transaction_ids)
        for txn in eligible:
            if txn.id in wanted:
                txn.recurring_series_id = one.id

    series = [s for s in series if s.confidence >= MIN_CONFIDENCE]
    series.sort(key=lambda s: -s.monthly_equivalent)
    return series


def _countable(txn: Transaction) -> bool:
    """Whether this row may take part in a series at all."""
    # Mirror legs would otherwise produce a phantom second series for every
    # EMI and card payment - and, worse, an income series for money that is
    # really the user paying their own card.
    if txn.is_mirror_leg:
        return False
    # A row excluded from every total (a known parser artifact - a
    # statement's own column-header line misread as a transaction - or a
    # user's own "leave this out" decision) is not a real recurring charge
    # either. A credit card's header row landing three months running with a
    # near-identical amount is exactly the shape this function looks for, and
    # without this check it showed up as its own "recurring series" - a
    # phantom entry nobody could act on, sitting in the same list as their
    # actual subscriptions and EMIs.
    return not txn.excluded


def _merge_variants(
    groups: dict[tuple, list[Transaction]]
) -> dict[tuple, list[Transaction]]:
    """Join groups that are one charge wearing two descriptions.

    Two cases, both real and both invisible to an exact-signature key:

      * A card is replaced and every subscription on it reappears under a new
        account id. The old series stops dead and a new one starts, and
        neither has enough occurrences to be believed on its own.
      * A merchant changes how it writes itself - "AXIS MUTUAL FUND SIP"
        becomes "AXIS MUTUAL FUND" - and one series becomes two.

    Merging is deliberately conservative: the two must agree on direction and
    on the loose signature, their amounts must be within a quarter of each
    other, their date ranges must not overlap, and the second must pick up
    roughly where the first left off. Overlapping ranges are the case this
    must NOT touch - two housemates paying rent from two accounts on the same
    day are two commitments, and merging them would put two charges in every
    period and destroy both fits.
    """
    by_loose: dict[tuple, list[tuple]] = defaultdict(list)
    for key in groups:
        account_id, direction, sig = key
        by_loose[(direction, _loose_signature(sig))].append(key)

    merged: dict[tuple, list[Transaction]] = {}
    absorbed: set[tuple] = set()

    for keys in by_loose.values():
        if len(keys) < 2:
            continue
        # Largest first: a short group joins an established one, never the
        # other way round, so the surviving key names the dominant variant.
        keys = sorted(keys, key=lambda k: -len(groups[k]))
        for small in keys[1:]:
            for large in keys:
                if large == small or large in absorbed:
                    continue
                if _joinable(groups[large], groups[small]):
                    groups[large].extend(groups[small])
                    groups[large].sort(key=lambda t: t.txn_date)
                    absorbed.add(small)
                    break

    for key, members in groups.items():
        if key not in absorbed:
            merged[key] = members
    return merged


#: The shortest gap that is definitely not "the card was replaced". A
#: replacement card is issued in days and the standing instruction resumes on
#: the next cycle; a charge that reappears a year later after a year of
#: silence is a new arrangement, whatever it is called.
_MIN_HANDOVER_GAP_DAYS = 45


def _joinable(established: list[Transaction],
              orphan: list[Transaction]) -> bool:
    """Whether an orphan group is the same charge as an established one.

    Continuity is the test. Two runs of the same charge on two accounts are
    one commitment when the second picks up where the first stopped - which
    is what happens when a card is replaced - and are two different things
    when they overlap or when there is a long silence between them.
    """
    a_dates = sorted(t.txn_date for t in established)
    b_dates = sorted(t.txn_date for t in orphan)
    if a_dates[0] <= b_dates[-1] and b_dates[0] <= a_dates[-1]:
        return False  # they ran at the same time, so they are two things

    gap = (b_dates[0] - a_dates[-1]).days
    if gap < 0:
        gap = (a_dates[0] - b_dates[-1]).days
    # Measured against the established group's own rhythm: a monthly charge
    # resumes within weeks, an annual premium within about a year.
    typical_gap = statistics.median(
        [(b - a).days for a, b in zip(a_dates, a_dates[1:])]) \
        if len(a_dates) > 1 else 30
    if gap > max(_MIN_HANDOVER_GAP_DAYS, typical_gap * 2.2):
        return False

    a_amount = _median([t.amount for t in established])
    b_amount = _median([t.amount for t in orphan])
    larger = max(a_amount, b_amount)
    if larger == 0:
        return False
    return abs(float(b_amount - a_amount) / float(larger)) <= 0.25


def _build_series(account_id: str | None, direction: Direction, sig: str,
                  members: list[Transaction],
                  as_of: date) -> RecurringSeries | None:
    if len(members) < MIN_OCCURRENCES:
        return None

    members.sort(key=lambda t: t.txn_date)
    dates = [t.txn_date for t in members]

    fit = fit_cadence(dates)
    if fit is None or fit.score < MIN_CADENCE_FIT:
        return None

    category = _dominant_category(members)
    tolerance = AMOUNT_TOLERANCE.get(category, AMOUNT_VARIANCE_TOLERANCE)
    amounts = model_amounts([t.amount for t in members], dates, tolerance)
    if not amounts.stable:
        return None  # same payee, wildly different amounts - not a fixed charge

    cadence_days = fit.candidate.days
    last_seen = dates[-1]
    next_expected = _next_after(last_seen, fit.candidate, fit.anchor_day,
                                fit.anchor_from_end)

    overdue_by = (as_of - last_seen).days
    if overdue_by <= cadence_days * 1.15 + 3:
        status = "active"
    elif overdue_by <= cadence_days * 1.6 + 5:
        status = "overdue"
    else:
        status = "ended"

    # Confidence is the cadence fit, discounted by how much the amount moves
    # and by how much history there is. A series is only as trustworthy as
    # its least regular dimension.
    amount_score = (0.85 if amounts.trend == "drifting"
                    else 1.0 - 0.5 * min(1.0, amounts.variance / tolerance)
                    if tolerance else 1.0)
    maturity = min(1.0, 0.55 + 0.15 * (len(members) - MIN_OCCURRENCES))
    confidence = round(fit.score * amount_score * maturity, 3)

    accounts = list(dict.fromkeys(
        t.account_id for t in members if t.account_id))
    series_id = hashlib.sha256(
        f"{account_id}|{direction.value}|{sig}".encode("utf-8")
    ).hexdigest()[:16]

    return RecurringSeries(
        id=series_id,
        account_id=account_id,
        label=_label_for(members, sig),
        category=category,
        direction=direction,
        median_amount=amounts.typical,
        cadence_days=cadence_days,
        cadence_name=fit.candidate.name,
        occurrences=len(members),
        first_seen=dates[0],
        last_seen=last_seen,
        next_expected=next_expected,
        is_active=status != "ended",
        confidence=confidence,
        amount_variance=round(amounts.variance, 3),
        transaction_ids=[t.id for t in members if t.id],
        status=status,
        coverage=round(fit.coverage, 3),
        missed=fit.missed,
        day_of_month=fit.anchor_day,
        lifetime_median=amounts.lifetime_median,
        last_amount=amounts.last,
        amount_trend=amounts.trend,
        changed_on=amounts.changed_on,
        account_ids=accounts,
        evidence=_evidence(fit, amounts, members, status, as_of),
    )


def _next_after(last: date, candidate: Candidate, anchor_day: int | None,
                anchor_from_end: int | None = None) -> date:
    """When the next charge is due.

    For a monthly-family cadence this steps by whole months and lands on the
    day the series actually uses, rather than adding a nominal 30 days -
    which drifts a day earlier every month and, over a year, predicts the
    wrong week.
    """
    if candidate.unit == "day":
        return last + timedelta(days=candidate.step)

    import calendar

    # Stepped from the month this charge BELONGS to, not the one it was dated
    # in - see `_anchored_month`. A salary paid on 1 June for May is next due
    # at the end of June, and stepping from the calendar month would say the
    # end of July.
    index = _anchored_month(last, anchor_day) + candidate.step
    year, month = divmod(index - 1, 12)
    month += 1
    length = calendar.monthrange(year, month)[1]
    if anchor_from_end is not None:
        # Counted back from the end, so a series paid on the last day lands
        # on the 30th in June and the 31st in July rather than on whichever
        # single number happened to be the median.
        return date(year, month, max(1, length - anchor_from_end))
    return date(year, month, min(anchor_day or last.day, length))


def _dominant_category(members: list[Transaction]) -> str:
    """The category most of these rows carry.

    Uncategorized is skipped while anything else is available: a series where
    eight rows are known and one is not is a series about the eight, and
    letting a tie fall to "uncategorized" hid real commitments behind a label
    that reads like a bug.
    """
    counts: Counter[str] = Counter(t.category for t in members)
    counts.pop(Category.UNCATEGORIZED, None)
    if not counts:
        return Category.UNCATEGORIZED
    return counts.most_common(1)[0][0]


#: An instalment counter - "(013/240)" - which changes on every charge and so
#: makes every month's label different from the last.
_COUNTER = re.compile(r"\(\s*\d+\s*[/\s]\s*\d+\s*\)")


def _label_for(members: list[Transaction], fallback: str) -> str:
    """Human-readable name for the series.

    The most common cleaned description wins, with the shortest breaking a
    tie. "Shortest" alone used to decide it, which picked an arbitrary month
    for anything carrying a counter - a home loan was labelled by whichever
    instalment happened to have the fewest characters.
    """
    if _dominant_category(members) == Category.SALARY:
        return "Salary"

    cleaned = []
    for txn in members:
        text = _COUNTER.sub(
            "", instalments.strip_offer_marker(txn.raw_description or ""))
        text = re.sub(r"\s+", " ", text).strip(" -/|,")
        if text:
            cleaned.append(text[:70])
    if not cleaned:
        return fallback.title() or "Recurring charge"

    counts = Counter(cleaned)
    top = max(counts.values())
    return min((t for t, n in counts.items() if n == top), key=len)


def _evidence(fit: CadenceFit, amounts: AmountModel,
              members: list[Transaction], status: str,
              as_of: date) -> list[str]:
    """Why this is a series, in words a person can check against the rows.

    A detected series is an inference, and the Recurring tab already lets the
    user open one to see the transactions behind it. This is the other half:
    saying what about those transactions made the inference, so a wrong one
    can be argued with rather than merely deleted.
    """
    notes: list[str] = []
    name = fit.candidate.name
    if fit.anchor_day and fit.candidate.unit == "month":
        notes.append(f"{len(members)} charges, {name}, around the "
                     f"{_ordinal(fit.anchor_day)} of the month")
    else:
        notes.append(f"{len(members)} charges, {name}")

    if fit.missed:
        notes.append(f"{fit.missed} period(s) with no charge out of "
                     f"{fit.missed + len(members) - fit.collisions}")
    if fit.collisions:
        notes.append(f"{fit.collisions} period(s) charged more than once")

    if amounts.trend == "flat":
        notes.append("the amount barely moves"
                     if amounts.variance < 0.02
                     else f"the amount varies by about "
                          f"{amounts.variance:.0%} either side")
    elif amounts.trend == "drifting":
        notes.append("the amount moves steadily in one direction, so the "
                     "latest charges set the going-forward figure")
    else:
        when = amounts.changed_on.isoformat() if amounts.changed_on else "once"
        was = amounts.previous if amounts.previous is not None \
            else amounts.lifetime_median
        notes.append(
            f"the amount {amounts.trend} on {when}, from {was:,.0f} to "
            f"{amounts.typical:,.0f}; the new level is what is carried "
            f"forward")

    if status == "overdue":
        notes.append(f"nothing since {members[-1].txn_date.isoformat()}, "
                     f"which is one period late as of {as_of.isoformat()}")
    elif status == "ended":
        notes.append(f"nothing since {members[-1].txn_date.isoformat()} - "
                     f"treated as finished")

    accounts = {t.account_id for t in members if t.account_id}
    if len(accounts) > 1:
        notes.append(f"charged to {len(accounts)} different accounts, so it "
                     f"followed a replaced card")
    return notes


def _ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

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
