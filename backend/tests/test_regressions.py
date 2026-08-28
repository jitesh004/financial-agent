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


# --------------------------------------------------------------------------
# Route registration order
# --------------------------------------------------------------------------

def test_bulk_endpoint_is_not_shadowed_by_the_id_route():
    """FastAPI matches routes in declaration order, so a literal segment must
    be declared before the parameterised one that would swallow it.

    With /{txn_id} first, every request to /bulk resolved as a transaction
    whose id is the string "bulk" and answered 404. The endpoint existed,
    was tested at the function level, and was unreachable over HTTP.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    # An empty id list is a no-op, so this asserts reachability rather than
    # relying on any particular row existing.
    res = client.patch("/api/transactions/bulk",
                       json={"txn_ids": [], "category": "shopping"})
    assert res.status_code != 404, "the /bulk route is shadowed by /{txn_id}"
    assert res.status_code == 200, res.text


def test_bulk_endpoint_reads_its_body_not_the_query_string():
    """The request model has to be defined ABOVE its endpoint. FastAPI
    resolves the annotation when the route is registered; with the class
    declared later the name did not exist yet, so `payload` was treated as a
    scalar query parameter and every call failed validation with 422."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    res = client.patch("/api/transactions/bulk",
                       json={"txn_ids": [], "note": "probe"})
    assert res.status_code != 422, (
        "the body was not recognised as a model - check declaration order")


def test_bulk_update_actually_changes_the_named_rows(tmp_path):
    """Reachability is necessary but not sufficient - the rows have to move."""
    from fastapi.testclient import TestClient
    import app.db.database as db_module
    from app.db import repository as repo
    from app import main as main_module
    from app.models.schemas import Account, AccountType

    previous = db_module._db
    try:
        db_module._db = Database(tmp_path / "bulk.db")
        db = db_module._db
        repo.upsert_account(db, Account(
            id="acc-bulk", institution="Axis Bank",
            account_type=AccountType.CREDIT_CARD,
            account_number_masked="XXXX1111", product_name="Test"))
        account_id = repo.get_accounts(db)[0].id

        repo.save_transactions(db, [Transaction(
            id="bulk-1", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="VSCHINCHWAD PUNE", amount=Decimal("500"),
            direction=Direction.DEBIT, category=Category.UNCATEGORIZED)])

        res = TestClient(main_module.app).patch(
            "/api/transactions/bulk",
            json={"txn_ids": ["bulk-1"], "category": "shopping"})
        assert res.status_code == 200, res.text
        assert res.json()["updated"] == 1
        assert repo.get_transactions(db)[0].category == "shopping"
    finally:
        db_module._db = previous


# --------------------------------------------------------------------------
# The single-file merge path must keep the Overview tab's figures alive
# --------------------------------------------------------------------------

def test_a_single_file_merge_populates_data_quality(tmp_path, monkeypatch):
    """_build_payload only fills `data_quality` from state["report"]["brief"],
    which only the full graph run (its `synthesize` node) ever populates.

    Retrying one failed file from the Coverage grid - or fetching one missing
    month from Gmail - never goes through that node, so this stayed {} and
    the Overview tab's entire Data Quality card (files reconciled, rules
    settled, duplicates avoided) went blank the moment anyone used the single
    most common recovery action in the whole app.
    """
    import sys
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    samples = root.parent / "data" / "samples"
    sample_path = samples / "icici_credit_card_2025_2026.pdf"
    if not sample_path.exists():
        import pytest as _pytest
        _pytest.skip("run backend/tools/generate_samples.py first")

    from app.db import database as db_module
    from app.db.database import Database
    from app.db import repository as repo
    from app.ingestion.gmail_source import FakeGmailClient
    from app.ingestion import router as ingest_router
    from app.normalize.normalizer import normalize
    import app.api.gmail_routes as gmail_routes_module
    import app.api.files_routes as files_routes_module
    import app.main as main_module

    original_db = db_module._db
    original_client = gmail_routes_module._require_client
    original_cache = gmail_routes_module.CACHE
    try:
        db = Database(tmp_path / "dataquality.db")
        db_module._db = db
        monkeypatch.setattr(gmail_routes_module, "CACHE", tmp_path / "cache")

        statement, account = normalize(
            ingest_router.extract(sample_path), sample_path.name)
        month = f"{statement.period_start.year:04d}-{statement.period_start.month:02d}"
        account_id = repo.upsert_account(db, account)

        fake = FakeGmailClient.from_files([
            ("alerts@icicibank.com", "Your Credit Card Statement",
             "icici_card.pdf", sample_path.read_bytes()),
        ])
        monkeypatch.setattr(gmail_routes_module, "_require_client", lambda: fake)

        result = files_routes_module._fetch_one_month("job-dq", account_id, month)
        assert result["status"] == "ok", result

        run = main_module.runs.latest()
        assert run is not None, "the merge should have registered a run"
        dq = run["result"]["data_quality"]
        assert dq, "data_quality was left empty - the Overview tab would go blank"
        assert dq["files_processed"] >= 1
        assert dq["files_reconciled"] >= 1
    finally:
        db_module._db = original_db
        gmail_routes_module._require_client = original_client
        gmail_routes_module.CACHE = original_cache
        main_module.runs.clear()


