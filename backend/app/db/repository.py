"""Read/write the domain model to SQLite.

Conversion between Decimal and TEXT happens exclusively here, so no other
module has to remember that money is stored as a string.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Sequence

from ..models.schemas import (Account, AccountType, Category, ConfidenceSource,
                              Direction, ReconciliationResult,
                              ReconciliationStatus, SourceFormat, Statement,
                              Transaction)
from .database import Database

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Conversion helpers
# --------------------------------------------------------------------------

def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _txt(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _d(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _new_id() -> str:
    return str(uuid.uuid4())


def _col(row, name: str, default: Any = None) -> Any:
    """Read a column that a given query may not have selected.

    `sqlite3.Row` raises IndexError rather than returning None for an absent
    key. Every caller here selects `*` against a migrated schema, so the
    columns are present in practice - this keeps a partial SELECT, or a
    database opened before its migration ran, from raising instead of simply
    falling back to the field default.
    """
    try:
        value = row[name]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------

def upsert_account(db: Database, account: Account) -> str:
    """Insert the account, or return the id of the matching existing one.

    Identity is the masked account number plus the account type, falling back to
    institution + type when no number could be read. Re-uploading next month's
    statement for the same card must attach to the same account, not create a
    second one - otherwise transfer detection stops working, the ledger holds
    two copies of every shared transaction, and the totals double.
    """
    with db.connection() as conn:
        # Match on the masked number first. It is the strongest identifier, and
        # requiring the institution to agree as well would create a duplicate
        # account whenever one statement's letterhead names the bank and
        # another's doesn't - which silently doubles every figure. Mirrors
        # graph.nodes._account_identity; the two must stay in step.
        row = None
        if account.account_number_masked:
            row = conn.execute(
                """SELECT id, institution FROM accounts
                   WHERE account_number_masked = ? AND account_type = ?""",
                (account.account_number_masked, account.account_type.value),
            ).fetchone()
        # No masked number, but the card's own product name is known: HSBC
        # masks its number so completely no digit survives extraction, so
        # without this a second, entirely different HSBC card would match the
        # fully-blank fallback below and silently merge into the first one.
        if row is None and not account.account_number_masked and account.product_name:
            row = conn.execute(
                """SELECT id, institution FROM accounts
                   WHERE institution = ? AND account_type = ? AND account_number_masked = ''
                     AND product_name = ?""",
                (account.institution, account.account_type.value, account.product_name),
            ).fetchone()
        # Fully-blank fallback - mirrors graph.nodes._account_identity exactly:
        # only reached when the incoming account has NEITHER a masked number
        # NOR a product name, so it can never merge into (or steal) a row
        # that some other statement was able to name more specifically.
        if row is None and not account.account_number_masked and not account.product_name:
            row = conn.execute(
                """SELECT id, institution FROM accounts
                   WHERE institution = ? AND account_type = ? AND account_number_masked = ''
                     AND (product_name IS NULL OR product_name = '')""",
                (account.institution, account.account_type.value),
            ).fetchone()

        if row:
            account_id = row["id"]
            # Later statements carry fresher loan/card figures, and a named
            # institution should replace a stored "Unknown".
            better_institution = (
                account.institution
                if row["institution"] == "Unknown" and account.institution != "Unknown"
                else None
            )
            conn.execute(
                """UPDATE accounts SET
                       principal_outstanding = COALESCE(?, principal_outstanding),
                       current_balance       = COALESCE(?, current_balance),
                       interest_rate         = COALESCE(?, interest_rate),
                       emi_amount            = COALESCE(?, emi_amount),
                       credit_limit          = COALESCE(?, credit_limit),
                       holder_name           = COALESCE(?, holder_name),
                       product_name          = CASE WHEN product_name IS NULL OR product_name = ''
                                                     THEN ? ELSE product_name END,
                       institution           = COALESCE(?, institution)
                   WHERE id = ?""",
                (_txt(account.principal_outstanding), _txt(account.current_balance),
                 _txt(account.interest_rate), _txt(account.emi_amount),
                 _txt(account.credit_limit), account.holder_name,
                 account.product_name or "", better_institution, account_id),
            )
            account.id = account_id
            return account_id

        account_id = account.id or _new_id()
        conn.execute(
            """INSERT INTO accounts
               (id, institution, account_type, account_number_masked, product_name,
                holder_name, currency, current_balance, principal_outstanding,
                interest_rate, emi_amount, tenure_months_remaining, credit_limit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            # An empty string, never NULL, for the same reason
            # account_number_masked is never NULL: SQLite's UNIQUE constraint
            # treats every NULL as distinct from every other NULL, so two
            # genuinely-unidentified accounts of the same institution+type
            # would both "uniquely" insert instead of being caught as
            # duplicates. Matching WHERE clauses account for legacy NULL rows
            # from before this column existed.
            (account_id, account.institution, account.account_type.value,
             account.account_number_masked, account.product_name or "",
             account.holder_name, account.currency, _txt(account.current_balance),
             _txt(account.principal_outstanding), _txt(account.interest_rate),
             _txt(account.emi_amount), account.tenure_months_remaining,
             _txt(account.credit_limit)),
        )
        account.id = account_id
        return account_id


def get_accounts(db: Database) -> list[Account]:
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY account_type, institution").fetchall()
    return [_row_to_account(r) for r in rows]


def _row_to_account(row) -> Account:
    return Account(
        id=row["id"],
        institution=row["institution"],
        account_type=AccountType(row["account_type"]),
        account_number_masked=row["account_number_masked"],
        product_name=row["product_name"] if "product_name" in row.keys() else None,
        holder_name=row["holder_name"],
        currency=row["currency"],
        current_balance=_dec(row["current_balance"]),
        principal_outstanding=_dec(row["principal_outstanding"]),
        interest_rate=_dec(row["interest_rate"]),
        emi_amount=_dec(row["emi_amount"]),
        tenure_months_remaining=row["tenure_months_remaining"],
        credit_limit=_dec(row["credit_limit"]),
    )


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------

