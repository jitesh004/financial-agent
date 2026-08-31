"""Mine account metadata out of the free text surrounding a statement table.

Institution, account type, masked account number, statement period and the
declared opening/closing balances all live in the letterhead rather than the
table. The opening/closing figures matter most: without them the reconciliation
gate has nothing to check against, and every downstream number becomes a guess.

Everything here is best-effort and returns None rather than guessing wildly. A
missing field degrades the analysis; a wrong field corrupts it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from ..models.schemas import AccountType
from .parsers import parse_amount, parse_date

#: Institution name fragments -> canonical display name.
INSTITUTIONS: dict[str, str] = {
    "hdfc": "HDFC Bank",
    "icici": "ICICI Bank",
    "state bank of india": "State Bank of India",
    "sbi": "State Bank of India",
    "axis": "Axis Bank",
    "kotak": "Kotak Mahindra Bank",
    "yes bank": "Yes Bank",
    "yesbank": "Yes Bank",
    "yes.bank": "Yes Bank",
    "indusind": "IndusInd Bank",
    "idfc": "IDFC First Bank",
    "punjab national": "Punjab National Bank",
    "bank of baroda": "Bank of Baroda",
    "canara": "Canara Bank",
    "union bank": "Union Bank of India",
    "citibank": "Citibank",
    "standard chartered": "Standard Chartered",
    "american express": "American Express",
    "amex": "American Express",
    "bajaj": "Bajaj Finserv",
    "tata capital": "Tata Capital",
    "lic housing": "LIC Housing Finance",
    "cred.club": "CRED",
    "paytm": "Paytm",
    "phonepe": "PhonePe",
    "razorpay": "Razorpay",
    "zerodha": "Zerodha",
    "groww": "Groww",
    "cams": "CAMS",
    "kfintech": "KFintech",
    # Added after real statements showed up under a raw email address or, worse,
    # the wrong bank. Fragments must work both in statement TEXT ("HSBC India")
    # and in a sender domain ("mail.hsbc.co.in").
    "hsbc": "HSBC",
    "rbl": "RBL Bank",
    "slice": "slice",
    "bobcard": "BOBCARD",
    "idfc first": "IDFC First Bank",
    "idfcfirst": "IDFC First Bank",
    "au small": "AU Small Finance Bank",
    "aubank": "AU Small Finance Bank",
    "bandhan": "Bandhan Bank",
    "federal bank": "Federal Bank",
    "federalbank": "Federal Bank",
    "dbs": "DBS Bank",
    "onecard": "OneCard",
    "sbicard": "SBI Card",
    "dhan": "Dhan",
    "upstox": "Upstox",
    "5paisa": "5paisa",
    "angelone": "Angel One",
    "paytmmoney": "Paytm Money",
    "protean": "Protean NPS",
}

#: Card product names, curated the same way as INSTITUTIONS above: a fragment
#: found in the statement's own letterhead, mapped to its canonical display
#: form. Covers the cards actually seen across HDFC/ICICI/Axis/SBI/HSBC/
#: IDFC First plus common co-brand cards - extend this list as new ones show
#: up, the same way INSTITUTIONS grew. Longest fragment wins (checked in
#: `detect_card_variant`), so "diners club" beats a bare "diners", and
#: "amazon pay" beats a coincidental bare "amazon".
CARD_VARIANTS: dict[str, str] = {
    # Axis
    "rewards": "Rewards", "privilege": "Privilege", "select": "Select",
    "magnus": "Magnus", "ace": "Ace", "flipkart": "Flipkart",
    "vistara": "Vistara", "neo": "Neo",
    # HDFC
    "regalia": "Regalia", "millennia": "Millennia", "infinia": "Infinia",
    "moneyback": "MoneyBack", "freedom": "Freedom", "diners club": "Diners Club",
    "diners": "Diners Club", "tata neu": "Tata Neu", "swiggy": "Swiggy",
    "pixel": "Pixel", "biz power": "BizPower",
    "marriott bonvoy": "Marriott Bonvoy", "bonvoy": "Marriott Bonvoy",
    # ICICI
    "amazon pay": "Amazon Pay", "coral": "Coral", "rubyx": "Rubyx",
    "sapphiro": "Sapphiro", "emeralde": "Emeralde", "hpcl": "HPCL",
    # SBI
    "simplyclick": "SimplyCLICK", "simplysave": "SimplySAVE",
    "prime": "Prime", "elite": "Elite", "cashback": "Cashback",
    # HSBC
    "travelone": "TravelOne", "travel one": "TravelOne",
    "premier": "Premier", "visa signature": "Visa Signature",
    "platinum": "Platinum",
    # IDFC First / others
    "wealth": "Wealth", "millennia first": "Millennia First",
    "eterna": "Eterna", "atlas": "Atlas", "ixigo": "ixigo",
}


def detect_card_variant(text: str) -> str | None:
    """The card's own product name - "Rewards", "Regalia" - from its
    letterhead. Returns None rather than guessing when nothing matches;
    an absent variant just falls back to the plain institution name."""
    lowered = (text or "").lower()
    best: tuple[int, str] | None = None
    for fragment, name in CARD_VARIANTS.items():
        if fragment in lowered and (best is None or len(fragment) > best[0]):
            best = (len(fragment), name)
    return best[1] if best else None


#: ICICI's own backend names each statement PDF after the card product it
#: belongs to (".../Retail_Amazon_NORM.pdf", ".../Retail_HPCL_NORM.pdf",
#: ".../Retail_Coral_NORM.pdf"), but the page content has no equivalent plain
#: text: the logo area in the top-left decodes as unmapped (cid:NNN) glyphs
#: instead of readable text on every ICICI template checked, and unlike Axis
#: or HSBC the product name is never repeated in plain words anywhere in the
#: letterhead either. The filename is the only signal that survives text
#: extraction at all for this issuer - confirmed against every ICICI credit
#: card statement in a real mailbox (HPCL, Amazon Pay, Coral: three separate
#: cards, one filename family each, no cross-contamination between them).
_ICICI_FILENAME_VARIANTS: dict[str, str] = {
    "amazon": "Amazon Pay", "hpcl": "HPCL", "coral": "Coral",
    "rubyx": "Rubyx", "sapphiro": "Sapphiro", "emeralde": "Emeralde",
}


def detect_card_variant_from_filename(filename: str) -> str | None:
    """ICICI-only fallback for `detect_card_variant` - see the dict above."""
    m = re.search(r"Retail_([A-Za-z]+)_NOR", filename or "", re.IGNORECASE)
    if not m:
        return None
    return _ICICI_FILENAME_VARIANTS.get(m.group(1).lower())


#: Ordered most-specific first: "home loan" must beat a bare "loan".
ACCOUNT_TYPE_PATTERNS: list[tuple[str, AccountType]] = [
    (r"\bhome\s+loan\b|\bhousing\s+loan\b|\bmortgage\b", AccountType.HOME_LOAN),
    (r"\bpersonal\s+loan\b|\bconsumer\s+loan\b", AccountType.PERSONAL_LOAN),
    (r"\bauto\s+loan\b|\bcar\s+loan\b|\bvehicle\s+loan\b|\btwo.wheeler\s+loan\b",
     AccountType.AUTO_LOAN),
    (r"\bcredit\s+card\b|\bcard\s+statement\b|\bcard\s+number\b|\bpayment\s+due\s+date\b",
     AccountType.CREDIT_CARD),
    (r"\bmutual\s+fund\b|\bfolio\b|\bnav\b|\bdemat\b|\bportfolio\b|\bsip\b|"
     r"\bconsolidated\s+account\s+statement\b|\bunits?\s+held\b", AccountType.INVESTMENT),
    (r"\bsavings\s+account\b|\bsavings\b|\bsb\s+a/?c\b", AccountType.SAVINGS),
    (r"\bcurrent\s+account\b|\bca\s+a/?c\b", AccountType.CURRENT),
    (r"\bwallet\b|\bprepaid\b", AccountType.WALLET),
    (r"\bloan\s+account\b|\bloan\b", AccountType.PERSONAL_LOAN),
]

#: Separator between a label and its value. Must NOT include \n: a bare \s*
#: lets a label at the end of a line swallow the whole next line, which turns
#: a "Closing Balance" column header into a match against the first data row.
_LABEL_VALUE = r"[:\-\t ]*"


@dataclass
class StatementMetadata:
    institution: str | None = None
    account_type: AccountType | None = None
    account_number_masked: str | None = None
    #: The card's own product name - "Rewards", "Privilege", "Regalia" - as
    #: opposed to `institution`, which only says which bank issued it. A user
    #: with three Axis cards needs this to tell them apart in the UI; it also
    #: doubles as a fallback identity key for issuers (HSBC) that mask their
    #: card number so completely no digit survives text extraction at all.
    product_name: str | None = None
    holder_name: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    credit_limit: Decimal | None = None
    interest_rate: Decimal | None = None
    emi_amount: Decimal | None = None
    currency: str = "INR"
    notes: list[str] = field(default_factory=list)


#: Labels whose masked value identifies the PERSON or the document, never the
#: account: one customer ID covers every account a bank holds for you.
_IDENTITY_NOT_ACCOUNT = re.compile(
    r"\b(?:customer\s*id|cust\.?\s*id|ckyc(?:\s*id)?|crn|relationship\s*(?:no\.?|number)"
    r"|customer\s*(?:no\.?|number))\s*[:\-]?\s*$",
    re.IGNORECASE,
)


#: A qualifier that demotes the label following it. "Alternate Account Number"
#: names a real field, but not the one that identifies the account.
_SUPERSEDED_LABEL = re.compile(
    r"\b(?:alternate|alternative|secondary|previous|old|linked|former"
    r"|reference|ref\.?)\s*$",
    re.IGNORECASE,
)


def _find_labeled(text: str, labels: list[str]) -> str | None:
    """Find 'Label: value' anywhere in the text, tolerant of layout noise.

    Handles both 'Opening Balance: 1,234.00' and the table-ish
    'Opening Balance      1,234.00' produced by PDF text extraction.
    """
    for label in labels:
        pattern = rf"{label}{_LABEL_VALUE}([^\n\r]{{1,80}})"
        for m in re.finditer(pattern, text, re.IGNORECASE):
            if _SUPERSEDED_LABEL.search(text[max(0, m.start() - 24):m.start()]):
                # "Alternate Account Number" is not the account number. HDFC's
                # Marriott card statement prints both, one line apart:
                #
                #     Credit Card No. 00361147XXXX6885
                #     Alternate Account Number 0001015980001716889
                #
                # and this function tries its LABELS in order, not the text in
                # order - so the generic "account number" pattern matched the
                # alternate one before "card number" was ever tried. The card
                # was filed as XXXX6889, its fifteen transaction alerts said
                # 6885, and every one of them was refused for belonging to an
                # account that did not exist.
                continue
            value = m.group(1).strip()
            # Trim at the first cell boundary: a tab, a run of spaces, or the
            # start of the next "Label:" token.
            value = re.split(r"\t|\s{3,}|\s+(?=[A-Z][A-Za-z ]{2,}\s*:)", value)[0].strip()
            if value:
                return value
    return None


#: Words that turn the bank named after them into a LANDMARK rather than the
#: issuer. A great many Indian addresses are written this way, and a statement
#: prints the cardholder's address above the issuer's own name:
#:
#:     M3 KALSAGAR SHRI RAM COLONY
#:     ALANDI ROAD BHOSARI
#:     OPP STATE BANK OF INDIA        <- not who issued this card
#:     MAHARASHTRA, PUNE 411039
#:
#: That address is the whole of the letterhead slice institution detection
#: reads, so an ICICI card statement was filed under State Bank of India, and
#: every figure on it was attributed to a bank the user does not hold a card
#: with.
_LANDMARK_BEFORE = re.compile(
    r"\b(?:opp|opps|oppo|opposite|near|nr|behind|beside|besides|adj|adjacent"
    r"|above|below|next\s+to|in\s+front\s+of|back\s+of|infront\s+of)\b"
    r"[\s.,:-]*$"
)


def detect_institution(text: str) -> str | None:
    # Underscores and hyphens are word characters, so "\bhdfc\b" does not match
    # inside "hdfc_savings_2025.csv". Filenames are one of the best institution
    # signals we have, so their separators are flattened to spaces first.
    lowered = re.sub(r"[_\-/]+", " ", text.lower())
    best: tuple[int, int, int, str] | None = None
    for fragment, name in INSTITUTIONS.items():
        hits = [
            m for m in re.finditer(rf"\b{re.escape(fragment)}\b", lowered)
            # "Opp State Bank of India" is where the user lives, not who they
            # bank with. Only the 30 characters before the name are examined,
            # which is enough for a preposition and its punctuation.
            if not _LANDMARK_BEFORE.search(lowered[max(0, m.start() - 30):m.start()])
        ]
        if not hits:
            continue
        # How OFTEN a bank is named decides it, and only then how early.
        #
        # Position alone read an ICICI card statement as State Bank of India,
        # because the cardholder's address is printed above the issuer's own
        # name and reads:
        #
        #     M3 KALSAGAR SHRI RAM COLONY
        #     ALANDI ROAD BHOSARI
        #     OPP STATE BANK OF INDIA
        #
        # Naming a nearby branch as a landmark is how a great many Indian
        # addresses are written, so the first bank mentioned on a statement is
        # routinely not the one that issued it. The issuer, by contrast, is
        # everywhere: its GST number, its registered office, its app. Four
        # mentions against one.
        #
        # Earliest mention still breaks a tie, since a letterhead does lead
        # the document; a longer fragment breaks the next one, so "bank of
        # baroda" beats a stray "bank".
        key = (-len(hits), hits[0].start(), -len(fragment), name)
        if best is None or key < best:
            best = key
    return best[3] if best else None


def detect_account_type(text: str, filename: str = "") -> AccountType | None:
    """Classify the account this statement belongs to.

    Checked in order of trustworthiness: the filename, then the letterhead.
    Body text is deliberately excluded by the caller - a savings statement is
    full of narrations like "HOME LOAN EMI" that describe money leaving for a
    different account entirely, and matching those flips the whole statement to
    the wrong type.
    """
    name = re.sub(r"[_\-]+", " ", filename).lower()
    for pattern, account_type in ACCOUNT_TYPE_PATTERNS:
        if re.search(pattern, name):
            return account_type
    for pattern, account_type in ACCOUNT_TYPE_PATTERNS:
        if re.search(pattern, text.lower()):
            return account_type
    return None


def detect_account_number(text: str, labeled_only: bool = False) -> str | None:
    """Return a masked account/card number - never the full value.

    We surface the last four digits only, which is enough for a user to tell
    two accounts apart and useless to anyone who intercepts the database.

    `labeled_only` stops before the unlabelled fallback, so a caller can ask
    the strong question ("does this document SAY which card it is?") without
    getting the weak answer ("here is a number that looked like one").
    """
    # Amex: XXXX-XXXXXX-31004
    amex_match = re.search(r"[X*]{4}-[X*]{6}-(\d{5})", text, re.IGNORECASE)
    if amex_match:
        return f"XXXX{amex_match.group(1)[-4:]}"

    # Deliberately NOT "customer id": one customer ID spans every account the
    # bank holds for you, so it identifies the person, not the account. ICICI
    # prints the account number plainly on some months and only "Cust ID" on
    # others; reading the ID as an account number filed half the salary
    # account's statements under XXXX9341 and half under XXXX1951, which
    # double-counted every overlapping month. Returning None is the safer
    # failure - the caller then falls back to the masked number printed in the
    # summary block, and failing that to institution + account type.
    # The CARD number is tried first, and always beats a plain account number.
    # `_find_labeled` tries its labels in the order given and returns the first
    # that matches, so this order is the rule.
    #
    # HDFC's Marriott card statement prints both, one line apart:
    #
    #     Credit Card No. 00361147XXXX6885
    #     Alternate Account Number 0001015980001716889
    #
    # With the generic label first, the card was filed as XXXX6889 - a number
    # that identifies something else entirely. Its fifteen transaction alerts
    # all said 6885 and every one of them was refused, for belonging to an
    # account that did not exist.
    labeled = _find_labeled(text, [
        r"card\s*(?:number|no\.?|#)",
        r"account\s*(?:number|no\.?|#)", r"a/?c\s*(?:number|no\.?)",
        r"loan\s*account\s*(?:number|no\.?)",
        r"folio\s*(?:number|no\.?)", r"membership\s*(?:number|no\.?)",
    ])
    if labeled:
        # Take the digits of a single account-shaped TOKEN, not every digit in
        # the window. Sweeping up the whole window turned ICICI's "Statement
        # 2025MTH08 341562729" into account XXXX2025 for one half of the year
        # and XXXX2026 for the other, splitting one salary account in two. X
        # and * count as part of the token so a pre-masked "XXXXXXXX1234"
        # still qualifies.
        token = re.search(
            # Excludes a digit run introduced by "to"/"at" - one ICICI
            # template's SMS-blocking instructions read "<YourCreditCard
            # number> to 9215676766 from your registered mobile number", and
            # "card number" there is a placeholder name, not a real label:
            # the actual value is a support phone number that happens to be
            # the right shape to pass as a masked account number otherwise.
            r"(?<![\dA-Za-z])(?<!to )(?<!at )([\dX*]{6,}(?:[\s-][\dX*]{2,})*)(?![\dA-Za-z])",
            labeled, re.IGNORECASE,
        )
        if token:
            digits = re.sub(r"\D", "", token.group(1))
            if len(digits) >= 4:
                return f"XXXX{digits[-4:]}"
        # Alphanumeric loan refs like HL4471929 have no long digit run. The
        # digit is what makes it a reference: without that check the pattern
        # matched any capitalised word, producing accounts called CREDIT,
        # CONTACT and INANCIAL (the tail of "financial").
        ref = re.search(r"\b([A-Z]{2,}[\dA-Z]{4,})\b", labeled.upper())
        if ref and any(c.isdigit() for c in ref.group(1)):
            return ref.group(1)[-8:]

    if labeled_only:
        return None

    # Unlabeled fallback: a masked card number printed on its own, in either
    # shape a real statement uses. "XXXX XXXX XXXX 1234" masks from the start;
    # several issuers (Axis, and one of HDFC's own card templates) instead
    # print real leading digits before the masked run: "438628******2343".
    # This was the difference between three of the user's own distinct Axis
    # cards correctly separating and all three silently merging into one
    # account, because the label ("Credit Card Number") sits on its own table
    # row with the value one row below it - too far apart for the labeled
    # search above to bridge - and the old pattern only matched the
    # from-the-start masking shape.
    # Two to four masking characters per group, not exactly four. HSBC prints
    # "51xx xxxx xxxx 1751", but the extracted text collapses the runs to
    # "51xx xx xx 1751" - so a pattern demanding groups of four found nothing,
    # and twelve HSBC statements were filed with NO account number at all.
    # Their transaction alerts all said 1751 and every one was refused for
    # naming an account that did not exist.
    # One pass over every masked-number candidate, taking the first that
    # survives both disqualifications below.
    #
    # The leading digit run is optional and captured explicitly so the masking
    # can begin mid-token: "4375XXXXXXXX2002" has no word boundary between the
    # BIN and the mask, and an ICICI filename is the only place that card's
    # number appears at all. The trailing guard is a lookahead rather than \b
    # because an underscore is a word character, and every one of those
    # filenames continues "..._459232_Retail_HPCL_NORM.pdf".
    for candidate in re.finditer(
            r"\b(\d{0,6})(?:[X*]{2,4}[\s-]?){2,4}(\d{4})(?![\dA-Za-z])",
            text, re.IGNORECASE):
        # A customer ID is masked exactly like an account number and printed
        # above it. ICICI's savings statement opens with
        #
        #     STATEMENT SUMMARY for Customer ID : XXXXX9341
        #     ...
        #     Savings A/c XXXXXXXX1951
        #
        # and one customer ID spans every account the bank holds for you, so
        # filing statements under it merges accounts that are not the same
        # account. The labelled search above already refuses to read one; this
        # is the same refusal for the unlabelled fallback, which was taking
        # the customer ID simply because it comes first.
        if _IDENTITY_NOT_ACCOUNT.search(
                text[max(0, candidate.start() - 40):candidate.start()]):
            continue
        # A real card's leading digits are its BIN (issuer/network prefix -
        # "4315", "4375", "6528" in this mailbox) and are never all zeros. One
        # ICICI statement's transaction table embeds an EMI/loan reference
        # shaped exactly like a masked card number - "0000XXXXXXXX4199" - a
        # few rows below the real card's own "4315XXXXXXXX5001" line, and a
        # first-match search took whichever came first in the extracted text,
        # splitting one physical card into two accounts depending on the month.
        prefix = candidate.group(1)
        if prefix and set(prefix) == {"0"}:
            continue
        return f"XXXX{candidate.group(2)}"

    # Every candidate was disqualified. Returning None is the safer failure:
    # the caller's other identity signals - product name, then institution
    # plus account type - take over instead of filing this statement under a
    # fabricated account.
    return None


def detect_period(text: str) -> tuple[date | None, date | None]:
    """Find the statement period from a 'from X to Y' style phrase."""
    patterns = [
        # The date alternatives are day-first ("12 May 2026", "12/05/2026"),
        # ISO, and month-first ("May 12, 2026"). ICICI prints the last of
        # those - "Statement period : May 12, 2026 to August 11, 2026" - and
        # with only the day-first form here that statement declared no period
        # at all, so its period was inferred from two rows lifted out of the
        # worked example in the terms and conditions and it claimed to cover
        # September to October 2023.
        r"(?:statement\s*period|period|from)\s*[:\-]?\s*"
        r"([\d]{1,2}[-/.\s][\w]{2,9}[-/.\s][\d]{2,4}"
        r"|\d{4}-\d{2}-\d{2}"
        r"|[A-Za-z]{3,9}\s+\d{1,2}\s*,?\s*\d{4})"
        r"\s*(?:to|-|–|through|till)\s*"
        r"([\d]{1,2}[-/.\s][\w]{2,9}[-/.\s][\d]{2,4}"
        r"|\d{4}-\d{2}-\d{2}"
        r"|[A-Za-z]{3,9}\s+\d{1,2}\s*,?\s*\d{4})",
        # HSBC heads the statement with a bare range and no keyword at all:
        # "24 OCT 2025 To 23 NOV 2025". Without the period the statement cannot
        # tell a transaction from the payment-due-date summary printed above it.
        r"([\d]{1,2}\s*[A-Za-z]{3,9}\s*[\d]{2,4})\s+(?:to|through|till)\s+"
        r"([\d]{1,2}\s*[A-Za-z]{3,9}\s*[\d]{2,4})",
        # IDFC FIRST prints the range under the title with no keyword at all
        # and slashes for separators:
        #
        #     Credit Card Statement
        #     23/Jul/2026 - 22/Aug/2026
        #
        # Read as a transaction row instead of as the period, that line became
        # a 2,622-rupee debit dated 2020-07-23: the year truncated to "20" and
        # the amount built out of "26 - 22". One row, and the dashboard's span
        # went from four months to seventy-four.
        #
        # Both sides must carry an alphabetic month and a four-digit year,
        # which is what keeps a bare "12 - 15" from reading as a period.
        r"(\d{1,2}[/-][A-Za-z]{3,9}[/-]\d{4})\s*[-–]\s*"
        r"(\d{1,2}[/-][A-Za-z]{3,9}[/-]\d{4})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            start, end = parse_date(m.group(1)), parse_date(m.group(2))
            # A statement covers a billing cycle, not a decade. The span check
            # is what makes a keyword-less pattern safe to run: two dates that
            # happen to sit near each other on the page cannot pass as a
            # period unless they also look like one.
            if (start and end and start <= end
                    and (end - start).days <= 400):
                return start, end

    # American Express states the range as "Statement Period From May 29 to
    # June 28, 2026" - only the END carries a year, trusting the reader to
    # infer the start's year from it (the same convention the card uses for
    # every transaction row, see parsers.parse_date). Parsed as its own
    # two-step case: the year comes from the end date, and is only carried
    # back to the start as-is if that does not put the start AFTER the end -
    # a period crossing a calendar year boundary ("December 29 to January 5,
    # 2026") must count the start as the year before.
    m = re.search(
        r"statement\s*period\s*from\s*"
        r"([A-Za-z]{3,9}\s+\d{1,2})\s*(?:to|-|–|through|till)\s*"
        r"([A-Za-z]{3,9}\s+\d{1,2}\s*,?\s*\d{2,4})",
        text, re.IGNORECASE,
    )
    if m:
        end = parse_date(m.group(2))
        if end:
            start = parse_date(m.group(1), default_year=end.year)
            if start and start > end:
                start = parse_date(m.group(1), default_year=end.year - 1)
            if start and start <= end:
                return start, end

    return None, None


#: A complete money token. Must accept any number of decimals ("342180.5" as
#: well as "1,18,162.35") and a bare fraction ("₹ .00"). Requiring exactly two
#: decimals truncated 342180.5 to 342180 - silently altering a balance.
_AMOUNT_IN_LINE = re.compile(
    r"(?<![\d.,])(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?![\d,])"
)


def _amounts_on_line(line: str) -> list[Decimal]:
    """Every parseable money value on a line, left to right."""
    out: list[Decimal] = []
    for token in _AMOUNT_IN_LINE.findall(line or ""):
        value = parse_amount(token).value
        if value is not None:
            out.append(value)
    return out


def _labeled_amount(
    text: str, labels: list[str], take: str = "first"
) -> Decimal | None:
    """Find a labelled money value, tolerating two real-world layouts.

    1. Each label is tried in turn. Previously the FIRST label that matched
       anything won outright, so a match whose value failed to parse killed the
       whole lookup and the remaining labels were never tried.

    2. Statements often print the labels as a header ROW with the figures on the
       NEXT line:

           Opening Balance   Payment/Credits   Closing Balance
           ₹ .00             ₹ .00             ₹ 51,488.08

       Reading only the label's own line finds no number at all. When that
       happens we take the values line beneath it - `take` says which end of it
       this label refers to, since the columns run in header order.
    """
    lines = (text or "").split("\n")

    for label in labels:
        pattern = re.compile(rf"{label}", re.IGNORECASE)
        for i, line in enumerate(lines):
            if not pattern.search(line):
                continue

            # (1) value on the same line, immediately after the label.
            tail = pattern.split(line, maxsplit=1)[-1]
            same_line = _amounts_on_line(tail)
            if same_line:
                return same_line[0] if take == "first" else same_line[-1]

    return None

# NOTE: reading the figures from the line BELOW a label was tried and reverted.
#
# Some statements do print labels as a header row with values underneath
# ("Opening Balance  Payment/Credits  Closing Balance" / "₹.00 ₹.00 ₹51,488.08"),
# and it fixed 5 BOBCARD files. But it also attached plausible-looking wrong
# balances to statements that previously, correctly, had none - reconciliation
# failures went from 5 to 27, with closing balances of 0 on live accounts.
#
# A wrong balance is worse than a missing one: a missing balance makes the gate
# report NOT_APPLICABLE and say so, while a wrong one makes it accuse the
# transactions. Those 5 files stay unverified rather than 22 becoming wrong.




#: A line that starts with a date is the first transaction row, which means the
#: letterhead has ended.
_DATE_TOKEN = r"\d{1,2}[-/.][\w]{2,9}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2}"
_TXN_LINE_START = re.compile(
    # A "date TO date" or "date - date" pair is a statement-period range, not
    # a transaction row - Axis's own relationship-summary export opens with
    # "01/11/2025 to 30/11/2025" and IDFC First's card statement opens with
    # "23/Sep/2025 - 22/Oct/2025" as literally the first line of the document,
    # both with no label in front. Without this guard that first line matches
    # the plain date-start pattern below, `letterhead()` truncates to nothing
    # before it even reaches the address block, and every downstream field
    # (institution, account type, balances) is read from an empty string.
    # The lookahead reuses the same date shape rather than a bare `\d` so it
    # can't misfire on a transaction whose amount happens to sit right after
    # the date with a "-" in between (e.g. a negative amount).
    #
    # The optional leading "NN% " covers a second, unrelated ICICI layout
    # quirk: a wrapped reward-percentage annotation from the reward-points
    # column sometimes lands at the very start of the row in extracted text,
    # ahead of the date - "58% 19/01/2026 12719230464 Autodebit Payment
    # Recd." - so the plain date-first pattern below never matches that row,
    # the letterhead never gets cut, and unrelated body text past it (a
    # stray "WALLET LOAD" merchant description) leaks into what should be
    # the identity-only letterhead and misclassifies the whole account.
    rf"^\s*(?:\d{{1,3}}%\s+)?(?:{_DATE_TOKEN})(?!\s*(?:to|-)\s*(?:{_DATE_TOKEN}))\b"
)


def letterhead(text: str) -> str:
    """The part of the document above the first transaction row.

    Identity fields must be read from here and nowhere else. A savings
    statement's body contains "HOME LOAN EMI" on every EMI row and
    "CREDIT CARD PAYMENT" on every bill payment - matching those relabels the
    whole account as a loan or a card, which then flips its sign convention and
    makes a perfectly good statement fail reconciliation.

    Balances are a different matter and may legitimately appear in a footer, so
    those still read from the wider head-and-tail slice.
    """
    if not text:
        return ""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if _TXN_LINE_START.match(line):
            return "\n".join(lines[:i])
    return text


#: Some PDF templates render specific bold labels as two overlapping text
#: layers; pdfplumber then extracts every character in that run twice in a
#: row, word by word - "PAYMENT DUE DATE" becomes "PPAAYYMMEENNTT DDUUEE
#: DDAATTEE". One of ICICI's own credit card templates (the Amazon Pay card,
#: confirmed on several real statements) does this to exactly the two labels
#: `detect_account_type` and the balance detectors key off, which silently
#: drops the account to UNKNOWN and its balances to None.
#:
#: Checked per whitespace-separated token and anchored to the WHOLE token
#: (^...$), not a mid-string search: a real word is only ever doubled by
#: this artifact in its entirety, never partway through, and no ordinary
#: English word is itself composed edge-to-edge of doubled-letter pairs from
#: its very first character (unlike a mid-string search, which would
#: misfire on "bookkeeper"'s genuine "...ookkee..." run). The 2-pair floor
#: just excludes incidental 2-letter tokens ("SS", abbreviations) that could
#: be real content rather than this artifact.
_FULLY_DOUBLED_TOKEN = re.compile(r"^(?:(.)\1){2,}$")


def _undo_bold_letter_doubling(text: str) -> str:
    def fix(m: re.Match) -> str:
        token = m.group(0)
        return token[::2] if _FULLY_DOUBLED_TOKEN.match(token) else token

    return re.sub(r"\S+", fix, text)


#: ICICI's Most Important Terms & Conditions annexe includes a purely
#: illustrative worked example of how the minimum amount due is calculated -
#: a numbered table using the exact same labels this file searches for
#: ("Total Amount Due", "Closing Balance", "Opening Balance"...) attached to
#: fabricated figures with no connection to the customer's real statement.
#: On a real HPCL-card statement this fake "Closing Balance 26,958.20" line
#: was found before the genuine one (a bare `_labeled_amount` search has no
#: notion of document position across different label variants - it tries
#: each label in the caller's list order, not in the order labels appear on
#: the page), overwriting a correct `0.00` with a number from someone else's
#: hypothetical example. The illustration always opens with this exact
#: sentence, so everything from there to the end of the document is excluded
#: before any balance/limit/EMI figure is read. Deliberately NOT applied to
#: the interest-rate lookup just below - unlike the amount fields, the real
#: applicable rate is often disclosed later in this same T&C section, so
#: cutting it here would trade one wrong answer for a missing one elsewhere.
_MITC_ILLUSTRATION_MARKER = re.compile(
    r"illustration will indicate the method of calculating", re.IGNORECASE
)


#: A card statement's summary line, as one equation:
#:
#:     opening  -  credits  +  debits  =  closing
#:
#: American Express prints exactly this, and its labels ("Opening Bala nce Rs")
#: come out of the PDF with spaces wedged inside the words, so the labelled
#: search below never finds them. Without an opening AND a closing figure the
#: reconciliation gate cannot run at all - which is how two Amex statements
#: were imported with three refunds booked as spending and ten thousand rupees
#: of charges missing, and nothing flagged either.
#:
#: The number pattern deliberately refuses a second decimal point: the figures
#: run together in the extracted text ("56,858.1514,053.00"), and a looser
#: pattern swallows the minimum-payment column into the closing balance.
_SUMMARY_NUMBER = r"(\d{1,3}(?:,\d{2,3})*(?:\.\d{2}))"
_SUMMARY_EQUATION = re.compile(
    _SUMMARY_NUMBER + r"\s*-\s*" + _SUMMARY_NUMBER
    + r"\s*\+\s*" + _SUMMARY_NUMBER + r"\s*=\s*" + _SUMMARY_NUMBER)


def _summary_equation(text: str) -> tuple[Decimal, Decimal] | None:
    """(opening, closing) from a summary line that proves itself.

    The match is only accepted when opening - credits + debits actually equals
    closing. That check is the whole point: it costs nothing, and it means a
    coincidental run of four numbers somewhere else in the document can never
    be mistaken for the statement's own summary.
    """
    for line in text.splitlines():
        squeezed = re.sub(r"[ \t]+", "", line)
        match = _SUMMARY_EQUATION.search(squeezed)
        if not match:
            continue
        try:
            opening, credits, debits, closing = (
                Decimal(g.replace(",", "")) for g in match.groups())
        except (InvalidOperation, ValueError):
            continue
        if opening - credits + debits == closing:
            return opening, closing
    return None


def _before_mitc_illustration(text: str) -> str:
    m = _MITC_ILLUSTRATION_MARKER.search(text or "")
    return text[: m.start()] if m else text


def extract_metadata(text: str, filename: str = "", sender: str = "") -> StatementMetadata:
    """Pull everything we can from the statement letterhead.

    `text` should be the document's full text (or the text surrounding the
    transaction table), not the table rows themselves. `sender`, when the
    file came from an email, is the strongest institution signal available -
    see below.
    """
    meta = StatementMetadata()
    if not text:
        text = ""
    text = _undo_bold_letter_doubling(text)

    head = letterhead(text)

    meta.institution = detect_institution(f"{filename} {head}")
    if not meta.institution:
        # The letterhead is preferred because it is the most trustworthy slice
        # - body text is full of other banks' names in transaction narrations.
        # But some issuers put nothing above the cardholder's address except
        # the cardholder's address: ICICI's HPCL card names itself only from
        # its GST number onward, a thousand characters down. Falling back to
        # the wider text costs nothing when the letterhead already answered,
        # and is the difference between knowing the issuer and filing a card
        # under "Unknown".
        meta.institution = detect_institution(f"{filename} {text}")
    if sender:
        # A sender's own domain almost never lies about who issued the
        # statement, but body text can: one Axis card's statement gives its
        # address as "...Pune City HDFC Bank," (a landmark near the user's
        # home, not the issuer), and that was the ONLY institution-shaped
        # text anywhere in this template's letterhead - the genuine "Axis
        # Bank" mention lives in the GST footer, past where the letterhead
        # is cut off. `detect_institution` also can't read the sender itself:
        # it requires a word boundary around each fragment, which fails on a
        # fused domain like "axisbank.com" (no break between "axis" and
        # "bank"). `institution_for_sender` is the plain-substring matcher
        # built for exactly this shape of text, so it succeeds where this
        # function's own body-text matcher cannot.
        from ..ingestion.gmail_source import institution_for_sender

        sender_institution = institution_for_sender(sender)
        if sender_institution in INSTITUTIONS.values():
            meta.institution = sender_institution
    meta.account_type = detect_account_type(head, filename)
    if meta.account_type == AccountType.CREDIT_CARD:
        # Restricted to the first several lines deliberately - never the whole
        # letterhead. Every real card statement checked prints its own product
        # name somewhere in this opening block: Axis names it on line 1
        # ("Axis Bank REWARDS Credit Card"), HSBC only after a payment-summary
        # preamble ("HSBC TRAVELONE CREDIT CARD" on line 6). But several of
        # these names (Rewards, Cashback, Ace) are also generic enough to
        # appear as ordinary statement content further down: SBI's own
        # transaction table prints a "Reward Points" COLUMN HEADER at line 19,
        # which matched "Rewards" and labelled three different real cards with
        # the same fictitious product. Eight lines comfortably covers every
        # title position seen while staying well clear of that false match.
        title_lines = "\n".join(head.split("\n")[:8])
        meta.product_name = detect_card_variant(title_lines)
        if not meta.product_name and meta.institution == "ICICI Bank":
            meta.product_name = detect_card_variant_from_filename(filename)
    # The filename sits between the two body reads on purpose.
    #
    # A number the document LABELS as its card or account is the strongest
    # evidence there is, so the letterhead goes first. But an issuer that
    # names the file after the account is being deliberate, and that beats an
    # unlabelled number found loose in the body - which is only ever a guess
    # about which of the numbers on the page identifies the statement.
    #
    # ICICI's HPCL card is the case that decides it. All ten of its statements
    # are named "4375XXXXXXXX2002_..._Retail_HPCL_NORM.pdf", and none of them
    # labels a card number anywhere. Each one's spends summary lists a
    # DIFFERENT card - 5241XXXXXXXX0005, 3747XXXXXXXX5005, 5241XXXXXXXX3006 -
    # so the unlabelled fallback split one card into three accounts, by month.
    meta.account_number_masked = (
        detect_account_number(head, labeled_only=True)
        or detect_account_number(text, labeled_only=True)
        or detect_account_number(filename or "")
        or detect_account_number(head)
        or detect_account_number(text)
    )
    # Tried against `head` first, then the wider `text` if that finds
    # nothing - same idiom as account_number_masked just above, and for the
    # same reason letterhead()'s own docstring gives for balances: a period
    # statement can legitimately sit past wherever _TXN_LINE_START decided
    # the letterhead ends. That guard is tuned to recognise transaction
    # rows, not every line that happens to start with a date - a wrapped
    # due-date reminder ("...by\n16/07/2026.") is exactly such a line, and
    # when it triggers early, the actual "Statement Period From ..." text
    # further down is truncated away before detect_period ever sees it.
    meta.period_start, meta.period_end = detect_period(head)
    if not meta.period_start:
        meta.period_start, meta.period_end = detect_period(text)

    holder = _find_labeled(head or text, [
        r"prepared\s*for", r"account\s*holder(?:\s*name)?", r"customer\s*name", r"borrower\s*name",
        r"investor\s*name", r"card\s*holder(?:\s*name)?", r"name\s*of\s*(?:the\s*)?holder",
    ])
    if holder and not re.search(r"\d{4,}", holder):
        meta.holder_name = holder.title()

    balances_text = _before_mitc_illustration(text)
    meta.opening_balance = _labeled_amount(balances_text, [
        r"opening\s*balance", r"balance\s*b/?f", r"brought\s*forward",
        r"previous\s*balance", r"opening\s*principal\s*outstanding",
        r"previous\s*statement\s*balance",
    ])
    meta.closing_balance = _labeled_amount(balances_text, [
        r"closing\s*balance", r"balance\s*c/?f", r"carried\s*forward",
        r"total\s*amount\s*due", r"closing\s*principal\s*outstanding",
        r"current\s*balance", r"net\s*balance", r"total\s*dues",
    ])

    # Fall back to the summary equation when the labels could not be read.
    # Only used to fill a gap, never to overrule a figure found by label.
    if meta.opening_balance is None or meta.closing_balance is None:
        summary = _summary_equation(balances_text)
        if summary is not None:
            opening, closing = summary
            if meta.opening_balance is None:
                meta.opening_balance = opening
            if meta.closing_balance is None:
                meta.closing_balance = closing
    meta.credit_limit = _labeled_amount(balances_text, [
        r"credit\s*limit", r"total\s*credit\s*limit", r"sanctioned\s*limit",
    ])
    meta.emi_amount = _labeled_amount(balances_text, [
        r"emi\s*amount", r"instal?ment\s*amount", r"monthly\s*instal?ment",
    ])

    rate = _find_labeled(text, [
        r"rate\s*of\s*interest", r"interest\s*rate", r"roi",
    ])
    if rate:
        m = re.search(r"([\d.]+)\s*%", rate)
        if m:
            try:
                meta.interest_rate = Decimal(m.group(1))
            except Exception:
                pass

    if re.search(r"\bUSD\b|\$", text) and not re.search(r"\bINR\b|₹|\bRs\.?\b", text):
        meta.currency = "USD"


    # LLM Fallback for identity fields
    if meta.institution == "Unknown" or meta.account_type == AccountType.UNKNOWN:
        from ..llm.client import get_client, LLMUnavailable
        from ..db.repository import get_ai_inference, save_ai_inference
        from ..api.dependencies import get_db
        import hashlib
        
        # Only use the letterhead slice!
        slice_to_send = head if head else text[:1000]
        
        fingerprint = hashlib.sha256(slice_to_send.encode()).hexdigest()
        db = get_db()
        
        def fill_gaps(answer: dict) -> None:
            """Fill only what is still missing.

            This block is entered when EITHER the institution or the account
            type is unknown, and it used to overwrite both. So a statement
            whose issuer had been read correctly but whose type had not could
            have its issuer replaced by a guess - the deterministic reader
            beaten by the model on a question it had already answered.
            """
            if meta.institution == "Unknown" and answer.get("institution"):
                meta.institution = answer["institution"]
            if meta.account_type == AccountType.UNKNOWN and answer.get("account_type"):
                try:
                    meta.account_type = AccountType(answer["account_type"])
                except ValueError:
                    pass  # a type the app does not have is not a type
            if not meta.product_name and answer.get("product_name"):
                meta.product_name = answer["product_name"]

        try:
            cached = get_ai_inference(db, fingerprint)
        except Exception as exc:  # a broken cache must not fail the parse
            import logging
            logging.getLogger(__name__).warning(
                "could not read the identity cache: %s", exc)
            cached = None

        if cached:
            fill_gaps(cached)
        else:
            try:
                client = get_client()
                if client.available:
                    prompt = f"""Extract bank name, account type, and product name from this statement letterhead.
Return JSON: {{"institution": "...", "account_type": "savings|credit_card|current|...", "product_name": "..."}}
Letterhead: {slice_to_send}"""
                    resp = client.complete_json(prompt, system="You return JSON only.", max_tokens=100)
                    if isinstance(resp, dict):
                        save_ai_inference(db, fingerprint, resp)
                        fill_gaps(resp)
            except (LLMUnavailable, Exception) as e:
                import logging
                logging.getLogger(__name__).warning("LLM fallback failed: %s", e)

    if meta.opening_balance is None and meta.closing_balance is None:
        meta.notes.append(
            "Statement declared no opening/closing balance, so the balance "
            "reconciliation check could not be run on this file."
        )
    return meta
