"""Download statements from Gmail into a persistent cache, then analyze them.

The same import the app does, from a shell. Everything is scoped to ONE
account, named by `--email`: files land in that user's cache, rows in that
user's ledger, and the Gmail grant read is the one that user made in the
browser. There is no "the user" any more, so there is no default.

Statements are cached under data/gmail_cache/<user>/ keyed by Gmail's
immutable message and attachment ids, so re-running this only downloads what
is genuinely new.

Usage:
    python backend/tools/import_gmail.py --email you@example.com
    python backend/tools/import_gmail.py --email you@example.com --analyze
    python backend/tools/import_gmail.py --email you@example.com --include broker
    python backend/tools/import_gmail.py --email you@example.com --max 500
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.auth import store as auth_store  # noqa: E402
from app.db.database import get_db  # noqa: E402
from app.db.engine import tenant_scope  # noqa: E402
from app.ingestion.gmail_source import (DEFAULT_QUERY, GoogleGmailClient,  # noqa: E402
                                        download_to_cache, find_statements)
from app.storage import gmail_cache  # noqa: E402

DEFAULT_CATEGORIES = ("bank", "card", "loan")


class _StoredToken:
    """The Gmail grant one user made in the browser."""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def load(self):
        return auth_store.get_google_token(get_db(), self.user_id)

    def save(self, token_json: str) -> None:
        auth_store.save_google_token(get_db(), self.user_id, token_json)


def _resolve_user(email: str):
    with get_db().identity_connection() as conn:
        return conn.execute(
            "SELECT id, email FROM users WHERE lower(email) = %s",
            (email.lower(),)).fetchone()


def _progress(i: int, total: int, att, cached: bool) -> None:
    tag = "cached" if cached else "downloaded"
    print(f"  [{i:3d}/{total}] {tag:10s} {att.filename[:52]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True,
                        help="whose mailbox and whose ledger")
    parser.add_argument("--include", nargs="*", default=list(DEFAULT_CATEGORIES),
                        help="sender categories: bank card loan broker unknown")
    parser.add_argument("--max", type=int, default=400, help="messages to scan")
    parser.add_argument("--analyze", action="store_true",
                        help="run the analysis pipeline after caching")
    args = parser.parse_args()

    user = _resolve_user(args.email)
    if user is None:
        print(f"No account for {args.email}. Sign in once in the browser first.")
        return 1
    user_id = str(user["id"])

    client = GoogleGmailClient(_StoredToken(user_id))
    if not client.authorize():
        print(f"{args.email} has not connected Gmail. Open the app and connect "
              f"it from the import screen - consent happens on Google\'s own "
              f"page and cannot be done from a shell.")
        return 1

    print(f"Scanning up to {args.max} messages as {user['email']}...")
    result = find_statements(client, query=DEFAULT_QUERY, max_messages=args.max)
    print(f"  {result.scanned_messages} messages, {len(result.attachments)} statement PDFs")

    by_category = Counter(a.category for a in result.attachments)
    print("\nBy category:")
    for category, count in by_category.most_common():
        mark = "include" if category in args.include else "SKIP"
        print(f"  {category:10s} {count:4d}   {mark}")

    wanted = [a for a in result.attachments if a.category in args.include]
    if not wanted:
        print("\nNothing matched the requested categories.")
        return 1

    total_mb = sum(a.size for a in wanted) / 1024 / 1024
    with tenant_scope(user_id):
        cache = gmail_cache()
    print(f"\nCaching {len(wanted)} files (~{total_mb:.1f} MB) into {cache}")
    saved = download_to_cache(client, wanted, cache, progress=_progress)

    fresh = sum(1 for a in saved if not a.from_cache)
    print(f"\n{len(saved)} files ready ({fresh} newly downloaded, "
          f"{len(saved) - fresh} already cached)")
    print(f"Cache location: {cache}")

    if not args.analyze:
        print("\nRe-run with --analyze to parse these into the ledger.")
        return 0

    # ---- Analysis ------------------------------------------------------
    from app.db import repository as repo
    from app.graph.build import build_graph
    from app.ingestion.passwords import derive_passwords

    # Everything from here writes rows, so it runs inside the tenant. Without
    # this the graph would parse the files perfectly and persist nothing - the
    # row-level security policy has no owner to match.
    with tenant_scope(user_id):
        profile = repo.get_profile(get_db())
        candidates = derive_passwords(profile)
        print(f"\nAnalyzing with {len(candidates)} password candidates "
              f"from your profile...")

        tasks = [{"path": a.saved_path, "filename": a.filename} for a in saved]
        state = build_graph().invoke(
            {"file_tasks": tasks, "password_candidates": candidates,
             "use_llm": False, "horizon_months": 6},
            {"recursion_limit": 200},
        )

    statuses = Counter(s.get("status") for s in state.get("statements", []))
    print("\nParse results:")
    for status, count in statuses.most_common():
        print(f"  {status:16s} {count}")

    print(f"\naccounts detected : {len(state.get('accounts', {}))}")
    print(f"transactions      : {len(state.get('transactions', []))}")

    analysis = state.get("analysis")
    if analysis and analysis.transaction_count:
        print(f"period            : {analysis.period_start} -> {analysis.period_end}")
        print(f"income            : {analysis.total_income:,.2f}")
        print(f"spend             : {analysis.total_spend:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
