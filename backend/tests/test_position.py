"""The attested position, and the arithmetic that keeps it from going stale.

The whole design rests on one claim: a figure a person confirmed on a date is
still usable months later, because what happens to it in between is
calculable. These tests are that claim, checked - and its limits, which matter
just as much. A loan ages deterministically. A credit card does not, and a
screen that pretended otherwise would be inventing a liability.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics import position as pos  # noqa: E402
from app.analytics.loans import months_to_payoff  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.main import app  # noqa: E402
from app.models.schemas import Account, AccountType  # noqa: E402

from .support import fresh_ledger  # noqa: E402

client = TestClient(app)


def item(**fields):
    base = {"id": "i1", "kind": "loan", "label": "Home loan",
            "institution": "Meridian", "reviewed_on": "2026-01-05"}
    return {**base, **fields}


# --------------------------------------------------------------------------
# Solving for the term nobody wrote down
# --------------------------------------------------------------------------

def test_the_rate_is_recoverable_from_the_other_three_terms():
    """Nobody remembers their interest rate, and every projection needs it.

    There is exactly one rate at which a given balance takes a given number
    of instalments to clear, so a user who knows the balance, the EMI and the
    term should not have to go and find the rate before this screen works.
    """
    rate = pos.infer_rate(Decimal("4200000"), Decimal("34200"), 221)
    assert rate is not None
    # The recovered rate must reproduce the term it was derived from.
    assert months_to_payoff(Decimal("4200000"), rate, Decimal("34200")) == 221


def test_a_term_no_rate_can_produce_is_refused():
    """100,000 at 5,000 a month cannot clear in 12 instalments at any rate -
    even at zero it takes 20. Guessing one would be worse than saying so."""
    assert pos.infer_rate(Decimal("100000"), Decimal("5000"), 12) is None
    assert pos.infer_rate(Decimal("100000"), Decimal("100"), 24) is None
    assert pos.infer_rate(Decimal("0"), Decimal("5000"), 24) is None


def test_the_missing_term_is_filled_in_and_labelled_as_derived():
    """A figure the app worked out and a figure the user confirmed are not
    the same kind of fact, and the screen has to be able to tell them apart."""
    filled = pos.complete_loan_terms(
        Decimal("4200000"), Decimal("34200"), None, 221)
    assert filled["interest_rate"] is not None
    assert filled["derived"] == ["interest_rate"]

    filled = pos.complete_loan_terms(
        Decimal("4200000"), Decimal("34200"), Decimal("8.45"), None)
    assert filled["months_remaining"] > 0
    assert filled["derived"] == ["months_remaining"]

    # Nothing invented when the user gave everything.
    filled = pos.complete_loan_terms(
        Decimal("4200000"), Decimal("34200"), Decimal("8.45"), 221)
    assert filled["derived"] == []


# --------------------------------------------------------------------------
# Cycles
# --------------------------------------------------------------------------

def test_a_cycle_day_is_clamped_to_shorter_months():
    """A statement dated the 31st is dated the 28th in February."""
    assert pos.next_on_day(31, date(2026, 2, 10)) == date(2026, 2, 28)
    assert pos.next_on_day(31, date(2026, 3, 1)) == date(2026, 3, 31)
    # Already past this month's, so it rolls to next month.
    assert pos.next_on_day(5, date(2026, 3, 20)) == date(2026, 4, 5)
    # On the day itself counts as today, not next month.
    assert pos.next_on_day(5, date(2026, 3, 5)) == date(2026, 3, 5)
    assert pos.next_on_day(None, date(2026, 3, 5)) is None


def test_cycles_are_counted_between_two_dates():
    assert pos.cycles_between(5, date(2026, 1, 4), date(2026, 4, 10)) == 4
    assert pos.cycles_between(5, date(2026, 1, 6), date(2026, 1, 20)) == 0
    # 5 Dec, 5 Jan, 5 Feb - the year boundary is not a special case.
    assert pos.cycles_between(5, date(2026, 12, 1), date(2027, 2, 6)) == 3
    assert pos.cycles_between(None, date(2026, 1, 1), date(2027, 1, 1)) == 0


# --------------------------------------------------------------------------
# A loan ages
# --------------------------------------------------------------------------

def test_a_loan_reviewed_three_months_ago_is_three_instalments_lighter():
    """The claim the whole design rests on."""
    # Whatever the term actually is at these figures - taken from the same
    # amortization the roll-forward walks, so the fixture cannot contradict
    # itself as real user input can (see the test below for when it does).
    term = months_to_payoff(Decimal("4200000"), Decimal("8.45"),
                            Decimal("34200"))
    aged = pos.age_item(
        item(outstanding="4200000", emi="34200", interest_rate="8.45",
             months_remaining=term, due_day=5, reviewed_on="2026-01-05"),
        as_of=date(2026, 4, 10))

    assert aged.emis_since_review == 3
    assert aged.outstanding < Decimal("4200000")
    assert aged.months_remaining == term - 3
    assert aged.principal_paid_since_review > 0
    assert aged.interest_paid_since_review > aged.principal_paid_since_review, \
        "early in a 20-year loan most of the EMI is interest"
    # The two together are three instalments, to the rupee.
    assert (aged.principal_paid_since_review
            + aged.interest_paid_since_review) == pytest.approx(
                Decimal("34200") * 3, abs=1)
    assert "rolled forward 3 instalment(s)" in aged.basis
    assert not aged.warnings


def test_four_terms_that_cannot_all_be_true_are_caught_when_entered():
    """A loan has four numbers and any three fix the fourth, so all four
    given can contradict each other - and usually do, because one was typed
    wrong or remembered from a different year.

    Caught at the moment it is entered rather than surfacing later as a
    payoff date four years out. 42,00,000 at 8.45% with a 34,200 instalment
    does not clear in 240 months, whatever the user believes.
    """
    aged = pos.age_item(
        item(outstanding="4200000", emi="34200", interest_rate="8.45",
             months_remaining=240, due_day=5, reviewed_on="2026-01-05"),
        as_of=date(2026, 4, 10))

    assert any("do not describe one loan" in w for w in aged.warnings)
    # It still projects, from the three figures that ARE consistent.
    assert aged.outstanding < Decimal("4200000")
    assert aged.months_remaining != 240


def test_the_day_the_emi_leaves_is_what_counts_not_the_calendar_month():
    """Counting whole calendar months is wrong by one for half of every
    month, which on a twenty-year loan is a payoff date a month out."""
    early = pos.age_item(
        item(outstanding="1000000", emi="20000", interest_rate="9",
             due_day=25, reviewed_on="2026-01-05"),
        as_of=date(2026, 2, 20))
    assert early.emis_since_review == 1, "only January's has gone out"

    late = pos.age_item(
        item(outstanding="1000000", emi="20000", interest_rate="9",
             due_day=25, reviewed_on="2026-01-05"),
        as_of=date(2026, 2, 26))
    assert late.emis_since_review == 2, "February's has now gone out too"


def test_a_loan_reviewed_today_is_exactly_what_was_attested():
    aged = pos.age_item(
        item(outstanding="4200000", emi="34200", interest_rate="8.45",
             months_remaining=240, reviewed_on="2026-04-10"),
        as_of=date(2026, 4, 10))
    assert aged.emis_since_review == 0
    assert aged.outstanding == Decimal("4200000")
    assert "rolled forward" not in aged.basis


def test_a_loan_that_has_run_its_course_reads_as_cleared():
    aged = pos.age_item(
        item(outstanding="60000", emi="21000", interest_rate="10",
             due_day=5, reviewed_on="2026-01-05"),
        as_of=date(2027, 6, 5))
    assert aged.outstanding == Decimal("0")
    assert aged.months_remaining == 0
    assert "cleared" in aged.basis


def test_a_loan_with_no_rate_and_no_emi_says_so_rather_than_guessing():
    """The honest failure. Without both terms there is no arithmetic to do,
    and a figure quoted as current when three instalments have gone out is a
    wrong number wearing a confident label."""
    aged = pos.age_item(
        item(outstanding="4200000", due_day=5, reviewed_on="2026-01-05"),
        as_of=date(2026, 4, 10))
    assert aged.outstanding == Decimal("4200000")
    assert aged.stale is True
    assert any("cannot be rolled forward" in w for w in aged.warnings)


def test_an_emi_that_does_not_cover_the_interest_is_reported_not_projected():
    aged = pos.age_item(
        item(outstanding="4200000", emi="1000", interest_rate="12",
             reviewed_on="2026-01-05"),
        as_of=date(2026, 4, 10))
    assert any("grows rather than reduces" in w for w in aged.warnings)


# --------------------------------------------------------------------------
# A card does not
# --------------------------------------------------------------------------

def test_a_card_balance_is_never_rolled_forward():
    """Projecting it would mean guessing what was spent, and a guessed
    liability is the one number on this screen that must not exist."""
    aged = pos.age_item(
        item(kind="card", label="Northwind Rewards", outstanding="82000",
             credit_limit="400000", statement_day=14, due_day=3,
             reviewed_on="2026-01-05"),
        as_of=date(2026, 4, 10))

    assert aged.outstanding == Decimal("82000"), "exactly as attested"
    assert aged.stale is True
    assert aged.cycles_since_review == 3
    assert "statement(s) have been generated since" in aged.basis


def test_a_cards_cycle_does_roll_because_that_part_is_arithmetic():
    aged = pos.age_item(
        item(kind="card", outstanding="82000", credit_limit="400000",
             statement_day=14, due_day=3, reviewed_on="2026-04-08"),
        as_of=date(2026, 4, 10))
    assert aged.next_statement_on == date(2026, 4, 14)
    assert aged.next_due_on == date(2026, 5, 3)
    assert aged.days_to_due == 23
    assert aged.utilisation_pct == 20.5
    assert aged.stale is False, "reviewed two days ago, no cycle has closed"


def test_a_balance_that_neither_amortizes_nor_cycles_just_gets_old():
    fresh = pos.age_item(
        item(kind="account", outstanding="182000", reviewed_on="2026-04-01"),
        as_of=date(2026, 4, 10))
    assert fresh.outstanding == Decimal("182000") and fresh.stale is False

    old = pos.age_item(
        item(kind="account", outstanding="182000", reviewed_on="2026-01-01"),
        as_of=date(2026, 4, 10))
    assert old.stale is True
    assert "99 days ago" in old.basis


# --------------------------------------------------------------------------
# Checked against the documents
# --------------------------------------------------------------------------

def test_the_statements_disagreeing_is_reported_rather_than_resolved():
    """Neither side wins. A statement is checked and an attestation is not,
    so the statement is usually right - but the reason this table exists is
    that statements are sometimes absent or months behind, and silently
    overwriting the user's own figure is the same mistake in reverse."""
    aged = pos.age_item(
        item(outstanding="4200000", emi="34200", interest_rate="8.45",
             due_day=5, reviewed_on="2026-01-05"),
        as_of=date(2026, 4, 10),
        observed={"outstanding": Decimal("3900000"),
                  "as_of": date(2026, 4, 1)})

    assert aged.drift is not None
    assert aged.drift < 0, "the statements say less is owed than the projection"
    assert aged.observed_outstanding == Decimal("3900000")
    assert aged.outstanding > Decimal("4100000"), \
        "the attested figure is NOT overwritten"
    assert any("the statements say" in w for w in aged.warnings)


