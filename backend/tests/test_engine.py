"""The database engine: dialect translation, row shape, schema evolution.

None of this is domain logic, and all of it is load-bearing. A translation bug
turns a correct query into a wrong one silently, a row that stops being
indexable by position breaks the query engine in a way no unit test of the
query engine would catch, and a migration that runs twice is how a deployment
that worked yesterday fails on restart.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from tests.support import fresh_ledger  # noqa: E402
from app.db import database as db_module  # noqa: E402
from app.db.engine import Row, translate  # noqa: E402


# --------------------------------------------------------------------------
# Dialect
# --------------------------------------------------------------------------

def test_placeholders_are_rewritten():
    assert translate("SELECT * FROM t WHERE a = ? AND b = ?", True) == \
        "SELECT * FROM t WHERE a = %s AND b = %s"


def test_a_literal_percent_survives_parameter_interpolation():
    """`LIKE 'UPI%'` is real SQL in analytics/query.py. psycopg reads the
    finished string byte by byte and would take that % for a placeholder."""
    assert translate("SELECT * FROM t WHERE d LIKE 'UPI%' AND x = ?", True) == \
        "SELECT * FROM t WHERE d LIKE 'UPI%%' AND x = %s"


def test_a_literal_percent_is_left_alone_when_there_are_no_parameters():
    """psycopg only interprets % when it is given something to interpolate.
    Doubling it in a bare statement would put a literal %% into the pattern."""
    assert translate("SELECT * FROM t WHERE d LIKE 'UPI%'", False) == \
        "SELECT * FROM t WHERE d LIKE 'UPI%'"


def test_a_question_mark_inside_a_string_is_not_a_placeholder():
    assert translate("SELECT '?' AS q WHERE a = ?", True) == \
        "SELECT '?' AS q WHERE a = %s"


def test_an_escaped_quote_does_not_end_the_literal():
    assert translate("SELECT 'it''s ? fine' WHERE a = ?", True) == \
        "SELECT 'it''s ? fine' WHERE a = %s"


def test_datetime_now_becomes_the_schema_function():
    assert translate("UPDATE t SET at = datetime('now')", False) == \
        "UPDATE t SET at = fa_now()"


def test_datetime_now_is_recognised_even_though_it_contains_a_literal():
    """It has to be matched before the `'now'` inside it is taken for a plain
    string, or the construct is never seen as one thing and survives into
    PostgreSQL as a call to a function that does not exist."""
    assert translate(
        "UPDATE t SET a = ?, at = datetime('now') WHERE f = ?", True) == \
        "UPDATE t SET a = %s, at = fa_now() WHERE f = %s"


def test_a_string_that_merely_says_datetime_now_is_left_alone():
    assert translate("SELECT 'datetime(''now'')' AS prose", False) == \
        "SELECT 'datetime(''now'')' AS prose"


def test_a_comment_is_not_rewritten():
    """The repository is full of prose in `--` comments, some of it mentioning
    the very constructs this translator rewrites."""
    sql = "SELECT a -- was datetime('now'), keyed on ?\nFROM t WHERE b = ?"
    out = translate(sql, True)
    assert "-- was datetime('now'), keyed on ?" in out
    assert "WHERE b = %s" in out


def test_translation_of_the_same_statement_is_stable():
    sql = "SELECT * FROM t WHERE a = ? AND d LIKE 'x%'"
    assert translate(sql, True) == translate(sql, True)
    # And the two parameter modes do not share a cache entry.
    assert translate(sql, True) != translate(sql, False)


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

def _row() -> Row:
    return Row(("a1", "HDFC", 42), {"id": 0, "institution": 1, "count": 2})


def test_a_row_is_readable_by_position_and_by_name():
    """Both, because sqlite3.Row was both and the codebase uses both:
    analytics/query.py reads row[0] over generated aliases, the repository
    reads row["institution"]."""
    row = _row()
    assert row[0] == "a1"
    assert row["institution"] == "HDFC"
    assert row[-1] == 42


def test_a_missing_column_raises_keyerror():
    """What `repository._col` is written to catch, so a partial SELECT falls
    back to a field default instead of crashing."""
    with pytest.raises(KeyError):
        _row()["nope"]


def test_a_row_converts_to_a_dict():
    assert dict(_row()) == {"id": "a1", "institution": "HDFC", "count": 42}


def test_a_row_iterates_over_its_values():
    """Matching sqlite3.Row - `list(row)` is the values, not the keys."""
    assert list(_row()) == ["a1", "HDFC", 42]
    assert len(_row()) == 3
    assert "institution" in _row()


# --------------------------------------------------------------------------
# Schema evolution
# --------------------------------------------------------------------------

def test_a_migration_runs_once_and_is_recorded(tmp_path, monkeypatch):
    """schema.sql is all IF NOT EXISTS, so it can never add a column to a
    table that already exists. That is what this directory is for, and running
    one twice has to be impossible rather than merely unlikely."""
    db = fresh_ledger()
    monkeypatch.setattr(db_module, "MIGRATIONS_DIR", tmp_path)
    (tmp_path / "0001_add_a_column.sql").write_text(
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS nickname TEXT;")

    db._apply_migrations()
    db._apply_migrations()          # must be a no-op, not an error

    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            ("0001_add_a_column.sql",)).fetchone()[0] == 1
        columns = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'accounts'")}
    assert "nickname" in columns

    with db.connection() as conn:
        conn.execute("ALTER TABLE accounts DROP COLUMN nickname")
        conn.execute("DELETE FROM schema_migrations WHERE version = ?",
                     ("0001_add_a_column.sql",))


def test_a_failing_migration_is_not_recorded_as_applied(tmp_path, monkeypatch):
    """Each migration commits with its own bookkeeping row, so a failure
    leaves the database at the last one that worked rather than skipping the
    broken one forever."""
    db = fresh_ledger()
    monkeypatch.setattr(db_module, "MIGRATIONS_DIR", tmp_path)
    (tmp_path / "0002_broken.sql").write_text("ALTER TABLE nope ADD COLUMN x TEXT;")

    with pytest.raises(Exception):
        db._apply_migrations()

    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            ("0002_broken.sql",)).fetchone()[0] == 0


def test_the_shipped_migrations_all_applied():
    """Whatever is in migrations/ has run against the test database - which is
    also the check that none of them is malformed."""
    db = fresh_ledger()
    on_disk = {p.name for p in db_module.MIGRATIONS_DIR.glob("*.sql")}
    with db.connection() as conn:
        applied = {r["version"] for r in
                   conn.execute("SELECT version FROM schema_migrations")}
    assert on_disk <= applied
