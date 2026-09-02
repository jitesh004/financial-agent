"""Guards on the rule registries.

These are not tests of behaviour so much as tests of *consistency*. The bugs
they exist to catch all have the same shape: two lists that describe the same
institution, edited at different times, quietly disagreeing. That failure is
invisible at the point it is made and shows up days later as "this statement
downloads but files itself under a raw domain name".
"""

from __future__ import annotations

import pathlib
import re
from datetime import date
from decimal import Decimal

import pytest

from app.categorize import rules as category_rules
from app.ingestion import bureau, gmail_source, passwords, portfolio, txn_email
from app.normalize import metadata, parsers
from app.rules import institutions as inst
from app.rules import formats
from app.rules import passwords as pw_formats


# --------------------------------------------------------------------------
# The registry is internally coherent
# --------------------------------------------------------------------------

def test_every_kind_is_a_known_kind():
    known = {inst.KIND_BANK, inst.KIND_CARD, inst.KIND_LOAN, inst.KIND_BROKER,
             inst.KIND_BUREAU, inst.KIND_WALLET}
    unknown = {i.name: i.kind for i in inst.REGISTRY if i.kind not in known}
    assert not unknown


def test_every_scan_target_is_a_real_scan():
    """An issuer whose `sends` names a scan that does not exist is never found
    by anything, and nothing says so."""
    known = set(gmail_source.SCAN_INTENTS)
    bad = {i.name: sorted(set(i.sends) - known) for i in inst.REGISTRY
           if set(i.sends) - known}
    assert not bad


def test_every_password_label_is_a_defined_format():
    known = set(pw_formats.BY_LABEL)
    bad = {i.name: i.password for i in inst.REGISTRY
           if i.password and i.password not in known}
    assert not bad


def test_every_institution_recognises_its_own_printed_name():
    """A fragment list that cannot match the issuer's own name on a letterhead
    cannot identify its statements. Domains have no spaces; letterheads do."""
    # CRED is the documented exception: "cred" matches inside "credit", which
    # appears on every card statement printed.
    exempt = {"CRED"}
    bad = {i.name: inst.name_for(i.name) for i in inst.REGISTRY
           if i.name not in exempt and inst.name_for(i.name) != i.name}
    assert not bad


def test_no_fragment_is_shadowed_within_its_own_kind():
    """Two records of the SAME kind whose fragments nest are harmless, but two
    of DIFFERENT kinds are how Bandhan Bank ("ban-dhan-bank") was classified as
    a brokerage by the Dhan record. `unshadowed` handles it at lookup time;
    this asserts the handling actually holds."""
    for a in inst.REGISTRY:
        for frag in a.match:
            for b in inst.REGISTRY:
                if b is a or b.kind == a.kind:
                    continue
                for other in b.match:
                    if frag != other and frag in other:
                        assert frag not in inst.unshadowed(other), (
                            f"{frag!r} ({a.name}) leaks into {other!r} ({b.name})")


def test_bandhan_bank_is_a_bank_not_a_broker():
    """The concrete case behind `unshadowed`, kept as its own test so the
    reason survives even if the general one is ever relaxed."""
    assert inst.classify("alerts@bandhanbank.co.in") == inst.KIND_BANK
    assert inst.password_format_for("alerts@bandhanbank.co.in") is None


def test_query_senders_contain_no_spaces():
    """A Gmail `from:` clause matches an address, and addresses have no
    spaces - so a spaced fragment is query length with no reach."""
    for intent in gmail_source.SCAN_INTENTS:
        spaced = [f for f in inst.query_senders(intent) if " " in f]
        assert not spaced, f"{intent}: {spaced}"


def test_statement_acceptance_admits_no_generic_mailer_words():
    """The query may say "noreply" because a false positive there costs one
    fetch. The local acceptance test may not: it would admit every automated
    mailer in the mailbox as a bank."""
    accepted = set(inst.fragments_for_scan(inst.SENDS_STATEMENT))
    assert not accepted & {"noreply", "donotreply", "alerts", "bank"}


# --------------------------------------------------------------------------
# The derived lists still cover what the hand-written ones did
#
# The literals below are the lists as they stood before the registry existed.
# They are a floor, not a specification: the registry may know MORE than they
# did (it does), but losing one of these means an institution silently stopped
# being found.
# --------------------------------------------------------------------------

_WAS_STATEMENT_SENDERS = {
    "hdfcbank", "icicibank", "axisbank", "sbi", "onlinesbi", "kotak",
    "yesbank", "indusind", "idfcfirstbank", "pnb", "bankofbaroda",
    "canarabank", "sc.com", "citi", "americanexpress", "bajajfinserv",
    "tatacapital", "cams", "kfintech", "camsonline", "nsdl", "cdsl",
    "zerodha", "groww", "angelone", "angelbroking", "upstox", "5paisa",
    "dhan.co", "paytmmoney", "statements", "estatement", "e-statement",
    "creditcard",
}

_WAS_ALERT_SENDERS = {
    "hdfcbank", "icicibank", "icici.bank", "sbi.bank", "onlinesbi",
    "alerts.sbi", "axisbank", "axis.bank", "kotak", "yes.bank", "yesbank",
    "indusind", "idfcfirst", "idfc", "pnb", "bankofbaroda", "bob.bank",
    "bobworld", "canarabank", "unionbank", "rblbank", "rbl.bank",
    "federalbank", "aubank", "bandhanbank", "southindianbank", "hsbc",
    "sbicard", "bobcard", "onecard", "amex", "americanexpress", "slice",
    "cred.club", "paytmbank",
}

_WAS_INVESTMENT_SENDERS = {
    "zerodha", "upstox", "5paisa", "dhan.co", "paytmmoney", "angelbroking",
    "angeltrade", "groww", "icicidirect", "kotaksecurities", "sharekhan",
    "motilaloswal", "angelone", "cdslstatement", "cdslindia", "nsdl",
    "proteantech", "kfintech", "camsonline", "cams.", "mfcentral",
}

_WAS_BUREAU_SENDERS = {
    "cibil", "transunion", "crif", "crifhighmark", "experian", "equifax",
    "creditreport", "creditscore", "onescore", "creditvidya", "bureau",
}


@pytest.mark.parametrize("intent, was", [
    (inst.SENDS_STATEMENT, _WAS_STATEMENT_SENDERS),
    (inst.SENDS_ALERT, _WAS_ALERT_SENDERS),
    (inst.SENDS_INVESTMENT, _WAS_INVESTMENT_SENDERS),
    (inst.SENDS_BUREAU, _WAS_BUREAU_SENDERS),
])
def test_scan_still_looks_for_everything_it_used_to(intent, was):
    now = set(inst.fragments_for_scan(intent)) | set(inst.query_senders(intent))
    assert not was - now


