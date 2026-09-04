"""Sign-in, sessions, onboarding, and the isolation they exist to provide.

The through-line: this app now holds several people's complete financial
histories in one database. Every test here is about a way that could go wrong -
an endpoint reachable without a session, a session that outlives a sign-out, a
query that sees rows it should not, an OAuth callback that can be forged or
replayed.
"""

from __future__ import annotations

import json

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from tests.conftest import anonymous  # noqa: E402
from tests.support import fresh_ledger, make_user  # noqa: E402
from app.auth import google, store  # noqa: E402
from app.config import config  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.db.database import get_db  # noqa: E402
from app.db.engine import TENANT, IsolationError, tenant_scope  # noqa: E402
from app.models.schemas import (Account, AccountType, Category,  # noqa: E402
                                Direction, Transaction)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def _account(db, institution: str) -> str:
    return repo.upsert_account(db, Account(
        institution=institution, account_type=AccountType.SAVINGS,
        account_number_masked="XXXX1111"))


def _spend(db, account_id: str, amount: str, description: str) -> None:
    repo.save_transactions(db, [Transaction(
        account_id=account_id, txn_date=date(2026, 3, 4),
        raw_description=description, amount=Decimal(amount),
        direction=Direction.DEBIT, category=Category.GROCERIES,
        fingerprint=description)])


# --------------------------------------------------------------------------
# The API is closed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/dashboard", "/api/accounts", "/api/transactions", "/api/statements",
    "/api/files", "/api/recurring", "/api/data/inventory", "/api/profile",
    "/api/gmail/status", "/api/onboarding", "/api/query/schema",
])
def test_every_data_endpoint_refuses_an_anonymous_caller(client, path):
    """Protection is a property of the middleware, not of each route.

    Listed one endpoint at a time because the failure being guarded against is
    a single route slipping out from behind the wall - which is invisible
    until somebody's statements are served to a stranger.
    """
    with anonymous():
        response = client.get(path)
    assert response.status_code == 401, path
    assert response.json()["code"] == "not_authenticated"


@pytest.mark.parametrize("path", ["/api/health", "/api/auth/config",
                                  "/api/auth/session"])
def test_the_public_endpoints_stay_reachable(client, path):
    """Health for a load balancer; the auth pair because signing in cannot
    require being signed in."""
    with anonymous():
        assert client.get(path).status_code == 200


def test_an_unknown_api_path_is_refused_before_it_404s(client):
    """Closed by default means a route nobody has written yet is also closed -
    which is what stops a new endpoint being born unprotected."""
    with anonymous():
        assert client.get("/api/something-nobody-has-built").status_code == 401


def test_health_reports_no_transactions_to_an_anonymous_caller(client):
    """A public endpoint that touches the ledger must see nothing.

    Not a special case in the handler - the row-level security policy matches
    no rows when no tenant is bound, so the honest answer falls out.
    """
    _spend(get_db(), _account(get_db(), "HDFC"), "500.00", "SWIGGY")
    with anonymous():
        body = client.get("/api/health").json()
    assert body["transactions_stored"] == 0


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------

def test_two_accounts_cannot_see_each_others_ledgers():
    mine = fresh_ledger()
    _spend(mine, _account(mine, "HDFC"), "1200.00", "MY GROCERIES")

    theirs = fresh_ledger()
    _spend(theirs, _account(theirs, "ICICI"), "999.00", "THEIR GROCERIES")

    assert [t.raw_description for t in repo.get_transactions(theirs)] == \
        ["THEIR GROCERIES"]
    assert repo.count_transactions(theirs) == 1


def test_the_api_serves_each_signed_in_user_only_their_own_rows(client):
    """The same assertion, through HTTP, because that is where it matters."""
    mine = get_db()
    _spend(mine, _account(mine, "HDFC"), "1200.00", "MY GROCERIES")
    my_rows = client.get("/api/transactions").json()

    fresh_ledger()   # rebinds the tenant; the same client, a different session
    their_rows = client.get("/api/transactions").json()

    assert [t["description"] for t in my_rows["transactions"]] == \
        ["MY GROCERIES"]
    assert their_rows["transactions"] == []


