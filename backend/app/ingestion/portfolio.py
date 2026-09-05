"""Read a holdings statement: CAS, CAMS/KFintech, or a broker's own.

Everything else in this app reconciles against a balance the bank printed. A
portfolio statement offers the same opportunity and it is the reason this
module bothers to compute anything: the document prints a total, and

    sum(units x NAV) == printed total

is a real check on whether the rows were read correctly. A statement whose
holdings do not add up to its own declared value has been misread, and saying
so is worth more than quietly reporting a wrong net worth.

Three layouts, one shape. A CAS from CDSL/NSDL lists demat holdings by ISIN; a
CAMS or KFintech statement lists mutual fund folios by scheme; a broker lists
positions by symbol. All three reduce to (instrument, units, price, value), and
everything below exists to get from one of those layouts to that tuple.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from ..normalize import parsers
from ..rules import institutions

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Layout detection
# --------------------------------------------------------------------------

#: (layout, provider, fragments) in the order they are tried - first match
#: wins, so the depository layout must be tested before the generic broker one.
#: Derived: each issuer's layout is recorded on its `rules.institutions`
#: record, and the document phrases that identify a layout without naming
#: anyone are in `institutions.LAYOUT_ORDER`.
LAYOUT_SIGNATURES: list[tuple[str, str, tuple[str, ...]]] = [
    (layout, provider, fragments)
    for layout, provider, fragments in institutions.portfolio_layouts()
]


def detect_layout(text: str, filename: str = "") -> tuple[str, str]:
    """(layout, provider) for this document, or ("unknown", "")."""
    haystack = f"{filename} {text[:6000]}".lower()
    for layout, provider, fragments in LAYOUT_SIGNATURES:
        if any(fragment in haystack for fragment in fragments):
            return layout, provider
    return "unknown", ""


#: Phrases that mark a securities document as a record of TRADES rather than
#: of holdings.
#:
#: Both are securities documents and both are full of ISINs, instrument names
#: and numbers, so every positive signal below fires on either. The difference
#: is what the numbers mean: a holdings statement's quantity is what you own,
#: a contract note's is what changed hands, and its "rate" may be a strike
#: price rather than a value.
#:
#: An Upstox "ANNUAL GLOBAL TRANSACTION STATEMENT ... Segment: Future &
#: Option" was read as holdings and produced one position - "NIFTY NIFTY NIFTY
#: NIFTY", 1,050 units at 22,500 - worth 2.36 CRORE, which was most of a
#: portfolio that should have totalled about 1.8 lakh. Every line on it in
#: fact closed at Net Quantity 0.00: nothing was held at all.
_TRADE_DOCUMENT_MARKERS = (
    "contract note",
    "global transaction statement",
    "transaction cum bill",
    "strike rate",
    "strike price",
    "future & option",
    "futures & options",
    "future and option",
    "futures and options",
)


def looks_like_trades(text: str) -> bool:
    """Is this a record of what was traded rather than of what is held?"""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _TRADE_DOCUMENT_MARKERS)


def looks_like_portfolio(text: str, filename: str = "") -> bool:
    """Whether this is a holdings document rather than a bank statement.

    An ISIN is the strongest single signal - nothing but a securities document
    contains one - so it alone is enough. Otherwise two softer markers must
    agree, which keeps a bank statement that merely mentions "mutual fund" out.
    """
    # Checked before every positive signal, because they all fire on a
    # contract note too - see the note above.
    if looks_like_trades(text):
        return False
    if ISIN.search(text):
        return True
    layout = detect_layout(text, filename)[0]
    # An EPF passbook is a holding - a retirement corpus - and carries none
    # of the markers below: no NAV, no units, no folio. It goes to the
    # statement reader otherwise, which read a 4.17 lakh corpus as a savings
    # account holding 31 rupees.
    #
    # Its own closing row is what identifies it here. The words that name
    # the scheme ("Employees Provident Fund", "Member ID", a UAN) are in the
    # cover matter, and classification reads the TABLES - see
    # `router.text_of` - where none of them survive. What does survive is a
    # closing balance followed by three money columns, one per pot, and no
    # bank statement closes three ways at once.
    if layout == "epf" or _EPF_CLOSING.search(text or ""):
        return True
    if layout == "unknown":
        return False
    lowered = text.lower()
    markers = ("nav", "units", "folio", "scheme name", "market value",
               "holdings", "portfolio value")
    return sum(1 for m in markers if m in lowered) >= 2


# --------------------------------------------------------------------------
# Field readers
# --------------------------------------------------------------------------

#: An ISIN is two country letters, nine alphanumerics and a check digit.
#:
#: The two negative lookaheads are load-bearing. A MASKED ACCOUNT NUMBER has
#: exactly this shape: "XXXXXXXX1951" is two letters, nine alphanumerics and a
#: digit, and one appears on every bank statement that masks its own account
#: number. Because an ISIN alone is treated as proof that a document is a
#: securities statement, twelve months of ICICI savings statements - eighty
#: transactions each - were routed to the holdings reader, which found no
#: holdings and filed them as empty portfolios. Those transactions were never
#: read at all.
#:
#: No real ISIN begins "XX" - not an assigned ISO 3166 country code - and none
#: contains a run of masking characters.
ISIN = re.compile(r"\b(?!XX)(?![A-Z0-9]*[X*]{4})([A-Z]{2}[A-Z0-9]{9}\d)\b")

_DATE_LINE = re.compile(
    r"(?:as\s+on|as\s+at|statement\s+date|valuation\s+date|holdings?\s+as\s+on)"
    r"\D{0,20}(\d{1,2}[-/\s][A-Za-z0-9]{2,9}[-/\s]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE)

_TOTAL_LINE = re.compile(
    r"(?:total|grand\s+total|portfolio)\s*(?:value|valuation)?"
    r"\D{0,30}((?:rs\.?|inr|₹)?\s*[\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)


#: Characters a PDF extraction leaves inside an identifier: a soft hyphen
#: where the text was wrapped, a zero-width space, a non-breaking space.
_INVISIBLE = re.compile(r"[­​‌‍﻿ \s]+")


def _clean_identifier(value: str) -> str:
    """Strip the invisible debris out of a folio, ISIN or symbol.

    These are keys, not prose: holdings are deduplicated by (account, ISIN,
    folio), so a folio that reads "5104091481/0" in one month and
    "510409148­ 1/0" in the next - the same number, wrapped across a line
    - is two different holdings as far as the database is concerned. One fund
    appeared twice in the portfolio at two different valuations, and every
    monthly statement added another copy.
    """
    return _INVISIBLE.sub("", value or "")


def to_decimal(raw: Any) -> Decimal | None:
    """A number out of a cell, or None.

    None rather than zero, throughout: a blank NAV column means the statement
    did not print one, and a zero there would silently value the holding at
    nothing and drag the whole portfolio total down with it.

    Signed, unlike the statement side: a negative quantity is a real thing on
    a securities document, where on a bank statement a leading minus means
    "debit" and the sign is carried by the direction instead.
    """
    return parsers.signed_money(raw)


def parse_as_of(text: str) -> date | None:
    """The date this snapshot was taken, from its own "as on" line.

    The label match stays here - it is what distinguishes the valuation date
    from the dozen other dates on a CAS - while reading the token it captured
    is the shared reader's job.
    """
    match = _DATE_LINE.search(text)
    return parsers.parse_date(match.group(1)) if match else None


def parse_declared_total(text: str) -> Decimal | None:
    """The portfolio value the document prints for itself.

    The LAST such figure wins. These statements total per section and then
    once at the end, and the final one is the grand total the holdings have to
    reproduce.
    """
    values = [to_decimal(m.group(1)) for m in _TOTAL_LINE.finditer(text)]
    values = [v for v in values if v is not None]
    return max(values) if values else None


#: Column header wording -> the field it holds, most specific first.
#:
#: Order is load-bearing. A CAS prints both "Cumulative Amount" (what was put
#: in) and "Valuation" (what it is worth now); with a bare "amount" hint ranked
#: above the invested ones, the money-in column was read as the holding's value
#: and every position was reported at its cost with no gain.
_COLUMN_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("isin",), "isin"),
    # "security" bare is what a CDSL demat table calls the instrument
    # column; the longer forms are what the fund houses use.
    (("schemename", "securityname", "scheme", "instrument", "stock",
      "companyname", "description", "securitydescription", "security"),
     "instrument"),
    (("symbol", "ticker", "tradingsymbol"), "symbol"),
    (("folio", "accountno", "clientid", "dpid"), "folio"),
    (("closingbalance", "closingunits", "unitbalance", "balanceunits",
      "closing", "units", "quantity", "qty", "freebalance", "holdings",
      "currentbal"), "units"),
    (("nav", "marketprice", "closingprice", "closingrate", "price", "rate"),
     "nav"),
    (("cumulativeamount", "amountinvested", "investedvalue", "invested",
      "costvalue", "totalcost", "investment", "purchasevalue"), "invested"),
    (("averagecost", "avgcost", "costprice", "purchaseprice", "buyaverage",
      "avgrate"), "avg_cost"),
    (("valuation", "marketvalue", "currentvalue", "closingvalue", "value",
      "amount"), "value"),
]

#: Header cells arrive with spaces wedged inside words - "V a lu a tio n",
#: "N A V" - because the PDF lays each glyph out separately and the extractor
#: preserves the gaps. Matching on the raw text finds none of them, so every
#: numeric column silently goes unmapped and the holding ends up with no units
#: and no NAV. The squeezed form catches the split words, the spaced one keeps
#: multi-word headers like "market value" working.
#:
#: A BILINGUAL document interleaves worse than that. CDSL prints every header
#: in English and Hindi, and the extractor hands back the two scripts woven
#: together a glyph at a time - the demat holdings column "Current Bal"
#: arrives as "Current वत मBाaनl शेष". Squeezing the spaces out does not help,
#: because the Devanagari sits INSIDE the English word; only dropping
#: everything that is not a latin letter or digit reads it back as
#: "currentbal". That third form is why the ascii one exists.
#:
#: Nine tenths of a CAS went missing on this. The demat section's columns
#: matched nothing, `rows_to_holdings` found no header row and returned
#: nothing, and the statement came back holding only its mutual funds -
#: 1.05 lakh of a 10.67 lakh portfolio - while still reporting itself parsed.
def _header_forms(cell: Any) -> tuple[str, str, str]:
    label = str(cell or "").strip().lower()
    return (re.sub(r"\s+", "", label), label,
            re.sub(r"[^a-z0-9]", "", label))


#: Fields that hold a number, and so can never be a column headed as a date.
#:
#: "Value Date" is the trap. An Upstox holdings table runs ISIN, Company,
#: Current Bal, Free Bal, VALUE DATE, Rate, VALUE - and "value date" contains
#: "value", so it claimed the value field, leaving the real one unmapped.
#: Every holding on those statements was then stored with no printed value at
#: all, which cost more than it looks: `computed_value` had nothing to fall
#: back on, and nothing could cross-check units x NAV against what the
#: statement actually printed for the row. That check is what would have
#: caught a misread quantity of 105105 shares.
_NUMERIC_FIELDS = frozenset({"units", "nav", "value", "invested", "avg_cost"})


def map_columns(header: Sequence[Any]) -> dict[int, str]:
    """Which column holds which field. Matched by header text, not position.

    Every one of these layouts reorders its columns between versions, and a
    reader that counts columns silently swaps NAV and value the first time one
    does - producing a portfolio worth units-times-value.
    """
    mapping: dict[int, str] = {}
    taken: set[str] = set()
    for index, cell in enumerate(header):
        forms = _header_forms(cell)
        if not forms[0]:
            continue
        is_date_column = any("date" in form for form in forms)
        for fragments, name in _COLUMN_HINTS:
            if is_date_column and name in _NUMERIC_FIELDS:
                continue
            if name in taken:
                continue
            if any(f in form for f in fragments for form in forms):
                mapping[index] = name
                taken.add(name)
                break
    return mapping


# --------------------------------------------------------------------------
# The parsed shape
# --------------------------------------------------------------------------

#: Row labels that are a total rather than a holding. Every one of these
#: statements carries subtotals per section and a grand total at the end, and a
#: reader that treats them as positions values the portfolio at roughly twice
#: what it is worth - while still passing any check that only looks at whether
#: the rows parsed.
_SUMMARY_ROW = re.compile(
    r"^\s*(?:grand\s+|sub\s*-?\s*)?(?:total|net|closing|opening|summary|"
    r"portfolio\s+value|valuation|balance)\b", re.IGNORECASE)

_FUND_WORDS = re.compile(
    r"\b(fund|scheme|plan|growth|dividend|idcw|liquid|elss|nifty|sensex)\b",
    re.IGNORECASE)
_ETF_WORDS = re.compile(r"\betf\b", re.IGNORECASE)
_BOND_WORDS = re.compile(r"\b(bond|debenture|ncd|sgb|g-?sec|treasury)\b",
                         re.IGNORECASE)


def classify_instrument(name: str, isin: str = "") -> str:
    """equity | mutual_fund | etf | bond | other, from the instrument's name.

    ISIN prefixes are checked first where they are unambiguous: an Indian
    mutual fund unit is issued under INF, an equity share under INE.
    """
    if isin.startswith("INF"):
        return "mutual_fund"
    if _ETF_WORDS.search(name or ""):
        return "etf"
    if _BOND_WORDS.search(name or ""):
        return "bond"
    if _FUND_WORDS.search(name or ""):
        return "mutual_fund"
    if isin.startswith("INE"):
        return "equity"
    return "other" if not name else "equity"


@dataclass
class Holding:
    instrument: str = ""
    isin: str = ""
    symbol: str = ""
    folio: str = ""
    kind: str = "equity"
    units: Decimal | None = None
    nav: Decimal | None = None
    value: Decimal | None = None
    avg_cost: Decimal | None = None
    invested: Decimal | None = None

    #: Set when units x NAV and the printed value cannot both be right.
    suspect: str = ""

    def computed_value(self) -> Decimal | None:
        """units x NAV, or the printed value when one of them is missing.

        Preferring the computed figure is the point: it is the one that can be
        checked against the declared total, and a printed value that disagrees
        with units x NAV means a column was misread.

        Which is exactly why a WIDE disagreement flips the preference. The
        computed figure is a product of two cells and a misalignment corrupts
        it without limit; the printed one is a single cell the issuer
        calculated. On a re-rendered Upstox page "Current Bal" and "Free Bal"
        merged, so 105 shares and 105 free read as 105105, and 105105 x 556.50
        put 5.85 CRORE next to a printed 58,432.50. Trusting the product there
        is trusting the corruption.

        Small gaps stay with the product - these statements round every line
        to two places, and that rounding is not a misread.
        """
        if self.units is None or self.nav is None:
            return self.value
        product = (self.units * self.nav).quantize(Decimal("0.01"))
        if self.value is None:
            return product
        gap = abs(product - self.value)
        if gap <= max(Decimal("1.00"), abs(self.value) / 100):
            return product
        self.suspect = (
            f"units x NAV is {product:,.2f} but the row is printed at "
            f"{self.value:,.2f}; a column was misread, so the printed figure "
            f"is used")
        return self.value


@dataclass
class PortfolioStatement:
    layout: str = "unknown"
    provider: str = ""
    #: Which account of this provider's, where the document names one. The
    #: provider alone is not an account: a subscriber holds an NPS account
    #: with each recordkeeper, and merging them loses one of the two whole.
    account_ref: str = ""
    as_of: date | None = None
    declared_value: Decimal | None = None
    holdings: list[Holding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def computed_value(self) -> Decimal:
        return sum((h.computed_value() or Decimal("0")) for h in self.holdings)

    def reconcile(self, tolerance: Decimal = Decimal("1.00")) -> tuple[str, Decimal | None, str]:
        """Do the holdings add up to the total the statement printed?

        The same gate the bank statements go through, applied to the one figure
        a portfolio document prints that can be checked against its own rows.
        Tolerance is a rupee: these statements round each line to two places
        and the rounding accumulates over a few dozen holdings.
        """
        if not self.holdings:
            return "not_applicable", None, "No holdings were read."
        if self.declared_value is None:
            return ("not_applicable", None,
                    "The statement prints no portfolio total to check against.")

        gap = self.computed_value - self.declared_value
        if abs(gap) <= tolerance:
            return ("passed", gap,
                    f"{len(self.holdings)} holdings add up to the declared "
                    f"{self.declared_value:,.2f}.")

        # A gap that equals one holding exactly says which row is wrong, which
        # is far more use than the size of the discrepancy.
        for holding in self.holdings:
            value = holding.computed_value()
            if value is not None and abs(abs(gap) - value) <= tolerance:
                return ("failed", gap,
                        f"Off by {gap:,.2f}, which is exactly "
                        f"{holding.instrument or holding.isin} - that row was "
                        f"likely double-counted or dropped.")
        return ("failed", gap,
                f"Holdings total {self.computed_value:,.2f} against a declared "
                f"{self.declared_value:,.2f} - a gap of {gap:,.2f}.")


# --------------------------------------------------------------------------
# Table reading
# --------------------------------------------------------------------------


#: How far into a table to look for the header row.
#:
#: Twelve, and deliberately shallow. It is tempting to widen it - "the first
#: qualifying row wins, so a deeper search can only find headers we are
#: currently missing" - and that argument is true of ONE table and false of a
#: document, because `parse_statement` concatenates what every table yields.
#: A table that finds no header contributes nothing; the same table searched
#: deeper contributes rows ON TOP of the correct ones.
#:
#: Widening it to forty cost 6.5 crore on a single Upstox statement. Its
#: holdings table reads cleanly at row zero, and the page is then re-rendered
#: further down the document with the columns misaligned - "Current Bal" and
#: "Free Bal" merged into one cell, so 105 shares and 105 free became 105105,
#: priced at the correct 556.50 and booked at 5.85 CRORE next to the correct
#: 58,432.50. A portfolio of 3.01 lakh read as 6.50 crore.
#:
#: A reader that takes the FIRST table to yield holdings rather than summing
#: them all cannot be hurt this way, and may pass a deeper window - see
#: `nps_holdings`.
_HEADER_SEARCH_ROWS = 12

#: What a single-table reader may use instead. KFintech returns its whole NPS
#: cover page as one twenty-six row table - subscriber details, nominees,
#: scheme preferences, the investment summary - with the holdings header
#: sixteen rows down.
_DEEP_HEADER_SEARCH_ROWS = 40


def rows_to_holdings(rows: Sequence[Sequence[Any]],
                     search_rows: int = _HEADER_SEARCH_ROWS) -> list[Holding]:
    """Read one extracted table into holdings.

    The header is located rather than assumed to be row zero: these documents
    put a title, an address block and a folio summary above the table.

    `search_rows` is how deep to look for it, and belongs to the CALLER
    because the safe depth depends on what the caller does with the result -
    see `_HEADER_SEARCH_ROWS`.
    """
    if not rows:
        return []

    header_index, mapping = -1, {}
    for index, row in enumerate(rows[:search_rows]):
        candidate = map_columns(row)
        # A real header names an instrument and at least one number about it.
        if "instrument" in candidate.values() and len(candidate) >= 3:
            header_index, mapping = index, candidate
            break
    if header_index < 0:
        return []

    holdings: list[Holding] = []
    for row in rows[header_index + 1:]:
        holding = Holding()
        for index, field_name in mapping.items():
            if index >= len(row):
                continue
            cell = row[index]
            if field_name in {"instrument", "symbol", "folio", "isin"}:
                value = str(cell or "").strip()
                if field_name in {"folio", "isin", "symbol"}:
                    value = _clean_identifier(value)
                setattr(holding, field_name, value)
            else:
                setattr(holding, field_name, to_decimal(cell))

        # An ISIN anywhere in the row beats one from a column that may not
        # exist; several layouts print it inside the scheme-name cell.
        if not holding.isin:
            found = ISIN.search(" ".join(str(c) for c in row if c))
            if found:
                holding.isin = found.group(1)

        # Only a real ISIN counts as one.
        #
        # A totals row's label bleeds into the ISIN column - "Total
        # Valuation" lands there and `_clean_identifier` squeezes it to
        # "TotalValuation", which is not an ISIN but is truthy. That mattered
        # because an ISIN VETOES the summary check below, so the statement's
        # own total row was admitted as a holding and every Upstox statement
        # came out at exactly twice its declared value - 301,417.49 of real
        # holdings plus a 301,417.49 row called "total".
        if holding.isin and not ISIN.fullmatch(holding.isin):
            holding.isin = ""

        # A row with no name and no units is a page footer.
        if not holding.instrument and not holding.isin:
            continue
        if holding.units is None and holding.value is None:
            continue
        # A totals row carries a value and a label but names no instrument.
        # An ISIN vetoes the check: no total has one, and a fund legitimately
        # called "Total Return Index Fund" would otherwise be dropped.
        #
        # The label is checked wherever it landed rather than only in the
        # instrument column, because on the row that started this it landed
        # in the ISIN one. Numeric cells are skipped - a figure is not a
        # label, and "Total" is only a label when it is the whole of a cell's
        # opening word.
        if not holding.isin and any(
                _SUMMARY_ROW.match(str(cell or "").strip())
                for cell in row if cell and to_decimal(cell) is None):
            continue

        holding.kind = classify_instrument(holding.instrument, holding.isin)
        holdings.append(holding)

    return holdings


# --------------------------------------------------------------------------
# NPS
#
# The one layout in this file that is TRANSPOSED. Every other statement puts
# one holding on a row; a CRA statement puts the schemes across the columns
# and the fields down the rows, because a subscriber holds three of them and
# the interesting comparison is between them:
#
#     Particulars                   References   SCHEME E   SCHEME C   SCHEME G
#     Scheme wise Value of Holdings E=U*N        232286.36  73520.21   3102.79
#     Total Units                   U            3139.9512  1588.7846  79.7140
#     NAV as on 30-Jun-26           N            73.9777    46.2745    38.9241
#
# `rows_to_holdings` cannot read that at any price - it looks for a header
# naming an instrument and finds "Particulars" - which is why an NPS corpus
# of 3.09 lakh was recorded as a portfolio with no holdings in it at all.
#
# The statement checks itself, though, and that is why it is worth reading
# rather than skipping: the three scheme values must add up to the "Value of
# your Holdings" the summary prints, so the same reconciliation every other
# document here goes through applies unchanged.
# --------------------------------------------------------------------------

#: Row label -> the field that row holds, matched against the folded label.
#: "value" is checked before "units" because the value row calls itself
#: "Scheme wise Value of your Holdings (Investments)" and the units row
#: "Total Units" - neither contains the other, but the order is cheap
#: insurance against a CRA rewording one of them.
_NPS_ROW_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("value", ("valueofyourholdings", "schemewisevalue", "valueofholdings")),
    ("units", ("totalunits", "unitsheld", "closingunits", "units")),
    ("nav", ("nav",)),
)

#: The summary cell that declares what the whole account is worth. Read from
#: the header rather than the prose because the label and its figure land on
#: different lines once the page is flattened to text.
#:
#: Protean's wording only. KFintech prints a "Current Valuation" that reads
#: like the same thing and is not: on the May statement it says 64,809.01
#: while the scheme table beneath it totals 64,882.29, and the three scheme
#: rows add up to the second. Reconciliation asks whether the rows that were
#: READ reproduce the total printed FOR THEM, so the anchor has to be the
#: holdings table's own total - `_table_total` - and a summary figure that
#: disagrees with it by 73.28 is a fact about the statement rather than a
#: parse error to chase.
_NPS_TOTAL_HEADERS = ("valueofyourholdings", "valueofholdings")

#: Columns that are labels rather than a scheme.
_NPS_LABEL_HEADERS = ("particulars", "references", "reference")

#: The subscriber's account number, which is the closest thing an NPS holding
#: has to a folio - and the only thing that tells two of them apart. The same
#: scheme (ICICI Prudential Pension Fund, say) is sold through both central
#: recordkeepers, so "SCHEME E" alone is ambiguous the moment a subscriber
#: holds accounts with each of them.
_PRAN = re.compile(r"\bPRAN\b\W{0,4}(\d{10,14})", re.IGNORECASE)


def parse_pran(text: str) -> str:
    """The PRAN this statement is for, or "" if it does not print one."""
    match = _PRAN.search(text or "")
    return match.group(1) if match else ""


def _folded(cell: Any) -> str:
    """A cell reduced to the letters and digits in it, lowercased."""
    return re.sub(r"[^a-z0-9]", "", str(cell or "").lower())


def _nps_scheme_columns(header: Sequence[Any]) -> dict[int, str]:
    """Which columns of the scheme-wise table are schemes, and their names."""
    return {index: str(cell).strip()
            for index, cell in enumerate(header)
            if str(cell or "").strip()
            and _folded(cell) not in _NPS_LABEL_HEADERS}


#: The row a UAN passbook ends each year with, and the three pots it
#: splits the money across:
#:
#:     Closing Balance as on 31/03/2027   2,51,542   1,06,023   60,000
#:
#: Recorded as three holdings rather than one total, because they are three
#: different things: the member's own share, the pension pot (EPS, which is
#: a pension entitlement rather than a withdrawable balance) and the
#: employer's share. Summing them would assert that all three are the same
#: kind of money, and they are not - so the split is kept and the reader
#: says which is which.
_EPF_CLOSING = re.compile(
    r"closing\s+balance\s+as\s+on\s+(\d{2}/\d{2}/\d{4})"
    r"((?:\s+[\d,]+){2,4})", re.IGNORECASE)

#: What the columns hold, in the order the passbook prints them.
_EPF_COLUMNS = ("EPF - member's share", "EPS - pension",
                "EPF - employer's share")


def epf_holdings(text: str) -> tuple[list[Holding], date | None]:
    """The corpus a UAN passbook closes on, split by pot."""
    match = _EPF_CLOSING.search(text or "")
    if not match:
        return [], None
    as_of = parsers.parse_date(match.group(1))
    figures = [to_decimal(tok) for tok in match.group(2).split()]
    figures = [f for f in figures if f is not None]

    holdings = []
    for index, value in enumerate(figures[:len(_EPF_COLUMNS)]):
        if value is None or value <= 0:
            continue
        holdings.append(Holding(
            instrument=_EPF_COLUMNS[index], kind="epf", value=value))
    return holdings, as_of


def _table_total(rows: Sequence[Any]) -> Decimal | None:
    """The figure on this table's own "Total" row, if it prints one."""
    for row in rows:
        if not row or _folded(row[0]) != "total":
            continue
        figures = [to_decimal(cell) for cell in row[1:]]
        figures = [f for f in figures if f is not None]
        if figures:
            return max(figures)
    return None


