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
from ..db.repository import lookup_merchants, save_merchant_categories
from ..llm.client import LLMClient, get_client
from ..models.schemas import Category, ConfidenceSource, Direction, Transaction

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
- DO NOT use the "emi" category just because the description contains the word "EMI". 
  Many banks prefix one-time purchases (like a hospital bill or electronics) with "EMI" 
  if the user converted it to an installment plan. Categorize these based on what was purchased (e.g. healthcare, shopping).
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


def _prompt(items: list[tuple[int, str, str]], allowed_names: list[str] | None = None) -> str:
    allowed = ", ".join(allowed_names or Category.all_builtins())
    lines = [f'{i}. [{direction}] "{merchant}"' for i, merchant, direction in items]
    return (
        f"Allowed categories: {allowed}\n\n"
        f"Transactions:\n" + "\n".join(lines) +
        f"\n\nReturn a JSON array of {len(items)} objects."
    )


def categorize_with_llm(
    transactions: list[Transaction],
    db: Database | None = None,
    client: LLMClient | None = None,
) -> tuple[int, int]:
    """Resolve uncategorized transactions. Returns (from_cache, from_model)."""
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
            answers = client.complete_json(
                _prompt(items, allowed_names), system=SYSTEM)
        except Exception as exc:
            # One failed batch must not lose the batches that succeeded.
            log.warning("LLM categorization batch failed: %s", exc)
            continue

        if not isinstance(answers, list):
            log.warning("expected a JSON array, got %s", type(answers).__name__)
            continue

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
            if category == Category.UNCATEGORIZED:
                continue
            # A category outside the offered set is a hallucination, and
            # storing it would invent a bucket nothing else knows about.
            # Dropped rather than coerced: leaving the row uncategorized is
            # honest, guessing which real category was meant is not.
            if category not in allowed_set:
                log.debug("discarding invented category %r", category)
                continue

            confidence = _clamp(answer.get("confidence", 0.6))
            key = batch[index]
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
    """Cache key. Merchant if we have one, else the normalized description."""
    key = (txn.merchant or txn.normalized_description or "").strip().upper()
    return key[:60]


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
    if key:
        save_merchant_categories(db, {key: (category, 1.0, "user")})
