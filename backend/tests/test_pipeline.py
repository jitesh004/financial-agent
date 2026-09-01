"""Pipeline tests.

The important ones here are not "does it run" but "does it produce the right
number, and does it notice when it doesn't". Fixtures are generated from a
simulation with known ground truth (backend/tools/generate_samples.py), so
every assertion below compares against a figure we can derive independently.

Run:  python -m pytest backend/tests -q
"""

from __future__ import annotations

import copy
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from tests.support import fresh_ledger  # noqa: E402
from app import storage  # noqa: E402
from app.categorize.rules import categorize_by_rules  # noqa: E402
from app.graph.build import build_graph  # noqa: E402
from app.ingestion import router  # noqa: E402
from app.models.schemas import (Account, AccountType, Category,  # noqa: E402
                                Direction, ReconciliationStatus, Transaction)
from app.normalize.column_map import map_columns  # noqa: E402
from app.normalize.normalizer import normalize  # noqa: E402
from app.normalize.parsers import (extract_merchant, infer_date_order,  # noqa: E402
                                   normalize_description, parse_amount,
                                   parse_date, parse_signed_amount,
                                   redact_account_numbers)
from app.reconcile.balance_check import reconcile  # noqa: E402
from app.reconcile.transfers import detect_transfers  # noqa: E402

SAMPLES = ROOT / "data" / "samples"

#: (filename, expected transactions, expected account type, opening, closing)
EXPECTED = [
    ("hdfc_savings_2025_2026.xlsx", 202, AccountType.SAVINGS, "342180.5", "222888.5"),
    ("hdfc_savings_2025_2026.csv", 202, AccountType.SAVINGS, "342180.50", "222888.50"),
    ("icici_credit_card_2025_2026.pdf", 392, AccountType.CREDIT_CARD, "0.00", "77029.00"),
    ("hdfc_home_loan_2025_2026.docx", 24, AccountType.HOME_LOAN, "4185000.00", "4086249.53"),
    ("bajaj_personal_loan_2025_2026.xlsx", 24, AccountType.PERSONAL_LOAN, "480000", "344795.24"),
    ("mf_portfolio_statement_2025_2026.pdf", 24, AccountType.INVESTMENT, None, None),
]


