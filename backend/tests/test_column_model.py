"""The model may say which column is which. It may not say what is in them.

This fallback runs only after every deterministic reader has failed, so the
choice it faces is a mapping or a discarded table. What keeps it safe is not
the model's accuracy - it is that its answer is checked against the cells it
named, by the same parsers that will read them.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.normalize import column_map                          # noqa: E402
from app.normalize.column_map import (ColumnMapping,          # noqa: E402
                                      _answer_survives_the_data,
                                      ask_model_for_columns)

# A table whose header is unreadable - the case the fallback exists for.
HEADER = ["", "", "", ""]
ROWS = [
    ["01/08/2026", "UPI/SWIGGY/BANGALORE", "450.00", "12,300.00"],
    ["03/08/2026", "NEFT SALARY CREDIT", "1,67,489.00", "1,79,789.00"],
    ["05/08/2026", "ACH HDFC HOME LOAN EMI", "64,032.00", "1,15,757.00"],
    ["07/08/2026", "AMAZON PAY GROCERY", "515.00", "1,15,242.00"],
]


def _mapping(**roles):
    m = ColumnMapping(roles=dict(roles), confidence=0.55)
    return m


def test_a_correct_mapping_is_accepted():
    assert _answer_survives_the_data(
        _mapping(txn_date=0, description=1, amount=2, balance=3), ROWS, 2026)


def test_an_amount_pointed_at_the_narration_is_rejected():
    """The guard that makes this safe.

    A model asked for indices can still point "amount" at the description,
    and nothing downstream would notice - a money column full of merchant
    names simply yields zeroes, which is a silently wrong ledger rather
    than a failed import.
    """
    assert not _answer_survives_the_data(
        _mapping(txn_date=0, description=2, amount=1), ROWS, 2026)


def test_a_date_pointed_at_a_money_column_is_rejected():
    assert not _answer_survives_the_data(
        _mapping(txn_date=2, description=1, amount=3), ROWS, 2026)


def test_a_mapping_with_no_money_column_is_rejected():
    assert not _answer_survives_the_data(
        _mapping(txn_date=0, description=1), ROWS, 2026)


def test_a_split_debit_credit_layout_still_passes():
    """Each money column is empty on most rows by definition, so requiring
    every one of them to carry amounts would reject the commonest shape
    this fallback is here to rescue."""
    split = [
        ["01/08/2026", "UPI/SWIGGY", "450.00", "", "12,300.00"],
        ["03/08/2026", "NEFT SALARY", "", "1,67,489.00", "1,79,789.00"],
        ["05/08/2026", "ACH EMI", "64,032.00", "", "1,15,757.00"],
        ["07/08/2026", "AMAZON PAY", "515.00", "", "1,15,242.00"],
    ]
    assert _answer_survives_the_data(
        _mapping(txn_date=0, description=1, debit=2, credit=3, balance=4),
        split, 2026)


def test_an_index_off_the_end_of_the_table_is_dropped(monkeypatch):
    """An index outside the table is not an answer about this table."""
    monkeypatch.setattr(column_map, "_MODEL_ROLES", column_map._MODEL_ROLES)

    class _Client:
        available = True

        def complete_json(self, *a, **k):
            return {"txn_date": 0, "description": 1, "amount": 99}

    monkeypatch.setattr("app.llm.client.get_client", lambda *a, **k: _Client())
    # Index 99 is dropped, leaving no money column, so the mapping is not
    # usable and the table is left unparsed rather than mis-parsed.
    assert ask_model_for_columns(HEADER, ROWS, 2026) is None


def test_a_hallucinated_mapping_never_reaches_the_ledger(monkeypatch):
    """End to end: the model answers confidently and wrongly, and the
    deterministic check throws the answer away."""
    class _Client:
        available = True

        def complete_json(self, *a, **k):
            # "amount" is the narration column.
            return {"txn_date": 0, "description": 2, "amount": 1}

    monkeypatch.setattr("app.llm.client.get_client", lambda *a, **k: _Client())
    assert ask_model_for_columns(HEADER, ROWS, 2026) is None


def test_a_good_answer_is_accepted_and_carries_no_values(monkeypatch):
    seen = {}

    class _Client:
        available = True

        def complete_json(self, prompt, **k):
            seen["prompt"] = prompt
            return {"txn_date": 0, "description": 1, "amount": 2, "balance": 3}

    monkeypatch.setattr("app.llm.client.get_client", lambda *a, **k: _Client())
    mapping = ask_model_for_columns(HEADER, ROWS, 2026)

    assert mapping is not None
    assert mapping.get("txn_date") == 0
    assert mapping.get("amount") == 2
    # Every role is an integer index. Nothing the model returned is carried
    # into the ledger as a value.
    assert all(isinstance(v, int) for v in mapping.roles.values())


def test_no_model_configured_is_not_an_error(monkeypatch):
    class _Client:
        available = False

        def complete_json(self, *a, **k):     # pragma: no cover
            raise AssertionError("must not be called")

    monkeypatch.setattr("app.llm.client.get_client", lambda *a, **k: _Client())
    assert ask_model_for_columns(HEADER, ROWS, 2026) is None


def test_an_empty_table_asks_nothing(monkeypatch):
    def _boom(*a, **k):                       # pragma: no cover
        raise AssertionError("must not build a client for an empty table")

    monkeypatch.setattr("app.llm.client.get_client", _boom)
    assert ask_model_for_columns([], [], 2026) is None


def test_a_table_with_no_date_column_costs_no_request(monkeypatch):
    """A question whose every answer is rejected must not be asked.

    `is_usable()` requires a date column and a model cannot invent one, so
    on a table that has none the best possible reply is still thrown away
    and the request is simply gone. Three Bank of Baroda tables cost four
    requests each on a real import, every one correctly answering
    "txn_date: null", because the extractor had merged the date into the
    description. Twelve requests out of a daily five hundred, for nothing.
    """
    def _boom(*a, **k):                       # pragma: no cover
        raise AssertionError("must not spend a request on an unanswerable table")

    monkeypatch.setattr("app.llm.client.get_client", _boom)

    dateless = [
        ["39 22-05-2025 22-05-2025 SMS Cha", "", "0.24", "-", "23,140.93"],
        ["NEFT-BARBZ25133423795-BRAHMA AYU", "", "4,505.00", "-", "28,341.17"],
        ["UPI/550206189620/16:43:08/UPI/go", "", "5,200.00", "-", "23,141.17"],
    ]
    assert ask_model_for_columns([], dateless, 2026) is None


def test_a_table_with_a_date_column_is_still_asked(monkeypatch):
    """The pre-check must not swallow the case the fallback exists for."""
    asked = {}

    class _Client:
        available = True

        def complete_json(self, prompt, **k):
            asked["yes"] = True
            return {"txn_date": 0, "description": 1, "amount": 2, "balance": 3}

    monkeypatch.setattr("app.llm.client.get_client", lambda *a, **k: _Client())
    assert ask_model_for_columns(HEADER, ROWS, 2026) is not None
    assert asked.get("yes")