def test_a_query_with_no_tenant_bound_returns_nothing_rather_than_everything():
    """The fail-closed direction. If the tenant is ever lost - a background
    thread that did not inherit it, a bug in the middleware - the result has
    to be an empty screen, never somebody else's money."""
    db = get_db()
    _spend(db, _account(db, "HDFC"), "1200.00", "GROCERIES")

    token = TENANT.set(None)
    try:
        assert repo.count_transactions(db) == 0
        assert repo.get_accounts(db) == []
    finally:
        TENANT.reset(token)


def test_writing_with_no_tenant_bound_is_refused_rather_than_silently_dropped():
    db = get_db()
    with tenant_scope(None):
        with pytest.raises(Exception) as caught:
            with db.connection() as conn:
                conn.execute(
                    "INSERT INTO accounts (id, institution) VALUES ('x', 'Ghost')")
    # Refused by the policy's WITH CHECK half - the row it would write has a
    # NULL owner, which is not `user_id = current_tenant()` for anybody.
    assert "row-level security" in str(caught.value)


def test_a_snapshot_needs_a_signed_in_user():
    """Snapshots are per person now; there is no whole-database copy to take."""
    db = get_db()
    with tenant_scope(None):
        with pytest.raises(IsolationError):
            db.snapshot("nobody")


def test_deleting_an_account_takes_its_whole_ledger_with_it(client):
    db = get_db()
    _spend(db, _account(db, "HDFC"), "1200.00", "GROCERIES")
    user = store.get_user(db, TENANT.get())

    response = client.post("/api/auth/delete-account",
                           json={"confirm_email": user.email})
    assert response.status_code == 200
    assert store.get_user(db, user.id) is None
    with db.identity_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id = %s",
            (user.id,)).fetchone()[0] == 0


def test_deleting_an_account_requires_typing_the_address(client):
    response = client.post("/api/auth/delete-account",
                           json={"confirm_email": "someone@else.com"})
    assert response.status_code == 400
    assert store.get_user(get_db(), TENANT.get()) is not None


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

def test_a_session_resolves_to_its_user_and_a_wrong_token_does_not():
    db = get_db()
    user = make_user()
    token = store.create_session(db, user.id, ttl_hours=1)

    assert store.resolve_session(db, token).id == user.id
    assert store.resolve_session(db, token + "x") is None
    assert store.resolve_session(db, "") is None


def test_only_the_hash_of_a_session_token_is_stored():
    """A leaked backup must not hand over live sessions."""
    db = get_db()
    user = make_user()
    token = store.create_session(db, user.id, ttl_hours=1)

    with db.identity_connection() as conn:
        stored = conn.execute(
            "SELECT token_hash FROM user_sessions WHERE user_id = %s",
            (user.id,)).fetchone()["token_hash"]
    assert token not in stored
    assert stored == store.hash_token(token)


def test_an_expired_session_stops_working():
    db = get_db()
    user = make_user()
    token = store.create_session(db, user.id, ttl_hours=1)
    with db.identity_connection() as conn:
        conn.execute(
            "UPDATE user_sessions SET expires_at = '2000-01-01 00:00:00'"
            " WHERE token_hash = %s", (store.hash_token(token),))
    assert store.resolve_session(db, token) is None


def test_signing_out_ends_that_session_immediately(client):
    """The reason sessions are server-side rather than a self-contained token:
    a JWT cannot be withdrawn before it expires."""
    from tests.conftest import session_cookie

    db = get_db()
    token = session_cookie(TENANT.get())     # the one this client is using
    assert store.resolve_session(db, token) is not None

    assert client.post("/api/auth/logout").status_code == 200
    assert store.resolve_session(db, token) is None


def test_signing_out_everywhere_ends_every_session(client):
    db = get_db()
    tokens = [store.create_session(db, TENANT.get(), ttl_hours=1)
              for _ in range(3)]

    assert client.post("/api/auth/logout-all").status_code == 200
    assert all(store.resolve_session(db, t) is None for t in tokens)


def test_a_disabled_account_cannot_use_a_session_it_already_had():
    db = get_db()
    user = make_user()
    token = store.create_session(db, user.id, ttl_hours=1)
    with db.identity_connection() as conn:
        conn.execute("UPDATE users SET status = 'disabled' WHERE id = %s",
                     (user.id,))
    assert store.resolve_session(db, token) is None


