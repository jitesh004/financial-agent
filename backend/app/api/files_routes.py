"""The file & password registry: every file ever attempted, whatever happened.

The statements/transactions tables hold successes exclusively, so a failed or
locked file has nowhere else to live. This router is the read side of that
registry (list, drill into one file's transactions) and the one write action a
user can take on it directly: retry a file after fixing whatever blocked it,
without re-parsing everything else that already succeeded.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..analytics.coverage import build_coverage, guess_period_hint
from ..db.database import get_db
from ..db import repository as repo
from ..ingestion import router as ingest_router
from ..ingestion.passwords import derive_passwords, resolve_password_status
from ..jobs import JobProgress, jobs
from . import serializers as ser

router = APIRouter(prefix="/api/files", tags=["files"])
coverage_router = APIRouter(prefix="/api/coverage", tags=["coverage"])

#: source_files.parse_status -> the dashboard's older statement-status
#: vocabulary, so the existing "Files & quality" tab keeps working unchanged.
_DASHBOARD_STATUS = {
    "parsed": "ok", "unreconciled": "unreconciled",
    "failed": "failed", "needs_password": "needs_password",
    "duplicate": "duplicate",
}


def all_statement_rows(db) -> list[dict[str, Any]]:
    """The dashboard's per-file table, rebuilt from the persistent registry.

    The alternative - keeping only the current run's in-memory list - goes
    stale the moment a SINGLE file is retried (only that one file's row would
    be known) or the server restarts (the list is empty until the next full
    re-scan). Reading the registry instead makes this table always reflect
    every file ever attempted, not just the most recent run.
    """
    accounts = {a.id: a for a in repo.get_accounts(db)}
    rows = []
    for record in repo.list_source_files(db):
        account = accounts.get(record.account_id) if record.account_id else None
        rows.append({
            "filename": record.filename,
            "status": _DASHBOARD_STATUS.get(record.parse_status, record.parse_status),
            "transaction_count": record.transaction_count,
            "account": account.display_name() if account else record.institution_guess,
            "message": record.error_message,
        })
    return rows


@router.get("")
def list_files() -> list[dict[str, Any]]:
    """Every file the app has ever attempted, most recent first."""
    return [ser.source_file_json(r) for r in repo.list_source_files(get_db())]


@router.get("/{file_id}/transactions")
def file_transactions(file_id: str) -> dict[str, Any]:
    """The parsed rows from one specific file - the "click a PDF, see its
    transactions" drill-down."""
    db = get_db()
    record = repo.get_source_file(db, file_id)
    if record is None:
        raise HTTPException(404, f"No file with id {file_id}")
    if not record.statement_id:
        return {"file": ser.source_file_json(record), "transactions": []}
    txns = repo.get_transactions(db, statement_id=record.statement_id)
    return {
        "file": ser.source_file_json(record),
        "transactions": [ser.transaction_json(t) for t in txns],
    }


