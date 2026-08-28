"""Run the Gmail OAuth consent flow.

Prints the Google sign-in URL and waits for you to approve it in your browser.
Authentication happens entirely on Google's page - this script never sees, asks
for, or stores your Google password. What it receives back is a scoped,
read-only token, saved locally to data/gmail_token.json.

Run:  .venv/Scripts/python backend/tools/connect_gmail.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingestion.gmail_source import SCOPES  # noqa: E402

CREDENTIALS = ROOT / "credentials.json"
TOKEN = ROOT / "data" / "gmail_token.json"


def main() -> int:
    if not CREDENTIALS.exists():
        print(f"No credentials.json at {CREDENTIALS}")
        print("Run: .venv/Scripts/python backend/tools/check_gmail_setup.py")
        return 1

    if TOKEN.exists():
        print(f"Already connected. Token at {TOKEN}")
        print("To reconnect as a different account, delete that file first.")
        return 0

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)

    print("=" * 72)
    print("Requesting READ-ONLY Gmail access:")
    for scope in SCOPES:
        print(f"   {scope}")
    print()
    print("Open the URL below, sign in, and approve. Google may warn that the")
    print("app is unverified - that is expected for your own private app.")
    print("Choose 'Advanced' -> 'Go to <app name>' to continue.")
    print("=" * 72)
    print(flush=True)

    # open_browser=False so the URL is always printed, even in a headless or
    # remote shell where launching a browser would silently do nothing.
    creds = flow.run_local_server(
        port=0,
        open_browser=False,
        authorization_prompt_message="Sign in here:\n\n    {url}\n",
        success_message=(
            "Connected. You can close this tab and return to the app."
        ),
    )

    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json())
    try:
        TOKEN.chmod(0o600)  # credential file: restrict where the OS supports it
    except OSError:
        pass

    print()
    print(f"Connected. Token saved to {TOKEN}")
    print(f"Scopes granted: {', '.join(creds.scopes or SCOPES)}")
    print("To disconnect later, delete that file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
