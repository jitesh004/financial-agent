"""The Explore query engine and the dashboards built on it.

Two properties carry most of the weight here.

The first is arithmetic. The engine exists so a user can total their own money
any way they like, and this project's whole premise is that such a total is
exact. Several tests below compute the same figure twice - once through SQL in
integer paise, once in Python with Decimal - and require them to be equal to
the paisa, not merely close.

The second is the closed surface. The endpoint accepts a query as JSON, which
would be indefensible if any part of that JSON reached SQLite as SQL. Every
field, operator and aggregation is looked up in a registry, so the tests that
matter most are the ones asserting that a name absent from the registry is
refused rather than interpreted.
"""

from __future__ import annotations

import sys
import tempfile
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.analytics import query as q  # noqa: E402
from app.api import dashboard_templates as templates  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.db.database import Database  # noqa: E402

ACCOUNTS = [
    ("a-bank", "HDFC Bank", "savings", "1234", ""),
    ("a-card", "American Express", "credit_card", "5001", "Platinum"),
]

#: (day offset, account, category, flow role, amount, direction, upi, mirror, excluded)
ROWS = [
    (0,  "a-bank", "salary",    "income",  "185000.00", "credit", 0, 0, 0),
    (1,  "a-bank", "groceries", "expense", "1499.50",   "debit",  1, 0, 0),
    (2,  "a-card", "dining",    "expense", "845.25",    "debit",  1, 0, 0),
    (3,  "a-card", "shopping",  "expense", "12000.99",  "debit",  0, 0, 0),
    (33, "a-bank", "groceries", "expense", "2250.75",   "debit",  0, 0, 0),
    (34, "a-card", "dining",    "expense", "399.00",    "debit",  1, 0, 0),
    (35, "a-bank", "rent",      "expense", "42000.00",  "debit",  0, 0, 0),
    # The mirror leg of a card payment, and a row the user excluded by hand.
    # Both are dropped by default and both must be reachable when asked for.
    (36, "a-card", "cc_payment", "card_settlement", "9000.00", "credit", 0, 1, 0),
    (37, "a-bank", "shopping",  "expense", "777.77",    "debit",  0, 0, 1),
]

START = date(2026, 1, 1)


def _seed() -> Database:
    db = Database(Path(tempfile.mkdtemp()) / "query.db")
    with db.connection() as conn:
        for account_id, institution, kind, masked, product in ACCOUNTS:
            conn.execute(
                "INSERT INTO accounts (id, institution, account_type,"
                " account_number_masked, product_name) VALUES (?, ?, ?, ?, ?)",
                (account_id, institution, kind, masked, product))
        for offset, account, category, role, amount, direction, upi, mirror, excluded in ROWS:
            day = START + timedelta(days=offset)
            conn.execute(
                "INSERT INTO transactions (id, account_id, txn_date,"
                " raw_description, normalized_description, merchant, amount,"
                " direction, category, flow_role, accounting_month,"
                " is_mirror_leg, excluded, needs_review, category_source,"
                " category_confidence)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), account, day.isoformat(),
                 ("UPI/" if upi else "POS/") + category.upper(), category,
                 category.title(), amount, direction, category, role,
                 day.strftime("%Y-%m"), mirror, excluded,
                 1 if category == "shopping" else 0, "rule", 0.9))
    return db


@pytest.fixture()
def db() -> Database:
    return _seed()


def _expected(predicate) -> Decimal:
    """The same total, computed in Decimal outside the engine."""
    return sum((Decimal(r[4]) for r in ROWS if predicate(r)), Decimal("0"))


def _live(row) -> bool:
    return not row[7] and not row[8]


# --------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------

def test_sum_is_exact_to_the_paisa(db):
    """The headline property: SQL and Decimal must agree exactly.

    Summing the TEXT amounts as REAL instead would pass an `approx` check and
    fail this one, which is the entire reason money is aggregated in integer
    paise.
    """
    result = q.run_query(db, {
        "dimensions": ["category"],
        "measures": [{"field": "outflow", "agg": "sum"}],
    })
    total = sum(Decimal(str(row["m0"])) for row in result["rows"])
    assert total == _expected(lambda r: r[5] == "debit" and _live(r))