def test_agreement_within_tolerance_is_not_reported_as_drift():
    aged = pos.age_item(
        item(outstanding="1000000", emi="20000", interest_rate="9",
             reviewed_on="2026-04-10"),
        as_of=date(2026, 4, 10),
        observed={"outstanding": Decimal("1000500"), "as_of": date(2026, 4, 9)})
    assert aged.drift is None
    assert not aged.warnings


# --------------------------------------------------------------------------
# The whole position
# --------------------------------------------------------------------------

def _accounts():
    return [
        Account(id="loan-1", institution="Meridian",
                account_type=AccountType.HOME_LOAN,
                account_number_masked="7781",
                principal_outstanding=Decimal("4150000"),
                interest_rate=Decimal("8.45"), emi_amount=Decimal("34200"),
                balance_as_of=date(2026, 3, 31)),
        Account(id="card-1", institution="Northwind",
                account_type=AccountType.CREDIT_CARD,
                account_number_masked="9931",
                credit_limit=Decimal("400000")),
        Account(id="bank-1", institution="HDFC",
                account_type=AccountType.SAVINGS,
                account_number_masked="4412",
                current_balance=Decimal("182000")),
    ]


def _bureau():
    return [
        {"id": "b1", "lender": "Meridian Bank", "account_type": "home_loan",
         "status": "open", "current_balance": "4150000",
         "emi_amount": "34200", "account_number_masked": "7781",
         "account_id": "loan-1"},
        # The one that matters: a live loan no statement covers.
        {"id": "b2", "lender": "Bajaj Finance", "account_type": "personal_loan",
         "status": "open", "current_balance": "180000", "emi_amount": "9200",
         "sanctioned": "300000", "account_number_masked": "5521",
         "account_id": None},
        {"id": "b3", "lender": "Old Bank", "account_type": "auto_loan",
         "status": "closed", "current_balance": "0", "account_id": None},
    ]