# --------------------------------------------------------------------------
# Claims and splits must actually change the numbers, not just log an
# intention. Both were write-only: a real table was written to, nothing else
# in the application ever read it back, and the transaction being claimed or
# split kept counting its full original amount exactly as before.
# --------------------------------------------------------------------------

def test_marking_a_transaction_as_a_claim_stops_it_counting_as_spend():
    """This is Scenario 1 from the original design conversation: a purchase
    that was never the user's should net to zero expense once claimed - not
    keep counting while a claim record quietly accumulates in a table
    nothing else reads.
    """
    from fastapi.testclient import TestClient
    from app import main as main_module
    from app.db import repository as repo
    import app.db.database as db_module
    from app.db.database import Database
    from app.models.schemas import Account, AccountType, Category, Direction, Transaction

    previous = db_module._db
    try:
        db_module._db = Database(Path(tempfile.mkdtemp()) / "x.db")
        db = db_module._db
        repo.upsert_account(db, Account(
            id="a1", institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
            account_number_masked="XXXX1111"))
        account_id = repo.get_accounts(db)[0].id

        purchase = Transaction(
            id="p1", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="CROMA ELECTRONICS", amount=Decimal("50000"),
            direction=Direction.DEBIT, category=Category.SHOPPING)
        repo.save_transactions(db, [purchase])

        client = TestClient(main_module.app)
        res = client.post(f"/api/transactions/{purchase.id}/claim", json={
            "amount": 50000, "direction": "owed_to_me", "counterparty": "Amit",
        })
        assert res.status_code == 200, res.text

        reloaded = repo.get_transactions(db)[0]
        assert reloaded.excluded is True, (
            "the claimed purchase still counts as the user's own spending")
        assert reloaded.is_spend is False

        claims = repo.get_claims(db)
        assert len(claims) == 1
        assert claims[0]["counterparty"] == "Amit"
    finally:
        db_module._db = previous


def test_a_claim_decision_survives_a_full_reenrichment():
    """The exclusion has to be durable, not a one-off row update - the same
    guarantee every other user decision gets."""
    from app import main as main_module
    from app.db import repository as repo
    import app.db.database as db_module
    from app.db.database import Database
    from app.models.schemas import Account, AccountType, Category, Direction, Transaction
    from app.pipeline.enrich import enrich_ledger
    from fastapi.testclient import TestClient

    previous = db_module._db
    try:
        db_module._db = Database(Path(tempfile.mkdtemp()) / "x.db")
        db = db_module._db
        repo.upsert_account(db, Account(
            id="a1", institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
            account_number_masked="XXXX1111"))
        account_id = repo.get_accounts(db)[0].id
        purchase = Transaction(
            id="p1", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="CROMA ELECTRONICS", amount=Decimal("50000"),
            direction=Direction.DEBIT, category=Category.SHOPPING)
        repo.save_transactions(db, [purchase])

        client = TestClient(main_module.app)
        client.post(f"/api/transactions/{purchase.id}/claim", json={
            "amount": 50000, "direction": "owed_to_me", "counterparty": "Amit",
        })

        # A fresh object with a brand-new id, exactly like a re-parse.
        reparsed = Transaction(
            id="different-uuid", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="CROMA ELECTRONICS", amount=Decimal("50000"),
            direction=Direction.DEBIT, category=Category.SHOPPING)
        accounts = {a.id: a for a in repo.get_accounts(db)}
        result = enrich_ledger(db, [reparsed], accounts, run_analysis=False)
        settled = result.transactions[0]
        assert settled.excluded is True, "the claim did not survive re-enrichment"
    finally:
        db_module._db = previous


