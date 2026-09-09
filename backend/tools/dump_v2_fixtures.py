"""Record what the API actually answers, as fixtures for the v2 front-end tests.

Run:  python backend/tools/dump_v2_fixtures.py

The v2 test suite drives every screen against a fake server (see
`frontend-v2/test/harness.jsx`). A fake server is only worth anything if it
answers in the shapes the real one does, and a hand-written fixture drifts the
moment an endpoint gains a field or renames one - quietly, because the fake
keeps answering the old shape and the tests keep passing while the app breaks.

So the fixtures are not hand-written. This script seeds a throwaway workspace
with the demo ledger, drives the real FastAPI app through every GET the v2
front-end makes, and writes the answers to
`frontend-v2/test/fixtures/api.json`. `backend/tests/test_v2_contract.py`
re-runs the same capture and fails when the committed fixtures no longer carry
the same keys as the live API, so drift is a red test rather than a silent one.

Needs a PostgreSQL to talk to, and an ORDINARY role to talk to it as - the app
refuses to run as a superuser, because PostgreSQL exempts superusers from the
row-level security every tenant boundary in this app is built on.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

#: Every GET the v2 front-end makes, with the parameters it sends. Keep this in
#: step with `frontend-v2/src/core/api.js`; the contract test asserts that every
#: entry still answers, which is what catches a route renamed out from under it.
GETS: list[str] = [
    "/api/auth/session",
    "/api/auth/config",
    "/api/onboarding",
    "/api/health",
    "/api/dashboard",
    "/api/periods",
    "/api/analysis?preset=all",
    "/api/budget?preset=all",
    "/api/accounts",
    "/api/categories",
    "/api/categories/custom",
    "/api/statements",
    "/api/workflow",
    "/api/transactions?limit=1000&sort_by=date&sort_dir=desc",
    "/api/claims",
    "/api/recurring",
    "/api/files",
    "/api/coverage",
    "/api/data/inventory",
    "/api/profile",
    "/api/settings",
    "/api/settings/demo",
    "/api/rules",
    "/api/bureau",
    "/api/bureau/reconciliation",
    "/api/portfolio",
    "/api/position",
    "/api/position/mappable",
    "/api/position/snapshots",
    "/api/agents",
    "/api/query/schema",
    "/api/dashboards",
    "/api/dashboards/templates",
    "/api/gmail/status",
    "/api/gmail/periods",
    "/api/gmail/intents",
    "/api/gmail/ignored",
    "/api/jobs",
    "/api/staging/review",
    "/api/staging/sections",
]

OUT = ROOT / "frontend-v2" / "test" / "fixtures" / "api.json"


def capture(database_url: str, data_dir: str) -> dict[str, object]:
    """Seed a demo workspace and record every GET in `GETS`."""
    os.environ["FA_DATABASE_URL"] = database_url
    os.environ["FA_DATA_DIR"] = data_dir

    from app.config import config

    config.DATABASE_URL = database_url
    config.DATA_DIR = data_dir

    from app.db import database

    database.DATA_DIR = Path(data_dir)
    database._db = database.Database(database_url)

    from fastapi.testclient import TestClient

    from app import demo
    from app.auth import store
    from app.db.database import get_db
    from app.db.engine import TENANT
    from app.main import app

    db = get_db()
    suffix = uuid.uuid4().hex[:12]
    user = store.upsert_user(
        db, google_sub=f"v2-fixtures-{suffix}",
        email=f"v2-fixtures-{suffix}@example.com", email_verified=True,
        name="Ada Lovelace", picture="")
    TENANT.set(user.id)
    # The demo ledger rather than a handful of hand-made rows: it is the app's
    # own output over generated input, so a screen that renders it is rendering
    # something the pipeline could really produce.
    demo.seed(db, user.id)

    client = TestClient(app)
    client.cookies.set(config.SESSION_COOKIE, store.create_session(db, user.id, ttl_hours=6))
    populate(client)

    captured: dict[str, object] = {}
    for path in GETS:
        response = client.get(path)
        if response.status_code != 200:
            raise SystemExit(f"{path} answered {response.status_code}: {response.text[:400]}")
        captured[path] = response.json()

    # Two answers that only exist once something has been created, and whose
    # URLs carry an id that changes on every capture. They are filed under
    # stable keys the fake server reads for any id, because what the test needs
    # from them is the SHAPE - a board with widgets, and a run of it.
    boards = captured.get("/api/dashboards") or []
    if boards:
        board_id = boards[0]["id"]
        detail = client.get(f"/api/dashboards/{board_id}")
        if detail.status_code == 200:
            captured["dashboard:detail"] = detail.json()
        run = client.post(f"/api/dashboards/{board_id}/run", json={})
        if run.status_code == 200:
            captured["dashboard:run"] = run.json()

    # One widget's result, as the editor's preview asks for it.
    board = captured.get("dashboard:detail") or {}
    widgets = board.get("widgets") or []
    if widgets:
        query = client.post("/api/query", json={"query": widgets[0]["query"], "board": None})
        if query.status_code == 200:
            captured["query:result"] = query.json()

    return captured


def populate(client) -> None:
    """Give the screens whose lists start empty something real to render.

    A demo ledger alone leaves Owed, Position and Explore with nothing in them,
    so a front-end test over those fixtures can only ever assert the empty
    state. Each of these goes through the API rather than the database, so what
    ends up in the fixture is exactly what the app would have created.
    """
    # Finished setting up. The fixtures stand in for an account somebody has
    # been using, and an unfinished wizard would put every screen behind it.
    client.post("/api/onboarding/complete")

    # Something owed: mark one ordinary card purchase as somebody else's.
    rows = client.get("/api/transactions?limit=200&sort_by=amount&sort_dir=desc").json()
    candidate = next(
        (t for t in rows["transactions"]
         if t["direction"] == "debit" and not t["is_internal_transfer"]), None)
    if candidate:
        client.post(f"/api/transactions/{candidate['id']}/claim", json={
            "counterparty": "Priya", "direction": "owed_to_me",
            "amount": candidate["amount"],
        })

    # A position built from what the ledger already knows, so the screen has
    # loans and cards to age forward rather than an empty table.
    client.post("/api/position/seed")

    # One saved dashboard, from a real template.
    templates = client.get("/api/dashboards/templates").json()
    if templates:
        client.post("/api/dashboards", json={
            "name": "My board", "template": templates[0]["key"],
        })

    # A category the user added, so Settings can tell built-ins from custom.
    client.post("/api/categories", json={"name": "Hobbies"})


def _scrub(payload: dict[str, object]) -> dict[str, object]:
    """Replace the identifiers that change on every capture.

    Without this every run rewrites the file with new uuids and the diff says
    nothing about whether the API changed.
    """
    text = json.dumps(payload, sort_keys=True)
    session = payload.get("/api/auth/session") or {}
    user = (session.get("user") or {}) if isinstance(session, dict) else {}
    replacements = {
        str(user.get("id") or ""): "00000000-0000-4000-8000-000000000001",
        str(user.get("email") or ""): "ada@example.com",
    }
    for actual, stable in replacements.items():
        if actual:
            text = text.replace(actual, stable)
    return json.loads(text)


def main() -> None:
    url = os.environ.get("FA_FIXTURE_DATABASE_URL")
    if not url:
        raise SystemExit(
            "set FA_FIXTURE_DATABASE_URL to a database owned by an ordinary "
            "(non-superuser) role, e.g. "
            "postgresql://financial_agent:...@localhost:5432/financial_agent_fixtures")
    data_dir = os.environ.get("FA_FIXTURE_DATA_DIR", "/tmp/fa-v2-fixtures")
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    captured = _scrub(capture(url, data_dir))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(captured, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(captured)} endpoints, {OUT.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()
