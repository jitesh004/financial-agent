"""Gmail import endpoints, staged and progress-reported.

Three separate stages, each its own job so the UI can show a real bar and a
per-file trace:

    scan     -> list what's in the mailbox (downloads nothing)
    download -> fetch the user's selected files into the persistent cache
    process  -> parse the cached files into the ledger

Keeping them separate is what makes the flow reviewable: nothing is downloaded
until the user has seen the list and chosen, and nothing is parsed until the
files are actually on disk.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..auth import google as google_auth
from ..auth import store as auth_store
from ..config import config
from ..db.database import get_db
from ..db import repository as repo
from ..db.engine import current_tenant
from ..ingestion.gmail_source import (PERIOD_OPTIONS, SCAN_INTENTS,
                                      FoundAlert, FoundAttachment,
                                      GoogleGmailClient, build_query,
                                      download_to_cache, find_alerts, find_statements,
                                      institution_for_sender)
from ..ingestion.passwords import (derive_passwords, password_hint,
                                   profile_can_satisfy, redact_candidate,
                                   resolve_password_status)
from ..jobs import JobProgress, jobs
from ..pipeline import alerts as alerts_pipeline
from ..storage import gmail_cache

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gmail", tags=["gmail"])

ROOT = Path(__file__).resolve().parents[3]


class _DatabaseTokenStore:
    """One user's Gmail grant, held in `google_tokens`.

    Replaces the single `data/gmail_token.json`: with several people signed
    in, one file meant whoever connected last owned everybody's import.
    """

    def __init__(self, user_id: str):
        self.user_id = user_id

    def load(self) -> str | None:
        return auth_store.get_google_token(get_db(), self.user_id)

    def save(self, token_json: str) -> None:
        auth_store.save_google_token(get_db(), self.user_id, token_json)

    def forget(self) -> None:
        auth_store.delete_google_token(get_db(), self.user_id)


def _client() -> GoogleGmailClient | None:
    """A Gmail client for whoever is signed in, or None if nobody is."""
    tenant = current_tenant()
    if not tenant:
        return None
    return GoogleGmailClient(_DatabaseTokenStore(tenant))


def _require_client() -> GoogleGmailClient:
    client = _client()
    if client is None or not client.authorize():
        raise HTTPException(
            400, "Gmail is not connected. Connect it from the import screen.")
    return client


# --------------------------------------------------------------------------
# Status & connect
# --------------------------------------------------------------------------

@router.get("/status")
def status() -> dict[str, Any]:
    cache = gmail_cache()
    cached = len(list(cache.glob("*.pdf"))) if cache.exists() else 0
    profile = repo.get_profile(get_db())
    client = _client()
    return {
        "available": config.google_configured,
        "connected": bool(client and client.is_authorized()),
        "cached_files": cached,
        "profile_ready": profile.has_password_material(),
        # Where to send the browser to grant read access. A URL rather than a
        # POST that "connects": consent happens on Google's own page, and the
        # server cannot open a browser on the user's machine.
        "connect_url": "/api/auth/google/start?purpose=gmail",
        "setup_hint": (
            None if config.google_configured else
            "Gmail import needs the same Google OAuth client the app signs in "
            "with. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        ),
    }


@router.post("/disconnect")
def disconnect() -> dict[str, str]:
    """Forget this user's Gmail grant. Cached files are kept.

    The grant is also revoked at Google, so disconnecting here really does
    end the app's access rather than merely hiding it from this screen.
    """
    tenant = current_tenant()
    db = get_db()
    stored = auth_store.get_google_token(db, tenant)
    if stored:
        try:
            token = json.loads(stored)
            google_auth.revoke(token.get("refresh_token") or token.get("token") or "")
        except (TypeError, ValueError):
            log.warning("stored Gmail token was unreadable; deleting it anyway")
    auth_store.delete_google_token(db, tenant)
    return {"status": "disconnected"}


# --------------------------------------------------------------------------
# Stage 1: scan
# --------------------------------------------------------------------------

@router.get("/ignored")
def get_ignored() -> dict[str, Any]:
    profile = repo.get_profile(get_db())
    return {"excluded_senders": profile.excluded_senders}


@router.put("/ignored")
def set_ignored(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace the list of sender fragments to ignore permanently."""
    db = get_db()
    profile = repo.get_profile(db)
    profile.excluded_senders = [
        str(f).strip() for f in (payload.get("excluded_senders") or []) if str(f).strip()
    ]
    repo.save_profile(db, profile)
    return {"status": "saved", "excluded_senders": profile.excluded_senders}


@router.get("/periods")
def periods() -> list[dict[str, Any]]:
    """Time windows the UI offers for how far back to search."""
    return [{"label": label, "months": months} for label, months in PERIOD_OPTIONS]


@router.get("/intents")
def intents() -> list[dict[str, Any]]:
    """The kinds of document a scan can look for.

    Each is a different reader behind a different search, so this is a real
    choice rather than a filter - the query that finds statements finds no
    alerts, and vice versa.
    """
    return [
        {"key": key, "label": spec["label"], "description": spec["description"],
         "needs_attachment": spec["needs_attachment"],
         "max_months": spec["max_months"]}
        for key, spec in SCAN_INTENTS.items()
    ]


