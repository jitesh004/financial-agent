"""Data lifecycle, snapshots, and the user-override layer.

The through-line in every test here: a user's own decisions and their statement
files must survive everything the app does to its own derived data. Those two
things are the only ones that cannot be regenerated - a ledger can always be
rebuilt by re-reading the files, and AI inference can be re-bought, but a
correction someone typed and a PDF they no longer have a copy of cannot.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from tests.support import fresh_ledger  # noqa: E402
from app.db.database import (CLEAR_SCOPES, MAX_SNAPSHOTS,  # noqa: E402
                             Database, get_db)
from app.models.schemas import (Account, AccountType, Category,  # noqa: E402
                                Direction, Transaction)


def _fresh_db() -> Database:
    """A ledger with nothing in it - now a new tenant, not a new file."""
    return fresh_ledger()


def _txn(account_id: str = "a1", amount: str = "450.00",
         desc: str = "SWIGGY BANGALORE", direction: Direction = Direction.DEBIT,
         txn_id: str = "t1") -> Transaction:
    return Transaction(
        id=txn_id, account_id=account_id, txn_date=date(2026, 3, 4),
        raw_description=desc, amount=Decimal(amount), direction=direction,
        category=Category.DINING,
    )


def _account(masked: str = "XXXX4321", product: str = "Rewards",
             account_id: str = "a1") -> Account:
    return Account(id=account_id, institution="Axis Bank",
                   account_type=AccountType.CREDIT_CARD,
                   account_number_masked=masked, product_name=product)


# --------------------------------------------------------------------------
# The override layer
# --------------------------------------------------------------------------

def test_a_user_correction_survives_a_full_reprocess():
    """Why decisions are keyed by content rather than row id.

    Re-parsing a statement mints brand-new uuid4 ids for every row, so a
    decision hung off the old id would be orphaned by the very act of
    re-reading the file it came from.
    """
    from app.pipeline.fingerprint import stamp_fingerprints
    from app.pipeline.overrides import apply_overrides, record_decision

    db = _fresh_db()
    accounts = {"a1": _account()}

    original = _txn()
    stamp_fingerprints([original], accounts)
    record_decision(db, original, accounts, category="groceries",
                    note="monthly grocery run")

    # A reprocess: same statement, same row, an entirely different object id.
    reparsed = _txn(txn_id="a-completely-different-uuid")
    stamp_fingerprints([reparsed], accounts)
    report = apply_overrides(db, [reparsed], accounts)

    assert reparsed.category == "groceries"
    assert reparsed.note == "monthly grocery run"
    assert report.applied == 1


def test_a_decision_is_recovered_when_the_account_identity_changes():
    """Account identity really does move - it did twice while this app was
    being built, when card-variant detection improved and one merged account
    correctly split into three. A decision must not be collateral damage."""
    from app.db import repository as repo
    from app.pipeline.fingerprint import stamp_fingerprints
    from app.pipeline.overrides import apply_overrides, record_decision

    db = _fresh_db()

    before = {"a1": _account(masked="", product="")}
    original = _txn()
    stamp_fingerprints([original], before)
    old_fingerprint = original.fingerprint
    record_decision(db, original, before, category="groceries")

    after = {"a1": _account(masked="XXXX4321", product="Rewards")}
    reparsed = _txn(txn_id="new-uuid")
    stamp_fingerprints([reparsed], after)
    assert reparsed.fingerprint != old_fingerprint, "identity should have moved"

    report = apply_overrides(db, [reparsed], after)
    assert reparsed.category == "groceries"
    assert report.repaired == 1
    # Re-pointed, so the next run finds it on the strict key without repair.
    assert repo.get_overrides(db)[0].fingerprint == reparsed.fingerprint


def test_an_ambiguous_recovery_is_refused_rather_than_guessed():
    """Two rows identical in date, amount, direction and description leave no
    honest way to tell which one the user meant. Applying the decision to the
    wrong row is worse than applying it to neither."""
    from app.pipeline.fingerprint import stamp_fingerprints
    from app.pipeline.overrides import apply_overrides, record_decision

    db = _fresh_db()
    before = {"a1": _account(masked="", product="")}
    original = _txn()
    stamp_fingerprints([original], before)
    record_decision(db, original, before, category="groceries")

    after = {"a1": _account()}
    twins = [_txn(txn_id="x"), _txn(txn_id="y")]
    stamp_fingerprints(twins, after)
    report = apply_overrides(db, twins, after)

    assert [t.category for t in twins] == ["dining", "dining"]
    assert report.applied == 0
    assert report.orphaned == 1
    assert report.notes, "the user should be told why it was left alone"


def test_recording_a_note_does_not_disturb_an_earlier_recategorization():
    """Each overridable field is independent. Writing one must not blank the
    others, or adding a note would silently revert a category correction."""
    from app.pipeline.fingerprint import stamp_fingerprints
    from app.pipeline.overrides import apply_overrides, record_decision

    db = _fresh_db()
    accounts = {"a1": _account()}
    txn = _txn()
    stamp_fingerprints([txn], accounts)

    record_decision(db, txn, accounts, category="groceries")
    record_decision(db, txn, accounts, note="split with flatmate")

    reparsed = _txn(txn_id="fresh")
    stamp_fingerprints([reparsed], accounts)
    apply_overrides(db, [reparsed], accounts)
    assert reparsed.category == "groceries"
    assert reparsed.note == "split with flatmate"


def test_clearing_one_override_field_leaves_the_others():
    from app.db import repository as repo
    from app.pipeline.fingerprint import stamp_fingerprints
    from app.pipeline.overrides import record_decision

    db = _fresh_db()
    accounts = {"a1": _account()}
    txn = _txn()
    stamp_fingerprints([txn], accounts)
    record_decision(db, txn, accounts, category="groceries", note="keep me")

    repo.clear_override_field(db, txn.fingerprint, "category")
    stored = repo.get_overrides(db)[0]
    assert stored.category is None
    assert stored.note == "keep me"


def test_an_override_with_no_opinions_left_is_dropped():
    """So the "N decisions" count the UI shows stays truthful."""
    from app.db import repository as repo
    from app.pipeline.fingerprint import stamp_fingerprints
    from app.pipeline.overrides import record_decision

    db = _fresh_db()
    accounts = {"a1": _account()}
    txn = _txn()
    stamp_fingerprints([txn], accounts)
    record_decision(db, txn, accounts, category="groceries")

    repo.clear_override_field(db, txn.fingerprint, "category")
    assert repo.count_overrides(db) == 0


def test_an_excluded_transaction_leaves_every_total():
    """An explicit human exclusion outranks anything inferred."""
    txn = _txn()
    assert txn.is_spend is True
    txn.excluded = True
    assert txn.is_spend is False


def test_enrich_ledger_applies_user_decisions_last():
    """Ordering is the entire point of the override layer.

    detect_transfers reassigns a category unconditionally the moment it pairs
    a row, with no check of category_source, so a hand correction survives
    only if it is re-applied after every automatic classifier has run.
    """
    from app.pipeline.enrich import enrich_ledger
    from app.pipeline.fingerprint import stamp_fingerprints
    from app.pipeline.overrides import record_decision

    db = _fresh_db()
    card = _account()
    bank = Account(id="a2", institution="Axis Bank",
                   account_type=AccountType.SAVINGS,
                   account_number_masked="XXXX9999")
    accounts = {"a1": card, "a2": bank}

    # A pair that transfer detection will claim and relabel CC_PAYMENT.
    debit = _txn(account_id="a2", amount="5000.00", desc="CARD PAYMENT", txn_id="d1")
    credit = _txn(account_id="a1", amount="5000.00", desc="PAYMENT RECEIVED",
                  direction=Direction.CREDIT, txn_id="c1")
    stamp_fingerprints([debit, credit], accounts)
    record_decision(db, debit, accounts, category="shopping")

    result = enrich_ledger(db, [debit, credit], accounts, run_analysis=False)
    settled = {t.id: t for t in result.transactions}

    assert settled["d1"].category == "shopping", (
        "the user decision must win over the settlement matcher")
    assert settled["d1"].category_source.value == "user"


# --------------------------------------------------------------------------
# Clearing scopes
# --------------------------------------------------------------------------

def _seed_one_of_everything(db: Database) -> None:
    with db.connection() as conn:
        conn.execute("INSERT INTO accounts (id, institution) VALUES ('a1','Axis')")
        conn.execute(
            "INSERT INTO transactions (id, account_id, txn_date, raw_description,"
            " amount, direction) VALUES ('t1','a1','2026-03-04','X','1','debit')")
        conn.execute("INSERT INTO source_files (id, filename) VALUES ('f1','s.pdf')")
        conn.execute(
            "INSERT INTO ai_inferences (cache_key, kind, input_hash, result_json)"
            " VALUES ('k1','statement_identity','h1','{}')")
        conn.execute(
            "INSERT INTO user_overrides (fingerprint, category)"
            " VALUES ('fp1','groceries')")


def _counts(db: Database) -> dict[str, int]:
    with db.connection() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                for t in ("transactions", "source_files", "ai_inferences",
                          "user_overrides")}


def test_each_clearing_scope_clears_exactly_its_own_tier():
    """Replacing one Reset button with seven scoped actions only helps if
    clearing a cheap tier never takes an expensive one with it."""
    db = _fresh_db()
    _seed_one_of_everything(db)

    db.clear("parsed_data")
    after = _counts(db)
    assert after["transactions"] == 0, "the parsed ledger should be gone"
    assert after["source_files"] == 1, "files are a more expensive tier"
    assert after["ai_inferences"] == 1, "AI inference costs real money"
    assert after["user_overrides"] == 1, "decisions cannot be regenerated"

    db.clear("files")
    assert _counts(db)["source_files"] == 0
    assert _counts(db)["ai_inferences"] == 1
    assert _counts(db)["user_overrides"] == 1

    db.clear("ai_inferences")
    assert _counts(db)["ai_inferences"] == 0
    assert _counts(db)["user_overrides"] == 1, "still not a decision's business"

    db.clear("decisions")
    assert _counts(db)["user_overrides"] == 0


def test_factory_reset_is_the_only_scope_that_clears_the_profile():
    db = _fresh_db()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO user_profile (id, full_name) VALUES ('me','Someone')")

    for scope in ("derived", "parsed_data", "files", "ai_inferences", "decisions"):
        db.clear(scope)
        with db.connection() as conn:
            assert conn.execute(
                "SELECT COUNT(*) c FROM user_profile").fetchone()["c"] == 1, (
                f"scope {scope!r} cleared the profile")

    db.clear("everything")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM user_profile").fetchone()["c"] == 0


def test_routine_scopes_never_reach_the_irreplaceable_tiers():
    """Stated declaratively because this is the property most likely to be
    broken by a later change: re-parsing is the routine action, and it has to
    stay safe to run at any time without thinking about it."""
    expensive = {"ai_inferences", "merchant_categories", "user_overrides",
                 "user_profile"}
    for scope in ("derived", "parsed_data", "files"):
        overlap = set(CLEAR_SCOPES[scope]) & expensive
        assert not overlap, (
            f"scope {scope!r} would destroy {sorted(overlap)}, which it has no "
            f"business touching")


def test_an_unknown_scope_is_refused():
    db = _fresh_db()
    with pytest.raises(ValueError):
        db.clear("everything-including-the-kitchen-sink")


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------

def test_a_snapshot_round_trips_and_restore_is_itself_undoable():
    db = _fresh_db()
    with db.connection() as conn:
        conn.execute("INSERT INTO accounts (id, institution) VALUES ('a1','Axis')")

    snap = db.snapshot("probe")
    db.clear("parsed_data")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"] == 0

    db.restore(snap.name)
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"] == 1

    assert any("pre-restore" in s["name"] for s in db.list_snapshots()), (
        "restoring must snapshot first, so the restore is undoable too")


def test_restore_refuses_a_path_outside_the_snapshot_folder():
    """The snapshot name arrives from an HTTP request."""
    db = _fresh_db()
    for attempt in ("../../etc/passwd", "../secrets.db", "sub/dir/x.db"):
        with pytest.raises(ValueError):
            db.restore(attempt)


def test_snapshots_are_pruned_to_the_keep_count():
    db = _fresh_db()
    for i in range(MAX_SNAPSHOTS + 4):
        db.snapshot(f"n{i}")
    assert len(db.list_snapshots()) == MAX_SNAPSHOTS


# --------------------------------------------------------------------------
# Migration and persistence
# --------------------------------------------------------------------------

def test_the_schema_has_every_column_analytics_depends_on():
    """Applying the schema must produce every column the ledger reads.

    These columns arrived one at a time, each behind a SQLite ALTER in a
    hand-written migration; the PostgreSQL schema declares them outright.
    What has to stay true is the same either way - is_mirror_leg in
    particular, whose absence made a dashboard rebuilt after a restart
    disagree with the one computed at ingestion.
    """
    db = _fresh_db()
    db.ensure_schema()
    with db.connection() as conn:
        cols = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'transactions'")}

    for column in ("is_mirror_leg", "fingerprint", "accounting_month",
                   "needs_review", "review_reason", "flow_role", "excluded",
                   "note", "source", "superseded", "category_rule",
                   "direction_reason", "user_id"):
        assert column in cols, f"{column} is missing from transactions"


def test_applying_the_schema_twice_changes_nothing():
    """ensure_schema runs on every boot, and re-applies the RLS policies.

    Every statement in it has to be idempotent, or the second start of a
    working deployment is the one that fails.
    """
    from app.db.database import Database
    from app.db.engine import current_tenant

    db = _fresh_db()
    tenant = current_tenant()
    with db.connection() as conn:
        conn.execute("INSERT INTO accounts (id, institution) VALUES (?, ?)",
                     ("a1", "Axis"))

    # A second Database against the same URL re-runs the whole script.
    Database(db.dsn)
    Database(db.dsn)

    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
        indexes = {r["indexname"] for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'transactions'")}
        policies = {r["policyname"] for r in conn.execute(
            "SELECT policyname FROM pg_policies WHERE tablename = 'transactions'")}
    assert "idx_txn_fingerprint" in indexes
    assert policies == {"transactions_tenant"}, (
        "re-applying the schema must leave exactly one policy per table, not "
        "stack a second copy on top")
    assert current_tenant() == tenant


def test_is_mirror_leg_survives_a_save_and_reload():
    """The bug this column exists to fix: analytics counts exactly one leg of
    a transfer as real cash, and every reloaded row used to claim to be the
    leg that counts."""
    from app.db import repository as repo

    db = _fresh_db()
    repo.upsert_account(db, _account())
    account_id = repo.get_accounts(db)[0].id

    txn = _txn(account_id=account_id)
    txn.is_mirror_leg = True
    txn.is_internal_transfer = True
    txn.needs_review = True
    txn.review_reason = "unknown_funding"
    txn.note = "checked by hand"
    repo.save_transactions(db, [txn])

    reloaded = repo.get_transactions(db)[0]
    assert reloaded.is_mirror_leg is True
    assert reloaded.is_internal_transfer is True
    assert reloaded.needs_review is True
    assert reloaded.review_reason == "unknown_funding"
    assert reloaded.note == "checked by hand"


def test_a_completed_run_is_stored_and_read_back():
    """So a restart restores the dashboard instead of recomputing a lossy
    version of it (no narrative, no transfer report)."""
    from app.db import repository as repo

    db = _fresh_db()
    payload = {"statements": [{"filename": "a.pdf"}],
               "analysis": {"totals": {"income": 100}}}
    repo.save_analysis_run(db, "run-1", "complete", 1, payload)

    stored = repo.get_latest_analysis_run(db)
    assert stored is not None
    run_id, restored = stored
    assert run_id == "run-1"
    assert restored["analysis"]["totals"]["income"] == 100


def test_run_history_is_capped():
    """Each stored payload is an entire dashboard."""
    from app.db import repository as repo

    db = _fresh_db()
    for i in range(25):
        repo.save_analysis_run(db, f"run-{i}", "complete", 1, {"statements": []})
    repo.prune_analysis_runs(db, keep=20)
    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) c FROM analysis_runs").fetchone()["c"] == 20


def test_clearing_data_forgets_the_cached_dashboard():
    """/api/dashboard returns the cached payload verbatim without
    revalidating, so leaving it in place after a clear shows the user totals
    for a ledger that no longer exists."""
    from app import main as main_module

    original = main_module.runs
    try:
        main_module.runs = main_module.RunStore()
        main_module.runs.create_from_payload("r1", {"statements": []})
        assert main_module.runs.latest() is not None
        main_module.runs.clear()
        assert main_module.runs.latest() is None
    finally:
        main_module.runs = original


# --------------------------------------------------------------------------
# Durable statement storage
# --------------------------------------------------------------------------

def test_a_manually_uploaded_file_lands_in_content_addressed_storage():
    """An uploaded statement can be the only copy in existence - an Amex PDF,
    anything from a bank that does not email them. It must not live somewhere
    that clearing a derived ledger would delete."""
    from app import storage

    tmp = Path(tempfile.mkdtemp())
    source = tmp / "amex.pdf"
    source.write_bytes(b"%PDF-1.4 amex statement")

    original_store = storage.STATEMENT_STORE
    try:
        storage.STATEMENT_STORE = tmp / "store"
        digest = "a" * 64
        first = storage.store_file(source, digest)
        assert first.exists()
        assert first.read_bytes() == b"%PDF-1.4 amex statement"

        # Content-addressed, so storing the same bytes twice resolves to the
        # same path rather than accumulating copies.
        assert storage.store_file(source, digest) == first
        assert len(list((tmp / "store").rglob("*.pdf"))) == 1
    finally:
        storage.STATEMENT_STORE = original_store


def test_storage_reports_replaceable_and_irreplaceable_separately():
    """A Gmail-sourced file can be fetched again; a hand-uploaded one cannot.
    No clearing action should present them as the same thing."""
    from app import storage

    tmp = Path(tempfile.mkdtemp())
    original_store, original_cache = storage.STATEMENT_STORE, storage.GMAIL_CACHE
    try:
        storage.STATEMENT_STORE = tmp / "store"
        storage.GMAIL_CACHE = tmp / "cache"
        # Both stores hang off the signed-in user now, so the fixture writes
        # into this tenant's own subdirectory - which is also the assertion
        # that the counts below are one person's, not the whole server's.
        storage.gmail_cache().mkdir(parents=True)
        (storage.gmail_cache() / "icici.pdf").write_bytes(b"x" * 10)

        source = tmp / "amex.pdf"
        source.write_bytes(b"y" * 20)
        storage.store_file(source, "b" * 64)

        stats = storage.store_stats()
        assert stats["uploaded_count"] == 1
        assert stats["gmail_cached_count"] == 1
        assert stats["count"] == 2
    finally:
        storage.STATEMENT_STORE, storage.GMAIL_CACHE = original_store, original_cache


# --------------------------------------------------------------------------
# The HTTP surface
# --------------------------------------------------------------------------

def test_destructive_scopes_require_the_typed_phrase():
    """The two scopes that can destroy something unrecoverable are the two
    that make the user type the words."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    for scope in ("files", "everything"):
        refused = client.post(f"/api/data/clear/{scope}", json={})
        assert refused.status_code == 400
        wrong = client.post(f"/api/data/clear/{scope}", json={"confirm": "yes"})
        assert wrong.status_code == 400


