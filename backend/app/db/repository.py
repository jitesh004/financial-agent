"""Read/write the domain model to SQLite.

Conversion between Decimal and TEXT happens exclusively here, so no other
module has to remember that money is stored as a string.
"""

from __future__ import annotations

import json
import logging
import uuid
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
    "transfer_pair_id, recurring_series_id, reference, source_row"
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
            txn.currency, txn.category.value, txn.category_source.value,
            txn.category_confidence, int(txn.is_internal_transfer),
            txn.transfer_pair_id, txn.recurring_series_id, txn.reference,
            txn.source_row,
        ))

    placeholders = ",".join(["?"] * 20)
    with db.connection() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO transactions ({_TXN_COLUMNS}) VALUES ({placeholders})",
            rows,
        )
    return len(rows)


def update_transaction_categories(db: Database, transactions: Iterable[Transaction]) -> int:
    """Persist categorization results without rewriting the whole row."""
    rows = [
        (t.category.value, t.category_source.value, t.category_confidence,
         int(t.is_internal_transfer), t.transfer_pair_id, t.merchant, t.id)
        for t in transactions if t.id
    ]
    with db.connection() as conn:
        conn.executemany(
            """UPDATE transactions
                  SET category = ?, category_source = ?, category_confidence = ?,
                      is_internal_transfer = ?, transfer_pair_id = ?, merchant = ?
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
        category=Category(row["category"]),
        category_source=ConfidenceSource(row["category_source"]),
        category_confidence=row["category_confidence"],
        is_internal_transfer=bool(row["is_internal_transfer"]),
        transfer_pair_id=row["transfer_pair_id"],
        recurring_series_id=row["recurring_series_id"],
        reference=row["reference"],
        source_row=row["source_row"],
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
                    Category(r["category"]), r["confidence"], r["source"]
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
    rows = [(s.id, s.account_id, s.label, s.category.value, s.direction.value,
             _txt(s.median_amount), s.cadence_days, s.occurrences,
             s.first_seen.isoformat() if s.first_seen else None,
             s.last_seen.isoformat() if s.last_seen else None,
             s.next_expected.isoformat() if s.next_expected else None,
             int(s.is_active), s.confidence) for s in series]
    with db.connection() as conn:
        conn.execute("DELETE FROM recurring_series")
        conn.executemany(
            """INSERT INTO recurring_series
               (id, account_id, label, category, direction, median_amount,
                cadence_days, occurrences, first_seen, last_seen, next_expected,
                is_active, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    return len(rows)
