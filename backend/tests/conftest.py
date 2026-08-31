"""Test-wide isolation.

Several tests drive the app through `TestClient`, and every endpoint reaches
for `get_db()`. With nothing overriding it that resolves to the real ledger in
data/, so running the suite wrote parsed statements, accounts and job history
straight into the user's own database - hundreds of rows of fixture data mixed
in with their real one, indistinguishable afterwards except by timestamp.

Pointing the singleton at a throwaway file for the whole session fixes it at
the root. It is session-scoped rather than per-test because a fresh database
per test would re-run the schema and migrations a few hundred times for no
benefit; tests that need a pristine one already build their own.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture(scope="session", autouse=True)
def isolated_database():
    """Redirect every get_db() caller to a temporary ledger.

    Assigned to the module global rather than passed through get_db(path),
    because get_db caches the first Database it builds and callers that
    already hold a reference would keep the old one.
    """
    from app.db import database

    previous = database._db
    database._db = database.Database(
        Path(tempfile.mkdtemp(prefix="fa-tests-")) / "test_ledger.db")
    try:
        yield database._db
    finally:
        database._db = previous


@pytest.fixture(scope="session", autouse=True)
def quiet_job_flusher():
    """Keep the job flusher's background thread out of the suite.

    Every test that starts a job would otherwise spawn a timer thread writing
    to whichever database was current when it began - a race that turns into
    an intermittent failure long after the test that caused it.
    """
    from app.jobs import jobs

    jobs._flusher.ensure_running = lambda: None
    yield


@pytest.fixture
def tmp_db():
    """A pristine ledger for tests that write and read their own rows.

    Separate from the session-wide redirect: the merchant cache is a shared
    table, and one test teaching it a merchant would otherwise decide what a
    later test sees.
    """
    import tempfile as _tempfile
    from app.db import database

    return database.Database(
        Path(_tempfile.mkdtemp(prefix="fa-case-")) / "case.db")
