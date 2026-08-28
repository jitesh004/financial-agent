"""Graph nodes.

Every node here is thin. The real work lives in ingestion/, normalize/,
reconcile/, categorize/ and analytics/ as plain functions that know nothing
about LangGraph, and each node just wires state into a call and results back
out. That separation is deliberate: the business logic stays unit-testable
without a graph, and the graph stays readable as a flowchart.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from pathlib import Path

from ..db.database import get_db
from ..ingestion import router
from ..models.schemas import (Account, AccountType, ReconciliationStatus,
                              Statement)
from ..normalize.normalizer import normalize
from ..normalize.parsers import extract_merchant
from ..reconcile.balance_check import reconcile
from ..reconcile.transfers import find_duplicate_transactions
from .state import AnalysisState, FileTask, ParsedFile

log = logging.getLogger(__name__)

#: How many times one file may be re-extracted before we give up and report it.
MAX_ATTEMPTS = 3


# --------------------------------------------------------------------------
# 1. Plan
# --------------------------------------------------------------------------

def plan_ingestion(state: AnalysisState) -> dict:
    """Validate inputs and set up the run. Runs once, before the fan-out."""
    tasks = state.get("file_tasks") or []
    errors: list[str] = []
    warnings: list[str] = []
    valid: list[FileTask] = []
    seen_hashes: dict[str, str] = {}  # content hash -> the filename that claimed it

    for task in tasks:
        path = Path(task["path"])
        if not path.exists():
            errors.append(f"{task.get('filename', path.name)}: file not found")
            continue
        if path.suffix.lower() not in router.SUPPORTED_EXTENSIONS:
            errors.append(
                f"{path.name}: unsupported file type '{path.suffix}'. "
                f"Supported: {', '.join(sorted(router.SUPPORTED_EXTENSIONS))}"
            )
            continue

        # Content-hash dedup: the same file re-added, even under a different
        # name, is the same statement and must not be parsed twice. This is the
        # name-independent line of defence; the upload endpoint catches the
        # same-name case earlier, and row-level dedup handles statements that
        # merely OVERLAP rather than being byte-identical.
        digest = router.file_hash(path)
        if digest in seen_hashes:
            warnings.append(
                f"{task.get('filename', path.name)}: identical in content to "
                f"'{seen_hashes[digest]}', skipped to avoid double counting."
            )
            continue
        seen_hashes[digest] = task.get("filename", path.name)
        # Attach the run-wide password candidates to each file, so the per-file
        # branch (which only sees its FileTask) can try them without reaching
        # back into the shared state or the profile table.
        valid.append({
            **task,
            "attempt": task.get("attempt", 0),
            "password_candidates": task.get("password_candidates")
            or state.get("password_candidates") or [],
            "file_hash": digest,
        })

    return {
        "run_id": state.get("run_id") or str(uuid.uuid4()),
        "file_tasks": valid,
        "errors": errors,
        "warnings": warnings,
        "status": "ingesting",
    }


# --------------------------------------------------------------------------
# 2. Per-file ingestion (runs in parallel, one branch per file)
# --------------------------------------------------------------------------

def ingest_file(state: FileTask) -> dict:
    """Extract, normalize and reconcile ONE file.

    Receives a FileTask rather than the whole AnalysisState, because this node
    is the target of a Send() fan-out. Returning into `statements` (which has an
    operator.add reducer) is what lets forty of these run concurrently and merge
    cleanly.
    """
    path = Path(state["path"])
    filename = state.get("filename") or path.name
    attempt = state.get("attempt", 0)
    candidates = state.get("password_candidates") or []

    result: ParsedFile = {"filename": filename, "attempt": attempt}
    result["filepath"] = str(path)
    result["file_hash"] = state.get("file_hash") or ""
    try:
        result["size_bytes"] = path.stat().st_size
    except OSError:
        pass
    # Resolved separately from the main extraction below, which never surfaces
    # which candidate it used - this is the only place that fact is captured,
    # on every outcome (including a failed or locked file), so the file
    # registry can record it and a later load can skip straight to it.
    from ..ingestion.passwords import resolve_password_status
    password, password_status = resolve_password_status(path, candidates)
    result["password"] = password
    result["password_status"] = password_status

    extraction = router.extract(
        path,
        password=state.get("password"),
        password_candidates=candidates,
    )

    if extraction.needs_password:
        result["status"] = "needs_password"
        result["message"] = f"{filename} is password protected."
        return {"statements": [result],
                "warnings": [f"{filename}: password required"]}

    if not extraction.tables:
        result["status"] = "failed"
        result["message"] = (
            f"{filename}: no transaction table could be extracted. "
            + " ".join(extraction.warnings)
        )
        return {"statements": [result], "errors": [result["message"]]}

    hint = state.get("account_type_hint")
    statement, account = normalize(
        extraction, filename,
        account_type_hint=AccountType(hint) if hint else None,
    )

    if not statement.transactions:
        result["status"] = "failed"
        result["message"] = f"{filename}: table found but no rows parsed."
        return {"statements": [result], "errors": [result["message"]]}

    # Carry the content hash onto the statement. The DB's unique index on
    # file_hash then makes an identical re-upload in a later run replace the old
    # statement (and, by cascade, its transactions) instead of duplicating them.
    statement.file_hash = state.get("file_hash") or ""

    recon = reconcile(statement, account.account_type)
    statement.reconciliation = recon

    result["statement"] = statement
    result["account"] = account
    result["reconciliation"] = recon
    result["transaction_count"] = len(statement.transactions)

    warnings = [f"{filename}: {w}" for w in statement.parse_warnings]

    if recon.status == ReconciliationStatus.FAILED:
        # A failed gate is not fatal on its own - it queues a retry with a
        # different extraction strategy. Only an exhausted retry budget makes
        # the file unusable, and even then we keep the rows and flag them.
        result["status"] = "unreconciled"
        result["message"] = recon.message
        out: dict = {
            "statements": [result],
            "warnings": warnings + [f"{filename}: {recon.message}"],
        }
        # Only write the key when there IS something to retry: an empty list is
        # the reset signal, and writing it here would cancel another file's
        # pending retry.
        if attempt + 1 < MAX_ATTEMPTS:
            out["retry_queue"] = [{**state, "attempt": attempt + 1}]
        return out

    result["status"] = "ok"
    result["message"] = recon.message
    return {"statements": [result], "warnings": warnings}


def fan_out_files(state: AnalysisState):
    """Conditional edge: one parallel branch per file.

    Returning a list of Send objects is LangGraph's map step. Each Send carries
    its own payload, so `ingest_file` never has to know about the other files.
    """
    from langgraph.types import Send

    tasks = state.get("file_tasks") or []
    if not tasks:
        return "merge_ledger"
    return [Send("ingest_file", task) for task in tasks]


def route_after_ingestion(state: AnalysisState) -> str:
    """Conditional edge: retry broken parses, or move on.

    This is the cycle that makes the reconciliation gate useful rather than
    merely informative - a failed check actually sends the file back through
    extraction with a different strategy.
    """
    if state.get("retry_queue"):
        return "retry_extraction"
    return "merge_ledger"


def retry_extraction(state: AnalysisState) -> dict:
    """Re-run the files whose balances did not tie out.

    Currently this re-attempts with the same extractor ladder while recording
    the attempt count; the hook is here so a vision-model fallback can be added
    as attempt 2 without touching the graph shape.
    """
    queue = state.get("retry_queue") or []
    notes = [
        f"{t.get('filename')}: reconciliation failed, re-extracting "
        f"(attempt {t.get('attempt', 0) + 1} of {MAX_ATTEMPTS})"
        for t in queue
    ]
    # Clearing the queue is what stops the cycle looping forever.
    return {"retry_queue": [], "file_tasks": queue, "warnings": notes}


# --------------------------------------------------------------------------
# 3. Merge into one ledger
# --------------------------------------------------------------------------

def merge_ledger(state: AnalysisState) -> dict:
    """Collapse per-file results into one account map and one transaction list.

    Account identity is (institution, type, masked number), so twelve monthly
    statements for one card collapse into a single account with one continuous
    ledger - which is what makes cross-account transfer detection work.
    """
    parsed = [s for s in (state.get("statements") or [])
              if s.get("status") in {"ok", "unreconciled"} and s.get("statement")]

    accounts: dict[str, Account] = {}
    identity_to_id: dict[tuple, str] = {}
    transactions = []

    for entry in parsed:
        account: Account = entry["account"]
        statement: Statement = entry["statement"]

        identity = _account_identity(account)
        account_id = identity_to_id.get(identity)
        if account_id is None:
            account_id = str(uuid.uuid4())
            account.id = account_id
            identity_to_id[identity] = account_id
            accounts[account_id] = account
        else:
            _merge_account_facts(accounts[account_id], account)

        statement.id = statement.id or str(uuid.uuid4())
        statement.account_id = account_id

        for txn in statement.transactions:
            txn.id = txn.id or str(uuid.uuid4())
            txn.account_id = account_id
            txn.statement_id = statement.id
            txn.merchant = extract_merchant(txn.raw_description)
            transactions.append(txn)

    duplicates = find_duplicate_transactions(transactions)
    duplicate_ids = {id(d) for d in duplicates}
    if duplicates:
        transactions = [t for t in transactions if id(t) not in duplicate_ids]

    transactions.sort(key=lambda t: (t.txn_date, t.account_id or ""))

    warnings = []
    if duplicates:
        warnings.append(
            f"Removed {len(duplicates)} duplicate transaction(s) caused by "
            f"overlapping statement periods."
        )
    if not transactions:
        warnings.append("No usable transactions were produced by any file.")

    return {
        "accounts": accounts,
        "transactions": transactions,
        "duplicate_count": len(duplicates),
        "warnings": warnings,
        "status": "enriching",
    }


def _account_identity(account: Account) -> tuple:
    """Key that decides whether two statements describe the same account.

    The masked account number is the strongest identifier available, so when we
    have one it alone (plus the account type) settles identity. Institution is
    deliberately NOT part of the key in that case: the same account exported
    twice can easily disagree about it, because one file's letterhead names the
    bank and another's doesn't. Treating that disagreement as two separate
    accounts silently doubles income, spending and investments - the single
    worst failure this app can have, because every figure still looks plausible.

    Without a masked number, the card's own product name is the next best
    thing: HSBC masks its card number so completely that no digit survives
    text extraction at all, but "TravelOne" versus another HSBC card still
    prints in the letterhead - and without this, two entirely different HSBC
    cards would silently merge into one account, doubling every figure on
    both. Only when neither signal exists do we fall back to institution +
    type alone, which is weaker but is all there is.
    """
    if account.account_number_masked:
        return (account.account_number_masked, account.account_type)
    if account.product_name:
        return (account.institution, account.account_type, account.product_name)
    return (account.institution, account.account_type, "")


def _merge_account_facts(target: Account, incoming: Account) -> None:
    """Keep the most informative version of each account fact.

    Later statements carry fresher loan balances; earlier ones sometimes carry
    details a later one omits. Preferring non-null on each field independently
    beats picking one statement wholesale.
    """
    for attr in ("holder_name", "product_name", "interest_rate", "emi_amount", "credit_limit",
                 "principal_outstanding", "current_balance",
                 "tenure_months_remaining"):
        if getattr(target, attr) is None and getattr(incoming, attr) is not None:
            setattr(target, attr, getattr(incoming, attr))
    if target.account_type == AccountType.UNKNOWN:
        target.account_type = incoming.account_type
    # A named institution always beats "Unknown", whichever file it came from.
    if target.institution == "Unknown" and incoming.institution != "Unknown":
        target.institution = incoming.institution


# --------------------------------------------------------------------------
# 4. Enrichment
# --------------------------------------------------------------------------

def enrich_node(state: AnalysisState) -> dict:
    """Everything between a merged ledger and a finished analysis.

    This was seven separate graph nodes - transfers, rules, model, fallback,
    recurring, analytics, loans, forecast - in a straight line with exactly
    one branch, which `enrich_ledger` now makes internally. Collapsing them
    is what lets the Gmail and file-retry routes run *the same* sequence
    instead of each maintaining its own copy; the three had already drifted
    apart, and the drift was invisible because every copy looked plausible.

    The fan-out and retry machinery above `merge_ledger` is untouched - that
    is where the graph earns its keep.
    """
    from ..pipeline.enrich import enrich_ledger

    result = enrich_ledger(
        get_db(),
        state.get("transactions") or [],
        state.get("accounts") or {},
        use_llm=bool(state.get("use_llm")),
        holder_names=state.get("holder_names") or [],
        horizon_months=state.get("horizon_months", 6),
    )

    warnings = list(result.warnings)
    for projection in result.loan_projections:
        account = (state.get("accounts") or {}).get(projection.account_id)
        label = account.display_name() if account else "Loan"
        warnings.extend(f"{label}: {w}" for w in projection.warnings)

    return {
        "transactions": result.transactions,
        "transfer_report": result.transfer_report,
        "recurring": result.recurring,
        "analysis": result.analysis,
        "loan_projections": result.loan_projections,
        "forecast": result.forecast,
        "rules_settled": result.rules_settled,
        "llm_settled": result.llm_settled,
        "warnings": warnings,
        "status": "reporting",
    }







# --------------------------------------------------------------------------
# 5. Analysis
# --------------------------------------------------------------------------



