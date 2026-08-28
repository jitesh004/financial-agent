"""SQLite persistence.

Plain sqlite3 rather than an ORM. The queries here are analytical (group-by,
window functions over a ledger), which is exactly what SQL is good at and what
an ORM makes harder to read. It also keeps the whole app dependency-light and
genuinely local - the user's financial data never leaves their machine.

Two SQLite specifics that matter for a financial ledger:

  - Money is stored as TEXT, not REAL. SQLite's REAL is an IEEE double, and
    round-tripping 0.1 through it is how a reconciled statement quietly stops
    reconciling. Decimal <-> TEXT is lossless.
  - WAL mode is enabled so the API can read while an ingestion job writes.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "financial_agent.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id                    TEXT PRIMARY KEY,
    institution           TEXT NOT NULL DEFAULT 'Unknown',
    account_type          TEXT NOT NULL DEFAULT 'unknown',
    account_number_masked TEXT NOT NULL DEFAULT '',
    -- The card's own product name ("Rewards", "Regalia"). Part of the unique
    -- key: an issuer that masks its card number so completely no digit
    -- survives extraction (HSBC) still needs two different cards to coexist
    -- as two accounts, distinguished by this instead.
    product_name          TEXT NOT NULL DEFAULT '',
    holder_name           TEXT,
    currency              TEXT NOT NULL DEFAULT 'INR',
    current_balance       TEXT,
    principal_outstanding TEXT,
    interest_rate         TEXT,
    emi_amount            TEXT,
    tenure_months_remaining INTEGER,
    credit_limit          TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (institution, account_type, account_number_masked, product_name)
);

CREATE TABLE IF NOT EXISTS statements (
    id               TEXT PRIMARY KEY,
    account_id       TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    source_filename  TEXT NOT NULL,
    source_format    TEXT NOT NULL DEFAULT 'unknown',
    file_hash        TEXT NOT NULL DEFAULT '',
    period_start     TEXT,
    period_end       TEXT,
    opening_balance  TEXT,
    closing_balance  TEXT,
    extractor_used   TEXT NOT NULL DEFAULT '',
    recon_status     TEXT NOT NULL DEFAULT 'not_applicable',
    recon_discrepancy TEXT,
    recon_message    TEXT NOT NULL DEFAULT '',
    parse_warnings   TEXT NOT NULL DEFAULT '[]',
    ingested_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The same file re-uploaded must not create a second copy of every row.
CREATE UNIQUE INDEX IF NOT EXISTS idx_statements_hash
    ON statements(file_hash) WHERE file_hash != '';

CREATE TABLE IF NOT EXISTS transactions (
    id                     TEXT PRIMARY KEY,
    account_id             TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    statement_id           TEXT REFERENCES statements(id) ON DELETE CASCADE,
    txn_date               TEXT NOT NULL,
    value_date             TEXT,
    raw_description        TEXT NOT NULL,
    normalized_description TEXT NOT NULL DEFAULT '',
    merchant               TEXT,
    amount                 TEXT NOT NULL,
    direction              TEXT NOT NULL,
    balance_after          TEXT,
    currency               TEXT NOT NULL DEFAULT 'INR',
    category               TEXT NOT NULL DEFAULT 'uncategorized',
    category_source        TEXT NOT NULL DEFAULT 'default',
    category_confidence    REAL NOT NULL DEFAULT 0.0,
    is_internal_transfer   INTEGER NOT NULL DEFAULT 0,
    transfer_pair_id       TEXT,
    recurring_series_id    TEXT,
    reference              TEXT,
    source_row             INTEGER
);

CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(txn_date);
CREATE INDEX IF NOT EXISTS idx_txn_account  ON transactions(account_id, txn_date);
CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_txn_transfer ON transactions(transfer_pair_id);

-- Learned merchant -> category map. This is what makes categorization get
-- cheaper and more deterministic the longer the app is used: an LLM decision
-- made once is reused forever, and a user correction overrides it permanently.
CREATE TABLE IF NOT EXISTS merchant_categories (
    merchant_key TEXT PRIMARY KEY,
    category     TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'llm',
    confidence   REAL NOT NULL DEFAULT 0.5,
    hit_count    INTEGER NOT NULL DEFAULT 1,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transfer_pairs (
    pair_id        TEXT PRIMARY KEY,
    debit_txn_id   TEXT,
    credit_txn_id  TEXT,
    amount         TEXT NOT NULL,
    day_gap        INTEGER NOT NULL DEFAULT 0,
    kind           TEXT NOT NULL DEFAULT 'self_transfer',
    confidence     REAL NOT NULL DEFAULT 0.0
);

-- Detected recurring series (salary, EMIs, subscriptions). Persisted because
-- the forecast layer needs them and recomputing over years of rows is wasteful.
CREATE TABLE IF NOT EXISTS recurring_series (
    id             TEXT PRIMARY KEY,
    account_id     TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    label          TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT 'uncategorized',
    direction      TEXT NOT NULL,
    median_amount  TEXT NOT NULL,
    cadence_days   INTEGER NOT NULL DEFAULT 30,
    occurrences    INTEGER NOT NULL DEFAULT 0,
    first_seen     TEXT,
    last_seen      TEXT,
    next_expected  TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1,
    confidence     REAL NOT NULL DEFAULT 0.0
);

-- The user's own identity details, for password derivation and account
-- matching. A single row (id = 'me'). PAN and DOB are sensitive, so this table
-- is the one thing the LLM layer is never allowed to read from.
CREATE TABLE IF NOT EXISTS user_profile (
    id            TEXT PRIMARY KEY DEFAULT 'me',
    full_name     TEXT NOT NULL DEFAULT '',
    date_of_birth TEXT,
    pan           TEXT NOT NULL DEFAULT '',
    mobile        TEXT NOT NULL DEFAULT '',
    custom_passwords TEXT NOT NULL DEFAULT '[]',
    excluded_senders TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    status       TEXT NOT NULL DEFAULT 'running',
    file_count   INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT,
    error        TEXT
);

-- Every file the app has ever attempted, regardless of outcome. A failed or
-- locked file is not persisted anywhere else - the statements/transactions
-- tables only ever hold successes - so without this table there is nothing to
-- show in a "which files parsed and which didn't" view, and nothing to retry.
CREATE TABLE IF NOT EXISTS source_files (
    id                 TEXT PRIMARY KEY,
    filename           TEXT NOT NULL,
    filepath           TEXT NOT NULL DEFAULT '',
    file_hash          TEXT NOT NULL DEFAULT '',
    source             TEXT NOT NULL DEFAULT 'upload',
    sender             TEXT NOT NULL DEFAULT '',
    message_id         TEXT NOT NULL DEFAULT '',
    size_bytes         INTEGER,
    -- The password that actually opened this file, in plain text. Consistent
    -- with the rest of this app's PII policy (see models/profile.py): stored
    -- only in the local SQLite file, used only to open the user's own files,
    -- never logged in full or sent anywhere. Storing it is what lets a later
    -- load skip password-guessing entirely and open the file on the first try.
    password           TEXT,
    password_status    TEXT NOT NULL DEFAULT 'unknown',
    parse_status       TEXT NOT NULL DEFAULT 'pending',
    institution_guess  TEXT NOT NULL DEFAULT '',
    account_type_guess TEXT NOT NULL DEFAULT '',
    account_id         TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    statement_id       TEXT REFERENCES statements(id) ON DELETE SET NULL,
    transaction_count  INTEGER NOT NULL DEFAULT 0,
    error_message      TEXT NOT NULL DEFAULT '',
    -- Which calendar month (YYYY-MM) this file is believed to be a statement
    -- FOR. A parsed file gets this precisely, from its own declared period. A
    -- failed or locked file has no parsed period to read - this is the only
    -- way to place it in the coverage grid at all, so it is filled in from
    -- the filename (a date embedded in the name) or the email's own date as a
    -- fallback, and left NULL only when neither is available.
    period_hint        TEXT,
    first_seen_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_attempted_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_files_hash
    ON source_files(file_hash) WHERE file_hash != '';
"""


