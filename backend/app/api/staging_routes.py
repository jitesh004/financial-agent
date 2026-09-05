"""The staged import: scan, parse, review, process.

Four verbs over one table, in the order the wizard walks them.

  POST /api/staging/scan-results   what a scan found, staged (parses nothing)
  POST /api/staging/parse          read the staged files that have not been read
  GET  /api/staging/review         what is staged, grouped for a decision
  POST /api/staging/select         tick and untick
  POST /api/staging/process        build the ledger from what is ticked

The separation is the feature. Everything before `process` leaves the ledger
exactly as it was, so a scan that turns up a badly-parsed statement changes no
total on any tab until someone has looked at it and said yes.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..db import repository as repo
from ..db import staging
from ..db.database import get_db
from ..jobs import JobProgress, jobs
from ..pipeline import staging_pipeline as pipeline

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/staging", tags=["staging"])


#: Staging's parse statuses in the registry's words. They very nearly agree -
#: `ok` and `empty` are the two that do not. `empty` means read and understood
#: with nothing in it that counts (a contract note, a trades report), which is
#: a success, not a failure: calling it one sends someone hunting a bug that
#: is not there. Same reasoning as main._PARSE_STATUS, whose vocabulary this
#: shares.
_REGISTRY_STATUS = {
    pipeline.STATUS_OK: "parsed",
    pipeline.STATUS_EMPTY: "parsed",
    pipeline.STATUS_FAILED: "failed",
    pipeline.STATUS_LOCKED: "needs_password",
}


KIND_LABELS = {
    "statement": "Statement",
    "alert": "Transaction alert",
    "loan_summary": "Loan summary",
    "bureau": "Credit report",
    "portfolio": "Investments",
    "trades": "Contract notes",
}

KIND_NOTES = {
    "statement": "Reconciled against the balances the issuer printed.",
    "alert": "Read from an alert email. Nothing has checked it against a "
             "statement.",
    "loan_summary": "What the lender itself reported: balance, rate, EMI and "
                    "instalments left. Some loans are collected by standing "
                    "instruction and never produce a statement at all, so "
                    "this email is the only record there is.",
    "bureau": "A credit bureau's own record of your accounts.",
    "portfolio": "Holdings on one date, not a ledger of transactions.",
    "trades": "A record of what was bought and sold. The money reaches your "
              "ledger through the bank statement that funded it, so nothing "
              "here is counted again.",
}


class StagedFile(BaseModel):
    file_hash: str
    filename: str
    path: str | None = None
    origin: str = "gmail"
    kind: str = "statement"
    message_id: str | None = None
    sender: str = ""
    subject: str = ""


class ScanResults(BaseModel):
    files: list[StagedFile] = []
    alerts: list[dict[str, Any]] = []


class Selection(BaseModel):
    ids: list[str] = []
    include: bool = True


class GroupSelection(BaseModel):
    """A whole account+origin group, ticked or unticked in one go."""
    key: str
    include: bool


class SelectionRequest(BaseModel):
    files: list[Selection] = []
    groups: list[GroupSelection] = []


#: Where an entry belongs when it never recorded which scan found it -
#: inferred from what reading it revealed, which is the next best evidence.
_INTENT_FOR_KIND = {
    "alert": "transactional",
    "loan_summary": "loan_summary",
    "bureau": "bureau",
    "portfolio": "investment",
    "trades": "investment",
    "statement": "statement",
}


def _group_key(entry: dict[str, Any]) -> str:
    return f"{entry.get('account_key') or 'unattached'}::{entry.get('kind')}"


# ------------------------------------------------------------------ staging --


@router.post("/scan-results")
def stage_scan_results(payload: ScanResults) -> dict[str, Any]:
    """Record what a scan found. Parses nothing, reads nothing.

    Called with the whole scan result, every time, including files staged on a
    previous run - `staging.add` recognises them by content hash and leaves
    their selection and their parse result alone. That is what makes running
    the wizard again cheap instead of destructive.
    """
    db = get_db()
    before = staging.counts(db)["total"]

    for item in payload.files:
        staging.add(
            db, item.file_hash,
            filename=item.filename, path=item.path, origin=item.origin,
            kind=item.kind, message_id=item.message_id, sender=item.sender,
            subject=item.subject,
        )

    for alert in payload.alerts:
        try:
            pipeline.stage_alert(db, alert)
        except Exception:  # pragma: no cover - one bad alert is not the batch
            log.exception("could not stage an alert")

    staging.apply_supersession(db)
    after = staging.counts(db)
    return {"added": after["total"] - before, **after}


@router.post("/parse")
def start_parse(background: BackgroundTasks,
                intent: str | None = None) -> dict[str, Any]:
    """Parse staged files that have not been read successfully yet.

    `intent` limits the run to one source, so the Parse step can offer a
    section per source rather than one all-or-nothing button.
    """
    db = get_db()
    pending = staging.unparsed(db, scan_intent=intent)
    if not pending:
        return {"job_id": None, "count": 0,
                "message": "Everything staged has already been read."}

    job = jobs.create("stage_parse", total=len(pending), phase="Queued",
                      request={"count": len(pending), "intent": intent})
    background.add_task(_run_parse, job.id, intent)
    return {"job_id": job.id, "count": len(pending)}


def _run_parse(job_id: str, intent: str | None = None) -> None:
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        db = get_db()
        from ..ingestion.passwords import derive_passwords
        candidates = list(derive_passwords(repo.get_profile(db)))

        pending = staging.unparsed(db, scan_intent=intent)
        progress.start(len(pending), "Reading documents")

        counts: dict[str, int] = defaultdict(int)
        for entry in pending:
            # Between documents, not inside one: a half-read PDF is not a
            # useful place to stop, and reading one takes seconds at most.
            # Anything already read stays staged - it is on the Review step
            # waiting, which is exactly where it would have been anyway.
            if progress.cancelled:
                staging.apply_supersession(db)
                progress.cancel(
                    f"Stopped after reading {sum(counts.values())} of "
                    f"{len(pending)}. What was read is on the Review step; "
                    f"the rest is still waiting to be read.")
                return
            status = pipeline.parse_entry(db, entry, candidates)
            counts[status] += 1
            fresh = staging.all_entries(db)
            detail = next((e for e in fresh if e["id"] == entry["id"]), {})
            progress.item(
                entry["filename"][:70],
                "done" if status in ("ok", "empty") else "failed",
                detail=(f"{detail.get('account_label') or '—'} · "
                        f"{detail.get('row_count') or 0} row(s)"
                        if status == "ok" else detail.get("parse_message", status)),
                key=entry["id"])

        staging.apply_supersession(db)
        totals = staging.counts(db)
        progress.complete(
            # Prefixed, because `totals` carries a `parsed` of its own - the
            # whole staging area's - and spreading it last silently replaced
            # this run's count with it.
            result={"read_now": counts.get("ok", 0),
                    "empty_now": counts.get("empty", 0),
                    "failed_now": counts.get("failed", 0),
                    "locked_now": counts.get("needs_password", 0), **totals},
            message=(f"{counts.get('ok', 0)} read, "
                     f"{counts.get('failed', 0) + counts.get('needs_password', 0)}"
                     f" could not be. Nothing has been added to your ledger yet -"
                     f" that happens on the last step."),
        )
    except Exception as exc:
        log.exception("staged parse failed")
        progress.fail(f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------- review --


@router.get("/sections")
def sections() -> dict[str, Any]:
    """What each source has brought in, counted per stage of the wizard.

    One row per scan source plus one for uploads, so Choose and Parse can show
    a section each. Keyed on `scan_intent` - the question a scan asked - not
    on `kind`, which is the answer reading the file gave and does not exist
    until the file has been read.
    """
    db = get_db()
    entries = staging.all_entries(db)

    from ..ingestion.gmail_source import SCAN_INTENTS
    order = [*SCAN_INTENTS.keys(), "upload"]

    out: dict[str, dict[str, Any]] = {}
    for key in order:
        spec = SCAN_INTENTS.get(key, {})
        out[key] = {
            "key": key,
            "label": spec.get("label") or "Files you added",
            "description": spec.get("description")
            or "Statements from this computer - anything Gmail does not carry.",
            "max_months": spec.get("max_months"),
            "staged": 0, "parsed": 0, "pending": 0, "failed": 0,
            "selected": 0, "rows": 0,
        }

    for entry in entries:
        # An entry with no recorded source is placed by WHAT IT IS rather than
        # assumed to be a statement. Assuming hid 113 alerts inside the
        # statements count, where nothing could act on them.
        key = entry.get("scan_intent") or _INTENT_FOR_KIND.get(
            entry.get("kind"), "statement")
        if key not in out:
            # A source that no longer exists in the registry still has files
            # staged against it, and they must stay visible.
            out[key] = {"key": key, "label": key.title(), "description": "",
                        "max_months": None, "staged": 0, "parsed": 0,
                        "pending": 0, "failed": 0, "selected": 0, "rows": 0}
        row = out[key]
        row["staged"] += 1
        status = entry.get("parse_status")
        if status in ("ok", "empty"):
            row["parsed"] += 1
        elif status == "pending":
            row["pending"] += 1
        else:
            row["failed"] += 1
        if entry.get("selected") and not entry.get("superseded_by"):
            row["selected"] += 1
            row["rows"] += entry.get("row_count") or 0

    return {"sections": list(out.values())}


@router.get("/review")
def review() -> dict[str, Any]:
    """What is staged, grouped by account and origin, files nested inside.

    Groups are what you judge; files are what you correct. A group answers
    "should this card's statements count at all", a file answers "all except
    that one".
    """
    db = get_db()
    entries = staging.all_entries(db)

    groups: dict[str, dict[str, Any]] = {}
    for entry in entries:
        key = _group_key(entry)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "key": key,
                "account_label": entry.get("account_label") or "Not yet read",
                "account_type": entry.get("account_type") or "unknown",
                "kind": entry.get("kind"),
                "kind_label": KIND_LABELS.get(entry.get("kind"), entry.get("kind")),
                "kind_note": KIND_NOTES.get(entry.get("kind"), ""),
                "files": [],
            }
        group["files"].append({
            "id": entry["id"],
            "filename": entry["filename"],
            "origin": entry["origin"],
            "selected": entry["selected"],
            "superseded_by": entry["superseded_by"],
            "parse_status": entry["parse_status"],
            "parse_message": entry["parse_message"],
            "period_start": entry["period_start"],
            "period_end": entry["period_end"],
            "row_count": entry["row_count"],
            "debits": entry["debits"],
            "credits": entry["credits"],
            "recon_status": entry["recon_status"],
            "warnings": entry["warnings"],
        })

    superseding: dict[str, str] = {e["id"]: e["filename"] for e in entries}

    out = []
    for group in groups.values():
        files = group["files"]
        live = [f for f in files if not f["superseded_by"]]
        counted = [f for f in live if f["selected"]]
        for f in files:
            if f["superseded_by"]:
                f["superseded_by_name"] = superseding.get(f["superseded_by"], "")
        out.append({
            **group,
            "file_count": len(files),
            "selected_count": len(counted),
            "superseded_count": sum(1 for f in files if f["superseded_by"]),
            "failed_count": sum(1 for f in files
                                if f["parse_status"] not in ("ok", "empty")),
            # Read fine, but the rows do not add up to the balances the issuer
            # printed. A different thing from a file that could not be read,
            # and the more interesting one: it means real transactions are
            # missing from a document that parsed without complaint.
            "unbalanced_count": sum(1 for f in files
                                    if f["recon_status"] == "failed"),
            "row_count": sum(f["row_count"] or 0 for f in counted),
            "debits": str(sum((Decimal(f["debits"] or 0) for f in counted),
                              Decimal("0"))),
            "credits": str(sum((Decimal(f["credits"] or 0) for f in counted),
                               Decimal("0"))),
            "first": min((f["period_start"] for f in counted
                          if f["period_start"]), default=None),
            "last": max((f["period_end"] for f in counted
                         if f["period_end"]), default=None),
            # A group is on unless every live file in it is off, so a group
            # with one file unticked still reads as on - which is what the
            # group checkbox then toggles.
            "included": bool(counted),
            "partial": 0 < len(counted) < len(live),
        })

    out.sort(key=lambda g: (g["account_label"], g["kind_label"]))
    totals = staging.counts(db)
    # Only the kinds that carry transactions count toward "rows". A credit
    # report lists 27 accounts and a portfolio lists 34 holdings; adding those
    # to a transaction count produces a number that is not a count of anything.
    return {"groups": out, **totals,
            "rows": sum(g["row_count"] for g in out
                        if g["kind"] in ("statement", "alert")),
            "items": sum(g["row_count"] for g in out
                         if g["kind"] not in ("statement", "alert")),
            "processed": repo.count_transactions(db)}


@router.post("/select")
def select(request: SelectionRequest) -> dict[str, Any]:
    """Tick or untick files, individually or a whole group at a time."""
    db = get_db()
    entries = staging.all_entries(db)
    by_id = {e["id"]: e for e in entries}

    decisions: dict[str, bool] = {}
    for group in request.groups:
        for entry in entries:
            if _group_key(entry) == group.key:
                decisions[entry["id"]] = group.include
    for item in request.files:
        for entry_id in item.ids:
            if entry_id in by_id:
                decisions[entry_id] = item.include

    if not decisions:
        raise HTTPException(400, "Nothing was selected or deselected.")

    staging.set_selected(db, decisions.items())
    # Unticking a statement can revive the alerts it was superseding.
    staging.apply_supersession(db)
    return {"changed": len(decisions), **staging.counts(db)}


@router.post("/forget")
def forget(intent: str | None = None) -> dict[str, Any]:
    """Drop staged documents - one source's, or all of them.

    Staging is a library, not a queue: it keeps every document it has ever
    read, so narrowing a scan from a year to three months does NOT make the
    older ones go away. That is the right default - they were read correctly
    and re-reading them costs minutes - but it means there has to be a way to
    say "forget those", and this is it.

    The ledger is untouched. What was already processed stays processed until
    the next Process data rebuilds it from whatever is left here.
    """
    db = get_db()
    entries = staging.all_entries(db)
    doomed = [e["id"] for e in entries
              if not intent or (e.get("scan_intent") or "statement") == intent]
    removed = staging.remove(db, doomed)
    staging.apply_supersession(db)
    return {"removed": removed, "scope": intent or "everything",
            **staging.counts(db)}


@router.delete("/files")
def remove_files(request: Selection) -> dict[str, Any]:
    """Drop staged files entirely. The ledger is untouched until Process."""
    db = get_db()
    removed = staging.remove(db, request.ids)
    staging.apply_supersession(db)
    return {"removed": removed, **staging.counts(db)}


# ------------------------------------------------------------------ process --


@router.post("/process")
def start_process(background: BackgroundTasks) -> dict[str, Any]:
    db = get_db()
    selected = staging.all_entries(db, selected_only=True)
    if not selected:
        raise HTTPException(400, "Nothing is selected to process.")

    job = jobs.create("stage_process", total=len(selected), phase="Queued",
                      request={"count": len(selected)})
    background.add_task(_run_process, job.id)
    return {"job_id": job.id, "count": len(selected)}


def _run_process(job_id: str) -> None:
    """Rebuild the ledger from the staged selection, then analyse it."""
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        db = get_db()
        selected = staging.all_entries(db, selected_only=True)
        progress.start(len(selected), "Rebuilding your ledger")

        # The only safe place to stop this one is before it starts. Unlike
        # the download and the read, which are per-document loops, this is a
        # single rebuild of the whole ledger followed by one analysis - and
        # half a rebuilt ledger is a worse outcome than a finished one
        # nobody wanted. So Cancel is honoured here and nowhere below.
        if progress.cancelled:
            progress.cancel("Stopped before anything was rebuilt. Your ledger "
                            "is untouched and the selection is still staged.")
            return

        built = pipeline.materialise(db, progress=progress.phase)
        # Registered here rather than after the analysis: when nothing parses
        # there is no ledger to publish, and those are exactly the runs whose
        # documents someone needs to see on the Files screen.
        try:
            _register_documents(db)
        except Exception:  # pragma: no cover - the registry is not the ledger
            log.exception("could not record the file registry")
        # Settle what each investment account is actually worth, now that
        # every holding is written.
        #
        # A portfolio statement sets its account's balance to the total it
        # declares, one file at a time, and nothing afterwards asked whether
        # two files were describing the same shares. A CDSL consolidated
        # statement reports every demat account the holder has, so an Upstox
        # statement is a subset of it - 20 of this holder's securities appear
        # on both, worth 3.01 lakh, and both totals were being added.
        #
        # `refresh_investment_balances` recomputes each balance from the
        # deduplicated holdings view the Portfolio tab already reads, so the
        # two screens stop disagreeing: assets read 22.97 lakh on the
        # Overview against 19.88 lakh on Portfolio, and the difference was
        # exactly one broker statement counted twice.
        try:
            progress.phase("Valuing your investments")
            repo.refresh_investment_balances(db)
        except Exception:  # pragma: no cover - a valuation is not the ledger
            log.exception("could not refresh investment balances")

        # Link the credit report to the accounts, now that both exist.
        #
        # `materialise` has just written every bureau line and every account
        # in one pass, and until they are matched the app believes it has two
        # of everything. It showed: the Position tab drafted the HDFC home
        # loan twice - once as "HDFC BANK LTD 203-664757833" from the report
        # and once as "HDFC Bank Home Loan (XX4757833)" from the lender's own
        # email - and totalled them, reporting 1.38 crore owed against a real
        # 69 lakh, over "30 cards" the holder does not have.
        #
        # The Gmail import has always done this. This path never did, so every
        # bureau line sat at match_status "unmatched", confidence 0, whatever
        # the ledger plainly showed.
        try:
            from ..reconcile import bureau_match
            from .wealth_routes import _Attr

            stored_bureau = repo.get_bureau_accounts(db)
            if stored_bureau:
                progress.phase("Matching your credit report to your accounts")
                repo.apply_bureau_matches(db, bureau_match.match_accounts(
                    [_Attr(row) for row in stored_bureau],
                    repo.get_accounts(db)))
        except Exception:  # pragma: no cover - a match is not the ledger
            log.exception("could not match the credit report")

        transactions = built.pop("_transactions", [])

        # Read back from the database rather than using the dict materialise
        # hands over. That dict holds only the accounts `resolve_account`
        # built - the ones a statement described - and it is missing exactly
        # the balances that make an asset an asset.
        #
        # Holdings accounts never pass through `resolve_account` at all: a
        # portfolio statement upserts its account directly and the balance is
        # written when the holdings are saved, so demat, NPS, EPF and the
        # broker account existed in the database with 22.9 lakh between them
        # and did not exist in this dict. The analysis computed net worth from
        # the dict, found nothing but credit cards and loans, and published
        # "Assets tracked 0" against "Liabilities 69,15,027" - then a cash
        # runway of zero months, and a projection of the holder going 19 lakh
        # overdrawn, from an opening balance of nothing.
        #
        # One read, and the analysis sees what the import actually stored.
        accounts = {a.id: a for a in repo.get_accounts(db)} \
            or built.pop("_accounts", {})
        built.pop("_accounts", None)
        progress.advance(len(selected))

        report = {}
        if transactions:
            from ..pipeline.enrich import enrich_ledger
            profile = repo.get_profile(db)
            enriched = enrich_ledger(
                db, transactions, accounts,
                # Rules and the learned merchant cache only. The model is a
                # separate, explicit action under Settings, and it stays that
                # way: calling it from here made Process data take eighteen
                # minutes on two thousand rows and spend money without anyone
                # choosing to, in a step whose job is to be predictable.
                use_llm=False,
                holder_names=[n for n in (profile.full_name,) if n],
                statement_periods={
                    e["id"]: (_as_date(e["period_start"]),
                              _as_date(e["period_end"]))
                    for e in selected if e["kind"] == "statement"},
                progress=progress.phase,
            )
            repo.save_transactions(db, enriched.transactions)

            # Transfers and recurring series are pipeline OUTPUT, computed by
            # enrichment and stored alongside the rows. The old import path
            # wrote them; this one did not, so a rebuild produced twenty-three
            # recurring series and then dropped every one - the Recurring tab
            # read an empty table and showed zero.
            try:
                report_obj = enriched.transfer_report
                if report_obj is not None and getattr(report_obj, "pairs", None):
                    repo.save_transfer_pairs(db, report_obj.pairs)
            except Exception:  # pragma: no cover - a pairing is not the ledger
                log.exception("could not store transfer pairs")
            try:
                if enriched.recurring:
                    repo.save_recurring_series(db, enriched.recurring)
            except Exception:  # pragma: no cover
                log.exception("could not store recurring series")

            report = enriched.override_report.as_dict()

            progress.phase("Storing the analysis")
            _publish(db, enriched, job_id,
                     [e for e in selected if e["kind"] == "statement"])

        uncategorized = sum(1 for t in transactions
                            if getattr(t, "category", "") == "uncategorized")
        result = {**built,
                  "uncategorized": uncategorized,
                  "recurring": len(enriched.recurring or []) if transactions else 0,
                  **{f"decisions_{k}": v for k, v in report.items()
                     if k != "notes"}}
        lost = report.get("orphaned", 0)
        progress.complete(
            result=result,
            message=(
                f"{built['transactions']} transaction(s) across "
                f"{built['accounts']} account(s) now count. "
                + (f"{report.get('applied', 0)} of your decisions were put back"
                   + (f"; {lost} could not be matched to a row and are kept "
                      f"for when it returns." if lost else ".")
                   if report else "")
                + (f" {uncategorized} row(s) the rules could not place are "
                   f"waiting for the model under Settings." if uncategorized
                   else "")
                + (f" {built.get('unread')} selected document(s) could not be "
                   f"read and were skipped." if built.get("unread") else "")
                + (f" {built.get('failed')} document(s) could not be rebuilt: "
                   f"{'; '.join(built.get('failures') or [])[:200]}"
                   if built.get("failed") else "")),
        )
    except Exception as exc:
        log.exception("staged process failed")
        progress.fail(f"{type(exc).__name__}: {exc}")


def _register_documents(db) -> None:
    """Record every staged document in the file registry.

    `source_files` is what the Data tab's "Files & passwords" screen reads,
    and it is the ONLY table that remembers a document which failed to parse
    or is still locked - `statements` and `transactions` hold successes
    exclusively.

    The old import path wrote it, in `main._save_file_registry`. The staged
    path - which is now how everything arrives - never did. So on any ledger
    built through the wizard that screen was empty: no files, and therefore no
    password box, because the box is rendered per file row. A fresh database
    (a Docker volume, say) showed nothing there no matter how much was
    imported, while the ledger beside it was fully populated.

    Every staged document is registered, not only the ticked ones: a document
    someone untick-ed because it would not parse is exactly the one they came
    to this screen to fix. Alerts are skipped - they are not files.
    """
    # Superseded rows are left out: one is a worse copy of a statement that
    # arrived twice, and the pipeline already ignores it. Listing both would
    # put the same month on screen twice with no way to tell which counted.
    entries = [e for e in staging.all_entries(db)
               if e["kind"] != "alert" and not e["superseded_by"]]
    if not entries:
        return

    # A staged entry's id becomes its statement's id (see
    # staging_pipeline.materialise), so the drill-down needs no join. Guard it
    # the way _save_file_registry does: an entry can carry an id without a
    # statement row ever having been written for it, and pointing the registry
    # at one of those is a dangling foreign key.
    with db.connection() as conn:
        written = {r["id"] for r in
                   conn.execute("SELECT id FROM statements").fetchall()}

    for entry in entries:
        status = _REGISTRY_STATUS.get(entry["parse_status"] or "", "failed")
        if status == "parsed" and entry["recon_status"] == "unreconciled":
            status = "unreconciled"
        period = (entry["period_start"] or "")[:7] or None
        # staged_files carries no size, but the file itself is still on disk
        # for anything downloaded this run. Without this the whole Size column
        # reads as a dash. A cleared cache just leaves it that way again.
        size = None
        if entry["path"]:
            try:
                size = Path(entry["path"]).stat().st_size
            except OSError:
                pass
        repo.upsert_source_file(db, repo.SourceFileRecord(
            id=str(uuid.uuid4()),   # ignored when the hash already resolves one
            filename=entry["filename"] or "",
            filepath=entry["path"] or "",
            file_hash=entry["file_hash"] or "",
            source=entry["origin"] or "gmail",
            size_bytes=size,
            sender=entry["sender"] or "",
            message_id=entry["message_id"] or "",
            # Staging derives a password from the profile at read time and
            # keeps no copy, so there is none to report here. "locked" and
            # "unknown" are both honest; a made-up "open" would not be.
            password_status=("locked" if status == "needs_password"
                             else "unknown"),
            parse_status=status,
            institution_guess=entry["account_label"] or "",
            account_type_guess=entry["account_type"] or "",
            statement_id=(entry["id"] if entry["id"] in written else None),
            transaction_count=entry["row_count"] or 0,
            error_message=entry["parse_message"] or "",
            period_hint=period,
        ))
    repo.backfill_source_file_account_ids(db)


def _as_date(value: Any):
    from datetime import date
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _publish(db, enriched, job_id: str, statement_entries: list) -> dict:
    """Hand the finished analysis to the dashboard, the way an import does.

    Reuses the import path's own payload builder rather than assembling a
    second one: two builders is how the Files tab and the coverage grid came
    to disagree about which statements existed.
    """
    from ..main import _build_payload, remember_run

    # Accounts come from the DATABASE, not from `enriched` - and that is the
    # difference between what the app knows and what it shows.
    #
    # `enriched.accounts` is the dict the parsers built as they went. By the
    # time the run ends the database holds something better: accounts merged
    # with ones already known, corrected by later files, and carrying the
    # balances written after parsing. This holder's savings account was
    # created from an Upstox client-master report, which names the bank it is
    # held at; a later ICICI statement corrected the institution, and the
    # balances were filled in afterwards. The database had "ICICI Bank
    # Savings, 49,977.90". The frozen dict had "Upstox Saving Bank Savings",
    # no balance - and the frozen dict is what the dashboard served, which is
    # why the Position read "Assets tracked 0" and the forecast gave a cash
    # runway of zero months against a funded account.
    from ..db import repository as _repo
    live_accounts = {a.id: a for a in _repo.get_accounts(db)}

    state = {
        "accounts": live_accounts or enriched.accounts,
        "transactions": enriched.transactions,
        "transfer_report": enriched.transfer_report,
        "recurring": enriched.recurring,
        "statements": [],
        "analysis": enriched.analysis,
        "loan_projections": enriched.loan_projections,
        "forecast": getattr(enriched, "forecast", None),
        "duplicate_count": enriched.duplicate_count,
    }
    payload = _build_payload(state)
    from ..models.schemas import file_state

    states = [file_state(e["recon_status"], e.get("kind") or "")
              for e in statement_entries]
    payload["statements"] = [{
        "filename": e["filename"],
        "status": state,
        "account": e["account_label"],
        "rows": e["row_count"],
        "detail": e["parse_message"],
    } for e, state in zip(statement_entries, states)]
    analysis = enriched.analysis
    payload["data_quality"] = {
        "files_processed": len(statement_entries),
        "files_reconciled": sum(1 for st in states if st == "ok"),
        "files_unreconciled": sum(1 for st in states if st == "unreconciled"),
        "files_failed": sum(1 for st in states if st == "failed"),
        "files_locked": sum(1 for st in states if st == "needs_password"),
        "duplicates_removed": enriched.duplicate_count,
        "uncategorized_count": getattr(analysis, "uncategorized_count", 0),
        # The name the Overview's tile actually reads. Omitting it did not
        # leave the tile blank - it left it reading a confident zero over
        # however many rows a rule had in fact settled.
        "rules_settled": getattr(enriched, "rules_settled", 0),
        "llm_settled": getattr(enriched, "llm_settled", 0),
        "notes": list(getattr(analysis, "notes", [])),
    }
    remember_run(job_id, payload)
    return payload
