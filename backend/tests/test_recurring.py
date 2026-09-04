"""What the recurring detector must and must not call a series.

Each case here is a shape the old median-gap detector got wrong, or one it got
right that the new lattice fit must not break. The two halves matter equally:
a detector that finds everything is as useless as one that finds nothing,
because the Budget tab reads this list to decide what a month costs.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics.recurring import (AMOUNT_TOLERANCE,  # noqa: E402
                                     MIN_CONFIDENCE, detect_recurring,
                                     fit_cadence, model_amounts, to_monthly)
from app.models.schemas import Category, Direction, Transaction  # noqa: E402


def series_of(dates_and_amounts, *, category=Category.SUBSCRIPTIONS,
              description="STREAMLINE MEDIA", account="a1",
              direction=Direction.DEBIT) -> list[Transaction]:
    return [
        Transaction(
            id=f"{account}-{description}-{i}", account_id=account,
            txn_date=when, raw_description=description,
            normalized_description=description, merchant=description,
            amount=Decimal(amount), direction=direction, category=category,
        )
        for i, (when, amount) in enumerate(dates_and_amounts)
    ]


def monthly(day: int, amount: str, months: int, *, start=(2025, 1),
            **kwargs) -> list[Transaction]:
    """The same day of each month, clamped to months that are shorter.

    Clamped rather than skipped, because "the 31st" IS how a standing
    instruction on the last day of the month reads in February.
    """
    import calendar

    year, month = start
    rows = []
    for _ in range(months):
        last = calendar.monthrange(year, month)[1]
        rows.append((date(year, month, min(day, last)), amount))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return series_of(rows, **kwargs)


def only(transactions):
    found = detect_recurring(transactions)
    assert len(found) == 1, [(s.label, s.cadence_name, s.confidence)
                             for s in found]
    return found[0]


# --------------------------------------------------------------------------
# Cadence: the thing the median gap could not do
# --------------------------------------------------------------------------

def test_a_missed_month_is_still_monthly():
    """January, February, April, May.

    The median gap across those is 30.5 days, which happens to survive - but
    Jan/Mar/May gives 59 and 61 and the old detector answered "bi-monthly"
    for a charge that is plainly monthly with a month absent. Fitting the
    dates to a lattice makes the missing month cost coverage and nothing
    else.
    """
    rows = series_of([(date(2025, 1, 5), "1200"), (date(2025, 2, 5), "1200"),
                      (date(2025, 4, 5), "1200"), (date(2025, 5, 5), "1200"),
                      (date(2025, 6, 5), "1200"), (date(2025, 7, 5), "1200")])
    found = only(rows)
    assert found.cadence_name == "monthly"
    assert found.missed == 1
    assert found.coverage == pytest.approx(6 / 7, abs=0.01)


def test_a_genuinely_bi_monthly_charge_is_not_called_monthly():
    """The same test in reverse: every other month really is bi-monthly.

    Fitting monthly to these dates leaves half the periods empty, and
    coverage is what tells the two cases apart.
    """
    rows = series_of([(date(2025, m, 5), "1200") for m in (1, 3, 5, 7, 9)])
    assert only(rows).cadence_name == "bi-monthly"


def test_the_last_day_of_the_month_is_perfectly_regular():
    """31, 28, 31, 30, 31, 30 - the same instruction every time.

    Measured in days that is a spread of three, which is why the old table
    needed a ±6 tolerance around a nominal 30 - and that slack was then
    available to any two unrelated charges falling a month or so apart.
    """
    rows = series_of([(date(2025, 1, 31), "9000"), (date(2025, 2, 28), "9000"),
                      (date(2025, 3, 31), "9000"), (date(2025, 4, 30), "9000"),
                      (date(2025, 5, 31), "9000"), (date(2025, 6, 30), "9000")])
    found = only(rows)
    assert found.cadence_name == "monthly"
    assert found.missed == 0
    assert found.confidence > 0.9


def test_a_salary_that_slips_across_the_month_boundary_is_monthly():
    """Paid on the last working day, so it lands on 31 May one year and on
    2 June the next - two calendar months apart, one salary.

    Slotting by calendar month puts two charges in June and none in May, the
    fit collapses, and a four-weekly reading wins outright. Correcting for
    the boundary is the same thing `analytics.periods` does when it decides
    which month a payment is REPORTED in, and the two agreeing is what stops
    a salary being monthly for reporting and four-weekly for forecasting.
    """
    rows = series_of(
        [(d, "186500") for d in (
            date(2025, 6, 2), date(2025, 6, 30), date(2025, 7, 31),
            date(2025, 9, 1), date(2025, 9, 30), date(2025, 10, 31),
            date(2025, 12, 1), date(2025, 12, 31), date(2026, 2, 2),
            date(2026, 3, 2), date(2026, 3, 31), date(2026, 4, 30))],
        category=Category.SALARY, description="ACME PAYROLL CREDIT",
        direction=Direction.CREDIT)
    found = only(rows)
    assert found.cadence_name == "monthly", found.evidence
    assert found.missed == 0
    assert found.confidence > 0.8
    # And the next one is due at the end of THIS month, not the next.
    assert found.next_expected.month == 5


def test_a_four_weekly_subscription_is_not_monthly():
    """Thirteen charges a year, drifting two days earlier each time."""
    start = date(2025, 1, 6)
    rows = series_of([(start + timedelta(days=28 * i), "499")
                      for i in range(9)])
    assert only(rows).cadence_name == "four-weekly"


def test_weekly_and_fortnightly_are_measured_in_days():
    start = date(2025, 1, 6)
    weekly = series_of([(start + timedelta(days=7 * i), "350")
                        for i in range(10)], category=Category.DINING,
                       description="CORNER CAFE")
    assert only(weekly).cadence_name == "weekly"

    fortnightly = series_of([(start + timedelta(days=14 * i), "700")
                             for i in range(8)], category=Category.DINING,
                            description="CORNER CAFE")
    assert only(fortnightly).cadence_name == "fortnightly"


def test_a_yearly_premium_needs_only_three_years():
    rows = series_of([(date(y, 3, 14), "18400") for y in (2023, 2024, 2025)],
                     category=Category.INSURANCE,
                     description="LIC PREMIUM TERM COVER")
    found = only(rows)
    assert found.cadence_name == "yearly"
    assert to_monthly(found.median_amount, "yearly", 365) \
        == Decimal("1533.33")


def test_scattered_shop_visits_are_not_a_series():
    """Five visits over fourteen months at no particular interval.

    Every one of the four things the fit scores says no: the dates keep no
    day of the month, two thirds of the periods are empty, and the amounts
    are all over the place. The rows are not lost - with no series claiming
    them they fall to the variable side of the budget, which is where
    irregular spending belongs.
    """
    rows = series_of([(date(2025, 9, 8), "3400"), (date(2025, 11, 27), "1200"),
                      (date(2026, 1, 3), "5100"), (date(2026, 4, 19), "2600"),
                      (date(2026, 7, 30), "3900")],
                     category=Category.SHOPPING,
                     description="NORTHGATE RETAIL")
    assert detect_recurring(rows) == []


def test_two_charges_are_never_a_series():
    rows = series_of([(date(2025, 1, 5), "500"), (date(2025, 2, 5), "500")])
    assert detect_recurring(rows) == []


def test_everything_on_one_day_is_not_a_rhythm():
    """Three charges to the same payee on the same afternoon."""
    rows = series_of([(date(2025, 5, 5), "500")] * 3)
    assert detect_recurring(rows) == []


# --------------------------------------------------------------------------
# Amounts: a price that changed is still one charge
# --------------------------------------------------------------------------

def test_a_price_rise_keeps_the_series_and_reports_the_new_price():
    """199 for four months, then 649.

    A flat variance gate rejected this outright, so a subscription that put
    its price up stopped being a subscription - and the Budget tab silently
    lost the commitment at the moment it got more expensive.
    """
    rows = series_of([(date(2025, m, 5), a) for m, a in
                      [(1, "199"), (2, "199"), (3, "199"), (4, "199"),
                       (5, "649"), (6, "649"), (7, "649"), (8, "649")]])
    found = only(rows)
    assert found.amount_trend == "rose"
    assert found.median_amount == Decimal("649"), \
        "next month's bill is 649, not the lifetime median"
    assert found.lifetime_median == Decimal("424")
    assert found.changed_on == date(2025, 5, 5)


def test_the_change_date_is_where_the_price_actually_moved():
    """The cut has to separate the two levels cleanly.

    Cutting after the second charge passes a median-deviation test - four of
    the six charges on the "after" side sit exactly on its median, so the MAD
    is zero - and reported the rise two months before it happened.
    """
    rows = series_of([(date(2025, m, 5), a) for m, a in
                      [(1, "199"), (2, "199"), (3, "199"), (4, "199"),
                       (5, "649"), (6, "649"), (7, "649"), (8, "649")]])
    assert only(rows).changed_on == date(2025, 5, 5)


def test_a_steady_drift_survives_too():
    """A loan's interest component falls every month for twenty years.

    Judged against a flat median it eventually fails any tolerance, so the
    largest single line in a long-running loan was never a series at all.
    """
    amounts = [Decimal("28400") - Decimal(600) * i for i in range(10)]
    rows = series_of(
        [(date(2025, i + 1, 6), str(a)) for i, a in enumerate(amounts)],
        category=Category.LOAN_INTEREST, description="HOME LOAN EMI INT")
    found = only(rows)
    assert found.amount_trend == "drifting"
    # The going-forward figure comes from the recent charges, not the middle
    # of a sequence that is still falling.
    assert found.median_amount < found.lifetime_median


def test_noise_is_still_rejected_as_noise():
    """Two levels are a price change; five levels are not the same charge."""
    rows = series_of([(date(2025, m, 5), a) for m, a in
                      [(1, "200"), (2, "9000"), (3, "450"), (4, "12000"),
                       (5, "700"), (6, "15000")]])
    assert detect_recurring(rows) == []


def test_the_amount_tolerance_follows_the_category():
    """An EMI is the same to the paisa; an electricity bill triples.

    One flat tolerance could only ever be wrong in one direction or the
    other, and it was wrong in both - it admitted scattered shop visits and
    threw away genuine utility bills.
    """
    assert AMOUNT_TOLERANCE[Category.EMI] < AMOUNT_TOLERANCE[Category.UTILITIES]

    swings = ["1250", "1400", "2100", "3600", "4500", "4200", "2600", "1900"]
    power = series_of([(date(2025, i + 1, 17), a)
                       for i, a in enumerate(swings)],
                      category=Category.UTILITIES,
                      description="BESCOM ELECTRICITY")
    assert only(power).cadence_name == "monthly"

    # The same swings against an EMI's tolerance are not one charge.
    emi = series_of([(date(2025, i + 1, 17), a) for i, a in enumerate(swings)],
                    category=Category.EMI, description="SOMELENDER LOAN EMI")
    assert detect_recurring(emi) == []


def test_one_double_charge_does_not_destroy_a_series():
    """A month billed twice is a billing error, not a different charge."""
    amounts = ["499"] * 7
    amounts[3] = "998"
    rows = series_of([(date(2025, i + 1, 9), a)
                      for i, a in enumerate(amounts)])
    assert only(rows).median_amount == Decimal("499")


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------

def test_a_subscription_follows_a_replaced_card():
    """The old card stops, a new one starts, and neither half is believable
    alone. They are one commitment, and the user never cancelled anything."""
    old = monthly(11, "649", 4, start=(2025, 1), account="card-old")
    new = monthly(11, "649", 4, start=(2025, 5), account="card-new")
    found = only(old + new)
    assert found.occurrences == 8
    assert len(found.account_ids) == 2
    assert any("followed a replaced card" in note for note in found.evidence)


def test_two_accounts_paying_the_same_rent_stay_two_commitments():
    """Two housemates, one landlord, two standing instructions.

    This is the case the merge must NOT touch. Joining them would put two
    charges in every period, which destroys the fit and loses both series -
    so overlapping date ranges are disqualifying.
    """
    a = monthly(4, "21000", 8, account="bank-a", category=Category.RENT,
                description="NEFT RENT HARBOUR VIEW")
    b = monthly(4, "21000", 8, account="bank-b", category=Category.RENT,
                description="NEFT RENT HARBOUR VIEW")
    found = detect_recurring(a + b)
    assert len(found) == 2, [(s.account_id, s.occurrences) for s in found]
    assert all(s.occurrences == 8 for s in found)


def test_the_emi_offer_marker_does_not_split_a_merchant_in_two():
    """The issuer prints "EMI" on some months' charges and not others,
    depending on whether it felt like advertising. Left in the signature that
    is two series of four rather than one of eight - and neither half is
    believable on its own."""
    rows = monthly(21, "2400", 4, start=(2025, 1),
                   description="RIDERS CHOICE", category=Category.SHOPPING)
    rows += monthly(21, "2400", 4, start=(2025, 5),
                    description="22:01 EMI RIDERS CHOICE",
                    category=Category.SHOPPING)
    found = only(rows)
    assert found.occurrences == 8


def test_a_mirror_leg_does_not_become_its_own_series():
    rows = monthly(5, "42000", 6, category=Category.RENT,
                   description="NEFT RENT")
    mirrors = monthly(5, "42000", 6, category=Category.RENT,
                      description="NEFT RENT", account="a2")
    for row in mirrors:
        row.is_mirror_leg = True
    assert only(rows + mirrors).account_id == "a1"


def test_an_excluded_row_takes_no_part():
    rows = monthly(5, "42000", 6, category=Category.RENT,
                   description="NEFT RENT")
    rows[2].excluded = True
    found = only(rows)
    assert found.occurrences == 5
    assert found.missed == 1


# --------------------------------------------------------------------------
# Status and projection
# --------------------------------------------------------------------------

def test_a_series_that_stopped_is_reported_as_ended():
    rows = monthly(5, "649", 6, start=(2025, 1))
    # Something else in the ledger, months later, so "now" is well past it.
    rows += series_of([(date(2025, 11, d), "120") for d in (3, 10, 17, 24)],
                      category=Category.DINING, description="CORNER CAFE")
    ended = next(s for s in detect_recurring(rows) if "STREAMLINE" in s.label)
    assert ended.status == "ended"
    assert ended.is_active is False


def test_one_missed_charge_is_overdue_rather_than_finished():
    rows = monthly(5, "649", 6, start=(2025, 1))
    rows += series_of([(date(2025, 7, 20), "120")], category=Category.DINING,
                      description="CORNER CAFE")
    found = next(s for s in detect_recurring(rows) if "STREAMLINE" in s.label)
    assert found.status == "overdue"
    assert found.is_active is True, \
        "one late charge is not evidence a standing instruction was cancelled"


def test_an_account_whose_statements_lag_does_not_look_abandoned():
    """A card uploaded to March and a bank uploaded to September.

    Judged against the whole ledger's end date, every series on the card
    reads as cancelled six months ago - which is a fact about which files
    have been imported, not about the user's subscriptions.
    """
    card = monthly(11, "649", 3, start=(2025, 1), account="card-1")
    bank = monthly(4, "42000", 9, start=(2025, 1), account="bank-1",
                   category=Category.RENT, description="NEFT RENT")
    found = {s.account_id: s for s in detect_recurring(card + bank)}
    assert found["card-1"].status == "active"
    assert found["card-1"].is_active is True


def test_the_next_date_lands_on_the_day_the_series_uses():
    """Stepping by a nominal 30 days drifts a day earlier every month and,
    across a year, predicts the wrong week.

    The last day of the month is the case worth pinning: this series reads as
    31, 28, 31, 30, 31, 30, and the answer is the last day of July - not
    whichever of those numbers happened to be the median.
    """
    rows = monthly(31, "9000", 6, start=(2025, 1))
    assert only(rows).next_expected == date(2025, 7, 31)

    # A charge genuinely dated the 12th keeps the 12th, in every month.
    assert only(monthly(12, "9000", 6)).next_expected == date(2025, 7, 12)


# --------------------------------------------------------------------------
# The parts, on their own
# --------------------------------------------------------------------------

def test_fit_cadence_prefers_the_reading_with_fewer_empty_periods():
    dates = [date(2025, m, 5) for m in (1, 2, 3, 4, 5, 6)]
    fit = fit_cadence(dates)
    assert fit.candidate.name == "monthly"
    assert fit.coverage == 1.0
    assert fit.collisions == 0
    assert fit.score > 0.9


def test_model_amounts_calls_a_flat_series_flat():
    amounts = [Decimal("499")] * 6
    dates = [date(2025, m, 5) for m in range(1, 7)]
    model = model_amounts(amounts, dates, 0.15)
    assert model.stable and model.trend == "flat"
    assert model.typical == Decimal("499")


def test_confidence_never_reaches_the_floor_on_three_charges():
    """Any three dates can be fitted by SOME cadence with no gaps in it.

    Three occurrences is the minimum this will look at and it is thin
    evidence, so the maturity discount keeps such a series well below what
    the Budget tab will call a fixed cost - it is worth showing, not worth
    building a monthly figure on.
    """
    rows = series_of([(date(2025, m, 5), "1200") for m in (1, 2, 3)])
    found = only(rows)
    assert MIN_CONFIDENCE <= found.confidence < 0.6


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------

def test_a_series_survives_storage_with_its_reasoning(tenant):
    """The table used to hold a verdict and nothing about how it was reached,
    so a series read back could not be explained or told apart from one the
    detector was only half sure of."""
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType
    from tests.support import fresh_ledger

    db = fresh_ledger()
    # The series carries a foreign key to the account it is charged to.
    repo.upsert_account(db, Account(id="a1", institution="HDFC",
                                    account_type=AccountType.CREDIT_CARD,
                                    account_number_masked="9931"))
    detected = only(series_of([(date(2025, m, 5), a) for m, a in
                               [(1, "199"), (2, "199"), (3, "199"),
                                (4, "199"), (5, "649"), (6, "649"),
                                (7, "649"), (8, "649")]]))
    assert repo.save_recurring_series(db, [detected]) == 1

    stored = repo.get_recurring_series(db)
    assert len(stored) == 1
    row = stored[0]
    assert row["cadence_name"] == "monthly"
    assert row["median_amount"] == Decimal("649.00")
    assert row["lifetime_median"] == Decimal("424.00")
    assert row["amount_trend"] == "rose"
    assert row["changed_on"] == "2025-05-05"
    assert row["status"] == "active"
    assert row["coverage"] == 1.0
    assert row["day_of_month"] == 5
    assert isinstance(row["evidence"], list) and row["evidence"]


def test_the_api_ships_the_reasoning_too(tenant):
    from app.api.serializers import recurring_json

    detected = only(monthly(5, "42000", 8, category=Category.RENT,
                            description="NEFT RENT HARBOUR VIEW"))
    payload = recurring_json(detected)
    for key in ("status", "coverage", "missed", "day_of_month",
                "amount_trend", "lifetime_median", "last_amount", "evidence"):
        assert key in payload, key
    assert payload["cadence"] == "monthly"
    assert payload["monthly_equivalent"] == 42000.0


def test_the_recurring_endpoint_serves_the_monthly_figure_itself(tenant):
    """The client used to work this out, and got it 1.5% wrong.

    Its fallback divided 30.44 days by a nominal 30-day cadence, so rent of a
    flat 41,500 was rendered as 42,109 a month - and the Recurring tab adds
    fourteen commitments up. The conversion has one home, in
    analytics.recurring, and this is the seam that was letting a second copy
    exist.
    """
    from fastapi.testclient import TestClient

    from app.db import repository as repo
    from app.main import app
    from app.models.schemas import Account, AccountType
    from tests.support import fresh_ledger

    db = fresh_ledger()
    repo.upsert_account(db, Account(id="a1", institution="HDFC",
                                    account_type=AccountType.SAVINGS,
                                    account_number_masked="4412"))
    repo.save_recurring_series(db, [only(monthly(
        4, "41500", 8, category=Category.RENT,
        description="NEFT RENT HARBOUR VIEW"))])

    rows = TestClient(app).get("/api/recurring").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["monthly_equivalent"] == 41500.0, \
        "rent of a flat 41,500 costs 41,500 a month"
    # And both names the frontend has ever read are present, so a component
    # cannot render zero by picking the wrong one.
    assert row["amount"] == 41500.0 and row["median_amount"] == 41500.0
    assert row["cadence"] == "monthly" and row["cadence_name"] == "monthly"


def test_a_quarterly_premium_is_normalised_by_its_own_cadence(tenant):
    from fastapi.testclient import TestClient

    from app.db import repository as repo
    from app.main import app
    from app.models.schemas import Account, AccountType
    from tests.support import fresh_ledger

    db = fresh_ledger()
    repo.upsert_account(db, Account(id="a1", institution="HDFC",
                                    account_type=AccountType.SAVINGS,
                                    account_number_masked="4412"))
    quarterly = series_of([(date(2025, m, 14), "9000") for m in (1, 4, 7, 10)],
                          category=Category.INSURANCE,
                          description="LIC PREMIUM TERM COVER")
    detected = only(quarterly)
    assert detected.cadence_name == "quarterly"
    repo.save_recurring_series(db, [detected])

    row = TestClient(app).get("/api/recurring").json()[0]
    assert row["monthly_equivalent"] == 3000.0, "a quarter of 9,000 a month"
