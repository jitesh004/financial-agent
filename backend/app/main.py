"""FastAPI application.

Analysis runs in a background thread rather than blocking the upload request:
parsing a decade of statements takes minutes, and an HTTP request that hangs
that long dies to a proxy timeout. The frontend polls /api/runs/{id} instead.

Results are held in memory for the active session AND persisted to PostgreSQL,
so a restart doesn't lose the ledger.

Every /api route except health and the sign-in endpoints requires a session,
and which user's rows a request can reach is decided by the database rather
than by this file - see auth/session.py and db/engine.py.
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from pydantic import BaseModel

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import storage
from .analytics import periods
from .api import (auth_routes, files_routes, gmail_routes, job_routes,
                  query_routes, rules_routes, settings_routes, staging_routes,
                  wealth_routes)
from .auth.session import AuthContextMiddleware
from .api import serializers as ser
from .db.database import CLEAR_SCOPES, get_db
from .db import repository as repo
from .db.engine import current_tenant
from .graph.build import build_graph
from .ingestion.router import SUPPORTED_EXTENSIONS, file_hash
from .jobs import jobs
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

# Suppress noisy third-party PDF library warnings that are harmless for
# Indian bank statements. pdfminer warns about missing FontBBox values in
# non-standard fonts; pypdf warns about /Perms signature verification on
# password-protected files it has already decrypted successfully. Neither
# affects text extraction quality.
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pypdf").setLevel(logging.ERROR)


ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Reconcile job state with reality before serving anything.

    A job cannot outlive the process that ran it, so anything still marked
    running belongs to a process that is gone. Leaving those rows alone would
    show a progress bar that can never move again - strictly worse than saying
    plainly that the work stopped, and the difference between offering to
    resume it and pretending nothing was lost.
    """
    try:
        # Once per account. `jobs` are per-user rows behind row-level
        # security, so there is no connection that can see all of them at
        # once - which is the point. Sweeping the user list is how a
        # cross-tenant maintenance pass is done here, deliberately one tenant
        # at a time rather than by handing anything a key to the whole table.
        db = get_db()
        stopped = sum(jobs.recover(owner) for owner in db.known_tenants())
        if stopped:
            log.info("marked %d job(s) interrupted from a previous run", stopped)
    except Exception:  # bookkeeping must never block startup
        log.exception("could not reconcile job state at startup")

    yield

    # Last chance to record where anything still in flight had got to.
    try:
        jobs.flush()
    except Exception:
        log.exception("could not flush job state at shutdown")


