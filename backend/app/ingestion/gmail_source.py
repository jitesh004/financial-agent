"""Fetch statements straight from the user's Gmail.

Instead of the user downloading every statement and dragging it in, this finds
the bank emails, downloads the PDF attachments, and feeds them into the same
pipeline as a manual upload.

The security model is the important part:

  - **OAuth only, read-only.** Authentication happens on Google's own consent
    screen. This app never sees or asks for the Gmail password - it receives a
    scoped token. The scope is `gmail.readonly`, so it can read and download but
    can never send, delete, or modify anything.

  - **A token per person.** The grant is held against the signed-in user and
    used only to call Google's API on their behalf. It used to be one file on
    disk, which was right for a program on one person's laptop and wrong the
    moment this became something several people sign into - whoever connected
    last would have owned everyone's import. `TokenStore` is the seam: the
    server hands in a store that reads and writes that user's row.

  - **The user stays in control.** Fetching lists what it found and downloads
    attachments (a permissioned action) - the caller decides whether to then
    analyse them. Nothing is auto-sent or auto-deleted, ever.

Connecting happens through the app's own Google sign-in (see `auth/google.py`),
as a second, separate grant the user makes deliberately - not bundled into
signing in. Without a configured OAuth client everything here is inert and the
manual upload path is unaffected. A `FakeGmailClient` mirrors the real client's
surface so the flow is fully testable offline.
"""

from __future__ import annotations

import base64
import logging
import re
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..rules import institutions

log = logging.getLogger(__name__)

# Read-only. This is a hard ceiling: even a compromised token cannot send or
# delete mail, only read it.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

#: Who and what a scan looks for.
#:
#: The issuer lists are DERIVED from `rules.institutions` - see that module for
#: why. What stays here is the wording: the subject phrases that mark an email
#: as carrying one kind of document rather than another. Those are properties
#: of the document, not of the bank that sent it, so they belong next to the
#: filter that reads them.

#: Statement senders, as a local acceptance test. Inclusive on purpose - a
#: false positive downloads a PDF the parser later ignores, while a false
#: negative silently drops a real statement.
STATEMENT_SENDERS = list(institutions.fragments_for_scan(
    institutions.SENDS_STATEMENT))
STATEMENT_KEYWORDS = [
    "statement", "e-statement", "estatement", "account statement",
    "credit card statement", "loan statement", "portfolio statement",
    "consolidated account statement", "monthly statement",
]

#: Gmail search terms that mark an email as carrying a statement. Broad on
#: purpose - the precise filtering happens locally in `_looks_like_statement`,
#: where we can be far more specific than Gmail's query language allows.
_SUBJECT_TERMS = (
    "statement", "e-statement", "estatement", "account statement",
    "credit card statement", "loan statement", "portfolio statement",
    "consolidated account statement", "statement of account",
    "monthly statement", "transaction statement", "funds statement",
)

#: Excluded at query level - promotional mail is the single biggest source of
#: false positives, and Gmail can filter it far more cheaply than we can.
_QUERY_EXCLUSIONS = "-category:promotions -category:social -in:spam -in:trash"


#: Credit bureaus. Their reports are PDFs like a statement, but they describe
#: what you owe across every lender rather than one account's activity, so they
#: are searched for separately and parsed by a different reader entirely.
_BUREAU_SUBJECT_TERMS = (
    "credit report", "credit information report", "credit score",
    "cibil report", "cibil score", "experian credit", "crif report",
    "equifax credit", "your credit health", "annual credit report",
)

#: Transaction alerts. Deliberately NOT `has:attachment` - the whole point of
#: these is that the amount is in the body of a one-line email, which is why
#: they arrive within minutes rather than the fortnight a statement takes.
#: Investments are scanned separately from bank statements because they are a
#: different question with a different useful window: a holdings statement is a
#: photograph of what you own on one date, so last quarter's is history, while
#: a bank statement from the same month is still money you need to account for.
#: Giving them one shared "look back" setting meant every answer was wrong for
#: one of them.
_INVESTMENT_SUBJECT_TERMS = (
    "portfolio", "holding", "holdings", "demat", "consolidated account statement",
    "contract note", "capital gain", "mutual fund", "nav", "folio",
    "transaction statement", "account statement",
)

_ALERT_SUBJECT_TERMS = (
    "transaction alert", "debited", "credited", "spent on", "txn alert",
    "transaction on", "payment of", "upi transaction", "debit alert",
    "credit alert", "card transaction", "withdrawn",
)


#: The three things a scan can look for. Each is a different kind of document
#: with a different reader behind it, so the search that finds one is no use
#: for the others.
SCAN_INTENTS: dict[str, dict[str, object]] = {
    "statement": {
        "label": "Account statements",
        "description": "Bank, card, loan and portfolio statement PDFs.",
        "needs_attachment": True,
        "subjects": _SUBJECT_TERMS,
        "senders": institutions.query_senders(institutions.SENDS_STATEMENT),
        "max_months": None,
    },
    "bureau": {
        "label": "Credit bureau reports",
        "description": "CIBIL, CRIF, Experian and Equifax reports - what every "
                       "lender says you owe, including accounts no statement "
                       "ever reaches you for.",
        "needs_attachment": True,
        "subjects": _BUREAU_SUBJECT_TERMS,
        "senders": institutions.query_senders(institutions.SENDS_BUREAU),
        "max_months": None,
    },
    "investment": {
        "label": "Investments",
        "description": "Broker, demat and mutual fund statements - holdings "
                       "and contract notes from Zerodha, CAMS, KFintech, NSDL "
                       "and the rest.",
        "needs_attachment": True,
        "subjects": _INVESTMENT_SUBJECT_TERMS,
        "senders": institutions.query_senders(institutions.SENDS_INVESTMENT),
        "max_months": None,
    },
    "transactional": {
        "label": "Transaction alerts (recent)",
        "description": "The one-line alerts your bank sends within minutes of "
                       "a payment. Covers the gap before a statement is cut.",
        "needs_attachment": False,
        "subjects": _ALERT_SUBJECT_TERMS,
        "senders": institutions.query_senders(institutions.SENDS_ALERT),
        # Hard-capped. Alerts are unreconciled by nature and only earn their
        # place by being fresher than the statement; a year of them would be a
        # year of unchecked figures sitting next to checked ones.
        "max_months": 2,
    },
}

DEFAULT_INTENT = "statement"


def build_query(months: int | None = None, extra: str = "",
                intent: str = DEFAULT_INTENT) -> str:
    """Build the Gmail search query for one kind of document.

    `months` limits how far back to look. Gmail's own `newer_than` is used
    rather than a computed date, so the window stays correct no matter when the
    scan runs. None means the whole mailbox - except where the intent caps it,
    which transaction alerts do.
    """
    spec = SCAN_INTENTS.get(intent) or SCAN_INTENTS[DEFAULT_INTENT]

    subjects = " OR ".join(f'subject:"{t}"' for t in spec["subjects"])
    senders = " OR ".join(f"from:{t}" for t in spec["senders"])

    parts: list[str] = []
    if spec["needs_attachment"]:
        parts += ["has:attachment", "filename:pdf"]
    parts += [f"(({subjects}) OR ({senders}))", _QUERY_EXCLUSIONS]

    # `max_months` is the DEFAULT window for a source, not a ceiling on it.
    #
    # It used to clamp: asking for a year of alerts silently got you two
    # months. That is the same fault as a dropdown showing one number while
    # the app uses another - the app quietly overruling a choice the user
    # made and telling them nothing. Alerts really are unreconciled and a year
    # of them really is mostly noise the statements supersede, but that is
    # advice to print next to the control, not a decision to take on someone's
    # behalf.
    cap = spec["max_months"]
    if cap is not None and months is None:
        months = cap
    if months:
        # Gmail understands d/m/y suffixes; months is the natural unit here.
        parts.append(f"newer_than:{months}m" if months < 12
                     else f"newer_than:{months // 12}y")
    if extra:
        parts.append(extra)
    return " ".join(parts)


#: Whole-mailbox default, kept for callers that don't specify a window.
DEFAULT_QUERY = build_query()

#: Offered in the UI. (label, months)
PERIOD_OPTIONS: list[tuple[str, int | None]] = [
    ("1 month", 1), ("3 months", 3), ("6 months", 6),
    ("1 year", 12), ("2 years", 24), ("3 years", 36),
    ("5 years", 60), ("10 years", 120), ("Everything", None),
]


#: Sender-domain fragments grouped by what kind of account they report on.
#: Used to import selectively - a weekly broker funds statement is a different
#: kind of document from a bank statement and rarely yields a useful ledger.
SENDER_CATEGORIES: dict[str, tuple[str, ...]] = {
    kind: institutions.fragments_for_kind(kind)
    for kind in institutions.CLASSIFY_ORDER
}


def institution_for_sender(sender: str) -> str:
    """The bank's real name, for grouping and display.

    The mailbox display name is useless for this: ICICI sends savings
    statements as "Estatement" and card statements as
    "credit_cards@icicibank.com", so the user's own salary account appeared in
    the list under a group called "Estatement" and was impossible to find by
    looking for "ICICI". Several other institutions showed a raw email address.

    Resolving from the domain also merges an institution's multiple mailers -
    icicibank.com and icici.bank.in are one bank, not two.
    """
    name = institutions.name_for(sender)
    if name:
        return name

    # Fall back to a readable display name, then to the domain.
    if "<" in (sender or ""):
        display = sender.split("<")[0].strip().strip('"')
        if display and "@" not in display:
            return display
    domain = re.search(r"@([\w.-]+)", sender or "")
    return domain.group(1) if domain else (sender or "Unknown")


def classify_sender(sender: str) -> str:
    """Bucket a sender into bank / card / loan / broker / bureau / unknown."""
    return institutions.classify(sender)


@dataclass
class FoundAttachment:
    message_id: str
    attachment_id: str
    filename: str
    sender: str
    subject: str
    date: str
    size: int
    #: Set once downloaded.
    saved_path: str | None = None
    #: True when served from the local cache instead of re-downloaded.
    from_cache: bool = False

    @property
    def category(self) -> str:
        return classify_sender(self.sender)

    def cache_key(self) -> str:
        """Stable per-attachment filename for the local cache.

        Deliberately does NOT use `attachment_id`. Gmail's attachment ids are
        ephemeral - they are regenerated on each messages.get call, so keying on
        one produces a cache that never hits and silently re-downloads the whole
        mailbox every run. The message id, filename and byte size are all stable
        for a delivered message, and together they identify one attachment.
        """
        stem = _SAFE_NAME.sub("_", Path(self.filename).stem[:40])
        return f"{self.message_id}_{self.size}_{stem}.pdf"

    def cache_glob(self) -> str:
        """Pattern matching this attachment under any past key scheme."""
        stem = _SAFE_NAME.sub("_", Path(self.filename).stem[:40])
        return f"{self.message_id}_*_{stem}.pdf"


@dataclass
class ExcludedMessage:
    """An email the filter rejected, with the reason.

    Surfaced in the UI so exclusions are auditable. A filter that silently drops
    mail is one the user has to second-guess; one that says "3 marketing,
    2 tax certificates" can be checked at a glance.
    """

    sender: str
    subject: str
    date: str
    reason: str
    attachment_count: int = 0


@dataclass
class FetchResult:
    attachments: list[FoundAttachment] = field(default_factory=list)
    excluded: list[ExcludedMessage] = field(default_factory=list)
    scanned_messages: int = 0
    warnings: list[str] = field(default_factory=list)
    connected: bool = False


class GmailClient(Protocol):
    """The slice of the Gmail API this module needs. Both the real and fake
    clients implement it, so nothing downstream knows which is in use."""

    def list_messages(self, query: str, max_results: int) -> list[str]: ...
    def get_message(self, message_id: str) -> dict[str, Any]: ...
    def get_attachment(self, message_id: str, attachment_id: str) -> bytes: ...


# --------------------------------------------------------------------------
# Real client
# --------------------------------------------------------------------------

class TokenStore(Protocol):
    """Where one user's Gmail grant is kept.

    An interface rather than a path, so the client does not care whether the
    grant lives in a database row (the server) or a file (a script, a test).
    """

    def load(self) -> str | None:
        """The stored authorized-user JSON, or None if never connected."""

    def save(self, token_json: str) -> None:
        """Persist a refreshed grant."""


class FileTokenStore:
    """A grant in a JSON file.

    Kept for `backend/tools/`, which run as one person against their own
    mailbox from a shell and have no signed-in user to look up.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> str | None:
        return self.path.read_text() if self.path.exists() else None

    def save(self, token_json: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(token_json)
        # A token is a credential: keep it readable only by this user where
        # the OS supports it. Best-effort; harmless where it doesn't.
        try:
            self.path.chmod(0o600)
        except OSError:
            pass


class GoogleGmailClient:
    """Real Gmail API client backed by one user's OAuth token.

    Construction never reaches the network; call `authorize()` explicitly, so
    the read path cannot accidentally spend a token refresh.

    There is no consent flow here any more. Obtaining a grant is the web
    OAuth round trip in `auth/google.py` - a server cannot open a browser on
    the user's machine, and the old `InstalledAppFlow` would have popped a
    consent screen on the host nobody can see.
    """

    def __init__(self, tokens: TokenStore):
        self.tokens = tokens
        self._service = None
        self._creds = None
        self._local = None

    def authorize(self, interactive: bool = False) -> bool:
        """Load the stored grant, refreshing it if it has expired.

        `interactive` is accepted and ignored: there is nothing interactive a
        server can do. It stays in the signature because several call sites
        pass `interactive=False` to say "do not pop anything", and that
        request is now simply always honoured.
        """
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        raw = self.tokens.load()
        if not raw:
            return False
        try:
            creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
        except (TypeError, ValueError) as exc:
            log.warning("stored Gmail token is unreadable: %s", exc)
            return False

        if creds.valid:
            self._build(creds)
            return True

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save(creds)
            self._build(creds)
            return True

        # Expired with nothing to refresh from. The user has to grant again;
        # saying so beats a confusing 401 from the first API call.
        return False

    def _build(self, creds) -> None:
        from googleapiclient.discovery import build
        self._creds = creds
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _thread_service(self):
        """A service instance private to the calling thread.

        googleapiclient services wrap an httplib2 connection, which is NOT
        thread-safe: sharing one across a thread pool causes interleaved reads
        on the same socket, and the scan simply hangs part-way through with no
        error. Each worker therefore builds its own service on first use, which
        is cheap next to the network round trips it then makes.
        """
        import threading

        local = getattr(self, "_local", None)
        if local is None:
            local = self._local = threading.local()

        service = getattr(local, "service", None)
        if service is None:
            from googleapiclient.discovery import build
            service = local.service = build(
                "gmail", "v1", credentials=self._creds, cache_discovery=False
            )
        return service

    def _save(self, creds) -> None:
        self.tokens.save(creds.to_json())

    def is_authorized(self) -> bool:
        return bool(self.tokens.load())

    #: Gmail's hard per-page ceiling. Asking for more silently returns 500.
    LIST_PAGE_SIZE = 500

    def list_messages(self, query: str, max_results: int) -> list[str]:
        """Message ids matching the query, paging until max_results is reached.

        Gmail caps maxResults at 500 PER PAGE and quietly truncates rather than
        erroring, so a single call can never return more than 500 ids no matter
        what is asked for. Without paging, "scan 1500 emails over 10 years"
        actually scanned the newest 500 - which looks exactly like the date
        filter being ignored, because the newest 500 statement emails happen to
        span about a year.
        """
        service = self._thread_service()
        ids: list[str] = []
        page_token: str | None = None

        while len(ids) < max_results:
            page = service.users().messages().list(
                userId="me",
                q=query,
                maxResults=min(self.LIST_PAGE_SIZE, max_results - len(ids)),
                pageToken=page_token,
            ).execute()

            ids.extend(m["id"] for m in page.get("messages", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                break  # no more results exist

        return ids[:max_results]

    def get_message(self, message_id: str) -> dict[str, Any]:
        return self._thread_service().users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        att = self._thread_service().users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
        return base64.urlsafe_b64decode(att["data"])


# --------------------------------------------------------------------------
# Message parsing (shared by both clients)
# --------------------------------------------------------------------------

def _header(message: dict[str, Any], name: str) -> str:
    for h in message.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _walk_parts(part: dict[str, Any]):
    yield part
    for child in part.get("parts", []) or []:
        yield from _walk_parts(child)


#: Subject phrases that mean "marketing", even from a real bank's domain. Banks
#: send offers from the same addresses they send statements from, so the sender
#: alone cannot be trusted.
PROMOTIONAL_SUBJECTS = re.compile(
    r"\boffer\b|\bdiscount\b|\bcashback\s+offer\b|\bsale\b|\bdeal\b|\bwin\b"
    r"|\bcongratulation|\bpre-?approved\b|\beligible\s+for\b|\bupgrade\s+your\b"
    r"|\bapply\s+now\b|\blimited\s+period\b|\bexclusive\b|\bintroducing\b"
    r"|\bnewsletter\b|\bwebinar\b|\bsurvey\b|\bfeedback\b|\brefer\s+a\s+friend\b"
    r"|\bloan\s+offer\b|\binstant\s+loan\b|\bincrease\s+your\s+limit\b"
    # Campaign phrasing: banks market services from the same address that sends
    # statements, e.g. "Make your EPFO Payments simple with IndusInd".
    r"|\bmake\s+your\b|\bnow\s+available\b|\bget\s+started\b|\bswitch\s+to\b"
    r"|\bhassle[\s-]?free\b|\bmade\s+easy\b|\bsimplif(y|ied)\b|\bunlock\b"
    r"|\bdon'?t\s+miss\b|\bhurry\b|\blast\s+chance\b|\bsave\s+(up\s+to|more)\b"
    # "EPFO payments now LIVE!!!" - a launch announcement. Repeated exclamation
    # marks are near-diagnostic: statement generators never emit them.
    r"|\bnow\s+live\b|\bgoing\s+live\b|\bcoming\s+soon\b|\bannouncing\b"
    r"|\bpresenting\b|!\s*!",
    re.IGNORECASE,
)

#: Marketing subject lines in this space almost always carry an emoji; real
#: statement mail is generated by back-office systems and never does. A cheap,
#: high-precision signal that no keyword list would catch.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿️⬀-⯿]"
)

#: Subject phrases that are transactional but are NOT statements - alerts,
#: OTPs, payment confirmations. They often carry a PDF receipt.
NON_STATEMENT_SUBJECTS = re.compile(
    r"\botp\b|\bone[\s-]?time\s+password\b|\bpassword\s+reset\b"
    r"|\btransaction\s+alert\b|\bdebit\s+alert\b|\bcredit\s+alert\b"
    r"|\bpayment\s+(received|successful|confirmation|reminder)\b"
    r"|\bdue\s+reminder\b|\bautopay\b|\bmandate\b|\be-?mandate\b"
    r"|\bnomination\b|\bkyc\b|\bre-?kyc\b|\baddress\s+change\b"
    r"|\bcheque\s+book\b|\bdebit\s+card\s+(dispatch|delivery)\b"
    r"|\brate\s+of\s+interest\b|\brevision\s+in\b|\binterest\s+rate\s+change\b"
    r"|\bpolicy\s+document\b|\bwelcome\b|\bactivation\b"
    # Application paperwork: "HSBC Credit Card Application - Acknowledgement",
    # "Your Application Form". Sent by the statement mailer, carries a PDF, and
    # contains no transactions.
    r"|\backnowledge?ment\b|\bapplication\s*(form|status|received)?\b"
    r"|\bdispatch(ed)?\b|\bin\s+principle\s+approval\b"
    # Corporate-action and governance mail from depositories: postal ballots,
    # AGM notices, e-voting. These arrive from the same NSDL/CDSL addresses that
    # send genuine holding statements, so the sender cannot distinguish them -
    # only the subject can.
    r"|\bpostal\s+ballot\b|\be-?voting\b|\bvoting\b|\bagm\b|\begm\b"
    r"|\bannual\s+(general\s+meeting|report)\b|\bnotice\s+of\s+(the\s+)?meeting\b"
    r"|\bdividend\s+(declaration|notice)\b|\brights\s+issue\b"
    r"|\bbuy-?back\b|\bscheme\s+of\s+(arrangement|amalgamation)\b"
    r"|\bcorporate\s+action\b|\bshareholder\b|\bproxy\s+form\b",
    re.IGNORECASE,
)

#: Strong positives: if the subject says this, it IS a statement.
STATEMENT_SUBJECTS = re.compile(
    r"\b(e-?)?statement\b|\bstatement\s+of\s+(account|funds|holdings|transactions)\b"
    r"|\baccount\s+summary\b|\bconsolidated\s+account\s+statement\b"
    r"|\bportfolio\s+statement\b|\bcontract\s+note\b"
    # Brokers name the same document differently: Angel One sends a weekly
    # "Register of Securities & Funds", which is exactly a holdings statement.
    # Without this it fell through to "no statement signal" and was dropped -
    # a false negative, which costs far more than a false positive because the
    # history just silently isn't there.
    r"|\bregister\s+of\s+(securities|funds)\b|\bholding\s+statement\b"
    r"|\bdemat\s+statement\b|\bledger\s+statement\b|\bfunds?\s+statement\b",
    re.IGNORECASE,
)


#: Tax and summary certificates. Real financial documents, but they contain no
#: transaction rows - only a year-end total - so the ledger parser can do
#: nothing with them and reports a confusing "no rows found".
CERTIFICATE_SUBJECTS = re.compile(
    r"\binterest\s+certificate\b|\bcertificate\s+of\s+interest\b"
    r"|\bdeposit\s+accounts?\s+interest\b|\btds\s+certificate\b"
    r"|\bform\s*(16|26as|15g|15h)\b|\bprovisional\s+(interest\s+)?certificate\b"
    r"|\binterest\s+paid\s+certificate\b|\btax\s+(saving|certificate)\b"
    r"|\bbalance\s+confirmation\b|\bholding\s+certificate\b",
    re.IGNORECASE,
)

#: Single-transaction advices. A NEFT/IMPS credit note is not a statement, but
#: it looks like one to a sender-based filter.
ADVICE_SUBJECTS = re.compile(
    r"\b(credit|debit|payment|remittance|transaction)\s+advice\b"
    r"|\bpayment\s+from\b|\bpayment\s+to\b|\bfunds?\s+transfer\b"
    r"|\b(neft|rtgs|imps|upi)\b.*\b(credit|received|transfer)\b"
    r"|\bdomneft\b|\bcredited\s+to\s+your\s+account\b",
    re.IGNORECASE,
)

#: Servicing notices about an account, rather than a report of its activity.
NOTICE_SUBJECTS = re.compile(
    r"\bunclaimed\b|\bexcess\s+amount\b|\bescrow\b|\bforeclosure\b"
    r"|\bpre-?closure\b|\bno\s+dues\b|\blien\b|\bdormant\b|\binoperative\b"
    r"|\bcharges?\s+levied\b|\bpenalty\s+notice\b|\bdispute\b",
    re.IGNORECASE,
)

#: (pattern, reason) in priority order. The reason is surfaced in the UI so an
#: excluded email is visibly excluded-for-a-stated-cause rather than silently
#: missing - which is the difference between a filter you can trust and one you
#: have to second-guess.
REJECTION_RULES: list[tuple[re.Pattern[str], str]] = [
    (PROMOTIONAL_SUBJECTS, "marketing"),
    (_EMOJI, "marketing"),
    (CERTIFICATE_SUBJECTS, "tax certificate"),
    (ADVICE_SUBJECTS, "payment advice"),
    (NOTICE_SUBJECTS, "account notice"),
    (NON_STATEMENT_SUBJECTS, "not a statement"),
]


def statement_rejection_reason(sender: str, subject: str,
                               intent: str = DEFAULT_INTENT) -> str | None:
    """Why this email is not a statement, or None if it is one.

    Returning a reason rather than a bare bool is what makes the filter
    auditable: the scan can report "7 excluded: 3 marketing, 2 tax
    certificates, 2 payment advices" instead of quietly dropping them.
    """
    subject = subject or ""
    haystack = f"{sender} {subject}".lower()

    if intent == "bureau":
        for pattern, reason in REJECTION_RULES:
            if pattern.search(subject):
                return reason
        bureau_senders = institutions.query_senders(institutions.SENDS_BUREAU)
        if (any(t in haystack for t in _BUREAU_SUBJECT_TERMS)
                or any(t in haystack for t in bureau_senders)):
            return None
        return "no bureau signal"

    # 1. Explicit non-statements lose regardless of who sent them.
    for pattern, reason in REJECTION_RULES:
        if pattern.search(subject):
            return reason

    # 2. An explicit statement subject wins.
    if STATEMENT_SUBJECTS.search(subject):
        return None

    # 3. Otherwise fall back to a known statement sender.
    if any(s in haystack for s in STATEMENT_SENDERS):
        return None
    if any(k in haystack for k in STATEMENT_KEYWORDS):
        return None
    return "no statement signal"


def _looks_like_statement(sender: str, subject: str) -> bool:
    """Whether an email carries a real statement.

    Order matters. A bank sends statements, offers, advices and certificates
    from the same addresses, so the sender is the weakest signal and is only
    consulted after the subject has been cleared. Getting this wrong in either
    direction is costly: a false positive downloads a PDF that fails to parse
    and looks like a bug, a false negative silently drops a month of history.
    """
    return statement_rejection_reason(sender, subject) is None


#: Attachment filenames that are boilerplate riding along with a real statement.
#: Card issuers routinely attach terms, tariff sheets and privacy notices to the
#: same email as the statement, so a sender/subject match alone would import
#: them. They are harmless but they clutter the import list, waste bandwidth,
#: and each one fails to parse and shows up as a scary-looking error.
NON_STATEMENT_FILENAMES = re.compile(
    r"terms\s*&?\s*conditions?|most\s+important\s+terms|\bmitc\b|privacy|policy"
    r"|tariff|schedule\s+of\s+charges|service\s+charges|brochure|\bfaq\b"
    r"|welcome\s*(kit|letter)|key\s+facts?\s+statement|annexure|disclaimer",
    re.IGNORECASE,
)


def is_probable_statement_file(filename: str) -> bool:
    """Whether an attachment filename looks like an actual statement."""
    if not filename.lower().endswith(".pdf"):
        return False
    return not NON_STATEMENT_FILENAMES.search(filename)


def _pdf_attachments(message: dict[str, Any]) -> list[tuple[str, str, int]]:
    """(attachment_id, filename, size) for every statement PDF in a message."""
    out = []
    for part in _walk_parts(message.get("payload", {})):
        filename = part.get("filename") or ""
        if not is_probable_statement_file(filename):
            continue
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")
        if attachment_id:
            out.append((attachment_id, filename, body.get("size", 0)))
    return out


#: Message metadata fetches are IO-bound round trips to Google, so they
#: parallelise almost linearly. Kept modest to stay well inside Gmail's
#: per-user rate limits - going wider trades a little speed for 429s.
FETCH_WORKERS = 8


def find_statements(
    client: GmailClient,
    query: str = DEFAULT_QUERY,
    max_messages: int = 100,
    require_statement_sender: bool = True,
    progress: Any = None,
    intent: str = DEFAULT_INTENT,
) -> FetchResult:
    """List statement PDF attachments matching the query. Downloads nothing.

    This is the review step: it returns what WOULD be downloaded so the user (or
    the API caller) can confirm before any file is pulled.

    `progress(done, total)` is called as messages are fetched. Without it a
    mailbox scan looks frozen for minutes, because listing ids is instant while
    fetching each message's headers is a separate round trip.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result = FetchResult(connected=True)

    try:
        message_ids = client.list_messages(query, max_messages)
    except Exception as exc:
        result.connected = False
        result.warnings.append(f"Could not query Gmail: {exc}")
        return result

    total = len(message_ids)
    if progress:
        progress(0, total)

    def fetch(message_id: str):
        return message_id, client.get_message(message_id)

    fetched: list[tuple[str, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = [pool.submit(fetch, mid) for mid in message_ids]
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                fetched.append(future.result())
            except Exception as exc:
                result.warnings.append(f"Skipped a message: {exc}")
            if progress:
                progress(done, total)

    # Restore the mailbox's original (newest-first) order, which completion
    # order destroys - a scan list jumbled by thread timing is disorienting.
    position = {mid: i for i, mid in enumerate(message_ids)}
    fetched.sort(key=lambda pair: position.get(pair[0], 0))

    for message_id, message in fetched:
        result.scanned_messages += 1
        sender = _header(message, "From")
        subject = _header(message, "Subject")
        date = _header(message, "Date")

        if require_statement_sender:
            reason = statement_rejection_reason(sender, subject, intent=intent)
            if reason is not None:
                pdf_count = sum(
                    1 for part in _walk_parts(message.get("payload", {}))
                    if (part.get("filename") or "").lower().endswith(".pdf")
                )
                result.excluded.append(ExcludedMessage(
                    sender=sender, subject=subject, date=date,
                    reason=reason, attachment_count=pdf_count,
                ))
                continue

        for attachment_id, filename, size in _pdf_attachments(message):
            result.attachments.append(FoundAttachment(
                message_id=message_id,
                attachment_id=attachment_id,
                filename=filename,
                sender=sender,
                subject=subject,
                date=date,
                size=size,
            ))

    return result


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def download_attachments(
    client: GmailClient,
    attachments: list[FoundAttachment],
    dest_dir: Path,
) -> list[FoundAttachment]:
    """Download the given attachments to disk. Fills in `saved_path`.

    Only downloads what it is given - the explicit list from `find_statements`,
    which the caller has had the chance to review. Filenames are sanitised and
    de-duplicated so a hostile or repeated name cannot escape the folder.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[FoundAttachment] = []

    for att in attachments:
        try:
            data = client.get_attachment(att.message_id, att.attachment_id)
        except Exception as exc:
            log.warning("attachment download failed: %s", exc)
            continue

        safe = _SAFE_NAME.sub("_", Path(att.filename).name) or "statement.pdf"
        target = dest_dir / safe
        counter = 1
        while target.exists():
            target = dest_dir / f"{target.stem}_{counter}{target.suffix}"
            counter += 1

        target.write_bytes(data)
        att.saved_path = str(target)
        saved.append(att)

    return saved


def download_to_cache(
    client: GmailClient,
    attachments: list[FoundAttachment],
    cache_dir: Path,
    progress: Any = None,
) -> list[FoundAttachment]:
    """Download into a persistent cache, skipping anything already there.

    Statements never change once sent, so re-downloading them is pure waste -
    for a mailbox with years of history that is tens of megabytes on every run.
    The cache is keyed on Gmail's own immutable message/attachment ids, so a
    later scan of the same mailbox re-uses every file it already has.

    Returns every requested attachment with `saved_path` set, whether it was
    downloaded now or already cached.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: list[FoundAttachment] = []

    for i, att in enumerate(attachments, start=1):
        target = cache_dir / att.cache_key()

        # Glob rather than an exact-name check, so files cached under an earlier
        # key scheme are still found instead of being downloaded again.
        existing = next(
            (p for p in cache_dir.glob(att.cache_glob()) if p.stat().st_size > 0),
            target if target.exists() and target.stat().st_size > 0 else None,
        )
        if existing is not None:
            att.saved_path = str(existing)
            att.from_cache = True
            out.append(att)
            if progress:
                progress(i, len(attachments), att, True)
            continue

        try:
            data = client.get_attachment(att.message_id, att.attachment_id)
        except Exception as exc:
            log.warning("download failed for %s: %s", att.filename, exc)
            continue

        target.write_bytes(data)
        att.saved_path = str(target)
        att.from_cache = False
        out.append(att)
        if progress:
            progress(i, len(attachments), att, False)

    return out


# --------------------------------------------------------------------------
# Fake client for offline testing
# --------------------------------------------------------------------------

class FakeGmailClient:
    """In-memory Gmail stand-in.

    Lets the whole fetch->download->analyse path be tested without a network,
    a Google project, or real credentials. Seeded with synthetic messages that
    carry real bytes, so downloads produce genuinely parseable files.
    """

    def __init__(self, messages: list[dict[str, Any]]):
        self._messages = {m["id"]: m for m in messages}

    @classmethod
    def from_files(cls, files: list[tuple[str, str, str, bytes]]):
        """Build from (sender, subject, filename, pdf_bytes) tuples."""
        messages = []
        for i, (sender, subject, filename, data) in enumerate(files):
            messages.append({
                "id": f"msg{i}",
                "payload": {
                    "headers": [
                        {"name": "From", "value": sender},
                        {"name": "Subject", "value": subject},
                        {"name": "Date", "value": "Mon, 01 Sep 2025 09:00:00 +0530"},
                    ],
                    "parts": [{
                        "filename": filename,
                        "body": {"attachmentId": f"att{i}", "size": len(data)},
                    }],
                },
                "_attachments": {f"att{i}": data},
            })
        return cls(messages)

    def list_messages(self, query: str, max_results: int) -> list[str]:
        return list(self._messages.keys())[:max_results]

    def get_message(self, message_id: str) -> dict[str, Any]:
        return self._messages[message_id]

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        return self._messages[message_id]["_attachments"][attachment_id]


# --------------------------------------------------------------------------
# Transaction alerts
#
# A different shape of fetch from `find_statements`: the payload is the body of
# the email, not a file hanging off it. Everything else - the paging, the
# thread pool, the progress reporting - is the same problem, so the same
# approach is used rather than a second mechanism.
# --------------------------------------------------------------------------

@dataclass
class FoundAlert:
    """One alert email, and whatever could be read out of it."""

    message_id: str
    sender: str
    subject: str
    date: str
    body: str
    #: Set by the caller once txn_email has had a look at it.
    parsed: Any = None
    skip_reason: str = ""

    @property
    def institution(self) -> str:
        return institution_for_sender(self.sender)


def _decode_body(data: str) -> str:
    """Gmail's base64url body payload as text."""
    import base64
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
            "utf-8", errors="replace")
    except Exception:
        return ""


def message_body(message: dict[str, Any]) -> str:
    """The readable body of an email, plain text preferred over HTML.

    Both are collected because banks are inconsistent about which they fill:
    some send the sentence only in the HTML part, some only in the plain part,
    and a reader that takes the first part it finds gets an empty string from
    roughly half of them.
    """
    plain, html_parts = [], []
    for part in _walk_parts(message.get("payload", {})):
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        if mime == "text/plain":
            plain.append(_decode_body(data))
        elif mime == "text/html":
            html_parts.append(_decode_body(data))

    if plain and any(p.strip() for p in plain):
        return "\n".join(plain)
    return "\n".join(html_parts)


@dataclass
class AlertFetchResult:
    connected: bool = True
    scanned_messages: int = 0
    alerts: list[FoundAlert] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def find_alerts(
    client: GmailClient,
    query: str = "",
    max_messages: int = 200,
    progress: Any = None,
) -> AlertFetchResult:
    """Fetch transaction alert emails and return their bodies.

    Downloads nothing and writes nothing: this is the review step, exactly as
    `find_statements` is for attachments. What comes back is what WOULD be
    imported, so a list of unreconciled figures can be looked at before any of
    them joins the ledger.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result = AlertFetchResult()
    query = query or build_query(intent="transactional")

    try:
        message_ids = client.list_messages(query, max_messages)
    except Exception as exc:
        result.connected = False
        result.warnings.append(f"Could not query Gmail: {exc}")
        return result

    total = len(message_ids)
    if progress:
        progress(0, total)

    def fetch(message_id: str):
        return message_id, client.get_message(message_id)

    fetched: list[tuple[str, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = [pool.submit(fetch, mid) for mid in message_ids]
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                fetched.append(future.result())
            except Exception as exc:
                result.warnings.append(f"Skipped a message: {exc}")
            if progress:
                progress(done, total)

    # Newest-first, as the mailbox had them; completion order is thread timing.
    position = {mid: i for i, mid in enumerate(message_ids)}
    fetched.sort(key=lambda pair: position.get(pair[0], 0))

    for message_id, message in fetched:
        result.scanned_messages += 1
        sender = _header(message, "From")
        subject = _header(message, "Subject")

        # Marketing reaches this query as easily as it reaches the statement
        # one - banks advertise from the address that sends alerts - and an
        # offer email mentioning an amount is exactly the shape that produces
        # a plausible, invented transaction.
        if PROMOTIONAL_SUBJECTS.search(subject or "") or _EMOJI.search(subject or ""):
            continue

        body = message_body(message)
        if not body.strip():
            continue

        result.alerts.append(FoundAlert(
            message_id=message_id, sender=sender, subject=subject,
            date=_header(message, "Date"), body=body,
        ))

    return result