@router.post("/scan")
def scan(
    background: BackgroundTasks,
    max_messages: int = 400,
    months: int | None = None,
    intent: str = "statement",
) -> dict[str, Any]:
    """Start a mailbox scan. Returns a job id to poll. Downloads nothing.

    `months` limits how far back to look; omit it to search everything.
    `intent` chooses what to look for - statements, bureau reports, or the
    transaction alerts that cover the fortnight before a statement is cut.
    """
    _require_client()
    # `intent` accepts a comma-separated list, because looking for statements
    # and credit reports is one errand, not two. Scanned in one job so there is
    # one progress bar and one result to review, rather than a queue the user
    # has to babysit.
    wanted = [i.strip() for i in intent.split(",") if i.strip()]
    unknown = [i for i in wanted if i not in SCAN_INTENTS]
    if unknown:
        raise HTTPException(400, f"Unknown scan intent '{unknown[0]}'.")
    if not wanted:
        raise HTTPException(400, "Nothing was chosen to scan for.")

    request = {"max_messages": max_messages, "months": months,
               "intent": ",".join(wanted)}
    job = jobs.create("scan", phase="Connecting to Gmail", request=request)
    if wanted == ["transactional"]:
        background.add_task(_run_alert_scan, job.id, max_messages, months)
    else:
        background.add_task(_run_multi_scan, job.id, max_messages, months, wanted)
    return {"job_id": job.id}


def _run_multi_scan(job_id: str, max_messages: int, months: int | None,
                    wanted: list[str]) -> None:
    """Scan for several kinds of mail under one job.

    Alerts are always included when asked for, and always last: a statement
    found in the same pass is what decides whether an alert is still needed,
    so reading the statements first means supersession is settled by the time
    the alerts land.
    """
    file_intents = [i for i in wanted if i != "transactional"]
    for index, one in enumerate(file_intents):
        # Each pass appends to the same job's result rather than replacing it.
        _run_scan(job_id, max_messages, months, one,
                  finish=(index == len(file_intents) - 1
                          and "transactional" not in wanted),
                  append=index > 0)
    if "transactional" in wanted:
        _run_alert_scan(job_id, max_messages, months, append=bool(file_intents))


def _staged_accounts(db, already: list | None = None) -> list:
    """Accounts described by staged statements that the LEDGER does not have.

    These are real accounts the user holds - simply not processed yet - and an
    alert matching one of them is matchable whatever the ledger currently
    says.

    Deduplicated against the ledger, and that is the whole difficulty: adding
    a staged copy of a card the ledger already knows gives `match_account` two
    candidates ending 9239 and it refuses the ambiguity, exactly as it should.
    Every alert for a known card was then rejected with "2 accounts end 9239
    and the sender does not say which" - a worse failure than the one this
    function exists to fix.
    """
    from ..db import staging as _staging
    from ..models.schemas import Account

    def key_of(institution: str, mask: str) -> str:
        return f"{(institution or '').strip().lower()}|{(mask or '').strip().lower()}"

    out = []
    seen = {key_of(a.institution, a.account_number_masked)
            for a in (already or [])}
    try:
        entries = _staging.all_entries(db, kinds=("statement",),
                                       with_payload=True)
    except Exception:  # pragma: no cover - a hint is never load-bearing
        log.warning("could not read staged accounts for alert matching")
        return out
    for entry in entries:
        payload = (entry.get("payload") or {}).get("account")
        if not payload:
            continue
        key = key_of(payload.get("institution", ""),
                     payload.get("account_number_masked", ""))
        if key in seen:
            continue
        seen.add(key)
        try:
            account = Account(**payload)
        except Exception:
            continue
        # A synthetic id so `match_account` has something to return; the
        # rebuild resolves the real one when it materialises the statement.
        account.id = f"staged:{key}"
        out.append(account)
    return out


def _run_alert_scan(job_id: str, max_messages: int,
                    months: int | None = None, append: bool = False) -> None:
    """Read alert emails and parse them, without writing anything.

    The review step for alerts. Nothing reaches the ledger here: what comes
    back is a list of unreconciled figures with a decision attached to each,
    so the user can see exactly what would be added - and what would be
    refused, and why - before any of it counts.
    """
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        client = _require_client()
        db = get_db()
        progress.start(max_messages, "Searching your mailbox")

        def on_fetch(done: int, total: int) -> None:
            progress.bump_total(total)
            progress.advance(done, f"Reading emails ({done}/{total})")

        query = build_query(months=months, intent="transactional")
        found = find_alerts(client, query=query, max_messages=max_messages,
                            progress=on_fetch)

        progress.advance(0)
        progress.bump_total(max(1, len(found.alerts)))
        progress.phase("Reading the alerts")

        # Accounts from the LEDGER and from anything staged.
        #
        # An alert names four digits and an issuer and nothing else, so it can
        # only be attached to an account some statement has described. Asking
        # only the ledger made that verdict depend on WHEN you scanned: with
        # the ledger cleared and 604 statements sitting in staging waiting to
        # be processed, every one of 211 alerts was refused for belonging to
        # "no account here" - a true statement about an empty table and a
        # useless one about the user's cards.
        ledger_accounts = repo.get_accounts(db)
        accounts = ledger_accounts + _staged_accounts(db, ledger_accounts)
        existing = repo.get_transactions(db)
        outcome = alerts_pipeline.build_transactions(
            found.alerts, accounts, existing)

        for one in outcome.outcomes:
            progress.item(
                one.subject[:70] or one.sender,
                "done" if one.status == "imported" else "skipped",
                detail=f"{one.status} · {one.reason}",
                key=one.message_id)

        carried = (job.result or {}) if append else {}
        progress.complete(
            result={
                **{k: v for k, v in carried.items()
                   if k in ("attachments", "excluded", "ignored_by_rule",
                            "excluded_senders", "profile_ready")},
                "intent": ("," .join(filter(None, [carried.get("intent"),
                                                   "transactional"]))
                           if append else "transactional"),
                "scanned_messages": found.scanned_messages,
                "months": months,
                "query": query,
                "counts": outcome.counts(),
                "importable": len(outcome.transactions),
                "alerts": [
                    {"message_id": o.message_id, "sender": o.sender,
                     "sender_name": _sender_name(o.sender),
                     "subject": o.subject, "status": o.status,
                     "reason": o.reason, "amount": o.amount,
                     "direction": o.direction, "date_iso": o.txn_date,
                     "account_id": o.account_id, "account": o.account_label,
                     "merchant": o.merchant,
                     "account_suffix": o.account_suffix,
                     "institution": o.institution}
                    for o in outcome.outcomes
                ],
            },
            message=(
                f"{len(outcome.transactions)} importable alert(s) out of "
                f"{len(outcome.outcomes)} read. These are unreconciled: each is "
                f"replaced automatically when its statement arrives."
            ),
        )
        for warning in found.warnings:
            progress.warn(warning)

    except Exception as exc:
        log.exception("gmail alert scan failed")
        progress.fail(f"{type(exc).__name__}: {exc}")


