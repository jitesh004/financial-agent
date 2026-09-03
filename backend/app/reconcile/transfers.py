"""Detect money that moved between the user's own accounts.

This is the module that decides whether the app tells the truth.

A user uploads a bank statement and a credit card statement. The card bill
payment appears as a debit in the bank AND as a payment credit on the card.
Sum the debits naively and you have inflated their spending by the entire bill.
The same applies to self-transfers, SIP debits matched against an investment
statement, and EMI debits matched against a loan statement.

Nothing here deletes a transaction. Both legs stay in the ledger - they really
did happen - but they get flagged so the analytics layer can exclude them from
"spending". Deleting would break the reconciliation gate, which must continue to
tie out against the original statement.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from ..models.schemas import (Account, AccountType, Category, ConfidenceSource,
                              Direction, LIABILITY_TYPES, Transaction)

log = logging.getLogger(__name__)

#: How many days apart the two legs of one transfer may be. NEFT/IMPS settle
#: same-day, but a weekend or a card issuer's posting lag can stretch it.
MAX_DAY_GAP = 4

#: Amounts must match this closely. Transfers move an exact figure; anything
#: looser starts pairing unrelated transactions of similar size.
AMOUNT_TOLERANCE = Decimal("0.01")


@dataclass
class TransferPair:
    pair_id: str
    debit_txn_id: str
    credit_txn_id: str
    amount: Decimal
    day_gap: int
    from_account: str
    to_account: str
    kind: str  # "cc_payment" | "investment" | "loan_repayment" | "self_transfer"
    confidence: float


@dataclass
class TransferReport:
    pairs: list[TransferPair] = field(default_factory=list)
    total_amount: Decimal = Decimal("0")
    #: Money that would have been double counted as spending without this pass.
    double_count_avoided: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)


def _classify(
    from_account: Account | None,
    to_account: Account | None,
) -> tuple[str, Category]:
    """Name the transfer by what the receiving account is."""
    to_type = to_account.account_type if to_account else AccountType.UNKNOWN

    if to_type == AccountType.CREDIT_CARD:
        return "cc_payment", Category.CC_PAYMENT
    if to_type == AccountType.INVESTMENT:
        return "investment", Category.INVESTMENT
    if to_type in {AccountType.HOME_LOAN, AccountType.PERSONAL_LOAN, AccountType.AUTO_LOAN}:
        return "loan_repayment", Category.EMI
    return "self_transfer", Category.TRANSFER


def detect_transfers(
    transactions: list[Transaction],
    accounts: dict[str, Account],
) -> TransferReport:
    """Pair debits in one account against credits in another.

    Matching is greedy over an index keyed by rounded amount, which keeps this
    near-linear rather than comparing every debit to every credit. With a decade
    of statements loaded that difference matters.
    """
    report = TransferReport()
    if len(accounts) < 2:
        report.notes.append(
            "Only one account was supplied, so no cross-account transfers could "
            "be detected. Upload the matching card/loan statements to avoid "
            "double counting transfers as spending."
        )
        return report

    # Index credits by exact amount for O(1) candidate lookup.
    credits_by_amount: dict[Decimal, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.direction == Direction.CREDIT and not txn.is_internal_transfer:
            credits_by_amount[txn.amount].append(txn)

    claimed: set[int] = set()  # id() of credit legs already paired

    debits = [t for t in transactions
              if t.direction == Direction.DEBIT and not t.is_internal_transfer]
    # Largest first: a big EMI or card payment is the highest-value match to get
    # right, and claiming it early stops a small coincidental row stealing it.
    debits.sort(key=lambda t: -t.amount)

    for debit in debits:
        candidates = _find_candidates(debit, credits_by_amount, claimed)
        if not candidates:
            continue

        best = min(candidates, key=lambda c: (abs((c.txn_date - debit.txn_date).days),
                                              c.account_id or ""))
        gap = abs((best.txn_date - debit.txn_date).days)

        from_account = accounts.get(debit.account_id or "")
        to_account = accounts.get(best.account_id or "")
        kind, category = _classify(from_account, to_account)

        pair_id = str(uuid.uuid4())
        for leg, cat in ((debit, category), (best, category)):
            leg.is_internal_transfer = True
            leg.transfer_pair_id = pair_id
            leg.category = cat
            leg.category_source = ConfidenceSource.RULE
            leg.category_confidence = 0.95

        # The debit is the cash actually leaving; the credit is the receiving
        # account's record of the same money. Cashflow must count the first and
        # ignore the second, or a 38,420 EMI shows up as 76,840 committed.
        debit.is_mirror_leg = False
        best.is_mirror_leg = True

        claimed.add(id(best))
        report.pairs.append(TransferPair(
            pair_id=pair_id,
            debit_txn_id=debit.id or "",
            credit_txn_id=best.id or "",
            amount=debit.amount,
            day_gap=gap,
            from_account=from_account.display_name() if from_account else "unknown",
            to_account=to_account.display_name() if to_account else "unknown",
            kind=kind,
            confidence=0.95 if gap <= 1 else 0.8,
        ))
        report.total_amount += debit.amount
        report.double_count_avoided += debit.amount

    _pair_investment_mirrors(transactions, accounts, report, claimed)
    _pair_card_payment_mirrors(transactions, accounts, report, claimed)

    if report.pairs:
        report.notes.append(
            f"Matched {len(report.pairs)} transfers between your own accounts "
            f"totalling {report.total_amount:,.2f}. These are excluded from "
            f"spending totals - without this, that amount would be counted twice."
        )
    return report


def _pair_card_payment_mirrors(
    transactions: list[Transaction],
    accounts: dict[str, Account],
    report: TransferReport,
    claimed: set[int],
) -> None:
    """Pair a bank bill payment against the card's own record of it.

    The debit/credit matcher only sees a payment when the card statement books
    it as a CREDIT. In practice the card row is often parsed as a debit - card
    layouts mark a payment with a bare "+" or "CR" glyph that does not always
    survive extraction - and the pair is missed entirely. Both legs then count
    as spending, and the same rupees are charged to the user twice.

    Matching is deliberately tight: identical amount, one side a card and the
    other a cash account, within a few days. The bank leg is kept as the real
    cash movement; the card leg is flagged as the mirror.
    """
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
        return

    cash_debits: dict[Decimal, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if (txn.direction == Direction.DEBIT
                and not txn.is_internal_transfer
                and txn.account_id in cash_ids):
            cash_debits[txn.amount].append(txn)

    for txn in transactions:
        if txn.account_id not in card_ids or txn.is_internal_transfer:
            continue

        candidates = [
            c for c in cash_debits.get(txn.amount, ())
            if id(c) not in claimed
            and not c.is_internal_transfer
            and abs((c.txn_date - txn.txn_date).days) <= MAX_DAY_GAP
        ]
        if not candidates:
            continue

        cash_leg = min(candidates, key=lambda c: abs((c.txn_date - txn.txn_date).days))
        gap = abs((cash_leg.txn_date - txn.txn_date).days)
        pair_id = str(uuid.uuid4())

        for leg in (cash_leg, txn):
            leg.is_internal_transfer = True
            leg.transfer_pair_id = pair_id
            leg.category = Category.CC_PAYMENT
            leg.category_source = ConfidenceSource.RULE
            leg.category_confidence = 0.85
        # The bank is where the money actually moved.
        cash_leg.is_mirror_leg = False
        txn.is_mirror_leg = True

        claimed.add(id(cash_leg))
        report.pairs.append(TransferPair(
            pair_id=pair_id,
            debit_txn_id=cash_leg.id or "",
            credit_txn_id=txn.id or "",
            amount=txn.amount,
            day_gap=gap,
            from_account=(accounts[cash_leg.account_id].display_name()
                          if cash_leg.account_id in accounts else "unknown"),
            to_account=(accounts[txn.account_id].display_name()
                        if txn.account_id in accounts else "unknown"),
            kind="cc_payment",
            confidence=0.85 if gap <= 1 else 0.7,
        ))
        report.total_amount += txn.amount
        report.double_count_avoided += txn.amount


def _pair_investment_mirrors(
    transactions: list[Transaction],
    accounts: dict[str, Account],
    report: TransferReport,
    claimed: set[int],
) -> None:
    """Pair a bank SIP debit against the same purchase on a fund statement.

    The debit/credit matcher cannot see these, because both legs are debits: the
    bank shows money going out, and the fund statement also shows a purchase
    (money in, from the fund's point of view, but rendered in the debit column
    of a holdings statement). Left unpaired, a 25,000 SIP is counted as 50,000
    invested the moment a user uploads both statements - and the more diligent
    the user is about uploading everything, the more wrong the number gets.

    The bank leg is kept as the real cash outflow; the fund leg is flagged as
    the mirror, so uploading the fund statement alone still counts correctly.
    """
    investment_ids = {
        aid for aid, acct in accounts.items()
        if acct.account_type == AccountType.INVESTMENT
    }
    if not investment_ids:
        return

    cash_debits: dict[Decimal, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if (txn.direction == Direction.DEBIT
                and not txn.is_internal_transfer
                and txn.account_id not in investment_ids):
            cash_debits[txn.amount].append(txn)

    for txn in transactions:
        if txn.account_id not in investment_ids:
            continue
        if txn.is_internal_transfer or txn.direction != Direction.DEBIT:
            continue

        candidates = [
            c for c in cash_debits.get(txn.amount, ())
            if id(c) not in claimed
            and abs((c.txn_date - txn.txn_date).days) <= MAX_DAY_GAP
        ]
        if not candidates:
            continue

        cash_leg = min(candidates, key=lambda c: abs((c.txn_date - txn.txn_date).days))
        gap = abs((cash_leg.txn_date - txn.txn_date).days)
        pair_id = str(uuid.uuid4())

        # Only the fund-side leg is marked internal. The bank leg stays a real
        # outflow categorized as INVESTMENT, which is what the cashflow view wants.
        txn.is_internal_transfer = True
        txn.is_mirror_leg = True
        txn.transfer_pair_id = pair_id
        txn.category = Category.INVESTMENT
        txn.category_source = ConfidenceSource.RULE
        txn.category_confidence = 0.9

        cash_leg.transfer_pair_id = pair_id
        cash_leg.category = Category.INVESTMENT
        cash_leg.category_source = ConfidenceSource.RULE
        cash_leg.category_confidence = 0.9

        claimed.add(id(cash_leg))
        report.pairs.append(TransferPair(
            pair_id=pair_id,
            debit_txn_id=cash_leg.id or "",
            credit_txn_id=txn.id or "",
            amount=txn.amount,
            day_gap=gap,
            from_account=(accounts.get(cash_leg.account_id or "").display_name()
                          if accounts.get(cash_leg.account_id or "") else "unknown"),
            to_account=(accounts.get(txn.account_id or "").display_name()
                        if accounts.get(txn.account_id or "") else "unknown"),
            kind="investment",
            confidence=0.9 if gap <= 1 else 0.75,
        ))
        report.total_amount += txn.amount
        report.double_count_avoided += txn.amount


def _find_candidates(
    debit: Transaction,
    credits_by_amount: dict[Decimal, list[Transaction]],
    claimed: set[int],
) -> list[Transaction]:
    """Credits that could be the far leg of this debit."""
    out: list[Transaction] = []
    for credit in credits_by_amount.get(debit.amount, ()):
        if id(credit) in claimed:
            continue
        if credit.account_id == debit.account_id:
            continue  # a transfer must cross accounts
        if abs((credit.txn_date - debit.txn_date).days) > MAX_DAY_GAP:
            continue
        out.append(credit)
    return out


# --------------------------------------------------------------------------
# Duplicate statement detection
# --------------------------------------------------------------------------

def find_duplicate_transactions(transactions: list[Transaction]) -> list[Transaction]:
    """Find transactions that appear more than once within the same account.

    Users upload overlapping statements constantly - a monthly and a quarterly
    covering the same weeks, or the same file twice under different names.
    Content hashing catches identical files; this catches identical *rows*,
    which is the case content hashing misses.

    Returns the extra copies (the first occurrence of each group is kept).
    """
    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for txn in sorted(transactions, key=lambda t: (t.txn_date, t.raw_description)):
        groups[(txn.account_id, txn.txn_date, txn.amount, txn.direction)].append(txn)

    duplicates: list[Transaction] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # Keep the longest description: when two extractions of the same row
        # disagree it is because one was truncated, and the longer one carries
        # the full bank reference.
        kept: list[Transaction] = []
        for txn in sorted(members, key=lambda t: -len(t.normalized_description or "")):
            if any(_is_same_row(txn, other) for other in kept):
                duplicates.append(txn)
            else:
                kept.append(txn)

    return duplicates


#: How many days apart a failed charge and its own reversal may post. A
#: gateway typically reverses same-day or the next business day; this is
#: deliberately tighter than MAX_DAY_GAP, which is for a transfer settling
#: across accounts, not a same-account refund of a failed attempt.
REVERSAL_MAX_DAY_GAP = 3


def detect_reversals(transactions: list[Transaction]) -> int:
    """Cancel out a failed charge against its own same-account refund.

    A payment gateway that fails a charge often posts BOTH the debit and its
    reversal before a retry succeeds - one debit, a same-amount credit, and a
    second debit, all on one card, one day. That is not three real events, it
    is one failed attempt (net zero) and one that actually went through. Left
    alone, the failed debit counted as real spending and the refund credit
    either inflated income or, if it happened to share a category with
    spending, silently netted against something it had nothing to do with.

    This is deliberately narrower than a transfer: nothing here crosses
    accounts (`detect_transfers` already refuses a same-account pair for
    exactly that reason), and a same-amount, same-account, same-day pair is
    common enough by coincidence that amount and date alone are not enough -
    requiring the same merchant too is what keeps this from cancelling two
    genuinely unrelated transactions that happen to match on size and timing.
    """
    by_account: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.is_internal_transfer or txn.excluded:
            continue
        by_account[txn.account_id or ""].append(txn)

    reversed_count = 0
    for acct_txns in by_account.values():
        credits = [t for t in acct_txns if t.direction == Direction.CREDIT]
        debits = [t for t in acct_txns if t.direction == Direction.DEBIT]
        claimed: set[int] = set()

        for credit in credits:
            candidates = [
                d for d in debits
                if id(d) not in claimed
                and d.amount == credit.amount
                and abs((d.txn_date - credit.txn_date).days) <= REVERSAL_MAX_DAY_GAP
                and _same_merchant(d, credit)
            ]
            if not candidates:
                continue
            best = min(candidates,
                      key=lambda d: abs((d.txn_date - credit.txn_date).days))
            claimed.add(id(best))

            best.excluded = True
            credit.excluded = True
            note = "Reversed: a failed charge refunded the same day, not real spending."
            best.note = f"{best.note} {note}".strip()
            credit.note = f"{credit.note} {note}".strip()
            reversed_count += 1

    return reversed_count


def _same_merchant(a: Transaction, b: Transaction) -> bool:
    """Same merchant, allowing for one description carrying an extra word.

    Real extractions of the same gateway's narration commonly disagree by a
    trailing city/rail token ("...Bengaluru U IND" vs "...Bengaluru IND") -
    a strict equality would miss the exact pair this function exists for.
    """
    m1 = (a.merchant or a.raw_description or "").strip().upper()
    m2 = (b.merchant or b.raw_description or "").strip().upper()
    if not m1 or not m2:
        return False
    return m1 == m2 or m1 in m2 or m2 in m1


def _balances_agree(a: Transaction, b: Transaction) -> bool:
    """A genuine repeat moves the running balance; a duplicate does not."""
    if a.balance_after is not None and b.balance_after is not None:
        return a.balance_after == b.balance_after
    return a.balance_after is None and b.balance_after is None


#: How much narration two rows must share before a matching running balance
#: is taken as proof they are one row.
#:
#: Measured on a real ledger. Pairs that were genuinely different payments -
#: same amount, same day, same account, on a card statement whose balance
#: column is not a running balance at all - shared 4 to 6 characters. Pairs
#: that were one row cut at different points shared 12 or more.
#:
#: Set at the true minimum rather than in the middle of that gap, because the
#: middle is not empty: two different stalls billing the same amount on the
#: same day read "CHAI STALL ONE" and "CHAI STALL TWO", which share eleven.
#: A shared merchant NAME is not evidence; a shared bank reference is, and
#: those run long.
_SHARED_RUN_FLOOR = 12


def _longest_shared_run(a: str, b: str) -> int:
    """Length of the longest run of characters the two have in common.

    A prefix test only catches truncation at the END. Real statements clip
    the other way just as often - one extraction keeps the head of a
    narration and the next keeps its tail, overlapping in the middle:

        UPI/AMOL BALAS/amol222patil@o/Fridge/BANK OF BA/XXXX8667/HDF
                                             BA/XXXX8667/HDFe180beb4f7e94a4

    Neither prefixes the other, and they are one payment.
    """
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        current = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                current[j] = previous[j - 1] + 1
                if current[j] > best:
                    best = current[j]
        previous = current
    return best


def _is_same_row(a: Transaction, b: Transaction) -> bool:
    """Whether two rows of identical account/date/amount/direction are one row.

    A genuine repeat (two identical chai payments in one afternoon) is entirely
    possible, so the running balance has to agree as well.

    Beyond an exact match, one description being a strict PREFIX of the other
    means a single row that two extractions cut at different lengths - the
    monthly and the quarterly statement both cover October, and one clipped
    "IBL897436BA0A5D47C88E89B68A0EBCA9" to "...E89B68". Two real transactions
    would carry different bank references, so neither could prefix the other.
    The 18-character floor keeps a short, generic narration from swallowing an
    unrelated row.
    """
    if not _balances_agree(a, b):
        return False
    da = (a.normalized_description or "").strip()
    db = (b.normalized_description or "").strip()
    if da[:60] == db[:60]:
        return True
    short, long_ = (da, db) if len(da) <= len(db) else (db, da)
    if len(short) >= 18 and long_.startswith(short):
        return True

    # A matching running balance is the strong evidence, and it unlocks a
    # weaker reading of the narration. Two credits of the same amount into
    # one account on one day CANNOT both leave the same closing balance - the
    # second would leave it higher by the amount. So when the balances agree
    # exactly, a shared run of narration is enough; the pair need not prefix
    # each other.
    #
    # Found 32 of these in a real ledger, worth 4.14 lakh of phantom money -
    # including one salary counted twice because the monthly statement wrote
    # "NEFT-CMS1812612535608-ACME TECHNOLOGIES..." and the quarterly one
    # wrote "TECHNOLOGIES PRIVATELIMI- PANKAJSALJUN26CMS1-".
    both_balances = a.balance_after is not None and b.balance_after is not None
    if both_balances:
        return _longest_shared_run(da, db) >= _SHARED_RUN_FLOOR
    # With no balance to check against, the prefix rule above is the only
    # evidence there is, and it has already said no.
    return False
