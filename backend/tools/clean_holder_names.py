"""Drop holder names that were never names.

`extract_metadata` used to accept any captured value with no long run of
digits as the account holder's name, which let a terms-and-conditions
sentence through. The reader is fixed; this clears what it already stored,
using the same predicate so there is only one definition of "a name".

Run once. A re-parse would also fix it, but re-reading every PDF to correct
one derived string is a poor trade.
"""
import sys

sys.path.insert(0, "/app")

from app.db.database import get_db, TENANT
from app.normalize.metadata import looks_like_a_persons_name


def main(user_id: str) -> int:
    TENANT.set(user_id)
    db = get_db()
    cleared = 0
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, institution, account_number_masked, holder_name"
            " FROM accounts WHERE holder_name IS NOT NULL"
            "   AND holder_name <> ''").fetchall()
        for row in rows:
            name = row["holder_name"]
            if looks_like_a_persons_name(name):
                print(f"  keep  {row['institution']:16s} {name}")
                continue
            print(f"  clear {row['institution']:16s} {name!r}")
            conn.execute("UPDATE accounts SET holder_name = NULL"
                         " WHERE id = ?", (row["id"],))
            cleared += 1
    return cleared


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: clean_holder_names.py <user_id>")
    print(f"cleared {main(sys.argv[1])} holder name(s)")
