"""The agent loop, its tools, and what it does when the model misbehaves.

No test here calls a real model. A fake one is scripted to reply the way real
ones actually do - the right shape, the wrong shape, a tool that does not
exist, a refusal to ever stop - because every one of those is a thing the loop
has to survive without losing the work it already did.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import catalogue, runner, toolbelt  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.db.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.schemas import (Account, AccountType,  # noqa: E402
                                Category, Direction, Transaction)

from .support import fresh_ledger  # noqa: E402

client = TestClient(app)


# --------------------------------------------------------------------------
# A fake model
# --------------------------------------------------------------------------

class ScriptedModel:
    """Replies from a script, one per turn.

    A callable entry is handed the prompt, so a test can assert on what the
    loop actually said to the model - which is the only way to check that
    tool results are being fed back at all.
    """

    def __init__(self, *replies, available: bool = True):
        self.replies = list(replies)
        self.available = available
        self.prompts: list[str] = []
        self.systems: list[str] = []

    def complete_json(self, prompt, system="", **kwargs):
        self.prompts.append(prompt)
        self.systems.append(system)
        if not self.replies:
            raise AssertionError("the loop asked for more turns than scripted")
        reply = self.replies.pop(0)
        return reply(prompt) if callable(reply) else reply


class BrokenModel:
    available = True

    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        raise self.exc


ANSWER = {
    "thought": "I have enough.",
    "answer": {
        "headline": "Your EMIs take 43% of take-home.",
        "summary": "Two loans, both current.",
        "metrics": [{"label": "EMI share of income", "value": "43", "unit": "%"}],
        "findings": [{"title": "Home loan dominates", "detail": "34,200 a month.",
                      "severity": "watch", "evidence": ["emi 34200"]}],
        "actions": [{"title": "Nothing urgent", "detail": "Both are current.",
                     "mechanism": "n/a", "effort": "low"}],
        "caveats": ["Only two statements are loaded."],
    },
}


# --------------------------------------------------------------------------
# A ledger to reason about
# --------------------------------------------------------------------------

@pytest.fixture
def ledger():
    db = fresh_ledger()
    repo.upsert_account(db, Account(
        id="bank-1", institution="HDFC", account_type=AccountType.SAVINGS,
        account_number_masked="4412", current_balance=Decimal("182000")))
    repo.upsert_account(db, Account(
        id="loan-1", institution="Meridian", account_type=AccountType.HOME_LOAN,
        account_number_masked="7781", principal_outstanding=Decimal("4200000"),
        interest_rate=Decimal("8.6"), emi_amount=Decimal("34200")))

    rows = []
    start = date(2025, 9, 1)
    for i in range(12):
        month = start.month - 1 + i
        when = date(start.year + month // 12, month % 12 + 1, 1)
        rows.append(Transaction(
            id=f"sal-{i}", account_id="bank-1", txn_date=when + timedelta(days=27),
            raw_description="ACME SALARY CREDIT",
            normalized_description="ACME SALARY CREDIT", merchant="ACME",
            amount=Decimal("185000"), direction=Direction.CREDIT,
            category=Category.SALARY))
        rows.append(Transaction(
            id=f"emi-{i}", account_id="bank-1", txn_date=when + timedelta(days=5),
            raw_description=f"MERIDIAN HOME LOAN EMI PRIN ({i + 1:03d}/240)",
            normalized_description="MERIDIAN HOME LOAN EMI PRIN",
            merchant="MERIDIAN HOME LOAN", amount=Decimal("34200"),
            direction=Direction.DEBIT, category=Category.EMI))
        rows.append(Transaction(
            id=f"rent-{i}", account_id="bank-1", txn_date=when + timedelta(days=3),
            raw_description="NEFT RENT HARBOUR VIEW APTS",
            normalized_description="NEFT RENT HARBOUR VIEW APTS",
            merchant="HARBOUR VIEW APTS", amount=Decimal("42000"),
            direction=Direction.DEBIT, category=Category.RENT))
    repo.save_transactions(db, rows)
    return db


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def test_every_tool_an_agent_names_exists():
    """An agent listing a tool the belt does not have would silently lose it -
    the loop refuses the call at runtime and the agent never learns why."""
    for agent in catalogue.AGENTS:
        unknown = [t for t in agent.tools if t not in toolbelt.TOOLS]
        assert not unknown, f"{agent.key} names missing tools: {unknown}"
        missing_opening = [t for t in agent.opening if t not in agent.tools]
        assert not missing_opening, \
            f"{agent.key} opens with tools it is not allowed: {missing_opening}"


def test_every_tool_is_described_for_the_prompt():
    described = {d["name"] for d in toolbelt.describe(list(toolbelt.TOOLS))}
    assert described == set(toolbelt.TOOLS)


def test_the_tools_return_figures_from_the_ledger(ledger):
    accounts = toolbelt.call(ledger, "accounts", {})
    assert accounts["count"] == 2
    assert any(a["type"] == "home_loan" for a in accounts["accounts"])

    loans = toolbelt.call(ledger, "loans", {})
    assert loans["loans"], "a loan with a balance, rate and EMI must project"
    loan = loans["loans"][0]
    assert loan["emi"] == 34200.0
    assert loan["months_remaining"] > 0
    assert loan["total_interest_remaining"] > 0

    analysis = toolbelt.call(ledger, "analysis", {"period": {"preset": "all"}})
    assert analysis["totals"]["income"] == 12 * 185000.0

    quality = toolbelt.call(ledger, "data_quality", {})
    assert quality["transactions"] == 36
    assert quality["months_covered"] == 12


def test_a_bad_tool_argument_comes_back_as_a_result_not_an_exception(ledger):
    """A wrong argument is the agent's mistake to fix on its next turn.

    Raising would kill a run that may already have five steps of good work
    behind it, over a typo the model would have corrected if told.
    """
    result = toolbelt.call(ledger, "ledger_query",
                           {"spec": {"dimensions": ["not_a_dimension"]}})
    assert "error" in result and "hint" in result

    unknown = toolbelt.call(ledger, "no_such_tool", {})
    assert "error" in unknown and unknown["available"]


def test_ledger_query_only_speaks_the_registry(ledger):
    """The query engine is a closed set of dimensions and measures, which is
    what makes it safe to let a model describe a query."""
    injected = toolbelt.call(ledger, "ledger_query", {"spec": {
        "dimensions": ["category; DROP TABLE transactions"],
        "measures": [{"field": "outflow", "agg": "sum"}]}})
    assert "error" in injected
    assert repo.count_transactions(get_db()) == 36


def test_simulate_prepayment_is_arithmetic_not_a_guess(ledger):
    result = toolbelt.call(ledger, "simulate_prepayment",
                           {"account_id": "loan-1", "lump_sum": 500000})
    scenario = result["scenarios"][0]
    assert scenario["months_saved"] > 0
    assert scenario["interest_saved"] > 0
    assert scenario["interest_after"] < scenario["interest_now"]

    nothing = toolbelt.call(ledger, "simulate_prepayment",
                            {"account_id": "loan-1"})
    assert "error" in nothing


def test_a_search_returns_rows_and_says_what_it_left_out(ledger):
    result = toolbelt.call(ledger, "search_transactions", {"text": "RENT"})
    assert result["matched"] == 12
    assert all("RENT" in t["description"] for t in result["transactions"])


def test_runway_reports_two_burn_rates(ledger):
    result = toolbelt.call(ledger, "runway", {})
    assert result["liquid_balance"] == 182000.0
    # Essentials cannot exceed the full cost - the whole point of the split.
    assert result["essential_monthly_cost"] <= result["full_monthly_cost"]
    assert result["committed_debt_monthly"] > 0


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

def test_a_run_calls_tools_and_reaches_an_answer(ledger):
    model = ScriptedModel(
        {"thought": "What does the debt cost?",
         "calls": [{"tool": "loans", "args": {}}]},
        ANSWER,
    )
    agent = catalogue.get("debt-strategist")
    result = runner.run(agent, ledger, client=model)

    assert result.status == "ok"
    assert result.answer["headline"].startswith("Your EMIs")
    assert result.answer["findings"][0]["severity"] == "watch"
    # Two turns of the model, plus the opening facts fetched before the first.
    assert len(model.prompts) == 2
    assert result.tool_calls == len(agent.opening) + 1


def test_the_opening_facts_are_in_the_first_prompt(ledger):
    """Every agent's first call is the obvious one, so it is made for them.

    A round trip spent asking for the accounts is a round trip not spent
    thinking, and the model has no way to know it could have had them.
    """
    model = ScriptedModel(ANSWER)
    agent = catalogue.get("debt-strategist")
    runner.run(agent, ledger, client=model)

    first = model.prompts[0]
    assert "Fetched for you" in first
    assert "home_loan" in first, "the opening account facts must be present"


def test_tool_results_are_fed_back(ledger):
    seen: dict[str, str] = {}

    def second_turn(prompt):
        seen["prompt"] = prompt
        return ANSWER

    model = ScriptedModel(
        {"thought": "Check the loan.", "calls": [{"tool": "loans"}]},
        second_turn,
    )
    runner.run(catalogue.get("debt-strategist"), ledger, client=model)
    assert "total_interest_remaining" in seen["prompt"], \
        "the agent must see what its own call returned"


def test_a_tool_outside_the_agents_list_is_refused(ledger):
    """An agent's tools are its tools. A model that reaches for another one is
    told so rather than quietly given it."""
    model = ScriptedModel(
        {"thought": "I want the portfolio.", "calls": [{"tool": "holdings"}]},
        ANSWER,
    )
    agent = catalogue.get("cashflow-sentinel")
    assert "holdings" not in agent.tools
    result = runner.run(agent, ledger, client=model)

    refused = result.steps[1].results[0]["result"]
    assert "not one of your tools" in refused["error"]
    assert result.status == "ok", "being refused a tool must not end the run"


def test_only_three_tools_run_in_one_turn(ledger):
    model = ScriptedModel(
        {"thought": "Everything at once.",
         "calls": [{"tool": "loans"}, {"tool": "accounts"},
                   {"tool": "budget"}, {"tool": "recurring"},
                   {"tool": "credit_report"}]},
        ANSWER,
    )
    result = runner.run(catalogue.get("debt-strategist"), ledger, client=model)
    assert len(result.steps[1].calls) == runner.MAX_CALLS_PER_STEP


def test_a_model_that_never_answers_is_stopped(ledger):
    """They do loop - re-querying the same thing when a result surprises them.

    The run comes back "exhausted" with its whole transcript, which is worth
    reading, rather than as a failure with nothing behind it.
    """
    agent = catalogue.get("cashflow-sentinel")
    forever = {"thought": "One more look.", "calls": [{"tool": "accounts"}]}
    model = ScriptedModel(*[forever] * agent.max_steps)

    result = runner.run(agent, ledger, client=model)
    assert result.status == "exhausted"
    assert result.answer is None
    assert len(result.steps) == agent.max_steps + 1  # + the opening facts
    assert "all" in result.error and "steps" in result.error


def test_the_last_turn_says_so(ledger):
    agent = catalogue.get("cashflow-sentinel")
    forever = {"thought": "Again.", "calls": [{"tool": "accounts"}]}
    model = ScriptedModel(*[forever] * agent.max_steps)
    runner.run(agent, ledger, client=model)
    assert "LAST turn" in model.prompts[-1]


def test_an_empty_reply_costs_a_step_rather_than_the_run(ledger):
    model = ScriptedModel({"thought": "..."}, ANSWER)
    result = runner.run(catalogue.get("debt-strategist"), ledger, client=model)
    assert result.status == "ok"
    assert result.steps[1].error == "no tool calls and no answer"
    assert "neither `calls` nor a complete `answer`" in model.prompts[1]


def test_a_model_error_keeps_the_steps_before_it(ledger):
    model = ScriptedModel(
        {"thought": "Check the loan.", "calls": [{"tool": "loans"}]},
        lambda _: (_ for _ in ()).throw(RuntimeError("429 rate limited")),
    )
    result = runner.run(catalogue.get("debt-strategist"), ledger, client=model)
    assert result.status == "failed"
    assert "429" in result.error
    assert result.steps[1].results, "the work before the failure survives"


def test_no_model_is_a_clear_refusal_not_a_crash(ledger):
    with pytest.raises(runner.AgentUnavailable) as raised:
        runner.run(catalogue.get("debt-strategist"), ledger,
                   client=ScriptedModel(available=False))
    assert "Settings" in str(raised.value)


def test_an_answer_wins_over_calls_in_the_same_reply(ledger):
    """A model that fills both has decided. Running the calls would only
    produce results nothing reads."""
    both = {**ANSWER, "calls": [{"tool": "loans"}]}
    model = ScriptedModel(both)
    agent = catalogue.get("debt-strategist")
    result = runner.run(agent, ledger, client=model)
    assert result.status == "ok"
    assert result.tool_calls == len(agent.opening)


def test_a_nearly_right_answer_is_normalised(ledger):
    """A string where a list belongs is the common model mistake, and every
    one of them would otherwise crash the screen rendering it."""
    sloppy = {
        "thought": "done",
        "answer": {
            "headline": "Something",
            "summary": "Something else",
            "caveats": "just the one",
            "findings": [{"title": "A", "detail": "B", "severity": "CRITICAL",
                          "evidence": "one string"}],
            "actions": [{"title": "Do", "effort": "enormous"}],
            "metrics": [{"label": "x", "value": 12}, {"label": "", "value": 1}],
        },
    }
    result = runner.run(catalogue.get("debt-strategist"), ledger,
                        client=ScriptedModel(sloppy))
    answer = result.answer
    assert answer["caveats"] == ["just the one"]
    assert answer["findings"][0]["severity"] == "info", "unknown -> info"
    assert answer["findings"][0]["evidence"] == ["one string"]
    assert answer["actions"][0]["effort"] == "medium"
    assert answer["metrics"] == [{"label": "x", "value": "12", "unit": "",
                                  "note": ""}]


def test_the_advice_boundary_is_in_every_agents_prompt(ledger):
    model = ScriptedModel(ANSWER)
    runner.run(catalogue.get("tax-utilisation"), ledger, client=model)
    system = model.systems[0]
    assert "No personalized investment advice" in system
    assert "Every number you report must come from a tool result" in system
    # And the agent's own brief is in there too, not just the shared rules.
    assert "80C" in system


# --------------------------------------------------------------------------
# Comparing runs
# --------------------------------------------------------------------------

def test_a_diff_reports_what_moved():
    now = {
        "metrics": [{"label": "EMI share of income", "value": "43", "unit": "%"},
                    {"label": "New thing", "value": "1", "unit": ""}],
        "findings": [{"title": "Home loan dominates"}, {"title": "Card revolving"}],
    }
    then = {
        "metrics": [{"label": "emi share of income", "value": "47%", "unit": "%"}],
        "findings": [{"title": "Home loan dominates"}, {"title": "Rent rose"}],
    }
    result = runner.diff(now, then)
    assert result["available"]
    moved = result["metrics_moved"]
    assert len(moved) == 1
    assert moved[0]["delta"] == -4.0 and moved[0]["direction"] == "down"
    assert result["new_findings"] == ["Card revolving"]
    assert result["resolved_findings"] == ["Rent rose"]
    assert result["unchanged_findings"] == 1


def test_a_first_run_has_nothing_to_diff_against():
    assert runner.diff({"metrics": []}, None) == {"available": False}


# --------------------------------------------------------------------------
# Persistence and the API
# --------------------------------------------------------------------------

def test_a_run_is_stored_with_its_working(ledger):
    run_id = repo.save_agent_run(ledger, {
        "agent": "debt-strategist", "status": "ok",
        "started_at": "2026-09-01T10:00:00+00:00",
        "finished_at": "2026-09-01T10:00:31+00:00", "seconds": 31.2,
        "answer": ANSWER["answer"],
        "transcript": [{"index": 1, "thought": "checking",
                        "calls": [{"tool": "loans", "args": {}}],
                        "results": [{"tool": "loans", "result": {"loans": []}}]}],
        "model": "strong", "provider": "gemini", "steps": 2, "tool_calls": 3,
    })
    stored = repo.get_agent_run(ledger, run_id)
    assert stored["answer"]["headline"] == ANSWER["answer"]["headline"]
    assert stored["transcript"][0]["calls"][0]["tool"] == "loans"

    # A listing must NOT carry the transcript - it is by far the largest
    # thing in the row and a list of twenty would be megabytes.
    listed = repo.get_agent_runs(ledger, "debt-strategist")
    assert len(listed) == 1 and "transcript" not in listed[0]


def test_a_run_survives_a_tool_that_hands_back_a_date(ledger):
    """A run costs a minute and real money. One unserialisable value in a
    transcript must not throw all of it away.

    This is not hypothetical: `position()` returned raw `date` objects in
    its totals, which is invisible over HTTP - FastAPI encodes them on the
    way out - and fatal here, where the transcript is stored as JSON. The
    tool is fixed; this is the belt that stops the next one costing a run.
    """
    run_id = repo.save_agent_run(ledger, {
        "agent": "debt-strategist", "status": "ok",
        "started_at": "2026-09-01T10:00:00+00:00",
        "answer": {"headline": "checked"},
        "transcript": [{"index": 1, "thought": "reading the position",
                        "calls": [{"tool": "position", "args": {}}],
                        "results": [{"tool": "position", "result": {
                            "totals": {"debt_free_on": date(2050, 10, 5)}}}]}],
    })
    stored = repo.get_agent_run(ledger, run_id)
    result = stored["transcript"][0]["results"][0]["result"]
    assert result["totals"]["debt_free_on"] == "2050-10-05"


def _attest(db) -> None:
    """A reviewed position on top of the ledger.

    The tools behave differently once there is one - `position` returns
    totals rather than a note saying nothing has been reviewed - and that
    is exactly the state the serialisation broke in.
    """
    repo.save_position_item(db, {
        "kind": "loan", "label": "Home loan", "institution": "Meridian",
        "account_id": "loan-1", "outstanding": "4200000", "emi": "34200",
        "interest_rate": "8.6", "reviewed_on": "2026-01-05"})
    repo.save_position_item(db, {
        "kind": "card", "label": "Northwind card", "institution": "Northwind",
        "outstanding": "48000", "credit_limit": "400000",
        "statement_day": 18, "due_day": 6, "reviewed_on": "2026-08-20"})


def test_the_position_tool_is_json_all_the_way_down(ledger):
    """The tool that caused it, checked at the shape the runner stores.

    Every other tool passes its dates through `_iso` on the way out.
    `position` hands `totals` straight through from the analytics, so the
    guarantee has to hold THERE - and the only way to know it does is to
    serialise the result strictly, with no `default=` to paper over it.
    """
    _attest(ledger)
    result = toolbelt.call(ledger, "position", {})
    json.dumps(result)

    totals = result["totals"]
    assert totals["reviewed_oldest"] == "2026-01-05", "an ISO string, not a date"
    assert totals["debt_free_on"] > "2026-01-05"
    assert totals["next_due_on"].endswith("-06")


def test_every_tool_returns_json_the_transcript_can_hold(ledger):
    """The general form of it, over the whole belt.

    A tool is only useful if what it returns can be stored beside the answer
    it produced. Run against a ledger that has been REVIEWED, because the
    thin one hides this: with no position items there are no totals, and no
    totals is where the dates were.
    """
    _attest(ledger)
    unserialisable = {}
    for name in toolbelt.TOOLS:
        try:
            json.dumps(toolbelt.call(ledger, name, {}))
        except TypeError as exc:
            unserialisable[name] = str(exc)
    assert not unserialisable, unserialisable


def test_the_previous_run_is_the_one_before_it(ledger):
    ids = []
    for minute in range(3):
        ids.append(repo.save_agent_run(ledger, {
            "agent": "debt-strategist", "status": "ok",
            "started_at": f"2026-09-01T10:0{minute}:00+00:00",
            "answer": {"headline": f"run {minute}"}}))
    previous = repo.previous_agent_run(ledger, "debt-strategist", ids[2])
    assert previous["answer"]["headline"] == "run 1"
    assert repo.previous_agent_run(ledger, "debt-strategist", ids[0]) is None


def test_history_is_pruned_to_what_is_worth_comparing(ledger):
    for i in range(30):
        repo.save_agent_run(ledger, {
            "agent": "debt-strategist",
            "started_at": f"2026-09-01T{i // 60:02d}:{i % 60:02d}:00+00:00",
            "answer": {}})
    dropped = repo.prune_agent_runs(ledger, "debt-strategist", keep=25)
    assert dropped == 5
    assert len(repo.get_agent_runs(ledger, "debt-strategist", limit=100)) == 25


def test_the_catalogue_endpoint_lists_the_agents_and_their_last_run(ledger):
    repo.save_agent_run(ledger, {
        "agent": "debt-strategist", "status": "ok",
        "started_at": "2026-09-01T10:00:00+00:00",
        "answer": ANSWER["answer"], "tool_calls": 4})

    payload = client.get("/api/agents").json()
    keys = [a["key"] for a in payload["agents"]]
    assert keys == [a.key for a in catalogue.AGENTS]

    debt = next(a for a in payload["agents"] if a["key"] == "debt-strategist")
    assert debt["last_run"]["headline"] == ANSWER["answer"]["headline"]
    assert debt["last_run"]["finding_count"] == 1
    assert next(a for a in payload["agents"]
                if a["key"] == "resilience")["last_run"] is None
    # No key is configured in the suite, so the screen must say so rather
    # than offering a button that cannot work.
    assert payload["model_available"] is False
    assert "Settings" in payload["model_note"]


def test_running_without_a_model_is_refused_before_a_job_is_made(ledger):
    response = client.post("/api/agents/debt-strategist/run", json={})
    assert response.status_code == 400
    assert "no language model" in response.json()["detail"].lower()


def test_an_unknown_agent_is_a_404(ledger):
    assert client.post("/api/agents/nope/run", json={}).status_code == 404
    assert client.get("/api/agents/nope/runs").status_code == 404


def test_reading_a_run_carries_the_diff_against_the_one_before(ledger):
    first = repo.save_agent_run(ledger, {
        "agent": "debt-strategist", "started_at": "2026-08-01T10:00:00+00:00",
        "answer": {"headline": "then", "summary": "",
                   "metrics": [{"label": "EMI share", "value": "47", "unit": "%"}],
                   "findings": [{"title": "Rent rose"}]}})
    second = repo.save_agent_run(ledger, {
        "agent": "debt-strategist", "started_at": "2026-09-01T10:00:00+00:00",
        "answer": {"headline": "now", "summary": "",
                   "metrics": [{"label": "EMI share", "value": "43", "unit": "%"}],
                   "findings": [{"title": "Card revolving"}]}})

    payload = client.get(f"/api/agents/runs/{second}").json()
    assert payload["previous"]["id"] == first
    assert payload["diff"]["metrics_moved"][0]["delta"] == -4.0
    assert payload["diff"]["new_findings"] == ["Card revolving"]
    assert "transcript" not in payload, "not unless it is asked for"

    detailed = client.get(f"/api/agents/runs/{second}?transcript=true").json()
    assert "transcript" in detailed


def test_a_run_can_be_deleted(ledger):
    run_id = repo.save_agent_run(ledger, {
        "agent": "resilience", "started_at": "2026-09-01T10:00:00+00:00",
        "answer": {}})
    assert client.delete(f"/api/agents/runs/{run_id}").status_code == 200
    assert client.get(f"/api/agents/runs/{run_id}").status_code == 404


# --------------------------------------------------------------------------
# The whole path, as the screen drives it
# --------------------------------------------------------------------------

def test_running_an_agent_through_the_job_system(ledger, monkeypatch):
    """POST, then poll, then read the run - which is what the screen does.

    Worth exercising end to end because the interesting failures are at the
    seams: a job that reports no progress, a run that is never stored, a
    finished job whose result does not name the run it produced.
    """
    from app.agents import runner as runner_mod
    from app.api import agent_routes

    model = ScriptedModel(
        {"thought": "What does the debt cost?",
         "calls": [{"tool": "loans"}, {"tool": "budget"}]},
        ANSWER,
    )
    monkeypatch.setattr(agent_routes, "get_client", lambda *a, **k: model)
    monkeypatch.setattr(runner_mod, "get_client", lambda *a, **k: model)

    started = client.post("/api/agents/debt-strategist/run",
                          json={"question": "Am I over-borrowed?"})
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]

    # TestClient runs background tasks before returning, so the job is done.
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "complete", job
    result = job["result"]
    assert result["status"] == "ok"
    assert result["headline"] == ANSWER["answer"]["headline"]
    assert result["tool_calls"] == 4  # two opening facts + two asked for

    run = client.get(f"/api/agents/runs/{result['run_id']}"
                     f"?transcript=true").json()
    assert run["agent"] == "debt-strategist"
    assert run["question"] == "Am I over-borrowed?"
    assert run["answer"]["headline"] == ANSWER["answer"]["headline"]
    assert run["agent_name"] == "Debt Strategist"
    # The working is there, which is what makes the figures checkable.
    tools_used = [c["tool"] for step in run["transcript"]
                  for c in step["calls"]]
    assert "loans" in tools_used and "budget" in tools_used

    # And the catalogue now shows it as the last run.
    listed = client.get("/api/agents").json()
    debt = next(a for a in listed["agents"] if a["key"] == "debt-strategist")
    assert debt["last_run"]["id"] == result["run_id"]


def test_the_users_own_question_replaces_the_agents_default(ledger, monkeypatch):
    from app.agents import runner as runner_mod

    model = ScriptedModel(ANSWER)
    monkeypatch.setattr(runner_mod, "get_client", lambda *a, **k: model)
    runner.run(catalogue.get("debt-strategist"), ledger, client=model,
               question="Which loan should I clear first?")
    assert "Which loan should I clear first?" in model.prompts[0]


def test_a_run_that_never_answers_is_still_saved_and_readable(ledger, monkeypatch):
    """An exhausted run's transcript is worth reading, so the job finishes
    with a warning rather than failing with nothing behind it."""
    from app.agents import runner as runner_mod
    from app.api import agent_routes

    agent = catalogue.get("cashflow-sentinel")
    forever = {"thought": "One more look.", "calls": [{"tool": "accounts"}]}
    model = ScriptedModel(*[forever] * agent.max_steps)
    monkeypatch.setattr(agent_routes, "get_client", lambda *a, **k: model)
    monkeypatch.setattr(runner_mod, "get_client", lambda *a, **k: model)

    job_id = client.post("/api/agents/cashflow-sentinel/run",
                         json={}).json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "complete"
    assert job["result"]["status"] == "exhausted"

    run = client.get(f"/api/agents/runs/{job['result']['run_id']}"
                     f"?transcript=true").json()
    assert run["status"] == "exhausted"
    assert run["transcript"], "the working survives a run that gave no answer"


# --------------------------------------------------------------------------
# "Unknown" is not "zero"
# --------------------------------------------------------------------------

def test_a_statement_with_no_running_balance_is_not_a_zero_balance(ledger):
    """The failure this guards against would have an agent tell somebody
    solvent that they are broke.

    A statement that prints no running balance leaves `current_balance`
    empty. Summed with a zero default that is a liquid balance of 0, a runway
    of 0 months, and a cashflow projection underwater on the first EMI of the
    month - none of which is a fact about the user's money.
    """
    from app.db.database import get_db

    db = get_db()
    with db.connection() as conn:
        conn.execute("UPDATE accounts SET current_balance = NULL"
                     " WHERE id = 'bank-1'")
        conn.execute("UPDATE transactions SET balance_after = NULL")

    result = toolbelt.call(ledger, "runway", {})
    assert result["balance_known"] is False
    assert result["liquid_balance"] is None
    assert result["months_at_full_cost"] is None, \
        "unknown must not read as zero months"
    assert result["accounts_without_a_balance"], "and it must say which account"
    # The costs are still real and still worth reporting.
    assert result["full_monthly_cost"] > 0
    assert "rather than reporting zero" in result["note"]

    forecast = toolbelt.call(ledger, "cashflow_forecast", {})
    assert forecast["balance_known"] is False
    assert forecast["first_shortfall"] is None, \
        "a shortfall cannot be claimed against a balance nobody knows"
    assert "net_change_by_then" in forecast["committed_events"][0]
    assert "must not report a shortfall" in forecast["note"]


def test_a_missing_balance_falls_back_to_the_statements_own_running_balance(ledger):
    """The same recovery analytics.engine makes: the last balance the
    statement printed is the best available answer, and far better than
    treating the account as empty."""
    from app.db.database import get_db

    db = get_db()
    with db.connection() as conn:
        conn.execute("UPDATE accounts SET current_balance = NULL"
                     " WHERE id = 'bank-1'")
        conn.execute("UPDATE transactions SET balance_after = '97500'"
                     " WHERE id = 'sal-11'")

    result = toolbelt.call(ledger, "runway", {})
    assert result["balance_known"] is True
    assert result["liquid_balance"] == 97500.0
    assert "running balance" in result["balance_basis"]
    assert result["months_at_full_cost"] > 0


def test_a_real_balance_still_projects_a_real_shortfall(ledger):
    """The other half: where the balance IS known, a shortfall is reported."""
    from app.db.database import get_db

    with get_db().connection() as conn:
        conn.execute("UPDATE accounts SET current_balance = '1000'"
                     " WHERE id = 'bank-1'")

    forecast = toolbelt.call(ledger, "cashflow_forecast",
                             {"horizon_days": 60})
    assert forecast["balance_known"] is True
    assert forecast["first_shortfall"] is not None
    assert forecast["first_shortfall"]["balance_after"] < 0


# --------------------------------------------------------------------------
# Fitting a small model's budget
# --------------------------------------------------------------------------

def test_a_whole_run_fits_inside_one_minute_of_a_metered_tier():
    """The number that decides whether agents work at all on a free tier.

    Gemini meters INPUT TOKENS PER MINUTE - 16,000, shared across every call
    made in the same minute. The settings this loop shipped with cost 91,250
    for a single ten-step run: six minutes of ceiling spent in a burst, which
    in practice is a cascade of 429s and a run that never finishes.
    """
    from app.agents import runner as runner_mod

    for agent in catalogue.AGENTS:
        system = runner_mod._system(agent, runner_mod.COMPACT)
        cost = runner_mod.COMPACT.estimate(len(system))
        assert cost < 16000, (
            f"{agent.key} costs ~{cost} input tokens a run, which does not "
            f"fit one minute of a metered free tier")


def test_the_compact_profile_sends_the_focus_and_the_full_one_the_brief():
    """Written short, not truncated. A prompt cut off mid-sentence produces
    reasoning cut off mid-thought, which is the failure being avoided rather
    than a cheaper version of it."""
    from app.agents import runner as runner_mod

    agent = catalogue.get("debt-strategist")
    compact = runner_mod._system(agent, runner_mod.COMPACT)
    full = runner_mod._system(agent, runner_mod.FULL)

    assert agent.focus in compact and agent.brief not in compact
    assert agent.brief in full
    assert len(compact) < len(full) / 1.5
    # Both still carry the rule that keeps the answer honest.
    for text in (compact, full):
        assert "must appear in a tool result" in text \
            or "must come from a tool result" in text


def test_every_agent_writes_both_a_focus_and_a_brief():
    for agent in catalogue.AGENTS:
        assert agent.focus.strip(), f"{agent.key} has no compact focus"
        assert agent.brief.strip(), f"{agent.key} has no brief"
        assert len(agent.focus) < len(agent.brief), \
            f"{agent.key}'s focus is not shorter than its brief"


def test_the_compact_profile_offers_fewer_tools():
    from app.agents import runner as runner_mod

    agent = catalogue.get("debt-strategist")
    assert len(agent.tools) > runner_mod.COMPACT.max_tools
    compact = runner_mod._system(agent, runner_mod.COMPACT)
    dropped = agent.tools[runner_mod.COMPACT.max_tools:]
    for name in dropped:
        assert f'"{name}"' not in compact
    # And the ones it leads with survive, in order.
    for name in agent.tools[:runner_mod.COMPACT.max_tools]:
        assert f'"{name}"' in compact


def test_a_small_model_is_recognised_by_its_name():
    from app.agents import runner as runner_mod

    for name in ("gemini-3.5-flash-lite", "gemini-2.0-flash-lite-001",
                 "gpt-5-mini", "gemma-4-26b-a4b-it", "qwen-3b-instruct",
                 "phi-4", "some-nano-model"):
        assert runner_mod.profile_for(name) is runner_mod.COMPACT, name
    # "mini" is a substring of "geMINI", so a substring match put Gemini Pro
    # on the compact budget. These are the names that must NOT match.
    for name in ("gemini-3-pro", "claude-opus-5", "gpt-5", "glm-5.2",
                 "z-ai/glm-5.2:free"):
        assert runner_mod.profile_for(name) is runner_mod.FULL, name


def test_an_unknown_model_gets_the_compact_budget(monkeypatch):
    """Compact still answers on a large model - it simply looks at fewer
    things. Full does not answer at all on a small one, so the safe default
    when nothing is known is the one that works either way."""
    from app.agents import runner as runner_mod
    from app.config import config

    monkeypatch.setattr(config, "LLM_PROVIDER", "")
    monkeypatch.setattr(config, "AGENT_PROFILE", "auto")
    assert runner_mod.profile_for() is runner_mod.COMPACT


def test_the_profile_can_be_forced(monkeypatch):
    from app.agents import runner as runner_mod
    from app.config import config

    monkeypatch.setattr(config, "AGENT_PROFILE", "full")
    assert runner_mod.profile_for("gemini-3.5-flash-lite") is runner_mod.FULL
    monkeypatch.setattr(config, "AGENT_PROFILE", "compact")
    assert runner_mod.profile_for("claude-opus-5") is runner_mod.COMPACT


def test_the_budget_caps_the_steps_an_agent_may_take(ledger):
    """An agent asking for ten steps gets five on the compact budget, and is
    told it is on its last turn at the right moment."""
    from app.agents import runner as runner_mod

    agent = catalogue.get("debt-strategist")
    assert agent.max_steps > runner_mod.COMPACT.max_steps
    forever = {"thought": "again", "calls": [{"tool": "loans"}]}
    model = ScriptedModel(*[forever] * runner_mod.COMPACT.max_steps)

    result = runner.run(agent, ledger, client=model,
                        budget=runner_mod.COMPACT)
    assert result.status == "exhausted"
    assert len(model.prompts) == runner_mod.COMPACT.max_steps
    assert "LAST turn" in model.prompts[-1]
    assert result.profile == "compact"


def test_a_large_tool_result_is_truncated_to_the_budget(ledger):
    from app.agents import runner as runner_mod

    model = ScriptedModel(
        {"thought": "everything", "calls": [{"tool": "ledger_query", "args": {
            "spec": {"dimensions": ["date", "merchant"],
                     "measures": [{"field": "outflow", "agg": "sum"}]}}}]},
        ANSWER)
    runner.run(catalogue.get("debt-strategist"), ledger, client=model,
               budget=runner_mod.COMPACT)
    # The second prompt carries the result, and the cap is what bounds it.
    carried = model.prompts[1]
    assert len(carried) < 20000, "a single result must not fill the window"


# --------------------------------------------------------------------------
# Every figure traced back to a tool
# --------------------------------------------------------------------------

def test_a_figure_the_tools_never_produced_is_flagged(ledger):
    """The failure this catches is quiet, not wild: a total the model added
    up itself and got slightly wrong, or a balance from two calls ago. Every
    one of those reads exactly like a correct answer."""
    invented = {
        "thought": "done",
        "answer": {
            "headline": "You owe 87,65,432 in total.",
            "summary": "Your EMI is 34,200 a month.",
            "metrics": [], "findings": [], "actions": [], "caveats": [],
        },
    }
    result = runner.run(catalogue.get("debt-strategist"), ledger,
                        client=ScriptedModel(invented))

    assert result.status == "ok", "a bad figure is reported, not a failed run"
    assert "87,65,432" in result.unverified
    assert "34,200" not in result.unverified, "the EMI IS in the loan tool"
    # And the reader is told, where the answer's other qualifications are.
    assert any("did not come from any tool" in c
               for c in result.answer["caveats"])


def test_an_answer_that_only_quotes_tool_figures_is_clean(ledger):
    from app.agents import toolbelt as belt

    loans = belt.call(ledger, "loans", {})
    emi = loans["loans"][0]["emi"]
    quoted = {
        "thought": "done",
        "answer": {"headline": f"Your EMI is {emi:,.0f} a month.",
                   "summary": "Nothing else to report.",
                   "metrics": [], "findings": [], "actions": [],
                   "caveats": []},
    }
    result = runner.run(catalogue.get("debt-strategist"), ledger,
                        client=ScriptedModel(quoted))
    assert result.unverified == []
    assert result.figures_checked >= 1
    assert not any("did not come from any tool"
                   for c in result.answer["caveats"] if False)


def test_counts_and_percentages_are_not_treated_as_money(ledger):
    """A model is entitled to work out "43% of take-home" from two figures it
    was given. Checking that would flag every correct derivation there is."""
    derived = {
        "thought": "done",
        "answer": {"headline": "EMIs are 43% of take-home across 2 loans.",
                   "summary": "221 instalments left, at 8.45%.",
                   "metrics": [], "findings": [], "actions": [],
                   "caveats": []},
    }
    result = runner.run(catalogue.get("debt-strategist"), ledger,
                        client=ScriptedModel(derived))
    assert result.unverified == []


def test_the_figure_check_reads_indian_formatting():
    from app.agents import verify

    figures = verify.collect_figures({"outstanding": "4124761.64"})
    for written in ("41,24,762", "41.2 lakh", "41 lakh", "4124761.64"):
        report = verify.check({"headline": f"You owe {written}."}, figures)
        assert report.clean, f"{written} should verify"

    for written in ("52,00,000", "60 lakh", "1.2 crore"):
        report = verify.check({"headline": f"You owe {written}."}, figures)
        assert not report.clean, f"{written} should not verify"


def test_an_id_or_a_date_is_not_a_quantity():
    from app.agents import verify

    report = verify.check(
        {"headline": "Reviewed 2026-04-10 on card XXXX9931, "
                     "instalment (013/240)."},
        set())
    assert report.checked == 0 and report.clean


def test_the_same_wrong_figure_twice_is_one_problem():
    from app.agents import verify

    report = verify.check(
        {"headline": "You owe 87,65,432.",
         "summary": "That 87,65,432 is the whole of it."},
        set())
    assert report.unverified == ["87,65,432"]


# --------------------------------------------------------------------------
# The whole catalogue
# --------------------------------------------------------------------------

def test_the_catalogue_is_complete_and_coherent():
    """Twelve agents, each with a distinct key, a question, and tools that
    exist. Checked as a set because the failure of a twelfth agent is
    typically a copied block with one field not changed."""
    keys = [a.key for a in catalogue.AGENTS]
    assert len(keys) == len(set(keys)) == 12

    for agent in catalogue.AGENTS:
        assert agent.name and agent.question and agent.blurb, agent.key
        assert agent.question.endswith("?"), agent.key
        assert agent.tools, agent.key
        assert len(agent.tools) == len(set(agent.tools)), agent.key
        for name in agent.tools:
            assert name in toolbelt.TOOLS, f"{agent.key}: {name}"
        for name in agent.opening:
            assert name in agent.tools, f"{agent.key} opens with {name}"


def test_every_opening_tool_works_with_no_arguments(ledger):
    """Opening facts are fetched before the model has said anything, so they
    are called with an empty argument dict. A tool that needs one would come
    back as an error in the very first thing the agent reads."""
    for agent in catalogue.AGENTS:
        for name in agent.opening:
            result = toolbelt.call(ledger, name, {})
            assert isinstance(result, dict), f"{agent.key}/{name}"
            assert "error" not in result, \
                f"{agent.key} opens with {name}, which needs arguments: " \
                f"{result.get('error')}"


def test_the_new_tools_all_answer_against_a_real_ledger(ledger):
    """Each returns figures, not prose, and none of them raises on a ledger
    with no bureau report, no portfolio and one account."""
    for name in ("anomalies", "duplicate_charges", "income", "coverage_gaps",
                 "review_queue"):
        result = toolbelt.call(ledger, name, {})
        assert isinstance(result, dict), name
        assert "error" not in result, f"{name}: {result.get('error')}"


def test_income_reports_a_distribution_not_a_total(ledger):
    result = toolbelt.call(ledger, "income", {})
    assert result["typical_month"] == 185000.0
    assert result["lowest_month"] is not None
    assert result["sources"], "an income source must be named"
    assert result["months_below_half_typical"] == 0


def test_duplicate_charges_finds_one_bill_taken_twice(ledger):
    """And says it is a candidate rather than a verdict."""
    from datetime import date as _date

    from app.models.schemas import Direction as _Dir

    rows = [
        Transaction(id="dup-1", account_id="bank-1", txn_date=_date(2026, 3, 4),
                    raw_description="ACME UTILITIES BILL",
                    normalized_description="ACME UTILITIES BILL",
                    merchant="ACME UTILITIES", amount=Decimal("4820"),
                    direction=_Dir.DEBIT, category=Category.UTILITIES),
        Transaction(id="dup-2", account_id="bank-1", txn_date=_date(2026, 3, 6),
                    raw_description="ACME UTILITIES BILL",
                    normalized_description="ACME UTILITIES BILL",
                    merchant="ACME UTILITIES", amount=Decimal("4820"),
                    direction=_Dir.DEBIT, category=Category.UTILITIES),
    ]
    repo.save_transactions(ledger, rows)

    result = toolbelt.call(ledger, "duplicate_charges", {})
    found = [c for c in result["candidates"] if "ACME" in c["merchant"]]
    assert len(found) == 1
    assert found[0]["days_apart"] == 2 and found[0]["amount"] == 4820.0
    assert "not confirmed duplicates" in result["note"]


def test_review_queue_says_how_much_money_is_waiting(ledger):
    result = toolbelt.call(ledger, "review_queue", {})
    assert "awaiting_review" in result and "uncategorized" in result
    assert result["awaiting_review_total"] is not None


def test_every_agent_runs_end_to_end_inside_the_budget(ledger):
    """The whole thing, twelve times, against a model that behaves like a
    small one: it calls what it is offered and then answers.

    This is the test that would have caught the original problem. Each agent
    individually looked fine; what was broken was the arithmetic across a
    whole run, and nothing measured that.
    """
    from app.agents import runner as runner_mod

    class SmallModel:
        available = True

        def __init__(self, tools):
            self.tools = list(tools)
            self.turn = 0
            self.chars = 0

        def complete_json(self, prompt, system="", **kwargs):
            self.chars += len(prompt) + len(system)
            self.turn += 1
            if self.turn <= 2 and self.tools:
                batch, self.tools = self.tools[:2], self.tools[2:]
                return {"thought": "looking",
                        "calls": [{"tool": t} for t in batch]}
            return {"thought": "done", "answer": {
                "headline": "Nothing notable.", "summary": "Checked.",
                "metrics": [], "findings": [], "actions": [], "caveats": []}}

    for agent in catalogue.AGENTS:
        model = SmallModel([t for t in agent.tools if t not in agent.opening])
        result = runner.run(agent, ledger, client=model,
                            budget=runner_mod.COMPACT)
        assert result.status == "ok", f"{agent.key}: {result.error}"
        assert result.tool_calls >= len(agent.opening), agent.key
        assert model.chars // 4 < 16000, (
            f"{agent.key} spent ~{model.chars // 4} input tokens, which does "
            f"not fit one minute of a metered free tier")


def test_an_unverified_figure_survives_into_the_stored_run(ledger, monkeypatch):
    """It has to reach the screen, or the check may as well not exist."""
    from app.agents import runner as runner_mod
    from app.api import agent_routes

    model = ScriptedModel({
        "thought": "done",
        "answer": {"headline": "You owe 99,88,777.", "summary": "All of it.",
                   "metrics": [], "findings": [], "actions": [],
                   "caveats": []},
    })
    monkeypatch.setattr(agent_routes, "get_client", lambda *a, **k: model)
    monkeypatch.setattr(runner_mod, "get_client", lambda *a, **k: model)

    job_id = client.post("/api/agents/debt-strategist/run",
                         json={}).json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["result"]["unverified"] == 1

    run = client.get(f"/api/agents/runs/{job['result']['run_id']}").json()
    assert any("did not come from any tool" in c
               for c in run["answer"]["caveats"])
    # And it is visible in the history without opening the run.
    assert "not traceable to a tool result" in run["error"]


def test_the_catalogue_says_which_budget_is_in_force(ledger):
    """A compact run and a broken run look identical from outside - three
    findings instead of six - so the screen has to be able to say which."""
    payload = client.get("/api/agents").json()
    assert payload["profile"]["name"] in {"compact", "full"}
    assert payload["profile"]["max_steps"] >= 5
    assert payload["profile"]["note"]
    assert len(payload["agents"]) == 12
