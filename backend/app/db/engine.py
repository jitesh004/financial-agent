"""The PostgreSQL connection layer, and the one place tenancy is enforced.

This app used to be a single-user program writing to a SQLite file. It is now
a server several people sign in to, and that changes two things at once:
concurrency, and the fact that a query which forgets a `WHERE user_id = ...`
is no longer a bug in a report - it is one person reading another person's
bank statements.

Rather than trust ~2,200 lines of hand-written SQL to remember that clause,
isolation is delegated to the database. Every tenant table carries a
`user_id` with

    DEFAULT current_tenant()

and a row-level security policy of

    USING (user_id = current_tenant())

so a SELECT can only see the current tenant's rows, an INSERT that names no
owner is stamped with the current tenant, and an INSERT that names the wrong
one is rejected by the WITH CHECK half of the same policy. `current_tenant()`
reads a per-transaction GUC that this module sets from `TENANT`, a ContextVar
carrying the signed-in user for the duration of a request. Set nothing and the
GUC is NULL, the policy matches no rows, and every query returns empty - the
failure mode is an empty screen, never someone else's money.

That guarantee only holds against a role RLS actually applies to. PostgreSQL
exempts superusers and roles with BYPASSRLS unconditionally, and the tables
are declared FORCE ROW LEVEL SECURITY so it applies to their owner too.
`assert_isolation_enforced` checks this at startup and refuses to serve rather
than run wide open (see the note there for the one deliberate escape hatch).

Three smaller compatibility concerns live here as well, so the rest of the
codebase reads as it did:

  - `?` placeholders are rewritten to `%s`, and literal `%` (`LIKE 'UPI%'`)
    doubled, so the SQL in the repository stays in the dialect it was written
    and reviewed in.
  - `datetime('now')` becomes `fa_now()`, a function the schema defines to
    return exactly the 'YYYY-MM-DD HH:MM:SS' text SQLite produced.
  - Rows are indexable by position AND by column name, because `sqlite3.Row`
    was and both styles are used - positionally by the query engine, by name
    almost everywhere else.
"""

from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

import psycopg
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

#: The signed-in user whose rows the current task may touch. Read on every
#: connection checkout. None means "no tenant", which RLS turns into "no rows"
#: rather than "all rows".
TENANT: ContextVar[str | None] = ContextVar("fa_tenant", default=None)

#: The connection currently held by this task, so nested `connection()` blocks
#: join the transaction already in flight instead of taking a second
#: connection out of the pool and deadlocking against their own uncommitted
#: writes. See `Database.connection`.
ACTIVE: ContextVar[Any] = ContextVar("fa_active_connection", default=None)

#: Name of the per-transaction setting the RLS policies read. Custom GUCs must
#: contain a dot; `app.` is the conventional prefix for an application's own.
TENANT_GUC = "app.user_id"


@contextmanager
def tenant_scope(user_id: str | None) -> Iterator[None]:
    """Run a block as `user_id`.

    Needed wherever work leaves the request that authorised it: a background
    thread, the job flusher's timer, a startup sweep over every account. An
    HTTP request gets the same thing from the auth middleware.
    """
    token = TENANT.set(str(user_id) if user_id else None)
    try:
        yield
    finally:
        TENANT.reset(token)


def current_tenant() -> str | None:
    return TENANT.get()


# ---------------------------------------------------------------------------
# Dialect
# ---------------------------------------------------------------------------

#: One pass over a statement, in precedence order. Everything the translator
#: must NOT touch is a token here, so it can be recognised and passed through
#: whole: a string literal (`LIKE 'UPI%'` is data, `'?'` is a question mark), a
#: dollar-quoted body, and a comment - the repository is full of prose
#: describing the very constructs this rewrites.
#:
#: `datetime('now')` leads the alternation because it CONTAINS a string
#: literal. Matched second, the scanner would see `datetime(`, then a literal
#: `'now'`, then `)`, and the construct would never be recognised as one thing.
_TOKENS = re.compile(
    r"(?P<now>\bdatetime\s*\(\s*'now'\s*\))"
    r"|(?P<literal>'(?:[^']|'')*')"   # 'a string'' with an escaped quote'
    r"|(?P<dollar>\$\$.*?\$\$)"      # $$ dollar-quoted body $$
    r"|(?P<line>--[^\n]*)"            # -- line comment
    r"|(?P<block>/\*.*?\*/)",         # /* block comment */
    re.DOTALL | re.IGNORECASE,
)

