"""Detect card bill payments that span multiple legs.

Transfer detection (transfers.py) handles 1:1 exact matches between
accounts.  This module handles the scenarios that 1:1 cannot reach:

  - CRED paying three cards in a single bank debit (1:N)
  - Two part-payments against one card statement (N:1)
  - A mix of bank debits and card credits that only tally as a group (N:M)

**Anti-false-positive design — the critical part.**

Subset-sum over a whole ledger *will* find coincidental matches.
Arithmetic alone is never sufficient evidence:

  - Multi-leg groups are only attempted when the bank leg independently
    names a payment rail (CRED, DREAMPLUG, BBPS, issuer name, card last-4).
  - Search is bounded: ≤12 date-nearest candidates, ≤5 legs per side.
  - Confidence decays with group size; below a floor it goes to review
    rather than applying.
  - Greedy order (1:1 → 1:N → N:1 → N:M) prevents large coincidental
    sums from stealing legs that would have matched exactly.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from ..models.schemas import (
    Account, AccountType, Category, ConfidenceSource,
    Direction, FlowRole, Transaction,
)
from ..rules import formats, institutions

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Narrations that independently identify a payment rail — the gate that
#: must open before any multi-leg group is attempted.  A coincidental
#: subset sum with none of these words in the narration must NOT match.
#:
#: The bill-payment wordings come from rules.formats: a core shared with the
#: direction reader and the categorizer, plus the two this gate alone accepts
#: (Dreamplug is CRED's payment entity; a bare "BBPS" is enough to attempt a
#: group even though it is not enough to name a category). A masked card
#: number is settlement's own signal and means nothing to the other two.
_SETTLEMENT_MARKERS = (formats.BILL_PAYMENT_MARKERS
                       + formats.SETTLEMENT_ONLY_BILL_MARKERS
                       + (r'XXXX\d{4}',))

#: The issuer half used to be eight bank names typed out here - a ninth copy
#: of the institution list, and an incomplete one: a Yes Bank, IDFC or Bank of
#: Baroda card bill named its issuer in the narration and still could not open
#: this gate, so those bills were never settled against the card.
#:
#: Only word-safe issuer tokens are used. "yes" and "bob" are registry
#: fragments and ordinary English words, and `\bYES\b` in a narration filter
#: would open the gate on any sentence containing the word "yes".
PAYMENT_RAIL_PATTERNS = re.compile(
    '|'.join(_SETTLEMENT_MARKERS
             + tuple(rf'\b{re.escape(word)}\b'
                     for word in institutions.narration_words())),
    re.IGNORECASE,
)

#: Wider than transfers.py's 4 — card issuers post with more lag.
MAX_DAY_GAP = 7
#: 1:1 matching uses near-exact amounts.
AMOUNT_TOLERANCE = Decimal("0.01")
#: Multi-leg residuals: wallet top-up or rounding.
RESIDUAL_ABS_MAX = Decimal("500")
RESIDUAL_PCT_MAX = Decimal("0.02")
#: Combinatorial bounds.
MAX_CANDIDATES = 12
MAX_LEGS_PER_SIDE = 5
#: Below this, a group goes to review rather than auto-applying.
CONFIDENCE_FLOOR = Decimal("0.5")


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass
class SettlementGroup:
    group_id: str
    kind: str  # 'card_settlement'
    outflow_legs: list = field(default_factory=list)   # bank debits
    inflow_legs: list = field(default_factory=list)     # card credits
    total_amount: Decimal = Decimal("0")
    residual: Decimal = Decimal("0")
    confidence: float = 0.0
    confirmed: bool = False


@dataclass
class SettlementReport:
    groups: list[SettlementGroup] = field(default_factory=list)
    review_items: list[tuple] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _group_confidence(legs: int) -> float:
    """How much to trust a match of `legs` legs on one side.

    Decays fast on purpose. One leg matching one leg on an exact amount is
    strong evidence; five legs summing to a sixth is the kind of coincidence
    a large ledger produces on its own, and must fall below CONFIDENCE_FLOOR
    so it goes to review rather than being applied.
    """
    return 0.78 - 0.09 * (legs - 1)


def _narration(txn: Transaction) -> str:
    """The text to search for payment-rail evidence."""
    return txn.normalized_description or txn.raw_description or ""


def _has_rail(txn: Transaction) -> bool:
    return bool(PAYMENT_RAIL_PATTERNS.search(_narration(txn)))


def _day_gap(a: Transaction, b: Transaction) -> int:
    return abs((a.txn_date - b.txn_date).days)


def _has_bank_coverage(
    txn_date: date,
    accounts: dict[str, Account],
    statements_by_account: dict[str, list] | None,
) -> bool:
    """Is there a parsed bank statement covering this date?

    This gates settlement inference: an unmatched card credit is only
    flagged as 'someone else paid' if the bank's statement for that period
    exists.  Without coverage, the flag is 'unknown_funding'.
    """
    if not statements_by_account:
        return False
    for aid, acct in accounts.items():
        if acct.account_type in {AccountType.SAVINGS, AccountType.CURRENT}:
            for stmt in statements_by_account.get(aid, []):
                if (stmt.period_start and stmt.period_end
                        and stmt.period_start <= txn_date <= stmt.period_end):
                    return True
    return False


def card_purchases_already_counted(
    txn_date: date,
    accounts: dict[str, Account],
    statements_by_account: dict[str, list] | None,
    window_days: int = 62,
) -> bool:
    """Has a card statement closed recently enough for this to be its bill?

    The mirror of `_has_bank_coverage`, asked from the other side, and it
    decides whether a card-bill payment is SPENDING or a TRANSFER.

    A bill is paid after the statement that raised it closes. If a credit
    card's statement closed in the weeks before this debit, the purchases on
    that statement are already in the ledger and already counted as spending
    - so counting the payment too charges the holder twice for one purchase.
    If no card statement has closed, the purchases were never imported and
    the payment is the only evidence any money was spent, which is when
    counting it is right.

    Sixty-two days because a card cycle is a month and a bill is due a few
    weeks after it closes; two cycles is generous without reaching back to
    a statement whose bill was plainly already settled.
    """
    if not statements_by_account:
        return False
    for account_id, account in (accounts or {}).items():
        if account.account_type != AccountType.CREDIT_CARD:
            continue
        for statement in statements_by_account.get(account_id, []):
            closed = getattr(statement, "period_end", None)
            if closed and 0 <= (txn_date - closed).days <= window_days:
                return True
    return False


def _load_confirmed_fingerprints(db) -> set[str]:
    """Fingerprints belonging to groups the user has already confirmed."""
    if db is None:
        return set()
    try:
        from ..db import repository as repo
        return repo.get_confirmed_fingerprints(db)
    except Exception as exc:
        log.warning("Could not load confirmed settlement groups: %s", exc)
        return set()


def _apply_group(group: SettlementGroup) -> None:
    """Stamp flags on every leg of a matched group."""
    for leg in group.outflow_legs:
        leg.is_internal_transfer = True
        leg.transfer_pair_id = group.group_id
        leg.is_mirror_leg = False
        leg.flow_role = FlowRole.TRANSFER_OUT.value
        leg.category = Category.CC_PAYMENT
        leg.category_source = ConfidenceSource.RULE
        leg.category_confidence = group.confidence

    for leg in group.inflow_legs:
        leg.is_internal_transfer = True
        leg.transfer_pair_id = group.group_id
        leg.is_mirror_leg = True
        leg.flow_role = FlowRole.CARD_SETTLEMENT.value
        leg.category = Category.CC_PAYMENT
        leg.category_source = ConfidenceSource.RULE
        leg.category_confidence = group.confidence


# --------------------------------------------------------------------------
# Core matcher
# --------------------------------------------------------------------------

def match_settlements(
    transactions: list[Transaction],
    accounts: dict[str, Account],
    db=None,
    statements_by_account: dict[str, list] | None = None,
) -> SettlementReport:
    """Match bank debits against card credits into settlement groups.

    Returns a report of matched groups and items needing review.
    Confirmed groups (from the DB) are never re-matched.
    """
    report = SettlementReport()

    card_ids = {
        aid for aid, acct in accounts.items()
        if acct.account_type == AccountType.CREDIT_CARD
    }
    cash_ids = {
        aid for aid, acct in accounts.items()
        if acct.account_type in {AccountType.SAVINGS, AccountType.CURRENT,
                                  AccountType.WALLET}
    }
    if not card_ids or not cash_ids:
        return report

    # 1. Load confirmed groups — their legs are never re-matched.
    confirmed_fps = _load_confirmed_fingerprints(db)

    # 2–3. Partition into card credits and bank debits.
    card_credits: list[Transaction] = []
    bank_debits: list[Transaction] = []

    for txn in transactions:
        if txn.fingerprint in confirmed_fps:
            continue
        if txn.is_internal_transfer:
            continue  # already claimed by transfer detection
        if txn.excluded:
            # A row taken out of every total is not a leg of anything.
            #
            # `detect_reversals` runs before this and excludes both halves of
            # a charge that failed and was refunded the same day - money that
            # never moved. Three of those were then matched into settlement
            # groups anyway, which put 1,922 of phantom charges inside real
            # card-bill settlements, and overwrote each row's role from
            # "excluded" to "card settlement". `derive_flow_role` states the
            # precedence this broke: an explicit exclusion beats everything,
            # because it is the only signal there that somebody looked.
            continue
        if txn.account_id in card_ids and txn.direction == Direction.CREDIT:
            card_credits.append(txn)
        elif txn.account_id in cash_ids and txn.direction == Direction.DEBIT:
            bank_debits.append(txn)

    # Track which transactions have been claimed by a group.
    claimed: set[str] = set()

    def available(txn: Transaction) -> bool:
        return txn.fingerprint not in claimed

    def claim(*txns: Transaction) -> None:
        for t in txns:
            claimed.add(t.fingerprint)

    # ------------------------------------------------------------------
    # Pass 1 — 1:1 exact match (highest confidence)
    # ------------------------------------------------------------------
    # Index card credits by amount for O(1) lookup.
    credits_by_amount: dict[Decimal, list[Transaction]] = defaultdict(list)
    for cc in card_credits:
        credits_by_amount[cc.amount].append(cc)

    # Largest first: a big card payment is the highest-value match to get
    # right, and claiming it early stops a small coincidental row stealing it.
    for debit in sorted(bank_debits, key=lambda t: -t.amount):
        if not available(debit):
            continue

        candidates = [
            cc for cc in credits_by_amount.get(debit.amount, [])
            if available(cc)
            and cc.account_id != debit.account_id
            and _day_gap(cc, debit) <= MAX_DAY_GAP
        ]
        if not candidates:
            continue

        best = min(candidates, key=lambda c: _day_gap(c, debit))
        gap = _day_gap(best, debit)
        confidence = 0.95 if gap <= 1 else (0.85 if gap <= 3 else 0.75)

        group = SettlementGroup(
            group_id=str(uuid.uuid4()),
            kind="card_settlement",
            outflow_legs=[debit],
            inflow_legs=[best],
            total_amount=debit.amount,
            residual=Decimal("0"),
            confidence=confidence,
        )
        _apply_group(group)
        report.groups.append(group)
        claim(debit, best)

    # ------------------------------------------------------------------
    # Pass 2 — 1:N (one bank debit pays multiple cards, e.g. CRED)
    # ------------------------------------------------------------------
    for debit in sorted(bank_debits, key=lambda t: -t.amount):
        if not available(debit):
            continue
        if not _has_rail(debit):
            continue  # narration gate

        # Date-nearest available card credits.
        candidates = sorted(
            [cc for cc in card_credits
             if available(cc)
             and cc.account_id != debit.account_id
             and _day_gap(cc, debit) <= MAX_DAY_GAP],
            key=lambda c: _day_gap(c, debit),
        )[:MAX_CANDIDATES]

        if len(candidates) < 2:
            continue

        found = False
        for r in range(2, min(MAX_LEGS_PER_SIDE, len(candidates)) + 1):
            if found:
                break
            for subset in combinations(candidates, r):
                if not all(available(c) for c in subset):
                    continue
                credit_sum = sum(c.amount for c in subset)
                residual = abs(debit.amount - credit_sum)

                if residual > RESIDUAL_ABS_MAX and (
                    debit.amount == 0
                    or residual / debit.amount > RESIDUAL_PCT_MAX
                ):
                    continue

                # NOT clamped to the floor. Clamping made the floor
                # unreachable, so the review branch below was dead code and
                # every multi-leg match was applied automatically however
                # speculative it was. The decay is also steep enough to
                # actually cross the floor within MAX_LEGS_PER_SIDE: a
                # two-legged match is decent evidence, a five-legged one is
                # arithmetic looking for a pattern.
                confidence = _group_confidence(len(subset))

                group = SettlementGroup(
                    group_id=str(uuid.uuid4()),
                    kind="card_settlement",
                    outflow_legs=[debit],
                    inflow_legs=list(subset),
                    total_amount=debit.amount,
                    residual=residual,
                    confidence=confidence,
                )
                if confidence >= float(CONFIDENCE_FLOOR):
                    _apply_group(group)
                    report.groups.append(group)
                    claim(debit, *subset)
                else:
                    for leg in [debit] + list(subset):
                        leg.needs_review = True
                        leg.review_reason = (
                            f"Low-confidence ({confidence:.0%}) multi-leg "
                            f"settlement match — confirm or dismiss"
                        )
                    report.review_items.extend(
                        (leg, leg.review_reason)
                        for leg in [debit] + list(subset)
                    )
                found = True
                break

    # ------------------------------------------------------------------
    # Pass 3 — N:1 (multiple bank debits pay one card credit)
    # ------------------------------------------------------------------
    for credit in sorted(card_credits, key=lambda t: -t.amount):
        if not available(credit):
            continue

        candidates = sorted(
            [d for d in bank_debits
             if available(d)
             and d.account_id != credit.account_id
             and _day_gap(d, credit) <= MAX_DAY_GAP],
            key=lambda d: _day_gap(d, credit),
        )[:MAX_CANDIDATES]

        if len(candidates) < 2:
            continue

        found = False
        for r in range(2, min(MAX_LEGS_PER_SIDE, len(candidates)) + 1):
            if found:
                break
            for subset in combinations(candidates, r):
                if not all(available(d) for d in subset):
                    continue
                # Narration gate: at least one bank leg must name a rail.
                if not any(_has_rail(d) for d in subset):
                    continue

                debit_sum = sum(d.amount for d in subset)
                residual = abs(debit_sum - credit.amount)

                if residual > RESIDUAL_ABS_MAX and (
                    credit.amount == 0
                    or residual / credit.amount > RESIDUAL_PCT_MAX
                ):
                    continue

                # NOT clamped to the floor. Clamping made the floor
                # unreachable, so the review branch below was dead code and
                # every multi-leg match was applied automatically however
                # speculative it was. The decay is also steep enough to
                # actually cross the floor within MAX_LEGS_PER_SIDE: a
                # two-legged match is decent evidence, a five-legged one is
                # arithmetic looking for a pattern.
                confidence = _group_confidence(len(subset))

                group = SettlementGroup(
                    group_id=str(uuid.uuid4()),
                    kind="card_settlement",
                    outflow_legs=list(subset),
                    inflow_legs=[credit],
                    total_amount=credit.amount,
                    residual=residual,
                    confidence=confidence,
                )
                if confidence >= float(CONFIDENCE_FLOOR):
                    _apply_group(group)
                    report.groups.append(group)
                    claim(credit, *subset)
                else:
                    for leg in list(subset) + [credit]:
                        leg.needs_review = True
                        leg.review_reason = (
                            f"Low-confidence ({confidence:.0%}) multi-leg "
                            f"settlement match — confirm or dismiss"
                        )
                    report.review_items.extend(
                        (leg, leg.review_reason)
                        for leg in list(subset) + [credit]
                    )
                found = True
                break

    # ------------------------------------------------------------------
    # 6. Handle unmatched card credits
    # ------------------------------------------------------------------
    for credit in card_credits:
        if not available(credit):
            continue
        # Only act on credits that look like a bill payment.
        if credit.category != Category.CC_PAYMENT:
            continue

        credit.flow_role = FlowRole.CARD_SETTLEMENT.value

        if _has_bank_coverage(credit.txn_date, accounts, statements_by_account):
            credit.needs_review = True
            credit.review_reason = (
                "Unmatched card payment with bank coverage present "
                "— likely third-party funding"
            )
        else:
            credit.needs_review = True
            credit.review_reason = "unknown_funding"

        report.review_items.append((credit, credit.review_reason))

    if report.groups:
        total = sum(g.total_amount for g in report.groups)
        report.notes.append(
            f"Matched {len(report.groups)} settlement group(s) totalling "
            f"{total:,.2f}. Card credits in these groups are excluded from "
            f"income — they settle a liability, not new money."
        )

    return report
