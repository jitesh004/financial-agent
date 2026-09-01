-- Runs once, the first time the postgres container initialises its volume.
--
-- The app deliberately does NOT connect as the superuser this container starts
-- with. PostgreSQL exempts superusers - and any role holding BYPASSRLS - from
-- every row-level security policy, which is exactly what keeps one signed-in
-- person out of another's bank statements. Connecting as one would leave the
-- app running, passing its tests, and quietly showing everybody everything.
-- `db/engine.assert_isolation_enforced` refuses to start in that state; this
-- script is what makes sure it never has to.

CREATE ROLE financial_agent LOGIN PASSWORD 'financial_agent';

-- Owner, so it can create its own schema on first boot, but an ordinary role:
-- no SUPERUSER, no BYPASSRLS.
CREATE DATABASE financial_agent OWNER financial_agent;

\connect financial_agent

GRANT ALL ON SCHEMA public TO financial_agent;
-- pgcrypto is what gen_random_uuid() comes from on older servers; creating it
-- here means the app never needs a privilege it should not have. Harmless on
-- PostgreSQL 13+, where the function is built in.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