def nps_holdings(tables: Sequence[Any], pran: str = "") -> list[Holding]:
    """One holding per scheme, out of the scheme-wise summary table."""
    for table in tables:
        rows = getattr(table, "rows", None) or table
        if not rows:
            continue
        columns = _nps_scheme_columns(rows[0])
        if not columns:
            continue

        # Read the field rows underneath, keyed by scheme column.
        figures: dict[int, dict[str, Decimal | None]] = {
            index: {} for index in columns}
        for row in rows[1:]:
            label = _folded(row[0] if row else "")
            field_name = next(
                (name for name, fragments in _NPS_ROW_FIELDS
                 if any(f in label for f in fragments)), None)
            if field_name is None:
                continue
            for index in columns:
                if index < len(row) and field_name not in figures[index]:
                    figures[index][field_name] = to_decimal(row[index])

        # A scheme is only a holding if the table said what it is worth.
        # Anything less is some other table that happened to have columns.
        holdings = [
            Holding(instrument=name, kind="nps", folio=pran,
                    units=figures[index].get("units"),
                    nav=figures[index].get("nav"),
                    value=figures[index].get("value"))
            for index, name in columns.items()
            if figures[index].get("value") is not None
        ]
        if holdings:
            return holdings
    return []


def nps_declared_total(tables: Sequence[Any]) -> Decimal | None:
    """What the account is worth, as the statement's own summary declares it.

    Deliberately NOT `parse_declared_total`: an NPS statement prints "Total
    Units" and a transaction table full of totals, and the generic reader -
    which takes the largest figure that follows the word "total" - came back
    with 3,139.95, a UNIT COUNT, as the value of a 3.09 lakh account.
    """
    for table in tables:
        rows = getattr(table, "rows", None) or table
        # Any row, not just the first. KFintech's whole cover page comes back
        # as one table, so the investment summary is row sixteen of a
        # twenty-six row block rather than a table of its own.
        for header, values in zip(rows, rows[1:]):
            for index, cell in enumerate(header):
                if any(f in _folded(cell) for f in _NPS_TOTAL_HEADERS):
                    value = to_decimal(values[index]
                                       if index < len(values) else None)
                    if value is not None:
                        return value
    return None


