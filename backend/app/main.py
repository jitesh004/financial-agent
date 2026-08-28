"""FastAPI application.

Analysis runs in a background thread rather than blocking the upload request:
parsing a decade of statements takes minutes, and an HTTP request that hangs
that long dies to a proxy timeout. The frontend polls /api/runs/{id} instead.

Results are held in memory for the active session AND persisted to SQLite, so
a restart doesn't lose the ledger.
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import files_routes, gmail_routes
from .api import serializers as ser
from .db.database import get_db
from .db import repository as repo
from .graph.build import build_graph
from .ingestion.router import SUPPORTED_EXTENSIONS, file_hash
from .models.schemas import Category

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # optional dependency; env vars still work without it
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("financial-agent")

ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

app = FastAPI(title="Financial Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    # The Vite dev server. Tightened deliberately: this app holds a complete
    # financial history, so it should never be reachable from an arbitrary origin.
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(gmail_routes.router)
app.include_router(files_routes.router)
app.include_router(files_routes.coverage_router)


class RunStore:
    """In-memory registry of analysis runs.

    Thread-safe because uploads run on a background thread while the API keeps
    serving reads.
    """

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._latest: str | None = None
        self._lock = threading.Lock()

    def create(self, run_id: str, file_count: int) -> None:
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id, "status": "queued", "progress": "Queued",
                "file_count": file_count, "errors": [], "warnings": [],
            }

    def update(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            self._runs.setdefault(run_id, {"run_id": run_id}).update(fields)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._runs.get(run_id)

    def set_latest(self, run_id: str) -> None:
        with self._lock:
            self._latest = run_id

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._runs.get(self._latest) if self._latest else None

    def create_from_payload(self, run_id: str, payload: dict[str, Any]) -> str:
        """Register an already-computed result as the current dashboard view.

        Used by the Gmail flow, which parses files itself (to report per-file
        progress) rather than going through the upload path, but must still
        land in the same place the dashboard reads from.
        """
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id, "status": "complete", "progress": "Done",
                "file_count": len(payload.get("statements") or []),
                "errors": [], "warnings": [], "result": payload,
            }
            self._order_latest(run_id)
        return run_id

    def _order_latest(self, run_id: str) -> None:
        self._latest = run_id


runs = RunStore()


# --------------------------------------------------------------------------
# Upload & analysis
# --------------------------------------------------------------------------

@app.post("/api/upload")
async def upload(
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    use_llm: bool = Form(True),
    horizon_months: int = Form(6),
) -> dict[str, Any]:
    """Accept statement files and kick off analysis in the background."""
    if not files:
        raise HTTPException(400, "No files were uploaded.")

    run_id = str(uuid.uuid4())
    run_dir = UPLOAD_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, Any]] = []
    rejected: list[str] = []
    seen_hashes: set[str] = set()

    for upload_file in files:
        name = Path(upload_file.filename or "unnamed").name
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            rejected.append(f"{name}: unsupported type '{suffix or 'none'}'")
            continue

        target = run_dir / name
        with target.open("wb") as out:
            shutil.copyfileobj(upload_file.file, out)

        size = target.stat().st_size
        if size == 0:
            rejected.append(f"{name}: file is empty")
            target.unlink(missing_ok=True)
            continue
        if size > MAX_UPLOAD_BYTES:
            rejected.append(f"{name}: exceeds the {MAX_UPLOAD_BYTES // 1024 // 1024}MB limit")
            target.unlink(missing_ok=True)
            continue

        # Catch the same file uploaded twice in one batch, before it can
        # duplicate every transaction it contains.
        digest = file_hash(target)
        if digest in seen_hashes:
            rejected.append(f"{name}: identical to another file in this upload, skipped")
            target.unlink(missing_ok=True)
            continue
        seen_hashes.add(digest)

        tasks.append({"path": str(target), "filename": name})

    if not tasks:
        raise HTTPException(
            400,
            {"message": "None of the uploaded files could be accepted.",
             "rejected": rejected},
        )

    runs.create(run_id, len(tasks))
    runs.update(run_id, warnings=rejected)
    background.add_task(_run_analysis, run_id, tasks, use_llm, horizon_months)

    return {
        "run_id": run_id,
        "accepted": [t["filename"] for t in tasks],
        "rejected": rejected,
        "status": "queued",
    }


def _run_analysis(
    run_id: str,
    tasks: list[dict[str, Any]],
    use_llm: bool,
    horizon_months: int,
) -> None:
    """Execute the graph and persist the result. Runs off the request thread."""
    runs.update(run_id, status="running", progress="Extracting statements")
    try:
        # Derive candidate passwords from the stored profile so protected PDFs
        # open automatically. This is the one place the profile (PII) is read;
        # it produces only passwords, which are used locally and never logged in
        # full or sent anywhere.
        from .ingestion.passwords import derive_passwords
        from .ingestion.router import file_hash as _file_hash
        db = get_db()
        profile = repo.get_profile(db)
        candidates = derive_passwords(profile) if profile.has_password_material() else []

        # A password that opened this exact file content before is tried
        # first, ahead of the profile's derived set - it survives a profile
        # edit, and it is the only thing that can open a file whose password
        # was never derivable at all (one the user typed in manually earlier).
        for task in tasks:
            try:
                digest = _file_hash(Path(task["path"]))
            except OSError:
                continue
            cached = repo.get_cached_password(db, digest)
            if cached:
                task["password_candidates"] = [cached, *candidates]

        graph = build_graph()
        state = graph.invoke(
            {
                "run_id": run_id,
                "file_tasks": tasks,
                "password_candidates": candidates,
                "holder_names": [profile.full_name] if profile.full_name else [],
                "use_llm": use_llm,
                "horizon_months": horizon_months,
            },
            {"recursion_limit": 60},
        )

        runs.update(run_id, progress="Saving results")
        _persist(state)
        _save_file_registry(state, source="upload")

        payload = _build_payload(state)
        runs.update(
            run_id,
            status="complete",
            progress="Done",
            errors=state.get("errors") or [],
            warnings=(runs.get(run_id) or {}).get("warnings", []) + (state.get("warnings") or []),
            result=payload,
        )
        runs.set_latest(run_id)
        log.info("run %s complete: %d transactions", run_id,
                 len(state.get("transactions") or []))

    except Exception as exc:
        log.exception("run %s failed", run_id)
        runs.update(run_id, status="failed", progress="Failed",
                    errors=[f"{type(exc).__name__}: {exc}"])


def _persist(state: dict[str, Any]) -> None:
    """Write accounts, statements and transactions to SQLite."""
    db = get_db()
    accounts = state.get("accounts") or {}

    graph_id_to_db_id: dict[str, str] = {}
    for graph_id, account in accounts.items():
        account.id = None  # let the repo resolve identity against existing rows
        db_id = repo.upsert_account(db, account)
        graph_id_to_db_id[graph_id] = db_id
        account.id = db_id

    for entry in state.get("statements") or []:
        statement = entry.get("statement")
        if statement is None:
            continue
        db_account_id = graph_id_to_db_id.get(statement.account_id or "")
        if db_account_id:
            repo.save_statement(db, statement, db_account_id,
                                entry.get("reconciliation"))

    transactions = state.get("transactions") or []
    for txn in transactions:
        txn.account_id = graph_id_to_db_id.get(txn.account_id or "", txn.account_id)
    if transactions:
        repo.save_transactions(db, transactions)

    report = state.get("transfer_report")
    if report is not None and report.pairs:
        repo.save_transfer_pairs(db, report.pairs)

    series = state.get("recurring") or []
    if series:
        for s in series:
            s.account_id = graph_id_to_db_id.get(s.account_id or "", s.account_id)
        repo.save_recurring_series(db, series)


#: ParsedFile.status -> the file registry's parse_status. Kept as separate
#: vocabularies because gmail_routes.py's own loop uses a couple of statuses
#: ("duplicate") this pipeline never produces.
_PARSE_STATUS = {
    "ok": "parsed", "unreconciled": "unreconciled",
    "failed": "failed", "needs_password": "needs_password",
}


def _save_file_registry(state: dict[str, Any], source: str) -> None:
    """Record every file this run touched - success, failure, or lock.

    This is the ONLY place a failed or locked file is remembered at all: the
    statements/transactions tables hold successes exclusively. Without this,
    "which files parsed and which didn't" and "retry this one" have nothing to
    query, and a solved password is forgotten the moment the run ends.
    """
    db = get_db()
    for entry in state.get("statements") or []:
        statement = entry.get("statement")
        record = repo.SourceFileRecord(
            id=str(uuid.uuid4()),
            filename=entry.get("filename") or "",
            filepath=entry.get("filepath") or "",
            file_hash=entry.get("file_hash") or "",
            source=source,
            size_bytes=entry.get("size_bytes"),
            password=entry.get("password"),
            password_status=entry.get("password_status") or "unknown",
            parse_status=_PARSE_STATUS.get(entry.get("status"), entry.get("status") or "failed"),
            institution_guess=(entry.get("account").institution
                               if entry.get("account") else ""),
            account_type_guess=(entry.get("account").account_type.value
                                if entry.get("account") else ""),
            statement_id=statement.id if statement else None,
            transaction_count=entry.get("transaction_count") or 0,
            error_message=entry.get("message") or "",
        )
        repo.upsert_source_file(db, record)
    repo.backfill_source_file_account_ids(db)


def _build_payload(state: dict[str, Any]) -> dict[str, Any]:
    accounts = state.get("accounts") or {}
    statements = state.get("statements") or []

    return {
        "analysis": ser.analysis_json(state.get("analysis")),
        "accounts": [ser.account_json(a) for a in accounts.values()],
        "loans": [ser.loan_json(p) for p in state.get("loan_projections") or []],
        "forecast": ser.forecast_json(state.get("forecast")),
        "recurring": [ser.recurring_json(s) for s in state.get("recurring") or []],
        "transfers": ser.transfer_json(state.get("transfer_report")),
        "narrative": ser.jsonable(state.get("narrative") or {}),
        "statements": [
            {
                "filename": s.get("filename"),
                "status": s.get("status"),
                "message": s.get("message", ""),
                "transaction_count": s.get("transaction_count", 0),
                **(ser.statement_json(s["statement"]) if s.get("statement") else {}),
            }
            for s in statements
        ],
        "data_quality": (state.get("report") or {}).get("brief", {}).get("data_quality", {}),
    }


# --------------------------------------------------------------------------
# Read endpoints
# --------------------------------------------------------------------------

@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(404, f"No run with id {run_id}")
    return run


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    """The most recent completed analysis."""
    run = runs.latest()
    if run and run.get("result"):
        return {"status": "ok", "run_id": run["run_id"], **run["result"]}

    # Nothing in memory - the server restarted since the last upload or Gmail
    # process. Every transaction already carries its computed category and
    # transfer flags (they were persisted, not just held in memory), so the
    # dashboard can be rebuilt straight from the database instead of asking
    # the user to re-parse every PDF just to see figures that already exist.
    db = get_db()
    if repo.count_transactions(db) == 0:
        return {"status": "empty",
                "message": "No statements have been analyzed yet."}

    run_id = _rebuild_from_persisted_data(db)
    rebuilt = runs.get(run_id)
    return {"status": "ok", "run_id": run_id, **rebuilt["result"]}


def _rebuild_from_persisted_data(db) -> str:
    """Recompute the dashboard from what is already in the database.

    No PDF is touched. Categorization and transfer-matching already happened
    once at ingestion time and their results are columns on each stored
    transaction, so this is pure in-memory aggregation - the same cost as any
    other dashboard load, not a re-import.
    """
    from .analytics.engine import analyze
    from .analytics import forecast as forecast_mod
    from .analytics import loans as loans_mod
    from .analytics.recurring import detect_recurring
    from .models.schemas import AccountType
    from .api.files_routes import all_statement_rows

    accounts = {a.id: a for a in repo.get_accounts(db)}
    transactions = repo.get_transactions(db)

    analysis = analyze(transactions, accounts)
    recurring = detect_recurring(transactions)
    loan_projections = []
    for account_id, account in accounts.items():
        account_txns = [t for t in transactions if t.account_id == account_id]
        projection = loans_mod.project_loan(account, account_txns)
        if projection:
            loan_projections.append(projection)
    opening = sum(
        (a.current_balance or 0) for a in accounts.values()
        if a.account_type in {AccountType.SAVINGS, AccountType.CURRENT, AccountType.WALLET}
    )
    forecast = forecast_mod.forecast(
        monthly=analysis.monthly, series=recurring, opening_balance=opening,
        horizon_months=6, as_of=analysis.period_end,
    )
    state = {
        "accounts": accounts, "transactions": transactions,
        "recurring": recurring, "analysis": analysis,
        "loan_projections": loan_projections, "forecast": forecast,
        "statements": [],
    }
    payload = _build_payload(state)
    payload["statements"] = all_statement_rows(db)
    return runs.create_from_payload(str(uuid.uuid4()), payload)


@app.post("/api/reanalyze")
def reanalyze(background: BackgroundTasks, horizon_months: int = 6,
              use_llm: bool = True) -> dict[str, Any]:
    """Re-run analysis over every file already uploaded in this workspace."""
    tasks = [
        {"path": str(p), "filename": p.name}
        for run_dir in sorted(UPLOAD_DIR.iterdir()) if run_dir.is_dir()
        for p in sorted(run_dir.iterdir())
        if p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not tasks:
        raise HTTPException(400, "No previously uploaded files were found.")

    # Same content under two run folders would double every transaction.
    unique: dict[str, dict[str, Any]] = {}
    for task in tasks:
        unique.setdefault(file_hash(Path(task["path"])), task)
    tasks = list(unique.values())

    run_id = str(uuid.uuid4())
    runs.create(run_id, len(tasks))
    get_db().reset()
    background.add_task(_run_analysis, run_id, tasks, use_llm, horizon_months)
    return {"run_id": run_id, "file_count": len(tasks), "status": "queued"}


@app.get("/api/accounts")
def list_accounts() -> list[dict[str, Any]]:
    return [ser.account_json(a) for a in repo.get_accounts(get_db())]


@app.get("/api/transactions")
def list_transactions(
    account_id: str | None = None,
    category: str | None = None,
    start: str | None = None,
    end: str | None = None,
    statement_id: str | None = None,
    rail: str | None = None,
    sort_by: str = "date",
    sort_dir: str = "asc",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    db = get_db()
    # Multiple accounts (or categories) come in as one comma-separated param -
    # "select card or account, multiple or single" needs an IN clause, not a
    # single equality check.
    account_ids = [a for a in account_id.split(",") if a] if account_id else None
    categories = [c for c in category.split(",") if c] if category else None
    filters = dict(
        account_id=account_ids,
        category=categories,
        start=date.fromisoformat(start) if start else None,
        end=date.fromisoformat(end) if end else None,
        statement_id=statement_id,
        rail=rail,
    )
    txns = repo.get_transactions(
        db, **filters, sort_by=sort_by, sort_dir=sort_dir,
        limit=min(limit, 1000), offset=offset,
    )
    return {
        "transactions": [ser.transaction_json(t) for t in txns],
        "limit": limit,
        "offset": offset,
        # Filtered to match what was actually returned - previously this
        # always reported the WHOLE table's row count regardless of any
        # filter, so a filtered view claimed far more pages than it had.
        "total": repo.count_transactions(db, **filters),
    }


@app.patch("/api/transactions/{txn_id}/category")
def recategorize(txn_id: str, payload: dict[str, str]) -> dict[str, Any]:
    """Apply a user correction and teach the merchant cache.

    The correction is permanent: the cache records source='user', which no
    later model guess is allowed to overwrite.
    """
    raw = (payload or {}).get("category", "")
    try:
        category = Category(raw.strip().lower())
    except ValueError:
        raise HTTPException(
            400,
            f"'{raw}' is not a valid category. Valid: "
            f"{', '.join(c.value for c in Category)}",
        )

    db = get_db()
    matches = [t for t in repo.get_transactions(db) if t.id == txn_id]
    if not matches:
        raise HTTPException(404, f"No transaction with id {txn_id}")

    from .categorize.llm_categorizer import record_user_correction

    txn = matches[0]
    record_user_correction(db, txn, category)
    repo.update_transaction_categories(db, [txn])
    return {"status": "ok", "transaction": ser.transaction_json(txn)}


@app.get("/api/statements")
def list_statements() -> list[dict[str, Any]]:
    return ser.jsonable(repo.get_statements(get_db()))


@app.get("/api/categories")
def list_categories() -> list[str]:
    return [c.value for c in Category]


@app.post("/api/reset")
def reset() -> dict[str, str]:
    """Delete all stored data. The user's 'start over' button."""
    get_db().reset()
    for run_dir in UPLOAD_DIR.iterdir():
        if run_dir.is_dir():
            shutil.rmtree(run_dir, ignore_errors=True)
    return {"status": "reset"}


