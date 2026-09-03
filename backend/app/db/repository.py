"""Read/write the domain model to PostgreSQL.

Conversion between Decimal and TEXT happens exclusively here, so no other
module has to remember that money is stored as a string.

Nothing in this module names a user. Which person's rows a call can see is
decided by the row-level security policy on every table, from the tenant that
`db/engine.py` binds to the connection - so a query written here cannot read
across accounts even by mistake, and adding a new one does not mean
remembering a WHERE clause.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Sequence

import psycopg

from ..analytics import periods
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


def _row_dict(row) -> dict[str, Any]:
    """A result row as a plain dict, without the tenancy column.

    Every table carries `user_id`, but it is bookkeeping for the row-level
    security policy rather than part of the domain - and a `SELECT *` that
    passes straight into an API response should not be shipping a UUID nobody
    asked for (the JSON encoder does not know what to do with one either).
    """
    return {k: v for k, v in zip(row.keys(), row) if k != "user_id"}


def _new_id() -> str:
    return str(uuid.uuid4())


def _col(row, name: str, default: Any = None) -> Any:
    """Read a column that a given query may not have selected.

    A result row raises KeyError rather than returning None for an absent
    key. Every caller here selects `*`, so the columns are present in
    practice - this keeps a partial SELECT from raising instead of simply
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
                       -- Relies on the caller having already resolved which of
                       -- two candidate balances is newer (see
                       -- graph.nodes._merge_account_facts) before this runs -
                       -- by the time an account reaches here its balance
                       -- fields already reflect the freshest statement seen,
                       -- so a plain "prefer non-null" is safe rather than
                       -- needing its own date comparison in SQL.
                       balance_as_of         = COALESCE(?, balance_as_of),
                       interest_rate         = COALESCE(?, interest_rate),
                       emi_amount            = COALESCE(?, emi_amount),
                       credit_limit          = COALESCE(?, credit_limit),
                       holder_name           = COALESCE(?, holder_name),
                       product_name          = CASE WHEN product_name IS NULL OR product_name = ''
                                                     THEN ? ELSE product_name END,
                       institution           = COALESCE(?, institution)
                   WHERE id = ?""",
                (_txt(account.principal_outstanding), _txt(account.current_balance),
                 account.balance_as_of.isoformat() if account.balance_as_of else None,
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
                holder_name, currency, current_balance, balance_as_of,
                principal_outstanding, interest_rate, emi_amount,
                tenure_months_remaining, credit_limit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
             account.balance_as_of.isoformat() if account.balance_as_of else None,
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
        balance_as_of=_d(_col(row, "balance_as_of")),
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
        # SQLite's INSERT OR REPLACE displaced a row colliding on ANY unique
        # index, not just the primary key, and this table has two: the id and
        # the content hash. ON CONFLICT can only name one of them, so the
        # hash collision is resolved first and explicitly - re-ingesting a
        # statement under a fresh id replaces the old one (and its rows, by
        # cascade) rather than failing on the hash index.
        if statement.file_hash:
            conn.execute(
                "DELETE FROM statements WHERE file_hash = ? AND id != ?",
                (statement.file_hash, statement_id))
        conn.execute(
            """INSERT INTO statements
               (id, account_id, source_filename, source_format, file_hash,
                period_start, period_end, opening_balance, closing_balance,
                extractor_used, recon_status, recon_discrepancy, recon_message,
                parse_warnings, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (user_id, id) DO UPDATE SET
                   account_id        = excluded.account_id,
                   source_filename   = excluded.source_filename,
                   source_format     = excluded.source_format,
                   file_hash         = excluded.file_hash,
                   period_start      = excluded.period_start,
                   period_end        = excluded.period_end,
                   opening_balance   = excluded.opening_balance,
                   closing_balance   = excluded.closing_balance,
                   extractor_used    = excluded.extractor_used,
                   recon_status      = excluded.recon_status,
                   recon_discrepancy = excluded.recon_discrepancy,
                   recon_message     = excluded.recon_message,
                   parse_warnings    = excluded.parse_warnings,
                   ingested_at       = excluded.ingested_at""",
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
        d = _row_dict(r)
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
    "excluded, note, source, superseded, category_rule, direction_reason"
)

#: The DO UPDATE half of the upsert, derived from the column list above so the
#: two cannot drift. Re-saving a row the ledger already holds - the same parse
#: run twice, a retry after a partial failure - overwrites it in place, which
#: is what INSERT OR REPLACE did.
_TXN_UPSERT = ", ".join(
    f"{column} = excluded.{column}"
    for column in (c.strip() for c in _TXN_COLUMNS.split(","))
    if column != "id"
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
            txn.source, int(txn.superseded), txn.category_rule,
            txn.direction_reason,
        ))

    placeholders = ",".join(["?"] * len(_TXN_COLUMNS.split(",")))
    stmt = (f"INSERT INTO transactions ({_TXN_COLUMNS}) VALUES ({placeholders})"
            f" ON CONFLICT (user_id, id) DO UPDATE SET {_TXN_UPSERT}")
    with db.connection() as conn:
        try:
            # A SAVEPOINT of its own, so a batch that fails can be abandoned
            # without abandoning the surrounding transaction. PostgreSQL - -
            # unlike SQLite - refuses every further statement on a connection
            # whose transaction has hit an error, so without this the recovery
            # below could not run at all.
            with conn.transaction():
                conn.executemany(stmt, rows)
            return len(rows)
        except psycopg.IntegrityError:
            pass

        # executemany gives no way to tell which of N rows was the
        # offender - every previous fix for a FOREIGN KEY failure here
        # (a dangling account_id, then a dangling statement_id) was found
        # by guesswork against a bare error naming neither the row nor
        # the column. Falling back to one row at a time trades speed
        # (only taken on the rare row that actually violates something)
        # for a diagnosis this specific, and for not losing the other
        # 2000+ perfectly good rows in the same batch to one bad one.
        known_accounts = {r["id"] for r in conn.execute("SELECT id FROM accounts")}
        known_statements = {r["id"] for r in conn.execute("SELECT id FROM statements")}
        saved = 0
        for row, txn in zip(rows, transactions):
            try:
                with conn.transaction():
                    conn.execute(stmt, row)
                saved += 1
            except psycopg.IntegrityError as exc:
                log.error(
                    "dropping transaction %s (%s %r) - %s. account_id=%r "
                    "(known=%s), statement_id=%r (known=%s)",
                    txn.id, txn.txn_date, txn.raw_description[:60], exc,
                    txn.account_id, txn.account_id in known_accounts,
                    txn.statement_id, (txn.statement_id in known_statements
                                      if txn.statement_id else "n/a - was None"),
                )
        return saved


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


#: How a merchant is keyed, matching how the analytics engine groups by one -
#: see engine._merchant_spend. Written once because a "show me the rows behind
#: this merchant" filter that keys the merchant differently from the figure it
#: was clicked on returns a different set of rows than the figure counted.
MERCHANT_KEY = ("COALESCE(NULLIF(merchant, ''),"
                " NULLIF(normalized_description, ''), raw_description)")


def _transaction_filters(
    account_id: str | Sequence[str] | None,
    start: date | None,
    end: date | None,
    category: str | Sequence[str] | None,
    statement_id: str | None,
    rail: str | None,
    needs_review: bool | None = None,
    accounting_month: str | None = None,
    month_start: str | None = None,
    month_end: str | None = None,
    flow_role: str | Sequence[str] | None = None,
    merchant: str | None = None,
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
    _in_or_eq("flow_role", flow_role)

    if merchant:
        # A prefix match on the same expression the engine groups by, because
        # its key is that expression truncated to 40 characters. `_` and `%`
        # are escaped: a merchant called "PAY_TM" would otherwise match
        # anything with any character in that position.
        escaped = str(merchant).replace("\\", "\\\\")
        escaped = escaped.replace("%", "\\%").replace("_", "\\_")
        clauses.append(f"{MERCHANT_KEY} LIKE ? ESCAPE '\\'")
        params.append(f"{escaped}%")
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

    if needs_review is not None:
        clauses.append("needs_review = ?")
        params.append(1 if needs_review else 0)

    # The reporting period a row belongs to, which is not always the calendar
    # month of its date - see analytics.periods. Filtering on txn_date instead
    # would put a salary paid on 1-Sep in September even when the ledger
    # counts it as August's.
    #
    # `accounting_month` selects exactly one; `month_start`/`month_end` select
    # a run of them, which is what every period preset in the app resolves to.
    # Both go through the same expression as analytics.periods.effective_month
    # so a row imported before accounting months existed still lands in the
    # month of its date rather than in no month at all - filtering a bare
    # column would silently drop every such row from every period.
    month_column = periods.effective_month_sql()
    if accounting_month:
        clauses.append(f"{month_column} = ?")
        params.append(accounting_month)
    if month_start:
        clauses.append(f"{month_column} >= ?")
        params.append(month_start)
    if month_end:
        clauses.append(f"{month_column} <= ?")
        params.append(month_end)

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
    needs_review: bool | None = None,
    accounting_month: str | None = None,
    month_start: str | None = None,
    month_end: str | None = None,
    flow_role: str | Sequence[str] | None = None,
    merchant: str | None = None,
) -> list[Transaction]:
    clauses, params = _transaction_filters(
        account_id, start, end, category, statement_id, rail,
        needs_review, accounting_month, month_start, month_end,
        flow_role, merchant)

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


def get_transaction(db: Database, txn_id: str) -> Transaction | None:
    """One transaction by id, or None."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    return _row_to_transaction(row) if row else None


def transactions_in_pair(db: Database, pair_id: str) -> list[Transaction]:
    """Every leg of one transfer or settlement group.

    Read from the transactions themselves rather than from `transfer_pairs`,
    which only records the two ends of a 1:1 match. A multi-leg settlement -
    one bank debit covering three cards - has no pair row per leg, and asking
    the pairs table would report a group of two for a group of four.
    """
    if not pair_id:
        return []
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE transfer_pair_id = ? "
            "ORDER BY txn_date, id", (pair_id,)).fetchall()
    return [_row_to_transaction(r) for r in rows]


