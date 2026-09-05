"""Settings, and the one action that spends money.

Calling a language model costs real money, so `use_llm` is off until somebody
turns it on and stays off across every import until they do. That is why the
switch lives in the database rather than in the browser: the server is what
decides whether a request is made, and a preference the browser owns is a
preference the server cannot honour.

Running the categoriser is a job like any other - progress, a per-item trace,
and a durable record - because it is the same shape of work as an import: slow,
worth watching, and something you should be able to walk away from.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from ..auth.deps import current_user
from ..auth.store import User
from ..db.database import get_db
from ..db import repository as repo
from ..jobs import JobProgress, jobs

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])



def _awaiting_a_category(txn) -> bool:
    """Whether asking a model about this row would change any figure.

    Uncategorised is not enough on its own. A row somebody has taken out of
    every total - a parser artifact, a reversed charge, an explicit "leave
    this out" - stays uncategorised for good reason, and its category is
    never read again. Counting those made the Settings page offer to
    categorise 301 rows against the 293 the Overview said were missing from
    the breakdown, and the eight in between would have cost model calls to
    label something nothing displays.
    """
    from ..models.schemas import NEUTRAL_ROLES

    if txn.category != "uncategorized":
        return False
    role = getattr(txn, "flow_role", "") or ""
    return role not in {r.value for r in NEUTRAL_ROLES}


def _llm_status() -> dict[str, Any]:
    """Which provider is selected, and whether it could actually be called.

    Reuses main's own check rather than repeating the per-provider key logic:
    two answers to "is a model reachable?" would eventually disagree, and the
    one the Settings screen shows has to be the one the run obeys.
    """
    try:
        from ..main import _llm_status as status
        provider, configured = status()
    except Exception:  # pragma: no cover - settings must never 500
        provider, configured = "unknown", False
    return {"llm_provider": provider, "llm_configured": bool(configured)}


def _settings_payload() -> dict[str, Any]:
    """One shape for both reading and writing.

    Returning a narrower body from PUT than from GET looks harmless and is
    not: the client replaces its state with the response, so saving the switch
    dropped `uncategorized_count`, which zeroed the count on screen and
    disabled the very button the switch had just enabled.
    """
    db = get_db()
    pending = sum(1 for t in repo.get_transactions(db)
                  if _awaiting_a_category(t))
    return {**repo.get_settings(db), **_llm_status(),
            "uncategorized_count": pending}


@router.get("")
def read_settings() -> dict[str, Any]:
    return _settings_payload()


@router.put("")
def write_settings(payload: dict[str, Any]) -> dict[str, Any]:
    repo.save_settings(get_db(), payload)
    return _settings_payload()


# --------------------------------------------------------------------------
# Running the categoriser
# --------------------------------------------------------------------------

@router.post("/categorize")
def start_categorize(background: BackgroundTasks,
                     payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Categorise everything the rules could not.

    Refuses when the switch is off rather than quietly turning it on: the
    whole point of the switch is that nothing calls a paid model without an
    explicit decision, and "the user clicked a button somewhere" is not the
    same decision.
    """
    db = get_db()
    settings = repo.get_settings(db)
    if not settings.get("use_llm"):
        raise HTTPException(
            400, "Model categorisation is switched off. Turn it on in "
                 "Settings first - it calls a paid API.")

    status = _llm_status()
    if not status["llm_configured"]:
        raise HTTPException(
            400, "No model provider is configured, so there is nothing to "
                 "call. Add an API key to your .env and restart the API.")

    pending = [t for t in repo.get_transactions(db)
               if _awaiting_a_category(t)]
    if not pending:
        raise HTTPException(400, "Nothing is uncategorised.")

    job = jobs.create("categorize", total=len(pending), phase="Queued",
                      request={"count": len(pending)})
    background.add_task(_run_categorize, job.id)
    return {"job_id": job.id, "count": len(pending)}


def _model_note(run: dict[str, int], from_model: int, still_pending: int) -> str:
    """Why nothing came back from the model, when nothing did.

    Silence and a refusal look identical in the counters and mean opposite
    things. "AYMENT", "HAS" and two rows with no description at all are not a
    broken API key - they are a model correctly declining to guess, and
    pointing at the API log over that wastes the reader's afternoon.
    """
    if from_model or not still_pending:
        return ""
    if run["failed_batches"]:
        return " The model could not be reached - check the API log."
    if run["declined"]:
        n = run["declined"]
        return (f" The model read {n} of them and recognised"
                f" {'neither' if n == 2 else 'none'}"
                f"; {'they need' if n > 1 else 'it needs'} a rule or a manual"
                " category.")
    if run.get("unevidenced"):
        # A refused answer is not silence either, and it is the one case
        # where the reader would otherwise go looking for a broken key over
        # the app working exactly as intended.
        n = run["unevidenced"]
        return (f" The model called {n} of them a loan or a card bill with"
                f" nothing in the name to support it, so"
                f" {'those answers' if n > 1 else 'that answer'} was refused"
                f" - see rules.instalments. They need a rule or a manual"
                f" category.")
    return " The model returned nothing - check the API log."


