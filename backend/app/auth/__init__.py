"""Sign-in, sessions and per-user onboarding.

  store.py    users, sessions, pending OAuth redirects, the Gmail grant
  google.py   the authorization-code + PKCE flow against Google
  session.py  the ASGI middleware that resolves a cookie and binds the tenant
  deps.py     FastAPI dependencies for reading the signed-in user

The tenancy this establishes is enforced in db/engine.py, not here. This
package decides *who* the request is; the database decides what that person
can see.
"""

from .deps import (admin_user, current_user, onboarded_user,
                   optional_user)
from .session import AuthContextMiddleware, clear_session_cookie, set_session_cookie
from .store import ONBOARDING_STEPS, User

__all__ = [
    "AuthContextMiddleware", "ONBOARDING_STEPS", "User", "admin_user",
    "clear_session_cookie",
    "current_user", "onboarded_user", "optional_user", "set_session_cookie",
]
