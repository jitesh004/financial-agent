"""Run the cases against the real agent and score what comes back.

Scoring is deliberately coarse. There is no partial credit for an answer
that is nearly right about money: a figure either matches the ledger or it
does not, and a rubric that awards 0.7 for "close" is a rubric that lets a
wrong number ship.

What IS graded separately is why a case failed, because the fixes are
different: a wrong figure is a tool or a prompt problem, an unverified
figure is the model inventing, a missing tool is bad selection, and a
crash is neither.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .cases import CASES, Case

log = logging.getLogger(__name__)

#: How close a reported figure has to be to count as the same number.
#:
#: Not exact, because an answer may legitimately round - "about 5,500" for
#: 5,499.60 is correct English. One rupee of tolerance catches rounding
#: without admitting a different figure: nothing in a ledger is one rupee
#: away from something else by coincidence.
TOLERANCE = Decimal("1")


@dataclass
class CaseResult:
    key: str
    question: str
    passed: bool = False
    #: Which check failed, so the reader knows where to look. One of
    #: "answer", "figure", "verified", "tools", "empty", "error".
    failure: str = ""
    detail: str = ""
    headline: str = ""
    expected: Decimal | None = None
    #: (as written, value) for every quantity the answer stated.
    reported: list[tuple[str, Decimal]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    steps: int = 0
    tool_calls: int = 0
    seconds: float = 0.0
    unverified: list[str] = field(default_factory=list)
    tags: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key, "question": self.question, "passed": self.passed,
            "failure": self.failure, "detail": self.detail,
            "headline": self.headline,
            "expected": str(self.expected) if self.expected is not None else None,
            "reported": [w for w, _ in self.reported],
            "tools_used": self.tools_used, "steps": self.steps,
            "tool_calls": self.tool_calls, "seconds": round(self.seconds, 2),
            "unverified": self.unverified, "tags": list(self.tags),
        }


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def by_failure(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            if not r.passed:
                out[r.failure] = out.get(r.failure, 0) + 1
        return out

    def as_json(self) -> dict[str, Any]:
        return {"passed": self.passed, "total": self.total,
                "seconds": round(self.seconds, 2),
                "by_failure": self.by_failure(),
                "results": [r.as_json() for r in self.results]}

    def render(self) -> str:
        lines = [
            "",
            f"  {self.passed}/{self.total} passed"
            f"   ({self.seconds:.0f}s)",
            "",
        ]
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{mark}] {r.key}")
            lines.append(f"         {r.question}")
            if r.headline:
                lines.append(f"         -> {r.headline[:96]}")
            if not r.passed:
                lines.append(f"         {r.failure.upper()}: {r.detail}")
            lines.append(
                f"         {r.steps} steps, {r.tool_calls} tool calls, "
                f"{r.seconds:.1f}s, tools: {', '.join(r.tools_used) or 'none'}")
            lines.append("")
        if self.by_failure():
            lines.append("  failures by kind: "
                         + ", ".join(f"{k}={v}" for k, v in
                                     sorted(self.by_failure().items())))
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reading figures out of prose
# ---------------------------------------------------------------------------

def figures_in(answer: dict) -> list[tuple[str, Decimal]]:
    """Every quantity the answer states, from wherever it states it.

    The headline, the summary and the metrics all count. An agent that
    puts the right number only in a metric and a different one in the
    headline has still told the reader something false, so both are read.

    Reads prose through `verify.figures_in` rather than a regex of its own,
    so the harness and the thing it is grading agree on what a number is.
    They did not: the harness counted the 2026 in "August 2026" as a figure,
    which made the it-should-find-nothing case fail for reporting a number
    that was a year. Two definitions of "a number in a sentence" is one
    too many, and the verifier's is the one that has to be right.
    """
    from ..agents import verify
    text_parts = [str(answer.get("headline") or ""),
                  str(answer.get("summary") or "")]
    for metric in answer.get("metrics") or []:
        if isinstance(metric, dict):
            text_parts.append(str(metric.get("value") or ""))
    for finding in answer.get("findings") or []:
        if isinstance(finding, dict):
            text_parts.append(str(finding.get("detail") or ""))

    found: list[tuple[str, Decimal]] = []
    for part in text_parts:
        found.extend(verify.figures_in(part))
    return found


#: How an agent says it gave up. Distinct from how it says the answer is
#: zero, which is a real answer: "you spent nothing on fuel" reports the
#: ledger, "fuel spending could not be determined" reports the agent.
#:
#: Every one of these is a phrase a real run produced while scoring as a
#: pass, because the cases they belonged to assert tool choice rather than
#: a figure and the agent had reached for the right tools before failing.
#: A case that passes on "I could not work it out" cannot catch a
#: regression - it is already reporting the failure as success.
_GAVE_UP = (
    "could not", "cannot be determined", "can not be determined",
    "unable to", "do not contain", "does not contain", "not available",
    "no data was returned", "without new data", "insufficient",
    "tool call", "tool results", "repetition limit", "inaccessible",
)


def _gave_up(headline: str) -> str:
    """The phrase that makes this a non-answer, or empty if it is one."""
    low = headline.lower()
    return next((p for p in _GAVE_UP if p in low), "")


def _said(answer: dict) -> str:
    """Everything the answer put in front of the reader, as one string."""
    parts = [str(answer.get("headline") or ""), str(answer.get("summary") or "")]
    for metric in answer.get("metrics") or []:
        if isinstance(metric, dict):
            parts += [str(metric.get("label") or ""), str(metric.get("note") or "")]
    for finding in answer.get("findings") or []:
        if isinstance(finding, dict):
            parts += [str(finding.get("title") or ""),
                      str(finding.get("detail") or "")]
    return " ".join(parts)


def _significant(written: str) -> int:
    """How many significant digits the answer actually committed to.

    "41.2 lakh" claims three. "5,500" claims two - the zeros are placeholders,
    not measurements. Reading this off the text is what lets the comparison
    below forgive precision the answer never claimed without forgiving a
    figure that is simply different.
    """
    mantissa = re.split(r"[^\d.,]", written.strip(), maxsplit=1)[0]
    digits = mantissa.replace(",", "")
    if "." in digits:
        digits = digits.replace(".", "").lstrip("0")
    else:
        digits = digits.lstrip("0").rstrip("0")
    return len(digits)


def _to_significant(value: Decimal, sig: int) -> Decimal:
    if not value or sig <= 0:
        return value
    shift = value.copy_abs().adjusted() - sig + 1
    return value.scaleb(-shift).quantize(Decimal(1),
                                         rounding=ROUND_HALF_UP).scaleb(shift)


def _matches(expected: Decimal, reported: list[tuple[str, Decimal]]) -> bool:
    """Is the expected figure among those the answer states?

    Two ways to match, and deliberately only two.

    Exactly, within a rupee - nothing in a ledger is one rupee away from
    something else by coincidence.

    Or at the precision the answer chose to state. "41.2 lakh" and
    4,124,761.64 are the same figure written two ways, and an eval that
    rejected the first would be marking correct English wrong. So the
    expected figure is rounded to as many significant digits as the answer
    committed to, and compared there. This forgives lost precision and
    nothing else: 5,600 against 5,500 is two significant digits either way
    and still a different number.

    The scale words are already resolved by `verify.figures_in`, which is
    why this no longer multiplies by a thousand and a lakh looking for a
    hit - it did, after that function started handling them, and "41.2
    lakh" silently stopped matching anything at all.
    """
    for written, value in reported:
        if abs(value - expected) <= TOLERANCE:
            return True
        sig = _significant(written)
        if sig and _to_significant(expected, sig) == _to_significant(value, sig):
            return True
    return False


# ---------------------------------------------------------------------------
# Running one case
# ---------------------------------------------------------------------------

def _score(case: Case, question: str, expected: Decimal | None,
           result: Any, expect_zero: bool) -> CaseResult:
    answer = result.answer or {}
    tools = sorted({c["tool"] for s in result.steps for c in s.calls})
    scored = CaseResult(
        key=case.key, question=question, headline=str(answer.get("headline") or ""),
        expected=expected, reported=figures_in(answer), tools_used=tools,
        steps=len(result.steps), tool_calls=result.tool_calls,
        seconds=result.seconds, unverified=list(result.unverified),
        tags=case.tags,
    )

    if result.status not in {"ok"} or not scored.headline:
        scored.failure = "answer"
        scored.detail = (result.error
                         or f"the run ended '{result.status}' with no answer")
        return scored

    # A figure the tools never produced is the failure mode this whole app
    # is built against, and it outranks being numerically right.
    if result.unverified:
        scored.failure = "verified"
        scored.detail = ("reported figures no tool returned: "
                         + ", ".join(result.unverified[:4]))
        return scored

    missing = [t for t in case.needs_tools if t not in tools]
    if missing:
        scored.failure = "tools"
        scored.detail = (f"never called {', '.join(missing)} - used "
                         f"{', '.join(tools) or 'nothing'}")
        return scored

    used_banned = [t for t in case.avoids_tools if t in tools]
    if used_banned:
        scored.failure = "tools"
        scored.detail = f"should not have needed {', '.join(used_banned)}"
        return scored

    if expect_zero:
        # "Nothing matched" is the right answer. Any substantial figure
        # here means a filter was dropped and the whole ledger came back.
        large = [w for w, f in scored.reported if abs(f) > 1000]
        if large:
            scored.failure = "empty"
            scored.detail = (f"nothing should have matched, but it reported "
                             f"{large[0]}")
            return scored
        scored.passed = True
        return scored

    # An agent that says it could not work the answer out has not answered.
    # Checked AFTER expect_zero, because "you spent nothing" is a finding
    # about the ledger and belongs in the other branch entirely.
    excuse = _gave_up(scored.headline)
    if excuse:
        scored.failure = "answer"
        scored.detail = (f"did not answer - the headline says {excuse!r}: "
                         f"{scored.headline[:90]}")
        return scored

    missing_words = [w for w in case.must_mention
                     if w.lower() not in _said(answer).lower()]
    if missing_words:
        scored.failure = "answer"
        scored.detail = (f"never mentioned {', '.join(missing_words)}, which "
                         f"this question cannot be answered without")
        return scored

    if expected is not None:
        if not _matches(expected, scored.reported):
            scored.failure = "figure"
            scored.detail = (f"expected {expected}, answer stated "
                             + (", ".join(w for w, _ in scored.reported[:6])
                                or "no figure at all"))
            return scored

    scored.passed = True
    return scored


def run_case(case: Case, db, *, agent=None, client=None) -> list[CaseResult]:
    """Run one case, and its follow-up if it has one."""
    from ..agents import catalogue, runner

    agent = agent or catalogue.get("copilot")
    results: list[CaseResult] = []
    history: list[dict] = []

    turns: list[tuple[str, Any, bool]] = [
        (case.question, case.truth, case.expect_zero)]
    if case.follow_up:
        turns.append((case.follow_up, case.follow_up_truth, False))

    for index, (question, truth, expect_zero) in enumerate(turns):
        expected = None
        if truth is not None:
            try:
                expected = truth(db)
            except Exception as exc:        # a broken case is not a failure
                log.warning("case %s: ground truth failed: %s", case.key, exc)

        try:
            run = runner.run(agent, db, question=question, history=history,
                             client=client)
        except Exception as exc:
            results.append(CaseResult(
                key=case.key if index == 0 else f"{case.key}+follow-up",
                question=question, failure="error",
                detail=f"{type(exc).__name__}: {exc}", tags=case.tags))
            break

        scored = _score(case, question, expected, run, expect_zero)
        if index:
            scored.key = f"{case.key}+follow-up"
        results.append(scored)

        history.append({"question": question, "answer": run.answer or {}})

    return results


def run_evals(db, *, keys: list[str] | None = None, agent=None,
              client=None) -> EvalReport:
    """Run the set. `keys` narrows it to named cases."""
    wanted = [c for c in CASES if not keys or c.key in keys]
    report = EvalReport()
    started = time.monotonic()
    for case in wanted:
        report.results.extend(run_case(case, db, agent=agent, client=client))
    report.seconds = time.monotonic() - started
    return report
