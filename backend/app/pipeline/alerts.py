"""Turn transaction alert emails into ledger rows.

The statement pipeline can afford to be trusting: every row it produces is
checked against an opening and a closing balance the bank printed, so a
misreading shows up as a failed reconciliation rather than as a wrong total.
Nothing here has that safety net, which is why this module is mostly refusals.

Four rules, each of which exists because breaking it produces a confidently
wrong number:

  1. **An alert must name an account we already know.** The email gives four
     digits and a sender, nothing more. Inventing an account from that would
     put real money against a fiction; if no known account matches, the alert
     is skipped and says so.

  2. **An alert is never counted twice.** The same alert is often delivered
     twice, and re-running a scan re-reads the same mail. Identity is the
     existing content fingerprint, so a re-import updates rather than adds.

  3. **A statement always wins.** The moment a statement covering the same
     payment is imported, the alert is superseded and drops out of every
     total. The statement is reconciled; the alert is not.

  4. **Nothing here is reconcilable.** Rows are marked `source='email_alert'`
     so the balance gate skips them, rather than reporting the statement they
     sit beside as broken.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from ..ingestion import txn_email
from ..ingestion.bureau import lender_key
from ..ingestion.gmail_source import institution_for_sender
from ..models.schemas import (Account, ConfidenceSource, Direction,
                              Transaction)
from .fingerprint import account_key, transaction_fingerprint

log = logging.getLogger(__name__)

#: What an alert row is marked as, everywhere.
SOURCE = "email_alert"


@dataclass
class AlertOutcome:
    """What happened to one alert, so the UI can explain every skip."""

    message_id: str
    sender: str
    subject: str
    status: str            # imported | duplicate | superseded | skipped
    reason: str = ""
    amount: str = ""
    direction: str = ""
    txn_date: str = ""
    account_id: str | None = None
    account_label: str = ""
    merchant: str = ""


@dataclass
class AlertImport:
    transactions: list[Transaction] = field(default_factory=list)
    outcomes: list[AlertOutcome] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for outcome in self.outcomes:
            out[outcome.status] = out.get(outcome.status, 0) + 1
        return out


def _suffix_of(account: Account) -> str:
    digits = "".join(c for c in (account.account_number_masked or "")
                     if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else ""


def match_account(suffix: str, sender: str,
                  accounts: Sequence[Account]) -> tuple[Account | None, str]:
    """Which known account an alert refers to, and why.

    The four digits are the only identifier an alert carries, and they are not
    unique on their own - a savings account and a card can both end 1234. The
    sender's institution breaks that tie; when it cannot, the alert is skipped
    rather than assigned to whichever account happened to sort first.
    """
    if not suffix:
        return None, "the alert names no account number"

    candidates = [a for a in accounts if _suffix_of(a) == suffix]
    if not candidates:
        return None, f"no account here ends {suffix}"
    if len(candidates) == 1:
        return candidates[0], f"matched the account ending {suffix}"

    # The sender is an address, not a lender name: lender_key("alerts@
    # icicibank.com") collapses the mailbox and the domain into one token and
    # matches nothing. institution_for_sender maps the domain to the bank's
    # real name first, which is the form the accounts are stored under.
    wanted = lender_key(institution_for_sender(sender))
    narrowed = [a for a in candidates
                if wanted and lender_key(a.institution) == wanted]
    if len(narrowed) == 1:
        return narrowed[0], f"matched {narrowed[0].institution} ending {suffix}"

    return None, (f"{len(candidates)} accounts end {suffix} and the sender "
                  f"does not say which")


def build_transactions(alerts: Iterable[Any], accounts: Sequence[Account],
                       existing: Sequence[Transaction] = (),
                       today: date | None = None) -> AlertImport:
    """Parse alerts into transactions, refusing everything unsafe.

    `existing` is the ledger as it stands: used both to skip alerts already
    imported and to drop any whose statement row has already arrived. An alert
    that duplicates a reconciled row must never be added, not even flagged -
    it would be a second copy of a payment that is already counted.
    """
    result = AlertImport()
    by_id = {a.id: a for a in accounts if a.id}
    account_keys = {a.id: account_key(a) for a in accounts if a.id}

    seen_fingerprints = {t.fingerprint for t in existing if t.fingerprint}
    statement_rows = [t for t in existing if t.source != SOURCE]

    for alert in alerts:
        received = _received_date(alert, today)
        parsed = txn_email.parse_alert(
            getattr(alert, "body", ""), getattr(alert, "subject", ""), received)

        base = AlertOutcome(
            message_id=getattr(alert, "message_id", ""),
            sender=getattr(alert, "sender", ""),
            subject=getattr(alert, "subject", ""),
            status="skipped",
        )

        if parsed is None:
            base.reason = "not a completed transaction"
            result.outcomes.append(base)
            continue

        base.amount = str(parsed.amount)
        base.direction = parsed.direction
        base.txn_date = parsed.txn_date.isoformat() if parsed.txn_date else ""
        base.merchant = parsed.counterparty

        if parsed.txn_date is None:
            base.reason = "no date could be read, so it cannot be placed"
            result.outcomes.append(base)
            continue

        account, why = match_account(parsed.account_suffix,
                                     getattr(alert, "sender", ""), accounts)
        if account is None or not account.id:
            base.reason = why
            result.outcomes.append(base)
            continue

        base.account_id = account.id
        base.account_label = account.display_name()

        txn = _to_transaction(parsed, account)
        txn.fingerprint = transaction_fingerprint(
            txn, account_keys.get(account.id, ""))

        if txn.fingerprint in seen_fingerprints:
            base.status = "duplicate"
            base.reason = "already in the ledger"
            result.outcomes.append(base)
            continue

        # A statement covering this payment has already been imported. The
        # alert adds nothing and would double the amount.
        if _already_on_a_statement(txn, statement_rows):
            base.status = "superseded"
            base.reason = "the statement for this payment is already here"
            result.outcomes.append(base)
            continue

        seen_fingerprints.add(txn.fingerprint)
        result.transactions.append(txn)
        base.status = "imported"
        base.reason = why
        result.outcomes.append(base)

    return result


def _received_date(alert: Any, today: date | None) -> date | None:
    """The email's own date, used when the body omits the year."""
    raw = getattr(alert, "date", "") or ""
    if raw:
        from email.utils import parsedate_to_datetime
        try:
            return parsedate_to_datetime(raw).date()
        except (TypeError, ValueError):
            pass
    return today or date.today()


