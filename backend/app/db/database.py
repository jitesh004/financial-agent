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
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

#: FA_DB_PATH points the whole app at a different ledger. Set it to work
#: against a copy - trying a migration, reproducing a bug, demoing with sample
#: statements - without putting the real database anywhere near the attempt.
DEFAULT_DB_PATH = Path(
    os.environ.get("FA_DB_PATH")
    or Path(__file__).resolve().parents[3] / "data" / "financial_agent.db"
)

#: How many automatic snapshots to keep before pruning the oldest.
MAX_SNAPSHOTS = 10

#: Derived data: reproducible from the parsed ledger in seconds.
#: Job history sits here as the one thing that is not reproducible but is also
#: not precious - it is an operational log of work already done, and losing it
#: costs nothing but the trace. A job still in flight re-inserts its own row on
#: the next flush, so clearing this scope mid-run does not orphan it.
_TIER_DERIVED = ("transfer_pairs", "recurring_series", "analysis_runs",
                 "job_items", "jobs")

#: Parsed data: reproducible from the statement files, at the cost of CPU.
#: Rows read from transaction alert emails live here too. They are
#: reproducible in the same sense - by re-scanning the mailbox rather than
#: by re-reading a file - so clearing this scope drops them and a fresh
#: alert scan brings them back.
_TIER_PARSED = ("transactions", "statements", "holdings",
                "portfolio_statements", "bureau_accounts",
                "bureau_reports", "accounts")

#: The file registry. The files themselves live on disk and are handled
#: separately - see the storage module - because losing a manually uploaded
#: statement the user no longer has a copy of is unrecoverable.
_TIER_FILES = ("source_files",)

#: The staging area. Deliberately NOT part of `parsed_data`: clearing the
#: parsed ledger is how someone asks for a rebuild, and a rebuild needs the
#: staged set to rebuild FROM. Wiping both would turn "recompute this" into
#: "download and re-parse everything", which is minutes of work and a fresh
#: dependence on Gmail still having the mail.
_TIER_STAGING = ("staged_files",)

#: Bought with real money. Never cleared as a side effect of anything else.
_TIER_AI = ("ai_inferences", "merchant_categories")

#: Authored by a human. Cannot be regenerated from any input at any price.
#: `claims` is listed before `claim_settlements` and `transaction_splits`
#: only for readability - both cascade from it anyway.
#: `custom_categories` and `recurring_series_overrides` belong here as much as
#: any other decision - a category someone invented and a series they renamed
#: cannot be regenerated from any statement. Leaving them out of every scope
#: meant a factory reset silently left them behind, so the workspace did not
#: actually return to its first-run state.
#: A dashboard someone assembled is authored, not derived: no statement, no
#: re-parse and no amount of CPU brings back a board of questions they wrote
#: themselves. Widgets are listed before their parent for readability only -
#: they cascade from `dashboards` either way.
_TIER_DECISIONS = ("user_overrides", "claim_settlements", "transaction_splits",
                   "custom_categories", "recurring_series_overrides",
                   "claims", "split_rules", "settlement_group_legs",
                   "settlement_groups", "dashboard_widgets", "dashboards",
                   "app_settings")

_TIER_IDENTITY = ("user_profile",)

