"""Credit bureau reports and the investment portfolio.

Grouped in one router because they answer the two halves of the same question
the transaction ledger cannot answer on its own: what do I owe that I have no
statement for, and what do I own that never appears as a transaction.

Both are read-mostly. The write side is import (which goes through the file
pipeline like anything else) and one human decision - confirming or rejecting a
bureau account's link to a ledger account, which is deliberately never
automatic below a very high confidence.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException

from ..db.database import get_db
from ..db import repository as repo
from ..reconcile import bureau_match

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["wealth"])


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value not in (None, "") else Decimal("0")
    except Exception:
        return Decimal("0")


# --------------------------------------------------------------------------
# Credit bureau
# --------------------------------------------------------------------------

@router.get("/bureau")
def bureau_overview() -> dict[str, Any]:
    """Every report held, with the latest score per bureau."""
    db = get_db()
    reports = repo.get_bureau_reports(db)
    latest: dict[str, dict[str, Any]] = {}
    for report in reports:
        # Reports come back newest first, so the first of each bureau wins.
        latest.setdefault(report["bureau"], report)

    accounts = repo.get_bureau_accounts(db)
    open_accounts = [a for a in accounts if a["status"] == "open"]
    return {
        "reports": reports,
        "latest_by_bureau": list(latest.values()),
        "totals": {
            "accounts": len(accounts),
            "open_accounts": len(open_accounts),
            "outstanding": str(sum(_decimal(a["current_balance"])
                                   for a in open_accounts)),
            "overdue": str(sum(_decimal(a["overdue"]) for a in accounts)),
            "worst_dpd": max((a["worst_dpd"] for a in accounts), default=0),
        },
    }


@router.get("/bureau/accounts")
def bureau_accounts(report_id: str | None = None) -> list[dict[str, Any]]:
    return repo.get_bureau_accounts(get_db(), report_id)


@router.get("/bureau/reconciliation")
def bureau_reconciliation(report_id: str | None = None) -> dict[str, Any]:
    """What the bureau and the ledger do and do not agree about.

    The point of importing a bureau report at all: it is the only source that
    can name an account no statement has ever been imported for, and every
    total in this app is blind to those until something says so.
    """
    db = get_db()
    if report_id is None:
        reports = repo.get_bureau_reports(db)
        if not reports:
            return {"linked": [], "bureau_only": [], "ledger_only": [],
                    "balance_deltas": [], "counts": {}, "report": None}
        report_id = reports[0]["id"]

    stored = repo.get_bureau_accounts(db, report_id)
    ledger = repo.get_accounts(db)

    # The matcher works on objects with attributes; the repository hands back
    # dicts. Wrapped rather than reshaped so the matcher stays usable directly
    # against parsed reports too, before anything is saved.
    bureau_objects = [_Attr(row) for row in stored]
    matches = [
        bureau_match.Match(
            bureau_account_id=row["id"],
            account_id=row["account_id"],
            status=row["match_status"],
            confidence=row["match_confidence"],
            reason=row["match_reason"],
        )
        for row in stored
    ]
    result = bureau_match.reconcile(bureau_objects, ledger, matches)
    result["report"] = next(
        (r for r in repo.get_bureau_reports(db) if r["id"] == report_id), None)
    return result


class _Attr:
    """Read a dict as if it had attributes.

    The matcher is written against parsed report objects so it can run before
    anything is persisted; this lets the same code run against stored rows
    without a second implementation that could disagree with the first.
    """

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)


@router.post("/bureau/accounts/{bureau_account_id}/match")
def set_match(bureau_account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Confirm or reject a suggested link.

    A suggestion is never applied on its own: two cards from the same bank
    match each other's lender and type exactly, and guessing wrong puts one
    card's debt on the other's row.
    """
    confirmed = bool(payload.get("confirmed", True))
    account_id = payload.get("account_id")
    if confirmed and not account_id:
        raise HTTPException(400, "Confirming a match needs an account_id.")
    if not repo.set_bureau_match(get_db(), bureau_account_id, account_id,
                                 confirmed):
        raise HTTPException(404, "No such bureau account.")
    return {"status": "ok"}


@router.post("/bureau/rematch")
def rematch() -> dict[str, Any]:
    """Re-run matching over every stored report.

    Useful after new statements have been imported: an account that had nothing
    to match against last time may now have a partner. Decisions a human made
    are left alone.
    """
    db = get_db()
    ledger = repo.get_accounts(db)
    stored = repo.get_bureau_accounts(db)
    matches = bureau_match.match_accounts([_Attr(row) for row in stored], ledger)
    return {"status": "ok", "updated": repo.apply_bureau_matches(db, matches)}


# --------------------------------------------------------------------------
# Portfolio
# --------------------------------------------------------------------------

@router.get("/portfolio")
def portfolio() -> dict[str, Any]:
    """Current holdings, grouped and totalled.

    Every figure here is `units x NAV` as read off a statement that reconciled
    against its own printed total - never a live price, which this app has no
    way to verify and would have no way to reproduce later.
    """
    db = get_db()
    holdings = repo.get_holdings(db, latest_only=True)
    statements = repo.get_portfolio_statements(db)

    total_value = sum(_decimal(h["value"]) for h in holdings)
    total_invested = sum(_decimal(h["invested"]) or
                         (_decimal(h["units"]) * _decimal(h["avg_cost"]))
                         for h in holdings)

    by_kind: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        bucket = by_kind.setdefault(
            holding["kind"], {"kind": holding["kind"], "value": Decimal("0"),
                              "count": 0})
        bucket["value"] += _decimal(holding["value"])
        bucket["count"] += 1

    unreconciled = [s for s in statements if s["recon_status"] == "failed"]
    return {
        "holdings": holdings,
        "statements": statements,
        "totals": {
            "value": str(total_value),
            "invested": str(total_invested),
            # Unrealised only, and only where a cost basis was actually
            # printed. A "gain" computed against a missing cost is just the
            # value again wearing a different label.
            "gain": str(total_value - total_invested) if total_invested else None,
            "instruments": len(holdings),
            "as_of": max((h["as_of"] for h in holdings if h["as_of"]),
                         default=None),
        },
        "by_kind": [{**b, "value": str(b["value"])} for b in
                    sorted(by_kind.values(), key=lambda b: b["value"],
                           reverse=True)],
        "unreconciled": [
            {"filename": s["source_filename"], "message": s["recon_message"],
             "discrepancy": s["recon_discrepancy"]}
            for s in unreconciled
        ],
    }


@router.get("/portfolio/holdings")
def holdings(latest_only: bool = True) -> list[dict[str, Any]]:
    return repo.get_holdings(get_db(), latest_only=latest_only)
