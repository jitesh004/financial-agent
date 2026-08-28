"""Regressions for defects found by auditing Workstreams 2-6.

Each test here corresponds to a bug that was live in a suite reporting 266
green. That is the point of the file: the existing tests passed *because* they
asserted the buggy behaviour, or picked inputs that stepped around it. These
assert the property the feature was supposed to deliver.
"""

from __future__ import annotations

import sys
import tempfile
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics.engine import analyze  # noqa: E402
from app.analytics.periods import assign_accounting_months  # noqa: E402
from app.analytics.recurring import RecurringSeries  # noqa: E402
from app.db.database import CLEAR_SCOPES, Database  # noqa: E402
from app.models.schemas import (Category, Direction, FlowRole,  # noqa: E402
                                Transaction, derive_flow_role)


def _credit(amount, category, desc="CREDIT", day=4):
    return Transaction(
        id=f"c{amount}{category}", account_id="a1", txn_date=date(2026, 3, day),
        raw_description=desc, amount=Decimal(str(amount)),
        direction=Direction.CREDIT, category=category,
    )


def _salary_series(txns, label="SALARY"):
    return RecurringSeries(
        id="r1", account_id="a1", label=label, category=Category.SALARY,
        direction=Direction.CREDIT, median_amount=Decimal("200000"),
        cadence_days=30, cadence_name="monthly", occurrences=len(txns),
        first_seen=txns[0].txn_date, last_seen=txns[-1].txn_date,
        next_expected=None, is_active=True, confidence=0.9,
        transaction_ids=[t.id for t in txns],
    )


def _salaries(dates):
    return [
        Transaction(id=f"s{i}", account_id="a1", txn_date=d,
                    raw_description="SALARY CREDIT", amount=Decimal("200000"),
                    direction=Direction.CREDIT, category=Category.SALARY)
        for i, d in enumerate(dates)
    ]


# --------------------------------------------------------------------------
# A credit carrying a spending category is money back, not earnings
# --------------------------------------------------------------------------

def test_a_credit_with_a_spending_category_is_not_income():
    """A 48,181 credit against an "education" merchant is a fee reversal or a
    reimbursement - never salary. Booking it as income inflated income AND
    left the original charge standing in spending.

    On the real ledger this was 22 rows worth 85,208.
    """
    for category in (Category.EDUCATION, Category.SHOPPING, Category.INSURANCE,
                     Category.TRAVEL, Category.HEALTHCARE, Category.FUEL):
        txn = _credit(1000, category)
        assert derive_flow_role(txn) == FlowRole.REFUND, (
            f"a credit categorised {category} was counted as income")


def test_a_reversal_nets_against_the_spending_it_undoes():
    """The charge and its reversal must cancel, leaving neither income nor
    net spending behind."""
    charge = Transaction(
        id="d", account_id="a1", txn_date=date(2026, 3, 1),
        raw_description="ADYPUEDU PUNE", amount=Decimal("48181"),
        direction=Direction.DEBIT, category=Category.EDUCATION)
    reversal = _credit(48181, Category.EDUCATION, desc="FEE REVERSAL", day=20)

    result = analyze([charge, reversal], {})
    assert result.total_income == Decimal("0.00"), "a reversal is not income"
    assert result.gross_spend == Decimal("48181.00")
    assert result.total_offsets == Decimal("48181.00")
    assert result.total_spend == Decimal("0.00"), "the pair should cancel"


def test_a_genuinely_unexplained_credit_still_counts_as_income():
    """The safe direction. Uncategorized money, or money from a named person,
    is ambiguous - and silently removing real income is worse than showing a
    figure that is too high, because too-high is visible and too-low is not."""
    for category in (Category.UNCATEGORIZED, Category.P2P_TRANSFER):
        assert derive_flow_role(_credit(5000, category)) == FlowRole.INCOME


def test_a_card_settlement_is_never_income_whoever_funded_it():
    """The original bug: someone else paying the user's card was booked as
    their salary, because an unmatched credit fell through to OTHER_INCOME."""
    txn = _credit(50000, Category.CC_PAYMENT, desc="PAYMENT RECEIVED")
    assert derive_flow_role(txn) == FlowRole.CARD_SETTLEMENT
    assert analyze([txn], {}).total_income == Decimal("0.00")


# --------------------------------------------------------------------------
# Salary drift: the collision guard has to prevent, not annotate
# --------------------------------------------------------------------------

def test_salary_paid_at_both_ends_of_a_month_does_not_double_up():
    """The exact failure the period engine exists to prevent.

    Pay lands 31-Aug and again 1-Sep. Shifting the September payment back to
    August - which the drift rule does on its own - puts two salaries in
    August and none in September, which is worse than leaving them alone. The
    guard has to put the loser back, not merely flag it.
    """
    txns = _salaries([date(2025, 6, 30), date(2025, 7, 31), date(2025, 8, 31),
                      date(2025, 9, 1), date(2025, 10, 31), date(2025, 11, 28)])
    assign_accounting_months(txns, [_salary_series(txns)])

    months = Counter(t.accounting_month for t in txns)
    assert not [m for m, n in months.items() if n > 1], (
        f"a month received two salaries: {dict(months)}")
    assert len(months) == len(txns), "every salary should have its own month"


def test_drift_correction_still_fills_an_empty_month():
    """The guard must not be so blunt it disables the feature: when September
    genuinely holds August's pay and August is empty, the shift must happen."""
    txns = _salaries([date(2025, 6, 30), date(2025, 7, 31), date(2025, 9, 1),
                      date(2025, 9, 30), date(2025, 10, 31)])
    assign_accounting_months(txns, [_salary_series(txns)])

    by_date = {t.txn_date: t.accounting_month for t in txns}
    assert by_date[date(2025, 9, 1)] == "2025-08", (
        "the 1-Sep payment is August's salary and should fill the empty month")
    assert by_date[date(2025, 9, 30)] == "2025-09"


def test_drift_correction_handles_a_year_boundary():
    txns = _salaries([date(2025, 12, 1), date(2025, 12, 31), date(2026, 2, 1),
                      date(2026, 3, 1)])
    assign_accounting_months(txns, [_salary_series(txns)])
    months = Counter(t.accounting_month for t in txns)
    assert not [m for m, n in months.items() if n > 1], dict(months)


def test_a_one_off_is_never_moved():
    txn = Transaction(id="x", account_id="a1", txn_date=date(2025, 7, 31),
                      raw_description="ONE OFF", amount=Decimal("5000"),
                      direction=Direction.DEBIT, category=Category.SHOPPING)
    assign_accounting_months([txn], [])
    assert txn.accounting_month == "2025-07"


# --------------------------------------------------------------------------
# The settlement confidence floor has to be reachable
# --------------------------------------------------------------------------

def test_multi_leg_confidence_can_fall_below_the_floor():
    """It was clamped with max(FLOOR, ...), which made the floor unreachable
    and the review branch dead code - every multi-leg match was applied
    automatically however speculative."""
    from app.reconcile.settlement import (CONFIDENCE_FLOOR, MAX_LEGS_PER_SIDE,
                                          _group_confidence)

    floor = float(CONFIDENCE_FLOOR)
    scores = [_group_confidence(n) for n in range(1, MAX_LEGS_PER_SIDE + 1)]
    assert scores == sorted(scores, reverse=True), "confidence must decay"
    assert scores[0] >= floor, "a single-leg match should be trusted"
    assert any(s < floor for s in scores), (
        "no group size can reach the floor, so the review path is unreachable")


# --------------------------------------------------------------------------
# The coverage gate has to be fed
# --------------------------------------------------------------------------

def test_enrich_supplies_statement_periods_to_the_coverage_gate():
    """Every caller omitted `statements_by_account`, so it defaulted to None,
    so the gate always answered "no coverage" and the distinction it exists to
    draw never happened."""
    from app.db import repository as repo
    from app.pipeline import enrich as enrich_mod

    db = Database(Path(tempfile.mkdtemp()) / "cov.db")
    seen = {}

    original = enrich_mod.__dict__.get("match_settlements")
    import app.reconcile.settlement as settlement_mod
    real = settlement_mod.match_settlements

    def spy(txns, accounts, db_, statements_by_account=None, **kw):
        seen["value"] = statements_by_account
        return real(txns, accounts, db_,
                    statements_by_account=statements_by_account, **kw)

    settlement_mod.match_settlements = spy
    try:
        txn = Transaction(id="t", account_id="a1", txn_date=date(2026, 3, 4),
                          raw_description="SWIGGY", amount=Decimal("450"),
                          direction=Direction.DEBIT, category=Category.DINING)
        enrich_mod.enrich_ledger(db, [txn], {}, run_analysis=False)
    finally:
        settlement_mod.match_settlements = real

    assert "value" in seen, "settlement matching was never called"
    assert seen["value"] is not None, (
        "the coverage gate was handed None, so it can only ever say "
        "'no coverage'")


def test_statement_periods_loader_groups_by_account():
    from app.db import repository as repo

    db = Database(Path(tempfile.mkdtemp()) / "periods.db")
    with db.connection() as conn:
        conn.execute("INSERT INTO accounts (id, institution) VALUES ('a1','Axis')")
        conn.execute(
            """INSERT INTO statements (id, account_id, source_filename,
                                       period_start, period_end)
               VALUES ('s1','a1','x.pdf','2026-03-01','2026-03-31')""")

    grouped = repo.get_statement_periods_by_account(db)
    assert "a1" in grouped
    assert grouped["a1"][0].period_start == date(2026, 3, 1)
    assert grouped["a1"][0].period_end == date(2026, 3, 31)


# --------------------------------------------------------------------------
# Redaction: real letterheads, not invented ones
# --------------------------------------------------------------------------

REAL_LETTERHEADS = {
    "ICICI Amazon Pay": "Mr Jitesh Agarwal\nA1004,,Utsav homes\n,Pune nashik "
                        "road\nBhosari\nMAHARASHTRA, PUNE 411039",
    "ICICI HPCL": "MR JITESH AGARWAL\nM3 KALSAGAR SHRI RAM COLONY\n"
                  "ALANDI ROAD BHOSARI",
    "Axis savings": "01/11/2025 to 30/11/2025\nJITESH MUKESH AGARWAL\n"
                    "Customer ID: XXXXX4254",
    "IDFC card": "Credit Card Statement\nJITESH MUKESH AGARWAL\nStatement Summary",
    "UPI narration": "UPI/Jitesh Muk/jitesh@okaxis/self transfer/HDFC",
}


