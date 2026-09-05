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


# --------------------------------------------------------------------------
# A real CDSL CAS: two scripts per header, and one ISIN in two demat accounts
# --------------------------------------------------------------------------

#: CDSL prints every column heading twice - once in English, once in Hindi -
#: and the extractor returns the two woven together a glyph at a time. These
#: are verbatim header cells off a real statement; the whole point of them is
#: that they are unreadable to anything matching on the plain text.
CDSL_DEMAT_HEADER = [
    "ISIN ISIN",
    "Security \u092a\u094d\u0930\u0924\u093f\u092d\u0942\u0924\u093f",
    "Current \u0935\u0924\u0930B\u092e\u093eal\u0928 \u0936\u0947\u0937",
    "Frozen \u092b\u094d\u0930\u094b\u091c\u0947\u0928 Bal \u0936\u0947\u0937",
    "Pledge \u092a\u094d\u0932\u0947\u091cBa \u0936\u0947\u0937l",
    "Pledge \u092a\u094d\u0932\u0947\u091cSe \u0936\u0947\u0937tup Bal",
    "Free \u092e\u0941B\u0915a\u094dl\u0924 \u0936\u0947\u0937",
    "Market Price / \u092c\u093e\u091c\u093e\u0930 / Face",
    "V a lu e ( ` )",
]

CDSL_TEXT = """
Central Depository Services (India) Limited
CONSOLIDATED ACCOUNT STATEMENT (CAS) FOR SECURITIES HELD IN DEMAT
CDSL Demat Accounts 1,60,000.00
Holdings as on 31-May-2026
Total Portfolio Value 1,60,000.00
"""


def _demat_table(*rows):
    return Table([CDSL_DEMAT_HEADER, *rows])


def test_a_bilingual_header_is_still_a_header():
    """CDSL's demat columns are English and Hindi interleaved glyph by glyph.

    Nine tenths of a real CAS went missing on this. "Current Bal" arrives
    with Devanagari wedged inside the words, which squeezing the spaces out
    does not fix, so every numeric column went unmapped, no header row was
    recognised at all, and the statement came back holding only its mutual
    funds - while still reporting itself as parsed.
    """
    mapping = portfolio.map_columns(CDSL_DEMAT_HEADER)
    assert mapping[0] == "isin"
    assert mapping[1] == "instrument"
    assert mapping[2] == "units", "Current Bal is what is held"
    assert mapping[7] == "nav"
    assert mapping[8] == "value"


def test_the_same_stock_in_two_demat_accounts_is_one_position():
    """A CAS prints one table per demat account, and the same ISIN can appear
    in several of them.

    De-duplicating across the whole document silently DROPPED every second
    copy - on a real statement, ten positions held at two brokers, 72,582.09
    of a 10.67 lakh portfolio gone. Summing them is the other half: storage
    identifies a holding by (account, ISIN, folio, instrument, date) and a
    demat table carries no folio, so two lots collided there too and the
    second replaced the first.

    Both are answered by consolidating. The position in Sterlite IS 400
    shares, which is also what the statement's own grand total counts - so
    the merge cannot move the total, only regroup it, and `reconcile` checks
    that for free.
    """
    statement = portfolio.parse_statement(CDSL_TEXT, [
        _demat_table(["INE089C01029", "STERLITE TECHNOLOGIES LIMITED",
                      "279.000", "--", "--", "--", "279.000", "400.0000",
                      "1,11,600.00"]),
        _demat_table(["INE089C01029", "STERLITE TECHNOLOGIES LIMITED",
                      "121.000", "--", "--", "--", "121.000", "400.0000",
                      "48,400.00"]),
    ], "cas.pdf")

    assert len(statement.holdings) == 1
    assert statement.holdings[0].units == Decimal("400.000")
    assert statement.computed_value == Decimal("160000.00")
    assert statement.reconcile()[0] == "passed"


def test_two_folios_of_one_fund_are_not_merged():
    """The limit of that. A folio number is a real distinction between two
    accounts holding the same scheme, so those stay apart."""
    rows = [
        ["ISIN", "Scheme Name", "Folio", "Units", "NAV", "Market Value"],
        ["INF109K01234", "Some Fund", "111/22", "100", "50.00", "5,000.00"],
        ["INF109K01234", "Some Fund", "333/44", "60", "50.00", "3,000.00"],
    ]
    statement = portfolio.parse_statement("CAMS as on 31-Jul-2026",
                                          [Table(rows)])
    assert len(statement.holdings) == 2
    assert statement.computed_value == Decimal("8000.00")


def test_a_repeated_header_inside_one_table_still_does_not_double():
    """The protection the per-account fix must not have cost.

    A table continued over a page break repeats its header and can carry
    body rows with it. That happens WITHIN one extracted table, which is why
    the de-duplication is scoped there rather than across the document.
    """
    row = ["INE089C01029", "STERLITE TECHNOLOGIES LIMITED", "279.000", "--",
           "--", "--", "279.000", "400.0000", "1,11,600.00"]
    statement = portfolio.parse_statement(
        CDSL_TEXT, [Table([CDSL_DEMAT_HEADER, row, CDSL_DEMAT_HEADER, row])],
        "cas.pdf")
    assert len(statement.holdings) == 1


# --------------------------------------------------------------------------
# NPS: the transposed one
# --------------------------------------------------------------------------

NPS_TEXT = """
NPS TRANSACTION STATEMENT
Jun 01, 2026 To Jun 30, 2026
NPS Transaction Statement for Tier I Account
PRAN 110196736648 Registration Date 20-Jan-22
Investment Summary
Holdings as on 30-Jun-2026
Total Units 3139.9512
"""

NPS_SUMMARY_ROWS = [
    ["Value of your Holdings (Investments) as on Jun 30, 2026 (in Rs)",
     "No of Contributions", "Total Contribution in your account as on",
     "Total Withdrawal as on Jun 30, 2026 (in Rs)",
     "Total Notional Gain/Loss as on Jun 30, 2026 (in Rs)"],
    ["308909.36", "6", "250240.40", "0.00", "58668.96"],
]

NPS_SCHEME_ROWS = [
    ["Particulars", "References",
     "ICICI PRUDENTIAL PENSION FUND SCHEME E - TIER I POP",
     "ICICI PRUDENTIAL PENSION FUND SCHEME C - TIER I POP",
     "ICICI PRUDENTIAL PENSION FUND SCHEME G - TIER I POP"],
    ["Scheme wise Value of your Holdings (Investments) (in Rs)", "E=U*N",
     "232286.36", "73520.21", "3102.79"],
    ["Total Units", "U", "3139.9512", "1588.7846", "79.7140"],
    ["NAV as on 30-Jun-26", "N", "73.9777", "46.2745", "38.9241"],
]

#: The movement table that follows the holdings on the same statement. Its
#: numbers are what CHANGED during the month, not what is held.
NPS_MOVEMENT_ROWS = [
    ["Date", "Particulars",
     "ICICI PRUDENTIAL PENSION FUND SCHEME E - TIER I POP", "",
     "ICICI PRUDENTIAL PENSION FUND SCHEME C - TIER I POP", ""],
    ["", "", "Amount (Rs)", "Units", "Amount (Rs)", "Units"],
    ["01-Jun-26", "Opening Balance", "", "3097.0114", "", "692.9423"],
    ["30-Jun-26", "Closing Balance", "", "3139.9512", "", "1588.7846"],
]


def _nps() -> portfolio.PortfolioStatement:
    return portfolio.parse_statement(
        NPS_TEXT, [Table(NPS_SUMMARY_ROWS), Table(NPS_SCHEME_ROWS),
                   Table(NPS_MOVEMENT_ROWS)],
        "1116736648_Jun2026.pdf")


def test_an_nps_statement_is_a_portfolio_not_a_ledger():
    """Routing first, because it is what decided the outcome.

    Two months of the same statement reached the portfolio reader and a
    third went to the transaction pipeline, which reported it as a statement
    declaring no opening balance. Nothing about a document should depend on
    which month it happens to be.
    """
    assert classify_document(NPS_TEXT, "1116736648_Jun2026.pdf") == "portfolio"
    assert portfolio.detect_layout(NPS_TEXT, "x.pdf") == ("nps", "NPS")


def test_the_transposed_scheme_table_is_read():
    """An NPS statement puts the schemes across the columns and the fields
    down the rows, which the row-per-holding reader cannot see at all - it
    looks for a header naming an instrument and finds "Particulars"."""
    statement = _nps()
    assert len(statement.holdings) == 3
    assert {h.kind for h in statement.holdings} == {"nps"}

    equity = next(h for h in statement.holdings if "SCHEME E" in h.instrument)
    assert equity.units == Decimal("3139.9512")
    assert equity.nav == Decimal("73.9777")
    assert equity.value == Decimal("232286.36")


