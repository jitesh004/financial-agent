"""What the model cost, and the window that says over what period.

The window is tested first and hardest because its failure mode is the
quiet one: a boundary in the wrong format matches no rows and returns an
empty list rather than an error, so the page looks like "nothing has run"
instead of "this query is broken".
"""

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.llm_routes import _stamp, _window                  # noqa: E402
from app.db import repository as repo                           # noqa: E402
from app.llm import telemetry                                   # noqa: E402


def _row(**kw):
    base = {
        "purpose": "agent", "subject": "debt-strategist · step 1",
        "job_id": "", "provider": "gemini", "model": "gemini-3.5-flash-lite",
        "tier": "fast", "reasoning": "model default",
        "key_label": "Default key", "key_hint": "AQ.A...Rb9w",
        "group_id": "g1", "attempt": 1, "status": "ok", "http_status": 200,
        "error": "", "input_tokens": 100, "output_tokens": 20,
        "total_tokens": 120, "prompt_chars": 400, "response_chars": 80,
        "latency_ms": 1200, "cost_micros": 0, "cost_known": 1,
        "request_preview": "prompt", "response_preview": "reply",
    }
    base.update(kw)
    return base


# ------------------------------------------------------------- the window

def test_a_boundary_matches_the_format_the_column_stores(tmp_db):
    """`created_at` is TEXT from `fa_now()`: "2026-09-11 06:08:09".

    Compared against an ISO string this is a TEXT comparison where the
    separator decides it - " " sorts below "T", so
    `'2026-09-11 06:08:09' >= '2026-09-11T06:08:38+00:00'` is false for
    every row ever written. Every windowed query came back empty, and
    nothing raised.
    """
    repo.save_llm_calls(tmp_db, [_row()])

    since, _, _ = _window("24h")
    assert "T" not in since, since
    assert "+" not in since, since

    found = repo.llm_usage_summary(tmp_db, since=since)
    assert found["overall"]["attempts"] == 1, (
        "a row written moments ago must fall inside a 24-hour window")


def test_every_window_a_person_might_ask_for_parses():
    """Minutes and hours are here because a rate limit is a per-minute
    window: "have I just burned my quota" is not a question about days."""
    for period in ("15m", "30min", "1h", "6hrs", "7d", "2w", "3mo", "1y"):
        since, until, label = _window(period)
        assert since and not until, period
        assert label

    assert _window("all") == ("", "", "all time")
    assert _window("") == ("", "", "all time")

    with pytest.raises(Exception):
        _window("last tuesday")


def test_the_window_actually_excludes_what_is_outside_it(tmp_db):
    """A filter that never excludes anything is not a filter."""
    old = _stamp(datetime.now(timezone.utc) - timedelta(days=40))
    repo.save_llm_calls(tmp_db, [_row(group_id="recent")])
    with tmp_db.connection() as conn:
        conn.execute("UPDATE llm_calls SET created_at = ? WHERE group_id = ?",
                     (old, "recent"))

    since, _, _ = _window("24h")
    assert repo.llm_usage_summary(tmp_db, since=since)["overall"]["attempts"] == 0
    assert repo.llm_usage_summary(tmp_db)["overall"]["attempts"] == 1


# -------------------------------------------------------------- the totals

def test_tokens_belong_to_the_attempt_that_answered(tmp_db):
    """A rate-limited attempt returned no content and consumed no output
    budget. Charging it the same tokens doubles every retried call."""
    repo.save_llm_calls(tmp_db, [
        _row(group_id="g", attempt=1, status="rate_limited", http_status=429,
             input_tokens=0, output_tokens=0, total_tokens=0),
        _row(group_id="g", attempt=2, status="ok"),
    ])
    overall = repo.llm_usage_summary(tmp_db)["overall"]

    # Two attempts, one logical request, one set of tokens.
    assert overall["attempts"] == 2
    assert overall["requests"] == 1
    assert overall["total_tokens"] == 120
    assert overall["failed"] == 1


def test_a_retry_is_shown_under_the_request_it_retried(tmp_db):
    """A failed attempt on its own in a flat list reads as a separate lost
    request, when the next row is the same question on another key."""
    repo.save_llm_calls(tmp_db, [
        _row(group_id="g", attempt=1, status="rejected", http_status=400,
             key_label="broken", error="API key not valid"),
        _row(group_id="g", attempt=2, status="ok", key_label="Default key"),
    ])
    found = repo.llm_calls(tmp_db)

    assert found["total"] == 1
    call = found["calls"][0]
    assert call["outcome"] == "ok"
    assert call["retries"] == 1
    assert call["key_label"] == "Default key", (
        "the listed row should be the attempt that answered")
    assert [a["status"] for a in call["attempts"]] == ["rejected", "ok"]
    assert "API key not valid" in call["attempts"][0]["error"]


def test_purposes_are_counted_apart(tmp_db):
    repo.save_llm_calls(tmp_db, [
        _row(group_id="a", purpose="agent"),
        _row(group_id="b", purpose="categorization", total_tokens=900),
        _row(group_id="c", purpose="categorization", total_tokens=900),
    ])
    by = {r["purpose"]: r for r in repo.llm_usage_summary(tmp_db)["by_purpose"]}
    assert by["agent"]["requests"] == 1
    assert by["categorization"]["requests"] == 2
    assert by["categorization"]["total_tokens"] == 1800


