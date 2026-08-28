"""Turn an ExtractionResult into a Statement of canonical Transactions.

This is where format-agnostic rows become finance. The work is:

  1. pick the transaction table(s) out of everything the extractor found
  2. merge tables that are continuations of each other (multi-page PDFs)
  3. recover a header, from the table, a sibling block, or the data itself
  4. parse each row, applying the account's sign convention
  5. hand back warnings rather than silently dropping rows

The design rule throughout: a row we cannot parse confidently is reported, not
guessed at. A dropped row shows up in the reconciliation gate as a discrepancy,
which is exactly the signal we want.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal

from ..models.schemas import (Account, AccountType, Direction, ExtractedTable,
                              ExtractionResult, LIABILITY_TYPES, SourceFormat,
                              Statement, Transaction)
from .column_map import (ColumnMapping, find_header_row, infer_roles_from_data,
                         looks_like_header, map_columns)
from .metadata import StatementMetadata, extract_metadata
from .parsers import (infer_date_order, normalize_description, parse_amount,
                      parse_date, parse_signed_amount, redact_account_numbers)

log = logging.getLogger(__name__)

#: A table needs at least this many parseable rows to be the transaction table.
#: One is permitted because a genuinely quiet month has a single transaction,
#: and a candidate must ALSO map to date + description + amount before it gets
#: this far, which excludes summary blocks.
MIN_PARSEABLE_ROWS = 1


class NormalizationError(Exception):
    """Raised only when no transaction table could be identified at all."""


def _apply_balance_to_account(account: Account, account_type: AccountType,
                              statement: Statement) -> None:
    """Copy a statement's closing balance onto its account, dated.

    The date is what makes this safe to merge later: statements do not
    always arrive in chronological order (Gmail search, a batch upload, a
    single-file retry can all process an old month after a newer one), and
    `graph.nodes._merge_account_facts` needs to know which of two candidate
    balances is actually the more recent one rather than assuming whichever
    file happened to be seen first.
    """
    if statement.closing_balance is None:
        return
    if account_type in LIABILITY_TYPES:
        account.principal_outstanding = statement.closing_balance
    else:
        account.current_balance = statement.closing_balance
    account.balance_as_of = statement.period_end


def normalize(
    extraction: ExtractionResult,
    filename: str,
    account_type_hint: AccountType | None = None,
    sender: str = "",
) -> tuple[Statement, Account]:
    """Convert extracted tables into a Statement plus the Account it belongs to.

    `sender` (the email address a Gmail-sourced file arrived from) is passed
    straight through to extract_metadata, where it overrides body-text
    institution detection when the two disagree - see the comment there.
    """

    meta = extract_metadata(
        _metadata_text(extraction), filename=filename, sender=sender
    )
    account_type = account_type_hint or meta.account_type or AccountType.UNKNOWN

    statement = Statement(
        source_filename=filename,
        source_format=extraction.source_format,
        extractor_used=extraction.extractor_used,
        parse_warnings=list(extraction.warnings),
        period_start=meta.period_start,
        period_end=meta.period_end,
        opening_balance=meta.opening_balance,
        closing_balance=meta.closing_balance,
    )
    statement.parse_warnings.extend(meta.notes)

    account = Account(
        institution=meta.institution or "Unknown",
        account_type=account_type,
        account_number_masked=meta.account_number_masked or "",
        product_name=meta.product_name,
        holder_name=meta.holder_name,
        currency=meta.currency,
        interest_rate=meta.interest_rate,
        emi_amount=meta.emi_amount,
        credit_limit=meta.credit_limit,
    )
    # A statement can legitimately contain nothing. slice sends a monthly
    # statement for a dormant account that says "No transactions found" with
    # every total at zero. Reporting that as a parse FAILURE is wrong: nothing
    # failed, and it sends the user hunting for a bug that isn't there.
    if _looks_empty(extraction.full_text):
        statement.extra["empty_statement"] = True
        statement.parse_warnings.append(
            "This statement reports no transactions for the period."
        )
        _apply_balance_to_account(account, account_type, statement)
        return statement, account

    candidates = _rank_tables(extraction.tables)
    if not candidates:
        statement.parse_warnings.append(
            "No table in this file looked like a transaction list."
        )
        _apply_balance_to_account(account, account_type, statement)
        return statement, account

    best_mapping, chosen = candidates[0]
    merged_rows = _merge_continuations(chosen, candidates, best_mapping)

    transactions, warnings = _rows_to_transactions(
        merged_rows, best_mapping, account_type, meta.currency,
        opening_balance=meta.opening_balance,
    )
    statement.transactions = transactions
    statement.parse_warnings.extend(warnings)
    _drop_rows_after_period(statement, meta.period_end)

    if best_mapping.inferred_from_data:
        statement.parse_warnings.append(
            "Column headers were missing, so column roles were inferred from the "
            "data. Verify debit/credit assignment before trusting totals."
        )

    _infer_period_from_rows(statement)
    _infer_balances_from_rows(statement, account_type)
    # After inference, not before it: a statement whose letterhead omits the
    # closing balance still has one derived from the last row's running
    # balance, and that derived figure must reach the account too.
    _apply_balance_to_account(account, account_type, statement)
    return statement, account


#: Phrases an issuer prints when an account had no activity at all.
_EMPTY_STATEMENT = re.compile(
    r"no\s+transactions?\s+(found|for\s+this\s+period|during)"
    r"|nil\s+transactions?"
    r"|there\s+are\s+no\s+transactions"
    r"|no\s+activity\s+(in|for)",
    re.IGNORECASE,
)


def _looks_empty(text: str) -> bool:
    """Whether the document itself declares that it holds no transactions."""
    return bool(text) and bool(_EMPTY_STATEMENT.search(text))


#: How much of the document counts as "letterhead" for metadata purposes.
_HEAD_LINES = 45
_TAIL_LINES = 15


def _metadata_text(extraction: ExtractionResult) -> str:
    """The slice of the document that plausibly contains statement metadata.

    Deliberately NOT the whole document. Transaction narrations are full of
    phrases that look like metadata - a savings statement contains "HOME LOAN
    EMI" on every EMI row, and matching that would relabel the entire account.
    Metadata lives in the letterhead at the top; totals sometimes appear in a
    footer, so we keep the tail too and drop everything in between.
    """
    text = extraction.full_text or "\n".join(
        t.surrounding_text for t in extraction.tables[:2]
    )
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) <= _HEAD_LINES + _TAIL_LINES:
        return "\n".join(lines)
    return "\n".join(lines[:_HEAD_LINES] + lines[-_TAIL_LINES:])


# --------------------------------------------------------------------------
# Table selection
# --------------------------------------------------------------------------

def _rank_tables(tables: list[ExtractedTable]) -> list[tuple[ColumnMapping, ExtractedTable]]:
    """Score every extracted table and return the usable ones, best first.

    A statement file typically contains several tables: a metadata block, the
    transactions, and sometimes a summary. We want the one that maps cleanly to
    transaction roles and has the most rows.
    """
    from collections import defaultdict

    entries: list[tuple[ColumnMapping, ExtractedTable, int]] = []
    for table in tables:
        mapping, body = _resolve_mapping(table)
        if mapping is None or not mapping.is_usable():
            continue
        parseable = _count_parseable(body, mapping)
        if parseable < MIN_PARSEABLE_ROWS:
            continue
        table.rows = body
        entries.append((mapping, table, parseable))

    # Group pages of the same logical table before scoring.
    #
    # Scoring individual tables is unfair across extraction strategies: a ruled
    # statement yields one table PER PAGE (45 rows each), while text-line
    # recovery yields a single table for the whole document (392 rows). Judged
    # row-by-row the text version always wins, even when the ruled version is
    # cleaner - and a lower-quality parse that still "works" is exactly the kind
    # of silent wrongness this pipeline exists to prevent.
    groups: dict[tuple, list[tuple[ColumnMapping, ExtractedTable, int]]] = defaultdict(list)
    for mapping, table, parseable in entries:
        key = (tuple(sorted(mapping.roles.items())), table.source_sheet)
        groups[key].append((mapping, table, parseable))

    scored: list[tuple[float, list]] = []
    for members in groups.values():
        total_rows = sum(p for _, _, p in members)
        mapping = members[0][0]
        best_confidence = max(t.confidence for _, t, _ in members)
        # Extraction confidence is weighted heavily enough to break a row-count
        # tie: when a ruled table and a text-recovered table both cover the same
        # rows, the structured one is materially more trustworthy.
        score = (total_rows * 10
                 + mapping.confidence * 20
                 + best_confidence * 40)
        scored.append((score, members))

    scored.sort(key=lambda s: -s[0])
    return [(m, t) for _, members in scored for m, t, _ in members]


def _resolve_mapping(table: ExtractedTable) -> tuple[ColumnMapping | None, list[list[str]]]:
    """Find this table's column mapping and return its data rows (header removed).

    Tries, in order: an explicit header field, a header row inside the table,
    then inference from cell content.
    """
    rows = table.rows
    if not rows:
        return None, []

    if table.header:
        mapping = map_columns(table.header)
        if mapping.is_usable():
            return mapping, rows

    header_idx = find_header_row(rows)
    if header_idx is not None:
        mapping = map_columns(rows[header_idx])
        if mapping.is_usable():
            return mapping, rows[header_idx + 1:]

    inferred = infer_roles_from_data(rows)
    if inferred.is_usable():
        # Drop any leading header-looking row so it isn't parsed as data.
        body = rows[1:] if looks_like_header(rows[0]) else rows
        return inferred, body

    return None, rows


def _count_parseable(rows: list[list[str]], mapping: ColumnMapping) -> int:
    date_col = mapping.get("txn_date")
    if date_col is None:
        return 0
    count = 0
    for row in rows:
        if date_col < len(row) and parse_date(row[date_col]) is not None:
            count += 1
    return count


def _merge_continuations(
    chosen: ExtractedTable,
    candidates: list[tuple[ColumnMapping, ExtractedTable]],
    mapping: ColumnMapping,
) -> list[list[str]]:
    """Stitch together tables that are continuations of the chosen one.

    A 40-page PDF statement yields ~40 tables with identical structure. Treating
    only the first as "the" transaction table would silently discard 97% of the
    user's data - a failure mode that produces a plausible-looking but
    completely wrong analysis, which is worse than an obvious crash.
    """
    merged: list[list[str]] = list(chosen.rows)

    for other_mapping, table in candidates[1:]:
        if table is chosen:
            continue
        # Same role layout on a later page means the same logical table.
        if other_mapping.roles != mapping.roles:
            continue
        if chosen.source_page is not None and table.source_page is None:
            continue
        if chosen.source_sheet != table.source_sheet:
            continue
        merged.extend(table.rows)

    return merged


# --------------------------------------------------------------------------
# Row parsing
# --------------------------------------------------------------------------

def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def _rows_to_transactions(
    rows: list[list[str]],
    mapping: ColumnMapping,
    account_type: AccountType,
    currency: str,
    opening_balance: Decimal | None = None,
) -> tuple[list[Transaction], list[str]]:
    warnings: list[str] = []
    transactions: list[Transaction] = []

    date_col = mapping.get("txn_date")
    if date_col is None:
        return [], ["No date column identified; cannot build transactions."]

    # Settle the date convention once for the whole statement.
    day_first = infer_date_order([_cell(r, date_col) for r in rows])

    is_liability = account_type in LIABILITY_TYPES
    skipped_no_date = 0
    skipped_no_amount = 0

    skipped_balance_marker = 0

    for i, row in enumerate(rows):
        txn_date = parse_date(_cell(row, date_col), day_first=day_first)
        if txn_date is None:
            skipped_no_date += 1
            continue

        raw_desc_probe = _cell(row, mapping.get("description"))
        # Brought-forward / carried-forward rows restate the opening or closing
        # BALANCE. Counting one as a transaction adds the entire account balance
        # to the period's spending - the largest single distortion possible.
        if BALANCE_MARKER_ROW.match(raw_desc_probe.strip()):
            skipped_balance_marker += 1
            continue

        amount, direction = _resolve_amount(row, mapping, is_liability)
        if amount is None:
            skipped_no_amount += 1
            continue
        if amount == 0:
            continue  # zero-value rows are statement furniture, not transactions

        raw_desc = raw_desc_probe
        if not raw_desc:
            raw_desc = "(no description)"
        raw_desc = redact_account_numbers(raw_desc)

        # A single-amount-column layout carries no debit/credit position, so
        # `_resolve_amount` has to default to DEBIT. Left uncorrected, every
        # salary credit is booked as spending: measured against real statements
        # this produced money-in of 1,009 against money-out of 1.01 crore.
        if not mapping.split_amount_columns:
            hinted = _direction_from_description(raw_desc, is_liability)
            if hinted is not None:
                direction = hinted

        # Signed: an overdraft or a cumulative outflow is genuinely negative.
        balance = parse_signed_amount(_cell(row, mapping.get("balance")))

        transactions.append(Transaction(
            txn_date=txn_date,
            value_date=parse_date(_cell(row, mapping.get("value_date")), day_first=day_first),
            raw_description=raw_desc,
            normalized_description=normalize_description(raw_desc),
            amount=amount,
            direction=direction,
            balance_after=balance,
            currency=currency,
            reference=_cell(row, mapping.get("reference")) or None,
            source_row=i,
        ))

    # The running balance overrides every heuristic above where it is available.
    corrected = _apply_balance_deltas(transactions, is_liability, opening_balance)
    if corrected:
        warnings.append(
            f"{corrected} transaction direction(s) corrected against the "
            f"statement's own running balance."
        )
    if skipped_balance_marker:
        warnings.append(
            f"{skipped_balance_marker} brought/carried-forward row(s) skipped - "
            f"they restate a balance rather than record a transaction."
        )

    if skipped_no_date:
        warnings.append(
            f"{skipped_no_date} row(s) had no parseable date and were skipped "
            f"(usually page headers, subtotals or footers)."
        )
    if skipped_no_amount:
        warnings.append(
            f"{skipped_no_amount} row(s) had a date but no parseable amount and "
            f"were skipped."
        )
    if not transactions:
        warnings.append("No transactions could be parsed from the selected table.")

    return transactions, warnings


#: Rows that restate a balance rather than record a movement.
BALANCE_MARKER_ROW = re.compile(
    # The abbreviations MUST carry their slash. Making it optional turned every
    # merchant starting "CF" or "BF" into a balance marker - "CF FOODS
    # BANGALORE" was silently dropped as if it were a carried-forward row.
    r"^(?:bal(?:ance)?\s*)?[bc]\s*/\s*f\b"
    r"|^(?:balance\s+)?(?:brought|carried)\s+forward\b"
    r"|^(?:opening|closing|previous)\s+balance\b"
    r"|^balance\s+as\s+on\b"
    # Card statements print their summary figures inside the transaction list.
    # HSBC's "NET OUTSTANDING BALANCE" alone added 4.28 lakh of phantom
    # spending across 11 statements - it is the balance, not a purchase.
    r"|^net\s+outstanding(\s+balance)?\b"
    r"|^(?:total|minimum)\s+amount\s+due\b"
    r"|^total\s+dues?\b|^statement\s+balance\b"
    r"|^(?:sub\s*)?total\b\s*:?\s*$",
    re.IGNORECASE,
)

#: Descriptions that mean money came IN, for layouts with a single amount column.
_CREDIT_WORDS = re.compile(
    r"\bpayment\s+received\b|\breceived\b|\brefund\b|\breversal\b|\bcashback\b"
    r"|\bcredited\b|\bcredit\b|\bsalary\b|\bsal\b|\bdividend\b|\binterest\s+cr"
    r"|\bneft[-\s]*cr\b|\bimps[-\s]*cr\b|\bby\s+transfer\b|\bdeposit\b"
    r"|\bthank\s*you\b|\brepayment\b|\bmat(urity)?\s+proceeds\b"
    # Indian payroll narrations run the tokens together, so \bsal\b never
    # matches: "PRIVATELIMI-JITESHSALNOV25//CMS3". Anchoring on SAL followed by
    # a month abbreviation and a year is specific enough not to catch "SALE".
    r"|SAL(?=(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2})"
    r"|\bSAL[-/]|[-/]SAL\b|\bSALARY\b",
    re.IGNORECASE,
)
#: A credit-card bill being settled. This is the one narration whose meaning
#: flips with the account: on the CARD it is money arriving (the balance owed
#: falls), while the very same wording on the BANK account funding it is money
#: leaving. Read as a debit on both sides, every bill payment was counted twice
#: - once as card "spend" and again as bank "spend".
#:
#: HDFC prints "BPPY CC PAYMENT DP0153...", and the user pays through CRED,
#: which lands on ICICI as "UPI/CRED Club/cred.club@axis/payment on/...".
#: '\bcred\b' cannot match inside "credit" - the word boundary requires a
#: non-word character after the d.
_CARD_BILL_PAYMENT = re.compile(
    r"\bbppy\b|\bcc\s*payment\b|\bcredit\s*card\s*payment\b|\bcard\s*payment\b"
    r"|\bcred\b|\bcred\.club\b|\bbillpay\b|\bpayment\s*[-,]?\s*thank\s*you\b",
    re.IGNORECASE,
)
#: Explicit outgoing markers, which beat a coincidental credit word.
_DEBIT_WORDS = re.compile(
    r"\bpayment\s+(made|to|of)\b|\bpurchase\b|\bwithdrawal\b|\batm\b|\bpos\b"
    r"|\bemi\b|\bcharge[sd]?\b|\bfee\b|\bdebited\b|\bdr\b",
    re.IGNORECASE,
)


def _direction_from_description(description: str, is_liability: bool) -> Direction | None:
    """Infer direction from wording when column position cannot say.

    Only consulted for single-amount-column layouts. Debit wording is checked
    first so "EMI PAYMENT RECEIVED BY BANK" is not read as money coming in.
    Returns None when the wording is genuinely ambiguous, leaving the caller's
    default in place rather than inventing a direction.
    """
    if not description:
        return None
    # Checked first, and the only place is_liability changes the answer: a bill
    # payment moves the balance in opposite directions on the card and on the
    # account paying it.
    if _CARD_BILL_PAYMENT.search(description):
        return Direction.CREDIT if is_liability else Direction.DEBIT
    if _DEBIT_WORDS.search(description) and not _CREDIT_WORDS.search(description):
        return Direction.DEBIT
    if _CREDIT_WORDS.search(description):
        return Direction.CREDIT
    return None


def _apply_balance_deltas(
    transactions: list[Transaction],
    is_liability: bool,
    opening_balance: Decimal | None = None,
) -> int:
    """Correct directions using the running-balance column.

    This is the strongest signal available: the balance moved up or it moved
    down, and the statement itself says by how much. Where a row's amount
    matches the delta, the delta decides the direction outright - no keyword
    heuristic can be as reliable. Rows whose amount does not match the delta are
    left alone, since a mismatch means something else is going on.

    Returns how many directions it corrected.
    """
    corrected = 0
    # Seed with the statement's declared opening balance. Without it the FIRST
    # transaction can never be checked - and on a quiet month with a single
    # transaction that means no check at all. IDFC statements with one ~2.00 row
    # were failing reconciliation by exactly twice that amount (the direction
    # flip signature) purely because there was no previous balance to compare to.
    previous: Decimal | None = opening_balance

    for txn in transactions:
        current = txn.balance_after
        if current is None:
            previous = None
            continue
        if previous is not None:
            delta = current - previous
            if abs(abs(delta) - txn.amount) <= Decimal("0.01") and delta != 0:
                # On a liability, a rising balance means money was spent.
                rising = delta > 0
                wanted = (Direction.DEBIT if rising else Direction.CREDIT) \
                    if is_liability else \
                    (Direction.CREDIT if rising else Direction.DEBIT)
                if txn.direction != wanted:
                    txn.direction = wanted
                    corrected += 1
        previous = current

    return corrected


def _resolve_amount(
    row: list[str],
    mapping: ColumnMapping,
    is_liability: bool,
) -> tuple[Decimal | None, Direction]:
    """Determine a row's amount and direction.

    Precedence, strongest signal first:
      1. separate debit/credit columns - unambiguous
      2. an explicit Cr/Dr annotation inside the amount cell
      3. a type/indicator column
      4. sign of a single amount column

    For liability accounts the columns mean the opposite thing: a "debit" on a
    credit card statement is a purchase, which is money leaving the user. We
    normalize to the user's point of view, so a card purchase is a DEBIT and a
    card payment received is a CREDIT.
    """
    if mapping.split_amount_columns:
        debit = parse_amount(_cell(row, mapping.get("debit"))).value
        credit = parse_amount(_cell(row, mapping.get("credit"))).value

        if debit and credit:
            # Both filled is contradictory; the larger is the real movement.
            if debit >= credit:
                credit = None
            else:
                debit = None

        if debit:
            return debit, Direction.DEBIT
        if credit:
            # On a liability statement, a credit-column entry is a bill payment
            # or refund flowing back to the user's benefit.
            return credit, Direction.CREDIT
        return None, Direction.DEBIT

    amount_col = mapping.get("amount")
    parsed = parse_amount(_cell(row, amount_col))
    if parsed.value is None:
        return None, Direction.DEBIT

    direction = Direction.DEBIT
    if parsed.explicit_direction == "credit":
        direction = Direction.CREDIT
    elif parsed.explicit_direction == "debit":
        direction = Direction.DEBIT
    else:
        type_text = _cell(row, mapping.get("type")).lower()
        if type_text:
            if any(k in type_text for k in ("cr", "credit", "deposit", "in")):
                direction = Direction.CREDIT
            elif any(k in type_text for k in ("dr", "debit", "withdraw", "out")):
                direction = Direction.DEBIT

    if is_liability:
        # A positive amount on a card statement is a charge against the user.
        direction = Direction.DEBIT if direction == Direction.DEBIT else Direction.CREDIT

    return parsed.value, direction


# --------------------------------------------------------------------------
# Post-parse inference
# --------------------------------------------------------------------------

def _drop_rows_after_period(statement: Statement, period_end: date | None) -> None:
    """Remove "transactions" dated after the statement's own closing date.

    A statement cannot contain activity that has not happened yet, so a later
    date means the row came from the summary block rather than the ledger.
    HSBC heads every statement with its payment due date and total amount due:

        08 DEC 2025 6,831.64
        MR JITESH MUKESH AGARWAL

    The extractor reads line one as a dated row with a trailing amount and,
    finding no description, borrows the cardholder's name from the line below -
    inventing a 6,831.64 purchase from the cardholder, two weeks after the
    statement closed. One appeared in every HSBC statement.

    Only the upper bound is enforced. A card's TRANSACTION date legitimately
    precedes the period start when a purchase posts late, so an early row is
    real data; a late one never is.
    """
    if period_end is None or not statement.transactions:
        return
    kept = [t for t in statement.transactions if t.txn_date <= period_end]
    dropped = len(statement.transactions) - len(kept)
    if not dropped:
        return
    statement.transactions = kept
    statement.parse_warnings.append(
        f"Ignored {dropped} row(s) dated after the statement period ended "
        f"({period_end}) - these are summary figures such as the payment due "
        f"date, not transactions."
    )


def _infer_period_from_rows(statement: Statement) -> None:
    """Fill a missing statement period from the transactions themselves."""
    if not statement.transactions:
        return
    dates = [t.txn_date for t in statement.transactions]
    if statement.period_start is None:
        statement.period_start = min(dates)
    if statement.period_end is None:
        statement.period_end = max(dates)


def _infer_balances_from_rows(statement: Statement, account_type: AccountType) -> None:
    """Derive opening/closing balances from the running-balance column.

    Only used when the letterhead didn't declare them. Deriving the opening
    balance from the first row's running balance is safe: opening = balance
    after row 1, minus row 1's own effect.
    """
    txns = statement.transactions
    if not txns:
        return

    if account_type == AccountType.INVESTMENT:
        # An investment statement's trailing numeric column is units or
        # cumulative cost, not a cash balance. Deriving a "balance" from it
        # produces a number that looks authoritative and means nothing.
        statement.parse_warnings.append(
            "Investment statement: no cash balance to reconcile against."
        )
        return

    with_balance = [t for t in txns if t.balance_after is not None]
    if not with_balance:
        return

    is_liability = account_type in LIABILITY_TYPES

    if statement.opening_balance is None:
        first = with_balance[0]
        effect = first.signed_amount
        if is_liability:
            effect = -effect
        statement.opening_balance = first.balance_after - effect
        statement.parse_warnings.append(
            "Opening balance was not stated; derived from the first row's "
            "running balance."
        )

    if statement.closing_balance is None:
        statement.closing_balance = with_balance[-1].balance_after
        if not is_liability:
            statement.extra["derived_current_balance"] = str(statement.closing_balance)
        statement.parse_warnings.append(
            "Closing balance was not stated; taken from the last row's running "
            "balance."
        )