app = FastAPI(title="Financial Agent", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # The Vite dev server. Tightened deliberately: this app holds a complete
    # financial history, so it should never be reachable from an arbitrary origin.
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added after CORS so it runs inside it: a 401 still needs the CORS headers,
# or the browser reports an opaque network error instead of "sign in".
app.add_middleware(AuthContextMiddleware)

app.include_router(auth_routes.router)
app.include_router(auth_routes.onboarding_router)
app.include_router(gmail_routes.router)
app.include_router(files_routes.router)
app.include_router(files_routes.coverage_router)
app.include_router(query_routes.router)
app.include_router(job_routes.router)
app.include_router(wealth_routes.router)
app.include_router(settings_routes.router)
app.include_router(staging_routes.router)
app.include_router(rules_routes.router)


class RunStore:
    """In-memory registry of analysis runs, per signed-in user.

    Thread-safe because uploads run on a background thread while the API keeps
    serving reads.

    Every read and write is scoped to the current tenant, and that is not a
    refinement - it is the whole correctness of this class. It began life in a
    single-user program, where "the latest run" could be one string. Carried
    unchanged into a server that several people sign in to, that one string
    meant whoever parsed last owned the dashboard for EVERYBODY: user A
    imported, user B imported, and A's next page load returned B's payload
    verbatim. `/api/dashboard` reads this cache before it touches the
    database, so the row-level security that protects every table underneath
    never got a chance to apply.

    Keying by `current_tenant()` rather than by a caller-supplied id is
    deliberate: it is the same function the database policies read, so the
    cache and the ledger cannot disagree about who is asking.
    """

    def __init__(self) -> None:
        #: tenant -> run_id -> run. Nested rather than flat so a run can never
        #: be reached from the wrong account even by knowing its uuid.
        self._runs: dict[str | None, dict[str, dict[str, Any]]] = {}
        #: tenant -> the run its dashboard should show.
        self._latest: dict[str | None, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _who() -> str | None:
        from .db.engine import current_tenant
        return current_tenant()

    def _bucket(self) -> dict[str, dict[str, Any]]:
        """This tenant's runs. Caller holds the lock."""
        return self._runs.setdefault(self._who(), {})

    def create(self, run_id: str, file_count: int) -> None:
        with self._lock:
            self._bucket()[run_id] = {
                "run_id": run_id, "status": "queued", "progress": "Queued",
                "file_count": file_count, "errors": [], "warnings": [],
            }

    def update(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            self._bucket().setdefault(run_id, {"run_id": run_id}).update(fields)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._bucket().get(run_id)

    def set_latest(self, run_id: str) -> None:
        with self._lock:
            self._latest[self._who()] = run_id

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            run_id = self._latest.get(self._who())
            return self._bucket().get(run_id) if run_id else None

    def create_from_payload(self, run_id: str, payload: dict[str, Any]) -> str:
        """Register an already-computed result as the current dashboard view.

        Used by the Gmail flow, which parses files itself (to report per-file
        progress) rather than going through the upload path, but must still
        land in the same place the dashboard reads from.
        """
        with self._lock:
            self._bucket()[run_id] = {
                "run_id": run_id, "status": "complete", "progress": "Done",
                "file_count": len(payload.get("statements") or []),
                "errors": [], "warnings": [], "result": payload,
            }
            self._latest[self._who()] = run_id
        return run_id

    def clear(self) -> None:
        """Forget this user's cached runs.

        Called after any clearing action. The dashboard payload is a snapshot
        of figures computed from rows that may have just been deleted, and
        `/api/dashboard` returns it verbatim without revalidating - so leaving
        it in place shows the user totals for a ledger that no longer exists.

        Only the caller's own runs go: one person clearing their data has no
        business emptying everybody else's dashboard.
        """
        with self._lock:
            who = self._who()
            self._runs.pop(who, None)
            self._latest.pop(who, None)
        # The stored payload is the same snapshot, and /api/dashboard prefers
        # it over recomputing - so clearing only the in-memory copy invalidated
        # nothing a reader could see.
        try:
            repo.clear_analysis_runs(get_db())
        except Exception:  # pragma: no cover - never block a clear on this
            log.warning("could not drop the stored analysis payload")


runs = RunStore()


@dataclass(frozen=True)
class ClearAction:
    """One clearing scope, described in terms of what it costs the user.

    The ordering is by cost of reacquisition, not by how much is deleted:
    parsed rows are pure CPU to rebuild, statement files cost network and
    Gmail quota (and are unrecoverable if the user no longer has the
    original), AI inference costs actual money, and a human decision cannot be
    regenerated at any price.
    """

    scope: str
    label: str
    description: str
    clears: tuple[str, ...]
    preserves: tuple[str, ...]
    destructive: bool = False
    removes_files: bool = False
    confirm_phrase: str = ""


CLEAR_ACTIONS: tuple[ClearAction, ...] = (
    ClearAction(
        scope="derived",
        label="Refresh dashboard",
        description="Recompute totals, recurring series and forecasts from the "
                    "transactions already stored.",
        clears=("dashboard aggregates", "recurring series", "run history"),
        preserves=("transactions", "statement files", "AI inference", "your decisions"),
    ),
    ClearAction(
        scope="parsed_data",
        label="Clear parsed ledger",
        description="Drop the parsed ledger and rebuild it from the statement "
                    "files. Use this after a parsing fix.",
        clears=("transactions", "accounts", "statements"),
        preserves=("statement files", "AI inference", "your decisions", "your profile"),
    ),
    ClearAction(
        scope="staged_imports",
        label="Forget everything staged",
        description="Empty the import wizard's staging area - every document "
                    "it has read and every choice about them - without "
                    "touching the ledger those choices already produced. "
                    "Start here when a narrower scan should mean a smaller "
                    "set, because staging accumulates and never shrinks on "
                    "its own.",
        clears=("staged documents", "what is ticked for import"),
        preserves=("your ledger", "downloaded files", "your decisions"),
    ),
    ClearAction(
        scope="files",
        label="Clear downloaded files",
        description="Remove the statement files themselves along with the "
                    "ledger built from them. Files you uploaded by hand cannot "
                    "be re-downloaded and will be gone for good.",
        clears=("statement files", "file registry", "transactions", "accounts"),
        preserves=("AI inference", "your decisions", "your profile"),
        destructive=True,
        removes_files=True,
        # The only non-factory-reset scope that can destroy something
        # unrecoverable: a manually uploaded statement exists nowhere else.
        confirm_phrase="DELETE FILES",
    ),
    ClearAction(
        scope="ai_inferences",
        label="Clear AI inference",
        description="Discard every cached model answer and learned merchant "
                    "category. These cost real money to produce and will be "
                    "re-billed if they are needed again.",
        clears=("cached model answers", "learned merchant categories"),
        preserves=("transactions", "statement files", "your decisions", "your profile"),
        destructive=True,
    ),
    ClearAction(
        scope="decisions",
        label="Clear my decisions",
        description="Discard every correction, note and exclusion you have "
                    "made. Nothing can regenerate these.",
        clears=("category corrections", "notes", "exclusions"),
        preserves=("transactions", "statement files", "AI inference", "your profile"),
        destructive=True,
    ),
    ClearAction(
        scope="everything",
        label="Factory reset",
        description="Delete everything, including your profile. The workspace "
                    "returns to its first-run state.",
        clears=("everything",),
        preserves=(),
        destructive=True,
        removes_files=True,
        confirm_phrase="DELETE EVERYTHING",
    ),
)


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

        # Move it into the durable, content-addressed store and work from
        # there. The per-run directory is staging only: a manually uploaded
        # statement can be the only copy in existence, and it must not live
        # somewhere that clearing a derived ledger would delete.
        durable = storage.adopt(target, digest, remove_source=True)

        tasks.append({"path": str(durable), "filename": name})

    if not tasks:
        raise HTTPException(
            400,
            {"message": "None of the uploaded files could be accepted.",
             "rejected": rejected},
        )

    # An upload is staged and read, not imported. It reaches the ledger the
    # same way a downloaded statement does - by being ticked on Review and
    # processed - so a file dragged in here changes no total until someone has
    # seen what was in it.
    from .db import staging as _staging
    from .api.staging_routes import _run_parse

    db = get_db()
    staged_ids = []
    for task in tasks:
        try:
            staged_ids.append(_staging.add(
                db, file_hash(Path(task["path"])),
                filename=task["filename"], path=task["path"],
                origin="upload", kind="statement",
                scan_intent="upload"))
        except Exception:
            log.exception("could not stage %s", task["filename"])
    _staging.apply_supersession(db)

    pending = _staging.unparsed(db)
    job_id = None
    if pending:
        job = jobs.create("stage_parse", total=len(pending), phase="Queued",
                          request={"count": len(pending)})
        job_id = job.id
        background.add_task(_run_parse, job.id)

    return {
        "run_id": run_id,
        "job_id": job_id,
        "staged": len(staged_ids),
        "accepted": [t["filename"] for t in tasks],
        "rejected": rejected,
        "status": "queued",
    }


def _run_analysis(
    run_id: str,
    tasks: list[dict[str, Any]],
    use_llm: bool,
    horizon_months: int,
    incremental: bool = False,
    owner: str | None = None,
) -> None:
    """Execute the graph and persist the result. Runs off the request thread.

    `owner` is captured in the request that asked for the work and re-bound
    here. Starlette does copy the request's context into the worker thread, so
    this is usually redundant - but everything this function writes, the ledger
    rows and the cached dashboard alike, is filed under whoever is bound at the
    time. That is not a guarantee to rest on framework behaviour: unbound, the
    run would be written under no tenant and the user's dashboard would come
    back empty, and worse, a future change to how this is dispatched could
    silently file one person's statements against another.
    """
    from .db.engine import tenant_scope

    with tenant_scope(owner or current_tenant()):
        _run_analysis_scoped(run_id, tasks, use_llm, horizon_months, incremental)


def _run_analysis_scoped(
    run_id: str,
    tasks: list[dict[str, Any]],
    use_llm: bool,
    horizon_months: int,
    incremental: bool = False,
) -> None:
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

        if incremental:
            # /api/upload's graph only ever sees the files just added - it
            # has no idea a ledger already exists, so `state` here is that
            # delta alone, not the whole picture. Building the dashboard
            # payload straight from it showed the user ONLY the newly
            # uploaded files' accounts and transactions - every earlier
            # upload appeared to have vanished, most visibly when the new
            # files failed to parse and the delta was empty. Nothing was
            # actually lost: `_persist` above merges into the same database
            # rows an ordinary reanalyze reads from, by account identity and
            # by id. Rebuilding from what is now in the database, the same
            # way a restart recovers the dashboard, is what makes adding
            # files actually additive instead of a fresh start every time.
            rebuilt_run_id = _rebuild_from_persisted_data(get_db())
            payload = runs.get(rebuilt_run_id)["result"]
        else:
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
        if not incremental:
            remember_run(run_id, payload)
        log.info("run %s complete: %d transactions", run_id,
                 len(state.get("transactions") or []))

    except Exception as exc:
        log.exception("run %s failed", run_id)
        runs.update(run_id, status="failed", progress="Failed",
                    errors=[f"{type(exc).__name__}: {exc}"])


def remember_run(run_id: str, payload: dict[str, Any]) -> str:
    """Register a completed run in memory AND on disk.

    The on-disk half is what makes the dashboard survive a restart intact.
    Recomputing it from the stored rows is possible but lossy - no narrative,
    no transfer report - so a restart used to quietly downgrade the dashboard
    rather than restore it.
    """
    runs.create_from_payload(run_id, payload)
    try:
        db = get_db()
        repo.save_analysis_run(
            db, run_id, "complete",
            file_count=len(payload.get("statements") or []),
            payload=payload,
        )
        # Each payload is a whole dashboard, so the history is capped rather
        # than allowed to grow without bound.
        repo.prune_analysis_runs(db)
    except Exception as exc:  # a history write must never fail a run
        log.warning("could not store run %s: %s", run_id, exc)
    return run_id


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

    # Which statement ids actually got a row written, so a transaction
    # pointing at one that didn't (its account resolved to nothing above, or
    # the statement carried no id at all) can be caught before it reaches
    # SQLite - `statement_id` is a foreign key too, and a dangling one fails
    # exactly as unhelpfully as a dangling account_id did.
    persisted_statement_ids: set[str] = set()
    for entry in state.get("statements") or []:
        statement = entry.get("statement")
        if statement is None:
            continue
        db_account_id = graph_id_to_db_id.get(statement.account_id or "")
        if db_account_id:
            saved_id = repo.save_statement(db, statement, db_account_id,
                                           entry.get("reconciliation"))
            persisted_statement_ids.add(saved_id)

    transactions = state.get("transactions") or []
    for txn in transactions:
        txn.account_id = graph_id_to_db_id.get(txn.account_id or "", txn.account_id)
        # Unlike account_id, this column allows NULL - "which statement this
        # came from" is useful attribution, not a fact the transaction needs
        # to exist. Dropping a stale reference loses that attribution for the
        # rows it affects; refusing to save any transaction over it would
        # lose those rows entirely, which is far worse.
        if txn.statement_id and txn.statement_id not in persisted_statement_ids:
            log.warning(
                "transaction %s referenced statement %s which was never "
                "persisted this run - clearing the reference rather than "
                "failing the whole save", txn.id, txn.statement_id)
            txn.statement_id = None

    # Every transaction's account_id was just remapped through the SAME
    # dict the accounts loop above resolved into real database ids, so this
    # can only be non-empty if a transaction referenced an account that never
    # went through that loop at all - a bug upstream, not something SQLite's
    # bare "FOREIGN KEY constraint failed" (no row, no column, no account id)
    # gives any way to diagnose. Caught here with the actual offending ids
    # and a sample row, instead of a full run failing on an error that names
    # nothing.
    known_ids = set(graph_id_to_db_id.values())
    orphaned = [t for t in transactions if t.account_id not in known_ids]
    if orphaned:
        samples = "; ".join(
            f"{t.txn_date} {t.raw_description[:40]!r} (acct={t.account_id})"
            for t in orphaned[:5]
        )
        raise RuntimeError(
            f"{len(orphaned)} transaction(s) reference an account that was "
            f"never persisted this run - refusing to write them rather than "
            f"hit an undiagnosable FOREIGN KEY error. Sample: {samples}"
        )

    if transactions:
        repo.save_transactions(db, transactions)
        # A statement covering a payment an email alert already reported
        # retires that alert. Every import goes through here - upload and
        # Gmail alike - so this is the one place it has to happen. Skipping it
        # leaves both copies in the ledger and inflates spending by exactly the
        # amount the user was most diligent about capturing.
        from .pipeline.alerts import supersede_after_import
        try:
            supersede_after_import(db, transactions)
        except Exception:
            # Never fail an import over this: the statement rows are the
            # reconciled ones and belong in the ledger either way.
            log.exception("could not retire alerts covered by this import")

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
    # Read successfully; it simply is not a bank statement. Recorded as
    # parsed rather than failed because nothing went wrong - a credit report
    # and a holdings statement are real documents with their own readers, and
    # calling them failures sends someone hunting for a bug that is not there.
    "not_a_statement": "parsed",
}


def _save_file_registry(state: dict[str, Any], source: str) -> None:
    """Record every file this run touched - success, failure, or lock.

    This is the ONLY place a failed or locked file is remembered at all: the
    statements/transactions tables hold successes exclusively. Without this,
    "which files parsed and which didn't" and "retry this one" have nothing to
    query, and a solved password is forgotten the moment the run ends.
    """
    db = get_db()
    # Called after _persist, which is the only place a statement actually
    # gets a row written - and only for statements whose account resolved.
    # A statement here can still carry an id from the graph (merge_ledger
    # mints one for every statement it sees) without ever having been
    # persisted, so checking against the id alone is not enough; this is the
    # same dangling-foreign-key shape _persist itself guards against, one
    # table over.
    from .graph.nodes import latest_attempt_per_file

    with db.connection() as conn:
        persisted_statement_ids = {
            r["id"] for r in conn.execute("SELECT id FROM statements").fetchall()
        }
    # Same reasoning as merge_ledger and synthesize: a retried file left
    # every attempt in "statements" (an additive channel), and
    # upsert_source_file's own content-hash dedup would otherwise leave the
    # registry holding whichever attempt happened to be recorded last rather
    # than whichever attempt actually reflects the final outcome.
    for entry in latest_attempt_per_file(state.get("statements") or []):
        statement = entry.get("statement")
        statement_id = statement.id if statement else None
        
        # Extract real period hint from the parsed statement if available
        period_hint = None
        if statement and statement.period_start:
            period_hint = f"{statement.period_start.year:04d}-{statement.period_start.month:02d}"

        if statement_id and statement_id not in persisted_statement_ids:
            statement_id = None
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
            statement_id=statement_id,
            transaction_count=entry.get("transaction_count") or 0,
            error_message=entry.get("message") or "",
            period_hint=period_hint,
        )
        repo.upsert_source_file(db, record)
    repo.backfill_source_file_account_ids(db)


def _build_payload(state: dict[str, Any]) -> dict[str, Any]:
    from .graph.nodes import latest_attempt_per_file

    accounts = state.get("accounts") or {}
    # Deduplicated the same way merge_ledger and synthesize do - a file
    # retried after a failed reconciliation left every attempt in
    # "statements" (an additive channel), so without this the Files/Coverage
    # tabs showed a retried file two or three times over.
    statements = latest_attempt_per_file(state.get("statements") or [])

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

    db = get_db()
    if repo.count_transactions(db) == 0:
        return {"status": "empty",
                "message": "No statements have been analyzed yet."}

    # The last completed run's payload, stored verbatim. Preferred over
    # recomputing because the rebuild below is lossy - it produces no
    # narrative and no transfer report - so a restart used to silently
    # downgrade the dashboard rather than restore it.
    stored = repo.get_latest_analysis_run(db)
    if stored:
        run_id, payload = stored
        runs.create_from_payload(run_id, payload)
        return {"status": "ok", "run_id": run_id, **payload}

    # No stored run either (an older workspace, or the run table was
    # cleared). Every transaction still carries its computed category and
    # transfer flags, so the figures can be recomputed rather than asking the
    # user to re-parse every PDF.
    run_id = _rebuild_from_persisted_data(db)
    rebuilt = runs.get(run_id)
    return {"status": "ok", "run_id": run_id, **rebuilt["result"]}


@app.get("/api/periods")
def list_periods() -> dict[str, Any]:
    """Every reporting period the app offers, already resolved.

    Resolved HERE rather than in the browser so one implementation of "last
    three months" serves the whole app. The frontend renders the picker from
    this list and sends back a preset name; nothing about which months a
    preset means is decided twice.

    `months` is every accounting month the ledger actually has rows in, which
    is what the custom-month pickers offer and what lets a period with no data
    in it say so instead of looking like a month where nothing happened.
    """
    db = get_db()
    months = repo.covered_months(db)
    today = date.today()
    resolved = []
    for preset in periods.PERIOD_PRESETS:
        key = preset["value"]
        if key in {"custom", "custom_months"}:
            # Nothing to resolve until the user draws the window, but the
            # picker still needs to know which shape each one takes.
            resolved.append({**preset,
                             "mode": "months" if key == "custom_months" else "dates",
                             "basis": "accounting" if key == "custom_months" else "date"})
            continue
        window = periods.resolve_period({"preset": key}, today).as_json()
        # The catalogue's own label wins: "Last 3 months" is the name of the
        # preset, and "Jan 2026 - Mar 2026" is what it currently resolves to.
        resolved.append({**preset, **window, "label": preset["label"],
                         "resolved_label": window["label"]})
    return {
        "today": today.isoformat(),
        "presets": resolved,
        "months": [{"month": m, "count": n} for m, n in months],
        "earliest": months[0][0] if months else None,
        "latest": months[-1][0] if months else None,
        "fy_start_month": periods.FY_START_MONTH,
    }


def _period_from_query(
    preset: str | None, start: str | None, end: str | None,
    start_month: str | None, end_month: str | None,
) -> "periods.Period":
    """One query string to one resolved window, or a 400 saying why not."""
    try:
        return periods.resolve_period({
            "preset": preset or ("custom_months" if (start_month or end_month)
                                 else "custom" if (start or end) else "all"),
            "start": start, "end": end,
            "start_month": start_month, "end_month": end_month,
        })
    except periods.PeriodError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/analysis")
def scoped_analysis(
    preset: str | None = None,
    start: str | None = None,
    end: str | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
) -> dict[str, Any]:
    """The dashboard's figures, recomputed for one period.

    Separate from /api/dashboard on purpose. That endpoint answers "what does
    the whole ledger say", including the narrative and the transfer report -
    neither of which is re-derivable per period without re-running the model.
    This one answers "what do the figures say for THIS window", from rows
    already in the database, which is pure aggregation.

    The window is a run of accounting months, so the numbers here agree with
    the Months tab by construction: a salary the period engine assigned to
    August counts in August, whichever side of the month boundary it landed.
    """
    period = _period_from_query(preset, start, end, start_month, end_month)
    db = get_db()

    # What the whole ledger holds, in one aggregate. Answers "is this
    # workspace empty?" and, for a window with nothing in it, "where is the
    # data then?" - without reading a decade of rows to find out.
    months = repo.covered_months(db)
    if not months:
        return {"status": "empty", "range": period.as_json(),
                "message": "No statements have been analyzed yet."}

    # Filtered in SQL rather than in Python: a one-month window on a ten-year
    # ledger should read a month, not the decade. `analyze` still takes the
    # period - it reports the window's own bounds and month count from it -
    # and re-filtering rows that already match is a no-op.
    accounts = {a.id: a for a in repo.get_accounts(db)}
    transactions = repo.get_transactions(
        db, start=period.start, end=period.end,
        month_start=period.start_month, month_end=period.end_month)

    from .analytics.engine import analyze

    result = analyze(transactions, accounts, period=period)
    return {
        "status": "ok",
        "range": period.as_json(),
        "analysis": ser.analysis_json(result),
        "available": {
            "earliest": months[0][0],
            "latest": months[-1][0],
            "transaction_count": sum(count for _, count in months),
        },
    }


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
    return remember_run(str(uuid.uuid4()), payload)


@app.post("/api/reanalyze")
def reanalyze(background: BackgroundTasks, horizon_months: int = 6,
              use_llm: bool = True, months: int | None = None) -> dict[str, Any]:
    """Re-parse every statement file this workspace knows about.

    `months`: if given, only files whose period_hint falls within the last
    N calendar months are included. Files with no period_hint are always
    included so they are not silently dropped.
    """
    from datetime import date
    from dateutil.relativedelta import relativedelta

    db = get_db()

    # Compute cutoff month string (YYYY-MM) when scoping by months
    cutoff_month: str | None = None
    if months:
        cutoff = date.today() - relativedelta(months=months)
        cutoff_month = f"{cutoff.year:04d}-{cutoff.month:02d}"

    tasks = []
    for record in repo.list_source_files(db):
        if not record.filepath:
            continue
        path = Path(record.filepath)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS or not path.exists():
            continue
        # Apply month filter when requested. Files with no period_hint always pass
        # through — we don't know when they are from, so we can't safely exclude them.
        if cutoff_month and record.period_hint and record.period_hint < cutoff_month:
            continue
        tasks.append({"path": str(path), "filename": record.filename})

    # Anything still sitting in the legacy per-run upload folders, for a
    # workspace that predates the durable store.
    if UPLOAD_DIR.is_dir():
        for run_dir in sorted(UPLOAD_DIR.iterdir()):
            if not run_dir.is_dir():
                continue
            for p in sorted(run_dir.iterdir()):
                if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                    tasks.append({"path": str(p), "filename": p.name})

    if not tasks:
        raise HTTPException(400, "No previously uploaded files were found.")

    # Same content under two run folders would double every transaction.
    unique: dict[str, dict[str, Any]] = {}
    for task in tasks:
        unique.setdefault(file_hash(Path(task["path"])), task)
    tasks = list(unique.values())

    run_id = str(uuid.uuid4())
    runs.create(run_id, len(tasks))
    db = get_db()
    db.snapshot("pre-reanalyze")
    db.clear("parsed_data")
    background.add_task(_run_analysis, run_id, tasks, use_llm, horizon_months,
                        owner=current_tenant())
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
    needs_review: bool | None = None,
    accounting_month: str | None = None,
    preset: str | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
) -> dict[str, Any]:
    """The ledger, filtered.

    Two ways to ask for a period, and they mean different things:

      * `preset` / `start_month` / `end_month` select whole ACCOUNTING months
        - the reporting period each row is counted in, which for a salary
        paid on 1 September can be August. This is what every period control
        in the app sends, and it is why a period's total matches the Months
        tab's.
      * `start` / `end` select literal transaction dates. What a custom day
        range means, and what a caller wanting "the rows dated in this week"
        should use.

    `accounting_month` is the single-month shortcut, kept because the Months
    tab has always sent it.
    """
    db = get_db()
    # Multiple accounts (or categories) come in as one comma-separated param -
    # "select card or account, multiple or single" needs an IN clause, not a
    # single equality check.
    account_ids = [a for a in account_id.split(",") if a] if account_id else None
    categories = [c for c in category.split(",") if c] if category else None
    period = _period_from_query(preset, start, end, start_month, end_month)
    filters = dict(
        account_id=account_ids,
        category=categories,
        start=period.start,
        end=period.end,
        statement_id=statement_id,
        rail=rail,
        needs_review=needs_review,
        accounting_month=accounting_month,
        month_start=period.start_month,
        month_end=period.end_month,
    )
    txns = repo.get_transactions(
        db, **filters, sort_by=sort_by, sort_dir=sort_dir,
        limit=min(limit, 1000), offset=offset,
    )
    return {
        "transactions": [ser.transaction_json(t) for t in txns],
        "limit": limit,
        "offset": offset,
        # What the server understood the period to be. A screen that shows
        # figures for a window should be able to print the window it got
        # rather than the one it asked for.
        "range": period.as_json(),
        # Filtered to match what was actually returned - previously this
        # always reported the WHOLE table's row count regardless of any
        # filter, so a filtered view claimed far more pages than it had.
        "total": repo.count_transactions(db, **filters),
    }


