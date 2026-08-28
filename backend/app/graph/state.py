"""Shared state for the analysis graph.

LangGraph merges each node's returned dict into this TypedDict. Fields written
by parallel branches MUST carry a reducer, otherwise concurrent writes to the
same key raise InvalidUpdateError - the graph cannot know whether you meant
"replace" or "combine".

The fan-out over statement files writes to `statements`, `errors` and
`warnings` from many branches at once, so those three accumulate. Everything
else is written by exactly one node and can be plain.
"""

from __future__ import annotations

import operator
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, TypedDict

from ..models.schemas import Account, Statement, Transaction


def merge_dicts(left: dict, right: dict) -> dict:
    """Reducer for dict-valued state written from parallel branches."""
    out = dict(left or {})
    out.update(right or {})
    return out


def merge_retry_queue(left: list, right: list) -> list:
    """Accumulate retries from parallel branches, but allow an explicit reset.

    `operator.add` cannot express "clear this list": returning [] appends
    nothing and leaves the old contents in place. That turns the reconciliation
    retry cycle into an infinite loop - the queue never empties, so the graph
    routes back to retry_extraction forever until it hits the recursion limit,
    re-extracting every PDF on each pass.

    So an explicit empty list means reset, and branches with nothing to add
    omit the key entirely rather than writing [].
    """
    if right == []:
        return []
    return (left or []) + (right or [])


class FileTask(TypedDict, total=False):
    """One unit of the ingestion fan-out."""

    path: str
    filename: str
    password: str | None
    #: Derived candidate passwords for a protected PDF (see ingestion.passwords).
    password_candidates: list[str]
    #: Content hash, stamped at plan time and carried onto the Statement so the
    #: DB's unique-hash index can self-heal an identical re-upload.
    file_hash: str
    account_type_hint: str | None
    #: Which extraction strategy to use. Bumped by the retry loop.
    attempt: int


class ParsedFile(TypedDict, total=False):
    """Result of ingesting one file, returned by the per-file branch."""

    filename: str
    filepath: str
    statement: Any          # Statement
    account: Any            # Account
    reconciliation: Any     # ReconciliationResult
    transaction_count: int
    status: str             # "ok" | "failed" | "needs_password" | "unreconciled"
    attempt: int
    message: str
    file_hash: str
    size_bytes: int
    #: The password that actually opened this file, if it was protected. Set
    #: even on a FAILED/unreconciled outcome, so a locked-then-later-fixed file
    #: does not have to re-discover its own password from scratch.
    password: str | None
    password_status: str    # "open" | "not_encrypted" | "locked" | "unknown"


class AnalysisState(TypedDict, total=False):
    """The graph's working memory.

    Roughly ordered by when it gets populated: inputs, then ingestion output,
    then the merged ledger, then analysis, then narrative.
    """

    # ---- Inputs ---------------------------------------------------------
    run_id: str
    file_tasks: list[FileTask]
    passwords: dict[str, str]
    #: Candidate passwords derived from the user's profile, tried against any
    #: protected PDF. Passed in by the caller so the graph never touches the
    #: profile table directly (keeping PII out of the graph's serialized state).
    password_candidates: list[str]
    #: The account holder's own name(s). Paying yourself is a transfer between
    #: your own accounts, not spending, and only the name can tell them apart.
    #: Passed in by the caller for the same reason as password_candidates.
    holder_names: list[str]
    horizon_months: int
    use_llm: bool

    # ---- Ingestion fan-out (parallel writes -> reducers required) -------
    statements: Annotated[list[ParsedFile], operator.add]
    errors: Annotated[list[str], operator.add]
    warnings: Annotated[list[str], operator.add]
    #: Files that failed reconciliation and are queued for another strategy.
    retry_queue: Annotated[list[FileTask], merge_retry_queue]

    # ---- Merged ledger --------------------------------------------------
    accounts: dict[str, Account]
    transactions: list[Transaction]
    duplicate_count: int

    # ---- Enrichment -----------------------------------------------------
    transfer_report: Any
    rules_settled: int
    llm_settled: int
    recurring: list[Any]

    # ---- Analysis -------------------------------------------------------
    analysis: Any
    loan_projections: list[Any]
    forecast: Any

    # ---- Output ---------------------------------------------------------
    narrative: dict[str, Any]
    report: dict[str, Any]

    # ---- Control --------------------------------------------------------
    #: Set when a node needs the user before the graph can continue.
    awaiting_input: str | None
    status: str
