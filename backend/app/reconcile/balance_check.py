"""The reconciliation gate.

Every statement declares an opening balance, a closing balance, and the rows in
between. If

    opening + credits - debits  !=  closing

then the parse is wrong, full stop. No amount of clever categorization or
narrative generation can rescue a ledger that doesn't add up, so this check runs
before anything else is allowed to consume the data.

This one check catches the overwhelming majority of extraction errors: dropped
rows, duplicated pages, a debit column read as credit, a decimal point lost to
OCR. It is the single highest-value component in the pipeline.

When the check fails we also try to localise the damage by walking the running
balance column, which usually points at the exact row where the parse broke.
"""

from __future__ import annotations

from decimal import Decimal

from ..models.schemas import (AccountType, Direction, LIABILITY_TYPES,
                              ReconciliationResult, ReconciliationStatus,
                              Statement, Transaction)

#: Rounding slack. Statements round to paise, and a few institutions publish
#: totals rounded to the rupee, so a sub-rupee gap is not evidence of a bug.
TOLERANCE = Decimal("1.00")

#: Above this, we stop calling it a rounding artefact and call it a broken parse.
MATERIAL_DISCREPANCY = Decimal("1.00")


def reconcile(statement: Statement, account_type: AccountType) -> ReconciliationResult:
    """Verify that the statement's transactions explain its balance movement."""

    txns = statement.transactions
    credits = sum((t.amount for t in txns if t.direction == Direction.CREDIT), Decimal("0"))
    debits = sum((t.amount for t in txns if t.direction == Direction.DEBIT), Decimal("0"))

    result = ReconciliationResult(
        status=ReconciliationStatus.NOT_APPLICABLE,
        opening_balance=statement.opening_balance,
        closing_balance=statement.closing_balance,
        total_credits=credits,
        total_debits=debits,
        transaction_count=len(txns),
    )

    if not txns:
        result.message = "No transactions to reconcile."
        return result

    if statement.opening_balance is None or statement.closing_balance is None:
        result.message = (
            "Statement did not declare both an opening and a closing balance, "
            "so the totals could not be independently verified."
        )
        result.suspect_rows = _walk_running_balance(txns, account_type)
        return result

    computed = _apply_movement(
        statement.opening_balance, credits, debits, account_type
    )
    discrepancy = computed - statement.closing_balance

    result.computed_closing = computed
    result.discrepancy = discrepancy

    if abs(discrepancy) <= TOLERANCE:
        result.status = ReconciliationStatus.PASSED
        result.message = (
            f"Balanced: {len(txns)} transactions account for the full movement "
            f"from {statement.opening_balance:,.2f} to {statement.closing_balance:,.2f}."
        )
        return result

    result.status = ReconciliationStatus.FAILED
    result.suspect_rows = _walk_running_balance(txns, account_type)
    result.message = _explain_failure(discrepancy, txns, result)
    return result


def _apply_movement(
    opening: Decimal,
    credits: Decimal,
    debits: Decimal,
    account_type: AccountType,
) -> Decimal:
    """Apply the period's movement to the opening balance.

    Liability accounts run the other way. On a credit card, a purchase (which we
    store as a DEBIT from the user's perspective) *increases* the outstanding
    balance, while a bill payment (a CREDIT) reduces it. Using the asset formula
    on a card statement produces a discrepancy of exactly twice the movement,
    which is a distinctive and very confusing failure - hence this branch.
    """
    if account_type in LIABILITY_TYPES:
        return opening + debits - credits
    return opening + credits - debits


def _walk_running_balance(
    txns: list[Transaction],
    account_type: AccountType,
) -> list[int]:
    """Find rows where the running balance column contradicts the amounts.

    Each row states the balance after it was applied. Given consecutive rows we
    can predict the next balance; where prediction and statement diverge, that
    row is where the parse went wrong. This turns "your file is broken
    somewhere" into "row 147 looks wrong", which is the difference between a
    usable error message and a useless one.
    """
    suspects: list[int] = []
    is_liability = account_type in LIABILITY_TYPES

    previous: Decimal | None = None
    for i, txn in enumerate(txns):
        if txn.balance_after is None:
            continue
        if previous is not None:
            movement = txn.amount if txn.direction == Direction.CREDIT else -txn.amount
            if is_liability:
                movement = -movement
            expected = previous + movement
            if abs(expected - txn.balance_after) > TOLERANCE:
                suspects.append(txn.source_row if txn.source_row is not None else i)
        previous = txn.balance_after

    return suspects[:25]  # a long list is noise; the first few localise the break


def _explain_failure(
    discrepancy: Decimal,
    txns: list[Transaction],
    result: ReconciliationResult,
) -> str:
    """Turn a numeric gap into an actionable diagnosis.

    Certain discrepancies have signature shapes worth naming explicitly,
    because each points at a different bug.
    """
    gap = abs(discrepancy)
    parts = [
        f"Reconciliation FAILED. Computed closing balance "
        f"{result.computed_closing:,.2f} but the statement declares "
        f"{result.closing_balance:,.2f} (off by {gap:,.2f})."
    ]

    # A gap equal to exactly one transaction means that row was dropped or
    # duplicated - the most common extraction failure by far.
    matches = [t for t in txns if t.amount == gap]
    if matches:
        sample = matches[0]
        parts.append(
            f"The gap exactly equals a transaction on {sample.txn_date} "
            f"({sample.raw_description[:60]!r}), suggesting one row was "
            f"duplicated or dropped."
        )

    # Twice a transaction's value means a direction flip: the row was counted
    # in the wrong column, so it is wrong by 2x its own amount.
    halves = [t for t in txns if t.amount * 2 == gap]
    if halves:
        sample = halves[0]
        parts.append(
            f"The gap is exactly twice the {sample.txn_date} transaction of "
            f"{sample.amount:,.2f}, which is the signature of a debit read as a "
            f"credit (or vice versa)."
        )

    if not matches and not halves:
        total_movement = result.total_credits + result.total_debits
        if total_movement and gap / total_movement > Decimal("0.5"):
            parts.append(
                "The gap is more than half the total movement, which usually "
                "means whole pages of transactions were missed, or the "
                "opening/closing balances were read from the wrong fields."
            )

    if result.suspect_rows:
        shown = ", ".join(str(r) for r in result.suspect_rows[:5])
        parts.append(f"Running balance first breaks at source row(s): {shown}.")

    return " ".join(parts)


def summarize(results: list[tuple[str, ReconciliationResult]]) -> str:
    """One-line-per-file summary for logs and the upload response."""
    lines = []
    for name, res in results:
        icon = {
            ReconciliationStatus.PASSED: "PASS",
            ReconciliationStatus.FAILED: "FAIL",
            ReconciliationStatus.NOT_APPLICABLE: "N/A ",
        }[res.status]
        lines.append(
            f"[{icon}] {name}: {res.transaction_count} txns, "
            f"credits {res.total_credits:,.2f}, debits {res.total_debits:,.2f}"
        )
    return "\n".join(lines)
