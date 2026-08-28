"""Tests for Workstream 2 (accounting model) and Workstream 3 (period engine).

Covers settlement matching, claims/splits, flow role derivation, period
attribution with salary drift, and the coverage gate.
"""

import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics.engine import analyze
from app.analytics.periods import (
    assign_accounting_months,
    circular_median_day,
    _circular_distance,
)
from app.analytics.recurring import RecurringSeries
from app.db.database import Database
from app.db import repository as repo
from app.models.schemas import (
    Account, AccountType, Category, ConfidenceSource,
    Direction, FlowRole, Transaction, derive_flow_role,
    CONTRA_EXPENSE_ROLES, NEUTRAL_ROLES,
)
from app.reconcile.settlement import (
    match_settlements,
    SettlementReport,
    PAYMENT_RAIL_PATTERNS,
)
from app.pipeline.enrich import enrich_ledger


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _txn(
    amount, direction=Direction.DEBIT, category=Category.UNCATEGORIZED,
    account_id="savings_1", txn_date=None, description="",
    is_internal_transfer=False, is_mirror_leg=False, flow_role="",
    fingerprint=None, excluded=False,
):
    txn_date = txn_date or date(2025, 8, 15)
    return Transaction(
        id=str(uuid.uuid4()),
        account_id=account_id,
        txn_date=txn_date,
        raw_description=description,
        normalized_description=description,
        amount=Decimal(str(amount)),
        direction=direction,
        category=category,
        category_source=ConfidenceSource.DEFAULT,
        is_internal_transfer=is_internal_transfer,
        is_mirror_leg=is_mirror_leg,
        flow_role=flow_role,
        fingerprint=fingerprint or str(uuid.uuid4()),
        excluded=excluded,
    )


def _accounts():
    return {
        "savings_1": Account(
            id="savings_1", institution="HDFC",
            account_type=AccountType.SAVINGS,
            account_number_masked="1234",
        ),
        "cc_axis": Account(
            id="cc_axis", institution="Axis",
            account_type=AccountType.CREDIT_CARD,
            account_number_masked="5678",
        ),
        "cc_hdfc": Account(
            id="cc_hdfc", institution="HDFC",
            account_type=AccountType.CREDIT_CARD,
            account_number_masked="9012",
        ),
        "cc_icici": Account(
            id="cc_icici", institution="ICICI",
            account_type=AccountType.CREDIT_CARD,
            account_number_masked="3456",
        ),
    }


import tempfile

def _db():
    return Database(Path(tempfile.mkdtemp()) / "accounting.db")


# ==========================================================================
# FlowRole derivation
# ==========================================================================

def test_card_settlement_credit_is_never_income():
    """A 'payment received' on a card is CARD_SETTLEMENT, not INCOME."""
    txn = _txn(25000, Direction.CREDIT, Category.CC_PAYMENT, account_id="cc_axis")
    role = derive_flow_role(txn)
    assert role == FlowRole.CARD_SETTLEMENT
    assert role not in {FlowRole.INCOME, FlowRole.EXPENSE}


def test_unmatched_card_credit_with_cc_payment_category():
    """Even without a matching bank debit, a card credit categorised as
    CC_PAYMENT is CARD_SETTLEMENT — never income."""
    txn = _txn(15000, Direction.CREDIT, Category.CC_PAYMENT, account_id="cc_axis")
    assert txn.role == FlowRole.CARD_SETTLEMENT
    assert not txn.is_spend


def test_matched_transfer_pair_excludes_from_spend():
    """Both legs of an internal transfer have neutral roles."""
    debit = _txn(25000, Direction.DEBIT, Category.CC_PAYMENT,
                 is_internal_transfer=True, is_mirror_leg=False)
    credit = _txn(25000, Direction.CREDIT, Category.CC_PAYMENT,
                  account_id="cc_axis",
                  is_internal_transfer=True, is_mirror_leg=True)

    assert debit.role == FlowRole.TRANSFER_OUT
    assert credit.role == FlowRole.TRANSFER_IN
    assert not debit.is_spend
    assert not credit.is_spend


