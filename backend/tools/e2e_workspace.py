"""Prepare a workspace for the browser suite, and print a session for it.

Run:  python backend/tools/e2e_workspace.py [--demo | --empty]

The end-to-end suite (`frontend-v2/e2e/run.mjs`) drives the real app in a real
browser, which means it needs a real signed-in session - and sign-in goes
through Google, which a headless test cannot do and should not have to. So the
session is minted here, against a database of the tool's own choosing, and
handed to the browser as a cookie.

  --demo   a workspace with the generated demo ledger already in it, for the
           checks that need something to look at.
  --empty  nothing at all, for the checks that import statements themselves.

Point FA_DATABASE_URL at a THROWAWAY database. This writes to it, and the
suite writes to it a great deal more.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true",
                        help="seed the generated demo ledger (default)")
    parser.add_argument("--empty", action="store_true",
                        help="leave the workspace empty")
    parser.add_argument("--email", default="e2e@example.invalid")
    args = parser.parse_args()

    url = os.environ.get("FA_DATABASE_URL")
    if not url:
        print("set FA_DATABASE_URL to a throwaway database owned by an ordinary "
              "(non-superuser) role", file=sys.stderr)
        return 2

    from app.config import config

    config.DATABASE_URL = url
    config.DATA_DIR = os.environ.get("FA_DATA_DIR", config.DATA_DIR)

    from app.db import database

    database.DATA_DIR = Path(config.DATA_DIR)
    database._db = database.Database(url)

    from app import demo
    from app.auth import store
    from app.db.database import get_db
    from app.db.engine import TENANT

    db = get_db()
    user = store.upsert_user(
        db, google_sub="e2e-workspace", email=args.email, email_verified=True,
        name="Ada Lovelace", picture="")
    TENANT.set(user.id)

    # Past the wizard: what the suite is about is the app, not the setup flow,
    # which has tests of its own.
    with db.identity_connection() as conn:
        conn.execute(
            "UPDATE users SET onboarding_step='done', onboarded_at=fa_now() "
            "WHERE id = ?", (user.id,))

    if not args.empty:
        demo.seed(db, user.id)

    token = store.create_session(db, user.id, ttl_hours=6)
    print(f"FA_SESSION_COOKIE={config.SESSION_COOKIE}")
    print(f"FA_SESSION={token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
