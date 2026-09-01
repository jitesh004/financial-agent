"""Sign-in, session management, and the onboarding wizard's server half.

Two routers live here because they are two halves of one story: the first
sign-in creates the account, and the wizard is what happens immediately after
it. Keeping them together means the step machine and the thing that advances
it are in the same file.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from ..auth import google, store
from ..auth.deps import current_user, optional_user
from ..auth.session import clear_session_cookie, set_session_cookie
from ..auth.store import ONBOARDING_STEPS, User
from ..config import config
from ..db import repository as repo
from ..db.database import get_db
from ..db import staging

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])
onboarding_router = APIRouter(prefix="/api/onboarding", tags=["auth"])


def _safe_redirect(target: str | None) -> str:
    """A same-origin path, or the app root.

    An open redirect on a sign-in callback is how a convincing phishing link
    gets built: the address bar shows this app's domain right up until Google
    hands control back. Only a path is ever accepted - never a scheme, never a
    host, and not `//evil.example` either, which a browser reads as protocol
    relative.
    """
    target = (target or "/").strip()
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


def _front_end(path: str) -> str:
    return f"{config.APP_BASE_URL}{path}"


def _fail(reason: str, redirect_to: str = "/") -> RedirectResponse:
    """Hand the browser back to the app with something it can display.

    A bare 400 in the middle of an OAuth round trip leaves the user on a blank
    JSON page with no way back; the sign-in screen can render this.
    """
    log.warning("sign-in failed: %s", reason)
    separator = "&" if "?" in redirect_to else "?"
    return RedirectResponse(
        _front_end(f"{redirect_to}{separator}auth_error={quote(reason)}"),
        status_code=303)


# --------------------------------------------------------------------------
# Who am I
# --------------------------------------------------------------------------

@router.get("/config")
def auth_config() -> dict[str, Any]:
    """What the sign-in screen needs to know before anyone has signed in.

    Chiefly whether Google is configured at all: an app whose operator has not
    set the client id should say so plainly, rather than sending someone to a
    Google page that answers 'invalid_client'.
    """
    return {
        "provider": "google",
        "configured": config.google_configured,
        "restricted": bool(config.ALLOWED_SIGNINS),
        "setup_hint": None if config.google_configured else (
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, and register "
            f"{config.oauth_redirect_uri} as an authorised redirect URI on a "
            "Web application OAuth client."
        ),
    }


@router.get("/session")
def read_session(user: User | None = Depends(optional_user)) -> dict[str, Any]:
    """The signed-in user, or `{"user": null}`.

    Public on purpose: this is the question the app asks on load, and it has
    to be answerable before there is an answer.
    """
    return {"user": user.public_json() if user else None}


@router.get("/sessions")
def list_sessions(request: Request,
                  user: User = Depends(current_user)) -> dict[str, Any]:
    """Every device currently signed in as this user.

    The reason sessions are server-side rather than a self-contained token:
    this list, and being able to end anything on it.
    """
    db = get_db()
    this_one = store.hash_token(request.cookies.get(config.SESSION_COOKIE, ""))
    with db.identity_connection() as conn:
        rows = conn.execute(
            "SELECT token_hash, issued_at, last_used_at, expires_at, user_agent, ip"
            "  FROM user_sessions"
            " WHERE user_id = ? AND revoked_at IS NULL AND expires_at > fa_now()"
            " ORDER BY last_used_at DESC", (user.id,)).fetchall()
    return {"sessions": [{
        "issued_at": r["issued_at"],
        "last_used_at": r["last_used_at"],
        "expires_at": r["expires_at"],
        "user_agent": r["user_agent"],
        "ip": r["ip"],
        "current": r["token_hash"] == this_one,
    } for r in rows]}


# --------------------------------------------------------------------------
# The Google round trip
# --------------------------------------------------------------------------

@router.get("/google/start")
def start_google(request: Request, redirect_to: str = "/", purpose: str = "signin"):
    """Begin the OAuth redirect.

    Serves both purposes: `signin` establishes who you are, `gmail` is the
    later, separate grant that lets the importer read your mailbox. The second
    requires an existing session - there would be nobody to attach the grant
    to otherwise.
    """
    if purpose not in ("signin", "gmail"):
        raise HTTPException(400, f"unknown purpose {purpose!r}")
    if not config.google_configured:
        raise HTTPException(503, auth_config()["setup_hint"])

    user: User | None = getattr(request.state, "user", None)
    if purpose == "gmail" and user is None:
        raise HTTPException(401, "Sign in before connecting Gmail.")

    trip = google.build_redirect(purpose)
    store.save_oauth_state(
        get_db(), state=trip.state, code_verifier=trip.code_verifier,
        purpose=purpose, redirect_to=_safe_redirect(redirect_to),
        user_id=user.id if user else None)
    return RedirectResponse(trip.url, status_code=307)


@router.get("/google/callback")
def google_callback(request: Request, code: str = "", state: str = "",
                    error: str = ""):
    """Where Google sends the browser back.

    Everything is verified before anything is created: that this server
    started the redirect (the `state` row exists and is consumed here), that
    the code was issued to us (the PKCE verifier), and that the identity is
    genuinely Google's (the ID token signature).
    """
    db = get_db()
    pending = store.take_oauth_state(db, state)
    redirect_to = _safe_redirect(pending.get("redirect_to") if pending else "/")

    if error:
        return _fail(error, redirect_to)
    if pending is None:
        # Either a forged callback, a replayed one, or a consent screen left
        # open past the state's lifetime.
        return _fail("This sign-in link has expired. Please try again.",
                     redirect_to)
    if not code:
        return _fail("Google did not return an authorization code.", redirect_to)

    try:
        tokens = google.exchange_code(code, pending["code_verifier"])
    except google.GoogleAuthError as exc:
        return _fail(str(exc), redirect_to)

    if pending["purpose"] == "gmail":
        return _finish_gmail_grant(db, pending, tokens, redirect_to)
    return _finish_sign_in(db, request, tokens, redirect_to)


def _finish_sign_in(db, request: Request, tokens: dict[str, Any],
                    redirect_to: str):
    raw_id_token = tokens.get("id_token")
    if not raw_id_token:
        return _fail("Google did not return an identity token.", redirect_to)
    try:
        identity = google.verify_id_token(raw_id_token)
    except google.GoogleAuthError as exc:
        return _fail(str(exc), redirect_to)

    if not identity.email_verified:
        return _fail("That Google account's email address is not verified.",
                     redirect_to)
    if not google.sign_in_allowed(identity.email):
        return _fail(f"{identity.email} is not allowed to sign in here.",
                     redirect_to)

    user = store.upsert_user(
        db, google_sub=identity.sub, email=identity.email,
        email_verified=identity.email_verified, name=identity.name,
        picture=identity.picture)
    if not user.is_active:
        return _fail("That account has been disabled.", redirect_to)

    token = store.create_session(
        db, user.id, ttl_hours=config.SESSION_TTL_HOURS,
        user_agent=request.headers.get("user-agent", ""),
        ip=(request.client.host if request.client else ""))

    # Housekeeping on the one event that happens often enough to keep the
    # tables tidy and rarely enough not to cost anything.
    try:
        store.purge_expired_sessions(db)
        store.purge_stale_oauth_states(db)
    except Exception:
        log.exception("session housekeeping failed")

    log.info("signed in: %s (%s)", user.email,
             "new account" if user.created_at == user.last_seen_at else "returning")
    response = RedirectResponse(_front_end(redirect_to), status_code=303)
    set_session_cookie(response, token)
    return response


def _finish_gmail_grant(db, pending: dict[str, Any], tokens: dict[str, Any],
                        redirect_to: str):
    user_id = pending.get("user_id")
    if not user_id:
        return _fail("That Gmail connection was not tied to an account.",
                     redirect_to)
    granted = tokens.get("scope") or ""
    if not any(scope in granted for scope in google.GMAIL_SCOPES):
        return _fail("Read access to Gmail was not granted.", redirect_to)

    previous = store.get_google_token(db, str(user_id))
    store.save_google_token(
        db, str(user_id), google.credentials_json(tokens, previous), granted)
    log.info("gmail connected for user %s", user_id)
    separator = "&" if "?" in redirect_to else "?"
    return RedirectResponse(
        _front_end(f"{redirect_to}{separator}gmail=connected"), status_code=303)


# --------------------------------------------------------------------------
# Signing out
# --------------------------------------------------------------------------

@router.post("/logout")
def logout(request: Request, _: User = Depends(current_user)) -> JSONResponse:
    store.revoke_session(get_db(), request.cookies.get(config.SESSION_COOKIE, ""))
    response = JSONResponse({"status": "signed_out"})
    clear_session_cookie(response)
    return response


@router.post("/logout-all")
def logout_everywhere(user: User = Depends(current_user)) -> JSONResponse:
    ended = store.revoke_all_sessions(get_db(), user.id)
    response = JSONResponse({"status": "signed_out", "sessions_ended": ended})
    clear_session_cookie(response)
    return response


class DeleteAccount(BaseModel):
    #: The user types their own email address. Deleting an account takes every
    #: statement, decision and dashboard with it and there is no undo, so the
    #: confirmation is deliberately something you cannot click by accident.
    confirm_email: str


@router.post("/delete-account")
def delete_account(payload: DeleteAccount,
                   user: User = Depends(current_user)) -> JSONResponse:
    """Erase the account and everything in it."""
    if payload.confirm_email.strip().lower() != user.email.lower():
        raise HTTPException(
            400, "Type your email address exactly to confirm deletion.")

    db = get_db()
    # Every tenant table cascades from users.id, so this one delete takes the
    # ledger, the staged files, the dashboards and the Gmail grant with it.
    with db.identity_connection() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user.id,))
    log.info("deleted account %s and all of its data", user.email)

    response = JSONResponse({"status": "deleted"})
    clear_session_cookie(response)
    return response


# --------------------------------------------------------------------------
# Onboarding
# --------------------------------------------------------------------------

class StepUpdate(BaseModel):
    step: str


def _onboarding_state(user: User) -> dict[str, Any]:
    """Everything the wizard needs to draw itself, in one round trip.

    The flags are read from the world rather than remembered as the user
    clicks, so a step that was genuinely completed stays completed - and one
    that looked completed but was later undone (a Gmail grant revoked from
    Google's own settings page) stops claiming to be.
    """
    db = get_db()
    profile = repo.get_profile(db)
    gmail_connected = store.get_google_token(db, user.id) is not None
    staged = staging.counts(db)["total"]
    return {
        "user": user.public_json(),
        "steps": list(ONBOARDING_STEPS),
        "step": user.onboarding_step,
        "complete": user.onboarded,
        "identity": {
            "ready": profile.has_password_material(),
            "full_name": profile.full_name,
            "has_dob": bool(profile.date_of_birth),
            "has_pan": bool(profile.pan),
            "has_mobile": bool(profile.mobile),
        },
        "mailbox": {
            "available": config.google_configured,
            "connected": gmail_connected,
        },
        "import": {
            "staged_files": staged,
            "transactions": repo.count_transactions(db),
        },
    }


@onboarding_router.get("")
def read_onboarding(user: User = Depends(current_user)) -> dict[str, Any]:
    return _onboarding_state(user)


@onboarding_router.post("/step")
def advance(payload: StepUpdate,
            user: User = Depends(current_user)) -> dict[str, Any]:
    if payload.step not in ONBOARDING_STEPS:
        raise HTTPException(
            400, f"unknown step {payload.step!r}. "
                 f"Valid: {', '.join(ONBOARDING_STEPS)}")
    updated = store.set_onboarding_step(get_db(), user.id, payload.step) or user
    return _onboarding_state(updated)


@onboarding_router.post("/complete")
def complete(user: User = Depends(current_user)) -> dict[str, Any]:
    """Finish the wizard.

    Deliberately not gated on the earlier steps having produced anything.
    Every one of them is optional - the app works with no profile, no mailbox
    and no statements yet - and a setup flow that will not let you out is a
    worse experience than an empty dashboard.
    """
    updated = store.set_onboarding_step(get_db(), user.id, "done") or user
    return _onboarding_state(updated)


@onboarding_router.post("/reopen")
def reopen(user: User = Depends(current_user)) -> dict[str, Any]:
    """Run through setup again - from Settings, after the first time."""
    updated = store.reopen_onboarding(get_db(), user.id) or user
    return _onboarding_state(updated)