def test_refund_is_contra_expense():
    txn = _txn(500, Direction.CREDIT, Category.REFUND)
    assert txn.role == FlowRole.REFUND
    assert txn.role in CONTRA_EXPENSE_ROLES


def test_excluded_transaction():
    txn = _txn(1000, excluded=True)
    assert txn.role == FlowRole.EXCLUDED


def test_investment_debit_via_transfer():
    """An investment debit that is an internal transfer mirror leg gets a neutral role."""
    txn = _txn(5000, Direction.DEBIT, Category.INVESTMENT,
               is_internal_transfer=True, is_mirror_leg=True)
    assert txn.role == FlowRole.TRANSFER_IN


# ==========================================================================
# Settlement matching — 1:1
# ==========================================================================

def test_settlement_1_1_exact():
    """A bank debit and a card credit of the same amount within 7 days match."""
    accounts = _accounts()
    bank_debit = _txn(25000, Direction.DEBIT, account_id="savings_1",
                      txn_date=date(2025, 8, 14), description="CRED CARD PAYMENT")
    card_credit = _txn(25000, Direction.CREDIT, Category.CC_PAYMENT,
                       account_id="cc_axis", txn_date=date(2025, 8, 15),
                       description="PAYMENT RECEIVED")

    report = match_settlements([bank_debit, card_credit], accounts, db=None)
    assert len(report.groups) == 1
    g = report.groups[0]
    assert g.outflow_legs == [bank_debit]
    assert g.inflow_legs == [card_credit]
    assert g.confidence >= 0.85
    # Legs are properly flagged.
    assert card_credit.is_mirror_leg is True
    assert bank_debit.is_mirror_leg is False
    assert card_credit.flow_role == FlowRole.CARD_SETTLEMENT.value
    assert bank_debit.flow_role == FlowRole.TRANSFER_OUT.value


def test_settlement_1_1_no_match_different_amount():
    """Amounts that differ by more than tolerance do not match."""
    accounts = _accounts()
    bank_debit = _txn(25000, Direction.DEBIT, account_id="savings_1",
                      txn_date=date(2025, 8, 14))
    card_credit = _txn(24000, Direction.CREDIT, Category.CC_PAYMENT,
                       account_id="cc_axis", txn_date=date(2025, 8, 15))

    report = match_settlements([bank_debit, card_credit], accounts, db=None)
    assert len(report.groups) == 0


def test_settlement_1_1_no_match_same_account():
    """A debit and credit on the same account cannot form a settlement."""
    accounts = _accounts()
    bank_debit = _txn(25000, Direction.DEBIT, account_id="savings_1",
                      txn_date=date(2025, 8, 14))
    card_credit = _txn(25000, Direction.CREDIT, Category.CC_PAYMENT,
                       account_id="savings_1", txn_date=date(2025, 8, 15))

    report = match_settlements([bank_debit, card_credit], accounts, db=None)
    assert len(report.groups) == 0


# ==========================================================================
# Settlement matching — 1:N (CRED paying multiple cards)
# ==========================================================================

def test_settlement_1_n_cred():
    """One CRED debit paying three card credits."""
    accounts = _accounts()
    bank_debit = _txn(35000, Direction.DEBIT, account_id="savings_1",
                      txn_date=date(2025, 8, 14),
                      description="CRED DREAMPLUG BILL PAYMENT")
    cc1 = _txn(10000, Direction.CREDIT, Category.CC_PAYMENT,
               account_id="cc_axis", txn_date=date(2025, 8, 14),
               description="PAYMENT RECEIVED")
    cc2 = _txn(15000, Direction.CREDIT, Category.CC_PAYMENT,
               account_id="cc_hdfc", txn_date=date(2025, 8, 15),
               description="PAYMENT RECEIVED")
    cc3 = _txn(10000, Direction.CREDIT, Category.CC_PAYMENT,
               account_id="cc_icici", txn_date=date(2025, 8, 14),
               description="PAYMENT RECEIVED")

    report = match_settlements(
        [bank_debit, cc1, cc2, cc3], accounts, db=None
    )
    assert len(report.groups) == 1
    g = report.groups[0]
    assert len(g.inflow_legs) == 3
    assert g.outflow_legs == [bank_debit]
    assert g.total_amount == Decimal("35000")


