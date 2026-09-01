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
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..normalize import parsers
from ..rules import formats, institutions

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Which bureau
# --------------------------------------------------------------------------

#: Fragments that identify the issuer, checked against the text and filename.
#: Derived from the four registry records that carry a `bureau_key`; a score
#: app like OneScore mails about your credit file without being a bureau, and
#: is deliberately findable by a scan but not nameable here.
BUREAU_SIGNATURES: list[tuple[str, tuple[str, ...]]] = [
    (key, fragments) for key, fragments in institutions.bureau_signatures()
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

# Both readers delegate to `normalize.parsers` - the same code that reads a
# bank statement cell. They used to be private reimplementations here, and the
# date one knew three shapes where the shared reader knows ten: "Aug 29, 2026",
# "15.01.2026" and "15-01-26" all appear on real reports and all produced no
# date at all.


def parse_money(raw: str | None) -> Decimal | None:
    """A rupee figure out of free text, or None.

    Returns None rather than zero for anything unreadable: a bureau printing
    "-" for a closed account's balance means "nothing reported", and recording
    that as zero would put a confident figure where there is none.
    """
    return parsers.money(raw)


def parse_date(raw: str | None) -> date | None:
    """The date inside a bureau field value, or None."""
    return parsers.find_date(raw or "")


#: Wording a bureau uses that the statement reader has no reason to know.
#: A bureau names the credit FACILITY ("Overdraft", "Loan Against Property");
#: a statement letterhead names the PRODUCT. Everything the two do share is in
#: `metadata.ACCOUNT_TYPE_PATTERNS`, which this defers to - the two lists were
#: independent and had already disagreed: a bureau line reading "Wallet"
#: mapped to "unknown" here and to WALLET there.
_BUREAU_ONLY_TYPES: tuple[tuple[str, str], ...] = (
    ("overdraft", "personal_loan"),
    # A bureau names the FACILITY, so a bare "Vehicle" or "Two-Wheeler" is
    # the account type. In statement prose they are just words, which is why
    # they cannot go in the shared list.
    ("two-wheeler", "auto_loan"),
    ("vehicle", "auto_loan"),
    ("loan against", "personal_loan"),
    ("business loan", "personal_loan"),
    ("charge card", "credit_card"),
)


def map_account_type(raw: str) -> str:
    """Bureau account-type wording -> this app's AccountType values.

    Everything a bureau calls a loan really is one; the distinction that
    matters downstream is only ever card vs loan vs other.
    """
    lowered = (raw or "").lower()
    for fragment, mapped in _BUREAU_ONLY_TYPES:
        if fragment in lowered:
            return mapped

    from ..normalize.metadata import detect_account_type
    detected = detect_account_type(lowered)
    return detected.value if detected else "unknown"


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


def number_suffix(masked: str) -> str:
    """The last four digits of an account number, or "".

    Bureaus mask differently from statements - XXXXXX1234, ****1234, or the
    full number - and the trailing digits are the only part that survives both.

    Shared with the statement and alert readers, because this is the key the
    three of them join on. Three implementations of it was three chances for a
    match to fail with nothing to show for it.
    """
    return formats.last_four(masked)


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
    "sanctioned": ("sanctioned amount", "credit limit", "high credit",
                   "sanctioned", "loan amount", "disbursed amount",
                   "disbd amt/high credit"),
    "balance": ("current balance", "outstanding balance", "balance",
                "current outstanding", "amount outstanding"),
    "overdue": ("amount overdue", "overdue amount", "overdue", "past due",
                "amount past due"),
    "emi": ("emi amount", "emi", "installment amount", "instalment amount"),
    "ownership": ("ownership", "account holder", "ownership type"),
}


#: Labels this reader does not want, but must still recognise. A value runs
#: until the next label starts, so a label that is not in the list is not a
#: boundary - and "Account #:" swallowed "Info. as of: 09-08-2026", making the
#: last four digits of every account on the report "2026".
_BOUNDARY_ONLY: tuple[str, ...] = (
    "info. as of", "info as of", "disbursed date", "disbd amt/high credit",
    "last payment date", "cash limit", "closed date", "last paid amt",
    "instlamt/freq", "tenure(month)", "tenure", "principal writeoff amt",
    "settlement amt", "total writeoff amt", "payment history/asset classification",
    # "credit grantor" is deliberately absent: it is a real label, and listing
    # it here (even with a trailing colon) makes the longer boundary-only form
    # win the match, so the lender is never captured at all.
    "payment history", "asset classification", "reported on",
    "date reported", "suit filed", "collateral", "interest rate",
)

#: Every label the readers below understand, longest first so "account type"
#: is preferred over a bare "account" when both could match at one position.
_ALL_LABELS: list[tuple[str, str]] = sorted(
    ([(alias, field) for field, aliases in _LABELS.items() for alias in aliases]
     + [(alias, "") for alias in _BOUNDARY_ONLY]),
    key=lambda pair: len(pair[0]), reverse=True)

#: The separator is REQUIRED, not optional. Without it a bare alias matches
#: inside a value - "Account Status: Closed" was read as the label "closed",
#: which truncated the status to empty and then reported a closed account as
#: open. Every real label on these reports is followed by a colon.
def _spaced(alias: str) -> str:
    """An alias that still matches when the PDF wedged spaces inside it.

    The extractor lays glyphs out individually and preserves the gaps, so
    "Info. as of:" arrives as "I nfo. as of:". Matching the literal alias
    missed it, which meant it never acted as a boundary - and the account
    number before it swallowed the words and the date, leaving every account
    on the report with a "number" of 2026.
    """
    return r"\s*".join(re.escape(ch) for ch in alias if not ch.isspace())


