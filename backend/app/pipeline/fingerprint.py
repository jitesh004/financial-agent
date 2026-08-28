"""Stable content identity for transactions and accounts.

Everything a human authors - a corrected category, a note, "this expense was
not mine" - has to survive re-parsing the statement it came from. It cannot be
keyed by `Transaction.id`, because that is a fresh uuid4 minted on every parse:
re-processing the same PDF produces the same rows with entirely different ids,
and anything hung off the old ids is orphaned.

So user data is keyed by what the row *is* rather than which object happened to
represent it: the account it belongs to, its date, its signed amount, and its
description. That tuple is stable across re-parses of the same statement.

Two deliberate weaknesses, both handled by the loose key below:

  - Account identity can genuinely change. It did twice while this app was
    being built, when card-variant detection improved and one merged account
    correctly split into three.
  - Description text can shift slightly between extractions of the same PDF,
    usually truncation.

`loose_key` drops the account and shortens the description precisely so a
decision can be recovered when the strict fingerprint moves underneath it.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # imported for typing only - avoids a circular import
    from ..models.schemas import Account, Transaction

#: How much of the description takes part in identity. The same row extracted
#: by two different code paths can disagree in its tail (one truncates, the
#: other carries the full bank reference), but the first 60 characters have
#: been stable across every such disagreement seen - which is also the length
#: `reconcile.transfers._is_same_row` settled on for the same reason.
_DESC_CHARS = 60


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").upper()).strip()


def desc_hash(description: str) -> str:
    """A short, stable hash of a description's identifying prefix."""
    return hashlib.sha256(
        _norm_text(description)[:_DESC_CHARS].encode("utf-8")
    ).hexdigest()[:16]


def norm_amount(amount: Decimal | str | None) -> str:
    """Amounts as a canonical 2dp string.

    `str(Decimal("100"))` and `str(Decimal("100.00"))` differ, and the same
    figure can arrive either way depending on which extractor read it. Left
    un-normalised that alone would break a fingerprint.
    """
    if amount is None:
        return "0.00"
    return f"{Decimal(str(amount)):.2f}"


def account_key(account: "Account | None") -> str:
    """Stable identity for an account - never its uuid.

    Mirrors the identity tuple `repository.upsert_account` and
    `graph.nodes._account_identity` resolve against, so a fingerprint keeps
    pointing at the same real-world account across a full reprocess.
    """
    if account is None:
        return ""
    account_type = getattr(account.account_type, "value", account.account_type)
    return "|".join((
        (account.institution or "").upper(),
        str(account_type or "").upper(),
        (account.account_number_masked or "").upper(),
        (account.product_name or "").upper(),
    ))


def transaction_fingerprint(txn: "Transaction", acct_key: str = "") -> str:
    """The strict key: account + date + amount + direction + description."""
    direction = getattr(txn.direction, "value", txn.direction)
    return hashlib.sha256("|".join((
        acct_key,
        txn.txn_date.isoformat() if txn.txn_date else "",
        norm_amount(txn.amount),
        str(direction or ""),
        desc_hash(txn.raw_description or txn.normalized_description or ""),
    )).encode("utf-8")).hexdigest()


def loose_key(txn: "Transaction") -> tuple[str, str, str, str]:
    """The recovery key: everything except the account.

    Used only when the strict fingerprint finds nothing. An account being
    re-identified is the one identity change that happens routinely and is
    nobody's mistake, so a decision must not be lost to it.
    """
    direction = getattr(txn.direction, "value", txn.direction)
    return (
        txn.txn_date.isoformat() if txn.txn_date else "",
        norm_amount(txn.amount),
        str(direction or ""),
        desc_hash(txn.raw_description or txn.normalized_description or ""),
    )


def stamp_fingerprints(transactions, accounts) -> int:
    """Fill in `fingerprint` on every transaction. Returns how many were set.

    `accounts` maps account id to Account, as the pipeline already holds it.
    """
    keys: dict[str, str] = {}
    stamped = 0
    for txn in transactions:
        aid = txn.account_id or ""
        if aid not in keys:
            keys[aid] = account_key(accounts.get(aid))
        txn.fingerprint = transaction_fingerprint(txn, keys[aid])
        stamped += 1
    return stamped
