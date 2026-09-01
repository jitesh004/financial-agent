"""The job endpoints, through HTTP.

These cover the behaviour a user actually feels: closing the tab and coming
back to find the work still running, and finding out what happened to a job
that was in flight when the server went down.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from tests.support import fresh_ledger  # noqa: E402
from app.db import database, repository as repo  # noqa: E402
from app.jobs import JobProgress, jobs  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Point the whole app at a throwaway ledger for the duration of one test.

    Patched onto the module global rather than through get_db(path), which
    would swap the singleton for every test that runs after this one.
    """
    fresh = fresh_ledger()
    monkeypatch.setattr(database, "_db", fresh)
    return fresh


@pytest.fixture()
def client(db) -> TestClient:
    return TestClient(app)


def _finished(kind: str = "process", message: str = "Done.") -> str:
    job = jobs.create(kind, total=1)
    JobProgress(job).complete(message=message)
    return job.id


def _running(kind: str = "download", total: int = 10, request=None) -> str:
    job = jobs.create(kind, total=total, request=request)
    JobProgress(job).start(total, "Working")
    return job.id


# --------------------------------------------------------------------------
# Polling
# --------------------------------------------------------------------------

def test_a_job_id_survives_the_job_leaving_memory(client, db):
    """The bug this replaced: a valid id answering 404 after a restart."""
    job_id = _finished(message="Parsed 9 files.")
    jobs.flush()

    # Whatever a restart does to memory, the id has to keep working.
    with jobs._lock:
        jobs._jobs.pop(job_id, None)

    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["message"] == "Parsed 9 files."
    assert response.json()["restored"] is True


def test_the_gmail_job_route_falls_back_to_storage_too(client, db):
    """The old path is what the wizard polls; it must not be the weak one."""
    job_id = _finished()
    jobs.flush()
    with jobs._lock:
        jobs._jobs.pop(job_id, None)

    assert client.get(f"/api/gmail/jobs/{job_id}").status_code == 200


def test_an_unknown_job_is_still_a_404(client, db):
    assert client.get("/api/jobs/nope").status_code == 404


def test_active_work_is_discoverable_without_an_id(client, db):
    """What lets the UI reconnect to a scan it was not watching."""
    running = _running(total=10)
    _finished()

    body = client.get("/api/jobs", params={"active": True}).json()
    assert [job["id"] for job in body["jobs"]] == [running]
    assert body["active_count"] == 1


def test_listing_covers_finished_work_too(client, db):
    running = _running()
    done = _finished()

    ids = [job["id"] for job in client.get("/api/jobs").json()["jobs"]]
    assert running in ids and done in ids


def test_listing_can_be_filtered_by_kind(client, db):
    _running(kind="download")
    scan = _running(kind="scan")

    body = client.get("/api/jobs", params={"kind": "scan"}).json()
    assert [job["id"] for job in body["jobs"]] == [scan]


def test_a_job_created_this_instant_is_not_missed(client, db):
    """Listing merges memory with storage, so a job younger than the flush
    interval still shows up rather than appearing a second later."""
    job = jobs.create("scan", total=3)
    JobProgress(job).start(3)
    body = client.get("/api/jobs", params={"active": True}).json()
    assert job.id in [one["id"] for one in body["jobs"]]


# --------------------------------------------------------------------------
# Restart and resume
# --------------------------------------------------------------------------

def test_a_job_running_at_shutdown_comes_back_interrupted(client, db):
    job_id = _running(total=50)
    jobs.flush()

    # What the lifespan handler does when the process comes back up.
    assert jobs.recover() >= 1

    stored = repo.get_job(db, job_id)
    assert stored["status"] == "interrupted"


def test_resume_dispatches_only_the_unfinished_files(client, db, tmp_path):
    files = []
    for i in range(4):
        path = tmp_path / f"statement-{i}.pdf"
        path.write_bytes(b"%PDF-1.4 not really a statement")
        files.append({"path": str(path), "filename": path.name})

    job = jobs.create("process", total=4,
                      request={"files": files, "use_llm": False})
    progress = JobProgress(job)
    progress.start(4)
    # Two finished before the interruption; the third failed and must be
    # retried rather than written off.
    progress.item(files[0]["filename"], "done", key=files[0]["path"])
    progress.item(files[1]["filename"], "skipped", key=files[1]["path"])
    progress.item(files[2]["filename"], "failed", key=files[2]["path"])
    jobs.flush()
    jobs.recover()

    body = client.post(f"/api/gmail/jobs/{job.id}/resume").json()
    assert body["skipped"] == 2
    assert body["remaining"] == 2, "the failed file should be retried"
    assert body["resumed_from"] == job.id

    resumed = jobs.get(body["job_id"])
    assert [f["filename"] for f in resumed.request["files"]] == [
        "statement-2.pdf", "statement-3.pdf"]


def test_a_job_that_is_not_interrupted_cannot_be_resumed(client, db):
    job_id = _finished()
    jobs.flush()
    response = client.post(f"/api/gmail/jobs/{job_id}/resume")
    assert response.status_code == 400
    assert "complete" in response.json()["detail"]


def test_a_job_with_nothing_left_to_do_says_so(client, db, tmp_path):
    path = tmp_path / "only.pdf"
    path.write_bytes(b"%PDF")
    job = jobs.create("process", total=1,
                      request={"files": [{"path": str(path),
                                          "filename": "only.pdf"}]})
    JobProgress(job).start(1)
    JobProgress(job).item("only.pdf", "done", key=str(path))
    jobs.flush()
    jobs.recover()

    response = client.post(f"/api/gmail/jobs/{job.id}/resume")
    assert response.status_code == 400
    assert "already finished" in response.json()["detail"]


def test_a_job_that_recorded_no_request_cannot_be_resumed(client, db):
    job_id = _running(kind="process", request=None)
    jobs.flush()
    jobs.recover()

    response = client.post(f"/api/gmail/jobs/{job_id}/resume")
    assert response.status_code == 400
    assert "did not record" in response.json()["detail"]


def test_resuming_an_unknown_job_is_a_404(client, db):
    assert client.post("/api/gmail/jobs/nope/resume").status_code == 404


def test_cancelling_still_works(client, db):
    job_id = _running()
    assert client.post(f"/api/jobs/{job_id}/cancel").json() == {"status": "cancelling"}
    assert jobs.get(job_id).cancel_requested is True