def test_splitting_a_transaction_changes_the_category_breakdown():
    """transaction_splits was write-only: save_splits stored rows, get_splits
    could read them back, and nothing else in the app ever called either -
    dividing a transaction had no effect on a single number anywhere."""
    from fastapi.testclient import TestClient
    from app import main as main_module
    from app.db import repository as repo
    import app.db.database as db_module
    from app.db.database import Database
    from app.models.schemas import Account, AccountType, Category, Direction, Transaction
    from app.pipeline.enrich import enrich_ledger

    previous = db_module._db
    try:
        db_module._db = Database(Path(tempfile.mkdtemp()) / "x.db")
        db = db_module._db
        repo.upsert_account(db, Account(
            id="a1", institution="Axis Bank", account_type=AccountType.SAVINGS))
        account_id = repo.get_accounts(db)[0].id

        groceries = Transaction(
            id="g1", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="BIGBASKET", amount=Decimal("2000"),
            direction=Direction.DEBIT, category=Category.GROCERIES)
        repo.save_transactions(db, [groceries])

        client = TestClient(main_module.app)
        res = client.post(f"/api/transactions/{groceries.id}/split", json={
            "splits": [
                {"amount": 1000, "category": "groceries"},
                {"amount": 1000, "category": "household"},
            ],
        })
        assert res.status_code == 200, res.text

        accounts = {a.id: a for a in repo.get_accounts(db)}
        fresh = Transaction(
            id="g1-reparsed", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="BIGBASKET", amount=Decimal("2000"),
            direction=Direction.DEBIT, category=Category.GROCERIES)
        result = enrich_ledger(db, [fresh], accounts, run_analysis=True)

        assert len(result.transactions) == 2, (
            "the split should replace the parent with its two parts")
        by_cat = {t.category: t.amount for t in result.transactions}
        assert by_cat.get("groceries") == Decimal("1000")
        assert by_cat.get("household") == Decimal("1000")
        assert result.analysis.total_spend == Decimal("2000.00"), (
            "the total must still be the original amount, just recategorised")
    finally:
        db_module._db = previous


def test_split_amounts_must_sum_to_the_parent_within_rounding():
    from fastapi.testclient import TestClient
    from app import main as main_module
    from app.db import repository as repo
    import app.db.database as db_module
    from app.db.database import Database
    from app.models.schemas import Account, AccountType, Category, Direction, Transaction

    previous = db_module._db
    try:
        db_module._db = Database(Path(tempfile.mkdtemp()) / "x.db")
        db = db_module._db
        repo.upsert_account(db, Account(id="a1", institution="Axis Bank",
                                        account_type=AccountType.SAVINGS))
        account_id = repo.get_accounts(db)[0].id
        txn = Transaction(
            id="t1", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="X", amount=Decimal("450.30"),
            direction=Direction.DEBIT, category=Category.SHOPPING)
        repo.save_transactions(db, [txn])

        client = TestClient(main_module.app)
        # Splits that sum to exactly the parent, expressed as JSON floats the
        # way a browser would send them - must be accepted.
        ok = client.post(f"/api/transactions/{txn.id}/split", json={
            "splits": [{"amount": 225.15}, {"amount": 225.15}],
        })
        assert ok.status_code == 200, ok.text

        # Genuinely wrong totals must still be rejected.
        bad = client.post(f"/api/transactions/{txn.id}/split", json={
            "splits": [{"amount": 100}, {"amount": 100}],
        })
        assert bad.status_code == 400
    finally:
        db_module._db = previous