def get_transfer_pair(db: Database, pair_id: str) -> dict[str, Any] | None:
    """The recorded match for a pair id: kind, day gap and confidence."""
    if not pair_id:
        return None
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM transfer_pairs WHERE pair_id = ?",
            (pair_id,)).fetchone()
    return _row_dict(row) if row else None


def count_transactions(
    db: Database,
    account_id: str | Sequence[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    category: str | Sequence[str] | None = None,
    statement_id: str | None = None,
    rail: str | None = None,
    needs_review: bool | None = None,
    accounting_month: str | None = None,
    month_start: str | None = None,
    month_end: str | None = None,
    flow_role: str | Sequence[str] | None = None,
    merchant: str | None = None,
) -> int:
    clauses, params = _transaction_filters(
        account_id, start, end, category, statement_id, rail,
        needs_review, accounting_month, month_start, month_end,
        flow_role, merchant)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connection() as conn:
        return conn.execute(
            f"SELECT COUNT(*) c FROM transactions {where}", params).fetchone()["c"]


def covered_months(db: Database) -> list[tuple[str, int]]:
    """Every accounting month the ledger has rows in, oldest first, with counts.

    Read through the same expression as analytics.periods.effective_month, so
    a month appears here exactly when filtering by it would return rows - a
    list built off the bare column would omit every month whose rows predate
    accounting periods and then the picker would not offer them.
    """
    month = periods.effective_month_sql()
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT m, COUNT(*) c FROM ("
            f"  SELECT {month} AS m FROM transactions"
            f") months WHERE m != '' GROUP BY m ORDER BY m"
        ).fetchall()
    return [(r["m"], r["c"]) for r in rows]


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
        category_rule=_col(row, "category_rule", ""),
        direction_reason=_col(row, "direction_reason", ""),
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
        source=_col(row, "source") or "statement",
        superseded=bool(_col(row, "superseded")),
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


def forget_merchant(db: Database, key: str) -> None:
    """Drop what the cache believes about one merchant.

    Used when a category is cleared rather than chosen. Storing
    "uncategorized" as a user decision would be worse than storing nothing:
    the upsert protects user rows from every later guess, so one cleared row
    would pin that merchant as unknowable forever - the model is never asked
    about it again, and the run that skipped it reports it as a cache hit.
    """
    if not key:
        return
    with db.connection() as conn:
        conn.execute("DELETE FROM merchant_categories WHERE merchant_key = ?",
                     (key,))


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
    # Categories used to be an Enum and are now plain strings. `getattr` so
    # this keeps working either way rather than raising on one of them - the
    # same change silently broke the model categoriser's prompt builder, and
    # a broad try/except upstream hid it for the whole of that run.
    rows = [(k, getattr(c, "value", c), src, conf)
            for k, (c, conf, src) in mapping.items()]
    with db.connection() as conn:
        conn.executemany(
            """INSERT INTO merchant_categories
                   (merchant_key, category, source, confidence, hit_count, updated_at)
               VALUES (?,?,?,?,1,datetime('now'))
               ON CONFLICT (user_id, merchant_key) DO UPDATE SET
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
            """INSERT INTO transfer_pairs
               (pair_id, debit_txn_id, credit_txn_id, amount, day_gap, kind, confidence)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT (user_id, pair_id) DO UPDATE SET
                   debit_txn_id  = excluded.debit_txn_id,
                   credit_txn_id = excluded.credit_txn_id,
                   amount        = excluded.amount,
                   day_gap       = excluded.day_gap,
                   kind          = excluded.kind,
                   confidence    = excluded.confidence""",
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
               ON CONFLICT (user_id, id) DO UPDATE SET
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
               ON CONFLICT (user_id, id) DO UPDATE SET
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


def get_recurring_series(db: Database) -> list[dict[str, Any]]:
    """Every recurring series, with the user's own edits already applied.

    `recurring_series` itself is pipeline output, fully rewritten by
    `save_recurring_series` on every full analysis - so baking an override
    into that table only shows up after the next reanalyze. Rename, mute or
    delete a series from the Recurring tab in between, and the plain
    `SELECT * FROM recurring_series` this used to be would keep showing the
    machine-picked label, `is_active` and category, and a "deleted" series
    right where it always was, until something eventually reprocessed
    everything. Merging live here, the same way `save_recurring_series`
    already merges before writing, means an edit is visible on the very next
    read instead of the next full reprocess.
    """
    with db.connection() as conn:
        rows = conn.execute(
            """SELECT s.*, o.label AS o_label, o.category AS o_category,
                      o.is_active AS o_is_active, o.deleted AS o_deleted
               FROM recurring_series s
               LEFT JOIN recurring_series_overrides o ON o.series_id = s.id
               ORDER BY s.is_active DESC, s.median_amount DESC"""
        ).fetchall()
    out = []
    for r in rows:
        d = _row_dict(r)
        o_label = d.pop("o_label")
        o_category = d.pop("o_category")
        o_is_active = d.pop("o_is_active")
        if d.pop("o_deleted"):
            continue
        if o_label is not None:
            d["label"] = o_label
        if o_category is not None:
            d["category"] = o_category
        if o_is_active is not None:
            d["is_active"] = o_is_active
        if "median_amount" in d:
            d["median_amount"] = _dec(d["median_amount"])
        out.append(d)
    return out


def recurring_overrides(db: Database) -> dict[str, dict[str, Any]]:
    """The user's own edits to detected series, by series id.

    Separate from `get_recurring_series` because a caller that re-detects the
    series itself - the budget does, to recover which transactions belong to
    which series - still has to honour a rename, a mute or a delete. The ids
    are content hashes of account + direction + merchant signature, so they
    survive re-detection and an override keeps pointing at the same series.
    """
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT series_id, label, category, is_active, deleted"
            " FROM recurring_series_overrides").fetchall()
    return {r["series_id"]: _row_dict(r) for r in rows}


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
               ON CONFLICT (user_id, fingerprint) DO UPDATE SET
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
               ON CONFLICT (user_id, id) DO UPDATE SET
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
                ORDER BY created_at DESC, seq DESC LIMIT 1"""
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


def clear_analysis_runs(db: Database) -> int:
    """Drop every stored dashboard payload.

    The stored payload exists so a restart restores the dashboard instead of
    silently downgrading it to a lossy rebuild. That is worth having - but it
    also means forgetting the in-memory cache is not enough to invalidate
    anything: `/api/dashboard` falls straight through to the stored row and
    serves the same stale figures back. Whatever decided the cache was stale
    was talking about these too.
    """
    with db.connection() as conn:
        return conn.execute("DELETE FROM analysis_runs").rowcount


def prune_analysis_runs(db: Database, keep: int = 20) -> int:
    """Keep the newest `keep` runs. Each payload is a full dashboard."""
    with db.connection() as conn:
        cur = conn.execute(
            """DELETE FROM analysis_runs WHERE id NOT IN (
                   SELECT id FROM analysis_runs
                    ORDER BY created_at DESC, seq DESC LIMIT ?)""",
            (keep,),
        )
        return cur.rowcount


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
    return [_row_dict(r) for r in rows]

def add_custom_category(db, name: str, color: str = "#6b7280", icon: str = "Tag") -> None:
    with db.connection() as conn:
        conn.execute("INSERT INTO custom_categories (name, color, icon)"
                     " VALUES (?, ?, ?) ON CONFLICT (user_id, name) DO NOTHING",
                     (name.strip().lower(), color, icon))

def delete_custom_category(db, name: str) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM custom_categories WHERE name = ?", (name.strip().lower(),))

def update_recurring_series_override(db, series_id: str, payload: dict) -> None:
    with db.connection() as conn:
        conn.execute("INSERT INTO recurring_series_overrides (series_id) VALUES (?)"
                     " ON CONFLICT (user_id, series_id) DO NOTHING", (series_id,))
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
        d = _row_dict(r)
        d['amount'] = _dec(d['amount'])
        d['settled_amount'] = _dec(d['settled_amount'])
        return d

def settle_claim(db, claim_id: str, method: str, amount, settled_on: str,
                 note: str = "", txn_fingerprint: str = "") -> str:
    """Record how a claim was resolved.

    `write_off` is not like the other five methods: choosing it means the
    money is confirmed never coming back, which converts the original
    purchase into a real expense rather than recovering it. Every other
    method means money genuinely returned, so the origin transaction rightly
    stays excluded from spending forever. Conflating the two - which the
    first version of this function did, by treating "write off" as just
    another way to fully settle a claim - left the purchase permanently
    invisible to spending even though nothing was ever actually recovered.
    """
    settlement_id = str(uuid.uuid4())
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO claim_settlements (id, claim_id, method, amount, settled_on, txn_fingerprint, note) "
            "VALUES (?,?,?,?,?,?,?)",
            (settlement_id, claim_id, method, _txt(amount), settled_on, txn_fingerprint or "", note or ""))

        claim_row = conn.execute(
            "SELECT amount, settled_amount, origin_fingerprint FROM claims WHERE id = ?",
            (claim_id,)).fetchone()
        if not claim_row:
            return settlement_id

        if method == "write_off":
            # No money moved, so settled_amount is untouched - whatever
            # portion (if any) was genuinely recovered earlier stays on
            # record as recovered, and the rest is simply written off.
            conn.execute(
                "UPDATE claims SET status = 'written_off', closed_on = ? WHERE id = ?",
                (settled_on, claim_id))
        else:
            new_settled = _dec(claim_row["settled_amount"]) + amount
            if new_settled >= _dec(claim_row["amount"]):
                status, closed_on = "settled", settled_on
            elif new_settled > 0:
                status, closed_on = "partial", None
            else:
                status, closed_on = "open", None
            conn.execute(
                "UPDATE claims SET settled_amount = ?, status = ?, closed_on = ? WHERE id = ?",
                (_txt(new_settled), status, closed_on, claim_id))

    if method == "write_off" and claim_row["origin_fingerprint"]:
        # Let the automatic value take over again rather than force
        # `excluded = false`: if the purchase is re-parsed from scratch it
        # should be judged like any other row, not permanently pinned either
        # way by a decision made at write-off time.
        clear_override_field(db, claim_row["origin_fingerprint"], "excluded")

    return settlement_id

def get_claim_settlements(db, claim_id: str) -> list:
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM claim_settlements WHERE claim_id = ?", (claim_id,)).fetchall()
        out = []
        for r in rows:
            d = _row_dict(r)
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
            d = _row_dict(r)
            if 'amount' in d: d['amount'] = _dec(d['amount'])
            if 'settled_amount' in d: d['settled_amount'] = _dec(d['settled_amount'])
            out.append(d)
        return out

def save_splits(db, parent_fingerprint: str, parent_amount, splits: list,
                origin_key: tuple[str, str, str, str] | None = None) -> list:
    # Rounded before comparing rather than compared exactly: a JSON number
    # for something like 450.30 can arrive as a float with binary rounding
    # noise, and Decimal preserves whatever noise it was handed rather than
    # correcting it - an exact != would reject a split a person would look at
    # and call correct.
    total = sum(s["amount"] for s in splits).quantize(Decimal("0.01"))
    if total != Decimal(str(parent_amount)).quantize(Decimal("0.01")):
        raise ValueError(f"Splits sum to {total}, expected {parent_amount}")

    # The parent's loose key (pipeline.fingerprint.loose_key), stored so
    # `pipeline.overrides.apply_splits` can recover this split if the parent's
    # strict fingerprint later moves - an account being re-identified across a
    # reprocess, the same event `user_overrides` already tolerates.
    origin_date, origin_amount, origin_direction, origin_desc_hash = (
        origin_key if origin_key else ("", "", "", ""))

    ids = []
    with db.connection() as conn:
        conn.execute("DELETE FROM transaction_splits WHERE parent_fingerprint = ?", (parent_fingerprint,))
        rows = []
        for i, s in enumerate(splits):
            split_id = str(uuid.uuid4())
            ids.append(split_id)
            # `.get("note") or ""`, not `.get("note", "")`: the key is always
            # present here (main.py's split_transaction always sends it), just
            # sometimes None when the caller left it blank - a default that
            # only fires on a *missing* key would never catch that and the
            # column's NOT NULL constraint would reject the insert.
            rows.append((split_id, parent_fingerprint, _txt(s["amount"]), s.get("category"), s.get("flow_role"), s.get("note") or "", i,
                        origin_date, origin_amount, origin_direction, origin_desc_hash))
        conn.executemany(
            "INSERT INTO transaction_splits (id, parent_fingerprint, amount, category, flow_role, note, position, "
            "origin_date, origin_amount, origin_direction, origin_desc_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return ids


def repoint_splits(db: Database, old_fingerprint: str, new_fingerprint: str) -> None:
    """Move a split onto the fingerprint its parent transaction now has.

    Mirrors `repoint_override` for the same reason: found by the loose key
    because the strict fingerprint had shifted, most often because the
    account it belongs to was re-identified. Without this the same recovery
    would have to happen on every subsequent run instead of just once.
    """
    if old_fingerprint == new_fingerprint:
        return
    with db.connection() as conn:
        # The destination may already have its own splits if two parents
        # collapsed onto one identity. Those are the more recent decision, so
        # they win and the stale rows under the old fingerprint are dropped
        # rather than merged.
        exists = conn.execute(
            "SELECT 1 FROM transaction_splits WHERE parent_fingerprint = ?",
            (new_fingerprint,)).fetchone()
        if exists:
            conn.execute("DELETE FROM transaction_splits WHERE parent_fingerprint = ?",
                        (old_fingerprint,))
        else:
            conn.execute(
                "UPDATE transaction_splits SET parent_fingerprint = ? WHERE parent_fingerprint = ?",
                (new_fingerprint, old_fingerprint))

def get_splits(db, parent_fingerprint: str) -> list:
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM transaction_splits WHERE parent_fingerprint = ?", (parent_fingerprint,)).fetchall()
        out = []
        for r in rows:
            d = _row_dict(r)
            if 'amount' in d: d['amount'] = _dec(d['amount'])
            if 'settled_amount' in d: d['settled_amount'] = _dec(d['settled_amount'])
            out.append(d)
        return out


def get_all_splits(db) -> dict[str, list[dict]]:
    """Every split, grouped by the fingerprint of the row it divides.

    One query for the whole ledger rather than one per transaction - this
    runs on every enrichment pass, so it has to stay cheap even when most
    transactions have no splits at all.
    """
    from collections import defaultdict

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM transaction_splits ORDER BY parent_fingerprint, position"
        ).fetchall()
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        d = _row_dict(r)
        d["amount"] = _dec(d["amount"])
        out[d["parent_fingerprint"]].append(d)
    return dict(out)

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

#: The one kind of inference these two helpers cache: "which bank issued this
#: statement, and what sort of account is it?", keyed on the letterhead.
_IDENTITY_KIND = "statement_identity"


def _identity_key(input_hash: str) -> str:
    return hashlib.sha256(f"{_IDENTITY_KIND}|{input_hash}".encode()).hexdigest()


def get_ai_inference(db, fingerprint: str) -> dict | None:
    """A cached model answer about a statement's identity, if there is one.

    Both of these named a `fingerprint` column that this table has never had -
    it stores `cache_key`, `kind` and `input_hash` - so every call raised
    OperationalError. Neither is wrapped in a try, and the caller is the
    identity fallback in `extract_metadata`, which runs precisely when the
    deterministic reader could NOT identify a statement. So the path meant to
    rescue an unrecognised statement was instead the one thing guaranteed to
    fail its parse outright. The table has been empty this whole time.
    """
    with db.connection() as conn:
        row = conn.execute(
            "SELECT result_json FROM ai_inferences WHERE cache_key = ?",
            (_identity_key(fingerprint),)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["result_json"])
    except (json.JSONDecodeError, TypeError):
        return None


def save_ai_inference(db, fingerprint: str, result: dict) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO ai_inferences (cache_key, kind, input_hash, result_json)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (user_id, cache_key) DO UPDATE SET"
            "   result_json = excluded.result_json,"
            "   hit_count = ai_inferences.hit_count + 1",
            (_identity_key(fingerprint), _IDENTITY_KIND, fingerprint,
             json.dumps(result)))


