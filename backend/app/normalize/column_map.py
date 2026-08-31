"""Map arbitrary statement headers onto canonical transaction fields.

Every institution names its columns differently ("Withdrawal Amt.", "Debit",
"Dr", "Paid Out"). Rather than maintain a per-bank template, we score each
header cell against alias sets and pick the best assignment.

When a table has no usable header at all - common in PDF extractions where the
header row is on a previous page - `infer_roles_from_data` classifies columns by
what the cells actually contain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .parsers import parse_amount, parse_date

#: Canonical role -> alias fragments (matched as substrings, lowercased).
#: Order within a list is irrelevant; specificity is handled by scoring.
COLUMN_ALIASES: dict[str, list[str]] = {
    "txn_date": [
        "transaction date", "txn date", "date of transaction", "tran date",
        "posting date", "post date", "date", "dt",
    ],
    "value_date": ["value date", "value dt", "val date", "effective date"],
    "description": [
        "description", "narration", "particulars", "transaction details",
        "details", "remarks", "transaction remarks", "narrative", "merchant",
        "transaction description", "payee", "reference", "transaction",
    ],
    "debit": [
        "withdrawal", "withdrawl", "debit", "dr", "paid out", "money out",
        "withdrawal amt", "debit amount", "dr amount", "spend", "purchase",
        "outflow", "amount debited",
    ],
    "credit": [
        "deposit", "credit", "cr", "paid in", "money in", "deposit amt",
        "credit amount", "cr amount", "inflow", "amount credited", "receipt",
    ],
    "amount": ["amount", "amt", "transaction amount", "value", "txn amount"],
    "balance": [
        "balance", "closing balance", "running balance", "available balance",
        "bal", "balance amt", "outstanding",
    ],
    "reference": [
        "ref no", "reference no", "cheque no", "chq no", "utr", "ref",
        "instrument id", "transaction id", "txn id", "chq/ref no",
    ],
    "type": ["type", "dr/cr", "cr/dr", "transaction type", "txn type", "indicator"],
}

#: Roles that must be present for a table to be usable as a transaction table.
#:
#: Description is deliberately NOT required. Real statements do produce rows
#: with no narration at all - IDFC card statements render a payment as
#: "30 Sep 25   190.96 CR" and nothing else. A date plus an amount is a
#: transaction; demanding a description discarded those entire statements.
#: The money requirement is enforced separately in `is_usable`.
REQUIRED_ROLES = {"txn_date"}


def _clean_header(text: str) -> str:
    text = str(text or "").strip().lower()
    text = re.sub(r"[^\w\s/]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _score_alias(header: str, alias: str) -> float:
    """Score one header cell against one alias.

    Exact match beats prefix beats substring. Longer aliases score higher so
    "withdrawal amt" wins over the bare "amt" for the same cell.
    """
    if not header or not alias:
        return 0.0
    if header == alias:
        return 100.0 + len(alias)
    # Token-boundary match: "debit" matches "debit amount" but not "debited by"
    if re.search(rf"\b{re.escape(alias)}\b", header):
        return 50.0 + len(alias)
    if alias in header:
        return 20.0 + len(alias)
    return 0.0


@dataclass
class ColumnMapping:
    """Which column index plays which role."""

    roles: dict[str, int] = field(default_factory=dict)
    confidence: float = 0.0
    #: True when debit and credit live in separate columns (the common case).
    split_amount_columns: bool = False
    inferred_from_data: bool = False

    def get(self, role: str) -> int | None:
        return self.roles.get(role)

    def is_usable(self) -> bool:
        has_required = REQUIRED_ROLES.issubset(self.roles.keys())
        has_money = bool(
            {"debit", "credit", "amount"} & self.roles.keys()
        )
        return has_required and has_money


def map_columns(header: list[str]) -> ColumnMapping:
    """Assign roles to header cells by best-score matching.

    Each role claims at most one column and each column serves at most one role.
    Resolved greedily by descending score, which handles the awkward case where
    "Debit Amount" and "Amount" both appear.
    """
    cleaned = [_clean_header(h) for h in header]

    candidates: list[tuple[float, str, int]] = []
    for role, aliases in COLUMN_ALIASES.items():
        for idx, head in enumerate(cleaned):
            if not head:
                continue
            best = max((_score_alias(head, a) for a in aliases), default=0.0)
            if best > 0:
                candidates.append((best, role, idx))

    candidates.sort(key=lambda c: -c[0])

    roles: dict[str, int] = {}
    taken_columns: set[int] = set()
    for score, role, idx in candidates:
        if role in roles or idx in taken_columns:
            continue
        roles[role] = idx
        taken_columns.add(idx)

    # A lone "amount" column alongside debit/credit columns is redundant and
    # usually a total - drop it so the normalizer doesn't double count.
    if "debit" in roles and "credit" in roles and "amount" in roles:
        roles.pop("amount")

    split = "debit" in roles and "credit" in roles
    matched = len(roles)
    confidence = min(1.0, matched / 5.0) if matched else 0.0

    return ColumnMapping(roles=roles, confidence=confidence, split_amount_columns=split)


def _looks_like_transaction_row(row: list[str], default_year: int | None = None) -> bool:
    """A crude but effective filter against page-footer clutter.

    Disclaimer text, payment instructions and bank contact details can land
    in the same extracted table as the real transaction rows - a full page
    of wrapped sentence fragments, one per "row", each column holding a
    slice of two unrelated paragraphs side by side. Left in the sample, that
    clutter dilutes every column's date/amount hit rate well below the
    confidence threshold before a single real column is ever recognised.

    A genuine transaction row - whatever the bank, whether it ends in the
    transaction amount or a running balance - has some number as its last
    populated cell. A wrapped sentence fragment essentially never does.

    That alone is not enough: a statement's own letterhead can carry a
    Credit Summary or interest-rate block whose rows ALSO end in a genuine
    number ("Available Credit Limit Rs 6,48,912.00", "Monthly Rate 3.50%")
    without being a transaction. Those blocks don't happen to also carry a
    parseable date anywhere in the row, whereas an actual transaction always
    does - so requiring both is what tells the two apart.
    """
    cells = [str(c or "").strip() for c in row]
    last_filled = next((c for c in reversed(cells) if c), None)
    if last_filled is None:
        return False
    # A trailing "%" is a rate, not an amount - "...Tax (IGST) @18%." parses
    # its digits as 18.0 same as a real amount would, and a disclaimer
    # paragraph quoting a GST or interest rate is common enough that this
    # would otherwise be the single most frequent false positive.
    if "%" in last_filled:
        return False
    if parse_amount(last_filled).value is None:
        return False
    return any(parse_date(c, default_year=default_year) is not None for c in cells if c)


def infer_roles_from_data(rows: list[list[str]], sample_size: int = 40,
                          default_year: int | None = None) -> ColumnMapping:
    """Classify columns by cell content when there is no usable header.

    Heuristics, in order of reliability:
      - a column that parses as a date in most rows is the transaction date
      - a column whose values are mostly non-numeric prose is the description
      - numeric columns are money; the one that changes monotonically and is
        largest in magnitude is the running balance
      - remaining numeric columns become debit/credit by fill pattern (they are
        mutually exclusive per row in a split layout)
    """
    if not rows:
        return ColumnMapping()

    width = max(len(r) for r in rows)
    # Fall back to the unfiltered rows if literally nothing survives - some
    # layout this heuristic does not fit is a better bet than an empty
    # mapping for a table that may still be perfectly usable.
    candidate_rows = [r for r in rows
                      if _looks_like_transaction_row(r, default_year=default_year)] or rows
    sample = candidate_rows[:sample_size]

    date_hits = [0] * width
    numeric_hits = [0] * width
    text_hits = [0] * width
    filled = [0] * width

    for row in sample:
        for c in range(width):
            cell = row[c] if c < len(row) else ""
            cell = str(cell or "").strip()
            if not cell:
                continue
            filled[c] += 1
            if parse_date(cell, default_year=default_year) is not None:
                date_hits[c] += 1
            elif parse_amount(cell).value is not None:
                numeric_hits[c] += 1
            elif len(cell) > 3:
                text_hits[c] += 1

    roles: dict[str, int] = {}
    n = max(1, len(sample))

    # The 60% threshold assumes a sample large enough for a stray false
    # positive to average out, but page clutter that slips past
    # _looks_like_transaction_row - a subtotal line, a credit-limit
    # summary, the statement-period year quoted on its own - is common
    # enough to drag even the obviously correct column under it with no
    # real competing candidate. Two narrower cases are trusted instead of
    # the strict percentage, each independent of how far under 60% the
    # winning column's own share sits:
    #   - a tiny sample: MIN_PARSEABLE_ROWS elsewhere already treats one
    #     genuine transaction as a legitimate statement, so a sample this
    #     small should not need more evidence than the rest of the
    #     pipeline does.
    #   - an unambiguous lead: no other column comes remotely close, so
    #     there is nothing for the winner to have been confused with.
    best_date_hits = max(date_hits, default=0)
    second_best_date_hits = sorted(date_hits, reverse=True)[1] if len(date_hits) > 1 else 0
    is_tiny_sample = n <= 5 and best_date_hits >= 1
    is_clear_winner = best_date_hits >= max(3, second_best_date_hits * 2)
    if best_date_hits >= n * 0.6 or is_tiny_sample or is_clear_winner:
        date_cols = [c for c in range(width) if date_hits[c] == best_date_hits]
    else:
        date_cols = []
    if date_cols:
        roles["txn_date"] = date_cols[0]
        if len(date_cols) > 1:
            roles["value_date"] = date_cols[1]

    text_cols = [
        c for c in range(width)
        if c not in roles.values() and text_hits[c] >= filled[c] * 0.5 and filled[c] > 0
    ]
    if text_cols:
        # The widest prose column is the narration.
        roles["description"] = max(
            text_cols,
            key=lambda c: sum(len(str(r[c])) for r in sample if c < len(r)),
        )

    money_cols = [
        c for c in range(width)
        if c not in roles.values() and numeric_hits[c] >= filled[c] * 0.7 and filled[c] > 0
    ]

    # Only claim a balance column when at least one money column would be left
    # over for the amount.
    #
    # Text-recovered rows are typically [date, description, amount] - a single
    # money column. Treating it as the running balance leaves no amount at all,
    # `is_usable()` fails, and the whole statement is discarded despite every
    # transaction having been extracted correctly. That single rule accounted
    # for 44 of 127 unparsed real statements.
    if len(money_cols) >= 2:
        # Balance column is dense (present on nearly every row) and typically last.
        dense = [c for c in money_cols if filled[c] >= n * 0.9]
        if dense:
            balance_col = max(dense, key=lambda c: c)
            roles["balance"] = balance_col
            money_cols = [c for c in money_cols if c != balance_col]

    if len(money_cols) >= 2:
        roles["debit"], roles["credit"] = money_cols[0], money_cols[1]
    elif len(money_cols) == 1:
        roles["amount"] = money_cols[0]

    split = "debit" in roles and "credit" in roles
    confidence = min(0.75, len(roles) / 5.0)  # capped: inference is never certain
    return ColumnMapping(
        roles=roles,
        confidence=confidence,
        split_amount_columns=split,
        inferred_from_data=True,
    )


def looks_like_header(row: list[str], default_year: int | None = None) -> bool:
    """True when a row reads as column titles rather than data.

    Used to find the header inside a PDF table where the first N rows are the
    bank's letterhead.
    """
    if not row:
        return False
    cells = [_clean_header(c) for c in row if str(c or "").strip()]
    if len(cells) < 2:
        return False

    # A header row contains no parseable dates and no parseable amounts.
    for cell in cells:
        if parse_date(cell, default_year=default_year) is not None:
            return False

    alias_hits = 0
    for cell in cells:
        for aliases in COLUMN_ALIASES.values():
            if any(_score_alias(cell, a) >= 50.0 for a in aliases):
                alias_hits += 1
                break

    return alias_hits >= 2


def find_header_row(rows: list[list[str]], max_scan: int = 15,
                    default_year: int | None = None) -> int | None:
    """Locate the header row index within the first `max_scan` rows."""
    best_idx: int | None = None
    best_score = 0
    for i, row in enumerate(rows[:max_scan]):
        if not looks_like_header(row, default_year=default_year):
            continue
        mapping = map_columns(row)
        score = len(mapping.roles)
        if score > best_score:
            best_score, best_idx = score, i
    return best_idx