class TransactionUpdateReq(BaseModel):
    category: str | None = None
    note: str | None = None
    flow_role: str | None = None
    excluded: bool | None = None

# Defined above its endpoint on purpose: FastAPI resolves the annotation
# when the route is registered, and with the class declared later the
# name did not exist yet - so `payload` was treated as a scalar QUERY
# parameter and every request failed validation with 422.
class BulkTransactionUpdateReq(BaseModel):
    txn_ids: list[str]
    category: str | None = None
    note: str | None = None
    flow_role: str | None = None
    excluded: bool | None = None


# Declared BEFORE /api/transactions/{txn_id}. FastAPI matches routes in
# declaration order, so with the parameterised route first every request
# to /bulk was resolved as a transaction whose id is the literal string
# "bulk" and answered 404 - the endpoint existed and was never reachable.

@app.patch("/api/transactions/bulk")
def bulk_update_transactions(payload: BulkTransactionUpdateReq) -> dict[str, Any]:
    db = get_db()
    update_args = payload.model_dump(exclude_unset=True, exclude={"txn_ids"})
    
    if not update_args or not payload.txn_ids:
        return {"status": "ok", "updated": 0}

    if "category" in update_args and update_args["category"] is not None:
        category = update_args["category"].strip().lower()
        valid_categories = set(Category.all_builtins())
        with db.connection() as conn:
            for r in conn.execute("SELECT name FROM custom_categories").fetchall():
                valid_categories.add(r["name"])
        if category and category not in valid_categories:
            raise HTTPException(400, f"'{update_args['category']}' is not a valid category.")
        update_args["category"] = category

    all_txns = repo.get_transactions(db)
    targets = [t for t in all_txns if t.id in set(payload.txn_ids)]
    
    from .pipeline.overrides import record_decision
    accounts = {a.id: a for a in repo.get_accounts(db) if a.id}
    
    for txn in targets:
        record_decision(db, txn, accounts, **update_args)
        if "category" in update_args and update_args["category"] is not None:
            from .categorize.llm_categorizer import record_user_correction
            record_user_correction(db, txn, update_args["category"])
        
    if targets:
        repo.update_transaction_categories(db, targets)
        
    # The dashboard payload is a snapshot of figures computed from
    # rows that were just edited, and /api/dashboard returns it verbatim
    # without revalidating - so leaving it cached shows the user totals
    # that no longer reflect their own correction.
    runs.clear()

    return {"status": "ok", "updated": len(targets)}


