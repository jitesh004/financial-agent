"""Primitive value parsers: dates, amounts, descriptions.

These are the lowest-level pieces of the pipeline and the most heavily tested,
because a single misread digit propagates into every downstream number.

Two deliberate choices:

1. Ambiguous dates (01/02/2026) default to DAY-FIRST. Every institution this
   tool targets uses DD/MM/YYYY. A statement-wide disambiguation pass
   (`infer_date_order`) overrides the default when the evidence is unambiguous.
2. Amounts are parsed to Decimal via string manipulation only. We never route
   through float, so 0.1 + 0.2 problems cannot enter the ledger.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from ..rules import formats

# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

#: Shared with every other reader - see rules.formats.
_MONTHS = formats.MONTHS

#: (regex, handler-key). Ordered most-specific first.
_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 2026-01-15 / 2026/01/15  -> unambiguous ISO
    (re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$"), "ymd"),
    # 15-Jan-2026 / 15 Jan 26 / 15.January.2026
    (re.compile(r"^(\d{1,2})[-/.\s,]+([A-Za-z]{3,9})[-/.\s,]+(\d{2,4})$"), "dmonthy"),
    # Jan-15-2026 / January 15, 2026
    (re.compile(r"^([A-Za-z]{3,9})[-/.\s,]+(\d{1,2})[,]?[-/.\s,]+(\d{2,4})$"), "monthdy"),
    # 15/01/2026 or 01/15/2026 -> ambiguous, resolved by day_first
    (re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$"), "ambiguous"),
    # 15Jan2026 (no separators, seen in some fixed-width PDF exports)
    (re.compile(r"^(\d{1,2})([A-Za-z]{3})(\d{2,4})$"), "dmonthy"),
    # 20260115
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), "ymd"),
    # June 18 - no year at all. American Express prints transaction dates
    # this way, relying on the statement period (printed once, elsewhere) to
    # supply the year rather than repeating it on every row. Least specific
    # on purpose, tried last, and only ever resolves with a `default_year`
    # supplied by the caller - see parse_date.
    (re.compile(r"^([A-Za-z]{3,9})[-/.\s,]+(\d{1,2})$"), "monthd_noyear"),
    # 18-Jun - the same omission the other way round, which is how bank alert
    # emails write it ("debited from A/c XX1234 on 15-Aug"). This lived in
    # txn_email as a private strptime format list; the two shapes belong
    # together, and neither resolves without a `default_year`.
    (re.compile(r"^(\d{1,2})[-/.\s,]+([A-Za-z]{3,9})$"), "dmonth_noyear"),
]

_DATE_NOISE = re.compile(r"\s+\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM)?$", re.IGNORECASE)


def _expand_year(y: int) -> int:
    """Two-digit years: 70-99 -> 19xx, 00-69 -> 20xx."""
    if y >= 100:
        return y
    return 1900 + y if y >= 70 else 2000 + y


def _year_for(month: int, day: int, default_year: int,
              period: tuple[date | None, date | None] | None) -> int:
    """Which year a yearless date belongs to.

    `default_year` unless the statement spans a year boundary, in which case
    the year that puts the date inside the period wins. A statement covering
    April 2025 to March 2026 prints "15 May" for a row in 2025; taking the
    period's end year would date it May 2026, two months after the statement
    closed, and the row would then be dropped as out of period.
    """
    if not period:
        return default_year
    start, end = period
    if not start or not end or start.year == end.year:
        return default_year
    for candidate in (start.year, end.year):
        try:
            if start <= date(candidate, month, day) <= end:
                return candidate
        except ValueError:
            continue  # 29 Feb in a non-leap candidate
    return default_year


def parse_date(value: str, day_first: bool = True,
               default_year: int | None = None,
               period: tuple[date | None, date | None] | None = None) -> date | None:
    """Parse a statement date cell. Returns None rather than raising.

    A None here is a signal to the caller that the row is probably not a
    transaction row at all (a header, a footer, a page break).

    `default_year` resolves a bare "June 18" - American Express prints
    transaction dates with no year at all, trusting the statement period
    (printed once, elsewhere) to supply it. Without a year to fall back on,
    that pattern never matches, and it is deliberately left unresolved
    rather than guessed at: a caller with no year to offer should keep
    treating the row as unparseable, the same as it always has.

    `period` fixes the one case a single `default_year` cannot: a statement
    that spans a year boundary. A quarterly statement covering Nov to Jan, or
    an annual one covering April to March, prints "15 May" for a row that
    belongs to the EARLIER year - and resolving it to the period's end year
    puts it after the statement closed, where `_drop_rows_after_period` then
    discards it. Given the period, the year that lands the date inside it wins.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Drop trailing timestamps: "15/01/2026 14:30:00"
    text = _DATE_NOISE.sub("", text).strip()
    # Real statements split a "DATE & TIME" column mid-cell, leaving "25/08/2025|".
    # Strip surrounding punctuation before matching rather than widening every
    # pattern with optional trailing junk.
    text = text.strip("|,;:()[] 	")
    text = text.replace(" ", " ")

    for pattern, kind in _DATE_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "dmonthy":
                d = int(m.group(1))
                mo = _MONTHS.get(m.group(2).lower(), 0)
                y = _expand_year(int(m.group(3)))
                if not mo:
                    continue
            elif kind == "monthdy":
                mo = _MONTHS.get(m.group(1).lower(), 0)
                d = int(m.group(2))
                y = _expand_year(int(m.group(3)))
                if not mo:
                    continue
            elif kind == "monthd_noyear":
                if default_year is None:
                    continue
                mo = _MONTHS.get(m.group(1).lower(), 0)
                d = int(m.group(2))
                if not mo:
                    continue
                y = _year_for(mo, d, default_year, period)
            elif kind == "dmonth_noyear":
                if default_year is None:
                    continue
                d = int(m.group(1))
                mo = _MONTHS.get(m.group(2).lower(), 0)
                if not mo:
                    continue
                y = _year_for(mo, d, default_year, period)
            else:  # ambiguous
                a, b = int(m.group(1)), int(m.group(2))
                y = _expand_year(int(m.group(3)))
                if a > 12:          # must be a day
                    d, mo = a, b
                elif b > 12:        # second field must be a day
                    d, mo = b, a
                else:
                    d, mo = (a, b) if day_first else (b, a)
            return date(y, mo, d)
        except (ValueError, TypeError):
            continue
    return None