def test_settlement_negative_no_rail_narration():
    """A coincidental subset sum with no payment-rail narration must NOT match.

    This is the critical negative test from the spec: arithmetic alone is
    never sufficient evidence.
    """
    accounts = _accounts()
    # This debit has no CRED/BBPS/card narration — it could be anything.
    bank_debit = _txn(35000, Direction.DEBIT, account_id="savings_1",
                      txn_date=date(2025, 8, 14),
                      description="NEFT TO JOHN DOE SAVINGS")
    cc1 = _txn(10000, Direction.CREDIT, Category.CC_PAYMENT,
               account_id="cc_axis", txn_date=date(2025, 8, 14))
    cc2 = _txn(15000, Direction.CREDIT, Category.CC_PAYMENT,
               account_id="cc_hdfc", txn_date=date(2025, 8, 15))
    cc3 = _txn(10000, Direction.CREDIT, Category.CC_PAYMENT,
               account_id="cc_icici", txn_date=date(2025, 8, 14))

    report = match_settlements(
        [bank_debit, cc1, cc2, cc3], accounts, db=None
    )
    # 1:1 won't match (no single credit == 35000), and 1:N is gated by rail.
    multi_leg_groups = [g for g in report.groups if len(g.inflow_legs) > 1]
    assert len(multi_leg_groups) == 0


# ==========================================================================
# Settlement matching — N:1
# ==========================================================================

def test_settlement_n_1_part_payments():
    """Two bank debits summing to one card credit."""
    accounts = _accounts()
    d1 = _txn(15000, Direction.DEBIT, account_id="savings_1",
              txn_date=date(2025, 8, 14), description="CRED CARD PAYMENT")
    d2 = _txn(10000, Direction.DEBIT, account_id="savings_1",
              txn_date=date(2025, 8, 15), description="NEFT AXIS CREDIT CARD")
    cc = _txn(25000, Direction.CREDIT, Category.CC_PAYMENT,
              account_id="cc_axis", txn_date=date(2025, 8, 15),
              description="PAYMENT RECEIVED")

    report = match_settlements([d1, d2, cc], accounts, db=None)
    assert len(report.groups) == 1
    g = report.groups[0]
    assert len(g.outflow_legs) == 2
    assert g.inflow_legs == [cc]


# ==========================================================================
# Unmatched card credits — coverage gate
# ==========================================================================

def test_unmatched_card_credit_without_coverage():
    """An unmatched card credit without bank coverage → unknown_funding."""
    accounts = _accounts()
    cc = _txn(15000, Direction.CREDIT, Category.CC_PAYMENT,
              account_id="cc_axis", txn_date=date(2025, 8, 15))

    report = match_settlements([cc], accounts, db=None,
                               statements_by_account=None)
    assert len(report.review_items) >= 1
    reasons = [r for _, r in report.review_items]
    assert any("unknown_funding" in r for r in reasons)


# ==========================================================================
# Claims & splits (repository CRUD)
# ==========================================================================

def test_claim_lifecycle():
    """Create → partial settle → full settle."""
    db = _db()
    claim_id = repo.save_claim(
        db, direction="owed_to_me", counterparty="Alice",
        origin_fingerprint="fp1", amount=Decimal("5000"),
        opened_on="2025-08-15",
    )
    claim = repo.get_claim(db, claim_id)
    assert claim is not None
    assert claim["status"] == "open"
    assert claim["amount"] == Decimal("5000")
    assert claim["settled_amount"] == Decimal("0")

    # Partial settlement.
    repo.settle_claim(
        db, claim_id, method="bank_inflow",
        amount=Decimal("2000"), settled_on="2025-08-20",
    )
    claim = repo.get_claim(db, claim_id)
    assert claim["status"] == "partial"
    assert claim["settled_amount"] == Decimal("2000")

    # Full settlement.
    repo.settle_claim(
        db, claim_id, method="cash",
        amount=Decimal("3000"), settled_on="2025-08-25",
    )
    claim = repo.get_claim(db, claim_id)
    assert claim["status"] == "settled"
    assert claim["settled_amount"] == Decimal("5000")