@app.patch("/api/transactions/{txn_id}")
def update_transaction(txn_id: str, payload: TransactionUpdateReq) -> dict[str, Any]:
    """Apply user corrections to a transaction."""
    db = get_db()
    
    if payload.category is not None:
        category = payload.category.strip().lower()
        valid_categories = set(Category.all_builtins())
        with db.connection() as conn:
            for r in conn.execute("SELECT name FROM custom_categories").fetchall():
                valid_categories.add(r["name"])
        
        if category and category not in valid_categories:
            raise HTTPException(400, f"'{payload.category}' is not a valid category.")
        payload.category = category

    matches = [t for t in repo.get_transactions(db) if t.id == txn_id]
    if not matches:
        raise HTTPException(404, f"No transaction with id {txn_id}")

    from .pipeline.overrides import record_decision

    txn = matches[0]
    update_args = payload.model_dump(exclude_unset=True)
    if update_args:
        accounts = {a.id: a for a in repo.get_accounts(db) if a.id}
        record_decision(db, txn, accounts, **update_args)
        if "category" in update_args and update_args["category"] is not None:
            from .categorize.llm_categorizer import record_user_correction
            record_user_correction(db, txn, update_args["category"])
        repo.update_transaction_categories(db, [txn])
        
    # The dashboard payload is a snapshot of figures computed from
    # rows that were just edited, and /api/dashboard returns it verbatim
    # without revalidating - so leaving it cached shows the user totals
    # that no longer reflect their own correction.
    runs.clear()

    return {"status": "ok", "transaction": ser.transaction_json(txn)}