def test_the_session_cookie_is_httponly_and_samesite_lax(client):
    """Not readable by script, and not sent on a cross-site POST - but still
    present on the top-level redirect back from Google's consent screen."""
    from app.auth.session import set_session_cookie
    from starlette.responses import JSONResponse

    response = JSONResponse({})
    set_session_cookie(response, "a-token")
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "samesite=lax" in header.lower()
    assert config.SESSION_COOKIE in header


# --------------------------------------------------------------------------
# The Google round trip
# --------------------------------------------------------------------------

@pytest.fixture()
def google_configured(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "client-secret")


def test_the_redirect_asks_only_for_identity_when_signing_in(google_configured):
    """Reading someone's mail is a separate question, asked later and
    separately - not bundled into the front door."""
    trip = google.build_redirect("signin")
    assert "scope=openid+email+profile" in trip.url
    assert "gmail" not in trip.url
    assert "code_challenge_method=S256" in trip.url


def test_the_gmail_grant_asks_offline_so_the_import_can_refresh(google_configured):
    trip = google.build_redirect("gmail")
    assert "gmail.readonly" in trip.url
    assert "access_type=offline" in trip.url
    assert "include_granted_scopes=true" in trip.url


def test_the_pkce_verifier_never_travels_to_the_browser(google_configured):
    trip = google.build_redirect("signin")
    assert trip.code_verifier not in trip.url
    assert trip.state in trip.url


def test_a_callback_with_an_unknown_state_is_refused(client, google_configured):
    with anonymous():
        response = client.get("/api/auth/google/callback",
                              params={"code": "abc", "state": "never-issued"},
                              follow_redirects=False)
    assert response.status_code == 303
    assert "auth_error" in response.headers["location"]


def test_a_callback_cannot_be_replayed(client, google_configured):
    """The state row is deleted as it is read, so an authorization code that
    leaked into a log or a referrer header cannot be spent twice."""
    db = get_db()
    store.save_oauth_state(db, state="s1", code_verifier="v1",
                           purpose="signin", redirect_to="/")
    assert store.take_oauth_state(db, "s1") is not None
    assert store.take_oauth_state(db, "s1") is None


def test_an_expired_redirect_is_not_honoured():
    db = get_db()
    store.save_oauth_state(db, state="s2", code_verifier="v2",
                           purpose="signin", redirect_to="/")
    with db.identity_connection() as conn:
        conn.execute("UPDATE oauth_states SET created_at = '2000-01-01 00:00:00'"
                     " WHERE state = %s", ("s2",))
    assert store.take_oauth_state(db, "s2") is None


@pytest.mark.parametrize("target,expected", [
    ("/spending", "/spending"),
    ("https://evil.example/steal", "/"),
    ("//evil.example/steal", "/"),
    ("", "/"),
    (None, "/"),
])
def test_the_callback_will_not_redirect_off_this_origin(target, expected):
    """An open redirect here is how a convincing phishing link gets built: the
    address bar shows this app right up until Google hands control back."""
    from app.api.auth_routes import _safe_redirect

    assert _safe_redirect(target) == expected


def test_signing_in_is_refused_when_google_is_not_configured(client, monkeypatch):
    # Cleared explicitly rather than assumed absent: `config` is loaded from
    # the developer's own .env, so on any machine that has actually set up
    # Google sign-in this asserted the opposite of what it reads and failed.
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")

    with anonymous():
        response = client.get("/api/auth/google/start", follow_redirects=False)
    assert response.status_code == 503
    with anonymous():
        assert client.get("/api/auth/config").json()["configured"] is False


