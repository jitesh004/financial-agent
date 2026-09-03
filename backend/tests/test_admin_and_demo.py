"""The operator's view, and the Demo switch.

Both touch the one guarantee this app makes about itself - that no account can
reach another's rows - so both are worth pinning down carefully:

  * the admin grant comes from the environment and nowhere else, so nothing in
    the database or the UI can award it
  * it is refused with a 404 rather than a 403, because whether a deployment
    has an operator's view at all is not a useful thing to confirm
  * it counts rows without reading them, through the same row-level security
    every request goes through
  * Demo points the app at a SEPARATE account, so nothing done during a demo
    can reach the real ledger - and turning it off leaves both untouched
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import demo
from app.analytics.budget import analyse_budget
from app.analytics.periods import assign_accounting_months, resolve_period
from app.analytics.recurring import detect_recurring
from app.auth import store
from app.config import config
from app.db import repository as repo
from app.db.database import get_db
from app.db.engine import TENANT, tenant_scope
from app.main import app
from app.models.schemas import (Account, AccountType, Category, Direction,
                                LOAN_TYPES, Transaction)

from .support import fresh_ledger, make_user

client = TestClient(app)


@pytest.fixture()
def admin(monkeypatch):
    """Make the CURRENT test's user the deployment's admin."""
    from app.db.engine import TENANT as tenant_var

    user = store.get_user(get_db(), tenant_var.get())
    monkeypatch.setattr(config, "ADMIN_EMAILS", (user.email.lower(),))
    return user


def _seed_ledger(rows: int = 3) -> None:
    """A few real transactions for whoever is currently bound."""
    db = get_db()
    account_id = repo.upsert_account(db, Account(
        institution="Meridian Bank", account_type=AccountType.SAVINGS,
        account_number_masked="XXXX4402"))
    repo.save_transactions(db, [
        Transaction(
            id=f"seed-{i}", account_id=account_id,
            txn_date=date(2026, 8, i + 1), raw_description=f"ROW {i}",
            amount=Decimal("1000"), direction=Direction.DEBIT,
            category=Category.GROCERIES, accounting_month="2026-08",
            fingerprint=f"fp-{account_id}-{i}")
        for i in range(rows)
    ])


# --------------------------------------------------------------------------
# Who may see the operator's view
# --------------------------------------------------------------------------

def test_without_an_admin_configured_nobody_is_one(monkeypatch):
    """The default is no admin at all - not "the first user", not "anyone"."""
    monkeypatch.setattr(config, "ADMIN_EMAILS", ())
    assert config.is_admin("anybody@example.com") is False
    assert client.get("/api/admin/overview").status_code == 404


def test_the_grant_is_the_whole_address_and_not_the_domain(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", ("owner@example.com",))
    assert config.is_admin("owner@example.com")
    assert config.is_admin("OWNER@Example.COM"), "addresses are not case-sensitive"
    assert not config.is_admin("someone@example.com")
    assert not config.is_admin("owner@example.com.evil.test")


def test_a_non_admin_is_told_nothing_at_all(monkeypatch):
    """404, not 403: a 403 confirms the view exists and that you are not on
    the list, which is information somebody probing for it wants."""
    monkeypatch.setattr(config, "ADMIN_EMAILS", ("somebody-else@example.com",))
    response = client.get("/api/admin/overview")
    assert response.status_code == 404
    assert "admin" not in response.text.lower()


def test_the_session_says_whether_to_offer_the_tab(admin):
    body = client.get("/api/auth/session").json()
    assert body["is_admin"] is True


def test_the_session_does_not_claim_admin_for_everyone_else(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", ("somebody-else@example.com",))
    assert client.get("/api/auth/session").json()["is_admin"] is False


# --------------------------------------------------------------------------
# What it reports
# --------------------------------------------------------------------------

def test_the_overview_lists_accounts_and_their_volumes(admin):
    fresh_ledger()          # a second account, with rows of its own
    _seed_ledger(4)
    TENANT.set(admin.id)
    _seed_ledger(2)

    body = client.get("/api/admin/overview").json()
    listed = {row["email"]: row for row in body["accounts"]}
    assert admin.email in listed

    mine = listed[admin.email]
    assert mine["is_admin"] is True
    assert mine["ledger"]["transactions"] == 2
    assert mine["ledger"]["accounts"] == 1
    assert "Meridian Bank" in mine["ledger"]["institutions"]
    # Every account on the deployment, not only the viewer's own.
    assert body["totals"]["accounts"] >= 2
    assert body["totals"]["transactions"] >= 6


def test_it_counts_visits_rather_than_sign_ins(admin):
    """One 72-hour cookie covers a week of visits, so session rows are the
    wrong unit for "how often do they come back"."""
    db = get_db()
    token = store.create_session(db, admin.id, ttl_hours=6)
    for _ in range(3):
        store.resolve_session(db, token)

    row = next(r for r in client.get("/api/admin/overview").json()["accounts"]
               if r["email"] == admin.email)
    assert row["requests"] >= 3
    assert row["sign_ins"] >= 1
    assert row["requests"] > row["sign_ins"]


def test_it_reports_no_amounts_anywhere(admin):
    """The line this view is drawn on: it counts rows without reading them."""
    _seed_ledger(3)
    body = client.get("/api/admin/overview").json()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in {
                    "amount", "income", "spend", "balance", "total_spend",
                    "net_savings", "description", "merchant", "category",
                }, f"an operator's view must not carry {key!r}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    assert "1000" not in str(body["accounts"]), "no amount reached the payload"


def test_a_demo_workspace_is_not_counted_as_a_person(admin):
    """It is generated data belonging to somebody's switch, not a signup."""
    before = client.get("/api/admin/overview").json()["totals"]["accounts"]
    demo.ensure_workspace(get_db(), admin.id, "Owner")
    after = client.get("/api/admin/overview").json()
    assert after["totals"]["accounts"] == before
    assert not any(row["email"].endswith("@demo.invalid")
                   for row in after["accounts"])
    assert next(r for r in after["accounts"]
                if r["email"] == admin.email)["has_demo_workspace"] is True