@router.post("/alerts/import")
def import_alerts(background: BackgroundTasks,
                  payload: dict[str, Any]) -> dict[str, Any]:
    """Write the alerts the user chose into the ledger.

    Separate from the scan on purpose, and never automatic: these are figures
    nothing has checked, and they only belong in the ledger because somebody
    looked at the list and decided the recency was worth it.
    """
    selected = payload.get("message_ids") or []
    if not selected:
        raise HTTPException(400, "No alerts selected.")
    scan_job_id = payload.get("scan_job_id")
    if not scan_job_id:
        raise HTTPException(400, "Which scan these came from is not recorded.")

    job = jobs.create("alerts", total=len(selected), phase="Queued",
                      request={"message_ids": selected,
                               "scan_job_id": scan_job_id})
    background.add_task(_run_alert_import, job.id, selected, scan_job_id)
    return {"job_id": job.id, "count": len(selected)}


def _run_alert_import(job_id: str, message_ids: list[str],
                      scan_job_id: str) -> None:
    """Re-read the chosen alerts and persist them.

    The mail is fetched again rather than the scan's parsed output being
    trusted: the scan result travels through the browser, and a figure that
    reached the ledger because a client sent it back is a figure nothing
    verified. Re-reading costs one round trip and keeps Gmail the source.
    """
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        client = _require_client()
        db = get_db()
        wanted = set(message_ids)

        scan = jobs.snapshot(scan_job_id)
        query = (scan or {}).get("result", {}).get("query") or build_query(
            intent="transactional")

        progress.start(len(wanted), "Re-reading the selected alerts")
        found = find_alerts(client, query=query,
                            max_messages=max(len(wanted) * 4, 100))
        chosen = [a for a in found.alerts if a.message_id in wanted]

        accounts = repo.get_accounts(db)
        existing = repo.get_transactions(db)
        outcome = alerts_pipeline.build_transactions(chosen, accounts, existing)

        for one in outcome.outcomes:
            progress.item(
                one.subject[:70] or one.sender,
                "done" if one.status == "imported" else "skipped",
                detail=f"{one.status} · {one.reason}",
                key=one.message_id)

        saved = 0
        if outcome.transactions:
            saved = repo.save_transactions(db, outcome.transactions)

        # Every figure on the dashboard was computed before these rows existed.
        runs_cleared = False
        try:
            from ..main import runs
            runs.clear()
            runs_cleared = True
        except Exception:  # pragma: no cover - only if imported oddly
            log.warning("could not clear the cached dashboard after an import")

        progress.complete(
            result={"imported": saved, "counts": outcome.counts(),
                    "dashboard_refreshed": runs_cleared},
            message=(f"{saved} alert(s) added as unreconciled rows. Each is "
                     f"replaced automatically when its statement arrives."),
        )
    except Exception as exc:
        log.exception("gmail alert import failed")
        progress.fail(f"{type(exc).__name__}: {exc}")