_WAS_INSTITUTIONS = {
    "hdfc": "HDFC Bank", "icici": "ICICI Bank", "sbi": "State Bank of India",
    "state bank of india": "State Bank of India", "axis": "Axis Bank",
    "kotak": "Kotak Mahindra Bank", "yes bank": "Yes Bank",
    "yesbank": "Yes Bank", "yes.bank": "Yes Bank",
    "indusind": "IndusInd Bank", "idfc": "IDFC First Bank",
    "idfc first": "IDFC First Bank", "idfcfirst": "IDFC First Bank",
    "punjab national": "Punjab National Bank",
    "bank of baroda": "Bank of Baroda", "canara": "Canara Bank",
    "union bank": "Union Bank of India", "citibank": "Citibank",
    "standard chartered": "Standard Chartered",
    "american express": "American Express", "amex": "American Express",
    "bajaj": "Bajaj Finserv", "tata capital": "Tata Capital",
    "lic housing": "LIC Housing Finance", "cred.club": "CRED",
    "paytm": "Paytm", "phonepe": "PhonePe", "razorpay": "Razorpay",
    "zerodha": "Zerodha", "groww": "Groww", "cams": "CAMS",
    "kfintech": "KFintech", "hsbc": "HSBC", "rbl": "RBL Bank",
    "slice": "slice", "bobcard": "BOBCARD", "au small": "AU Small Finance Bank",
    "aubank": "AU Small Finance Bank", "bandhan": "Bandhan Bank",
    "federal bank": "Federal Bank", "federalbank": "Federal Bank",
    "dbs": "DBS Bank", "onecard": "OneCard", "sbicard": "SBI Card",
    "dhan": "Dhan", "upstox": "Upstox", "5paisa": "5paisa",
    "angelone": "Angel One", "paytmmoney": "Paytm Money",
    "protean": "Protean NPS",
}


def test_every_name_the_app_used_to_resolve_still_resolves():
    for fragment, expected in _WAS_INSTITUTIONS.items():
        assert metadata.INSTITUTIONS.get(fragment) == expected, fragment


def test_categories_still_cover_what_they_used_to():
    was = {
        "bank": {"hdfcbank", "icicibank", "sbi.bank", "onlinesbi", "alerts.sbi",
                 "axis.bank", "axisbank", "kotak", "yes.bank", "yesbank",
                 "indusind", "idfcfirst", "pnbmail", "pnb.bank", "bankofbaroda",
                 "canarabank", "unionbank", "rbl.bank", "rblbank",
                 "federalbank", "aubank", "bandhanbank", "estatement",
                 "statement@"},
        "card": {"creditcardstatement", "creditcard", "cards@", "bobcard",
                 "sbicard", "amex", "americanexpress", "hsbc", "onecard",
                 "cred.club"},
        "loan": {"loanestatement", "loanstatement", "bajajfinserv",
                 "tatacapital", "lichousing", "hdfcltd", "homeloan"},
        "bureau": {"cibil", "transunion", "crif", "crifhighmark", "experian",
                   "equifax", "onescore", "creditreport", "creditscore"},
        "broker": {"zerodha", "upstox", "5paisa", "dhan.co", "paytmmoney",
                   "angelbroking", "angeltrade", "groww", "icicidirect",
                   "kotaksecurities", "sharekhan", "motilaloswal", "angelone",
                   "cdslstatement", "cdslindia", "nsdl", "proteantech",
                   "kfintech", "camsonline", "cams."},
    }
    for kind, expected in was.items():
        assert not expected - set(gmail_source.SENDER_CATEGORIES[kind]), kind


def test_every_bureau_is_still_detected():
    for text, expected in [("CIBIL report", "cibil"), ("TransUnion", "cibil"),
                           ("CRIF High Mark", "crif"), ("highmark", "crif"),
                           ("Experian", "experian"), ("Equifax", "equifax"),
                           ("a bank statement", "unknown")]:
        assert bureau.detect_bureau(text) == expected, text


def test_every_portfolio_layout_is_still_detected():
    for text, expected in [
        ("consolidated account statement", "cas"), ("cdsl", "cas"),
        ("nsdl", "cas"), ("demat account", "cas"), ("depository", "cas"),
        ("camsonline", "cams"), ("karvy", "cams"),
        ("consolidated portfolio", "cams"),
        ("kfintech", "kfintech"), ("kfin technologies", "kfintech"),
        ("holdings statement", "broker"), ("portfolio holdings", "broker"),
        ("zerodha", "broker"), ("groww", "broker"), ("upstox", "broker"),
        ("angel one", "broker"), ("icici direct", "broker"),
        ("kotak securities", "broker"), ("5paisa", "broker"),
    ]:
        assert portfolio.detect_layout(text)[0] == expected, text


def test_every_password_format_is_still_offered():
    for sender, expected in [
        ("x@hdfcbank.com", "Name(4) + DDMM"), ("x@icicibank.com", "Name(4) + DDMM"),
        ("x@onlinesbi.com", "DDMMYYYY"), ("x@axisbank.com", "Name(4) + DDMM"),
        ("x@kotak.com", "Name(4) + DDMM"), ("x@indusind.com", "Name(4) + DDMM"),
        ("x@idfcfirstbank.com", "Mobile(10)"), ("x@pnb.co.in", "DDMMYYYY"),
        ("x@bankofbaroda.com", "Name(4) + DDMM"), ("x@yesbank.in", "Name(4) + DDMM"),
        ("x@hsbc.co.in", "DDMMYYYY"), ("x@rblbank.com", "Name(4) + DDMM"),
        ("x@slice.com", "PAN"), ("x@americanexpress.com", "Card(4) + DDMM"),
        ("x@camsonline.com", "PAN"), ("x@kfintech.com", "PAN"),
        ("x@cdsl.co.in", "PAN"), ("x@nsdl.com", "PAN"),
        ("x@zerodha.com", "PAN"), ("x@upstox.com", "PAN"),
        ("x@bajajfinserv.in", "Name(4) + DDMM"),
        ("x@tatacapital.com", "Name(4) + DDMM"),
        ("x@cibil.com", "DDMMYYYY"), ("x@experian.in", "PAN"),
        ("x@crif.com", "DDMMYYYY"), ("x@equifax.com", "PAN"),
    ]:
        assert passwords.password_hint(sender)[0] == expected, sender


def test_password_explanations_are_one_wording_per_format():
    """Twelve issuers shared one format and three different wordings for it.
    The format's own sentence is now the only source; an issuer may append a
    parenthesised note, and nothing else."""
    by_label: dict[str, set[str]] = {}
    for _, label, explanation in passwords.PASSWORD_RULES:
        base = re.sub(r"\s*\([^)]*\)$", "", explanation)
        by_label.setdefault(label, set()).add(base)
    drifted = {k: v for k, v in by_label.items() if len(v) > 1}
    assert not drifted