def test_claim_cash_settlement_no_ledger_row():
    """A cash settlement closes the claim with no ledger fingerprint."""
    db = _db()
    claim_id = repo.save_claim(
        db, direction="owed_to_me", counterparty="Bob",
        origin_fingerprint="fp2", amount=Decimal("10000"),
        opened_on="2025-08-10",
    )
    repo.settle_claim(
        db, claim_id, method="cash",
        amount=Decimal("10000"), settled_on="2025-08-20",
    )
    settlements = repo.get_claim_settlements(db, claim_id)
    assert len(settlements) == 1
    assert settlements[0]["method"] == "cash"
    assert settlements[0]["txn_fingerprint"] == ""  # no ledger row


def test_split_invariant_enforced():
    """Splits that don't sum to the parent amount are rejected."""
    db = _db()
    import pytest
    with pytest.raises(ValueError, match="Splits sum"):
        repo.save_splits(
            db, "parent_fp", parent_amount=Decimal("10000"),
            splits=[
                {"amount": Decimal("6000"), "note": "mine"},
                {"amount": Decimal("3000"), "note": "theirs"},
            ],
        )


def test_split_invariant_passes():
    """Splits that sum to the parent amount are saved successfully."""
    db = _db()
    ids = repo.save_splits(
        db, "parent_fp", parent_amount=Decimal("10000"),
        splits=[
            {"amount": Decimal("6000"), "note": "mine"},
            {"amount": Decimal("4000"), "note": "theirs"},
        ],
    )
    assert len(ids) == 2
    splits = repo.get_splits(db, "parent_fp")
    assert len(splits) == 2
    total = sum(s["amount"] for s in splits)
    assert total == Decimal("10000")


def test_claims_filter_by_status():
    db = _db()
    repo.save_claim(db, direction="owed_to_me", counterparty="A",
                    origin_fingerprint="fp_a", amount=Decimal("1000"),
                    opened_on="2025-08-01")
    cid = repo.save_claim(db, direction="owed_to_me", counterparty="B",
                          origin_fingerprint="fp_b", amount=Decimal("500"),
                          opened_on="2025-08-02")
    repo.settle_claim(db, cid, method="cash", amount=Decimal("500"),
                      settled_on="2025-08-03")

    open_claims = repo.get_claims(db, status="open")
    assert len(open_claims) == 1
    settled = repo.get_claims(db, status="settled")
    assert len(settled) == 1


# ==========================================================================
# Settlement group persistence
# ==========================================================================

def test_settlement_group_roundtrip():
    db = _db()
    repo.save_settlement_group(
        db, group_id="g1", kind="card_settlement",
        total_amount=Decimal("25000"), residual=Decimal("0"),
        confidence=0.95, note="test",
        legs=[("fp_bank", "outflow"), ("fp_card", "inflow")],
    )
    groups = repo.get_confirmed_groups(db)
    assert len(groups) == 1
    assert groups[0]["total_amount"] == Decimal("25000")
    assert len(groups[0]["legs"]) == 2

    fps = repo.get_confirmed_fingerprints(db)
    assert fps == {"fp_bank", "fp_card"}


# ==========================================================================
# Period engine — circular median
# ==========================================================================

def test_circular_median_month_boundary():
    """Days clustering around month boundary: [31, 1, 30, 2] → ~31 or 1."""
    dates = [date(2025, 7, 31), date(2025, 9, 1),
             date(2025, 10, 30), date(2025, 12, 2)]
    median = circular_median_day(dates)
    # Should be near 31 or 1, NOT ~16.
    assert median >= 29 or median <= 3


