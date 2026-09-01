"""Signing in with Google, and asking for Gmail read access afterwards.

One Google connection serves both. Signing in asks only for identity
(`openid email profile`); reading the mailbox is a *separate, later* grant
that the user makes deliberately during onboarding and can decline without
losing access to the app. That split is the point - "let me see who you are"
and "let me read your mail" are very different questions, and bundling them
into one consent screen at the front door would make the first one unanswerable
without also answering the second.

The flow is the authorization-code flow with PKCE. The `state` and the code
verifier live in the database (see store.py), never in the browser, so the
callback can prove it belongs to a redirect this server actually started, and
the code cannot be exchanged by anyone who merely observed it.

The previous Gmail integration used google-auth-oauthlib's `InstalledAppFlow`,
which opens a browser and starts a loopback listener *on the machine running
the code*. That was right for a program on someone's laptop and is wrong for a
server: it would pop a consent screen nobody can see, and there is only ever
one token file for one person. Both problems go away here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

from ..config import config

log = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

#: Who you are. Nothing else - see the module docstring.
SIGNIN_SCOPES = ("openid", "email", "profile")

#: Read-only. This is a hard ceiling: even a compromised token cannot send,
#: delete or modify mail. Mirrors ingestion.gmail_source.SCOPES.
GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)

REQUEST_TIMEOUT = 20


class GoogleAuthError(RuntimeError):
    """Google refused, or answered with something unusable."""


@dataclass(frozen=True)
class Identity:
    """The verified claims from an ID token."""

    sub: str
    email: str
    email_verified: bool
    name: str
    picture: str


@dataclass(frozen=True)
class Redirect:
    """A prepared trip to Google's consent screen."""

    url: str
    state: str
    code_verifier: str


def _require_client() -> tuple[str, str]:
    if not config.google_configured:
        raise GoogleAuthError(
            "Google sign-in is not configured. Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET - see the setup steps in the README.")
    return config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_redirect(purpose: str = "signin") -> Redirect:
    """Where to send the browser, and the secrets to remember for the callback.

    `purpose` picks the scopes. The Gmail trip additionally asks for offline
    access with `prompt=consent`, because a refresh token is only issued on a
    grant the user explicitly re-confirms - without it the import stops
    working an hour later and there is no way to renew it unattended.
    `include_granted_scopes` keeps the identity scopes already agreed to, so
    the second grant adds to the first rather than replacing it.
    """
    client_id, _ = _require_client()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)

    scopes = GMAIL_SCOPES if purpose == "gmail" else SIGNIN_SCOPES
    params = {
        "client_id": client_id,
        "redirect_uri": config.oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if purpose == "gmail":
        params.update({
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        })
    else:
        # Someone with two Google accounts should be asked which one, rather
        # than being silently signed into whichever their browser prefers.
        params["prompt"] = "select_account"

    return Redirect(url=f"{AUTH_ENDPOINT}?{urlencode(params)}",
                    state=state, code_verifier=verifier)


def exchange_code(code: str, code_verifier: str) -> dict[str, Any]:
    """Trade an authorization code for tokens."""
    client_id, client_secret = _require_client()
    try:
        response = requests.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": config.oauth_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GoogleAuthError(f"could not reach Google: {exc}") from exc

    if response.status_code != 200:
        # Google's error body names the actual problem (a redirect_uri that
        # does not match the registered one, most often), and guessing at it
        # from a bare 400 is how an afternoon disappears.
        raise GoogleAuthError(
            f"Google rejected the sign-in ({response.status_code}): "
            f"{_error_detail(response)}")
    return response.json()


def verify_id_token(raw_token: str) -> Identity:
    """Check an ID token's signature and audience, and read its claims.

    Verification is not optional decoration: without it, anything that can
    reach this endpoint could present a self-made token and become any user.
    google-auth fetches and caches Google's signing certificates and checks
    the signature, the issuer, the audience and the expiry.
    """
    client_id, _ = _require_client()
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    try:
        claims = google_id_token.verify_oauth2_token(
            raw_token, google_requests.Request(), client_id)
    except Exception as exc:                     # ValueError and friends
        raise GoogleAuthError(f"the ID token did not verify: {exc}") from exc

    subject = claims.get("sub")
    email = claims.get("email") or ""
    if not subject or not email:
        raise GoogleAuthError(
            "Google returned an identity with no subject or email address.")
    return Identity(
        sub=str(subject),
        email=email,
        email_verified=bool(claims.get("email_verified")),
        name=claims.get("name") or "",
        picture=claims.get("picture") or "",
    )


def credentials_json(token_response: dict[str, Any],
                     previous: str | None = None) -> str:
    """The token response as google-auth's own authorized-user JSON.

    Written in the shape `google.oauth2.credentials.Credentials` reads, so the
    Gmail client can be handed a stored grant with no translation.

    A re-grant that arrives without a refresh token keeps the one already
    held: Google only issues a refresh token on the first consent of a given
    scope set, and dropping the old one on a later grant is how an import that
    worked yesterday starts demanding a fresh sign-in every hour.
    """
    client_id, client_secret = _require_client()
    refresh_token = token_response.get("refresh_token")
    if not refresh_token and previous:
        try:
            refresh_token = json.loads(previous).get("refresh_token")
        except (TypeError, ValueError):
            refresh_token = None

    expiry = None
    if token_response.get("expires_in"):
        # google-auth parses this as a naive UTC datetime.
        expiry = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() + int(token_response["expires_in"])))

    return json.dumps({
        "token": token_response.get("access_token"),
        "refresh_token": refresh_token,
        "token_uri": TOKEN_ENDPOINT,
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": (token_response.get("scope") or "").split(),
        "expiry": expiry,
        "universe_domain": "googleapis.com",
    })


def revoke(token: str) -> None:
    """Ask Google to invalidate a grant. Best effort.

    Failing to reach Google must not stop us deleting our own copy - the local
    delete is the part that matters for the promise made in the UI, and the
    user can always finish the job at myaccount.google.com/permissions.
    """
    try:
        requests.post(REVOKE_ENDPOINT, data={"token": token},
                      timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        log.warning("could not revoke a Google token remotely: %s", exc)


def sign_in_allowed(email: str) -> bool:
    """Whether this address may have an account here.

    Empty allowlist means open sign-up, which is the right default for a tool
    someone runs for themselves. A deployment that is not open sets
    FA_ALLOWED_SIGNINS to addresses, or to @domains.
    """
    if not config.ALLOWED_SIGNINS:
        return True
    lowered = (email or "").lower()
    domain = "@" + lowered.split("@")[-1]
    return any(entry == lowered or entry == domain
               for entry in config.ALLOWED_SIGNINS)


def _error_detail(response) -> str:
    try:
        body = response.json()
        return (f"{body.get('error', '?')}: "
                f"{body.get('error_description', response.text[:200])}")
    except ValueError:
        return response.text[:200]
