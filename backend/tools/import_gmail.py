"""Download statements from Gmail into a persistent cache, then analyze them.

Statements are cached in data/gmail_cache/ keyed by Gmail's immutable message
and attachment ids, so re-running this only downloads what is genuinely new.

Usage:
    python backend/tools/import_gmail.py                 # scan + cache, no analysis
    python backend/tools/import_gmail.py --analyze       # cache then run the pipeline
    python backend/tools/import_gmail.py --include broker
    python backend/tools/import_gmail.py --max 500
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingestion.gmail_source import (DEFAULT_QUERY, GoogleGmailClient,  # noqa: E402
                                        download_to_cache, find_statements)

CREDENTIALS = ROOT / "credentials.json"
TOKEN = ROOT / "data" / "gmail_token.json"
CACHE = ROOT / "data" / "gmail_cache"

DEFAULT_CATEGORIES = ("bank", "card", "loan")


def _progress(i: int, total: int, att, cached: bool) -> None:
    tag = "cached" if cached else "downloaded"
    print(f"  [{i:3d}/{total}] {tag:10s} {att.filename[:52]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include", nargs="*", default=list(DEFAULT_CATEGORIES),
                        help="sender categories: bank card loan broker unknown")
    parser.add_argument("--max", type=int, default=400, help="messages to scan")
    parser.add_argument("--analyze", action="store_true",
                        help="run the analysis pipeline after caching")
    args = parser.parse_args()

    if not TOKEN.exists():
        print("Not connected. Run backend/tools/connect_gmail.py first.")
        return 1

    client = GoogleGmailClient(CREDENTIALS, TOKEN)
    client.authorize(interactive=False)

    print(f"Scanning up to {args.max} messages...")
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
    print(f"\nCaching {len(wanted)} files (~{total_mb:.1f} MB) into {CACHE}")
    saved = download_to_cache(client, wanted, CACHE, progress=_progress)

    fresh = sum(1 for a in saved if not a.from_cache)
    print(f"\n{len(saved)} files ready ({fresh} newly downloaded, "
          f"{len(saved) - fresh} already cached)")
    print(f"Cache location: {CACHE}")

    if not args.analyze:
        print("\nRe-run with --analyze to parse these into the ledger.")
        return 0

    # ---- Analysis ------------------------------------------------------
    from app.db.database import get_db
    from app.db import repository as repo
    from app.graph.build import build_graph
    from app.ingestion.passwords import derive_passwords

    profile = repo.get_profile(get_db())
    candidates = derive_passwords(profile)
    print(f"\nAnalyzing with {len(candidates)} password candidates from your profile...")

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