_translation_cache: dict[tuple[str, bool], str] = {}
_translation_lock = threading.Lock()


def _rewrite_code(chunk: str, escape_percent: bool) -> str:
    """Rewrite one span of actual SQL - never a literal or a comment."""
    chunk = chunk.replace("?", "%s")
    if escape_percent:
        # A `%` that is not part of a `%s` we just wrote is a modulo operator
        # or a stray character, and psycopg would read it as a placeholder.
        chunk = re.sub(r"%(?!s\b)", "%%", chunk)
    return chunk


def translate(query: str, has_params: bool) -> str:
    """SQLite-flavoured SQL as PostgreSQL will accept it.

    `has_params` matters because psycopg only interprets `%` when it is given
    parameters to interpolate; doubling it in a statement executed bare would
    put literal `%%` into a LIKE pattern.
    """
    key = (query, has_params)
    cached = _translation_cache.get(key)
    if cached is not None:
        return cached

    out: list[str] = []
    position = 0
    for match in _TOKENS.finditer(query):
        out.append(_rewrite_code(query[position:match.start()], has_params))
        kind = match.lastgroup
        token = match.group(0)
        if kind == "now":
            token = "fa_now()"
        elif kind in ("literal", "dollar") and has_params:
            # The % is inside the literal, so it survived the rewrite above -
            # but psycopg still reads the finished string byte by byte and
            # would take it for a placeholder.
            token = token.replace("%", "%%")
        out.append(token)
        position = match.end()
    out.append(_rewrite_code(query[position:], has_params))

    translated = "".join(out)
    with _translation_lock:
        _translation_cache[key] = translated
    return translated


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

class Row:
    """A result row addressable by position or by column name.

    `sqlite3.Row` was both, and the codebase uses both: `analytics.query`
    reads `row[0]` because its columns are generated aliases, while the
    repository reads `row["institution"]`. Missing keys raise KeyError, which
    is what `repository._col` is written to catch.
    """

    __slots__ = ("_values", "_index")

    def __init__(self, values: tuple, index: dict[str, int]):
        self._values = values
        self._index = index

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, (int, slice)):
            return self._values[key]
        try:
            return self._values[self._index[key]]
        except KeyError:
            raise KeyError(key) from None

    def keys(self) -> list[str]:
        return list(self._index)

    # Yields values, not keys - matching sqlite3.Row. `dict(row)` still works
    # because dict() prefers the keys()/__getitem__ protocol when keys() exists.
    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, key: object) -> bool:
        return key in self._index

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._index, self._values))!r})"


def row_factory(cursor):
    description = cursor.description
    if description is None:            # a statement that returns nothing
        return lambda values: values
    index = {column.name: i for i, column in enumerate(description)}
    return lambda values: Row(values, index)


class Cursor(psycopg.Cursor):
    """A cursor that speaks the dialect the repository was written in."""

    def execute(self, query, params=None, **kwargs):        # type: ignore[override]
        if isinstance(query, str):
            query = translate(query, params is not None)
        return super().execute(query, params, **kwargs)

    def executemany(self, query, params_seq, **kwargs):      # type: ignore[override]
        if isinstance(query, str):
            query = translate(query, True)
        return super().executemany(query, params_seq, **kwargs)


class Connection(psycopg.Connection):
    """Adds the sqlite3 conveniences the codebase relies on.

    `sqlite3.Connection` offered `executemany` and `executescript` directly;
    psycopg only puts `execute` on the connection, so both are added here
    rather than rewritten at every call site.
    """

    cursor_factory = Cursor

    def executemany(self, query, params_seq):
        cursor = self.cursor()
        cursor.executemany(query, params_seq)
        return cursor

    def executescript(self, script: str) -> None:
        """Run several statements at once, as `sqlite3.Connection` did.

        psycopg sends a multi-statement string as one simple-query batch when
        no parameters are involved, which is exactly the semantics wanted for
        DDL.
        """
        with self.cursor() as cur:
            cur.execute(script)


