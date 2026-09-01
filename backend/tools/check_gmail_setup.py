"""Check whether Google sign-in and Gmail import are set up, and say what to fix.

Run:  .venv/Scripts/python backend/tools/check_gmail_setup.py

Every failure mode here produces a specific instruction rather than a stack
trace, because the Google Cloud setup has several steps that each fail in a way
that looks identical from inside the app ("it just doesn't connect").

The single most common one now is the client TYPE. This used to want a
*Desktop* client, because the old Gmail import ran a loopback consent flow on
the user's own machine. It now wants a *Web application* client, because
sign-in and the mailbox grant both happen as a redirect through the browser -
and a Desktop client rejects the redirect URI with `invalid_request` and no
useful explanation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import config  # noqa: E402

OK, WARN, BAD = "[ OK ]", "[WARN]", "[FAIL]"


def main() -> int:
    print("Checking Google configuration\n")
    problems = 0

    if config.GOOGLE_CLIENT_ID:
        print(f"{OK}  GOOGLE_CLIENT_ID is set")
        if not config.GOOGLE_CLIENT_ID.endswith(".apps.googleusercontent.com"):
            print(f"{WARN}  ...but it does not look like a Google client id. "
                  f"They end in .apps.googleusercontent.com.")
    else:
        problems += 1
        print(f"{BAD}  GOOGLE_CLIENT_ID is not set")

    if config.GOOGLE_CLIENT_SECRET:
        print(f"{OK}  GOOGLE_CLIENT_SECRET is set")
    else:
        problems += 1
        print(f"{BAD}  GOOGLE_CLIENT_SECRET is not set")

    print(f"{OK}  Redirect URI: {config.oauth_redirect_uri}")
    print("       This exact string must appear under 'Authorised redirect "
          "URIs' on the OAuth client. Google matches it character for "
          "character - a trailing slash or http vs https is a mismatch.")

    if config.APP_BASE_URL.startswith("http://") \
            and "localhost" not in config.APP_BASE_URL \
            and "127.0.0.1" not in config.APP_BASE_URL:
        problems += 1
        print(f"{BAD}  FA_APP_BASE_URL is plain http on a non-local host. "
              f"Google refuses non-https redirect URIs, and a session cookie "
              f"for a financial app should never travel unencrypted.")

    if config.APP_BASE_URL.startswith("https://") \
            and not config.SESSION_COOKIE_SECURE:
        print(f"{WARN}  The app is served over https but "
              f"FA_SESSION_COOKIE_SECURE is off. Turn it on.")

    if config.ALLOWED_SIGNINS:
        print(f"{OK}  Sign-up is restricted to: "
              f"{', '.join(config.ALLOWED_SIGNINS)}")
    else:
        print(f"{WARN}  Sign-up is open: anyone with a Google account can "
              f"create an account here. Set FA_ALLOWED_SIGNINS to restrict it.")

    print()
    if problems:
        print(f"{problems} thing(s) to fix. In Google Cloud Console:")
        print("  1. APIs & Services -> Library -> enable the Gmail API")
        print("  2. OAuth consent screen -> External, add yourself as a test user")
        print("  3. Credentials -> Create credentials -> OAuth client ID")
        print("     Application type: WEB APPLICATION  <- not Desktop")
        print(f"     Authorised redirect URI: {config.oauth_redirect_uri}")
        print("  4. Put the client id and secret in .env as GOOGLE_CLIENT_ID "
              "and GOOGLE_CLIENT_SECRET")
        return 1

    print("Configuration looks right. Open the app, sign in, and connect Gmail")
    print("from the setup wizard or the import screen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
