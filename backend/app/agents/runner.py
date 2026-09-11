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
    from ..llm import settings as llm_settings

    forced = (llm_settings.effective()["agent_profile"] or "auto").strip().lower()
    if forced == "compact":
        return COMPACT
    if forced == "full":
        return FULL

    name = (model_name or _configured_model()).lower()
    if not name:
        return COMPACT
    return COMPACT if _IS_SMALL.search(name) else FULL


def _configured_model() -> str:
    """The model this workspace will actually call, whoever provides it.

    Empty when no implemented provider is selected: "no model" and "a model
    whose name says nothing" both have to land on the compact budget, and only
    an empty answer here gets them both there.
    """
    from ..llm import settings as llm_settings

    provider = llm_settings.selected_provider()
    if provider not in llm_settings.BY_KEY:
        return ""
    return (llm_settings.model_for("strong", provider)
            or llm_settings.model_for("fast", provider))

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
    #: True when the answer came from the last-chance turn rather than from
    #: the agent deciding it was done. The figures in it were still produced
    #: by tools and still checked, but the agent had stopped making progress
    #: when it was asked to conclude - which the reader should know.
    partial: bool = False


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
                    # The arguments, as a JSON STRING.
                    #
                    # Not as an object, which is what this used to be and
                    # is the obvious way to write it. Google's structured
                    # output takes an OpenAPI subset where an OBJECT with
                    # no declared properties has nothing it is PERMITTED
                    # to emit - so it returns `{}`, every time, however
                    # clearly the model said what it wanted. Verified
                    # against the live API: the same request answered
                    # `{"args": {}}` under an object schema and
                    # `{"args_json": "{\"text\": \"fuel\", ...}"}`
                    # under this one.
                    #
                    # That silently disarmed every tool that takes
                    # arguments. `ledger_query` ran unfiltered over the
                    # whole ledger on every call an agent ever made, and
                    # `search_transactions` returned whatever came first -
                    # so an agent asked about August fuel was reading the
                    # same all-time totals as one asked about anything
                    # else, and reasoning confidently over them.
                    #
                    # `args` is kept and still honoured: OpenRouter and
                    # Azure handle a bare object correctly, and a model
                    # that fills it is not wrong.
                    "args_json": {"type": "string"},
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


def _shrink(value: Any, limit: int) -> Any:
    """Drop whole rows from the longest list until the result fits.

    Character truncation is the wrong tool for a result shaped like
    `{"accounts": [...22 of them...], "count": 22}`. Cut at 1,800 characters
    it becomes JSON that stops mid-object, and a model handed that reads it
    as a broken tool rather than a long one: asked how many credit cards
    this holder has, one read a severed account list and reported that the
    records were inaccessible. They were not - there were 22 of them.

    Dropping rows keeps the structure intact and keeps the parts that are
    not rows - `count`, `range`, `truncated` - which are frequently the
    whole answer. What is lost is said in place, in the list, where a model
    reading the list will see it.
    """
    if not isinstance(value, dict):
        return None
    lists = [(k, v) for k, v in value.items()
             if isinstance(v, list) and len(v) > 1]
    if not lists:
        return None
    key, rows = max(lists, key=lambda kv: len(_json(kv[1])))

    def marker(kept: int) -> str:
        return (f"[{len(rows) - kept} of {len(rows)} not shown - too long "
                f"for one result. Filter the call to see the rest; any "
                f"count or total alongside this list still covers all "
                f"{len(rows)}.]")

    def attempt(kept: int) -> dict:
        out = dict(value)
        out[key] = rows[:kept] + [marker(kept)]
        return out

    # Measured with the REAL marker, not a short stand-in. Measuring a
    # placeholder and emitting a sentence is how this overflowed the limit
    # on its first outing and fell straight back to cutting characters -
    # which is the thing it exists to avoid.
    low, high, best = 0, len(rows) - 1, -1
    while low <= high:
        mid = (low + high) // 2
        if len(_json(attempt(mid))) <= limit:
            best, low = mid, mid + 1
        else:
            high = mid - 1

    # Even zero rows plus the marker does not fit; there is nothing useful
    # to hand back and the caller should truncate instead.
    return attempt(best) if best >= 0 else None


def _json(value: Any) -> str:
    return json.dumps(value, default=_json_default, separators=(",", ":"))