@app.get("/api/statements")
def list_statements() -> list[dict[str, Any]]:
    return ser.jsonable(repo.get_statements(get_db()))


@app.get("/api/categories")
def list_categories() -> list[str]:
    db = get_db()
    builtins = Category.all_builtins()
    with db.connection() as conn:
        custom = [r["name"] for r in conn.execute("SELECT name FROM custom_categories").fetchall()]
    return builtins + custom

@app.get("/api/categories/custom")
def list_custom_categories() -> list[str]:
    """Just the user-added names, so a caller can tell them apart from the
    built-ins without guessing - Settings.jsx used to assume the first 30
    entries of /api/categories were built-in, a count that happens to match
    today but silently breaks the moment a built-in category is ever added
    or removed."""
    db = get_db()
    with db.connection() as conn:
        return [r["name"] for r in conn.execute("SELECT name FROM custom_categories").fetchall()]

class CustomCategoryReq(BaseModel):
    name: str
    color: str = "#6b7280"
    icon: str = "Tag"

@app.post("/api/categories")
def create_category(payload: CustomCategoryReq) -> dict[str, str]:
    repo.add_custom_category(get_db(), payload.name, payload.color, payload.icon)
    return {"status": "ok"}

@app.delete("/api/categories/{name}")
def delete_category(name: str) -> dict[str, str]:
    repo.delete_custom_category(get_db(), name)
    return {"status": "ok"}


