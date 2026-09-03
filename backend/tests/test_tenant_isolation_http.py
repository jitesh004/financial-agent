"""Two signed-in users, driven through the real endpoints.

Row-level security protects every table, and `test_auth.py` proves it at the
database layer. This file exists because that was not enough: the API caches
answers in memory in front of the database, and a cache that is not keyed by
tenant hands one user another's data without a single SQL statement being
involved. That is exactly what happened - `/api/dashboard` read a
process-global "latest run", so whoever imported last owned the dashboard for
everybody, and the policies underneath never got a chance to apply.

So these tests deliberately go through HTTP rather than the repository. The
question is not "can the database keep two accounts apart" but "can a signed-in
user, using nothing but the app's own endpoints, see anything of somebody
else's".
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from tests.support import fresh_ledger, make_user      # noqa: E402
from app.db.database import get_db                     # noqa: E402
from app.db.engine import TENANT, tenant_scope         # noqa: E402
from app.db import repository as repo                  # noqa: E402
from app.models.schemas import (Account, AccountType,   # noqa: E402
                                Category, Direction, SourceFormat,
                                Statement, Transaction)


@pytest.fixture
def api():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _import_a_ledger(institution: str, amount: str, merchant: str) -> str:
    """Give the current tenant a parsed statement, and a dashboard for it."""
    import app.main as main

    db = get_db()
    account = Account(institution=institution, account_type=AccountType.SAVINGS,
                      account_number_masked="****0001")
    account_id = repo.upsert_account(db, account)
    statement = Statement(
        account=account, source_filename=f"{institution}.pdf",
        source_format=SourceFormat.PDF, file_hash=f"hash-{institution}",
        period_start=date(2025, 1, 1), period_end=date(2025, 1, 31),
        opening_balance=Decimal("1000"), closing_balance=Decimal("900"))
    statement_id = repo.save_statement(db, statement, account_id)
    repo.save_transactions(db, [Transaction(
        account_id=account_id, statement_id=statement_id,
        txn_date=date(2025, 1, 5), raw_description=merchant,
        amount=Decimal(amount), direction=Direction.DEBIT,
        category=Category.DINING, fingerprint=f"fp-{institution}")])

    # The dashboard payload, cached the way a real import caches it.
    run_id = main.runs.create_from_payload(
        f"run-{institution}", {"analysis": {"totals": {"marker": institution}},
                               "statements": [{"source_filename": f"{institution}.pdf"}]})
    return run_id


def test_the_dashboard_does_not_follow_whoever_imported_last(api):
    """The reported bug, end to end.

    A imports and sees their ledger. B imports and sees theirs. A comes back -
    and used to be shown B's, because the cache in front of the database had
    one "latest run" for the whole process.
    """
    alice = make_user()
    bob = make_user()

    with tenant_scope(alice.id):
        _import_a_ledger("AliceBank", "111.00", "ALICE CAFE")
        assert api.get("/api/dashboard").json()["analysis"]["totals"]["marker"] \
            == "AliceBank"

    with tenant_scope(bob.id):
        _import_a_ledger("BobBank", "222.00", "BOB DINER")
        assert api.get("/api/dashboard").json()["analysis"]["totals"]["marker"] \
            == "BobBank"

    # Back to Alice. This is the assertion that was failing in production.
    with tenant_scope(alice.id):
        body = api.get("/api/dashboard").json()
        assert body["analysis"]["totals"]["marker"] == "AliceBank", (
            "the dashboard served another account's payload from cache")
        assert "BobBank" not in api.get("/api/dashboard").text


def test_a_run_id_from_another_account_is_not_readable(api):
    alice = make_user()
    bob = make_user()

    with tenant_scope(alice.id):
        run_id = _import_a_ledger("AliceBank", "111.00", "ALICE CAFE")

    with tenant_scope(bob.id):
        # Bob knows Alice's run id and asks for it directly.
        assert api.get(f"/api/runs/{run_id}").status_code == 404


def test_clearing_one_account_leaves_the_others_dashboard_alone(api):
    """A shared cache made this destructive across accounts, not just leaky."""
    alice = make_user()
    bob = make_user()

    with tenant_scope(alice.id):
        _import_a_ledger("AliceBank", "111.00", "ALICE CAFE")
    with tenant_scope(bob.id):
        _import_a_ledger("BobBank", "222.00", "BOB DINER")
        api.post("/api/data/clear/parsed_data", json={})

    with tenant_scope(alice.id):
        assert api.get("/api/dashboard").json()["analysis"]["totals"]["marker"] \
            == "AliceBank"


def test_the_jobs_list_shows_only_your_own(api):
    """`/api/jobs` needed no id to guess - it just listed everybody's."""
    from app.jobs import jobs

    alice = make_user()
    bob = make_user()

    with tenant_scope(alice.id):
        alice_job = jobs.create("gmail_scan", total=10)
    with tenant_scope(bob.id):
        bob_job = jobs.create("gmail_scan", total=10)

        listed = api.get("/api/jobs").text
        assert bob_job.id in listed
        assert alice_job.id not in listed, "another account's job was listed"

        # And not reachable by id either.
        assert api.get(f"/api/jobs/{alice_job.id}").status_code == 404


def test_every_read_endpoint_is_empty_for_a_brand_new_account(api):
    """The broad sweep: a fresh account sees nothing of the account before it.

    Cheap to run and it covers the endpoints a future cache would most likely
    be added to.
    """
    with tenant_scope(make_user().id):
        _import_a_ledger("AliceBank", "111.00", "ALICE CAFE")

    fresh_ledger()   # a brand-new tenant
    for path in ("/api/dashboard", "/api/accounts", "/api/statements",
                 "/api/transactions", "/api/files", "/api/jobs",
                 "/api/recurring", "/api/data/inventory"):
        body = api.get(path).text
        assert "AliceBank" not in body, f"{path} leaked the other account"
        assert "ALICE CAFE" not in body, f"{path} leaked the other account"


def test_a_background_run_is_filed_under_the_user_who_asked_for_it():
    """The other half of scoping the cache: it must still reach its owner.

    Everything `_run_analysis` writes is filed under whoever is bound while it
    runs, and it runs off the request thread. Bound to nobody, the import
    would succeed and the user's dashboard would come back empty - the cache
    scoped correctly and filed under no one.
    """
    import app.main as main
    from app.db.engine import current_tenant

    alice = make_user()
    seen: dict[str, str | None] = {}

    def _record(*args, **kwargs):
        seen["tenant"] = current_tenant()

    original = main._run_analysis_scoped
    main._run_analysis_scoped = _record
    try:
        # Dispatched the way the endpoint dispatches it, with the owner
        # captured at request time and no ambient tenant on the worker.
        main._run_analysis("run-x", [], False, 6, False, owner=alice.id)
    finally:
        main._run_analysis_scoped = original

    assert seen["tenant"] == alice.id


def test_the_analysis_endpoint_captures_the_caller_as_owner():
    """Guards the wiring, not just the function: the endpoint must pass it."""
    import inspect
    import app.main as main

    source = inspect.getsource(main)
    assert "background.add_task(_run_analysis" in source
    dispatch = source.split("background.add_task(_run_analysis", 1)[1][:200]
    assert "owner=current_tenant()" in dispatch, (
        "the analysis task is dispatched without capturing who asked for it")