#: A date-shaped run, for finding one inside prose rather than parsing a cell
#: that is entirely a date. Deliberately permissive - whatever it finds is
#: handed to `parse_date`, which is the thing that decides if it is real.
_DATE_IN_TEXT = re.compile(
    rf"\b(\d{{1,2}}[-/. ]{{1,2}}(?:{formats.MONTH_ALTERNATION})[-/. ]{{1,2}}\d{{2,4}}"
    rf"|(?:{formats.MONTH_ALTERNATION})[-/. ,]{{1,2}}\d{{1,2}},?\s*\d{{2,4}}"
    rf"|\d{{4}}-\d{{1,2}}-\d{{1,2}}"
    rf"|\d{{1,2}}[-/.]\d{{1,2}}[-/.]\d{{2,4}}"
    rf"|\d{{1,2}}[-/. ](?:{formats.MONTH_ALTERNATION}))\b",
    re.IGNORECASE,
)


def find_date(text: str, day_first: bool = True,
              default_year: int | None = None) -> date | None:
    """The first date inside a longer string, or None.

    `parse_date` is anchored: it answers "is this cell a date". Bureau reports
    and alert emails ask the other question - "there is a date somewhere in
    this sentence" - and each had grown its own smaller pattern set to answer
    it. The bureau one knew five shapes and returned None for "Aug 29, 2026",
    "15.01.2026" and "15-01-26", all of which appear on real reports.
    """
    match = _DATE_IN_TEXT.search(text or "")
    if not match:
        return None
    return parse_date(match.group(1), day_first=day_first,
                      default_year=default_year)


def infer_date_order(values: list[str]) -> bool:
    """Decide day-first vs month-first for a whole statement.

    Looks for a single unambiguous cell (a field > 12 in one position). One
    proven example settles it for every row, which is far safer than guessing
    per-row and ending up with a mixed-convention ledger.

    Returns True for day-first (the default when there is no evidence).
    """
    ambiguous = _DATE_PATTERNS[3][0]
    day_first_votes = 0
    month_first_votes = 0

    for raw in values:
        if not raw:
            continue
        m = ambiguous.match(_DATE_NOISE.sub("", str(raw).strip()))
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b <= 12:
            day_first_votes += 1
        elif b > 12 and a <= 12:
            month_first_votes += 1

    if month_first_votes > day_first_votes:
        return False
    return True


# --------------------------------------------------------------------------
# Amounts
# --------------------------------------------------------------------------

