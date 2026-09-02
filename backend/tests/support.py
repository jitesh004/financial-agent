"""Helpers shared by the tests.

Separate from conftest so tests can import it plainly, without depending on
how pytest happens to have imported the conftest module.
"""

from __future__ import annotations

import uuid


def make_user(email: str | None = None):
    """A fresh account, with nothing in it."""
    from app.auth import store
    from app.db.database import get_db

    suffix = uuid.uuid4().hex[:12]
    return store.upsert_user(
        get_db(),
        google_sub=f"test-sub-{suffix}",
        email=email or f"test-{suffix}@example.com",
        email_verified=True,
        name="Test User",
        picture="",
    )


def fresh_ledger():
    """Switch the current test to a brand-new, empty account.

    This is what replaces `Database(tmp_path / "something.db")`. A pristine
    ledger is no longer a new file - the database is shared and always up -
    it is a new tenant, whose rows row-level security keeps to itself.
    Returns the shared Database; the tenant is rebound as a side effect, and
    the autouse `tenant` fixture restores the original after the test.
    """
    from app.db.database import get_db
    from app.db.engine import TENANT

    TENANT.set(make_user().id)
    return get_db()