def test_a_position_totals_what_is_owed_and_what_is_held():
    built = pos.build(
        [item(id="l", kind="loan", outstanding="4200000", emi="34200",
              interest_rate="8.45", reviewed_on="2026-04-10"),
         item(id="c", kind="card", label="Card", outstanding="82000",
              credit_limit="400000", due_day=3, reviewed_on="2026-04-10"),
         item(id="a", kind="account", label="Savings", outstanding="182000",
              reviewed_on="2026-04-10")],
        [], [], as_of=date(2026, 4, 10))

    totals = built["totals"]
    assert totals["loan_outstanding"] == 4200000.0
    assert totals["card_outstanding"] == 82000.0
    assert totals["total_owed"] == 4282000.0
    assert totals["card_utilisation_pct"] == 20.5
    assert totals["assets"] == 182000.0
    assert totals["net"] == 182000.0 - 4282000.0
    assert totals["monthly_emi"] == 34200.0
    assert totals["debt_free_on"] is not None


def test_a_live_bureau_account_nothing_covers_is_named():
    """The single most important thing this screen does.

    A total assembled from the statements alone is short by whatever an
    unmapped loan holds, and nothing anywhere else in the app says so.
    """
    built = pos.build([], _accounts(), _bureau(), as_of=date(2026, 4, 10))
    unaccounted = built["unaccounted"]["bureau"]
    lenders = [b["lender"] for b in unaccounted]
    assert "Bajaj Finance" in lenders
    assert "Old Bank" not in lenders, \
        "a closed account nobody has statements for is history, not a blind spot"


def test_mapping_an_item_takes_the_bureau_line_off_the_unaccounted_list():
    built = pos.build(
        [item(id="p1", kind="loan", label="Bajaj personal loan",
              bureau_account_id="b2", outstanding="180000", emi="9200",
              reviewed_on="2026-04-10")],
        _accounts(), _bureau(), as_of=date(2026, 4, 10))
    assert not [b for b in built["unaccounted"]["bureau"]
                if b["lender"] == "Bajaj Finance"]


def test_an_archived_item_is_out_of_the_totals_but_not_gone():
    live = pos.build(
        [item(id="x", outstanding="100000", emi="5000", interest_rate="9",
              reviewed_on="2026-04-10", archived=1)],
        [], [], as_of=date(2026, 4, 10))
    assert live["items"] == []
    assert live["totals"]["loan_outstanding"] is None, \
        "no loans is not the same claim as loans totalling nothing"

    shown = pos.build(
        [item(id="x", outstanding="100000", emi="5000", interest_rate="9",
              reviewed_on="2026-04-10", archived=1)],
        [], [], as_of=date(2026, 4, 10), include_archived=True)
    assert len(shown["items"]) == 1 and shown["items"][0]["archived"] is True


# --------------------------------------------------------------------------
# Seeding: nobody types twelve accounts in from memory
# --------------------------------------------------------------------------

def test_seeding_drafts_the_position_from_what_is_already_known():
    drafts = pos.seed(_accounts(), _bureau(), [], as_of=date(2026, 4, 10),
                      bureau_as_of=date(2026, 2, 28))
    by_kind = {}
    for draft in drafts:
        by_kind.setdefault(draft["kind"], []).append(draft)

    assert len(by_kind["loan"]) == 2, "the ledger loan and the bureau-only one"
    assert len(by_kind["card"]) == 1
    assert len(by_kind["account"]) == 1

    loan = next(d for d in by_kind["loan"] if d.get("account_id") == "loan-1")
    assert loan["outstanding"] == "4150000"
    assert loan["emi"] == "34200"
    # Dated when the figure is true as of, NOT today - saying "you confirmed
    # this today" on somebody's behalf is the lie this design exists to avoid.
    assert loan["reviewed_on"] == "2026-03-31"

    bureau_only = next(d for d in by_kind["loan"]
                       if d.get("bureau_account_id") == "b2")
    assert bureau_only["reviewed_on"] == "2026-02-28", "the bureau's pull date"
    assert "no statement has been imported" in bureau_only["notes"]


