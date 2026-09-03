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


# --------------------------------------------------------------------------
# Cancelling
#
# The button existed, the endpoint existed, and the flag it set was read by
# exactly one worker. Pressing Cancel while documents were downloading or
# being read did nothing at all, for as long as the batch took.
# --------------------------------------------------------------------------

def test_cancelling_stops_a_download_within_one_document(client, db, tmp_path):
    """The loop asks before each attachment, not after the batch."""
    from app.ingestion.gmail_source import FoundAttachment, download_to_cache

    attachments = [
        FoundAttachment(message_id=f"m{i}", attachment_id=f"a{i}",
                        filename=f"statement-{i}.pdf", sender="bank@example.com",
                        subject="Your statement", date="2026-08-01", size=1024)
        for i in range(10)
    ]
    fetched: list[str] = []

    class _Client:
        def get_attachment(self, message_id, attachment_id):
            fetched.append(message_id)
            return b"%PDF-1.4 stub"

    # Stops once two have been fetched, the way a user pressing Cancel does.
    def should_stop():
        return len(fetched) >= 2

    saved = download_to_cache(_Client(), attachments, tmp_path / "gmail",
                              should_stop=should_stop)

    assert len(fetched) == 2, "it kept going after the stop was requested"
    # What was already downloaded comes back rather than being thrown away:
    # those files are on disk and a later run should find them cached.
    assert len(saved) == 2
    assert all(a.saved_path for a in saved)


def test_a_cancelled_job_says_cancelled_and_not_failed(client, db):
    """A run somebody stopped is not an error, and must not read as one."""
    job = jobs.create("download", total=3, phase="Downloading")
    progress = JobProgress(job)
    progress.cancel("Stopped after 1 of 3.")

    body = client.get(f"/api/jobs/{job.id}").json()
    assert body["status"] == "cancelled"
    assert body["active"] is False
    assert not body["errors"], "cancelling is not an error to report"
    assert "Stopped after 1 of 3." in body["message"]


def test_cancelling_a_read_leaves_what_it_already_read(client, db, tmp_path):
    """Documents read before the stop stay staged, waiting on Review."""
    from app.api.staging_routes import _run_parse
    from app.db import staging

    for i in range(4):
        path = tmp_path / f"doc-{i}.pdf"
        path.write_bytes(b"%PDF-1.4 not really a statement")
        staging.add(db, f"hash-{i}", filename=path.name, path=str(path),
                    origin="upload", kind="statement")

    job = jobs.create("stage_parse", total=4, phase="Queued")
    # Requested before the worker starts, which is the same flag the endpoint
    # sets - so this covers the endpoint's effect without racing a thread.
    job.cancel_requested = True
    _run_parse(job.id)

    body = client.get(f"/api/jobs/{job.id}").json()
    assert body["status"] == "cancelled"
    assert staging.counts(db)["total"] == 4, "nothing staged was discarded"


def test_cancelling_the_final_rebuild_leaves_the_ledger_alone(client, db):
    """Half a rebuilt ledger is worse than a finished one nobody wanted."""
    from app.api.staging_routes import _run_process

    before = repo.count_transactions(db)
    job = jobs.create("stage_process", total=1, phase="Queued")
    job.cancel_requested = True
    _run_process(job.id)

    body = client.get(f"/api/jobs/{job.id}").json()
    assert body["status"] == "cancelled"
    assert repo.count_transactions(db) == before


def test_cancelling_an_unknown_job_is_a_404(client, db):
    assert client.post("/api/jobs/not-a-job/cancel").status_code == 404
