"""The download -> parse chain, and what happens when nobody is watching.

The import used to be chained in the browser: post to /download, await the
job, then post the resulting files to /process. That works right up until
someone closes the tab between the two, which leaves a pile of downloaded PDFs
that nothing will ever parse - and no record that anything was meant to.

Moving the chain onto the server is what makes "close the UI, the import keeps
going" true rather than aspirational, so these tests pin it: the follow-on job
is registered before the download reports success, it is reachable from the
download's own result, and it runs to completion with no client involved.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.api import gmail_routes  # noqa: E402
from app.db import database, repository as repo  # noqa: E402
from app.ingestion.gmail_source import FakeGmailClient  # noqa: E402
from app.jobs import jobs  # noqa: E402


def _pdf(name: str) -> bytes:
    """Bytes that are recognisably a PDF but carry no statement.

    Enough for the download stage, which only moves bytes; the parse stage is
    expected to fail on them and that is the point - the chain has to run and
    report honestly, not only work on happy input.
    """
    return b"%PDF-1.4\n% " + name.encode() + b"\n"


@pytest.fixture()
def db(monkeypatch, tmp_path):
    fresh = database.Database(tmp_path / "chain.db")
    monkeypatch.setattr(database, "_db", fresh)
    return fresh


@pytest.fixture()
def cache(monkeypatch, tmp_path):
    """Downloads land in a temp cache, never the user's real one."""
    target = tmp_path / "cache"
    target.mkdir()
    monkeypatch.setattr(gmail_routes, "CACHE", target)
    return target


@pytest.fixture()
def fake_client(monkeypatch):
    client = FakeGmailClient.from_files([
        ("statements@hdfcbank.net", "Your account statement",
         "hdfc-statement.pdf", _pdf("hdfc")),
        ("cards@sbicard.com", "Credit card statement",
         "sbi-card.pdf", _pdf("sbi")),
    ])
    monkeypatch.setattr(gmail_routes, "_require_client", lambda: client)
    return client


def _selection() -> list[dict]:
    return [
        {"message_id": "msg0", "attachment_id": "att0",
         "filename": "hdfc-statement.pdf", "sender": "statements@hdfcbank.net",
         "subject": "Your account statement", "date": "", "size": 32},
        {"message_id": "msg1", "attachment_id": "att1",
         "filename": "sbi-card.pdf", "sender": "cards@sbicard.com",
         "subject": "Credit card statement", "date": "", "size": 30},
    ]


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------

def test_a_download_alone_stops_at_the_files(db, cache, fake_client):
    job = jobs.create("download", total=2, request={"attachments": _selection()})
    gmail_routes._run_download(job.id, _selection(), then_process=False)

    assert job.status == "complete"
    assert len(job.result["files"]) == 2
    assert job.result["next_job_id"] is None


def test_a_chained_download_starts_the_parse_itself(db, cache, fake_client):
    job = jobs.create("download", total=2, request={"attachments": _selection()})
    gmail_routes._run_download(job.id, _selection(), then_process=True)

    follow_on = job.result["next_job_id"]
    assert follow_on, "the download should have handed off to a parse job"

    parse = jobs.get(follow_on)
    # A download now hands off to STAGING, not to the ledger: the files are
    # read into the staging area and stop there, and nothing counts until
    # Process data on the last step of the wizard.
    assert parse.kind == "stage_parse"
    # It ran to completion here on the download's own worker, with no second
    # request from anyone - which is the whole point.
    assert parse.status in {"complete", "failed"}
    assert not parse.is_active


def test_the_follow_on_job_exists_before_the_download_reports_success(db, cache,
                                                                     fake_client):
    """Order matters: a client polling at the wrong instant must not see a
    finished download whose successor does not exist yet."""
    job = jobs.create("download", total=2, request={"attachments": _selection()})
    seen = {}

    original = gmail_routes.JobProgress.complete

    def spy(self, result=None, message=""):
        # At the moment the download is marked complete, the id it advertises
        # has to resolve to a real job.
        if result and result.get("next_job_id"):
            seen["resolvable"] = jobs.get(result["next_job_id"]) is not None
        return original(self, result=result, message=message)

    gmail_routes.JobProgress.complete = spy
    try:
        gmail_routes._run_download(job.id, _selection(), then_process=True)
    finally:
        gmail_routes.JobProgress.complete = original

    assert seen.get("resolvable") is True


def test_a_chained_import_is_durable_end_to_end(db, cache, fake_client):
    """Both halves survive the process that ran them."""
    job = jobs.create("download", total=2, request={"attachments": _selection()})
    gmail_routes._run_download(job.id, _selection(), then_process=True)
    jobs.flush()

    stored_download = repo.get_job(db, job.id)
    assert stored_download["status"] == "complete"

    stored_parse = repo.get_job(db, stored_download["result"]["next_job_id"])
    assert stored_parse is not None
    assert stored_parse["status"] in {"complete", "failed"}


def test_a_failed_download_starts_nothing(db, cache, monkeypatch):
    def explode():
        raise RuntimeError("token expired")

    monkeypatch.setattr(gmail_routes, "_require_client", explode)
    job = jobs.create("download", total=1, request={"attachments": _selection()})
    before = len(jobs.active())

    gmail_routes._run_download(job.id, _selection(), then_process=True)

    assert job.status == "failed"
    assert len(jobs.active()) <= before, "a failed download must not queue a parse"


# --------------------------------------------------------------------------
# Resuming a chained import
# --------------------------------------------------------------------------

def test_the_endpoint_records_whether_to_chain(db, cache, fake_client):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    body = client.post("/api/gmail/download", json={
        "attachments": _selection(), "then_process": True,
    }).json()

    stored = jobs.get(body["job_id"])
    assert stored.request["then_process"] is True


def test_a_resumed_download_still_goes_on_to_parse(db, cache, fake_client):
    """Whether the run was going to parse is part of what it was asked to do.

    Dropped on resume, the user gets their files downloaded a second time and
    still no transactions - the least useful possible outcome.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    job = jobs.create("download", total=2,
                      request={"attachments": _selection(),
                               "then_process": True, "use_llm": False})
    from app.jobs import JobProgress
    progress = JobProgress(job)
    progress.start(2)
    progress.item("hdfc-statement.pdf", "done", key="msg0/hdfc-statement.pdf")
    jobs.flush()
    jobs.recover()

    body = TestClient(app).post(f"/api/gmail/jobs/{job.id}/resume").json()
    assert body["remaining"] == 1
    assert jobs.get(body["job_id"]).request["then_process"] is True
