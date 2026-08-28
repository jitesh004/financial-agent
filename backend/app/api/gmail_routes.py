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

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..db.database import get_db
from ..db import repository as repo
from ..ingestion.gmail_source import (PERIOD_OPTIONS, FoundAttachment,
                                      GoogleGmailClient, build_query,
                                      classify_sender, download_to_cache,
                                      find_statements, institution_for_sender)
from ..ingestion.passwords import (derive_passwords, password_hint,
                                   profile_can_satisfy, redact_candidate,
                                   resolve_password_status)
from ..jobs import JobProgress, jobs

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gmail", tags=["gmail"])

ROOT = Path(__file__).resolve().parents[3]
CREDENTIALS = ROOT / "credentials.json"
TOKEN = ROOT / "data" / "gmail_token.json"
CACHE = ROOT / "data" / "gmail_cache"


def _client() -> GoogleGmailClient | None:
    if not CREDENTIALS.exists() and not TOKEN.exists():
        return None
    return GoogleGmailClient(CREDENTIALS, TOKEN)


def _require_client() -> GoogleGmailClient:
    client = _client()
    if client is None or not client.is_authorized():
        raise HTTPException(400, "Gmail is not connected.")
    client.authorize(interactive=False)
    return client


# --------------------------------------------------------------------------
# Status & connect
# --------------------------------------------------------------------------

@router.get("/status")
def status() -> dict[str, Any]:
    client = _client()
    cached = len(list(CACHE.glob("*.pdf"))) if CACHE.exists() else 0
    profile = repo.get_profile(get_db())
    return {
        "available": CREDENTIALS.exists(),
        "connected": bool(client and client.is_authorized()),
        "cached_files": cached,
        "profile_ready": profile.has_password_material(),
        "setup_hint": (
            None if CREDENTIALS.exists() else
            "Gmail import needs a Google Cloud OAuth client saved as "
            "credentials.json in the project root."
        ),
    }


@router.post("/connect")
def connect() -> dict[str, Any]:
    client = _client()
    if client is None:
        raise HTTPException(400, "No credentials.json found.")
    try:
        client.authorize(interactive=True)
    except Exception as exc:
        raise HTTPException(400, f"Authorization failed: {exc}")
    return {"status": "connected"}


@router.post("/disconnect")
def disconnect() -> dict[str, str]:
    """Remove the stored token. Cached files are kept."""
    TOKEN.unlink(missing_ok=True)
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


@router.post("/scan")
def scan(
    background: BackgroundTasks,
    max_messages: int = 400,
    months: int | None = None,
) -> dict[str, Any]:
    """Start a mailbox scan. Returns a job id to poll. Downloads nothing.

    `months` limits how far back to look; omit it to search everything.
    """
    _require_client()
    job = jobs.create("scan", phase="Connecting to Gmail")
    background.add_task(_run_scan, job.id, max_messages, months)
    return {"job_id": job.id}