def _to_transaction(parsed: Any, account: Account) -> Transaction:
    return Transaction(
        id=str(uuid.uuid4()),
        account_id=account.id,
        statement_id=None,
        txn_date=parsed.txn_date,
        raw_description=parsed.description,
        normalized_description=parsed.counterparty or parsed.description,
        merchant=parsed.counterparty or None,
        amount=parsed.amount,
        direction=Direction(parsed.direction),
        currency=account.currency or "INR",
        source=SOURCE,
        # Never rule-categorised on arrival: an alert carries a payee and
        # nothing else, so a guess here would be a guess about a figure that
        # is already unchecked. The review queue decides.
        category_source=ConfidenceSource.DEFAULT,
        needs_review=True,
        review_reason="Read from an email alert, not a reconciled statement.",
        accounting_month=parsed.txn_date.strftime("%Y-%m"),
        note="From a transaction alert. Unreconciled until the statement arrives.",
    )


def _already_on_a_statement(txn: Transaction,
                            statement_rows: Sequence[Transaction]) -> bool:
    for row in statement_rows:
        if row.account_id != txn.account_id:
            continue
        if row.direction != txn.direction or row.amount != txn.amount:
            continue
        if not row.txn_date or not txn.txn_date:
            continue
        if abs((row.txn_date - txn.txn_date).days) <= txn_email.SUPERSEDE_DAY_WINDOW:
            return True
    return False


# --------------------------------------------------------------------------
# The other direction: a statement arrives after the alerts
# --------------------------------------------------------------------------


def supersede_after_import(db, new_rows: Sequence[Transaction]) -> int:
    """Retire alerts that a freshly imported statement now accounts for.

    Called at the end of every statement import. Without it the ledger keeps
    both copies of every payment from the last fortnight, and spending grows by
    exactly the amount the user was most careful about capturing - the same
    failure transfer detection exists to prevent, arriving by a different road.
    """
    from ..db import repository as repo

    statement_rows = [t for t in new_rows if t.source != SOURCE]
    if not statement_rows:
        return 0

    alerts = [t for t in repo.get_transactions(db)
              if t.source == SOURCE and not t.superseded]
    if not alerts:
        return 0

    count = txn_email.supersede_matched(alerts, statement_rows)
    if count:
        repo.save_transactions(db, [a for a in alerts if a.superseded])
        log.info("superseded %d email alert(s) covered by the new statements",
                 count)
    return count