def merge_extracted_file_into_ledger(
    db, record, extraction, resolved_password: str | None, password_status: str,
    digest: str, target_month: str | None = None,
) -> dict[str, Any]:
    """Given one already-extracted file, merge it into the persisted ledger.

    Shared by the retry endpoint and the "fetch this month from Gmail" flow -
    both end at the same place (one new statement to fold into everything
    already in the database), they just differ in how the file arrived.
    Transfer-matching and duplicate detection are re-run over the WHOLE
    ledger, because the new file's rows can legitimately pair with a
    transaction that was already there (a bill payment on an existing card,
    say) - but that is pure in-memory work over already-parsed rows, not PDF
    extraction, so it stays fast regardless of how many files came before it.

    `target_month` (a "YYYY-MM" string), if given, additionally checks that
    the parsed statement actually covers the month it was fetched FOR - a
    Gmail search can turn up a plausible-looking but wrong-month attachment,
    and merging that silently would be worse than reporting "not found".
    """
    from ..analytics.coverage import statement_months
    from ..graph.nodes import _account_identity, _merge_account_facts
    from ..models.schemas import ReconciliationStatus
    from ..normalize.normalizer import normalize
    from ..normalize.parsers import extract_merchant
    from ..reconcile.balance_check import reconcile

    def _fail(status: str, message: str) -> dict[str, Any]:
        # upsert_source_file resolves identity by content hash FIRST - if this
        # exact file was already attempted (a retried fetch, a second search
        # candidate that turns out to be the same PDF), it returns THAT row's
        # id, not `record.id`. Using `record.id` regardless crashed here: the
        # freshly-minted id from this attempt matched no row at all once the
        # write actually landed on the earlier one.
        resolved_id = repo.upsert_source_file(db, repo.SourceFileRecord(
            id=record.id, filename=record.filename, filepath=record.filepath,
            file_hash=digest, source=record.source, sender=record.sender,
            message_id=record.message_id, size_bytes=record.size_bytes,
            password=resolved_password, password_status=password_status,
            parse_status=status, error_message=message, period_hint=target_month,
        ))
        return {"status": status, "message": message,
                "file": ser.source_file_json(repo.get_source_file(db, resolved_id))}

    if extraction.needs_password:
        return _fail("needs_password",
                     "Still locked - none of the tried passwords opened it.")
    if not extraction.tables:
        return _fail("failed", "No transaction table could be extracted. "
                                + " ".join(extraction.warnings)[:200])

    statement, account = normalize(extraction, record.filename, sender=record.sender)
    if not statement.transactions:
        return _fail("failed", "Table found but no rows parsed.")

    if target_month and target_month not in statement_months(
            statement.period_start, statement.period_end):
        return _fail(
            "failed",
            f"Found a statement, but it covers "
            f"{statement.period_start}-{statement.period_end}, not {target_month}.")

    statement.file_hash = digest
    recon = reconcile(statement, account.account_type)
    statement.reconciliation = recon

    # Merge against every account already in the database - not just the
    # graph-local identity of this one file - so a card that already has
    # eleven months of statements gets a twelfth, not a duplicate account.
    existing_accounts = {a.id: a for a in repo.get_accounts(db)}
    identity_to_id = {_account_identity(a): a.id for a in existing_accounts.values()}
    identity = _account_identity(account)
    account_id = identity_to_id.get(identity)
    if account_id is None:
        account_id = str(uuid.uuid4())
        account.id = account_id
        existing_accounts[account_id] = account
    else:
        _merge_account_facts(existing_accounts[account_id], account)

    statement.id = statement.id or str(uuid.uuid4())
    statement.account_id = account_id
    new_txns = []
    for txn in statement.transactions:
        txn.id = str(uuid.uuid4())
        txn.account_id = account_id
        txn.statement_id = statement.id
        txn.merchant = extract_merchant(txn.raw_description)
        new_txns.append(txn)

    profile = repo.get_profile(db)
    existing_txns = repo.get_transactions(db)

    from ..pipeline.enrich import enrich_ledger

    enriched = enrich_ledger(
        db,
        existing_txns + new_txns,
        existing_accounts,
        holder_names=[profile.full_name] if profile.full_name else [],
    )
    combined = enriched.transactions

    state = {
        "accounts": existing_accounts, "transactions": combined,
        "transfer_report": enriched.transfer_report, "recurring": enriched.recurring,
        "statements": [{"statement": statement, "account": account, "reconciliation": recon}],
        "analysis": enriched.analysis, "loan_projections": enriched.loan_projections,
        "forecast": enriched.forecast,
    }

    from ..main import _build_payload, _persist, runs
    _persist(state)

    ok = recon.status != ReconciliationStatus.FAILED
    # Same id-resolution subtlety as _fail above: upsert_source_file may
    # write to an existing row (matched by content hash) rather than the
    # fresh id this record started with.
    resolved_id = repo.upsert_source_file(db, repo.SourceFileRecord(
        id=record.id, filename=record.filename, filepath=record.filepath,
        file_hash=digest, source=record.source, sender=record.sender,
        message_id=record.message_id, size_bytes=record.size_bytes,
        password=resolved_password, password_status=password_status,
        parse_status="parsed" if ok else "unreconciled",
        institution_guess=account.institution,
        account_type_guess=account.account_type.value,
        statement_id=statement.id, transaction_count=len(new_txns),
        error_message="" if ok else recon.message,
        period_hint=(statement.period_start.strftime("%Y-%m")
                    if statement.period_start else target_month),
    ))
    repo.backfill_source_file_account_ids(db)

    # Refresh the in-memory dashboard, same as a full Gmail process run, so
    # the change shows up without requiring a separate re-scan. The per-file
    # table is rebuilt from the registry (not just this one file), so every
    # previously-processed file's row survives the refresh too.
    payload = _build_payload(state)
    payload["statements"] = all_statement_rows(db)
    run_id = runs.create_from_payload(str(uuid.uuid4()), payload)

    return {
        "status": "ok" if ok else "unreconciled",
        "message": recon.message,
        "transaction_count": len(new_txns),
        "account": account.display_name(),
        "run_id": run_id,
        "file": ser.source_file_json(repo.get_source_file(db, resolved_id)),
    }


