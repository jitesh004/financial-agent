"""Assemble the analysis graph.

    START
      |
      v
    plan_ingestion
      |
      +--Send(per file)-->  ingest_file   (parallel: extract -> normalize -> reconcile)
      |                        |
      |                        v
      |                  route_after_ingestion
      |                    |            |
      |          retry_extraction    merge_ledger        <-- the reconciliation cycle
      |                    |            |
      |                    +-- Send ----+
      v
    merge_ledger -> enrich -> synthesize -> END

`enrich` is one node covering transfers -> rules -> merchant cache -> model ->
fallback -> user overrides -> recurring -> analytics -> loans -> forecast. That
used to be nine nodes in a straight line with a single branch, and the same
sequence was also written out by hand in the Gmail route and the file-retry
route. Three copies had already drifted: the Gmail path took a `use_llm` flag
and never read it, and neither of the other two consulted the learned merchant
cache at all, so a category the user had corrected was re-guessed from scratch
whenever a statement arrived that way. It now lives in `pipeline.enrich` and
all three call it.

Why this is still a graph rather than a script:

  - `ingest_file` fans out with Send, so forty statements parse concurrently.
  - `route_after_ingestion` is a real cycle: a statement whose balances do not
    tie out goes back through extraction with a different strategy.
  - A checkpointer makes the whole run resumable: parsing ten years of
    statements is slow, and a crash at the analytics step must not throw away
    the parsing work.

The fan-out and the retry cycle are where the graph earns its keep; the
straight line after the merge never did.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from ..llm.narrative import build_brief, generate_narrative
from . import nodes
from .state import AnalysisState

log = logging.getLogger(__name__)


def synthesize(state: AnalysisState) -> dict:
    """Build the fact brief, then have the model write it up."""
    analysis = state.get("analysis")
    if analysis is None:
        return {"narrative": {}, "report": {}, "status": "failed",
                "errors": ["Analysis produced no result to summarize."]}

    # Deduplicated the same way merge_ledger builds the ledger itself - a
    # file retried after a failed reconciliation left every attempt in
    # "statements" (an additive channel), so without this the file count and
    # the reconciliation/parse-failure notes below counted and named a
    # retried file two or three times over.
    statements = nodes.latest_attempt_per_file(state.get("statements") or [])
    unreconciled = [s for s in statements if s.get("status") == "unreconciled"]
    failed = [s for s in statements if s.get("status") == "failed"]

    data_quality: dict[str, Any] = {
        "files_processed": len(statements),
        "files_reconciled": sum(1 for s in statements if s.get("status") == "ok"),
        "files_unreconciled": len(unreconciled),
        "files_failed": len(failed),
        "duplicates_removed": state.get("duplicate_count", 0),
        "uncategorized_count": analysis.uncategorized_count,
        "rules_settled": state.get("rules_settled", 0),
        "llm_settled": state.get("llm_settled", 0),
        "notes": list(analysis.notes),
    }
    if unreconciled:
        data_quality["notes"].append(
            f"{len(unreconciled)} file(s) did not reconcile against their stated "
            f"balances. Figures that depend on them may be incomplete: "
            + "; ".join(s.get("filename", "?") for s in unreconciled)
        )
    if failed:
        data_quality["notes"].append(
            f"{len(failed)} file(s) could not be parsed at all: "
            + "; ".join(s.get("filename", "?") for s in failed)
        )

    transfer_report = state.get("transfer_report")
    if transfer_report is not None and transfer_report.double_count_avoided:
        data_quality["double_count_avoided"] = float(
            transfer_report.double_count_avoided
        )

    brief = build_brief(
        analysis=analysis,
        loan_projections=state.get("loan_projections") or [],
        forecast=state.get("forecast"),
        recurring=state.get("recurring") or [],
        accounts=state.get("accounts") or {},
        data_quality=data_quality,
    )

    narrative = generate_narrative(brief) if state.get("use_llm", True) else {}
    if not narrative:
        from ..llm.narrative import _fallback
        narrative = _fallback(brief, "Model narration was disabled for this run.")

    return {
        "narrative": narrative,
        "report": {"brief": brief, "narrative": narrative},
        "status": "complete",
    }


def build_graph(checkpointer: Any = None):
    """Wire and compile the analysis graph."""
    graph = StateGraph(AnalysisState)

    graph.add_node("plan_ingestion", nodes.plan_ingestion)
    graph.add_node("ingest_file", nodes.ingest_file)
    graph.add_node("retry_extraction", nodes.retry_extraction)
    graph.add_node("merge_ledger", nodes.merge_ledger)
    # One node covering transfers -> rules -> model -> fallback -> user
    # overrides -> recurring -> analytics -> loans -> forecast. It was nine
    # nodes in a straight line with a single branch; the shared pipeline makes
    # that branch internally, and collapsing it is what lets the Gmail and
    # file-retry routes execute the identical sequence rather than each
    # carrying its own copy of it.
    graph.add_node("enrich", nodes.enrich_node)
    graph.add_node("synthesize", synthesize)

    graph.add_edge(START, "plan_ingestion")

    # Map: one parallel branch per file.
    graph.add_conditional_edges(
        "plan_ingestion", nodes.fan_out_files, ["ingest_file", "merge_ledger"]
    )

    # Reduce, with a cycle back through extraction for files that didn't tie out.
    graph.add_conditional_edges(
        "ingest_file", nodes.route_after_ingestion,
        ["retry_extraction", "merge_ledger"],
    )
    graph.add_conditional_edges(
        "retry_extraction", nodes.fan_out_files, ["ingest_file", "merge_ledger"]
    )

    graph.add_edge("merge_ledger", "enrich")
    graph.add_edge("enrich", "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile(checkpointer=checkpointer)


def build_checkpointer(path: str | None = None):
    """SQLite checkpointer so a long ingestion run can resume after a crash.

    Returns a context manager; the caller owns the connection lifetime.
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    from ..db.database import DEFAULT_DB_PATH

    target = path or str(DEFAULT_DB_PATH.parent / "graph_checkpoints.sqlite")
    return SqliteSaver.from_conn_string(target)


_compiled = None


def get_graph():
    """Process-wide compiled graph (no checkpointer - used for one-shot runs)."""
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled
