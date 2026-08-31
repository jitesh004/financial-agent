"""Background job tracking with granular progress.

Every long operation - scanning a mailbox, downloading attachments, parsing a
few hundred statements - runs off the request thread and reports progress here.
The frontend polls one endpoint and renders a real bar with real counts.

The design rule: progress must be *derived from actual work completed*, never
from a timer. A bar that advances on a schedule while the work is stuck is
worse than no bar, because it lies about whether anything is happening.

The second rule, added once these jobs got long enough to outlive a browser
session: **a job's state must survive the process that ran it.** In-memory
progress is the fast path and stays authoritative while a job runs, but every
job is mirrored to SQLite by a background flusher, so closing the UI and coming
back - or restarting the API entirely - shows what actually happened rather
than a 404. See `_Flusher`.

Writes are batched rather than synchronous. A four-hundred-file download calls
`item()` four hundred times; committing each one would turn a progress report
into the slowest part of the job.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: How often the flusher mirrors in-memory jobs to SQLite.
FLUSH_INTERVAL_SECONDS = 0.75

#: Statuses that mean the job is no longer doing anything.
TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled", "interrupted"})


@dataclass
class JobItem:
    """One unit of work, so the UI can show a per-file trace."""

    name: str
    status: str = "pending"   # pending | active | done | skipped | failed
    detail: str = ""
    #: Set for files: whether it came from the local cache.
    cached: bool = False
    #: Stable identity for this unit of work, used when resuming an
    #: interrupted job. Falls back to the name, which is fine for display but
    #: not unique - two banks both send "statement.pdf".
    key: str = ""


@dataclass
class Job:
    id: str
    kind: str                 # "scan" | "download" | "process"
    status: str = "queued"    # queued | running | complete | failed
                              # | cancelled | interrupted
    phase: str = ""           # human-readable current stage
    current: int = 0
    total: int = 0
    message: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    items: list[JobItem] = field(default_factory=list)
    result: Any = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cancel_requested: bool = False

    #: What this job was asked to do. Persisted so an interrupted job can be
    #: resumed - without it, all a restart can report is that something was
    #: running once.
    request: Any = None

    #: The store that created this job, so finishing it can persist through
    #: the same one. Reaching for the module-level registry instead would be
    #: invisible in production - there is only ever one - and wrong the moment
    #: anything holds a second store, which the tests do.
    store: "JobStore | None" = field(default=None, repr=False)

    #: Shared by JobProgress and the flusher. On the job rather than on
    #: JobProgress so two handles over the same job actually exclude each
    #: other, and so the flusher can snapshot a consistent view.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    #: How many items have reached the database, so a flush appends only what
    #: is new instead of rewriting the whole list every tick.
    persisted_items: int = field(default=0, repr=False)
    #: Set when anything on the header changed since the last flush.
    dirty: bool = field(default=True, repr=False)

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return round(min(100.0, (self.current / self.total) * 100), 1)

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    @property
    def is_active(self) -> bool:
        return self.status not in TERMINAL_STATUSES

    def eta_seconds(self) -> float | None:
        """Remaining time from the observed rate so far.

        Returns None until enough work has completed to make the estimate
        meaningful - a projection from one sample is noise dressed up as
        information.
        """
        if self.current < 3 or self.total <= 0 or self.status != "running":
            return None
        rate = self.current / max(self.elapsed, 0.001)
        if rate <= 0:
            return None
        return round((self.total - self.current) / rate, 1)

    def to_dict(self, item_limit: int = 400) -> dict[str, Any]:
        with self.lock:
            items = self.items[-item_limit:]
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "phase": self.phase,
                "current": self.current,
                "total": self.total,
                "percent": self.percent,
                "message": self.message,
                "elapsed": self.elapsed,
                "eta_seconds": self.eta_seconds(),
                "errors": self.errors,
                "warnings": self.warnings[-50:],
                "result": self.result,
                "active": self.is_active,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "resumable": self.status == "interrupted" and self.request is not None,
                "items": [
                    {"name": i.name, "status": i.status, "detail": i.detail,
                     "cached": i.cached}
                    for i in items
                ],
                "item_count": len(self.items),
                "counts": self._counts(),
            }

    def counts(self) -> dict[str, int]:
        with self.lock:
            return self._counts()

    def _counts(self) -> dict[str, int]:
        """Caller must hold the lock."""
        out: dict[str, int] = {}
        for item in self.items:
            out[item.status] = out.get(item.status, 0) + 1
        return out

    def header(self) -> dict[str, Any]:
        """The row shape the repository persists. Caller must hold the lock."""
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "phase": self.phase, "current": self.current, "total": self.total,
            "message": self.message, "started_at": self.started_at,
            "finished_at": self.finished_at, "result": self.result,
            "request": self.request, "errors": self.errors,
            "warnings": self.warnings,
        }


class _Flusher:
    """Mirrors in-memory jobs to SQLite on a timer.

    A daemon thread started on first use rather than at import: creating the
    database as a side effect of importing this module would make the import
    order load-bearing, and the tests that build a throwaway Database would
    each spawn a thread they never asked for.
    """

    def __init__(self, store: "JobStore"):
        self._store = store
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._lock = threading.Lock()

    def ensure_running(self) -> None:
        if not self._store.persist:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop, name="job-flusher", daemon=True)
            self._thread.start()

    def nudge(self) -> None:
        """Ask for a flush now - used when a job reaches a terminal state, so
        the final result is durable before the caller moves on."""
        self._wake.set()

    def _loop(self) -> None:
        while True:
            self._wake.wait(FLUSH_INTERVAL_SECONDS)
            self._wake.clear()
            try:
                self._store.flush()
            except Exception:  # a persistence fault must never kill a job
                log.exception("could not flush job state")


class JobStore:
    """Thread-safe registry of running and finished jobs, mirrored to SQLite."""

    def __init__(self, keep: int = 20):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._keep = keep
        self._lock = threading.Lock()
        self._flusher = _Flusher(self)
        #: Turned off by tests that build a throwaway Database - the flusher
        #: writes through get_db(), which is the real one.
        self.persist = True

    # ---- persistence wiring ------------------------------------------------

    def _db(self):
        from .db.database import get_db
        return get_db()

    def flush_job(self, job: Job) -> bool:
        """Write one job if it has changed. Returns whether anything was written.

        Takes the job rather than an id so a job evicted from the registry can
        still record its own outcome - `complete()` must be durable even if
        twenty newer jobs have pushed this one out of memory.
        """
        if not self.persist:
            return False
        with job.lock:
            if not job.dirty and job.persisted_items == len(job.items):
                return False
            header = job.header()
            new_items = [
                {"seq": index, "name": item.name, "key": item.key or item.name,
                 "status": item.status, "detail": item.detail,
                 "cached": item.cached}
                for index, item in enumerate(job.items[job.persisted_items:],
                                             start=job.persisted_items)
            ]
            pending_count = len(job.items)

        # Written outside the job lock: a slow disk must not stall the worker
        # thread that is trying to report its next item.
        from .db import repository as repo
        repo.save_job(self._db(), header, new_items)

        with job.lock:
            job.persisted_items = pending_count
            # Only clears what this flush covered. A tick that lands mid-write
            # sets dirty again and is picked up next time rather than lost.
            if len(job.items) == pending_count:
                job.dirty = False
        return True

    def flush(self) -> int:
        """Write every job with pending changes. Returns how many were written."""
        with self._lock:
            candidates = [self._jobs[job_id] for job_id in self._order
                          if job_id in self._jobs]
        return sum(1 for job in candidates if self.flush_job(job))

    def recover(self) -> int:
        """Mark jobs left running by a dead process as interrupted.

        Called once at startup. Returns how many were adjusted.
        """
        if not self.persist:
            return 0
        from .db import repository as repo
        db = self._db()
        count = repo.mark_unfinished_jobs_interrupted(db)
        repo.prune_jobs(db)

        # Memory has to agree with what was just written. At startup this is a
        # no-op because nothing is in memory yet, but leaving it out means the
        # two disagree whenever recovery runs in a live process: SQLite says
        # interrupted while `active()` still reports the job as running, so
        # anything asking "is work happening?" gets the wrong answer.
        with self._lock:
            live = [self._jobs[job_id] for job_id in self._order
                    if job_id in self._jobs]
        for job in live:
            with job.lock:
                if job.status in ("queued", "running"):
                    job.status = "interrupted"
                    job.phase = "Interrupted"
                    job.finished_at = job.finished_at or time.time()
                    job.dirty = True

        if count:
            log.info("marked %d unfinished job(s) as interrupted", count)
        return count

    # ---- registry ----------------------------------------------------------

    def create(self, kind: str, total: int = 0, phase: str = "",
               request: Any = None) -> Job:
        job = Job(id=str(uuid.uuid4()), kind=kind, total=total, phase=phase,
                  request=request, store=self)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            # Bound memory: a long session shouldn't accumulate every job's
            # full per-file item list forever. Evicting from memory is safe
            # now that the row survives in SQLite - `get` falls back to it.
            while len(self._order) > self._keep:
                self._jobs.pop(self._order.pop(0), None)
        self._flusher.ensure_running()
        self._flusher.nudge()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        """A job as a dict, from memory if it is there and from SQLite if not.

        This is what makes a job id outlive both the eviction cap and the
        process: the poller keeps working after a restart instead of getting a
        404 for work that demonstrably happened.
        """
        job = self.get(job_id)
        if job is not None:
            return job.to_dict()

        if not self.persist:
            return None
        from .db import repository as repo
        stored = repo.get_job(self._db(), job_id)
        if stored is None:
            return None
        return _stored_to_dict(stored)

    def latest(self, kind: str | None = None) -> Job | None:
        with self._lock:
            for job_id in reversed(self._order):
                job = self._jobs.get(job_id)
                if job and (kind is None or job.kind == kind):
                    return job
        return None

    def active(self) -> list[Job]:
        with self._lock:
            return [self._jobs[job_id] for job_id in self._order
                    if job_id in self._jobs and self._jobs[job_id].is_active]


jobs = JobStore()


def _stored_to_dict(stored: dict[str, Any]) -> dict[str, Any]:
    """Shape a persisted job like a live one, so callers need not care which."""
    total = stored["total"] or 0
    current = stored["current"] or 0
    finished = stored["finished_at"]
    elapsed = round((finished or stored["started_at"]) - stored["started_at"], 1)
    counts: dict[str, int] = {}
    for item in stored["items"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "id": stored["id"],
        "kind": stored["kind"],
        "status": stored["status"],
        "phase": stored["phase"],
        "current": current,
        "total": total,
        "percent": round(min(100.0, (current / total) * 100), 1) if total else 0.0,
        "message": stored["message"],
        "elapsed": elapsed,
        # Never estimated for a stored job: the only honest answer for work
        # that is not currently moving is that there is no rate to project.
        "eta_seconds": None,
        "errors": stored["errors"],
        "warnings": stored["warnings"][-50:],
        "result": stored["result"],
        "active": stored["status"] not in TERMINAL_STATUSES,
        "started_at": stored["started_at"],
        "finished_at": finished,
        "resumable": (stored["status"] == "interrupted"
                      and stored["request"] is not None),
        "items": [{"name": i["name"], "status": i["status"],
                   "detail": i["detail"], "cached": i["cached"]}
                  for i in stored["items"][-400:]],
        "item_count": len(stored["items"]),
        "counts": counts,
        "restored": True,
    }


class JobProgress:
    """Handle passed into worker functions to report progress.

    Deliberately tiny: workers should be able to report progress without
    importing the whole job system or knowing how it is stored.
    """

    def __init__(self, job: Job):
        self.job = job

    def start(self, total: int, phase: str = "") -> None:
        with self.job.lock:
            self.job.total = total
            self.job.status = "running"
            if phase:
                self.job.phase = phase
            self.job.dirty = True

    def phase(self, phase: str, message: str = "") -> None:
        with self.job.lock:
            self.job.phase = phase
            if message:
                self.job.message = message
            self.job.dirty = True

    def item(self, name: str, status: str = "done", detail: str = "",
             cached: bool = False, advance: bool = True,
             key: str = "") -> None:
        """Record one unit of work.

        `key` identifies the unit for resuming; it defaults to the name, which
        is right for display and wrong for resuming when two files share one.
        Callers that can supply something stable - a path, a message id -
        should.
        """
        with self.job.lock:
            self.job.items.append(
                JobItem(name=name, status=status, detail=detail, cached=cached,
                        key=key or name)
            )
            if advance:
                self.job.current += 1
            self.job.dirty = True

    def advance(self, current: int, phase: str = "") -> None:
        """Set the counter directly, for work reported as a running tally
        rather than item by item."""
        with self.job.lock:
            self.job.current = current
            if phase:
                self.job.phase = phase
            self.job.dirty = True

    def warn(self, message: str) -> None:
        with self.job.lock:
            self.job.warnings.append(message)
            self.job.dirty = True

    def bump_total(self, total: int) -> None:
        """Adjust the denominator once the real amount of work is known."""
        with self.job.lock:
            self.job.total = total
            self.job.dirty = True

    def complete(self, result: Any = None, message: str = "") -> None:
        with self.job.lock:
            self.job.status = "complete"
            self.job.finished_at = time.time()
            self.job.result = result
            self.job.phase = "Done"
            if message:
                self.job.message = message
            self.job.dirty = True
        self._persist_now()

    def fail(self, error: str) -> None:
        with self.job.lock:
            self.job.status = "failed"
            self.job.finished_at = time.time()
            self.job.errors.append(error)
            self.job.phase = "Failed"
            self.job.dirty = True
        self._persist_now()

    def _persist_now(self) -> None:
        """Flush synchronously at the end of a job.

        The timed flush would get there within a second, but the outcome is the
        one piece of state worth paying a commit for immediately: a crash in
        that window is exactly when someone most wants to know what finished.
        """
        store = self.job.store or jobs
        try:
            store.flush_job(self.job)
        except Exception:  # never let bookkeeping fail the work itself
            log.exception("could not persist final job state")

    @property
    def cancelled(self) -> bool:
        return self.job.cancel_requested
