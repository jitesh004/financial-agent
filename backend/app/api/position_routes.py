"""The Position tab's API: what the user says is true, and what it implies.

Read `GET /api/position` as the whole answer - every attested item aged to
today, the totals, and the credit accounts nothing accounts for. Everything
else here edits that: add a row, correct a field, map it to a statement or to
a bureau line, re-attest it, archive it, or freeze the lot as a snapshot.

One shape rule worth stating, because it is what keeps the screen honest: a
GET never returns a stored figure on its own. It returns the attested baseline
AND what that baseline must be today, with a sentence saying which is which.
The arithmetic between them lives in analytics.position, not here.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..analytics import position as position_mod
from ..db import repository as repo
from ..db.database import get_db
from ..models.schemas import LOAN_TYPES, AccountType

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/position", tags=["position"])


class ItemPayload(BaseModel):
    """Every editable field, all optional.

    Sent as a partial patch so correcting one figure never disturbs another,
    and `model_dump(exclude_unset=True)` is what makes "set this to null"
    distinguishable from "do not touch this".
    """

    kind: str | None = None
    label: str | None = None
    institution: str | None = None
    account_id: str | None = None
    bureau_account_id: str | None = None
    reviewed_on: str | None = None
    outstanding: str | float | None = None
    original_amount: str | float | None = None
    emi: str | float | None = None
    interest_rate: str | float | None = None
    months_remaining: int | None = None
    months_total: int | None = None
    credit_limit: str | float | None = None
    min_due: str | float | None = None
    statement_day: int | None = None
    due_day: int | None = None
    notes: str | None = None
    archived: bool | None = None
    sort_order: int | None = None


class ReviewPayload(BaseModel):
    reviewed_on: str | None = None
    note: str = ""
    #: Re-attest every item as part of taking the snapshot. This is the
    #: "I have been through all of it" button, as opposed to confirming one
    #: row at a time.
    review_all: bool = True


def _bureau_context(db) -> tuple[list[dict[str, Any]], date | None]:
    """The latest report's accounts, and the day it was pulled.

    The pull date matters as much as the balances: a bureau figure is
    routinely a month or two old, and dating a seeded row today would make a
    stale number look freshly confirmed.
    """
    reports = repo.get_bureau_reports(db)
    if not reports:
        return [], None
    latest = reports[0]
    pulled = latest.get("pulled_on")
    try:
        as_of = date.fromisoformat(str(pulled)[:10]) if pulled else None
    except ValueError:
        as_of = None
    return repo.get_bureau_accounts(db, latest["id"]), as_of


@router.get("")
def read_position(include_archived: bool = False) -> dict[str, Any]:
    db = get_db()
    bureau_accounts, _pulled = _bureau_context(db)
    return position_mod.build(
        repo.get_position_items(db, include_archived=include_archived),
        repo.get_accounts(db),
        bureau_accounts,
        include_archived=include_archived,
    )


@router.get("/mappable")
def mappable() -> dict[str, Any]:
    """What a position item can be pointed at, and what is already taken.

    Both lists come back whole rather than filtered to the unclaimed ones: a
    mapping being changed needs to offer the account it currently points at,
    and hiding it would make the picker unable to show its own value.
    """
    db = get_db()
    items = repo.get_position_items(db, include_archived=True)
    claimed_accounts = {i["account_id"]: i["id"] for i in items
                        if i.get("account_id")}
    claimed_bureau = {i["bureau_account_id"]: i["id"] for i in items
                      if i.get("bureau_account_id")}
    bureau_accounts, _pulled = _bureau_context(db)

    return {
        "accounts": [
            {"id": a.id, "name": a.display_name(),
             "type": a.account_type.value,
             "institution": a.institution,
             "masked": a.account_number_masked,
             "outstanding": _num(a.principal_outstanding),
             "balance": _num(a.current_balance),
             "credit_limit": _num(a.credit_limit),
             "claimed_by": claimed_accounts.get(a.id)}
            for a in repo.get_accounts(db) if a.id
        ],
        "bureau": [
            {"id": b["id"], "lender": b.get("lender"),
             "type": b.get("account_type"), "status": b.get("status"),
             "masked": b.get("account_number_masked"),
             "balance": b.get("current_balance"),
             "emi": b.get("emi_amount"),
             "credit_limit": b.get("credit_limit"),
             # What the bureau matcher already thinks this is, so a manual
             # mapping starts from the suggestion rather than from nothing.
             "suggested_account_id": b.get("account_id"),
             "match_status": b.get("match_status"),
             "claimed_by": claimed_bureau.get(b["id"])}
            for b in bureau_accounts
        ],
        "kinds": list(position_mod.KINDS),
    }


@router.post("/seed")
def seed_position() -> dict[str, Any]:
    """Draft the position from everything already known.

    Additive and re-runnable. Anything already in the position is left exactly
    alone, so this can be pressed again after importing a new statement to
    pick up an account that did not exist before - without touching a figure
    the user has since corrected.
    """
    from ..analytics.loans import project_loan

    db = get_db()
    existing = repo.get_position_items(db, include_archived=True)
    accounts = repo.get_accounts(db)
    transactions = repo.get_transactions(db)
    projections = [p for p in (
        project_loan(a, [t for t in transactions if t.account_id == a.id])
        for a in accounts if a.id) if p]
    bureau_accounts, pulled = _bureau_context(db)

    # When an account never declared a balance date, the last row on it is
    # the best available "as of" - see `seed`.
    last_activity: dict[str, Any] = {}
    for txn in transactions:
        if txn.excluded or not txn.account_id:
            continue
        current = last_activity.get(txn.account_id)
        if current is None or txn.txn_date > current:
            last_activity[txn.account_id] = txn.txn_date

    drafts = position_mod.seed(accounts, bureau_accounts, projections,
                               bureau_as_of=pulled,
                               last_activity=last_activity,
                               existing=existing)
    for draft in drafts:
        repo.save_position_item(db, draft)
    return {"status": "ok", "added": len(drafts),
            "already_present": len(existing)}


@router.post("/items")
def create_item(payload: ItemPayload) -> dict[str, Any]:
    body = payload.model_dump(exclude_unset=True)
    kind = body.get("kind") or "other"
    if kind not in position_mod.KINDS:
        raise HTTPException(
            400, f"'{kind}' is not a kind. Valid: "
                 f"{', '.join(position_mod.KINDS)}")
    body["kind"] = kind
    item_id = repo.save_position_item(get_db(), body)
    return {"status": "ok", "id": item_id}


@router.patch("/items/{item_id}")
def patch_item(item_id: str, payload: ItemPayload) -> dict[str, Any]:
    body = payload.model_dump(exclude_unset=True)
    if body.get("kind") and body["kind"] not in position_mod.KINDS:
        raise HTTPException(
            400, f"'{body['kind']}' is not a kind. Valid: "
                 f"{', '.join(position_mod.KINDS)}")
    db = get_db()
    if repo.get_position_item(db, item_id) is None:
        raise HTTPException(404, "No such position item.")
    repo.update_position_item(db, item_id, body)
    return {"status": "ok", "item": _aged(db, item_id)}


@router.post("/items/{item_id}/review")
def review_item(item_id: str, payload: ReviewPayload | None = None
                ) -> dict[str, Any]:
    """Re-baseline one item: this figure is true, as of this date.

    Distinct from a PATCH because it resets the roll-forward. Correcting a
    label must not silently re-attest a balance nobody looked at.
    """
    db = get_db()
    if repo.get_position_item(db, item_id) is None:
        raise HTTPException(404, "No such position item.")
    repo.review_position_item(db, item_id,
                              payload.reviewed_on if payload else None)
    return {"status": "ok", "item": _aged(db, item_id)}


@router.delete("/items/{item_id}")
def delete_item(item_id: str, permanent: bool = False) -> dict[str, Any]:
    if not repo.delete_position_item(get_db(), item_id, archive=not permanent):
        raise HTTPException(404, "No such position item.")
    return {"status": "ok", "archived": not permanent}


@router.post("/review")
def review_position(payload: ReviewPayload | None = None) -> dict[str, Any]:
    """Freeze the whole position as reviewed, on a date.

    The snapshot is the point. "This is my reality and I checked it myself"
    is only a claim that survives if there is a dated record of it - and it is
    also what a later roll-forward can be audited against when the next
    statement finally arrives and disagrees.
    """
    payload = payload or ReviewPayload()
    db = get_db()
    when = payload.reviewed_on or date.today().isoformat()

    if payload.review_all:
        for item in repo.get_position_items(db):
            repo.review_position_item(db, item["id"], when)

    bureau_accounts, _pulled = _bureau_context(db)
    built = position_mod.build(repo.get_position_items(db),
                               repo.get_accounts(db), bureau_accounts)
    snapshot_id = repo.save_position_snapshot(
        db, when, payload.note, built["items"], built["totals"])
    return {"status": "ok", "snapshot_id": snapshot_id,
            "reviewed_on": when, "items": len(built["items"])}


@router.get("/snapshots")
def list_snapshots(limit: int = 24) -> dict[str, Any]:
    return {"snapshots": repo.get_position_snapshots(get_db(), limit=limit)}


@router.get("/snapshots/{snapshot_id}")
def read_snapshot(snapshot_id: str) -> dict[str, Any]:
    snapshot = repo.get_position_snapshot(get_db(), snapshot_id)
    if snapshot is None:
        raise HTTPException(404, "No such snapshot.")
    return snapshot


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: str) -> dict[str, str]:
    if not repo.delete_position_snapshot(get_db(), snapshot_id):
        raise HTTPException(404, "No such snapshot.")
    return {"status": "ok"}


def _aged(db, item_id: str) -> dict[str, Any] | None:
    """One item, re-aged, so an edit answers with its own consequences.

    A user who corrects an EMI wants to see the payoff date move in the same
    round trip. Returning the stored row instead would make the screen
    re-fetch the whole position to show the effect of a single keystroke.
    """
    bureau_accounts, _pulled = _bureau_context(db)
    built = position_mod.build(repo.get_position_items(db,
                                                       include_archived=True),
                               repo.get_accounts(db), bureau_accounts,
                               include_archived=True)
    return next((i for i in built["items"] if i["id"] == item_id), None)


def _num(value: Any) -> float | None:
    from decimal import Decimal
    if value is None:
        return None
    return float(round(Decimal(str(value)), 2))
