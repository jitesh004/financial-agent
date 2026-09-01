"""Test-wide isolation.

Several tests drive the app through `TestClient`, and every endpoint reaches
for `get_db()`. With nothing overriding it that resolves to the real ledger,
so running the suite used to write parsed statements, accounts and job history
straight into the user's own database - hundreds of rows of fixture data mixed
in with their real one, indistinguishable afterwards except by timestamp.

Against PostgreSQL that is solved twice over:

  - The suite runs against its own database (FA_TEST_DATABASE_URL, or a
    `financial_agent_test` built here), created fresh and dropped at the end.
  - Every test gets its own USER. That is what "a pristine ledger" means now:
    row-level security scopes each test to rows nothing else can see, so tests
    cannot leak state into one another through the shared merchant cache or
    anywhere else - and the suite exercises the isolation mechanism on every
    single test rather than in one dedicated case.

The app role is deliberately not a superuser. PostgreSQL exempts superusers
from row-level security, so a suite running as one would pass while proving
nothing about the guarantee it is meant to be checking.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

#: Where to reach a PostgreSQL superuser that can create the test database.
#: Only used when FA_TEST_DATABASE_URL is not set.
ADMIN_URL = os.environ.get(
    "FA_TEST_ADMIN_URL", "postgresql://postgres@localhost:5432/postgres")

TEST_DB = "financial_agent_test"
TEST_ROLE = "financial_agent_test"
TEST_PASSWORD = "financial_agent_test"


def _provision() -> str:
    """Build a throwaway database owned by an ordinary (non-superuser) role."""
    import psycopg

    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (TEST_ROLE,)).fetchone()
        if not exists:
            admin.execute(
                f"CREATE ROLE {TEST_ROLE} LOGIN PASSWORD '{TEST_PASSWORD}'")
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        admin.execute(f"CREATE DATABASE {TEST_DB} OWNER {TEST_ROLE}")

    host = urlsplit(ADMIN_URL)
    port = host.port or 5432
    return (f"postgresql://{TEST_ROLE}:{TEST_PASSWORD}"
            f"@{host.hostname or 'localhost'}:{port}/{TEST_DB}")


@pytest.fixture(scope="session", autouse=True)
def database_url(tmp_path_factory) -> str:
    """Point the whole suite at a database of its own.

    Skips rather than fails when there is no PostgreSQL to talk to: a
    contributor without one should get a clear message, not a wall of
    connection errors.
    """
    url = os.environ.get("FA_TEST_DATABASE_URL")
    if not url:
        try:
            url = _provision()
        except Exception as exc:                    # pragma: no cover
            pytest.skip(
                f"no PostgreSQL available for the test suite ({exc}). Start one "
                f"and set FA_TEST_ADMIN_URL, or set FA_TEST_DATABASE_URL to an "
                f"existing database owned by a non-superuser role.")

    os.environ["FA_DATABASE_URL"] = url
    # Statement files and snapshots go somewhere disposable too - the suite
    # uploads fixtures, and they should not land in the developer's data/.
    os.environ["FA_DATA_DIR"] = str(tmp_path_factory.mktemp("fa-data"))
    return url


@pytest.fixture(scope="session", autouse=True)
def isolated_database(database_url):
    """Redirect every get_db() caller to the test database.

    Assigned to the module global rather than passed through get_db(url),
    because get_db caches the first Database it builds and callers that
    already hold a reference would keep the old one.
    """
    from app.config import config
    from app.db import database

    config.DATABASE_URL = database_url
    config.DATA_DIR = os.environ["FA_DATA_DIR"]
    database.DATA_DIR = Path(config.DATA_DIR)

    previous = database._db
    database._db = database.Database(database_url)
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


@pytest.fixture(autouse=True)
def tenant(isolated_database):
    """Give every test its own user, and bind it for the whole test.

    This is what replaces "a fresh SQLite file per test": the rows a test
    writes are visible to it and to nothing else, enforced by the database.
    """
    from app.db.engine import TENANT

    from .support import make_user

    user = make_user()
    token = TENANT.set(user.id)
    try:
        yield user
    finally:
        TENANT.reset(token)


@pytest.fixture
def tmp_db(isolated_database):
    """A pristine ledger for tests that write and read their own rows.

    Now simply the shared database seen through this test's own tenant - the
    `tenant` fixture above has already made it private.
    """
    return isolated_database


_TOKENS: dict[str, str] = {}
_AUTO_SIGN_IN = True


def session_cookie(user_id: str) -> str:
    """A live session token for `user_id`, minted once per run."""
    from app.auth import store
    from app.db.database import get_db

    if user_id not in _TOKENS:
        _TOKENS[user_id] = store.create_session(get_db(), user_id, ttl_hours=6)
    return _TOKENS[user_id]


@contextmanager
def anonymous():
    """Run a block with test clients signed OUT.

    For the tests that assert the API is actually closed - without this they
    would be handed a session by the fixture below and prove nothing.
    """
    global _AUTO_SIGN_IN
    _AUTO_SIGN_IN = False
    try:
        yield
    finally:
        _AUTO_SIGN_IN = True


@pytest.fixture(scope="session", autouse=True)
def signed_in_test_clients(isolated_database):
    """Every TestClient request carries a session for the current tenant.

    The API is closed by default now - `auth/session.py` answers 401 to
    anything under /api that is not explicitly public - so more than sixty
    existing tests would otherwise be testing the sign-in wall rather than the
    endpoint they were written for. Patching rather than editing each fixture
    keeps those tests about what they were about; whether the wall itself
    works is asserted deliberately in test_auth.py, under `anonymous()`.

    The cookie is attached per REQUEST rather than at construction, because
    several `client` fixtures are module-scoped: built once, then used from
    tests that each have a different tenant. Reading the tenant at call time
    is what keeps such a client pointed at the right account.
    """
    from starlette import testclient

    from app.config import config
    from app.db.engine import current_tenant

    original = testclient.TestClient.request

    def request(self, *args, **kwargs):
        tenant = current_tenant()
        if _AUTO_SIGN_IN and tenant:
            self.cookies.set(config.SESSION_COOKIE, session_cookie(tenant))
        else:
            self.cookies.pop(config.SESSION_COOKIE, None)
        return original(self, *args, **kwargs)

    testclient.TestClient.request = request
    try:
        yield
    finally:
        testclient.TestClient.request = original


@pytest.fixture
def signed_in_client(signed_in_test_clients):
    """A TestClient for the current tenant."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def pytest_sessionfinish(session, exitstatus):     # pragma: no cover
    """Drop the throwaway database, if this run built one."""
    if os.environ.get("FA_TEST_DATABASE_URL"):
        return
    try:
        from app.db import database
        if database._db is not None:
            database._db.close()
        import psycopg
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    except Exception:
        pass