# ---------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------

class Pool:
    """A lazily-opened psycopg connection pool that binds a tenant per checkout.

    Opened on first use rather than at construction so importing the app - in
    a test collection, a CLI tool, `--help` - does not require a database to
    be up.
    """

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10,
                 application_name: str = "financial-agent"):
        self.dsn = dsn
        self._pool = ConnectionPool(
            dsn,
            connection_class=Connection,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=30.0,
            # Pooled connections outlive database restarts, failovers and
            # idle-timeout disconnects, and a dead one handed to a request
            # fails it. The check is one round trip on checkout - cheap next
            # to the query about to follow, and the difference between a
            # restart being invisible and every open tab showing an error.
            check=ConnectionPool.check_connection,
            kwargs={
                "row_factory": row_factory,
                "cursor_factory": Cursor,
                "application_name": application_name,
                "autocommit": False,
            },
        )
        self._opened = False
        self._lock = threading.Lock()

    def _ensure_open(self) -> None:
        if self._opened:
            return
        with self._lock:
            if not self._opened:
                self._pool.open(wait=True, timeout=30.0)
                self._opened = True

    @contextmanager
    def connection(self, tenant: str | None) -> Iterator[Connection]:
        """A connection inside a transaction, scoped to `tenant`.

        `set_config(..., true)` is SET LOCAL: the tenant is attached to this
        transaction and gone the moment it ends, so a pooled connection can
        never carry one user's identity into the next user's request.
        """
        self._ensure_open()
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "SELECT set_config(%s, %s, true)", (TENANT_GUC, tenant or ""))
                yield conn

    def close(self) -> None:
        if self._opened:
            self._pool.close()
            self._opened = False


# ---------------------------------------------------------------------------
# Isolation self-check
# ---------------------------------------------------------------------------

class IsolationError(RuntimeError):
    """Raised when the database cannot enforce per-user separation."""


def assert_isolation_enforced(conn, *, allow_unenforced: bool = False) -> None:
    """Refuse to serve if row-level security would not actually apply.

    PostgreSQL exempts superusers and BYPASSRLS roles from every policy. Under
    such a role this app still runs, still passes its tests, and silently
    shows every user the whole database - which is the worst way for an
    isolation guarantee to fail, because nothing looks wrong. So it is checked
    once at startup and refused loudly.

    `allow_unenforced` exists for a genuinely single-user deployment where
    someone has decided the separate role is not worth it; it logs at WARNING
    every time rather than being quietly settable and forgotten.
    """
    row = conn.execute(
        "SELECT current_user AS role,"
        "       current_setting('is_superuser') = 'on' AS superuser,"
        "       COALESCE(rolbypassrls, false) AS bypassrls"
        "  FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if row is None:                     # cannot happen; treated as unsafe
        raise IsolationError("could not determine the database role in use")

    exempt = bool(row["superuser"]) or bool(row["bypassrls"])
    if not exempt:
        return

    reason = "a superuser" if row["superuser"] else "granted BYPASSRLS"
    message = (
        f"the database role {row['role']!r} is {reason}, so PostgreSQL will "
        f"ignore this app's row-level security and every signed-in user would "
        f"see every other user's financial data. Connect as an ordinary role "
        f"instead - see the app role created in docker-compose.yml, or run:\n"
        f"    CREATE ROLE financial_agent LOGIN PASSWORD '...';\n"
        f"    GRANT ALL ON DATABASE financial_agent TO financial_agent;\n"
        f"and point FA_DATABASE_URL at it."
    )
    if allow_unenforced:
        log.warning("PER-USER ISOLATION IS OFF: %s", message)
        return
    raise IsolationError(message)


__all__ = [
    "ACTIVE", "Connection", "Cursor", "IsolationError", "Pool", "Row",
    "TENANT", "TENANT_GUC", "assert_isolation_enforced", "current_tenant",
    "row_factory", "tenant_scope", "translate",
]
