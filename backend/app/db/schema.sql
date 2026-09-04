-- PostgreSQL schema for the financial agent.
--
-- Ported from the original SQLite schema, which lived as a string in
-- database.py. Three things changed in the move and nothing else did:
--
--   1. Every table that holds a user's data carries `user_id`, defaulted from
--      the current tenant and protected by a row-level security policy.
--      db/engine.py explains how that is enforced; the policies themselves
--      are applied by database.py from ONE list, so a new table cannot be
--      added with the guard accidentally left off.
--   2. Identity keys are per-user. Two people can both hold a category named
--      "travel", both upload the same statement PDF, and both have a card
--      masked ****4412, without colliding.
--   3. Types: REAL -> DOUBLE PRECISION, and datetime('now') -> fa_now().
--
-- Money is still TEXT holding a decimal. That is not an oversight in the port:
-- the whole ledger is built on Decimal <-> TEXT being lossless, every figure
-- in the app is computed in Python's Decimal or as integer paise inside SQL
-- (see analytics/query.py), and the arithmetic ties out to the rupee today.
-- Retyping every money column as part of a driver migration would put that
-- guarantee at risk for no gain the queries can use - `CAST(amount AS REAL)`
-- reads the same either way.
--
-- Dates are likewise still ISO-8601 TEXT. They are compared, grouped and
-- truncated lexicographically throughout analytics, which is exactly what
-- 'YYYY-MM-DD' text supports.

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

-- The tenant for the current transaction, or NULL when none was bound.
-- NULL is deliberate: `user_id = NULL` is NULL, never true, so a query issued
-- without a signed-in user matches no rows instead of all of them.
CREATE OR REPLACE FUNCTION current_tenant() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;

-- What SQLite's datetime('now') returned, to the character: UTC, no timezone
-- suffix, second resolution. Kept identical so timestamps written before the
-- migration and after it sort against each other.
CREATE OR REPLACE FUNCTION fa_now() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS') $$;


-- ---------------------------------------------------------------------------
-- Identity
--
-- These four tables are the only ones NOT under row-level security, because
-- they are what the request is authenticated against - they have to be
-- readable before there is a tenant to read them as. Nothing here is another
-- user's financial data; the sensitive material (the Gmail refresh token) is
-- reachable only by primary key, and only the auth layer touches these tables.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Google's stable subject identifier. The email is what people recognise,
    -- but it is NOT the key: Google Workspace addresses get renamed, and
    -- keying on one would either lock someone out of their own ledger or -
    -- worse - hand a recycled address the previous holder's data. `sub` never
    -- changes and is never reissued.
    google_sub      TEXT NOT NULL UNIQUE,
    email           TEXT NOT NULL,
    email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    name            TEXT NOT NULL DEFAULT '',
    picture         TEXT NOT NULL DEFAULT '',
    -- active | disabled. A disabled user keeps their data and cannot sign in.
    status          TEXT NOT NULL DEFAULT 'active',
    -- Where the onboarding wizard should resume. See auth/onboarding.py for
    -- the ordered list of steps; NULL onboarded_at means it has not finished.
    onboarding_step TEXT NOT NULL DEFAULT 'identity',
    onboarded_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT fa_now(),
    last_seen_at    TEXT NOT NULL DEFAULT fa_now(),
    -- A demo workspace: an ordinary account holding generated statements,
    -- owned by whoever is demonstrating the app. Set on the DEMO row and
    -- points at the real one. A separate account rather than a flag on the
    -- rows, because every screen, query and security policy already works
    -- per account - so pointing the tenant at another one needs no special
    -- case anywhere, and nothing done during a demo can reach the real
    -- ledger. See app/demo.py.
    demo_of         UUID REFERENCES users(id) ON DELETE CASCADE,
    -- Set on the REAL row: show me my demo workspace rather than my ledger.
    demo_mode       BOOLEAN NOT NULL DEFAULT FALSE
);

-- The index on `demo_of` is created by migrations/0001_visits_and_demo.sql,
-- not here. This file's CREATE TABLE is IF NOT EXISTS, so on a database that
-- already has `users` the column above is not added and an index over it here
-- would fail the whole script before the migration that adds the column has
-- had a chance to run. Migrations replay on a new database too, so both
-- paths end up with it.