def test_settling_a_claim_uses_the_field_name_the_frontend_actually_sends():
    """SettleClaimReq required a field called `date`; Claims.jsx has only ever
    sent `settled_on`. Every click of Settle failed its request validation
    before repo.settle_claim ever ran - the button looked wired up but did
    nothing but return a 422."""
    from fastapi.testclient import TestClient
    from app import main as main_module
    from app.db import repository as repo
    import app.db.database as db_module
    from app.db.database import Database
    from app.models.schemas import Account, AccountType, Category, Direction, Transaction

    previous = db_module._db
    try:
        db_module._db = Database(Path(tempfile.mkdtemp()) / "x.db")
        db = db_module._db
        repo.upsert_account(db, Account(
            id="a1", institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
            account_number_masked="XXXX1111"))
        account_id = repo.get_accounts(db)[0].id
        purchase = Transaction(
            id="p1", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="CROMA ELECTRONICS", amount=Decimal("50000"),
            direction=Direction.DEBIT, category=Category.SHOPPING)
        repo.save_transactions(db, [purchase])

        client = TestClient(main_module.app)
        created = client.post(f"/api/transactions/{purchase.id}/claim", json={
            "amount": 50000, "direction": "owed_to_me", "counterparty": "Amit",
        })
        claim_id = created.json()["claim_id"]

        # The exact body Claims.jsx sends - `settled_on`, not `date`.
        res = client.post(f"/api/claims/{claim_id}/settle", json={
            "method": "cash", "amount": 50000,
            "settled_on": "2026-04-01", "note": "paid me back in cash",
        })
        assert res.status_code == 200, res.text

        claims = repo.get_claims(db)
        assert claims[0]["status"] == "settled"
        assert claims[0]["settled_amount"] == Decimal("50000")

        settlements = repo.get_claim_settlements(db, claim_id)
        assert settlements[0]["note"] == "paid me back in cash", (
            "the settlement note was silently discarded")
    finally:
        db_module._db = previous


def test_writing_off_a_claim_restores_the_purchase_as_real_spending():
    """Write-off is not just another way to settle a claim - it means the
    opposite of every other method: the money is confirmed never coming
    back, so the purchase must go back to counting as the user's own
    expense. The first version of settle_claim treated `write_off` exactly
    like `cash` or `bank_inflow`, so a written-off claim left its origin
    transaction excluded from spending forever, with nothing having actually
    been recovered."""
    from fastapi.testclient import TestClient
    from app import main as main_module
    from app.db import repository as repo
    import app.db.database as db_module
    from app.db.database import Database
    from app.models.schemas import Account, AccountType, Category, Direction, Transaction

    previous = db_module._db
    try:
        db_module._db = Database(Path(tempfile.mkdtemp()) / "x.db")
        db = db_module._db
        repo.upsert_account(db, Account(
            id="a1", institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
            account_number_masked="XXXX1111"))
        account_id = repo.get_accounts(db)[0].id
        purchase = Transaction(
            id="p1", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="CROMA ELECTRONICS", amount=Decimal("50000"),
            direction=Direction.DEBIT, category=Category.SHOPPING)
        repo.save_transactions(db, [purchase])

        client = TestClient(main_module.app)
        created = client.post(f"/api/transactions/{purchase.id}/claim", json={
            "amount": 50000, "direction": "owed_to_me", "counterparty": "Amit",
        })
        claim_id = created.json()["claim_id"]

        reloaded = repo.get_transactions(db)[0]
        assert reloaded.excluded is True

        res = client.post(f"/api/claims/{claim_id}/settle", json={
            "method": "write_off", "amount": 50000, "settled_on": "2026-06-01",
        })
        assert res.status_code == 200, res.text

        claims = repo.get_claims(db)
        assert claims[0]["status"] == "written_off"
        assert claims[0]["settled_amount"] == Decimal("0"), (
            "nothing was actually recovered - settled_amount must not move")

        reloaded = repo.get_transactions(db)[0]
        assert reloaded.excluded is False, (
            "a written-off claim must restore the purchase as real spending")
    finally:
        db_module._db = previous


def test_looks_right_in_the_review_queue_actually_clears_needs_review():
    """The button sent {needs_review: false}, a field TransactionUpdateReq
    does not have. Pydantic silently dropped it, update_args came back
    empty, and the endpoint's own `if update_args:` guard skipped
    record_decision entirely - the only place needs_review is ever cleared.
    The request still returned 200, so the row vanished from the list on
    screen while staying exactly as unresolved in the database, ready to
    reappear on the next load."""
    from fastapi.testclient import TestClient
    from app import main as main_module
    from app.db import repository as repo
    import app.db.database as db_module
    from app.db.database import Database
    from app.models.schemas import Account, AccountType, Category, Direction, Transaction

    previous = db_module._db
    try:
        db_module._db = Database(Path(tempfile.mkdtemp()) / "x.db")
        db = db_module._db
        repo.upsert_account(db, Account(
            id="a1", institution="Axis Bank", account_type=AccountType.SAVINGS))
        account_id = repo.get_accounts(db)[0].id
        txn = Transaction(
            id="t1", account_id=account_id, txn_date=date(2026, 3, 4),
            raw_description="UPI/POOJA ROHA/school fee", amount=Decimal("2000"),
            direction=Direction.CREDIT, category=Category.OTHER_INCOME,
            flow_role="refund", needs_review=True,
            review_reason="Confirm or flip.")
        repo.save_transactions(db, [txn])

        client = TestClient(main_module.app)
        # The exact body ReviewQueue.jsx's "Looks right" button now sends.
        res = client.patch(f"/api/transactions/{txn.id}", json={
            "flow_role": txn.flow_role,
        })
        assert res.status_code == 200, res.text

        reloaded = repo.get_transactions(db)[0]
        assert reloaded.needs_review is False, (
            "Looks right must clear needs_review, not just remove the row "
            "from the frontend's own list")
    finally:
        db_module._db = previous


