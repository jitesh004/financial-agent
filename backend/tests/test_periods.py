"""Selecting a reporting period.

The rule under test everywhere below: a period names whole ACCOUNTING months,
and rows are selected by the month the ledger counts them in - not by the
month printed on the date. A salary paid on 31 August and the next one paid on
1 September are August's and September's pay; a window that says "August" has
to contain exactly one of them, whichever side of the boundary it landed on.

That is the same rule the Months tab has always applied. These tests exist
because it is now applied by every screen, through one implementation, and
"one implementation" is only true while something checks.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.analytics import periods as p
from app.analytics.engine import analyze
from app.main import app
from app.models.schemas import Category, Direction, Transaction

from .support import fresh_ledger

client = TestClient(app)

TODAY = date(2026, 3, 15)


def _txn(day: date, amount: str, direction: Direction, category=Category.DINING,
         accounting_month: str | None = None) -> Transaction:
    return Transaction(
        id=f"{day.isoformat()}-{amount}-{category}",
        account_id="acct", txn_date=day, raw_description=str(category),
        amount=Decimal(amount), direction=direction, category=category,
        accounting_month=accounting_month or p.month_key(day),
    )


# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------

@pytest.mark.parametrize("preset,first,last", [
    ("this_month", "2026-03", "2026-03"),
    ("last_month", "2026-02", "2026-02"),
    ("last_3m", "2026-01", "2026-03"),
    ("last_6m", "2025-10", "2026-03"),
    ("last_12m", "2025-04", "2026-03"),
    ("ytd", "2026-01", "2026-03"),
    ("last_year", "2025-01", "2025-12"),
    ("this_fy", "2025-04", "2026-03"),
    ("last_fy", "2024-04", "2025-03"),
])
def test_every_preset_resolves_to_whole_months(preset, first, last):
    period = p.resolve_period({"preset": preset}, TODAY)
    assert (period.mode, period.start_month, period.end_month) \
        == ("months", first, last)


def test_all_time_filters_nothing():
    period = p.resolve_period({"preset": "all"}, TODAY)
    assert period.is_all and period.bounds() == (None, None)
    assert p.filter_transactions(
        [_txn(date(1999, 1, 1), "1", Direction.DEBIT)], period)


def test_the_financial_year_turns_over_in_april():
    """April to March, which is the year every Indian statement is filed on."""
    assert p.resolve_period({"preset": "this_fy"}, date(2026, 3, 31)).start_month \
        == "2025-04"
    assert p.resolve_period({"preset": "this_fy"}, date(2026, 4, 1)).start_month \
        == "2026-04"
    # "So far" stops at the current month rather than running on to a March
    # that has not happened yet.
    assert p.resolve_period({"preset": "this_fy"}, date(2026, 4, 1)).end_month \
        == "2026-04"


def test_a_preset_that_does_not_exist_is_refused():
    with pytest.raises(p.PeriodError):
        p.resolve_period({"preset": "since_forever"}, TODAY)


def test_month_arithmetic_crosses_year_boundaries():
    assert p.shift_month_key("2026-01", -1) == "2025-12"
    assert p.shift_month_key("2025-12", 1) == "2026-01"
    assert p.shift_month_key("2026-03", -14) == "2025-01"
    assert p.months_between("2025-11", "2026-02") == \
        ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert p.month_end_date("2024-02") == date(2024, 2, 29), "leap year"
    assert p.month_end_date("2026-12") == date(2026, 12, 31)


# --------------------------------------------------------------------------
# Custom windows
# --------------------------------------------------------------------------

def test_a_custom_month_window_is_accounting_months():
    period = p.resolve_period(
        {"preset": "custom_months", "start_month": "2025-11", "end_month": "2026-01"})
    assert period.mode == "months" and period.month_count == 3
    assert period.as_json()["basis"] == "accounting"


def test_a_custom_date_window_is_literal_dates():
    period = p.resolve_period(
        {"preset": "custom", "start": "2026-02-03", "end": "2026-02-20"})
    assert period.mode == "dates" and period.as_json()["basis"] == "date"
    assert period.bounds() == (date(2026, 2, 3), date(2026, 2, 20))


def test_a_window_drawn_backwards_is_read_forwards():
    """Picking the end month first should not select nothing."""
    period = p.resolve_period(
        {"preset": "custom_months", "start_month": "2026-05", "end_month": "2026-02"})
    assert (period.start_month, period.end_month) == ("2026-02", "2026-05")
    dates = p.resolve_period(
        {"preset": "custom", "start": "2026-05-09", "end": "2026-02-01"})
    assert dates.bounds() == (date(2026, 2, 1), date(2026, 5, 9))


def test_a_half_finished_custom_window_shows_everything():
    """A range with neither end typed yet is not a request for no rows."""
    assert p.resolve_period({"preset": "custom"}).is_all
    assert p.resolve_period({"preset": "custom_months"}).is_all


def test_one_open_end_stays_open():
    until = p.resolve_period({"preset": "custom", "end": "2026-02-20"})
    assert until.start is None and until.end == date(2026, 2, 20)
    assert until.contains(_txn(date(2020, 1, 1), "1", Direction.DEBIT))
    assert not until.contains(_txn(date(2026, 3, 1), "1", Direction.DEBIT))


def test_a_month_given_where_a_date_was_expected_is_read_as_a_month():
    """The pickers send one pair of parameters; both shapes arrive in them."""
    period = p.resolve_period({"preset": "custom_months",
                               "start": "2026-02", "end": "2026-03"})
    assert (period.mode, period.start_month, period.end_month) \
        == ("months", "2026-02", "2026-03")


def test_a_malformed_month_is_refused_rather_than_guessed():
    with pytest.raises(p.PeriodError):
        p.resolve_period({"preset": "custom_months", "start_month": "2026-13"})
    with pytest.raises(p.PeriodError):
        p.resolve_period({"preset": "custom_months", "start_month": "last august"})
    with pytest.raises(p.PeriodError):
        p.resolve_period({"preset": "custom", "start": "the 3rd"})


# --------------------------------------------------------------------------
# What a window selects
# --------------------------------------------------------------------------

#: Two salaries either side of a month boundary, the way they actually arrive:
#: 31 August is August's pay, 1 September is September's - and the period
#: engine has already recorded that on the rows.
SALARY_AUG = _txn(date(2026, 8, 31), "185000", Direction.CREDIT,
                  Category.SALARY, accounting_month="2026-08")
SALARY_SEP = _txn(date(2026, 9, 1), "185000", Direction.CREDIT,
                  Category.SALARY, accounting_month="2026-09")
#: ...and the case that makes the whole exercise necessary: a salary that
#: arrived in September and belongs to August.
SALARY_AUG_LATE = _txn(date(2026, 9, 1), "190000", Direction.CREDIT,
                       Category.SALARY, accounting_month="2026-08")
DINNER_AUG = _txn(date(2026, 8, 12), "845", Direction.DEBIT)


def test_a_month_window_follows_the_accounting_month():
    august = p.resolve_period(
        {"preset": "custom_months", "start_month": "2026-08", "end_month": "2026-08"})
    kept = p.filter_transactions([SALARY_AUG_LATE, SALARY_SEP, DINNER_AUG], august)
    assert SALARY_AUG_LATE in kept, "pay for August, whatever date it landed on"
    assert SALARY_SEP not in kept, "September's pay is not August's"
    assert DINNER_AUG in kept


def test_a_date_window_follows_the_date():
    """The other reading, kept available on purpose."""
    september = p.resolve_period(
        {"preset": "custom", "start": "2026-09-01", "end": "2026-09-30"})
    kept = p.filter_transactions([SALARY_AUG_LATE, SALARY_SEP, DINNER_AUG], september)
    assert SALARY_AUG_LATE in kept and SALARY_SEP in kept
    assert DINNER_AUG not in kept


def test_a_row_from_before_accounting_months_still_lands_in_a_month():
    """An un-reanalysed ledger must not vanish from every period.

    Rows imported before the period engine existed carry no accounting month.
    Selecting on the bare value would exclude every one of them from every
    window - the ledger would look empty rather than un-migrated.
    """
    legacy = Transaction(
        id="legacy", account_id="acct", txn_date=date(2026, 8, 4),
        raw_description="old row", amount=Decimal("100"),
        direction=Direction.DEBIT, accounting_month="")
    august = p.resolve_period(
        {"preset": "custom_months", "start_month": "2026-08", "end_month": "2026-08"})
    assert p.filter_transactions([legacy], august) == [legacy]
    assert p.effective_month(legacy) == "2026-08"


def test_the_previous_window_matches_the_one_before_it():
    three_months = p.resolve_period({"preset": "last_3m"}, TODAY)
    before = p.previous_period(three_months)
    assert (before.start_month, before.end_month) == ("2025-10", "2025-12")

    fortnight = p.resolve_period(
        {"preset": "custom", "start": "2026-02-15", "end": "2026-02-28"})
    assert p.previous_period(fortnight).bounds() == \
        (date(2026, 2, 1), date(2026, 2, 14))

    assert p.previous_period(p.ALL_TIME) is None


def test_labels_read_the_way_the_ui_prints_them():
    one = p.resolve_period({"preset": "last_month"}, TODAY)
    assert one.label() == "Feb 2026"
    many = p.resolve_period({"preset": "last_3m"}, TODAY)
    assert many.label() == "Jan 2026 – Mar 2026"
    assert p.ALL_TIME.label() == "All time"


# --------------------------------------------------------------------------
# The figures a window produces
# --------------------------------------------------------------------------

def test_a_month_window_counts_the_pay_it_is_owed():
    """August's income is August's salary, arriving on 1 September."""
    august = p.resolve_period(
        {"preset": "custom_months", "start_month": "2026-08", "end_month": "2026-08"})
    result = analyze([SALARY_AUG_LATE, SALARY_SEP, DINNER_AUG], {}, period=august)
    assert result.total_income == Decimal("190000.00")
    assert result.total_spend == Decimal("845.00")
    assert [m.month for m in result.monthly] == ["2026-08"]


def test_one_accounting_month_is_one_month_of_averages():
    """The divisor is months counted, not the calendar span of the dates.

    August's rows can run from 27 July to 1 September. Counting the span gives
    three months and divides every average on the screen by three.
    """
    august = p.resolve_period(
        {"preset": "custom_months", "start_month": "2026-08", "end_month": "2026-08"})
    result = analyze([SALARY_AUG_LATE, DINNER_AUG], {}, period=august)
    assert result.months_covered == 1
    assert result.average_monthly_income == Decimal("190000.00")


def test_an_empty_window_says_so_instead_of_reading_as_a_quiet_month():
    period = p.resolve_period(
        {"preset": "custom_months", "start_month": "2027-01", "end_month": "2027-01"})
    result = analyze([SALARY_AUG, DINNER_AUG], {}, period=period)
    assert result.transaction_count == 0
    assert result.notes and "Jan 2027" in result.notes[0]
    # The window it asked for, so the screen can name the empty period.
    assert result.period_start == date(2027, 1, 1)


def test_the_full_ledger_is_unaffected_by_the_period_machinery():
    """`period=None` and `preset=all` have to produce identical figures."""
    rows = [SALARY_AUG, SALARY_SEP, DINNER_AUG]
    assert analyze(rows, {}).total_income == \
        analyze(rows, {}, period=p.ALL_TIME).total_income


# --------------------------------------------------------------------------
# Over the wire
# --------------------------------------------------------------------------

def _seed_ledger():
    """A ledger with one late salary in it, saved the way an import saves."""
    from app.db import repository as repo

    db = fresh_ledger()
    account = _account()
    account_id = repo.upsert_account(db, account)
    rows = []
    for txn in (SALARY_AUG_LATE, SALARY_SEP, DINNER_AUG):
        row = txn.model_copy()
        row.account_id = account_id
        rows.append(row)
    repo.save_transactions(db, rows)
    return db


def _account():
    from app.models.schemas import Account, AccountType

    return Account(institution="Test Bank", account_type=AccountType.SAVINGS,
                   account_number_masked="0001")


def test_the_period_catalogue_is_resolved_server_side():
    """The picker renders from this; nothing about a preset is decided twice."""
    _seed_ledger()
    body = client.get("/api/periods").json()
    presets = {one["value"]: one for one in body["presets"]}
    assert presets["all"]["mode"] == "all"
    assert presets["last_3m"]["months"] == 3
    assert presets["last_3m"]["start_month"] < presets["last_3m"]["end_month"]
    assert presets["custom"]["basis"] == "date"
    assert presets["custom_months"]["basis"] == "accounting"
    # Only months the ledger actually holds rows in, so an empty window can
    # point at where the data is.
    assert [m["month"] for m in body["months"]] == ["2026-08", "2026-09"]
    assert (body["earliest"], body["latest"]) == ("2026-08", "2026-09")


def test_the_transactions_endpoint_selects_by_accounting_month():
    _seed_ledger()
    august = client.get("/api/transactions",
                        params={"start_month": "2026-08", "end_month": "2026-08"}).json()
    assert august["total"] == 2
    assert august["range"]["basis"] == "accounting"
    assert {t["accounting_month"] for t in august["transactions"]} == {"2026-08"}

    # The same days by date pull in September's salary and drop August's
    # dinner - the literal reading, which is what a date range is for.
    by_date = client.get("/api/transactions",
                         params={"start": "2026-09-01", "end": "2026-09-30"}).json()
    assert by_date["total"] == 2
    assert by_date["range"]["basis"] == "date"
    assert {t["date"] for t in by_date["transactions"]} == {"2026-09-01"}


def test_the_scoped_analysis_endpoint_agrees_with_the_months_tab():
    """One window, two screens, one set of figures.

    The Months tab lists rows from /api/transactions and totals them in the
    browser; the Overview reads /api/analysis. If those two disagree the app
    is reporting two different Augusts.
    """
    _seed_ledger()
    scoped = client.get("/api/analysis",
                        params={"start_month": "2026-08", "end_month": "2026-08"}).json()
    assert scoped["status"] == "ok"
    assert scoped["range"]["label"] == "Aug 2026"
    assert scoped["analysis"]["totals"]["income"] == 190000.0
    assert scoped["analysis"]["totals"]["transaction_count"] == 2

    rows = client.get("/api/transactions",
                      params={"start_month": "2026-08", "end_month": "2026-08",
                              "limit": 500}).json()["transactions"]
    listed = sum(r["amount"] for r in rows if r["flow_role"] == "income")
    assert listed == scoped["analysis"]["totals"]["income"]


def test_a_preset_can_be_asked_for_by_name():
    _seed_ledger()
    body = client.get("/api/analysis", params={"preset": "all"}).json()
    assert body["range"]["mode"] == "all"
    assert body["analysis"]["totals"]["transaction_count"] == 3
    assert body["available"] == {"earliest": "2026-08", "latest": "2026-09",
                                 "transaction_count": 3}


def test_an_empty_window_over_the_wire_reports_the_window_and_the_data():
    _seed_ledger()
    body = client.get("/api/analysis",
                      params={"start_month": "2020-01", "end_month": "2020-03"}).json()
    assert body["status"] == "ok"
    assert body["analysis"]["totals"]["transaction_count"] == 0
    assert body["range"]["label"] == "Jan 2020 – Mar 2020"
    # Where the ledger actually is, so the screen can offer to go there.
    assert body["available"]["latest"] == "2026-09"


def test_a_nonsense_period_is_a_bad_request_not_a_server_fault():
    _seed_ledger()
    assert client.get("/api/analysis", params={"preset": "whenever"}).status_code == 400
    assert client.get("/api/transactions",
                      params={"start_month": "2026-99"}).status_code == 400