-- Case-insensitive, because nobody thinks of Pank@example.com and
-- pank@example.com as two accounts.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (lower(email));

-- Server-side sessions rather than a self-contained JWT. This app holds a
-- complete financial history, so "sign out everywhere" and "revoke that
-- laptop" have to actually work - and a stateless token cannot be withdrawn
-- before it expires. Only the SHA-256 of the cookie value is stored, so a
-- leaked database backup does not hand over live sessions.
CREATE TABLE IF NOT EXISTS user_sessions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL UNIQUE,
    issued_at    TEXT NOT NULL DEFAULT fa_now(),
    expires_at   TEXT NOT NULL,
    last_used_at TEXT NOT NULL DEFAULT fa_now(),
    revoked_at   TEXT,
    user_agent   TEXT NOT NULL DEFAULT '',
    ip           TEXT NOT NULL DEFAULT '',
    -- Requests served on this session. Incremented by the same UPDATE that
    -- resolves the cookie, so "how often does this person actually come
    -- back" costs no extra query - and session ROWS would answer a different
    -- question, since one 72-hour cookie covers a week of visits.
    uses         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions (user_id);

-- One in-flight OAuth redirect. Holds the CSRF state and the PKCE verifier,
-- which must not travel to the browser, plus where to land afterwards.
-- `purpose` distinguishes signing in from the later incremental grant that
-- adds Gmail read access, so a callback cannot be replayed against the other.
CREATE TABLE IF NOT EXISTS oauth_states (
    state         TEXT PRIMARY KEY,
    code_verifier TEXT NOT NULL,
    purpose       TEXT NOT NULL DEFAULT 'signin',
    redirect_to   TEXT NOT NULL DEFAULT '/',
    user_id       UUID REFERENCES users(id) ON DELETE CASCADE,
    created_at    TEXT NOT NULL DEFAULT fa_now()
);

-- The Gmail grant, per user. Replaces data/gmail_token.json, which was a
-- single file for a single person and had no place in a multi-user server:
-- whoever connected last owned everyone's mailbox import.
CREATE TABLE IF NOT EXISTS google_tokens (
    user_id       UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_json    TEXT NOT NULL,
    scopes        TEXT NOT NULL DEFAULT '',
    connected_at  TEXT NOT NULL DEFAULT fa_now(),
    updated_at    TEXT NOT NULL DEFAULT fa_now()
);


-- ---------------------------------------------------------------------------
-- The ledger
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS accounts (
    id                    TEXT NOT NULL,
    user_id               UUID NOT NULL DEFAULT current_tenant()
                              REFERENCES users(id) ON DELETE CASCADE,
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
    created_at            TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, institution, account_type, account_number_masked, product_name)
);

CREATE TABLE IF NOT EXISTS statements (
    id               TEXT NOT NULL,
    user_id          UUID NOT NULL DEFAULT current_tenant()
                         REFERENCES users(id) ON DELETE CASCADE,
    account_id       TEXT,
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
    ingested_at      TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, account_id)
        REFERENCES accounts(user_id, id) ON DELETE CASCADE
);

-- The same file re-uploaded must not create a second copy of every row.
-- Scoped to the user: two people banking with the same institution can be
-- sent byte-identical statements, and neither should shadow the other's.
CREATE UNIQUE INDEX IF NOT EXISTS idx_statements_hash
    ON statements (user_id, file_hash) WHERE file_hash <> '';