#: Each scope is cumulative over the cheaper tiers below it: re-parsing has to
#: drop the derived data built on top of the rows it is replacing, and clearing
#: the file registry has to drop the rows parsed out of those files, or the
#: ledger would keep transactions whose provenance no longer exists.
CLEAR_SCOPES: dict[str, tuple[str, ...]] = {
    "derived": _TIER_DERIVED,
    "parsed_data": _TIER_DERIVED + _TIER_PARSED,
    "files": _TIER_DERIVED + _TIER_PARSED + _TIER_STAGING + _TIER_FILES,
    # Its own scope, because it is the one thing a rebuild cannot do without.
    "staged_imports": _TIER_STAGING,
    # What Process data replaces. Everything derived from documents, and
    # nothing else - notably NOT `jobs`, which "parsed_data" includes as
    # operational log. A rebuild runs INSIDE a job, so clearing that scope
    # deleted the row of the job doing the clearing: it then ran to completion
    # against a row that no longer existed, and the screen watching it waited
    # forever for a status that was never going to be written.
    "rebuild": ("transfer_pairs", "recurring_series", "analysis_runs")
               + _TIER_PARSED,
    "ai_inferences": _TIER_AI,
    "decisions": _TIER_DECISIONS,
    "everything": (_TIER_DERIVED + _TIER_PARSED + _TIER_STAGING + _TIER_FILES
                   + _TIER_AI + _TIER_DECISIONS + _TIER_IDENTITY),
}

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
    -- The statement period this balance was read from. Without it, merging
    -- statements that arrive out of chronological order (Gmail search, a
    -- batch upload, a single-file retry) has no way to tell a fresher
    -- balance from a stale one, and "whichever file was processed first"
    -- silently won forever - which is how a savings account with real money
    -- in it ended up reporting a balance of zero.
    balance_as_of         TEXT,
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
    category_rule          TEXT NOT NULL DEFAULT '',
    direction_reason       TEXT NOT NULL DEFAULT '',
    is_internal_transfer   INTEGER NOT NULL DEFAULT 0,
    -- The duplicate leg of an internal move. Analytics counts exactly one leg
    -- of a transfer as a real cash movement; without this column every
    -- dashboard rebuilt after a restart silently recomputed with the flag
    -- reset to False, so the same ledger produced different figures depending
    -- on whether the server had restarted. See models/schemas.py for the full
    -- is_internal_transfer vs is_mirror_leg distinction.
    is_mirror_leg          INTEGER NOT NULL DEFAULT 0,
    transfer_pair_id       TEXT,
    recurring_series_id    TEXT,
    reference              TEXT,
    source_row             INTEGER,
    -- Content fingerprint: what user overrides, splits and claims key off.
    -- Deliberately NOT the id - re-parsing a statement mints fresh uuids, so
    -- anything keyed by id is lost on every reprocess.
    fingerprint            TEXT NOT NULL DEFAULT '',
    -- The accounting period this row belongs to (YYYY-MM), which is not always
    -- the calendar month of txn_date: a salary paid on the last working day
    -- lands on the 31st one month and the 1st of the month after next, which
    -- double-counts one month and empties another. Filled by analytics.periods.
    accounting_month       TEXT NOT NULL DEFAULT '',
    -- Set when automatic classification was not confident enough to decide
    -- alone. Never blocks a figure - the safe default is applied and the row
    -- is surfaced for confirmation.
    needs_review           INTEGER NOT NULL DEFAULT 0,
    review_reason          TEXT NOT NULL DEFAULT '',
    -- Which side of the books this row lands on. Empty until Workstream 2
    -- populates it; the column exists now so the accounting change does not
    -- need a second migration over the same table.
    flow_role              TEXT NOT NULL DEFAULT '',
    -- The user's own edits, denormalised onto the row so reading the ledger
    -- needs no join. user_overrides remains the durable copy: these two
    -- columns are wiped with the rest of the parsed data and re-applied from
    -- there on the next run.
    excluded               INTEGER NOT NULL DEFAULT 0,
    note                   TEXT NOT NULL DEFAULT ''
);

