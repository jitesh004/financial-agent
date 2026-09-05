"""The loop: ask, execute, feed back, stop.

Provider-agnostic on purpose. Native function calling is spelled three
different ways by the three providers this app supports and is absent from
several of the free models it is most likely to be pointed at, so the loop is
built on the one thing they all do: return a JSON object matching a schema.
The model replies with either tool calls or an answer, the calls are executed
here, and the results go back as the next turn's input.

Two properties matter more than elegance:

  It cannot run away. A step budget, a cap on calls per step, and a hard
  ceiling on how much tool output is carried forward. A model that loops -
  and they do, re-querying the same thing when a result surprises them - hits
  the budget and is asked for its answer with what it has.

  It cannot lose its work. Every step is recorded as it happens, so a run
  that fails on step six still has five steps of tool results, and the
  failure is reported with the transcript rather than as a bare error.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..llm.client import LLMClient, NARRATIVE_MODEL, get_client
from . import toolbelt
from . import verify
from .catalogue import COMPACT_RULES, SHARED_RULES, Agent

log = logging.getLogger(__name__)

#: How many tools the model may ask for in one turn.
MAX_CALLS_PER_STEP = 3


@dataclass(frozen=True)
class Budget:
    """What one run is allowed to spend, and how much detail it carries.

    A profile rather than a set of constants because the ceiling is not the
    same everywhere, and the difference is not small. Gemini's free tier
    meters INPUT TOKENS PER MINUTE - 16,000, shared across every call made in
    the same minute, which the provider layer had to learn the hard way (see
    llm.providers._token_budget_note). Measured against that, the settings
    this loop shipped with cost 91,250 input tokens for a single ten-step
    run: roughly six minutes of ceiling, spent in a burst, which in practice
    means a cascade of 429s and a run that never finishes.

    The fix is not to send less of everything by a fixed fraction. It is to
    decide, per model, how much room there is and then spend it where it
    buys the most - which for a small model is fewer, sharper steps over
    smaller results, and for a large one is the breadth it can actually use.
    """

    name: str
    #: Never more steps than this, whatever an agent asks for.
    max_steps: int
    #: How much of ONE tool result is carried into the next prompt.
    max_result_chars: int
    #: And how much of the transcript in total. Older results are dropped
    #: from the prompt (never from the stored run) once this is exceeded,
    #: oldest first, because the recent ones are what the reasoning is about.
    max_transcript_chars: int
    #: How many tools to offer. A small model handed nine picks badly, and
    #: every one it is offered costs prompt on every single turn.
    max_tools: int
    #: Whether to send the agent's full brief or its short focus.
    full_brief: bool
    #: Whether to include the worked example on tools that carry one.
    examples: bool

    def estimate(self, system_chars: int) -> int:
        """Roughly what a whole run costs, in input tokens.

        Four characters to the token, which is close enough for a budget and
        wrong in the safe direction for JSON, where it is nearer three.
        """
        total = 0
        for step in range(1, self.max_steps + 1):
            carried = min(self.max_transcript_chars,
                          step * self.max_result_chars)
            total += (system_chars + carried) // 4
        return total


#: For a small or rate-limited model. Sized so a whole run fits inside one
#: minute of Gemini's free-tier input-token budget, which is what makes an
#: agent usable there at all rather than a queue of 429s.
COMPACT = Budget(name="compact", max_steps=5, max_result_chars=1800,
                 max_transcript_chars=6000, max_tools=6, full_brief=False,
                 examples=False)

#: For a model with room to think. Close to what this loop shipped with.
FULL = Budget(name="full", max_steps=10, max_result_chars=8000,
              max_transcript_chars=40000, max_tools=12, full_brief=True,
              examples=True)

#: Words in a model's name that mean "small". The naming is a zoo and the
#: only thing the small ones reliably share is a word saying so.
#:
#: Matched on token boundaries, NOT as substrings, which is not a nicety:
#: "mini" is a substring of "geMINI", so a substring match put every Gemini
#: model - Pro included - on the compact budget. The boundary is any
#: non-alphanumeric, because these names are delimited by hyphens, dots and
#: nothing else.
_SMALL_MODEL_MARKERS = (
    "lite", "mini", "nano", "small", "tiny", "gemma", "phi", "haiku",
    "1b", "2b", "3b", "4b", "7b", "8b", "9b",
)

_IS_SMALL = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(_SMALL_MODEL_MARKERS) + r")(?![a-z0-9])",
    re.IGNORECASE,
)


def profile_for(model_name: str | None = None) -> Budget:
    """Which budget this deployment gets.

    `FA_AGENT_PROFILE` forces it; otherwise the model's own name decides.
    Defaulting to compact when the name is unknown is deliberate: compact
    still produces a sound answer on a large model - it simply looks at
    fewer things - whereas full on a small one produces no answer at all.
    """
    from ..config import config

    forced = (config.AGENT_PROFILE or "auto").strip().lower()
    if forced == "compact":
        return COMPACT
    if forced == "full":
        return FULL

    name = (model_name or _configured_model()).lower()
    if not name:
        return COMPACT
    return COMPACT if _IS_SMALL.search(name) else FULL


def _configured_model() -> str:
    """The model this deployment will actually call, whoever provides it."""
    from ..config import config

    provider = (config.LLM_PROVIDER or "").lower()
    if provider == "gemini":
        return config.GEMINI_MODEL_STRONG or ""
    if provider == "openrouter":
        return config.OPENROUTER_MODEL_STRONG or ""
    if provider == "azure":
        return getattr(config, "AZURE_OPENAI_DEPLOYMENT_STRONG", "") or ""
    return ""


@dataclass
class Step:
    index: int
    thought: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    error: str = ""


@dataclass
class AgentRun:
    agent: str
    status: str  # "ok" | "failed" | "exhausted"
    answer: dict[str, Any] | None = None
    steps: list[Step] = field(default_factory=list)
    tool_calls: int = 0
    error: str = ""
    seconds: float = 0.0
    model: str = ""
    started_at: str = ""
    finished_at: str = ""
    #: Which budget this run was given, and roughly what it cost. Recorded
    #: because "the agent only looked at three things" is a fact about the
    #: profile rather than about the ledger, and the reader is owed it.
    profile: str = ""
    prompt_chars: int = 0
    #: Money figures in the answer that no tool result contains - see
    #: agents.verify. Empty is the normal case and the one worth trusting.
    unverified: list[str] = field(default_factory=list)
    figures_checked: int = 0


class AgentUnavailable(RuntimeError):
    """No model is configured, so there is nothing to run."""


# ---------------------------------------------------------------------------
# The reply contract
# ---------------------------------------------------------------------------

#: What the model is allowed to return. Both shapes in one schema because a
#: provider's structured-output mode takes exactly one, and the model chooses
#: between them by which key it fills - `calls` to keep going, `answer` to
#: stop. Nothing here is `required` beyond `thought`: a strict schema that
#: demanded both would force the model to emit an empty answer alongside its
#: tool calls, which reads as a finished run.
REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["tool"],
            },
        },
        "answer": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "unit": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": ["label", "value"],
                    },
                },
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "detail": {"type": "string"},
                            "severity": {"type": "string",
                                         "enum": ["info", "watch", "urgent"]},
                            "evidence": {"type": "array",
                                         "items": {"type": "string"}},
                        },
                        "required": ["title", "detail"],
                    },
                },
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "detail": {"type": "string"},
                            "mechanism": {"type": "string"},
                            "effort": {"type": "string",
                                       "enum": ["low", "medium", "high"]},
                        },
                        "required": ["title"],
                    },
                },
                "caveats": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["headline", "summary"],
        },
    },
    "required": ["thought"],
}


def _json_default(obj: Any) -> Any:
    from datetime import date
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return float(round(obj, 2))
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    return str(obj)


def _dump(value: Any, limit: int) -> str:
    text = json.dumps(value, default=_json_default, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return (text[:limit]
            + f'… [truncated at {limit} characters of {len(text)}; narrow the '
              f'query or raise a filter to see the rest]')


def _system(agent: Agent, budget: Budget = FULL) -> str:
    """The instruction, sized to the budget.

    Three things shrink together and they have to, because the system prompt
    is re-sent on EVERY turn - so a kilobyte here is a kilobyte times the
    step count. The rules lose their worked examples, the agent sends its
    focus rather than its full brief, and the tool catalogue drops to the
    ones that agent leads with.

    None of that is truncation. Each agent writes both a focus and a brief,
    and a prompt cut off mid-sentence produces reasoning cut off mid-thought
    - which is the failure this exists to avoid, not a cheaper version of it.
    """
    offered = list(agent.tools)[:budget.max_tools]
    tools = toolbelt.describe(offered, examples=budget.examples)
    rules = SHARED_RULES if budget.full_brief else COMPACT_RULES
    job = agent.brief if budget.full_brief else (agent.focus or agent.brief)
    return (
        f"{rules}\n\n"
        f"YOUR JOB\n{job}\n\n"
        f"YOUR TOOLS\n"
        f"{json.dumps(tools, separators=(',', ':'), default=_json_default)}\n"
    )


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def run(
    agent: Agent,
    db,
    *,
    client: LLMClient | None = None,
    question: str = "",
    budget: Budget | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> AgentRun:
    """Run one agent to an answer, or to the end of its step budget."""
    client = client or get_client(NARRATIVE_MODEL)
    if not client.available:
        raise AgentUnavailable(
            "No language model is configured, so agents cannot run. Add a "
            "provider and key on the Settings tab.")

    budget = budget or profile_for()
    started = time.monotonic()
    run_record = AgentRun(agent=agent.key, status="failed",
                          model=NARRATIVE_MODEL, profile=budget.name,
                          started_at=_now())
    #: Everything the tools returned, as numbers, so the answer can be
    #: checked against them at the end - see agents.verify.
    figures: set = set()

    def progress(label: str) -> None:
        if on_progress:
            try:
                on_progress(label)
            except Exception:  # pragma: no cover - reporting must never break
                log.debug("progress callback failed", exc_info=True)

    system = _system(agent, budget)
    steps_allowed = min(agent.max_steps, budget.max_steps)
    transcript: list[str] = []

    # The opening facts. Fetched before the first turn rather than left to the
    # model, because every agent's first call is the same obvious one and
    # spending a whole round trip on it buys nothing.
    if agent.opening:
        progress("Reading your ledger")
        opening = Step(index=0, thought="(opening facts, fetched for you)")
        for name in agent.opening:
            result = toolbelt.call(db, name, {})
            opening.calls.append({"tool": name, "args": {}})
            opening.results.append({"tool": name, "result": result})
            verify.collect_figures(result, figures)
            run_record.tool_calls += 1
        run_record.steps.append(opening)
        transcript.append(
            "Fetched for you before you started:\n"
            + "\n".join(
                f"{c['tool']} -> {_dump(r['result'], budget.max_result_chars)}"
                for c, r in zip(opening.calls, opening.results)))

    task = (question.strip() or agent.question)

    for index in range(1, steps_allowed + 1):
        last_turn = index == steps_allowed
        progress(f"Thinking (step {index} of {steps_allowed})")
        step = Step(index=index)
        turn_started = time.monotonic()

        prompt = _prompt(task, transcript, index, steps_allowed, last_turn)
        run_record.prompt_chars += len(system) + len(prompt)
        try:
            reply = client.complete_json(
                prompt, system=system, schema=REPLY_SCHEMA, max_tokens=8000)
        except Exception as exc:
            step.error = f"{type(exc).__name__}: {exc}"
            step.seconds = round(time.monotonic() - turn_started, 2)
            run_record.steps.append(step)
            run_record.error = step.error
            log.warning("agent %s failed on step %d: %s", agent.key, index, exc)
            break

        if not isinstance(reply, dict):
            step.error = f"the model returned a {type(reply).__name__}, not an object"
            step.seconds = round(time.monotonic() - turn_started, 2)
            run_record.steps.append(step)
            run_record.error = step.error
            break

        step.thought = str(reply.get("thought") or "")[:400]
        answer = reply.get("answer")
        calls = reply.get("calls") or []

        # An answer wins over calls in the same reply: a model that fills both
        # has decided, and running the calls it also asked for would only
        # produce results nothing reads.
        if isinstance(answer, dict) and answer.get("headline"):
            step.seconds = round(time.monotonic() - turn_started, 2)
            run_record.steps.append(step)
            run_record.answer = _clean_answer(answer)
            run_record.status = "ok"
            break

        if not isinstance(calls, list) or not calls:
            # Neither a decision nor a question. Rather than burn the run,
            # say so and let it try again - a model that returns an empty
            # object once usually recovers when told.
            step.error = "no tool calls and no answer"
            step.seconds = round(time.monotonic() - turn_started, 2)
            run_record.steps.append(step)
            transcript.append(
                "Your last reply contained neither `calls` nor a complete "
                "`answer`. Return one or the other.")
            continue

        progress(f"Checking the numbers (step {index})")
        lines: list[str] = []
        for requested in calls[:MAX_CALLS_PER_STEP]:
            if not isinstance(requested, dict):
                continue
            name = str(requested.get("tool") or "")
            args = requested.get("args")
            if name not in agent.tools:
                result: Any = {
                    "error": f"{name!r} is not one of your tools.",
                    "your_tools": list(agent.tools)}
            else:
                result = toolbelt.call(db, name, args)
                verify.collect_figures(result, figures)
                run_record.tool_calls += 1
            step.calls.append({"tool": name, "args": args if isinstance(args, dict) else {}})
            step.results.append({"tool": name, "result": result})
            lines.append(f"{name}({_dump(args, 400)}) -> "
                         f"{_dump(result, budget.max_result_chars)}")

        step.seconds = round(time.monotonic() - turn_started, 2)
        run_record.steps.append(step)
        transcript.append(
            f"Step {index}. You said: {step.thought}\n" + "\n".join(lines))
        transcript = _trim(transcript, budget.max_transcript_chars)
    else:
        # The loop finished without breaking, so the budget ran out.
        run_record.status = "exhausted"

    if run_record.answer is None and run_record.status != "failed":
        run_record.status = "exhausted"
        run_record.error = run_record.error or (
            f"The agent used all {steps_allowed} of its steps without "
            f"reaching an answer.")

    # Every money figure in the answer, against everything the tools said.
    # Mechanical, and the last thing that happens - see agents.verify for
    # why it is money only and why nothing is deleted.
    if run_record.answer:
        report = verify.check(run_record.answer, figures)
        run_record.unverified = report.unverified
        run_record.figures_checked = report.checked
        caveat = verify.caveat_for(report)
        if caveat:
            run_record.answer.setdefault("caveats", []).append(caveat)

    run_record.seconds = round(time.monotonic() - started, 2)
    run_record.finished_at = _now()
    return run_record


def _prompt(task: str, transcript: list[str], index: int, budget: int,
            last_turn: bool) -> str:
    parts = [f"THE QUESTION\n{task}\n"]
    if transcript:
        parts.append("WHAT YOU HAVE LOOKED AT SO FAR\n"
                     + "\n\n".join(transcript))
    if last_turn:
        parts.append(
            "This is your LAST turn - there is no step after it, and any "
            "tool calls you ask for now will not be run. Return your "
            "`answer` using what you already have, and say in `caveats` what "
            "you did not get to check.")
    else:
        parts.append(
            f"This is step {index} of at most {budget}. Call more tools if "
            f"you genuinely need them, or return your `answer` now if you "
            f"have enough. Do not re-run a call whose result is already "
            f"above.")
    return "\n\n".join(parts)


def _trim(transcript: list[str], limit: int) -> list[str]:
    """Keep the prompt inside its budget, dropping the oldest results first.

    Dropped from the PROMPT only - the run keeps every step, so nothing the
    user is shown is lost. What the model loses is the detail of an early
    query it has presumably already drawn its conclusion from, which is a
    better thing to lose than the ability to think about the last one.
    """
    total = sum(len(t) for t in transcript)
    if total <= limit:
        return transcript
    kept = list(transcript)
    while len(kept) > 1 and total > limit:
        total -= len(kept.pop(0))
    return ["[earlier steps dropped to save room; do not repeat calls you "
            "have already made]", *kept]


def _clean_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """Normalise the answer's shape so the UI never has to guess.

    A model that is nearly right about a schema is the common case - a string
    where a list belongs, a missing severity, an action with no effort - and
    every one of those would otherwise become a render-time crash on a run
    the user has already waited for.
    """
    def as_list(value: Any) -> list:
        if isinstance(value, list):
            return value
        if value in (None, "", {}):
            return []
        return [value]

    def strings(value: Any) -> list[str]:
        return [str(v) for v in as_list(value) if str(v).strip()]

    findings = []
    for raw in as_list(answer.get("findings")):
        if not isinstance(raw, dict):
            continue
        severity = str(raw.get("severity") or "info").lower()
        findings.append({
            "title": str(raw.get("title") or "").strip(),
            "detail": str(raw.get("detail") or "").strip(),
            "severity": severity if severity in {"info", "watch", "urgent"}
                        else "info",
            "evidence": strings(raw.get("evidence")),
        })

    actions = []
    for raw in as_list(answer.get("actions")):
        if not isinstance(raw, dict):
            continue
        effort = str(raw.get("effort") or "medium").lower()
        actions.append({
            "title": str(raw.get("title") or "").strip(),
            "detail": str(raw.get("detail") or "").strip(),
            "mechanism": str(raw.get("mechanism") or "").strip(),
            "effort": effort if effort in {"low", "medium", "high"} else "medium",
        })

    metrics = []
    for raw in as_list(answer.get("metrics")):
        if not isinstance(raw, dict):
            continue
        metrics.append({
            "label": str(raw.get("label") or "").strip(),
            "value": str(raw.get("value") or "").strip(),
            "unit": str(raw.get("unit") or "").strip(),
            "note": str(raw.get("note") or "").strip(),
        })

    return {
        "headline": str(answer.get("headline") or "").strip(),
        "summary": str(answer.get("summary") or "").strip(),
        "metrics": [m for m in metrics if m["label"]],
        "findings": [f for f in findings if f["title"]],
        "actions": [a for a in actions if a["title"]],
        "caveats": strings(answer.get("caveats")),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Comparing two runs
# ---------------------------------------------------------------------------

def diff(current: dict[str, Any] | None,
         previous: dict[str, Any] | None) -> dict[str, Any]:
    """What changed between this run and the one before it.

    This is what makes re-running an agent worth doing. "Your EMIs are 43% of
    take-home" is a fact; "your EMIs were 47% when this last ran in March" is
    the thing somebody actually wants to know, and neither the model nor the
    ledger can produce it - only two runs side by side can.

    Matched on the metric LABEL and the finding TITLE, which the model writes
    freely, so the match is deliberately forgiving about case and spacing. A
    label it words differently this time reads as one metric gone and one
    new, which is honest: the two figures may not be the same figure.
    """
    if not current or not previous:
        return {"available": False}

    def key(text: str) -> str:
        return " ".join(str(text).lower().split())

    def number(value: Any) -> float | None:
        cleaned = "".join(c for c in str(value)
                          if c.isdigit() or c in ".-").strip(".-")
        try:
            return float(cleaned)
        except ValueError:
            return None

    before = {key(m["label"]): m for m in previous.get("metrics", [])}
    moved = []
    for metric in current.get("metrics", []):
        was = before.get(key(metric["label"]))
        if was is None:
            continue
        now_value, then_value = number(metric["value"]), number(was["value"])
        if now_value is None or then_value is None or now_value == then_value:
            continue
        moved.append({
            "label": metric["label"], "unit": metric.get("unit", ""),
            "now": metric["value"], "then": was["value"],
            "delta": round(now_value - then_value, 2),
            "direction": "up" if now_value > then_value else "down",
        })

    now_findings = {key(f["title"]): f for f in current.get("findings", [])}
    then_findings = {key(f["title"]): f for f in previous.get("findings", [])}
    return {
        "available": True,
        "metrics_moved": moved,
        "new_findings": [f["title"] for k, f in now_findings.items()
                         if k not in then_findings],
        "resolved_findings": [f["title"] for k, f in then_findings.items()
                              if k not in now_findings],
        "unchanged_findings": sum(1 for k in now_findings if k in then_findings),
    }