def _run_scan(job_id: str, max_messages: int, months: int | None = None,
              intent: str = "statement", finish: bool = True,
              append: bool = False) -> None:
    """One scan pass.

    `append` keeps what an earlier pass in the same job already found, and
    `finish` says whether this pass is the last - a multi-intent scan reports
    a single result at the end rather than completing the job three times.
    """
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        client = _require_client()
        profile = repo.get_profile(get_db())

        progress.start(max_messages, "Searching your mailbox")

        def on_fetch(done: int, total: int) -> None:
            # Report the real message-fetch progress. Without this the bar sits
            # at zero for the entire scan, which is the slowest stage.
            # Routed through the progress API rather than assigning to the job
            # directly: those writes are what the flusher watches, and a field
            # set behind its back is a tick that never reaches the database.
            progress.bump_total(total)
            progress.advance(done, f"Reading emails ({done}/{total})")

        query = build_query(months=months, intent=intent)
        result = find_statements(client, query=query,
                                 max_messages=max_messages, progress=on_fetch,
                                 intent=intent)

        # Second phase, so the counter restarts against a new denominator
        # instead of continuing from the message count.
        progress.advance(0)
        progress.bump_total(max(1, len(result.attachments)))
        progress.phase("Classifying attachments")

        cache = gmail_cache()

        rows = []
        ignored = 0
        for att in result.attachments:
            # Accounts the user has chosen to ignore (a family member's, a
            # business account) never reach the list at all. Reported as a
            # deliberate choice rather than as a parse failure.
            if profile.is_excluded(att.sender):
                ignored += 1
                continue
            label, explanation = password_hint(att.sender, att.filename)
            is_cached = bool(
                list(cache.glob(att.cache_glob()))
            ) if cache.exists() else False

            rows.append({
                "message_id": att.message_id,
                "attachment_id": att.attachment_id,
                "filename": att.filename,
                "sender": att.sender,
                "sender_name": _sender_name(att.sender),
                "sender_domain": _sender_domain(att.sender),
                # The bank's real name, so the group is findable by the
                # name the user actually thinks in.
                "institution": institution_for_sender(att.sender),
                "subject": att.subject,
                "date": att.date,
                "date_iso": _parse_mail_date(att.date),
                "size": att.size,
                "category": att.category,
                "password_rule": label,
                "password_explanation": explanation,
                "password_ready": profile_can_satisfy(profile, label),
                # Which scan turned this up. Carried all the way into staging
                # so Choose and Parse can show you what each source brought
                # back, before anything has been read and can say what it is.
                "intent": intent,
                "cached": is_cached,
            })
            progress.item(att.filename, "done",
                          detail=f"{att.category} · {label}", cached=is_cached,
                          key=f"{att.message_id}/{att.filename}")

        # Every figure a multi-pass scan reports has to be the TOTAL, not the
        # last pass's. Reporting the last one gave "Found 400 statement PDFs
        # in 2 emails" - the 400 accumulated across passes, the 2 from the
        # bureau pass alone - and named the whole scan "bureau".
        scanned = result.scanned_messages
        seen_intents = [intent]
        excluded_rows = [
            {"sender": e.sender, "sender_name": _sender_name(e.sender),
             "subject": e.subject, "date_iso": _parse_mail_date(e.date),
             "reason": e.reason, "attachment_count": e.attachment_count}
            for e in result.excluded
        ]
        if append:
            carried = job.result or {}
            previous = carried.get("attachments") or []
            seen = {(r.get("message_id"), r.get("filename")) for r in previous}
            rows = previous + [r for r in rows
                               if (r.get("message_id"), r.get("filename")) not in seen]
            ignored += int(carried.get("ignored_by_rule") or 0)
            scanned += int(carried.get("scanned_messages") or 0)
            seen_intents = [i for i in
                            (carried.get("intent") or "").split(",") if i] + [intent]
            excluded_rows = (carried.get("excluded") or []) + excluded_rows

        finisher = progress.complete if finish else progress.checkpoint
        finisher(
            result={
                "attachments": rows,
                "scanned_messages": scanned,
                "profile_ready": profile.has_password_material(),
                "months": months,
                "intent": ",".join(dict.fromkeys(seen_intents)),
                "query": query,
                "ignored_by_rule": ignored,
                "excluded_senders": profile.excluded_senders,
                # Surfaced so exclusions are auditable rather than invisible.
                "excluded": excluded_rows,
            },
            message=(
                f"Found {len(rows)} document(s) in {scanned} emails."
                + (f" {ignored} skipped from ignored accounts." if ignored else "")
            ),
        )
        for w in result.warnings:
            progress.warn(w)

    except Exception as exc:
        log.exception("gmail scan failed")
        progress.fail(f"{type(exc).__name__}: {exc}")


def _parse_mail_date(raw: str) -> str:
    """RFC 2822 mail date -> ISO 8601, for reliable sorting and grouping.

    Parsed server-side rather than in the browser: mail dates carry a variety of
    timezone spellings that JavaScript's Date parser handles inconsistently, and
    a mis-sorted statement list is confusing in a way that is hard to spot.
    """
    from email.utils import parsedate_to_datetime

    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return ""


def _sender_name(sender: str) -> str:
    """'HSBC India <a@b.com>' -> 'HSBC India'; bare address -> the address."""
    if "<" in sender:
        name = sender.split("<")[0].strip().strip('"')
        if name:
            return name
    return sender.strip("<> ")


def _sender_domain(sender: str) -> str:
    import re
    m = re.search(r"@([\w.-]+)", sender or "")
    return m.group(1) if m else ""


# --------------------------------------------------------------------------
# Stage 2: download
# --------------------------------------------------------------------------

@router.post("/download")
def download(background: BackgroundTasks, payload: dict[str, Any]) -> dict[str, Any]:
    """Download the selected attachments into the persistent cache.

    `then_process` continues straight into parsing when the download finishes.
    The chain used to live in the browser - download, await, then post the
    files to /process - which meant closing the tab between the two stages left
    a pile of downloaded files that nothing ever parsed. Server-side, the whole
    pipeline runs to completion whether anyone is watching or not.
    """
    _require_client()
    selected = payload.get("attachments") or []
    if not selected:
        raise HTTPException(400, "No attachments selected.")

    then_process = bool(payload.get("then_process", False))
    use_llm = bool(payload.get("use_llm", False))
    job = jobs.create("download", total=len(selected), phase="Preparing",
                      request={"attachments": selected,
                               "then_process": then_process,
                               "use_llm": use_llm})
    background.add_task(_run_download, job.id, selected, then_process, use_llm)
    return {"job_id": job.id, "count": len(selected)}


