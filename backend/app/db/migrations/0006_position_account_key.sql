-- Attestation must point at an account by its IDENTITY, never by its uuid.
--
-- `position_items.account_id` held a raw uuid with no foreign key behind it.
-- Account uuids are not stable: `staging/process` rebuilds the ledger from
-- the selected files and mints fresh ones, so every reference taken before a
-- rebuild silently rots. On the ledger this audit was run against, 25 of 28
-- links pointed at accounts that no longer existed, and the three that still
-- resolved included BOTH loans pointing at the same Axis Bank credit card.
--
-- That is not a cosmetic break. The whole justification for this table is
-- that an attested figure is CHECKABLE - rolled forward, then compared with
-- what the statements say. Comparing against a card with a zero balance is
-- how a 66.7 lakh home loan came to be reported as 66.7 lakh of "drift", and
-- how `assets` came back null on the Position totals.
--
-- The fix is not new: `user_overrides` has always keyed on
-- `pipeline.fingerprint.account_key` - INSTITUTION|TYPE|MASKED|PRODUCT - for
-- exactly this reason, and that is why a correction survives a reprocess when
-- an attestation does not. The same key is adopted here. `product_name` is
-- part of it as the tiebreak for accounts that carry no masked number (an
-- IDFC Millennia and a slice card both have an empty one).
--
-- `account_id` is kept and still populated. It stays the fast path; the key
-- is what repairs it when the uuid has moved underneath.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'position_items'
                     AND column_name = 'account_key') THEN
        ALTER TABLE position_items ADD COLUMN account_key TEXT NOT NULL DEFAULT '';
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_position_items_account_key
    ON position_items (user_id, account_key);

-- Backfill, and repair the broken links while we are here.
--
-- Matched on the rendered display name rather than the stored uuid, because
-- the uuid is the thing that is wrong. The label was written from
-- `Account.display_name()` when the item was created, so it still names the
-- real-world account even after a rebuild renumbered everything.
--
-- Items with no match are left empty on purpose: those are the bureau-only
-- cards, which have no ledger account by definition and are linked through
-- `bureau_account_id` instead. An empty key means "no ledger account", which
-- is a different statement from "an account I cannot find" - and telling
-- those two apart is the point of the exercise.
WITH resolved AS (
    SELECT p.id AS item_id,
           a.id  AS real_account_id,
           upper(a.institution) || '|' || upper(a.account_type) || '|'
             || upper(a.account_number_masked) || '|' || upper(a.product_name) AS akey
    FROM position_items p
    JOIN accounts a
      ON a.user_id = p.user_id
     AND a.institution
         || CASE WHEN a.product_name <> '' THEN ' ' || a.product_name ELSE '' END
         || ' ' || initcap(replace(a.account_type, '_', ' '))
         || CASE WHEN a.account_number_masked <> ''
                 THEN ' (' || a.account_number_masked || ')' ELSE '' END = p.label
)
UPDATE position_items p
   SET account_key = r.akey,
       account_id  = r.real_account_id
  FROM resolved r
 WHERE p.id = r.item_id;
