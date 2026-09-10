-- `google_tokens` is written by the IDENTITY layer, which has no tenant.
--
-- 0007 gave it the strict policy every ledger table has. That is wrong for
-- this table: `auth.store.save_google_token` and `get_google_token` run on
-- `identity_connection()`, which deliberately binds no tenant - the same
-- reason `user_sessions` cannot take the strict form. A grant is written
-- during the OAuth callback, at the moment the app is still working out who
-- the user is, so `current_tenant()` is NULL and the WITH CHECK refused
-- every insert.
--
-- The relaxed form still does the job it was added for: any query arriving
-- on a tenant-bound connection - which is every request handler - can only
-- see and write its own row. The identity layer, which passes `user_id`
-- explicitly and is the only caller that runs untenanted, is exempt.
DO $$
BEGIN
    ALTER TABLE google_tokens NO FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS google_tokens_tenant ON google_tokens;
    CREATE POLICY google_tokens_tenant ON google_tokens
        USING (current_tenant() IS NULL OR user_id = current_tenant())
        WITH CHECK (current_tenant() IS NULL
                    OR user_id = current_tenant());
END $$;
