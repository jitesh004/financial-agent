#!/bin/bash
# Runs once, the first time the postgres container initialises its volume.
#
# The app deliberately does NOT connect as the superuser this container starts
# with. PostgreSQL exempts superusers - and any role holding BYPASSRLS - from
# every row-level security policy, which is exactly what keeps one signed-in
# person out of another's bank statements. Connecting as one would leave the
# app running, passing its tests, and quietly showing everybody everything.
# `db/engine.assert_isolation_enforced` refuses to start in that state; this
# script is what makes sure it never has to.
#
# Shell rather than plain .sql because the role's password has to come from the
# environment. It used to be a literal in a .sql file while the app built its
# connection string from $FA_DB_PASSWORD - so setting that to anything other
# than the hardcoded value produced "password authentication failed", with the
# .env looking entirely correct.
set -euo pipefail

# Defaulted so the development compose file needs no secrets at all; the
# production one sets this and refuses to start without it.
APP_PASSWORD="${FA_DB_PASSWORD:-financial_agent}"

# -v plus :'name' is psql's own quoting: it escapes the value as a SQL string
# literal, so a password containing a quote cannot end the statement early.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
     -v pw="$APP_PASSWORD" <<-'SQL'
	CREATE ROLE financial_agent LOGIN PASSWORD :'pw';

	-- Owner, so it can create its own schema on first boot, but an ordinary
	-- role: no SUPERUSER, no BYPASSRLS.
	CREATE DATABASE financial_agent OWNER financial_agent;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname financial_agent <<-'SQL'
	GRANT ALL ON SCHEMA public TO financial_agent;
	-- pgcrypto is what gen_random_uuid() comes from on older servers; creating
	-- it here means the app never needs a privilege it should not have.
	-- Harmless on PostgreSQL 13+, where the function is built in.
	CREATE EXTENSION IF NOT EXISTS pgcrypto;
SQL

echo "created the financial_agent role and database"