def test_profile_requirements_come_from_the_format_not_its_name():
    """`profile_can_satisfy` used to sniff substrings out of the label, so
    renaming a label silently changed what the app believed it needed."""
    from app.models.profile import UserProfile

    only_pan = UserProfile(full_name="", pan="ABCDE1234F")
    assert passwords.profile_can_satisfy(only_pan, "PAN")
    assert not passwords.profile_can_satisfy(only_pan, "DDMMYYYY")
    assert not passwords.profile_can_satisfy(only_pan, "Mobile(10)")


# --------------------------------------------------------------------------
# The shared value readers
#
# Four modules used to parse a date, three a rupee figure and three a masked
# account number, each with its own implementation. These assert the survivors
# still answer every shape the originals did - and, where a reader is now
# strictly better than the one it replaced, that it is.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("15/01/2026", date(2026, 1, 15)),
    ("2026-01-15", date(2026, 1, 15)),
    ("15-Jan-2026", date(2026, 1, 15)),
    ("15 Jan 26", date(2026, 1, 15)),
    ("Jan-15-2026", date(2026, 1, 15)),
    ("January 15, 2026", date(2026, 1, 15)),
    ("Aug 29, 2026", date(2026, 8, 29)),
    ("15Jan2026", date(2026, 1, 15)),
    ("20260115", date(2026, 1, 15)),
    ("15.01.2026", date(2026, 1, 15)),
    ("15-01-26", date(2026, 1, 15)),
    ("not a date", None),
])
def test_one_date_reader_knows_every_shape(raw, expected):
    assert parsers.parse_date(raw) == expected


@pytest.mark.parametrize("text, expected", [
    ("Date Opened: 15-01-2026", date(2026, 1, 15)),
    # The three the bureau's own reader returned None for.
    ("opened Aug 29, 2026 at", date(2026, 8, 29)),
    ("reported 15.01.2026 by", date(2026, 1, 15)),
    ("closed 15-01-26 x", date(2026, 1, 15)),
    ("Info. as of: 09-08-2026", date(2026, 8, 9)),
    ("account 1234567 no date", None),
])
def test_find_date_reads_a_date_out_of_prose(text, expected):
    assert parsers.find_date(text) == expected


def test_a_yearless_date_needs_a_year_from_the_caller():
    """Both halves of the same rule: Amex writes "June 18", a bank alert
    writes "18-Jun", and neither resolves without a year to lend it."""
    assert parsers.parse_date("June 18") is None
    assert parsers.parse_date("18-Jun") is None
    assert parsers.parse_date("June 18", default_year=2026) == date(2026, 6, 18)
    assert parsers.parse_date("18-Jun", default_year=2026) == date(2026, 6, 18)


def test_an_alert_falls_back_to_the_day_it_arrived():
    received = date(2026, 8, 15)
    assert txn_email.parse_date("15-Aug", received) == date(2026, 8, 15)
    assert txn_email.parse_date("gibberish", received) == received
    assert txn_email.parse_date(None, received) == received


@pytest.mark.parametrize("raw, expected", [
    ("1,234.00", "1234.00"), ("Rs. 1,234", "1234"), ("INR 1234.56", "1234.56"),
    ("12,34,567.89", "1234567.89"), ("0.00", "0.00"),
    ("-", None), ("--", None), ("N/A", None), ("nil", None), ("", None),
])
def test_one_money_reader(raw, expected):
    got = parsers.money(raw)
    assert (str(got) if got is not None else None) == expected


def test_holdings_keep_their_sign_and_statements_do_not():
    """The one real difference between the money readers, kept explicit."""
    assert parsers.money("-500") == 500
    assert parsers.signed_money("-500") == -500
    assert parsers.parse_amount("-500").explicit_direction == "debit"


@pytest.mark.parametrize("raw, expected", [
    ("XXXX4345", "4345"), ("xx1751", "1751"), ("**5001", "5001"),
    ("XXXXXX1234", "1234"), ("0001015980001716889", "6889"),
    ("12", ""), ("", ""), (None, ""),
])
def test_one_masked_account_reader(raw, expected):
    """A statement, an alert and a bureau line are joined on these four
    digits. Three implementations was three ways for that join to fail."""
    assert formats.last_four(raw) == expected
    assert txn_email.parse_account(raw) == expected
    assert bureau.number_suffix(raw or "") == expected


def test_rail_vocabulary_has_one_home():
    """Three modules match on rail names. The subsets differ by design - the
    spellings must not."""
    for subset in (formats.PREFIX_RAILS, formats.SIGNATURE_RAILS):
        assert not set(subset) - set(formats.RAIL_NAMES)


def test_month_names_include_the_full_forms():
    """The bureau reader's private map had abbreviations only, so a report
    printing "December 2025" parsed to nothing."""
    for name in ("jan", "january", "sep", "sept", "september", "dec", "december"):
        assert formats.month_number(name)


# --------------------------------------------------------------------------
# One source of truth, asserted
# --------------------------------------------------------------------------

def test_the_matched_rule_is_kept_not_discarded():
    """"Categorised by a rule" is half an answer. The categorizer has always
    known which rule fired; it used to throw the label away, so a user looking
    at a row they disagreed with had nothing to read."""
    from app.categorize.rules import apply_rules, categorize_by_rules
    from app.models.schemas import Category, Direction, Transaction

    txn = Transaction(
        txn_date=date(2026, 6, 1), raw_description="SWIGGY BANGALORE",
        normalized_description="SWIGGY BANGALORE",
        amount=Decimal("250"), direction=Direction.DEBIT)
    assert categorize_by_rules([txn]) == 1
    assert txn.category != Category.UNCATEGORIZED
    assert txn.category_rule
    assert txn.category_rule == apply_rules(
        Transaction(txn_date=date(2026, 6, 1),
                    raw_description="SWIGGY BANGALORE",
                    normalized_description="SWIGGY BANGALORE",
                    amount=Decimal("250"), direction=Direction.DEBIT))[2]


def test_bill_payment_wording_is_shared_but_not_flattened():
    """Three readers ask about a card bill for three different reasons. The
    core wording is shared; the parts that differ are named, not duplicated."""
    from app.normalize import normalizer
    from app.reconcile import settlement

    core = set(formats.BILL_PAYMENT_MARKERS)
    assert core <= set(settlement._SETTLEMENT_MARKERS)
    # And the per-consumer sets stay disjoint from the core, so nothing is
    # listed twice.
    for extra in (formats.DIRECTION_ONLY_BILL_MARKERS,
                  formats.CATEGORY_ONLY_BILL_MARKERS,
                  formats.SETTLEMENT_ONLY_BILL_MARKERS):
        assert not core & set(extra)
    # The concrete case that stopped the three being merged outright.
    assert normalizer._CARD_BILL_PAYMENT.search("BPPY CC PAYMENT DP0153")
    assert not normalizer._CARD_BILL_PAYMENT.search(
        "BBPS PAYMENT RECEIVED - DP015271185122")


