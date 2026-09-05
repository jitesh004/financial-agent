"""Dispatch an uploaded file to the right extractor.

Format detection uses content signatures (magic bytes) first and the file
extension only as a fallback. Users rename files, and mail clients hand out
.xls files that are really HTML or CSV inside - trusting the extension alone
is how you get a confusing "no tables found" on a perfectly good statement.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ..models.schemas import ExtractionResult, SourceFormat
from . import extractors

log = logging.getLogger(__name__)

#: Leading bytes -> format. ZIP is shared by xlsx/docx, disambiguated below.
_MAGIC: list[tuple[bytes, str]] = [
    (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"\xd0\xcf\x11\xe0", "ole"),  # legacy .xls / .doc
]

SUPPORTED_EXTENSIONS = {
    ".pdf", ".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".txt", ".docx",
}


def file_hash(path: Path) -> str:
    """Content hash, used to detect the same statement uploaded twice.

    Deduplicating on filename would miss 'statement.pdf' vs 'statement (1).pdf',
    which is the single most common way a user double-counts their own spending.
    """
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_format(path: Path) -> SourceFormat:
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        head = b""

    kind = next((k for sig, k in _MAGIC if head.startswith(sig)), None)
    suffix = path.suffix.lower()

    if kind == "pdf":
        return SourceFormat.PDF
    if kind == "ole":
        return SourceFormat.XLS
    if kind == "zip":
        # Both xlsx and docx are ZIP containers; the extension decides, and if
        # that is missing we peek at the archive's internal layout.
        if suffix in {".xlsx", ".xlsm"}:
            return SourceFormat.XLSX
        if suffix == ".docx":
            return SourceFormat.DOCX
        return _sniff_ooxml(path)

    if suffix in {".csv", ".tsv", ".txt"}:
        return SourceFormat.CSV
    if suffix in {".xlsx", ".xlsm"}:
        return SourceFormat.XLSX
    if suffix == ".xls":
        return SourceFormat.XLS
    if suffix == ".docx":
        return SourceFormat.DOCX
    if suffix == ".pdf":
        return SourceFormat.PDF

    return SourceFormat.UNKNOWN


def _sniff_ooxml(path: Path) -> SourceFormat:
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return SourceFormat.UNKNOWN

    if "xl/workbook.xml" in names:
        return SourceFormat.XLSX
    if "word/document.xml" in names:
        return SourceFormat.DOCX
    return SourceFormat.UNKNOWN


def extract(
    path: Path,
    password: str | None = None,
    password_candidates: list[str] | None = None,
) -> ExtractionResult:
    """Run the appropriate extractor. Never raises for a bad file.

    `password_candidates` are the user's own derived passwords, used only for
    protected PDFs (see ingestion.passwords).
    """
    fmt = detect_format(path)
    log.info("extracting %s as %s", path.name, fmt.value)

    try:
        if fmt == SourceFormat.PDF:
            return extractors.extract_pdf(
                path, password=password, password_candidates=password_candidates
            )
        if fmt in (SourceFormat.XLSX, SourceFormat.XLS):
            return extractors.extract_excel(path)
        if fmt == SourceFormat.DOCX:
            return extractors.extract_docx(path)
        if fmt == SourceFormat.CSV:
            return extractors.extract_csv(path)
    except Exception as exc:  # extractor bugs must not take down an upload batch
        log.exception("extractor crashed on %s", path.name)
        result = ExtractionResult(source_format=fmt, extractor_used="failed")
        result.warnings.append(f"Extraction failed: {type(exc).__name__}: {exc}")
        return result

    result = ExtractionResult(source_format=fmt, extractor_used="none")
    result.warnings.append(
        f"Unsupported file type '{path.suffix or 'unknown'}'. "
        f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
    )
    return result


# --------------------------------------------------------------------------
# Which reader a document belongs to
#
# A bureau report, a holdings statement and a bank statement are all PDFs, and
# only their contents distinguish them. Routing on the sender or the filename
# guesses wrong often enough to matter: a CIBIL report pushed through the
# statement pipeline finds no transactions and gets recorded as a parse
# failure, which reads like a bug rather than a misrouted file.
# --------------------------------------------------------------------------

#: What a document is, once something has looked inside it.
DOC_STATEMENT = "statement"
DOC_BUREAU = "bureau"
DOC_PORTFOLIO = "portfolio"
#: Nothing came out of the file. Distinct from "statement" on purpose - see
#: classify_document.
DOC_UNREADABLE = "unreadable"


def classify_document(text: str, filename: str = "",
                      full_text: str = "") -> str:
    """Which reader should handle this document.

    Order matters. A bureau report mentions credit limits and balances often
    enough to look statement-shaped, and a CAS lists ISINs that no bank
    statement carries - so the two specific tests run first and the statement
    pipeline is the fallback, which is also the safe default: it is the only
    one with a reconciliation gate to catch its own mistakes.

    The two tests want DIFFERENT text, which is why `full_text` exists.

    A bureau report announces itself in prose - "Consumer Credit Report",
    "Credit Information Report" - and its tables say only what any statement
    says, so it must be judged on the whole document or not at all. Given
    tables alone, a real CRIF report classified as a bank statement.

    A holdings statement is the opposite: judged on the whole document, a
    mutual fund's monthly statement is reclassified by its own letterhead
    ("Mutual Fund Consolidated Account Statement") when its body is a year
    of SIP purchases that belong in the ledger. So that test keeps the
    tables - see `text_of`.
    """
    from .bureau import looks_like_bureau_report
    from .portfolio import looks_like_portfolio, looks_like_trades

    # An empty extraction is not a bank statement, it is an unread file.
    # Falling through to the statement reader made "I could not open this"
    # and "this is a statement" the same answer, and the statement reader is
    # the one with a fallback for everything.
    if not (text or "").strip():
        return DOC_UNREADABLE

    if looks_like_bureau_report(full_text or text, filename):
        return DOC_BUREAU
    # A record of TRADES is neither a ledger nor a portfolio: its quantities
    # are what changed hands and its "rate" may be a strike price. Checked
    # before the holdings test, which fires on a contract note too.
    if looks_like_trades(text):
        return DOC_PORTFOLIO
    if looks_like_portfolio(text, filename):
        return DOC_PORTFOLIO
    return DOC_STATEMENT


def text_of(result: "ExtractionResult") -> str:
    """The text `classify_document` reads: the TABLES, and nothing above them.

    Deliberately not the whole document, which is the surprising part and so
    is worth stating. A statement's cover matter says what the issuer would
    like the document to be called; its tables say what it actually contains,
    and where the two disagree the tables are right.

    A mutual fund's monthly statement is the case that settles it. Its
    heading reads "Mutual Fund Consolidated Account Statement" and it prints
    a units-and-NAV summary underneath - every signal of a holdings
    document - and then the body is a dated ledger of SIP purchases with a
    running balance, which is a statement and has to reach the ledger as one.
    Reading the heading reclassified it, and a year of SIPs stopped being
    transactions.

    A reader that has already been TOLD what kind of document it has wants
    everything; that is `full_text_of`.
    """
    text = ""
    for table in getattr(result, "tables", []) or []:
        for row in getattr(table, "rows", []) or []:
            text += "\n" + " ".join(str(cell) for cell in row if cell)
    return text


def full_text_of(result: "ExtractionResult") -> str:
    """Everything an extraction produced - the running text and the tables.

    For readers past the classification step, which know what they are
    holding and need the fields a document prints outside its tables: a CAS
    puts its grand total in a table but an NPS statement prints its valuation
    date in prose, and a reader that saw only tables would date the holdings
    from nothing.
    """
    text = getattr(result, "full_text", "") or getattr(result, "text", "") or ""
    return text + text_of(result)
