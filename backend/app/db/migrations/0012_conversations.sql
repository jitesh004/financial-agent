-- Multi-turn conversations over the ledger.
--
-- `agent_runs` already records one question and one answer. What it cannot
-- express is the SECOND question: "what about last month?" has no referent
-- in a store where every run begins from nothing.
--
-- A turn keeps the question, the answer, and a pointer to the `agent_runs`
-- row holding the full working - the tool calls, the figures they returned
-- and the verification verdict. Two tables rather than one column because
-- the working is large and is wanted rarely, while the conversation itself
-- is small and is read on every page load.
CREATE TABLE IF NOT EXISTS conversations (
    user_id    UUID NOT NULL DEFAULT current_tenant()
                   REFERENCES users(id) ON DELETE CASCADE,
    id         TEXT NOT NULL,
    -- Taken from the first question until the user renames it. A list of
    -- conversations called "Conversation 1..9" is a list nobody can search.
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT fa_now(),
    updated_at TEXT NOT NULL DEFAULT fa_now(),
    archived   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_conversations_recent
    ON conversations (user_id, archived, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_turns (
    user_id         UUID NOT NULL DEFAULT current_tenant()
                        REFERENCES users(id) ON DELETE CASCADE,
    id              TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    -- Monotonic within a conversation. Ordering by timestamp alone breaks
    -- on two turns inside the same second, which a fast cached answer
    -- reaches easily.
    seq             INTEGER NOT NULL DEFAULT 0,
    question        TEXT NOT NULL DEFAULT '',
    answer_json     TEXT NOT NULL DEFAULT '{}',
    -- The full working: `agent_runs.id`. Kept as a pointer rather than
    -- copied, so "show me how you got that" costs nothing until asked.
    run_id          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'ok',
    error           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT fa_now(),
    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_turns_conversation
    ON conversation_turns (user_id, conversation_id, seq);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE conversation_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_turns FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename = 'conversations'
                     AND policyname = 'conversations_tenant') THEN
        CREATE POLICY conversations_tenant ON conversations
            USING (user_id = current_tenant());
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename = 'conversation_turns'
                     AND policyname = 'conversation_turns_tenant') THEN
        CREATE POLICY conversation_turns_tenant ON conversation_turns
            USING (user_id = current_tenant());
    END IF;
END $$;