def test_the_ui_period_list_is_the_servers():
    """The mailbox fetches /api/gmail/periods and a local copy in the dropdown
    used to win, having drifted: no "3 years", no "10 years", and the
    server's "1 year" relabelled "12 months"."""
    source = (pathlib.Path(__file__).resolve().parents[2]
              / "frontend/src/components/mailbox/SourceSections.jsx").read_text(
                  encoding="utf-8")
    assert "const PERIODS = [" not in source
    assert "periodsFor(periods," in source


def test_category_tone_is_declared_once_and_covers_every_category():
    root = pathlib.Path(__file__).resolve().parents[2] / "frontend/src"
    declaring = [p for p in root.rglob("*.jsx")
                 if "CATEGORY_TONE = {" in p.read_text(encoding="utf-8")]
    assert len(declaring) == 1, [p.name for p in declaring]
    text = declaring[0].read_text(encoding="utf-8")
    for kind in inst.CLASSIFY_ORDER:
        assert f"{kind}:" in text, kind


def test_account_type_wording_is_one_vocabulary():
    """The bureau reader and the statement reader both map wording onto
    AccountType. They were independent lists and had disagreed: "Wallet" on a
    bureau line mapped to unknown while the same word on a letterhead mapped
    to WALLET."""
    from app.models.schemas import AccountType
    shared = {
        "credit card": "credit_card", "creditcard": "credit_card",
        "housing loan": "home_loan", "home loan": "home_loan",
        "mortgage": "home_loan", "auto loan": "auto_loan",
        "car loan": "auto_loan", "personal loan": "personal_loan",
        "consumer loan": "personal_loan", "gold loan": "personal_loan",
        "education loan": "personal_loan", "savings": "savings",
        "wallet": "wallet",
    }
    for wording, expected in shared.items():
        assert bureau.map_account_type(wording) == expected, wording
        detected = metadata.detect_account_type(wording)
        assert detected is not None and detected.value == expected, wording
    assert AccountType(expected)


def test_bureau_only_wording_stays_bureau_only():
    """A bureau names the facility; a statement names the product. A bare
    "vehicle" is an auto loan on a bureau line and just a word in prose."""
    assert bureau.map_account_type("vehicle") == "auto_loan"
    assert metadata.detect_account_type("vehicle hire charges") is None


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


# --------------------------------------------------------------------------
# The Rules screen
#
# The endpoint behind it must show what the app ACTUALLY does. A page that
# says "transfers pair within 4 days" while the matcher uses 7 is worse than
# no page: it is a confident wrong answer about the app's own behaviour.
# --------------------------------------------------------------------------

def test_the_catalogue_shows_every_rule_family(client):
    body = client.get("/api/rules").json()
    assert len(body["find"]["institutions"]) == len(inst.REGISTRY)
    assert len(body["find"]["scans"]) == len(gmail_source.SCAN_INTENTS)
    assert len(body["find"]["rejections"]) == len(gmail_source.REJECTION_RULES)
    assert len(body["check"]["categories"]) == len(category_rules.RULES)
    assert len(body["read"]["alert_templates"]) == len(txn_email.TEMPLATES)
    assert len(body["open"]["password_formats"]) == len(pw_formats.FORMATS)
    assert body["thresholds"]


def test_published_thresholds_are_the_live_values(client):
    """Imported, never retyped - so the screen cannot drift from the code."""
    from app.reconcile import settlement, transfers

    published = {(t["group"], t["name"]): t["value"]
                 for t in client.get("/api/rules").json()["thresholds"]}
    assert published[("Transfers", "Days apart")] == str(transfers.MAX_DAY_GAP)
    assert published[("Card settlement", "Days apart")] == str(settlement.MAX_DAY_GAP)
    assert (published[("Card settlement", "Confidence floor")]
            == str(settlement.CONFIDENCE_FLOOR))


def test_category_rules_are_published_in_the_order_they_run(client):
    """Order IS the rule - the first match wins - so a page that reordered
    them would misexplain every overlap."""
    published = client.get("/api/rules").json()["check"]["categories"]
    assert [r["order"] for r in published] == list(range(1, len(published) + 1))
    assert [r["category"] for r in published] == [
        r.category for r in category_rules.RULES]


def test_explaining_a_narration_names_the_winning_rule(client):
    body = client.post("/api/rules/test", json={
        "description": "UPI/SWIGGY/AUG25/123456", "direction": "debit",
    }).json()["description"]
    assert body["winner"]["category"] == "dining"
    assert body["normalized"] == "SWIGGY AUG25 123456"
    assert body["rails_stripped"] is True


def test_explaining_agrees_with_what_the_pipeline_would_do(client):
    """The screen reads the same functions the import reads. If these two ever
    disagree the screen is lying about the app."""
    from app.models.schemas import Direction, Transaction

    narration = "BPPY CC PAYMENT DP0153271185122"
    shown = client.post("/api/rules/test", json={
        "description": narration, "direction": "debit"}).json()["description"]

    txn = Transaction(
        txn_date=date(2026, 6, 1), raw_description=narration,
        normalized_description=narration, amount=Decimal("100"),
        direction=Direction.DEBIT)
    category_rules.categorize_by_rules([txn])

    assert shown["winner"]["category"] == txn.category
    assert shown["bill_payment"] is True


def test_explaining_an_email_reports_every_scans_verdict(client):
    body = client.post("/api/rules/test", json={
        "sender": "alerts@hdfcbank.net",
        "subject": "Your HDFC Bank Credit Card Statement - Offer inside!",
        "filename": "Retail_HPCL_NORM.pdf",
    }).json()["email"]

    assert body["institution"] == "HDFC Bank"
    assert body["category"] == "bank"
    # An offer from a real bank is still an offer, and every scan says so.
    assert set(body["scans"].values()) == {"marketing"}
    assert body["password"]["format"] == "Name(4) + DDMM"
    assert "CAPS" in body["password"]["explanation"]


def test_explaining_nothing_returns_nothing(client):
    assert client.post("/api/rules/test", json={}).json() == {}


def test_the_rules_screen_needs_no_ledger(client):
    """It describes what the app WOULD do, so it is at its most useful before
    anything has been imported."""
    assert client.get("/api/rules").status_code == 200


# --------------------------------------------------------------------------
# Explaining one row
# --------------------------------------------------------------------------

def _row(**kw):
    from app.models.schemas import Direction, Transaction
    base = dict(txn_date=date(2026, 6, 1), raw_description="X",
                normalized_description="X", amount=Decimal("100"),
                direction=Direction.DEBIT)
    base.update(kw)
    return Transaction(**base)


def test_direction_records_which_signal_decided_it():
    """Direction is the one field whose mistake lands on both sides of every
    total at once, so every path through the reader names its evidence."""
    from app.models.schemas import Direction
    from app.normalize.normalizer import _direction_from_description
    from app.rules import directions

    way, reason = _direction_from_description("BPPY CC PAYMENT", is_liability=True)
    assert way == Direction.CREDIT
    assert reason == directions.BILL_PAYMENT

    way, reason = _direction_from_description("SALARY CREDIT", is_liability=False)
    assert way == Direction.CREDIT
    assert reason == directions.NARRATION

    way, reason = _direction_from_description("BUYING THINGS", is_liability=False)
    assert way is None
    assert reason == directions.DEFAULTED


