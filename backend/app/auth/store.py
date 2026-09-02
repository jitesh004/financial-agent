"""Users, sessions and OAuth grants.

The only module that writes to the four tables outside row-level security.
Everything here is keyed by a user id or a token hash, and none of it touches
a single row of anybody's financial data - which is what makes it safe to run
without a tenant bound.

Sessions are server-side rather than a self-contained JWT. A ledger of
someone's entire financial history is exactly the case where "sign out
everywhere" and "revoke that laptop" have to actually work, and a stateless
token cannot be withdrawn before it expires. Only the SHA-256 of the cookie
value is stored, so a database backup that leaks does not hand over live
sessions.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db.database import Database

log = logging.getLogger(__name__)

#: How long an unfinished sign-in redirect stays valid. Long enough to read a
#: consent screen, short enough that an abandoned one is not lying around.
OAUTH_STATE_TTL_MINUTES = 15

ONBOARDING_STEPS = ("identity", "mailbox", "import", "done")


def _utc_text(moment: datetime | None = None) -> str:
    """A timestamp in the exact shape `fa_now()` writes.

    Every timestamp column in this schema is TEXT in this format, so times
    written from Python and times written by a column default compare
    correctly against each other with a plain string comparison.
    """
    moment = moment or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class User:
    id: str
    google_sub: str
    email: str
    email_verified: bool
    name: str
    picture: str
    status: str
    onboarding_step: str
    onboarded_at: str | None
    created_at: str
    last_seen_at: str

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def onboarded(self) -> bool:
        return bool(self.onboarded_at)

    @property
    def display_name(self) -> str:
        return self.name or self.email.split("@")[0]

    def public_json(self) -> dict[str, Any]:
        """What the browser is told about the signed-in person."""
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "display_name": self.display_name,
            "picture": self.picture,
            "onboarded": self.onboarded,
            "onboarding_step": self.onboarding_step,
            "created_at": self.created_at,
        }


def _row_to_user(row) -> User:
    return User(
        id=str(row["id"]),
        google_sub=row["google_sub"],
        email=row["email"],
        email_verified=bool(row["email_verified"]),
        name=row["name"],
        picture=row["picture"],
        status=row["status"],
        onboarding_step=row["onboarding_step"],
        onboarded_at=row["onboarded_at"],
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def upsert_user(db: Database, *, google_sub: str, email: str,
                email_verified: bool, name: str, picture: str) -> User:
    """Find the account for this Google identity, or create it.

    Keyed on `sub`, never on the email address. Workspace addresses get
    renamed and personal ones get recycled; keying on the address would either
    lock someone out of their own ledger or hand a new owner of an old address
    the previous holder's statements. The address is still refreshed on every
    sign-in so the UI shows the current one.
    """
    with db.identity_connection() as conn:
        row = conn.execute(
            """INSERT INTO users (google_sub, email, email_verified, name, picture)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (google_sub) DO UPDATE SET
                   email          = excluded.email,
                   email_verified = excluded.email_verified,
                   -- Google's profile name and picture are theirs to change;
                   -- a blank one arriving later must not wipe what we have.
                   name           = COALESCE(NULLIF(excluded.name, ''), users.name),
                   picture        = COALESCE(NULLIF(excluded.picture, ''), users.picture),
                   last_seen_at   = fa_now()
               RETURNING *""",
            (google_sub, email, email_verified, name, picture),
        ).fetchone()
    return _row_to_user(row)


def get_user(db: Database, user_id: str) -> User | None:
    with db.identity_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def count_users(db: Database) -> int:
    with db.identity_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def set_onboarding_step(db: Database, user_id: str, step: str) -> User | None:
    """Record how far the wizard has got, and stamp completion at the end.

    The step only ever moves forward. Someone revisiting an earlier screen to
    correct a detail is not un-onboarding themselves, and the next sign-in
    should still land them where they actually left off.
    """
    if step not in ONBOARDING_STEPS:
        raise ValueError(f"unknown onboarding step {step!r}")
    with db.identity_connection() as conn:
        row = conn.execute(
            """UPDATE users
                  SET onboarding_step = CASE
                          WHEN array_position(?::text[], ?) >
                               array_position(?::text[], onboarding_step)
                          THEN ? ELSE onboarding_step END,
                      onboarded_at = CASE
                          WHEN ? = 'done' THEN COALESCE(onboarded_at, fa_now())
                          ELSE onboarded_at END
                WHERE id = ?
            RETURNING *""",
            (list(ONBOARDING_STEPS), step, list(ONBOARDING_STEPS), step, step, user_id),
        ).fetchone()
    return _row_to_user(row) if row else None


def reopen_onboarding(db: Database, user_id: str) -> User | None:
    """Send someone back through the wizard, from the top."""
    with db.identity_connection() as conn:
        row = conn.execute(
            "UPDATE users SET onboarding_step = 'identity', onboarded_at = NULL"
            " WHERE id = ? RETURNING *", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(db: Database, user_id: str, *, ttl_hours: int,
                   user_agent: str = "", ip: str = "") -> str:
    """Mint a session and return the value to put in the cookie.

    The raw token is returned once and never stored; the database keeps only
    its hash, so it cannot be read back out - not by this code, not by anyone
    holding a backup.
    """
    token = secrets.token_urlsafe(32)
    expires = _utc_text(datetime.now(timezone.utc) + timedelta(hours=ttl_hours))
    with db.identity_connection() as conn:
        conn.execute(
            """INSERT INTO user_sessions
                   (user_id, token_hash, expires_at, user_agent, ip)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, hash_token(token), expires, user_agent[:400], ip[:64]),
        )
    return token