@app.get("/api/data/inventory")
def data_inventory() -> dict[str, Any]:
    """What exists right now, and what each clearing action would cost.

    The old UI had one unlabelled "Reset" button that deleted the ledger, the
    file registry AND every uploaded file. Someone clearing a bad parse lost
    the only copy of the statement that produced it. Showing the real counts
    against each scope is the point: the user should be able to see that
    re-parsing keeps their files and their corrections before they click it.
    """
    db = get_db()
    counts: dict[str, int] = {}
    with db.connection() as conn:
        for table in ("transactions", "accounts", "statements", "source_files",
                      "ai_inferences", "merchant_categories", "user_overrides"):
            counts[table] = conn.execute(
                f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]

    return {
        "counts": counts,
        "files": storage.store_stats(),
        "snapshots": db.list_snapshots(),
        "actions": [
            {"scope": s.scope, "label": s.label, "description": s.description,
             "clears": list(s.clears), "preserves": list(s.preserves),
             "destructive": s.destructive, "confirm_phrase": s.confirm_phrase}
            for s in CLEAR_ACTIONS
        ],
    }



#: What is worth showing about each table when a clearing action is about to
#: delete it. Only these columns are ever selected, so a preview cannot
#: accidentally start echoing a column added later - `source_files.password`
#: is the one that matters there, and it is deliberately absent.
#:
#: `user_profile` shows the name and the date it changed but NOT the PAN: a
#: preview exists to say what you are about to lose, and "your identity
#: details, last edited on..." says it without putting a PAN in a table
#: somebody might screenshot.
PREVIEW_COLUMNS: dict[str, str] = {
    # Authored by a human - the rows a preview matters most for.
    "user_overrides": "fingerprint, category, flow_role, note, excluded, updated_at",
    "claims": "counterparty, direction, amount, settled_amount, status, opened_on",
    "claim_settlements": "claim_id, method, amount, settled_on",
    "transaction_splits": "parent_fingerprint, amount, category, note",
    "split_rules": "label, match_text, mine_pct, counterparty, active",
    "settlement_groups": "kind, total_amount, residual, confirmed, created_at",
    "settlement_group_legs": "group_id, fingerprint, side",
    "custom_categories": "name, color, icon, created_at",
    "recurring_series_overrides": "series_id, label, category, is_active, deleted",
    "dashboards": "name, description, is_default, position, updated_at",
    "dashboard_widgets": "dashboard_id, title, type, position",
    "app_settings": "key, value, updated_at",
    "user_profile": "full_name, date_of_birth, updated_at",
    # Bought with real money.
    "merchant_categories": "merchant_key, category, hit_count, updated_at",
    "ai_inferences": "cache_key, kind, provider, model, created_at, hit_count",
    # Downloaded, and irreplaceable if the mail is gone.
    "source_files": "filename, size_bytes, parse_status, transaction_count",
    "staged_files": ("filename, kind, account_label, parse_status, row_count,"
                     " period_start, period_end, added_at"),
    # Reproducible from the files, at the cost of CPU.
    "accounts": "product_name, account_number_masked, account_type, institution",
    "statements": "source_filename, period_start, period_end, recon_status",
    "transactions": "id, txn_date, amount, merchant, category, flow_role",
    "bureau_reports": "bureau, score, pulled_on, holder_name, source_filename",
    "bureau_accounts": ("lender, account_type, number_suffix, status,"
                        " current_balance, match_status"),
    "portfolio_statements": "provider, as_of, declared_value, recon_status, source_filename",
    "holdings": "instrument, isin, folio, units, nav, value, as_of",
    # Derived, and cheap to rebuild.
    "transfer_pairs": "amount, kind, day_gap, confidence",
    "recurring_series": "label, category, direction, median_amount, cadence_days, next_expected",
    "analysis_runs": "id, created_at, status, file_count",
    "jobs": "id, kind, status, phase, message, updated_at",
    "job_items": "job_id, seq, name, status, detail",
}

#: Rows per table. Keep in step with PREVIEW_CAP in DataManager.jsx, which
#: uses it to decide whether to render the count as "200+".
PREVIEW_ROW_LIMIT = 200


@app.get("/api/data/preview/{scope}")
def preview_data(scope: str) -> dict[str, Any]:
    """A sample of what clearing `scope` would delete, table by table.

    Driven by CLEAR_SCOPES rather than a branch per scope, which is how three
    of the seven scopes came to have no preview at all: the buttons were added
    to the UI and the endpoint was never extended, so "Preview data" on the
    staged imports - the statements downloaded from Gmail - returned an empty
    object and rendered as an empty box. Reading the tier straight from the
    same constant the clearing itself uses means a scope cannot be previewed
    incompletely again.

    Tables are returned in PREVIEW_COLUMNS order, which runs from
    irreplaceable to cheaply rebuilt - so the widest scope, the factory reset,
    leads with the rows nothing can bring back. Empty tables are omitted
    rather than listed: a factory reset spans twenty-nine of them, and a wall
    of "Table is empty" buries the four that actually hold something.
    """
    tables = CLEAR_SCOPES.get(scope)
    if tables is None:
        raise HTTPException(
            400, f"Unknown scope '{scope}'. Valid: {', '.join(CLEAR_SCOPES)}")

    preview: dict[str, Any] = {}
    with get_db().connection() as conn:
        for table in PREVIEW_COLUMNS:
            if table not in tables:
                continue
            rows = conn.execute(
                f"SELECT {PREVIEW_COLUMNS[table]} FROM {table}"
                f" LIMIT {PREVIEW_ROW_LIMIT}").fetchall()
            if rows:
                preview[table] = [dict(row) for row in rows]
    return preview


