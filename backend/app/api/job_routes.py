"""Background jobs, across every kind.

The existing job endpoints live under `/api/gmail/jobs` for historical reasons,
but jobs are not a Gmail concept - the file registry's retry creates one too.
These are the kind-agnostic routes, and the ones the header's mailbox button
polls to know whether anything is running at all.

The important one is `GET /api/jobs?active=true`. It answers "is work happening
right now?" without the caller needing to have kept a job id, which is what
makes progress survive closing the tab: the UI reconnects to work in flight
instead of assuming that whatever it was not watching had stopped.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..db.database import get_db
from ..db import repository as repo
from ..jobs import jobs

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(active: bool = False, kind: str | None = None,
              limit: int = 20) -> dict[str, Any]:
    """Recent jobs, newest first.

    Live jobs are read from memory and stored ones from SQLite, then merged -
    a job that finished before the last restart and one running right now both
    belong in the same list, and the caller should not have to ask twice.
    """
    jobs.flush()
    live = {job.id: job.to_dict() for job in jobs.active()}
    stored = repo.list_jobs(get_db(), limit=max(limit, len(live)), kind=kind,
                            active_only=active)

    merged: list[dict[str, Any]] = []
    for row in stored:
        merged.append(live.pop(row["id"], None) or jobs_snapshot(row))
    # Anything still live but not yet flushed (a job created this instant)
    # belongs at the front rather than being missed entirely.
    merged = [*live.values(), *merged]
    if kind:
        merged = [job for job in merged if job["kind"] == kind]
    if active:
        merged = [job for job in merged if job["active"]]

    merged.sort(key=lambda job: job.get("started_at") or 0, reverse=True)
    return {
        "jobs": merged[:limit],
        "active_count": sum(1 for job in merged if job["active"]),
    }


def jobs_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    from ..jobs import _stored_to_dict
    return _stored_to_dict(row)


@router.get("/{job_id}")
def read_job(job_id: str) -> dict[str, Any]:
    """One job, from memory if it is live and from SQLite if it is not.

    The fallback is the point: a job id handed out before a restart used to
    answer 404, which is indistinguishable from "that never happened".
    """
    snapshot = jobs.snapshot(job_id)
    if snapshot is None:
        raise HTTPException(404, f"No job {job_id}")
    return snapshot


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, str]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"No job {job_id}")
    job.cancel_requested = True
    return {"status": "cancelling"}