def _run_categorize(job_id: str) -> None:
    """Resolve uncategorised rows through the model, then rebuild the ledger.

    Two things have to happen after the categories change, and forgetting
    either leaves the app showing figures that no longer match its own data:
    the rows are written back, and every cached analysis is dropped so the
    next read recomputes.
    """
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        from ..categorize import llm_categorizer
        from ..categorize.llm_categorizer import categorize_with_llm

        db = get_db()
        everything = repo.get_transactions(db)
        pending = [t for t in everything if _awaiting_a_category(t)]
        progress.start(len(pending), "Asking the model")

        before = {t.id: t.category for t in pending}
        from_cache, from_model = categorize_with_llm(pending, db=db)

        changed = [t for t in pending if t.category != before.get(t.id)]
        for txn in changed:
            progress.item(
                (txn.merchant or txn.raw_description or "")[:70],
                "done",
                detail=f"{txn.category} ({txn.category_source.value}, "
                       f"{txn.category_confidence:.0%})",
                key=txn.id)
        # Rows the model could not place still count as work done, or the bar
        # stops short of its own total and looks stuck.
        still_pending = len(pending) - len(changed)
        if still_pending:
            progress.advance(len(pending))

        by_source: dict[str, int] = {}
        for txn in changed:
            key = getattr(txn.category_source, "value", str(txn.category_source))
            by_source[key] = by_source.get(key, 0) + 1

        if changed:
            repo.update_transaction_categories(db, changed)

        # Every figure on every tab was computed before these categories
        # existed. Dropping the cached run is what makes the whole UI agree.
        refreshed = False
        try:
            from ..main import runs
            runs.clear()
            refreshed = True
        except Exception:  # pragma: no cover
            log.warning("could not clear the cached analysis after categorising")

        progress.complete(
            result={"considered": len(pending), "updated": len(changed),
                    # What the categoriser looked at, kept for the API log.
                    "from_cache": from_cache, "from_model": from_model,
                    # What actually changed, which is what a reader is owed.
                    # These two pairs disagree on purpose: a cached merchant
                    # whose stored category IS "uncategorized" is a cache hit
                    # that resolves nothing, so reporting "37 categorised, 4
                    # from the cache and 37 from the model" would be 41 out
                    # of 37. The UI reads the changed_* pair and adds up.
                    "changed_from_cache": by_source.get("merchant_cache", 0),
                    "changed_from_model": by_source.get("llm", 0),
                    "still_uncategorized": still_pending,
                    "model_declined": llm_categorizer.last_run["declined"],
                    "model_unevidenced": llm_categorizer.last_run["unevidenced"],
                    "model_failed_batches": llm_categorizer.last_run["failed_batches"],
                    "dashboard_refreshed": refreshed},
            # Counted from what actually changed, not from what the
            # categoriser reported looking at: a merchant cache that holds
            # "uncategorized" for a key is a hit that changes nothing, and
            # "0 categorised (4 from the merchant cache)" reads as a
            # contradiction rather than as the truth it is.
            message=(
                f"{len(changed)} of {len(pending)} categorised"
                + (f" ({by_source.get('merchant_cache', 0)} from the merchant "
                   f"cache, {by_source.get('llm', 0)} from the model)"
                   if changed else "")
                + f". {still_pending} still unresolved."
                + _model_note(llm_categorizer.last_run, from_model,
                              still_pending)),
        )
    except Exception as exc:
        log.exception("categorisation run failed")
        progress.fail(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Demo mode
# --------------------------------------------------------------------------


class DemoRequest(BaseModel):
    enabled: bool


@router.post("/cards/analyze")
def analyze_cards() -> dict[str, Any]:
    """Re-read every credit card's newest statement for its cycle.

    A card's summary box - limit, amount due, minimum due, statement and due
    dates - is the one part of a statement that resists a deterministic
    reader, because the labels are a header row and the figures sit wherever
    the issuer's layout puts them. `normalize.card_summary` handles it, and
    it runs during parsing; this is the same read on demand, for cards
    already in the ledger whose statements were parsed before it existed.

    One statement per card - the newest - because that is the only one whose
    figures are current. Answers are cached by the text they were read from,
    so pressing this twice costs one round of model calls, not two.
    """
    from pathlib import Path

    from ..ingestion import extractors
    from ..ingestion.passwords import derive_passwords
    from ..normalize.normalizer import normalize

    db = get_db()
    profile = repo.get_profile(db)
    candidates = (derive_passwords(profile)
                  if profile.has_password_material() else [])

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (a.id) a.id, a.institution,"
            "       a.account_number_masked AS masked, f.filepath, f.sender"
            "  FROM accounts a"
            "  JOIN statements s ON s.account_id = a.id"
            "  JOIN source_files f ON f.statement_id = s.id"
            " WHERE a.account_type = 'credit_card'"
            " ORDER BY a.id, s.period_end DESC NULLS LAST").fetchall()

    read: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {
            "account_id": row["id"],
            "card": f"{row['institution']} {row['masked'] or ''}".strip(),
        }
        path = Path(row["filepath"] or "")
        if not path.exists():
            entry["status"] = "the statement file is no longer on disk"
            read.append(entry)
            continue
        try:
            extraction = extractors.extract_pdf(
                path, password_candidates=candidates)
            statement, account = normalize(extraction, path.name,
                                           sender=row["sender"] or "")
        except Exception as exc:
            log.warning("could not analyse %s: %s", path.name, exc)
            entry["status"] = f"{type(exc).__name__}"
            read.append(entry)
            continue
        if getattr(extraction, "needs_password", False):
            entry["status"] = "still locked"
            read.append(entry)
            continue

        # Written straight onto the account: the newest statement IS the
        # current cycle, so there is nothing older to prefer.
        with db.connection() as conn:
            conn.execute(
                "UPDATE accounts SET credit_limit = ?,"
                "  principal_outstanding = ?, balance_as_of = ?"
                " WHERE id = ?",
                (_txt(account.credit_limit),
                 _txt(statement.closing_balance),
                 statement.period_end.isoformat()
                 if statement.period_end else None,
                 row["id"]))
        entry.update({
            "status": "read",
            "credit_limit": _txt(account.credit_limit),
            "amount_due": _txt(statement.closing_balance),
            "cycle_start": (statement.period_start.isoformat()
                            if statement.period_start else None),
            "cycle_end": (statement.period_end.isoformat()
                          if statement.period_end else None),
            "notes": list(statement.parse_warnings)[:2],
        })
        read.append(entry)

    complete = sum(1 for e in read
                   if e.get("credit_limit") and e.get("amount_due"))
    return {
        "cards": read,
        "analysed": len(read),
        "complete": complete,
        "note": (
            f"{complete} of {len(read)} cards now carry both a limit and an "
            f"amount due. A card left blank is one whose statement does not "
            f"state the figure where it could be found - reporting nothing "
            f"is deliberate, because a wrong limit is what would let the "
            f"credit report be matched to the wrong card."),
    }