_CURRENCY_CHARS = "₹$€£¥"
# 1,23,456.78 (Indian grouping) and 123,456.78 (Western) both reduce to digits.
_AMOUNT_CLEAN = re.compile(r"[^\d.,\-()]")
#: Currency markers, removed before deciding whether a cell is numeric at all.
_CURRENCY_TOKENS = re.compile(
    # A lone C or backtick immediately before digits is a mis-rendered rupee
    # glyph - several issuers' PDFs produce "C 759.23" and "`0.00" where the
    # font maps the rupee sign to another codepoint. Anchored on a non-letter
    # boundary so it can never eat the C of a real word like CREDIT.
    r"[₹$€£¥`]"
    # C (HDFC/ICICI) and r (IDFC First) are the two substitutions seen in real
    # statements where the font maps the rupee sign to another codepoint.
    r"|(?<![A-Za-z])[Ccr](?=\s*[\d,])"
    r"|\b(?:INR|RS|USD|EUR|GBP)\b\.?",
    re.IGNORECASE,
)
#: Any surviving letter means the cell is a description or reference, not money.
_HAS_LETTER = re.compile(r"[A-Za-z]")
#: A cell holding two separators from -./ between digit runs is a date, not
#: money. Bank of Baroda prints the serial and the date in one cell ("57
#: 10-12-2025"); stripping the separators turned that into 5,710,122,025 and
#: 12 such rows added 75 *billion* rupees of phantom spending.
_DATE_LIKE = re.compile(r"\d{1,4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{2,4}")
#: An interior '-' between digits is a reference or a date range. Money only
#: ever carries a sign at one end.
_INTERIOR_DASH = re.compile(r"(?<=\d)-(?=\d)")
#: Space-grouped thousands, the one legitimate reason a money cell contains a
#: space: "1 234 567,89". Anything else with a space is two separate fields.
_SPACE_GROUPED = re.compile(r"^\d{1,3}(?:\s\d{3})+(?:[.,]\d{1,2})?$")
_TRAILING_SIGN = re.compile(r"^(.*?)\s*(Cr|Dr|CR|DR)\.?$")


class AmountParse:
    """Result of parsing an amount cell.

    `explicit_direction` is set when the cell itself declared Cr/Dr or used
    parentheses/minus for negative. Column position is a weaker signal than the
    cell's own annotation, so the normalizer prefers this when present.
    """

    __slots__ = ("value", "explicit_direction")

    def __init__(self, value: Decimal | None, explicit_direction: str | None = None):
        self.value = value
        self.explicit_direction = explicit_direction

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AmountParse({self.value!r}, {self.explicit_direction!r})"


def parse_amount(value: str) -> AmountParse:
    """Parse a money cell into a positive Decimal plus an optional direction.

    Handles: '₹1,23,456.78', '1234.56 Cr', '(500.00)', '-500', '1.234,56'
    (European), '12,34,567', '' and '-'.
    """
    if value is None:
        return AmountParse(None)

    text = str(value).strip()
    if not text or text in {"-", "--", "N/A", "NA", "nil", "NIL", ".", "0.00-"}:
        # A bare '-' is how most statements render "nothing in this column".
        return AmountParse(None)

    direction: str | None = None

    # Cr / Dr suffix, e.g. "5,000.00 Cr"
    m = _TRAILING_SIGN.match(text)
    if m:
        text = m.group(1).strip()
        direction = "credit" if m.group(2).lower() == "cr" else "debit"

    # Leading Cr/Dr, e.g. "Dr 5,000.00"
    lead = re.match(r"^(Cr|Dr|CR|DR)\.?\s+(.*)$", text)
    if lead:
        direction = "credit" if lead.group(1).lower() == "cr" else "debit"
        text = lead.group(2).strip()

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    # Drop currency markers before the letter check below.
    text = _CURRENCY_TOKENS.sub("", text).strip()

    # Reject anything that isn't shaped like a number.
    #
    # Without this, `_AMOUNT_CLEAN` simply deletes the letters and slashes from
    # a reference string like "BAN/557970195644/AXB22d731f1a034ea407fab" and
    # happily returns 557970195644223173110344074875515. Column inference then
    # classifies the DESCRIPTION column as money, finds no description, and
    # discards a statement whose rows were extracted perfectly. That one flaw
    # accounted for the largest single group of unparsed real statements.
    if _HAS_LETTER.search(text):
        return AmountParse(None, direction)
    if "/" in text or ":" in text:
        return AmountParse(None, direction)
    if _DATE_LIKE.search(text) or _INTERIOR_DASH.search(text):
        return AmountParse(None, direction)
    if re.search(r"\d\s+\d", text) and not _SPACE_GROUPED.match(text):
        # Two numbers in one cell - a serial next to a date, or two columns the
        # extractor failed to split. Either way it is not one amount.
        return AmountParse(None, direction)

    text = _AMOUNT_CLEAN.sub("", text)

    # A run of digits this long is an account or reference number, not money.
    if len(text.replace(",", "").replace(".", "").replace("-", "")) > 15:
        return AmountParse(None, direction)
    if text.startswith("-"):
        negative = True
    text = text.replace("-", "").replace("(", "").replace(")", "")

    if not text:
        return AmountParse(None, direction)

    text = _normalize_decimal_separators(text)

    try:
        amount = Decimal(text)
    except InvalidOperation:
        return AmountParse(None, direction)

    if negative and direction is None:
        direction = "debit"

    return AmountParse(abs(amount), direction)