def test_renaming_a_recurring_series_shows_up_without_a_full_reprocess():
    """PATCH /api/recurring/{id} only ever wrote to
    recurring_series_overrides. GET /api/recurring did a bare `SELECT *
    FROM recurring_series`, a table only save_recurring_series rewrites -
    and only a full analysis calls that. A rename, mute or delete from the
    Recurring tab returned 200 and changed nothing the user could see until
    the next full reanalyze happened to run."""
    from fastapi.testclient import TestClient
    from app import main as main_module
    from app.db import repository as repo
    import app.db.database as db_module
    from app.db.database import Database
    from app.analytics.recurring import RecurringSeries

    previous = db_module._db
    try:
        db_module._db = Database(Path(tempfile.mkdtemp()) / "x.db")
        db = db_module._db
        series = RecurringSeries(
            id="s1", account_id=None, label="NETFLIX", category="entertainment",
            direction=Direction.DEBIT, median_amount=Decimal("649"),
            cadence_days=30, cadence_name="monthly", occurrences=6,
            first_seen=date(2026, 1, 5), last_seen=date(2026, 6, 5),
            next_expected=date(2026, 7, 5), is_active=True, confidence=0.9)
        repo.save_recurring_series(db, [series])

        client = TestClient(main_module.app)
        res = client.patch("/api/recurring/s1", json={"label": "Netflix (shared)"})
        assert res.status_code == 200, res.text

        rows = client.get("/api/recurring").json()
        assert rows[0]["label"] == "Netflix (shared)", (
            "the rename must be visible immediately, not after a reanalyze")

        deleted = client.delete("/api/recurring/s1")
        assert deleted.status_code == 200, deleted.text
        rows = client.get("/api/recurring").json()
        assert rows == [], "a deleted series must disappear immediately"
    finally:
        db_module._db = previous


def test_a_dormant_account_with_no_activity_is_not_treated_as_a_parse_failure():
    """A statement whose own letterhead says 'No transactions found', with
    opening balance equal to closing balance, is a real document correctly
    read - not a parser failure. Two real accounts hit this: a slice bank
    savings account that sat untouched for 13 straight months, and an Axis
    salary account that had a quiet month. Before this, `ingest_file`
    treated every zero-row statement as "failed", so the account itself
    never reached merge_ledger's output - not a missing month, a missing
    ACCOUNT, absent from the accounts list and net worth with nothing to
    say anything had gone wrong. A statement that actually declares its
    balance MOVED despite zero rows being extracted must still be flagged -
    that is a real extraction failure, not a quiet period."""
    from app.graph.nodes import _is_genuinely_quiet_period
    from app.models.schemas import Statement
    from decimal import Decimal

    quiet = Statement(source_filename="slice.pdf",
                      opening_balance=Decimal("0.00"), closing_balance=Decimal("0.00"))
    assert _is_genuinely_quiet_period(quiet) is True

    quiet_nonzero = Statement(source_filename="axis.pdf",
                              opening_balance=Decimal("12309.42"),
                              closing_balance=Decimal("12309.42"))
    assert _is_genuinely_quiet_period(quiet_nonzero) is True

    moved = Statement(source_filename="broken.pdf",
                      opening_balance=Decimal("1000.00"), closing_balance=Decimal("500.00"))
    assert _is_genuinely_quiet_period(moved) is False, (
        "a balance that moved despite zero extracted rows is a genuine "
        "extraction failure, not a quiet period")

    unknown = Statement(source_filename="unclear.pdf",
                        opening_balance=None, closing_balance=Decimal("500.00"))
    assert _is_genuinely_quiet_period(unknown) is False, (
        "an undeclared balance is not evidence of a quiet period either way "
        "- defaulting to 'failed' here is the safe direction")


