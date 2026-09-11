"""The bugs the eval sweep found, each pinned so it cannot come back.

Every test here corresponds to a way the copilot produced a wrong or useless
answer on real data. None of them were caught by the unit tests that existed,
because each one is about what the agent ENDS UP SAYING rather than about
whether the machinery ran - which is the gap the eval harness exists to fill
and the reason these are worth writing down separately.

The first sweep scored 1 of 11. The failures were not eleven different
problems; they were five, and four of the five were silent.
"""

from __future__ import annotations

import json

import pytest

from app.agents import catalogue, runner, toolbelt, verify
from app.agents import runner as runner_mod

# `ledger` is a fixture defined next door; imported so pytest can resolve it
# here. The agent tests and these share one seeded ledger on purpose - a
# repair proven against a different fixture is not proven against the thing
# that broke.
from .test_agents import ANSWER, ScriptedModel, ledger   # noqa: F401


# ---------------------------------------------------------------------------
# A year is not an amount of money
# ---------------------------------------------------------------------------

class TestAYearIsNotMoney:
    """`2026` in "August 2026" was being checked as if it were rupees.

    It matched nothing any tool returned - because it is a year - so every
    correct, dated answer was served under a warning that one of its figures
    could not be traced. Three of the eleven eval cases failed this way while
    stating exactly the right number.
    """

    @pytest.mark.parametrize("text", [
        "You spent 5,500 on fuel in August 2026.",
        "You spent nothing on fuel in July 2026.",
        "Nothing was spent on scuba diving lessons in 2026.",
        "Your total for FY 2026-27 is on track.",
        "Spending rose in Q3 2026.",
        "Reviewed in Sept 2026.",
    ])
    def test_a_year_in_date_context_is_not_a_figure(self, text):
        money = [v for _, v in verify.figures_in(text)
                 if v >= verify.ONE_RUPEE_SCALE]
        assert 2026 not in money, f"the year was read as money in: {text}"

    def test_the_money_beside_the_year_is_still_read(self):
        money = [v for _, v in verify.figures_in(
            "You spent 5,500 on fuel in August 2026.")
            if v >= verify.ONE_RUPEE_SCALE]
        assert money == [5500]

    def test_a_bare_number_is_still_money(self):
        """Only a year in DATE CONTEXT is skipped. Left alone, 2026 rupees is
        an amount, and a model that invents it must still be caught."""
        money = [v for _, v in verify.figures_in("Paid 2026 to the vendor.")
                 if v >= verify.ONE_RUPEE_SCALE]
        assert money == [2026]

    def test_a_dated_answer_no_longer_carries_a_false_caveat(self):
        answer = {"headline": "You spent 5,500 on fuel in August 2026.",
                  "summary": "", "metrics": [], "findings": [],
                  "actions": [], "caveats": []}
        report = verify.check(answer, {5500})
        assert report.clean, f"falsely unverified: {report.unverified}"


# ---------------------------------------------------------------------------
# A query that cannot be honoured must not run
# ---------------------------------------------------------------------------

class TestLedgerQueryRefusesWhatItCannotDo:
    """The worst of the five, because it produced a confident wrong number.

    `ledger_query(category="fuel", start=..., end=...)` folded those keys
    into the spec, the compiler recognised none of them and dropped all
    three, and the query ran with no filter and no window - returning the net
    total of the ENTIRE ledger, labelled "All time", with no error anywhere.
    Asked what was spent on fuel in one month, the agent answered with
    twenty-one lakh.
    """

    def test_flat_arguments_are_honoured_not_dropped(self, ledger):
        result = toolbelt.call(ledger, "ledger_query", {
            "category": "fuel", "start": "2026-08-01", "end": "2026-08-31"})
        assert "error" not in result
        assert result["range"]["start"] == "2026-08-01"
        assert result["range"]["end"] == "2026-08-31"
        assert result["range"]["preset"] != "all", (
            "the window was dropped and the query ran over all time")

    def test_a_flat_query_agrees_with_the_nested_one(self, ledger):
        """The two spellings are the same question and must give one answer."""
        flat = toolbelt.call(ledger, "ledger_query", {
            "category": "fuel", "start": "2026-08-01", "end": "2026-08-31",
            "measures": [{"field": "outflow", "agg": "sum"}]})
        nested = toolbelt.call(ledger, "ledger_query", {"spec": {
            "measures": [{"field": "outflow", "agg": "sum"}],
            "filters": [{"field": "category", "op": "eq", "value": "fuel"}],
            "date_range": {"start": "2026-08-01", "end": "2026-08-31"}}})
        assert flat["rows"] == nested["rows"]

    def test_an_unknown_key_is_refused_by_name(self, ledger):
        result = toolbelt.call(ledger, "ledger_query", {"categorie": "fuel"})
        assert "categorie" in result["error"]
        assert "rows" not in result, "a refused query must return no figure"

    def test_a_nested_spec_is_validated_too(self, ledger):
        """The same silent drop was reachable through `spec`, not only flat."""
        result = toolbelt.call(ledger, "ledger_query",
                               {"spec": {"nonsense_field": 1}})
        assert "nonsense_field" in result["error"]

    def test_spec_and_flat_arguments_are_merged_not_chosen_between(self, ledger):
        """`spec or kwargs` took one and binned the other. A model that sends
        both meant both."""
        result = toolbelt.call(ledger, "ledger_query", {
            "spec": {"measures": [{"field": "outflow", "agg": "sum"}]},
            "category": "fuel"})
        assert "error" not in result
        assert result["columns"][0]["label"] == "Money out"


