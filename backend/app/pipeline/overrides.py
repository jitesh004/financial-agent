"""Apply the user's own decisions over everything the pipeline inferred.

This runs LAST, after every automatic classifier, and that ordering is the
whole point. Before it existed, `reconcile.transfers` reassigned a category
unconditionally the moment it paired a row - with no check of
`category_source` - so a category the user had corrected by hand was silently
overwritten on the next run by machinery that had no idea a human had already
ruled on it.

Nothing here infers anything. It copies stored decisions onto the matching
rows and reports what it could not place.
"""

from __future__ import annotations

import logging

from ..db import repository as repo
from ..models.schemas import Category, ConfidenceSource
from .fingerprint import account_key, loose_key

log = logging.getLogger(__name__)


class OverrideReport:
    """What applying the overrides actually did."""

    def __init__(self) -> None:
        self.applied = 0
        #: Matched on the loose key after the strict fingerprint missed, and
        #: re-pointed at the row's current fingerprint.
        self.repaired = 0
        #: Decisions whose transaction is not in this ledger at all. Usually
        #: benign - the statement it came from has not been re-parsed yet -
        #: so they are kept, never deleted.
        self.orphaned = 0
        self.notes: list[str] = []

    def as_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "repaired": self.repaired,
            "orphaned": self.orphaned,
            "notes": list(self.notes),
        }


def apply_overrides(db, transactions, accounts) -> OverrideReport:
    """Overlay stored user decisions onto `transactions`, in place."""
    report = OverrideReport()
    stored = repo.get_overrides(db)
    if not stored:
        return report

    by_fingerprint = {t.fingerprint: t for t in transactions if t.fingerprint}
    # Built lazily - the loose index is only needed when a strict lookup
    # misses, which is the uncommon path.
    loose_index: dict[tuple, list] | None = None

    for record in stored:
        if not record.has_any():
            continue

        txn = by_fingerprint.get(record.fingerprint)
        repaired = False

        if txn is None:
            if loose_index is None:
                loose_index = {}
                for t in transactions:
                    loose_index.setdefault(loose_key(t), []).append(t)

            candidates = loose_index.get((
                record.txn_date, record.amount, record.direction, record.desc_hash
            ), [])
            # Only an unambiguous recovery is accepted. If the same date,
            # amount, direction and description appear on two rows, there is
            # no honest way to tell which one the user meant, and applying it
            # to the wrong one is worse than applying it to neither.
            if len(candidates) == 1:
                txn = candidates[0]
                repaired = True
            elif len(candidates) > 1:
                report.orphaned += 1
                report.notes.append(
                    f"A saved decision matches {len(candidates)} identical rows "
                    f"on {record.txn_date} for {record.amount}; left unapplied "
                    f"because there is no way to tell which row was meant."
                )
                continue

        if txn is None:
            report.orphaned += 1
            continue

        _apply_one(record, txn)
        report.applied += 1

        if repaired:
            repo.repoint_override(
                db, record.fingerprint, txn.fingerprint,
                account_key(accounts.get(txn.account_id or "")),
            )
            report.repaired += 1

    if report.repaired:
        log.info("re-pointed %d user decision(s) onto new fingerprints",
                 report.repaired)
    return report


def _apply_one(record, txn) -> None:
    """Copy one stored decision onto one transaction."""
    if record.category is not None:
        try:
            txn.category = Category(record.category)
        except ValueError:
            # A category that no longer exists in the enum. Skip the category
            # but keep the rest of the decision rather than dropping it whole.
            log.warning("stored override names unknown category %r", record.category)
        else:
            txn.category_source = ConfidenceSource.USER
            txn.category_confidence = 1.0

    if record.flow_role is not None:
        txn.flow_role = record.flow_role
    if record.accounting_month is not None:
        txn.accounting_month = record.accounting_month
    if record.note is not None:
        txn.note = record.note
    if record.excluded is not None:
        txn.excluded = bool(record.excluded)

    # A row the user has ruled on is, by definition, no longer awaiting their
    # ruling - leaving it in the review queue would ask the same question
    # again on every run.
    txn.needs_review = False
    txn.review_reason = ""


def record_decision(db, txn, accounts=None, **fields) -> None:
    """Persist a user decision for `txn` so it survives re-processing.

    Writes through to both places deliberately: `user_overrides` is the
    durable copy that outlives a re-parse, and the transaction object carries
    the same values so the current run reflects the change immediately
    without a round trip.
    """
    unknown = set(fields) - set(repo.OVERRIDE_FIELDS)
    if unknown:
        raise ValueError(f"not overridable: {', '.join(sorted(unknown))}")

    if not txn.fingerprint:
        from .fingerprint import transaction_fingerprint
        acct_key = account_key((accounts or {}).get(txn.account_id or ""))
        txn.fingerprint = transaction_fingerprint(txn, acct_key)
    else:
        acct_key = account_key((accounts or {}).get(txn.account_id or ""))

    date_str, amount_str, direction_str, desc = loose_key(txn)
    record = repo.OverrideRecord(
        fingerprint=txn.fingerprint,
        account_key=acct_key,
        txn_date=date_str,
        amount=amount_str,
        direction=direction_str,
        desc_hash=desc,
        **fields,
    )
    repo.save_override(db, record)
    _apply_one(record, txn)