def test_filtering_by_key_narrows_both_the_totals_and_the_list(tmp_db):
    """The figure and the list you get by clicking it must describe the
    same set, or the page argues with itself."""
    repo.save_llm_calls(tmp_db, [
        _row(group_id="a", key_label="one"),
        _row(group_id="b", key_label="two"),
    ])
    assert repo.llm_usage_summary(tmp_db, key_label="one")["overall"]["requests"] == 1
    assert repo.llm_calls(tmp_db, key_label="one")["total"] == 1


# --------------------------------------------------------------- the cost

def test_an_unpriced_model_is_not_reported_as_free(tmp_db):
    """"Not priced" and "free" are different facts, and this is the one
    page where that difference matters. A confident zero would be the
    single most misleading thing it could say."""
    assert telemetry.price_for("gemini-3.5-flash-lite") is not None
    assert telemetry.price_for("some-model-nobody-priced") is None
    # An OpenRouter ":free" variant states its price in its name.
    assert telemetry.price_for("meta-llama/llama-3.3-70b-instruct:free") is not None


def test_thinking_tokens_are_counted_as_output():
    """A reasoning model bills its thinking and does not put it in
    `candidatesTokenCount`. Left out, its output looks a tenth of its real
    size and "why did this cost so much" has no answer on the page."""
    call = telemetry.Call(group_id="g")
    token = telemetry.CURRENT.set(call)
    try:
        telemetry.note_usage({"usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 50,
            "thoughtsTokenCount": 700,
            "totalTokenCount": 1750,
        }}, "reply")
    finally:
        telemetry.CURRENT.reset(token)

    assert call.input_tokens == 1000
    assert call.output_tokens == 750, "thinking belongs in output"


# --------------------------------------------------------------- pricing

def test_the_same_model_has_two_prices(monkeypatch):
    """Free or paid is a fact about the ACCOUNT, not the model.

    Nothing can infer it, and guessing wrong makes every figure on the
    page wrong in the same direction.
    """
    free = telemetry.price_for("gemini-3.5-flash-lite", "free")
    paid = telemetry.price_for("gemini-3.5-flash-lite", "paid")

    assert free.input_usd == 0 and free.output_usd == 0
    assert paid.input_usd == Decimal("0.30")
    assert paid.output_usd == Decimal("2.50")


def test_a_promotional_rate_reverts_on_its_end_date():
    """The 3.x Flash family is half price through 31 December 2026.

    Hard-coding the promotional figure would under-report every call from
    January onwards, silently - the worst shape of error for a page whose
    job is reporting cost.
    """
    rate = telemetry.price_for("gemini-3.6-flash", "paid")

    during = rate.on(date(2026, 12, 31))
    assert during.input_usd == Decimal("0.75")
    assert during.output_usd == Decimal("3.75")

    after = rate.on(date(2027, 1, 1))
    assert after.input_usd == Decimal("1.50")
    assert after.output_usd == Decimal("7.50")


def test_a_long_prompt_is_charged_at_the_long_context_rate():
    """2.5 Pro is $1.25/M input up to 200k and $2.50/M beyond it, and an
    agent prompt carrying a whole ledger crosses that line."""
    rate = telemetry.price_for("gemini-2.5-pro", "paid")

    short_in, short_out = rate.per_million(100_000)
    assert (short_in, short_out) == (Decimal("1.25"), Decimal("10.00"))

    long_in, long_out = rate.per_million(250_000)
    assert (long_in, long_out) == (Decimal("2.50"), Decimal("15.00"))


def test_cost_is_computed_in_dollars_not_rupees(monkeypatch):
    """Google prices in USD. `pipeline.enrich` refuses to invent an
    exchange rate anywhere else in this app and this is not the place to
    start - the unit is stated rather than translated."""
    monkeypatch.setattr(telemetry, "pricing_tier", lambda: "paid")

    # 1M input and 1M output of flash-lite: $0.30 + $2.50.
    micros, known = telemetry._cost_micros("gemini-3.5-flash-lite",
                                           1_000_000, 1_000_000)
    assert known
    assert micros == 2_800_000, micros          # $2.80, in millionths


def test_an_unknown_model_stays_unpriced_on_the_paid_tier(monkeypatch):
    monkeypatch.setattr(telemetry, "pricing_tier", lambda: "paid")
    assert telemetry.price_for("some-model-nobody-published", "paid") is None
    assert telemetry._cost_micros("some-model-nobody-published", 100, 100) \
        == (0, False)


def test_gemma_is_free_rather_than_unpriced():
    """Published with a free tier and no paid rate at all, which is not
    the same as "we could not find one"."""
    assert telemetry.price_for("gemma-4-26b-a4b-it", "paid") is not None
    assert telemetry.price_for("gemma-4-26b-a4b-it", "paid").input_usd == 0


def test_a_vendor_prefixed_name_resolves(monkeypatch):
    """OpenRouter spells the same model "google/gemini-3.5-flash-lite"."""
    monkeypatch.setattr(telemetry, "pricing_tier", lambda: "paid")
    assert telemetry.price_for("google/gemini-3.5-flash-lite").input_usd \
        == Decimal("0.30")
    # And ":free" states its price in its own name, whatever the tier.
    assert telemetry.price_for("meta-llama/llama-3.3-70b:free").input_usd == 0
