-- The position: what the user says is true, and when they said it.
--
-- Everything else in this app is derived from a document. This table is the
-- one place a human ASSERTS something, and it exists because there are facts
-- no statement carries: a loan serviced from an account that was never
-- uploaded, the tenure a lender agreed verbally, a card whose statement PDF
-- nobody can find. Without somewhere to put those, the app's totals are
-- confidently incomplete and nothing says so.
--
-- The obvious objection to a table like this is that a typed number goes
-- stale and then quietly disagrees with the statements. Two things answer it,
-- and both are why `reviewed_on` is NOT NULL:
--
--   * An attested figure AGES. A loan outstanding is only true on the day it
--     was read; the day after, one EMI has moved some of it to principal. So
--     nothing here is displayed as-is - it is rolled forward from
--     `reviewed_on` through the same amortization the Debt tab uses, and the
--     screen shows both the baseline and today's figure. See
--     analytics/position.py.
--   * An attested figure is CHECKABLE. Where the item is mapped to a ledger
--     account, the rolled-forward number is compared against what the
--     statements actually say, and the difference is reported rather than
--     silently preferred either way.
--
-- A card is the exception and is treated as one: a card's balance does not
-- amortize, it depends on what was spent, so it is never rolled forward. Its
-- CYCLE does roll - the next statement and due dates are arithmetic - and the
-- balance is marked stale once a cycle has closed since the review.
CREATE TABLE IF NOT EXISTS position_items (
    id            TEXT NOT NULL,
    user_id       UUID NOT NULL DEFAULT current_tenant()
                      REFERENCES users(id) ON DELETE CASCADE,
    -- "loan", "card", "account", "investment" or "other".
    kind          TEXT NOT NULL DEFAULT 'other',
    label         TEXT NOT NULL DEFAULT '',
    institution   TEXT NOT NULL DEFAULT '',

    -- What this is the same thing as. Both nullable and both editable by
    -- hand: an item can exist with no statement behind it (that is the point)
    -- and the bureau's view of a loan is a third record of the same debt.
    account_id        TEXT,
    bureau_account_id TEXT,

    -- The day the user reviewed this and said it was true. Everything derived
    -- is derived FROM this date, so it is required.
    reviewed_on   TEXT NOT NULL,

    -- The attested baseline. Money is TEXT holding a decimal, as everywhere.
    outstanding       TEXT,   -- owed on a loan or card; balance on an account
    original_amount   TEXT,   -- what was borrowed, where it is known
    emi               TEXT,
    interest_rate     TEXT,   -- annual percent
    months_remaining  INTEGER,
    months_total      INTEGER,

    -- Cards.
    credit_limit  TEXT,
    min_due       TEXT,
    -- Day of the month the statement is generated, and the day payment is
    -- due. Two numbers rather than dates, because a cycle repeats.
    statement_day INTEGER,
    due_day       INTEGER,

    notes         TEXT NOT NULL DEFAULT '',
    -- Removed by the user. Kept rather than deleted so a snapshot taken while
    -- it existed still resolves, and so "I closed this in March" is a fact
    -- the position can show rather than a gap it cannot explain.
    archived      INTEGER NOT NULL DEFAULT 0,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT fa_now(),
    updated_at    TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_position_kind
    ON position_items (user_id, kind, archived);


-- The whole position, frozen on the day it was reviewed.
--
-- "No one can deny it, because I reviewed it myself" only holds if the review
-- is a record rather than a state. A snapshot is what makes "this is what I
-- was carrying in September" answerable in December, and what the
-- roll-forward can be audited against when the next statement arrives.
CREATE TABLE IF NOT EXISTS position_snapshots (
    id         TEXT NOT NULL,
    user_id    UUID NOT NULL DEFAULT current_tenant()
                   REFERENCES users(id) ON DELETE CASCADE,
    taken_on   TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    -- Every item exactly as it stood, and the totals computed from them.
    -- Stored rather than recomputed: the point of a snapshot is that it does
    -- not change when the items do.
    items_json  TEXT NOT NULL DEFAULT '[]',
    totals_json TEXT NOT NULL DEFAULT '{}',
    item_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_position_snapshots_taken
    ON position_snapshots (user_id, taken_on DESC);
