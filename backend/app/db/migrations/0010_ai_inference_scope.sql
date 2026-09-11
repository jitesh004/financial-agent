-- Make the inference cache answer the question it was asked, and keep a
-- record of every answer it gave.
--
-- Two faults, both structural:
--
-- 1. `save_ai_inference` hardcoded ONE kind, `statement_identity`. The card
--    summary reader calls the same two helpers, so two different questions -
--    "who issued this?" and "what does this bill total?" - shared a key
--    namespace and a `kind` column that named only the first of them. The
--    column was decoration; nothing scoped anything.
--
-- 2. The key was sha256 of the RAW letterhead slice. A letterhead carries
--    the statement period, the masked account number and the closing
--    balance, so every month's statement from one card hashed differently
--    and cost a fresh request. Twelve ICICI Amazon Pay statements are one
--    template and were twelve cache misses. On a free tier metered at 500
--    requests a DAY, that is the difference between an import that finishes
--    and one that stops halfway.
--
-- The key is now sha256(kind | normalised-template), where normalisation
-- flattens every digit to '#' - see repository.template_hash. What varies
-- month to month is exactly what it removes.
--
-- The three identity columns are the other half of the request: they record
-- what the model concluded, so the cache can also be read the way a person
-- thinks about it - per institution, per account type, per product. ICICI is
-- one institution with a savings account, several cards and a personal loan,
-- and those are four different answers that must not collapse into one.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'ai_inferences'
                     AND column_name = 'institution') THEN
        ALTER TABLE ai_inferences
            ADD COLUMN institution  TEXT NOT NULL DEFAULT '',
            ADD COLUMN account_type TEXT NOT NULL DEFAULT '',
            ADD COLUMN product_name TEXT NOT NULL DEFAULT '',
            ADD COLUMN prompt       TEXT NOT NULL DEFAULT '';
    END IF;
END $$;

-- "Have I already worked out what an ICICI credit card statement looks
-- like?", asked without knowing which template this month's file uses.
CREATE INDEX IF NOT EXISTS idx_ai_identity
    ON ai_inferences (user_id, kind, institution, account_type, product_name);

-- Every model call this workspace has made, per file, kept apart from the
-- cache that deduplicates them.
--
-- The cache stores one row per TEMPLATE - that is the point of it - but the
-- import wizard has to be able to say what happened to each FILE, including
-- the files whose answer came back without a request being spent. Folding
-- the two together would mean either losing the per-file view or losing the
-- deduplication, and both are load-bearing.
CREATE TABLE IF NOT EXISTS ai_inference_log (
    user_id      UUID NOT NULL DEFAULT current_tenant()
                     REFERENCES users(id) ON DELETE CASCADE,
    id           TEXT NOT NULL,
    job_id       TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL,
    cache_key    TEXT NOT NULL DEFAULT '',
    source_label TEXT NOT NULL DEFAULT '',
    prompt       TEXT NOT NULL DEFAULT '',
    response_json TEXT NOT NULL DEFAULT '',
    -- What the app actually took from the answer, field by field, and what
    -- it refused. An inference the deterministic reader had already beaten
    -- is the interesting case: the model answered, and nothing was used.
    applied_json TEXT NOT NULL DEFAULT '',
    cached       INTEGER NOT NULL DEFAULT 0,
    provider     TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_ai_log_job
    ON ai_inference_log (user_id, job_id, created_at);

ALTER TABLE ai_inference_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_inference_log FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename = 'ai_inference_log'
                     AND policyname = 'ai_inference_log_tenant') THEN
        CREATE POLICY ai_inference_log_tenant ON ai_inference_log
            USING (user_id = current_tenant());
    END IF;
END $$;