def test_seeding_again_never_touches_what_is_already_there():
    """Re-runnable after importing a new statement, without undoing a single
    correction the user has made since."""
    existing = [{"id": "kept", "account_id": "loan-1",
                 "bureau_account_id": "b2"}]
    drafts = pos.seed(_accounts(), _bureau(), [], as_of=date(2026, 4, 10),
                      existing=existing)
    assert not [d for d in drafts if d.get("account_id") == "loan-1"]
    assert not [d for d in drafts if d.get("bureau_account_id") == "b2"]
    assert len(drafts) == 2, "the card and the savings account are still new"


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------

@pytest.fixture
def seeded():
    db = fresh_ledger()
    for account in _accounts():
        repo.upsert_account(db, account)
    return db


def test_the_whole_round_trip(seeded):
    """Seed, correct, map, re-attest, snapshot - the way the screen drives it."""
    assert client.post("/api/position/seed").json()["added"] == 3

    payload = client.get("/api/position").json()
    assert len(payload["items"]) == 3
    loan = next(i for i in payload["items"] if i["kind"] == "loan")

    # Correct a figure. The response carries the consequences, so the screen
    # does not have to re-fetch everything to show one keystroke's effect.
    # The rate is cleared deliberately: it is the term nobody remembers, and
    # a user correcting a balance and an EMI should not be blocked on finding
    # it. Sending an explicit null is how a field is UNSET, as distinct from
    # being left alone.
    patched = client.patch(f"/api/position/items/{loan['id']}",
                           json={"outstanding": "4000000", "emi": "36000",
                                 "months_remaining": 180,
                                 "interest_rate": None}).json()
    assert patched["item"]["attested_outstanding"] == 4000000.0
    assert patched["item"]["interest_rate"] is not None, \
        "the rate is recovered from the other three terms"
    assert "interest_rate" in patched["item"]["derived"], \
        "and it is labelled as worked out rather than confirmed"

    # Re-attest it as of today, which resets the roll-forward: reviewed now,
    # so nothing has aged and the current figure IS the attested one.
    today = date.today().isoformat()
    reviewed = client.post(f"/api/position/items/{loan['id']}/review",
                           json={"reviewed_on": today}).json()
    assert reviewed["item"]["reviewed_on"] == today
    assert reviewed["item"]["emis_since_review"] == 0
    assert reviewed["item"]["outstanding"] == 4000000.0

    # Freeze the lot.
    snapshot = client.post("/api/position/review",
                           json={"note": "quarterly check"}).json()
    assert snapshot["items"] == 3
    assert snapshot["reviewed_on"] == today

    listed = client.get("/api/position/snapshots").json()["snapshots"]
    assert len(listed) == 1 and listed[0]["note"] == "quarterly check"
    frozen = client.get(f"/api/position/snapshots/{listed[0]['id']}").json()
    assert len(frozen["items"]) == 3
    assert frozen["totals"]["loan_outstanding"] == 4000000.0


def test_an_item_can_be_added_and_removed_by_hand(seeded):
    created = client.post("/api/position/items", json={
        "kind": "loan", "label": "Loan from my brother",
        "outstanding": "250000", "emi": "10000", "reviewed_on": "2026-04-01",
    }).json()
    assert created["status"] == "ok"

    payload = client.get("/api/position").json()
    assert any(i["label"] == "Loan from my brother" for i in payload["items"])

    # Archived by default - a snapshot taken while it existed still refers to
    # it, and "I closed this in March" is a fact rather than a gap.
    client.delete(f"/api/position/items/{created['id']}")
    assert not any(i["id"] == created["id"]
                   for i in client.get("/api/position").json()["items"])
    with_archived = client.get("/api/position?include_archived=true").json()
    assert any(i["id"] == created["id"] for i in with_archived["items"])


def test_a_bad_kind_is_refused(seeded):
    response = client.post("/api/position/items",
                           json={"kind": "spaceship", "label": "x"})
    assert response.status_code == 400
    assert "not a kind" in response.json()["detail"]


def test_seeding_twice_adds_nothing_the_second_time(seeded):
    assert client.post("/api/position/seed").json()["added"] == 3
    second = client.post("/api/position/seed").json()
    assert second["added"] == 0
    assert second["already_present"] == 3


