"""PostgreSQL persistence.

Plain SQL over psycopg rather than an ORM. The queries here are analytical
(group-by, window functions over a ledger), which is exactly what SQL is good
at and what an ORM makes harder to read.

This used to be a SQLite file. It moved for two reasons that arrived together:
the app now serves several people who sign in, and one writer at a time with a
file lock is not a server. What PostgreSQL brings that the file could not:

  - Real concurrency. An ingestion job parsing forty statements no longer
    blocks the dashboard someone else is reading.
  - Row-level security. Per-user separation is enforced by the database on
    every statement, rather than by 2,200 lines of SQL each remembering a
    WHERE clause. See db/engine.py - that is the important half of this
    migration, and the one worth reading first.

Two decisions carried over unchanged, on purpose:

  - Money is stored as TEXT holding a decimal, not as a float. Decimal <-> TEXT
    is lossless, every figure is computed in Python's Decimal or as integer
    paise inside SQL, and the ledger ties out to the rupee today. A driver
    change is not the moment to re-type the column that guarantee rests on.
  - Timestamps are the same 'YYYY-MM-DD HH:MM:SS' UTC text SQLite wrote, via
    the `fa_now()` function the schema defines, so rows written on either side
    of the migration sort against each other.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.adapt import Dumper

from ..config import config
from .engine import (ACTIVE, TENANT, IsolationError, Pool,
                     assert_isolation_enforced, current_tenant, tenant_scope)

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

#: Where per-user snapshots are written. The database itself is a server now
#: and no longer a file in here, but the data directory is still where
#: statement files and caches live.
DATA_DIR = Path(config.DATA_DIR)

#: How many automatic snapshots to keep per user before pruning the oldest.
MAX_SNAPSHOTS = 10

#: Derived data: reproducible from the parsed ledger in seconds.
#: Job history sits here as the one thing that is not reproducible but is also
#: not precious - it is an operational log of work already done, and losing it
#: costs nothing but the trace. A job still in flight re-inserts its own row on
#: the next flush, so clearing this scope mid-run does not orphan it.
_TIER_DERIVED = ("transfer_pairs", "recurring_series", "analysis_runs",
                 "job_items", "jobs")

#: Parsed data: reproducible from the statement files, at the cost of CPU.
#: Rows read from transaction alert emails live here too. They are
#: reproducible in the same sense - by re-scanning the mailbox rather than
#: by re-reading a file - so clearing this scope drops them and a fresh
#: alert scan brings them back.
_TIER_PARSED = ("transactions", "statements", "holdings",
                "portfolio_statements", "bureau_accounts",
                "bureau_reports", "accounts")

#: The file registry. The files themselves live on disk and are handled
#: separately - see the storage module - because losing a manually uploaded
#: statement the user no longer has a copy of is unrecoverable.
_TIER_FILES = ("source_files",)

#: The staging area. Deliberately NOT part of `parsed_data`: clearing the
#: parsed ledger is how someone asks for a rebuild, and a rebuild needs the
#: staged set to rebuild FROM. Wiping both would turn "recompute this" into
#: "download and re-parse everything", which is minutes of work and a fresh
#: dependence on Gmail still having the mail.
_TIER_STAGING = ("staged_files",)

#: Bought with real money. Never cleared as a side effect of anything else.
#: `agent_runs` belongs here rather than with the derived data. Re-parsing a
#: statement should not throw away an analysis somebody paid a model to
#: produce - and unlike a recurring series, no amount of CPU brings it back.
_TIER_AI = ("agent_runs", "ai_inferences", "merchant_categories")

#: Authored by a human. Cannot be regenerated from any input at any price.
#: `claims` is listed before `claim_settlements` and `transaction_splits`
#: only for readability - both cascade from it anyway.
#: `custom_categories` and `recurring_series_overrides` belong here as much as
#: any other decision - a category someone invented and a series they renamed
#: cannot be regenerated from any statement. Leaving them out of every scope
#: meant a factory reset silently left them behind, so the workspace did not
#: actually return to its first-run state.
#: A dashboard someone assembled is authored, not derived: no statement, no
#: re-parse and no amount of CPU brings back a board of questions they wrote
#: themselves. Widgets are listed before their parent for readability only -
#: they cascade from `dashboards` either way.
_TIER_DECISIONS = ("user_overrides", "claim_settlements", "transaction_splits",
                   "custom_categories", "recurring_series_overrides",
                   "claims", "split_rules", "settlement_group_legs",
                   "settlement_groups", "dashboard_widgets", "dashboards",
                   "app_settings")

_TIER_IDENTITY = ("user_profile",)

#: Each scope is cumulative over the cheaper tiers below it: re-parsing has to
#: drop the derived data built on top of the rows it is replacing, and clearing
#: the file registry has to drop the rows parsed out of those files, or the
#: ledger would keep transactions whose provenance no longer exists.
CLEAR_SCOPES: dict[str, tuple[str, ...]] = {
    "derived": _TIER_DERIVED,
    "parsed_data": _TIER_DERIVED + _TIER_PARSED,
    "files": _TIER_DERIVED + _TIER_PARSED + _TIER_STAGING + _TIER_FILES,
    # Its own scope, because it is the one thing a rebuild cannot do without.
    "staged_imports": _TIER_STAGING,
    # What Process data replaces. Everything derived from documents, and
    # nothing else - notably NOT `jobs`, which "parsed_data" includes as
    # operational log. A rebuild runs INSIDE a job, so clearing that scope
    # deleted the row of the job doing the clearing: it then ran to completion
    # against a row that no longer existed, and the screen watching it waited
    # forever for a status that was never going to be written.
    "rebuild": ("transfer_pairs", "recurring_series", "analysis_runs")
               + _TIER_PARSED,
    "ai_inferences": _TIER_AI,
    "decisions": _TIER_DECISIONS,
    "everything": (_TIER_DERIVED + _TIER_PARSED + _TIER_STAGING + _TIER_FILES
                   + _TIER_AI + _TIER_DECISIONS + _TIER_IDENTITY),
}

#: Every table holding one user's data, parents before children. Row-level
#: security is applied to exactly this list (so a table added without being
#: listed here is loud rather than silently world-readable), and snapshots are
#: written in this order and restored in reverse.
TENANT_TABLES: tuple[str, ...] = (
    "accounts",
    "statements",
    "transactions",
    "user_profile",
    "custom_categories",
    "recurring_series",
    "recurring_series_overrides",
    "merchant_categories",
    "transfer_pairs",
    "analysis_runs",
    "source_files",
    "user_overrides",
    "ai_inferences",
    "claims",
    "claim_settlements",
    "transaction_splits",
    "split_rules",
    "settlement_groups",
    "settlement_group_legs",
    "dashboards",
    "dashboard_widgets",
    "agent_runs",
    "jobs",
    "job_items",
    "staged_files",
    "app_settings",
    "bureau_reports",
    "bureau_accounts",
    "portfolio_statements",
    "holdings",
)

#: Tables the auth layer owns. Not under row-level security, because they are
#: what a request is authenticated *against* - they have to be readable before
#: there is a tenant to read them as.
IDENTITY_TABLES: tuple[str, ...] = (
    "users", "user_sessions", "oauth_states", "google_tokens",
)

_SAFE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Parameter adaptation
#
# sqlite3 converted these on the way in, and the SQL was written expecting it.
# `oid = 0` means "server, infer the type from where this value is going",
# which is what keeps a Decimal usable against a TEXT money column without
# every call site remembering to str() it.
# ---------------------------------------------------------------------------

class _TextishDumper(Dumper):
    oid = 0

    def dump(self, obj: Any) -> bytes:
        return str(obj).encode()


class _DecimalDumper(_TextishDumper):
    """Money as its exact decimal string - never a float."""


class _DateDumper(_TextishDumper):
    """ISO-8601, matching what sqlite3's default adapter wrote."""


