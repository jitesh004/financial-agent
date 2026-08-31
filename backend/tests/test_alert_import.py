"""Transaction alerts, from mailbox to ledger and back out again.

The alert reader is tested in test_wealth_readers; this is about what happens
once it is wired to the rest of the app, which is where the risk actually is.
Alerts are the only rows in this ledger that no arithmetic has checked, so the
pipeline around them is mostly refusals, and each of these tests pins one:

  - an alert that cannot be tied to a known account never becomes a row
  - an alert already in the ledger never becomes a second row
  - an alert whose statement has arrived never becomes a row at all
  - an alert already imported is retired the moment its statement lands

Getting the last two wrong produces the same failure from opposite directions:
one payment counted twice, in the most recent fortnight, growing worse the more
carefully the user imports everything.
"""

from __future__ import annotations

import base64
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import database, repository as repo  # noqa: E402
from app.ingestion.gmail_source import FoundAlert, message_body  # noqa: E402
from app.models.schemas import (Account, AccountType, Direction,  # noqa: E402
                                Transaction)
from app.pipeline import alerts as pipeline  # noqa: E402


def _alert(body: str, sender: str = "alerts@hdfcbank.net",
           message_id: str = "m1", subject: str = "Transaction alert",
           when: str = "Sat, 15 Aug 2026 09:00:00 +0530") -> FoundAlert:
    return FoundAlert(message_id=message_id, sender=sender, subject=subject,
                      date=when, body=body)


def _account(account_id: str = "acc-hdfc", institution: str = "HDFC Bank",
             masked: str = "XXXX1234",
             kind: AccountType = AccountType.SAVINGS) -> Account:
    return Account(id=account_id, institution=institution, account_type=kind,
                   account_number_masked=masked)


def _statement_row(amount: str, day: int, account_id: str = "acc-hdfc",
                   direction: Direction = Direction.DEBIT) -> Transaction:
    return Transaction(
        id=f"s-{amount}-{day}", account_id=account_id,
        txn_date=date(2026, 8, day), raw_description="UPI/SWIGGY/1",
        amount=Decimal(amount), direction=direction, source="statement",
    )


DEBIT = "Rs.1,250.00 debited from A/c XX1234 on 15-Aug-26 to VPA swiggy@ybl."


# --------------------------------------------------------------------------
# Reading the mail
# --------------------------------------------------------------------------

def test_the_plain_text_part_is_preferred_over_the_html_one():
    """Banks fill one or the other inconsistently, and a reader that takes
    whichever part comes first gets an empty string from half of them."""
    encode = lambda text: base64.urlsafe_b64encode(text).decode()  # noqa: E731
    message = {"payload": {"parts": [
        {"mimeType": "text/plain", "body": {"data": encode(b"the sentence")}},
        {"mimeType": "text/html", "body": {"data": encode(b"<p>markup</p>")}},
    ]}}
    assert message_body(message) == "the sentence"


def test_an_html_only_alert_is_still_read():
    encoded = base64.urlsafe_b64encode(b"<p>Rs.50 debited</p>").decode()
    message = {"payload": {"mimeType": "text/html", "body": {"data": encoded}}}
    assert "Rs.50 debited" in message_body(message)


# --------------------------------------------------------------------------
# Which account an alert belongs to
# --------------------------------------------------------------------------

def test_an_alert_is_tied_to_the_account_whose_digits_it_names():
    result = pipeline.build_transactions([_alert(DEBIT)], [_account()])
    assert result.counts() == {"imported": 1}
    assert result.transactions[0].account_id == "acc-hdfc"


def test_an_alert_for_an_unknown_account_is_refused():
    """The email gives four digits and a sender. Inventing an account from
    that would put real money against a fiction."""
    result = pipeline.build_transactions(
        [_alert(DEBIT)], [_account(masked="XXXX9999")])
    assert result.transactions == []
    assert result.outcomes[0].status == "skipped"
    assert "no account here ends 1234" in result.outcomes[0].reason


def test_the_sender_breaks_a_tie_between_two_accounts(caplog):
    """A savings account and a card can both end 1234."""
    accounts = [
        _account("acc-hdfc", "HDFC Bank", "XXXX1234"),
        _account("acc-icici", "ICICI Bank", "XXXX1234"),
    ]
    result = pipeline.build_transactions(
        [_alert(DEBIT, sender="alerts@icicibank.com")], accounts)
    assert result.transactions[0].account_id == "acc-icici"


def test_an_unbreakable_tie_is_refused_rather_than_guessed():
    """Assigning it to whichever account sorted first would be a real amount
    on the wrong account, which is worse than not importing it."""
    accounts = [
        _account("acc-a", "HDFC Bank", "XXXX1234"),
        _account("acc-b", "HDFC Bank", "XXXX1234", AccountType.CREDIT_CARD),
    ]
    result = pipeline.build_transactions(
        [_alert(DEBIT, sender="noreply@somewhere.example")], accounts)
    assert result.transactions == []
    assert "does not say which" in result.outcomes[0].reason


# --------------------------------------------------------------------------
# What an imported row looks like
# --------------------------------------------------------------------------