def test_an_unknown_clear_scope_is_a_400_not_a_500():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    assert client.post("/api/data/clear/nonsense", json={}).status_code == 400


def test_the_workflow_view_reports_every_stage():
    """Derived on each request rather than stored as a "current step" pointer,
    which would be a second source of truth about state the database already
    knows and would drift the moment anything happened out of band."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    body = client.get("/api/workflow").json()
    stage_ids = [s["id"] for s in body["stages"]]
    assert stage_ids == ["profile", "sources", "collect", "parse", "review",
                         "analyze"]
    for stage in body["stages"]:
        assert isinstance(stage["complete"], bool)
        assert stage["detail"], "every stage should say where it stands"
    assert "transactions" in body["counts"]


def test_the_inventory_names_what_each_action_preserves():
    """The old UI had one unlabelled Reset that deleted the ledger, the file
    registry and every uploaded file. Naming what survives is the fix."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    body = client.get("/api/data/inventory").json()
    by_scope = {a["scope"]: a for a in body["actions"]}

    assert set(by_scope) == {"derived", "parsed_data", "staged_imports", "files",
                             "ai_inferences", "decisions", "everything"}
    assert "your decisions" in by_scope["parsed_data"]["preserves"]
    assert "AI inference" in by_scope["parsed_data"]["preserves"]
    assert by_scope["everything"]["confirm_phrase"] == "DELETE EVERYTHING"
    for action in by_scope.values():
        assert action["description"], "an action with no explanation is a trap"


