"""The alert scan and import, through HTTP with a fake mailbox.

Covers the wiring rather than the parsing: that the scan writes nothing, that
the import writes only what was chosen, and that the stage the UI derives from
the job is the one that matches what actually happened.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from tests.support import fresh_ledger  # noqa: E402
from app.api import gmail_routes  # noqa: E402
from app.db import database, repository as repo  # noqa: E402
from app.jobs import jobs  # noqa: E402
from app.main import app  # noqa: E402
from app.models.schemas import Account, AccountType  # noqa: E402

ALERTS = [
    ("alerts@hdfcbank.net", "Transaction alert",
     "Rs.1,250.00 debited from A/c XX1234 on 15-Aug-2026 to VPA swiggy@ybl."),
    ("alerts@hdfcbank.net", "Transaction alert",
     "Rs.4,999.00 debited from A/c XX1234 on 16-Aug-2026 to VPA amazon@apl."),
    # An account nothing here knows about.
    ("alerts@kotak.com", "Transaction alert",
     "Rs.700.00 debited from A/c XX8890 on 16-Aug-2026 to VPA shop@ybl."),
    # Not a transaction at all.
    ("alerts@hdfcbank.net", "Payment reminder",
     "Your credit card payment of Rs 12,000 is due on 20-Aug-2026."),
]


class FakeAlertMailbox:
    """A Gmail client whose messages carry bodies rather than attachments."""

    def __init__(self, alerts):
        self._messages = {}
        for index, (sender, subject, body) in enumerate(alerts):
            encoded = base64.urlsafe_b64encode(body.encode()).decode()
            self._messages[f"alert{index}"] = {
                "id": f"alert{index}",
                "payload": {
                    "headers": [
                        {"name": "From", "value": sender},
                        {"name": "Subject", "value": subject},
                        {"name": "Date",
                         "value": "Sat, 16 Aug 2026 09:00:00 +0530"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": encoded},
                },
            }

    def list_messages(self, query, max_results):
        return list(self._messages)[:max_results]

    def get_message(self, message_id):
        return self._messages[message_id]

    def get_attachment(self, message_id, attachment_id):  # pragma: no cover
        raise AssertionError("an alert scan must never fetch an attachment")


@pytest.fixture()
def db(monkeypatch, tmp_path):
    fresh = fresh_ledger()
    monkeypatch.setattr(database, "_db", fresh)
    repo.upsert_account(fresh, Account(
        id="acc-hdfc", institution="HDFC Bank",
        account_type=AccountType.SAVINGS, account_number_masked="XXXX1234"))
    return fresh


@pytest.fixture()
def client(db, monkeypatch) -> TestClient:
    monkeypatch.setattr(gmail_routes, "_require_client",
                        lambda: FakeAlertMailbox(ALERTS))
    return TestClient(app)


def _scan(client) -> dict:
    body = client.post("/api/gmail/scan",
                       params={"intent": "transactional", "max_messages": 50}).json()
    return jobs.snapshot(body["job_id"])


# --------------------------------------------------------------------------
# Intents
# --------------------------------------------------------------------------

def test_the_available_intents_are_listed(client):
    keys = [i["key"] for i in client.get("/api/gmail/intents").json()]
    assert {"statement", "bureau", "transactional"} <= set(keys)


def test_an_unknown_intent_is_refused(client):
    assert client.post("/api/gmail/scan",
                       params={"intent": "nonsense"}).status_code == 400


# --------------------------------------------------------------------------
# Scanning writes nothing
# --------------------------------------------------------------------------

def test_a_scan_reads_the_mail_and_decides_but_writes_nothing(client, db):
    """The review step. Nothing reaches the ledger until someone has looked."""
    before = len(repo.get_transactions(db))
    job = _scan(client)

    assert job["status"] == "complete"
    assert job["result"]["intent"] == "transactional"
    assert job["result"]["importable"] == 2
    assert len(repo.get_transactions(db)) == before


def test_every_alert_is_accounted_for_including_the_refusals(client):
    """A silent skip is indistinguishable from a scan that missed something."""
    result = _scan(client)["result"]
    by_status = {}
    for one in result["alerts"]:
        by_status.setdefault(one["status"], []).append(one)

    assert len(by_status["imported"]) == 2
    assert len(by_status["skipped"]) == 2
    reasons = " ".join(o["reason"] for o in by_status["skipped"])
    assert "no account here ends 8890" in reasons
    assert "not a completed transaction" in reasons


def test_the_importable_alerts_carry_what_the_user_needs_to_decide(client):
    ready = [a for a in _scan(client)["result"]["alerts"]
             if a["status"] == "imported"]
    one = next(a for a in ready if a["amount"] == "1250.00")
    assert one["direction"] == "debit"
    assert one["date_iso"] == "2026-08-15"
    assert one["account"].startswith("HDFC Bank")
    assert one["merchant"] == "swiggy@ybl"


# --------------------------------------------------------------------------
# Importing writes only what was chosen
# --------------------------------------------------------------------------

def test_only_the_chosen_alerts_are_written(client, db):
    scan = _scan(client)
    ready = [a for a in scan["result"]["alerts"] if a["status"] == "imported"]
    chosen = [ready[0]["message_id"]]

    body = client.post("/api/gmail/alerts/import", json={
        "message_ids": chosen, "scan_job_id": scan["id"]}).json()
    result = jobs.snapshot(body["job_id"])

    assert result["status"] == "complete"
    assert result["result"]["imported"] == 1
    stored = repo.get_transactions(db)
    assert len(stored) == 1
    assert stored[0].source == "email_alert"


def test_an_imported_alert_arrives_flagged_for_review(client, db):
    scan = _scan(client)
    ready = [a["message_id"] for a in scan["result"]["alerts"]
             if a["status"] == "imported"]
    client.post("/api/gmail/alerts/import",
                json={"message_ids": ready, "scan_job_id": scan["id"]})

    for txn in repo.get_transactions(db):
        assert txn.needs_review is True
        assert txn.is_reconcilable is False
        assert txn.statement_id is None


def test_importing_the_same_alerts_twice_adds_nothing(client, db):
    scan = _scan(client)
    ready = [a["message_id"] for a in scan["result"]["alerts"]
             if a["status"] == "imported"]
    payload = {"message_ids": ready, "scan_job_id": scan["id"]}

    first = client.post("/api/gmail/alerts/import", json=payload).json()
    assert jobs.snapshot(first["job_id"])["result"]["imported"] == 2

    second = client.post("/api/gmail/alerts/import", json=payload).json()
    outcome = jobs.snapshot(second["job_id"])["result"]
    assert outcome["imported"] == 0
    assert outcome["counts"]["duplicate"] == 2
    assert len(repo.get_transactions(db)) == 2


def test_an_import_with_nothing_selected_is_refused(client):
    assert client.post("/api/gmail/alerts/import",
                       json={"message_ids": [], "scan_job_id": "x"}
                       ).status_code == 400


def test_an_import_that_cannot_name_its_scan_is_refused(client):
    """The scan's query is how the mail is found again. Without it the import
    would have to trust a list that travelled through the browser."""
    assert client.post("/api/gmail/alerts/import",
                       json={"message_ids": ["alert0"]}).status_code == 400


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

def test_an_alert_import_is_a_job_like_any_other(client, db):
    scan = _scan(client)
    ready = [a["message_id"] for a in scan["result"]["alerts"]
             if a["status"] == "imported"]
    body = client.post("/api/gmail/alerts/import", json={
        "message_ids": ready, "scan_job_id": scan["id"]}).json()

    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["kind"] == "alerts"
    assert job["active"] is False
    # Per-item trace, so a refusal is visible in the same place as a success.
    assert job["item_count"] == len(ready)


def test_an_interrupted_alert_import_can_be_resumed(client, db):
    scan = _scan(client)
    ready = [a["message_id"] for a in scan["result"]["alerts"]
             if a["status"] == "imported"]
    job = jobs.create("alerts", total=2,
                      request={"message_ids": ready, "scan_job_id": scan["id"]})
    from app.jobs import JobProgress
    progress = JobProgress(job)
    progress.start(2)
    progress.item("first", "done", key=ready[0])
    jobs.flush()
    jobs.recover()

    body = client.post(f"/api/gmail/jobs/{job.id}/resume").json()
    assert body["remaining"] == 1
    assert body["skipped"] == 1