def _run_scan(job_id: str, max_messages: int, months: int | None = None) -> None:
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        client = _require_client()
        profile = repo.get_profile(get_db())

        progress.start(max_messages, "Searching your mailbox")

        def on_fetch(done: int, total: int) -> None:
            # Report the real message-fetch progress. Without this the bar sits
            # at zero for the entire scan, which is the slowest stage.
            progress.bump_total(total)
            progress.job.current = done
            progress.job.phase = f"Reading emails ({done}/{total})"

        query = build_query(months=months)
        result = find_statements(client, query=query,
                                 max_messages=max_messages, progress=on_fetch)

        # Second phase, so the counter restarts against a new denominator
        # instead of continuing from the message count.
        progress.job.current = 0
        progress.bump_total(max(1, len(result.attachments)))
        progress.phase("Classifying attachments")

        cached_names = {p.name for p in CACHE.glob("*.pdf")} if CACHE.exists() else set()

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
                list(CACHE.glob(att.cache_glob()))
            ) if CACHE.exists() else False

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
                "cached": is_cached,
            })
            progress.item(att.filename, "done",
                          detail=f"{att.category} · {label}", cached=is_cached)

        progress.complete(
            result={
                "attachments": rows,
                "scanned_messages": result.scanned_messages,
                "profile_ready": profile.has_password_material(),
                "months": months,
                "query": query,
                "ignored_by_rule": ignored,
                "excluded_senders": profile.excluded_senders,
                # Surfaced so exclusions are auditable rather than invisible.
                "excluded": [
                    {"sender": e.sender, "sender_name": _sender_name(e.sender),
                     "subject": e.subject, "date_iso": _parse_mail_date(e.date),
                     "reason": e.reason, "attachment_count": e.attachment_count}
                    for e in result.excluded
                ],
            },
            message=(
                f"Found {len(rows)} statement PDFs in "
                f"{result.scanned_messages} emails."
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
    """Download the selected attachments into the persistent cache."""
    _require_client()
    selected = payload.get("attachments") or []
    if not selected:
        raise HTTPException(400, "No attachments selected.")

    job = jobs.create("download", total=len(selected), phase="Preparing")
    background.add_task(_run_download, job.id, selected)
    return {"job_id": job.id, "count": len(selected)}


def _run_download(job_id: str, selected: list[dict[str, Any]]) -> None:
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
            )

        saved = download_to_cache(client, attachments, CACHE, progress=on_item)

        fresh = sum(1 for a in saved if not a.from_cache)
        progress.complete(
            result={
                "files": [
                    {"path": a.saved_path, "filename": a.filename,
                     "cached": a.from_cache, "sender": a.sender}
                    for a in saved
                ],
                "downloaded": fresh,
                "from_cache": len(saved) - fresh,
            },
            message=f"{len(saved)} files ready ({fresh} downloaded, "
                    f"{len(saved) - fresh} already cached).",
        )
    except Exception as exc:
        log.exception("gmail download failed")
        progress.fail(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Stage 3: process
# --------------------------------------------------------------------------

@router.post("/process")
def process(background: BackgroundTasks, payload: dict[str, Any]) -> dict[str, Any]:
    """Parse cached files into the ledger, reporting per-file progress."""
    files = payload.get("files") or []
    if not files:
        raise HTTPException(400, "No files to process.")

    job = jobs.create("process", total=len(files), phase="Preparing")
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
        from ..analytics.engine import analyze
        from ..analytics.recurring import detect_recurring
        from ..analytics import forecast as forecast_mod
        from ..analytics import loans as loans_mod
        from ..categorize.rules import categorize_by_rules
        from ..graph.nodes import _account_identity, _merge_account_facts
        from ..ingestion import router as ingest_router
        from ..models.schemas import AccountType, LIABILITY_TYPES
        from ..normalize.normalizer import normalize
        from ..normalize.parsers import extract_merchant
        from ..reconcile.balance_check import reconcile
        from ..reconcile.transfers import (detect_transfers,
                                           find_duplicate_transactions)
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
                    statement_id=statement_obj.id if statement_obj else None,
                    transaction_count=extra.get("transaction_count", 0),
                    error_message=message,
                ))

            if not path.exists():
                progress.item(name, "failed", "file missing")
                record_file("failed", "file missing")
                continue

            digest = ingest_router.file_hash(path)
            size_bytes = path.stat().st_size
            if digest in seen_hashes:
                progress.item(name, "skipped",
                              f"identical to {seen_hashes[digest]}")
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
                progress.item(name, "failed", str(exc)[:80])
                record_file("failed", str(exc)[:200], file_hash=digest,
                           size_bytes=size_bytes, password=resolved_password,
                           password_status=password_status)
                continue

            if extraction.needs_password:
                # Name the format this issuer uses, so "locked" is actionable
                # rather than a dead end - the user can either correct their
                # profile or add the exact password under Known passwords.
                label, explanation = password_hint(entry.get("sender", ""), name)
                progress.item(name, "skipped", f"locked · needs {label}")
                record_file(
                    "needs_password",
                    f"Protected. This issuer uses: {explanation}. "
                    f"None of the passwords derived from your profile "
                    f"opened it - add the exact password under "
                    f"Profile > Known passwords.",
                    password_rule=label, file_hash=digest, size_bytes=size_bytes,
                    password_status=password_status)
                continue

            if not extraction.tables:
                progress.item(name, "failed", "no table found")
                record_file(
                    "failed",
                    "No transaction table could be extracted. "
                    + " ".join(extraction.warnings)[:200],
                    file_hash=digest, size_bytes=size_bytes,
                    password=resolved_password, password_status=password_status)
                continue

            statement, account = normalize(extraction, name, sender=entry.get("sender", ""))
            if not statement.transactions:
                progress.item(name, "failed", "no rows parsed")
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

        if not transactions:
            progress.complete(
                result={"statements": statement_rows, "transaction_count": 0},
                message="No transactions could be parsed from these files.")
            return

        # ---- Shared enrichment + analysis ------------------------------
        progress.phase("Removing duplicates")
        duplicates = find_duplicate_transactions(transactions)
        dupe_ids = {id(d) for d in duplicates}
        if duplicates:
            transactions = [t for t in transactions if id(t) not in dupe_ids]
        transactions.sort(key=lambda t: (t.txn_date, t.account_id or ""))

        progress.phase("Matching transfers between accounts")
        transfer_report = detect_transfers(transactions, accounts)

        progress.phase("Categorizing")
        categorize_by_rules(transactions)

        # Anything the rules could not place still needs a bucket. Without this
        # an unrecognised CREDIT stays "uncategorized", and since only income
        # categories count towards money-in, a whole salary history silently
        # vanishes from the dashboard: 37 lakh of credits sat uncategorised
        # while money-in read 56,000.
        from ..categorize.rules import fallback_category
        from ..models.schemas import Category, ConfidenceSource
        # The holder's own name turns "UPI/Jitesh Muk/..." from spending into a
        # transfer between the user's own accounts.
        holder_names = [n for n in (profile.full_name,) if n]
        fell_back = 0
        for txn in transactions:
            if txn.category != Category.UNCATEGORIZED:
                continue
            guess = fallback_category(txn, holder_names)
            if guess != Category.UNCATEGORIZED:
                txn.category = guess
                txn.category_source = ConfidenceSource.DEFAULT
                txn.category_confidence = 0.3
                fell_back += 1
        if fell_back:
            progress.warn(
                f"{fell_back} transaction(s) had no matching rule and were "
                f"placed in a default category."
            )

        progress.phase("Detecting recurring commitments")
        recurring = detect_recurring(transactions)

        progress.phase("Computing analysis")
        analysis = analyze(transactions, accounts)

        loan_projections = []
        for account_id, account in accounts.items():
            account_txns = [t for t in transactions if t.account_id == account_id]
            projection = loans_mod.project_loan(account, account_txns)
            if projection:
                loan_projections.append(projection)

        opening = sum(
            (a.current_balance or 0) for a in accounts.values()
            if a.account_type in {AccountType.SAVINGS, AccountType.CURRENT,
                                  AccountType.WALLET}
        )
        forecast = forecast_mod.forecast(
            monthly=analysis.monthly, series=recurring,
            opening_balance=opening, horizon_months=6,
            as_of=analysis.period_end,
        )

        progress.phase("Saving")
        state = {
            "accounts": accounts, "transactions": transactions,
            "transfer_report": transfer_report, "recurring": recurring,
            "statements": parsed_entries, "analysis": analysis,
            "loan_projections": loan_projections, "forecast": forecast,
            "duplicate_count": len(duplicates),
        }
        from ..main import _persist, _build_payload, runs
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
            "duplicates_removed": len(duplicates),
            "uncategorized_count": analysis.uncategorized_count,
            "notes": list(analysis.notes),
        }

        # Make it the dashboard's current view, same as an upload run.
        run = runs.create_from_payload(job.id, payload)
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
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"No job {job_id}")
    return job.to_dict()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, str]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"No job {job_id}")
    job.cancel_requested = True
    return {"status": "cancelling"}
