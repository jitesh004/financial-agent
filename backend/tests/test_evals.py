"""The eval harness has to be right before the scores it produces mean much.

A harness is a measuring instrument, and an instrument nobody checks is a
source of confident wrong readings. Both failures below were real: the
harness disagreed with the app about what "spending" means and failed an
agent for being correct, and it scored "I could not work it out" as a pass
because the case asserted tool choice rather than a figure.

These tests never call a model. The cases themselves need a ledger and a
model and are run on request by `run_evals.py`; what is tested here is the
scoring, which is ordinary code and should be held to ordinary standards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.evals import harness
from app.evals.cases import CASES, Case


# ---------------------------------------------------------------------------
# Enough of a run to score
# ---------------------------------------------------------------------------

@dataclass
class FakeStep:
    calls: list[dict] = field(default_factory=list)


@dataclass
class FakeRun:
    answer: dict[str, Any] | None = None
    status: str = "ok"
    steps: list[FakeStep] = field(default_factory=list)
    tool_calls: int = 0
    seconds: float = 0.1
    unverified: list[str] = field(default_factory=list)
    error: str = ""


def answer(headline: str, **extra) -> dict[str, Any]:
    return {"headline": headline, "summary": "", "metrics": [],
            "findings": [], "actions": [], "caveats": [], **extra}


def run_with(headline: str, *, tools: tuple[str, ...] = (), **extra) -> FakeRun:
    return FakeRun(answer=answer(headline, **extra),
                   steps=[FakeStep(calls=[{"tool": t} for t in tools])],
                   tool_calls=len(tools))


def score(case: Case, run: FakeRun, expected=None, expect_zero=False):
    return harness._score(case, case.question, expected, run, expect_zero)


# ---------------------------------------------------------------------------
# Reading figures
# ---------------------------------------------------------------------------

class TestReadingFiguresOutOfAnswers:

    def test_a_year_is_not_read_as_a_figure(self):
        """The harness had its own number regex, and it counted the 2026 in
        "August 2026". That made the should-find-nothing case fail for
        reporting a figure that was a date."""
        found = harness.figures_in(
            answer("You spent nothing on scuba lessons in 2026."))
        assert not [w for w, v in found if v > 1000]

    def test_the_money_is_still_read(self):
        found = harness.figures_in(
            answer("You spent 5,500 on fuel in August 2026."))
        assert Decimal(5500) in [v for _, v in found]

    def test_a_figure_in_a_metric_counts(self):
        """An agent that puts the right number in a metric and a different
        one in the headline has still told the reader something false."""
        found = harness.figures_in(answer(
            "Spending was high.",
            metrics=[{"label": "Total", "value": "12,345", "unit": "INR"}]))
        assert Decimal(12345) in [v for _, v in found]

    def test_lakh_shorthand_matches_the_full_figure(self):
        assert harness._matches(Decimal("4124761.64"),
                                harness.figures_in(answer("About 41.2 lakh.")))

    def test_a_different_figure_does_not_match(self):
        assert not harness._matches(
            Decimal("5500"), [("5,600", Decimal("5600")), ("12", Decimal("12"))])


# ---------------------------------------------------------------------------
# Giving up is not answering
# ---------------------------------------------------------------------------

class TestANonAnswerIsNotAPass:
    """Every headline here was produced by a real run and scored as a pass.

    The cases they belonged to assert tool choice rather than a figure, and
    the agent had reached for the right tools before failing - so every
    check the harness ran came back clean while the reader got nothing.
    """

    CASE = Case(key="c", question="What was my biggest category last month?",
                why="test", needs_tools=())

    def test_could_not_be_determined_fails(self):
        scored = score(self.CASE, run_with(
            "The specific spending category could not be determined."))
        assert not scored.passed
        assert scored.failure == "answer"

    def test_blaming_the_tools_fails(self):
        scored = score(self.CASE, run_with(
            "The available tool results do not contain the breakdown needed."))
        assert not scored.passed

    def test_a_real_answer_passes(self):
        scored = score(self.CASE, run_with(
            "Your biggest category last month was groceries."))
        assert scored.passed, scored.detail

    def test_a_genuine_zero_is_an_answer_not_a_surrender(self):
        """'You spent nothing' reports the ledger. 'It could not be
        determined' reports the agent. The difference is the whole point."""
        case = Case(key="z", question="How much on scuba?", why="test",
                    expect_zero=True)
        scored = score(case, run_with("You spent nothing on scuba lessons."),
                       expect_zero=True)
        assert scored.passed, scored.detail

    def test_an_all_ledger_total_fails_a_should_be_empty_case(self):
        """The failure this case exists for: a dropped filter returning the
        whole ledger, reported as if it were the answer."""
        case = Case(key="z", question="How much on scuba?", why="test",
                    expect_zero=True)
        scored = score(case, run_with("You spent 2,111,933 on scuba lessons."),
                       expect_zero=True)
        assert not scored.passed
        assert scored.failure == "empty"