-- User-defined custom categories
CREATE TABLE IF NOT EXISTS custom_categories (
    name        TEXT PRIMARY KEY,
    color       TEXT NOT NULL DEFAULT '#6b7280',
    icon        TEXT NOT NULL DEFAULT 'Tag',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recurring_series_overrides (
    series_id   TEXT PRIMARY KEY,
    is_active   INTEGER,
    label       TEXT,
    category    TEXT,
    deleted     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(txn_date);
CREATE INDEX IF NOT EXISTS idx_txn_account  ON transactions(account_id, txn_date);
CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_txn_transfer ON transactions(transfer_pair_id);
-- Indexes over the columns added by _migrate are created there, not here.
-- This script runs BEFORE the migration, and on an existing database
-- CREATE TABLE IF NOT EXISTS is a no-op - so an index naming a
-- newly-added column would be asking for a column that does not exist yet.

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

-- ---------------------------------------------------------------------------
-- Tier 0: things a human decided. Cannot be recomputed from any input, so
-- these survive every clearing action except an explicit factory reset. Each
-- is keyed by a CONTENT FINGERPRINT rather than a transaction id, because
-- re-parsing a statement mints fresh uuids and would orphan every decision.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_overrides (
    fingerprint      TEXT PRIMARY KEY,
    -- The identity components are stored alongside the hash so a fingerprint
    -- whose inputs shifted (a description normaliser changed, an account was
    -- re-identified) can be found again on the looser key and re-pointed,
    -- instead of the decision silently vanishing.
    account_key      TEXT NOT NULL DEFAULT '',
    txn_date         TEXT NOT NULL DEFAULT '',
    amount           TEXT NOT NULL DEFAULT '',
    direction        TEXT NOT NULL DEFAULT '',
    desc_hash        TEXT NOT NULL DEFAULT '',
    -- Every field is nullable: an override carries only what the user actually
    -- changed, so a note does not silently pin a category too.
    category         TEXT,
    flow_role        TEXT,
    accounting_month TEXT,
    note             TEXT,
    excluded         INTEGER,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_overrides_loose
    ON user_overrides(txn_date, amount, direction, desc_hash);

-- ---------------------------------------------------------------------------
-- Tier 1: inference that cost real money. Cleared only by its own dedicated
-- action, never as a side effect of clearing parsed data.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ai_inferences (
    -- sha256(kind|input_hash). Deliberately NOT keyed on provider or model:
    -- the answer to "which bank issued this statement?" does not depend on
    -- who was asked, and re-billing for it after switching provider would
    -- defeat the point of the cache. Provider and model are recorded as
    -- metadata for auditability.
    cache_key   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    input_hash  TEXT NOT NULL,
    result_json TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    hit_count   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ai_kind ON ai_inferences(kind);

-- ---------------------------------------------------------------------------
-- Claims: expenses that were never really the user's.
--
-- Amount matching cannot solve this, and cash proves why. If someone repays a
-- card purchase in cash there is no ledger row anywhere, so no algorithm can
-- find it. And when one 62,000 card payment covers 50,000 that was somebody
-- else's plus 12,000 of the user's own spending, the figures never line up.
--
-- So the primary act is marking the PURCHASE - the thing the user knows for
-- certain - and repayment becomes a separate, optionally invisible event.
-- Tier 0: nothing can regenerate these.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS claims (
    id                 TEXT PRIMARY KEY,
    direction          TEXT NOT NULL,   -- owed_to_me | owed_by_me
    counterparty       TEXT NOT NULL DEFAULT '',
    -- The transaction that created the claim, by content fingerprint so it
    -- survives re-parsing the statement it came from.
    origin_fingerprint TEXT NOT NULL DEFAULT '',
    amount             TEXT NOT NULL,
    settled_amount     TEXT NOT NULL DEFAULT '0',
    status             TEXT NOT NULL DEFAULT 'open',  -- open|partial|settled|written_off
    -- accrual: the expense was never the user's, so it leaves the month the
    -- purchase happened in. cash: it counts until the money actually comes
    -- back, and the offset lands in the month of settlement.
    basis              TEXT NOT NULL DEFAULT 'accrual',
    opened_on          TEXT NOT NULL,
    closed_on          TEXT,
    note               TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_claims_origin ON claims(origin_fingerprint);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);

CREATE TABLE IF NOT EXISTS claim_settlements (
    id              TEXT PRIMARY KEY,
    claim_id        TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    -- cash and write_off have no ledger row at all, which is exactly why a
    -- claim must be closeable by hand rather than only by matching.
    method          TEXT NOT NULL,
    amount          TEXT NOT NULL,
    settled_on      TEXT NOT NULL,
    txn_fingerprint TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_claim_settlements_claim
    ON claim_settlements(claim_id);
CREATE INDEX IF NOT EXISTS idx_claim_settlements_txn
    ON claim_settlements(txn_fingerprint) WHERE txn_fingerprint != '';

-- One transaction divided into parts with different owners or categories: a
-- grocery bill that was half a flatmate's, a card purchase only partly the
-- user's. Invariant enforced on write: the parts sum to the parent.
CREATE TABLE IF NOT EXISTS transaction_splits (
    id                 TEXT PRIMARY KEY,
    parent_fingerprint TEXT NOT NULL,
    amount             TEXT NOT NULL,
    category           TEXT,
    flow_role          TEXT,
    claim_id           TEXT REFERENCES claims(id) ON DELETE SET NULL,
    note               TEXT NOT NULL DEFAULT '',
    position           INTEGER NOT NULL DEFAULT 0,
    -- The parent's own loose key (see pipeline.fingerprint), stored so a
    -- split survives its parent's strict fingerprint moving underneath it -
    -- the same recovery `user_overrides` gets, and for the same reason: an
    -- account being re-identified across a reprocess is routine, not a bug,
    -- and a split resolved by parent_fingerprint alone would simply stop
    -- applying the moment that happened.
    origin_date        TEXT NOT NULL DEFAULT '',
    origin_amount      TEXT NOT NULL DEFAULT '',
    origin_direction   TEXT NOT NULL DEFAULT '',
    origin_desc_hash   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_splits_parent
    ON transaction_splits(parent_fingerprint);

-- A recurring shared cost - rent split with a flatmate - as a rule rather
-- than a chore to repeat every month.
CREATE TABLE IF NOT EXISTS split_rules (
    id           TEXT PRIMARY KEY,
    label        TEXT NOT NULL DEFAULT '',
    match_text   TEXT NOT NULL,
    account_key  TEXT NOT NULL DEFAULT '',
    mine_pct     TEXT NOT NULL DEFAULT '100',
    counterparty TEXT NOT NULL DEFAULT '',
    category     TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Settlement groups the user has CONFIRMED.
--
-- Only confirmed ones are stored. Inferred groups are recomputed on every run
-- and held in memory - persisting a guess would make it look like a decision.
-- Storing the confirmations is what stops the app asking the same question
-- after every re-parse. Replaces transfer_pairs, which was written on every
-- run and never read by anything.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS settlement_groups (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL DEFAULT 'card_settlement',
    total_amount  TEXT NOT NULL DEFAULT '0',
    residual      TEXT NOT NULL DEFAULT '0',
    confidence    REAL NOT NULL DEFAULT 1.0,
    confirmed     INTEGER NOT NULL DEFAULT 1,
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settlement_group_legs (
    group_id    TEXT NOT NULL REFERENCES settlement_groups(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    side        TEXT NOT NULL,   -- outflow (funding) | inflow (settled)
    PRIMARY KEY (group_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_settlement_legs_fp
    ON settlement_group_legs(fingerprint);


-- ---------------------------------------------------------------------------
-- Explore: user-built dashboards.
--
-- A widget stores a QUERY, never a result. Storing computed figures would mean
-- a saved dashboard could disagree with the ledger it was built from the
-- moment a category is corrected or a statement re-parsed. Everything here is
-- re-executed against the live tables on open.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dashboards (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    -- The one that opens first. Enforced as at-most-one in the repository
    -- rather than by a constraint: SQLite has no partial unique constraint
    -- that survives an UPDATE swapping two rows.
    is_default   INTEGER NOT NULL DEFAULT 0,
    position     INTEGER NOT NULL DEFAULT 0,
    -- Board-level date range and filters, applied on top of every widget's
    -- own query so one control can re-cut the whole board.
    filters_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dashboard_widgets (
    id           TEXT PRIMARY KEY,
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    title        TEXT NOT NULL DEFAULT '',
    type         TEXT NOT NULL DEFAULT 'table',
    query_json   TEXT NOT NULL DEFAULT '{}',
    viz_json     TEXT NOT NULL DEFAULT '{}',
    position     INTEGER NOT NULL DEFAULT 0,
    -- Columns of a 12-wide grid, and height in 120px row units.
    width        INTEGER NOT NULL DEFAULT 6,
    height       INTEGER NOT NULL DEFAULT 2,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_widgets_dashboard
    ON dashboard_widgets(dashboard_id, position);


-- ---------------------------------------------------------------------------
-- Background jobs.
--
-- Scanning a mailbox and parsing a few hundred statements takes minutes, and
-- the progress of that work used to live in a process-local dict. Closing the
-- browser was survivable; restarting the API was not, and a job in flight
-- simply vanished - the UI had a job id that answered 404 and no way to tell
-- "finished" from "never happened".
--
-- `request_json` is what the job was asked to do. It is the difference between
-- reporting that a job was interrupted and being able to pick it up again.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    -- queued | running | complete | failed | cancelled | interrupted
    status        TEXT NOT NULL DEFAULT 'queued',
    phase         TEXT NOT NULL DEFAULT '',
    current       INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    message       TEXT NOT NULL DEFAULT '',
    started_at    REAL NOT NULL DEFAULT 0,
    finished_at   REAL,
    result_json   TEXT NOT NULL DEFAULT 'null',
    request_json  TEXT NOT NULL DEFAULT 'null',
    errors_json   TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs(started_at DESC);


-- ---------------------------------------------------------------------------
-- Application settings: a handful of switches the user owns.
--
-- Key/value rather than columns, because these are preferences rather than
-- data: a new switch should not need a migration. Kept in the database and not
-- in localStorage because they change what the SERVER does - whether a model
-- is called and money is spent - so the browser cannot be the authority.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- The staging area.
--
-- Everything a scan or an upload produces lands here FIRST, and nothing else
-- in this schema reads it. That is the whole point: parsing a file used to be
-- the same act as counting it, so a bad parse was already inside every total
-- by the time anyone saw it. Now parsing fills this table, the ledger is built
-- from it only when someone presses Process data, and what gets built is
-- exactly the set of rows ticked here.
--
-- Identity is the file's content hash, so re-scanning a mailbox recognises
-- what it has already read and re-parses nothing. A statement that is deleted
-- and re-downloaded is the same entry, with the same selection.
--
-- `payload` holds the parse result verbatim. Storing it means Process data
-- never re-reads a PDF, and the Review screen can summarise a file without
-- deserialising anything - the summary columns beside it are enough.
CREATE TABLE IF NOT EXISTS staged_files (
    id            TEXT PRIMARY KEY,
    file_hash     TEXT NOT NULL UNIQUE,
    filename      TEXT NOT NULL,
    origin        TEXT NOT NULL DEFAULT 'gmail',
    -- Which scan turned this up, as opposed to what reading it revealed.
    -- `kind` is the answer ('this is a portfolio'); `scan_intent` is the
    -- question ('found while looking for investments'). The wizard needs
    -- the question, because Choose and Parse show you what each scan
    -- brought back - before anything has been read and can say what it is.
    scan_intent   TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'statement',
    path          TEXT,
    message_id    TEXT,
    sender        TEXT NOT NULL DEFAULT '',
    subject       TEXT NOT NULL DEFAULT '',
    selected      INTEGER NOT NULL DEFAULT 1,
    -- The staged statement that makes this entry redundant. Set on alerts once
    -- a statement covering the same account and date arrives; such a row is
    -- shown struck through rather than deleted, so the supersession is visible
    -- rather than something that silently happened.
    superseded_by TEXT REFERENCES staged_files(id) ON DELETE SET NULL,
    parse_status  TEXT NOT NULL DEFAULT 'pending',
    parse_message TEXT NOT NULL DEFAULT '',
    parsed_at     TEXT,
    account_label TEXT NOT NULL DEFAULT '',
    account_key   TEXT NOT NULL DEFAULT '',
    account_type  TEXT NOT NULL DEFAULT '',
    period_start  TEXT,
    period_end    TEXT,
    row_count     INTEGER NOT NULL DEFAULT 0,
    debits        TEXT NOT NULL DEFAULT '0',
    credits       TEXT NOT NULL DEFAULT '0',
    recon_status  TEXT NOT NULL DEFAULT '',
    warnings      TEXT NOT NULL DEFAULT '[]',
    payload       TEXT NOT NULL DEFAULT '{}',
    added_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_staged_kind ON staged_files(kind, selected);
CREATE INDEX IF NOT EXISTS idx_staged_account ON staged_files(account_key);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Credit bureau reports.
--
-- A bureau report is not a statement: no opening balance, no rows, nothing to
-- reconcile. It is an independent account of what the user owes, which makes
-- it the one source that can reveal an account the ledger has never seen - a
-- card whose statements never arrive by email is invisible here until a bureau
-- names it.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bureau_reports (
    id              TEXT PRIMARY KEY,
    bureau          TEXT NOT NULL,          -- cibil | crif | experian | equifax
    score           INTEGER,
    score_band      TEXT NOT NULL DEFAULT '',
    pulled_on       TEXT,
    holder_name     TEXT NOT NULL DEFAULT '',
    file_hash       TEXT NOT NULL DEFAULT '',
    source_filename TEXT NOT NULL DEFAULT '',
    warnings        TEXT NOT NULL DEFAULT '[]',
    ingested_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bureau_hash
    ON bureau_reports(file_hash) WHERE file_hash != '';

CREATE TABLE IF NOT EXISTS bureau_accounts (
    id            TEXT PRIMARY KEY,
    report_id     TEXT NOT NULL REFERENCES bureau_reports(id) ON DELETE CASCADE,
    lender        TEXT NOT NULL DEFAULT '',
    -- Normalised lender name ("HDFC BANK LTD" -> "hdfc"). Bureaus and
    -- statements spell the same institution differently, and matching on the
    -- printed name alone finds almost nothing.
    lender_key    TEXT NOT NULL DEFAULT '',
    account_type  TEXT NOT NULL DEFAULT 'unknown',
    account_number_masked TEXT NOT NULL DEFAULT '',
    -- Last four digits: the only part of an account number that survives both
    -- a bureau's masking and a statement's.
    number_suffix TEXT NOT NULL DEFAULT '',
    ownership     TEXT NOT NULL DEFAULT '',
    opened_on     TEXT,
    closed_on     TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    sanctioned    TEXT,
    current_balance TEXT,
    overdue       TEXT,
    credit_limit  TEXT,
    emi_amount    TEXT,
    dpd_history   TEXT NOT NULL DEFAULT '[]',
    worst_dpd     INTEGER NOT NULL DEFAULT 0,
    -- The ledger account this was matched to, once someone agreed to it.
    account_id    TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    -- unmatched | auto | suggested | confirmed | rejected
    match_status  TEXT NOT NULL DEFAULT 'unmatched',
    match_confidence REAL NOT NULL DEFAULT 0.0,
    match_reason  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_bureau_accounts_report
    ON bureau_accounts(report_id);
CREATE INDEX IF NOT EXISTS idx_bureau_accounts_match
    ON bureau_accounts(account_id);


-- ---------------------------------------------------------------------------
-- Investment holdings.
--
-- A portfolio statement declares a total, and units x NAV has to reproduce it.
-- That is the same reconciliation gate the bank statements go through, applied
-- to the one number a broker prints that can be checked against its own rows.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS portfolio_statements (
    id              TEXT PRIMARY KEY,
    account_id      TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    layout          TEXT NOT NULL DEFAULT '',   -- cas | cams | kfintech | broker
    provider        TEXT NOT NULL DEFAULT '',
    as_of           TEXT,
    declared_value  TEXT,
    computed_value  TEXT,
    recon_status    TEXT NOT NULL DEFAULT 'not_applicable',
    recon_discrepancy TEXT,
    recon_message   TEXT NOT NULL DEFAULT '',
    file_hash       TEXT NOT NULL DEFAULT '',
    source_filename TEXT NOT NULL DEFAULT '',
    ingested_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_hash
    ON portfolio_statements(file_hash) WHERE file_hash != '';

CREATE TABLE IF NOT EXISTS holdings (
    id           TEXT PRIMARY KEY,
    statement_id TEXT REFERENCES portfolio_statements(id) ON DELETE CASCADE,
    account_id   TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    isin         TEXT NOT NULL DEFAULT '',
    symbol       TEXT NOT NULL DEFAULT '',
    instrument   TEXT NOT NULL DEFAULT '',
    -- equity | mutual_fund | etf | bond | other
    kind         TEXT NOT NULL DEFAULT 'equity',
    folio        TEXT NOT NULL DEFAULT '',
    units        TEXT,
    avg_cost     TEXT,
    nav          TEXT,
    value        TEXT,
    invested     TEXT,
    as_of        TEXT,
    -- One row per instrument per folio per valuation date: re-importing the
    -- same statement updates the position rather than adding a second copy.
    UNIQUE (account_id, isin, folio, as_of)
);

CREATE INDEX IF NOT EXISTS idx_holdings_asof ON holdings(as_of DESC);
CREATE INDEX IF NOT EXISTS idx_holdings_statement ON holdings(statement_id);


CREATE TABLE IF NOT EXISTS job_items (
    job_id   TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    seq      INTEGER NOT NULL,
    name     TEXT NOT NULL,
    -- A stable identifier for the unit of work, so resuming can tell which
    -- items are already done. Names are not unique - two banks both send
    -- "statement.pdf" - and resuming on a name would skip real work.
    key      TEXT NOT NULL DEFAULT '',
    status   TEXT NOT NULL DEFAULT 'pending',
    detail   TEXT NOT NULL DEFAULT '',
    cached   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, seq)
);
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
            ("accounts", (("current_balance", "TEXT"), ("product_name", "TEXT"),
                         ("balance_as_of", "TEXT"))),
            ("user_profile", (("excluded_senders", "TEXT NOT NULL DEFAULT '[]'"),)),
            ("staged_files", (("scan_intent", "TEXT NOT NULL DEFAULT ''"),)),
            ("source_files", (("period_hint", "TEXT"),)),
            ("transactions", (
                ("is_mirror_leg", "INTEGER NOT NULL DEFAULT 0"),
                ("fingerprint", "TEXT NOT NULL DEFAULT ''"),
                ("accounting_month", "TEXT NOT NULL DEFAULT ''"),
                ("needs_review", "INTEGER NOT NULL DEFAULT 0"),
                ("review_reason", "TEXT NOT NULL DEFAULT ''"),
                ("flow_role", "TEXT NOT NULL DEFAULT ''"),
                ("excluded", "INTEGER NOT NULL DEFAULT 0"),
                ("note", "TEXT NOT NULL DEFAULT ''"),
                # Where the row came from. An email alert is a real
                # transaction but an unreconciled one, and no total can be
                # trusted that cannot tell the two apart.
                ("source", "TEXT NOT NULL DEFAULT 'statement'"),
                # Set when the statement covering this row arrived later and
                # replaced it. Kept rather than deleted, so the alert that
                # arrived first stays auditable.
                ("superseded", "INTEGER NOT NULL DEFAULT 0"),
                # WHICH rule decided the category. `category_source` already
                # said a rule did it; this says which one, so "why is this
                # Dining?" has an answer the user can read. The categorizer
                # has always computed it and always threw it away.
                ("category_rule", "TEXT NOT NULL DEFAULT ''"),
                # Why the row is money in or money out. Direction is the
                # one field whose mistake lands on both sides of every
                # total at once, so it carries its reasoning too.
                ("direction_reason", "TEXT NOT NULL DEFAULT ''"),
            )),
            ("transaction_splits", (
                ("origin_date", "TEXT NOT NULL DEFAULT ''"),
                ("origin_amount", "TEXT NOT NULL DEFAULT ''"),
                ("origin_direction", "TEXT NOT NULL DEFAULT ''"),
                ("origin_desc_hash", "TEXT NOT NULL DEFAULT ''"),
            )),
        ):
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            for column, ddl in columns:
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                    log.info("migrated %s: added %s", table, column)

        # Indexes over the columns just added. These live here rather than in
        # SCHEMA because SCHEMA is executed before this runs, and on an
        # existing database its CREATE TABLE is a no-op - so an index there
        # would reference a column that does not exist yet and abort the whole
        # script. By this point the ALTERs above guarantee the columns exist.
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_txn_fingerprint ON transactions(fingerprint)",
            "CREATE INDEX IF NOT EXISTS idx_txn_acct_month ON transactions(accounting_month)",
            "CREATE INDEX IF NOT EXISTS idx_txn_review ON transactions(needs_review) "
            "WHERE needs_review = 1",
            "CREATE INDEX IF NOT EXISTS idx_txn_source ON transactions(source)",
        ):
            conn.execute(stmt)

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
                        balance_as_of         TEXT,
                        principal_outstanding TEXT,
                        interest_rate         TEXT,
                        emi_amount            TEXT,
                        tenure_months_remaining INTEGER,
                        credit_limit          TEXT,
                        created_at            TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE (institution, account_type, account_number_masked, product_name)
                    );
                    INSERT INTO accounts_new (id, institution, account_type, account_number_masked,
                        product_name, holder_name, currency, current_balance, balance_as_of,
                        principal_outstanding, interest_rate, emi_amount,
                        tenure_months_remaining, credit_limit, created_at)
                    SELECT id, institution, account_type, account_number_masked,
                        product_name, holder_name, currency, current_balance,
                        -- balance_as_of postdates this migration, so a database still
                        -- taking this path never had the column to carry over.
                        NULL,
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

    def snapshot(self, label: str = "") -> Path:
        """Copy the whole database to a timestamped file and return its path.

        Every destructive action takes one of these first. The entire database
        is a single file, so a byte copy is a complete, consistent backup for
        almost no code - which is what makes "nothing is ever lost" a promise
        the app can actually keep rather than an aspiration.
        """
        backups = self.path.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = f".{label}" if label else ""
        target = backups / f"{self.path.stem}{suffix}.{stamp}.db"

        with self.connection() as conn:
            # sqlite3's own backup API rather than a file copy: it takes a
            # read lock and captures a consistent image even if a WAL write is
            # in flight, where copying the file alone could miss the -wal.
            #
            # Closed explicitly, not via `with`: sqlite3's context manager
            # commits the transaction but does NOT close the connection, so
            # the destination stayed open and held a lock on the file it had
            # just written. On Windows that made every snapshot undeletable,
            # and pruning failed silently for all of them.
            dest = sqlite3.connect(target)
            try:
                conn.backup(dest)
            finally:
                dest.close()

        self._prune_snapshots(backups)
        log.info("snapshot written: %s", target.name)
        return target

    def _prune_snapshots(self, backups: Path, keep: int = MAX_SNAPSHOTS) -> None:
        """Keep the newest `keep` automatic snapshots; never touch anything else.

        Only files this class created are considered - a backup the user made
        by hand, under any other name, is not ours to delete.
        """
        ours = sorted(
            backups.glob(f"{self.path.stem}*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in ours[keep:]:
            try:
                stale.unlink()
            except OSError:  # a locked or vanished file is not worth failing over
                log.warning("could not prune snapshot %s", stale.name)

    def list_snapshots(self) -> list[dict[str, object]]:
        backups = self.path.parent / "backups"
        if not backups.is_dir():
            return []
        out = []
        for p in sorted(backups.glob(f"{self.path.stem}*.db"),
                        key=lambda p: p.stat().st_mtime, reverse=True):
            stat = p.stat()
            out.append({
                "name": p.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
        return out

    def restore(self, name: str) -> None:
        """Replace the live database with a named snapshot.

        Snapshots the current state first, so restoring is itself undoable and
        cannot be the thing that loses data.
        """
        backups = self.path.parent / "backups"
        source = backups / name
        # Resolved and re-checked rather than trusting the name: this value
        # arrives from an HTTP request, and "../../something.db" would
        # otherwise let a caller read or clobber a file outside the folder.
        if source.resolve().parent != backups.resolve() or not source.is_file():
            raise ValueError(f"no such snapshot: {name}")

        self.snapshot("pre-restore")
        # Closed explicitly - see the note in `snapshot` about sqlite3's
        # context manager not closing the connection.
        src = sqlite3.connect(source)
        try:
            with self.connection() as conn:
                src.backup(conn)
        finally:
            src.close()
        log.info("restored from snapshot: %s", name)

    def clear(self, scope: str) -> dict[str, int]:
        """Delete exactly one tier of data, and report what went.

        Replaces the old single `reset()`. The tiers are ordered by what it
        costs to get the data back: parsed rows are pure CPU and can be
        rebuilt from the files, downloaded files cost network and Gmail quota
        (and are irreplaceable if the user no longer has the original), AI
        inference costs actual money, and a human decision cannot be
        regenerated at all. A single "delete everything" button cannot express
        that difference, which is how expensive data ends up being destroyed
        to fix a cheap problem.
        """
        if scope not in CLEAR_SCOPES:
            raise ValueError(
                f"unknown scope {scope!r}. Valid: {', '.join(CLEAR_SCOPES)}")

        tables = CLEAR_SCOPES[scope]
        removed: dict[str, int] = {}
        with self.connection() as conn:
            for table in tables:
                before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                conn.execute(f"DELETE FROM {table}")
                if before:
                    removed[table] = before
            
            # If we wipe the ledger, the source_files that generated it should
            # Revert ALL file statuses to 'pending' so the file grid reflects
            # reality after a ledger clear. Failed / needs_password files get
            # reset too - clearing the ledger is a fresh start, and the user
            # should be able to see which files still need attention on the
            # next parse pass.
            if scope in ("parsed_data", "everything"):
                conn.execute("""
                    UPDATE source_files 
                    SET parse_status = 'pending', 
                        transaction_count = 0, 
                        error_message = '',
                        account_id = NULL,
                        statement_id = NULL
                    WHERE parse_status != 'pending'
                """)
        log.info("cleared scope=%s removed=%s", scope, removed)
        return removed

    def reset(self) -> None:
        """Clear parsed data, leaving files, AI inference and decisions intact.

        Kept because the test suite and several internal callers rely on it to
        get a clean ledger. It is deliberately NOT the widest scope any more -
        wiping a user's downloaded statements and paid-for inference is not
        something any caller should get by accident.
        """
        self.clear("parsed_data")


_db: Database | None = None


def get_db(path: Path | str | None = None) -> Database:
    """Process-wide singleton, created on first use."""
    global _db
    if _db is None or path is not None:
        _db = Database(path)
    return _db