# ---------------------------------------------------------------------------
# Counting things
# ---------------------------------------------------------------------------

class TestAccountsCanBeCounted:
    """Asked how many credit cards it had, the agent said the account records
    were inaccessible. They were not - there were twenty-two of them, and the
    list was cut off a quarter of the way through."""

    def test_accounts_can_be_filtered_by_type(self, ledger):
        every = toolbelt.call(ledger, "accounts", {})
        cards = toolbelt.call(ledger, "accounts",
                              {"account_type": "credit_card"})
        assert cards["count"] <= every["count"]
        assert all(a["type"] == "credit_card" for a in cards["accounts"])

    def test_the_count_is_the_answer_without_tallying_a_list(self, ledger):
        cards = toolbelt.call(ledger, "accounts",
                              {"account_type": "credit_card"})
        assert cards["count"] == len(cards["accounts"])


class TestAnOversizedResultStaysReadable:
    """Cut at the character, a long result becomes JSON that stops mid-object,
    and a model reads that as a broken tool rather than a long one."""

    def test_a_trimmed_result_is_still_valid_json(self):
        value = {"accounts": [{"name": f"Account {i}", "balance": i * 1000}
                              for i in range(60)],
                 "count": 60}
        dumped = runner_mod._dump(value, 800)
        assert len(dumped) <= 800
        json.loads(dumped)          # raises if the structure was severed

    def test_the_total_survives_the_trim(self):
        """`count` is frequently the whole answer, and it is not a row."""
        value = {"accounts": [{"name": f"Account {i}"} for i in range(60)],
                 "count": 60}
        back = json.loads(runner_mod._dump(value, 600))
        assert back["count"] == 60

    def test_what_was_dropped_is_said_in_place(self):
        value = {"rows": [{"n": i, "pad": "x" * 40} for i in range(60)],
                 "row_count": 60}
        back = json.loads(runner_mod._dump(value, 600))
        assert "not shown" in str(back["rows"][-1])
        assert "60" in str(back["rows"][-1]), "the real total must be stated"

    def test_a_result_with_no_list_still_falls_back_to_characters(self):
        value = {"note": "y" * 4000}
        dumped = runner_mod._dump(value, 300)
        assert "truncated at 300 characters" in dumped


# ---------------------------------------------------------------------------
# The loop guard
# ---------------------------------------------------------------------------

