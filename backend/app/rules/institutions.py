"""Every financial institution this app knows, one record each.

WHY THIS FILE EXISTS
--------------------
The same knowledge - "HDFC Bank exists, its mail comes from hdfcbank.com, it
sends statements and alerts, and it locks its PDFs with name+DDMM" - used to be
spread across fourteen separate lists in four modules:

    gmail_source.STATEMENT_SENDERS          metadata.INSTITUTIONS
    gmail_source._FROM_TERMS                passwords.PASSWORD_RULES
    gmail_source._ALERT_FROM_TERMS          bureau.BUREAU_SIGNATURES
    gmail_source._INVESTMENT_FROM_TERMS     portfolio.LAYOUT_SIGNATURES
    gmail_source._BUREAU_FROM_TERMS
    gmail_source.SENDER_CATEGORIES[bank|card|loan|bureau|broker]

303 entries, 138 of them distinct - and no test that any two lists agreed. They
did not. HDFC was "hdfc" in two of them and "hdfcbank" in three others, and
neither spelling knew about the other. Bank of Baroda could be found by an
alert scan but could not be named, so it displayed as a raw domain. Motilal
Oswal, Sharekhan, ICICI Direct and Kotak Securities were all scanned for and
none of them could be named. Nine institutions the app could name were never
searched for at all.

Adding a bank meant finding and editing up to ten lists. Missing one produced a
silent, specific bug days later - a statement that downloads but files itself
under "estatement", an alert refused for an account that "does not exist".

So: one institution, one record, every list derived. Adding a bank is one entry
here and nothing else. `test_rules.py` asserts the derived lists still cover
what the hand-written ones did.

HOW TO ADD ONE
--------------
Append an `Institution(...)`. Only `name`, `kind` and `match` are required.

    match     - fragments looked for inside a lowercased sender address AND
                inside statement text. Include every spelling you have seen:
                "yesbank", "yes.bank" and "yes bank" are three different
                mailers at one bank. Longest match wins when two records could
                both claim a sender, so a more specific fragment beats a
                shorter one that is a prefix of it.
    sends     - which scans should look for this issuer's mail. An issuer left
                out of a scan is never found by it.
    password  - the format the issuer locks its PDFs with, if known. See
                `rules.passwords`.

Two records may share a `name`: HDFC Bank and its housing-loan arm are one
brand and two lenders, and `kind` is what routes a document, so they cannot be
one record.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: What kind of account an issuer's mail is about. This is what
#: `classify_sender` returns, and it is checked most-specific first: a card
#: mailer at a bank's own domain must classify as a card, not as that bank's
#: savings account. WALLET is deliberately outside that order - a wallet is
#: not one of the categories the import filter offers.
KIND_BUREAU = "bureau"
KIND_CARD = "card"
KIND_LOAN = "loan"
KIND_BROKER = "broker"
KIND_BANK = "bank"
KIND_WALLET = "wallet"

#: The order `classify_sender` tries. Unlisted kinds are never returned by it.
CLASSIFY_ORDER = (KIND_BUREAU, KIND_CARD, KIND_LOAN, KIND_BROKER, KIND_BANK)

#: A scan is a question, and each one wants a different set of issuers. These
#: match the keys of `gmail_source.SCAN_INTENTS`.
SENDS_STATEMENT = "statement"
SENDS_BUREAU = "bureau"
SENDS_INVESTMENT = "investment"
SENDS_ALERT = "transactional"


@dataclass(frozen=True)
class Institution:
    #: Canonical display name. Shown to the user; two records may share one.
    name: str
    #: One of the KIND_* values above.
    kind: str
    #: Fragments matched against a lowercased sender address or document text.
    match: tuple[str, ...]
    #: Which scans should look for this issuer. Empty means "never searched
    #: for by name" - it can still be recognised in a document it appears in.
    sends: tuple[str, ...] = ()
    #: PDF password format label, from `rules.passwords.FORMATS`.
    password: str | None = None
    #: Issuer-specific detail appended to the format's explanation - the
    #: casing HDFC and ICICI each document, for instance. The format stays
    #: shared; only the note differs.
    password_note: str = ""
    #: Set only on the four real credit bureaus. `bureau.detect_bureau`
    #: returns this.
    bureau_key: str | None = None
    #: Set on issuers whose holdings statements have a distinct layout.
    #: `portfolio.detect_layout` returns (this, name).
    portfolio_layout: str | None = None


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

_ALL = (SENDS_STATEMENT, SENDS_ALERT)

REGISTRY: tuple[Institution, ...] = (
    # ---- Banks -----------------------------------------------------------
    Institution("HDFC Bank", KIND_BANK, ("hdfc", "hdfcbank"), _ALL,
                password="Name(4) + DDMM", password_note="name in CAPS"),
    Institution("ICICI Bank", KIND_BANK, ("icici", "icicibank", "icici.bank"),
                _ALL, password="Name(4) + DDMM",
                password_note="name in lowercase"),
    Institution("State Bank of India", KIND_BANK,
                ("sbi", "onlinesbi", "sbi.bank", "alerts.sbi",
                 "state bank of india"), _ALL, password="DDMMYYYY"),
    Institution("Axis Bank", KIND_BANK, ("axis", "axisbank", "axis.bank"),
                _ALL, password="Name(4) + DDMM"),
    Institution("Kotak Mahindra Bank", KIND_BANK, ("kotak",), _ALL,
                password="Name(4) + DDMM"),
    Institution("Yes Bank", KIND_BANK, ("yes", "yesbank", "yes.bank",
                                        "yes bank"), _ALL,
                password="Name(4) + DDMM"),
    Institution("IndusInd Bank", KIND_BANK, ("indusind",), _ALL,
                password="Name(4) + DDMM"),
    Institution("IDFC First Bank", KIND_BANK,
                ("idfc", "idfcfirst", "idfcfirstbank", "idfc first"), _ALL,
                password="Mobile(10)"),
    Institution("Punjab National Bank", KIND_BANK,
                ("pnb", "punjab", "punjab national", "pnbmail", "pnb.bank"),
                _ALL, password="DDMMYYYY"),
    Institution("Bank of Baroda", KIND_BANK,
                ("bob", "baroda", "bankofbaroda", "bank of baroda",
                 "bob.bank", "bobworld"), _ALL, password="Name(4) + DDMM"),
    Institution("Canara Bank", KIND_BANK, ("canara", "canarabank"), _ALL),
    Institution("Union Bank of India", KIND_BANK,
                ("unionbank", "union bank"), _ALL),
    Institution("RBL Bank", KIND_BANK, ("rbl", "rbl.bank", "rblbank"), _ALL,
                password="Name(4) + DDMM"),
    Institution("Federal Bank", KIND_BANK, ("federalbank", "federal bank"),
                _ALL),
    Institution("AU Small Finance Bank", KIND_BANK, ("aubank", "au small"),
                _ALL),
    Institution("Bandhan Bank", KIND_BANK, ("bandhan", "bandhanbank"), _ALL),
    Institution("South Indian Bank", KIND_BANK,
                ("southindianbank", "south indian bank"), _ALL),
    Institution("DBS Bank", KIND_BANK, ("dbs",), (SENDS_STATEMENT,)),
    Institution("Citibank", KIND_BANK, ("citi", "citibank"),
                (SENDS_STATEMENT,)),
    Institution("Standard Chartered", KIND_BANK,
                ("sc.com", "standard chartered"), (SENDS_STATEMENT,)),

    # ---- Cards -----------------------------------------------------------
    # A card issuer's mail must classify as a card even when it arrives from
    # the parent bank's domain, which is why these are separate records
    # rather than a flag on the bank.
    Institution("American Express", KIND_CARD,
                ("amex", "americanexpress", "american express"), _ALL,
                password="Card(4) + DDMM"),
    Institution("HSBC", KIND_CARD, ("hsbc",), _ALL, password="DDMMYYYY"),
    # The password these four resolved to before the registry existed, when
    # a bare "sbi"/"bob"/"icici"/"kotak" fragment reached them by accident.
    # Kept as an explicit, unverified inheritance rather than a silent one -
    # the hint is advisory, and every candidate is tried regardless.
    Institution("SBI Card", KIND_CARD, ("sbicard", "sbi card"), _ALL,
                password="DDMMYYYY"),
    Institution("BOBCARD", KIND_CARD, ("bobcard",), _ALL,
                password="Name(4) + DDMM"),
    Institution("OneCard", KIND_CARD, ("onecard",), _ALL),
    Institution("slice", KIND_CARD, ("slice", "slice.bank"), _ALL,
                password="PAN"),
    # "cred" alone would match inside "credit" on every card statement
    # printed; the club suffix is what makes it a name.
    Institution("CRED", KIND_CARD, ("cred.club", "cred club"),
                (SENDS_ALERT,)),

    # ---- Lenders ---------------------------------------------------------
    Institution("Bajaj Finserv", KIND_LOAN, ("bajaj", "bajajfinserv"),
                (SENDS_STATEMENT,), password="Name(4) + DDMM"),
    Institution("Tata Capital", KIND_LOAN, ("tatacapital", "tata capital"),
                (SENDS_STATEMENT,), password="Name(4) + DDMM"),
    Institution("LIC Housing Finance", KIND_LOAN,
                ("lichousing", "lic housing"), (SENDS_STATEMENT,),
                password="Name(4) + DDMM"),
    # HDFC's housing arm. Same brand, different lender, and `kind` is what
    # routes the document - so it cannot share the bank's record.
    Institution("HDFC Bank", KIND_LOAN, ("hdfcltd",), (SENDS_STATEMENT,),
                password="Name(4) + DDMM"),

    # ---- Brokers, registrars and depositories ----------------------------
    Institution("Zerodha", KIND_BROKER, ("zerodha",),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN",
                portfolio_layout="broker"),
    Institution("Groww", KIND_BROKER, ("groww",),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN",
                portfolio_layout="broker"),
    Institution("Upstox", KIND_BROKER, ("upstox",),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN",
                portfolio_layout="broker"),
    Institution("5paisa", KIND_BROKER, ("5paisa",),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN",
                portfolio_layout="broker"),
    Institution("Dhan", KIND_BROKER, ("dhan", "dhan.co"),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN"),
    Institution("Paytm Money", KIND_BROKER, ("paytmmoney", "paytm money"),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN"),
    Institution("Angel One", KIND_BROKER,
                ("angel", "angelone", "angel one", "angelbroking",
                 "angeltrade"), (SENDS_STATEMENT, SENDS_INVESTMENT),
                password="PAN", portfolio_layout="broker"),
    Institution("ICICI Direct", KIND_BROKER, ("icicidirect", "icici direct"),
                (SENDS_INVESTMENT,), password="Name(4) + DDMM",
                portfolio_layout="broker"),
    Institution("Kotak Securities", KIND_BROKER,
                ("kotaksecurities", "kotak securities"), (SENDS_INVESTMENT,),
                password="Name(4) + DDMM", portfolio_layout="broker"),
    Institution("Sharekhan", KIND_BROKER, ("sharekhan",), (SENDS_INVESTMENT,)),
    Institution("Motilal Oswal", KIND_BROKER,
                ("motilaloswal", "motilal oswal"), (SENDS_INVESTMENT,)),
    Institution("CAMS", KIND_BROKER, ("cams", "cams.", "camsonline", "karvy"),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN",
                portfolio_layout="cams"),
    Institution("KFintech", KIND_BROKER, ("kfintech", "kfin technologies"),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN",
                portfolio_layout="kfintech"),
    Institution("NSDL", KIND_BROKER, ("nsdl",),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN",
                portfolio_layout="cas"),
    Institution("CDSL", KIND_BROKER,
                ("cdsl", "cdslindia", "cdslstatement"),
                (SENDS_STATEMENT, SENDS_INVESTMENT), password="PAN",
                portfolio_layout="cas"),
    Institution("Protean NPS", KIND_BROKER, ("protean", "proteantech"),
                (SENDS_INVESTMENT,), password="PAN"),
    Institution("MF Central", KIND_BROKER, ("mfcentral", "mf central"),
                (SENDS_INVESTMENT,), password="PAN"),

    # ---- Credit bureaus --------------------------------------------------
    Institution("CIBIL", KIND_BUREAU, ("cibil", "transunion"),
                (SENDS_BUREAU,), password="DDMMYYYY", bureau_key="cibil"),
    Institution("CRIF High Mark", KIND_BUREAU,
                ("crif", "crifhighmark", "high mark", "highmark"),
                (SENDS_BUREAU,), password="DDMMYYYY", bureau_key="crif"),
    Institution("Experian", KIND_BUREAU, ("experian",), (SENDS_BUREAU,),
                password="PAN", bureau_key="experian"),
    Institution("Equifax", KIND_BUREAU, ("equifax",), (SENDS_BUREAU,),
                password="PAN", bureau_key="equifax"),
    # Score apps. They mail about your credit file without being a bureau, so
    # a scan should find them but `detect_bureau` must not name one.
    Institution("OneScore", KIND_BUREAU, ("onescore",), (SENDS_BUREAU,)),
    Institution("CreditVidya", KIND_BUREAU, ("creditvidya",), (SENDS_BUREAU,)),

    # ---- Wallets ---------------------------------------------------------
    # Outside CLASSIFY_ORDER on purpose: a wallet is not one of the account
    # categories the import filter offers, so classifying a sender as one
    # would hide it rather than describe it.
    Institution("Paytm", KIND_WALLET, ("paytm", "paytmbank"), (SENDS_ALERT,)),
    Institution("PhonePe", KIND_WALLET, ("phonepe",)),
    Institution("Razorpay", KIND_WALLET, ("razorpay",)),
)


# --------------------------------------------------------------------------
# Fragments that are not institutions
#
# "estatement", "noreply" and "cards@" name a MAILBOX, not a bank. They earn
# their place because a great many issuers use them and no list of names will
# ever cover the long tail, but they say nothing about who sent the mail - so
# they are kept apart from the registry rather than faked into it as
# institutions with no name.
# --------------------------------------------------------------------------

#: Mailbox-shaped fragments, by the scan they help. Same shape as `sends`.
GENERIC_SENDERS: dict[str, tuple[str, ...]] = {
    SENDS_STATEMENT: (
        "bank", "creditcard", "creditcardstatement", "statements",
        "estatement", "e-statement", "loanestatement", "alerts", "noreply",
        "donotreply",
    ),
    SENDS_BUREAU: ("creditreport", "creditscore", "bureau"),
}

#: The `from:` terms a statement scan puts in its GMAIL QUERY, as opposed to
#: the much longer list it accepts locally.
#:
#: These two are deliberately not the same list. Statements arrive from every
#: institution that exists, so naming them one by one in the query would still
#: miss the long tail while making the query enormous; a handful of generic
#: mailbox words casts a wider net for less. The precise filtering then happens
#: locally in `statement_rejection_reason`, where the full registry is
#: available and a wrong answer costs nothing.
#:
#: The other three scans do name their issuers in the query, because alerts,
#: bureau reports and holdings statements come from a knowable set - and there
#: the generic words are actively harmful: "noreply" and "alerts" are how every
#: SaaS product on earth addresses you, and a scan carrying them came back with
#: LinkedIn, Zoom and a jobs board.
STATEMENT_QUERY_SENDERS: tuple[str, ...] = (
    "bank", "creditcard", "creditcardstatement", "statements", "estatement",
    "e-statement", "loanestatement", "cams", "kfintech", "cdsl", "nsdl",
    "alerts", "noreply", "donotreply",
)

#: Mailbox fragments that also say what KIND of account the mail is about.
GENERIC_KINDS: dict[str, tuple[str, ...]] = {
    KIND_BANK: ("estatement", "statement@"),
    KIND_CARD: ("cards@", "creditcard", "creditcardstatement"),
    KIND_LOAN: ("loanestatement", "loanstatement", "homeloan"),
    KIND_BUREAU: ("creditreport", "creditscore"),
}

#: Fragments a plain sender-name match should treat as a statement signal.
#: Broader than GENERIC_SENDERS because this one runs only after the subject
#: has already been cleared, so a false positive costs a download, not a
#: misfiled statement.
EXTRA_STATEMENT_SENDERS: tuple[str, ...] = (
    "statements", "estatement", "e-statement", "creditcard",
)


# --------------------------------------------------------------------------
# Derived views
#
# Everything below is computed. Nothing downstream should hand-maintain a
# parallel list - if you need a new slice of the registry, add a helper here.
# --------------------------------------------------------------------------

def fragments_for_scan(intent: str) -> tuple[str, ...]:
    """Sender fragments that mark mail as relevant to `intent`.

    Institutions that send that kind of mail, plus the generic mailbox names
    that help find the long tail. This is the LOCAL acceptance list; what goes
    into the Gmail query is `query_senders`.
    """
    named = {f for inst in REGISTRY if intent in inst.sends for f in inst.match}
    if intent == SENDS_STATEMENT:
        # NOT GENERIC_SENDERS: that list is for the query, where a false
        # positive costs one wasted fetch. Here it would accept every
        # noreply@ address in the mailbox as a bank.
        named |= set(EXTRA_STATEMENT_SENDERS)
    elif intent == SENDS_BUREAU:
        named |= set(GENERIC_SENDERS.get(intent, ()))
    return tuple(sorted(named))


def query_senders(intent: str) -> tuple[str, ...]:
    """The `from:` terms this scan puts in its Gmail query.

    Statements use a short generic list and filter locally; every other scan
    names its issuers. See STATEMENT_QUERY_SENDERS for why.
    """
    if intent == SENDS_STATEMENT:
        return STATEMENT_QUERY_SENDERS
    named = tuple(f for inst in REGISTRY if intent in inst.sends
                  for f in inst.match if " " not in f)
    return named + GENERIC_SENDERS.get(intent, ())


def fragments_for_kind(kind: str) -> tuple[str, ...]:
    """Sender fragments that mean "this mail is about a `kind` account"."""
    named = [f for inst in REGISTRY if inst.kind == kind for f in inst.match]
    return tuple(sorted(set(named) | set(GENERIC_KINDS.get(kind, ()))))


def display_names() -> dict[str, str]:
    """Fragment -> canonical name, for resolving a sender or a letterhead.

    Longest fragment wins at lookup time, so "bank of baroda" beats a stray
    "bank" and "hdfcltd" beats "hdfc".
    """
    return {frag: inst.name for inst in REGISTRY for frag in inst.match}


def name_for(text: str) -> str | None:
    """The institution named in `text`, or None. Longest match wins."""
    lowered = (text or "").lower()
    best_len, best_name = 0, None
    for frag, name in display_names().items():
        if frag in lowered and len(frag) > best_len:
            best_len, best_name = len(frag), name
    return best_name


def _all_fragments() -> tuple[str, ...]:
    named = {f for inst in REGISTRY for f in inst.match}
    generic = {f for frags in GENERIC_KINDS.values() for f in frags}
    return tuple(named | generic)


def unshadowed(text: str) -> set[str]:
    """Fragments present in `text` that no longer present fragment contains.

    Substring matching is what makes a fragment list cheap, and it is also
    how it goes wrong: "dhan" is a broker and it sits inside "bandhanbank",
    so a plain any()-over-a-list classified Bandhan Bank as a brokerage. The
    same shape put Bandhan's PDFs on the broker password format.

    Length is the tie-breaker precisely because a longer fragment is a more
    specific claim about the same text. "icicidirect" beats "icici";
    "bandhan" beats "dhan"; "hdfcltd" beats "hdfc".

    It is NOT a substitute for CLASSIFY_ORDER, which still decides between
    two fragments that merely coexist: "cards@hdfcbank.com" contains both
    "cards@" and "hdfc", neither inside the other, and it is a card.
    """
    lowered = (text or "").lower()
    present = [f for f in _all_fragments() if f in lowered]
    return {f for f in present
            if not any(len(o) > len(f) and f in o for o in present)}


def classify(text: str) -> str:
    """bank / card / loan / broker / bureau for a sender, or "unknown".

    Checked most-specific first - see CLASSIFY_ORDER.
    """
    live = unshadowed(text)
    for kind in CLASSIFY_ORDER:
        if live & set(fragments_for_kind(kind)):
            return kind
    return "unknown"


def bureau_signatures() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(bureau key, fragments) for the four real bureaus, in registry order."""
    return tuple((inst.bureau_key, inst.match)
                 for inst in REGISTRY if inst.bureau_key)