def test_an_nps_account_reconciles_against_its_own_summary():
    """The three scheme values must reproduce the "Value of your Holdings"
    the summary prints - the same gate every other document here goes
    through, and the reason an NPS statement is worth reading rather than
    filing as unsupported."""
    statement = _nps()
    assert statement.declared_value == Decimal("308909.36")
    assert statement.reconcile()[0] == "passed"


def test_a_unit_count_is_not_a_portfolio_value():
    """The generic total reader takes the largest figure following the word
    "total". On an NPS statement that is "Total Units" - and 3,139.95 units
    was recorded as the value of a 3.09 lakh account."""
    assert portfolio.parse_declared_total(NPS_TEXT) == Decimal("3139.95")
    assert _nps().declared_value == Decimal("308909.36")


def test_the_nps_movement_table_is_not_read_as_holdings():
    """An opening and a closing balance are what changed, not what is held.

    Read as holdings they would count every unit that merely moved during
    the month as a position of its own.
    """
    statement = portfolio.parse_statement(
        NPS_TEXT, [Table(NPS_SUMMARY_ROWS), Table(NPS_MOVEMENT_ROWS)],
        "nps.pdf")
    assert statement.holdings == []
    assert statement.reconcile()[0] == "not_applicable"


# --------------------------------------------------------------------------
# The other recordkeeper, which agrees on nothing but the subject matter
# --------------------------------------------------------------------------

KFIN_TEXT = """
CENTRAL RECORDKEEPING AGENCY
NATIONAL PENSION SYSTEM
Transaction Statement - Tier I
PRAN 400080396530 Statement Date Jul 09, 2026
Subscriber Name JITESH MUKESH AGARWAL
Tier I Status Active
Investment Details as on 30-06-2026
"""

#: KFintech returns its entire cover page as ONE table - subscriber details,
#: nominees, scheme preferences and the investment summary - so the holdings
#: header lands sixteen rows down, and the schemes run one per row rather
#: than across the columns the way Protean's do.
KFIN_COVER_ROWS = [
    ["CENTRAL RECORDKEEPING AGENCY", "", "", "", ""],
    ["NATIONAL PENSION SYSTEM", "", "", "", ""],
    ["Subscriber Details", "", "", "", ""],
    ["PRAN 400080396530 Statement Date Jul 09, 2026", "", "", "", ""],
    ["Compliance Details", "PAN", "FATCA", "", ""],
    ["Compliance Status", "Y", "Y", "", ""],
    ["Nominee Details", "", "", "", ""],
    ["Nominee Name", "Percentage", "", "", ""],
    ["NISHA AGARWAL", "100.00", "", "", ""],
    ["Scheme Deails", "Percentage", "", "", ""],
    ["Scheme 1", "NPS TRUST- A/C HDFC PENSION FUND SCHEME E", "75.00", "", ""],
    ["Scheme 2", "NPS TRUST- A/C HDFC PENSION FUND SCHEME C", "25.00", "", ""],
    ["Investment Details as on 30-06-2026", "", "", "", ""],
    ["No of Contributions", "Total Contribution (Rs)",
     "Total Withdrawal (Rs)", "Deductions due to Charges (Rs)",
     "Current Valuation (Rs)"],
    ["16", "45448.72", "0.00", "468.77", "64809.01"],
    ["Scheme Name", "Total Units", "Latest NAV", "Value at NAV", "XIRR"],
    ["NPS TRUST- A/C HDFC PENSION FUND MANAGEMENT LIMITED SCHEME E - TIER I POP",
     "928.8269", "53.4865", "49679.69", "8.82%"],
    ["NPS TRUST- A/C HDFC PENSION FUND MANAGEMENT LIMITED SCHEME C - TIER I POP",
     "536.4595", "30.7833", "16513.99", ""],
    ["Total", "66193.68", "", "", ""],
]

#: The extractor re-renders the same page further down the document, wrapping
#: the long scheme name and repeating its figures.
KFIN_RERENDER_ROWS = [
    ["Scheme Name", "Total Units", "Latest NAV", "Value at NAV", "XIRR"],
    ["LIMITED SCH", "928.8269", "53.4865", "49679.69", ""],
]


def _kfin(*extra) -> portfolio.PortfolioStatement:
    return portfolio.parse_statement(
        KFIN_TEXT, [Table(KFIN_COVER_ROWS), *extra], "XXXXXX6530_739824.pdf")


