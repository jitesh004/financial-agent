-- What an agent found, kept.
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