def parse_signed_amount(value: str) -> Decimal | None:
    """Parse a cell that may legitimately be negative.

    Used for running-balance columns, where a minus sign is real information -
    an overdrawn account, or a cumulative investment outflow. `parse_amount`
    deliberately returns magnitude only (direction is a separate field), so
    using it for balances silently flips overdrafts into positive balances.
    """
    parsed = parse_amount(value)
    if parsed.value is None:
        return None
    if parsed.explicit_direction == "debit":
        return -parsed.value
    return parsed.value


def _normalize_decimal_separators(text: str) -> str:
    """Reduce mixed thousands/decimal conventions to a plain decimal string.

    The hard case is a lone separator: '1,234' is 1234 (thousands) but '1,23'
    is 1.23 in European notation. We use the group-length heuristic: exactly
    three digits after the separator means thousands.
    """
    has_comma = "," in text
    has_dot = "." in text

    if has_comma and has_dot:
        # Whichever appears last is the decimal separator.
        if text.rfind(",") > text.rfind("."):
            return text.replace(".", "").replace(",", ".")
        return text.replace(",", "")

    if has_comma:
        parts = text.split(",")
        # 1,234 / 12,34,567 -> thousands grouping
        if len(parts[-1]) == 3:
            return text.replace(",", "")
        # 1,23 -> European decimal
        return text.replace(",", ".")

    if has_dot:
        parts = text.split(".")
        if len(parts) > 2:
            # 1.234.567 -> European thousands
            return text.replace(".", "")
    return text