def test_an_excluded_parser_artifact_does_not_corrupt_which_series_look_active():
    """detect_recurring's "today" was `max(t.txn_date for t in transactions)`
    over EVERY transaction, including ones already marked excluded - and a
    known parser artifact (a credit card statement's own column-header row,
    "PaymentDueDate Min.AmountDue ChequeNo Date Bank Amount", misread as a
    transaction) lands with a garbage date months past the real ledger.
    `pipeline.enrich` already excludes that row from every total for
    exactly this reason, but detect_recurring still counted its date as
    "the most recent activity in the whole ledger" - so a salary series,
    regular for 11 straight months, measured its own gap against a phantom
    future date and came out inactive though nothing about it had changed."""
    from app.analytics.recurring import detect_recurring

    salary_dates = [date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]
    txns = _salaries(salary_dates)

    artifact = Transaction(
        id="artifact", account_id="a1", txn_date=date(2026, 9, 4),
        raw_description="PaymentDueDate Min.AmountDue ChequeNo Date Bank Amount",
        amount=Decimal("732.00"), direction=Direction.DEBIT,
        category=Category.FEES_CHARGES, excluded=True)

    series = detect_recurring(txns + [artifact])
    salary = next(s for s in series if s.category == Category.SALARY)
    assert salary.is_active is True, (
        "an excluded parser artifact's date must not make a genuinely "
        "regular series look overdue and inactive")


def test_a_retried_file_does_not_leave_two_statements_in_the_ledger():
    """`statements` is an additive LangGraph channel (state.py) so a file
    retried after a failed reconciliation check ends up with TWO ParsedFile
    entries - one per attempt - not one replacing the other. Keeping both
    meant merge_ledger's transactions could come from either attempt's
    Statement while only one of the two ever got persisted, leaving some
    transactions pointing at a statement_id nothing else knew about - the
    exact dangling foreign key that made a real reanalyze fail outright
    on a database with a handful of never-reconciling statements in it.
    """
    from app.graph.nodes import merge_ledger
    from app.models.schemas import Account, AccountType, Statement, Transaction

    def _attempt(attempt: int, desc: str) -> dict:
        account = Account(institution="HDFC Bank", account_type=AccountType.CREDIT_CARD,
                          account_number_masked="XXXX5529")
        statement = Statement(source_filename="card.pdf")
        txn = Transaction(
            id=f"t{attempt}", account_id=None, txn_date=date(2026, 4, 1),
            raw_description=desc, amount=Decimal("500"),
            direction=Direction.DEBIT, category=Category.SHOPPING)
        statement.transactions = [txn]
        return {
            "filename": "card.pdf", "filepath": "/tmp/card.pdf",
            "file_hash": "same-file-every-attempt", "attempt": attempt,
            "status": "unreconciled", "statement": statement, "account": account,
        }

    # Two attempts at the same physical file - extraction was not
    # byte-identical (a different description survived each pass), so the
    # ordinary duplicate-transaction check does not collapse them on its own.
    attempt1, attempt2 = _attempt(1, "PURCHASE A"), _attempt(2, "PURCHASE B")
    state = {"statements": [attempt1, attempt2]}
    result = merge_ledger(state)

    assert result["duplicate_count"] == 0, (
        "this test is about two DIFFERENT-looking rows from the same retried "
        "file, not ordinary duplicate detection")
    assert len(result["transactions"]) == 1, (
        "a retry must supersede the attempt before it, not add another copy "
        "of the same statement's rows alongside it")

    surviving = result["transactions"][0]
    assert surviving.raw_description == "PURCHASE B", (
        "the later attempt should be the one that survives, not the earlier one")
    # The surviving transaction's statement_id must belong to the SAME
    # attempt's Statement - not the discarded earlier one. A mismatch here
    # is precisely the dangling foreign key `save_transactions` hit for real.
    assert surviving.statement_id == attempt2["statement"].id
    assert surviving.statement_id != attempt1["statement"].id


def test_no_duplicate_function_definitions_shadow_the_live_claims_code():
    """save_claim, get_claims and settle_claim were each defined twice in the
    same module; Python silently keeps only the later one. The earlier
    get_claims in particular returned after its first row, dropping every
    other claim - dead code, but exactly the kind of landmine that goes live
    again the moment someone edits the wrong copy."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/db/repository.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = [n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.col_offset == 0]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate top-level function definitions: {sorted(dupes)}"
