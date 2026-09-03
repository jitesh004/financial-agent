"""The operator's view: who is on this deployment, and how much they use it.

Two constraints shape everything here.

**The grant is not self-service.** Access comes from FA_ADMIN_EMAILS in the
environment and nowhere else - see auth/deps.admin_user. Nothing in the
database or the UI can award it.

**It reports volumes, never amounts.** This app's central promise, stated on
its own front page, is that no query of one account can reach a row of
another's. An operator screen listing everybody's income would contradict
that in the one place it matters most, so what is counted here is
operational: how many statements each account has imported, how many rows
came out, which sources they use, how often they come back. Not what any of
it says. Widening that is a deliberate decision for whoever runs this, not a
default.

The mechanism is worth reading too. Row-level security applies to the role
this app connects as - the tables are FORCE ROW LEVEL SECURITY and the app
refuses to start under a role exempt from them (db/engine) - so there is no
"see everything" query available, and this does not add one. Each account's
figures are read with that account bound as the tenant, one at a time,
through exactly the mechanism every request uses. An admin therefore cannot
read a row the database would not hand to that account's own session; they
can only count them.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends

from ..auth.deps import admin_user
from ..auth.store import User
from ..config import config
from ..db import repository as repo
from ..db.database import get_db
from ..db.engine import tenant_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

#: How many accounts to read per-account figures for. The listing itself is
#: cheap; the per-account counts are a handful of queries each, so a
#: deployment with thousands of accounts gets the busiest ones and a total.
DETAIL_LIMIT = 200


def _accounts_overview() -> list[dict[str, Any]]:
    """Every real account, with its identity-side figures.

    One query. `demo_of IS NULL` excludes demo workspaces: they are generated
    data belonging to somebody's Demo switch, not people who signed up, and
    counting them as users would overstate every number on this page.
    """
    db = get_db()
    with db.identity_connection() as conn:
        rows = conn.execute(
            """SELECT u.id, u.email, u.name, u.status, u.created_at,
                      u.onboarding_step, u.onboarded_at, u.demo_mode,
                      (SELECT COUNT(*) FROM users d WHERE d.demo_of = u.id)
                          AS demo_workspaces,
                      (SELECT COUNT(*) FROM user_sessions s
                        WHERE s.user_id = u.id) AS sign_ins,
                      -- Requests served, which is what "how often do they
                      -- come back" actually means: one 72-hour cookie can
                      -- cover a week of visits, so counting sessions would
                      -- answer a different question.
                      (SELECT COALESCE(SUM(s.uses), 0) FROM user_sessions s
                        WHERE s.user_id = u.id) AS requests,
                      (SELECT MAX(s.last_used_at) FROM user_sessions s
                        WHERE s.user_id = u.id) AS last_seen,
                      (SELECT COUNT(*) FROM user_sessions s
                        WHERE s.user_id = u.id AND s.revoked_at IS NULL
                          AND s.expires_at > fa_now()) AS live_sessions,
                      (SELECT COUNT(*) FROM google_tokens g
                        WHERE g.user_id = u.id) AS gmail_connected
                 FROM users u
                WHERE u.demo_of IS NULL
                ORDER BY u.created_at DESC"""
        ).fetchall()

    return [
        {
            "id": str(row["id"]),
            "email": row["email"],
            "name": row["name"],
            "status": row["status"],
            "created_at": row["created_at"],
            "onboarding_step": row["onboarding_step"],
            "onboarded": bool(row["onboarded_at"]),
            "demo_mode": bool(row["demo_mode"]),
            "has_demo_workspace": bool(row["demo_workspaces"]),
            "sign_ins": int(row["sign_ins"] or 0),
            "requests": int(row["requests"] or 0),
            "last_seen": row["last_seen"],
            "live_sessions": int(row["live_sessions"] or 0),
            "gmail_connected": bool(row["gmail_connected"]),
            "is_admin": config.is_admin(row["email"]),
        }
        for row in rows
    ]


def _ledger_figures(user_id: str) -> dict[str, Any]:
    """One account's volumes, read as that account.

    Bound through `tenant_scope`, which is the same mechanism the auth
    middleware uses on every request - so this can count what that account's
    own session could see, and nothing else.
    """
    db = get_db()
    with tenant_scope(user_id):
        try:
            files = repo.list_source_files(db)
            accounts = repo.get_accounts(db)
            months = repo.covered_months(db)
            transactions = repo.count_transactions(db)
        except Exception:  # pragma: no cover - one account is not the page
            log.exception("could not read figures for %s", user_id)
            return {"unavailable": True}

    by_status = Counter(f.parse_status for f in files)
    # Where their documents come from. `source` is what put the file here -
    # a manual upload, the mailbox scan, or the demo generator.
    by_source = Counter(f.source or "unknown" for f in files)
    return {
        "transactions": transactions,
        "accounts": len(accounts),
        "institutions": sorted({a.institution for a in accounts if a.institution}),
        "account_types": dict(Counter(
            a.account_type.value for a in accounts)),
        "files": len(files),
        "files_by_status": dict(by_status),
        "sources": dict(by_source),
        "months_covered": len(months),
        "first_month": months[0][0] if months else None,
        "last_month": months[-1][0] if months else None,
    }


@router.get("/overview")
def overview(detail: bool = True, user: User = Depends(admin_user)
             ) -> dict[str, Any]:
    """Everything this page shows, in one request.

    `detail=false` skips the per-account ledger figures, which are the only
    expensive part - a handful of queries per account. Useful on a large
    deployment, or to check the listing alone is healthy.
    """
    accounts = _accounts_overview()

    if detail:
        # Busiest first, so the ones a cap leaves out are the quietest.
        ordered = sorted(accounts, key=lambda a: -a["requests"])
        for row in ordered[:DETAIL_LIMIT]:
            row["ledger"] = _ledger_figures(row["id"])

    signed_up = Counter((a["created_at"] or "")[:7] for a in accounts if a["created_at"])
    with_ledger = [a for a in accounts if (a.get("ledger") or {}).get("transactions")]

    return {
        "viewer": {"email": user.email, "name": user.display_name},
        "admins": list(config.ADMIN_EMAILS),
        "totals": {
            "accounts": len(accounts),
            "onboarded": sum(1 for a in accounts if a["onboarded"]),
            "never_returned": sum(1 for a in accounts if a["sign_ins"] <= 1),
            "signed_in_now": sum(1 for a in accounts if a["live_sessions"]),
            "gmail_connected": sum(1 for a in accounts if a["gmail_connected"]),
            "in_demo_mode": sum(1 for a in accounts if a["demo_mode"]),
            "requests": sum(a["requests"] for a in accounts),
            "sign_ins": sum(a["sign_ins"] for a in accounts),
            "transactions": sum(
                (a.get("ledger") or {}).get("transactions") or 0
                for a in accounts),
            "files": sum((a.get("ledger") or {}).get("files") or 0
                         for a in accounts),
            "with_a_ledger": len(with_ledger),
        },
        # Oldest month first, so a chart reads left to right.
        "signups_by_month": [
            {"month": month, "count": count}
            for month, count in sorted(signed_up.items()) if month
        ],
        "sources": _source_totals(accounts),
        "institutions": _institution_totals(accounts),
        "accounts": accounts,
        "detail_limit": DETAIL_LIMIT if detail else 0,
        "note": ("Counts only. No account's amounts, categories or "
                 "descriptions are read here - see the module docstring for "
                 "why that line is drawn where it is."),
    }


def _source_totals(accounts: list[dict[str, Any]]) -> dict[str, int]:
    """Which import routes are actually used, across the deployment."""
    out: Counter[str] = Counter()
    for row in accounts:
        for source, count in ((row.get("ledger") or {}).get("sources") or {}).items():
            out[source] += count
    return dict(out.most_common())


def _institution_totals(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which banks and issuers this deployment sees, and how many accounts
    hold one. Counted per ACCOUNT, not per statement: the useful figure is
    "seven people bank with this one", not "seven hundred files"."""
    out: Counter[str] = Counter()
    for row in accounts:
        for name in ((row.get("ledger") or {}).get("institutions") or []):
            out[name] += 1
    return [{"institution": name, "accounts": count}
            for name, count in out.most_common()]