CREATE TABLE IF NOT EXISTS transactions (
    id                     TEXT NOT NULL,
    user_id                UUID NOT NULL DEFAULT current_tenant()
                               REFERENCES users(id) ON DELETE CASCADE,
    account_id             TEXT NOT NULL,
    statement_id           TEXT,
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
    category_confidence    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    -- WHICH rule decided the category. `category_source` already says a rule
    -- did it; this says which one, so "why is this Dining?" has an answer the
    -- user can read.
    category_rule          TEXT NOT NULL DEFAULT '',
    -- Why the row is money in or money out. Direction is the one field whose
    -- mistake lands on both sides of every total at once, so it carries its
    -- reasoning too.
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
    -- Which side of the books this row lands on.
    flow_role              TEXT NOT NULL DEFAULT '',
    -- The user's own edits, denormalised onto the row so reading the ledger
    -- needs no join. user_overrides remains the durable copy: these two
    -- columns are wiped with the rest of the parsed data and re-applied from
    -- there on the next run.
    excluded               INTEGER NOT NULL DEFAULT 0,
    note                   TEXT NOT NULL DEFAULT '',
    -- Where the row came from. An email alert is a real transaction but an
    -- unreconciled one, and no total can be trusted that cannot tell the two
    -- apart.
    source                 TEXT NOT NULL DEFAULT 'statement',
    -- Set when the statement covering this row arrived later and replaced it.
    -- Kept rather than deleted, so the alert that arrived first stays
    -- auditable.
    superseded             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, account_id)
        REFERENCES accounts(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, statement_id)
        REFERENCES statements(user_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_txn_date        ON transactions (txn_date);
CREATE INDEX IF NOT EXISTS idx_txn_account     ON transactions (account_id, txn_date);
CREATE INDEX IF NOT EXISTS idx_txn_category    ON transactions (category);
CREATE INDEX IF NOT EXISTS idx_txn_transfer    ON transactions (transfer_pair_id);
CREATE INDEX IF NOT EXISTS idx_txn_fingerprint ON transactions (fingerprint);
CREATE INDEX IF NOT EXISTS idx_txn_acct_month  ON transactions (accounting_month);
CREATE INDEX IF NOT EXISTS idx_txn_source      ON transactions (source);
CREATE INDEX IF NOT EXISTS idx_txn_review      ON transactions (needs_review)
    WHERE needs_review = 1;
-- Every read is already narrowed to one tenant by the RLS policy; giving the
-- planner an index that leads with user_id keeps that narrowing cheap once
-- more than one person's ledger shares the table.
CREATE INDEX IF NOT EXISTS idx_txn_user_date   ON transactions (user_id, txn_date);

-- User-defined custom categories.
CREATE TABLE IF NOT EXISTS custom_categories (
    user_id     UUID NOT NULL DEFAULT current_tenant()
                    REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '#6b7280',
    icon        TEXT NOT NULL DEFAULT 'Tag',
    created_at  TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, name)
);

CREATE TABLE IF NOT EXISTS recurring_series_overrides (
    user_id     UUID NOT NULL DEFAULT current_tenant()
                    REFERENCES users(id) ON DELETE CASCADE,
    series_id   TEXT NOT NULL,
    is_active   INTEGER,
    label       TEXT,
    category    TEXT,
    deleted     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, series_id)
);

-- Learned merchant -> category map. This is what makes categorization get
-- cheaper and more deterministic the longer the app is used: an LLM decision
-- made once is reused forever, and a user correction overrides it permanently.
-- Per-user, not shared: the map is derived from one person's statements, and
-- "who this merchant is to me" is a private fact, not a public one.
CREATE TABLE IF NOT EXISTS merchant_categories (
    user_id      UUID NOT NULL DEFAULT current_tenant()
                     REFERENCES users(id) ON DELETE CASCADE,
    merchant_key TEXT NOT NULL,
    category     TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'llm',
    confidence   DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    hit_count    INTEGER NOT NULL DEFAULT 1,
    updated_at   TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, merchant_key)
);

CREATE TABLE IF NOT EXISTS transfer_pairs (
    pair_id        TEXT NOT NULL,
    user_id        UUID NOT NULL DEFAULT current_tenant()
                       REFERENCES users(id) ON DELETE CASCADE,
    debit_txn_id   TEXT,
    credit_txn_id  TEXT,
    amount         TEXT NOT NULL,
    day_gap        INTEGER NOT NULL DEFAULT 0,
    kind           TEXT NOT NULL DEFAULT 'self_transfer',
    confidence     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    PRIMARY KEY (user_id, pair_id)
);