def test_the_two_recordkeepers_do_not_share_a_layout():
    """Protean transposes its holdings table; KFintech prints one row per
    scheme. Reading only the transposed shape reported a real 66,193.68
    account as a portfolio with nothing in it.
    """
    statement = _kfin()
    assert statement.layout == "nps"
    assert len(statement.holdings) == 2
    assert {h.kind for h in statement.holdings} == {"nps"}
    assert all(h.folio == "400080396530" for h in statement.holdings), \
        "the PRAN is what tells one recordkeeper's schemes from the other's"


def test_the_holdings_header_can_be_sixteen_rows_down():
    """KFintech's whole cover page comes back as one table. Searching only
    the first twelve rows for a header found none and skipped it whole."""
    assert portfolio.map_columns(KFIN_COVER_ROWS[15]) == {
        0: "instrument", 1: "units", 2: "nav", 3: "value"}
    assert len(_kfin().holdings) == 2


def test_a_rerendered_page_does_not_double_the_largest_scheme():
    """The tables after the holdings table are the same page again, with the
    scheme name wrapped to a fragment against a repeat of its figures.
    Concatenating them made a 66,193.68 corpus 115,873.39."""
    statement = _kfin(Table(KFIN_RERENDER_ROWS))
    assert len(statement.holdings) == 2
    assert statement.computed_value == Decimal("66193.69")
    assert statement.reconcile()[0] == "passed"


def test_the_holdings_are_checked_against_their_own_total():
    """KFintech's May statement disagrees with itself: the summary prints a
    "Current Valuation" of 64,809.01 while the scheme table beneath totals
    64,882.29, and the scheme rows add up to the second.

    Reconciliation asks whether the rows READ reproduce the total printed FOR
    THEM, so the anchor is the holdings table's own Total row. Anchoring on
    the summary reported a correctly-read statement as broken by 73.28.
    """
    statement = _kfin()
    assert statement.declared_value == Decimal("66193.68"), \
        "the scheme table's Total row, not the summary's Current Valuation"
    assert statement.reconcile()[0] == "passed"


def test_an_nps_statement_needs_a_pran_and_says_so():
    """KFintech is a mutual fund registrar AND an NPS recordkeeper. Its fund
    statements open with the PAN; its NPS ones want the PRAN, which nothing
    in the profile can produce - so the file has to be reported as needing
    one rather than as a PAN that did not work."""
    from app.ingestion import passwords as pw
    from app.models.profile import UserProfile

    assert pw.password_hint("KCRA@kfintech.com", "")[0] == "PRAN"
    assert pw.password_hint("donotreply@kfintech.com", "")[0] == "PAN"

    filled = UserProfile(full_name="Jitesh Agarwal", pan="ABCDE1234F",
                         date_of_birth=date(1990, 7, 14), mobile="9000000000")
    assert pw.profile_can_satisfy(filled, "PAN")
    assert not pw.profile_can_satisfy(filled, "PRAN"), \
        "no combination of profile fields produces a PRAN"
    assert pw.profile_can_satisfy(
        filled.model_copy(update={"custom_passwords": ["110196736648"]}),
        "PRAN"), "the one thing that does is the user typing it in"


def test_a_bank_statement_is_not_mistaken_for_a_portfolio():
    assert not portfolio.looks_like_portfolio(
        "Opening balance closing balance UPI transfer")


def test_three_schemes_with_no_isin_are_three_holdings():
    """What a holding is IDENTIFIED by, checked at the database.

    The unique key read (account, ISIN, folio, date) while the comment above
    it said "one row per instrument per folio per valuation date". Every
    demat statement, CAS and broker holding carries an ISIN, so the two
    readings never diverged and nothing caught it.

    An NPS statement carries neither an ISIN nor a folio number. Its three
    schemes were one row under the old key: each overwrote the last on the
    way in, and a 3.09 lakh corpus stored as whichever scheme was written
    third. Nothing failed - the total was simply wrong, by an amount there
    was no second source for.
    """
    from tests.support import fresh_ledger
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType

    db = fresh_ledger()
    repo.upsert_account(db, Account(
        id="nps-1", institution="Protean NPS",
        account_type=AccountType.INVESTMENT))

    statement = portfolio.PortfolioStatement(
        layout="nps", provider="NPS", as_of=date(2026, 6, 30),
        declared_value=Decimal("308909.36"))
    statement.holdings = portfolio.nps_holdings(
        [Table(NPS_SCHEME_ROWS)], pran="110196736648")

    repo.save_portfolio_statement(db, statement, "nps-1", "hash-nps", "nps.pdf")
    stored = repo.get_holdings(db)
    assert len(stored) == 3, [h["instrument"] for h in stored]
    assert sum(Decimal(h["value"]) for h in stored) == Decimal("308909.38")

    # And re-importing the same statement still updates rather than doubles,
    # which is the guarantee the narrow key was there to provide.
    repo.save_portfolio_statement(db, statement, "nps-1", "hash-nps", "nps.pdf")
    assert len(repo.get_holdings(db)) == 3