class _DatetimeDumper(Dumper):
    oid = 0

    def dump(self, obj: datetime) -> bytes:
        return obj.isoformat(" ").encode()


class _BoolDumper(Dumper):
    """'1'/'0', which PostgreSQL accepts for both integer and boolean columns.

    The flag columns on `transactions` are INTEGER (they were in SQLite, and
    `WHERE is_mirror_leg = 0` is written that way in a dozen places), so a
    Python bool arriving unconverted would otherwise be rejected outright.
    """

    oid = 0

    def dump(self, obj: bool) -> bytes:
        return b"1" if obj else b"0"


psycopg.adapters.register_dumper(Decimal, _DecimalDumper)
psycopg.adapters.register_dumper(date, _DateDumper)
psycopg.adapters.register_dumper(datetime, _DatetimeDumper)
psycopg.adapters.register_dumper(bool, _BoolDumper)


# ---------------------------------------------------------------------------


class Database:
    """The connection pool, the schema, and per-user snapshots.

    One instance per process. It holds no tenant of its own: which user's rows
    a call can see comes from `engine.TENANT`, bound per request by the auth
    middleware and per background job by `engine.tenant_scope`.
    """

    def __init__(self, dsn: str | None = None, *, ensure_schema: bool = True):
        self.dsn = dsn or config.DATABASE_URL
        self._pool = Pool(self.dsn, max_size=config.DB_POOL_SIZE)
        self._schema_ready = False
        if ensure_schema:
            self.ensure_schema()

    # ---- schema ----------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create anything missing, migrate anything old, re-apply the policies.

        Runs on every boot, in three parts, because each handles a case the
        others cannot:

          - `schema.sql` builds a new database. Every statement in it is
            IF NOT EXISTS, so on an existing one it only fills in objects that
            are entirely absent - a new table, a new index.
          - The migrations directory handles everything IF NOT EXISTS cannot:
            a new column on a table that already exists, a changed constraint.
            Applied in order and recorded, so each runs exactly once.
          - The row-level security policies are dropped and recreated, so a
            change to them ships with the code that needs it rather than
            needing a migration someone has to remember to write.
        """
        if self._schema_ready:
            return
        with self._pool.connection(None) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        self._apply_migrations()
        with self._pool.connection(None) as conn:
            self._apply_row_security(conn)
            assert_isolation_enforced(
                conn, allow_unenforced=config.ALLOW_UNENFORCED_ISOLATION)
        self._schema_ready = True
        log.info("schema ready on %s", _safe_dsn(self.dsn))

    def _apply_migrations(self) -> None:
        """Run any `migrations/NNNN_*.sql` this database has not seen.

        Each in its own transaction with its bookkeeping row, so a migration
        that fails leaves the database at the last one that succeeded rather
        than half-applied and recorded as done.
        """
        with self._pool.connection(None) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  version    TEXT PRIMARY KEY,"
                "  applied_at TEXT NOT NULL DEFAULT fa_now())")
            applied = {row["version"] for row in
                       conn.execute("SELECT version FROM schema_migrations")}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            with self._pool.connection(None) as conn:
                conn.executescript(path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)",
                    (path.name,))
            log.info("applied migration %s", path.name)

    @staticmethod
    def _apply_row_security(conn) -> None:
        """Put every tenant table behind `user_id = current_tenant()`.

        Generated from TENANT_TABLES rather than written out in schema.sql
        twenty-nine times, because the failure mode of a missed table is
        invisible: everything keeps working, and one user's ledger is simply
        readable by the next.

        FORCE is what makes this apply to the table's owner as well, which in
        a typical deployment is the very role the app connects as. Without it
        the owner is exempt and the policies are decoration.
        """
        present = {
            row["tablename"] for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        }
        for table in TENANT_TABLES:
            if table not in present:
                raise RuntimeError(
                    f"table {table!r} is listed in TENANT_TABLES but does not "
                    f"exist - schema.sql and that list have drifted apart")
            conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            conn.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            conn.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
            conn.execute(
                f"CREATE POLICY {table}_tenant ON {table}"
                f" USING (user_id = current_tenant())"
                f" WITH CHECK (user_id = current_tenant())")

    # ---- connections -----------------------------------------------------

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """A transactional connection scoped to the current tenant.

        Reentrant. Repository functions call one another, and each opens a
        `connection()` block of its own; against SQLite that meant a second
        connection to the same file, but against a pool it would mean waiting
        for a connection while holding one - a deadlock that only appears
        under load. A nested block therefore joins the transaction already in
        flight, wrapped in a SAVEPOINT so an inner failure the caller catches
        (see `repository.save_transactions`, which recovers from a bad row by
        retrying the batch one row at a time) does not poison the outer
        transaction the way a plain PostgreSQL error would.
        """
        existing = ACTIVE.get()
        if existing is not None:
            with existing.transaction():          # SAVEPOINT / RELEASE
                yield existing
            return

        self.ensure_schema()
        with self._pool.connection(TENANT.get()) as conn:
            token = ACTIVE.set(conn)
            try:
                yield conn
            finally:
                ACTIVE.reset(token)

    @contextmanager
    def identity_connection(self) -> Iterator[Any]:
        """A connection for the tables that authenticate a request.

        `users`, `user_sessions`, `oauth_states` and `google_tokens` sit
        outside row-level security - they have to be readable before there is
        a tenant to read them as. Kept to its own method, and its own short
        list of callers in `auth/`, so "which code can touch a table without a
        tenant" is a question with a greppable answer.
        """
        self.ensure_schema()
        with self._pool.connection(None) as conn:
            yield conn

    @contextmanager
    def as_tenant(self, user_id: str | None) -> Iterator[Any]:
        """`connection()`, but for a named user rather than the ambient one."""
        with tenant_scope(user_id):
            with self.connection() as conn:
                yield conn

    def known_tenants(self) -> list[str]:
        """Every user id, for maintenance that must sweep all of them.

        `users` is outside row-level security, so this is the one legitimate
        way to reach across tenants - and it hands back ids to be scoped to
        one at a time, never a connection that can see everybody's rows.
        """
        with self._pool.connection(None) as conn:
            return [str(row["id"]) for row in
                    conn.execute("SELECT id FROM users ORDER BY created_at")]

    def close(self) -> None:
        self._pool.close()

    # ---- snapshots -------------------------------------------------------

    @property
    def snapshot_dir(self) -> Path:
        """Where the current user's snapshots live.

        Per user, because a snapshot is now one person's ledger rather than
        the whole file, and a shared directory would let one user's restore
        list - or delete - another's backup.
        """
        tenant = self._require_tenant()
        return DATA_DIR / "backups" / tenant

    def _require_tenant(self) -> str:
        tenant = current_tenant()
        if not tenant:
            raise IsolationError(
                "this operation needs a signed-in user; no tenant is bound")
        return tenant

    def snapshot(self, label: str = "") -> Path:
        """Write the signed-in user's whole ledger to a file and return it.

        Every destructive action takes one of these first. Against SQLite this
        was a byte copy of the file, which is no longer either possible or
        correct - the file holds everybody now. It is a logical export instead:
        one gzipped JSON-lines document, tables in dependency order, produced
        by `COPY (SELECT ...) TO STDOUT`, so the row-level security policy
        decides what goes into it and the answer can only ever be this user's
        own rows.

        Column names travel with each row, so a snapshot taken before a column
        was added still restores afterwards.
        """
        tenant = self._require_tenant()
        directory = self.snapshot_dir
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = f".{label}" if label else ""
        target = directory / f"ledger{suffix}.{stamp}.jsonl.gz"

        rows_written = 0
        with self.connection() as conn:
            with gzip.open(target, "wt", encoding="utf-8") as out:
                out.write(json.dumps({
                    "__meta__": {
                        "version": 1,
                        "user_id": tenant,
                        "label": label,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                    },
                }) + "\n")
                for table in TENANT_TABLES:
                    out.write(json.dumps({"__table__": table}) + "\n")
                    # to_jsonb(t) - user_id: the owner is re-applied from the
                    # column default on restore, so a snapshot is not welded
                    # to the account that produced it.
                    copy_sql = (
                        f"COPY (SELECT (to_jsonb(t) - 'user_id')::text"
                        f"        FROM {table} t) TO STDOUT")
                    with conn.cursor() as cur, cur.copy(copy_sql) as copy:
                        for line in copy:
                            # COPY's text format escapes backslashes and tabs;
                            # JSON has neither at the top level of a row here,
                            # but unescaping is still what makes this exact.
                            out.write(_unescape_copy(bytes(line).decode()) + "\n")
                            rows_written += 1

        self._prune_snapshots(directory)
        log.info("snapshot written: %s (%d rows)", target.name, rows_written)
        return target

    def _prune_snapshots(self, directory: Path, keep: int = MAX_SNAPSHOTS) -> None:
        """Keep the newest `keep` automatic snapshots; never touch anything else.

        Only files this class created are considered - a backup the user made
        by hand, under any other name, is not ours to delete.
        """
        ours = sorted(directory.glob("ledger*.jsonl.gz"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in ours[keep:]:
            try:
                stale.unlink()
            except OSError:  # a locked or vanished file is not worth failing over
                log.warning("could not prune snapshot %s", stale.name)

    def list_snapshots(self) -> list[dict[str, object]]:
        directory = self.snapshot_dir
        if not directory.is_dir():
            return []
        out = []
        for p in sorted(directory.glob("ledger*.jsonl.gz"),
                        key=lambda p: p.stat().st_mtime, reverse=True):
            stat = p.stat()
            out.append({
                "name": p.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(
                    stat.st_mtime).isoformat(timespec="seconds"),
            })
        return out

    def _snapshot_path(self, name: str) -> Path:
        directory = self.snapshot_dir
        target = directory / name
        # Resolved and re-checked rather than trusting the name: this value
        # arrives from an HTTP request, and "../../someone-else/ledger.jsonl.gz"
        # would otherwise read another user's backup.
        if target.resolve().parent != directory.resolve() or not target.is_file():
            raise ValueError(f"no such snapshot: {name}")
        return target

    def restore(self, name: str) -> None:
        """Replace this user's rows with a named snapshot of them.

        Snapshots the current state first, so restoring is itself undoable and
        cannot be the thing that loses data. Runs as one transaction: a
        half-restored ledger would be worse than either end of the operation.
        """
        self._require_tenant()
        source = self._snapshot_path(name)
        self.snapshot("pre-restore")

        sections: dict[str, list[dict[str, Any]]] = {}
        current: list[dict[str, Any]] | None = None
        with gzip.open(source, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if "__meta__" in record:
                    continue
                if "__table__" in record:
                    table = record["__table__"]
                    if table not in TENANT_TABLES:
                        raise ValueError(
                            f"snapshot names an unknown table: {table!r}")
                    current = sections.setdefault(table, [])
                    continue
                if current is None:
                    raise ValueError("malformed snapshot: a row before any table")
                current.append(record)

        with self.connection() as conn:
            for table in reversed(TENANT_TABLES):
                conn.execute(f"DELETE FROM {table}")
            for table in TENANT_TABLES:
                rows = sections.get(table) or []
                if rows:
                    _insert_rows(conn, table, rows)
        log.info("restored %s from snapshot %s",
                 sum(len(v) for v in sections.values()), name)

    def delete_snapshot(self, name: str) -> None:
        self._snapshot_path(name).unlink()

    # ---- clearing --------------------------------------------------------

    def clear(self, scope: str) -> dict[str, int]:
        """Delete exactly one tier of the signed-in user's data, and report it.

        The tiers are ordered by what it costs to get the data back: parsed
        rows are pure CPU and can be rebuilt from the files, downloaded files
        cost network and Gmail quota (and are irreplaceable if the user no
        longer has the original), AI inference costs actual money, and a human
        decision cannot be regenerated at all. A single "delete everything"
        button cannot express that difference, which is how expensive data ends
        up being destroyed to fix a cheap problem.

        Every DELETE below is unqualified, and stays correct because the
        row-level security policy narrows it to the current user. That is also
        why `_require_tenant` runs first: with no tenant bound the policy
        matches nothing, and this would quietly report deleting zero rows
        instead of refusing.
        """
        if scope not in CLEAR_SCOPES:
            raise ValueError(
                f"unknown scope {scope!r}. Valid: {', '.join(CLEAR_SCOPES)}")
        self._require_tenant()

        removed: dict[str, int] = {}
        with self.connection() as conn:
            for table in CLEAR_SCOPES[scope]:
                before = conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                conn.execute(f"DELETE FROM {table}")
                if before:
                    removed[table] = before

            # If we wipe the ledger, the source_files that generated it should
            # revert to 'pending' so the file grid reflects reality after a
            # ledger clear. Failed / needs_password files get reset too -
            # clearing the ledger is a fresh start, and the user should be able
            # to see which files still need attention on the next parse pass.
            if scope in ("parsed_data", "everything"):
                conn.execute("""
                    UPDATE source_files
                    SET parse_status = 'pending',
                        transaction_count = 0,
                        error_message = '',
                        account_id = NULL,
                        statement_id = NULL
                    WHERE parse_status != 'pending'
                """)
        log.info("cleared scope=%s removed=%s", scope, removed)
        return removed

    def reset(self) -> None:
        """Clear parsed data, leaving files, AI inference and decisions intact.

        Kept because the test suite and several internal callers rely on it to
        get a clean ledger. It is deliberately NOT the widest scope any more -
        wiping a user's downloaded statements and paid-for inference is not
        something any caller should get by accident.
        """
        self.clear("parsed_data")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COPY_UNESCAPE = {"\\b": "\b", "\\f": "\f", "\\n": "\n", "\\r": "\r",
                  "\\t": "\t", "\\v": "\v", "\\\\": "\\"}


def _unescape_copy(text: str) -> str:
    """Undo COPY's text-format escaping of a single column."""
    if "\\" not in text:
        return text
    out: list[str] = []
    i = 0
    while i < len(text):
        pair = text[i:i + 2]
        if pair in _COPY_UNESCAPE:
            out.append(_COPY_UNESCAPE[pair])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _insert_rows(conn, table: str, rows: list[dict[str, Any]]) -> None:
    """Re-insert snapshot rows, grouped by the columns they actually carry.

    Plain INSERT rather than COPY FROM: COPY bypasses nothing about row-level
    security here, but it also cannot take the `user_id` column default, and
    letting the default stamp the owner is what keeps a restore incapable of
    writing into somebody else's account.
    """
    if not _SAFE_NAME.match(table):
        raise ValueError(f"unsafe table name: {table!r}")

    batches: dict[tuple[str, ...], list[list[Any]]] = {}
    for row in rows:
        columns = tuple(sorted(row))
        for column in columns:
            if not _SAFE_NAME.match(column):
                raise ValueError(f"unsafe column name: {column!r}")
        batches.setdefault(columns, []).append([row[c] for c in columns])

    for columns, values in batches.items():
        placeholders = ", ".join(["%s"] * len(columns))
        conn.cursor().executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )


def _safe_dsn(dsn: str) -> str:
    """A DSN with the password removed, for logs."""
    return re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", dsn)


_db: Database | None = None


def get_db(dsn: str | None = None) -> Database:
    """Process-wide singleton, created on first use."""
    global _db
    if _db is None or dsn is not None:
        _db = Database(dsn)
    return _db