def test_an_allowlist_keeps_strangers_out(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_SIGNINS", ("pankaj@acme.com", "@work.example"))
    assert google.sign_in_allowed("pankaj@acme.com")
    assert google.sign_in_allowed("anyone@work.example")
    assert not google.sign_in_allowed("stranger@example.com")


def test_an_empty_allowlist_lets_anyone_sign_up(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_SIGNINS", ())
    assert google.sign_in_allowed("stranger@example.com")


def test_a_returning_google_identity_lands_in_the_same_account():
    """Keyed on `sub`, not on the email address: Workspace addresses get
    renamed, and personal ones get recycled."""
    db = get_db()
    first = store.upsert_user(db, google_sub="stable-sub", email="old@example.com",
                              email_verified=True, name="A", picture="")
    again = store.upsert_user(db, google_sub="stable-sub", email="new@example.com",
                              email_verified=True, name="A", picture="")
    assert first.id == again.id
    assert again.email == "new@example.com"


def test_a_re_grant_without_a_refresh_token_keeps_the_one_already_held(
        google_configured):
    """Google issues a refresh token only on the first consent. Dropping the
    stored one on a later grant is how an import that worked yesterday starts
    demanding a fresh sign-in every hour."""
    previous = google.credentials_json(
        {"access_token": "old", "refresh_token": "keep-me", "expires_in": 3600,
         "scope": " ".join(google.GMAIL_SCOPES)})
    updated = google.credentials_json(
        {"access_token": "new", "expires_in": 3600,
         "scope": " ".join(google.GMAIL_SCOPES)}, previous)

    import json
    assert json.loads(updated)["refresh_token"] == "keep-me"
    assert json.loads(updated)["token"] == "new"


# --------------------------------------------------------------------------
# The Gmail grant is per user
# --------------------------------------------------------------------------

def test_one_users_gmail_grant_is_invisible_to_another(client):
    db = get_db()
    store.save_google_token(db, TENANT.get(), '{"token": "mine"}', "gmail.readonly")
    assert client.get("/api/gmail/status").json()["connected"] is True

    fresh_ledger()
    assert client.get("/api/gmail/status").json()["connected"] is False


def test_the_gmail_client_reads_the_signed_in_users_stored_grant():
    """No credentials.json, no token file, no per-user Google Cloud setup.

    The importer is handed a token store that resolves to whoever is signed
    in, so scanning and downloading run on that person's grant and nobody
    else's. This is the whole of what replaced the desktop OAuth flow.
    """
    from app.api.gmail_routes import _client

    db = get_db()
    assert _client().is_authorized() is False       # nothing granted yet

    store.save_google_token(db, TENANT.get(), '{"token": "mine"}',
                            " ".join(google.GMAIL_SCOPES))
    assert _client().is_authorized() is True

    # And it is genuinely per user, not a shared file.
    fresh_ledger()
    assert _client().is_authorized() is False


def test_the_gmail_client_refuses_to_run_a_consent_flow():
    """`authorize()` must never block on a browser.

    The old client ran a loopback consent flow on the machine executing the
    code, which on a server means popping a screen nobody can see. With no
    stored grant the answer is now simply False.
    """
    from app.api.gmail_routes import _client

    assert _client().authorize(interactive=True) is False


def test_an_unreadable_stored_grant_does_not_crash_the_import():
    from app.api.gmail_routes import _client

    store.save_google_token(get_db(), TENANT.get(), "not json at all", "")
    assert _client().authorize() is False


def _expired_token(refresh_token: str = "rt") -> str:
    """A stored grant whose access token has already expired."""
    return json.dumps({
        "token": "at", "refresh_token": refresh_token,
        "client_id": "cid", "client_secret": "sec",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": list(google.GMAIL_SCOPES),
        "expiry": "2020-01-01T00:00:00Z",
    })


def test_a_revoked_grant_is_forgotten_rather_than_raising(monkeypatch):
    """The weekly event on an External + Testing OAuth app.

    Such a project is issued refresh tokens that expire after seven days, so
    every user's grant dies on a schedule. That used to surface as an
    unhandled RefreshError - a 500 from whichever endpoint happened to ask -
    and the import screen went on reporting "connected" against a token
    Google would never honour again.
    """
    from google.auth.exceptions import RefreshError
    from app.api.gmail_routes import _client

    db = get_db()
    store.save_google_token(db, TENANT.get(), _expired_token(),
                            " ".join(google.GMAIL_SCOPES))

    def _dead(self, request):
        raise RefreshError("('invalid_grant: Token has been expired or revoked.')")

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", _dead)

    assert _client().authorize() is False
    # Forgotten, so the screen offers to reconnect instead of lying.
    assert store.get_google_token(db, TENANT.get()) is None
    assert _client().is_authorized() is False


def test_a_transient_refresh_failure_keeps_the_grant(monkeypatch):
    """Google being briefly unreachable must not cost the user a re-consent.

    Both outcomes arrive as the same exception type, so the difference is read
    from the payload - and anything unrecognised is treated as transient,
    because discarding a working grant is the more expensive mistake.
    """
    from google.auth.exceptions import RefreshError
    from app.api.gmail_routes import _client

    db = get_db()
    store.save_google_token(db, TENANT.get(), _expired_token(),
                            " ".join(google.GMAIL_SCOPES))

    def _blip(self, request):
        raise RefreshError("HTTPSConnectionPool: Read timed out.")

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", _blip)

    assert _client().authorize() is False
    assert store.get_google_token(db, TENANT.get()) is not None


def test_a_grant_that_still_refreshes_is_kept_and_rewritten(monkeypatch):
    from app.api.gmail_routes import _client

    db = get_db()
    store.save_google_token(db, TENANT.get(), _expired_token(),
                            " ".join(google.GMAIL_SCOPES))

    def _renew(self, request):
        self.token = "fresh-access-token"
        self.expiry = None          # google-auth reads None as "not expired"

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", _renew)
    monkeypatch.setattr("app.ingestion.gmail_source.GoogleGmailClient._build",
                        lambda self, creds: None)

    assert _client().authorize() is True
    assert "fresh-access-token" in (store.get_google_token(db, TENANT.get()) or "")


def test_disconnecting_gmail_forgets_the_grant(client, monkeypatch):
    monkeypatch.setattr(google, "revoke", lambda token: None)
    db = get_db()
    store.save_google_token(db, TENANT.get(), '{"token": "mine"}', "gmail.readonly")

    assert client.post("/api/gmail/disconnect").status_code == 200
    assert store.get_google_token(db, TENANT.get()) is None


# --------------------------------------------------------------------------
# Onboarding
# --------------------------------------------------------------------------

def test_a_new_account_starts_at_the_first_step(client):
    body = client.get("/api/onboarding").json()
    assert body["step"] == "identity"
    assert body["complete"] is False
    assert body["identity"]["ready"] is False


def test_the_wizard_reports_what_has_actually_been_done(client):
    """Read from the world rather than remembered as the user clicks, so a
    step that was genuinely completed stays completed - and one that was later
    undone stops claiming to be."""
    from app.models.profile import UserProfile

    repo.save_profile(get_db(), UserProfile(
        full_name="Pankaj K", date_of_birth=date(1988, 7, 14), pan="ABCDE1234F"))

    body = client.get("/api/onboarding").json()
    assert body["identity"]["ready"] is True
    assert body["identity"]["full_name"] == "Pankaj K"
    assert body["identity"]["has_dob"] is True


def test_finishing_the_wizard_sticks_across_a_reload(client):
    assert client.post("/api/onboarding/complete").json()["complete"] is True
    assert client.get("/api/onboarding").json()["complete"] is True
    assert client.get("/api/auth/session").json()["user"]["onboarded"] is True


def test_the_wizard_can_be_finished_with_nothing_filled_in(client):
    """Every step is optional - the app works with no profile, no mailbox and
    no statements. A setup flow that will not let you out is worse than an
    empty dashboard."""
    assert client.post("/api/onboarding/complete").status_code == 200


def test_the_step_only_ever_moves_forward(client):
    """Revisiting an earlier screen to correct a detail is not un-onboarding
    yourself, and the next sign-in should land where you actually left off."""
    client.post("/api/onboarding/step", json={"step": "import"})
    body = client.post("/api/onboarding/step", json={"step": "identity"}).json()
    assert body["step"] == "import"


def test_an_unknown_step_is_refused(client):
    assert client.post("/api/onboarding/step",
                       json={"step": "profit"}).status_code == 400


def test_setup_can_be_run_again_from_settings(client):
    client.post("/api/onboarding/complete")
    body = client.post("/api/onboarding/reopen").json()
    assert body["complete"] is False
    assert body["step"] == "identity"