def _run_download(job_id: str, selected: list[dict[str, Any]],
                  then_process: bool = False, use_llm: bool = False) -> None:
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        client = _require_client()
        attachments = [
            FoundAttachment(
                message_id=a["message_id"], attachment_id=a["attachment_id"],
                filename=a.get("filename", "statement.pdf"),
                sender=a.get("sender", ""), subject=a.get("subject", ""),
                date=a.get("date", ""), size=a.get("size", 0),
            )
            for a in selected
        ]

        progress.start(len(attachments), "Downloading statements")

        def on_item(i: int, total: int, att: FoundAttachment, cached: bool) -> None:
            progress.item(
                att.filename,
                "done",
                detail="from cache" if cached else f"{att.size / 1024:.0f} KB",
                cached=cached,
                # Message id plus filename, not the filename alone: half a
                # mailbox's statements are called "statement.pdf", and resuming
                # on the name would skip every one after the first.
                key=f"{att.message_id}/{att.filename}",
            )

        saved = download_to_cache(client, attachments, gmail_cache(),
                                  progress=on_item)

        fresh = sum(1 for a in saved if not a.from_cache)
        by_name = {(a.get("message_id"), a.get("filename")): a.get("intent")
                   for a in (selected or []) if isinstance(a, dict)}
        files = [
            {"path": a.saved_path, "filename": a.filename,
             "cached": a.from_cache, "sender": a.sender,
             "intent": by_name.get((a.message_id, a.filename)) or "statement"}
            for a in saved
        ]

        # The follow-on job is registered BEFORE this one is marked complete,
        # so its id is in the result the UI reads. A client that reconnects at
        # any moment can follow the chain from whichever link it lands on,
        # rather than having to guess that a second job exists.
        next_job = None
        if then_process and files:
            # Downloaded files go to STAGING, not to the ledger. The chain is
            # download -> stage -> parse, and it stops there: what a parse
            # produced is looked at on the Review step before any of it counts.
            from ..db import staging as _staging
            from ..ingestion.router import file_hash as _hash
            db = get_db()
            for record in files:
                try:
                    _staging.add(
                        db, _hash(Path(record["path"])),
                        filename=record["filename"], path=record["path"],
                        origin="gmail", kind="statement",
                        scan_intent=record.get("intent") or "statement",
                        sender=record.get("sender", ""))
                except Exception:  # pragma: no cover - one file is not the batch
                    log.exception("could not stage %s", record.get("filename"))
            _staging.apply_supersession(db)

            pending = _staging.unparsed(db)
            if pending:
                next_job = jobs.create(
                    "stage_parse", total=len(pending), phase="Queued",
                    request={"count": len(pending)})

        progress.complete(
            result={
                "files": files,
                "downloaded": fresh,
                "from_cache": len(saved) - fresh,
                "next_job_id": next_job.id if next_job else None,
            },
            message=f"{len(saved)} files ready ({fresh} downloaded, "
                    f"{len(saved) - fresh} already cached).",
        )
    except Exception as exc:
        log.exception("gmail download failed")
        progress.fail(f"{type(exc).__name__}: {exc}")
        return

    # Run inline, on this same worker thread: the download's own background
    # task has not returned yet, so parsing continues without needing another
    # request to start it.
    if next_job is not None:
        from .staging_routes import _run_parse
        _run_parse(next_job.id)


# --------------------------------------------------------------------------
# Stage 3: process
# --------------------------------------------------------------------------

@router.post("/process")
def process(background: BackgroundTasks, payload: dict[str, Any]) -> dict[str, Any]:
    """Parse cached files into the ledger, reporting per-file progress."""
    files = payload.get("files") or []
    if not files:
        raise HTTPException(400, "No files to process.")

    job = jobs.create("process", total=len(files), phase="Preparing",
                      request={"files": files,
                               "use_llm": bool(payload.get("use_llm", False))})
    background.add_task(_run_process, job.id, files,
                        bool(payload.get("use_llm", False)))
    return {"job_id": job.id, "count": len(files)}


