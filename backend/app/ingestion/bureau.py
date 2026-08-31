"""Read a credit bureau report.

A bureau report is not a statement and deliberately does not go through the
statement pipeline. There is no opening balance, no running total and no set of
rows that has to add up - so the reconciliation gate has nothing to check, and
running it would report every report as unreconciled forever.

What a bureau report is instead: an independent second opinion on what the user
owes. It lists every credit account every lender has reported, which makes it
the only source in this app that can reveal an account the ledger has never
seen. A card whose statements never arrive by email is invisible until a bureau
names it.

The four bureaus lay their reports out differently but describe the same
things, so the reader below finds fields by label rather than by position. That
is slower than a fixed layout and survives the layout changing, which it does.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Which bureau
# --------------------------------------------------------------------------

#: Fragments that identify the issuer, checked against the text and filename.
BUREAU_SIGNATURES: list[tuple[str, tuple[str, ...]]] = [
    ("cibil", ("cibil", "transunion")),
    ("crif", ("crif", "high mark", "highmark")),
    ("experian", ("experian",)),
    ("equifax", ("equifax",)),
]


def detect_bureau(text: str, filename: str = "") -> str:
    """Which bureau produced this, or "unknown"."""
    haystack = f"{filename} {text[:4000]}".lower()
    for name, fragments in BUREAU_SIGNATURES:
        if any(fragment in haystack for fragment in fragments):
            return name
    return "unknown"


def looks_like_bureau_report(text: str, filename: str = "") -> bool:
    """Whether this document is a credit report rather than a statement.

    Checked before parsing so a misrouted bank statement is rejected outright
    instead of producing a report with a score of None and no accounts, which
    reads like a parser bug rather than the wrong file.
    """
    if detect_bureau(text, filename) == "unknown":
        return False
    lowered = text.lower()
    markers = ("credit report", "credit information", "credit score",
               "account information", "enquiry", "cir ", "credit vision")
    return sum(1 for m in markers if m in lowered) >= 2


# --------------------------------------------------------------------------
# Value readers
# --------------------------------------------------------------------------

_MONEY = re.compile(r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_DATE_PATTERNS = (
    (re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})"), ("d", "m", "y")),
    (re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"(\d{1,2})[-\s]([A-Za-z]{3})[-\s](\d{2,4})"), ("d", "b", "y")),
)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_money(raw: str | None) -> Decimal | None:
    """A rupee figure out of free text, or None.

    Returns None rather than zero for anything unreadable: a bureau printing
    "-" for a closed account's balance means "nothing reported", and recording
    that as ₹0 would put a confident figure where there is none.
    """
    if not raw:
        return None
    match = _MONEY.search(raw.replace(" ", ""))
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    for pattern, order in _DATE_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        parts = dict(zip(order, match.groups()))
        try:
            year = int(parts["y"])
            if year < 100:
                year += 2000 if year < 50 else 1900
            month = (_MONTHS.get(parts["b"][:3].lower(), 0) if "b" in parts
                     else int(parts["m"]))
            if not 1 <= month <= 12:
                continue
            return date(year, month, int(parts["d"]))
        except (ValueError, KeyError):
            continue
    return None


#: Bureau account-type wording -> this app's AccountType values. Everything a
#: bureau calls a loan really is one; the distinction that matters downstream is
#: only ever card vs loan vs other.
_TYPE_MAP: list[tuple[tuple[str, ...], str]] = [
    (("credit card", "creditcard", "charge card"), "credit_card"),
    (("housing loan", "home loan", "mortgage"), "home_loan"),
    (("auto loan", "car loan", "two-wheeler", "vehicle"), "auto_loan"),
    (("personal loan", "consumer loan", "business loan", "gold loan",
      "education loan", "loan against", "overdraft"), "personal_loan"),
    (("savings", "current account"), "savings"),
]


def map_account_type(raw: str) -> str:
    lowered = (raw or "").lower()
    for fragments, mapped in _TYPE_MAP:
        if any(fragment in lowered for fragment in fragments):
            return mapped
    return "unknown"


#: Corporate suffixes that carry no identity. Stripped so "HDFC BANK LTD" and
#: "HDFC Bank" reduce to the same key.
_NOISE = re.compile(
    r"\b(ltd|limited|pvt|private|bank|banking|financial|finance|services|"
    r"corp|corporation|india|indian|co|company|nbfc|housing|"
    r"credit|card|cards)\b", re.IGNORECASE)


def lender_key(name: str) -> str:
    """A comparable form of a lender's name.

    Bureaus print the registered entity ("HOUSING DEVELOPMENT FINANCE CORP"),
    statements print the brand ("HDFC Bank"). Reducing both to their
    distinctive words is what gives a match anything to work with - and it is
    still only a hint, which is why nothing auto-links on the name alone.
    """
    cleaned = _NOISE.sub(" ", (name or "").lower())
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned)
    words = [w for w in cleaned.split() if len(w) > 2]
    return "".join(words[:2])


_DIGITS = re.compile(r"\d")


def number_suffix(masked: str) -> str:
    """The last four digits of an account number, or "".

    Bureaus mask differently from statements - XXXXXX1234, ****1234, or the
    full number - and the trailing digits are the only part that survives both.
    """
    digits = "".join(_DIGITS.findall(masked or ""))
    return digits[-4:] if len(digits) >= 4 else ""


# --------------------------------------------------------------------------
# The parsed shape
# --------------------------------------------------------------------------


@dataclass
class BureauAccount:
    lender: str = ""
    account_type: str = "unknown"
    account_number_masked: str = ""
    ownership: str = ""
    opened_on: date | None = None
    closed_on: date | None = None
    status: str = "open"
    sanctioned: Decimal | None = None
    current_balance: Decimal | None = None
    overdue: Decimal | None = None
    credit_limit: Decimal | None = None
    emi_amount: Decimal | None = None
    dpd_history: list[str] = field(default_factory=list)

    @property
    def lender_key(self) -> str:
        return lender_key(self.lender)

    @property
    def number_suffix(self) -> str:
        return number_suffix(self.account_number_masked)

    @property
    def worst_dpd(self) -> int:
        """The largest days-past-due ever reported on this account.

        "STD", "XXX" and "000" all mean nothing was overdue; only a number
        counts, and the worst of them is what a lender actually looks at.
        """
        worst = 0
        for entry in self.dpd_history:
            digits = re.sub(r"\D", "", str(entry))
            if digits:
                worst = max(worst, int(digits))
        return worst


@dataclass
class BureauReport:
    bureau: str = "unknown"
    score: int | None = None
    score_band: str = ""
    pulled_on: date | None = None
    holder_name: str = ""
    accounts: list[BureauAccount] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_outstanding(self) -> Decimal:
        return sum((a.current_balance or Decimal("0")) for a in self.accounts)

    @property
    def total_overdue(self) -> Decimal:
        return sum((a.overdue or Decimal("0")) for a in self.accounts)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

_SCORE_PATTERNS = (
    re.compile(r"(?:cibil\s*(?:transunion\s*)?score|credit\s*score|score)"
               r"\D{0,40}?\b(\d{3})\b", re.IGNORECASE),
    re.compile(r"\b(\d{3})\b\s*(?:out of|/)\s*900", re.IGNORECASE),
)

#: A bureau score runs 300-900. Anything outside that is a page number, a
#: postcode or a truncated amount that happened to sit near the word "score".
SCORE_RANGE = (300, 900)

_LABELS = {
    "lender": ("member name", "lender", "credit grantor", "institution",
               "bank name", "account holder type"),
    "account_type": ("account type", "type of credit facility", "credit type",
                     "loan type", "type"),
    "account_number": ("account number", "account no", "acct number",
                       "account #", "credit card number"),
    "opened": ("date opened", "opened", "date of opening", "disbursed on",
               "sanction date"),
    "closed": ("date closed", "closed", "date of closure"),
    "status": ("account status", "status", "current status"),
    "sanctioned": ("sanctioned amount", "high credit", "credit limit",
                   "sanctioned", "loan amount", "disbursed amount"),
    "balance": ("current balance", "outstanding balance", "balance",
                "current outstanding", "amount outstanding"),
    "overdue": ("amount overdue", "overdue amount", "overdue", "past due",
                "amount past due"),
    "emi": ("emi amount", "emi", "installment amount", "instalment amount"),
    "ownership": ("ownership", "account holder", "ownership type"),
}


def _label_for(line: str) -> tuple[str | None, str]:
    """(field, value) if this line is a labelled field, else (None, "")."""
    if ":" not in line:
        return None, ""
    label, _, value = line.partition(":")
    label = label.strip().lower().rstrip("*# ")
    for name, aliases in _LABELS.items():
        if any(label == alias or label.startswith(alias) for alias in aliases):
            return name, value.strip()
    return None, ""


_DPD = re.compile(r"\b(?:000|std|xxx|\d{1,3})\b", re.IGNORECASE)
_ACCOUNT_BREAK = re.compile(
    r"^\s*(?:account\s*(?:details|information)?\s*[-#:]?\s*\d*|"
    r"credit\s*facility|\d+\.\s*(?:credit card|loan))\s*$", re.IGNORECASE)


def parse_report(text: str, filename: str = "") -> BureauReport:
    """Read a bureau report out of its extracted text.

    Never raises: a report that cannot be read returns with warnings and no
    accounts, so the file registry records why rather than the import dying.
    """
    report = BureauReport(bureau=detect_bureau(text, filename))
    if not text.strip():
        report.warnings.append("No text could be extracted from the report.")
        return report

    if report.bureau == "unknown":
        report.warnings.append(
            "Could not tell which bureau issued this report; parsed on a "
            "best-effort basis.")

    # ---- score ----
    for pattern in _SCORE_PATTERNS:
        for match in pattern.finditer(text):
            value = int(match.group(1))
            if SCORE_RANGE[0] <= value <= SCORE_RANGE[1]:
                report.score = value
                break
        if report.score is not None:
            break
    if report.score is None:
        report.warnings.append("No credit score found in the report.")
    else:
        report.score_band = score_band(report.score)

    # ---- pulled-on date ----
    for line in text.splitlines()[:60]:
        lowered = line.lower()
        if any(k in lowered for k in ("date:", "report date", "generated on",
                                      "as on", "date of report")):
            found = parse_date(line)
            if found:
                report.pulled_on = found
                break

    # ---- holder ----
    for line in text.splitlines()[:80]:
        lowered = line.lower()
        if lowered.startswith(("name:", "consumer name", "member name:")):
            report.holder_name = line.partition(":")[2].strip()
            break

    report.accounts = _parse_accounts(text)
    if not report.accounts:
        report.warnings.append(
            "No credit accounts could be read out of this report.")
    return report


def _parse_accounts(text: str) -> list[BureauAccount]:
    """Split the report into account blocks and read each one.

    Blocks are found by label rather than by position: the bureaus reflow their
    layouts often enough that anything counting lines or columns breaks within
    a year, while "the line that says Account Number" keeps working.
    """
    accounts: list[BureauAccount] = []
    current: BureauAccount | None = None
    seen: set[tuple[str, str]] = set()

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        # A block with neither a lender nor a number is a heading that happened
        # to contain a colon, not an account.
        if current.lender or current.account_number_masked:
            identity = (current.lender_key, current.number_suffix)
            if identity != ("", "") and identity not in seen:
                seen.add(identity)
                accounts.append(current)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if _ACCOUNT_BREAK.match(line):
            flush()
            current = BureauAccount()
            continue

        name, value = _label_for(line)
        if name is None:
            if current is not None and _looks_like_dpd_row(line):
                current.dpd_history += _DPD.findall(line)
            continue

        # A second "account number" means the previous block ended, whether or
        # not a heading separated them - which several layouts do not.
        if name == "account_number" and current is not None \
                and current.account_number_masked:
            flush()
        if current is None:
            current = BureauAccount()

        if name == "lender":
            current.lender = value
        elif name == "account_type":
            current.account_type = map_account_type(value)
        elif name == "account_number":
            current.account_number_masked = value
        elif name == "opened":
            current.opened_on = parse_date(value)
        elif name == "closed":
            current.closed_on = parse_date(value)
            if current.closed_on:
                current.status = "closed"
        elif name == "status":
            current.status = _normalise_status(value)
        elif name == "sanctioned":
            amount = parse_money(value)
            current.sanctioned = amount
            if current.account_type == "credit_card":
                current.credit_limit = amount
        elif name == "balance":
            current.current_balance = parse_money(value)
        elif name == "overdue":
            current.overdue = parse_money(value)
        elif name == "emi":
            current.emi_amount = parse_money(value)
        elif name == "ownership":
            current.ownership = value

    flush()
    return accounts


def _looks_like_dpd_row(line: str) -> bool:
    """A payment-history row: mostly 000/STD/XXX tokens and little else."""
    tokens = line.split()
    if len(tokens) < 4:
        return False
    hits = sum(1 for t in tokens if re.fullmatch(r"(?:000|STD|XXX|\d{1,3})",
                                                 t, re.IGNORECASE))
    return hits >= max(3, len(tokens) // 2)


def _normalise_status(raw: str) -> str:
    lowered = (raw or "").lower()
    if any(k in lowered for k in ("closed", "settled", "written off",
                                  "written-off", "paid")):
        return "closed"
    if "doubtful" in lowered or "sub-standard" in lowered or "loss" in lowered:
        return "delinquent"
    return "open"


def score_band(score: int | None) -> str:
    """The band a score falls in, in the wording lenders use.

    Bands rather than a bare number because 742 means nothing on its own and
    the boundaries are what actually change an interest rate.
    """
    if score is None:
        return ""
    if score >= 800:
        return "excellent"
    if score >= 750:
        return "very good"
    if score >= 700:
        return "good"
    if score >= 650:
        return "fair"
    if score >= 550:
        return "poor"
    return "very poor"


# --------------------------------------------------------------------------
# File entry point
# --------------------------------------------------------------------------


def read_file(path: Path, password: str | None = None,
              password_candidates: list[str] | None = None
              ) -> tuple[BureauReport | None, list[str]]:
    """Parse a bureau report PDF. Returns (report, warnings).

    Returns (None, warnings) when the file is not a bureau report at all, so
    the caller can route it back to the statement pipeline rather than
    recording a parse failure for a file that was simply the wrong kind.
    """
    from . import extractors

    try:
        result = extractors.extract_pdf(
            path, password=password, password_candidates=password_candidates)
    except Exception as exc:  # extractor bugs must not take down an import
        log.exception("bureau extraction crashed on %s", path.name)
        return None, [f"Extraction failed: {type(exc).__name__}: {exc}"]

    text = _text_of(result)
    if not looks_like_bureau_report(text, path.name):
        return None, ["Not a credit bureau report."]

    report = parse_report(text, path.name)
    report.warnings = [*result.warnings, *report.warnings]
    return report, report.warnings


def _text_of(result: Any) -> str:
    """Whatever text an ExtractionResult carries, however it carries it.

    The PDF extractor returns tables for statements and raw text for anything
    else; a bureau report can come back either way depending on how the issuer
    laid it out, and both have to be readable here.
    """
    text = getattr(result, "full_text", "") or getattr(result, "text", "") or ""
    for table in getattr(result, "tables", []) or []:
        for row in getattr(table, "rows", []) or []:
            text += "\n" + " ".join(str(cell) for cell in row if cell)
    return text
