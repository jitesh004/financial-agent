"""Turning a cookie into a signed-in user, for the whole request.

This runs as plain ASGI middleware rather than as a FastAPI dependency, for
two reasons:

  1. It is the choke point that closes the API. Protection by dependency is
     opt-in, and the failure mode of forgetting one on a new route is silent -
     an endpoint serving somebody's transactions to anyone who asks. Here the
     default is closed and a route has to be named in `PUBLIC_PATHS` to be
     reachable without signing in.
  2. It binds the tenant for everything downstream, including work that
     outlives the response. FastAPI's `BackgroundTasks` run after the endpoint
     returns but still inside this middleware's `await`, so the ContextVar set
     here is still in force when an ingestion job starts writing rows.

Deliberately NOT Starlette's `BaseHTTPMiddleware`: that runs the rest of the
app in a separate task, which changes when context is copied and turns the
guarantee in (2) into something subtle. Plain ASGI has no such ambiguity.
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import config
from ..db.database import get_db
from ..db.engine import TENANT
from . import store

log = logging.getLogger(__name__)

#: Reachable without a session. Everything else under /api is not.
#: `/api/health` so a load balancer can probe without credentials, and the
#: auth endpoints because they are how a session is obtained in the first
#: place - a sign-in that required being signed in would be a closed loop.
PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/config",
    "/api/auth/session",
    "/api/auth/google/start",
    "/api/auth/google/callback",
})


def set_session_cookie(response, token: str) -> None:
    """Attach a session cookie.

    httponly so no script can read it, samesite=lax so it survives the
    top-level redirect back from Google's consent screen but is not sent on a
    cross-site POST, and secure wherever the app is served over TLS.
    """
    response.set_cookie(
        config.SESSION_COOKIE,
        token,
        max_age=config.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=config.SESSION_COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(
        config.SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=config.SESSION_COOKIE_SECURE,
        path="/",
    )


def _cookie_value(scope: Scope) -> str:
    for name, value in scope.get("headers") or ():
        if name == b"cookie":
            for part in value.decode("latin-1").split(";"):
                key, _, val = part.strip().partition("=")
                if key == config.SESSION_COOKIE:
                    return val
    return ""


class AuthContextMiddleware:
    """Resolves the session, binds the tenant, and closes the API by default."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        token = _cookie_value(scope)
        user = None
        if token:
            try:
                user = await run_in_threadpool(store.resolve_session, get_db(), token)
            except Exception:
                # A database that is down is a 500 on the endpoint that needs
                # it, not a silent 401 telling the user their sign-in expired.
                log.exception("could not resolve the session cookie")

        path = scope.get("path", "")
        if user is None and path.startswith("/api/") and path not in PUBLIC_PATHS:
            response = JSONResponse(
                {"detail": "Sign in to continue.", "code": "not_authenticated"},
                status_code=401,
            )
            return await response(scope, receive, send)

        state = scope.setdefault("state", {})
        state["user"] = user

        reset = TENANT.set(user.id if user else None)
        try:
            await self.app(scope, receive, send)
        finally:
            TENANT.reset(reset)