def test_skipping_the_detail_still_lists_the_accounts(admin):
    _seed_ledger(2)
    body = client.get("/api/admin/overview?detail=false").json()
    assert body["accounts"]
    assert "ledger" not in body["accounts"][0]
    assert body["detail_limit"] == 0


# --------------------------------------------------------------------------
# Demo mode
# --------------------------------------------------------------------------

def test_the_demo_workspace_is_a_separate_account_with_its_own_rows():
    user = store.get_user(get_db(), TENANT.get())
    _seed_ledger(2)

    workspace = demo.ensure_workspace(get_db(), user.id, user.display_name)
    assert workspace != user.id

    with tenant_scope(workspace):
        generated = repo.get_transactions(get_db())
    assert len(generated) > 100, "a demo worth demonstrating has a history"

    # The real ledger is untouched by any of it.
    assert repo.count_transactions(get_db()) == 2


def test_the_demo_ledger_holds_one_salary_per_month():
    """The case the accounting-month rule exists for, in the demo data itself:
    pay dated the last day of the month is credited on the next working day,
    so it lands on the 31st in one month and the 1st of the next in another,
    and no month may end up with two."""
    user = store.get_user(get_db(), TENANT.get())
    workspace = demo.ensure_workspace(get_db(), user.id, "Demo")

    with tenant_scope(workspace):
        rows = repo.get_transactions(get_db())

    salaries = [t for t in rows if t.category == Category.SALARY]
    assert len(salaries) >= 12
    per_month = {}
    for row in salaries:
        per_month.setdefault(row.accounting_month, []).append(row)
    doubled = {m: len(v) for m, v in per_month.items() if len(v) > 1}
    assert not doubled, f"a month held two salaries: {doubled}"