@app.get("/api/profile")
def get_profile() -> dict[str, Any]:
    """Return the profile. PAN and DOB are echoed back so the form can show them,
    but never leave this local API."""
    profile = repo.get_profile(get_db())
    return {
        "full_name": profile.full_name,
        "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else "",
        "pan": profile.pan,
        "mobile": profile.mobile,
        "custom_passwords": profile.custom_passwords,
        "has_password_material": profile.has_password_material(),
    }


@app.put("/api/profile")
def put_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Save the profile used for password derivation and account matching."""
    from .models.profile import UserProfile

    dob = (payload.get("date_of_birth") or "").strip()
    # excluded_senders is managed separately (PUT /api/gmail/ignored) and this
    # form never sends it - building a fresh UserProfile without carrying it
    # forward would silently wipe every family/firm account the user had
    # excluded the next time they touched their name, DOB, PAN or mobile.
    existing = repo.get_profile(get_db())
    try:
        profile = UserProfile(
            full_name=(payload.get("full_name") or "").strip(),
            date_of_birth=date.fromisoformat(dob) if dob else None,
            pan=(payload.get("pan") or "").strip(),
            mobile=(payload.get("mobile") or "").strip(),
            custom_passwords=[p for p in (payload.get("custom_passwords") or []) if p],
            excluded_senders=existing.excluded_senders,
        )
    except ValueError as exc:
        raise HTTPException(400, f"Invalid profile: {exc}")

    repo.save_profile(get_db(), profile)

    # Report how many candidate passwords this profile can produce, without
    # ever returning the passwords themselves.
    from .ingestion.passwords import derive_passwords
    return {
        "status": "saved",
        "password_candidates": len(derive_passwords(profile)),
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    import os
    db = get_db()
    return {
        "status": "ok",
        "database": str(db.path),
        "transactions_stored": repo.count_transactions(db),
        "llm_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "supported_formats": sorted(SUPPORTED_EXTENSIONS),
    }


@app.exception_handler(Exception)
async def unhandled(request, exc):  # pragma: no cover
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )
