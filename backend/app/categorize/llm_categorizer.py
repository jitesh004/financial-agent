"""Categorize the tail of transactions that rules could not resolve.

Three things make this cheap and stable rather than slow and flaky:

  1. It works on unique MERCHANTS, not transactions. Six hundred unresolved
     rows are usually forty distinct merchants.
  2. Every answer is written to the merchant cache, so the same merchant is
     never sent to a model twice - month three, this layer barely runs.
  3. Batched, with a strict enum. The model picks from a fixed list and returns
     JSON; anything it invents is discarded rather than trusted.

If the model is unavailable the rows simply stay uncategorized, which the UI
shows as "needs review". That is a better outcome than a confident guess.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ..db.database import Database
from ..db.repository import (forget_merchant, lookup_merchants,
                             save_merchant_categories)
from ..llm.client import LLMClient, get_client
from ..models.schemas import Category, ConfidenceSource, Direction, Transaction
from ..rules import instalments

log = logging.getLogger(__name__)

BATCH_SIZE = 40

SYSTEM = """You categorize bank and credit-card transaction descriptions.

You will be given a numbered list of merchant strings from Indian bank
statements. For each, choose exactly one category from the allowed list.

Rules:
- Return ONLY a JSON array. No prose, no code fences.
- One object per input: {"i": <index>, "category": "<category>", "confidence": <0..1>}
- Use "uncategorized" when the string is genuinely uninformative (a bare
  reference number, an unreadable code). Do NOT guess to fill the list.
- Descriptions are payment-rail strings, so they are abbreviated and noisy.
- "direction" tells you whether money came in or went out; a credit is far more
  likely to be income, a refund, or a reversal.
- The "emi" category means a LENDER collecting a loan instalment, and nothing
  else. Card issuers print the word "EMI" against ordinary purchases purely to
  advertise that the charge could be split into instalments if the cardholder
  asked - nothing was borrowed and the full price was paid, exactly like any
  other purchase. Those markers have already been removed from the strings
  below, so if you still see one treat it as noise and categorise by the
  merchant: a hospital is healthcare, a school is education, an electronics
  shop is shopping.
- Never answer "emi", "loan_interest" or "cc_payment" for a string that only
  names a merchant. Those three describe who is being repaid, and a merchant
  name is not evidence of a debt.