def test_ungrouped_total_matches_grouped_total(db):
    """Grouping must not change the answer, only its shape."""
    grouped = q.run_query(db, {
        "dimensions": ["account", "month"],
        "measures": [{"field": "gross_amount", "agg": "sum"}],
    })
    flat = q.run_query(db, {"measures": [{"field": "gross_amount", "agg": "sum"}]})
    assert (sum(Decimal(str(r["m0"])) for r in grouped["rows"])
            == Decimal(str(flat["rows"][0]["m0"])))


def test_net_amount_signs_credits_and_debits(db):
    result = q.run_query(db, {"measures": [{"field": "net_amount", "agg": "sum"}]})
    credits = _expected(lambda r: r[5] == "credit" and _live(r))
    debits = _expected(lambda r: r[5] == "debit" and _live(r))
    assert Decimal(str(result["rows"][0]["m0"])) == credits - debits


def test_average_is_returned_in_whole_paise(db):
    result = q.run_query(db, {"measures": [{"field": "gross_amount", "agg": "avg"}]})
    value = Decimal(str(result["rows"][0]["m0"]))
    assert value == value.quantize(Decimal("0.01"))


# --------------------------------------------------------------------------
# Defaults that must stay visible
# --------------------------------------------------------------------------

def test_mirror_legs_and_excluded_rows_are_dropped_by_default(db):
    """Both legs of an internal move are real; counting both is not."""
    default = q.run_query(db, {"measures": [{"field": "txn_count", "agg": "count"}]})
    assert default["rows"][0]["m0"] == sum(1 for r in ROWS if _live(r))


def test_defaults_can_be_turned_off(db):
    everything = q.run_query(db, {
        "measures": [{"field": "txn_count", "agg": "count"}],
        "exclude_mirror_legs": False, "exclude_excluded": False,
    })
    assert everything["rows"][0]["m0"] == len(ROWS)


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------

def test_money_filters_are_typed_in_rupees_not_paise(db):
    """A filter typed as 1,000 must not silently mean ten rupees."""
    result = q.run_query(db, {
        "measures": [{"field": "txn_count", "agg": "count"}],
        "filters": [{"field": "amount", "op": "gt", "value": 1000}],
    })
    expected = sum(1 for r in ROWS if _live(r) and Decimal(r[4]) > 1000)
    assert result["rows"][0]["m0"] == expected


def test_in_filter_accepts_several_values(db):
    result = q.run_query(db, {
        "measures": [{"field": "outflow", "agg": "sum"}],
        "filters": [{"field": "category", "op": "in",
                     "value": ["groceries", "dining"]}],
    })
    assert (Decimal(str(result["rows"][0]["m0"]))
            == _expected(lambda r: r[2] in {"groceries", "dining"} and _live(r)))


def test_empty_in_filter_is_ignored_rather_than_matching_nothing(db):
    """A half-built filter is someone mid-edit, not a request for zero rows."""
    with_empty = q.run_query(db, {
        "measures": [{"field": "txn_count", "agg": "count"}],
        "filters": [{"field": "category", "op": "in", "value": []}],
    })
    without = q.run_query(db, {"measures": [{"field": "txn_count", "agg": "count"}]})
    assert with_empty["rows"][0]["m0"] == without["rows"][0]["m0"]


def test_like_wildcards_in_a_value_are_escaped(db):
    """'%' typed as a search term must match a literal percent sign."""
    result = q.run_query(db, {
        "measures": [{"field": "txn_count", "agg": "count"}],
        "filters": [{"field": "description", "op": "contains", "value": "%"}],
    })
    assert result["rows"][0]["m0"] == 0


def test_boolean_filter_reaches_the_flag(db):
    result = q.run_query(db, {
        "measures": [{"field": "txn_count", "agg": "count"}],
        "filters": [{"field": "needs_review", "op": "is_true"}],
    })
    assert result["rows"][0]["m0"] == sum(
        1 for r in ROWS if _live(r) and r[2] == "shopping")


