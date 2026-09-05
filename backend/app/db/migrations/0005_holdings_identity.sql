-- What makes two holdings the same holding.
--
-- The constraint said (account, ISIN, folio, date) while the comment above it
-- said "one row per INSTRUMENT per folio per valuation date". The comment was
-- right and the constraint was not, and the difference is invisible for as
-- long as every holding carries an ISIN - which is true of a demat statement,
-- a CAS and a broker's own, so nothing caught it.
--
-- An NPS statement carries no ISIN and no folio number. A subscriber holds
-- three schemes at once - equity, corporate debt and government securities -
-- and under the old key all three were the same row: two of them overwrote
-- the first on the way in, and a 3.09 lakh corpus was recorded as whichever
-- scheme happened to be written last. Nothing failed; the total was simply
-- wrong, and only by an amount nobody had a second source for.
--
-- Widening a unique key can only ever resolve conflicts, never create them,
-- so no existing row needs touching.
DO $$
BEGIN
    ALTER TABLE holdings
        DROP CONSTRAINT IF EXISTS holdings_user_id_account_id_isin_folio_as_of_key;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'holdings_identity') THEN
        ALTER TABLE holdings ADD CONSTRAINT holdings_identity
            UNIQUE (user_id, account_id, isin, folio, instrument, as_of);
    END IF;
END $$;