def test_the_demo_ledger_actually_contains_the_boundary_case():
    """...and the case is present, or the demo proves nothing.

    Generated against a fixed date rather than today's: which months end at a
    weekend is a property of the calendar, and a demo whose point depends on
    it should be checked on a calendar that is known rather than on whichever
    one the suite happens to run under. 31 May 2025 was a
    Saturday, so May's pay landed on Monday 2 June - which puts TWO credits
    in calendar June (the 2nd and the 30th) and none in calendar May, the
    exact shape the rule was written for.
    """
    today = date(2026, 6, 15)
    rows, _, _ = demo.build_rows(
        {"bank": "bank", "card": "card", "loan": "loan"}, today)
    series = detect_recurring(rows)
    assign_accounting_months(
        rows, series,
        {s.id: (s.period_start, s.period_end)
         for s, _ in demo._statements(
             {"bank": "bank", "card": "card", "loan": "loan"}, rows, today)})

    salaries = [t for t in rows if t.category == Category.SALARY]
    crossed = [t for t in salaries
               if t.accounting_month != t.txn_date.strftime("%Y-%m")]
    assert crossed, "no salary crossed a month boundary"
    landed = {t.txn_date.strftime("%Y-%m-%d"): t.accounting_month
              for t in salaries}
    # Two credits in calendar June 2025, at either end of it...
    assert landed.get("2025-06-02") == "2025-05", landed
    assert landed.get("2025-06-30") == "2025-06", landed
    # ...and calendar May, which received nothing, is still paid.
    assert "2025-05" in set(landed.values()), landed
    assert landed.get("2026-06-01") == "2026-05", landed
    # And no month gained a second one in the process.
    per_month = {}
    for row in salaries:
        per_month.setdefault(row.accounting_month, []).append(row)
    assert not [m for m, v in per_month.items() if len(v) > 1]


def test_the_demo_ledger_stops_at_today():
    """A demo that shows next week's groceries - and a balance including pay
    not yet received - teaches the wrong thing about every figure on screen."""
    today = date(2026, 6, 15)
    rows, _, _ = demo.build_rows(
        {"bank": "bank", "card": "card", "loan": "loan"}, today)
    ahead = [t for t in rows if t.txn_date > today]
    assert not ahead, f"{len(ahead)} rows dated after the demo was generated"
    assert any(t.txn_date.month == today.month for t in rows), \
        "the current month should be present, just partial"


def test_the_demo_ledger_exercises_what_it_is_meant_to_show():
    user = store.get_user(get_db(), TENANT.get())
    workspace = demo.ensure_workspace(get_db(), user.id, "Demo")
    with tenant_scope(workspace):
        rows = repo.get_transactions(get_db())
        accounts = repo.get_accounts(get_db())

    kinds = {a.account_type for a in accounts}
    assert AccountType.CREDIT_CARD in kinds and AccountType.SAVINGS in kinds
    assert any(a.account_type in LOAN_TYPES
               for a in accounts), "no loan, so Debt and payoff dates are empty"
    assert any(t.is_mirror_leg for t in rows), "no matched card bill"
    assert any(t.needs_review for t in rows), "an empty review queue shows nothing"
    assert any(t.category == Category.INVESTMENT for t in rows), "no SIP"