def test_circular_median_mid_month():
    """Days clustering mid-month: [14, 15, 16, 15] → ~15."""
    dates = [date(2025, 1, 14), date(2025, 2, 15),
             date(2025, 3, 16), date(2025, 4, 15)]
    median = circular_median_day(dates)
    assert 14 <= median <= 16


def test_circular_distance():
    assert _circular_distance(1, 31) == 1  # wraps around
    assert _circular_distance(15, 15) == 0
    assert _circular_distance(1, 16) == 15


# ==========================================================================
# Period engine — salary drift
# ==========================================================================

def test_salary_drift_no_double_month():
    """Salary on 31-Jul and 1-Sep → exactly one per accounting month."""
    series = RecurringSeries(
        id="salary_1", account_id="savings_1", label="Salary",
        category=Category.SALARY, direction=Direction.CREDIT,
        median_amount=Decimal("100000"), cadence_days=30,
        cadence_name="monthly", occurrences=3,
        first_seen=date(2025, 6, 30), last_seen=date(2025, 9, 1),
        next_expected=date(2025, 10, 1), is_active=True,
        confidence=0.95,
        transaction_ids=["t_jun", "t_jul", "t_sep"],
    )
    t_jun = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 6, 30))
    t_jun.id = "t_jun"
    t_jul = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 7, 31))
    t_jul.id = "t_jul"
    t_sep = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 9, 1))
    t_sep.id = "t_sep"

    transactions = [t_jun, t_jul, t_sep]
    assign_accounting_months(transactions, [series])

    months = [t.accounting_month for t in transactions]
    # Each salary should land in a different accounting month.
    assert len(set(months)) == len(months), f"Duplicate months: {months}"


def test_one_offs_never_shifted():
    """Non-recurring transactions are never shifted."""
    txn = _txn(5000, txn_date=date(2025, 7, 31))
    assign_accounting_months([txn], [])
    assert txn.accounting_month == "2025-07"


def test_salary_drift_year_boundary():
    """Salary on 2-Jan: circular median handles year boundary."""
    series = RecurringSeries(
        id="salary_y", account_id="savings_1", label="Salary",
        category=Category.SALARY, direction=Direction.CREDIT,
        median_amount=Decimal("100000"), cadence_days=30,
        cadence_name="monthly", occurrences=4,
        first_seen=date(2024, 9, 30), last_seen=date(2025, 1, 2),
        next_expected=date(2025, 2, 1), is_active=True,
        confidence=0.95,
        transaction_ids=["y_sep", "y_oct", "y_nov", "y_jan"],
    )
    t_sep = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2024, 9, 30))
    t_sep.id = "y_sep"
    t_oct = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2024, 10, 31))
    t_oct.id = "y_oct"
    t_nov = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2024, 11, 29))
    t_nov.id = "y_nov"
    t_jan = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 1, 2))
    t_jan.id = "y_jan"

    transactions = [t_sep, t_oct, t_nov, t_jan]
    assign_accounting_months(transactions, [series])

    months = [t.accounting_month for t in transactions]
    assert len(set(months)) == 4, f"Duplicate months: {months}"
    # Jan 2nd should shift to December since anchor is ~31/1.
    assert t_jan.accounting_month == "2024-12"


# ==========================================================================
# Period engine — collision guard
# ==========================================================================

def test_collision_guard_flags_duplicate():
    """Two salaries in one accounting month after drift → one gets needs_review."""
    series = RecurringSeries(
        id="salary_c", account_id="savings_1", label="Salary",
        category=Category.SALARY, direction=Direction.CREDIT,
        median_amount=Decimal("100000"), cadence_days=30,
        cadence_name="monthly", occurrences=3,
        first_seen=date(2025, 7, 28), last_seen=date(2025, 9, 2),
        next_expected=date(2025, 10, 1), is_active=True,
        confidence=0.95,
        transaction_ids=["c_1", "c_2", "c_3"],
    )
    t1 = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 7, 28))
    t1.id = "c_1"
    t2 = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 8, 1))
    t2.id = "c_2"
    t3 = _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 9, 2))
    t3.id = "c_3"

    transactions = [t1, t2, t3]
    assign_accounting_months(transactions, [series])

    # Check that collision guard marked at least one for review.
    months = [t.accounting_month for t in transactions]
    from collections import Counter
    dupes = {m for m, c in Counter(months).items() if c > 1}
    if dupes:
        # If there are duplicates, at least one should be flagged.
        flagged = [t for t in transactions if t.needs_review]
        assert len(flagged) >= 1


