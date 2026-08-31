"""Match bureau accounts against the ledger's own accounts.

This is the part of the bureau import that is actually worth having. A report
on its own is a list of numbers; laid alongside the ledger it answers the
question the ledger cannot ask of itself - *what am I missing?*

Three outcomes, and the difference between them is the whole design:

  - **Both agree.** The bureau names an account the ledger has, and the two
    balances can be compared. A gap is worth a look.
  - **Bureau only.** A lender is reporting an account no statement has ever
    been imported for. This is the valuable one: it is money owed that every
    total in this app is currently blind to.
  - **Ledger only.** Usually fine. Bureaus do not report savings accounts,
    wallets or investments, and a closed card can drop off entirely.

Matching is deliberately conservative. A suffix match on the last four digits
plus a compatible account type is strong enough to link automatically; a lender
name alone never is. "HDFC BANK LTD" and "HDFC Bank Credit Card" reduce to the
same key while being different accounts, and silently merging them would put
one card's debt on another card's row - a wrong number produced confidently,
which is the one failure this project does not tolerate. Everything weaker than
a suffix match is offered as a suggestion for a human to confirm.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Sequence

from ..ingestion.bureau import lender_key, number_suffix

#: Bureau type -> ledger types it may legitimately match. A bureau credit card
#: can only ever be a ledger credit card; the loan kinds are interchangeable
#: because bureaus and banks disagree about what counts as "personal".
_COMPATIBLE: dict[str, set[str]] = {
    "credit_card": {"credit_card"},
    "home_loan": {"home_loan", "personal_loan"},
    "auto_loan": {"auto_loan", "personal_loan"},
    "personal_loan": {"personal_loan", "home_loan", "auto_loan"},
    "savings": {"savings", "current"},
    "unknown": set(),
}

#: Ledger account types no bureau reports on. Their absence from a report is
#: expected and must never be surfaced as a gap.
NOT_REPORTED_BY_BUREAUS = {"savings", "current", "wallet", "investment", "unknown"}

AUTO_LINK_CONFIDENCE = 0.9
SUGGEST_CONFIDENCE = 0.5


@dataclass
class Match:
    bureau_account_id: str
    account_id: str | None
    status: str            # auto | suggested | unmatched
    confidence: float
    reason: str


def _types_compatible(bureau_type: str, ledger_type: str) -> bool:
    allowed = _COMPATIBLE.get(bureau_type)
    if allowed is None:
        return False
    if not allowed:
        # An unknown bureau type constrains nothing, so the suffix has to carry
        # the whole match on its own.
        return True
    return ledger_type in allowed


def score_pair(bureau: Any, account: Any) -> tuple[float, str]:
    """How strongly one bureau account matches one ledger account.

    Returns (confidence, reason). The reason is carried through to the UI: a
    link a user is asked to confirm has to say what it was based on, or the
    only honest answer to "is this right?" is a shrug.
    """
    b_suffix = getattr(bureau, "number_suffix", "") or ""
    a_suffix = number_suffix(getattr(account, "account_number_masked", "") or "")
    b_type = getattr(bureau, "account_type", "unknown")
    a_type = getattr(account, "account_type", "unknown")
    a_type = getattr(a_type, "value", a_type)

    b_lender = getattr(bureau, "lender_key", "") or lender_key(
        getattr(bureau, "lender", ""))
    a_lender = lender_key(getattr(account, "institution", ""))

    same_lender = bool(b_lender) and b_lender == a_lender
    same_suffix = bool(b_suffix) and b_suffix == a_suffix
    compatible = _types_compatible(b_type, a_type)

    if not compatible:
        return 0.0, ""

    if same_suffix and same_lender:
        return 0.97, f"last 4 digits ({b_suffix}) and lender both match"
    if same_suffix:
        return 0.9, f"last 4 digits ({b_suffix}) match, lender name differs"
    if same_lender and b_type == a_type and b_type != "unknown":
        # No digits to go on. Real often enough to offer, never enough to
        # apply: a person with two HDFC cards would get them silently swapped.
        return 0.55, "same lender and account type, but no digits to compare"
    if same_lender:
        return 0.35, "same lender only"
    return 0.0, ""


def match_accounts(bureau_accounts: Sequence[Any],
                   ledger_accounts: Sequence[Any]) -> list[Match]:
    """Best available match for each bureau account.

    Greedy, highest confidence first, one ledger account used at most once. A
    ledger account claimed by two bureau rows would double-count the same debt,
    which is exactly the error the reconciliation this feeds is meant to catch.
    """
    scored: list[tuple[float, str, Any, Any]] = []
    for bureau in bureau_accounts:
        for account in ledger_accounts:
            confidence, reason = score_pair(bureau, account)
            if confidence > 0:
                scored.append((confidence, reason, bureau, account))

    scored.sort(key=lambda item: item[0], reverse=True)

    taken_ledger: set[str] = set()
    decided: dict[str, Match] = {}

    for confidence, reason, bureau, account in scored:
        bureau_id = getattr(bureau, "id", None) or id(bureau)
        account_id = getattr(account, "id", None)
        if bureau_id in decided or account_id in taken_ledger:
            continue
        status = ("auto" if confidence >= AUTO_LINK_CONFIDENCE
                  else "suggested" if confidence >= SUGGEST_CONFIDENCE
                  else "unmatched")
        if status == "unmatched":
            continue
        taken_ledger.add(account_id)
        decided[bureau_id] = Match(str(bureau_id), account_id, status,
                                   round(confidence, 2), reason)

    for bureau in bureau_accounts:
        bureau_id = getattr(bureau, "id", None) or id(bureau)
        decided.setdefault(bureau_id, Match(
            str(bureau_id), None, "unmatched", 0.0,
            "no account with matching digits, lender or type"))

    return list(decided.values())


# --------------------------------------------------------------------------
# The reconciliation view
# --------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def reconcile(bureau_accounts: Iterable[Any], ledger_accounts: Iterable[Any],
              matches: Iterable[Match]) -> dict[str, Any]:
    """What the bureau and the ledger do and do not agree about.

    The output is three lists rather than one score, because the three cases
    mean completely different things and lumping them into a percentage would
    hide the only one that needs acting on.
    """
    bureau_by_id = {str(getattr(b, "id", None) or id(b)): b
                    for b in bureau_accounts}
    ledger_by_id = {a.id: a for a in ledger_accounts if getattr(a, "id", None)}
    match_by_bureau = {m.bureau_account_id: m for m in matches}

    linked, bureau_only, deltas = [], [], []
    matched_ledger_ids: set[str] = set()

    for bureau_id, bureau in bureau_by_id.items():
        match = match_by_bureau.get(bureau_id)
        entry = {
            "bureau_account_id": bureau_id,
            "lender": getattr(bureau, "lender", ""),
            "account_type": getattr(bureau, "account_type", "unknown"),
            "masked": getattr(bureau, "account_number_masked", ""),
            "status": getattr(bureau, "status", "open"),
            "balance": str(_decimal(getattr(bureau, "current_balance", None)) or ""),
            "overdue": str(_decimal(getattr(bureau, "overdue", None)) or ""),
            "worst_dpd": getattr(bureau, "worst_dpd", 0),
        }

        if match and match.account_id and match.status in {"auto", "confirmed"}:
            account = ledger_by_id.get(match.account_id)
            matched_ledger_ids.add(match.account_id)
            entry["account_id"] = match.account_id
            entry["confidence"] = match.confidence
            entry["reason"] = match.reason
            linked.append(entry)

            bureau_balance = _decimal(getattr(bureau, "current_balance", None))
            ledger_balance = _decimal(
                getattr(account, "principal_outstanding", None)) if account else None
            if bureau_balance is not None and ledger_balance is not None:
                gap = bureau_balance - ledger_balance
                if gap:
                    deltas.append({
                        **entry,
                        "ledger_balance": str(ledger_balance),
                        "bureau_balance": str(bureau_balance),
                        "difference": str(gap),
                    })
        else:
            entry["suggestion"] = match.account_id if match else None
            entry["confidence"] = match.confidence if match else 0.0
            entry["reason"] = match.reason if match else ""
            # A closed account nobody has statements for is not a blind spot,
            # it is history. Only what is still open counts as missing.
            entry["is_blind_spot"] = getattr(bureau, "status", "open") == "open"
            bureau_only.append(entry)

    ledger_only = []
    for account_id, account in ledger_by_id.items():
        if account_id in matched_ledger_ids:
            continue
        account_type = getattr(account.account_type, "value",
                               account.account_type)
        ledger_only.append({
            "account_id": account_id,
            "label": account.display_name() if hasattr(account, "display_name")
                     else account_id,
            "account_type": account_type,
            # Bureaus report credit, not deposits. Saying a savings account is
            # "missing from your credit report" would be noise dressed as a
            # finding.
            "expected_in_report": account_type not in NOT_REPORTED_BY_BUREAUS,
        })

    blind_spots = [e for e in bureau_only if e.get("is_blind_spot")]
    return {
        "linked": linked,
        "bureau_only": bureau_only,
        "ledger_only": ledger_only,
        "balance_deltas": deltas,
        "counts": {
            "linked": len(linked),
            "blind_spots": len(blind_spots),
            "unreported_here": len(bureau_only) - len(blind_spots),
            "ledger_only": len([e for e in ledger_only
                                if e["expected_in_report"]]),
            "balance_deltas": len(deltas),
        },
    }
