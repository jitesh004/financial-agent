"""The contract the v2 front-end is tested against.

`frontend-v2/test/fixtures/api.json` holds what the API answered when it was
captured, and the whole v2 test suite runs against those answers. That is only
worth anything while they still match what the API answers TODAY - otherwise
the front-end tests go on passing against a shape the server stopped returning,
which is precisely the failure they exist to catch.

So this file re-runs the capture and compares. It is deliberately about SHAPE
rather than values: the demo ledger moves with the calendar, so the figures
differ on every run and none of them is what the fixtures are for.

It also asserts the things the fake server in `frontend-v2/test/harness.jsx`
re-implements rather than replays - the /api/transactions filters - because a
fake that filters differently from the real thing is a test that proves the
wrong app works.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import demo
from app.db.database import get_db
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "frontend-v2" / "test" / "fixtures" / "api.json"
DUMPER = ROOT / "backend" / "tools" / "dump_v2_fixtures.py"

pytestmark = pytest.mark.skipif(
    not FIXTURES.exists(), reason="the v2 fixtures have not been captured")


@pytest.fixture(scope="module")
def committed() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _dumper():
    """The capture tool, imported from a path rather than a package."""
    spec = importlib.util.spec_from_file_location("dump_v2_fixtures", DUMPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seeded(tenant, signed_in_client):
    """An account in the same state the fixtures were captured from.

    The demo ledger, plus the handful of things `populate` creates - a claim, a
    drafted position, a dashboard, a custom category. Without those the live
    answers for Owed, Position and Explore are empty lists, and a comparison
    against fixtures captured WITH them reports every field inside those lists
    as missing.
    """
    demo.seed(get_db(), tenant.id)
    _dumper().populate(signed_in_client)
    return signed_in_client


def _shape(value, depth: int = 0):
    """The structure of a payload, with every scalar replaced by its type.

    Lists collapse to their first element's shape: a list of a hundred
    transactions and a list of one make the same claim about the API.
    """
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0], depth + 1)] if value else []
    if value is None:
        # Null and a value are the same claim about a key's presence; which of
        # the two comes back depends on the ledger, not on the API.
        return "?"
    return type(value).__name__


def _keys(value, prefix: str = "") -> set[str]:
    """Every key path in a payload, so a comparison ignores order and values."""
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(f"{prefix}{k}")
            out |= _keys(v, f"{prefix}{k}.")
    elif isinstance(value, list) and value:
        out |= _keys(value[0], f"{prefix}[].")
    return out


def _get_paths(committed: dict) -> list[str]:
    """The captured keys that really are GET paths.

    Some entries are filed under a label rather than a URL - a dashboard's
    detail, a query result, an explanation - because the URL that produced
    them carries an id that changes on every capture.
    """
    return [k for k in committed if k.startswith("/")]


def test_every_endpoint_the_front_end_reads_still_answers(seeded, committed):
    """A route renamed or removed shows up here, not as a blank screen."""
    for path in _get_paths(committed):
        response = seeded.get(path)
        assert response.status_code == 200, \
            f"{path} answered {response.status_code}: {response.text[:200]}"


def test_no_endpoint_has_dropped_a_key_the_front_end_reads(seeded, committed):
    """Renaming or removing a field is a breaking change for v2.

    Compared one way only: the API gaining a field is fine and needs no
    fixture re-capture, while losing one silently turns a figure on screen
    into `undefined`.
    """
    missing: dict[str, list[str]] = {}
    for path in _get_paths(committed):
        recorded = committed[path]
        live = seeded.get(path).json()
        gone = sorted(_keys(recorded) - _keys(live))
        if gone:
            missing[path] = gone
    assert not missing, (
        "these endpoints no longer return keys the v2 front-end reads:\n"
        + json.dumps(missing, indent=1)
        + "\n\nIf the change is intended, re-capture with "
          "`python backend/tools/dump_v2_fixtures.py`.")


def test_the_shape_of_a_transaction_is_what_the_fixtures_say(seeded, committed):
    """The one payload every screen in the app reads."""
    key = next(k for k in committed if k.startswith("/api/transactions"))
    live = seeded.get(key).json()
    recorded = committed[key]

    assert set(live) == set(recorded)
    assert _shape(live["transactions"][0]) == _shape(recorded["transactions"][0])


# ---------------------------------------------------------------------------
# What the fake server re-implements rather than replays.
# ---------------------------------------------------------------------------

def test_search_filters_and_counts_what_it_matched(seeded):
    """The ledger's search box, end to end.

    It sent `search=` for a long time against an endpoint that had no such
    parameter, so FastAPI discarded it and answered the whole ledger.
    """
    everything = seeded.get("/api/transactions?limit=1000").json()
    assert everything["total"] > 20

    needle = everything["transactions"][0]["description"].split()[0]
    found = seeded.get(f"/api/transactions?limit=1000&search={needle}").json()

    assert 0 < found["total"] < everything["total"], \
        "a search that matches everything is not filtering"
    # `total` is the count of what MATCHED, not of the table - otherwise the
    # pager offers pages that do not exist.
    assert found["total"] == len(found["transactions"])
    for row in found["transactions"]:
        haystack = f"{row['description']} {row['merchant'] or ''}".lower()
        assert needle.lower() in haystack


def test_search_is_case_insensitive_and_matches_anywhere(seeded):
    rows = seeded.get("/api/transactions?limit=1000").json()["transactions"]
    word = next(w for w in rows[0]["description"].split() if len(w) > 4)
    middle = word[1:-1]

    upper = seeded.get(f"/api/transactions?limit=5&search={word.upper()}").json()
    lower = seeded.get(f"/api/transactions?limit=5&search={word.lower()}").json()
    inner = seeded.get(f"/api/transactions?limit=5&search={middle}").json()

    assert upper["total"] == lower["total"] > 0
    # Unanchored: on a rail like UPI every narration starts "UPI/", so a prefix
    # match would find almost nothing.
    assert inner["total"] >= upper["total"]


def test_search_escapes_sql_wildcards(seeded):
    """`%` and `_` are literal to somebody typing them, not wildcards."""
    assert seeded.get("/api/transactions?limit=5&search=%25").json()["total"] == 0
    assert seeded.get("/api/transactions?limit=5&search=____").json()["total"] == 0


def test_search_composes_with_the_other_filters(seeded):
    rows = seeded.get("/api/transactions?limit=1000").json()["transactions"]
    row = rows[0]
    needle = row["description"].split()[0]

    narrowed = seeded.get(
        f"/api/transactions?limit=1000&search={needle}"
        f"&account_id={row['account_id']}").json()
    assert narrowed["total"] > 0
    for found in narrowed["transactions"]:
        assert found["account_id"] == row["account_id"]


def test_sorting_by_every_column_the_ledger_offers(seeded):
    """Description and category were silently unsortable.

    An unknown sort key falls back to date order rather than erroring, so a
    dead sort control looks exactly like a working one over a ledger you have
    not memorised.
    """
    for field, key in (("description", "description"), ("category", "category"),
                       ("amount", "amount"), ("date", "date")):
        rows = seeded.get(
            f"/api/transactions?limit=200&sort_by={field}&sort_dir=asc"
        ).json()["transactions"]
        values = [r[key] for r in rows]
        if isinstance(values[0], str):
            values = [v.lower() for v in values]
        assert values == sorted(values), f"sort_by={field} did not sort"

        descending = seeded.get(
            f"/api/transactions?limit=200&sort_by={field}&sort_dir=desc"
        ).json()["transactions"]
        got = [r[key] for r in descending]
        if isinstance(got[0], str):
            got = [v.lower() for v in got]
        assert got == sorted(got, reverse=True), f"sort_by={field} did not reverse"


def test_a_series_row_filter_returns_only_that_series(seeded):
    rows = seeded.get("/api/transactions?limit=1000").json()["transactions"]
    series = next(r["recurring_series_id"] for r in rows if r["recurring_series_id"])

    found = seeded.get(
        f"/api/transactions?limit=1000&recurring_series_id={series}").json()
    assert found["total"] > 0
    assert all(r["recurring_series_id"] == series for r in found["transactions"])


def test_a_page_is_capped_however_much_is_asked_for(seeded):
    """`api.PAGE_MAX` in the front-end is this number, and has to stay it."""
    answered = seeded.get("/api/transactions?limit=999999").json()
    assert len(answered["transactions"]) <= 1000


def test_no_accounts_selected_returns_no_rows(seeded):
    """What the ledger sends when every account is unticked."""
    answered = seeded.get("/api/transactions?limit=100&account_id=__none__").json()
    assert answered["total"] == 0
    assert answered["transactions"] == []


def test_a_filtered_total_is_the_filtered_count(seeded):
    """Paging is built on `total`, so it has to count the filter."""
    everything = seeded.get("/api/transactions?limit=1").json()
    one_account = seeded.get("/api/transactions?limit=1").json()["transactions"][0]
    scoped = seeded.get(
        f"/api/transactions?limit=1&account_id={one_account['account_id']}").json()
    assert scoped["total"] <= everything["total"]
    assert scoped["total"] > 0


def test_an_explanation_carries_the_keys_the_panel_reads(seeded, committed):
    """The ledger's "why is this row the way it is?" panel.

    It read `direction.signal`, `direction.detail`, `transfer.note` and
    `transfer.counterpart` for a long time. None of them has ever been in this
    payload, so two of the panel's three sections were structurally empty on
    every row - which looks exactly like a row with nothing to say.
    """
    rows = seeded.get("/api/transactions?limit=1000").json()["transactions"]

    plain = next(r for r in rows if not r["is_internal_transfer"])
    answer = seeded.get(f"/api/rules/explain/{plain['id']}").json()
    assert set(answer) == {"id", "category", "direction", "transfer"}
    assert set(answer["direction"]) == {"value", "reason", "recorded"}
    assert set(answer["category"]) >= {"value", "source", "rule", "pattern", "confidence"}

    paired = next((r for r in rows if r["is_internal_transfer"]), None)
    if paired is None:
        pytest.skip("this ledger has no paired rows")
    transfer = seeded.get(f"/api/rules/explain/{paired['id']}").json()["transfer"]
    assert transfer is not None
    assert set(transfer) >= {
        "kind", "what_it_means", "confidence", "day_gap", "counted", "legs"}
    assert any(not leg["is_this_row"] for leg in transfer["legs"]), \
        "a pairing with no other leg is not a pairing"


def test_a_recorded_direction_reason_is_a_record_not_a_string(seeded):
    """What the panel renders as a label, a strength and a sentence."""
    from app.rules import directions

    described = directions.describe(next(iter(directions.BY_CODE)))
    assert set(described) == {"code", "label", "detail", "strength"}