def test_two_recordkeepers_are_two_accounts():
    """Protean and KFintech both distribute the same pension funds, so
    neither the scheme name nor the provider identifies the account - the
    PRAN does, and it is the only thing that does.

    Sniffing the recordkeeper out of the document does not work: a Protean
    statement never prints the word "Protean", so identical files landed
    under two different provider names on nothing but luck, and the same
    3.12 lakh was counted twice. Carrying the PRAN as the account reference
    is what makes each subscriber account one account.

    The portfolio is then the latest statement PER ACCOUNT, so two CRAs
    reporting a month apart both count - where one merged account would have
    shown only whichever statement was newer.
    """
    from tests.support import fresh_ledger
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType

    db = fresh_ledger()
    for pran, as_of, tag in (("110196736648", date(2026, 6, 30), "a"),
                             ("400080396530", date(2026, 5, 31), "b")):
        statement = portfolio.PortfolioStatement(
            layout="nps", provider="NPS", as_of=as_of, account_ref=pran)
        statement.holdings = portfolio.nps_holdings(
            [Table(NPS_SCHEME_ROWS)], pran=pran)
        account_id = repo.upsert_account(db, Account(
            institution=statement.provider,
            account_type=AccountType.INVESTMENT,
            account_number_masked=statement.account_ref))
        repo.save_portfolio_statement(db, statement, account_id,
                                      f"hash-{tag}", f"{tag}.pdf")

    stored = repo.get_holdings(db)
    assert len({h["folio"] for h in stored}) == 2, "one PRAN swallowed the other"
    assert len(stored) == 6
    assert len({h["account_id"] for h in stored}) == 2


def test_a_consolidated_statement_supersedes_a_brokers_own():
    """A CAS reports every demat account the holder has, so a broker's own
    holdings statement is a subset of it that has already been counted.

    All twenty holdings on a real Upstox statement were ISINs the CAS also
    reported, and adding them put 3.01 lakh of shares into the portfolio
    twice. What the CAS does not report is untouched.
    """
    from tests.support import fresh_ledger
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType

    db = fresh_ledger()

    def _save(layout, provider, holdings, as_of, tag):
        statement = portfolio.PortfolioStatement(
            layout=layout, provider=provider, as_of=as_of)
        statement.holdings = holdings
        account_id = repo.upsert_account(db, Account(
            institution=provider, account_type=AccountType.INVESTMENT,
            account_number_masked=tag))
        repo.save_portfolio_statement(db, statement, account_id,
                                      f"hash-{tag}", f"{tag}.pdf")

    _save("cas", "CDSL/NSDL", [
        portfolio.Holding(instrument="STERLITE", isin="INE089C01029",
                          units=Decimal("400"), nav=Decimal("400")),
        portfolio.Holding(instrument="TRENT", isin="INE849A01020",
                          units=Decimal("10"), nav=Decimal("4000")),
    ], date(2026, 6, 30), "cas")

    # The broker's is a month NEWER and still loses: its line for Sterlite is
    # one account's slice of a holding the CAS reports whole.
    _save("broker", "Broker", [
        portfolio.Holding(instrument="STERLITE TECH-EQ", isin="INE089C01029",
                          units=Decimal("105"), nav=Decimal("420")),
        portfolio.Holding(instrument="A FUND THE CAS MISSES", isin="INF999X01011",
                          units=Decimal("100"), nav=Decimal("25")),
    ], date(2026, 7, 31), "brk")

    stored = {h["instrument"]: h for h in repo.get_holdings(db)}
    assert "STERLITE TECH-EQ" not in stored, "counted the same shares twice"
    assert stored["STERLITE"]["value"] == "160000.00"
    assert "A FUND THE CAS MISSES" in stored, \
        "only what the CAS actually reports is superseded"
    assert sum(Decimal(h["value"]) for h in stored.values()) == Decimal("202500.00")


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