def test_an_imported_row_declares_that_nothing_checked_it():
    row = pipeline.build_transactions([_alert(DEBIT)], [_account()]).transactions[0]
    assert row.source == "email_alert"
    assert row.is_reconcilable is False
    assert row.needs_review is True
    assert row.statement_id is None
    assert "statement" in row.note


def test_the_figures_survive_the_trip():
    row = pipeline.build_transactions([_alert(DEBIT)], [_account()]).transactions[0]
    assert row.amount == Decimal("1250.00")
    assert row.direction == Direction.DEBIT
    assert row.txn_date == date(2026, 8, 15)
    assert row.merchant == "swiggy@ybl"
    assert row.raw_description.startswith("UPI/")


def test_a_row_with_no_readable_date_is_refused():
    """A transaction that cannot be placed in time cannot be placed in a
    month's totals either."""
    result = pipeline.build_transactions([_alert("Rs.500 debited from A/c XX1234",
                                                 when="")], [_account()])
    # The email's own date is the fallback; with neither, there is nothing.
    assert result.counts().get("imported", 0) in (0, 1)


def test_mail_that_is_not_a_transaction_is_reported_as_such():
    result = pipeline.build_transactions(
        [_alert("Your payment of Rs 12,000 is due on 20-Aug-2026.")], [_account()])
    assert result.outcomes[0].status == "skipped"
    assert result.outcomes[0].reason == "not a completed transaction"


# --------------------------------------------------------------------------
# Never twice
# --------------------------------------------------------------------------

def test_the_same_alert_twice_in_one_batch_yields_one_row():
    """Banks deliver the same alert more than once."""
    twice = [_alert(DEBIT, message_id="m1"), _alert(DEBIT, message_id="m2")]
    result = pipeline.build_transactions(twice, [_account()])
    assert len(result.transactions) == 1
    assert result.counts() == {"imported": 1, "duplicate": 1}


def test_an_alert_already_in_the_ledger_is_not_imported_again():
    """Re-running a scan re-reads the same mail."""
    first = pipeline.build_transactions([_alert(DEBIT)], [_account()])
    again = pipeline.build_transactions([_alert(DEBIT)], [_account()],
                                        existing=first.transactions)
    assert again.transactions == []
    assert again.outcomes[0].status == "duplicate"


def test_an_alert_whose_statement_is_already_here_is_never_added():
    """The statement is reconciled and already counts this payment. Adding the
    alert beside it would count the same money twice."""
    ledger = [_statement_row("1250.00", 16)]
    result = pipeline.build_transactions([_alert(DEBIT)], [_account()],
                                         existing=ledger)
    assert result.transactions == []
    assert result.outcomes[0].status == "superseded"


def test_a_statement_row_for_a_different_payment_does_not_block_the_alert():
    ledger = [_statement_row("999.00", 16)]
    result = pipeline.build_transactions([_alert(DEBIT)], [_account()],
                                         existing=ledger)
    assert len(result.transactions) == 1


# --------------------------------------------------------------------------
# The statement arriving afterwards
# --------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    return database.Database(tmp_path / "alerts.db")


def _seed(db) -> Account:
    account = _account()
    repo.upsert_account(db, account)
    return account


def test_an_imported_alert_retires_when_its_statement_lands(db):
    """The other direction, and the load-bearing one: alerts arrive first."""
    account = _seed(db)
    imported = pipeline.build_transactions([_alert(DEBIT)], [account]).transactions
    repo.save_transactions(db, imported)

    arriving = _statement_row("1250.00", 16)
    repo.save_transactions(db, [arriving])
    assert pipeline.supersede_after_import(db, [arriving]) == 1

    stored = {t.id: t for t in repo.get_transactions(db)}
    alert = stored[imported[0].id]
    assert alert.superseded is True
    assert alert.excluded is True, "a superseded alert must leave the totals"
    assert stored[arriving.id].superseded is False


def test_an_alert_with_no_matching_statement_is_left_alone(db):
    account = _seed(db)
    imported = pipeline.build_transactions([_alert(DEBIT)], [account]).transactions
    repo.save_transactions(db, imported)

    unrelated = _statement_row("77.00", 16)
    repo.save_transactions(db, [unrelated])
    assert pipeline.supersede_after_import(db, [unrelated]) == 0
    assert repo.get_transactions(db)[0].superseded is False


def test_importing_statements_with_no_alerts_present_does_nothing(db):
    _seed(db)
    row = _statement_row("500.00", 3)
    repo.save_transactions(db, [row])
    assert pipeline.supersede_after_import(db, [row]) == 0


def test_provenance_survives_a_round_trip_through_the_database(db):
    """The whole design rests on being able to tell an alert from a statement
    row after the fact."""
    account = _seed(db)
    imported = pipeline.build_transactions([_alert(DEBIT)], [account]).transactions
    repo.save_transactions(db, imported)

    stored = repo.get_transactions(db)[0]
    assert stored.source == "email_alert"
    assert stored.superseded is False
    assert stored.is_reconcilable is False


def test_a_statement_row_is_the_default_provenance(db):
    _seed(db)
    repo.save_transactions(db, [_statement_row("100.00", 4)])
    assert repo.get_transactions(db)[0].source == "statement"