def _dump(value: Any, limit: int) -> str:
    text = _json(value)
    if len(text) <= limit:
        return text

    shrunk = _shrink(value, limit)
    if shrunk is not None:
        trimmed = _json(shrunk)
        if len(trimmed) <= limit:
            return trimmed

    return (text[:limit]
            + f'… [truncated at {limit} characters of {len(text)}; narrow the '
              f'query or raise a filter to see the rest]')


#: What the model is allowed to return when it is out of steps. Tools are
#: not on the menu, so the only shape left is an answer.
_FINAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": dict(REPLY_SCHEMA["properties"]["answer"]["properties"]),
    "required": ["headline"],
}


def _last_chance(client, system: str, task: str, transcript: list[str],
                 run_record: "AgentRun", agent: Agent, event) -> dict | None:
    """One turn to conclude, with the tools taken away.

    Called only when a run stopped without answering. Everything the tools
    returned is still in the transcript; what the agent failed at was
    deciding it had enough, which is a different failure from not having
    enough and should not be handed to the reader as the same blank page.

    Given no `calls` key to fill, so there is nothing to do but answer or
    say what is missing. Returns None on any failure - a last chance that
    breaks is simply a run with no answer, which is where it already was.
    """
    event("model", "Out of steps - asking for a conclusion",
          detail="no tools offered; answer from what the run already has")
    prompt = (
        f"The question: {task}\n\n"
        f"What the tools returned:\n" + "\n".join(transcript) + "\n\n"
        "You are out of steps and there are no more tool calls available. "
        "Answer the question NOW using only the results above.\n"
        "- Use only figures that appear above. Do not calculate new ones "
        "and do not estimate.\n"
        "- If the results do not answer the question, say exactly that in "
        "the headline and put what is missing in `caveats`. That is a "
        "useful answer; an invented figure is not."
    )
    try:
        from ..llm import telemetry
        with telemetry.purpose("agent", f"{agent.key} · conclude"):
            reply = client.complete_json(prompt, system=system,
                                         schema=_FINAL_SCHEMA, max_tokens=4000)
    except Exception as exc:
        log.warning("agent %s: last-chance turn failed: %s", agent.key, exc)
        return None
    if not isinstance(reply, dict) or not reply.get("headline"):
        return None
    event("answer", "Concluded from what the run already had",
          detail=str(reply.get("headline"))[:200])
    return _clean_answer(reply)


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
    history: list[dict] | None = None,
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

    #: (tool, arguments) -> (step it was first answered on, its result).
    #:
    #: Every agent tool is a read, and nothing writes to the ledger while a
    #: run is in flight - so the same call with the same arguments has the
    #: same answer for the whole run, and asking twice buys nothing.
    #:
    #: The step-signature guard below cannot see this. It compares the
    #: WHOLE set a step asked for, so a model that re-reads one tool while
    #: varying its companions never trips it: a real run called
    #: `recurring({})` on steps 0, 1, 2 and 3 - the same 9,016-character
    #: result four times - and only the last pair matched. Three wasted
    #: reads, and 27,000 characters of duplicated transcript, which is what
    #: grew that run's prompt from 18,000 characters to 46,000.
    answered: dict[tuple[str, str], tuple[int, Any]] = {}

    # The opening facts. Fetched before the first turn rather than left to the
    # model, because every agent's first call is the same obvious one and
    # spending a whole round trip on it buys nothing.
    if agent.opening:
        progress("Reading your ledger")
        opening = Step(index=0, thought="(opening facts, fetched for you)")
        for name in agent.opening:
            result = toolbelt.call(db, name, {})
            opening.calls.append({"tool": name, "args": {},
                                  "repeat_of_step": None})
            opening.results.append({"tool": name, "result": result,
                                    "repeat_of_step": None})
            verify.collect_figures(result, figures)
            run_record.tool_calls += 1
            # Seeded, not just recorded. These are the obvious first calls -
            # which is exactly why the model asks for them again on step
            # one, and on the run that prompted this it did: `recurring`
            # was fetched here and re-read three more times.
            answered[(name, _dump({}, 400))] = (0, result)
        run_record.steps.append(opening)
        transcript.append(
            "Fetched for you before you started:\n"
            + "\n".join(
                f"{c['tool']} -> {_dump(r['result'], budget.max_result_chars)}"
                for c, r in zip(opening.calls, opening.results)))

    task = (question.strip() or agent.question)
    #: How many steps running have taught the model nothing new. See the
    #: loop guard below for what "nothing new" means and why it is not the
    #: same question as "the same tools again".
    repeats = 0
    #: Calls the MODEL made that returned something the run did not already
    #: have. Distinct from `run_record.tool_calls`, which also counts the
    #: opening facts - those are fetched for the model, not by it, and a run
    #: cannot be accused of going in circles on the strength of work it did
    #: not do.
    learned = 0

    def event(kind: str, text: str, *, detail: str = "", step: int = 0,
              ok: bool = True) -> None:
        """Report one thing happening, as it happens.

        `on_progress` has always taken a bare string, and a bare string is
        all the phase line needs. A reader watching a run needs more than a
        phase: which tool, with what arguments, how long it took, and
        whether the pause they are looking at is a model call or a query.

        Passed as a dict to callers that accept one and flattened to the
        old string for those that do not, so nothing that already listens
        has to change.
        """
        if on_progress is None:
            return
        try:
            on_progress({"kind": kind, "text": text, "detail": detail,
                         "step": step, "ok": ok})
        except TypeError:
            on_progress(f"{text}{f' - {detail}' if detail else ''}")
        except Exception:               # pragma: no cover - reporting only
            pass

    for index in range(1, steps_allowed + 1):
        last_turn = index == steps_allowed
        progress(f"Thinking (step {index} of {steps_allowed})")
        step = Step(index=index)
        turn_started = time.monotonic()

        prompt = _prompt(task, transcript, index, steps_allowed, last_turn,
                         history=history)
        run_record.prompt_chars += len(system) + len(prompt)
        # Said before the call, not after. A model call is the slowest thing
        # in a run - ten to thirty seconds - and for all of it the screen
        # used to read "Thinking (step 3 of 10)" with no indication that
        # anything was in flight, which is indistinguishable from a hang.
        event("model", f"Asking the model (step {index} of {steps_allowed})",
              detail=f"{len(system) + len(prompt):,} characters of prompt")
        try:
            # Named down to the STEP. "an agent called the model" is not a
            # useful record when a single run makes ten calls of wildly
            # different sizes - this run's prompt grew from 18,000
            # characters to 46,000 across four steps, and the only way to
            # see that is per step.
            from ..llm import telemetry
            with telemetry.purpose("agent",
                                   f"{agent.key} · step {index}"):
                reply = client.complete_json(
                    prompt, system=system, schema=REPLY_SCHEMA,
                    max_tokens=8000)
        except Exception as exc:
            event("error", f"The model call failed on step {index}",
                  detail=f"{type(exc).__name__}: {exc}")
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
        if step.thought:
            # The model's own account of what it is about to do. The single
            # most useful line in a run and it was only ever visible after
            # the run had finished, behind a collapsed panel.
            event("thought", step.thought, step=index)

        # An answer wins over calls in the same reply: a model that fills both
        # has decided, and running the calls it also asked for would only
        # produce results nothing reads.
        if isinstance(answer, dict) and answer.get("headline"):
            event("answer", "The agent reached an answer",
                  detail=str(answer.get("headline"))[:200], step=index)
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
        #: Tool results this step that the run did not already have. The
        #: loop guard reads this and nothing else.
        new_results = 0
        for requested in calls[:MAX_CALLS_PER_STEP]:
            if not isinstance(requested, dict):
                continue
            name = str(requested.get("tool") or "")
            args = _arguments(requested)
            memo_key = (name, _dump(args, 400))
            repeat_of = answered.get(memo_key)

            if name not in agent.tools:
                result: Any = {
                    "error": f"{name!r} is not one of your tools.",
                    "your_tools": list(agent.tools)}
                event("tool", f"Refused {name or '(unnamed)'}",
                      detail="not one of this agent's tools", step=index,
                      ok=False)
            elif repeat_of is not None:
                # Already answered this run. Not re-run, and - the part that
                # actually costs - not re-appended to the transcript: the
                # result is already in there once, and a second copy only
                # crowds out the reasoning the model needs to see.
                earlier, result = repeat_of
                event("tool", f"{name}({_dump(args, 120)})",
                      detail=f"already answered at step {earlier + 1}; "
                             f"served from that result",
                      step=index)
            else:
                call_started = time.monotonic()
                result = toolbelt.call(db, name, args)
                verify.collect_figures(result, figures)
                run_record.tool_calls += 1
                new_results += 1
                learned += 1
                answered[memo_key] = (index - 1, result)
                # Named individually, with what it was asked and how long it
                # took. "23 tool calls" at the end of a run says nothing
                # about which of them was slow or which returned nothing.
                event("tool", f"{name}({_dump(args, 120)})",
                      detail=f"{_dump(result, 160)} "
                             f"[{time.monotonic() - call_started:.2f}s]",
                      step=index)

            step.calls.append({"tool": name,
                               "args": args if isinstance(args, dict) else {},
                               # Recorded rather than hidden: the model DID
                               # ask, and a transcript that quietly drops
                               # the request misrepresents what it did.
                               "repeat_of_step": (repeat_of[0] + 1
                                                  if repeat_of else None)})
            step.results.append({"tool": name, "result": result,
                                 "repeat_of_step": (repeat_of[0] + 1
                                                    if repeat_of else None)})
            if repeat_of is not None:
                lines.append(
                    f"{name}({_dump(args, 400)}) -> you already have this "
                    f"from step {repeat_of[0] + 1}; it has not changed. Use "
                    f"that result rather than asking again.")
            else:
                lines.append(f"{name}({_dump(args, 400)}) -> "
                             f"{_dump(result, budget.max_result_chars)}")

        step.seconds = round(time.monotonic() - turn_started, 2)
        run_record.steps.append(step)

        # A model that learns nothing from a step is stuck, and the only
        # thing left to spend is the budget. Half of all runs on this
        # workspace ended that way - steps 4 through 8 issuing the same
        # request with the same reasoning, then a silent give-up at step 10.
        # Say so the first time and stop the second.
        #
        # What counts as stuck is NEW INFORMATION, not a repeated tool name.
        # This guard used to compare `sorted(tool names)` between steps,
        # which was a fair proxy only for as long as arguments never
        # arrived: with every call carrying empty arguments, the same tool
        # twice really was the same call twice. The moment arguments started
        # working it became wrong in the most damaging direction - asking
        # `ledger_query` for dining and then for groceries is two different
        # questions and exactly how "how much do I spend on food?" has to be
        # answered, and the guard was killing those runs outright.
        #
        # The memo already knows the honest answer: a call whose result was
        # served from an earlier step produced nothing new, and a step where
        # every call was served that way is a step that went nowhere.
        if step.calls and not new_results:
            repeats += 1
            # A run has not STOPPED making progress until it has made some.
            # The memo is seeded with the opening facts, and for most agents
            # the opening tools are the first ones listed - so a model that
            # politely starts by asking for exactly what it was already
            # handed trips two no-progress steps before it has made a single
            # call of its own, and the run died at step 2. That is a
            # confused opening, not a loop, and the nudge below is the right
            # response to it. Killing the run is reserved for a model that
            # has been somewhere and stopped going anywhere.
            if repeats >= 2 and learned:
                run_record.status = "looping"
                run_record.error = (
                    "Asked for the same data three times without reaching "
                    "a conclusion, so the run was stopped rather than left "
                    "to use up its remaining steps.")
                log.warning("agent %s learned nothing new for two steps; "
                            "stopped at step %d", agent.key, index)
                break
            transcript.append(
                "Every tool you just called had already been answered this "
                "run, so nothing has changed. Do not call them again - "
                "answer with what you already have, or say what is missing.")
        else:
            repeats = 0
        transcript.append(
            f"Step {index}. You said: {step.thought}\n" + "\n".join(lines))
        transcript = _trim(transcript, budget.max_transcript_chars)
    else:
        # The loop finished without breaking, so the budget ran out.
        run_record.status = "exhausted"

    # A run that stopped without answering still gathered everything it
    # gathered. Throwing that away and showing the reader "the agent used
    # all of its steps" is the least useful thing to do with it: the tool
    # results that would have answered the question are usually already
    # sitting in the transcript, and what failed was the deciding, not the
    # looking.
    #
    # So one last turn, with no tools on offer and no option to ask for
    # any - answer from what is here, or say plainly what is missing. It
    # costs a single request and it is the difference between a dead run
    # and a qualified answer. It is deliberately NOT attempted when the
    # model itself failed: if the call is erroring, another call is not a
    # recovery, it is the same error again.
    if (run_record.answer is None
            and run_record.status in {"looping", "exhausted"}
            and run_record.tool_calls):
        run_record.answer = _last_chance(
            client, system, task, transcript, run_record, agent, event)
        if run_record.answer is not None:
            run_record.status = "ok"
            run_record.partial = True

    # "looping" is a diagnosis, not a synonym for running out - it says the
    # run was CUT SHORT because it had stopped making progress, and burying
    # that under "exhausted" would hide the one detail that explains why
    # there is no answer.
    if run_record.answer is None and run_record.status not in {"failed",
                                                               "looping"}:
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
            # Also as structured data, so the screen can put it ABOVE the
            # headline instead of in a muted list underneath it. The check
            # worked on the run that reported a 14,34,500 debt this holder
            # does not have - it named all three invented figures - but the
            # invention was the first thing on screen in large type and the
            # correction was the last thing in small grey text.
            run_record.answer["unverified_figures"] = list(report.unverified)

    run_record.seconds = round(time.monotonic() - started, 2)
    run_record.finished_at = _now()
    return run_record


