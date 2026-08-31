"""Background jobs have to outlive the thing that started them.

Progress used to live in a process-local dict capped at twenty entries. That
survived neither closing the browser nor restarting the API, and the failure
mode was the worst available one: a job id that answered 404, which from the
caller's side is indistinguishable from work that never happened.

The tests here pin the three properties that fix it - a job is mirrored to
SQLite while it runs, a job id keeps working once the job is gone from memory,
and a job that was running when the process died is reported as interrupted
rather than left showing a progress bar that can never move again.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import repository as repo  # noqa: E402
from app.db.database import Database  # noqa: E402
from app.jobs import JobProgress, JobStore  # noqa: E402


@pytest.fixture()
def db() -> Database:
    return Database(Path(tempfile.mkdtemp()) / "jobs.db")


@pytest.fixture()
def store(db, monkeypatch) -> JobStore:
    """A store writing to a throwaway database.

    The timed flusher is left off: every test here flushes explicitly, so the
    assertions are about what the code writes rather than about whether a
    background thread happened to have woken up yet.
    """
    made = JobStore(keep=3)
    monkeypatch.setattr(made, "_db", lambda: db)
    monkeypatch.setattr(made._flusher, "ensure_running", lambda: None)
    return made


# --------------------------------------------------------------------------
# Mirroring
# --------------------------------------------------------------------------

def test_a_job_reaches_the_database(store, db):
    job = store.create("scan", total=10, phase="Searching",
                       request={"max_messages": 10})
    store.flush()

    stored = repo.get_job(db, job.id)
    assert stored["kind"] == "scan"
    assert stored["total"] == 10
    assert stored["request"] == {"max_messages": 10}


def test_progress_and_items_are_mirrored(store, db):
    job = store.create("download", total=3)
    progress = JobProgress(job)
    progress.start(3, "Downloading")
    progress.item("a.pdf", "done", "12 KB", key="m1/a.pdf")
    progress.item("b.pdf", "skipped", "locked", key="m2/b.pdf")
    store.flush()

    stored = repo.get_job(db, job.id)
    assert stored["status"] == "running"
    assert stored["current"] == 2
    assert [i["name"] for i in stored["items"]] == ["a.pdf", "b.pdf"]
    assert [i["key"] for i in stored["items"]] == ["m1/a.pdf", "m2/b.pdf"]


def test_items_are_appended_not_rewritten(store, db):
    """A four-hundred-file download must not rewrite its whole list per tick."""
    job = store.create("download", total=3)
    progress = JobProgress(job)
    progress.item("a.pdf")
    store.flush()
    assert job.persisted_items == 1

    progress.item("b.pdf")
    progress.item("c.pdf")
    written: list[int] = []
    original = repo.save_job

    def counting_save(database, header, items):
        written.append(len(items))
        return original(database, header, items)

    repo.save_job = counting_save
    try:
        store.flush()
    finally:
        repo.save_job = original

    assert written == [2], "the flush should have written only the two new items"
    assert len(repo.get_job(db, job.id)["items"]) == 3


def test_a_finished_job_persists_without_waiting_for_the_timer(store, db):
    job = store.create("process", total=1)
    JobProgress(job).complete(result={"run_id": "r1"}, message="Done.")

    stored = repo.get_job(db, job.id)
    assert stored["status"] == "complete"
    assert stored["result"] == {"run_id": "r1"}
    assert stored["finished_at"] is not None


def test_a_failed_job_persists_its_error(store, db):
    job = store.create("scan")
    JobProgress(job).fail("RuntimeError: no token")

    stored = repo.get_job(db, job.id)
    assert stored["status"] == "failed"
    assert stored["errors"] == ["RuntimeError: no token"]


def test_an_unserialisable_result_does_not_take_the_job_down(store, db):
    """The work already happened; a bookkeeping fault must not erase it."""
    job = store.create("process")
    JobProgress(job).complete(result={"file": object()})

    stored = repo.get_job(db, job.id)
    assert stored["status"] == "complete"


# --------------------------------------------------------------------------
# Outliving memory
# --------------------------------------------------------------------------

def test_a_job_id_still_answers_after_eviction(store, db):
    """The eviction cap must not turn a finished job into a 404."""
    first = store.create("process", total=1)
    JobProgress(first).complete(message="Parsed 4 files.")

    for _ in range(store._keep + 1):
        store.create("scan")
    store.flush()

    assert store.get(first.id) is None, "expected the job to leave memory"
    snapshot = store.snapshot(first.id)
    assert snapshot is not None
    assert snapshot["status"] == "complete"
    assert snapshot["message"] == "Parsed 4 files."
    assert snapshot["restored"] is True


def test_a_restored_job_reports_no_eta(store, db):
    """There is no honest rate to project for work that is not moving."""
    job = store.create("download", total=100)
    progress = JobProgress(job)
    progress.start(100)
    for i in range(10):
        progress.item(f"f{i}.pdf")
    store.flush()

    restored = repo.get_job(db, job.id)
    from app.jobs import _stored_to_dict
    shaped = _stored_to_dict(restored)
    assert shaped["eta_seconds"] is None
    assert shaped["percent"] == 10.0


def test_snapshot_prefers_live_state_over_the_stored_copy(store, db):
    job = store.create("download", total=2)
    progress = JobProgress(job)
    progress.start(2)
    store.flush()
    progress.item("late.pdf")  # after the flush: only in memory

    assert repo.get_job(db, job.id)["current"] == 0
    assert store.snapshot(job.id)["current"] == 1


def test_an_unknown_id_is_still_unknown(store):
    assert store.snapshot("no-such-job") is None


# --------------------------------------------------------------------------
# Surviving a restart
# --------------------------------------------------------------------------

def test_recovery_marks_a_running_job_interrupted(store, db):
    job = store.create("download", total=50)
    JobProgress(job).start(50, "Downloading")
    store.flush()

    # A restart: a brand new store over the same database, with nothing in
    # memory - exactly the state the process comes back in.
    assert repo.mark_unfinished_jobs_interrupted(db) == 1

    stored = repo.get_job(db, job.id)
    assert stored["status"] == "interrupted"
    assert "restart" in stored["message"]


def test_recovery_leaves_memory_and_storage_agreeing(store, db):
    """Recovery normally runs at startup with nothing in memory, so the two
    cannot disagree. Run it in a live process and they could: SQLite said
    interrupted while active() still reported the job as running, which is the
    wrong answer to "is work happening right now?"."""
    job = store.create("download", total=9)
    JobProgress(job).start(9)
    store.flush()
    assert job.is_active is True

    store.recover()

    assert job.status == "interrupted"
    assert job.is_active is False
    assert store.active() == []
    assert repo.get_job(db, job.id)["status"] == "interrupted"


def test_recovery_leaves_finished_jobs_alone(store, db):
    done = store.create("scan")
    JobProgress(done).complete(message="Found 12 statements.")
    failed = store.create("scan")
    JobProgress(failed).fail("boom")

    assert repo.mark_unfinished_jobs_interrupted(db) == 0
    assert repo.get_job(db, done.id)["status"] == "complete"
    assert repo.get_job(db, failed.id)["status"] == "failed"


def test_recovery_does_not_overwrite_an_existing_message(store, db):
    job = store.create("download", total=5)
    JobProgress(job).phase("Downloading", "3 of 5 files fetched")
    store.flush()
    repo.mark_unfinished_jobs_interrupted(db)
    assert repo.get_job(db, job.id)["message"] == "3 of 5 files fetched"


def test_an_interrupted_job_is_marked_resumable(store, db):
    job = store.create("download", total=5, request={"attachments": [{"a": 1}]})
    JobProgress(job).start(5)
    store.flush()
    repo.mark_unfinished_jobs_interrupted(db)

    from app.jobs import _stored_to_dict
    assert _stored_to_dict(repo.get_job(db, job.id))["resumable"] is True


def test_a_job_with_no_recorded_request_is_not_resumable(store, db):
    job = store.create("download", total=5)
    JobProgress(job).start(5)
    store.flush()
    repo.mark_unfinished_jobs_interrupted(db)

    from app.jobs import _stored_to_dict
    assert _stored_to_dict(repo.get_job(db, job.id))["resumable"] is False


# --------------------------------------------------------------------------
# Resuming
# --------------------------------------------------------------------------

def test_completed_keys_drive_what_a_resume_skips(store, db):
    job = store.create("download", total=4,
                       request={"attachments": [{"message_id": "m1"}]})
    progress = JobProgress(job)
    progress.item("a.pdf", "done", key="m1/a.pdf")
    progress.item("b.pdf", "skipped", key="m2/b.pdf")
    progress.item("c.pdf", "failed", key="m3/c.pdf")
    store.flush()

    done = repo.completed_job_keys(db, job.id)
    assert done == {"m1/a.pdf", "m2/b.pdf"}
    # A failure is retried on resume: whatever went wrong may have been the
    # interruption, and re-reading one file is cheap.
    assert "m3/c.pdf" not in done


def test_two_files_with_the_same_name_are_not_confused(store, db):
    """The reason items carry a key at all - half a mailbox calls its
    attachment statement.pdf, and resuming on the name would skip real work.

    Asserted as an equality on the stored key rather than as "the other one is
    absent": with the key thrown away and the name stored instead, the absence
    still holds and the test would pass while the bug was live.
    """
    job = store.create("download", total=2)
    progress = JobProgress(job)
    progress.item("statement.pdf", "done", key="msg-a/statement.pdf")
    store.flush()

    assert repo.completed_job_keys(db, job.id) == {"msg-a/statement.pdf"}


def test_an_item_without_a_key_falls_back_to_its_name(store, db):
    job = store.create("process", total=1)
    JobProgress(job).item("solo.pdf", "done")
    store.flush()
    assert repo.completed_job_keys(db, job.id) == {"solo.pdf"}


# --------------------------------------------------------------------------
# Listing and housekeeping
# --------------------------------------------------------------------------

def test_active_jobs_can_be_found_without_holding_an_id(store, db):
    """What lets the UI reconnect to work in flight after a reload."""
    running = store.create("download", total=9)
    JobProgress(running).start(9)
    finished = store.create("scan")
    JobProgress(finished).complete()
    store.flush()

    active = repo.list_jobs(db, active_only=True)
    assert [j["id"] for j in active] == [running.id]
    assert len(repo.list_jobs(db)) == 2


def test_listing_omits_items(store, db):
    job = store.create("download", total=2)
    JobProgress(job).item("a.pdf")
    store.flush()
    assert repo.list_jobs(db)[0]["items"] == []


def test_pruning_keeps_the_most_recent_and_cascades_to_items(store, db):
    for i in range(6):
        job = store.create("scan")
        job.started_at = 1000 + i
        JobProgress(job).item(f"f{i}.pdf")
    store.flush()

    repo.prune_jobs(db, keep=2)
    assert len(repo.list_jobs(db, limit=50)) == 2
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM job_items").fetchone()["c"] == 2


def test_a_running_job_reinserts_itself_if_its_row_is_cleared(store, db):
    """Clearing the derived scope mid-run must not orphan a live job."""
    job = store.create("download", total=3)
    progress = JobProgress(job)
    progress.start(3)
    store.flush()

    with db.connection() as conn:
        conn.execute("DELETE FROM jobs")

    progress.item("a.pdf")
    store.flush()
    assert repo.get_job(db, job.id) is not None


def test_persistence_can_be_turned_off_entirely(db, monkeypatch):
    made = JobStore()
    made.persist = False
    monkeypatch.setattr(made, "_db", lambda: db)
    job = made.create("scan")
    JobProgress(job).complete()

    assert made.flush() == 0
    assert repo.get_job(db, job.id) is None
    assert made.snapshot(job.id)["status"] == "complete"  # memory still works


def test_a_job_stops_being_active_once_it_finishes(store):
    job = store.create("scan")
    JobProgress(job).start(5)
    assert job.is_active is True
    assert job.finished_at is None

    JobProgress(job).complete()
    assert job.is_active is False
    assert job.finished_at is not None
    # The clock stops with it: elapsed must not keep growing after the fact.
    first = job.elapsed
    time.sleep(0.02)
    assert job.elapsed == first