def statement_exists(db: Database, file_hash: str) -> str | None:
    """Return the existing statement id for this file content, if any."""
    if not file_hash:
        return None
    with db.connection() as conn:
        row = conn.execute(
            "SELECT id FROM statements WHERE file_hash = ?", (file_hash,)
        ).fetchone()
    return row["id"] if row else None


def save_statement(
    db: Database,
    statement: Statement,
    account_id: str,
    reconciliation: ReconciliationResult | None = None,
) -> str:
    statement_id = statement.id or _new_id()
    statement.id = statement_id
    recon = reconciliation or statement.reconciliation

    with db.connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO statements
               (id, account_id, source_filename, source_format, file_hash,
                period_start, period_end, opening_balance, closing_balance,
                extractor_used, recon_status, recon_discrepancy, recon_message,
                parse_warnings, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (statement_id, account_id, statement.source_filename,
             statement.source_format.value, statement.file_hash,
             statement.period_start.isoformat() if statement.period_start else None,
             statement.period_end.isoformat() if statement.period_end else None,
             _txt(statement.opening_balance), _txt(statement.closing_balance),
             statement.extractor_used,
             recon.status.value if recon else "not_applicable",
             _txt(recon.discrepancy) if recon else None,
             recon.message if recon else "",
             json.dumps(statement.parse_warnings),
             statement.ingested_at.isoformat()),
        )
    return statement_id


def get_statements(db: Database) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            """SELECT s.*, a.institution, a.account_type, a.account_number_masked
               FROM statements s LEFT JOIN accounts a ON a.id = s.account_id
               ORDER BY s.ingested_at DESC"""
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["parse_warnings"] = json.loads(d.get("parse_warnings") or "[]")
        out.append(d)
    return out


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------

_TXN_COLUMNS = (
    "id, account_id, statement_id, txn_date, value_date, raw_description, "
    "normalized_description, merchant, amount, direction, balance_after, currency, "
    "category, category_source, category_confidence, is_internal_transfer, "
    "is_mirror_leg, transfer_pair_id, recurring_series_id, reference, source_row, "
    "fingerprint, accounting_month, needs_review, review_reason, flow_role, "
    "excluded, note"
)


def save_transactions(db: Database, transactions: Sequence[Transaction]) -> int:
    rows = []
    for txn in transactions:
        txn.id = txn.id or _new_id()
        rows.append((
            txn.id, txn.account_id, txn.statement_id,
            txn.txn_date.isoformat(),
            txn.value_date.isoformat() if txn.value_date else None,
            txn.raw_description, txn.normalized_description, txn.merchant,
            _txt(txn.amount), txn.direction.value, _txt(txn.balance_after),
            txn.currency, txn.category, txn.category_source.value,
            txn.category_confidence, int(txn.is_internal_transfer),
            int(txn.is_mirror_leg),
            txn.transfer_pair_id, txn.recurring_series_id, txn.reference,
            txn.source_row,
            txn.fingerprint, txn.accounting_month, int(txn.needs_review),
            txn.review_reason, txn.flow_role, int(txn.excluded), txn.note,
        ))

    placeholders = ",".join(["?"] * len(_TXN_COLUMNS.split(",")))
    with db.connection() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO transactions ({_TXN_COLUMNS}) VALUES ({placeholders})",
            rows,
        )
    return len(rows)


def update_transaction_categories(db: Database, transactions: Iterable[Transaction]) -> int:
    """Persist enrichment results without rewriting the whole row.

    Carries `is_mirror_leg` and the user-authored columns as well as the
    category. Leaving the mirror flag out of this write is what made a
    dashboard rebuilt after a restart disagree with the one computed at
    ingestion: analytics counts exactly one leg of a transfer as real cash,
    and every reloaded row claimed to be the leg that counts.
    """
    rows = [
        (t.category, t.category_source.value, t.category_confidence,
         int(t.is_internal_transfer), int(t.is_mirror_leg), t.transfer_pair_id,
         t.merchant, t.fingerprint, t.accounting_month, int(t.needs_review),
         t.review_reason, t.flow_role, int(t.excluded), t.note, t.id)
        for t in transactions if t.id
    ]
    with db.connection() as conn:
        conn.executemany(
            """UPDATE transactions
                  SET category = ?, category_source = ?, category_confidence = ?,
                      is_internal_transfer = ?, is_mirror_leg = ?,
                      transfer_pair_id = ?, merchant = ?, fingerprint = ?,
                      accounting_month = ?, needs_review = ?, review_reason = ?,
                      flow_role = ?, excluded = ?, note = ?
                WHERE id = ?""",
            rows,
        )
    return len(rows)


#: Columns a caller may sort transactions by, as the SQL fragment to order on.
#: An explicit allowlist, since this is interpolated into SQL rather than
#: bound as a parameter. Money is stored as TEXT (never REAL - see module
#: docstring), so ordering by the raw column sorts lexicographically: "500"
#: comes before "50" comes before "150". CAST to REAL for ordering only; the
#: stored value and every arithmetic use of it are untouched.
_SORTABLE_COLUMNS = {
    "date": "txn_date",
    "amount": "CAST(amount AS REAL)",
    "balance": "CAST(balance_after AS REAL)",
}


