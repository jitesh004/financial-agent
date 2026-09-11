"""What every model call cost, and what became of it.

One row per ATTEMPT, written from `llm.providers` - the single point all
three providers' traffic passes through, so a new call site cannot bypass
it by forgetting to instrument itself.

The attempt is the unit on purpose. A request that was rate limited on one
key and succeeded on the next is two facts, and rolling them into one is
exactly how a quota problem stays invisible: the summary says "1 request,
ok" and the reason the import took four minutes is nowhere.

WHY a call was made travels in a context variable rather than through every
signature. The alternative is threading a purpose argument through
`categorize -> client.complete_json -> provider.complete`, and through the
agent runner, and through four normalisers - for a field none of them care
about. `db.engine.TENANT` and `JOB` are bound the same way for the same
reason.

Nothing here may raise. A failure to record must never fail the call it was
describing, so every public function swallows.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)

#: (purpose, subject) for the work currently in flight on this task.
#: Empty means a call nothing labelled, which is worth seeing as such
#: rather than silently filed under something plausible.
PURPOSE: ContextVar[tuple[str, str]] = ContextVar(
    "fa_llm_purpose", default=("unknown", ""))

#: The purposes this app actually has. Not a constraint - an unknown
#: purpose is recorded as given - but the list the statistics screen builds
#: its tabs from, so a new one appears there without a frontend change.
PURPOSES: tuple[tuple[str, str], ...] = (
    ("categorization", "Merchant categorisation"),
    ("letterhead", "Statement identity"),
    ("column_map", "Column mapping"),
    ("card_summary", "Card summary"),
    ("agent", "Copilot agents"),
    ("narrative", "Written narrative"),
    ("probe", "Connection test"),
)

PURPOSE_LABEL = dict(PURPOSES)


@contextmanager
def purpose(kind: str, subject: str = ""):
    """Label every model call made inside this block."""
    token = PURPOSE.set((kind or "unknown", str(subject or "")[:200]))
    try:
        yield
    finally:
        PURPOSE.reset(token)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
#
# Published rates, in US DOLLARS per million tokens, as Google lists them
# on ai.google.dev/gemini-api/docs/pricing.
#
# DOLLARS, and the page says so. Google prices in USD, this app totals a
# person's money in rupees, and `pipeline.enrich` states the rule the rest
# of the codebase follows: "inventing an exchange rate would be a worse
# answer than saying the figure needs a human." A model cost converted at a
# rate nobody chose would be exactly that. The screen reports $ and offers
# an optional rupee line only when the holder supplies the rate themselves.
#
# Three things the published table forces into the model, none of them
# optional if the number is to be right:
#
#   1. The same model has two prices. Free tier is metered in requests per
#      day and costs nothing; paid tier costs the rates below. Which one
#      applies is a fact about the account, not the model, so it is a
#      setting - see `pricing_tier`.
#   2. Some models charge more above a context threshold. 2.5 Pro is
#      $1.25/M input up to 200k and $2.50/M beyond it, and a long agent
#      prompt crosses that line.
#   3. Some prices are promotional and revert on a date. The 3.x Flash
#      family is half price through 31 December 2026. Hard-coding the
#      promotional figure would under-report every call from January
#      onwards, silently.


@dataclass(frozen=True)
class Rate:
    """What one model charges, per million tokens, in USD."""

    input_usd: Decimal
    output_usd: Decimal
    #: Above this many INPUT tokens the long-context rates apply instead.
    long_from: int | None = None
    long_input_usd: Decimal | None = None
    long_output_usd: Decimal | None = None
    #: A promotional rate stops on this date and `then` takes over.
    until: date | None = None
    then: "Rate | None" = None

    def on(self, when: date) -> "Rate":
        """This rate as it stands on a given day."""
        if self.until and self.then and when > self.until:
            return self.then.on(when)
        return self

    def per_million(self, input_tokens: int) -> tuple[Decimal, Decimal]:
        """Input and output rates for a call of this size."""
        if (self.long_from is not None and input_tokens > self.long_from
                and self.long_input_usd is not None):
            return (self.long_input_usd,
                    self.long_output_usd or self.output_usd)
        return self.input_usd, self.output_usd


def _d(value: str) -> Decimal:
    return Decimal(value)


#: The 3.x Flash promotional price runs out at the end of 2026.
_PROMO_ENDS = date(2026, 12, 31)

#: Half price until then, full price after - one entry, not two, so the
#: page cannot go on quoting the promotion once it has lapsed.
_FLASH_3X = Rate(_d("0.75"), _d("3.75"), until=_PROMO_ENDS,
                 then=Rate(_d("1.50"), _d("7.50")))

#: USD per million tokens, paid tier. Free tier is handled separately and
#: is genuinely zero, which is a different statement from "unpriced".
PRICES: dict[str, Rate] = {
    # ---- Gemini 3.x Flash: promotional through 2026-12-31 ---------------
    "gemini-3.8-flash": _FLASH_3X,
    "gemini-3.7-flash": _FLASH_3X,
    "gemini-3.6-flash": _FLASH_3X,

    # ---- Gemini 3.x, flat ------------------------------------------------
    "gemini-3.5-flash": Rate(_d("1.50"), _d("9.00")),
    "gemini-3.5-flash-lite": Rate(_d("0.30"), _d("2.50")),
    "gemini-3.1-flash-lite": Rate(_d("0.25"), _d("1.50")),

    # ---- Pro: dearer past 200k of context --------------------------------
    "gemini-3.1-pro-preview": Rate(
        _d("2.00"), _d("12.00"),
        long_from=200_000, long_input_usd=_d("4.00"),
        long_output_usd=_d("18.00")),
    "gemini-2.5-pro": Rate(
        _d("1.25"), _d("10.00"),
        long_from=200_000, long_input_usd=_d("2.50"),
        long_output_usd=_d("15.00")),

    # ---- Gemini 2.5 ------------------------------------------------------
    "gemini-2.5-flash": Rate(_d("0.30"), _d("2.50")),
    "gemini-2.5-flash-lite": Rate(_d("0.10"), _d("0.40")),

    # ---- Embeddings, for completeness. Nothing here calls them yet. -----
    "gemini-embedding": Rate(_d("0.15"), _d("0")),
    "gemini-embedding-2": Rate(_d("0.20"), _d("0")),
}

#: Models that cost nothing on either tier.
#:
#: Gemma is published with a free tier and no paid rate at all, which is
#: not the same as "we could not find one" - so it is recorded as free
#: rather than left to read as unpriced.
FREE_MODELS: frozenset[str] = frozenset({
    "gemma-4-26b-a4b-it", "gemma-4", "gemma-3-27b-it",
})

_ZERO = Rate(Decimal("0"), Decimal("0"))


def pricing_tier() -> str:
    """'free' or 'paid' - a fact about the account, not the model.

    Defaults to free, because that is where a workspace starts and because
    over-reporting a bill is the less useful error: a zero that should be
    a number prompts the question, a number that should be zero is simply
    believed.
    """
    try:
        from .settings import effective
        return (effective().get("llm_pricing_tier") or "free").lower()
    except Exception:                   # pragma: no cover - defensive
        return "free"


def price_for(model: str, tier: str = "") -> Rate | None:
    """The rate for this model, or None when nothing is published."""
    if not model:
        return None
    name = model.strip().lower()
    tier = (tier or pricing_tier()).lower()

    # An OpenRouter ":free" variant states its price in its own name, and
    # that is true whatever tier the account is on.
    if name.endswith(":free"):
        return _ZERO

    base = name.split("/")[-1]
    if base in FREE_MODELS or name in FREE_MODELS:
        return _ZERO
    if tier != "paid":
        # Free tier: metered in requests per day, not money. Zero is the
        # real price, and saying so is not the same as not knowing.
        return _ZERO if (name in PRICES or base in PRICES) else None

    return PRICES.get(name) or PRICES.get(base)


def _cost_micros(model: str, input_tokens: int, output_tokens: int
                 ) -> tuple[int, bool]:
    """Estimated cost in MILLIONTHS OF A US DOLLAR, and whether it is known.

    Millionths because a single categorisation batch costs a fraction of a
    cent, and rounding that to the nearest cent would report every call in
    a day's work as zero.
    """
    rate = price_for(model)
    if rate is None:
        return 0, False
    today = date.today()
    per_in, per_out = rate.on(today).per_million(input_tokens)
    usd = (Decimal(input_tokens) * per_in
           + Decimal(output_tokens) * per_out) / Decimal(1_000_000)
    return int(usd * 1_000_000), True


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

@dataclass
class Attempt:
    """One HTTP request to a provider, however it ended."""

    attempt: int
    key_label: str = ""
    key_hint: str = ""
    status: str = "ok"
    http_status: int = 0
    error: str = ""
    latency_ms: int = 0


@dataclass
class Call:
    """One logical model call: the attempts it took, and what came back."""

    group_id: str
    provider: str = ""
    model: str = ""
    tier: str = ""
    reasoning: str = ""
    prompt_chars: int = 0
    request_preview: str = ""
    response_preview: str = ""
    response_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    attempts: list[Attempt] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def attempt(self, **kw: Any) -> Attempt:
        made = Attempt(attempt=len(self.attempts) + 1, **kw)
        self.attempts.append(made)
        return made


#: The call being made on this task, if any. Set by `provider.complete`
#: and read by `_post_with_retries`, which is a level below it and has no
#: other way to reach it.
CURRENT: ContextVar[Call | None] = ContextVar("fa_llm_call", default=None)


@contextmanager
def record(provider: str, model: str, tier: str = "", reasoning: str = "",
           prompt: str = ""):
    """Record one model call and everything it took to make it."""
    call = Call(group_id=str(uuid.uuid4()), provider=provider, model=model,
                tier=tier, reasoning=reasoning,
                prompt_chars=len(prompt or ""),
                request_preview=(prompt or "")[:4000])
    token = CURRENT.set(call)
    try:
        yield call
    except Exception as exc:
        # The provider raised after its HTTP attempts - a bad payload, an
        # unparseable reply. Worth a row: it cost tokens and produced
        # nothing, which is invisible if only HTTP failures are recorded.
        if not any(a.status != "ok" for a in call.attempts):
            call.attempt(status="failed",
                         error=f"{type(exc).__name__}: {exc}"[:500])
        raise
    finally:
        CURRENT.reset(token)
        _flush(call)


def note_usage(data: Any, text: str = "") -> None:
    """Take the token counts off a provider's reply, in either dialect."""
    call = CURRENT.get()
    if call is None or not isinstance(data, dict):
        return
    try:
        # Google
        usage = data.get("usageMetadata")
        if isinstance(usage, dict):
            call.input_tokens = int(usage.get("promptTokenCount") or 0)
            call.output_tokens = int(usage.get("candidatesTokenCount") or 0)
            # Thinking is billed and is NOT in candidatesTokenCount. Left
            # out, a reasoning model's output looks a tenth of its real
            # size and the "why did this cost so much" question has no
            # answer on the page.
            call.output_tokens += int(usage.get("thoughtsTokenCount") or 0)
            call.total_tokens = int(usage.get("totalTokenCount") or 0)
        # OpenAI-shaped
        usage = data.get("usage")
        if isinstance(usage, dict):
            call.input_tokens = int(usage.get("prompt_tokens") or 0)
            call.output_tokens = int(usage.get("completion_tokens") or 0)
            call.total_tokens = int(usage.get("total_tokens") or 0)
        if not call.total_tokens:
            call.total_tokens = call.input_tokens + call.output_tokens
        if text:
            call.response_chars = len(text)
            call.response_preview = text[:4000]
    except Exception:                   # pragma: no cover - recording only
        pass