def test_the_running_balance_stamps_its_own_reason():
    """It overrides every other signal, so it must also overwrite the reason -
    otherwise the row claims to have been decided by the wording it just
    contradicted."""
    from app.models.schemas import Direction
    from app.normalize.normalizer import _apply_balance_deltas
    from app.rules import directions

    txn = _row(amount=Decimal("500"), direction=Direction.DEBIT,
               balance_after=Decimal("1500"),
               direction_reason=directions.NARRATION)
    _apply_balance_deltas([txn], is_liability=False,
                          opening_balance=Decimal("1000"))
    assert txn.direction == Direction.CREDIT
    assert txn.direction_reason == directions.RUNNING_BALANCE


def test_every_direction_reason_the_reader_can_set_has_a_sentence():
    """A code with no sentence renders as a blank on the row, which reads as
    the app having no reason rather than as a missing string."""
    from app.normalize import normalizer
    from app.rules import directions

    used = {getattr(directions, name) for name in dir(directions)
            if name.isupper() and isinstance(getattr(directions, name), str)}
    used.discard(directions.UNRECORDED)
    assert used, "the reader sets codes from this module"
    for code in used:
        assert directions.describe(code), code
    assert normalizer.directions is directions


def test_explaining_a_row_answers_all_three_questions(client):
    from app.db.database import get_db
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType, Direction
    from app.rules import directions

    db = get_db()
    account = Account(institution="Test Bank", account_type=AccountType.SAVINGS)
    repo.upsert_account(db, account)
    txn = _row(account_id=account.id, raw_description="SWIGGY BANGALORE",
               normalized_description="SWIGGY BANGALORE",
               direction=Direction.DEBIT,
               direction_reason=directions.COLUMN,
               category="dining", category_rule="dining rule")
    repo.save_transactions(db, [txn])

    body = client.get(f"/api/rules/explain/{txn.id}").json()
    assert body["category"]["value"] == "dining"
    assert body["direction"]["value"] == "debit"
    assert body["direction"]["reason"]["code"] == directions.COLUMN
    assert body["direction"]["recorded"] is True
    # Not paired with anything, and the panel must say so rather than showing
    # an empty group.
    assert body["transfer"] is None


def test_explaining_an_unknown_row_is_a_404(client):
    assert client.get("/api/rules/explain/nope").status_code == 404


def test_a_row_read_before_the_reason_existed_says_so(client):
    """The honest reading. Claiming a default would be inventing a reason the
    app never had."""
    from app.db.database import get_db
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType

    db = get_db()
    account = Account(institution="Test Bank", account_type=AccountType.SAVINGS)
    repo.upsert_account(db, account)
    txn = _row(account_id=account.id, direction_reason="", category_rule="")
    repo.save_transactions(db, [txn])

    body = client.get(f"/api/rules/explain/{txn.id}").json()
    assert body["direction"]["reason"] is None
    assert body["direction"]["recorded"] is False


def test_a_multi_leg_group_reports_every_leg(tmp_db):
    """Read from the transactions, not from `transfer_pairs`, which only
    records the two ends of a 1:1 match - a settlement covering three cards
    would otherwise report a group of two."""
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType, Direction

    account = Account(institution="Test Bank", account_type=AccountType.SAVINGS)
    repo.upsert_account(tmp_db, account)
    legs = [
        _row(account_id=account.id, transfer_pair_id="g1",
             direction=Direction.DEBIT, amount=Decimal("300")),
        _row(account_id=account.id, transfer_pair_id="g1",
             direction=Direction.CREDIT, amount=Decimal("100")),
        _row(account_id=account.id, transfer_pair_id="g1",
             direction=Direction.CREDIT, amount=Decimal("200")),
        _row(account_id=account.id, transfer_pair_id="other"),
    ]
    repo.save_transactions(tmp_db, legs)
    assert len(repo.transactions_in_pair(tmp_db, "g1")) == 3
    assert repo.transactions_in_pair(tmp_db, "") == []


# --------------------------------------------------------------------------
# A header row is not a transaction
# --------------------------------------------------------------------------

def test_a_collapsed_header_row_is_not_a_transaction():
    """`looks_like_header` asks this of separate CELLS. When the extractor
    collapses a header into one cell, the row below reads as a transaction
    whose narration is the header - and on a card statement's payment slip the
    "amount" it picks up is the MINIMUM AMOUNT DUE, which is not a transaction
    at all. Three of these sat in a real ledger as spending."""
    from app.normalize.column_map import is_header_text

    for header in [
        "PaymentDueDate Min.AmountDue ChequeNo Date Bank Amount",
        "Date Description Withdrawal Deposit Balance",
        "Txn Date Value Date Particulars Debit Credit Balance",
        "Date Narration Chq/Ref No Value Dt Withdrawal Amt Deposit Amt "
        "Closing Balance",
    ]:
        assert is_header_text(header), header


def test_a_narration_containing_a_column_word_survives():
    """Deliberately strict: EVERY word must be header vocabulary. Dropping a
    real transaction is far worse than keeping a header, and these three are
    real rows from a real ledger."""
    from app.normalize.column_map import is_header_text

    for narration in [
        "CREDIT BALANCE REFUND", "CREDIT CARD PAYMENT",
        "REWARD POINTS CREDIT INR", "SWIGGY BANGALORE",
        "UPI/AJINKYA DE/ajinkyashere7@/Deposit/State Bank/XXXX9378",
        "By Contribution On Account of Subscriber Initiated Scheme Preference",
        # Two words is not enough evidence either way.
        "Deposit Balance",
    ]:
        assert not is_header_text(narration), narration


def test_the_header_row_never_reaches_the_ledger():
    """End to end, through the reader that produced the three real ones."""
    from app.models.schemas import (ExtractedTable, ExtractionResult,
                                    SourceFormat)
    from app.normalize.normalizer import normalize

    table = ExtractedTable(rows=[
        ["Date", "Description", "Amount"],
        ["05/07/2026", "PaymentDueDate Min.AmountDue ChequeNo Date Bank Amount",
         "735.00"],
        ["06/07/2026", "SWIGGY BANGALORE", "250.00"],
    ])
    statement, _ = normalize(
        ExtractionResult(source_format=SourceFormat.PDF, tables=[table],
                         text="HDFC Bank credit card statement"),
        filename="card.pdf")

    descriptions = [t.raw_description for t in statement.transactions]
    assert "SWIGGY BANGALORE" in descriptions
    assert not any("PaymentDueDate" in d for d in descriptions)
    assert any("column titles" in w for w in statement.parse_warnings)


# --------------------------------------------------------------------------
# A statement covering more than one month
# --------------------------------------------------------------------------