def parse_statement(text: str, tables: Sequence[Any] = (),
                    filename: str = "") -> PortfolioStatement:
    """Read a portfolio statement out of extracted text and tables."""
    layout, provider = detect_layout(text, filename)
    statement = PortfolioStatement(
        layout=layout, provider=provider,
        as_of=parse_as_of(text),
        declared_value=parse_declared_total(text),
    )

    # NPS totals itself differently - see above - and the two recordkeepers
    # do not even agree on the shape of the holdings table. Protean transposes
    # it; KFintech prints one row per scheme, which the ordinary reader
    # handles once it is allowed to look. Try the transposed reader, then
    # fall back rather than reporting an account with nothing in it.
    if layout == "epf":
        statement.holdings, closed_on = epf_holdings(text)
        statement.as_of = closed_on or statement.as_of
        # The passbook prints no grand total, so there is nothing to
        # reconcile the three pots against. Said plainly rather than left
        # to look like a check that passed.
        statement.declared_value = None
        if not statement.holdings:
            statement.warnings.append(
                "No closing balance could be read out of this EPF passbook.")
        return statement

    if layout == "nps":
        pran = parse_pran(text)
        # The PRAN is the account, and it is the only thing that says which.
        #
        # A subscriber can hold an NPS account with each central recordkeeper
        # - this one does - and they hold different schemes. Sniffing the
        # provider name out of the text to tell them apart does not work:
        # a Protean statement does not print the word "Protean" anywhere,
        # so identical files landed under "NPS" and "Protean NPS" depending
        # on nothing, and the same 3.12 lakh was counted under both.
        statement.account_ref = pran
        statement.holdings = nps_holdings(tables, pran)
        if not statement.holdings:
            # The FIRST table that yields any, and then stop. A subscriber
            # has one holdings table; the tables after it are the extractor
            # re-rendering the same page, and they come back with the scheme
            # name wrapped ("LIMITED SCH") against a repeat of its figures.
            # Concatenating them counted the largest scheme twice and made a
            # 66,193.68 corpus 115,873.39.
            for table in tables:
                rows = getattr(table, "rows", None) or table
                found = _unique(rows_to_holdings(
                    rows, search_rows=_DEEP_HEADER_SEARCH_ROWS))
                if found:
                    statement.holdings = found
                    # From the same table as the rows, so the check is
                    # against the total printed for exactly these holdings.
                    statement.declared_value = _table_total(rows)
                    break
            for holding in statement.holdings:
                holding.kind = "nps"
                holding.folio = holding.folio or pran
        else:
            statement.declared_value = (nps_declared_total(tables)
                                        or statement.declared_value)
        if not statement.holdings:
            statement.warnings.append(
                "No scheme-wise holdings table could be read out of this NPS "
                "statement.")
        if statement.as_of is None:
            statement.warnings.append(
                "No valuation date found; the holdings cannot be dated.")
        return statement

    for table in tables:
        rows = getattr(table, "rows", None) or table
        # De-duplicated per table, which is the scope the duplicate actually
        # occurs in: a table continued over a page break repeats its header
        # and can repeat body rows with it. ACROSS tables the same ISIN is a
        # different thing entirely - it is the same stock held in a second
        # demat account, and a CAS lists one table per account.
        #
        # Deduplicating globally silently dropped every such second holding.
        # On a real CAS that was ten positions - Sterlite, Ola, Vodafone Idea
        # and seven more, held at both Zerodha and Upstox - worth 72,582.09
        # of a 10.67 lakh portfolio, gone with no warning beyond a
        # reconciliation gap nobody could account for.
        statement.holdings += _unique(rows_to_holdings(rows))

    statement.holdings = consolidate(statement.holdings)

    suspect = [h for h in statement.holdings
               if h.computed_value() is not None and h.suspect]
    for holding in suspect[:5]:
        statement.warnings.append(
            f"{holding.instrument or holding.isin}: {holding.suspect}")

    if not statement.holdings:
        statement.warnings.append(
            "No holdings table could be read out of this statement.")
    if statement.as_of is None:
        statement.warnings.append(
            "No valuation date found; the holdings cannot be dated.")

    return statement