#: (layout, display provider, phrases that are not an issuer's name), in the
#: order `portfolio.detect_layout` tries them - first match wins, so the
#: depository layout must be tested before the generic broker one. A CAS from
#: CDSL names the brokers whose holdings it consolidates, and reading it as a
#: broker's own statement picks the wrong column map.
LAYOUT_ORDER: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("cas", "CDSL/NSDL",
     ("consolidated account statement", "demat account", "depository")),
    ("cams", "CAMS", ("consolidated portfolio",)),
    ("kfintech", "KFintech", ()),
    ("broker", "Broker", ("holdings statement", "portfolio holdings")),
)


def portfolio_layouts() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """(layout, provider, fragments) for `portfolio.detect_layout`.

    Each layout's issuers come from the registry; the document phrases that
    identify it without naming anyone come from LAYOUT_ORDER.
    """
    out = []
    for layout, provider, phrases in LAYOUT_ORDER:
        issuers = tuple(f for inst in REGISTRY
                        if inst.portfolio_layout == layout for f in inst.match)
        out.append((layout, provider, phrases + issuers))
    return tuple(out)


#: Fragments that are ordinary English words, or too short to be distinctive,
#: and so must never be used as a whole-word match inside a narration. They
#: work fine inside an email address, where the surrounding text is a domain.
_NOT_A_NARRATION_WORD = frozenset({
    "yes", "bob", "citi", "dbs", "sbi", "rbl", "axis", "cams", "dhan", "na",
    "angel", "select", "prime", "elite", "wealth", "freedom",
})