def _run_process(job_id: str, files: list[dict[str, Any]], use_llm: bool) -> None:
    """Parse each file individually, then run the shared analysis.

    Files are extracted one at a time here rather than through the graph's
    parallel fan-out, purely so progress can be reported per file. The heavy
    lifting still uses the same extract -> normalize -> reconcile functions, so
    the result is identical to the batch path.
    """
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        from ..graph.nodes import _account_identity, _merge_account_facts
        from ..ingestion import router as ingest_router
        from ..normalize.normalizer import normalize
        from ..normalize.parsers import extract_merchant
        from ..reconcile.balance_check import reconcile
        import uuid as _uuid

        db = get_db()
        profile = repo.get_profile(db)
        candidates = derive_passwords(profile)

        progress.start(len(files), "Parsing statements")

        accounts: dict[str, Any] = {}
        identity_to_id: dict[tuple, str] = {}
        transactions: list[Any] = []
        statement_rows: list[dict[str, Any]] = []
        # The Statement objects themselves, for persistence. Kept separate from
        # statement_rows (which is display-only): transactions carry a
        # statement_id foreign key, so the statements MUST be written or every
        # insert fails with a FOREIGN KEY constraint error.
        parsed_entries: list[dict[str, Any]] = []
        seen_hashes: dict[str, str] = {}

        file_records: list[Any] = []

        for entry in files:
            if progress.cancelled:
                progress.fail("Cancelled by user.")
                return

            path = Path(entry["path"])
            name = entry.get("filename") or path.name
            statement_obj = None  # set once parsing succeeds, read below

            # statement_rows (the older, display-only shape used by the
            # dashboard's per-run summary) has always called a clean success
            # "ok"; the file registry instead uses "parsed", the same word
            # main.py's upload pipeline and the retry endpoint use for the
            # identical outcome. Translated once here so every source of a
            # file record agrees on one vocabulary, rather than the frontend
            # having to know that two pipelines spell the same status two ways.
            _REGISTRY_STATUS = {"ok": "parsed"}

            def record_file(status: str, message: str = "", **extra: Any) -> None:
                """Append to both the display list and the persisted registry.

                One call site per outcome keeps the two in sync - the registry
                is the ONLY record of a failed or locked file, since the
                statements/transactions tables hold successes exclusively.
                """
                # The raw working password goes to the local DB only, never
                # into an API response - the browser's network tab is not
                # somewhere this app's own passwords belong in the clear.
                display = {k: v for k, v in extra.items() if k != "password"}
                if extra.get("password"):
                    display["password_redacted"] = redact_candidate(extra["password"])
                statement_rows.append({"filename": name, "status": status,
                                       "message": message, **display})
                file_records.append(repo.SourceFileRecord(
                    id=str(_uuid.uuid4()), filename=name, filepath=str(path),
                    file_hash=extra.get("file_hash", ""), source="gmail",
                    sender=entry.get("sender", ""),
                    message_id=entry.get("message_id", ""),
                    size_bytes=extra.get("size_bytes"),
                    password=extra.get("password"),
                    password_status=extra.get("password_status", "unknown"),
                    parse_status=_REGISTRY_STATUS.get(status, status),
                    institution_guess=extra.get("institution", ""),
                    account_type_guess=extra.get("account_type", ""),
                    statement_id=None,
                    transaction_count=extra.get("transaction_count", 0),
                    error_message=message,
                ))

            if not path.exists():
                progress.item(name, "failed", "file missing", key=str(path))
                record_file("failed", "file missing")
                continue

            digest = ingest_router.file_hash(path)
            size_bytes = path.stat().st_size
            if digest in seen_hashes:
                progress.item(name, "skipped",
                              f"identical to {seen_hashes[digest]}",
                              key=str(path))
                record_file("duplicate",
                            f"identical in content to {seen_hashes[digest]}",
                            file_hash=digest, size_bytes=size_bytes)
                continue
            seen_hashes[digest] = name

            # A password that opened this exact content before is tried first -
            # it survives a profile edit, and it is the only thing that can open
            # a file whose password was never derivable from the profile at all.
            cached_password = repo.get_cached_password(db, digest)
            file_candidates = (
                [cached_password, *candidates] if cached_password else candidates
            )
            resolved_password, password_status = resolve_password_status(
                path, file_candidates)

            try:
                extraction = ingest_router.extract(
                    path, password_candidates=file_candidates)
            except Exception as exc:
                progress.item(name, "failed", str(exc)[:80], key=str(path))
                record_file("failed", str(exc)[:200], file_hash=digest,
                           size_bytes=size_bytes, password=resolved_password,
                           password_status=password_status)
                continue

            if extraction.needs_password:
                # Name the format this issuer uses, so "locked" is actionable
                # rather than a dead end - the user can either correct their
                # profile or add the exact password under Known passwords.
                label, explanation = password_hint(entry.get("sender", ""), name)
                progress.item(name, "skipped", f"locked · needs {label}",
                              key=str(path))
                record_file(
                    "needs_password",
                    f"Protected. This issuer uses: {explanation}. "
                    f"None of the passwords derived from your profile "
                    f"opened it - add the exact password under "
                    f"Profile > Known passwords.",
                    password_rule=label, file_hash=digest, size_bytes=size_bytes,
                    password_status=password_status)
                continue

            # Check if this is a credit bureau report
            from ..ingestion import bureau
            text = bureau._text_of(extraction)
            if bureau.looks_like_bureau_report(text, name):
                bureau_report = bureau.parse_report(text, name)
                bureau_report.warnings = [*extraction.warnings, *bureau_report.warnings]
                repo.save_bureau_report(db, bureau_report, file_hash=digest, filename=name)
                try:
                    ledger_accounts = repo.get_accounts(db)
                    stored_bureau = repo.get_bureau_accounts(db)
                    from .wealth_routes import _Attr
                    from ..reconcile import bureau_match
                    matches = bureau_match.match_accounts([_Attr(row) for row in stored_bureau], ledger_accounts)
                    repo.apply_bureau_matches(db, matches)
                except Exception as e:
                    log.warning("failed to rematch bureau: %s", e)

                progress.item(
                    name, "done",
                    f"Bureau report · {bureau_report.bureau.upper()} · Score {bureau_report.score or '—'}",
                    key=str(path),
                )
                record_file("ok", f"Credit bureau report: {bureau_report.bureau.upper()} (score: {bureau_report.score or '—'})",
                            transaction_count=0,
                            account=bureau_report.bureau.upper(),
                            file_hash=digest, size_bytes=size_bytes,
                            password=resolved_password, password_status=password_status,
                            institution=bureau_report.bureau.upper(),
                            account_type="credit_report")
                continue

            # A holdings statement is not a ledger either. It has no opening
            # or closing balance and no transactions - it declares what is
            # owned on one date - so the statement pipeline finds no rows in
            # it and records a perfectly good CAS as a parse failure. Routed
            # here instead, where units x NAV is checked against the total the
            # document prints for itself.
            from ..ingestion import portfolio as portfolio_reader
            from ..models.schemas import Account, AccountType
            if portfolio_reader.looks_like_portfolio(text, name):
                holdings_statement = portfolio_reader.parse_statement(
                    text, extraction.tables, name)
                holdings_statement.warnings = [
                    *extraction.warnings, *holdings_statement.warnings]

                # Holdings hang off an account so re-importing the same
                # statement updates the position instead of adding a second
                # copy: the uniqueness key includes account_id, and SQLite
                # treats NULLs as distinct, so a null account would let the
                # portfolio double every time.
                provider = (holdings_statement.provider
                            or institution_for_sender(entry.get("sender", ""))
                            or "Investments")
                holdings_account = Account(
                    institution=provider,
                    account_type=AccountType.INVESTMENT,
                    account_number_masked="",
                )
                holdings_account_id = repo.upsert_account(db, holdings_account)

                repo.save_portfolio_statement(
                    db, holdings_statement, account_id=holdings_account_id,
                    file_hash=digest, filename=name)

                recon_status, _, recon_message = holdings_statement.reconcile()
                count = len(holdings_statement.holdings)
                progress.item(
                    name, "done" if recon_status != "failed" else "failed",
                    f"Holdings · {count} position(s) · {recon_status}",
                    key=str(path),
                )
                record_file(
                    "ok" if recon_status != "failed" else "unreconciled",
                    f"{holdings_statement.layout.upper()} holdings statement: "
                    f"{count} position(s). {recon_message}",
                    transaction_count=0, account=provider,
                    file_hash=digest, size_bytes=size_bytes,
                    password=resolved_password, password_status=password_status,
                    institution=provider, account_type="investment")
                continue

            if not extraction.tables:
                progress.item(name, "failed", "no table found", key=str(path))
                record_file(
                    "failed",
                    "No transaction table could be extracted. "
                    + " ".join(extraction.warnings)[:200],
                    file_hash=digest, size_bytes=size_bytes,
                    password=resolved_password, password_status=password_status)
                continue

            statement, account = normalize(extraction, name, sender=entry.get("sender", ""))
            if not statement.transactions:
                # Same reasoning as graph.nodes.ingest_file: a statement
                # whose own declared opening and closing balance are equal -
                # a dormant account, or one opened partway through the cycle
                # - is a real document correctly read as "nothing happened",
                # not a parser failure. Falls through to the normal success
                # path below when true, so the account still shows up with
                # its (unchanged) balance instead of vanishing silently.
                from ..graph.nodes import _is_genuinely_quiet_period
                if not _is_genuinely_quiet_period(statement):
                    progress.item(name, "failed", "no rows parsed",
                                  key=str(path))
                    record_file(
                        "failed", "Table found but no rows parsed.",
                        file_hash=digest, size_bytes=size_bytes,
                        password=resolved_password, password_status=password_status,
                        institution=account.institution,
                        account_type=account.account_type.value)
                    continue

            statement.file_hash = digest
            recon = reconcile(statement, account.account_type)
            statement.reconciliation = recon

            identity = _account_identity(account)
            account_id = identity_to_id.get(identity)
            if account_id is None:
                account_id = str(_uuid.uuid4())
                account.id = account_id
                identity_to_id[identity] = account_id
                accounts[account_id] = account
            else:
                _merge_account_facts(accounts[account_id], account)

            statement.id = statement.id or str(_uuid.uuid4())
            statement.account_id = account_id
            statement_obj = statement  # so record_file below can link to it
            for txn in statement.transactions:
                txn.id = txn.id or str(_uuid.uuid4())
                txn.account_id = account_id
                txn.statement_id = statement.id
                txn.merchant = extract_merchant(txn.raw_description)
                transactions.append(txn)

            parsed_entries.append({
                "statement": statement,
                "account": account,
                "reconciliation": recon,
            })

            ok = recon.status.value != "failed"
            progress.item(
                name, "done" if ok else "failed",
                f"{len(statement.transactions)} txns · "
                f"{account.account_type.value} · {recon.status.value}",
                key=str(path),
            )
            record_file(
                "ok" if ok else "unreconciled", recon.message[:300],
                transaction_count=len(statement.transactions),
                account=account.display_name(), recon_status=recon.status.value,
                file_hash=digest, size_bytes=size_bytes,
                password=resolved_password, password_status=password_status,
                institution=account.institution,
                account_type=account.account_type.value,
            )

        for record in file_records:
            repo.upsert_source_file(db, record)

        if not transactions:
            progress.complete(
                result={"statements": statement_rows, "transaction_count": 0},
                message=f"{len(statement_rows)} file(s) processed (no bank transactions).")
            return

        # ---- Shared enrichment + analysis ------------------------------
        # One implementation, shared with the upload and file-retry routes.
        # This path used to spell the sequence out itself, which is how it
        # ended up accepting `use_llm` and never reading it, and never
        # consulting the learned merchant cache at all.
        from ..pipeline.enrich import enrich_ledger

        enriched = enrich_ledger(
            db, transactions, accounts,
            use_llm=use_llm,
            # These statements are not in the database yet - they are written
            # after this returns - so their periods have to travel with them
            # or the rows being imported are the only ones attribution cannot
            # see.
            statement_periods={
                e["statement"].id: (e["statement"].period_start,
                                    e["statement"].period_end)
                for e in parsed_entries if e.get("statement")
                and e["statement"].id},
            # The holder's own name turns "UPI/Jitesh Muk/..." from spending
            # into a transfer between the user's own accounts.
            holder_names=[n for n in (profile.full_name,) if n],
            progress=progress.phase,
        )
        transactions = enriched.transactions
        transfer_report = enriched.transfer_report
        recurring = enriched.recurring
        analysis = enriched.analysis
        loan_projections = enriched.loan_projections
        forecast = enriched.forecast
        if enriched.fell_back:
            progress.warn(
                f"{enriched.fell_back} transaction(s) had no matching rule and "
                f"were placed in a default category."
            )

        progress.phase("Saving")
        state = {
            "accounts": accounts, "transactions": transactions,
            "transfer_report": transfer_report, "recurring": recurring,
            "statements": parsed_entries, "analysis": analysis,
            "loan_projections": loan_projections, "forecast": forecast,
            "duplicate_count": enriched.duplicate_count,
        }
        from ..main import _persist, _build_payload, remember_run
        _persist(state)
        for record in file_records:
            repo.upsert_source_file(db, record)
        repo.backfill_source_file_account_ids(db)
        payload = _build_payload(state)
        payload["statements"] = statement_rows
        payload["data_quality"] = {
            "files_processed": len(statement_rows),
            "files_reconciled": sum(1 for s in statement_rows if s["status"] == "ok"),
            "files_unreconciled": sum(1 for s in statement_rows if s["status"] == "unreconciled"),
            "files_failed": sum(1 for s in statement_rows
                                if s["status"] in {"failed", "needs_password"}),
            "duplicates_removed": enriched.duplicate_count,
            "uncategorized_count": analysis.uncategorized_count,
            "notes": list(analysis.notes),
        }

        # Make it the dashboard's current view, same as an upload run.
        run = remember_run(job.id, payload)
        progress.complete(
            result={"statements": statement_rows,
                    "transaction_count": len(transactions),
                    "account_count": len(accounts),
                    "run_id": run},
            message=f"{len(transactions)} transactions across {len(accounts)} accounts.",
        )

    except Exception as exc:
        log.exception("gmail process failed")
        progress.fail(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Job polling
# --------------------------------------------------------------------------

@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    """One job's progress.

    Falls back to the stored copy when the job is not in memory, which is
    every job after a restart and any job pushed out by the eviction cap. This
    endpoint used to answer 404 in both cases - identical, from the caller's
    side, to work that never happened.
    """
    snapshot = jobs.snapshot(job_id)
    if snapshot is None:
        raise HTTPException(404, f"No job {job_id}")
    return snapshot


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, str]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"No job {job_id}")
    job.cancel_requested = True
    return {"status": "cancelling"}


