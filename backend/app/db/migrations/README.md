# Schema changes after the first release

`schema.sql` is the shape of a *new* database. Every statement in it is
`CREATE ... IF NOT EXISTS`, which means it creates a table that is missing and
does nothing at all to one that already exists — so a new **column** on an
existing table, a changed constraint, or a backfill will never happen through
that file. It has to be a migration.

Add one `NNNN_short_name.sql` file here, numbered in order. `Database.ensure_schema`
applies anything not yet recorded in `schema_migrations`, in filename order,
each inside its own transaction, and records it on success. They are applied
before the row-level security policies are re-asserted, so a migration that
adds a table gets its policy without needing to say so.

Two rules:

- **Never edit a migration that has shipped.** It has already run on somebody's
  database and will not run again; changing it only makes the file disagree
  with reality. Write another one.
- **Change `schema.sql` too.** A new database is built from that file alone and
  never replays this directory, so the two have to arrive at the same shape.
  `tests/test_lifecycle.py` checks the columns analytics depends on are present
  in a freshly created database, which is the assertion that catches forgetting.

Migrations run as the app's ordinary role — the same one row-level security
applies to — so a migration that touches rows sees no rows unless it binds a
tenant. Prefer DDL and column defaults; a data backfill across every account
belongs in a script under `backend/tools/`, not here.
