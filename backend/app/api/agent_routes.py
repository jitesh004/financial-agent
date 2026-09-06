"""The Agents tab's API.

Running an agent takes tens of seconds - several model round trips with tool
execution between them - so a run is a JOB, not a request. The POST returns a
job id immediately and the screen watches it the same way it watches an
import; the finished run is written to `agent_runs` and read back by id.

That shape is what makes "run it on the fly" honest. The alternative is an
HTTP request held open for a minute, which dies to a proxy timeout and takes
the whole analysis with it - and the analysis is the expensive part.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..agents import catalogue, runner
from ..agents import toolbelt
from ..db import repository as repo
from ..db.database import get_db
from ..jobs import JobProgress, jobs
from ..llm.client import get_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

#: How many runs of one agent to keep. Enough to see a trend; not so many
#: that a comparison reaches back past the last time the ledger was rebuilt.
KEEP_RUNS = 25


class RunRequest(BaseModel):
    #: The user's own question, when they have one. Empty means the agent's
    #: own - which is the normal case and the reason the card states it.
    question: str = ""


@router.get("")
def list_agents() -> dict[str, Any]:
    """The catalogue, with each agent's most recent run beside it.

    One request, because that is what the screen is: a set of cards each
    showing what its agent found last time and how long ago that was.
    """
    db = get_db()
    latest = repo.latest_agent_runs(db)
    client = get_client()
    cards = []
    for agent in catalogue.as_json():
        run = latest.get(agent["key"])
        cards.append({
            **agent,
            "last_run": None if run is None else {
                "id": run["id"], "status": run["status"],
                "started_at": run["started_at"], "seconds": run["seconds"],
                "headline": (run["answer"] or {}).get("headline", ""),
                "finding_count": len((run["answer"] or {}).get("findings", [])),
                "error": run["error"],
            },
        })
    budget = runner.profile_for()
    return {
        "agents": cards,
        # Said out loud rather than left to be inferred from short answers.
        # A compact run is not a broken run, and a reader who does not know
        # which budget is in force cannot tell those apart.
        "profile": {
            "name": budget.name,
            "max_steps": budget.max_steps,
            "note": (
                "This model is on the compact budget: fewer steps and "
                "smaller tool results, sized so a whole run fits inside one "
                "minute of a metered free tier. Answers are narrower, not "
                "less accurate - every figure still comes from a tool and "
                "is checked against one."
                if budget.name == "compact" else
                "This model is on the full budget: more steps and larger "
                "tool results, so an agent can follow a thread further."),
        },
        # Said once, here, rather than discovered by the user pressing Run:
        # an agent is the one feature in this app that cannot degrade to a
        # computed answer, because choosing what to look at IS the feature.
        "model_available": client.available,
        "model_note": "" if client.available else (
            "Agents need a language model - they work by reading your ledger "
            "and deciding what to look at next. Add a provider and key on "
            "the Settings tab."),
        "tools": [
            {"name": t.name, "does": t.summary}
            for t in toolbelt.TOOLS.values()
        ],
    }


@router.post("/{key}/run")
def start_run(key: str, background: BackgroundTasks,
              payload: RunRequest | None = None) -> dict[str, Any]:
    agent = catalogue.get(key)
    if agent is None:
        raise HTTPException(404, f"There is no agent called '{key}'.")
    if not get_client().available:
        raise HTTPException(
            400, "No language model is configured, so agents cannot run. Add "
                 "a provider and key on the Settings tab.")

    question = (payload.question if payload else "") or ""
    job = jobs.create("agent", total=agent.max_steps, phase="Queued",
                      request={"agent": agent.key, "name": agent.name,
                               "question": question})
    background.add_task(_run_agent, job.id, agent.key, question)
    return {"job_id": job.id, "agent": agent.key}


def _run_agent(job_id: str, key: str, question: str) -> None:
    """The job body: run the agent, store the run, report it."""
    job = jobs.get(job_id)
    progress = JobProgress(job)
    agent = catalogue.get(key)
    if agent is None:  # pragma: no cover - the route already checked
        progress.fail(f"There is no agent called '{key}'.")
        return

    try:
        db = get_db()
        progress.start(agent.max_steps, "Starting")

        # `advance` needs a step number, and the runner reports phases as
        # text. Counted here so the bar moves with the agent's actual
        # progress rather than sitting at zero until it finishes.
        seen = {"steps": 0}

        def on_progress(label: str) -> None:
            if label.startswith("Thinking"):
                seen["steps"] += 1
                progress.advance(min(seen["steps"], agent.max_steps), label)
            else:
                progress.phase(label)

        result = runner.run(agent, db, question=question,
                            on_progress=on_progress)

        progress.phase("Saving what it found")
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
            "provider": _provider_name(),
            "steps": len(result.steps),
            "tool_calls": result.tool_calls,
            "error": result.error,
            # Which budget it ran under, and what it cost - so "it only
            # looked at three things" reads as a fact about the profile
            # rather than about the ledger.
            "profile": result.profile,
            "prompt_chars": result.prompt_chars,
            # Money figures in the answer that no tool produced. Empty is
            # the normal case and the one worth trusting.
            "unverified": result.unverified,
            "figures_checked": result.figures_checked,
        })
        repo.prune_agent_runs(db, agent.key, keep=KEEP_RUNS)

        answer = result.answer or {}
        if result.status == "ok":
            progress.complete(
                result={"run_id": run_id, "agent": agent.key,
                        "status": result.status,
                        "headline": answer.get("headline", ""),
                        "findings": len(answer.get("findings", [])),
                        "tool_calls": result.tool_calls,
                        "unverified": len(result.unverified)},
                message=answer.get("headline")
                        or f"{agent.name} finished.")
        else:
            # An exhausted run is not a crash and its transcript is worth
            # keeping and reading - so it is reported as a finished job with
            # a warning rather than as a failure with nothing behind it.
            progress.warn(result.error or "The agent did not reach an answer.")
            progress.complete(
                result={"run_id": run_id, "agent": agent.key,
                        "status": result.status, "error": result.error,
                        "tool_calls": result.tool_calls},
                message=f"{agent.name} stopped without an answer - its "
                        f"working is saved.")
    except runner.AgentUnavailable as exc:
        progress.fail(str(exc))
    except Exception as exc:
        log.exception("agent run failed")
        progress.fail(f"{type(exc).__name__}: {exc}")


def _provider_name() -> str:
    from ..config import config
    return config.LLM_PROVIDER or ""


@router.get("/runs/{run_id}")
def read_run(run_id: str, transcript: bool = False) -> dict[str, Any]:
    """One run, with what changed since the run before it.

    The diff is computed on read rather than stored, because "the previous
    run" changes as soon as another one happens - baking it in at write time
    would leave a run permanently comparing itself against something two
    positions back.
    """
    db = get_db()
    run = repo.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(404, "No such agent run.")
    previous = repo.previous_agent_run(db, run["agent"], run_id)
    agent = catalogue.get(run["agent"])

    if not transcript:
        run.pop("transcript", None)
    return {
        **run,
        "agent_name": agent.name if agent else run["agent"],
        "agent_question": agent.question if agent else "",
        "previous": None if previous is None else {
            "id": previous["id"], "started_at": previous["started_at"],
            "headline": (previous["answer"] or {}).get("headline", ""),
        },
        "diff": runner.diff(run.get("answer"),
                            (previous or {}).get("answer")),
    }


@router.get("/{key}/runs")
def list_runs(key: str, limit: int = 20) -> dict[str, Any]:
    agent = catalogue.get(key)
    if agent is None:
        raise HTTPException(404, f"There is no agent called '{key}'.")
    return {
        "agent": agent.key,
        "name": agent.name,
        "runs": repo.get_agent_runs(get_db(), agent.key, limit=limit),
    }


@router.delete("/runs/{run_id}")
def delete_run(run_id: str) -> dict[str, str]:
    if not repo.delete_agent_run(get_db(), run_id):
        raise HTTPException(404, "No such agent run.")
    return {"status": "ok"}
