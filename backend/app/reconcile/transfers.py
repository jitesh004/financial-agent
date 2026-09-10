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
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from ..rules import formats
from ..models.schemas import (Account, AccountType, Category, ConfidenceSource,
                              Direction, LIABILITY_TYPES, LOAN_TYPES,
                              Transaction)

log = logging.getLogger(__name__)

#: How many days apart the two legs of one transfer may be. NEFT/IMPS settle
#: same-day, but a weekend or a card issuer's posting lag can stretch it.
MAX_DAY_GAP = 4

#: Amounts must match this closely. Transfers move an exact figure; anything
#: looser starts pairing unrelated transactions of similar size.
AMOUNT_TOLERANCE = Decimal("0.01")


#: What `category_rule` says on a category a MATCHER assigned rather than a
#: narration rule. Pairing rewrites the category of both legs - a debit to a
#: broker becomes "cc_payment" because the row it was paired with was a card
#: - and `ConfidenceSource.RULE` cannot tell that apart from a rule that
#: actually read the narration. So when the pairing is later withdrawn the
#: label it wrote survives it, and the row goes on being counted as a card
#: settlement with nothing left to settle. Naming the source is what lets
#: `enrich` release the label along with the pairing.
PAIRING_RULE = "transfer-pairing"


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
            leg.category_rule = PAIRING_RULE
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


#: A narration that identifies a counterparty the holder paid: a UPI
#: handle, a VPA, or a person/merchant name in the payee slot. Present on a
#: purchase, absent from a bill payment - which names a rail ("BBPS-PAYMENT
#: INR"), an issuer, or nothing readable at all.
_NAMES_A_PAYEE = re.compile(
    r"\bUPI[_/]|@[a-z]{2,}|\bVPA\b|\bP2[AMP]\b",
    re.IGNORECASE,
)


def _names_a_payee(txn: Transaction) -> bool:
    """Does this card row name who was paid, rather than what settled it?"""
    text = txn.normalized_description or txn.raw_description or ""
    if formats.BILL_PAYMENT.search(text):
        return False        # says "payment" outright; that wins.
    return bool(_NAMES_A_PAYEE.search(text))


#: A payee that identifies the money's real destination, where that
#: destination is not a credit card. Kept in step with the INVESTMENT rules
#: in `categorize.rules`, which is where these names are already known.
#:
#: Leading word boundary only. A UPI handle welds the brand to a suffix -
#: the row that started this reads "BR/zerodhabroking/XXXX9584/..." - so a
#: trailing boundary refuses the exact strings this exists to catch. The
#: leading one is what keeps a brand from matching inside another word.
_NAMED_ELSEWHERE = re.compile(
    r"\bZERODHA|\bGROWW|\bUPSTOX|\bANGEL\s*ONE|\bKUVERA|"
    r"\bSMALLCASE|\bINDMONEY|\bBSE\s*STAR\s*MF|\bBSESTARMF|"
    r"\bKFINTECH|\bCAMSONLINE|\bMF\s*CENTRAL|\bDEMAT|"
    r"\bNSE\s*CLEARING|\bINDIANCLEA",
    re.IGNORECASE,
)


