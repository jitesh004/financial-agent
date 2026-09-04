"""Check whether the configured model can actually categorise, and say what to fix.

Run:  .venv/Scripts/python backend/tools/check_llm_setup.py
      python backend/tools/check_llm_setup.py --tier strong
      python backend/tools/check_llm_setup.py "SWIGGY BANGALORE" "UPI/9876543210"

This exercises the real path - the real system prompt from the categoriser,
the real prompt builder, the real provider, your real key - and stops short
only of the database. So a pass here means the model call itself works, and
any remaining "0 categorised" is about the ledger rather than the provider.

It needs no PostgreSQL. `allowed_categories(None)` falls back to the built-in
list, which is what a fresh workspace offers anyway.

Every failure mode prints a specific instruction rather than a stack trace,
because from inside the app they all look identical: rows stay uncategorized
and the log says "0 from the model". The ones actually seen in this project:

  - no key, or a provider name nothing implements (LLM_PROVIDER=gemini after
    the move to OpenRouter), which degrades silently by design
  - 429, because free OpenRouter models allow 20 requests a minute and 50 a
    day until the account has bought credit
  - a model that ignores `response_format` and prefaces the array with a
    sentence, which parses as nothing
  - a reasoning model that spends the whole token budget thinking and
    returns an empty `content`
  - a category the model invented, which the real run discards rather than
    trusting - counted here so you can see it happening
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.categorize.llm_categorizer import (  # noqa: E402
    BATCH_SIZE, SYSTEM, _prompt, allowed_categories)
from app.config import config  # noqa: E402
from app.llm.client import LLMUnavailable, get_client, redact  # noqa: E402

#: Deliberately awkward. Real statement strings are abbreviated payment-rail
#: text, and two of these have no honest answer at all - a model that labels
#: every one of them confidently is a worse sign than one that declines.
SAMPLE = [
    ("SWIGGY BANGALORE", "debit"),
    ("UPI/PAYTM/9876543210/Payment", "debit"),
    ("ACH-D- HDFC LTD HOME LOAN EMI", "debit"),
    ("IRCTC WEB", "debit"),
    ("SALARY CREDIT SEP", "credit"),
    ("BESCOM ELECTRICITY BILL", "debit"),
    ("AMAZON PAY INDIA", "debit"),
    ("NEFT RETURN CHARGES", "debit"),
    ("APOLLO PHARMACY EMI 3/6", "debit"),
    ("AYMENT", "debit"),
    ("REF 0098127364", "credit"),
]


def _mask(secret: str | None) -> str:
    if not secret:
        return "(not set)"
    return f"{secret[:6]}…{secret[-4:]} ({len(secret)} chars)" if len(
        secret) > 12 else "(set, short)"


def _report_config() -> bool:
    """Print what is configured. False if there is no point calling anything."""
    print("Configuration")
    print(f"  LLM_PROVIDER              {config.LLM_PROVIDER or '(not set)'}")

    if config.LLM_PROVIDER == "openrouter":
        print(f"  OPENROUTER_API_KEY        {_mask(config.OPENROUTER_API_KEY)}")
        print(f"  OPENROUTER_BASE_URL       {config.OPENROUTER_BASE_URL}")
        print(f"  OPENROUTER_MODEL_FAST     {config.OPENROUTER_MODEL_FAST}")
        print(f"  OPENROUTER_MODEL_STRONG   {config.OPENROUTER_MODEL_STRONG}")
        print("  OPENROUTER_REASONING_EFFORT "
              f"{config.OPENROUTER_REASONING_EFFORT or '(none - no reasoning field sent)'}")
        print(f"  OPENROUTER_JSON_MODE      {config.OPENROUTER_JSON_MODE}")
        if not config.OPENROUTER_API_KEY:
            print("\nFAIL  No OPENROUTER_API_KEY.")
            print("      Get one at https://openrouter.ai/keys and put it in .env")
            print("      beside this repository, then run this again.")
            return False
        if "openrouter.ai" not in config.OPENROUTER_BASE_URL and (
                config.OPENROUTER_REASONING_EFFORT):
            print("\nWARN  OPENROUTER_BASE_URL is not OpenRouter, but a reasoning")
            print("      effort is set. `reasoning: {effort}` is how OpenRouter")
            print("      spells it; other endpoints - Gemini's included - read")
            print("      `reasoning_effort` and will ignore it. Set")
            print("      OPENROUTER_REASONING_EFFORT=none in .env.")
    elif config.LLM_PROVIDER == "azure":
        print(f"  AZURE_OPENAI_ENDPOINT     {config.AZURE_OPENAI_ENDPOINT or '(not set)'}")
        print(f"  AZURE_OPENAI_API_KEY      {_mask(config.AZURE_OPENAI_API_KEY)}")
        print(f"  deployment (fast)         {config.AZURE_OPENAI_DEPLOYMENT_FAST}")
        print(f"  deployment (strong)       {config.AZURE_OPENAI_DEPLOYMENT_STRONG}")
        if not (config.AZURE_OPENAI_ENDPOINT and config.AZURE_OPENAI_API_KEY):
            print("\nFAIL  Azure needs both AZURE_OPENAI_ENDPOINT and "
                  "AZURE_OPENAI_API_KEY.")
            return False
    elif not config.LLM_PROVIDER:
        print("\nFAIL  LLM_PROVIDER is not set, so no model will be called.")
        print("      That is a supported configuration - rules still")
        print("      categorise and the narrative falls back to the computed")
        print("      figures - but nothing here can be tested. Set")
        print("      LLM_PROVIDER=openrouter in .env to use a model.")
        return False
    else:
        print(f"\nFAIL  LLM_PROVIDER={config.LLM_PROVIDER!r} is not a provider this")
        print("      app implements. Known: openrouter, azure. This degrades to")
        print("      no model at all, which is why rows stay uncategorized with")
        print("      no error anywhere.")
        if config.LLM_PROVIDER == "gemini":
            print("\n      Gemini moved to its OpenAI-compatible endpoint. In .env:")
            print("        LLM_PROVIDER=openrouter")
            print("        OPENROUTER_BASE_URL=https://generativelanguage"
                  ".googleapis.com/v1beta/openai")
            print("        OPENROUTER_API_KEY=<your Gemini key>")
            print("        OPENROUTER_MODEL_FAST=gemini-2.5-flash")
            print("        OPENROUTER_MODEL_STRONG=gemini-2.5-pro")
            print("        OPENROUTER_REASONING_EFFORT=none")
        return False
    return True


def _explain(exc: Exception) -> None:
    """Turn the exception into the thing to go and change."""
    text = str(exc)
    status = getattr(getattr(exc, "response", None), "status_code", None)

    print(f"\nFAIL  {type(exc).__name__}: {text[:400]}")

    if status == 401 or "No auth credentials" in text or "invalid" in text.lower():
        print("\n      The key was rejected. Check OPENROUTER_API_KEY in .env is")
        print("      the whole key, unquoted, with no trailing spaces.")
    elif status == 402 or "credit" in text.lower():
        print("\n      Out of credit. Free models need none, so this usually means")
        print("      OPENROUTER_MODEL_* names a paid model - check the :free")
        print("      suffix at https://openrouter.ai/models?max_price=0")
    elif status == 429:
        print("\n      Rate limited. Free models allow 20 requests a minute, and")
        print("      50 a day until the account has bought $10 of credit (then")
        print("      1000). Wait a minute and retry; if it is the daily cap,")
        print("      that resets at 00:00 UTC.")
    elif status == 404:
        print("\n      No such model. Copy the id exactly, including the `:free`")
        print("      suffix, from https://openrouter.ai/models?max_price=0")
    elif status == 400 and "response_format" in text:
        print("\n      This model rejects response_format. Set")
        print("      OPENROUTER_JSON_MODE=false in .env, or pick a model that")
        print("      supports it - the free ones that do include")
        print("      z-ai/glm-5.2:free and google/gemma-4-26b-a4b-it:free.")
    elif "did not return parseable JSON" in text:
        print("\n      The model answered, but not with JSON. If")
        print("      OPENROUTER_JSON_MODE is false, turn it on. If it is already")
        print("      on, this model ignores it - pick one that does not.")
    elif isinstance(exc, LLMUnavailable):
        print("\n      No provider is available. See the configuration above.")
    else:
        print("\n      Not a failure this script recognises. The full error is")
        print("      above; run with --debug for the request log.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("merchants", nargs="*",
                        help="merchant strings to categorise (default: a "
                             "built-in sample of awkward real ones)")
    parser.add_argument("--tier", choices=("fast", "strong"), default="fast",
                        help="fast is the categoriser's model, strong is the "
                             "narrative's (default: fast)")
    parser.add_argument("--debug", action="store_true",
                        help="log the HTTP request and the raw reply")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s")

    print(f"Reading .env from {ROOT / '.env'}"
          f"{'' if (ROOT / '.env').exists() else '  (MISSING)'}\n")

    if not _report_config():
        return 1

    items_src = ([(m, "debit") for m in args.merchants] if args.merchants
                 else SAMPLE)
    items = [(i, merchant, direction)
             for i, (merchant, direction) in enumerate(items_src)]
    allowed = allowed_categories(None)
    allowed_set = {a.lower() for a in allowed}

    print(f"\nAsking the {args.tier} model about {len(items)} merchants "
          f"(the real run sends up to {BATCH_SIZE} per request)")

    prompt = _prompt(items, allowed)
    # The same last-gate redaction the real call applies, shown so you can see
    # what actually leaves the machine.
    if args.debug:
        print("\n--- prompt as sent (redacted) ---")
        print(redact(prompt))
        print("--- end ---\n")

    client = get_client(args.tier)
    if not client.available:
        print("\nFAIL  The provider reports itself unavailable. See above.")
        return 1

    started = time.monotonic()
    try:
        answers = client.complete_json(prompt, system=SYSTEM, model=args.tier)
    except Exception as exc:  # noqa: BLE001 - every failure is explained
        _explain(exc)
        return 1
    elapsed = time.monotonic() - started

    print(f"\nPASS  the model replied in {elapsed:.1f}s\n")

    if not isinstance(answers, list):
        print(f"FAIL  expected a JSON array, got {type(answers).__name__}:")
        print(f"      {json.dumps(answers)[:400]}")
        print("\n      The real run discards this batch and logs a warning, so")
        print("      the rows would stay uncategorized.")
        return 1

    # The real validation loop, so what you see here is what the run accepts.
    answered = declined = invented = 0
    print(f"  {'merchant':34} {'direction':9} {'category':18} conf")
    print(f"  {'-' * 34} {'-' * 9} {'-' * 18} ----")
    by_index = {}
    for answer in answers:
        if isinstance(answer, dict):
            try:
                by_index[int(answer.get("i", -1))] = answer
            except (TypeError, ValueError):
                continue

    for i, merchant, direction in items:
        answer = by_index.get(i)
        if answer is None:
            note, conf = "(no answer)", ""
        else:
            category = str(answer.get("category", "")).strip().lower()
            conf = f"{answer.get('confidence', '')}"
            if category == "uncategorized":
                note, declined = "uncategorized ↩", declined + 1
            elif category not in allowed_set:
                note, invented = f"{category} ✗INVENTED", invented + 1
            else:
                note, answered = category, answered + 1
        print(f"  {merchant[:34]:34} {direction:9} {note:18} {conf}")

    print(f"\n  answered {answered}   declined {declined}   "
          f"invented (discarded) {invented}   missing "
          f"{len(items) - len(by_index)}")

    if invented:
        print("\n  Invented categories are dropped, not coerced - leaving the row")
        print("  uncategorized is honest, guessing which real bucket was meant")
        print("  is not. A few is normal; most of them means this model is a bad")
        print("  fit for the fast tier.")
    if declined == len(items):
        print("\n  WARN  it declined every single one. Either the merchant strings")
        print("        really are uninformative, or the model is not following")
        print("        the prompt - try --tier strong to compare.")

    print("\nThe model call works. Anything still uncategorized in the app is")
    print("about the ledger or the merchant cache, not the provider.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