def test_the_demo_ledger_has_both_halves_of_the_fixed_variable_split():
    """A ledger where everything recurs answers none of the questions.

    The first version of this data put every charge with the same payee on
    the same day of every month, so the recurring detector - correctly -
    called all fourteen of them commitments, and the Budget tab reported a
    person with no discretionary spending at all. "Which of my expenses are
    fixed?" cannot be demonstrated on a ledger that has only one answer.
    """
    today = date(2026, 6, 15)
    ids = {"bank": "bank", "card": "card", "loan": "loan"}
    rows, _, _ = demo.build_rows(ids, today)
    series = detect_recurring(rows)
    assign_accounting_months(
        rows, series,
        {s.id: (s.period_start, s.period_end)
         for s, _ in demo._statements(ids, rows, today)})

    accounts = {}
    for key, account in demo._accounts().items():
        account.id = key
        accounts[key] = account
    result = analyse_budget(rows, series, period=resolve_period(
        {"preset": "all"}, today=today), accounts=accounts, today=today)

    # Both halves are present and substantial.
    assert result.commitments, "nothing was found to be fixed"
    assert result.variable_typical > 0, \
        "no discretionary spending, so the split has nothing to show"

    # A commitment is something that turned up nearly every month. This is
    # the check that fails if the tail ever becomes regular enough to be
    # mistaken for one.
    for c in result.commitments:
        assert c.months_seen >= result.months - 2, \
            f"{c.label} was seen in only {c.months_seen} of {result.months}"

    # The three kinds the Budget tab reports separately are all exercised.
    kinds = {c.kind for c in result.commitments}
    assert kinds == {"debt", "spending", "saving"}, kinds

    # And a month leaves something over, or the demo shows an insolvent
    # person and every screen reads as a warning.
    assert result.headroom > 0, result.headroom


def test_turning_demo_on_points_the_app_at_it_and_off_puts_it_back():
    _seed_ledger(2)
    assert client.get("/api/transactions").json()["total"] == 2

    on = client.post("/api/settings/demo", json={"enabled": True}).json()
    assert on["enabled"] is True and on["prepared"] is True
    # The whole app follows: this is the ordinary ledger endpoint, unchanged.
    assert client.get("/api/transactions").json()["total"] > 100

    off = client.post("/api/settings/demo", json={"enabled": False}).json()
    assert off["enabled"] is False
    assert client.get("/api/transactions").json()["total"] == 2, \
        "the real ledger came back exactly as it was"


def test_a_demo_leaves_no_mark_on_the_real_ledger():
    """The point of a separate account: everything a demo does stays in it."""
    _seed_ledger(3)
    client.post("/api/settings/demo", json={"enabled": True})

    # Something destructive, during the demo.
    rows = client.get("/api/transactions?limit=5").json()["transactions"]
    client.patch(f"/api/transactions/{rows[0]['id']}",
                 json={"category": "entertainment"})

    client.post("/api/settings/demo", json={"enabled": False})
    mine = client.get("/api/transactions").json()
    assert mine["total"] == 3
    assert {t["category"] for t in mine["transactions"]} == {"groceries"}


def test_rebuilding_the_demo_only_touches_the_demo():
    _seed_ledger(2)
    client.post("/api/settings/demo", json={"enabled": True})
    before = client.get("/api/transactions").json()["total"]

    rebuilt = client.post("/api/settings/demo/rebuild").json()
    assert rebuilt["status"] == "rebuilt"
    assert client.get("/api/transactions").json()["total"] == before

    client.post("/api/settings/demo", json={"enabled": False})
    assert client.get("/api/transactions").json()["total"] == 2


def test_the_demo_switch_reports_what_is_in_the_workspace():
    body = client.post("/api/settings/demo", json={"enabled": True}).json()
    workspace = body["workspace"]
    assert workspace["transactions"] > 100
    assert workspace["months"] >= 12
    assert workspace["first_month"] < workspace["last_month"]
    client.post("/api/settings/demo", json={"enabled": False})


def test_a_demo_workspace_cannot_be_signed_in_as():
    """It is a workspace, not a person: nothing must ever authenticate as it."""
    user = store.get_user(get_db(), TENANT.get())
    workspace = demo.ensure_workspace(get_db(), user.id, "Demo")
    row = store.get_user(get_db(), workspace)
    assert row.demo_of == user.id
    # Google issues numeric subjects; this one is minted locally and could
    # never arrive from a real sign-in.
    assert row.google_sub.startswith("demo-workspace:")
    assert row.email.endswith("@demo.invalid")