@router.post("/{file_id}/retry")
def retry_file(file_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-attempt one file that previously failed or needed a password.

    Everything already in the ledger stays exactly as it is - this parses ONE
    file (never re-parses the rest) and merges it into what's already there.
    """
    db = get_db()
    record = repo.get_source_file(db, file_id)
    if record is None:
        raise HTTPException(404, f"No file with id {file_id}")

    path = Path(record.filepath) if record.filepath else None
    if not path or not path.exists():
        raise HTTPException(
            409, "The original file is no longer on disk, so it cannot be "
                 "retried. Re-download or re-upload it instead.")

    profile = repo.get_profile(db)
    derived = derive_passwords(profile) if profile.has_password_material() else []
    explicit = ((payload or {}).get("password") or "").strip() or None
    # Order of preference: a password the user just typed for this retry, then
    # one already known to work, then everything the profile can derive.
    ordered = [p for p in (explicit, record.password) if p] + derived

    extraction = ingest_router.extract(path, password_candidates=ordered)
    resolved_password, password_status = resolve_password_status(path, ordered)
    digest = record.file_hash or ingest_router.file_hash(path)

    return merge_extracted_file_into_ledger(
        db, record, extraction, resolved_password, password_status, digest)


# --------------------------------------------------------------------------
# Coverage grid: which months have a statement, per account
# --------------------------------------------------------------------------

def _load_statements_by_account(db) -> dict[str, list[Any]]:
    """Statement period info, grouped by account - just enough for the grid,
    not the full Statement model."""
    from types import SimpleNamespace

    by_account: dict[str, list[Any]] = defaultdict(list)
    for row in repo.get_statements(db):
        if not row.get("account_id"):
            continue
        by_account[row["account_id"]].append(SimpleNamespace(
            id=row["id"],
            period_start=date.fromisoformat(row["period_start"]) if row.get("period_start") else None,
            period_end=date.fromisoformat(row["period_end"]) if row.get("period_end") else None,
        ))
    return by_account


def _attribute_and_group_files(db, accounts: list[Any]) -> dict[str, list[Any]]:
    """Every file, grouped by the account it belongs to.

    A parsed file already carries its account_id. A failed or locked file
    never got far enough for normalize() to identify an account at all, so it
    is attributed by INSTITUTION instead - and only when that is unambiguous.
    Dad's SBI mail matches no account of the user's own, so it is correctly
    attributed to nothing and never appears on this grid; it would only be
    misattributed if the user held two SBI accounts of their own, which is
    the one case this intentionally leaves unplaced rather than guess wrong.
    """
    from ..ingestion.gmail_source import institution_for_sender

    by_institution: dict[str, list[Any]] = defaultdict(list)
    for a in accounts:
        by_institution[a.institution.lower()].append(a)

    grouped: dict[str, list[Any]] = defaultdict(list)
    to_persist = []
    for record in repo.list_source_files(db):
        if not record.period_hint:
            guess = guess_period_hint(record.filename)
            if guess:
                record.period_hint = guess
                to_persist.append(record)

        account_id = record.account_id
        if not account_id:
            guess_inst = (institution_for_sender(record.sender) if record.sender
                         else record.institution_guess)
            candidates = by_institution.get((guess_inst or "").lower(), [])
            # Two accounts at the same bank (a card and a savings account, say)
            # make the institution alone ambiguous - the type this file's own
            # extraction reached (even on a run that ultimately failed) is
            # often enough to break the tie, e.g. "unknown" for a bank account
            # a statement never labels explicitly versus "credit_card".
            if len(candidates) > 1 and record.account_type_guess:
                narrowed = [a for a in candidates
                           if a.account_type.value == record.account_type_guess]
                if len(narrowed) == 1:
                    candidates = narrowed
            if len(candidates) == 1:
                account_id = candidates[0].id
        if account_id:
            grouped[account_id].append(record)

    # Cache filename-derived guesses so this scan is cheap on the next load.
    for record in to_persist:
        repo.upsert_source_file(db, record)

    return grouped


@coverage_router.get("")
def get_coverage() -> dict[str, Any]:
    """One row per account, one cell per month: parsed (green), failed
    (orange), or missing (red) - see analytics.coverage for the algorithm."""
    db = get_db()
    accounts = repo.get_accounts(db)
    statements_by_account = _load_statements_by_account(db)
    files_by_account = _attribute_and_group_files(db, accounts)
    return {"accounts": build_coverage(accounts, statements_by_account, files_by_account)}


# --------------------------------------------------------------------------
# Fetching a missing month straight from Gmail
# --------------------------------------------------------------------------

def _search_fragments_for_institution(institution: str) -> list[str]:
    """Reverse of metadata.INSTITUTIONS: sender fragments likely to belong to
    this bank, longest (most specific) first."""
    from ..normalize.metadata import INSTITUTIONS

    lowered = institution.lower()
    fragments = [frag for frag, name in INSTITUTIONS.items() if name.lower() == lowered]
    return sorted(fragments, key=len, reverse=True) or [lowered]


def _month_bounds(month: str) -> tuple[date, date]:
    import calendar as _cal
    year, mon = (int(x) for x in month.split("-"))
    last_day = _cal.monthrange(year, mon)[1]
    return date(year, mon, 1), date(year, mon, last_day)


def _fetch_one_month(job_id: str, account_id: str, month: str) -> dict[str, Any]:
    """Search Gmail for one account's statement for one month, and merge it
    in if found. Returns the outcome dict either way - the caller decides
    whether that means the job succeeded or simply found nothing."""
    from ..ingestion.gmail_source import build_query, find_statements, download_to_cache
    from ..graph.nodes import _account_identity
    from ..normalize.normalizer import normalize
    from datetime import timedelta

    db = get_db()
    account = next((a for a in repo.get_accounts(db) if a.id == account_id), None)
    if account is None:
        return {"status": "failed", "message": "That account no longer exists."}

    from .gmail_routes import _require_client, CACHE
    try:
        client = _require_client()
    except Exception as exc:
        return {"status": "failed", "message": str(exc)}

    profile = repo.get_profile(db)
    candidates = derive_passwords(profile) if profile.has_password_material() else []

    month_start, month_end = _month_bounds(month)
    # A statement is typically emailed days after month-end, sometimes much
    # later for an annual/quarterly one - the window is generous on purpose,
    # and the account-identity + statement_months check afterwards is what
    # actually decides whether a candidate belongs here.
    after = (month_start - timedelta(days=5)).strftime("%Y/%m/%d")
    before = (month_end + timedelta(days=60)).strftime("%Y/%m/%d")
    fragments = _search_fragments_for_institution(account.institution)
    sender_clause = " OR ".join(f"from:{f}" for f in fragments)
    query = build_query(months=None, extra=f"({sender_clause}) after:{after} before:{before}")

    found = find_statements(client, query=query, max_messages=25)
    if not found.attachments:
        return {"status": "failed",
                "message": f"No {account.institution} email matching this "
                           f"window was found in your mailbox."}

    tried = []
    for att in found.attachments:
        [saved] = download_to_cache(client, [att], CACHE)
        path = Path(saved.saved_path)
        digest = ingest_router.file_hash(path)
        cached_pw = repo.get_cached_password(db, digest)
        file_candidates = [cached_pw, *candidates] if cached_pw else candidates

        try:
            extraction = ingest_router.extract(path, password_candidates=file_candidates)
        except Exception as exc:
            tried.append(f"{att.filename}: {exc}")
            continue
        if extraction.needs_password or not extraction.tables:
            tried.append(f"{att.filename}: could not be opened/parsed")
            continue

        statement, parsed_account = normalize(extraction, att.filename, sender=att.sender)
        if _account_identity(parsed_account) != _account_identity(account):
            tried.append(f"{att.filename}: belongs to a different account")
            continue

        resolved_password, password_status = resolve_password_status(path, file_candidates)
        record = repo.SourceFileRecord(
            id=str(uuid.uuid4()), filename=att.filename, filepath=str(path),
            source="gmail", sender=att.sender, message_id=att.message_id,
            size_bytes=att.size, parse_status="pending",
        )
        result = merge_extracted_file_into_ledger(
            db, record, extraction, resolved_password, password_status, digest,
            target_month=month,
        )
        if result["status"] in {"ok", "unreconciled"}:
            return result
        tried.append(f"{att.filename}: {result['message']}")

    return {"status": "failed",
            "message": f"Found {len(found.attachments)} email(s) but none matched "
                       f"this account and month. " + " | ".join(tried[:5])}


def _run_fetch_month_job(job_id: str, account_id: str, month: str, account_label: str) -> None:
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        progress.start(1, f"Searching Gmail for {account_label} · {month}")
        result = _fetch_one_month(job_id, account_id, month)
        progress.item(f"{account_label} {month}",
                     "done" if result["status"] in {"ok", "unreconciled"} else "failed",
                     result["message"])
        progress.complete(result=result, message=result["message"])
    except Exception as exc:  # pragma: no cover - defensive
        progress.fail(f"{type(exc).__name__}: {exc}")


@coverage_router.post("/{account_id}/{month}/fetch")
def fetch_one_month(
    account_id: str, month: str, background: BackgroundTasks,
) -> dict[str, Any]:
    """Search Gmail for exactly one account's statement for exactly one
    month, download it, and parse it - never touching any other month."""
    db = get_db()
    account = next((a for a in repo.get_accounts(db) if a.id == account_id), None)
    if account is None:
        raise HTTPException(404, "No such account.")

    job = jobs.create("process", total=1, phase="Queued")
    background.add_task(_run_fetch_month_job, job.id, account_id, month, account.display_name())
    return {"job_id": job.id}


def _run_fetch_all_missing_job(job_id: str, targets: list[tuple[str, str, str]]) -> None:
    job = jobs.get(job_id)
    progress = JobProgress(job)
    try:
        progress.start(len(targets), "Fetching missing statements")
        found = 0
        for account_id, month, label in targets:
            if progress.cancelled:
                progress.fail("Cancelled by user.")
                return
            result = _fetch_one_month(job_id, account_id, month)
            ok = result["status"] in {"ok", "unreconciled"}
            found += ok
            progress.item(f"{label} · {month}", "done" if ok else "skipped", result["message"])
        progress.complete(
            result={"found": found, "attempted": len(targets)},
            message=f"Found {found} of {len(targets)} missing statement(s).")
    except Exception as exc:  # pragma: no cover - defensive
        progress.fail(f"{type(exc).__name__}: {exc}")


@coverage_router.post("/fetch-all-missing")
def fetch_all_missing(background: BackgroundTasks) -> dict[str, Any]:
    """Attempt every red (missing) cell on the grid, one Gmail search per
    account+month. Each fetch is independent, so one miss never blocks the
    rest, and only ever adds statements - nothing already green is touched.
    """
    db = get_db()
    accounts = repo.get_accounts(db)
    statements_by_account = _load_statements_by_account(db)
    files_by_account = _attribute_and_group_files(db, accounts)
    rows = build_coverage(accounts, statements_by_account, files_by_account)

    targets = [
        (row["account_id"], m["month"], row["display_name"])
        for row in rows for m in row["months"] if m["status"] == "missing"
    ]
    # A red cell for the CURRENT month is not really "missing" yet - the
    # statement may not have been issued. Skip it so this doesn't nag about a
    # billing cycle still in progress.
    from datetime import date as _date
    current_month = f"{_date.today().year:04d}-{_date.today().month:02d}"
    targets = [t for t in targets if t[1] != current_month]

    if not targets:
        raise HTTPException(400, "Nothing is missing.")

    job = jobs.create("process", total=len(targets), phase="Queued")
    background.add_task(_run_fetch_all_missing_job, job.id, targets)
    return {"job_id": job.id, "count": len(targets)}