@router.post("/jobs/{job_id}/resume")
def resume_job(background: BackgroundTasks, job_id: str) -> dict[str, Any]:
    """Restart an interrupted job from what it had not yet finished.

    Only the remaining work is re-dispatched: the items the original run
    recorded as done or skipped are filtered out by their stable key. Failed
    items are deliberately retried - whatever went wrong may well have been the
    interruption itself, and re-reading one file is cheaper than explaining why
    it was quietly dropped.

    A scan is the exception. Gmail paging has no checkpoint worth keeping, so a
    resumed scan simply runs again; it downloads nothing, so the cost is time.
    """
    db = get_db()
    stored = repo.get_job(db, job_id)
    if stored is None:
        raise HTTPException(404, f"No job {job_id}")
    if stored["status"] != "interrupted":
        raise HTTPException(
            400, f"That job is {stored['status']}, not interrupted.")

    request = stored["request"]
    if not request:
        raise HTTPException(400, "That job did not record what it was doing.")

    done = repo.completed_job_keys(db, job_id)
    kind = stored["kind"]

    if kind == "scan":
        _require_client()
        job = jobs.create("scan", phase="Connecting to Gmail", request=request)
        if request.get("intent") == "transactional":
            background.add_task(_run_alert_scan, job.id,
                                request.get("max_messages", 400),
                                request.get("months"))
        else:
            background.add_task(_run_scan, job.id,
                                request.get("max_messages", 400),
                                request.get("months"),
                                request.get("intent", "statement"))
        return {"job_id": job.id, "resumed_from": job_id, "remaining": None}

    if kind == "download":
        _require_client()
        remaining = [
            a for a in (request.get("attachments") or [])
            if f"{a.get('message_id')}/{a.get('filename')}" not in done
        ]
        if not remaining:
            raise HTTPException(400, "That job had already finished its work.")
        # Whether the original was going on to parse is part of what it was
        # asked to do, so the resumed run inherits it rather than stopping at
        # a pile of downloaded files.
        then_process = bool(request.get("then_process", False))
        use_llm = bool(request.get("use_llm", False))
        job = jobs.create("download", total=len(remaining), phase="Preparing",
                          request={"attachments": remaining,
                                   "then_process": then_process,
                                   "use_llm": use_llm})
        background.add_task(_run_download, job.id, remaining, then_process, use_llm)
        return {"job_id": job.id, "resumed_from": job_id,
                "remaining": len(remaining), "skipped": len(done)}

    if kind == "process":
        remaining = [f for f in (request.get("files") or [])
                     if str(Path(f["path"])) not in done]
        if not remaining:
            raise HTTPException(400, "That job had already finished its work.")
        use_llm = bool(request.get("use_llm", False))
        job = jobs.create("process", total=len(remaining), phase="Preparing",
                          request={"files": remaining, "use_llm": use_llm})
        background.add_task(_run_process, job.id, remaining, use_llm)
        return {"job_id": job.id, "resumed_from": job_id,
                "remaining": len(remaining), "skipped": len(done)}

    if kind == "alerts":
        _require_client()
        remaining = [m for m in (request.get("message_ids") or []) if m not in done]
        if not remaining:
            raise HTTPException(400, "That job had already finished its work.")
        job = jobs.create("alerts", total=len(remaining), phase="Queued",
                          request={"message_ids": remaining,
                                   "scan_job_id": request.get("scan_job_id")})
        background.add_task(_run_alert_import, job.id, remaining,
                            request.get("scan_job_id"))
        return {"job_id": job.id, "resumed_from": job_id,
                "remaining": len(remaining), "skipped": len(done)}

    raise HTTPException(400, f"A '{kind}' job cannot be resumed.")
