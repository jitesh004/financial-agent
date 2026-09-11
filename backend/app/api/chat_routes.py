"""Multi-turn conversation over the ledger.

A thin layer over `agents.runner`. Each turn is a full agent run - tools
called, figures verified against what they returned - so a conversational
answer is held to exactly the same standard as a specialist report. What
the conversation adds is the thread: earlier questions and headlines, so
"what about last month" has a referent.

Deliberately read-only. Every tool the copilot can reach is a query; none
of them writes. That is the property that makes it safe to let an open
question drive the tool choice, and it should be checked rather than
assumed - see `_refuse_write_tools`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..agents import catalogue, runner
from ..db import repository as repo
from ..db.database import get_db
from ..jobs import JobProgress, jobs
from ..llm.client import get_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

#: The agent a conversation runs on. A generalist with the whole read-only
#: toolbelt - see `catalogue`.
COPILOT = "copilot"

#: How many turns of a conversation are handed to the next one.
#: `runner.HISTORY_TURNS` decides how many are actually used; this is the
#: read, and it is small for the same reason.
HISTORY_FETCH = 12


class AskRequest(BaseModel):
    question: str
    #: Start a new conversation when absent.
    conversation_id: str | None = None


class RenameRequest(BaseModel):
    title: str


def _refuse_write_tools(agent) -> None:
    """Fail loudly if the copilot is ever handed a tool that mutates.

    The read-only property is what makes it safe to let an arbitrary
    question pick the tools - there is no question that can be phrased to
    make a query destructive. That holds today because no tool in the
    belt writes, which is a fact about the code rather than a guarantee,
    and it will stop holding the first time somebody adds one.

    Checked here rather than trusted, so the failure is a clear error at
    the seam instead of a conversation that quietly changed the ledger.
    """
    from ..agents import toolbelt

    writes = getattr(toolbelt, "WRITE_TOOLS", frozenset())
    offending = sorted(set(agent.tools) & set(writes))
    if offending:
        raise HTTPException(
            500, f"The copilot is read-only, but {', '.join(offending)} "
                 f"can change data. Remove it from the copilot's tools, or "
                 f"put it behind an explicit confirmation step.")


@router.get("/conversations")
def list_conversations(include_archived: bool = False) -> dict[str, Any]:
    return {"conversations": repo.list_conversations(
        get_db(), include_archived=include_archived)}


@router.get("/conversations/{conversation_id}")
def read_conversation(conversation_id: str) -> dict[str, Any]:
    db = get_db()
    found = repo.get_conversation(db, conversation_id)
    if not found:
        raise HTTPException(404, "There is no conversation with that id.")
    return {**found, "turns": repo.get_turns(db, conversation_id)}


@router.patch("/conversations/{conversation_id}")
def rename(conversation_id: str, payload: RenameRequest) -> dict[str, Any]:
    if not repo.rename_conversation(get_db(), conversation_id, payload.title):
        raise HTTPException(404, "There is no conversation with that id.")
    return {"ok": True, "title": payload.title}


@router.delete("/conversations/{conversation_id}")
def delete(conversation_id: str) -> dict[str, Any]:
    if not repo.delete_conversation(get_db(), conversation_id):
        raise HTTPException(404, "There is no conversation with that id.")
    return {"ok": True}


@router.post("/ask")
def ask(payload: AskRequest, background: BackgroundTasks) -> dict[str, Any]:
    """Ask a question. Returns a job id and the conversation it landed in.

    Run as a job rather than inline for the same reason the specialists
    are: a turn makes several model calls and can take half a minute, and
    a request held open for that long times out somewhere between here and
    the browser. The job also carries the live step feed, which is the
    only progress a reader gets while the model is thinking.
    """
    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(400, "Ask a question.")

    agent = catalogue.get(COPILOT)
    if agent is None:               # pragma: no cover - registry is static
        raise HTTPException(500, f"The {COPILOT!r} agent is not registered.")
    _refuse_write_tools(agent)

    if not get_client().available:
        raise HTTPException(
            400, "No language model is configured, so the copilot cannot "
                 "answer. Add a provider and key on the Settings tab.")

    db = get_db()
    conversation_id = payload.conversation_id
    if conversation_id:
        if not repo.get_conversation(db, conversation_id):
            raise HTTPException(404, "There is no conversation with that id.")
    else:
        conversation_id = repo.create_conversation(db)

    job = jobs.create("chat", total=agent.max_steps, phase="Queued",
                      request={"agent": agent.key, "question": question,
                               "conversation_id": conversation_id})
    background.add_task(_answer, job.id, conversation_id, question)
    return {"job_id": job.id, "conversation_id": conversation_id}


def _answer(job_id: str, conversation_id: str, question: str) -> None:
    """The job body: run the copilot with the thread, store the turn."""
    job = jobs.get(job_id)
    progress = JobProgress(job)
    agent = catalogue.get(COPILOT)

    try:
        db = get_db()
        progress.start(agent.max_steps, "Reading the question")

        # What has been said already. Fetched before the run so the model
        # can resolve "that" and "the same period"; see
        # `runner._history_block` for how little of it is actually sent.
        history = repo.get_turns(db, conversation_id, limit=HISTORY_FETCH)

        seen = {"steps": 0}

        def on_progress(event) -> None:
            if isinstance(event, str):
                if event.startswith("Thinking"):
                    seen["steps"] += 1
                    progress.advance(min(seen["steps"], agent.max_steps), event)
                else:
                    progress.phase(event)
                return
            kind = event.get("kind", "")
            text = event.get("text", "")
            detail = event.get("detail", "")
            if kind == "model":
                progress.phase(text, detail)
            progress.item(f"{_LABEL.get(kind, kind)}: {text}"[:300],
                          status="done" if event.get("ok", True) else "failed",
                          detail=detail[:400], advance=False,
                          key=f"{kind}:{event.get('step', 0)}:{text[:40]}")

        result = runner.run(agent, db, question=question, history=history,
                            on_progress=on_progress)

        progress.phase("Saving the answer")
        run_id = repo.save_agent_run(db, {
            "agent": agent.key,
            "status": result.status,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "seconds": result.seconds,
            "question": question,
            "answer": result.answer or {},
            "transcript": [
                {"index": s.index, "thought": s.thought, "calls": s.calls,
                 "results": s.results, "seconds": s.seconds, "error": s.error}
                for s in result.steps
            ],
            "model": result.model,
            "provider": "",
            "steps": len(result.steps),
            "tool_calls": result.tool_calls,
            "error": result.error,
            "profile": result.profile,
            "prompt_chars": result.prompt_chars,
            "unverified": result.unverified,
            "figures_checked": result.figures_checked,
        })

        repo.append_turn(db, conversation_id, question=question,
                         answer=result.answer or {}, run_id=run_id,
                         status=result.status, error=result.error)

        answer = result.answer or {}
        progress.complete(
            result={"conversation_id": conversation_id, "run_id": run_id,
                    "status": result.status,
                    "headline": answer.get("headline", ""),
                    "unverified": len(result.unverified),
                    "tool_calls": result.tool_calls},
            message=answer.get("headline") or "Answered.")

    except runner.AgentUnavailable as exc:
        progress.fail(str(exc))
    except Exception as exc:                 # pragma: no cover - defensive
        log.exception("chat turn failed")
        # Recorded as a turn even in failure: a question that produced
        # nothing is part of the conversation, and losing it leaves the
        # user looking at a thread with a gap in it.
        try:
            repo.append_turn(get_db(), conversation_id, question=question,
                             status="failed", error=str(exc))
        except Exception:
            pass
        progress.fail(f"{type(exc).__name__}: {exc}")


_LABEL = {
    "model": "AI call", "thought": "Reasoning", "tool": "Tool",
    "answer": "Answer", "error": "Error",
}