def get_statement_period_by_id(db: Database) -> dict[str, tuple[Any, Any]]:
    """Every statement's period, keyed by statement id.

    Keyed by statement rather than by account because the question here is
    "which billing cycle did this row arrive on", and an account has many.
    """
    out: dict[str, tuple[Any, Any]] = {}
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, period_start, period_end FROM statements").fetchall()
    for r in rows:
        out[r["id"]] = (_as_date(r["period_start"]), _as_date(r["period_end"]))
    return out


def _as_date(value: Any):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


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


# --------------------------------------------------------------------------
# Explore dashboards
#
# A widget row stores a query, not a result. Reading one back therefore never
# involves stale figures - the query is re-run against the live ledger.
# --------------------------------------------------------------------------

def _json(raw: Any, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _widget_json(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "dashboard_id": row["dashboard_id"],
        "title": row["title"],
        "type": row["type"],
        "query": _json(row["query_json"], {}),
        "viz": _json(row["viz_json"], {}),
        "position": row["position"],
        "width": row["width"],
        "height": row["height"],
    }


def _dashboard_json(row, widgets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "is_default": bool(row["is_default"]),
        "position": row["position"],
        "filters": _json(row["filters_json"], {}),
        "updated_at": row["updated_at"],
    }
    if widgets is not None:
        out["widgets"] = widgets
    return out


def list_dashboards(db: Database) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT d.*, (SELECT COUNT(*) FROM dashboard_widgets w"
            "             WHERE w.dashboard_id = d.id) AS widget_count"
            " FROM dashboards d ORDER BY d.position, d.created_at"
        ).fetchall()
    return [{**_dashboard_json(r), "widget_count": r["widget_count"]} for r in rows]


def get_dashboard(db: Database, dashboard_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM dashboards WHERE id = ?", (dashboard_id,)).fetchone()
        if row is None:
            return None
        widgets = conn.execute(
            "SELECT * FROM dashboard_widgets WHERE dashboard_id = ?"
            " ORDER BY position, created_at", (dashboard_id,)).fetchall()
    return _dashboard_json(row, [_widget_json(w) for w in widgets])


def create_dashboard(db: Database, name: str, description: str = "",
                     filters: dict[str, Any] | None = None,
                     widgets: list[dict[str, Any]] | None = None) -> str:
    dashboard_id = str(uuid.uuid4())
    with db.connection() as conn:
        position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM dashboards"
        ).fetchone()["p"]
        first = conn.execute("SELECT COUNT(*) c FROM dashboards").fetchone()["c"] == 0
        conn.execute(
            "INSERT INTO dashboards (id, name, description, is_default, position,"
            " filters_json) VALUES (?, ?, ?, ?, ?, ?)",
            (dashboard_id, name, description, 1 if first else 0, position,
             json.dumps(filters or {})),
        )
    for index, widget in enumerate(widgets or []):
        create_widget(db, dashboard_id, {**widget, "position": index})
    return dashboard_id


def update_dashboard(db: Database, dashboard_id: str, **fields: Any) -> bool:
    sets, params = [], []
    for column in ("name", "description"):
        if fields.get(column) is not None:
            sets.append(f"{column} = ?")
            params.append(fields[column])
    if fields.get("filters") is not None:
        sets.append("filters_json = ?")
        params.append(json.dumps(fields["filters"]))
    if fields.get("position") is not None:
        sets.append("position = ?")
        params.append(int(fields["position"]))

    with db.connection() as conn:
        if conn.execute("SELECT 1 FROM dashboards WHERE id = ?",
                        (dashboard_id,)).fetchone() is None:
            return False
        if fields.get("is_default"):
            # At most one default. Cleared for everyone first, so the two
            # statements can never both be true even briefly.
            conn.execute("UPDATE dashboards SET is_default = 0")
            sets.append("is_default = 1")
        if not sets:
            return True
        sets.append("updated_at = datetime('now')")
        conn.execute(f"UPDATE dashboards SET {', '.join(sets)} WHERE id = ?",
                     [*params, dashboard_id])
        return True


def delete_dashboard(db: Database, dashboard_id: str) -> bool:
    with db.connection() as conn:
        row = conn.execute("SELECT is_default FROM dashboards WHERE id = ?",
                           (dashboard_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM dashboards WHERE id = ?", (dashboard_id,))
        # Deleting the default would otherwise leave the Explore tab with no
        # board to open and an empty state that reads as data loss.
        if row["is_default"]:
            survivor = conn.execute(
                "SELECT id FROM dashboards ORDER BY position LIMIT 1").fetchone()
            if survivor:
                conn.execute("UPDATE dashboards SET is_default = 1 WHERE id = ?",
                             (survivor["id"],))
    return True


def create_widget(db: Database, dashboard_id: str,
                  widget: dict[str, Any]) -> str | None:
    widget_id = str(uuid.uuid4())
    with db.connection() as conn:
        if conn.execute("SELECT 1 FROM dashboards WHERE id = ?",
                        (dashboard_id,)).fetchone() is None:
            return None
        position = widget.get("position")
        if position is None:
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM dashboard_widgets"
                " WHERE dashboard_id = ?", (dashboard_id,)).fetchone()["p"]
        conn.execute(
            "INSERT INTO dashboard_widgets (id, dashboard_id, title, type,"
            " query_json, viz_json, position, width, height)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (widget_id, dashboard_id, widget.get("title", ""),
             widget.get("type", "table"), json.dumps(widget.get("query") or {}),
             json.dumps(widget.get("viz") or {}), position,
             int(widget.get("width", 6)), int(widget.get("height", 2))),
        )
        conn.execute("UPDATE dashboards SET updated_at = datetime('now')"
                     " WHERE id = ?", (dashboard_id,))
    return widget_id


def update_widget(db: Database, widget_id: str, widget: dict[str, Any]) -> bool:
    sets, params = [], []
    for column in ("title", "type"):
        if widget.get(column) is not None:
            sets.append(f"{column} = ?")
            params.append(widget[column])
    for column, key in (("query_json", "query"), ("viz_json", "viz")):
        if widget.get(key) is not None:
            sets.append(f"{column} = ?")
            params.append(json.dumps(widget[key]))
    for column in ("position", "width", "height"):
        if widget.get(column) is not None:
            sets.append(f"{column} = ?")
            params.append(int(widget[column]))
    if not sets:
        return True
    with db.connection() as conn:
        cursor = conn.execute(
            f"UPDATE dashboard_widgets SET {', '.join(sets)} WHERE id = ?",
            [*params, widget_id])
        conn.execute(
            "UPDATE dashboards SET updated_at = datetime('now') WHERE id ="
            " (SELECT dashboard_id FROM dashboard_widgets WHERE id = ?)",
            (widget_id,))
        return cursor.rowcount > 0


def delete_widget(db: Database, widget_id: str) -> bool:
    with db.connection() as conn:
        cursor = conn.execute("DELETE FROM dashboard_widgets WHERE id = ?",
                              (widget_id,))
        return cursor.rowcount > 0


def save_layout(db: Database, dashboard_id: str,
                layout: list[dict[str, Any]]) -> int:
    """Persist position and size for several widgets at once.

    All of them inside one connection: a drag that reorders six tiles must not
    be able to land half-applied, which would leave two widgets claiming the
    same slot and the order changing again on reload.
    """
    updated = 0
    with db.connection() as conn:
        for entry in layout:
            cursor = conn.execute(
                "UPDATE dashboard_widgets SET position = ?, width = ?, height = ?"
                " WHERE id = ? AND dashboard_id = ?",
                (int(entry.get("position", 0)), int(entry.get("width", 6)),
                 int(entry.get("height", 2)), entry.get("id"), dashboard_id))
            updated += cursor.rowcount
        conn.execute("UPDATE dashboards SET updated_at = datetime('now')"
                     " WHERE id = ?", (dashboard_id,))
    return updated


# --------------------------------------------------------------------------
# Background jobs
#
# The in-memory JobStore stays the source of truth for a job that is running -
# it is written to on every tick and reading it costs nothing. These functions
# are the durable mirror: enough to answer "what happened to that scan?" after
# a restart, and enough to pick an interrupted one back up.
# --------------------------------------------------------------------------

#: A job in one of these states was alive when the process stopped.
UNFINISHED_JOB_STATES = ("queued", "running")


def save_job(db: Database, header: dict[str, Any],
             items: list[dict[str, Any]] | None = None) -> None:
    """Upsert one job row and append any items not yet written.

    Items are appended by sequence number rather than rewritten, because a
    download of four hundred attachments would otherwise rewrite the whole
    list on every tick.

    The header is an ON CONFLICT upsert, emphatically NOT INSERT OR REPLACE.
    Replace deletes the existing row before inserting the new one, and
    job_items cascades from jobs - so every flush would have silently wiped
    the per-file trace the flush before it had just written, leaving a
    completed job claiming it processed nothing. An upsert edits the row in
    place and the children survive.

    Plain INSERT still happens when the row is genuinely absent, so a job
    whose row was cleared mid-run (the derived scope is clearable) puts
    itself back rather than quietly failing to record anything more.
    """
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO jobs (id, kind, status, phase, current,"
            " total, message, started_at, finished_at, result_json,"
            " request_json, errors_json, warnings_json, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))"
            " ON CONFLICT (user_id, id) DO UPDATE SET"
            "   kind = excluded.kind, status = excluded.status,"
            "   phase = excluded.phase, current = excluded.current,"
            "   total = excluded.total, message = excluded.message,"
            "   finished_at = excluded.finished_at,"
            "   result_json = excluded.result_json,"
            "   request_json = excluded.request_json,"
            "   errors_json = excluded.errors_json,"
            "   warnings_json = excluded.warnings_json,"
            "   updated_at = datetime('now')",
            (header["id"], header["kind"], header["status"], header["phase"],
             header["current"], header["total"], header["message"],
             header["started_at"], header["finished_at"],
             _job_json(header.get("result")), _job_json(header.get("request")),
             _job_json(header.get("errors") or []),
             _job_json(header.get("warnings") or [])),
        )
        if items:
            conn.executemany(
                "INSERT INTO job_items (job_id, seq, name, key,"
                " status, detail, cached) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (user_id, job_id, seq) DO UPDATE SET"
                "   name = excluded.name, key = excluded.key,"
                "   status = excluded.status, detail = excluded.detail,"
                "   cached = excluded.cached",
                [(header["id"], item["seq"], item["name"], item.get("key", ""),
                  item["status"], item.get("detail", ""),
                  1 if item.get("cached") else 0)
                 for item in items],
            )


def _job_json(value: Any) -> str:
    """Serialise a job payload, never raising.

    A result that cannot be serialised must not take down the job that
    produced it - the work already happened. Anything exotic is coerced to its
    string form and the trace is preserved rather than the process dying at the
    final flush.
    """
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        log.warning("job payload was not serialisable: %s", exc)
        return json.dumps({"unserialisable": str(exc)})


def get_job(db: Database, job_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        items = conn.execute(
            "SELECT * FROM job_items WHERE job_id = ? ORDER BY seq", (job_id,)
        ).fetchall()
    return _job_row(row, items)


def list_jobs(db: Database, limit: int = 20, kind: str | None = None,
              active_only: bool = False) -> list[dict[str, Any]]:
    """Recent jobs, newest first. Items are omitted - a list view never shows
    four hundred per-file rows, and loading them would dominate the query."""
    clauses, params = [], []
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if active_only:
        clauses.append(f"status IN ({', '.join('?' * len(UNFINISHED_JOB_STATES))})")
        params.extend(UNFINISHED_JOB_STATES)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY started_at DESC LIMIT ?",
            [*params, limit]).fetchall()
    return [_job_row(row, []) for row in rows]


def _job_row(row, items) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "phase": row["phase"],
        "current": row["current"],
        "total": row["total"],
        "message": row["message"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "result": _json(row["result_json"], None),
        "request": _json(row["request_json"], None),
        "errors": _json(row["errors_json"], []),
        "warnings": _json(row["warnings_json"], []),
        "updated_at": row["updated_at"],
        "items": [
            {"seq": i["seq"], "name": i["name"], "key": i["key"],
             "status": i["status"], "detail": i["detail"],
             "cached": bool(i["cached"])}
            for i in items
        ],
    }


def mark_unfinished_jobs_interrupted(db: Database) -> int:
    """Called once at startup. A job cannot outlive the process that ran it.

    Without this, a scan killed by a restart stays 'running' forever and the
    UI shows a progress bar that will never move again - strictly worse than
    saying plainly that it stopped.
    """
    placeholders = ", ".join("?" * len(UNFINISHED_JOB_STATES))
    with db.connection() as conn:
        cursor = conn.execute(
            f"UPDATE jobs SET status = 'interrupted',"
            f" message = CASE WHEN message = '' THEN"
            f"   'Stopped when the server restarted.' ELSE message END,"
            f" phase = 'Interrupted', updated_at = datetime('now')"
            f" WHERE status IN ({placeholders})",
            UNFINISHED_JOB_STATES)
        return cursor.rowcount


def completed_job_keys(db: Database, job_id: str) -> set[str]:
    """Work units this job already finished, for resuming it.

    A failed item is deliberately NOT counted as finished: whatever went wrong
    may well have been the interruption itself, and retrying one file is
    cheaper than explaining to someone why it was skipped.
    """
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT key FROM job_items WHERE job_id = ?"
            " AND status IN ('done', 'skipped') AND key != ''", (job_id,)
        ).fetchall()
    return {row["key"] for row in rows}


def prune_jobs(db: Database, keep: int = 100) -> int:
    """Keep the most recent jobs and drop the rest, items and all."""
    with db.connection() as conn:
        cursor = conn.execute(
            "DELETE FROM jobs WHERE id NOT IN ("
            "  SELECT id FROM jobs ORDER BY started_at DESC LIMIT ?)", (keep,))
        return cursor.rowcount


# --------------------------------------------------------------------------
# Credit bureau reports
#
# Stored alongside the ledger rather than merged into it. A bureau's figure for
# an account is a second opinion, not a correction: it is reported monthly, is
# often weeks stale, and overwriting a reconciled balance with it would replace
# a checked number with an unchecked one.
# --------------------------------------------------------------------------

def save_bureau_report(db: Database, report: Any, file_hash: str = "",
                       filename: str = "") -> str:
    """Persist a parsed report and its accounts. Returns the report id.

    Re-importing the same file replaces the previous parse of it rather than
    adding a second copy - the file hash is the identity, exactly as it is for
    statements.
    """
    report_id = str(uuid.uuid4())
    with db.connection() as conn:
        if file_hash:
            existing = conn.execute(
                "SELECT id FROM bureau_reports WHERE file_hash = ?",
                (file_hash,)).fetchone()
            if existing:
                report_id = existing["id"]
                conn.execute("DELETE FROM bureau_accounts WHERE report_id = ?",
                             (report_id,))
                conn.execute("DELETE FROM bureau_reports WHERE id = ?",
                             (report_id,))

        conn.execute(
            "INSERT INTO bureau_reports (id, bureau, score, score_band,"
            " pulled_on, holder_name, file_hash, source_filename, warnings)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, report.bureau, report.score, report.score_band,
             report.pulled_on.isoformat() if report.pulled_on else None,
             report.holder_name, file_hash, filename,
             json.dumps(report.warnings)),
        )
        for account in report.accounts:
            conn.execute(
                "INSERT INTO bureau_accounts (id, report_id, lender, lender_key,"
                " account_type, account_number_masked, number_suffix, ownership,"
                " opened_on, closed_on, status, sanctioned, current_balance,"
                " overdue, credit_limit, emi_amount, dpd_history, worst_dpd)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), report_id, account.lender,
                 account.lender_key, account.account_type,
                 account.account_number_masked, account.number_suffix,
                 account.ownership,
                 account.opened_on.isoformat() if account.opened_on else None,
                 account.closed_on.isoformat() if account.closed_on else None,
                 account.status, _txt(account.sanctioned),
                 _txt(account.current_balance), _txt(account.overdue),
                 _txt(account.credit_limit), _txt(account.emi_amount),
                 json.dumps(account.dpd_history), account.worst_dpd),
            )
    return report_id