def _require_samples():
    if not SAMPLES.exists() or not any(SAMPLES.iterdir()):
        pytest.skip("run backend/tools/generate_samples.py first")


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("15/01/2026", date(2026, 1, 15)),
    ("2026-01-15", date(2026, 1, 15)),
    ("15-Jan-2026", date(2026, 1, 15)),
    ("January 15, 2026", date(2026, 1, 15)),
    ("15Jan26", date(2026, 1, 15)),
    ("20260115", date(2026, 1, 15)),
    ("15/01/2026 14:30:00", date(2026, 1, 15)),
    ("31/12/1999", date(1999, 12, 31)),
    ("not a date", None),
    ("", None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_ambiguous_dates_default_to_day_first():
    """01/02/2026 is 1 February for every institution this targets."""
    assert parse_date("01/02/2026") == date(2026, 2, 1)
    assert parse_date("01/02/2026", day_first=False) == date(2026, 1, 2)


def test_date_order_inferred_from_unambiguous_row():
    """One row with a field > 12 settles the convention for the whole file."""
    assert infer_date_order(["01/02/2026", "15/03/2026"]) is True
    assert infer_date_order(["02/15/2026", "03/20/2026"]) is False
    assert infer_date_order(["01/02/2026"]) is True  # no evidence -> default


@pytest.mark.parametrize("text,value,direction", [
    ("1,23,456.78", "123456.78", None),      # Indian grouping
    ("123,456.78", "123456.78", None),       # Western grouping
    ("1.234,56", "1234.56", None),           # European decimal
    ("₹5,000.00 Cr", "5000.00", "credit"),
    ("45,000.00 DR", "45000.00", "debit"),
    ("Dr 1234.56", "1234.56", "debit"),
    ("(500.00)", "500.00", "debit"),         # accounting negative
    ("-500", "500", "debit"),
    ("-", None, None),
    ("", None, None),
])
def test_parse_amount(text, value, direction):
    parsed = parse_amount(text)
    assert parsed.value == (Decimal(value) if value else None)
    assert parsed.explicit_direction == direction


def test_amount_magnitude_only_but_balance_keeps_sign():
    """Amounts are magnitudes; balances are not. An overdraft is really negative."""
    assert parse_amount("-4,500.00").value == Decimal("4500.00")
    assert parse_signed_amount("-4,500.00") == Decimal("-4500.00")
    assert parse_signed_amount("4,500.00") == Decimal("4500.00")


def test_account_numbers_are_redacted_without_eating_spacing():
    out = redact_account_numbers("POS 4728123456789012 SWIGGY BANGALORE")
    assert "4728123456789012" not in out
    assert "XXXX9012" in out
    assert "SWIGGY" in out and " SWIGGY" in out


def test_rail_prefixes_are_stripped_to_a_stable_merchant():
    """Two spellings of the same merchant must reduce to the same key."""
    a = extract_merchant("UPI/SWIGGY/928374652/Payment")
    b = extract_merchant("POS 4728 SWIGGY BANGALORE")
    assert "SWIGGY" in a and "SWIGGY" in b


def test_column_mapping_prefers_specific_headers():
    mapping = map_columns(
        ["Date", "Narration", "Chq/Ref No", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
    )
    assert mapping.is_usable()
    assert mapping.split_amount_columns
    assert mapping.get("txn_date") == 0
    assert mapping.get("description") == 1
    assert mapping.get("debit") == 3
    assert mapping.get("credit") == 4
    assert mapping.get("balance") == 5


# --------------------------------------------------------------------------
# Ingestion: every format, exact counts
# --------------------------------------------------------------------------

@pytest.mark.parametrize("filename,count,account_type,opening,closing", EXPECTED)
def test_every_format_parses_to_ground_truth(filename, count, account_type, opening, closing):
    _require_samples()
    path = SAMPLES / filename
    statement, account = normalize(router.extract(path), path.name)

    assert len(statement.transactions) == count, f"{filename} row count"
    assert account.account_type == account_type
    if opening is not None:
        assert statement.opening_balance == Decimal(opening)
        assert statement.closing_balance == Decimal(closing)


def test_same_account_two_formats_agree():
    """The XLSX and CSV exports of one account must produce identical ledgers."""
    _require_samples()
    xlsx, _ = normalize(router.extract(SAMPLES / "hdfc_savings_2025_2026.xlsx"),
                        "hdfc_savings_2025_2026.xlsx")
    csv, _ = normalize(router.extract(SAMPLES / "hdfc_savings_2025_2026.csv"),
                       "hdfc_savings_2025_2026.csv")

    assert len(xlsx.transactions) == len(csv.transactions)
    assert (sum(t.signed_amount for t in xlsx.transactions)
            == sum(t.signed_amount for t in csv.transactions))


# --------------------------------------------------------------------------
# The reconciliation gate
# --------------------------------------------------------------------------

def test_clean_statements_reconcile_exactly():
    _require_samples()
    for filename, _, account_type, opening, _ in EXPECTED:
        statement, account = normalize(router.extract(SAMPLES / filename), filename)
        result = reconcile(statement, account.account_type)
        if opening is None:
            assert result.status == ReconciliationStatus.NOT_APPLICABLE
        else:
            assert result.status == ReconciliationStatus.PASSED, f"{filename}: {result.message}"
            assert result.discrepancy == Decimal("0.00")


@pytest.fixture(scope="module")
def savings():
    _require_samples()
    # Real filename: the app always passes the uploaded name through, and the
    # filename is the strongest account-type signal there is.
    return normalize(router.extract(SAMPLES / "hdfc_savings_2025_2026.csv"),
                     "hdfc_savings_2025_2026.csv")


def test_gate_catches_a_dropped_row(savings):
    statement, account = savings
    broken = copy.deepcopy(statement)
    dropped = broken.transactions.pop(57)

    result = reconcile(broken, account.account_type)
    assert result.status == ReconciliationStatus.FAILED
    assert abs(result.discrepancy) == dropped.amount
    assert "duplicated or dropped" in result.message


def test_gate_catches_a_duplicated_row(savings):
    statement, account = savings
    broken = copy.deepcopy(statement)
    broken.transactions.append(copy.deepcopy(broken.transactions[12]))

    result = reconcile(broken, account.account_type)
    assert result.status == ReconciliationStatus.FAILED


def test_gate_catches_a_flipped_direction(savings):
    """A debit read as a credit is wrong by exactly twice its own value."""
    statement, account = savings
    broken = copy.deepcopy(statement)
    txn = broken.transactions[30]
    txn.direction = (Direction.CREDIT if txn.direction == Direction.DEBIT
                     else Direction.DEBIT)

    result = reconcile(broken, account.account_type)
    assert result.status == ReconciliationStatus.FAILED
    assert abs(result.discrepancy) == txn.amount * 2
    assert "debit read as a credit" in result.message


def test_gate_localises_a_decimal_shift(savings):
    statement, account = savings
    broken = copy.deepcopy(statement)
    broken.transactions[80].amount *= 10

    result = reconcile(broken, account.account_type)
    assert result.status == ReconciliationStatus.FAILED
    assert result.suspect_rows, "running-balance walk should point at the bad row"


def test_gate_catches_lost_pages(savings):
    statement, account = savings
    broken = copy.deepcopy(statement)
    broken.transactions = broken.transactions[:60]
    assert reconcile(broken, account.account_type).status == ReconciliationStatus.FAILED


def test_liability_sign_convention():
    """Applying the asset formula to a card statement must not accidentally pass."""
    _require_samples()
    statement, account = normalize(
        router.extract(SAMPLES / "icici_credit_card_2025_2026.pdf"),
        "icici_credit_card_2025_2026.pdf")

    assert reconcile(statement, AccountType.CREDIT_CARD).status == ReconciliationStatus.PASSED
    assert reconcile(statement, AccountType.SAVINGS).status == ReconciliationStatus.FAILED


# --------------------------------------------------------------------------
# The full graph
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def full_run():
    _require_samples()
    files = [p for p in sorted(SAMPLES.iterdir()) if not p.name.endswith(".csv")]
    graph = build_graph()
    return graph.invoke(
        {"file_tasks": [{"path": str(p), "filename": p.name} for p in files],
         "use_llm": False, "horizon_months": 6},
        {"recursion_limit": 60},
    )


def test_graph_completes_and_merges_accounts(full_run):
    assert full_run["status"] == "complete"
    assert len(full_run["accounts"]) == 5
    assert len(full_run["transactions"]) == 666


def test_every_file_reconciles_in_the_full_run(full_run):
    failed = [s for s in full_run["statements"] if s["status"] == "failed"]
    assert not failed, failed


def test_transfers_prevent_double_counting(full_run):
    """Card payments, EMIs and SIP mirrors must all be paired."""
    report = full_run["transfer_report"]
    kinds = {p.kind for p in report.pairs}
    assert {"cc_payment", "loan_repayment", "investment"} <= kinds
    assert report.double_count_avoided > 0


def test_investment_total_is_counted_exactly_once(full_run):
    """40,000/month of SIPs over 12 months is 480,000 - not 960,000.

    Both the bank statement and the fund statement record every purchase. This
    is the assertion that catches the regression where uploading MORE of your
    own statements makes the numbers WORSE.
    """
    assert full_run["analysis"].total_invested == Decimal("480000.00")


def test_income_excludes_transfer_mirror_legs(full_run):
    """A card bill payment arriving on the card statement is not income."""
    analysis = full_run["analysis"]
    # Salary 168,400 x 12 + freelance 85,000 x 2 + bonus 340,000 + interest.
    assert Decimal("2500000") < analysis.total_income < Decimal("2600000")


def test_no_transaction_is_both_income_and_spend(full_run):
    for txn in full_run["transactions"]:
        if txn.is_spend:
            assert txn.direction == Direction.DEBIT
            assert not txn.is_internal_transfer


def test_rules_categorize_nearly_everything(full_run):
    txns = full_run["transactions"]
    unknown = [t for t in txns if t.category == Category.UNCATEGORIZED]
    assert len(unknown) / len(txns) < 0.05, f"{len(unknown)} uncategorized"


def test_loan_projections_are_arithmetically_sound(full_run):
    projections = {p.label: p for p in full_run["loan_projections"]}
    assert len(projections) == 2

    for projection in projections.values():
        assert projection.months_remaining > 0
        assert projection.payoff_date is not None
        # Every EMI splits into exactly interest + principal.
        for row in projection.schedule[:5]:
            assert row.interest + row.principal == pytest.approx(row.emi, abs=0.01)
            assert row.opening - row.principal == pytest.approx(row.closing, abs=0.01)

    home = next(p for p in projections.values() if "Home" in p.label)
    assert home.annual_rate == Decimal("8.75")
    # Early in a 17-year loan most of the payment is interest.
    assert home.next_interest_share > 0.7


def test_forecast_is_internally_consistent(full_run):
    forecast = full_run["forecast"]
    assert len(forecast.months) == 6

    for month in forecast.months:
        expected_net = (month.committed_income - month.committed_outflow
                        - month.discretionary_expected)
        assert month.net_expected == pytest.approx(expected_net, abs=0.01)
        assert month.discretionary_low <= month.discretionary_expected <= month.discretionary_high
        assert month.closing_balance_low <= month.closing_balance_expected

    # Committed outflow must not exceed what any real person could pay, which
    # is what happened when card bills were counted alongside card purchases.
    assert forecast.commitment_ratio < 1.5


def test_recurring_finds_the_real_commitments(full_run):
    labels = " ".join(s.label.upper() for s in full_run["recurring"])
    for expected in ("SALARY", "RENT", "HOME LOAN", "SIP"):
        assert expected in labels, f"missed recurring: {expected}"

    # No series should be a mirror leg of another account's record.
    assert all(s.confidence >= 0.25 for s in full_run["recurring"])


def test_monthly_totals_reconcile_to_the_period_totals(full_run):
    analysis = full_run["analysis"]
    assert sum(m.income for m in analysis.monthly) == analysis.total_income
    assert sum(m.spend for m in analysis.monthly) == analysis.total_spend


def test_per_month_category_totals_match_the_period_breakdown(full_run):
    """The trend chart's numbers must be the same numbers as the totals."""
    analysis = full_run["analysis"]
    from collections import defaultdict

    rolled = defaultdict(Decimal)
    for month in analysis.monthly_by_category.values():
        for category, amount in month.items():
            rolled[category] += amount

    for row in analysis.by_category:
        assert rolled[row.category] == row.total, row.category


# --------------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------------

def test_unsupported_and_broken_files_fail_softly(tmp_path):
    junk = tmp_path / "notes.rtf"
    junk.write_text("this is not a statement")
    result = router.extract(junk)
    assert result.warnings and not result.tables

    corrupt = tmp_path / "broken.pdf"
    corrupt.write_bytes(b"%PDF-1.4 truncated garbage")
    result = router.extract(corrupt)
    assert result.warnings  # reported, not raised


def test_empty_transaction_list_analyzes_without_crashing():
    from app.analytics.engine import analyze
    result = analyze([], {})
    assert result.transaction_count == 0
    assert result.notes


def test_account_type_ignores_transaction_narrations():
    """A savings statement must not be relabelled by its own EMI rows.

    Every EMI row narrates "HOME LOAN EMI". Reading account type from the body
    turns the savings account into a loan, which flips its sign convention and
    makes a correct statement fail reconciliation.
    """
    _require_samples()
    extraction = router.extract(SAMPLES / "hdfc_savings_2025_2026.csv")
    assert "HOME LOAN" in extraction.full_text.upper()

    # Even with a filename carrying no signal, it must not become a loan.
    _, account = normalize(extraction, "statement.csv")
    assert account.account_type not in {
        AccountType.HOME_LOAN, AccountType.PERSONAL_LOAN, AccountType.CREDIT_CARD,
    }


def test_letterhead_stops_at_the_first_transaction_row():
    from app.normalize.metadata import letterhead

    text = """HDFC BANK LTD
Account Type: SAVINGS ACCOUNT
01/09/2025  ACH-D- HDFC LTD HOME LOAN EMI  38420.00
05/09/2025  CREDIT CARD PAYMENT  12000.00
"""
    head = letterhead(text)
    assert 'SAVINGS' in head
    assert 'HOME LOAN' not in head


# --------------------------------------------------------------------------
# The same account uploaded twice, in two formats
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def run_with_duplicate_format():
    """Every fixture INCLUDING the CSV export of the savings XLSX."""
    _require_samples()
    files = sorted(SAMPLES.iterdir())
    return build_graph().invoke(
        {"file_tasks": [{"path": str(p), "filename": p.name} for p in files],
         "use_llm": False, "horizon_months": 6},
        {"recursion_limit": 60},
    )


def test_same_account_in_two_formats_collapses_to_one(run_with_duplicate_format):
    """Exporting one account as XLSX and CSV must not create two accounts.

    This is the highest-consequence bug in the whole app: two account records
    for one real account means every shared transaction is stored twice, and
    income, spending and investments all silently double while still looking
    entirely plausible.
    """
    state = run_with_duplicate_format
    assert len(state["accounts"]) == 5
    assert len(state["transactions"]) == 666
    assert state["duplicate_count"] == 202


def test_duplicate_upload_does_not_change_any_total(run_with_duplicate_format, full_run):
    """The extra file must leave every figure exactly as it was without it."""
    with_dupe = run_with_duplicate_format["analysis"]
    without = full_run["analysis"]

    assert with_dupe.total_income == without.total_income
    assert with_dupe.total_spend == without.total_spend
    assert with_dupe.total_invested == without.total_invested
    assert with_dupe.savings_rate == without.savings_rate


def test_institution_survives_underscored_filenames():
    """"\bhdfc\b" does not match inside "hdfc_savings_2025.csv"."""
    from app.normalize.metadata import detect_institution

    assert detect_institution("hdfc_savings_2025_2026.csv") == "HDFC Bank"
    assert detect_institution("icici-credit-card.pdf") == "ICICI Bank"
    # A bare substring must still not match: "cred" lives inside "credit".
    assert detect_institution("monthly credit summary.pdf") != "CRED"


def test_account_identity_ignores_a_missing_institution():
    """One file naming the bank and another not is still one account."""
    from app.graph.nodes import _account_identity

    named = Account(institution="HDFC Bank", account_type=AccountType.SAVINGS,
                    account_number_masked="XXXX8842")
    unnamed = Account(institution="Unknown", account_type=AccountType.SAVINGS,
                      account_number_masked="XXXX8842")
    assert _account_identity(named) == _account_identity(unnamed)

    other = Account(institution="HDFC Bank", account_type=AccountType.SAVINGS,
                    account_number_masked="XXXX1111")
    assert _account_identity(named) != _account_identity(other)


# --------------------------------------------------------------------------
# Transaction query filters (multi-select, sort, UPI rail)
# --------------------------------------------------------------------------

def _seed_transactions(db, rows):
    """rows: list of (account_id, date, amount, direction, description)."""
    from app.db import repository as repo
    from app.models.schemas import Direction as Dir
    txns = []
    for i, (acct, day, amount, direction, desc) in enumerate(rows):
        txns.append(Transaction(
            id=f"t{i}", account_id=acct, txn_date=day, amount=Decimal(str(amount)),
            direction=Dir(direction), raw_description=desc,
            normalized_description=desc.upper(),
        ))
    repo.save_transactions(db, txns)
    return txns


def test_multi_account_filter_uses_an_in_clause(tmp_path):
    """Selecting several accounts must return rows from ALL of them, not just
    the first - a bare '= ?' silently drops every account after the first."""
    from app.db import repository as repo

    db = fresh_ledger()
    for aid in ("a1", "a2", "a3"):
        repo.upsert_account(db, Account(institution=aid, account_type=AccountType.SAVINGS,
                                        account_number_masked=aid))
    accounts = {a.institution: a.id for a in repo.get_accounts(db)}
    _seed_transactions(db, [
        (accounts["a1"], date(2025, 1, 1), 100, "debit", "shop one"),
        (accounts["a2"], date(2025, 1, 2), 200, "debit", "shop two"),
        (accounts["a3"], date(2025, 1, 3), 300, "debit", "shop three"),
    ])

    got = repo.get_transactions(db, account_id=[accounts["a1"], accounts["a3"]])
    assert {t.account_id for t in got} == {accounts["a1"], accounts["a3"]}
    assert repo.count_transactions(db, account_id=[accounts["a1"], accounts["a3"]]) == 2


def test_sort_by_amount_descending(tmp_path):
    from app.db import repository as repo

    db = fresh_ledger()
    repo.upsert_account(db, Account(institution="X", account_type=AccountType.SAVINGS,
                                    account_number_masked="X1"))
    aid = repo.get_accounts(db)[0].id
    _seed_transactions(db, [
        (aid, date(2025, 1, 1), 50, "debit", "small"),
        (aid, date(2025, 1, 2), 500, "debit", "big"),
        (aid, date(2025, 1, 3), 150, "debit", "medium"),
    ])
    got = repo.get_transactions(db, sort_by="amount", sort_dir="desc")
    assert [t.raw_description for t in got] == ["big", "medium", "small"]


def test_upi_rail_filter(tmp_path):
    """The card/UPI split: a UPI transaction can happen on ANY account type,
    not just a wallet, so the filter has to key off the narration."""
    from app.db import repository as repo

    db = fresh_ledger()
    repo.upsert_account(db, Account(institution="X", account_type=AccountType.CREDIT_CARD,
                                    account_number_masked="X1"))
    aid = repo.get_accounts(db)[0].id
    _seed_transactions(db, [
        (aid, date(2025, 1, 1), 10, "debit", "UPI/SWIGGY/12345/order"),
        (aid, date(2025, 1, 2), 20, "debit", "POS 4728 AMAZON"),
    ])
    upi_only = repo.get_transactions(db, rail="upi")
    assert len(upi_only) == 1 and "SWIGGY" in upi_only[0].raw_description

    non_upi = repo.get_transactions(db, rail="non_upi")
    assert len(non_upi) == 1 and "AMAZON" in non_upi[0].raw_description


def test_statement_id_filter_drills_into_one_file(tmp_path):
    from app.db import repository as repo

    db = fresh_ledger()
    repo.upsert_account(db, Account(institution="X", account_type=AccountType.SAVINGS,
                                    account_number_masked="X1"))
    aid = repo.get_accounts(db)[0].id
    from app.models.schemas import Statement
    repo.save_statement(db, Statement(id="stmt-A", source_filename="a.pdf"), aid)
    repo.save_statement(db, Statement(id="stmt-B", source_filename="b.pdf"), aid)
    txns = _seed_transactions(db, [
        (aid, date(2025, 1, 1), 10, "debit", "from file A"),
        (aid, date(2025, 1, 2), 20, "debit", "from file B"),
    ])
    txns[0].statement_id = "stmt-A"
    txns[1].statement_id = "stmt-B"
    repo.save_transactions(db, txns)  # INSERT OR REPLACE - same ids, updated row

    only_a = repo.get_transactions(db, statement_id="stmt-A")
    assert len(only_a) == 1 and only_a[0].raw_description == "from file A"
    assert repo.count_transactions(db, statement_id="stmt-B") == 1


# --------------------------------------------------------------------------
# Source file registry (password cache, parse status, retry bookkeeping)
# --------------------------------------------------------------------------

def test_source_file_upserts_by_content_hash_not_filename(tmp_path):
    """Gmail attachment names are not stable across a re-download; the file's
    bytes are. Keying on the hash is what lets the SAME file re-appear under a
    different name and still be recognised as already seen."""
    from app.db import repository as repo

    db = fresh_ledger()
    first_id = repo.upsert_source_file(db, repo.SourceFileRecord(
        id="ignored", filename="statement_v1.pdf", file_hash="abc123",
        parse_status="parsed",
    ))
    second_id = repo.upsert_source_file(db, repo.SourceFileRecord(
        id="ignored2", filename="statement_v1_renamed.pdf", file_hash="abc123",
        parse_status="parsed",
    ))
    assert first_id == second_id
    assert len(repo.list_source_files(db)) == 1
    assert repo.get_source_file(db, first_id).filename == "statement_v1_renamed.pdf"


def test_working_password_is_never_erased_by_a_later_failed_attempt(tmp_path):
    """A retry that fails (e.g. the profile's DOB was cleared) must not forget
    a password that is known to work - that would make the file permanently
    slower to open again for no reason."""
    from app.db import repository as repo

    db = fresh_ledger()
    fid = repo.upsert_source_file(db, repo.SourceFileRecord(
        id="f1", filename="card.pdf", file_hash="h1",
        password="jite0602", password_status="open", parse_status="parsed",
    ))
    repo.upsert_source_file(db, repo.SourceFileRecord(
        id="f1", filename="card.pdf", file_hash="h1",
        password=None, password_status="locked", parse_status="failed",
    ))
    record = repo.get_source_file(db, fid)
    assert record.password == "jite0602"
    assert record.parse_status == "failed"  # status itself does update


def test_get_cached_password_returns_none_when_never_solved(tmp_path):
    from app.db import repository as repo

    db = fresh_ledger()
    assert repo.get_cached_password(db, "never-seen-hash") is None
    repo.upsert_source_file(db, repo.SourceFileRecord(
        id="f1", filename="x.pdf", file_hash="h2", password="secret99",
    ))
    assert repo.get_cached_password(db, "h2") == "secret99"


def test_transfer_pairs_do_not_duplicate_across_repeated_saves(tmp_path):
    """detect_transfers mints a fresh uuid4 pair_id every run, so saving the
    same logical pair twice must not leave two rows behind - a retry endpoint
    that recomputes transfers over the whole ledger calls this every time."""
    from types import SimpleNamespace
    from app.db import repository as repo

    db = fresh_ledger()
    repo.upsert_account(db, Account(institution="X", account_type=AccountType.SAVINGS,
                                    account_number_masked="X1"))
    aid = repo.get_accounts(db)[0].id
    txns = _seed_transactions(db, [
        (aid, date(2025, 1, 1), 100, "debit", "to card"),
        (aid, date(2025, 1, 2), 100, "credit", "from savings"),
    ])
    pair = SimpleNamespace(pair_id="p-1", debit_txn_id=txns[0].id,
                           credit_txn_id=txns[1].id, amount=Decimal("100"),
                           day_gap=1, kind="cc_payment", confidence=0.9)
    repo.save_transfer_pairs(db, [pair])
    # Same logical pair, freshly re-detected with a NEW random id, as a retry
    # endpoint would produce on a second pass over the same ledger.
    pair_again = SimpleNamespace(pair_id="p-2", debit_txn_id=txns[0].id,
                                 credit_txn_id=txns[1].id, amount=Decimal("100"),
                                 day_gap=1, kind="cc_payment", confidence=0.9)
    repo.save_transfer_pairs(db, [pair_again])

    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM transfer_pairs").fetchall()
    assert len(rows) == 1


# --------------------------------------------------------------------------
# Files API (registry list, per-file drill-down, retry)
# --------------------------------------------------------------------------

def _isolated_app_db(tmp_path, name="files_api.db"):
    """Point the app's global DB singleton at a throwaway file for one test.

    Returns (client, db). Caller does not need to restore anything - use as
    the fixture pattern: `db_module._db = original` is handled by the caller's
    own try/finally, mirroring test_saving_the_profile_form_does_not_wipe_.
    """
    from app.db import database as db_module
    from fastapi.testclient import TestClient
    import app.main as main_module

    db_module._db = fresh_ledger()
    return TestClient(main_module.app), db_module._db


def test_files_endpoint_lists_every_attempted_file(tmp_path):
    """A locked or failed file has nowhere else to live - this list is the
    only place it can be seen and retried from."""
    from app.db import database as db_module
    from app.db import repository as repo

    original = db_module._db
    try:
        client, db = _isolated_app_db(tmp_path)
        repo.upsert_source_file(db, repo.SourceFileRecord(
            id="f1", filename="locked.pdf", file_hash="h1",
            parse_status="needs_password", password_status="locked",
        ))
        repo.upsert_source_file(db, repo.SourceFileRecord(
            id="f2", filename="ok.pdf", file_hash="h2",
            parse_status="parsed", password_status="not_encrypted",
        ))
        resp = client.get("/api/files")
        assert resp.status_code == 200
        rows = resp.json()
        assert {r["filename"] for r in rows} == {"locked.pdf", "ok.pdf"}
        locked = next(r for r in rows if r["filename"] == "locked.pdf")
        assert locked["parse_status"] == "needs_password"
        # The raw password is never in this response, even when known.
        assert "password" not in locked
    finally:
        db_module._db = original


def test_file_transactions_drill_down(tmp_path):
    from app.db import database as db_module
    from app.db import repository as repo

    original = db_module._db
    try:
        client, db = _isolated_app_db(tmp_path)
        repo.upsert_account(db, Account(institution="X", account_type=AccountType.SAVINGS,
                                        account_number_masked="X1"))
        aid = repo.get_accounts(db)[0].id
        from app.models.schemas import Statement
        repo.save_statement(db, Statement(id="stmt-1", source_filename="a.pdf"), aid)
        _seed_transactions(db, [(aid, date(2025, 1, 1), 10, "debit", "row one")])
        txns = repo.get_transactions(db)
        txns[0].statement_id = "stmt-1"
        repo.save_transactions(db, txns)
        repo.upsert_source_file(db, repo.SourceFileRecord(
            id="f1", filename="a.pdf", parse_status="parsed",
            statement_id="stmt-1", transaction_count=1,
        ))

        resp = client.get("/api/files/f1/transactions")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["transactions"]) == 1
        assert body["transactions"][0]["description"].startswith("row one") or \
               "row one" in body["transactions"][0]["description"]

        assert client.get("/api/files/does-not-exist/transactions").status_code == 404
    finally:
        db_module._db = original


def test_retry_reports_conflict_when_file_missing_from_disk(tmp_path):
    from app.db import database as db_module
    from app.db import repository as repo

    original = db_module._db
    try:
        client, db = _isolated_app_db(tmp_path)
        repo.upsert_source_file(db, repo.SourceFileRecord(
            id="f1", filename="gone.pdf", filepath=str(tmp_path / "gone.pdf"),
            parse_status="failed",
        ))
        resp = client.post("/api/files/f1/retry")
        assert resp.status_code == 409
    finally:
        db_module._db = original


def test_retry_parses_a_previously_failed_file_into_the_ledger(tmp_path):
    """The end-to-end happy path: a file marked failed gets a real second
    chance, and the result lands in the actual accounts/transactions tables -
    not just a response payload."""
    _require_samples()
    import shutil
    from app.db import database as db_module
    from app.db import repository as repo

    original = db_module._db
    try:
        client, db = _isolated_app_db(tmp_path)
        sample = SAMPLES / "hdfc_savings_2025_2026.csv"
        local_copy = tmp_path / "hdfc_savings_2025_2026.csv"
        shutil.copy(sample, local_copy)

        repo.upsert_source_file(db, repo.SourceFileRecord(
            id="f1", filename=local_copy.name, filepath=str(local_copy),
            parse_status="failed", error_message="transient error",
        ))

        resp = client.post("/api/files/f1/retry")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["transaction_count"] == 202

        assert repo.count_transactions(db) == 202
        record = repo.get_source_file(db, "f1")
        assert record.parse_status == "parsed"
        assert record.statement_id is not None
        assert record.account_id is not None  # backfilled from the statement
    finally:
        db_module._db = original


def test_upi_rail_filter_still_respects_the_account_filter(tmp_path):
    """Regression: an unparenthesized OR inside the rail clause let SQL's
    AND-binds-tighter-than-OR rule silently drop the account_id restriction
    for every lowercase "upi"-prefixed row, so a card's UPI-only view pulled
    in UPI rows from every OTHER account too - a scoped view showing MORE
    rows than the same accounts' unfiltered view."""
    from app.db import repository as repo

    db = fresh_ledger()
    for aid in ("card", "other"):
        repo.upsert_account(db, Account(institution=aid, account_type=AccountType.CREDIT_CARD,
                                        account_number_masked=aid))
    accounts = {a.institution: a.id for a in repo.get_accounts(db)}
    _seed_transactions(db, [
        (accounts["card"], date(2025, 1, 1), 10, "debit", "UPI/SWIGGY/1/order"),
        (accounts["card"], date(2025, 1, 2), 20, "debit", "POS 100 AMAZON"),
        (accounts["other"], date(2025, 1, 3), 30, "debit", "upi-zomato-order"),
    ])

    scoped = repo.get_transactions(db, account_id=accounts["card"], rail="upi")
    assert len(scoped) == 1
    assert scoped[0].raw_description.startswith("UPI/SWIGGY")

    assert repo.count_transactions(db, account_id=accounts["card"], rail="upi") == 1


def test_dashboard_rebuilds_from_persisted_data_after_a_restart(tmp_path):
    """A server restart clears the in-memory run cache but not the database -
    every transaction already carries its computed category and transfer
    flags, so the dashboard should recompute from those, not tell the user to
    re-parse every PDF just to see figures that already exist."""
    from app.db import database as db_module
    from app.db import repository as repo

    import app.main as main_module

    original = db_module._db
    original_runs = main_module.runs
    try:
        # The in-memory run cache is a process-global singleton, so a run
        # left behind by an earlier test would otherwise be returned here
        # instead of ever reaching the rebuild-from-database path this test
        # means to exercise.
        main_module.runs = main_module.RunStore()
        client, db = _isolated_app_db(tmp_path, "rebuild.db")
        repo.upsert_account(db, Account(institution="X", account_type=AccountType.SAVINGS,
                                        account_number_masked="X1"))
        aid = repo.get_accounts(db)[0].id
        _seed_transactions(db, [
            (aid, date(2025, 1, 1), 1000, "credit", "SALARY CREDIT"),
            (aid, date(2025, 1, 2), 200, "debit", "GROCERY STORE"),
        ])

        # No run has ever been created in-memory - this simulates a fresh
        # process that only has the database, exactly like a restart.
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["analysis"]["totals"]["transaction_count"] == 2
    finally:
        db_module._db = original
        main_module.runs = original_runs


# --------------------------------------------------------------------------
# Coverage grid: fetching a missing month straight from Gmail
# --------------------------------------------------------------------------

def test_fetch_one_month_finds_and_merges_the_right_statement(tmp_path, monkeypatch):
    """End-to-end against a fake mailbox: search, download, verify identity
    AND month, then merge - all offline, no real Gmail needed."""
    _require_samples()
    from app.db import database as db_module
    from app.db import repository as repo
    from app.ingestion.gmail_source import FakeGmailClient
    from app.ingestion import router as ingest_router
    from app.normalize.normalizer import normalize
    import app.api.gmail_routes as gmail_routes_module
    import app.api.files_routes as files_routes_module

    original_db = db_module._db
    original_client = gmail_routes_module._require_client
    original_cache = gmail_routes_module.CACHE
    try:
        db = fresh_ledger()
        db_module._db = db
        monkeypatch.setattr(storage, "GMAIL_CACHE", tmp_path / "cache")

        sample_path = SAMPLES / "icici_credit_card_2025_2026.pdf"

        # Discover the sample's real period so the test targets a month it
        # genuinely covers, rather than guessing.
        statement, account = normalize(ingest_router.extract(sample_path), sample_path.name)
        month = f"{statement.period_start.year:04d}-{statement.period_start.month:02d}"

        db_account_id = repo.upsert_account(db, account)

        fake = FakeGmailClient.from_files([
            ("alerts@icicibank.com", "Your Credit Card Statement",
             "icici_card.pdf", sample_path.read_bytes()),
        ])
        monkeypatch.setattr(gmail_routes_module, "_require_client", lambda: fake)

        result = files_routes_module._fetch_one_month(
            "job-1", db_account_id, month)
        assert result["status"] == "ok", result
        assert result["transaction_count"] == 392
        assert repo.count_transactions(db) == 392

        record = repo.list_source_files(db)[0]
        assert record.parse_status == "parsed"
        assert record.account_id == db_account_id
    finally:
        db_module._db = original_db
        gmail_routes_module._require_client = original_client
        gmail_routes_module.CACHE = original_cache


def test_fetch_one_month_rejects_a_wrong_month_match(tmp_path, monkeypatch):
    """A candidate email that parses fine but covers the WRONG month must be
    reported as not found, not silently merged into the wrong grid cell."""
    _require_samples()
    from app.db import database as db_module
    from app.db import repository as repo
    from app.ingestion.gmail_source import FakeGmailClient
    from app.ingestion import router as ingest_router
    from app.normalize.normalizer import normalize
    import app.api.gmail_routes as gmail_routes_module
    import app.api.files_routes as files_routes_module

    original_db = db_module._db
    original_client = gmail_routes_module._require_client
    original_cache = gmail_routes_module.CACHE
    try:
        db = fresh_ledger()
        db_module._db = db
        monkeypatch.setattr(storage, "GMAIL_CACHE", tmp_path / "cache2")

        sample_path = SAMPLES / "icici_credit_card_2025_2026.pdf"
        statement, account = normalize(ingest_router.extract(sample_path), sample_path.name)
        db_account_id = repo.upsert_account(db, account)

        # A month the sample statement does NOT cover.
        wrong_month = "1999-01"

        fake = FakeGmailClient.from_files([
            ("alerts@icicibank.com", "Your Credit Card Statement",
             "icici_card.pdf", sample_path.read_bytes()),
        ])
        monkeypatch.setattr(gmail_routes_module, "_require_client", lambda: fake)

        result = files_routes_module._fetch_one_month(
            "job-2", db_account_id, wrong_month)
        assert result["status"] == "failed"
        assert repo.count_transactions(db) == 0
    finally:
        db_module._db = original_db
        gmail_routes_module._require_client = original_client
        gmail_routes_module.CACHE = original_cache


def test_fail_path_resolves_to_existing_row_when_hash_already_seen(tmp_path):
    """Regression: upsert_source_file resolves identity by content hash
    FIRST - a second attempt at the exact same file content must not crash
    trying to re-read a row under the wrong (freshly-minted) id.

    This is exactly what a wrong-month Gmail candidate does on a second
    fetch attempt: same PDF bytes, same content hash, a brand new record id
    each time - and the failure path used to look up the record by that new
    id instead of whatever id the write actually landed on.
    """
    from app.db import repository as repo
    from app.ingestion import router as ingest_router
    from app.api.files_routes import merge_extracted_file_into_ledger

    db = fresh_ledger()
    sample_path = SAMPLES / "icici_credit_card_2025_2026.pdf"
    _require_samples()
    extraction = ingest_router.extract(sample_path)
    digest = ingest_router.file_hash(sample_path)

    for _ in range(2):
        record = repo.SourceFileRecord(
            id=str(__import__("uuid").uuid4()), filename=sample_path.name,
            filepath=str(sample_path), source="gmail",
        )
        result = merge_extracted_file_into_ledger(
            db, record, extraction, None, "not_encrypted", digest,
            target_month="1999-01",  # a month the statement never covers
        )
        assert result["status"] == "failed"
        assert result["file"]["id"]  # must resolve to a real row, not crash

    # Both attempts must have landed on the SAME row, not created two.
    assert len(repo.list_source_files(db)) == 1


def test_two_same_bank_cards_coexist_when_only_the_product_name_differs():
    """The reason the accounts unique key includes `product_name`.

    HSBC masks its card number so completely no digit survives extraction. On
    the old three-column key both of someone's HSBC cards hashed to the same
    account identity, so the second one violated the unique index and the
    insert failed outright - and before that, silently merged two cards'
    transactions into one account.
    """
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType

    db = fresh_ledger()
    id_a = repo.upsert_account(db, Account(
        institution="New Bank", account_type=AccountType.CREDIT_CARD,
        product_name="Rewards"))
    id_b = repo.upsert_account(db, Account(
        institution="New Bank", account_type=AccountType.CREDIT_CARD,
        product_name="Privilege"))
    assert id_a != id_b

    # And the same card seen again attaches to the account it already has,
    # rather than minting a third.
    assert repo.upsert_account(db, Account(
        institution="New Bank", account_type=AccountType.CREDIT_CARD,
        product_name="Rewards")) == id_a


def test_the_same_account_identity_can_exist_in_two_accounts_at_once():
    """One person's HDFC savings account must not collide with another's.

    The unique key is per user. Before that it was global, and the second
    person to upload a statement from the same bank - with the same masked
    number, which is only four digits - would have had the insert refused.
    """
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType

    identity = dict(institution="HDFC", account_type=AccountType.SAVINGS,
                    account_number_masked="XXXX1111")

    first = fresh_ledger()
    mine = repo.upsert_account(first, Account(**identity))

    second = fresh_ledger()
    theirs = repo.upsert_account(second, Account(**identity))

    assert mine != theirs
    # And neither can see the other's.
    assert [a.institution for a in repo.get_accounts(second)] == ["HDFC"]


def test_a_files_link_to_its_account_survives_a_round_trip():
    """source_files.account_id is a foreign key into the user's own accounts."""
    from app.db import repository as repo

    db = fresh_ledger()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO accounts (id, institution, account_type,"
            " account_number_masked) VALUES (?, ?, ?, ?)",
            ("acc-1", "Old Bank", "savings", "XXXX1111"))

    repo.upsert_source_file(db, repo.SourceFileRecord(
        id="file-1", filename="old.pdf", file_hash="h1", account_id="acc-1"))
    repo.upsert_source_file(db, repo.SourceFileRecord(
        id="file-2", filename="new.pdf", file_hash="h2", parse_status="parsed"))

    assert len(repo.list_source_files(db)) == 2
    with db.connection() as conn:
        linked = conn.execute(
            "SELECT account_id FROM source_files WHERE id = 'file-1'").fetchone()
    assert linked["account_id"] == "acc-1"
