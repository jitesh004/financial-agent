"""Import an existing SQLite ledger into a PostgreSQL account.

The move off SQLite is not a reason to lose a ledger. This reads the old
`data/financial_agent.db` and writes every row into one user's account in
PostgreSQL - accounts, statements, transactions, the merchant cache, the
overrides and claims and dashboards someone authored, and the file registry -
then relocates the statement files and the Gmail cache under that user.

Usage:

    python backend/tools/migrate_sqlite_to_postgres.py --email you@example.com

`--email` names the account that will own the imported data. It must already
exist, which means signing in once first - the account is created by Google,
not by this script, because its identity IS a Google subject id and inventing
one here would produce a row nobody can ever sign into.

    --sqlite PATH   the old database (default: data/financial_agent.db)
    --dry-run       report what would be imported and change nothing
    --force         import even if the account already holds data

Safe to run twice: every table is written with ON CONFLICT DO NOTHING on the
key it already had, so a re-run after a partial failure fills in what is
missing rather than duplicating what landed.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import config                              # noqa: E402
from app.db.database import TENANT_TABLES, Database        # noqa: E402
from app.db.engine import tenant_scope                     # noqa: E402

#: Which column the destination table is keyed on, for the DO NOTHING clause.
#: `user_id` is prepended to each - the keys are per-user now.
CONFLICT_KEYS: dict[str, tuple[str, ...]] = {
    "accounts": ("id",),
    "statements": ("id",),
    "transactions": ("id",),
    "user_profile": ("id",),
    "custom_categories": ("name",),
    "recurring_series": ("id",),
    "recurring_series_overrides": ("series_id",),
    "merchant_categories": ("merchant_key",),
    "transfer_pairs": ("pair_id",),
    "analysis_runs": ("id",),
    "source_files": ("id",),
    "user_overrides": ("fingerprint",),
    "ai_inferences": ("cache_key",),
    "claims": ("id",),
    "claim_settlements": ("id",),
    "transaction_splits": ("id",),
    "split_rules": ("id",),
    "settlement_groups": ("id",),
    "settlement_group_legs": ("group_id", "fingerprint"),
    "dashboards": ("id",),
    "dashboard_widgets": ("id",),
    "jobs": ("id",),
    "job_items": ("job_id", "seq"),
    "staged_files": ("id",),
    "app_settings": ("key",),
    "bureau_reports": ("id",),
    "bureau_accounts": ("id",),
    "portfolio_statements": ("id",),
    "holdings": ("id",),
}

#: Rows are inserted in bounded batches so a decade of transactions does not
#: build one statement with a million parameters in it.
BATCH = 500


def read_table(source: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    """Every row of `table`, or nothing if the old database never had it."""
    exists = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,)).fetchone()
    if not exists:
        return [], []
    cursor = source.execute(f"SELECT * FROM {table}")
    columns = [d[0] for d in cursor.description]
    return columns, cursor.fetchall()


def importable_columns(target, table: str, columns: list[str]) -> list[str]:
    """The old columns that still exist in the new schema.

    A column the port dropped is left behind rather than failing the import,
    and `user_id` never comes from the source - the destination's own default
    stamps the owner, which is what makes it impossible for this script to
    write into the wrong account.
    """
    present = {row["column_name"] for row in target.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = %s", (table,))}
    return [c for c in columns if c in present and c != "user_id"]


def copy_table(target, table: str, columns: list[str], rows: list[tuple],
               source_columns: list[str]) -> int:
    if not rows or not columns:
        return 0
    picks = [source_columns.index(c) for c in columns]
    keys = ", ".join(("user_id", *CONFLICT_KEYS[table]))
    placeholders = ", ".join(["%s"] * len(columns))
    statement = (f"INSERT INTO {table} ({', '.join(columns)})"
                 f" VALUES ({placeholders})"
                 f" ON CONFLICT ({keys}) DO NOTHING")

    written = 0
    cursor = target.cursor()
    for start in range(0, len(rows), BATCH):
        batch = [[row[i] for i in picks] for row in rows[start:start + BATCH]]
        cursor.executemany(statement, batch)
        written += len(batch)
    return written


def move_files(old_root: Path, new_root: Path, user_id: str, dry_run: bool) -> int:
    """Put the statement store and the Gmail cache under the owner.

    Copied rather than moved: the old tree is left exactly as it was, so a
    migration that turns out to have gone wrong can simply be re-run.
    """
    moved = 0
    for name in ("statements", "gmail_cache"):
        source = old_root / name
        if not source.is_dir():
            continue
        destination = new_root / name / user_id
        for path in source.rglob("*"):
            if not path.is_file() or path.name.endswith(".part"):
                continue
            relative = path.relative_to(source)
            # An already-migrated tree has the user directory in it already;
            # do not nest a second copy inside itself.
            if relative.parts and relative.parts[0] == user_id:
                continue
            target = destination / relative
            if target.exists():
                continue
            moved += 1
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True,
                        help="the signed-up account that will own the data")
    parser.add_argument("--sqlite", default=str(ROOT / "data" / "financial_agent.db"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="import even if the account already holds rows")
    args = parser.parse_args()

    old = Path(args.sqlite)
    if not old.is_file():
        print(f"no SQLite database at {old}", file=sys.stderr)
        return 1

    db = Database()
    with db.identity_connection() as conn:
        row = conn.execute("SELECT id, email FROM users WHERE lower(email) = %s",
                           (args.email.lower(),)).fetchone()
    if row is None:
        print(f"no account for {args.email}. Sign in once with that Google "
              f"account first - this script fills an existing account, it "
              f"cannot create one.", file=sys.stderr)
        return 1
    user_id = str(row["id"])

    source = sqlite3.connect(f"file:{old}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row

    print(f"importing {old} into {row['email']} ({user_id})")
    if args.dry_run:
        print("(dry run - nothing will be written)")

    total = 0
    with tenant_scope(user_id):
        with db.connection() as target:
            existing = target.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
            if existing and not args.force:
                print(f"that account already holds {existing} transactions. "
                      f"Re-run with --force to import anyway.", file=sys.stderr)
                return 1

            for table in TENANT_TABLES:
                source_columns, rows = read_table(source, table)
                if not rows:
                    continue
                columns = importable_columns(target, table, source_columns)
                dropped = set(source_columns) - set(columns) - {"user_id"}
                if args.dry_run:
                    print(f"  {table:<28} {len(rows):>7} rows"
                          + (f"  (ignoring {', '.join(sorted(dropped))})"
                             if dropped else ""))
                    total += len(rows)
                    continue
                written = copy_table(target, table, columns, rows, source_columns)
                total += written
                print(f"  {table:<28} {written:>7} rows"
                      + (f"  (ignored {', '.join(sorted(dropped))})"
                         if dropped else ""))

    # The old statement store and Gmail cache sat beside the SQLite file, so
    # that is where to look for them - not at ROOT/data, which is where they
    # are going and may already be the same place.
    files = move_files(old.parent, Path(config.DATA_DIR), user_id, args.dry_run)
    source.close()

    print(f"\n{total} rows and {files} files "
          f"{'would be imported' if args.dry_run else 'imported'}.")
    if not args.dry_run:
        print("The SQLite file and the original file tree are untouched - "
              "keep them until you have checked the figures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
