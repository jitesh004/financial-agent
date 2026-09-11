-- Every request this workspace makes to a language model, one row per
-- ATTEMPT.
--
-- `ai_inference_log` (0010) answers "what was the model asked about this
-- file, and did the app believe it" - a per-document audit for the import
-- wizard. It deliberately holds one row per template and says nothing about
-- cost, keys, latency or failure.
--
-- This answers a different question: what is this workspace spending, on
-- which model, through which key, and what went wrong. Those need the
-- attempt as the unit, because a request that was rate limited on one key
-- and succeeded on the next is TWO facts - and rolling them into one is
-- exactly how a quota problem stays invisible.
--
-- Written from `llm.providers`, the single point every provider's traffic
-- passes through, so nothing can bypass it by adding a call site.
CREATE TABLE IF NOT EXISTS llm_calls (
    user_id        UUID NOT NULL DEFAULT current_tenant()
                       REFERENCES users(id) ON DELETE CASCADE,
    id             TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT fa_now(),

    -- WHY the call was made. 'categorization', 'letterhead', 'agent',
    -- 'column_map', 'card_summary', 'narrative', 'probe'.
    purpose        TEXT NOT NULL DEFAULT 'unknown',
    -- WHICH one: the agent's key, the file being parsed, the batch size.
    -- "an agent called the model" is not useful; "debt-strategist did, at
    -- 05:41, on step 3" is.
    subject        TEXT NOT NULL DEFAULT '',
    job_id         TEXT NOT NULL DEFAULT '',

    provider       TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT '',
    tier           TEXT NOT NULL DEFAULT '',
    -- What was actually sent, not what is configured: a thinking model
    -- charges its reasoning against the same budget, so whether this was
    -- on is the first thing to look at when a reply comes back empty.
    reasoning      TEXT NOT NULL DEFAULT '',

    -- Which credential. The label the holder gave it, never the secret.
    key_label      TEXT NOT NULL DEFAULT '',
    key_hint       TEXT NOT NULL DEFAULT '',

    -- Attempts of one logical call share a group, so a retry can be shown
    -- under the request it retried rather than as an unrelated failure.
    group_id       TEXT NOT NULL DEFAULT '',
    attempt        INTEGER NOT NULL DEFAULT 1,

    status         TEXT NOT NULL DEFAULT 'ok',
    http_status    INTEGER NOT NULL DEFAULT 0,
    error          TEXT NOT NULL DEFAULT '',

    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens   INTEGER NOT NULL DEFAULT 0,
    prompt_chars   INTEGER NOT NULL DEFAULT 0,
    response_chars INTEGER NOT NULL DEFAULT 0,
    latency_ms     INTEGER NOT NULL DEFAULT 0,

    -- Estimated, in millionths of a rupee, and only when a price for this
    -- model is actually known. `cost_known = 0` means "not priced" - which
    -- the screen says out loud rather than rendering a confident zero.
    cost_micros    BIGINT  NOT NULL DEFAULT 0,
    cost_known     INTEGER NOT NULL DEFAULT 0,

    request_preview  TEXT NOT NULL DEFAULT '',
    response_preview TEXT NOT NULL DEFAULT '',

    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_when
    ON llm_calls (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_purpose
    ON llm_calls (user_id, purpose, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_group
    ON llm_calls (user_id, group_id);

ALTER TABLE llm_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_calls FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename = 'llm_calls'
                     AND policyname = 'llm_calls_tenant') THEN
        CREATE POLICY llm_calls_tenant ON llm_calls
            USING (user_id = current_tenant());
    END IF;
END $$;