def _flush(call: Call) -> None:
    """Write the attempts. Never raises."""
    if not call.attempts:
        # No HTTP request was made at all - the provider was unavailable,
        # or a cache answered. Nothing was spent, so there is nothing to
        # record.
        return
    try:
        from ..db import repository as repo
        from ..db.database import get_db
        from ..db.engine import current_job

        kind, subject = PURPOSE.get()
        cost, known = _cost_micros(call.model, call.input_tokens,
                                   call.output_tokens)
        rows = []
        for made in call.attempts:
            ok = made.status == "ok"
            rows.append({
                "purpose": kind, "subject": subject,
                "job_id": current_job(),
                "provider": call.provider, "model": call.model,
                "tier": call.tier, "reasoning": call.reasoning,
                "key_label": made.key_label, "key_hint": made.key_hint,
                "group_id": call.group_id, "attempt": made.attempt,
                "status": made.status, "http_status": made.http_status,
                "error": made.error,
                # Tokens and cost belong to the attempt that SUCCEEDED. A
                # rate-limited attempt returned no content and consumed no
                # output budget; charging it the same tokens would double
                # every retried call on the statistics page.
                "input_tokens": call.input_tokens if ok else 0,
                "output_tokens": call.output_tokens if ok else 0,
                "total_tokens": call.total_tokens if ok else 0,
                "prompt_chars": call.prompt_chars,
                "response_chars": call.response_chars if ok else 0,
                "latency_ms": made.latency_ms,
                "cost_micros": cost if ok else 0,
                "cost_known": int(known),
                "request_preview": call.request_preview,
                "response_preview": call.response_preview if ok else "",
            })
        repo.save_llm_calls(get_db(), rows)
    except Exception as exc:            # pragma: no cover - recording only
        log.debug("could not record model call telemetry: %s", exc)
