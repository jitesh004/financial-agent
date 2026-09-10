-- Row-level security on the three tables that hold credentials.
--
-- Every other tenant table is behind `USING (user_id = current_tenant())`,
-- and `engine.py` explains why: isolation is delegated to the database so a
-- query that forgets its WHERE clause returns nothing rather than someone
-- else's money. These three carry a `user_id` and were never enrolled:
--
--   google_tokens   OAuth refresh tokens for mailbox access
--   oauth_states    in-flight sign-in state
--   user_sessions   session token hashes
--
-- They are reached through helpers that pass `user_id` explicitly, so this
-- is a missing safety net rather than a live leak - but they are the three
-- tables where a missing WHERE clause costs the most, and the whole point of
-- the policy is not having to trust that every query remembered one.
--
-- `user_sessions` is deliberately NOT forced: `resolve_session` has to find
-- a session BEFORE it knows whose it is, so it runs with no tenant bound.
-- The policy therefore allows a NULL tenant to read, and constrains
-- everything else. The other two are only ever touched by a signed-in
-- request and get the strict form.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename = 'google_tokens') THEN
        ALTER TABLE google_tokens ENABLE ROW LEVEL SECURITY;
        ALTER TABLE google_tokens FORCE ROW LEVEL SECURITY;
        CREATE POLICY google_tokens_tenant ON google_tokens
            USING (user_id = current_tenant())
            WITH CHECK (user_id = current_tenant());
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename = 'oauth_states') THEN
        ALTER TABLE oauth_states ENABLE ROW LEVEL SECURITY;
        CREATE POLICY oauth_states_tenant ON oauth_states
            USING (current_tenant() IS NULL OR user_id = current_tenant())
            WITH CHECK (current_tenant() IS NULL
                        OR user_id = current_tenant());
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename = 'user_sessions') THEN
        ALTER TABLE user_sessions ENABLE ROW LEVEL SECURITY;
        CREATE POLICY user_sessions_tenant ON user_sessions
            USING (current_tenant() IS NULL OR user_id = current_tenant())
            WITH CHECK (current_tenant() IS NULL
                        OR user_id = current_tenant());
    END IF;
END $$;