def get_bureau_reports(db: Database) -> list[dict[str, Any]]:
    """Every report, newest pull first."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM bureau_accounts a"
            "             WHERE a.report_id = r.id) AS account_count"
            " FROM bureau_reports r"
            " ORDER BY COALESCE(r.pulled_on, r.ingested_at) DESC"
        ).fetchall()
    return [{
        "id": r["id"], "bureau": r["bureau"], "score": r["score"],
        "score_band": r["score_band"], "pulled_on": r["pulled_on"],
        "holder_name": r["holder_name"], "source_filename": r["source_filename"],
        "warnings": _json(r["warnings"], []), "account_count": r["account_count"],
        "ingested_at": r["ingested_at"],
    } for r in rows]


def get_bureau_accounts(db: Database, report_id: str | None = None
                        ) -> list[dict[str, Any]]:
    where = "WHERE report_id = ?" if report_id else ""
    params = (report_id,) if report_id else ()
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM bureau_accounts {where} ORDER BY lender, number_suffix",
            params).fetchall()
    return [_bureau_account_json(r) for r in rows]


def _bureau_account_json(row) -> dict[str, Any]:
    return {
        "id": row["id"], "report_id": row["report_id"], "lender": row["lender"],
        "lender_key": row["lender_key"], "account_type": row["account_type"],
        "account_number_masked": row["account_number_masked"],
        "number_suffix": row["number_suffix"], "ownership": row["ownership"],
        "opened_on": row["opened_on"], "closed_on": row["closed_on"],
        "status": row["status"], "sanctioned": row["sanctioned"],
        "current_balance": row["current_balance"], "overdue": row["overdue"],
        "credit_limit": row["credit_limit"], "emi_amount": row["emi_amount"],
        "dpd_history": _json(row["dpd_history"], []),
        "worst_dpd": row["worst_dpd"], "account_id": row["account_id"],
        "match_status": row["match_status"],
        "match_confidence": row["match_confidence"],
        "match_reason": row["match_reason"],
    }


def apply_bureau_matches(db: Database, matches: Iterable[Any]) -> int:
    """Record automatic and suggested links.

    A link a human already confirmed or rejected is never overwritten: the
    whole reason fuzzy matches are offered rather than applied is that the
    person is the authority, and re-running the matcher must not quietly
    overrule them.
    """
    updated = 0
    with db.connection() as conn:
        for match in matches:
            row = conn.execute(
                "SELECT match_status FROM bureau_accounts WHERE id = ?",
                (match.bureau_account_id,)).fetchone()
            if row is None or row["match_status"] in {"confirmed", "rejected"}:
                continue
            conn.execute(
                "UPDATE bureau_accounts SET account_id = ?, match_status = ?,"
                " match_confidence = ?, match_reason = ? WHERE id = ?",
                (match.account_id if match.status == "auto" else None,
                 match.status, match.confidence, match.reason,
                 match.bureau_account_id))
            updated += 1
    return updated


def set_bureau_match(db: Database, bureau_account_id: str,
                     account_id: str | None, confirmed: bool) -> bool:
    """A human's decision about one link. Final until they change it."""
    with db.connection() as conn:
        cursor = conn.execute(
            "UPDATE bureau_accounts SET account_id = ?, match_status = ?,"
            " match_confidence = ?, match_reason = ? WHERE id = ?",
            (account_id if confirmed else None,
             "confirmed" if confirmed else "rejected",
             1.0 if confirmed else 0.0,
             "confirmed by you" if confirmed else "rejected by you",
             bureau_account_id))
        return cursor.rowcount > 0