class TestTheLoopGuardMeasuresProgress:
    """It used to compare `sorted(tool names)` between consecutive steps.

    That was a fair proxy only while arguments never arrived - with every
    call carrying `{}`, the same tool twice really was the same call twice.
    The moment arguments started working it began killing legitimate runs:
    asking `ledger_query` for dining and then for groceries is two different
    questions, and it is exactly how "how much do I spend on food?" has to be
    answered.
    """

    def test_the_same_tool_with_different_arguments_is_progress(self, ledger):
        agent = catalogue.get("copilot")
        model = ScriptedModel(
            {"thought": "Dining.", "calls": [{"tool": "ledger_query", "args": {
                "spec": {"filters": [{"field": "category", "op": "eq",
                                      "value": "dining"}]}}}]},
            {"thought": "Groceries.", "calls": [{"tool": "ledger_query", "args": {
                "spec": {"filters": [{"field": "category", "op": "eq",
                                      "value": "groceries"}]}}}]},
            {"thought": "Transport.", "calls": [{"tool": "ledger_query", "args": {
                "spec": {"filters": [{"field": "category", "op": "eq",
                                      "value": "transport"}]}}}]},
            ANSWER,
        )
        result = runner.run(agent, ledger, client=model)
        assert result.status == "ok", (
            f"three different questions were read as a loop: {result.error}")
        assert result.tool_calls == 3

    def test_the_same_tool_with_the_same_arguments_is_not(self, ledger):
        agent = catalogue.get("copilot")
        same = {"tool": "ledger_query",
                "args": {"spec": {"dimensions": ["category"]}}}
        model = ScriptedModel(*[{"thought": "again", "calls": [same]}
                                for _ in range(6)])
        result = runner.run(agent, ledger, client=model)
        assert result.status in {"looping", "ok"}
        # Whatever it ends as, the work was done once and served from the
        # memo after that.
        assert result.tool_calls == 1

    def test_a_confused_opening_is_not_a_loop(self, ledger):
        """The memo is seeded with the opening facts, and for most agents the
        opening tools are the first ones listed. A model that starts by
        asking for what it was already handed used to trip two no-progress
        steps and die at step 2, having made no calls of its own."""
        agent = catalogue.get("debt-strategist")
        assert agent.opening, "this test needs an agent with opening facts"
        model = ScriptedModel(
            *[{"thought": "Let me look.", "calls": [{"tool": name}]}
              for name in agent.opening],
            ANSWER,
        )
        result = runner.run(agent, ledger, client=model)
        assert result.status == "ok", (
            f"killed before making a single call of its own: {result.error}")


class TestAStoppedRunStillAnswers:
    """A run that stops without answering has still gathered everything it
    gathered. Handing the reader a blank page is the least useful thing to do
    with it - what failed was the deciding, not the looking."""

    def test_a_looping_run_is_asked_to_conclude(self, ledger):
        agent = catalogue.get("copilot")
        new = {"tool": "ledger_query",
               "args": {"spec": {"dimensions": ["category"]}}}
        repeat = {"tool": "accounts"}
        model = ScriptedModel(
            {"thought": "Look.", "calls": [new]},
            {"thought": "Again.", "calls": [repeat]},
            {"thought": "Again.", "calls": [repeat]},
            {"thought": "Again.", "calls": [repeat]},
            # The concluding turn, offered no tools.
            {"headline": "Spending by category, as far as it got.",
             "summary": "From the one query that ran.",
             "metrics": [], "findings": [], "actions": [], "caveats": []},
        )
        result = runner.run(agent, ledger, client=model)
        assert result.answer is not None, "a stopped run threw its work away"
        assert result.answer["headline"].startswith("Spending by category")
        assert result.partial, "the reader is owed the fact that it was cut short"

    def test_the_concluding_turn_is_offered_no_tools(self, ledger):
        agent = catalogue.get("copilot")
        repeat = {"tool": "accounts"}
        model = ScriptedModel(
            {"thought": "Look.", "calls": [{"tool": "ledger_query", "args": {
                "spec": {"dimensions": ["category"]}}}]},
            {"thought": "Again.", "calls": [repeat]},
            {"thought": "Again.", "calls": [repeat]},
            {"thought": "Again.", "calls": [repeat]},
            {"headline": "Concluded.", "summary": "", "metrics": [],
             "findings": [], "actions": [], "caveats": []},
        )
        runner.run(agent, ledger, client=model)
        assert "no more tool calls" in model.prompts[-1]

    def test_a_run_that_gathered_nothing_is_not_asked_to_conclude(self, ledger):
        """Nothing to conclude FROM. Asking anyway spends a request to invite
        the model to make something up, which is the one thing this app is
        built to prevent."""
        agent = catalogue.get("copilot")
        model = ScriptedModel(*[{"thought": "hmm"} for _ in range(9)])
        result = runner.run(agent, ledger, client=model)
        assert result.answer is None
        assert result.tool_calls == 0
        assert len(model.prompts) <= agent.max_steps