def narration_words() -> tuple[str, ...]:
    """Issuer tokens safe to match as whole words inside a transaction
    narration.

    A narration is prose written by the bank's own system, so a fragment that
    is also an English word will fire on sentences that have nothing to do
    with that bank. Anything short, spaced, punctuated or listed in
    `_NOT_A_NARRATION_WORD` is left out; what survives is a name no narration
    contains by accident.
    """
    words = set()
    for inst in REGISTRY:
        for frag in inst.match:
            if (len(frag) >= 4 and frag.isalnum()
                    and frag not in _NOT_A_NARRATION_WORD):
                words.add(frag.upper())
    return tuple(sorted(words))


def password_rules() -> tuple[tuple[tuple[str, ...], str, str], ...]:
    """(fragments, format label, issuer note) for every documented issuer."""
    return tuple((inst.match, inst.password, inst.password_note)
                 for inst in REGISTRY if inst.password)


def password_note_for(text: str) -> str:
    """The issuer-specific password detail for this sender, or ""."""
    live = unshadowed(text)
    for inst in REGISTRY:
        if inst.password and live & set(inst.match):
            return inst.password_note
    return ""


def password_format_for(text: str) -> str | None:
    """The PDF password format for whoever sent this, or None.

    Uses `unshadowed` for the same reason `classify` does: the old flat list
    matched "dhan" inside "bandhanbank" and told the user their Bandhan Bank
    statement was locked with their PAN.
    """
    live = unshadowed(text)
    for inst in REGISTRY:
        if inst.password and live & set(inst.match):
            return inst.password
    return None