def test_a_long_period_is_read_and_a_decade_is_not():
    """A quarterly, half-yearly or annual statement is a normal thing to be
    sent. A seven-year span is a date pair that is not a statement period."""
    from app.normalize.metadata import detect_period

    for text, days in [
        ("Statement period: 01-Jan-2026 to 31-Mar-2026", 89),
        ("Statement period 01/04/2025 to 31/03/2026", 364),
    ]:
        start, end = detect_period(text)
        assert start and end and (end - start).days == days, text

    assert detect_period("For the period 01/01/2020 to 31/12/2026") == (None, None)


def test_a_long_statement_covers_every_month_it_spans():
    """The coverage grid must not show eleven months missing because one file
    covered all twelve."""
    from app.analytics.coverage import statement_months

    months = statement_months(date(2025, 4, 1), date(2026, 3, 31))
    assert len(months) == 12
    assert months[0] == "2025-04" and months[-1] == "2026-03"


def test_a_yearless_date_lands_in_the_right_year_across_a_boundary():
    """Amex prints transaction dates with no year. On a statement covering
    April 2025 to March 2026, "15 May" belongs to 2025 - and taking the
    period's end year would date it two months AFTER the statement closed,
    where the out-of-period guard then discards the row."""
    from app.normalize.parsers import parse_date

    year = (date(2025, 4, 1), date(2026, 3, 31))
    assert parse_date("15 May", default_year=2026) == date(2026, 5, 15)
    assert parse_date("15 May", default_year=2026, period=year) == date(2025, 5, 15)
    assert parse_date("15 Feb", default_year=2026, period=year) == date(2026, 2, 15)

    # A statement inside one calendar year is untouched.
    month = (date(2026, 6, 24), date(2026, 7, 23))
    assert parse_date("June 18", default_year=2026, period=month) == date(2026, 6, 18)


def test_rows_from_overlapping_statements_are_deduplicated():
    """Importing a monthly and a quarterly covering the same weeks is the
    normal case, not an edge one - content hashing catches an identical FILE,
    and this catches identical rows inside different ones."""
    from app.models.schemas import Direction
    from app.reconcile.transfers import find_duplicate_transactions

    def row(desc, amount="250"):
        return _row(account_id="a1", raw_description=desc,
                    normalized_description=desc, amount=Decimal(amount),
                    direction=Direction.DEBIT, txn_date=date(2026, 5, 15),
                    balance_after=Decimal("1000"))

    # The documented case: one row that two extractions cut at different
    # lengths. A short narration cannot do this - there is an 18-character
    # floor, so a generic description never swallows an unrelated row.
    full = "IBL897436BA0A5D47C88E89B68A0EBCA9 ZOMATO"
    rows = [row(full), row(full[:26]), row("A DIFFERENT PAYMENT", "999")]
    extra = find_duplicate_transactions(rows)
    assert len(extra) == 1
    # The fuller narration survives: a truncated copy is the one to drop.
    assert extra[0].raw_description == full[:26]

    # Two genuinely different rows of the same amount and date stay.
    both = [row("CHAI STALL ONE"), row("CHAI STALL TWO")]
    assert find_duplicate_transactions(both) == []


# --------------------------------------------------------------------------
# The half of the app that happens after the ledger exists
# --------------------------------------------------------------------------

def test_the_catalogue_covers_what_happens_after_the_rows_exist(client):
    """It used to describe how a document is found, opened, classified and
    read, then stop - exactly where the decisions a user is most likely to
    question begin."""
    from app.analytics import recurring
    from app.models.schemas import FlowRole
    from app.rules import directions

    ledger = client.get("/api/rules").json()["ledger"]
    assert len(ledger["directions"]) == len(directions.REASONS)
    assert len(ledger["flow_roles"]) == len(list(FlowRole))
    assert len(ledger["cadences"]) == len(recurring.CADENCES)
    assert ledger["attribution"]["steps"]
    assert ledger["pairing"]


def test_published_salary_drift_numbers_are_the_live_ones():
    """The screen prints the days that decide whether a salary moves month.
    Retyping them is how a page comes to describe behaviour the app does not
    have."""
    from app.analytics import periods
    from app.api.rules_routes import _attribution_rules

    shown = _attribution_rules(periods)
    assert shown["month_end_anchor"] == periods.MONTH_END_ANCHOR
    assert shown["month_start_anchor"] == periods.MONTH_START_ANCHOR
    assert shown["arrived_early_from"] == periods.ARRIVED_EARLY_FROM
    assert shown["arrived_late_until"] == periods.ARRIVED_LATE_UNTIL
    assert shown["min_occurrences"] == periods.MIN_OCCURRENCES_TO_SHIFT


def test_a_month_end_salary_arriving_on_the_first_counts_in_the_month_before():
    from app.analytics.periods import assign_accounting_months
    from app.analytics.recurring import RecurringSeries
    from app.models.schemas import Direction

    days = [date(2026, 5, 31), date(2026, 6, 30), date(2026, 8, 1)]
    rows = [_row(id=f"t{i}", txn_date=d, direction=Direction.CREDIT,
                 amount=Decimal("100000")) for i, d in enumerate(days)]
    series = RecurringSeries(
        id="s1", account_id=None, label="Salary", category="salary",
        direction=Direction.CREDIT, median_amount=Decimal("100000"),
        cadence_days=30, cadence_name="monthly", occurrences=3,
        first_seen=days[0], last_seen=days[-1], next_expected=None,
        is_active=True, confidence=0.9,
        transaction_ids=[r.id for r in rows])

    assign_accounting_months(rows, [series])
    # Paid at month end; the August 1st arrival is July's pay.
    assert rows[2].accounting_month == "2026-07"
    assert rows[0].accounting_month == "2026-05"


def test_two_salaries_in_one_month_are_never_both_moved_into_it():
    """The collision guard. Shifting can CREATE the double count it exists to
    prevent: pay on 31 August and again on 1 September, and moving September
    back lands both in August and empties September."""
    from app.analytics.periods import assign_accounting_months
    from app.analytics.recurring import RecurringSeries
    from app.models.schemas import Direction

    days = [date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 31),
            date(2026, 9, 1)]
    rows = [_row(id=f"t{i}", txn_date=d, direction=Direction.CREDIT,
                 amount=Decimal("100000")) for i, d in enumerate(days)]
    series = RecurringSeries(
        id="s1", account_id=None, label="Salary", category="salary",
        direction=Direction.CREDIT, median_amount=Decimal("100000"),
        cadence_days=30, cadence_name="monthly", occurrences=4,
        first_seen=days[0], last_seen=days[-1], next_expected=None,
        is_active=True, confidence=0.9,
        transaction_ids=[r.id for r in rows])

    assign_accounting_months(rows, [series])
    months = [r.accounting_month for r in rows]
    assert len(months) == len(set(months)), months