def consolidate(holdings: list[Holding]) -> list[Holding]:
    """One row per security, summing the lots a CAS lists per demat account.

    A consolidated statement is consolidated across ACCOUNTS, not across
    securities: 279 Sterlite at Zerodha and 105 at Upstox are printed as two
    rows in two tables. Both are real and both are the same holding, and
    which of those two facts matters depends on who is asking.

    Storage says one holding. A row is identified by (account, ISIN, folio,
    instrument, valuation date) and neither lot carries a folio - a demat
    table has no folio column - so the two collided and the second silently
    replaced the first. Ten securities were held at two brokers, and the
    portfolio came out 3.13 lakh short of the statement it was read from
    while every intermediate step reported success.

    Summing is the honest resolution rather than a workaround: the position
    in Sterlite IS 384 shares, and that is also what the CAS's own grand
    total counts. Merging cannot change the total - it only regroups it -
    which is the property `reconcile` then checks for free.

    Only rows carrying an ISIN and no folio are merged. A folio number is a
    real distinction between two accounts holding the same fund, and NPS
    schemes have no ISIN at all.
    """
    merged: dict[tuple[str, str], Holding] = {}
    out: list[Holding] = []
    for holding in holdings:
        if not holding.isin or holding.folio:
            out.append(holding)
            continue
        key = (holding.isin, holding.folio)
        first = merged.get(key)
        if first is None:
            merged[key] = holding
            out.append(holding)
            continue
        lot = holding.computed_value()
        running = first.computed_value()
        if first.units is not None and holding.units is not None:
            first.units += holding.units
        # The value is what has to survive; the NAV is shared between lots,
        # so the sum has to be carried explicitly rather than recomputed.
        if lot is not None:
            first.value = (running or Decimal("0")) + lot
            first.nav = None if first.units in (None, 0) else (
                first.value / first.units).quantize(Decimal("0.0001"))
    return out