def _transaction_filters(
    account_id: str | Sequence[str] | None,
    start: date | None,
    end: date | None,
    category: str | Sequence[str] | None,
    statement_id: str | None,
    rail: str | None,
) -> tuple[list[str], list[Any]]:
    """Shared WHERE-clause builder, so a filtered count matches its list.

    `account_id` and `category` each accept one value or several - the
    dashboard's "select card or account, multiple or single" filter needs an
    IN clause, and a bare `= ?` would silently drop every account after the
    first one selected.
    """
    clauses: list[str] = []
    params: list[Any] = []

    def _in_or_eq(column: str, value: str | Sequence[str] | None) -> None:
        if not value:
            return
        values = [value] if isinstance(value, str) else [v for v in value if v]
        if not values:
            return
        if len(values) == 1:
            clauses.append(f"{column} = ?")
            params.append(values[0])
        else:
            clauses.append(f"{column} IN ({', '.join('?' * len(values))})")
            params.extend(values)

    _in_or_eq("account_id", account_id)
    _in_or_eq("category", category)
    if start:
        clauses.append("txn_date >= ?")
        params.append(start.isoformat())
    if end:
        clauses.append("txn_date <= ?")
        params.append(end.isoformat())
    if statement_id:
        clauses.append("statement_id = ?")
        params.append(statement_id)
    if rail == "upi":
        # UPI narrations always open with the rail name, on every issuer this
        # app has seen - ICICI's "UPI/...", Yes Bank's "UPI_...", and the
        # occasional lowercase "upi-...". Parenthesized deliberately: joined
        # with the other clauses via AND, an unparenthesized OR binds looser
        # than AND in SQL, so "account_id IN (...) AND ... LIKE 'UPI%' OR ...
        # LIKE 'upi%'" parses as "(account_id... AND UPI%) OR upi%" - silently
        # dropping the account filter for every lowercase-prefixed row. That
        # is exactly how the UPI-only view ended up showing MORE rows than
        # the unfiltered "All" view: it was pulling upi-prefixed rows from
        # every account in the system, not just the selected ones.
        clauses.append("(raw_description LIKE 'UPI%' OR raw_description LIKE 'upi%')")
    elif rail == "non_upi":
        clauses.append("(raw_description NOT LIKE 'UPI%' AND raw_description NOT LIKE 'upi%')")

    return clauses, params


def get_transactions(
    db: Database,
    account_id: str | Sequence[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    category: str | Sequence[str] | None = None,
    statement_id: str | None = None,
    rail: str | None = None,
    sort_by: str = "date",
    sort_dir: str = "asc",
    limit: int | None = None,
    offset: int = 0,
) -> list[Transaction]:
    clauses, params = _transaction_filters(
        account_id, start, end, category, statement_id, rail)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    column = _SORTABLE_COLUMNS.get(sort_by, "txn_date")
    direction = "DESC" if str(sort_dir).lower().startswith("desc") else "ASC"
    # A secondary key keeps same-day (or same-amount) rows in a stable,
    # reproducible order instead of whatever SQLite's scan happens to return.
    sql = f"SELECT * FROM transactions {where} ORDER BY {column} {direction}, id"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, limit, offset]

    with db.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_transaction(r) for r in rows]