# --------------------------------------------------------------------------
# Investment holdings
# --------------------------------------------------------------------------

def save_portfolio_statement(db: Database, statement: Any, account_id: str | None,
                             file_hash: str = "", filename: str = "") -> str:
    """Persist a holdings statement and its positions.

    Positions are keyed by (account, ISIN, folio, valuation date), so importing
    the same statement twice updates the holdings rather than doubling the
    portfolio - the failure this would otherwise cause is a net worth that
    grows every time you re-import.
    """
    statement_id = str(uuid.uuid4())
    status, gap, message = statement.reconcile()
    as_of = statement.as_of.isoformat() if statement.as_of else None

    with db.connection() as conn:
        if file_hash:
            existing = conn.execute(
                "SELECT id FROM portfolio_statements WHERE file_hash = ?",
                (file_hash,)).fetchone()
            if existing:
                statement_id = existing["id"]
                conn.execute("DELETE FROM holdings WHERE statement_id = ?",
                             (statement_id,))
                conn.execute("DELETE FROM portfolio_statements WHERE id = ?",
                             (statement_id,))

        conn.execute(
            "INSERT INTO portfolio_statements (id, account_id, layout, provider,"
            " as_of, declared_value, computed_value, recon_status,"
            " recon_discrepancy, recon_message, file_hash, source_filename)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (statement_id, account_id, statement.layout, statement.provider,
             as_of, _txt(statement.declared_value),
             _txt(statement.computed_value), status, _txt(gap), message,
             file_hash, filename),
        )
        for holding in statement.holdings:
            conn.execute(
                "INSERT INTO holdings (id, statement_id, account_id, isin,"
                " symbol, instrument, kind, folio, units, avg_cost, nav, value,"
                " invested, as_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (user_id, account_id, isin, folio, as_of) DO UPDATE SET"
                "   statement_id = excluded.statement_id,"
                "   units = excluded.units, nav = excluded.nav,"
                "   value = excluded.value, avg_cost = excluded.avg_cost,"
                "   invested = excluded.invested,"
                "   instrument = excluded.instrument, kind = excluded.kind",
                (str(uuid.uuid4()), statement_id, account_id, holding.isin,
                 holding.symbol, holding.instrument, holding.kind,
                 holding.folio, _txt(holding.units), _txt(holding.avg_cost),
                 _txt(holding.nav), _txt(holding.computed_value()),
                 _txt(holding.invested), as_of),
            )
    return statement_id