def parse_indian_shorthand(text: str) -> Decimal | None:
    """Parse '12.5 lakh', '1.2 Cr', '50K' from free text (loan summary blocks).

    Statements sometimes state a sanctioned amount only in words. This is used
    for metadata, never for transaction rows.
    """
    if not text:
        return None
    m = re.search(
        r"([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|cr|k|thousand|mn|million)\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    try:
        base = Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None

    unit = m.group(2).lower()
    multipliers = {
        "k": 1_000, "thousand": 1_000,
        "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
        "mn": 1_000_000, "million": 1_000_000,
        "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000,
    }
    return base * Decimal(multipliers.get(unit, 1))


# --------------------------------------------------------------------------
# Descriptions
# --------------------------------------------------------------------------

#: Payment-rail prefixes that carry no merchant information.
#: See rules.formats.PREFIX_RAILS - the names live there because three
#: modules strip or match on them and each used to spell its own list.
_RAIL_PREFIXES = formats.PREFIX_RAIL_PATTERN
_REF_NUMBERS = re.compile(r"\b\d{8,}\b")
_MULTISPACE = re.compile(r"\s+")
#: Payment aggregators that prefix the merchant they collected for. The "X6098Z"
#: form is HDFC's per-terminal code on UPI-routed card rows.
_AGGREGATOR_PREFIX = re.compile(
    r"^\s*(?:PTM|RAZ|EBZ|PPSL|INF|BBPS|PAYU|CCA|IPG|MSWIPE|PINELABS)\s*\*+\s*"
    r"|^\s*[A-Z]\d{4}[A-Z]\s+",
    re.IGNORECASE,
)
_CITIES = (
    r"NEWDELHI|NEW\s*DELHI|DELHI|GURUGRAM|GURGAON|GURGOAN|BANGALORE|BENGALURU|"
    r"NAVI\s*MUMBAI|MUMBAI|THANE|PIMPRI|CHINCHWAD|PUNE|NOIDA|GHAZIABAD|HYDERABAD|"
    r"CHENNAI|KOLKATA|JAIPUR|AHMEDABAD|SURAT|LUCKNOW|INDORE|NAGPUR|BHOSARI"
)
#: A city fused onto the end of a merchant token, which is how card statements
#: print it: "ZOMATOnewdelhi", "MILKBASKETBANGALORE", "DISTRICTDININGGURGOAN".
#: Case cannot separate these reliably - "ZOMATOGurugram" gives no clue whether
#: the G ends the brand or starts the city - but the city NAME does. The
#: lookbehind requires a letter immediately before, so a city standing as its
#: own word ("SAMARTH CLINIC PUNE") is left for _TRAILING_CITY below.
_GLUED_CITY = re.compile(rf"(?<=[A-Za-z])(?:{_CITIES})(?=\s|$)", re.IGNORECASE)
#: A city as its own trailing word, plus the state/currency codes card
#: statements append. Only at the very end, so "PUNE TYRES" keeps its name.
_TRAILING_CITY = re.compile(
    rf"(?:[\s,]*\b(?:{_CITIES}|MAHARASHTRA|MAH|HAR|UTT|KAR|IND|INR)\b[\s,.]*)+$",
    re.IGNORECASE,
)
#: Full account numbers appearing inside descriptions - redacted at ingestion.
#: Starts and ends on a digit so an adjacent space survives the substitution.
_ACCOUNT_IN_DESC = re.compile(r"\b\d(?:[ -]?\d){8,17}\b")


def redact_account_numbers(text: str) -> str:
    """Replace long digit runs that look like account/card numbers with a mask.

    Applied before any text can reach an LLM. Deliberately aggressive: a false
    positive costs a slightly less readable description, a false negative leaks
    an account number.
    """
    if not text:
        return text

    def _mask(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return f"XXXX{digits[-4:]}" if len(digits) >= 4 else "XXXX"

    return _ACCOUNT_IN_DESC.sub(_mask, text)


def normalize_description(raw: str) -> str:
    """Strip payment-rail noise so the same merchant produces the same string.

    'UPI/SWIGGY/928374652/Payment' and 'POS 4728 SWIGGY BANGALORE' should both
    reduce to something a merchant rule can match.
    """
    if not raw:
        return ""
    text = str(raw).strip()
    text = text.replace("\n", " ").replace(" ", " ")

    # Aggregators sit OUTSIDE the rail prefix ("X6098Z UPI-RUDRA ASSOCIATES"),
    # so they come off first or the rail underneath is never exposed. They
    # prefix the real merchant: "PTM*RELIANCE RETAIL", "EBZ*TRAYA".
    # Rails themselves can stack: "UPI/MMT/..." - strip repeatedly.
    for _ in range(3):
        stripped = _RAIL_PREFIXES.sub("", _AGGREGATOR_PREFIX.sub("", text))
        if stripped == text:
            break
        text = stripped

    # Card statements fuse the city onto the merchant - "ZOMATOnewdelhi" - which
    # hides the brand from every rule anchored on a word boundary.
    text = _GLUED_CITY.sub("", text)

    text = _REF_NUMBERS.sub(" ", text)
    text = re.sub(r"[/\-_|]+", " ", text)
    text = _MULTISPACE.sub(" ", text).strip()
    text = text.upper()
    text = _TRAILING_CITY.sub("", text).strip()
    return _MULTISPACE.sub(" ", text)


def extract_merchant(raw: str) -> str | None:
    """Best-effort merchant token from a normalized description.

    Returns the longest alphabetic segment, which for the vast majority of UPI
    and POS strings is the merchant name. Used as the merchant-cache key, so
    stability matters more than prettiness.
    """
    normalized = normalize_description(raw)
    if not normalized:
        return None

    segments = [s.strip() for s in normalized.split() if s.strip()]
    words = [s for s in segments if s.isalpha() and len(s) > 2]
    if not words:
        return None

    # Merchant names are usually the first 1-3 alphabetic words after the rail.
    return " ".join(words[:3])


def money(raw: object) -> Decimal | None:
    """A plain rupee figure out of a cell, or None.

    The same reader as `parse_amount`, without the direction - which is what
    a bureau balance, a NAV and an alert amount each want. None rather than
    zero for anything blank: see `formats.NO_FIGURE`.
    """
    return parse_amount("" if raw is None else str(raw)).value


def signed_money(raw: object) -> Decimal | None:
    """`money`, but a leading minus survives.

    Holdings keep their sign - a negative quantity is a real thing on a
    contract note - while a bank statement's "-500" is a debit of 500 and the
    sign is carried by the direction instead.
    """
    parsed = parse_amount("" if raw is None else str(raw))
    if parsed.value is None:
        return None
    negative = parsed.explicit_direction == "debit" and "-" in str(raw)
    return -parsed.value if negative else parsed.value
