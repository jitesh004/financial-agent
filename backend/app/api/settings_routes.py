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

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..db.database import get_db
from ..db import repository as repo
from ..jobs import JobProgress, jobs

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


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
                  if t.category == "uncategorized")
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
               if t.category == "uncategorized"]
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
        pending = [t for t in everything if t.category == "uncategorized"]
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
