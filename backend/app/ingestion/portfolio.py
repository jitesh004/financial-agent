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
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Layout detection
# --------------------------------------------------------------------------

LAYOUT_SIGNATURES: list[tuple[str, str, tuple[str, ...]]] = [
    ("cas", "CDSL/NSDL", ("consolidated account statement", "cdsl", "nsdl",
                          "demat account", "depository")),
    ("cams", "CAMS", ("cams", "camsonline", "karvy", "consolidated portfolio")),
    ("kfintech", "KFintech", ("kfintech", "kfin technologies")),
    ("broker", "Broker", ("holdings statement", "portfolio holdings",
                          "zerodha", "groww", "upstox", "angel one",
                          "icici direct", "kotak securities", "5paisa")),
]


def detect_layout(text: str, filename: str = "") -> tuple[str, str]:
    """(layout, provider) for this document, or ("unknown", "")."""
    haystack = f"{filename} {text[:6000]}".lower()
    for layout, provider, fragments in LAYOUT_SIGNATURES:
        if any(fragment in haystack for fragment in fragments):
            return layout, provider
    return "unknown", ""


def looks_like_portfolio(text: str, filename: str = "") -> bool:
    """Whether this is a holdings document rather than a bank statement.

    An ISIN is the strongest single signal - nothing but a securities document
    contains one - so it alone is enough. Otherwise two softer markers must
    agree, which keeps a bank statement that merely mentions "mutual fund" out.
    """
    if ISIN.search(text):
        return True
    if detect_layout(text, filename)[0] == "unknown":
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

_NUMBER = re.compile(r"-?[\d,]+(?:\.\d+)?")
_DATE_LINE = re.compile(
    r"(?:as\s+on|as\s+at|statement\s+date|valuation\s+date|holdings?\s+as\s+on)"
    r"\D{0,20}(\d{1,2}[-/\s][A-Za-z0-9]{2,9}[-/\s]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE)

_TOTAL_LINE = re.compile(
    r"(?:total|grand\s+total|portfolio)\s*(?:value|valuation)?"
    r"\D{0,30}((?:rs\.?|inr|₹)?\s*[\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)


def to_decimal(raw: Any) -> Decimal | None:
    """A number out of a cell, or None.

    None rather than zero, throughout: a blank NAV column means the statement
    did not print one, and a zero there would silently value the holding at
    nothing and drag the whole portfolio total down with it.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text in {"-", "--", "N/A", "NA", "nil", "Nil"}:
        return None
    match = _NUMBER.search(text.replace(" ", ""))
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def parse_as_of(text: str) -> date | None:
    match = _DATE_LINE.search(text)
    if not match:
        return None
    token = match.group(1).strip().replace("/", "-").replace(" ", "-")
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%d-%b-%y", "%d-%m-%y",
                "%Y-%m-%d"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


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
    (("schemename", "securityname", "scheme", "instrument", "stock",
      "companyname", "description", "securitydescription"), "instrument"),
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
#: and no NAV. Both forms are compared: the squeezed one catches the split
#: words, the spaced one keeps multi-word headers like "market value" working.
def _header_forms(cell: Any) -> tuple[str, str]:
    label = str(cell or "").strip().lower()
    return re.sub(r"\s+", "", label), label


def map_columns(header: Sequence[Any]) -> dict[int, str]:
    """Which column holds which field. Matched by header text, not position.

    Every one of these layouts reorders its columns between versions, and a
    reader that counts columns silently swaps NAV and value the first time one
    does - producing a portfolio worth units-times-value.
    """
    mapping: dict[int, str] = {}
    taken: set[str] = set()
    for index, cell in enumerate(header):
        squeezed, spaced = _header_forms(cell)
        if not squeezed:
            continue
        for fragments, name in _COLUMN_HINTS:
            if name in taken:
                continue
            if any(f in squeezed or f in spaced for f in fragments):
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

    def computed_value(self) -> Decimal | None:
        """units x NAV, or the printed value when one of them is missing.

        Preferring the computed figure is the point: it is the one that can be
        checked against the declared total, and a printed value that disagrees
        with units x NAV means a column was misread.
        """
        if self.units is not None and self.nav is not None:
            return (self.units * self.nav).quantize(Decimal("0.01"))
        return self.value


@dataclass
class PortfolioStatement:
    layout: str = "unknown"
    provider: str = ""
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


def rows_to_holdings(rows: Sequence[Sequence[Any]]) -> list[Holding]:
    """Read one extracted table into holdings.

    The header is located rather than assumed to be row zero: these documents
    put a title, an address block and a folio summary above the table.
    """
    if not rows:
        return []

    header_index, mapping = -1, {}
    for index, row in enumerate(rows[:12]):
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
                setattr(holding, field_name, str(cell or "").strip())
            else:
                setattr(holding, field_name, to_decimal(cell))

        # An ISIN anywhere in the row beats one from a column that may not
        # exist; several layouts print it inside the scheme-name cell.
        if not holding.isin:
            found = ISIN.search(" ".join(str(c) for c in row if c))
            if found:
                holding.isin = found.group(1)

        # A row with no name and no units is a page footer.
        if not holding.instrument and not holding.isin:
            continue
        if holding.units is None and holding.value is None:
            continue
        # A totals row carries a value and a label but names no instrument.
        # An ISIN vetoes the check: no total has one, and a fund legitimately
        # called "Total Return Index Fund" would otherwise be dropped.
        if not holding.isin and _SUMMARY_ROW.match(holding.instrument):
            continue

        holding.kind = classify_instrument(holding.instrument, holding.isin)
        holdings.append(holding)

    return holdings


def parse_statement(text: str, tables: Sequence[Any] = (),
                    filename: str = "") -> PortfolioStatement:
    """Read a portfolio statement out of extracted text and tables."""
    layout, provider = detect_layout(text, filename)
    statement = PortfolioStatement(
        layout=layout, provider=provider,
        as_of=parse_as_of(text),
        declared_value=parse_declared_total(text),
    )

    for table in tables:
        rows = getattr(table, "rows", None) or table
        statement.holdings += rows_to_holdings(rows)

    if not statement.holdings:
        statement.warnings.append(
            "No holdings table could be read out of this statement.")
    if statement.as_of is None:
        statement.warnings.append(
            "No valuation date found; the holdings cannot be dated.")

    # De-duplicate: a multi-page table repeats its header, and a holding read
    # twice would inflate the portfolio by its own value.
    unique: dict[tuple[str, str, str], Holding] = {}
    for holding in statement.holdings:
        key = (holding.isin, holding.folio, holding.instrument.upper())
        unique.setdefault(key, holding)
    statement.holdings = list(unique.values())

    return statement


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
