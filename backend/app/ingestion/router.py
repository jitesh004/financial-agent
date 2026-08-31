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


def classify_document(text: str, filename: str = "") -> str:
    """Which reader should handle this document.

    Order matters. A bureau report mentions credit limits and balances often
    enough to look statement-shaped, and a CAS lists ISINs that no bank
    statement carries - so the two specific tests run first and the statement
    pipeline is the fallback, which is also the safe default: it is the only
    one with a reconciliation gate to catch its own mistakes.
    """
    from .bureau import looks_like_bureau_report
    from .portfolio import looks_like_portfolio

    if looks_like_bureau_report(text, filename):
        return DOC_BUREAU
    if looks_like_portfolio(text, filename):
        return DOC_PORTFOLIO
    return DOC_STATEMENT


def text_of(result: "ExtractionResult") -> str:
    """All the text an extraction produced, tables included.

    Classification needs to see everything: the marker that identifies a
    document is as likely to be inside a table cell as in the running text,
    depending on how the issuer laid the page out.
    """
    text = getattr(result, "text", "") or ""
    for table in getattr(result, "tables", []) or []:
        for row in getattr(table, "rows", []) or []:
            text += "\n" + " ".join(str(cell) for cell in row if cell)
    return text