def _unique(holdings: list[Holding]) -> list[Holding]:
    """One table's holdings with any row read twice removed.

    A holding read twice inflates the portfolio by its own value, and a
    repeated header is how that happens - see `parse_statement` for why the
    scope is one table rather than the whole document.
    """
    unique: dict[tuple[str, str, str], Holding] = {}
    for holding in holdings:
        key = (holding.isin, holding.folio, holding.instrument.upper())
        unique.setdefault(key, holding)
    return list(unique.values())


def read_file(path: Path, password: str | None = None,
              password_candidates: list[str] | None = None
              ) -> tuple[PortfolioStatement | None, list[str]]:
    """Parse a holdings statement. Returns (statement, warnings).

    (None, warnings) means this was not a portfolio document, so the caller can
    route it to the statement pipeline instead of recording a parse failure for
    a file that was simply a different kind.
    """
    from . import extractors

    try:
        result = extractors.extract_pdf(
            path, password=password, password_candidates=password_candidates)
    except Exception as exc:
        log.exception("portfolio extraction crashed on %s", path.name)
        return None, [f"Extraction failed: {type(exc).__name__}: {exc}"]

    text = getattr(result, "full_text", "") or getattr(result, "text", "") or ""
    tables = getattr(result, "tables", []) or []
    for table in tables:
        for row in getattr(table, "rows", []) or []:
            text += "\n" + " ".join(str(cell) for cell in row if cell)

    if not looks_like_portfolio(text, path.name):
        return None, ["Not a portfolio or holdings statement."]

    statement = parse_statement(text, tables, path.name)
    statement.warnings = [*result.warnings, *statement.warnings]
    return statement, statement.warnings