@app.post("/api/data/clear/{scope}")
def clear_data(scope: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Clear exactly one tier of data. See CLEAR_ACTIONS for the tiers."""
    action = next((a for a in CLEAR_ACTIONS if a.scope == scope), None)
    if action is None:
        raise HTTPException(
            400, f"Unknown scope '{scope}'. "
                 f"Valid: {', '.join(a.scope for a in CLEAR_ACTIONS)}")

    # The widest scope is the only one that can destroy something a human
    # authored, so it is the only one that asks the user to type the word.
    if action.confirm_phrase:
        typed = str((payload or {}).get("confirm", "")).strip()
        if typed != action.confirm_phrase:
            raise HTTPException(
                400, f"This action needs confirmation. Send "
                     f'{{"confirm": "{action.confirm_phrase}"}} to proceed.')

    db = get_db()
    snapshot = db.snapshot(f"pre-{scope}")
    removed = db.clear(scope) if action.scope != "derived" else db.clear("derived")

    files_removed = 0
    if action.removes_files:
        for path in storage.stored_files():
            try:
                path.unlink()
                files_removed += 1
            except OSError:
                pass
        if UPLOAD_DIR.is_dir():
            for run_dir in UPLOAD_DIR.iterdir():
                if run_dir.is_dir():
                    shutil.rmtree(run_dir, ignore_errors=True)

    # The in-memory dashboard describes data that may no longer exist.
    runs.clear()

    return {
        "status": "cleared",
        "scope": scope,
        "removed": removed,
        "files_removed": files_removed,
        "snapshot": snapshot.name,
    }


@app.post("/api/data/restore")
def restore_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Roll the database back to a snapshot taken before a clearing action."""
    name = str((payload or {}).get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Which snapshot? Send {\"name\": \"...\"}.")
    db = get_db()
    try:
        db.restore(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    runs.clear()
    return {"status": "restored", "name": name}

@app.delete("/api/data/snapshots/{name}")
def delete_snapshot(name: str) -> dict[str, Any]:
    """Delete one of the signed-in user's snapshots.

    The name arrives from an HTTP request, so `Database.delete_snapshot`
    resolves it against that user's own snapshot directory and refuses
    anything that lands outside - which now also means one user cannot delete
    another's backups by guessing at a path.
    """
    try:
        get_db().delete_snapshot(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"status": "deleted"}



@app.post("/api/reset")
def reset() -> dict[str, str]:
    """Deprecated: clears the parsed ledger only.

    Kept so an older frontend build does not 404, but it no longer deletes
    statement files, AI inference or user decisions - the three things it had
    no business deleting. New callers should use /api/data/clear/{scope}.
    """
    db = get_db()
    db.snapshot("pre-reset")
    db.clear("parsed_data")
    runs.clear()
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


@app.get("/api/workflow")
def workflow() -> dict[str, Any]:
    """Where this workspace stands, derived fresh from what is stored.

    Deliberately computed on every request rather than tracked as a "current
    step" pointer. A stored pointer is a second source of truth about state
    the database already knows, and the two drift the moment anything happens
    out of band - a file retried from the coverage grid, a server restart
    mid-import, a decision recorded from the transactions table. Every stage
    stays reachable at all times; this only reports what is done and what is
    blocking the next thing.
    """
    db = get_db()
    profile = repo.get_profile(db)
    files = repo.list_source_files(db)
    accounts = repo.get_accounts(db)

    by_status: dict[str, int] = {}
    for record in files:
        by_status[record.parse_status] = by_status.get(record.parse_status, 0) + 1

    txn_count = repo.count_transactions(db)
    with db.connection() as conn:
        review_count = conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE needs_review = 1"
        ).fetchone()["c"]
        override_count = conn.execute(
            "SELECT COUNT(*) c FROM user_overrides").fetchone()["c"]

    gmail_connected = bool(gmail_routes._client()
                           and gmail_routes._client().is_authorized())

    # Coverage is the honest measure of "have I collected everything?" - it
    # knows which months each account is missing, which a file count cannot.
    missing_months = 0
    try:
        coverage = files_routes.get_coverage()["accounts"]
        missing_months = sum(
            1 for row in coverage for cell in row.get("months", [])
            if cell.get("status") == "missing"
        )
    except Exception:  # a coverage failure must not break the whole view
        coverage = []

    stages = [
        {
            "id": "profile",
            "label": "Profile",
            "complete": profile.has_password_material(),
            "detail": ("Ready" if profile.has_password_material() else
                       "Add your name and date of birth so locked statements "
                       "can be opened."),
        },
        {
            "id": "sources",
            "label": "Sources",
            "complete": gmail_connected or bool(files),
            "detail": (f"{len(files)} file(s) known"
                       + (", Gmail connected" if gmail_connected else "")),
        },
        {
            "id": "collect",
            "label": "Collect",
            "complete": bool(files) and missing_months == 0,
            "detail": (f"{missing_months} month(s) still missing"
                       if missing_months else "Every known month is present"),
        },
        {
            "id": "parse",
            "label": "Parse",
            "complete": bool(txn_count) and not by_status.get("failed")
            and not by_status.get("needs_password"),
            "detail": ", ".join(
                f"{count} {status}" for status, count in sorted(by_status.items())
            ) or "Nothing parsed yet",
        },
        {
            "id": "review",
            "label": "Review",
            "complete": review_count == 0,
            "detail": (f"{review_count} item(s) awaiting your decision"
                       if review_count else "Nothing needs review"),
        },
        {
            "id": "analyze",
            "label": "Analyze",
            "complete": txn_count > 0,
            "detail": (f"{txn_count} transaction(s) across {len(accounts)} account(s)"
                       if txn_count else "No transactions yet"),
        },
    ]

    return {
        "stages": stages,
        "counts": {
            "files": len(files),
            "files_by_status": by_status,
            "accounts": len(accounts),
            "transactions": txn_count,
            "needs_review": review_count,
            "decisions": override_count,
            "missing_months": missing_months,
        },
    }


def _llm_status() -> tuple[str, bool]:
    """Which provider is selected, and whether it could actually be called."""
    try:
        from .config import config

        provider = config.LLM_PROVIDER
        if provider == "gemini":
            return provider, bool(config.GEMINI_API_KEY)
        if provider == "azure":
            return provider, bool(config.AZURE_OPENAI_ENDPOINT
                                  and config.AZURE_OPENAI_API_KEY)
        return provider, False
    except Exception:  # pragma: no cover - health must never 500
        return "unknown", False


@app.get("/api/health")
def health() -> dict[str, Any]:
    db = get_db()
    return {
        "status": "ok",
        "database": "postgresql",
        # Zero when nobody is signed in: this endpoint is public so a load
        # balancer can probe it, and with no tenant bound the row-level
        # security policy matches nothing - which is the correct answer to
        # "how many transactions can this caller see".
        "transactions_stored": repo.count_transactions(db),
        # Reports the provider actually in use. This checked
        # ANTHROPIC_API_KEY, which nothing reads any more, so health said
        # "no model configured" however carefully the user had set one up.
        "llm_provider": _llm_status()[0],
        "llm_configured": _llm_status()[1],
        "supported_formats": sorted(SUPPORTED_EXTENSIONS),
    }


class SplitPartReq(BaseModel):
    amount: Decimal
    category: str | None = None
    flow_role: str | None = None
    note: str | None = None

class SplitReq(BaseModel):
    splits: list[SplitPartReq]

@app.post("/api/transactions/{txn_id}/split")
def split_transaction(txn_id: str, payload: SplitReq) -> dict[str, Any]:
    """Divide one transaction into parts, each free to carry its own
    category, flow role and note.

    Persists the split and nothing else - `pipeline.overrides.apply_splits`
    is what turns the stored rows into actual accounting effect on the next
    enrichment pass, the same way a category correction is stored here and
    applied by the pipeline rather than computed inline.
    """
    db = get_db()
    matches = [t for t in repo.get_transactions(db) if t.id == txn_id]
    if not matches:
        raise HTTPException(404, f"No transaction with id {txn_id}")
    txn = matches[0]
    if not payload.splits:
        raise HTTPException(400, "At least one split part is required.")

    # Rounded before comparing: a JSON number like 450.30 can arrive as a
    # float with binary rounding noise, and an exact != would reject a split
    # a person looking at it would call correct.
    cent = Decimal("0.01")
    total = sum((p.amount for p in payload.splits), Decimal("0")).quantize(cent)
    if total != txn.amount.quantize(cent):
        raise HTTPException(
            400, f"Splits sum to {total}, expected {txn.amount}")

    splits = [{"amount": p.amount, "category": p.category,
              "flow_role": p.flow_role, "note": p.note}
             for p in payload.splits]
    from .pipeline.fingerprint import loose_key
    ids = repo.save_splits(db, txn.fingerprint, txn.amount, splits,
                           origin_key=loose_key(txn))

    # Same reasoning as every other transaction edit: the cached dashboard
    # payload was computed before this split existed.
    runs.clear()

    return {"status": "ok", "split_ids": ids}

class ClaimReq(BaseModel):
    amount: Decimal
    direction: str
    counterparty: str
    note: str = ""

@app.post("/api/transactions/{txn_id}/claim")
def create_claim(txn_id: str, payload: ClaimReq) -> dict[str, Any]:
    """Mark a transaction as not really the user's - the start of a claim.

    Every claim opens on ACCRUAL basis, the only one exposed today: the
    schema's own comment on the column is explicit that accrual "leaves the
    month the purchase happened in" - immediately, not when the money
    eventually comes back. Creating the claim record without touching the
    transaction it names would leave the purchase counting as the user's own
    spending forever, which defeats the entire point of marking it in the
    first place - a claim that exists only in the Owed tab and nowhere in
    the numbers is not a fix, it just moves the bug somewhere less visible.
    """
    db = get_db()
    matches = [t for t in repo.get_transactions(db) if t.id == txn_id]
    if not matches:
        raise HTTPException(404, f"No transaction with id {txn_id}")
    txn = matches[0]

    # record_decision stamps txn.fingerprint if it was ever empty (a row
    # saved before fingerprinting existed, or - as this session's own tests
    # found - one saved by a path that skips the pipeline). That has to
    # happen BEFORE origin_fingerprint is read below: read in the other
    # order, the claim remembers the stale empty value while the row's own
    # persisted fingerprint becomes the freshly-stamped one, and settling the
    # claim later can never find its way back to this transaction.
    from .pipeline.overrides import record_decision
    accounts = {a.id: a for a in repo.get_accounts(db) if a.id}
    note = f"Not my expense - claimed against {payload.counterparty}"
    if payload.note:
        note += f" ({payload.note})"
    record_decision(db, txn, accounts, excluded=True, note=note)

    claim_id = repo.save_claim(
        db,
        direction=payload.direction,
        counterparty=payload.counterparty,
        origin_fingerprint=txn.fingerprint,
        amount=payload.amount,
        opened_on=txn.txn_date.isoformat()
    )
    repo.update_transaction_categories(db, [txn])

    # Same reasoning as every other transaction edit: the cached dashboard
    # payload was computed before this exclusion existed.
    runs.clear()

    return {"status": "ok", "claim_id": claim_id}

@app.get("/api/claims")
def list_claims() -> list[dict]:
    return repo.get_claims(get_db())

class SettleClaimReq(BaseModel):
    amount: Decimal
    method: str
    txn_fingerprint: str | None = None
    note: str = ""
    settled_on: date

@app.post("/api/claims/{claim_id}/settle")
def settle_claim(claim_id: str, payload: SettleClaimReq) -> dict[str, Any]:
    """Record how a claim was resolved.

    Every method but `write_off` means real money moved, so the origin
    purchase rightly stays excluded forever - somebody else already paid for
    it. `write_off` means the opposite: the money is confirmed never coming
    back, which makes it the user's own expense after all, so the exclusion
    that `create_claim` applied has to be reversed here the same explicit way
    it was applied - `repo.settle_claim` alone only updates the `claims` row
    and the durable override; without this, whichever run computed the
    dashboard the user is currently looking at would keep showing the
    purchase as excluded until the next full re-enrichment noticed.
    """
    db = get_db()
    claim = repo.get_claim(db, claim_id)
    if claim is None:
        raise HTTPException(404, f"No claim with id {claim_id}")
    repo.settle_claim(
        db,
        claim_id=claim_id,
        method=payload.method,
        amount=payload.amount,
        settled_on=payload.settled_on.isoformat(),
        note=payload.note,
        txn_fingerprint=payload.txn_fingerprint or "",
    )

    if payload.method == "write_off" and claim.get("origin_fingerprint"):
        matches = [t for t in repo.get_transactions(db)
                  if t.fingerprint == claim["origin_fingerprint"]]
        if matches:
            from .pipeline.overrides import record_decision
            accounts = {a.id: a for a in repo.get_accounts(db) if a.id}
            record_decision(db, matches[0], accounts, excluded=False,
                            note=f"Written off {payload.settled_on.isoformat()} - "
                                 f"never recovered, counted as a real expense")
            repo.update_transaction_categories(db, matches)

    runs.clear()
    return {"status": "ok"}


class RecurringUpdateReq(BaseModel):
    is_active: bool | None = None
    label: str | None = None
    category: str | None = None


@app.get("/api/recurring")
def get_recurring_series() -> list[dict[str, Any]]:
    return repo.get_recurring_series(get_db())

@app.patch("/api/recurring/{series_id}")
def update_recurring(series_id: str, payload: RecurringUpdateReq) -> dict[str, Any]:
    db = get_db()
    update_args = payload.model_dump(exclude_unset=True)
    if update_args:
        if "category" in update_args and update_args["category"] is not None:
            category = update_args["category"].strip().lower()
            valid_categories = set(Category.all_builtins())
            with db.connection() as conn:
                for r in conn.execute("SELECT name FROM custom_categories").fetchall():
                    valid_categories.add(r["name"])
            if category and category not in valid_categories:
                raise HTTPException(400, f"'{update_args['category']}' is not a valid category.")
            update_args["category"] = category

        repo.update_recurring_series_override(db, series_id, update_args)
        # /api/recurring reads live, but the cached dashboard payload embeds
        # its own recurring list computed before this edit.
        runs.clear()
    return {"status": "ok"}

@app.delete("/api/recurring/{series_id}")
def delete_recurring(series_id: str) -> dict[str, Any]:
    repo.update_recurring_series_override(get_db(), series_id, {"deleted": 1})
    runs.clear()
    return {"status": "ok"}

@app.exception_handler(Exception)
async def unhandled(request, exc):  # pragma: no cover
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )
