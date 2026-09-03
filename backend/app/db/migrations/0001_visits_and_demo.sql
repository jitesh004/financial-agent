-- Two things the operator's view and the Demo switch need, on databases that
-- already exist. `schema.sql` carries the same columns for a new one.
--
-- `uses` is incremented by the UPDATE that already resolves a session on every
-- request, so counting how often somebody actually comes back costs no extra
-- query and no extra write. Session ROWS only count sign-ins, which for a
-- 72-hour cookie is a very different number.
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS uses INTEGER NOT NULL DEFAULT 0;

-- A demo workspace is an ordinary account with generated statements in it,
-- owned by the person demonstrating the app. `demo_of` points back at them;
-- `demo_mode` on their own row says the app should be pointed at it.
--
-- A separate account rather than a flag on the rows: every screen, every
-- query and every row-level security policy already works per account, so
-- pointing the tenant at another one needs no special case anywhere - and
-- nothing that happens during a demo can touch the real ledger.
ALTER TABLE users ADD COLUMN IF NOT EXISTS demo_of UUID
    REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS demo_mode BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_users_demo_of ON users (demo_of);