# ==========================================================================
# Engine — date range support
# ==========================================================================

def test_analyze_with_date_range():
    """analyze() filters to the requested range when start/end are given."""
    txns = [
        _txn(10000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 7, 1)),
        _txn(5000, Direction.DEBIT, Category.DINING, txn_date=date(2025, 7, 15)),
        _txn(10000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 8, 1)),
        _txn(6000, Direction.DEBIT, Category.DINING, txn_date=date(2025, 8, 15)),
    ]
    # Request only August.
    result = analyze(txns, start=date(2025, 8, 1), end=date(2025, 8, 31))
    assert result.transaction_count == 2
    assert result.period_start == date(2025, 8, 1)


def test_analyze_empty_range():
    """analyze() with no transactions in range returns a note."""
    txns = [_txn(10000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 7, 1))]
    result = analyze(txns, start=date(2025, 9, 1), end=date(2025, 9, 30))
    assert result.transaction_count == 0
    assert any("range" in n.lower() for n in result.notes)


# ==========================================================================
# Engine — internal_transfer_total fix
# ==========================================================================

def test_internal_transfer_total_no_double_count():
    """internal_transfer_total should count each transfer ONCE, not both legs."""
    debit = _txn(25000, Direction.DEBIT, Category.CC_PAYMENT,
                 is_internal_transfer=True, is_mirror_leg=False,
                 flow_role=FlowRole.TRANSFER_OUT.value)
    credit = _txn(25000, Direction.CREDIT, Category.CC_PAYMENT,
                  account_id="cc_axis",
                  is_internal_transfer=True, is_mirror_leg=True,
                  flow_role=FlowRole.TRANSFER_IN.value)

    result = analyze([debit, credit])
    # Previously this was 50000 (both legs). Now it should be 25000 (one leg).
    assert result.internal_transfer_total == Decimal("25000")


# ==========================================================================
# Flow role stamping in pipeline
# ==========================================================================

def test_flow_role_stamped_after_enrichment():
    """Every transaction has an explicit flow_role after enrich_ledger."""
    db = _db()
    txns = [
        _txn(100000, Direction.CREDIT, Category.SALARY, txn_date=date(2025, 8, 1)),
        _txn(5000, Direction.DEBIT, Category.DINING, txn_date=date(2025, 8, 10)),
        _txn(3000, Direction.DEBIT, Category.GROCERIES, txn_date=date(2025, 8, 15)),
    ]
    accounts = {"savings_1": Account(
        id="savings_1", institution="HDFC",
        account_type=AccountType.SAVINGS,
        account_number_masked="1234",
    )}

    result = enrich_ledger(db, txns, accounts, run_analysis=False)
    for txn in result.transactions:
        assert txn.flow_role, f"flow_role empty on {txn.raw_description}"


# ==========================================================================
# Payment rail detection
# ==========================================================================

def test_payment_rail_patterns():
    """CRED, BBPS, card payment narrations are detected."""
    assert PAYMENT_RAIL_PATTERNS.search("CRED DREAMPLUG BBPS")
    assert PAYMENT_RAIL_PATTERNS.search("NEFT TO HDFC CREDIT CARD")
    assert PAYMENT_RAIL_PATTERNS.search("BILLPAY CC PAYMENT XXXX5678")
    assert not PAYMENT_RAIL_PATTERNS.search("NEFT TO JOHN DOE SAVINGS ACCOUNT")
