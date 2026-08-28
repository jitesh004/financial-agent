"""Background job tracking with granular progress.

Every long operation - scanning a mailbox, downloading attachments, parsing a
few hundred statements - runs off the request thread and reports progress here.
The frontend polls one endpoint and renders a real bar with real counts.

The design rule: progress must be *derived from actual work completed*, never
from a timer. A bar that advances on a schedule while the work is stuck is
worse than no bar, because it lies about whether anything is happening.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobItem:
    """One unit of work, so the UI can show a per-file trace."""

    name: str
    status: str = "pending"   # pending | active | done | skipped | failed
    detail: str = ""
    #: Set for files: whether it came from the local cache.
    cached: bool = False


@dataclass
class Job:
    id: str
    kind: str                 # "scan" | "download" | "process"
    status: str = "queued"    # queued | running | complete | failed | cancelled
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

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return round(min(100.0, (self.current / self.total) * 100), 1)

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

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
            "items": [
                {"name": i.name, "status": i.status, "detail": i.detail,
                 "cached": i.cached}
                for i in items
            ],
            "item_count": len(self.items),
            "counts": self.counts(),
        }

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.items:
            out[item.status] = out.get(item.status, 0) + 1
        return out


class JobStore:
    """Thread-safe registry of running and finished jobs."""

    def __init__(self, keep: int = 20):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._keep = keep
        self._lock = threading.Lock()

    def create(self, kind: str, total: int = 0, phase: str = "") -> Job:
        job = Job(id=str(uuid.uuid4()), kind=kind, total=total, phase=phase)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            # Bound memory: a long session shouldn't accumulate every job's
            # full per-file item list forever.
            while len(self._order) > self._keep:
                self._jobs.pop(self._order.pop(0), None)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def latest(self, kind: str | None = None) -> Job | None:
        with self._lock:
            for job_id in reversed(self._order):
                job = self._jobs.get(job_id)
                if job and (kind is None or job.kind == kind):
                    return job
        return None


jobs = JobStore()


class JobProgress:
    """Handle passed into worker functions to report progress.

    Deliberately tiny: workers should be able to report progress without
    importing the whole job system or knowing how it is stored.
    """

    def __init__(self, job: Job):
        self.job = job
        self._lock = threading.Lock()

    def start(self, total: int, phase: str = "") -> None:
        with self._lock:
            self.job.total = total
            self.job.status = "running"
            if phase:
                self.job.phase = phase

    def phase(self, phase: str, message: str = "") -> None:
        with self._lock:
            self.job.phase = phase
            if message:
                self.job.message = message

    def item(self, name: str, status: str = "done", detail: str = "",
             cached: bool = False, advance: bool = True) -> None:
        with self._lock:
            self.job.items.append(
                JobItem(name=name, status=status, detail=detail, cached=cached)
            )
            if advance:
                self.job.current += 1

    def warn(self, message: str) -> None:
        with self._lock:
            self.job.warnings.append(message)

    def bump_total(self, total: int) -> None:
        """Adjust the denominator once the real amount of work is known."""
        with self._lock:
            self.job.total = total

    def complete(self, result: Any = None, message: str = "") -> None:
        with self._lock:
            self.job.status = "complete"
            self.job.finished_at = time.time()
            self.job.result = result
            self.job.phase = "Done"
            if message:
                self.job.message = message

    def fail(self, error: str) -> None:
        with self._lock:
            self.job.status = "failed"
            self.job.finished_at = time.time()
            self.job.errors.append(error)
            self.job.phase = "Failed"

    @property
    def cancelled(self) -> bool:
        return self.job.cancel_requested
