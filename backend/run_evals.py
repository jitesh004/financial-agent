#!/usr/bin/env python
"""Run the copilot evals against the real ledger.

Not part of the test suite, and deliberately so: every case spends real
model requests against a tier metered at 500 a day, so it runs when
someone asks for it rather than on every commit.

    docker exec financial-agent-backend-1 sh -c \\
      'cd /app && python run_evals.py <user-id>'

    ... --only fuel-one-month,total-spend-month
    ... --json
"""

import argparse
import json
import sys

from app.db.engine import TENANT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_id", help="whose ledger to ask about")
    parser.add_argument("--only", default="",
                        help="comma-separated case keys")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output")
    args = parser.parse_args()

    TENANT.set(args.user_id)

    from app.db.database import get_db
    from app.evals import run_evals
    from app.llm.client import get_client
    from app.llm import telemetry

    if not get_client().available:
        print("No language model is configured - nothing to evaluate.",
              file=sys.stderr)
        return 2

    db = get_db()
    keys = [k.strip() for k in args.only.split(",") if k.strip()] or None

    # Labelled so the run shows up on the Model Usage screen as its own
    # thing rather than inflating the agent tab. An eval sweep is not the
    # user asking questions, and counting it as such makes the cost of
    # actually using the app unreadable.
    with telemetry.purpose("eval", "copilot eval sweep"):
        report = run_evals(db, keys=keys)

    if args.json:
        print(json.dumps(report.as_json(), indent=1))
    else:
        print(report.render())

    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