def get_holdings(db: Database, latest_only: bool = True) -> list[dict[str, Any]]:
    """Current positions.

    `latest_only` keeps one row per instrument - the most recent valuation.
    Without it a portfolio imported across several statement dates counts the
    same holding once per date, which is a net worth that multiplies with
    diligence.
    """
    with db.connection() as conn:
        if latest_only:
            # IS NOT DISTINCT FROM, not IS. SQLite spells null-safe equality
            # `a IS b`; PostgreSQL has no such operator and rejects the whole
            # statement as a syntax error, which is how the Portfolio tab came
            # to 500 after the migration. The null-safety is load-bearing:
            # account_id is nullable for a portfolio never matched to a ledger
            # account, and a plain `=` silently drops exactly those holdings.
            #
            # NULLIF guards the cast: `value` is nullable TEXT, and where
            # SQLite reads '' as 0.0, PostgreSQL raises. NULLS LAST restores
            # SQLite's ordering, which sorts NULL below every number on DESC
            # where PostgreSQL would put it on top.
            rows = conn.execute(
                "SELECT h.* FROM holdings h"
                " JOIN (SELECT account_id, isin, folio, MAX(as_of) AS latest"
                "       FROM holdings GROUP BY account_id, isin, folio) newest"
                "   ON h.account_id IS NOT DISTINCT FROM newest.account_id"
                "  AND h.isin = newest.isin AND h.folio = newest.folio"
                "  AND h.as_of IS NOT DISTINCT FROM newest.latest"
                " ORDER BY CAST(NULLIF(h.value, '') AS REAL) DESC NULLS LAST"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM holdings ORDER BY as_of DESC").fetchall()
    return [{
        "id": r["id"], "account_id": r["account_id"], "isin": r["isin"],
        "symbol": r["symbol"], "instrument": r["instrument"], "kind": r["kind"],
        "folio": r["folio"], "units": r["units"], "avg_cost": r["avg_cost"],
        "nav": r["nav"], "value": r["value"], "invested": r["invested"],
        "as_of": r["as_of"],
    } for r in rows]


def get_portfolio_statements(db: Database) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio_statements ORDER BY as_of DESC").fetchall()
    return [_row_dict(r) for r in rows]


# --------------------------------------------------------------------------
# Application settings
#
# Server-side rather than in the browser: these decide whether a model is
# called and money is spent, so the browser cannot be the authority on them.
# --------------------------------------------------------------------------

#: Every switch, with the value it takes when nobody has said otherwise.
#: `use_llm` defaults OFF: calling a model costs real money, and an app that
#: starts spending it because a default said so is not one you can trust.
SETTING_DEFAULTS: dict[str, Any] = {
    "use_llm": False,
}


def get_settings(db: Database) -> dict[str, Any]:
    with db.connection() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    out: dict[str, Any] = {}
    for key, default in SETTING_DEFAULTS.items():
        raw = stored.get(key)
        if raw is None:
            out[key] = default
        elif isinstance(default, bool):
            out[key] = raw == "1"
        else:
            out[key] = raw
    return out


def save_settings(db: Database, values: dict[str, Any]) -> dict[str, Any]:
    """Store only the keys this app knows about.

    Unknown keys are dropped rather than saved: a settings table that accepts
    anything becomes a place bugs hide, and there is no caller that needs it.
    """
    with db.connection() as conn:
        for key, value in values.items():
            if key not in SETTING_DEFAULTS:
                continue
            stored = "1" if value else "0" if isinstance(
                SETTING_DEFAULTS[key], bool) else str(value)
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at)"
                " VALUES (?, ?, datetime('now'))"
                " ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value,"
                "   updated_at = datetime('now')",
                (key, stored))
    return get_settings(db)