def test_rail_dimension_splits_upi_from_everything_else(db):
    result = q.run_query(db, {
        "dimensions": ["rail"],
        "measures": [{"field": "txn_count", "agg": "count"}],
    })
    counts = {row["rail"]: row["m0"] for row in result["rows"]}
    assert counts["upi"] == sum(1 for r in ROWS if _live(r) and r[6])


# --------------------------------------------------------------------------
# The closed surface
# --------------------------------------------------------------------------

@pytest.mark.parametrize("spec", [
    {"dimensions": ["no_such_field"]},
    {"dimensions": ["amount"]},                                   # not groupable
    {"measures": [{"field": "no_such_measure", "agg": "sum"}]},
    {"measures": [{"field": "net_amount", "agg": "count"}]},      # wrong agg
    {"measures": [{"field": "txn_count", "agg": "sum"}]},
    {"filters": [{"field": "category", "op": "is_true"}]},        # wrong op for type
    {"filters": [{"field": "category", "op": "; DROP TABLE transactions"}]},
    {"filters": [{"field": "1=1", "op": "eq", "value": "x"}]},
    {"date_range": {"preset": "since_forever"}},
])
def test_anything_outside_the_registry_is_refused(db, spec):
    with pytest.raises(q.QueryError):
        q.run_query(db, spec)


def test_a_refused_query_leaves_the_data_alone(db):
    with pytest.raises(q.QueryError):
        q.run_query(db, {"filters": [{"field": "category",
                                      "op": "; DROP TABLE transactions"}]})
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == len(ROWS)


def test_row_limit_is_capped_and_truncation_is_reported(db):
    result = q.run_query(db, {"dimensions": ["date"], "limit": 2})
    assert result["row_count"] == 2
    assert result["truncated"] is True

    huge = q.compile_query({"dimensions": ["date"], "limit": 10 ** 9})
    assert f"LIMIT {q.MAX_ROWS + 1}" in huge.sql


def test_the_sql_comes_back_with_the_answer(db):
    """Auditability: a figure you cannot inspect is a figure you must trust."""
    result = q.run_query(db, {"dimensions": ["month"]})
    assert result["sql"].startswith("SELECT")
    assert "transactions" in result["sql"]


# --------------------------------------------------------------------------
# Date ranges
# --------------------------------------------------------------------------

def test_date_presets_resolve_against_a_fixed_today():
    today = date(2026, 3, 15)
    assert q.resolve_range({"preset": "all"}, today) == (None, None)
    assert q.resolve_range({"preset": "this_month"}, today) == ("2026-03-01", "2026-03-15")
    assert q.resolve_range({"preset": "last_month"}, today) == ("2026-02-01", "2026-02-28")
    assert q.resolve_range({"preset": "last_3m"}, today) == ("2026-01-01", "2026-03-15")
    assert q.resolve_range({"preset": "ytd"}, today) == ("2026-01-01", "2026-03-15")
    assert q.resolve_range({"preset": "last_year"}, today) == ("2025-01-01", "2025-12-31")


def test_last_month_handles_the_january_boundary():
    assert q.resolve_range({"preset": "last_month"}, date(2026, 1, 9)) \
        == ("2025-12-01", "2025-12-31")


def test_comparison_window_is_the_same_length_immediately_before():
    assert q.shift_range("2026-03-01", "2026-03-31") == ("2026-01-29", "2026-02-28")
    # An open-ended range has no "period before", and saying so beats inventing one.
    assert q.shift_range(None, None) == (None, None)


def test_compare_attaches_previous_and_delta(db):
    result = q.run_query(db, {
        "dimensions": ["category"],
        "measures": [{"field": "outflow", "agg": "sum"}],
        "date_range": {"preset": "custom", "start": "2026-02-01", "end": "2026-02-28"},
        "compare": True,
    })
    assert result["compared_to"] == {"start": "2026-01-04", "end": "2026-01-31"}
    for row in result["rows"]:
        assert "m0__prev" in row and "m0__delta" in row
        if row["m0__prev"] is not None:
            assert row["m0__delta"] == pytest.approx(row["m0"] - row["m0__prev"])


def test_compare_over_all_time_explains_itself_instead_of_failing(db):
    result = q.run_query(db, {"dimensions": ["category"],
                              "date_range": {"preset": "all"}, "compare": True})
    assert "compare_note" in result
    assert "all time" in result["compare_note"]


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