-- Detected recurring series (salary, EMIs, subscriptions). Persisted because
-- the forecast layer needs them and recomputing over years of rows is wasteful.
CREATE TABLE IF NOT EXISTS recurring_series (
    id             TEXT NOT NULL,
    user_id        UUID NOT NULL DEFAULT current_tenant()
                       REFERENCES users(id) ON DELETE CASCADE,
    account_id     TEXT,
    label          TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT 'uncategorized',
    direction      TEXT NOT NULL,
    -- The going-forward figure: what the NEXT charge is expected to be, which
    -- for a series whose price rose is the level SINCE the rise rather than
    -- the lifetime median. `lifetime_median` below keeps the other one.
    median_amount  TEXT NOT NULL,
    cadence_days   INTEGER NOT NULL DEFAULT 30,
    -- Stored rather than mapped back from cadence_days by whoever reads this.
    -- The frontend carried its own copy of that table and could not name a
    -- cadence the detector learned about later.
    cadence_name   TEXT NOT NULL DEFAULT '',
    occurrences    INTEGER NOT NULL DEFAULT 0,
    first_seen     TEXT,
    last_seen      TEXT,
    next_expected  TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1,
    -- "active", "overdue" (a charge has been missed, but not enough of them
    -- to call it finished) or "ended". is_active collapses the first two.
    status         TEXT NOT NULL DEFAULT 'active',
    confidence     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    -- How the detector reached its verdict, so a series read back from
    -- storage can still be explained. Coverage is the share of periods
    -- between the first and last charge that actually hold one, which is the
    -- difference between rent and a shop visited now and then.
    coverage        DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    missed          INTEGER NOT NULL DEFAULT 0,
    day_of_month    INTEGER,
    amount_variance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    -- "flat", "rose", "fell" or "drifting", with the level it moved from and
    -- when, so a price rise is visible rather than averaged away.
    amount_trend    TEXT NOT NULL DEFAULT 'flat',
    lifetime_median TEXT,
    last_amount     TEXT,
    changed_on      TEXT,
    -- The sentences the detector wrote about its own reasoning, as JSON.
    evidence        TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, account_id)
        REFERENCES accounts(user_id, id) ON DELETE CASCADE
);

-- The user's own identity details, for password derivation and account
-- matching. One row per user (id = 'me', kept from the single-user schema so
-- every query that named it still reads the same). PAN and DOB are sensitive,
-- so this table is the one thing the LLM layer is never allowed to read from.
CREATE TABLE IF NOT EXISTS user_profile (
    user_id       UUID NOT NULL DEFAULT current_tenant()
                      REFERENCES users(id) ON DELETE CASCADE,
    id            TEXT NOT NULL DEFAULT 'me',
    full_name     TEXT NOT NULL DEFAULT '',
    date_of_birth TEXT,
    pan           TEXT NOT NULL DEFAULT '',
    mobile        TEXT NOT NULL DEFAULT '',
    custom_passwords TEXT NOT NULL DEFAULT '[]',
    excluded_senders TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id           TEXT NOT NULL,
    user_id      UUID NOT NULL DEFAULT current_tenant()
                     REFERENCES users(id) ON DELETE CASCADE,
    -- Insertion order, as the tiebreaker for two runs stored in the same
    -- second. SQLite got this from its implicit `rowid`; PostgreSQL has no
    -- equivalent that means "the order these arrived", so it is a real
    -- column. BY DEFAULT rather than ALWAYS so restoring a snapshot can carry
    -- the original values rather than renumbering the history.
    seq          BIGINT GENERATED BY DEFAULT AS IDENTITY,
    created_at   TEXT NOT NULL DEFAULT fa_now(),
    status       TEXT NOT NULL DEFAULT 'running',
    file_count   INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT,
    error        TEXT,
    PRIMARY KEY (user_id, id)
);

