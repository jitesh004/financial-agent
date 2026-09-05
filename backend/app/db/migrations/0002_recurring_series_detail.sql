-- What the recurring detector found, not just what it concluded.
--
-- The table held a verdict - cadence_days, an amount, a confidence - and
-- nothing about how the verdict was reached, so a series read back from
-- storage could not be explained, argued with, or told apart from one the
-- detector was only half sure of. Four things in particular were missing and
-- had to be re-derived or guessed by every reader:
--
--   cadence_name    the frontend was mapping cadence_days back to a name
--                   through its own copy of the table, which cannot name a
--                   cadence the server adds later
--   coverage/missed how many periods in the span actually have a charge -
--                   the difference between rent and a shop visited now and
--                   then, and invisible in a confidence score alone
--   the amount's    a subscription whose price rose is one series with two
--   history         levels, and "median_amount" alone cannot say which one
--                   is next month's bill
--   evidence        the sentences the detector wrote about its own reasoning
--
-- All nullable with defaults, so a row written by the previous version reads
-- back unchanged rather than erroring.

ALTER TABLE recurring_series
    ADD COLUMN IF NOT EXISTS cadence_name    TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS status          TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS coverage        DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS missed          INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS day_of_month    INTEGER,
    ADD COLUMN IF NOT EXISTS amount_variance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS amount_trend    TEXT NOT NULL DEFAULT 'flat',
    ADD COLUMN IF NOT EXISTS lifetime_median TEXT,
    ADD COLUMN IF NOT EXISTS last_amount     TEXT,
    ADD COLUMN IF NOT EXISTS changed_on      TEXT,
    ADD COLUMN IF NOT EXISTS evidence        TEXT NOT NULL DEFAULT '[]';