def test_the_holder_name_never_survives_redaction():
    """Checked against the four letterhead formats this workspace actually
    receives, not invented ones. The previous pattern required a literal
    period after the honorific ("Mr\\."), and real statements print "Mr Jitesh
    Agarwal" with none - so every format sailed through."""
    from app.llm.client import redact

    names = ["Jitesh Mukesh Agarwal"]
    for label, text in REAL_LETTERHEADS.items():
        out = redact(text, names=names)
        for fragment in ("Jitesh", "JITESH", "Agarwal", "AGARWAL", "Mukesh",
                         "MUKESH"):
            assert fragment not in out, f"{label}: {fragment!r} leaked"


def test_redaction_survives_a_bare_name_with_no_honorific():
    """Two of the formats print the name alone on its own line, which no
    honorific rule can catch - hence matching against known holder names."""
    from app.llm.client import redact

    out = redact("JITESH MUKESH AGARWAL", names=["Jitesh Mukesh Agarwal"])
    assert "JITESH" not in out and "AGARWAL" not in out


def test_redaction_leaves_ordinary_statement_text_alone():
    """Over-redaction is a real risk: the accounts table holds junk holder
    names like ". (Monday To Friday Between 9:30 A.M. ...)", and feeding those
    in would strike "Monday" out of every statement the model reads."""
    from app.llm.client import redact

    text = ("STATEMENT DATE\nMonday To Friday Between 9:30 AM\n"
            "AMAZON PAY IN E COMMERC BANGALORE\nTotal Amount due")
    assert redact(text, names=["Jitesh Mukesh Agarwal"]) == text


def test_junk_holder_names_are_not_used_for_redaction():
    from app.llm.client import _looks_like_a_person_name

    for junk in ("S", "S.", ". (Monday To Friday Between 9:30 A.M.)",
                 "Willnotbeheldliableforanytransaction",
                 "Mr. Jitesh Mukesh Agarwal Account Branch Pune - Bhosari Branch",
                 "Account 12345"):
        assert not _looks_like_a_person_name(junk), f"{junk!r} accepted"

    for real in ("Jitesh Mukesh Agarwal", "Jitesh Agarwal", "Pooja Roha"):
        assert _looks_like_a_person_name(real), f"{real!r} rejected"


def test_redaction_still_strips_the_original_identifiers():
    from app.llm.client import redact

    text = ("Card 4315 1234 5678 5001\nPAN BJXPA1234R\n"
            "mail jitesh@example.com\nphone 9876543210")
    out = redact(text, names=[])
    assert "4315 1234 5678 5001" not in out
    assert "BJXPA1234R" not in out
    assert "jitesh@example.com" not in out
    assert "9876543210" not in out


# --------------------------------------------------------------------------
# Clearing scopes must account for every table
# --------------------------------------------------------------------------

def test_every_table_belongs_to_some_clearing_scope():
    """`custom_categories` and `recurring_series_overrides` were in none, so a
    factory reset left them behind and the workspace did not actually return
    to its first-run state."""
    db = Database(Path(tempfile.mkdtemp()) / "scopes.db")
    with db.connection() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")}

    covered: set[str] = set()
    for names in CLEAR_SCOPES.values():
        covered |= set(names)

    assert not (tables - covered), (
        f"no clearing scope reaches {sorted(tables - covered)}")
    assert not (covered - tables), (
        f"a scope names tables that do not exist: {sorted(covered - tables)}")


def test_a_factory_reset_empties_every_table():
    db = Database(Path(tempfile.mkdtemp()) / "factory.db")
    with db.connection() as conn:
        conn.execute("INSERT INTO custom_categories (name) VALUES ('Pets')")
        conn.execute(
            "INSERT INTO user_overrides (fingerprint, category)"
            " VALUES ('fp','groceries')")
        conn.execute(
            "INSERT INTO user_profile (id, full_name) VALUES ('me','Someone')")

    db.clear("everything")
    with db.connection() as conn:
        for table in ("custom_categories", "user_overrides", "user_profile"):
            assert conn.execute(
                f"SELECT COUNT(*) c FROM {table}").fetchone()["c"] == 0, (
                f"{table} survived a factory reset")


def test_routine_scopes_never_reach_a_user_decision():
    """Re-parsing is the routine action and has to stay safe to run without
    thinking about it."""
    protected = {"user_overrides", "claims", "claim_settlements",
                 "transaction_splits", "split_rules", "settlement_groups",
                 "settlement_group_legs", "custom_categories",
                 "recurring_series_overrides", "ai_inferences",
                 "merchant_categories", "user_profile"}
    for scope in ("derived", "parsed_data", "files"):
        overlap = set(CLEAR_SCOPES[scope]) & protected
        assert not overlap, f"{scope} would destroy {sorted(overlap)}"
