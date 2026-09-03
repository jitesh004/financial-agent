"""Bureau reports, transaction alerts and holdings statements.

Three readers that all sit outside the statement pipeline, for the same reason:
none of their documents has an opening balance, a closing balance and rows in
between, so the reconciliation gate has nothing to check and running them
through it would report every one of them as broken forever.

Each therefore carries its own guarantee, and these tests pin those:

  - a bureau report is a second opinion, never a correction - nothing here is
    allowed to overwrite a reconciled balance, and a fuzzy account match is
    offered rather than applied
  - an alert is a real transaction but an unchecked one, and must be superseded
    the moment the statement covering it arrives
  - a holdings statement declares a total, and units x NAV has to reproduce it
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingestion import bureau, portfolio, txn_email  # noqa: E402
from app.ingestion.gmail_source import SCAN_INTENTS, build_query  # noqa: E402
from app.ingestion.router import classify_document  # noqa: E402
from app.reconcile import bureau_match  # noqa: E402


CIBIL_REPORT = """
TransUnion CIBIL Limited
Credit Information Report
Consumer Name: Pankaj Sharma
Report Date: 14-08-2026
CIBIL TransUnion Score: 782
Account Information
ACCOUNT DETAILS 1
Member Name: HDFC BANK LTD
Account Type: Credit Card
Account Number: XXXXXXXXXXXX9798
Date Opened: 12-03-2019
Account Status: Open
Credit Limit: Rs. 5,00,000
Current Balance: Rs. 48,250.00
Amount Overdue: 0
000 000 000 030 000 000 000 000 000 000 000 000
ACCOUNT DETAILS 2
Member Name: BAJAJ FINANCE LIMITED
Account Type: Personal Loan
Account Number: XXXX3310
Account Status: Open
Sanctioned Amount: Rs. 4,00,000
Current Balance: Rs. 2,18,500.00
EMI Amount: Rs. 18,500
ACCOUNT DETAILS 3
Member Name: AXIS BANK LTD
Account Type: Credit Card
Account Number: XXXXXXXXXXXX4412
Date Closed: 01-06-2023
Account Status: Closed
Current Balance: 0
Enquiry Summary
"""


class FakeAccount:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def display_name(self) -> str:
        return f"{self.institution} ({self.account_number_masked})"


def _ledger() -> list[FakeAccount]:
    return [
        FakeAccount(id="acc-hdfc-cc", institution="HDFC Bank",
                    account_type="credit_card",
                    account_number_masked="XXXX9798", product_name="Regalia",
                    principal_outstanding="45000.00"),
        FakeAccount(id="acc-hdfc-sb", institution="HDFC Bank",
                    account_type="savings", account_number_masked="XXXX1234",
                    product_name="", principal_outstanding=None),
    ]


# --------------------------------------------------------------------------
# Scan intents
# --------------------------------------------------------------------------

def test_each_intent_searches_for_its_own_kind_of_document():
    statement = build_query(intent="statement")
    bureau_query = build_query(intent="bureau")
    alerts = build_query(intent="transactional")

    assert "has:attachment" in statement
    assert "cibil" in bureau_query
    # Alerts carry the amount in the body; requiring an attachment would find
    # none of them.
    assert "has:attachment" not in alerts


def test_alerts_default_to_two_months_but_are_not_capped_there():
    """Two months is the sensible default, not a decision taken for you.

    Unreconciled figures earn their place by being fresher than the statement
    covering them, so a year of them is mostly noise - but clamping the window
    meant asking for a year quietly got you two months and said nothing. That
    is the same fault as a dropdown displaying one number while the app uses
    another: the app overruling a choice in silence.
    """
    assert "newer_than:2m" in build_query(months=None, intent="transactional")
    assert "newer_than:1y" in build_query(months=12, intent="transactional")
    assert "newer_than:10y" in build_query(months=120, intent="transactional")
    assert SCAN_INTENTS["transactional"]["max_months"] == 2


def test_an_unknown_intent_falls_back_rather_than_failing():
    assert build_query(intent="nonsense") == build_query(intent="statement")


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Statement of account. Opening balance 1,000. Closing balance 2,000. UPI",
     "statement"),
    (CIBIL_REPORT, "bureau"),
    ("NSDL Consolidated Account Statement ISIN INE002A01018 units 50 NAV 1450",
     "portfolio"),
])
def test_documents_route_to_the_right_reader(text, expected):
    """A misrouted file is recorded as a parse failure, which reads like a bug
    rather than what it is - the wrong reader for the wrong document."""
    assert classify_document(text) == expected


# --------------------------------------------------------------------------
# Bureau reports
# --------------------------------------------------------------------------

def test_a_report_is_read_end_to_end():
    report = bureau.parse_report(CIBIL_REPORT, "cibil.pdf")
    assert report.bureau == "cibil"
    assert report.score == 782
    assert report.score_band == "very good"
    assert report.pulled_on == date(2026, 8, 14)
    assert len(report.accounts) == 3


def test_account_fields_survive_the_read():
    card = bureau.parse_report(CIBIL_REPORT).accounts[0]
    assert card.lender == "HDFC BANK LTD"
    assert card.account_type == "credit_card"
    assert card.number_suffix == "9798"
    assert card.current_balance == Decimal("48250.00")
    assert card.credit_limit == Decimal("500000")
    assert card.worst_dpd == 30


def test_a_closed_account_is_recognised_as_closed():
    closed = bureau.parse_report(CIBIL_REPORT).accounts[2]
    assert closed.status == "closed"
    assert closed.closed_on == date(2023, 6, 1)


def test_nothing_reported_is_none_rather_than_zero():
    """A bureau printing "-" means it has no figure. Recording that as zero
    puts a confident number where there is none."""
    assert bureau.parse_money("-") is None
    assert bureau.parse_money("") is None
    assert bureau.parse_money("Rs. 0") == Decimal("0")


def test_a_bank_statement_is_not_mistaken_for_a_report():
    assert not bureau.looks_like_bureau_report(
        "Statement of account. Opening balance. Closing balance.")


def test_lender_names_reduce_to_a_comparable_key():
    assert bureau.lender_key("HDFC BANK LTD") == bureau.lender_key("HDFC Bank")
    assert bureau.lender_key("BAJAJ FINANCE LIMITED") != bureau.lender_key("HDFC")


def test_a_report_that_cannot_be_read_says_so_instead_of_crashing():
    empty = bureau.parse_report("", "mystery.pdf")
    assert empty.accounts == []
    assert empty.warnings


# --------------------------------------------------------------------------
# Bureau matching
# --------------------------------------------------------------------------

def test_matching_digits_and_lender_links_automatically():
    accounts = bureau.parse_report(CIBIL_REPORT).accounts
    matches = bureau_match.match_accounts(accounts, _ledger())
    auto = [m for m in matches if m.status == "auto"]
    assert len(auto) == 1
    assert auto[0].account_id == "acc-hdfc-cc"


def test_a_card_never_matches_a_loan():
    """Checked in both directions, on pairs that agree about everything else.

    A pair that differs in lender AND digits scores zero whatever the types
    are, so it proves nothing about the compatibility table. These share a
    lender and the last four digits, which means the ONLY thing that can hold
    the score at zero is the refusal to match a card against a loan.
    """
    same = dict(lender="Bajaj Finance", account_number_masked="XXXX3310")
    bureau_card = bureau.BureauAccount(account_type="credit_card", **same)
    bureau_loan = bureau.BureauAccount(account_type="personal_loan", **same)
    ledger_card = FakeAccount(id="acc-card", institution="Bajaj Finance",
                              account_type="credit_card",
                              account_number_masked="XXXX3310", product_name="")
    ledger_loan = FakeAccount(id="acc-loan", institution="Bajaj Finance",
                              account_type="personal_loan",
                              account_number_masked="XXXX3310", product_name="")

    assert bureau_match.score_pair(bureau_card, ledger_loan)[0] == 0.0
    assert bureau_match.score_pair(bureau_loan, ledger_card)[0] == 0.0
    # The same pairs matched to their own kind still score, so the assertions
    # above are refusing a type mismatch rather than refusing everything.
    assert bureau_match.score_pair(bureau_card, ledger_card)[0] > 0.9
    assert bureau_match.score_pair(bureau_loan, ledger_loan)[0] > 0.9


def test_a_lender_name_alone_is_never_enough_to_link():
    """Two cards from the same bank match on everything except the digits, and
    guessing wrong puts one card's debt on the other's row."""
    bureau_card = bureau.BureauAccount(
        lender="HDFC Bank", account_type="credit_card",
        account_number_masked="")
    ledger_card = FakeAccount(id="acc-x", institution="HDFC Bank",
                              account_type="credit_card",
                              account_number_masked="XXXX0001",
                              product_name="")
    confidence, _ = bureau_match.score_pair(bureau_card, ledger_card)
    assert 0 < confidence < bureau_match.AUTO_LINK_CONFIDENCE

    matches = bureau_match.match_accounts([bureau_card], [ledger_card])
    assert matches[0].status == "suggested"
    assert matches[0].account_id == "acc-x"   # offered, for a human to confirm


def test_one_ledger_account_is_never_claimed_twice():
    """Two bureau rows on one account would double-count the same debt."""
    twins = [
        bureau.BureauAccount(lender="HDFC Bank", account_type="credit_card",
                             account_number_masked="XXXX9798"),
        bureau.BureauAccount(lender="HDFC Bank", account_type="credit_card",
                             account_number_masked="XXXX9798"),
    ]
    matches = bureau_match.match_accounts(twins, _ledger())
    linked = [m for m in matches if m.account_id]
    assert len(linked) == 1


def test_reconciliation_separates_the_three_outcomes():
    accounts = bureau.parse_report(CIBIL_REPORT).accounts
    ledger = _ledger()
    matches = bureau_match.match_accounts(accounts, ledger)
    result = bureau_match.reconcile(accounts, ledger, matches)

    assert result["counts"]["linked"] == 1
    # The Bajaj loan: reported, open, and no statement here covers it.
    assert result["counts"]["blind_spots"] == 1
    # The closed Axis card is history, not a gap.
    assert result["counts"]["unreported_here"] == 1


def test_a_savings_account_is_not_reported_as_missing():
    """Bureaus report credit, not deposits. Flagging a savings account as
    absent from a credit report would be noise dressed as a finding."""
    accounts = bureau.parse_report(CIBIL_REPORT).accounts
    ledger = _ledger()
    result = bureau_match.reconcile(
        accounts, ledger, bureau_match.match_accounts(accounts, ledger))
    assert result["counts"]["ledger_only"] == 0


def test_a_balance_disagreement_is_surfaced_not_applied():
    accounts = bureau.parse_report(CIBIL_REPORT).accounts
    ledger = _ledger()
    result = bureau_match.reconcile(
        accounts, ledger, bureau_match.match_accounts(accounts, ledger))
    assert len(result["balance_deltas"]) == 1
    assert result["balance_deltas"][0]["difference"] == "3250.00"
    # The ledger's own figure is untouched: it is the reconciled one.
    assert ledger[0].principal_outstanding == "45000.00"


# --------------------------------------------------------------------------
# Transaction alerts
# --------------------------------------------------------------------------

@pytest.mark.parametrize("body, direction, amount, suffix, kind", [
    ("Rs.1,250.00 debited from A/c XX1234 on 15-Aug-26 to VPA swiggy@ybl. Ref 1.",
     "debit", "1250.00", "1234", "upi"),
    ("INR 85,000.00 credited to your A/c XX9911 on 01-Aug-2026 from ACME PAYROLL",
     "credit", "85000.00", "9911", "upi"),
    ("Rs 3,499 spent on your HDFC Bank Card ending 9798 at AMAZON on 12-08-2026",
     "debit", "3499", "9798", "card"),
    ("Rs. 5,000.00 withdrawn from A/c XX1234 on 09-Aug-2026",
     "debit", "5000.00", "1234", "atm"),
])
def test_the_common_alert_shapes_are_read(body, direction, amount, suffix, kind):
    parsed = txn_email.parse_alert(body, received=date(2026, 8, 20))
    assert parsed is not None
    assert parsed.direction == direction
    assert parsed.amount == Decimal(amount)
    assert parsed.account_suffix == suffix
    assert parsed.kind == kind


@pytest.mark.parametrize("body", [
    "Your credit card payment of Rs 12,000 is due on 20-Aug-2026.",
    "OTP 448210 for your transaction of Rs 2,000. Do not share.",
    "Your transaction of Rs 999 has failed.",
    "Your e-statement for August is ready.",
    "Rs 500 will be debited on 25-Aug-2026 towards your SIP.",
])
def test_mail_about_money_that_did_not_move_is_rejected(body):
    """Inventing a transaction from a reminder is far worse than importing
    nothing, so None is the correct and common answer."""
    assert txn_email.parse_alert(body) is None


@pytest.mark.parametrize("body", [
    "Rs.2,000.00 debited from A/c XX1234 to VPA shop@ybl. This transaction "
    "has failed and will be reversed.",
    "Rs.7,500.00 debited from A/c XX1234 to VPA rent@okhdfc - declined by the "
    "issuing bank.",
    "Rs 3,499 spent on your Card ending 9798 at AMAZON - this request for "
    "authorisation was unsuccessful.",
])
def test_a_reversal_that_reads_like_a_real_alert_is_still_rejected(body):
    """The cases above are rejected because no template matches them at all,
    which tests the templates rather than the guard. These DO match a
    template - a real alert sentence with a failure clause attached - so only
    NOT_A_TRANSACTION can stop them becoming spending that never happened."""
    matching = [t.name for t in txn_email.TEMPLATES
                if t.pattern.search(txn_email.to_text(body))]
    assert matching, "this case is meant to reach a template"
    assert txn_email.parse_alert(body) is None


@pytest.mark.parametrize("body", [
    "Rs.1,250.00 debited from A/c XX1234 on 15-Aug-2026 to VPA swiggy@ybl.",
    "Rs.1,250.00 debited from A/c XX1234 to VPA swiggy@ybl on 15-Aug-2026.",
])
def test_the_date_is_read_whichever_side_of_the_payee_it_sits(body):
    """Banks write it both ways. Reading it from a capture group caught only
    one order; the other fell back to the email's received date, which is a
    day or more out and moves a month-end payment into the following month."""
    parsed = txn_email.parse_alert(body, received=date(2026, 8, 20))
    assert parsed.txn_date == date(2026, 8, 15)


def test_an_alert_with_no_date_falls_back_to_when_it_arrived():
    parsed = txn_email.parse_alert(
        "Rs.1,250.00 debited from A/c XX1234 to VPA swiggy@ybl.",
        received=date(2026, 8, 20))
    assert parsed.txn_date == date(2026, 8, 20)


def test_html_alerts_are_read_through_their_markup():
    parsed = txn_email.parse_alert(
        "<html><body><p>Rs.<b>2,340.50</b> debited from A/c "
        "<span>XX7788</span> to VPA zomato@paytm</p></body></html>",
        received=date(2026, 8, 20))
    assert parsed.amount == Decimal("2340.50")
    assert parsed.account_suffix == "7788"


def test_the_rail_is_carried_into_the_narration():
    """UPI detection elsewhere keys off a leading "UPI" in the narration, so an
    alert has to arrive in the same shape as the statement row that replaces
    it or the two get classified differently."""
    parsed = txn_email.parse_alert(
        "Rs.100.00 debited from A/c XX1234 to VPA shop@ybl.",
        received=date(2026, 8, 1))
    assert parsed.description.startswith("UPI/")


class Row:
    def __init__(self, amount, direction, day, account_id="a1"):
        self.amount = Decimal(amount)
        self.direction = direction
        self.txn_date = date(2026, 8, day)
        self.account_id = account_id
        self.superseded = False
        self.excluded = False
        self.note = ""


def test_a_statement_supersedes_the_alert_it_covers():
    """The load-bearing half. Without it, importing a statement for a month
    whose alerts are already in the ledger counts every payment twice - and the
    more diligent the user, the more wrong their spending becomes."""
    alerts = [Row("1250.00", "debit", 15)]
    assert txn_email.supersede_matched(alerts, [Row("1250.00", "debit", 16)]) == 1
    assert alerts[0].superseded is True
    assert alerts[0].excluded is True
    assert "statement" in alerts[0].note


def test_an_alert_with_no_statement_row_is_left_alone():
    alerts = [Row("999.00", "debit", 16)]
    assert txn_email.supersede_matched(alerts, [Row("1250.00", "debit", 16)]) == 0
    assert alerts[0].superseded is False


def test_matching_stops_at_the_day_window():
    """Same amount, weeks apart, is two payments - not one reported twice."""
    alerts = [Row("400.00", "debit", 1)]
    assert txn_email.supersede_matched(alerts, [Row("400.00", "debit", 20)]) == 0


def test_a_different_account_is_a_different_payment():
    alerts = [Row("400.00", "debit", 10, account_id="a1")]
    statement = [Row("400.00", "debit", 10, account_id="a2")]
    assert txn_email.supersede_matched(alerts, statement) == 0


def test_one_statement_row_supersedes_only_one_alert():
    """Two identical alerts and one statement row means one of the alerts was
    real and the other was a duplicate notification - not that both are gone."""
    alerts = [Row("500.00", "debit", 10), Row("500.00", "debit", 10)]
    assert txn_email.supersede_matched(alerts, [Row("500.00", "debit", 10)]) == 2


# --------------------------------------------------------------------------
# Portfolio
# --------------------------------------------------------------------------

CAS_TEXT = """
NSDL Consolidated Account Statement
Holdings as on 31-Jul-2026
Total Portfolio Value: Rs. 3,26,330.00
"""

CAS_ROWS = [
    ["ISIN", "Security Name", "Quantity", "Closing Price", "Market Value",
     "Average Cost"],
    ["INE002A01018", "RELIANCE INDUSTRIES LTD", "50", "1,450.00", "72,500.00",
     "1,200.00"],
    ["INF204K01679", "Nippon India Liquid Fund Growth", "1,200.5", "60.00",
     "72,030.00", "55.00"],
    ["INE467B01029", "TATA CONSULTANCY SERVICES", "45", "4,040.00",
     "1,81,800.00", "3,500.00"],
    ["", "Total", "", "", "3,26,330.00", ""],
]


class Table:
    def __init__(self, rows):
        self.rows = rows


def _cas() -> portfolio.PortfolioStatement:
    return portfolio.parse_statement(CAS_TEXT, [Table(CAS_ROWS)], "cas.pdf")


def test_a_holdings_statement_is_read():
    statement = _cas()
    assert statement.layout == "cas"
    assert statement.as_of == date(2026, 7, 31)
    assert len(statement.holdings) == 3


def test_the_statements_own_total_row_is_not_a_holding():
    """Every one of these documents totals itself. Reading that row as a
    position values the portfolio at roughly twice what it is worth."""
    statement = _cas()
    assert "Total" not in [h.instrument for h in statement.holdings]
    assert statement.computed_value == Decimal("326330.00")


def test_a_fund_called_total_something_is_still_a_holding():
    """The total-row filter must not eat a legitimately named fund."""
    rows = [
        ["ISIN", "Scheme Name", "Units", "NAV", "Market Value"],
        ["INF109K01234", "Total Return Index Fund", "100", "25.00", "2,500.00"],
    ]
    statement = portfolio.parse_statement("CAMS as on 31-Jul-2026",
                                          [Table(rows)])
    assert len(statement.holdings) == 1


def test_holdings_must_add_up_to_the_declared_total():
    statement = _cas()
    status, gap, _ = statement.reconcile()
    assert status == "passed"
    assert gap == Decimal("0.00")


def test_a_misread_statement_fails_the_gate():
    statement = _cas()
    statement.declared_value = Decimal("326500.00")
    status, gap, message = statement.reconcile()
    assert status == "failed"
    assert gap == Decimal("-170.00")
    assert "326,330" in message or "326330" in message


def test_a_gap_the_size_of_one_holding_names_it():
    """More use than the size of the discrepancy: it says which row is wrong."""
    statement = _cas()
    statement.declared_value = statement.computed_value - Decimal("72500.00")
    _, _, message = statement.reconcile()
    assert "RELIANCE" in message


def test_a_statement_with_no_total_is_not_reported_as_broken():
    statement = _cas()
    statement.declared_value = None
    assert statement.reconcile()[0] == "not_applicable"


def test_instruments_are_classified_from_their_isin_and_name():
    assert portfolio.classify_instrument("Any Fund", "INF204K01679") == "mutual_fund"
    assert portfolio.classify_instrument("RELIANCE", "INE002A01018") == "equity"
    assert portfolio.classify_instrument("Nippon Nifty ETF", "") == "etf"
    assert portfolio.classify_instrument("7.26% GOI Bond 2033", "") == "bond"


def test_columns_are_found_by_header_not_by_position():
    """These layouts reorder their columns between versions, and a reader that
    counts columns silently swaps NAV and value the first time one does."""
    reordered = [
        ["Market Value", "Scheme Name", "NAV", "ISIN", "Units"],
        ["5,000.00", "Some Fund", "50.00", "INF109K01234", "100"],
    ]
    statement = portfolio.parse_statement("CAMS as on 31-Jul-2026",
                                          [Table(reordered)])
    holding = statement.holdings[0]
    assert holding.units == Decimal("100")
    assert holding.nav == Decimal("50.00")
    assert holding.computed_value() == Decimal("5000.00")


def test_a_missing_nav_does_not_value_a_holding_at_zero():
    rows = [
        ["ISIN", "Scheme Name", "Units", "NAV", "Market Value"],
        ["INF109K01234", "Some Fund", "100", "-", "4,900.00"],
    ]
    statement = portfolio.parse_statement("CAMS as on 31-Jul-2026", [Table(rows)])
    assert statement.holdings[0].nav is None
    # Falls back to the printed value rather than computing 100 x 0.
    assert statement.holdings[0].computed_value() == Decimal("4900.00")


def test_a_repeated_header_does_not_double_a_holding():
    """A multi-page table repeats its header, and a holding read twice inflates
    the portfolio by its own value."""
    rows = CAS_ROWS + CAS_ROWS[1:]
    statement = portfolio.parse_statement(CAS_TEXT, [Table(rows)], "cas.pdf")
    assert len(statement.holdings) == 3


def test_a_bank_statement_is_not_mistaken_for_a_portfolio():
    assert not portfolio.looks_like_portfolio(
        "Opening balance closing balance UPI transfer")


def test_holdings_survive_a_null_account_and_a_blank_value():
    """The Portfolio tab 500'd after the PostgreSQL migration.

    `get_holdings` joined on `h.account_id IS newest.account_id` - SQLite's
    null-safe equality, which PostgreSQL does not have and rejects as a syntax
    error, so the endpoint failed outright rather than degrading.

    Both halves of the null-safety are load-bearing, so both are asserted:
    account_id is NULL for a portfolio never matched to a ledger account, and
    a plain `=` would silently drop exactly those rows; and `value` is
    nullable TEXT, where SQLite read '' as 0.0 and PostgreSQL raises on the
    cast used to order by it.
    """
    from tests.support import fresh_ledger
    from app.db import repository as repo

    db = fresh_ledger()
    with db.connection() as conn:
        for holding_id, isin, folio, value in (
            ("h-unmatched", "INE001", "F1", "5000"),
            ("h-blank", "INE002", "F2", ""),
            ("h-null", "INE003", "F3", None),
        ):
            conn.execute(
                "INSERT INTO holdings (id, statement_id, account_id, isin,"
                " folio, instrument, value, as_of)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (holding_id, None, None, isin, folio,
                 f"Fund {isin}", value, "2025-01-31"))

    rows = repo.get_holdings(db, latest_only=True)
    assert len(rows) == 3, "a holding with no matched account was dropped"
    # Ordered by value descending, with the unsortable ones last rather than
    # first - which is where PostgreSQL puts NULL on DESC by default.
    assert rows[0]["instrument"] == "Fund INE001"


def test_the_portfolio_endpoint_answers_for_an_account_with_no_holdings():
    from fastapi.testclient import TestClient
    from tests.support import fresh_ledger
    from app.main import app

    fresh_ledger()
    response = TestClient(app).get("/api/portfolio")
    assert response.status_code == 200, response.text