#: How many earlier exchanges a follow-up can see.
#:
#: Four, because a follow-up refers to the turn before it and occasionally
#: the one before that - and because history is re-sent on EVERY step of
#: EVERY turn. One run already grew its prompt from 18,000 characters to
#: 46,000 across four steps; multiplying that by an unbounded conversation
#: is how a chat feature becomes slow, expensive and finally too long to
#: send at all.
HISTORY_TURNS = 4

#: How much of one earlier turn is worth carrying.
HISTORY_ANSWER_CHARS = 400


def _history_block(history: list[dict]) -> str:
    """Earlier turns, compressed to what a follow-up actually needs.

    The QUESTION and the HEADLINE, not the working. What "that" and "the
    same period" refer to lives in those two lines; the tool results
    behind them are large, already spent, and re-sending them would crowd
    out the reasoning for the question actually being asked.

    Figures are deliberately NOT carried as fact - see the brief. They are
    here so the model can tell what is being referred to, and it is told
    to re-query rather than reuse.
    """
    lines = []
    for turn in history[-HISTORY_TURNS:]:
        question = str(turn.get("question") or "").strip()
        answer = turn.get("answer") or {}
        headline = str(answer.get("headline") or "").strip()
        if not question:
            continue
        entry = f"Q: {question[:300]}"
        if headline:
            entry += f"\nA: {headline[:HISTORY_ANSWER_CHARS]}"
        elif turn.get("error"):
            entry += "\nA: (that question could not be answered)"
        lines.append(entry)
    return "\n\n".join(lines)


