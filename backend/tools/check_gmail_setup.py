"""Check whether Gmail import is set up correctly, and say what to fix.

Run:  .venv/Scripts/python backend/tools/check_gmail_setup.py

Every failure mode here produces a specific instruction rather than a stack
trace, because the Google Cloud setup has several steps that each fail in a
way that looks identical from inside the app ("it just doesn't connect").
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS = ROOT / "credentials.json"
TOKEN = ROOT / "data" / "gmail_token.json"

OK, WARN, BAD = "[ OK ]", "[WARN]", "[FAIL]"


def main() -> int:
    print(f"Looking for credentials at: {CREDENTIALS}\n")

    if not CREDENTIALS.exists():
        print(f"{BAD} credentials.json not found.")
        print()
        print("  This file comes from Google Cloud Console. It identifies THIS APP")
        print("  to Google - it is not your password and contains no personal data.")
        print()
        print("  See the 'Connecting Gmail' section of the README for the steps.")
        print(f"  Save the downloaded file to exactly: {CREDENTIALS}")
        return 1

    try:
        data = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{BAD} credentials.json is not valid JSON: {exc}")
        print("  Re-download it from Google Cloud Console without editing it.")
        return 1

    # A Desktop OAuth client is stored under "installed". A Web client uses
    # "web" and will fail at the redirect step with an opaque error, so it is
    # worth catching here rather than at connect time.
    if "installed" in data:
        client = data["installed"]
        print(f"{OK} Found a Desktop OAuth client (correct type).")
    elif "web" in data:
        client = data["web"]
        print(f"{BAD} This is a WEB application client, not a Desktop client.")
        print("  The local sign-in flow needs a Desktop client.")
        print("  In Google Cloud Console create a new OAuth client and choose")
        print("  application type 'Desktop app', then replace this file.")
        return 1
    elif "type" in data and data.get("type") == "service_account":
        print(f"{BAD} This is a SERVICE ACCOUNT key, not an OAuth client.")
        print("  A service account cannot read your personal Gmail. Create an")
        print("  OAuth client ID with application type 'Desktop app' instead.")
        return 1
    else:
        print(f"{BAD} Unrecognised credentials file.")
        print(f"  Top-level keys found: {sorted(data.keys())}")
        print("  Expected a key named 'installed' (a Desktop OAuth client).")
        return 1

    missing = [k for k in ("client_id", "client_secret", "auth_uri", "token_uri")
               if not client.get(k)]
    if missing:
        print(f"{BAD} The client is missing required fields: {', '.join(missing)}")
        print("  Re-download the JSON from Google Cloud Console.")
        return 1

    client_id = client["client_id"]
    print(f"{OK} client_id looks well-formed ({client_id[:14]}…{client_id[-14:]})")

    if not client_id.endswith(".apps.googleusercontent.com"):
        print(f"{WARN} client_id doesn't end in .apps.googleusercontent.com -")
        print("       double-check you downloaded the right file.")

    # Token state
    print()
    if TOKEN.exists():
        try:
            token = json.loads(TOKEN.read_text(encoding="utf-8"))
            scopes = token.get("scopes", [])
            print(f"{OK} Already connected. Token stored at {TOKEN}")
            print(f"       Scopes granted: {', '.join(scopes) or '(none listed)'}")
            if any("readonly" not in s for s in scopes):
                print(f"{WARN} A non-read-only scope is present. Delete the token")
                print("       and reconnect to reset it to read-only.")
            print()
            print("  Gmail import is ready. Open the app and use 'Scan for statements'.")
            print(f"  To disconnect: delete {TOKEN}")
        except json.JSONDecodeError:
            print(f"{WARN} Token file exists but is unreadable. Delete it and reconnect:")
            print(f"       {TOKEN}")
    else:
        print(f"{OK} Setup looks correct. Not connected yet.")
        print()
        print("  Next: start the app and click 'Connect Gmail'. A Google sign-in")
        print("  page opens in your browser - approve it there. Nothing in this")
        print("  app ever sees your Gmail password.")
        print()
        print("  If Google warns the app is unverified, that is expected: it is")
        print("  your own private app. Choose Advanced -> Go to <app name>.")

    # The Gmail API itself must be enabled on the project; that can only be
    # confirmed by an actual call, so flag it as the usual next failure.
    print()
    print("  If connecting fails with 'Gmail API has not been used in project…',")
    print("  enable the Gmail API for that project in the Google Cloud Console")
    print("  API Library, wait a minute, then try again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