def count_transactions(
    db: Database,
    account_id: str | Sequence[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    category: str | Sequence[str] | None = None,
    statement_id: str | None = None,
    rail: str | None = None,
) -> int:
    clauses, params = _transaction_filters(
        account_id, start, end, category, statement_id, rail)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connection() as conn:
        return conn.execute(
            f"SELECT COUNT(*) c FROM transactions {where}", params).fetchone()["c"]


def _row_to_transaction(row) -> Transaction:
    return Transaction(
        id=row["id"],
        account_id=row["account_id"],
        statement_id=row["statement_id"],
        txn_date=_d(row["txn_date"]),
        value_date=_d(row["value_date"]),
        raw_description=row["raw_description"],
        normalized_description=row["normalized_description"] or "",
        merchant=row["merchant"],
        amount=_dec(row["amount"]) or Decimal("0"),
        direction=Direction(row["direction"]),
        balance_after=_dec(row["balance_after"]),
        currency=row["currency"],
        category=row["category"],
        category_source=ConfidenceSource(row["category_source"]),
        category_confidence=row["category_confidence"],
        is_internal_transfer=bool(row["is_internal_transfer"]),
        is_mirror_leg=bool(_col(row, "is_mirror_leg")),
        transfer_pair_id=row["transfer_pair_id"],
        recurring_series_id=row["recurring_series_id"],
        reference=row["reference"],
        source_row=row["source_row"],
        fingerprint=_col(row, "fingerprint") or "",
        accounting_month=_col(row, "accounting_month") or "",
        needs_review=bool(_col(row, "needs_review")),
        review_reason=_col(row, "review_reason") or "",
        flow_role=_col(row, "flow_role") or "",
        excluded=bool(_col(row, "excluded")),
        note=_col(row, "note") or "",
    )


# --------------------------------------------------------------------------
# Merchant category cache
# --------------------------------------------------------------------------

def lookup_merchants(db: Database, keys: Sequence[str]) -> dict[str, tuple[Category, float, str]]:
    """Bulk-load cached merchant categories.

    Bulk rather than per-row: categorizing 20,000 transactions with one query
    per merchant is the difference between milliseconds and minutes.
    """
    if not keys:
        return {}
    unique = list({k for k in keys if k})
    out: dict[str, tuple[Category, float, str]] = {}
    with db.connection() as conn:
        for i in range(0, len(unique), 500):  # stay under SQLite's variable limit
            chunk = unique[i:i + 500]
            placeholders = ",".join(["?"] * len(chunk))
            rows = conn.execute(
                f"SELECT * FROM merchant_categories WHERE merchant_key IN ({placeholders})",
                chunk,
            ).fetchall()
            for r in rows:
                out[r["merchant_key"]] = (
                    r["category"], r["confidence"], r["source"]
                )
        return out


def save_merchant_categories(
    db: Database,
    mapping: dict[str, tuple[Category, float, str]],
) -> int:
    """Write learned merchant -> category decisions.

    A USER source always wins and is never overwritten by a later model guess;
    that is the whole point of letting someone correct a category.
    """
    if not mapping:
        return 0
    rows = [(k, c.value, src, conf) for k, (c, conf, src) in mapping.items()]
    with db.connection() as conn:
        conn.executemany(
            """INSERT INTO merchant_categories
                   (merchant_key, category, source, confidence, hit_count, updated_at)
               VALUES (?,?,?,?,1,datetime('now'))
               ON CONFLICT(merchant_key) DO UPDATE SET
                   category   = CASE WHEN merchant_categories.source = 'user'
                                     THEN merchant_categories.category
                                     ELSE excluded.category END,
                   source     = CASE WHEN merchant_categories.source = 'user'
                                     THEN 'user' ELSE excluded.source END,
                   confidence = CASE WHEN merchant_categories.source = 'user'
                                     THEN merchant_categories.confidence
                                     ELSE excluded.confidence END,
                   hit_count  = merchant_categories.hit_count + 1,
                   updated_at = datetime('now')""",
            rows,
        )
    return len(rows)


# --------------------------------------------------------------------------
# Transfer pairs & recurring series
# --------------------------------------------------------------------------

def save_transfer_pairs(db: Database, pairs: Sequence[Any]) -> int:
    """Replace the transfer-pairs table with the given set.

    `pair_id` is a fresh uuid4 every time `detect_transfers` runs (see
    reconcile/transfers.py), so the SAME logical pair gets a different id on
    every call - an INSERT alone would accumulate a duplicate row per retry.
    Every caller always passes the COMPLETE set of pairs for its transaction
    list (never a partial delta), so clearing first is correct, not lossy.
    """
    rows = [(p.pair_id, p.debit_txn_id, p.credit_txn_id, _txt(p.amount),
             p.day_gap, p.kind, p.confidence) for p in pairs]
    with db.connection() as conn:
        conn.execute("DELETE FROM transfer_pairs")
        conn.executemany(
            """INSERT OR REPLACE INTO transfer_pairs
               (pair_id, debit_txn_id, credit_txn_id, amount, day_gap, kind, confidence)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )
    return len(rows)


# --------------------------------------------------------------------------
# Source files - every file ever attempted, whatever the outcome
# --------------------------------------------------------------------------

class SourceFileRecord:
    """One row of the source_files table. Not a pydantic model - this is a
    pure bookkeeping record, never sent through the analysis pipeline."""

    def __init__(
        self, id: str, filename: str, filepath: str = "", file_hash: str = "",
        source: str = "upload", sender: str = "", message_id: str = "",
        size_bytes: int | None = None, password: str | None = None,
        password_status: str = "unknown", parse_status: str = "pending",
        institution_guess: str = "", account_type_guess: str = "",
        account_id: str | None = None, statement_id: str | None = None,
        transaction_count: int = 0, error_message: str = "",
        period_hint: str | None = None,
        first_seen_at: str | None = None, last_attempted_at: str | None = None,
    ) -> None:
        self.id = id
        self.filename = filename
        self.filepath = filepath
        self.file_hash = file_hash
        self.source = source
        self.sender = sender
        self.message_id = message_id
        self.size_bytes = size_bytes
        self.password = password
        self.password_status = password_status
        self.parse_status = parse_status
        self.institution_guess = institution_guess
        self.account_type_guess = account_type_guess
        self.account_id = account_id
        self.statement_id = statement_id
        self.transaction_count = transaction_count
        self.error_message = error_message
        self.period_hint = period_hint
        self.first_seen_at = first_seen_at
        self.last_attempted_at = last_attempted_at


def _row_to_source_file(row) -> SourceFileRecord:
    return SourceFileRecord(
        id=row["id"], filename=row["filename"], filepath=row["filepath"],
        file_hash=row["file_hash"], source=row["source"], sender=row["sender"],
        message_id=row["message_id"], size_bytes=row["size_bytes"],
        password=row["password"], password_status=row["password_status"],
        parse_status=row["parse_status"], institution_guess=row["institution_guess"],
        account_type_guess=row["account_type_guess"], account_id=row["account_id"],
        statement_id=row["statement_id"], transaction_count=row["transaction_count"],
        error_message=row["error_message"], period_hint=row["period_hint"],
        first_seen_at=row["first_seen_at"],
        last_attempted_at=row["last_attempted_at"],
    )


def upsert_source_file(db: Database, record: SourceFileRecord) -> str:
    """Insert or update a file's record, keyed by content hash when known.

    Keying on the hash (not the filename) is what makes the password cache
    work across a re-download under a different name - Gmail's attachment
    names are not stable, but the file's bytes are.
    """
    with db.connection() as conn:
        existing = None
        if record.file_hash:
            existing = conn.execute(
                "SELECT id FROM source_files WHERE file_hash = ?", (record.file_hash,)
            ).fetchone()
        record_id = existing["id"] if existing else (record.id or _new_id())
        conn.execute(
            """INSERT INTO source_files
               (id, filename, filepath, file_hash, source, sender, message_id,
                size_bytes, password, password_status, parse_status,
                institution_guess, account_type_guess, account_id, statement_id,
                transaction_count, error_message, period_hint, last_attempted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                 filename=excluded.filename, filepath=excluded.filepath,
                 file_hash=excluded.file_hash, source=excluded.source,
                 sender=excluded.sender, message_id=excluded.message_id,
                 size_bytes=excluded.size_bytes,
                 -- A later attempt with no working password, or no resolved
                 -- period, must not erase one found on an earlier attempt -
                 -- only a non-null value overwrites.
                 password=COALESCE(excluded.password, source_files.password),
                 password_status=excluded.password_status,
                 parse_status=excluded.parse_status,
                 institution_guess=excluded.institution_guess,
                 account_type_guess=excluded.account_type_guess,
                 account_id=excluded.account_id, statement_id=excluded.statement_id,
                 transaction_count=excluded.transaction_count,
                 error_message=excluded.error_message,
                 period_hint=COALESCE(excluded.period_hint, source_files.period_hint),
                 last_attempted_at=excluded.last_attempted_at""",
            (record_id, record.filename, record.filepath, record.file_hash,
             record.source, record.sender, record.message_id, record.size_bytes,
             record.password, record.password_status, record.parse_status,
             record.institution_guess, record.account_type_guess,
             record.account_id, record.statement_id, record.transaction_count,
             record.error_message, record.period_hint),
        )
    return record_id


def list_source_files(db: Database) -> list[SourceFileRecord]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM source_files ORDER BY last_attempted_at DESC"
        ).fetchall()
    return [_row_to_source_file(r) for r in rows]


def get_source_file(db: Database, file_id: str) -> SourceFileRecord | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM source_files WHERE id = ?", (file_id,)
        ).fetchone()
    return _row_to_source_file(row) if row else None


def backfill_source_file_account_ids(db: Database) -> int:
    """Fill in account_id for file records saved before their statement's
    account identity was resolved to a real database id.

    A file's account is only known for certain once `_persist` has mapped the
    graph's throwaway account id onto an existing-or-new database row - which
    happens after the per-file registry entry was written. Rather than thread
    that mapping back through the ingestion pipeline, this reads it straight
    off the statement the file already points to.
    """
    with db.connection() as conn:
        cur = conn.execute(
            """UPDATE source_files
                  SET account_id = (
                      SELECT account_id FROM statements
                       WHERE statements.id = source_files.statement_id
                  )
                WHERE statement_id IS NOT NULL
                  AND (account_id IS NULL OR account_id = '')"""
        )
        return cur.rowcount


def get_cached_password(db: Database, file_hash_value: str) -> str | None:
    """The password that opened this exact file content before, if any."""
    if not file_hash_value:
        return None
    with db.connection() as conn:
        row = conn.execute(
            "SELECT password FROM source_files WHERE file_hash = ? AND password IS NOT NULL",
            (file_hash_value,),
        ).fetchone()
    return row["password"] if row else None


def get_profile(db: Database):
    """Load the single user profile, or an empty one."""
    from ..models.profile import UserProfile

    with db.connection() as conn:
        row = conn.execute("SELECT * FROM user_profile WHERE id = 'me'").fetchone()
    if row is None:
        return UserProfile()
    return UserProfile(
        full_name=row["full_name"] or "",
        date_of_birth=_d(row["date_of_birth"]),
        pan=row["pan"] or "",
        mobile=row["mobile"] or "",
        custom_passwords=json.loads(row["custom_passwords"] or "[]"),
        excluded_senders=json.loads(
            (row["excluded_senders"] if "excluded_senders" in row.keys() else None) or "[]"
        ),
    )


def save_profile(db: Database, profile) -> None:
    with db.connection() as conn:
        conn.execute(
            """INSERT INTO user_profile
                   (id, full_name, date_of_birth, pan, mobile, custom_passwords,
                    excluded_senders, updated_at)
               VALUES ('me', ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                   full_name = excluded.full_name,
                   date_of_birth = excluded.date_of_birth,
                   pan = excluded.pan,
                   mobile = excluded.mobile,
                   custom_passwords = excluded.custom_passwords,
                   excluded_senders = excluded.excluded_senders,
                   updated_at = datetime('now')""",
            (profile.full_name,
             profile.date_of_birth.isoformat() if profile.date_of_birth else None,
             profile.pan, profile.mobile,
             json.dumps(profile.custom_passwords or []),
             json.dumps(profile.excluded_senders or [])),
        )


def save_recurring_series(db: Database, series: Sequence[Any]) -> int:
    with db.connection() as conn:
        overrides = {r["series_id"]: r for r in conn.execute("SELECT * FROM recurring_series_overrides").fetchall()}
        rows = []
        for s in series:
            o = overrides.get(s.id)
            if o and o["deleted"]: continue
            label = o["label"] if o and o["label"] is not None else s.label
            category = o["category"] if o and o["category"] is not None else s.category
            is_active = o["is_active"] if o and o["is_active"] is not None else int(s.is_active)
            rows.append((s.id, s.account_id, label, category, s.direction.value, _txt(s.median_amount), s.cadence_days, s.occurrences, s.first_seen.isoformat() if s.first_seen else None, s.last_seen.isoformat() if s.last_seen else None, s.next_expected.isoformat() if s.next_expected else None, is_active, s.confidence))
        conn.execute("DELETE FROM recurring_series")
        conn.executemany("INSERT INTO recurring_series (id, account_id, label, category, direction, median_amount, cadence_days, occurrences, first_seen, last_seen, next_expected, is_active, confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


# --------------------------------------------------------------------------
# User overrides - tier 0
#
# Everything a human decided about a transaction. Keyed by content
# fingerprint rather than transaction id, because re-parsing a statement
# mints fresh uuids and would orphan every decision. Deliberately NOT
# cleared by Database.clear("parsed_data"): the whole point is that
# re-processing the ledger leaves the user's own judgements intact.
# --------------------------------------------------------------------------

#: The fields a user can override. Each is nullable and applied only when
#: set, so recording a note does not silently pin a category as a side effect.
OVERRIDE_FIELDS = ("category", "flow_role", "accounting_month", "note", "excluded")


@dataclass
class OverrideRecord:
    fingerprint: str
    account_key: str = ""
    txn_date: str = ""
    amount: str = ""
    direction: str = ""
    desc_hash: str = ""
    category: str | None = None
    flow_role: str | None = None
    accounting_month: str | None = None
    note: str | None = None
    excluded: bool | None = None
    updated_at: str = ""

    def has_any(self) -> bool:
        return any(getattr(self, f) is not None for f in OVERRIDE_FIELDS)


def _row_to_override(row) -> OverrideRecord:
    excluded = _col(row, "excluded")
    return OverrideRecord(
        fingerprint=row["fingerprint"],
        account_key=_col(row, "account_key") or "",
        txn_date=_col(row, "txn_date") or "",
        amount=_col(row, "amount") or "",
        direction=_col(row, "direction") or "",
        desc_hash=_col(row, "desc_hash") or "",
        category=_col(row, "category"),
        flow_role=_col(row, "flow_role"),
        accounting_month=_col(row, "accounting_month"),
        note=_col(row, "note"),
        excluded=None if excluded is None else bool(excluded),
        updated_at=_col(row, "updated_at") or "",
    )


def save_override(db: Database, record: OverrideRecord) -> None:
    """Insert or update one decision.

    Only the fields actually supplied are written; the rest keep whatever the
    user set previously. Passing an explicit value is the only way to change
    a field, so adding a note never disturbs an earlier recategorization.
    """
    sets, params = [], []
    for field in OVERRIDE_FIELDS:
        value = getattr(record, field)
        if value is None:
            continue
        sets.append(f"{field} = ?")
        params.append(int(value) if field == "excluded" else value)

    with db.connection() as conn:
        conn.execute(
            """INSERT INTO user_overrides
                   (fingerprint, account_key, txn_date, amount, direction, desc_hash)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(fingerprint) DO UPDATE SET
                   account_key = excluded.account_key,
                   txn_date    = excluded.txn_date,
                   amount      = excluded.amount,
                   direction   = excluded.direction,
                   desc_hash   = excluded.desc_hash""",
            (record.fingerprint, record.account_key, record.txn_date,
             record.amount, record.direction, record.desc_hash),
        )
        if sets:
            conn.execute(
                f"UPDATE user_overrides SET {', '.join(sets)}, "
                f"updated_at = datetime('now') WHERE fingerprint = ?",
                (*params, record.fingerprint),
            )


def clear_override_field(db: Database, fingerprint: str, field: str) -> None:
    """Unset one field, letting the automatic value take over again.

    Distinct from setting it to a falsy value: NULL means "no opinion", which
    is not the same as an explicit `excluded = false`.
    """
    if field not in OVERRIDE_FIELDS:
        raise ValueError(f"{field!r} is not an overridable field")
    with db.connection() as conn:
        conn.execute(
            f"UPDATE user_overrides SET {field} = NULL, updated_at = datetime('now') "
            f"WHERE fingerprint = ?", (fingerprint,))
        # A row that no longer carries any opinion is noise; drop it so the
        # "N decisions" count the UI shows stays truthful.
        conn.execute(
            "DELETE FROM user_overrides WHERE fingerprint = ? AND "
            + " AND ".join(f"{f} IS NULL" for f in OVERRIDE_FIELDS),
            (fingerprint,))


def get_overrides(db: Database) -> list[OverrideRecord]:
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM user_overrides").fetchall()
    return [_row_to_override(r) for r in rows]


def count_overrides(db: Database) -> int:
    with db.connection() as conn:
        return conn.execute("SELECT COUNT(*) c FROM user_overrides").fetchone()["c"]


def repoint_override(db: Database, old_fingerprint: str, new_fingerprint: str,
                     account_key: str) -> None:
    """Move a decision onto the fingerprint its transaction now has.

    Called when a decision was found by the loose key because its strict
    fingerprint had shifted - an account being re-identified is the usual
    cause, and is nobody's mistake. Without this the same recovery would have
    to happen on every subsequent run.
    """
    if old_fingerprint == new_fingerprint:
        return
    with db.connection() as conn:
        # The destination may already exist if two rows collapsed onto one
        # identity. The existing decision there is the more recent statement
        # of intent, so it wins and the stale row is simply dropped.
        exists = conn.execute(
            "SELECT 1 FROM user_overrides WHERE fingerprint = ?",
            (new_fingerprint,)).fetchone()
        if exists:
            conn.execute("DELETE FROM user_overrides WHERE fingerprint = ?",
                         (old_fingerprint,))
            return
        conn.execute(
            "UPDATE user_overrides SET fingerprint = ?, account_key = ? "
            "WHERE fingerprint = ?",
            (new_fingerprint, account_key, old_fingerprint))


# --------------------------------------------------------------------------
# Analysis runs
#
# The completed dashboard payload, stored so it survives a restart intact.
# Without it a restarted server had to recompute the whole dashboard from the
# stored rows, and that path is lossy: it produces no narrative, no transfer
# report, and (before is_mirror_leg was persisted) different totals.
# --------------------------------------------------------------------------

def save_analysis_run(db: Database, run_id: str, status: str, file_count: int,
                      payload: dict | None = None, error: str = "") -> None:
    with db.connection() as conn:
        conn.execute(
            """INSERT INTO analysis_runs (id, status, file_count, summary_json, error)
               VALUES (?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   status       = excluded.status,
                   file_count   = excluded.file_count,
                   summary_json = COALESCE(excluded.summary_json, analysis_runs.summary_json),
                   error        = excluded.error""",
            (run_id, status, file_count,
             json.dumps(payload, default=str) if payload is not None else None,
             error),
        )


def get_latest_analysis_run(db: Database) -> tuple[str, dict] | None:
    """The most recent completed run and its payload, or None."""
    with db.connection() as conn:
        row = conn.execute(
            """SELECT id, summary_json FROM analysis_runs
                WHERE status = 'complete' AND summary_json IS NOT NULL
                ORDER BY created_at DESC, rowid DESC LIMIT 1"""
        ).fetchone()
    if not row:
        return None
    try:
        return row["id"], json.loads(row["summary_json"])
    except (ValueError, TypeError):
        # A payload we cannot read is not worth crashing the dashboard over;
        # the caller falls back to recomputing from the stored rows.
        log.warning("stored analysis run %s has an unreadable payload", row["id"])
        return None


def prune_analysis_runs(db: Database, keep: int = 20) -> int:
    """Keep the newest `keep` runs. Each payload is a full dashboard."""
    with db.connection() as conn:
        cur = conn.execute(
            """DELETE FROM analysis_runs WHERE id NOT IN (
                   SELECT id FROM analysis_runs
                    ORDER BY created_at DESC, rowid DESC LIMIT ?)""",
            (keep,),
        )
        return cur.rowcount


from dataclasses import dataclass
from decimal import Decimal
from datetime import date

@dataclass
class TransactionSplit:
    id: str
    parent_fingerprint: str
    amount: Decimal
    category: str | None
    flow_role: str | None
    note: str
    position: int

def save_splits(db, txn_fingerprint: str, parts: list) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM transaction_splits WHERE parent_fingerprint = ?", (txn_fingerprint,))
        rows = [(p.id, p.parent_fingerprint, _txt(p.amount), p.category, p.flow_role, p.note, p.position) for p in parts]
        conn.executemany("INSERT INTO transaction_splits VALUES (?,?,?,?,?,?,?)", rows)

@dataclass
class Claim:
    id: str
    direction: str
    counterparty: str
    origin_fingerprint: str
    amount: Decimal
    settled_amount: Decimal
    status: str
    basis: str
    opened_on: date
    closed_on: date | None = None
    note: str = ""

@dataclass
class ClaimSettlement:
    id: str
    claim_id: str
    method: str
    amount: Decimal
    settled_on: date
    txn_fingerprint: str
    note: str = ""

def save_claim(db, claim) -> None:
    with db.connection() as conn:
        conn.execute("INSERT OR REPLACE INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            claim.id, claim.direction, claim.counterparty, claim.origin_fingerprint,
            _txt(claim.amount), _txt(claim.settled_amount), claim.status, claim.basis,
            claim.opened_on.isoformat(), claim.closed_on.isoformat() if claim.closed_on else None, claim.note
        ))

def get_claims(db) -> list:
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM claims").fetchall()
    out = []
    for r in rows:
        c = Claim(
            id=r["id"], direction=r["direction"], counterparty=r["counterparty"],
            origin_fingerprint=r["origin_fingerprint"], amount=_dec(r["amount"]),
            settled_amount=_dec(r["settled_amount"]), status=r["status"], basis=r["basis"],
            opened_on=_d(r["opened_on"]), closed_on=_d(r["closed_on"]), note=r["note"]
        )
        out.append(c)
        return out

def settle_claim(db, claim_id: str, settlement: ClaimSettlement) -> None:
    with db.connection() as conn:
        conn.execute("INSERT INTO claim_settlements VALUES (?,?,?,?,?,?,?)", (
            settlement.id, settlement.claim_id, settlement.method, _txt(settlement.amount),
            settlement.settled_on.isoformat(), settlement.txn_fingerprint, settlement.note
        ))
        claim_row = conn.execute("SELECT amount, settled_amount FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if claim_row:
            new_settled = _dec(claim_row["settled_amount"]) + settlement.amount
            status = "closed" if new_settled >= _dec(claim_row["amount"]) else "open"
            closed_on = settlement.settled_on.isoformat() if status == "closed" else None
            conn.execute("UPDATE claims SET settled_amount = ?, status = ?, closed_on = ? WHERE id = ?", (_txt(new_settled), status, closed_on, claim_id))

def get_confirmed_groups(db) -> list[dict]:
    with db.connection() as conn:
        groups = conn.execute("SELECT * FROM settlement_groups WHERE confirmed = 1").fetchall()
        if not groups: return []
        group_ids = [g["id"] for g in groups]
        placeholders = ",".join(["?"] * len(group_ids))
        legs = conn.execute(f"SELECT * FROM settlement_group_legs WHERE group_id IN ({placeholders})", group_ids).fetchall()
    legs_by_group = {}
    for leg in legs:
        legs_by_group.setdefault(leg["group_id"], []).append({"fingerprint": leg["fingerprint"], "side": leg["side"]})
    out = []
    for g in groups:
        d = dict(g)
        d["total_amount"] = _dec(d["total_amount"])
        d["residual"] = _dec(d["residual"])
        d["legs"] = legs_by_group.get(d["id"], [])
        out.append(d)
    return out

def get_confirmed_fingerprints(db) -> set[str]:
    with db.connection() as conn:
        rows = conn.execute("SELECT l.fingerprint FROM settlement_group_legs l JOIN settlement_groups g ON l.group_id = g.id WHERE g.confirmed = 1").fetchall()
    return {r["fingerprint"] for r in rows}

def confirm_group(db, group_id: str) -> None:
    with db.connection() as conn:
        conn.execute("UPDATE settlement_groups SET confirmed = 1 WHERE id = ?", (group_id,))

def save_settlement_groups(db, groups: list, legs: list) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM settlement_group_legs WHERE group_id IN (SELECT id FROM settlement_groups WHERE confirmed = 0)")
        conn.execute("DELETE FROM settlement_groups WHERE confirmed = 0")
        g_rows = [(g.group_id, g.kind, _txt(g.total_amount), _txt(g.residual), g.confidence, int(g.confirmed)) for g in groups if not g.confirmed]
        if g_rows:
            conn.executemany("INSERT INTO settlement_groups VALUES (?,?,?,?,?,?)", g_rows)
        l_rows = [(l["group_id"], l["fingerprint"], l["side"]) for l in legs]
        if l_rows:
            conn.executemany("INSERT INTO settlement_group_legs VALUES (?,?,?)", l_rows)

def get_custom_categories(db) -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM custom_categories ORDER BY name ASC").fetchall()
    return [dict(r) for r in rows]

def add_custom_category(db, name: str, color: str = "#6b7280", icon: str = "Tag") -> None:
    with db.connection() as conn:
        conn.execute("INSERT OR IGNORE INTO custom_categories (name, color, icon) VALUES (?, ?, ?)", (name.strip().lower(), color, icon))

def delete_custom_category(db, name: str) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM custom_categories WHERE name = ?", (name.strip().lower(),))

def update_recurring_series_override(db, series_id: str, payload: dict) -> None:
    with db.connection() as conn:
        conn.execute("INSERT OR IGNORE INTO recurring_series_overrides (series_id) VALUES (?)", (series_id,))
        updates = []
        params = []
        for k, v in payload.items():
            if k in {"is_active", "label", "category", "deleted"}:
                updates.append(f"{k} = ?")
                params.append(v)
        if updates:
            params.append(series_id)
            conn.execute(f"UPDATE recurring_series_overrides SET {', '.join(updates)} WHERE series_id = ?", params)

import uuid

def save_claim(db, direction: str, counterparty: str, origin_fingerprint: str, amount, opened_on: str) -> str:
    claim_id = str(uuid.uuid4())
    with db.connection() as conn:
        conn.execute("INSERT INTO claims (id, direction, counterparty, origin_fingerprint, amount, settled_amount, status, basis, opened_on, closed_on, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            claim_id, direction, counterparty, origin_fingerprint, _txt(amount), "0", "open", "accrual", opened_on, None, ""
        ))
    return claim_id

def get_claim(db, claim_id: str) -> dict:
    with db.connection() as conn:
        r = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if not r: return None
        d = dict(r)
        d['amount'] = _dec(d['amount'])
        d['settled_amount'] = _dec(d['settled_amount'])
        return d

def settle_claim(db, claim_id: str, method: str, amount, settled_on: str) -> str:
    settlement_id = str(uuid.uuid4())
    with db.connection() as conn:
        conn.execute("INSERT INTO claim_settlements (id, claim_id, method, amount, settled_on, txn_fingerprint, note) VALUES (?,?,?,?,?,?,?)", (
            settlement_id, claim_id, method, _txt(amount), settled_on, "", ""
        ))
        
        claim_row = conn.execute("SELECT amount, settled_amount FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if claim_row:
            new_settled = _dec(claim_row["settled_amount"]) + amount
            status = "closed" if new_settled >= _dec(claim_row["amount"]) else "partial" if new_settled > 0 else "open"
            status = "settled" if status == "closed" else status
            closed_on = settled_on if status == "settled" else None
            conn.execute("UPDATE claims SET settled_amount = ?, status = ?, closed_on = ? WHERE id = ?", (_txt(new_settled), status, closed_on, claim_id))
    return settlement_id

def get_claim_settlements(db, claim_id: str) -> list:
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM claim_settlements WHERE claim_id = ?", (claim_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if 'amount' in d: d['amount'] = _dec(d['amount'])
            if 'settled_amount' in d: d['settled_amount'] = _dec(d['settled_amount'])
            out.append(d)
        return out

def get_claims(db, status: str = None) -> list:
    with db.connection() as conn:
        if status:
            rows = conn.execute("SELECT * FROM claims WHERE status = ?", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM claims").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if 'amount' in d: d['amount'] = _dec(d['amount'])
            if 'settled_amount' in d: d['settled_amount'] = _dec(d['settled_amount'])
            out.append(d)
        return out

def save_splits(db, parent_fingerprint: str, parent_amount, splits: list) -> list:
    total = sum(s["amount"] for s in splits)
    if total != parent_amount:
        raise ValueError(f"Splits sum to {total}, expected {parent_amount}")
        
    ids = []
    with db.connection() as conn:
        conn.execute("DELETE FROM transaction_splits WHERE parent_fingerprint = ?", (parent_fingerprint,))
        rows = []
        for i, s in enumerate(splits):
            split_id = str(uuid.uuid4())
            ids.append(split_id)
            rows.append((split_id, parent_fingerprint, _txt(s["amount"]), s.get("category"), s.get("flow_role"), s.get("note", ""), i))
        conn.executemany("INSERT INTO transaction_splits (id, parent_fingerprint, amount, category, flow_role, note, position) VALUES (?,?,?,?,?,?,?)", rows)
    return ids

def get_splits(db, parent_fingerprint: str) -> list:
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM transaction_splits WHERE parent_fingerprint = ?", (parent_fingerprint,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if 'amount' in d: d['amount'] = _dec(d['amount'])
            if 'settled_amount' in d: d['settled_amount'] = _dec(d['settled_amount'])
            out.append(d)
        return out

def save_settlement_group(db, group_id: str, kind: str, total_amount, residual, confidence: float, note: str, legs: list) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM settlement_group_legs WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM settlement_groups WHERE id = ?", (group_id,))
        
        conn.execute("INSERT INTO settlement_groups (id, kind, total_amount, residual, confidence, note, confirmed) VALUES (?,?,?,?,?,?,1)", (
            group_id, kind, _txt(total_amount), _txt(residual), confidence, note
        ))
        
        l_rows = [(group_id, l[0], l[1]) for l in legs]
        if l_rows:
            conn.executemany("INSERT INTO settlement_group_legs (group_id, fingerprint, side) VALUES (?,?,?)", l_rows)

def get_confirmed_groups(db) -> list:
    with db.connection() as conn:
        groups = conn.execute("SELECT * FROM settlement_groups WHERE confirmed = 1").fetchall()
        if not groups: return []
        group_ids = [g["id"] for g in groups]
        placeholders = ",".join(["?"] * len(group_ids))
        legs = conn.execute(f"SELECT * FROM settlement_group_legs WHERE group_id IN ({placeholders})", group_ids).fetchall()
    
    legs_by_group = {}
    for leg in legs:
        legs_by_group.setdefault(leg["group_id"], []).append(leg["fingerprint"]) # or dict
        
    out = []
    for g in groups:
        d = dict(g)
        d["total_amount"] = _dec(d["total_amount"])
        d["residual"] = _dec(d["residual"])
        d["legs"] = legs_by_group.get(d["id"], [])
        out.append(d)
    return out

def get_confirmed_fingerprints(db) -> set:
    with db.connection() as conn:
        rows = conn.execute("SELECT l.fingerprint FROM settlement_group_legs l JOIN settlement_groups g ON l.group_id = g.id WHERE g.confirmed = 1").fetchall()
    return {r["fingerprint"] for r in rows}

import json

def get_ai_inference(db, fingerprint: str) -> dict | None:
    with db.connection() as conn:
        r = conn.execute("SELECT result_json FROM ai_inferences WHERE fingerprint = ?", (fingerprint,)).fetchone()
        if r:
            try:
                return json.loads(r["result_json"])
            except json.JSONDecodeError:
                pass
        return None

def save_ai_inference(db, fingerprint: str, result: dict) -> None:
    with db.connection() as conn:
        conn.execute("INSERT OR REPLACE INTO ai_inferences (fingerprint, result_json) VALUES (?, ?)", (fingerprint, json.dumps(result)))


def get_statement_periods_by_account(db: Database) -> dict[str, list[Any]]:
    """Each account's statement periods, keyed by account id.

    Just the period bounds, not full Statement objects - both callers only
    need to answer "is there a parsed statement covering this date?".

    Used by the coverage grid and, more importantly, by settlement matching:
    an unmatched card payment means "somebody else funded it" ONLY if the
    funding account's statement for that period is actually present. Without
    that check a missing bank statement makes every card payment that month
    look third-party-funded, and spending collapses.
    """
    from collections import defaultdict
    from types import SimpleNamespace

    by_account: dict[str, list[Any]] = defaultdict(list)
    for row in get_statements(db):
        if not row.get("account_id"):
            continue
        by_account[row["account_id"]].append(SimpleNamespace(
            id=row["id"],
            period_start=_d(row.get("period_start")),
            period_end=_d(row.get("period_end")),
        ))
    return by_account