-- Every file the app has ever attempted, regardless of outcome. A failed or
-- locked file is not persisted anywhere else - the statements/transactions
-- tables only ever hold successes - so without this table there is nothing to
-- show in a "which files parsed and which didn't" view, and nothing to retry.
CREATE TABLE IF NOT EXISTS source_files (
    id                 TEXT NOT NULL,
    user_id            UUID NOT NULL DEFAULT current_tenant()
                           REFERENCES users(id) ON DELETE CASCADE,
    filename           TEXT NOT NULL,
    filepath           TEXT NOT NULL DEFAULT '',
    file_hash          TEXT NOT NULL DEFAULT '',
    source             TEXT NOT NULL DEFAULT 'upload',
    sender             TEXT NOT NULL DEFAULT '',
    message_id         TEXT NOT NULL DEFAULT '',
    size_bytes         INTEGER,
    -- The password that actually opened this file, in plain text. Consistent
    -- with the rest of this app's PII policy (see models/profile.py): stored
    -- only in the user's own rows, used only to open that user's own files,
    -- never logged in full or sent anywhere. Storing it is what lets a later
    -- load skip password-guessing entirely and open the file on the first try.
    password           TEXT,
    password_status    TEXT NOT NULL DEFAULT 'unknown',
    parse_status       TEXT NOT NULL DEFAULT 'pending',
    institution_guess  TEXT NOT NULL DEFAULT '',
    account_type_guess TEXT NOT NULL DEFAULT '',
    account_id         TEXT,
    statement_id       TEXT,
    transaction_count  INTEGER NOT NULL DEFAULT 0,
    error_message      TEXT NOT NULL DEFAULT '',
    -- Which calendar month (YYYY-MM) this file is believed to be a statement
    -- FOR. A parsed file gets this precisely, from its own declared period. A
    -- failed or locked file has no parsed period to read - this is the only
    -- way to place it in the coverage grid at all, so it is filled in from
    -- the filename (a date embedded in the name) or the email's own date as a
    -- fallback, and left NULL only when neither is available.
    period_hint        TEXT,
    first_seen_at      TEXT NOT NULL DEFAULT fa_now(),
    last_attempted_at  TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id),
    -- `SET NULL (column)` names which column to clear: the plain form would
    -- try to null user_id too, and user_id is what the row-level security
    -- policy matches on - it can never be NULL.
    FOREIGN KEY (user_id, account_id)
        REFERENCES accounts(user_id, id) ON DELETE SET NULL (account_id),
    FOREIGN KEY (user_id, statement_id)
        REFERENCES statements(user_id, id) ON DELETE SET NULL (statement_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_files_hash
    ON source_files (user_id, file_hash) WHERE file_hash <> '';


-- ---------------------------------------------------------------------------
-- Tier 0: things a human decided. Cannot be recomputed from any input, so
-- these survive every clearing action except an explicit factory reset. Each
-- is keyed by a CONTENT FINGERPRINT rather than a transaction id, because
-- re-parsing a statement mints fresh uuids and would orphan every decision.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_overrides (
    user_id          UUID NOT NULL DEFAULT current_tenant()
                         REFERENCES users(id) ON DELETE CASCADE,
    fingerprint      TEXT NOT NULL,
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
    updated_at       TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_overrides_loose
    ON user_overrides (txn_date, amount, direction, desc_hash);


-- ---------------------------------------------------------------------------
-- Tier 1: inference that cost real money. Cleared only by its own dedicated
-- action, never as a side effect of clearing parsed data.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ai_inferences (
    user_id     UUID NOT NULL DEFAULT current_tenant()
                    REFERENCES users(id) ON DELETE CASCADE,
    -- sha256(kind|input_hash). Deliberately NOT keyed on provider or model:
    -- the answer to "which bank issued this statement?" does not depend on
    -- who was asked, and re-billing for it after switching provider would
    -- defeat the point of the cache. Provider and model are recorded as
    -- metadata for auditability.
    cache_key   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    input_hash  TEXT NOT NULL,
    result_json TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT fa_now(),
    hit_count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_ai_kind ON ai_inferences (kind);


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
    id                 TEXT NOT NULL,
    user_id            UUID NOT NULL DEFAULT current_tenant()
                           REFERENCES users(id) ON DELETE CASCADE,
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
    updated_at         TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_claims_origin ON claims (origin_fingerprint);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims (status);

CREATE TABLE IF NOT EXISTS claim_settlements (
    id              TEXT NOT NULL,
    user_id         UUID NOT NULL DEFAULT current_tenant()
                        REFERENCES users(id) ON DELETE CASCADE,
    claim_id        TEXT NOT NULL,
    -- cash and write_off have no ledger row at all, which is exactly why a
    -- claim must be closeable by hand rather than only by matching.
    method          TEXT NOT NULL,
    amount          TEXT NOT NULL,
    settled_on      TEXT NOT NULL,
    txn_fingerprint TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, claim_id)
        REFERENCES claims(user_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_claim_settlements_claim
    ON claim_settlements (claim_id);
CREATE INDEX IF NOT EXISTS idx_claim_settlements_txn
    ON claim_settlements (txn_fingerprint) WHERE txn_fingerprint <> '';

-- One transaction divided into parts with different owners or categories: a
-- grocery bill that was half a flatmate's, a card purchase only partly the
-- user's. Invariant enforced on write: the parts sum to the parent.
CREATE TABLE IF NOT EXISTS transaction_splits (
    id                 TEXT NOT NULL,
    user_id            UUID NOT NULL DEFAULT current_tenant()
                           REFERENCES users(id) ON DELETE CASCADE,
    parent_fingerprint TEXT NOT NULL,
    amount             TEXT NOT NULL,
    category           TEXT,
    flow_role          TEXT,
    claim_id           TEXT,
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
    origin_desc_hash   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, claim_id)
        REFERENCES claims(user_id, id) ON DELETE SET NULL (claim_id)
);

CREATE INDEX IF NOT EXISTS idx_splits_parent
    ON transaction_splits (parent_fingerprint);

-- A recurring shared cost - rent split with a flatmate - as a rule rather
-- than a chore to repeat every month.
CREATE TABLE IF NOT EXISTS split_rules (
    id           TEXT NOT NULL,
    user_id      UUID NOT NULL DEFAULT current_tenant()
                     REFERENCES users(id) ON DELETE CASCADE,
    label        TEXT NOT NULL DEFAULT '',
    match_text   TEXT NOT NULL,
    account_key  TEXT NOT NULL DEFAULT '',
    mine_pct     TEXT NOT NULL DEFAULT '100',
    counterparty TEXT NOT NULL DEFAULT '',
    category     TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);


-- ---------------------------------------------------------------------------
-- Settlement groups the user has CONFIRMED.
--
-- Only confirmed ones are stored. Inferred groups are recomputed on every run
-- and held in memory - persisting a guess would make it look like a decision.
-- Storing the confirmations is what stops the app asking the same question
-- after every re-parse.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS settlement_groups (
    id            TEXT NOT NULL,
    user_id       UUID NOT NULL DEFAULT current_tenant()
                      REFERENCES users(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL DEFAULT 'card_settlement',
    total_amount  TEXT NOT NULL DEFAULT '0',
    residual      TEXT NOT NULL DEFAULT '0',
    confidence    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    confirmed     INTEGER NOT NULL DEFAULT 1,
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE TABLE IF NOT EXISTS settlement_group_legs (
    user_id     UUID NOT NULL DEFAULT current_tenant()
                    REFERENCES users(id) ON DELETE CASCADE,
    group_id    TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    side        TEXT NOT NULL,   -- outflow (funding) | inflow (settled)
    PRIMARY KEY (user_id, group_id, fingerprint),
    FOREIGN KEY (user_id, group_id)
        REFERENCES settlement_groups(user_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_settlement_legs_fp
    ON settlement_group_legs (fingerprint);


-- ---------------------------------------------------------------------------
-- Explore: user-built dashboards.
--
-- A widget stores a QUERY, never a result. Storing computed figures would mean
-- a saved dashboard could disagree with the ledger it was built from the
-- moment a category is corrected or a statement re-parsed. Everything here is
-- re-executed against the live tables on open.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dashboards (
    id           TEXT NOT NULL,
    user_id      UUID NOT NULL DEFAULT current_tenant()
                     REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    -- The one that opens first. Enforced as at-most-one in the repository
    -- rather than by a constraint, so an UPDATE that swaps two rows does not
    -- have to be ordered to stay legal at every intermediate step.
    is_default   INTEGER NOT NULL DEFAULT 0,
    position     INTEGER NOT NULL DEFAULT 0,
    -- Board-level date range and filters, applied on top of every widget's
    -- own query so one control can re-cut the whole board.
    filters_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT fa_now(),
    updated_at   TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE TABLE IF NOT EXISTS dashboard_widgets (
    id           TEXT NOT NULL,
    user_id      UUID NOT NULL DEFAULT current_tenant()
                     REFERENCES users(id) ON DELETE CASCADE,
    dashboard_id TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    type         TEXT NOT NULL DEFAULT 'table',
    query_json   TEXT NOT NULL DEFAULT '{}',
    viz_json     TEXT NOT NULL DEFAULT '{}',
    position     INTEGER NOT NULL DEFAULT 0,
    -- Columns of a 12-wide grid, and height in 120px row units.
    width        INTEGER NOT NULL DEFAULT 6,
    height       INTEGER NOT NULL DEFAULT 2,
    created_at   TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, dashboard_id)
        REFERENCES dashboards(user_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_widgets_dashboard
    ON dashboard_widgets (dashboard_id, position);


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
    id            TEXT NOT NULL,
    user_id       UUID NOT NULL DEFAULT current_tenant()
                      REFERENCES users(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    -- queued | running | complete | failed | cancelled | interrupted
    status        TEXT NOT NULL DEFAULT 'queued',
    phase         TEXT NOT NULL DEFAULT '',
    current       INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    message       TEXT NOT NULL DEFAULT '',
    started_at    DOUBLE PRECISION NOT NULL DEFAULT 0,
    finished_at   DOUBLE PRECISION,
    result_json   TEXT NOT NULL DEFAULT 'null',
    request_json  TEXT NOT NULL DEFAULT 'null',
    errors_json   TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs (started_at DESC);

CREATE TABLE IF NOT EXISTS job_items (
    user_id  UUID NOT NULL DEFAULT current_tenant()
                 REFERENCES users(id) ON DELETE CASCADE,
    job_id   TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    name     TEXT NOT NULL,
    -- A stable identifier for the unit of work, so resuming can tell which
    -- items are already done. Names are not unique - two banks both send
    -- "statement.pdf" - and resuming on a name would skip real work.
    key      TEXT NOT NULL DEFAULT '',
    status   TEXT NOT NULL DEFAULT 'pending',
    detail   TEXT NOT NULL DEFAULT '',
    cached   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, job_id, seq),
    FOREIGN KEY (user_id, job_id) REFERENCES jobs(user_id, id) ON DELETE CASCADE
);


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
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staged_files (
    id            TEXT NOT NULL,
    user_id       UUID NOT NULL DEFAULT current_tenant()
                      REFERENCES users(id) ON DELETE CASCADE,
    file_hash     TEXT NOT NULL,
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
    -- Deferrable because it points into its own table: restoring a snapshot
    -- inserts the rows in file order, and the entry doing the superseding is
    -- not necessarily written before the one it supersedes.
    superseded_by TEXT,
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
    added_at      TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, file_hash),
    -- Deferrable because it points into its own table: restoring a snapshot
    -- inserts the rows in file order, and the entry doing the superseding is
    -- not necessarily written before the one it supersedes.
    FOREIGN KEY (user_id, superseded_by)
        REFERENCES staged_files(user_id, id) ON DELETE SET NULL (superseded_by)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_staged_kind ON staged_files (kind, selected);
CREATE INDEX IF NOT EXISTS idx_staged_account ON staged_files (account_key);


-- ---------------------------------------------------------------------------
-- Application settings: a handful of switches the user owns.
--
-- Key/value rather than columns, because these are preferences rather than
-- data: a new switch should not need a migration. Kept in the database and not
-- in localStorage because they change what the SERVER does - whether a model
-- is called and money is spent - so the browser cannot be the authority.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS app_settings (
    user_id    UUID NOT NULL DEFAULT current_tenant()
                   REFERENCES users(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, key)
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
    id              TEXT NOT NULL,
    user_id         UUID NOT NULL DEFAULT current_tenant()
                        REFERENCES users(id) ON DELETE CASCADE,
    bureau          TEXT NOT NULL,          -- cibil | crif | experian | equifax
    score           INTEGER,
    score_band      TEXT NOT NULL DEFAULT '',
    pulled_on       TEXT,
    holder_name     TEXT NOT NULL DEFAULT '',
    file_hash       TEXT NOT NULL DEFAULT '',
    source_filename TEXT NOT NULL DEFAULT '',
    warnings        TEXT NOT NULL DEFAULT '[]',
    ingested_at     TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bureau_hash
    ON bureau_reports (user_id, file_hash) WHERE file_hash <> '';

CREATE TABLE IF NOT EXISTS bureau_accounts (
    id            TEXT NOT NULL,
    user_id       UUID NOT NULL DEFAULT current_tenant()
                      REFERENCES users(id) ON DELETE CASCADE,
    report_id     TEXT NOT NULL,
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
    account_id    TEXT,
    -- unmatched | auto | suggested | confirmed | rejected
    match_status  TEXT NOT NULL DEFAULT 'unmatched',
    match_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    match_reason  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, report_id)
        REFERENCES bureau_reports(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, account_id)
        REFERENCES accounts(user_id, id) ON DELETE SET NULL (account_id)
);

CREATE INDEX IF NOT EXISTS idx_bureau_accounts_report
    ON bureau_accounts (report_id);
CREATE INDEX IF NOT EXISTS idx_bureau_accounts_match
    ON bureau_accounts (account_id);


-- ---------------------------------------------------------------------------
-- Investment holdings.
--
-- A portfolio statement declares a total, and units x NAV has to reproduce it.
-- That is the same reconciliation gate the bank statements go through, applied
-- to the one number a broker prints that can be checked against its own rows.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS portfolio_statements (
    id              TEXT NOT NULL,
    user_id         UUID NOT NULL DEFAULT current_tenant()
                        REFERENCES users(id) ON DELETE CASCADE,
    account_id      TEXT,
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
    ingested_at     TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, account_id)
        REFERENCES accounts(user_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_hash
    ON portfolio_statements (user_id, file_hash) WHERE file_hash <> '';

CREATE TABLE IF NOT EXISTS holdings (
    id           TEXT NOT NULL,
    user_id      UUID NOT NULL DEFAULT current_tenant()
                     REFERENCES users(id) ON DELETE CASCADE,
    statement_id TEXT,
    account_id   TEXT,
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
    PRIMARY KEY (user_id, id),
    -- One row per instrument per folio per valuation date: re-importing the
    -- same statement updates the position rather than adding a second copy.
    UNIQUE (user_id, account_id, isin, folio, as_of),
    FOREIGN KEY (user_id, statement_id)
        REFERENCES portfolio_statements(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, account_id)
        REFERENCES accounts(user_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_holdings_asof ON holdings (as_of DESC);
CREATE INDEX IF NOT EXISTS idx_holdings_statement ON holdings (statement_id);


-- ---------------------------------------------------------------------------
-- Agent runs.
--
-- An agent run is model output that cost real money to produce, so it is
-- stored rather than recomputed - and, more importantly, stored so that TWO
-- runs can be compared. "Your EMIs are 43% of take-home" is a fact anybody
-- can read off a screen; "they were 47% when this last ran in March" is the
-- thing somebody actually wants to know, and only a history can say it.
--
-- The transcript is kept alongside the answer because an agent's figures are
-- only trustworthy if they can be traced: every tool call and every result is
-- in there, so any number in an answer can be checked against the call that
-- produced it.
CREATE TABLE IF NOT EXISTS agent_runs (
    id              TEXT NOT NULL,
    user_id         UUID NOT NULL DEFAULT current_tenant()
                        REFERENCES users(id) ON DELETE CASCADE,
    agent           TEXT NOT NULL,
    -- "ok", "exhausted" (ran out of steps) or "failed".
    status          TEXT NOT NULL DEFAULT 'ok',
    started_at      TEXT NOT NULL DEFAULT fa_now(),
    finished_at     TEXT,
    seconds         DOUBLE PRECISION NOT NULL DEFAULT 0,
    -- The question asked, when the user asked their own rather than taking
    -- the agent's default.
    question        TEXT NOT NULL DEFAULT '',
    answer_json     TEXT NOT NULL DEFAULT '{}',
    transcript_json TEXT NOT NULL DEFAULT '[]',
    model           TEXT NOT NULL DEFAULT '',
    provider        TEXT NOT NULL DEFAULT '',
    steps           INTEGER NOT NULL DEFAULT 0,
    tool_calls      INTEGER NOT NULL DEFAULT 0,
    error           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_agent
    ON agent_runs (user_id, agent, started_at DESC);