def resolve_session(db: Database, token: str) -> User | None:
    """The signed-in user for a cookie value, or None.

    Expiry and revocation are checked in the same statement that reads the
    row, so there is no window where an expired session is briefly valid
    because two queries disagreed.
    """
    if not token:
        return None
    with db.identity_connection() as conn:
        row = conn.execute(
            """UPDATE user_sessions s
                  SET last_used_at = fa_now()
                 FROM users u
                WHERE s.user_id = u.id
                  AND s.token_hash = ?
                  AND s.revoked_at IS NULL
                  AND s.expires_at > fa_now()
                  AND u.status = 'active'
            RETURNING u.*""",
            (hash_token(token),),
        ).fetchone()
    return _row_to_user(row) if row else None


def revoke_session(db: Database, token: str) -> None:
    with db.identity_connection() as conn:
        conn.execute(
            "UPDATE user_sessions SET revoked_at = fa_now()"
            " WHERE token_hash = ? AND revoked_at IS NULL", (hash_token(token),))


def revoke_all_sessions(db: Database, user_id: str) -> int:
    with db.identity_connection() as conn:
        cursor = conn.execute(
            "UPDATE user_sessions SET revoked_at = fa_now()"
            " WHERE user_id = ? AND revoked_at IS NULL", (user_id,))
        return cursor.rowcount


def purge_expired_sessions(db: Database) -> int:
    """Drop sessions nobody can use any more.

    Kept a week past expiry rather than deleted the moment they lapse, so
    "when did that laptop last sign in" survives long enough to answer.
    """
    with db.identity_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM user_sessions"
            " WHERE expires_at < to_char((now() AT TIME ZONE 'utc') - interval '7 days',"
            "                            'YYYY-MM-DD HH24:MI:SS')")
        return cursor.rowcount


# ---------------------------------------------------------------------------
# In-flight OAuth redirects
# ---------------------------------------------------------------------------

def save_oauth_state(db: Database, *, state: str, code_verifier: str,
                     purpose: str, redirect_to: str,
                     user_id: str | None = None) -> None:
    with db.identity_connection() as conn:
        conn.execute(
            """INSERT INTO oauth_states
                   (state, code_verifier, purpose, redirect_to, user_id)
               VALUES (?, ?, ?, ?, ?)""",
            (state, code_verifier, purpose, redirect_to, user_id),
        )


def take_oauth_state(db: Database, state: str) -> dict[str, Any] | None:
    """Consume a pending redirect. Single-use, by deleting as it is read.

    Deleting in the same statement is what makes the callback un-replayable:
    a second request carrying the same `state` finds nothing and is rejected,
    so an authorization code that leaked into a log or a referrer header
    cannot be spent twice.
    """
    if not state:
        return None
    with db.identity_connection() as conn:
        row = conn.execute(
            "DELETE FROM oauth_states WHERE state = ?"
            " AND created_at > to_char((now() AT TIME ZONE 'utc')"
            f"                          - interval '{OAUTH_STATE_TTL_MINUTES} minutes',"
            "                          'YYYY-MM-DD HH24:MI:SS')"
            " RETURNING *",
            (state,),
        ).fetchone()
    return dict(zip(row.keys(), row)) if row else None


def purge_stale_oauth_states(db: Database) -> int:
    with db.identity_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM oauth_states WHERE created_at <"
            " to_char((now() AT TIME ZONE 'utc')"
            f"         - interval '{OAUTH_STATE_TTL_MINUTES} minutes',"
            "         'YYYY-MM-DD HH24:MI:SS')")
        return cursor.rowcount


# ---------------------------------------------------------------------------
# The Gmail grant
# ---------------------------------------------------------------------------

def save_google_token(db: Database, user_id: str, token_json: str,
                      scopes: str = "") -> None:
    with db.identity_connection() as conn:
        conn.execute(
            """INSERT INTO google_tokens (user_id, token_json, scopes)
               VALUES (?, ?, ?)
               ON CONFLICT (user_id) DO UPDATE SET
                   token_json = excluded.token_json,
                   scopes     = excluded.scopes,
                   updated_at = fa_now()""",
            (user_id, token_json, scopes),
        )


def get_google_token(db: Database, user_id: str) -> str | None:
    with db.identity_connection() as conn:
        row = conn.execute(
            "SELECT token_json FROM google_tokens WHERE user_id = ?",
            (user_id,)).fetchone()
    return row["token_json"] if row else None


def delete_google_token(db: Database, user_id: str) -> None:
    with db.identity_connection() as conn:
        conn.execute("DELETE FROM google_tokens WHERE user_id = ?", (user_id,))