def _adapt_decimal(value: Decimal) -> str:
    return str(value)


sqlite3.register_adapter(Decimal, _adapt_decimal)


class Database:
    """Thin connection manager. One instance per process."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets the API serve reads while an ingestion job holds a write lock.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn) -> None:
        """Add columns introduced after a database was first created.

        CREATE TABLE IF NOT EXISTS silently does nothing for an existing table,
        so new columns need an explicit ALTER. Kept idempotent by checking the
        current column list rather than catching the duplicate-column error.
        """
        for table, columns in (
            ("accounts", (("current_balance", "TEXT"), ("product_name", "TEXT"))),
            ("user_profile", (("excluded_senders", "TEXT NOT NULL DEFAULT '[]'"),)),
            ("source_files", (("period_hint", "TEXT"),)),
        ):
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            for column, ddl in columns:
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                    log.info("migrated %s: added %s", table, column)

        # A table-level UNIQUE constraint can't be altered in place in SQLite -
        # the only way to change it is to rebuild the table. The original
        # constraint didn't include product_name, so two cards from the same
        # bank that share no extractable account number (HSBC masks its
        # number so completely no digit survives extraction) could never
        # coexist: the second one violated the unique index and the insert
        # itself would fail outright.
        old_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='accounts'"
        ).fetchone()
        # Checked as an exact match, not a substring: the FIXED constraint text
        # contains the broken one as a prefix ("...account_number_masked)" is a
        # substring of "...account_number_masked, product_name)"), so a naive
        # `in` check would see this as still-broken forever and rebuild the
        # table on every single startup.
        if old_sql and old_sql["sql"] and (
            "UNIQUE (institution, account_type, account_number_masked)" in old_sql["sql"]
            and "product_name)" not in old_sql["sql"].split("UNIQUE")[-1]
        ):
            # Built under a temporary name and swapped in at the end, rather
            # than renaming `accounts` itself away first: statements and
            # source_files hold `REFERENCES accounts(id)`, and SQLite rewrites
            # a foreign key's target automatically when the table it points at
            # gets renamed - so renaming `accounts` to `accounts_old` first
            # left those other tables silently pointing at "accounts_old",
            # which then vanished under the final DROP, breaking every insert
            # into them with "no such table: accounts_old". Nothing named
            # `accounts` is ever renamed away while other tables reference it.
            #
            # Foreign keys are disabled for the duration regardless: DROP TABLE
            # counts as deleting every row for cascade purposes, so with FKs
            # still on, `source_files.account_id ... ON DELETE SET NULL` fired
            # the moment the old table was dropped and permanently wiped every
            # link to a real account - restoring a table with the same ids
            # afterward does not undo an already-applied SET NULL. This is
            # SQLite's own documented procedure for changing a table-level
            # constraint (see "Making Other Kinds Of Table Schema Changes" in
            # the ALTER TABLE docs).
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.executescript("""
                    CREATE TABLE accounts_new (
                        id                    TEXT PRIMARY KEY,
                        institution           TEXT NOT NULL DEFAULT 'Unknown',
                        account_type          TEXT NOT NULL DEFAULT 'unknown',
                        account_number_masked TEXT NOT NULL DEFAULT '',
                        product_name          TEXT,
                        holder_name           TEXT,
                        currency              TEXT NOT NULL DEFAULT 'INR',
                        current_balance       TEXT,
                        principal_outstanding TEXT,
                        interest_rate         TEXT,
                        emi_amount            TEXT,
                        tenure_months_remaining INTEGER,
                        credit_limit          TEXT,
                        created_at            TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE (institution, account_type, account_number_masked, product_name)
                    );
                    INSERT INTO accounts_new (id, institution, account_type, account_number_masked,
                        product_name, holder_name, currency, current_balance,
                        principal_outstanding, interest_rate, emi_amount,
                        tenure_months_remaining, credit_limit, created_at)
                    SELECT id, institution, account_type, account_number_masked,
                        product_name, holder_name, currency, current_balance,
                        principal_outstanding, interest_rate, emi_amount,
                        tenure_months_remaining, credit_limit, created_at
                    FROM accounts;
                    DROP TABLE accounts;
                    ALTER TABLE accounts_new RENAME TO accounts;
                """)
                bad = conn.execute("PRAGMA foreign_key_check").fetchall()
                if bad:
                    raise RuntimeError(f"accounts migration broke foreign keys: {bad}")
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
            log.info("migrated accounts: rebuilt UNIQUE constraint to include product_name")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Transactional connection. Commits on success, rolls back on error."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reset(self) -> None:
        """Drop all data. Used by tests and the 'start over' action in the UI."""
        with self.connection() as conn:
            for table in ("transactions", "statements", "transfer_pairs",
                          "recurring_series", "analysis_runs", "accounts",
                          "source_files"):
                conn.execute(f"DELETE FROM {table}")
        log.info("database reset: %s", self.path)


_db: Database | None = None


def get_db(path: Path | str | None = None) -> Database:
    """Process-wide singleton, created on first use."""
    global _db
    if _db is None or path is not None:
        _db = Database(path)
    return _db