class TestAnAmbiguousQuestionMustBeAddressed:

    CASE = Case(key="food", question="How much do I spend on food?",
                why="test", must_mention=("dining", "groceries"))

    def test_naming_only_one_side_fails(self):
        scored = score(self.CASE, run_with("You spend 8,000 on groceries."))
        assert not scored.passed
        assert "dining" in scored.detail

    def test_naming_both_passes(self):
        scored = score(self.CASE, run_with(
            "Food splits two ways: dining 4,000 and groceries 8,000."))
        assert scored.passed, scored.detail

    def test_the_words_may_appear_anywhere_the_reader_sees_them(self):
        scored = score(self.CASE, run_with(
            "Food splits two ways.",
            findings=[{"title": "Dining", "detail": "4,000"},
                      {"title": "Groceries", "detail": "8,000"}]))
        assert scored.passed, scored.detail


# ---------------------------------------------------------------------------
# Ordering of the checks
# ---------------------------------------------------------------------------

class TestWhichFailureIsReported:
    """A case can fail several checks at once. Which one is named decides
    where the next person looks, so the order is part of the contract."""

    def test_an_unverified_figure_outranks_being_right(self):
        """Numerically correct and unsourced is still the failure this app
        is built against."""
        case = Case(key="c", question="q", why="test")
        run = run_with("You spent 5,500.")
        run.unverified = ["5,500"]
        scored = score(case, run, expected=Decimal(5500))
        assert scored.failure == "verified"

    def test_a_missing_tool_outranks_a_wrong_figure(self):
        case = Case(key="c", question="q", why="test",
                    needs_tools=("position",))
        scored = score(case, run_with("You owe 1,000.", tools=("ledger_query",)),
                       expected=Decimal(42781))
        assert scored.failure == "tools"
        assert "position" in scored.detail

    def test_a_run_with_no_answer_fails_first_of_all(self):
        case = Case(key="c", question="q", why="test")
        scored = score(case, FakeRun(answer=None, status="looping",
                                     error="stopped"))
        assert scored.failure == "answer"


# ---------------------------------------------------------------------------
# The set itself
# ---------------------------------------------------------------------------

class TestTheCasesAreWellFormed:

    def test_keys_are_unique(self):
        keys = [c.key for c in CASES]
        assert len(keys) == len(set(keys))

    def test_every_case_says_why_it_exists(self):
        """A failing case is read by someone deciding whether to change the
        prompt or the tool, and "it should get this right" is not enough to
        decide with."""
        for case in CASES:
            assert len(case.why) > 40, f"{case.key} does not justify itself"

    def test_every_case_asks_something(self):
        for case in CASES:
            assert case.question.strip().endswith("?"), case.key

    def test_a_case_expecting_zero_asserts_no_figure(self):
        """Both at once is contradictory - one of them would always fail."""
        for case in CASES:
            if case.expect_zero:
                assert case.truth is None, case.key

    def test_a_follow_up_has_a_question_to_follow(self):
        for case in CASES:
            if case.follow_up_truth is not None:
                assert case.follow_up, case.key