def _arguments(requested: dict) -> dict:
    """The arguments for one call, from whichever field carries them.

    `args_json` first, because that is the one Google's structured output
    can actually populate; `args` second, for the providers that handle a
    bare object. A model that fills both is taken at its word on the
    richer of the two.
    """
    parsed: dict = {}
    raw = requested.get("args_json")
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                parsed = loaded
        except (ValueError, TypeError):
            # Not fatal: the tool runs with what is left, and the model
            # sees the result and can correct itself next step. Saying so
            # is what makes that possible.
            log.warning("agent sent unparseable args_json: %.120s", raw)

    direct = requested.get("args")
    if isinstance(direct, dict) and direct:
        # Merged rather than replaced, so a model that splits its
        # arguments across both fields does not silently lose half.
        return {**parsed, **direct} if parsed else direct
    return parsed


def _prompt(task: str, transcript: list[str], index: int, budget: int,
            last_turn: bool, history: list[dict] | None = None) -> str:
    parts = []
    if history:
        block = _history_block(history)
        if block:
            parts.append(
                "EARLIER IN THIS CONVERSATION\n" + block
                + "\n\nThese are for working out what the question REFERS "
                  "to - what \"that\", \"it\" and \"the same period\" "
                  "mean. Do not reuse a figure from them: query for it "
                  "again. The ledger can have changed, and a number carried "
                  "forward is a number nobody checked.")
    parts.append(f"THE QUESTION\n{task}\n")
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
