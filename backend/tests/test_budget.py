"""What a month costs, and what it leaves.

The figures on the Budget tab are the ones people act on - "can I afford
this?" is decided by the headroom line - so the things worth pinning down are
the ones that would flatter or frighten someone if they were wrong:

  * a SIP must not be counted as an expense (it makes a saver look reckless)
  * a card bill must not be counted on top of the purchases it settles
  * the same rupee must not appear as both a commitment and variable spending
  * "typical" must be the median month, not the mean, or one holiday sets the
    expectation for every month after it
  * a loan's end date must come from its own amortization, not from a guess
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.analytics import periods
from app.analytics.budget import analyse_budget
from app.analytics.recurring import detect_recurring
from app.main import app
from app.models.schemas import (Account, AccountType, Category, Direction,
                                FlowRole, Transaction)

from .support import fresh_ledger

client = TestClient(app)

BANK = "bank-1"
CARD = "card-1"


def _txn(day: date, amount: str, category, *, direction=Direction.DEBIT,
         role=None, account=BANK, description=None, txn_id=None):
    return Transaction(
        id=txn_id or f"{account}-{day.isoformat()}-{amount}-{category}",
        account_id=account, txn_date=day,
        raw_description=description or str(category).upper(),
        normalized_description=description or str(category),
        merchant=description or str(category).title(),
        amount=Decimal(amount), direction=direction, category=category,
        flow_role=(role or _default_role(category, direction)).value,
        accounting_month=periods.month_key(day),
    )


def _default_role(category, direction) -> FlowRole:
    if direction == Direction.CREDIT:
        return FlowRole.INCOME
    if category == Category.INVESTMENT:
        return FlowRole.INVESTMENT
    if category == Category.EMI:
        return FlowRole.TRANSFER_OUT
    return FlowRole.EXPENSE


#: Sep 2025 to Aug 2026 inclusive - twelve whole months, so a window over any
#: one of them has something in it. Stepped by month rather than by 30 days:
#: adding 30 days twelve times lands on October twice and never reaches
#: August, which is a fixture that quietly proves less than it claims.
FIXTURE_MONTHS = [(2025, m) for m in range(9, 13)] + [(2026, m) for m in range(1, 9)]


def _twelve_months(**amounts):
    """A year of statements: salary, and whatever charges are asked for.

    Every charge lands on the same day of each month, which is what makes it a
    detectable series - three occurrences at a steady interval.
    """
    rows = []
    for year, month in FIXTURE_MONTHS:
        first = date(year, month, 1)
        rows.append(_txn(first + timedelta(days=27), "185000", Category.SALARY,
                         direction=Direction.CREDIT, description="SALARY CREDIT"))
        for (category, amount, description, day) in amounts.get("charges", []):
            rows.append(_txn(first + timedelta(days=day), amount, category,
                             description=description))
    for (day, amount, category, description) in amounts.get("one_offs", []):
        rows.append(_txn(day, amount, category, description=description))
    return rows


ACCOUNTS = {
    BANK: Account(id=BANK, institution="HDFC", account_type=AccountType.SAVINGS,
                  account_number_masked="4412"),
    CARD: Account(id=CARD, institution="HSBC",
                  account_type=AccountType.CREDIT_CARD,
                  account_number_masked="9931"),
}


def _budget(rows, period=None, loans=None):
    # `detect_recurring` measures "is this still running?" against the latest
    # transaction in the ledger rather than against the wall clock (see its
    # own comment on why), so these fixtures do not drift out of active as
    # real time passes.
    series = detect_recurring(rows)
    return analyse_budget(rows, series, period=period, loans=loans or [],
                          accounts=ACCOUNTS, today=date(2026, 9, 3))


# --------------------------------------------------------------------------
# What is committed
# --------------------------------------------------------------------------

def test_a_recurring_charge_becomes_a_commitment():
    rows = _twelve_months(charges=[
        (Category.RENT, "42000", "RENT PAYMENT NEFT", 4),
    ])
    result = _budget(rows)
    labels = [c.label for c in result.commitments]
    assert any("RENT" in label for label in labels)
    rent = next(c for c in result.commitments if "RENT" in c.label)
    assert rent.kind == "spending"
    assert rent.monthly == Decimal("42616.00"), "42,000 every 30 days"
    assert rent.months_seen == 12


def test_a_sip_is_committed_but_is_not_a_cost():
    """Counting investment as spending makes a diligent saver look reckless."""
    rows = _twelve_months(charges=[
        (Category.INVESTMENT, "25000", "SIP AXIS BLUECHIP", 8),
    ])
    result = _budget(rows)
    sip = next(c for c in result.commitments if "SIP" in c.label)
    assert sip.kind == "saving"
    assert result.committed_saving > 0
    assert result.committed_spending == 0
    # It is spoken for, so it counts against income...
    assert result.committed_total == result.committed_saving
    # ...but it is not what a month COSTS, because the money is still theirs.
    assert result.monthly_cost == 0


def test_an_emi_is_debt_and_carries_its_end_date():
    rows = _twelve_months(charges=[
        (Category.EMI, "38500", "HDFC HOME LOAN EMI PRIN", 6),
    ])

    class _Projection:
        account_id = BANK
        emi = Decimal("38500")
        payoff_date = date(2041, 5, 1)
        months_remaining = 176

    result = _budget(rows, loans=[_Projection()])
    emi = next(c for c in result.commitments if "EMI" in c.label)
    assert emi.kind == "debt"
    assert emi.ends_on == date(2041, 5, 1)
    assert emi.months_left == 176
    assert result.committed_debt == emi.monthly


def test_a_loan_whose_emi_is_nothing_like_the_charge_is_not_its_loan():
    """One account can carry several loans; the wrong one is worse than none."""
    rows = _twelve_months(charges=[
        (Category.EMI, "38500", "HDFC HOME LOAN EMI PRIN", 6),
    ])

    class _OtherLoan:
        account_id = "somewhere-else"
        emi = Decimal("2500")
        payoff_date = date(2027, 1, 1)
        months_remaining = 4

    result = _budget(rows, loans=[_OtherLoan()])
    emi = next(c for c in result.commitments if "EMI" in c.label)
    assert emi.ends_on is None, "a 2,500 EMI does not schedule a 38,500 charge"


def test_the_card_bill_is_not_a_commitment_of_its_own():
    """Its purchases are already counted one by one, line by line."""
    rows = _twelve_months(charges=[
        (Category.CC_PAYMENT, "31000", "CREDIT CARD PAYMENT", 20),
        (Category.DINING, "1400", "SWIGGY ORDER", 13),
    ])
    result = _budget(rows)
    assert not any("CARD PAYMENT" in c.label for c in result.commitments)


def test_a_commitment_and_the_variable_side_never_claim_the_same_rupee():
    rows = _twelve_months(charges=[
        (Category.GROCERIES, "6200", "BIGBASKET ORDER", 9),
    ])
    result = _budget(rows)
    assert any("BIGBASKET" in c.label for c in result.commitments)
    # Every groceries row belongs to that series, so nothing is left to be
    # counted again as variable spending.
    assert not any(v.category == Category.GROCERIES for v in result.variable)


# --------------------------------------------------------------------------
# What varies
# --------------------------------------------------------------------------

def test_typical_is_the_middle_month_not_the_average():
    """One holiday must not become the monthly expectation."""
    rows = _twelve_months()
    for i, amount in enumerate(["2000", "2200", "2100", "180000"]):
        month = date(2026, 1 + i, 12)
        rows.append(_txn(month, amount, Category.TRAVEL,
                         description=f"TRAVEL {i}"))
    result = _budget(rows)
    travel = next(v for v in result.variable if v.category == Category.TRAVEL)
    assert travel.typical_monthly == Decimal("2150.00"), "median of the four"
    assert travel.high_monthly == Decimal("180000.00"), "and the worst is shown"
    assert travel.months_seen == 4


def test_a_category_present_every_month_is_flagged_as_effectively_fixed():
    """Groceries recur even when no single merchant does."""
    rows = _twelve_months()
    # A genuinely different merchant every month, so no series is detected -
    # the detector's signature strips digits, so "KIRANA 1" and "KIRANA 2"
    # would have been one series with two names.
    shops = ["BIGBASKET", "DMART", "RELIANCE FRESH", "ZEPTO", "BLINKIT",
             "SPENCERS", "MORE SUPERMARKET", "NATURES BASKET", "STAR BAZAAR",
             "LOCAL KIRANA", "METRO CASH", "JIOMART"]
    for i, (year, month) in enumerate(FIXTURE_MONTHS):
        first = date(year, month, 1)
        rows.append(_txn(first + timedelta(days=9), f"{6000 + i * 50}",
                         Category.GROCERIES, description=shops[i]))
    result = _budget(rows)
    groceries = next(v for v in result.variable
                     if v.category == Category.GROCERIES)
    assert groceries.every_month is True
    assert groceries.months_seen == result.months


def test_money_back_reduces_the_category_it_came_back_against():
    rows = _twelve_months()
    rows.append(_txn(date(2026, 4, 10), "5000", Category.SHOPPING,
                     description="BIG PURCHASE"))
    rows.append(_txn(date(2026, 4, 27), "2000", Category.SHOPPING,
                     direction=Direction.CREDIT, role=FlowRole.REFUND,
                     description="BIG PURCHASE REFUND"))
    result = _budget(rows)
    shopping = next(v for v in result.variable
                    if v.category == Category.SHOPPING)
    assert shopping.total == Decimal("3000.00"), "5,000 out, 2,000 back"


# --------------------------------------------------------------------------
# The arithmetic between them
# --------------------------------------------------------------------------

def test_the_month_adds_up():
    rows = _twelve_months(charges=[
        (Category.RENT, "42000", "RENT PAYMENT NEFT", 4),
        (Category.EMI, "38500", "HDFC HOME LOAN EMI PRIN", 6),
        (Category.INVESTMENT, "25000", "SIP AXIS BLUECHIP", 8),
    ])
    rows.append(_txn(date(2026, 5, 12), "3000", Category.DINING,
                     description="ONE BIG DINNER"))
    result = _budget(rows)

    assert result.income_typical == Decimal("185000.00")
    assert result.committed_total == (result.committed_debt
                                      + result.committed_spending
                                      + result.committed_saving)
    # A month costs what leaves for good, plus the typical variable spend.
    assert result.monthly_cost == (result.committed_debt
                                   + result.committed_spending
                                   + result.variable_typical)
    # ...and what is left is measured after the saving as well, because that
    # money is not available to spend either.
    assert result.headroom == (result.income_typical - result.monthly_cost
                               - result.committed_saving)
    assert 0 < result.committed_ratio < 100


def test_a_window_uses_the_charges_in_it():
    """Rent that went up should read as the new rent for a recent window."""
    rows = _twelve_months()
    for year, month in FIXTURE_MONTHS:
        first = date(year, month, 1)
        amount = "42000" if first < date(2026, 6, 1) else "50000"
        rows.append(_txn(first + timedelta(days=4), amount, Category.RENT,
                         description="RENT PAYMENT NEFT"))

    recent = periods.resolve_period(
        {"preset": "custom_months", "start_month": "2026-06",
         "end_month": "2026-08"})
    after = _budget(rows, period=recent)
    rent_after = next(c for c in after.commitments if "RENT" in c.label)
    assert rent_after.monthly > Decimal("50000") * Decimal("0.99")

    whole = _budget(rows)
    rent_all = next(c for c in whole.commitments if "RENT" in c.label)
    assert rent_all.monthly < rent_after.monthly, \
        "the lifetime median is the older, lower rent"


def test_a_one_month_window_says_a_typical_month_cannot_be_established():
    rows = _twelve_months(charges=[
        (Category.RENT, "42000", "RENT PAYMENT NEFT", 4),
    ])
    august = periods.resolve_period(
        {"preset": "custom_months", "start_month": "2026-08",
         "end_month": "2026-08"})
    result = _budget(rows, period=august)
    assert result.months == 1
    assert any("typical month cannot be established" in n for n in result.notes)
    assert any("appeared only once" in n for n in result.notes)


def test_spending_more_than_you_earn_is_stated_rather_than_left_to_arithmetic():
    rows = []
    for i in range(6):
        first = date(2026, 1 + i, 1)
        rows.append(_txn(first + timedelta(days=27), "40000", Category.SALARY,
                         direction=Direction.CREDIT, description="SALARY CREDIT"))
        rows.append(_txn(first + timedelta(days=4), "60000", Category.RENT,
                         description="RENT PAYMENT NEFT"))
    result = _budget(rows)
    assert result.headroom < 0
    assert any("costs more than" in n for n in result.notes)


def test_an_empty_window_budgets_nothing_and_says_so():
    rows = _twelve_months()
    result = _budget(rows, period=periods.resolve_period(
        {"preset": "custom_months", "start_month": "2020-01",
         "end_month": "2020-02"}))
    assert result.months == 0
    assert result.commitments == [] and result.variable == []
    assert result.notes


# --------------------------------------------------------------------------
# Over the wire
# --------------------------------------------------------------------------

def _seed():
    from app.db import repository as repo

    db = fresh_ledger()
    ids = {}
    for key, account in ACCOUNTS.items():
        stored = Account(**{**account.model_dump(), "id": None})
        ids[key] = repo.upsert_account(db, stored)

    rows = _twelve_months(charges=[
        (Category.RENT, "42000", "RENT PAYMENT NEFT", 4),
        (Category.INVESTMENT, "25000", "SIP AXIS BLUECHIP", 8),
    ])
    saved = []
    for row in rows:
        copy = row.model_copy()
        copy.account_id = ids[BANK]
        copy.fingerprint = copy.id
        saved.append(copy)
    repo.save_transactions(db, saved)
    return db


def test_the_endpoint_answers_what_a_month_costs():
    _seed()
    body = client.get("/api/budget", params={"preset": "all"}).json()
    assert body["status"] == "ok"
    assert body["totals"]["income_typical"] == 185000.0
    kinds = {c["kind"] for c in body["commitments"]}
    assert "saving" in kinds and "spending" in kinds
    # The saving is spoken for but is not part of what the month costs.
    assert body["totals"]["committed_saving"] > 0
    assert body["totals"]["monthly_cost"] < body["totals"]["committed_total"]


def test_the_endpoint_scopes_to_the_period_it_is_given():
    _seed()
    august = client.get("/api/budget", params={
        "start_month": "2026-08", "end_month": "2026-08"}).json()
    assert august["months"] == 1
    assert all(c["months_seen"] <= 1 for c in august["commitments"])
    assert august["range"]["label"] == "Aug 2026"


def test_the_endpoint_refuses_a_nonsense_period():
    _seed()
    assert client.get("/api/budget",
                      params={"preset": "whenever"}).status_code == 400


def test_an_empty_workspace_says_so_rather_than_reporting_zeroes():
    fresh_ledger()
    body = client.get("/api/budget", params={"preset": "all"}).json()
    assert body["status"] == "empty"