"""


def allowed_categories(db: Database | None = None) -> list[str]:
    """Every category a transaction may be assigned, built-in or user-made.

    `Category` used to be an Enum and is now a plain class with named string
    constants, so iterating it raises rather than yielding members. That went
    unnoticed because the only caller sits inside a broad try/except in the
    pipeline: every attempt to categorise with a model threw here, was
    swallowed as "merchant categorization skipped", and the tail of unknown
    merchants was never resolved by anything. On this ledger that left 476
    rows worth 1,090,451 permanently uncategorized with a working API key
    configured.
    """
    names = list(Category.all_builtins())
    if db is not None:
        try:
            with db.connection() as conn:
                names += [r["name"] for r in
                          conn.execute("SELECT name FROM custom_categories")]
        except Exception:  # pragma: no cover - a missing table must not block
            log.debug("custom categories unavailable", exc_info=True)
    # Deduplicated, order preserved, so the prompt is stable between runs and
    # the model is not offered the same label twice.
    return list(dict.fromkeys(names))


def _response_schema(allowed_names: list[str]) -> dict:
    """The exact shape of an answer, as a constraint rather than a request.

    Wrapped in an object with a single `results` key because that is the only
    way to be handed a list: `response_format` takes a JSON *object* at the
    root, so a schema whose root is an array is not expressible. Asked for a
    bare array through plain JSON mode instead, a model satisfies the
    constraint by returning the first object on its own - one merchant
    answered, thirty-nine dropped, and the batch then discarded here as "got
    dict".

    The category enum is the other half of the point. A bucket outside the
    allowed set was previously caught after the fact and thrown away, which
    silently cost that merchant its answer; inside the schema it is not a
    token the model can emit.
    """
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {"type": "integer"},
                        "category": {"type": "string",
                                     "enum": list(allowed_names)},
                        "confidence": {"type": "number"},
                    },
                    "required": ["i", "category", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }


def _answers_from(reply: object) -> list | None:
    """The list of answers, however this model chose to present it.

    The schema asks for {"results": [...]}, but a provider that ignores
    `response_format` (or an Azure API version too old for it) can still
    return the bare array the prompt asks for, and that answer is perfectly
    usable. Anything else is a failed batch.
    """
    if isinstance(reply, dict):
        reply = reply.get("results")
    return reply if isinstance(reply, list) else None


def _prompt(items: list[tuple[int, str, str]], allowed_names: list[str] | None = None) -> str:
    allowed = ", ".join(allowed_names or Category.all_builtins())
    lines = [f'{i}. [{direction}] "{merchant}"' for i, merchant, direction in items]
    return (
        f"Allowed categories: {allowed}\n\n"
        f"Transactions:\n" + "\n".join(lines) +
        f"\n\nReturn a JSON array of {len(items)} objects."
    )


#: What the model did on the most recent run, for reporting only.
#:
#: "0 from the model" has two very different causes and the counters cannot
#: tell them apart: the call failed (a key, a timeout, an unparseable reply)
#: or the model answered honestly that it did not recognise the merchant.
#: Rows like "AYMENT", "HAS" and two with no description at all are the second
#: case, and telling someone to "check the API log" over a correct answer
#: sends them hunting a bug that is not there.
last_run: dict[str, int] = {"asked": 0, "answered": 0, "declined": 0,
                            "invented": 0, "unevidenced": 0,
                            "failed_batches": 0}


#: Categories that are a claim about a COUNTERPARTY rather than about a
#: purchase, and so cannot be read off a merchant name.
#:
#: "emi" is the one that kept being wrong. A model handed "EMI CLOUDNINE"
#: answers "emi" almost every time - the token is right there, and no amount
#: of prompting reliably beats it - which filed a maternity hospital under
#: debt servicing and, through the merchant cache, kept it there for every
#: future statement. The marker is now stripped before the model ever sees
#: the string (see `_merchant_key`), and this is the backstop for the case
#: where it answers "emi" anyway: an unevidenced answer is refused, the same
#: way an invented category is, and the row stays uncategorized for the user
#: to decide. Refusing is not a loss - a wrong debt figure is worse than a
#: missing one, and the review queue is where an unknown row belongs.
COUNTERPARTY_CATEGORIES = frozenset({
    Category.EMI, Category.LOAN_INTEREST, Category.CC_PAYMENT,
})


def _has_debt_evidence(key: str) -> bool:
    """Whether this merchant string names a lender rather than a shop.

    The looser of the two tests in rules.instalments, deliberately. The
    merchant key is built from the NORMALIZED description, which has already
    had the rail prefix taken off it - "ACH-D- BAJAJ FINANCE LTD" arrives
    here as "D BAJAJ FINANCE LTD" - so insisting on the mandate marker would
    refuse the model's correct answer about a real loan.
    """
    return instalments.names_a_lender(key)


def categorize_with_llm(
    transactions: list[Transaction],
    db: Database | None = None,
    client: LLMClient | None = None,
) -> tuple[int, int]:
    """Resolve uncategorized transactions. Returns (from_cache, from_model)."""
    last_run.update(asked=0, answered=0, declined=0, invented=0,
                    unevidenced=0, failed_batches=0)
    pending = [t for t in transactions if t.category == Category.UNCATEGORIZED]
    if not pending:
        return 0, 0

    # Group by merchant key: one decision serves every transaction that shares it.
    by_key: dict[str, list[Transaction]] = defaultdict(list)
    for txn in pending:
        key = _merchant_key(txn)
        if key:
            by_key[key].append(txn)

    if not by_key:
        return 0, 0

    from_cache = 0
    cached: dict[str, tuple[Category, float, str]] = {}
    if db is not None:
        cached = lookup_merchants(db, list(by_key.keys()))
        # A stored "uncategorized" is the absence of an answer, not one. Left
        # in, it counts as a cache hit that changes nothing and, worse, keeps
        # the merchant out of `unresolved` - so the model is never asked and
        # the run reports "0 categorised" over rows it never looked at.
        cached = {k: v for k, v in cached.items()
                  if v[0] != Category.UNCATEGORIZED}
        # A model answer of "emi" over a merchant name is wrong (see
        # COUNTERPARTY_CATEGORIES) and the cache is permanent, so a run that
        # only stopped making the mistake would go on applying the ones it
        # had already made forever. Forgotten here rather than migrated:
        # this is per-user data behind row-level security, so it heals on
        # the next run each account does instead of needing a backfill
        # somebody has to remember to run. A category the USER set is left
        # exactly alone - they are allowed to call a merchant whatever they
        # need it to be.
        poisoned = [k for k, (category, _, source) in cached.items()
                    if source != "user" and category in COUNTERPARTY_CATEGORIES
                    and not _has_debt_evidence(k)]
        for key in poisoned:
            cached.pop(key, None)
            if db is not None:
                forget_merchant(db, key)
        if poisoned:
            log.info("dropped %d cached debt categor%s with no lender in the "
                     "merchant name", len(poisoned),
                     "y" if len(poisoned) == 1 else "ies")
        for key, (category, confidence, source) in cached.items():
            for txn in by_key[key]:
                txn.category = category
                txn.category_source = (ConfidenceSource.USER if source == "user"
                                       else ConfidenceSource.MERCHANT_CACHE)
                txn.category_confidence = confidence
                from_cache += 1

    unresolved = [k for k in by_key if k not in cached]
    if not unresolved:
        return from_cache, 0

    client = client or get_client()
    if not client.available:
        log.info("no API key: leaving %d merchants uncategorized", len(unresolved))
        return from_cache, 0

    learned: dict[str, tuple[Category, float, str]] = {}
    from_model = 0

    allowed_names = allowed_categories(db)
    allowed_set = {n.lower() for n in allowed_names}

    for start in range(0, len(unresolved), BATCH_SIZE):
        batch = unresolved[start:start + BATCH_SIZE]
        items = [
            (i, key, by_key[key][0].direction.value)
            for i, key in enumerate(batch)
        ]

        try:
            reply = client.complete_json(
                _prompt(items, allowed_names), system=SYSTEM,
                schema=_response_schema(allowed_names))
        except Exception as exc:
            # One failed batch must not lose the batches that succeeded.
            log.warning("LLM categorization batch failed: %s", exc)
            last_run["failed_batches"] += 1
            continue

        answers = _answers_from(reply)
        if answers is None:
            log.warning("expected a JSON array or {\"results\": [...]}, got %s",
                        type(reply).__name__)
            last_run["failed_batches"] += 1
            continue

        last_run["asked"] += len(batch)

        # One answer per index, the first one. A model is asked for exactly
        # one object per input and some return more - Gemma emits a repeated
        # index readily, which is how "ATM WDL SELF 1234" ended up filed
        # under dining: a later duplicate overwrote the answer the model had
        # already given correctly. The count of answered rows exceeding the
        # count asked is the visible symptom.
        answered_indices: set[int] = set()

        for answer in answers:
            if not isinstance(answer, dict):
                continue
            try:
                index = int(answer.get("i", -1))
                category = str(answer.get("category", "")).strip().lower()
            except (ValueError, TypeError):
                continue  # a hallucinated category is discarded, not coerced
            if not 0 <= index < len(batch):
                continue
            if index in answered_indices:
                continue
            if category == Category.UNCATEGORIZED:
                # A refusal is an answer. "RBL*SULOCHANA BH" is a person's
                # name; there is no honest bucket for it.
                last_run["declined"] += 1
                continue
            # A category outside the offered set is a hallucination, and
            # storing it would invent a bucket nothing else knows about.
            # Dropped rather than coerced: leaving the row uncategorized is
            # honest, guessing which real category was meant is not.
            if category not in allowed_set:
                log.debug("discarding invented category %r", category)
                last_run["invented"] += 1
                continue
            key = batch[index]
            # A debt category needs a lender in the string, not a merchant.
            if (category in COUNTERPARTY_CATEGORIES
                    and not _has_debt_evidence(key)):
                log.debug("refusing %r for %r: nothing in it is a lender",
                          category, key)
                last_run["unevidenced"] += 1
                continue

            answered_indices.add(index)
            last_run["answered"] += 1
            confidence = _clamp(answer.get("confidence", 0.6))
            learned[key] = (category, confidence, "llm")
            for txn in by_key[key]:
                txn.category = category
                txn.category_source = ConfidenceSource.LLM
                txn.category_confidence = confidence
                from_model += 1

    if db is not None and learned:
        save_merchant_categories(db, learned)
        log.info("cached %d new merchant categories", len(learned))

    return from_cache, from_model


def _merchant_key(txn: Transaction) -> str:
    """Cache key. Merchant if we have one, else the normalized description.

    The issuer's EMI offer marker comes off first, and that matters twice
    over. It is what the model SEES, so leaving it in was handing the model
    the one token most likely to drag it to the wrong answer - the prompt
    said to ignore it and a small model reliably did not. And it is the CACHE
    KEY, so "EMI CLOUDNINE" and "CLOUDNINE" were two different merchants: the
    same hospital learned twice, once correctly and once as debt, with which
    one applied decided by whether that particular charge happened to carry
    the marker.

    A genuine loan instalment keeps its wording - see rules.instalments.
    """
    key = (txn.merchant or txn.normalized_description or "").strip()
    return instalments.strip_offer_marker(key).upper()[:60]


def _clamp(value, low: float = 0.0, high: float = 1.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return 0.6


def record_user_correction(
    db: Database,
    txn: Transaction,
    category: str,
) -> None:
    """Persist a user's manual recategorization.

    Stored with source='user', which the cache's upsert refuses to overwrite
    with a later model guess. Correcting a merchant once fixes it permanently,
    including for transactions not yet uploaded.
    """
    txn.category = category
    txn.category_source = ConfidenceSource.USER
    txn.category_confidence = 1.0

    key = _merchant_key(txn)
    if not key:
        return
    if category == Category.UNCATEGORIZED:
        # Clearing a category is not a decision about the merchant, so the
        # cache should stop asserting one. This row stays uncategorized; the
        # next run is free to ask about the merchant again.
        forget_merchant(db, key)
        return
    save_merchant_categories(db, {key: (category, 1.0, "user")})