def test_schema_offers_only_operators_the_engine_accepts(db):
    schema = q.schema(db)
    for field in schema["fields"]:
        for op in field["ops"]:
            spec = {"filters": [{"field": field["key"], "op": op, "value": "1"}]}
            q.compile_query(spec)  # must not raise


def test_schema_enumerates_live_accounts_and_months(db):
    schema = q.schema(db)
    assert {a["value"] for a in schema["options"]["accounts"]} == {"a-bank", "a-card"}
    assert "2026-01" in {m["value"] for m in schema["options"]["months"]}


def test_account_picker_labels_match_the_grouped_values(db):
    """The filter dropdown and the chart legend have to agree on a name."""
    schema = q.schema(db)
    picker = {a["label"] for a in schema["options"]["accounts"]}
    grouped = {row["account"] for row in
               q.run_query(db, {"dimensions": ["account"]})["rows"]}
    assert grouped <= picker


# --------------------------------------------------------------------------
# Dashboards
# --------------------------------------------------------------------------

def test_a_widget_stores_its_query_not_its_numbers(db):
    """A saved board must never be able to disagree with the ledger."""
    board_id = repo.create_dashboard(db, "Board")
    repo.create_widget(db, board_id, {
        "title": "Spend", "type": "bar",
        "query": {"dimensions": ["category"],
                  "measures": [{"field": "outflow", "agg": "sum"}]},
    })
    stored = repo.get_dashboard(db, board_id)["widgets"][0]
    assert stored["query"]["dimensions"] == ["category"]
    assert not any(key in stored for key in ("rows", "result", "value"))


def test_first_board_becomes_the_default_and_deleting_it_hands_over(db):
    first = repo.create_dashboard(db, "First")
    second = repo.create_dashboard(db, "Second")
    assert repo.get_dashboard(db, first)["is_default"] is True
    assert repo.get_dashboard(db, second)["is_default"] is False

    repo.delete_dashboard(db, first)
    # Explore must always have a board to open, or an empty tab reads as data loss.
    assert repo.get_dashboard(db, second)["is_default"] is True


def test_only_one_board_is_ever_the_default(db):
    boards = [repo.create_dashboard(db, f"B{i}") for i in range(3)]
    repo.update_dashboard(db, boards[2], is_default=True)
    defaults = [b for b in repo.list_dashboards(db) if b["is_default"]]
    assert [b["id"] for b in defaults] == [boards[2]]


def test_layout_survives_a_round_trip(db):
    board_id = repo.create_dashboard(db, "Board")
    widgets = [repo.create_widget(db, board_id, {"title": f"W{i}"}) for i in range(3)]
    repo.save_layout(db, board_id, [
        {"id": widgets[2], "position": 0, "width": 12, "height": 4},
        {"id": widgets[0], "position": 1, "width": 3, "height": 1},
        {"id": widgets[1], "position": 2, "width": 6, "height": 2},
    ])
    order = [w["id"] for w in repo.get_dashboard(db, board_id)["widgets"]]
    assert order == [widgets[2], widgets[0], widgets[1]]
    assert repo.get_dashboard(db, board_id)["widgets"][0]["width"] == 12


def test_deleting_a_board_takes_its_widgets_with_it(db):
    board_id = repo.create_dashboard(db, "Board")
    repo.create_widget(db, board_id, {"title": "W"})
    repo.delete_dashboard(db, board_id)
    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) c FROM dashboard_widgets").fetchone()["c"] == 0


def test_a_widget_cannot_be_attached_to_a_board_that_does_not_exist(db):
    assert repo.create_widget(db, "not-a-board", {"title": "W"}) is None


@pytest.mark.parametrize("key", sorted(templates.TEMPLATES))
def test_every_starter_template_runs(db, key):
    """A shipped board that errors on open is worse than no board at all."""
    for widget in templates.TEMPLATES[key]["widgets"]:
        if widget["type"] == "text":
            continue
        result = q.run_query(db, widget["query"])
        assert "rows" in result, f"{key}/{widget['title']} returned nothing"