def test_the_mappable_list_says_what_is_already_claimed(seeded):
    client.post("/api/position/seed")
    mappable = client.get("/api/position/mappable").json()
    assert len(mappable["accounts"]) == 3
    assert all(a["claimed_by"] for a in mappable["accounts"]), \
        "seeding mapped every one of them"
    assert "loan" in mappable["kinds"]


def test_the_agent_tool_reads_the_position(seeded):
    from app.agents import toolbelt

    empty = toolbelt.call(seeded, "position", {})
    assert empty["reviewed"] is False
    assert "no attested position" in empty["note"]

    client.post("/api/position/seed")
    filled = toolbelt.call(seeded, "position", {})
    assert filled["reviewed"] is True
    # The seeded loan is dated to the statement it came from, so by the time
    # anybody reads it some instalments have gone out and the figure the
    # agent sees is lower than the one on that statement. That IS the point.
    loan = next(i for i in filled["items"] if i["kind"] == "loan")
    assert 0 < loan["outstanding"] < 4150000.0
    assert "rolled forward" in loan["basis"]
    assert all("attested_outstanding" not in i for i in filled["items"]), \
        "the agent gets the current figure, not the screen's full row"


def test_every_agent_can_read_the_position():
    """It outranks every other source, so no agent should be without it."""
    from app.agents import catalogue

    for agent in catalogue.AGENTS:
        assert "position" in agent.tools, agent.key


# --------------------------------------------------------------------------
# A blank is not a zero
# --------------------------------------------------------------------------

def test_a_missing_figure_never_totals_as_zero():
    """The most dangerous shortcut on this screen, and the one that bit.

    A savings account whose balance nobody has recorded is not an account
    holding nothing. Summed with a zero default it produced "assets: 0" and a
    net worth stated as if the person owned nothing - from a position that
    had one blank field in it. A card with no balance likewise came out at
    "0% utilisation", which reads as a compliment.
    """
    built = pos.build(
        [item(id="l", kind="loan", outstanding="4200000", emi="34200",
              interest_rate="8.45", reviewed_on="2026-04-10"),
         item(id="c", kind="card", label="Card", credit_limit="250000",
              reviewed_on="2026-04-10"),          # no balance recorded
         item(id="a", kind="account", label="Savings",
              reviewed_on="2026-04-10")],         # no balance recorded
        [], [], as_of=date(2026, 4, 10))

    totals = built["totals"]
    assert totals["card_outstanding"] is None
    assert totals["card_utilisation_pct"] is None, \
        "0% used would read as a compliment on a card nobody has priced"
    assert totals["assets"] is None
    assert totals["is_complete"] is False
    assert totals["unknown"] == {"loans": 0, "cards": 1, "assets": 1}
    # What IS known is still totalled - the screen is not blanked over one gap.
    assert totals["loan_outstanding"] == 4200000.0
    assert totals["total_owed"] == 4200000.0


def test_utilisation_only_counts_cards_that_have_both_numbers():
    """Averaging a known balance against a limit that includes unpriced
    cards understates it by however many are blank."""
    built = pos.build(
        [item(id="c1", kind="card", label="A", outstanding="80000",
              credit_limit="200000", reviewed_on="2026-04-10"),
         item(id="c2", kind="card", label="B", credit_limit="800000",
              reviewed_on="2026-04-10")],
        [], [], as_of=date(2026, 4, 10))
    assert built["totals"]["card_utilisation_pct"] == 40.0, \
        "80,000 of the 200,000 that is actually priced, not of 1,000,000"
    assert built["totals"]["credit_limit"] == 1000000.0


def test_a_complete_position_says_so():
    built = pos.build(
        [item(id="l", kind="loan", outstanding="100000", emi="5000",
              interest_rate="9", reviewed_on="2026-04-10"),
         item(id="a", kind="account", label="Savings", outstanding="50000",
              reviewed_on="2026-04-10")],
        [], [], as_of=date(2026, 4, 10))
    assert built["totals"]["is_complete"] is True
    assert built["totals"]["net"] == 50000.0 - 100000.0


def test_a_seeded_row_is_dated_when_its_figure_is_true(seeded):
    """Not today. Saying "you confirmed this today" on somebody's behalf is
    the lie this whole design exists to avoid."""
    client.post("/api/position/seed")
    items = client.get("/api/position").json()["items"]
    loan = next(i for i in items if i["kind"] == "loan")
    # _accounts() gives the loan a balance_as_of of 31 March.
    assert loan["reviewed_on"] == "2026-03-31"