def _names_another_destination(txn: Transaction) -> bool:
    """Does this debit say where it went, and say somewhere that is not a card?

    A settlement is inferred from amount and timing, and on a large ledger
    those coincide often. The narration is the one piece of direct evidence
    about where the money actually went, and it was not being read: a 64,000
    UPI transfer to "zerodhabroking" was adopted as the funding leg of a
    group whose other members were an HSBC and an ICICI card bill summing to
    63,820 - close enough on the numbers, and wrong. It took 64,000 out of
    "invested" and attributed two real card payments to a transfer that had
    nothing to do with them.

    A row that names its payee is not available to be guessed about. It can
    still be a card payment when the narration says nothing either way,
    which is the ordinary case this matcher is for.
    """
    text = f"{txn.merchant or ''} {txn.raw_description or ''}"
    return bool(_NAMED_ELSEWHERE.search(text))


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

        # A card row that plainly names a MERCHANT is a purchase, and a
        # purchase is not a bill being settled.
        #
        # This loop took every row on a credit-card account as a candidate,
        # because the docstring above explains that a payment is often
        # parsed as a debit and so direction cannot be the test. That is
        # right, but nothing was put in its place - so an ordinary card
        # purchase was eligible, and a 90 paan-shop charge on a Yes Bank
        # card got paired against a same-day 90 UPI transfer to a broker.
        # Both were then relabelled "credit card payment".
        #
        # Stated as a refusal rather than a requirement, deliberately. The
        # rows this function exists for are the ones whose narration did NOT
        # survive extraction - the HDFC card writes a 1.13 lakh payment as
        # "+ C" - so demanding positive evidence of a payment would throw
        # away exactly the case it was written to catch. What can be said
        # with confidence is the other direction: a row that names a UPI
        # payee is that payee's purchase, whatever else is true.
        if txn.direction == Direction.DEBIT and _names_a_payee(txn):
            continue

        candidates = [
            c for c in cash_debits.get(txn.amount, ())
            if id(c) not in claimed
            and not c.is_internal_transfer
            and abs((c.txn_date - txn.txn_date).days) <= MAX_DAY_GAP
            # ...and the funding row must not name somewhere else it went.
            # Amount and timing coincide constantly on a large ledger; a
            # narration that names a broker is direct evidence that beats
            # both. See settlement._names_another_destination.
            and not _names_another_destination(c)
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
            leg.category_rule = PAIRING_RULE
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
        txn.category_rule = PAIRING_RULE
        txn.category_confidence = 0.9

        cash_leg.transfer_pair_id = pair_id
        cash_leg.category = Category.INVESTMENT
        cash_leg.category_source = ConfidenceSource.RULE
        cash_leg.category_rule = PAIRING_RULE
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


def detect_reversals(transactions: list[Transaction],
                     accounts: dict[str, Account] | None = None) -> int:
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
    # A loan account's own statement is out of scope entirely.
    #
    # Those rows are the LENDER's bookkeeping - "EMI due for Inst.43" on one
    # line and "Receipt Chq No..." on the next - so every instalment is a
    # same-day, same-amount, opposite-direction pair by construction. They
    # are already excluded from every total as LENDER_LEDGER, so cancelling
    # them changes nothing and flagging them buries the real ambiguous pairs
    # under 114 rows of routine loan servicing.
    #
    # Taken from the ACCOUNT because this runs before roles are stamped -
    # `flow_role` is not populated yet at step 2c of the pipeline.
    lender_accounts = {
        account_id for account_id, account in (accounts or {}).items()
        if getattr(account, "account_type", None) in LOAN_TYPES
    }

    by_account: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.is_internal_transfer or txn.excluded:
            continue
        if txn.account_id in lender_accounts:
            continue
        by_account[txn.account_id or ""].append(txn)

    reversed_count = 0
    for acct_txns in by_account.values():
        credits = [t for t in acct_txns if t.direction == Direction.CREDIT]
        debits = [t for t in acct_txns if t.direction == Direction.DEBIT]
        claimed: set[int] = set()

        for credit in credits:
            near = [
                d for d in debits
                if id(d) not in claimed
                and d.amount == credit.amount
                and abs((d.txn_date - credit.txn_date).days) <= REVERSAL_MAX_DAY_GAP
            ]
            if not near:
                continue

            # Either kind of evidence will do, and the marker is the
            # stronger of the two.
            #
            # Requiring the merchant to match missed the case the rails
            # actually produce: a UPI reversal does not repeat the payee, it
            # PREFIXES a marker to it - "UPI/Parviom Te/parkplus.payu@"
            # reversed by "UPI/RFNDPARKPL/parkplusio.pay", and
            # "UPI/DUMMY NAME/XXXX0004" by "UPI/RVSLDUMMY /XXXX0004". The
            # merchant tokens differ, so `_same_merchant` said no, and both
            # halves stood: the debit counted as spending and the credit as
            # income, for money that never moved.
            candidates = [d for d in near
                          if _same_merchant(d, credit) or _reversal_of(credit, d)]
            if candidates:
                best = min(candidates,
                          key=lambda d: abs((d.txn_date - credit.txn_date).days))
                claimed.add(id(best))

                best.excluded = True
                credit.excluded = True
                note = ("Reversed: a failed charge refunded the same day, "
                        "not real spending.")
                best.note = f"{best.note} {note}".strip()
                credit.note = f"{credit.note} {note}".strip()
                reversed_count += 1
                continue

            # Same account, same amount, same day, opposite directions - and
            # nothing to say whether it is one cancelled transaction or two
            # real ones. Left counted as BOTH an expense and income, which is
            # the one reading that is wrong either way: a 55,604 debit at
            # Vijay Sales against a same-day credit from the same store put
            # 55,604 on each side of this ledger for a purchase that was
            # probably returned at the till.
            #
            # Not cancelled, because the evidence is not there - flagged, so
            # the guess belongs to the person who can actually check it.
            best = min(near, key=lambda d: abs((d.txn_date - credit.txn_date).days))
            claimed.add(id(best))
            reason = (
                "This is the same amount, the same day and the same account "
                "as its opposite - it looks like a cancelled transaction, but "
                "nothing on either row says so. Counted as real spending AND "
                "real money in until you confirm."
            )
            for leg in (best, credit):
                if not leg.needs_review:
                    leg.needs_review = True
                    leg.review_reason = reason

    return reversed_count


#: Markers a rail glues onto the payee when it sends money back. Matched as
#: a prefix on a description TOKEN, never anywhere in the string: "RETURN"
#: inside "RETURNS PVT LTD" is a company name, not a reversal.
_REVERSAL_MARKERS = ("RVSL", "RFND", "REVERSAL", "REFUND", "RETURN", "REV")


def _reversal_of(credit: Transaction, debit: Transaction) -> bool:
    """Does this credit's narration announce itself as undoing that debit?

    True when the credit carries a reversal marker AND enough of the debit's
    payee survives alongside it to tie the two together. Both halves are
    needed: the marker alone would cancel any refund against any same-sized
    debit that happened to land the same day.
    """
    text = (credit.raw_description or "").upper()
    tokens = re.split(r"[^A-Z0-9]+", text)
    if not any(tok.startswith(m) for tok in tokens for m in _REVERSAL_MARKERS):
        return False

    # The payee, as the rail writes it: alphabetic runs of 4+ characters,
    # which skips the masked digits and the two-letter rail codes that every
    # UPI narration carries and that would otherwise match anything.
    def _words(txn: Transaction) -> set[str]:
        raw = f"{txn.merchant or ''} {txn.raw_description or ''}".upper()
        return {w for w in re.findall(r"[A-Z]{4,}", raw)
                if w not in {"UPI", "BANK", "NEFT", "IMPS", "RTGS"}}

    debit_words = _words(debit)
    if not debit_words:
        return False
    credit_words = _words(credit)
    # A marker is often welded to the payee ("RVSLDUMMY"), so a plain
    # intersection misses it - test containment both ways.
    return any(w in c or c in w
               for w in debit_words for c in credit_words
               if len(w) >= 4 and len(c) >= 4)


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

    # With no running balance on either row, the RAW narration is the only
    # evidence there is - and it is the field that still carries the bank
    # reference.
    #
    # This function's own reasoning says "two real transactions would carry
    # different bank references", and that is true; but it was reading
    # `normalized_description`, which is precisely where normalisation
    # strips the reference out. Three 26 payments to one pan shop on one day
    # all normalise to "UPI VIJAY BHOJA SHETTY IND" while their raw rows read
    # "...Ref No: RT260490388000420000075" and "...RT260490388000770000358" -
    # different payments, deduplicated down to one. 28 real rows on this
    # ledger, and the shape is routine: any card statement with no running
    # balance and a habit of small repeat payments hits it.
    #
    # A prefix still counts as one row: that is the truncation case, where
    # two extractions cut the same narration at different lengths. Anything
    # else, with no balance to appeal to, is two transactions.
    raw_a = (a.raw_description or "").strip()
    raw_b = (b.raw_description or "").strip()
    no_balance = a.balance_after is None and b.balance_after is None
    if no_balance and raw_a and raw_b and raw_a != raw_b:
        short_raw, long_raw = ((raw_a, raw_b) if len(raw_a) <= len(raw_b)
                               else (raw_b, raw_a))
        if not long_raw.startswith(short_raw):
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