# ---------------------------------------------------------------------------
# Previewing what a clearing action would destroy
#
# Three of the seven scopes had no preview at all: the endpoint carried a
# branch per scope and the UI a hardcoded list of four, and neither was
# extended when scopes were added. "Preview data" on the staged imports - the
# statements downloaded from Gmail - answered `{}`, which the browser rendered
# as an empty box. Both ends are now driven by CLEAR_SCOPES; these tests are
# what stop them drifting apart again.
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_every_clearing_scope_can_be_previewed():
    from app.main import PREVIEW_COLUMNS

    for scope, tables in CLEAR_SCOPES.items():
        previewable = [t for t in tables if t in PREVIEW_COLUMNS]
        assert previewable, (
            f"scope {scope!r} would delete {tables} and none of them has "
            f"preview columns, so the user cannot see what they are losing")


def test_every_table_a_scope_deletes_has_preview_columns():
    """The drift guard, in the direction that actually bit.

    A table added to a tier without an entry here is silently invisible in
    every preview that covers it.
    """
    from app.main import PREVIEW_COLUMNS

    deletable = {t for tables in CLEAR_SCOPES.values() for t in tables}
    missing = sorted(deletable - set(PREVIEW_COLUMNS))
    assert not missing, f"no preview columns for: {', '.join(missing)}"