_LABEL_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(_spaced(a) for a, _ in _ALL_LABELS)
    + r")\s*[:#]\s*", re.IGNORECASE)

#: Keyed without spaces, to match what the regex hands back after a
#: split-word alias is squeezed.
_ALIAS_TO_FIELD = {re.sub(r"\s+", "", alias).lower(): field
                   for alias, field in _ALL_LABELS}


def fields_in(line: str) -> list[tuple[str, str]]:
    """Every (field, value) pair on one line.

    CRIF packs four fields onto a single line -

        Account Type: CREDIT CARD Credit Grantor: HSBC Account #: 2612 Info...

    - so splitting on the first colon and taking the rest as the value reads
    one field and destroys three. Values are instead bounded by wherever the
    NEXT label begins, which is what makes a multi-field line readable at all.
    CIBIL's one-field-per-line layout is just the degenerate case.
    """
    matches = list(_LABEL_RE.finditer(line))
    if not matches:
        return []
    out: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        squeezed = re.sub(r"\s+", "", match.group(1)).lower()
        field = _ALIAS_TO_FIELD.get(squeezed)
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        if not field:
            # A boundary-only label: it ends the previous value and starts
            # nothing. Skipped after the bound has been taken, not before.
            continue
        value = line[match.end():stop].strip(" :\t")
        out.append((field, value))
    return out


def _label_for(line: str) -> tuple[str | None, str]:
    """The first labelled field on a line, or (None, "")."""
    found = fields_in(line)
    return found[0] if found else (None, "")


_DPD = re.compile(r"\b(?:000|std|xxx|\d{1,3})\b", re.IGNORECASE)
#: What starts a new account block. Two shapes, because the bureaus disagree:
#: CIBIL heads each block with a line of its own ("ACCOUNT DETAILS 2"), while
#: CRIF opens with an index and the first field on the same line ("2 Account
#: Type: CREDIT CARD ..."). Matching only the first silently read a CRIF report
#: as one enormous account and then discarded it.
_ACCOUNT_BREAK = re.compile(
    r"^\s*(?:account\s*(?:details|information)?\s*[-#:]?\s*\d*|"
    r"credit\s*facility|\d+\.\s*(?:credit card|loan))\s*$", re.IGNORECASE)

_NUMBERED_ACCOUNT = re.compile(r"^\s*(\d{1,3})\s+account\s*type\s*:",
                               re.IGNORECASE)


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



#: A lender's name is printed in capitals and usually carries a corporate
#: suffix. Requiring that stops the "the name is on the next line" fallback
#: from adopting whatever prose happens to follow - which is how three
#: accounts came back owed to "s and the same is up to date as".
_LENDER_SHAPE = re.compile(
    r"\b(bank|ltd|limited|pvt|private|corp|finance|financial|nbfc|"
    r"housing|capital|card|services|fintech|india)\b", re.IGNORECASE)


def _looks_like_a_lender(name: str) -> bool:
    if not name or len(name) < 4 or name.isdigit():
        return False
    letters = [c for c in name if c.isalpha()]
    if not letters:
        return False
    mostly_capitals = sum(c.isupper() for c in letters) / len(letters) > 0.8
    return mostly_capitals or bool(_LENDER_SHAPE.search(name))


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

    pending_grantor = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        numbered = _NUMBERED_ACCOUNT.match(line)
        if _ACCOUNT_BREAK.match(line) or numbered:
            flush()
            current = BureauAccount()
            if not numbered:
                continue

        found = fields_in(line)
        if not found:
            if current is None:
                continue
            # CRIF leaves "Credit Grantor:" empty and prints the lender on the
            # next line, followed by the account number. Taking the words
            # before that first long digit run recovers the name.
            if pending_grantor and not current.lender:
                # CRIF leaves both "Credit Grantor:" and "Account #:" empty on
                # the header line and prints them together on the next one:
                #
                #   THE HONGKONG AND SHANGHAI BANKING CORP INDIA LTD 2612596264
                #
                # Taking only the name threw the number away, leaving most
                # accounts with nothing to match a ledger account against.
                trailing = re.search(r"^(.*?)\s+(\d{6,})\s*$", line)
                name = (trailing.group(1) if trailing
                        else re.split(r"\s\d{6,}", line)[0]).strip()
                if _looks_like_a_lender(name):
                    current.lender = name
                    if trailing and not current.account_number_masked:
                        current.account_number_masked = trailing.group(2)
                    pending_grantor = False
                    continue
            if _looks_like_dpd_row(line):
                current.dpd_history += _DPD.findall(line)
            continue

        if current is None:
            current = BureauAccount()

        for name, value in found:
            if name == "account_number" and current.account_number_masked \
                    and not numbered:
                flush()
                current = BureauAccount()

            if name == "lender":
                if value:
                    current.lender = value
                    pending_grantor = False
                else:
                    pending_grantor = True
            elif name == "account_type":
                current.account_type = map_account_type(value)
            elif name == "account_number":
                current.account_number_masked = value
            elif name == "opened":
                current.opened_on = parse_date(value)
            elif name == "closed":
                closed = parse_date(value)
                if closed:
                    current.closed_on = closed
                    current.status = "closed"
            elif name == "status":
                current.status = _normalise_status(value)
            elif name == "sanctioned":
                amount = parse_money(value)
                if amount is not None:
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

        # "ACTIVE" / "CLOSED" often sits alone on the line after the header.
        if current and re.fullmatch(r"(ACTIVE|CLOSED|WRITTEN OFF|SETTLED)",
                                    line.strip(), re.IGNORECASE):
            current.status = _normalise_status(line)

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