def test_a_one_off_payment_is_never_moved():
    """Only a monthly series can drift. A single large credit on the 1st stays
    where it happened."""
    from app.analytics.periods import assign_accounting_months
    from app.models.schemas import Direction

    row = _row(id="t1", txn_date=date(2026, 9, 1), direction=Direction.CREDIT,
               amount=Decimal("500000"))
    assign_accounting_months([row], [])
    assert row.accounting_month == "2026-09"


def test_what_leaves_the_machine_is_listed(client):
    """"What is sent to a model" is a question a user is entitled to a precise
    answer to, and the precise answer is a list."""
    privacy = client.get("/api/rules").json()["privacy"]
    listed = " ".join(r["what"].lower() for r in privacy["removed"])
    for expected in ("digit", "pan", "email", "phone", "pin", "name"):
        assert expected in listed, expected


def test_account_identity_and_person_detection_are_published(client):
    """Two rules that change what the user sees and were nowhere on screen.
    Account identity decides whether two statements are one account - getting
    it wrong doubles every figure while leaving them all plausible."""
    read = client.get("/api/rules").json()["read"]
    assert read["account_identity"]["fallbacks"]
    assert "doubles" in read["account_identity"]["why_it_matters"]
    assert len(read["person_vs_business"]["signals"]) == 3


def test_every_catalogue_section_is_rendered_by_the_screen(client):
    """A section the API serves and the screen never shows is a rule that is
    published and still invisible - which is the failure this whole screen
    exists to fix."""
    import pathlib

    body = client.get("/api/rules").json()
    source = (pathlib.Path(__file__).resolve().parents[2]
              / "frontend/src/components/Rules.jsx").read_text(encoding="utf-8")
    for section in body:
        assert f"data.{section}" in source or f"['{section}']" in source, section


# --------------------------------------------------------------------------
# The plumbing, the model, and the maths
# --------------------------------------------------------------------------

def test_the_pipeline_order_is_published(client):
    """Order is a rule here, not an implementation detail: duplicates are
    removed before transfers are matched, and saved decisions are applied
    last so a decision always beats a rule."""
    p = client.get("/api/rules").json()["pipeline"]
    names = [s["name"] for s in p["stages"]]
    assert names.index("Remove duplicates") < names.index(
        "Match transfers between accounts")
    assert names.index("Categorise") < names.index("Apply your saved decisions")
    assert p["classification_order"][-1]["reader"].startswith("Bank")
    assert p["formats"]["magic_bytes"]


def test_published_forecast_numbers_are_the_live_ones(client):
    from app.analytics import forecast

    f = client.get("/api/rules").json()["money"]["forecast"]
    assert f["min_band_share"] == float(forecast.MIN_BAND_SHARE)
    assert f["committed_confidence"] == forecast.COMMITTED_SERIES_CONFIDENCE
    assert [c["level"] for c in f["confidence"]] == ["high", "medium", "low"]


def test_the_model_section_says_what_it_is_never_used_for(client):
    """The important half. Every figure is arithmetic; a model never touches
    one."""
    m = client.get("/api/rules").json()["model"]
    never = " ".join(m["never_used_for"]).lower()
    assert "figure" in never
    assert "arithmetic" in never
    # The actual instructions, not a paraphrase of them.
    from app.categorize import llm_categorizer
    assert m["instructions"] == llm_categorizer.SYSTEM.strip()


def test_every_clearing_scope_is_published_with_what_it_keeps(client):
    from app.db.database import CLEAR_SCOPES

    storage = client.get("/api/rules").json()["storage"]
    assert {s["scope"] for s in storage["scopes"]} == set(CLEAR_SCOPES)
    for scope in storage["scopes"]:
        assert scope["note"], scope["scope"]


def test_money_is_rounded_in_exactly_one_place():
    """Two implementations of "round to paise" is two ways for a ledger to
    disagree with the statement it came from."""
    from app.analytics import engine, loans
    from app.rules import formats

    assert engine.CENT is formats.CENT
    assert loans.CENT is formats.CENT
    assert loans._q is formats.to_paise
    # Half away from zero, the way a bank rounds - not banker's rounding,
    # which would send 0.125 to 0.12 and fail reconciliation on the half-paise.
    assert formats.to_paise(Decimal("0.125")) == Decimal("0.13")
    assert engine.q(Decimal("0.125")) == Decimal("0.13")


def test_the_month_vocabulary_has_one_home():
    """Four modules carried their own. The coverage grid's came from the
    stdlib - correct, but not the same set: no "sept"."""
    from app.analytics import coverage
    from app.normalize import parsers
    from app.rules import formats

    assert coverage._MONTH_NAMES is formats.MONTHS
    assert parsers._MONTHS is formats.MONTHS
    assert coverage.guess_period_hint("Statement_SEPT2025.pdf") == "2025-09"


def test_the_package_map_names_modules_that_exist():
    """`rules/__init__.py` is the map a developer reads first. A map naming a
    file that moved is worse than no map."""
    import pathlib
    import re

    from app import rules

    app = pathlib.Path(rules.__file__).resolve().parents[1]
    named = set(re.findall(r"\b([a-z_]+/[a-z_]+\.py)\b", rules.__doc__))
    named |= {f"rules/{m}" for m in
              re.findall(r"^    ([a-z_]+\.py)", rules.__doc__, re.MULTILINE)}
    assert named, "the map lists modules"
    # The map points at app modules and at the test file that guards them, so
    # each path is resolved against whichever of the two roots holds it.
    missing = [n for n in sorted(named)
               if not (app / n).exists() and not (app.parent / n).exists()]
    assert not missing, missing


def test_the_account_number_precedence_is_published(client):
    """The most load-bearing identity rule in the app, and it was not on the
    page: card number beats account number, and a customer ID is never
    either. Both have cost a real ledger a split account."""
    from app.normalize import metadata

    a = client.get("/api/rules").json()["read"]["account_number"]
    assert [lbl["label"] for lbl in a["labels"]][0] == "Card Number"
    assert len(a["labels"]) == len(metadata.ACCOUNT_NUMBER_LABELS)
    never = " ".join(n["label"] for n in a["never"]).lower()
    assert "customer id" in never and "alternate" in never


def test_card_number_still_beats_a_neighbouring_account_number():
    """The HDFC Marriott statement, which prints both one line apart."""
    from app.normalize.metadata import detect_account_number

    assert detect_account_number(
        "Credit Card No. 00361147XXXX6885\n"
        "Alternate Account Number 0001015980001716889") == "XXXX6885"
    # A customer ID alone identifies the person, so nothing is returned.
    assert detect_account_number("Cust ID: 341562729") is None


def test_a_rule_worth_asking_about_can_be_found_by_searching_for_it(client):
    """The page is only useful if the words someone would actually type reach
    the rule they are looking for."""
    import json

    body = client.get("/api/rules").json()
    for term, section in [
        ("salary", "ledger"),          # which month my salary counts in
        ("payday", "ledger"),
        ("card number", "read"),       # why my card is filed under that number
        ("customer id", "read"),
        ("alternate", "read"),
    ]:
        assert term in json.dumps(body[section]).lower(), (term, section)


# --------------------------------------------------------------------------
# Form controls
#
# Not a rule about money, but the same class of bug: a list of cases someone
# typed out, which then failed to cover the cases nobody remembered.
# --------------------------------------------------------------------------

def _stylesheet() -> str:
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[2]
            / "frontend/src/styles.css").read_text(encoding="utf-8")


def test_text_controls_are_matched_by_shape_not_by_a_list_of_types():
    """The old selector named three types and missed 25 controls: seventeen
    inputs written with no `type` at all, five dates, two numbers and a
    textarea. Each fell through to the browser default and rendered white on a
    dark page."""
    css = _stylesheet()
    for selector in ("input:not([type])", "input[type=\"date\"]",
                     "input[type=\"number\"]", "textarea"):
        assert selector in css, selector


def test_both_themes_declare_a_colour_scheme():
    """A <select>'s open menu is drawn by the operating system and cannot be
    styled. It follows `color-scheme` - without it, a dropdown on a dark page
    opens a white menu and no CSS on the <select> changes that."""
    css = _stylesheet()
    assert "color-scheme: light" in css
    assert "color-scheme: dark" in css


def test_every_input_in_the_app_is_a_shape_the_stylesheet_covers():
    """A new control with an uncovered type is the way this regresses."""
    import pathlib
    import re

    root = (pathlib.Path(__file__).resolve().parents[2] / "frontend/src")
    css = _stylesheet()
    used = set()
    for path in root.rglob("*.jsx"):
        used |= set(re.findall(r'<input[^>]*\stype="([a-z-]+)"',
                               path.read_text(encoding="utf-8")))
    # These are not text boxes and are styled (or invisible) elsewhere.
    used -= {"checkbox", "radio", "file", "button", "submit", "hidden", "range",
             "monotone"}
    missing = [t for t in sorted(used) if f'input[type="{t}"]' not in css]
    assert not missing, missing


# --------------------------------------------------------------------------
# A holdings statement is not a bank statement
# --------------------------------------------------------------------------

def test_an_unreadable_file_is_not_called_a_statement():
    """The statement reader is the deliberate fallback for anything
    unrecognised, so "I could not open this" and "this is a statement" used to
    be the same answer - and the fallback reader always finds something."""
    from app.ingestion import router

    assert router.classify_document("", "mystery.pdf") == router.DOC_UNREADABLE
    assert router.classify_document("   \n ", "x.pdf") == router.DOC_UNREADABLE


def test_a_record_of_trades_never_routes_to_the_statement_reader():
    from app.ingestion import router

    for text in ["Contract Note for the trades below",
                 "ANNUAL GLOBAL TRANSACTION STATEMENT Segment: Future & Option"]:
        assert router.classify_document(text, "note.pdf") == router.DOC_PORTFOLIO


def test_an_amount_that_is_a_slice_of_the_filename_is_not_money():
    """A Zerodha holdings statement names itself after its DP and client ids:

        transaction-with-holding-statement_UC9050-1208160028236891.pdf

    Read as a bank statement that produced two debits of 60,028,236,891 -
    120 billion of money out against an actual 75 lakh."""
    from app.normalize.normalizer import _amount_came_from_the_filename

    name = "transaction-with-holding-statement_UC9050-1208160028236891.pdf"
    assert _amount_came_from_the_filename(Decimal("60028236891"), name)
    # A real amount that happens to share a few digits is untouched, and a
    # short run is never evidence.
    assert not _amount_came_from_the_filename(Decimal("1208"), name)
    assert not _amount_came_from_the_filename(Decimal("167489.00"), name)
    assert not _amount_came_from_the_filename(Decimal("60028236891"), "")


def test_only_a_readable_statement_contributes_transactions():
    """The guard that was already there and never tested: a file the run did
    not read as a usable statement contributes nothing to the ledger, whatever
    else its record says."""
    from app.graph.nodes import merge_ledger

    def entry(status, rows):
        from app.models.schemas import (Account, AccountType, Direction,
                                        Statement, Transaction)
        acct = Account(institution="Test", account_type=AccountType.SAVINGS,
                       account_number_masked="XXXX1234")
        stmt = Statement(source_filename=f"{status}.pdf")
        # Distinct rows: identical ones on one account are indistinguishable
        # from duplicates, and this test is about STATUS, not deduplication.
        stmt.transactions = [
            Transaction(txn_date=date(2026, 6, i + 1),
                        raw_description=f"{status} row {i}",
                        normalized_description=f"{status} row {i}",
                        amount=Decimal(100 + i), direction=Direction.DEBIT)
            for i in range(rows)]
        return {"filename": f"{status}.pdf", "attempt": 1, "status": status,
                "statement": stmt, "account": acct}

    out = merge_ledger({"statements": [
        entry("ok", 2), entry("unreconciled", 1),
        entry("failed", 5), entry("not_a_statement", 5),
        entry("needs_password", 5),
    ]})
    assert len(out["transactions"]) == 3


def test_a_labelled_alert_is_read_as_well_as_a_sentence():
    """Axis does not write a sentence, it writes a form. Every other template
    reads "207.46 spent on card XX5207 at BOOKMYSHOW"; this body has no verb
    at all, so not one of them matched and every Axis card alert was skipped
    silently. The SUBJECT is a sentence, which is what makes it easy to miss:
    the email looks parseable and the body is not."""
    from app.ingestion import txn_email

    body = ("01-09-2026 Dear Jitesh Agarwal, Here's the summary of your Axis "
            "Bank Credit Card Transaction: Transaction Amount: INR 207.46 "
            "Merchant Name: BOOKMYSHOW Axis Bank Credit Card No. XX5207 "
            "Date & Time: 01-09-2026, 14:13:19 IST Available Limit*: "
            "INR 359814.54")
    a = txn_email.parse_alert(body)
    assert a is not None
    assert a.template == "card-labelled-summary"
    assert a.amount == Decimal("207.46")
    assert a.account_suffix == "5207"
    # The merchant runs to the issuer's own "Card No." phrase. A lazy capture
    # stopped at two characters here ("BO"), because the filler that followed
    # absorbed the rest of the name.
    assert a.counterparty == "BOOKMYSHOW"
    # The available limit must not be mistaken for the amount.
    assert a.amount != Decimal("359814.54")


def test_the_card_number_connector_covers_how_banks_write_it():
    """"card no. XX5207" matched the sentence but captured no account, so the
    alert arrived with an amount, a payee and a date and was thrown away by
    match_account with "the alert names no account number"."""
    from app.ingestion import txn_email

    for wording in ["card no. XX5207", "card no XX5207", "card number XX5207",
                    "card XX5207", "card ending XX5207",
                    "card ending with XX5207"]:
        body = f"INR 207.46 spent on credit {wording} at AMAZON on 12-08-26."
        a = txn_email.parse_alert(body)
        assert a is not None and a.account_suffix == "5207", wording
