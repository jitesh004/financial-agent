"""Which months have a statement, per account - the coverage grid's backend.

A parsed statement knows its own period precisely. A failed or locked file
does not - normalize() never ran on it - so the only way to place it on the
grid at all is to guess its month from the filename (or, failing that, the
email's own date), which is what `guess_period_hint` is for. Getting this
guess wrong only miscolors one box in a diagnostic UI; it never touches the
ledger, so a best-effort heuristic here is the right trade-off.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..rules import formats

#: `\b` treats "_" as a word character, so it does not mark a boundary right
#: after "Statement_OCT2025" or "...90_17-09-2025" - the underscore-heavy
#: filenames every issuer here actually uses. These lookarounds check for the
#: thing that actually matters (not extending a longer digit or letter run)
#: instead, so a separator can be a space, "_", "-", or nothing at all.
_NOT_BEFORE_DIGIT = r"(?!\d)"
_NOT_AFTER_DIGIT = r"(?<!\d)"
_NOT_AFTER_LETTER = r"(?<![A-Za-z])"

#: 2025MTH08, MTH08_2025, MTH-08-2025 - ICICI's own filename convention.
_MTH_PATTERN = re.compile(
    rf"{_NOT_AFTER_DIGIT}(\d{{4}})[\s_-]?MTH[\s_-]?(\d{{2}}){_NOT_BEFORE_DIGIT}"
    rf"|MTH[\s_-]?(\d{{2}})[\s_-]?(\d{{4}}){_NOT_BEFORE_DIGIT}",
    re.IGNORECASE,
)
#: The fourth copy of the month vocabulary, now the shared one - see
#: rules.formats. The stdlib's lists were correct but they are not the same
#: set: they carry no "sept", which real statements do print.
_MONTH_NAMES = formats.MONTHS
_MONTH_ABBR = formats.MONTHS
#: "OCT2025", "December 2025", "Dec_2025", "Dec-2025".
_MONTH_NAME_YEAR = re.compile(
    _NOT_AFTER_LETTER
    + r"(" + "|".join(sorted({*_MONTH_NAMES, *_MONTH_ABBR}, key=len, reverse=True)) + r")"
    + r"[\s_-]*(\d{4})" + _NOT_BEFORE_DIGIT,
    re.IGNORECASE,
)
#: An explicit day-month-year with separators: 17-12-2025, 17_09_2025, 17.09.2025.
_DMY = re.compile(
    rf"{_NOT_AFTER_DIGIT}(\d{{1,2}})[-_.](\d{{1,2}})[-_.](\d{{4}}){_NOT_BEFORE_DIGIT}"
)
#: A bare 8-digit date, most commonly YYYYMMDD (HSBC's own filenames).
_YYYYMMDD = re.compile(rf"{_NOT_AFTER_DIGIT}(\d{{4}})(\d{{2}})(\d{{2}}){_NOT_BEFORE_DIGIT}")

_CURRENT_YEAR = date.today().year
_YEAR_FLOOR = 2015  # No statement in scope predates this app's usefulness.


def _plausible(year: int, month: int) -> bool:
    return _YEAR_FLOOR <= year <= _CURRENT_YEAR + 1 and 1 <= month <= 12


def guess_period_hint(filename: str) -> str | None:
    """Best-effort "YYYY-MM" this filename's statement is probably for.

    Tried in order from most to least specific, since a looser pattern (a
    bare 8-digit run) can accidentally match inside an account or reference
    number - the specific patterns are checked first so they win when both
    could apply.
    """
    name = filename or ""

    m = _MTH_PATTERN.search(name)
    if m:
        year, month = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        year_i, month_i = int(year), int(month)
        if _plausible(year_i, month_i):
            return f"{year_i:04d}-{month_i:02d}"

    m = _MONTH_NAME_YEAR.search(name)
    if m:
        token, year = m.group(1).lower(), int(m.group(2))
        month_i = _MONTH_NAMES.get(token) or _MONTH_ABBR.get(token)
        if month_i and _plausible(year, month_i):
            return f"{year:04d}-{month_i:02d}"

    m = _DMY.search(name)
    if m:
        _, month, year = m.groups()
        month_i, year_i = int(month), int(year)
        if _plausible(year_i, month_i):
            return f"{year_i:04d}-{month_i:02d}"

    for m in _YYYYMMDD.finditer(name):
        year_i, month_i, day_i = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _plausible(year_i, month_i) and 1 <= day_i <= 31:
            return f"{year_i:04d}-{month_i:02d}"

    return None


def month_range(start: str, end: str) -> list[str]:
    """Every "YYYY-MM" from start to end, inclusive."""
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    months = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def statement_months(period_start: date | None, period_end: date | None) -> list[str]:
    """Every calendar month a statement's own declared period touches.

    Most statements are exactly one month; a quarterly or annual one
    legitimately covers several, and every one of those months should show
    green, not just the first.
    """
    if not period_start or not period_end:
        return []
    return month_range(f"{period_start.year:04d}-{period_start.month:02d}",
                       f"{period_end.year:04d}-{period_end.month:02d}")


def build_coverage(
    accounts: list[Any], statements_by_account: dict[str, list[Any]],
    files_by_account: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    """One row per account: every month from its earliest known statement to
    the current month, colored by what is known about that month.

    `statements_by_account`: account_id -> list of Statement objects (parsed,
    with a real period). `files_by_account`: account_id -> list of
    SourceFileRecord (every attempt, success or not).

    Green wins over orange wins over red for a given month: if any file for
    that account+month parsed successfully, it counts as covered even if an
    earlier attempt at the same month failed first.
    """
    today = date.today()
    current_month = f"{today.year:04d}-{today.month:02d}"
    rows = []

    for account in accounts:
        parsed_months: dict[str, Any] = {}  # month -> dict
        for stmt in statements_by_account.get(account.id, []):
            file_record = next((f for f in files_by_account.get(account.id, []) if f.statement_id == stmt.id), None)
            file_id = file_record.id if file_record else None
            for month in statement_months(stmt.period_start, stmt.period_end):
                parsed_months[month] = {"statement_id": stmt.id, "file_id": file_id}

        failed_months: dict[str, Any] = {}  # month -> file record
        earliest = None
        for record in files_by_account.get(account.id, []):
            month = record.period_hint
            if record.statement_id and record.statement_id in parsed_months.values():
                # Already counted via parsed_months, which is authoritative
                # for the exact months a multi-month statement covers.
                if month:
                    earliest = month if earliest is None else min(earliest, month)
                continue
            if month and record.parse_status in {"failed", "needs_password", "unreconciled"}:
                failed_months[month] = record
            if month:
                earliest = month if earliest is None else min(earliest, month)

        if parsed_months:
            earliest = min(parsed_months) if earliest is None else min(earliest, min(parsed_months))
        if earliest is None:
            # Nothing at all is known about when this account starts - show
            # only the current month rather than an arbitrarily long, entirely
            # red history that was never actually in scope.
            earliest = current_month

        months = []
        for month in month_range(earliest, max(earliest, current_month)):
            if month in parsed_months:
                months.append({"month": month, "status": "parsed",
                               "statement_id": parsed_months[month]["statement_id"], "file_id": parsed_months[month]["file_id"]})
            elif month in failed_months:
                rec = failed_months[month]
                months.append({"month": month, "status": "failed",
                               "statement_id": None, "file_id": rec.id})
            else:
                months.append({"month": month, "status": "missing",
                               "statement_id": None, "file_id": None})

        rows.append({
            "account_id": account.id,
            "display_name": account.display_name(),
            "institution": account.institution,
            "account_type": account.account_type.value,
            "months": months,
        })

    return rows