def _txt(value: Any) -> str | None:
    return None if value is None else str(value)


@router.get("/demo")
def read_demo(user: User = Depends(current_user)) -> dict[str, Any]:
    """Whether this account is looking at its demo workspace, and what is in it."""
    from .. import demo

    db = get_db()
    workspace = demo.workspace_for(db, user.id)
    figures: dict[str, Any] = {}
    if workspace:
        from ..db.engine import tenant_scope

        with tenant_scope(workspace):
            months = repo.covered_months(db)
            figures = {
                "transactions": repo.count_transactions(db),
                "accounts": len(repo.get_accounts(db)),
                "months": len(months),
                "first_month": months[0][0] if months else None,
                "last_month": months[-1][0] if months else None,
            }
    return {
        "enabled": bool(user.demo_mode),
        "prepared": bool(workspace),
        "workspace": figures,
    }


@router.post("/demo")
def set_demo(payload: DemoRequest,
             user: User = Depends(current_user)) -> dict[str, Any]:
    """Point this account at its demo workspace, or back at its own ledger.

    Turning it ON builds the workspace if it does not exist yet, so the first
    demo does not open on an empty dashboard. Turning it OFF leaves the
    workspace and everything in it alone: the next demo starts where the last
    one left off, and rebuilding it is a separate, explicit action.

    Nothing about the real ledger changes either way. The switch decides which
    account's rows the app reads; it does not move, copy or delete a row.
    """
    from .. import demo

    db = get_db()
    if payload.enabled:
        demo.ensure_workspace(db, user.id, user.display_name)

    with db.identity_connection() as conn:
        conn.execute("UPDATE users SET demo_mode = ? WHERE id = ?",
                     (bool(payload.enabled), user.id))
    log.info("demo mode %s for %s", "on" if payload.enabled else "off", user.id)
    return read_demo(user=_reread(db, user.id) or user)


@router.post("/demo/rebuild")
def rebuild_demo(user: User = Depends(current_user)) -> dict[str, Any]:
    """Throw the demo data away and generate it again.

    For a workspace a demo has been walked all over - categories changed, rows
    excluded, a ledger cleared - so the next one starts clean. Only ever
    touches the demo workspace; the real ledger is not reachable from here.
    """
    from .. import demo

    db = get_db()
    workspace = demo.ensure_workspace(db, user.id, user.display_name)
    result = demo.reset(db, workspace)
    return {"status": "rebuilt", **result}


def _reread(db, user_id: str) -> User | None:
    from ..auth import store

    return store.get_user(db, user_id)