def test_preview_columns_name_only_tables_something_clears():
    from app.main import PREVIEW_COLUMNS

    deletable = {t for tables in CLEAR_SCOPES.values() for t in tables}
    stray = sorted(set(PREVIEW_COLUMNS) - deletable)
    assert not stray, f"preview columns for tables no scope clears: {stray}"


def test_the_staged_imports_preview_shows_the_downloaded_files(api_client):
    """The reported bug: Gmail-downloaded statements had no preview."""
    from app.db import staging

    db = get_db()
    staging.add(db, "hash-downloaded", filename="icici_card.pdf",
                origin="gmail", kind="statement", account_label="ICICI Card",
                row_count=42, parse_status="ok")

    body = api_client.get("/api/data/preview/staged_imports").json()
    assert "staged_files" in body, body
    assert body["staged_files"][0]["filename"] == "icici_card.pdf"


def test_a_preview_never_echoes_a_stored_file_password(api_client):
    """source_files.password is plaintext by design, and stays out of here.

    The columns are an allowlist rather than `SELECT *` precisely so that a
    preview cannot start leaking a column added later.
    """
    from app.db import repository as repo

    repo.upsert_source_file(get_db(), repo.SourceFileRecord(
        id="sf-secret", filename="locked.pdf", file_hash="h-secret",
        password="pank1407", parse_status="parsed"))

    for scope in ("files", "everything"):
        assert "pank1407" not in api_client.get(
            f"/api/data/preview/{scope}").text


def test_the_factory_reset_preview_leads_with_the_irreplaceable(api_client):
    """Ordered by what it costs to get the data back, not by table name.

    Someone reading this preview is about to lose everything; the rows nothing
    can regenerate are the ones worth seeing first.
    """
    from app.db import repository as repo

    db = get_db()
    repo.add_custom_category(db, "Sabbatical")
    repo.save_merchant_categories(db, {"swiggy": (Category.DINING, 0.9, "llm")})
    repo.upsert_source_file(db, repo.SourceFileRecord(
        id="sf-1", filename="a.pdf", file_hash="h1", parse_status="parsed"))

    tables = list(api_client.get("/api/data/preview/everything").json())
    assert tables.index("custom_categories") < tables.index("merchant_categories")
    assert tables.index("merchant_categories") < tables.index("source_files")


def test_an_unknown_preview_scope_is_refused(api_client):
    response = api_client.get("/api/data/preview/nonsense")
    assert response.status_code == 400
    assert "Unknown scope" in response.text
