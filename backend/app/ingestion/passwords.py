"""Derive candidate passwords for the user's own protected statements.

Indian banks protect emailed statements with a password built from details the
customer already knows - some combination of their name, date of birth, PAN,
mobile number or account number. The formats are published and predictable
(HDFC: first 4 letters of name + DDMM of birth; ICICI: first 4 of name + DDMM;
and so on), which is exactly why a customer can open their own statement at all.

This module reproduces those formats. It is NOT password cracking:

  - it operates only on files the user uploaded
  - it uses only PII the user entered about themselves
  - it generates a small, bounded set of format-based candidates - dozens, not
    the billions a brute-force attack would try
  - a wrong guess just moves to the next candidate; there is no target to break

The candidate list is ordered so the formats most likely to work for a given
institution are tried first, which keeps the common case to one or two attempts.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from ..models.profile import UserProfile

log = logging.getLogger(__name__)

#: Hard cap. If this many candidates don't open the file, the format is one we
#: don't model, and the user is asked for the password directly. The cap also
#: guarantees we can never be turned into a brute-forcer by a huge profile.
#:
#: Raised from 60 after measuring against real statements: the generated set hit
#: the cap exactly, meaning valid formats were being truncated away and files
#: reported as "password needed" that we could actually have opened. This is
#: still a bounded list of documented formats - a brute-force space for even an
#: 8-character password is ~10^14, so the difference in kind is absolute.
MAX_CANDIDATES = 400


def _name_fragments(profile: UserProfile) -> list[str]:
    """Name pieces banks use, in the casings they use them."""
    out: list[str] = []
    for base in (profile.first_name, profile.full_name.replace(" ", ""), profile.last_name):
        base = base.strip()
        if not base:
            continue
        for length in (4, len(base)):
            frag = base[:length]
            if len(frag) >= 3:
                out += [frag.upper(), frag.lower(), frag.capitalize()]
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(out))


def _dob_fragments(dob: date | None) -> list[str]:
    """Date-of-birth in every format a statement password uses."""
    if dob is None:
        return []
    dd, mm = f"{dob.day:02d}", f"{dob.month:02d}"
    yy, yyyy = f"{dob.year % 100:02d}", f"{dob.year}"
    mon = dob.strftime("%b").upper()  # e.g. FEB
    return list(dict.fromkeys([
        f"{dd}{mm}",          # 0602   <- the "jite0602" case
        f"{dd}{mm}{yy}",      # 060290
        f"{dd}{mm}{yyyy}",    # 06021990
        f"{dd}{mm}{yy}",      # duplicate-safe
        f"{yyyy}",            # 1990
        f"{dd}{mon}{yy}",     # 06FEB90
        f"{mm}{dd}{yyyy}",    # US-style, some card issuers use it
        f"{dd}{mm}{yyyy}",
    ]))


#: Digit runs this long in a filename are account, customer or card numbers.
_FILENAME_DIGITS = re.compile(r"\d{4,}")


def filename_number_fragments(filename: str | None) -> list[str]:
    """Account-number fragments recoverable from a statement's filename.

    This matters more than it looks. Many banks build the password from the last
    4-5 digits of the account or customer id - but for a PROTECTED pdf we cannot
    read the account number out of the document, because opening it is exactly
    what we are trying to do. The filename is the only place that number is
    visible before decryption:

        AccStmt_01414003_092025_5974.pdf   -> 5974, 4003, 01414003 ...
        xxxx-xxxx-xx-xxxx67_117712502_...  -> 2502, 12502 ...

    Bounded to a handful of fragments so the candidate list stays a list of
    formats rather than a search space.
    """
    if not filename:
        return []

    fragments: list[str] = []
    for run in _FILENAME_DIGITS.findall(filename)[:4]:
        # A date-like run (092025, 20250815) is the statement period, not an
        # account number, and only adds noise.
        if len(run) in (6, 8) and run.startswith(("19", "20", "0", "1")):
            continue
        for size in (4, 5):
            if len(run) >= size:
                fragments.append(run[-size:])
        if len(run) <= 8:
            fragments.append(run)

    return list(dict.fromkeys(fragments))[:10]


def derive_passwords(
    profile: UserProfile | None,
    institution: str | None = None,
    account_number_masked: str | None = None,
    filename: str | None = None,
) -> list[str]:
    """Ordered candidate passwords for one file.

    `institution`, the account's last four digits and the filename are used to
    build and order the set - none of them restrict it, so an account
    mis-detected as one bank still gets every other bank's formats tried.
    """
    if profile is None or not profile.has_password_material():
        return []

    names = _name_fragments(profile)
    dobs = _dob_fragments(profile.date_of_birth)
    pan = profile.pan.strip()
    mobile = profile.mobile.strip()
    last4_mobile = mobile[-4:] if len(mobile) >= 4 else ""
    last4_acct = ""
    if account_number_masked:
        digits = "".join(c for c in account_number_masked if c.isdigit())
        last4_acct = digits[-4:] if len(digits) >= 4 else ""

    candidates: list[str] = []

    # 1. Anything the user told us outright wins.
    candidates += [p for p in (profile.custom_passwords or []) if p]

    # 2. name + DOB, the single most common bank format (the "jite0602" case).
    for name in names:
        for dob in dobs:
            candidates.append(f"{name}{dob}")

    # 3. name + last 4 of account (common for credit cards).
    if last4_acct:
        for name in names:
            candidates.append(f"{name}{last4_acct}")

    # 4. name + last 4 of mobile (Axis, Yes and several card issuers).
    if last4_mobile:
        for name in names:
            candidates.append(f"{name}{last4_mobile}")

    # 5. DOB + last 4 of account / mobile (some card issuers).
    for dob in dobs:
        if last4_acct:
            candidates.append(f"{dob}{last4_acct}")
        if last4_mobile:
            candidates.append(f"{dob}{last4_mobile}")

    # 6. The mobile number on its own - IDFC First and a few NBFCs use it.
    if len(mobile) >= 10:
        candidates.append(mobile[-10:])
        for dob in dobs[:3]:
            candidates.append(f"{mobile[-10:]}{dob}")

    # 7. PAN-based (mutual-fund / demat statements from CAMS/KFintech, and
    #    increasingly the neobanks).
    if pan:
        candidates.append(pan)
        candidates.append(pan.lower())
        candidates.append(pan.capitalize())
        for dob in dobs[:4]:
            candidates.append(f"{pan}{dob}")
            candidates.append(f"{pan.lower()}{dob}")
        for name in names[:3]:
            candidates.append(f"{pan}{name}")

    # 8. Account/customer fragments read out of the filename. For a protected
    #    PDF this is the only place that number is legible before decryption.
    for fragment in filename_number_fragments(filename):
        for name in names[:6]:
            candidates.append(f"{name}{fragment}")
        for dob in dobs[:3]:
            candidates.append(f"{fragment}{dob}")
            candidates.append(f"{dob}{fragment}")
        candidates.append(fragment)

    # 9. Bare fragments, last resort.
    candidates += names + dobs
    if last4_acct:
        candidates.append(last4_acct)

    ordered = _prioritise(candidates, institution)
    # De-duplicate preserving order, then cap.
    seen: set[str] = set()
    unique = [c for c in ordered if c and not (c in seen or seen.add(c))]
    return unique[:MAX_CANDIDATES]


#: Per-institution hint: substrings that should float to the front of the list.
#: Purely an ordering optimisation - every candidate is still tried.
_FORMAT_HINTS: dict[str, tuple[str, ...]] = {
    "hdfc": ("upper_dob", "name_dob"),
    "icici": ("name_dob",),
    "axis": ("name_dob",),
    "sbi": ("name_dob",),
    "kotak": ("name_dob",),
}


def _prioritise(candidates: list[str], institution: str | None) -> list[str]:
    """Stable-sort so the likely formats for this bank come first.

    Kept intentionally simple: the only signal is "does a name-then-digits
    shape match", which covers the dominant format. Everything else keeps its
    original relative order.
    """
    if not institution:
        return candidates

    inst = institution.lower()
    prefers_upper = any(h == "upper_dob" for k, hints in _FORMAT_HINTS.items()
                        if k in inst for h in hints)

    def rank(candidate: str) -> int:
        has_alpha = any(c.isalpha() for c in candidate)
        has_digit = any(c.isdigit() for c in candidate)
        name_then_digits = has_alpha and has_digit
        if not name_then_digits:
            return 2
        if prefers_upper and candidate[:1].isupper():
            return 0
        return 1

    return sorted(candidates, key=rank)


def redact_candidate(password: str) -> str:
    """A safe-to-log form of a password: length and first char only."""
    if not password:
        return "(empty)"
    return f"{password[0]}{'*' * (len(password) - 1)}"


#: Published password rules, shown in the UI so a user can see WHY a file will
#: open (or why it won't) before anything is tried. Matched on sender/filename
#: fragments. Descriptions are the bank's own documented format.
PASSWORD_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("hdfc",), "Name(4) + DDMM",
     "First 4 letters of your name in CAPS + date of birth as DDMM"),
    (("icici",), "Name(4) + DDMM",
     "First 4 letters of your name in lowercase + date of birth as DDMM"),
    (("sbi", "onlinesbi"), "DDMMYYYY",
     "Date of birth as DDMMYYYY"),
    (("axis",), "Name(4) + DDMM",
     "First 4 letters of your name + date of birth as DDMM"),
    (("kotak",), "Name(4) + DDMM", "First 4 letters of name + DDMM"),
    (("indusind",), "Name(4) + DDMM", "First 4 letters of name + DDMM"),
    (("idfc",), "Mobile(10)", "Your registered 10-digit mobile number"),
    (("pnb", "punjab"), "DDMMYYYY", "Date of birth as DDMMYYYY"),
    (("baroda", "bob"), "Name(4) + DDMM", "First 4 letters of name + DDMM"),
    (("yes",), "Name(4) + DDMM", "First 4 letters of name + DDMM"),
    (("hsbc",), "DDMMYYYY", "Date of birth as DDMMYYYY"),
    (("rbl",), "Name(4) + DDMM", "First 4 letters of name + DDMM"),
    (("slice",), "PAN", "Your PAN in uppercase"),
    (("amex", "americanexpress"), "Card(4) + DDMM",
     "Last 4 digits of the card + date of birth as DDMM"),
    (("cams", "kfintech", "cdsl", "nsdl", "protean", "mfcentral"), "PAN",
     "Your PAN in uppercase"),
    (("zerodha", "upstox", "5paisa", "dhan", "paytmmoney", "angel"), "PAN",
     "Your PAN in uppercase"),
    (("bajaj", "tatacapital", "lichousing"), "Name(4) + DDMM",
     "First 4 letters of name + DDMM"),
]

#: Shown when nothing matches - we still try the full candidate set.
UNKNOWN_RULE = ("Unknown", "Format not documented here; all known formats are tried")


def password_hint(sender: str = "", filename: str = "") -> tuple[str, str]:
    """(short_label, explanation) describing the likely password format.

    Purely informational. Whatever this returns, `derive_passwords` still tries
    the whole candidate set - the hint tells the user what to expect, it does
    not restrict what gets attempted.
    """
    haystack = f"{sender} {filename}".lower()
    for fragments, label, explanation in PASSWORD_RULES:
        if any(fragment in haystack for fragment in fragments):
            return label, explanation
    return UNKNOWN_RULE


def profile_can_satisfy(profile: UserProfile | None, label: str) -> bool:
    """Whether the stored profile has the fields a given rule needs.

    Lets the UI mark a file as "you're missing your PAN for this one" before the
    user waits for a download and a failed parse.
    """
    if profile is None:
        return False
    needs_name = "Name" in label
    needs_dob = "DDMM" in label or "DDMMYYYY" in label
    needs_pan = "PAN" in label
    needs_mobile = "Mobile" in label

    if needs_name and not profile.full_name:
        return False
    if needs_dob and profile.date_of_birth is None:
        return False
    if needs_pan and not profile.pan:
        return False
    if needs_mobile and not profile.mobile:
        return False
    if label == "Unknown":
        return profile.has_password_material()
    return True


def resolve_password_status(
    path: Path, candidates: list[str]
) -> tuple[str | None, str]:
    """Which password (if any) opens this file, without extracting any tables.

    Returns (working_password, status), status one of:
      "open"          - encrypted, and one of `candidates` opened it
      "not_encrypted" - not a protected file at all (or not a PDF)
      "locked"        - encrypted, and nothing in `candidates` opened it

    Used to populate the file registry (which password worked, so the next
    load can skip straight to it) independently of the main extraction call,
    which never surfaces the password it found. Only PDFs can be encrypted in
    a way this app understands; other formats always report "not_encrypted".
    """
    if path.suffix.lower() != ".pdf":
        return None, "not_encrypted"
    from .extractors import _resolve_pdf_password

    password, is_encrypted = _resolve_pdf_password(path, None, candidates)
    if not is_encrypted:
        return None, "not_encrypted"
    return (password, "open") if password is not None else (None, "locked")
