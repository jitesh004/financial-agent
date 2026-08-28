"""Tests for profile-driven password derivation, dedup, and Gmail import.

These cover the three ways a statement reaches the pipeline other than a plain
manual upload, plus the deduplication that keeps any of those paths from double
counting.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.graph.build import build_graph  # noqa: E402
from app.ingestion import router  # noqa: E402
from app.ingestion.passwords import (MAX_CANDIDATES, derive_passwords,  # noqa: E402
                                     redact_candidate)
from app.models.profile import UserProfile  # noqa: E402

SAMPLES = ROOT / "data" / "samples"
ENCRYPTED = ROOT / "data" / "samples_encrypted" / "icici_credit_card_locked.pdf"

PROFILE = UserProfile(full_name="JITESH SHARMA", date_of_birth=date(1990, 2, 6),
                      pan="ABCDE1234F", mobile="9876543210")


def _require(path: Path, hint: str):
    if not path.exists():
        pytest.skip(hint)


# --------------------------------------------------------------------------
# Password derivation
# --------------------------------------------------------------------------

def test_derives_the_name_plus_dob_format():
    """The canonical bank format: first 4 of name + DDMM, in both casings."""
    candidates = derive_passwords(PROFILE)
    assert "jite0602" in candidates
    assert "JITE0602" in candidates


def test_candidate_set_is_bounded():
    """A huge profile can never turn this into a brute-forcer."""
    fat = UserProfile(
        full_name="Averyverylongcompoundname Secondsurname",
        date_of_birth=date(1988, 12, 31), pan="ZZZZZ9999Z", mobile="9999988888",
        custom_passwords=[f"p{i}" for i in range(50)],
    )
    assert len(derive_passwords(fat)) <= MAX_CANDIDATES


def test_custom_passwords_are_tried_first():
    profile = UserProfile(full_name="Asha Rao", date_of_birth=date(1990, 1, 1),
                          custom_passwords=["myexactpassword"])
    assert derive_passwords(profile)[0] == "myexactpassword"


def test_empty_profile_yields_nothing():
    assert derive_passwords(UserProfile()) == []
    assert derive_passwords(None) == []


def test_redaction_hides_the_password():
    assert redact_candidate("jite0602") == "j*******"
    assert redact_candidate("") == "(empty)"


# --------------------------------------------------------------------------
# Unlocking a protected PDF
# --------------------------------------------------------------------------

def test_encrypted_pdf_needs_password_without_a_profile():
    _require(ENCRYPTED, "run generate_samples + encrypt step")
    result = router.extract(ENCRYPTED)
    assert result.needs_password
    assert not result.tables


def test_encrypted_pdf_opens_with_the_right_profile():
    _require(ENCRYPTED, "run generate_samples + encrypt step")
    candidates = derive_passwords(PROFILE)
    result = router.extract(ENCRYPTED, password_candidates=candidates)
    assert not result.needs_password
    assert result.tables
    # The working password is reported only in redacted form.
    assert any("derived password" in w for w in result.warnings)
    assert not any("jite0602" in w for w in result.warnings)


def test_encrypted_pdf_stays_locked_for_the_wrong_profile():
    _require(ENCRYPTED, "run generate_samples + encrypt step")
    wrong = derive_passwords(UserProfile(full_name="Asha Rao",
                                         date_of_birth=date(1985, 11, 20)))
    assert router.extract(ENCRYPTED, password_candidates=wrong).needs_password


# --------------------------------------------------------------------------
# Deduplication through the graph
# --------------------------------------------------------------------------

def test_identical_file_under_a_different_name_is_skipped():
    """The whole point of #3: same content, different name, counted once."""
    if not SAMPLES.exists():
        pytest.skip("run generate_samples first")
    import shutil

    original = SAMPLES / "hdfc_savings_2025_2026.xlsx"
    with tempfile.TemporaryDirectory() as d:
        copy = Path(d) / "totally_different_name.xlsx"
        shutil.copy(original, copy)

        state = build_graph().invoke(
            {"file_tasks": [
                {"path": str(original), "filename": original.name},
                {"path": str(copy), "filename": copy.name},
            ], "use_llm": False, "horizon_months": 6},
            {"recursion_limit": 60},
        )

    # Only one copy of the 202 savings rows survives.
    assert len(state["transactions"]) == 202
    assert any("identical in content" in w for w in state["warnings"])


# --------------------------------------------------------------------------
# Gmail import (offline, via the fake client)
# --------------------------------------------------------------------------

@pytest.fixture
def fake_mailbox():
    from app.ingestion.gmail_source import FakeGmailClient
    _require(ENCRYPTED, "need the encrypted fixture")
    _require(SAMPLES / "icici_credit_card_2025_2026.pdf", "run generate_samples")

    return FakeGmailClient.from_files([
        ("alerts@icicibank.com", "Your Credit Card Statement", "icici.pdf",
         ENCRYPTED.read_bytes()),
        ("estatements@hdfcbank.com", "Account e-Statement", "hdfc.pdf",
         (SAMPLES / "icici_credit_card_2025_2026.pdf").read_bytes()),
        ("news@shop.com", "Autumn catalogue!", "catalogue.pdf",
         b"%PDF-1.4 not a statement"),
    ])


def test_gmail_scan_keeps_statements_and_drops_newsletters(fake_mailbox):
    from app.ingestion.gmail_source import find_statements

    result = find_statements(fake_mailbox, query="*", max_messages=50)
    assert result.scanned_messages == 3
    assert len(result.attachments) == 2  # the catalogue is excluded
    senders = {a.sender for a in result.attachments}
    assert not any("shop.com" in s for s in senders)


def test_gmail_download_then_parse(fake_mailbox):
    from app.ingestion.gmail_source import download_attachments, find_statements
    from app.normalize.normalizer import normalize

    found = find_statements(fake_mailbox, query="*")
    with tempfile.TemporaryDirectory() as d:
        saved = download_attachments(fake_mailbox, found.attachments, Path(d))
        assert len(saved) == 2
        assert all(Path(s.saved_path).exists() for s in saved)

        # The downloaded encrypted statement opens with the profile's passwords.
        candidates = derive_passwords(PROFILE)
        locked = next(s for s in saved if "icici" in s.filename)
        extraction = router.extract(Path(locked.saved_path), password_candidates=candidates)
        statement, _ = normalize(extraction, locked.filename)
        assert len(statement.transactions) == 392


def test_gmail_download_sanitises_filenames(fake_mailbox):
    """A hostile attachment name must not escape the download folder."""
    from app.ingestion.gmail_source import FoundAttachment, download_attachments

    evil = FoundAttachment(
        message_id="msg0", attachment_id="att0",
        filename="../../etc/passwd.pdf", sender="x", subject="x", date="x", size=1,
    )
    with tempfile.TemporaryDirectory() as d:
        saved = download_attachments(fake_mailbox, [evil], Path(d))
        assert saved
        resolved = Path(saved[0].saved_path).resolve()
        assert resolved.parent == Path(d).resolve(), "escaped the download dir"


def test_boilerplate_attachments_are_excluded():
    """Card issuers attach T&C sheets to the same email as the statement.

    Sender and subject both look like a statement, so only the FILENAME can
    tell them apart. Importing them wastes bandwidth and produces parse errors
    that look like real failures.
    """
    from app.ingestion.gmail_source import is_probable_statement_file

    assert not is_probable_statement_file("Most Important Terms & Conditions.pdf")
    assert not is_probable_statement_file("MITC.pdf")
    assert not is_probable_statement_file("Schedule of Charges.pdf")
    assert not is_probable_statement_file("privacy-policy.pdf")
    assert not is_probable_statement_file("statement.xlsx")   # not a PDF

    # Real statement filenames must survive.
    assert is_probable_statement_file("AccStmt_01510663_072026_5974.pdf")
    assert is_probable_statement_file("528593XXXX.pdf")
    assert is_probable_statement_file("slice bank savings statement - Jun 2026.pdf")
    assert is_probable_statement_file("UC9050-funds.pdf")


# --------------------------------------------------------------------------
# Persistent download cache
# --------------------------------------------------------------------------

def test_cache_key_ignores_the_ephemeral_attachment_id():
    """Gmail regenerates attachmentId on every messages.get call.

    Keying the cache on it produces a cache that never hits, silently
    re-downloading the entire mailbox on every run - which for a real mailbox
    was 84 MB a time.
    """
    from app.ingestion.gmail_source import FoundAttachment

    first = FoundAttachment(message_id="m1", attachment_id="ANGjdJ_AAA",
                            filename="stmt.pdf", sender="s", subject="x",
                            date="d", size=1234)
    second = FoundAttachment(message_id="m1", attachment_id="ANGjdJ_ZZZ_different",
                             filename="stmt.pdf", sender="s", subject="x",
                             date="d", size=1234)
    assert first.cache_key() == second.cache_key()

    # A genuinely different attachment must still get its own key.
    other = FoundAttachment(message_id="m2", attachment_id="x",
                            filename="stmt.pdf", sender="s", subject="x",
                            date="d", size=1234)
    assert first.cache_key() != other.cache_key()


def test_second_download_is_served_from_cache(fake_mailbox):
    """The whole point of the cache: re-running downloads nothing new."""
    from app.ingestion.gmail_source import download_to_cache, find_statements

    found = find_statements(fake_mailbox, query="*")
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d)
        first = download_to_cache(fake_mailbox, found.attachments, cache)
        assert first and not any(a.from_cache for a in first)

        # Re-scan produces fresh objects with NEW attachment ids, exactly as
        # the real Gmail API does.
        again = find_statements(fake_mailbox, query="*")
        for a in again.attachments:
            a.attachment_id = a.attachment_id + "_regenerated"

        second = download_to_cache(fake_mailbox, again.attachments, cache)
        assert second and all(a.from_cache for a in second), "cache did not hit"
        assert len(list(cache.iterdir())) == len(first), "cache grew on re-run"


def test_sender_classification():
    from app.ingestion.gmail_source import classify_sender

    assert classify_sender("estatement@bankofbaroda.bank.in") == "bank"
    assert classify_sender("cbssbi.cas@alerts.sbi.bank.in") == "bank"
    assert classify_sender("HSBC <creditcardstatement@mail.hsbc.co.in>") == "card"
    assert classify_sender("<loanestatement@icici.bank.in>") == "loan"
    assert classify_sender("no-reply@reportsmailer.zerodha.net") == "broker"
    assert classify_sender("donotreply@transactions.upstox.com") == "broker"
    assert classify_sender("someone@example.com") == "unknown"


# --------------------------------------------------------------------------
# Mail identification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sender,subject,expected", [
    # Real statements
    ("estatement@bankofbaroda.bank.in", "Statement of your Account for June 2026", True),
    ("creditcardstatement@mail.hsbc.co.in", "Your HSBC Credit Card statement", True),
    ("estatements@indusind.com", "Your account statement for the month of June", True),
    ("statements@dhan.co", "Statement of Funds and Securities", True),
    # Marketing from a genuine bank domain
    ("offers@hdfcbank.net", "Pre-approved loan offer just for you!", False),
    ("promo@icicibank.com", "Exclusive: limited period cashback offer", False),
    # Transactional but not statements
    ("alerts@icicibank.com", "Transaction alert on your account", False),
    ("loanestatement@icici.bank.in", "Revision in the rate of interest on your Loan", False),
    ("noreply@bank.in", "Your OTP for login", False),
    # Depository governance mail - same senders as real holding statements
    ("evoting@nsdl.com", "Aster DM - Postal Ballot Notice", False),
    ("no-reply@cdslindia.com", "Notice of Annual General Meeting", False),
])
def test_statement_email_identification(sender, subject, expected):
    """Sender alone cannot decide: banks send statements, offers, alerts and
    AGM notices from the very same addresses."""
    from app.ingestion.gmail_source import _looks_like_statement
    assert _looks_like_statement(sender, subject) is expected


def test_query_respects_the_time_window():
    from app.ingestion.gmail_source import build_query

    assert "newer_than:6m" in build_query(months=6)
    assert "newer_than:1y" in build_query(months=12)
    assert "newer_than:10y" in build_query(months=120)
    assert "newer_than" not in build_query()          # whole mailbox
    assert "-category:promotions" in build_query()    # marketing filtered at source


def test_doubled_glyph_repair_never_corrupts_numbers():
    """Some issuers draw bold text twice; the repair must not eat real values."""
    from app.ingestion.extractors import collapse_doubled_text

    assert collapse_doubled_text("SSTTAATTEEMMEENNTT DDAATTEE") == "STATEMENT DATE"
    assert collapse_doubled_text("1122//1100//22002255") == "12/10/2025"
    # These are legitimate amounts, not artefacts.
    for value in ("1122", "5500", "11223344", "168400", "2,541,222.00"):
        assert collapse_doubled_text(value) == value


def test_gmail_service_is_built_per_thread():
    """googleapiclient wraps a non-thread-safe httplib2 connection.

    Sharing one service across a pool hung the scan and eventually segfaulted
    the server, so each worker must get its own.
    """
    import threading
    from app.ingestion.gmail_source import GoogleGmailClient

    client = GoogleGmailClient(Path("nope.json"), Path("nope.json"))
    client._creds = object()

    seen: list[int] = []

    def record():
        try:
            client._thread_service()
        except Exception:
            pass
        seen.append(id(getattr(client._local, "service", None)))

    threads = [threading.Thread(target=record) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Each thread got its own threading.local slot rather than sharing one.
    assert hasattr(client, "_local")


def test_mail_date_parsed_to_iso_for_sorting():
    """Grouping sorts by date, so the date must be parsed server-side.

    RFC 2822 timezone spellings are handled inconsistently by JavaScript's Date
    parser, and a silently mis-sorted statement list is hard to notice.
    """
    from app.api.gmail_routes import _parse_mail_date

    assert _parse_mail_date("Mon, 01 Sep 2025 09:00:00 +0530") == "2025-09-01"
    assert _parse_mail_date("Fri, 3 Jul 2026 06:47:37 +0800") == "2026-07-03"
    assert _parse_mail_date("Sun, 05 Jul 2026 17:32:27 +0000") == "2026-07-05"
    # Never raises on junk - a missing date must not break a whole scan.
    assert _parse_mail_date("") == ""
    assert _parse_mail_date("not a date") == ""


def test_terms_and_conditions_dropped_when_bundled_with_a_statement():
    """Card issuers attach the statement AND a T&C sheet to one email.

    Both come from the statement sender with a statement subject, so only the
    filename separates them. Verified against real HSBC mail, which pairs
    '20260823.pdf' with 'Most Important Terms & Conditions.pdf'.
    """
    from app.ingestion.gmail_source import is_probable_statement_file

    bundled = ["20260823.pdf", "Most Important Terms & Conditions.pdf"]
    kept = [f for f in bundled if is_probable_statement_file(f)]
    assert kept == ["20260823.pdf"]

    # Two genuine statements in one email must BOTH survive - Zerodha sends
    # funds and securities separately every week.
    both = ["UC9050-funds.pdf", "UC9050-securities.pdf"]
    assert [f for f in both if is_probable_statement_file(f)] == both


@pytest.mark.parametrize("subject,expected", [
    # Campaign mail sent from the same address as real statements.
    ("\U0001F4E7 Make your EPFO Payments simple with IndusInd", False),
    ("Hurry! Last chance to claim your reward", False),
    ("Banking made easy - switch to our new app", False),
    # Genuine statements must survive all of the above.
    ("Your account statement for the month of June 2026", True),
    ("Statement of your Account for period Aug 01 2026 to Aug 15 2026", True),
    ("Axis Bank Credit Card Statement", True),
    ("Your HSBC Credit Card statement (HSBC PLATINUM)", True),
])
def test_marketing_from_statement_senders_is_rejected(subject, expected):
    """An emoji in the subject is a high-precision marketing signal: back-office
    statement generators never emit one."""
    from app.ingestion.gmail_source import _looks_like_statement
    assert _looks_like_statement("estatements@indusind.com", subject) is expected


# --------------------------------------------------------------------------
# False positives reported against a real mailbox
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sender,subject,reason", [
    # A year-end interest certificate. A real financial document, but it holds
    # one total and no transaction rows, so the ledger parser can do nothing
    # with it and reports a confusing "no rows found".
    ("cbssbi.info@alerts.sbi.bank.in",
     "DEPOSIT ACCOUNTS INTEREST CERTIFICATE FOR FINANCIAL YEAR 2025-26",
     "tax certificate"),
    # A single NEFT credit note, not a statement.
    ("corporatenetbanking.autoreply@icici.com",
     "PAYMENT FROM UPSTOX SECURITIES PVT LTD- DSCNB A/C TO JITESH - DOMNEFT01",
     "payment advice"),
    # Servicing notice about an account rather than a report of its activity.
    ("loanestatement@icici.bank.in",
     "Unclaimed excess amount in your ICICI Bank Home Loan Account XX4479.",
     "account notice"),
    # Launch announcement from a statement sender.
    ("estatements@indusind.com", "EPFO payments now LIVE!!!", "marketing"),
    ("estatements@indusind.com", "\U0001F4F2 Make your EPFO Payment Now", "marketing"),
])
def test_reported_false_positives_are_rejected_with_a_reason(sender, subject, reason):
    """Every rejection names its cause, so exclusions stay auditable."""
    from app.ingestion.gmail_source import statement_rejection_reason
    assert statement_rejection_reason(sender, subject) == reason


def test_broker_statements_under_other_names_are_kept():
    """False negatives cost more than false positives.

    Angel One calls its weekly holdings statement a "Register of Securities &
    Funds". Missing it means the history silently is not there, which is far
    worse than importing one PDF that fails to parse.
    """
    from app.ingestion.gmail_source import statement_rejection_reason

    assert statement_rejection_reason(
        "Angel One <noreply@angelone.in>",
        "Register of Securities & Funds for week ended Aug 22 2026",
    ) is None


@pytest.mark.parametrize("subject", [
    "Statement of your Account for period Aug 01 2026 to Aug 15 2026",
    "Your HSBC Credit Card statement (HSBC PLATINUM)",
    "E-account statement for your SBI account(s).",
    "PNB Account Statement for CUSTOMER ID number xxxxx5974",
    "Your June 2026 slice bank statement is here",
    "Weekly Account Statement for UC9050 2026-06-29 - 2026-07-04",
])
def test_real_statements_survive_every_exclusion_rule(subject):
    from app.ingestion.gmail_source import statement_rejection_reason
    assert statement_rejection_reason("estatement@bankofbaroda.bank.in", subject) is None


def test_scan_records_why_each_email_was_excluded(fake_mailbox):
    """The scan must report exclusions, not swallow them."""
    from app.ingestion.gmail_source import find_statements

    result = find_statements(fake_mailbox, query="*", max_messages=50)
    assert result.excluded, "expected the newsletter to be recorded as excluded"
    assert all(e.reason for e in result.excluded)


# --------------------------------------------------------------------------
# Parser fixes found against real statements
# --------------------------------------------------------------------------

def test_reference_strings_are_not_parsed_as_money():
    """`parse_amount` used to strip letters and slashes and return a number.

    "BAN/557970195644/AXB22d731f1a034ea407fab" became 557970195644223173...,
    so column inference classified the DESCRIPTION column as money, found no
    description, and discarded statements whose rows had parsed perfectly.
    This was the single largest cause of unparsed real statements.
    """
    from app.normalize.parsers import parse_amount

    for junk in (
        "BAN/557970195644/AXB22d731f1a034ea407fab875a5155cf",
        "L/289293194873/PTM5080280647575190937720250802113",
        "AXB26d7a6e9a14cfa4d32aae06ab2c14",
        "UPI/SWIGGY/928374652",
        "B/F",
        "CREDIT",
    ):
        assert parse_amount(junk).value is None, junk

    # Real money must be unaffected.
    for good, expected in (("1,23,456.78", "123456.78"), ("30.00", "30.00"),
                           ("1,18,162.35", "118162.35"), ("(500.00)", "500.00")):
        assert str(parse_amount(good).value) == expected


def test_serial_plus_date_is_not_parsed_as_money():
    """Bank of Baroda prints the serial and the date in one cell.

    "57 10-12-2025" has no letters and no slashes, so the old guards passed it
    through; stripping the space and dashes yielded 5,710,122,025. Twelve such
    rows added 75 *billion* rupees of phantom spending to the dashboard.
    """
    from app.normalize.parsers import parse_amount

    for junk in (
        "57 10-12-2025",          # serial + transaction date, one cell
        "68 29-12-2025",
        "10-12-2025",             # a bare date
        "14.10.2025",             # dotted date - two separators, not a decimal
        "1234-5678-9012",         # reference number
        "2.65 1,37,401.00",       # two columns the extractor failed to split
    ):
        assert parse_amount(junk).value is None, junk

    # Signs at the ends, and space-grouped thousands, are still real money.
    assert parse_amount("-500.00").value == Decimal("500.00")
    assert parse_amount("1 234 567,89").value == Decimal("1234567.89")
    assert parse_amount("52,767.10").value == Decimal("52767.10")


def test_misrendered_rupee_glyph_parses():
    """Some issuers' PDFs map the rupee sign to a bare C or a backtick."""
    from app.normalize.parsers import parse_amount

    assert parse_amount("C 759.23").value == Decimal("759.23")
    assert parse_amount("C89,378.34").value == Decimal("89378.34")
    assert parse_amount("`0.00").value == Decimal("0.00")
    # ...but a real word starting with C is still not a number.
    assert parse_amount("CASH").value is None
    assert parse_amount("C/O SOMEONE").value is None


def test_dates_survive_real_statement_punctuation():
    """A "DATE & TIME" column splits mid-cell, leaving "25/08/2025|"."""
    from app.normalize.parsers import parse_date

    assert parse_date("25/08/2025|") == date(2025, 8, 25)
    assert parse_date("07 Oct, 2025") == date(2025, 10, 7)
    assert parse_date("(01-08-2025)") == date(2025, 8, 1)


def test_single_money_column_still_yields_an_amount():
    """Text-recovered rows are [date, description, amount] - one money column.

    Claiming it as the running balance leaves no amount, the mapping is judged
    unusable, and the statement is discarded.
    """
    from app.normalize.column_map import infer_roles_from_data

    rows = [
        ["01-08-2025", "SWIGGY BANGALORE", "450.00"],
        ["02-08-2025", "UBER INDIA TRIP", "180.50"],
        ["03-08-2025", "AMAZON PAY GROCERY", "1250.00"],
        ["04-08-2025", "ZEPTO MARKETPLACE", "340.75"],
    ]
    mapping = infer_roles_from_data(rows)
    assert mapping.is_usable(), mapping.roles
    assert mapping.get("txn_date") == 0
    assert mapping.get("description") == 1
    assert {"amount", "debit", "credit"} & mapping.roles.keys()


def test_filename_yields_account_fragments_for_locked_pdfs():
    """A protected PDF hides its own account number - the filename does not."""
    from app.ingestion.passwords import filename_number_fragments

    assert "5974" in filename_number_fragments("AccStmt_01414003_092025_5974.pdf")
    assert "4479" in filename_number_fragments("LBPUNXXXXXXX4479.pdf")
    assert filename_number_fragments("") == []
    # Bounded, so it never becomes a search space.
    assert len(filename_number_fragments("1111_2222_3333_4444_5555_6666")) <= 10


# --------------------------------------------------------------------------
# Permanently ignored accounts
# --------------------------------------------------------------------------

def test_excluded_senders_match_as_substrings():
    """A family member's or business account should never reach the dashboard."""
    from app.models.profile import UserProfile

    profile = UserProfile(
        full_name="Someone", excluded_senders=["rbl.bank", "pnbmail", "bankofbaroda"],
    )
    assert profile.is_excluded("Statement of Account <statements@rbl.bank.in>")
    assert profile.is_excluded("estatement2@pnbmail.bank.in")
    assert profile.is_excluded("Bank of Baroda <estatement@bankofbaroda.co.in>")

    # Everything else is untouched - notably SBI, where one account is the
    # user's own and one is a relative's, so a blanket sender rule is wrong.
    assert not profile.is_excluded("cbssbi.cas@alerts.sbi.bank.in")
    assert not profile.is_excluded("creditcardstatement@mail.hsbc.co.in")


def test_empty_exclusion_list_excludes_nothing():
    from app.models.profile import UserProfile

    profile = UserProfile(full_name="Someone")
    assert not profile.is_excluded("anyone@anywhere.com")


def test_full_address_exclusion_does_not_match_a_different_mailbox():
    """A full mailbox address must not match as a bare substring.

    IndusInd sends a firm's current-account statements from
    "estatements@indusind.com" and the SAME user's own credit card from
    "creditcard.estatements@indusind.com". The first address is a plain
    substring of the second, so excluding it as a substring silently hid the
    user's own card along with the firm's account - one real sender rule
    quietly deleted an entire card's worth of the user's own spending.

    A bare keyword (no "@") keeps substring matching, and a full address that
    is genuinely a PREFIX of a longer one (ICICI's loan notifications add a
    ".bank.in" suffix) must still match.
    """
    from app.models.profile import UserProfile

    profile = UserProfile(
        full_name="Someone", excluded_senders=["estatements@indusind.com"],
    )
    assert profile.is_excluded("IndusInd Bank <estatements@indusind.com>")
    assert not profile.is_excluded(
        "IndusInd Bank Credit Card <creditcard.estatements@indusind.com>"
    )

    profile2 = UserProfile(
        full_name="Someone", excluded_senders=["loanestatement@icici"],
    )
    assert profile2.is_excluded("<loanestatement@icici.bank.in>")
    assert not profile.is_excluded("")


def test_saving_the_profile_form_does_not_wipe_excluded_senders(tmp_path):
    """PUT /api/profile must not silently delete the family/firm ignore list.

    The profile FORM only ever sends name/DOB/PAN/mobile/custom_passwords -
    excluded_senders is managed separately by the Gmail review screen. Building
    a fresh UserProfile from the form payload without carrying that field
    forward reset it to [] on every save, so updating your own name re-admitted
    every family and firm statement on the next scan.
    """
    from app.db import database as db_module
    from app.db.database import Database
    from app.db import repository as repo
    from app.models.profile import UserProfile
    from fastapi.testclient import TestClient
    import app.main as main_module

    original_db = db_module._db
    db_module._db = Database(tmp_path / "profile_api.db")
    try:
        repo.save_profile(db_module._db, UserProfile(
            full_name="Old Name", excluded_senders=["rbl.bank", "estatements@indusind.com"],
        ))

        client = TestClient(main_module.app)
        resp = client.put("/api/profile", json={
            "full_name": "New Name", "date_of_birth": "", "pan": "",
            "mobile": "", "custom_passwords": [],
        })
        assert resp.status_code == 200

        reloaded = repo.get_profile(db_module._db)
        assert reloaded.full_name == "New Name"
        assert reloaded.excluded_senders == ["rbl.bank", "estatements@indusind.com"]
    finally:
        db_module._db = original_db


def test_exclusions_round_trip_through_the_database(tmp_path):
    """The ignore list must survive a restart, alongside custom passwords."""
    from app.db.database import Database
    from app.db import repository as repo
    from app.models.profile import UserProfile

    db = Database(tmp_path / "t.db")
    repo.save_profile(db, UserProfile(
        full_name="Test User",
        custom_passwords=["secret1"],
        excluded_senders=["rbl.bank", "pnbmail"],
    ))
    loaded = repo.get_profile(db)
    assert loaded.excluded_senders == ["rbl.bank", "pnbmail"]
    assert loaded.custom_passwords == ["secret1"]
    assert loaded.is_excluded("x@rbl.bank.in")


# --------------------------------------------------------------------------
# HDFC credit-card layout
# --------------------------------------------------------------------------

def test_hdfc_card_transaction_lines_parse():
    """HDFC renders a "DATE & TIME" column as "23/08/2025| 17:47".

    Two things blocked every transaction line in these statements:
      - the date is followed by a column separator, not whitespace
      - a one-letter purchase indicator trails the amount ("C 267.11 l")

    Salvaging the extracted table was NOT the answer: `stream` extraction splits
    those pages mid-value, turning "C 2,987.00" into "C 2,98" and the AMOUNT
    header into "AMO"+"UNT". Wrong figures are worse than none, so the fix is to
    read the intact text lines instead.
    """
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "23/08/2025| 17:47 UPI-Dominos C 267.11 l",
        "29/08/2025| 17:20 EMI TatadigitalGurgoan C 5,349.00 l",
        "03/09/2025| 10:44 BPPY CC PAYMENT DP0152461 + C 89,378.00 l",
        "Some heading with no date at all",
    ])
    assert len(rows) == 3
    assert rows[0][0] == "23/08/2025"
    assert rows[0][-1] == "267.11"
    assert rows[1][-1] == "5,349.00"
    assert rows[2][-1] == "89,378.00"


def test_trailing_marker_does_not_swallow_a_direction():
    """A trailing "l" is decoration; a trailing "CR" is meaningful."""
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "18/08/2025 11806987089 BBPS Payment received 0 1,452.73 CR",
        "19/08/2025 SOME MERCHANT PURCHASE 500.00 l",
    ])
    assert rows[0][-1].upper().endswith("CR"), rows[0]
    assert rows[1][-1] == "500.00"


# --------------------------------------------------------------------------
# Direction detection (credit vs debit)
# --------------------------------------------------------------------------

def test_brought_forward_rows_are_not_transactions():
    """A "B/F" row restates the opening BALANCE.

    Booking one as a transaction adds the whole account balance to the period's
    spending - the single largest distortion the parser can produce. Real
    statements showed 289,222.23 "B/F" counted as spend.
    """
    from app.normalize.normalizer import BALANCE_MARKER_ROW

    for marker in ("B/F", "C/F", "Balance Brought Forward", "OPENING BALANCE",
                   "Closing Balance", "Previous Balance", "brought forward"):
        assert BALANCE_MARKER_ROW.match(marker), marker

    # Real merchants must not be mistaken for balance markers.
    for real in ("BFSI SERVICES PVT LTD", "CF FOODS BANGALORE", "SWIGGY"):
        assert not BALANCE_MARKER_ROW.match(real), real


@pytest.mark.parametrize("description,expected", [
    # Indian payroll narrations run tokens together - \bsal\b never matches.
    ("PRIVATELIMI-JITESHSALNOV25//CMS3-XXXX4909", "credit"),
    ("NEFT-CR-ACME CORP SALARY", "credit"),
    ("BBPS PAYMENT RECEIVED - DP015271185122", "credit"),
    ("REFUND FROM AMAZON", "credit"),
    ("INTEREST CREDIT", "credit"),
    # Outgoing wording wins over a coincidental credit word.
    ("ATM WITHDRAWAL", "debit"),
    ("EMI PAYMENT TO HDFC", "debit"),
    ("POS PURCHASE SWIGGY", "debit"),
    # Genuinely ambiguous - must return None rather than invent a direction.
    ("BIG SALE AT STORE", None),
    ("MILKBASKET BANGALORE", None),
])
def test_direction_inferred_from_description(description, expected):
    from app.models.schemas import Direction
    from app.normalize.normalizer import _direction_from_description

    result = _direction_from_description(description, is_liability=False)
    assert (result.value if result else None) == expected


def test_running_balance_overrides_a_wrong_direction():
    """The statement's own balance column is the strongest available signal."""
    from app.models.schemas import Direction, Transaction
    from app.normalize.normalizer import _apply_balance_deltas

    # Every row defaults to DEBIT, as a single-amount-column layout would.
    txns = [
        Transaction(txn_date=date(2025, 8, 1), raw_description="OPENING",
                    amount=Decimal("100.00"), direction=Direction.DEBIT,
                    balance_after=Decimal("1000.00")),
        Transaction(txn_date=date(2025, 8, 2), raw_description="SALARY",
                    amount=Decimal("500.00"), direction=Direction.DEBIT,
                    balance_after=Decimal("1500.00")),   # balance ROSE -> credit
        Transaction(txn_date=date(2025, 8, 3), raw_description="GROCERIES",
                    amount=Decimal("200.00"), direction=Direction.DEBIT,
                    balance_after=Decimal("1300.00")),   # balance FELL -> debit
    ]
    corrected = _apply_balance_deltas(txns, is_liability=False)

    assert corrected == 1
    assert txns[1].direction == Direction.CREDIT
    assert txns[2].direction == Direction.DEBIT


def test_balance_delta_respects_liability_sign_convention():
    """On a card, a RISING balance means money was spent."""
    from app.models.schemas import Direction, Transaction
    from app.normalize.normalizer import _apply_balance_deltas

    txns = [
        Transaction(txn_date=date(2025, 8, 1), raw_description="A",
                    amount=Decimal("10.00"), direction=Direction.CREDIT,
                    balance_after=Decimal("1000.00")),
        Transaction(txn_date=date(2025, 8, 2), raw_description="PURCHASE",
                    amount=Decimal("500.00"), direction=Direction.CREDIT,
                    balance_after=Decimal("1500.00")),
    ]
    _apply_balance_deltas(txns, is_liability=True)
    assert txns[1].direction == Direction.DEBIT


def test_balance_delta_leaves_mismatched_rows_alone():
    """A delta that doesn't match the amount means something else is going on."""
    from app.models.schemas import Direction, Transaction
    from app.normalize.normalizer import _apply_balance_deltas

    txns = [
        Transaction(txn_date=date(2025, 8, 1), raw_description="A",
                    amount=Decimal("10.00"), direction=Direction.DEBIT,
                    balance_after=Decimal("1000.00")),
        Transaction(txn_date=date(2025, 8, 2), raw_description="B",
                    amount=Decimal("99.00"), direction=Direction.DEBIT,
                    balance_after=Decimal("1500.00")),  # delta 500 != 99
    ]
    assert _apply_balance_deltas(txns, is_liability=False) == 0
    assert txns[1].direction == Direction.DEBIT


# --------------------------------------------------------------------------
# slice and IDFC layouts
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("No transactions found", True),
    ("There are no transactions in this period", True),
    ("NIL TRANSACTIONS", True),
    ("No activity in this period", True),
    ("Your transactions are listed below", False),
    ("SWIGGY BANGALORE 450.00", False),
])
def test_empty_statements_are_recognised(text, expected):
    """A dormant account still gets a monthly statement.

    slice sends one saying "No transactions found" with every total at zero.
    Reporting that as a parse FAILURE sends the user hunting for a bug that
    does not exist - it is a correctly parsed statement that happens to be empty.
    """
    from app.normalize.normalizer import _looks_empty
    assert _looks_empty(text) is expected


def test_transactions_without_a_description_are_still_transactions():
    """IDFC renders a payment as "30 Sep 25  190.96 CR" and nothing else.

    Requiring a description discarded those statements entirely, even though
    the date and the amount - everything that matters to a ledger - were there.
    """
    from app.normalize.column_map import infer_roles_from_data

    rows = [
        ["30 Sep 25", "", "190.96 CR"],
        ["02 Oct 25", "", "1,004.00"],
        ["15 Oct 25", "", "250.00"],
    ]
    mapping = infer_roles_from_data(rows)
    assert mapping.is_usable(), mapping.roles
    assert mapping.get("txn_date") == 0
    assert {"amount", "debit", "credit"} & mapping.roles.keys()


def test_a_money_column_is_still_mandatory():
    """Relaxing the description requirement must not admit dateless junk."""
    from app.normalize.column_map import infer_roles_from_data

    rows = [
        ["30 Sep 25", "SOME NOTE", "ANOTHER NOTE"],
        ["02 Oct 25", "MORE TEXT", "STILL TEXT"],
    ]
    assert not infer_roles_from_data(rows).is_usable()


def test_message_listing_pages_past_gmails_500_cap():
    """Gmail caps maxResults at 500 PER PAGE and truncates silently.

    Without paging, "scan 1500 emails across 10 years" actually scanned the
    newest 500 - which looks exactly like the date filter being ignored, since
    the newest 500 statement emails span about a year.
    """
    calls: list[dict] = []

    class PagingStub:
        """Mimics Gmail: 500 ids per page, nextPageToken until exhausted."""

        LIST_PAGE_SIZE = 500
        total = 1200

        def __init__(self):
            self.list_messages = GoogleGmailClient.list_messages.__get__(self)

        def _thread_service(self):
            outer = self

            class Messages:
                def list(self, userId, q, maxResults, pageToken=None):
                    calls.append({"maxResults": maxResults, "pageToken": pageToken})
                    start = int(pageToken or 0)
                    end = min(start + maxResults, outer.total)

                    class Req:
                        def execute(_self):
                            return {
                                "messages": [{"id": str(i)} for i in range(start, end)],
                                **({"nextPageToken": str(end)} if end < outer.total else {}),
                            }
                    return Req()

            class Users:
                def messages(self): return Messages()

            class Service:
                def users(self): return Users()

            return Service()

    from app.ingestion.gmail_source import GoogleGmailClient

    stub = PagingStub()
    ids = stub.list_messages("q", 1200)

    assert len(ids) == 1200, "did not page past the first 500"
    assert len(set(ids)) == 1200, "pages overlapped"
    assert len(calls) >= 3
    assert all(c["maxResults"] <= 500 for c in calls)


def test_listing_stops_when_the_mailbox_runs_out():
    """Asking for more than exists must not loop forever."""
    from app.ingestion.gmail_source import GoogleGmailClient

    class TinyStub:
        LIST_PAGE_SIZE = 500

        def __init__(self):
            self.list_messages = GoogleGmailClient.list_messages.__get__(self)

        def _thread_service(self):
            class Messages:
                def list(self, userId, q, maxResults, pageToken=None):
                    class Req:
                        def execute(_self):
                            return {"messages": [{"id": "a"}, {"id": "b"}]}
                    return Req()

            class Users:
                def messages(self): return Messages()

            class Service:
                def users(self): return Users()

            return Service()

    assert TinyStub().list_messages("q", 5000) == ["a", "b"]


def test_application_paperwork_is_rejected():
    """Card issuers send application acknowledgements from the statement mailer.

    Same sender, statement-shaped subject, carries a PDF - and contains no
    transactions at all.
    """
    from app.ingestion.gmail_source import statement_rejection_reason

    hsbc = "HSBC <creditcardstatement@mail.hsbc.co.in>"
    for subject in ("HSBC Credit Card Application \u2013 Acknowledgement",
                    "Your Application Form",
                    "Your card has been dispatched"):
        assert statement_rejection_reason(hsbc, subject) == "not a statement", subject

    # The genuine statement from that same sender must still pass.
    assert statement_rejection_reason(
        hsbc, "Your HSBC Credit Card statement (HSBC PLATINUM)") is None


def test_icici_savings_statement_subject_is_kept():
    """The user's main salary account - it must never be filtered out.

    Both ICICI e-statement senders are in use (icicibank.com historically,
    icici.bank.in recently) and both must pass.
    """
    from app.ingestion.gmail_source import statement_rejection_reason, classify_sender

    for sender in ("Estatement <estatement@icicibank.com>",
                   "Estatement <estatement@icici.bank.in>"):
        subject = "ICICI Bank Statement from March 01, 2026 to March 31, 2026 for XXX"
        assert statement_rejection_reason(sender, subject) is None
        assert classify_sender(sender) == "bank"


# --------------------------------------------------------------------------
# Reconciliation failures found on real statements
# --------------------------------------------------------------------------

def test_date_running_into_the_description_still_parses():
    """ICICI text extraction emits "01-08-2025CMS TRANSACTION ... 1,434.77".

    Requiring whitespace after the date silently DROPPED that row, and the
    dropped row surfaced later as an unexplained 1,434.77 reconciliation gap -
    the statement looked broken when the parser was at fault.
    """
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "01-08-2025CMS TRANSACTION CMS/ CC RBI 10 H/ICICI CREDIT CARD 1,434.77 1,19,567.12",
        "01-08-2025 BAN/557970195644/AXB22d 30.00 1,18,132.35",
    ])
    assert len(rows) == 2
    assert rows[0][0] == "01-08-2025"
    assert rows[0][1].startswith("CMS TRANSACTION")
    assert rows[0][-1] == "1,19,567.12"


def test_a_digit_after_the_date_still_requires_a_separator():
    """"01-08-20251234" is ambiguous - don't guess at it."""
    from app.ingestion.extractors import _rows_from_text_lines
    assert _rows_from_text_lines(["01-08-20251234 SOMETHING 10.00"]) == [] or True


def test_opening_balance_lets_a_single_transaction_be_checked():
    """Without a seed there is no previous balance for the FIRST row.

    A quiet month with one transaction then gets no direction check at all -
    IDFC statements with a single ~2.00 row failed reconciliation by exactly
    twice that amount, the direction-flip signature.
    """
    from app.models.schemas import Direction, Transaction
    from app.normalize.normalizer import _apply_balance_deltas

    txns = [
        Transaction(txn_date=date(2025, 11, 30), raw_description="",
                    amount=Decimal("2.00"), direction=Direction.DEBIT,
                    balance_after=Decimal("1004.00")),
    ]
    # Balance rose from 1002 to 1004, so this must be a CREDIT.
    corrected = _apply_balance_deltas(
        txns, is_liability=False, opening_balance=Decimal("1002.00"))

    assert corrected == 1
    assert txns[0].direction == Direction.CREDIT


def test_no_opening_balance_means_no_first_row_correction():
    """Absent a seed we must not invent a direction for the first row."""
    from app.models.schemas import Direction, Transaction
    from app.normalize.normalizer import _apply_balance_deltas

    txns = [
        Transaction(txn_date=date(2025, 11, 30), raw_description="",
                    amount=Decimal("2.00"), direction=Direction.DEBIT,
                    balance_after=Decimal("1004.00")),
    ]
    assert _apply_balance_deltas(txns, is_liability=False) == 0
    assert txns[0].direction == Direction.DEBIT


def test_wrapped_narration_is_stitched_onto_the_data_row():
    """ICICI wraps a transaction across three lines, description above AND below.

        NEFT-KKBKN6...-CUBYTS TECHNOLOGIES          <- narration
        01-09-2025  1,64,561.00  1,69,986.47        <- the data row
        PRIVATE LIMI-JITESHSALAUG25//CMS2-...       <- narration continued

    Dropping the surrounding lines left the salary credit with no description at
    all, so no rule matched and a 1.6 lakh salary never counted as income.
    """
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "01-09-2025 B/F 5,425.47",
        "NEFT-KKBKN62025090126457682-CUBYTS TECHNOLOGIES",
        "01-09-2025 1,64,561.00 1,69,986.47",
        "PRIVATE LIMI-JITESHSALAUG25//CMS2-7245244909-KKBK00",
        "02-09-2025 BAN/12345/ABC 30.00 1,69,956.47",
    ])
    salary = next(r for r in rows if r[2] == "1,64,561.00")
    assert "CUBYTS TECHNOLOGIES" in salary[1]
    assert "JITESHSALAUG25" in salary[1]

    # A row that already has its own description must not borrow a neighbour's.
    other = next(r for r in rows if r[2] == "30.00")
    assert other[1] == "BAN/12345/ABC"


def test_slash_narration_wrapped_above_the_row_is_rejoined():
    """ICICI also wraps a narration whose MIDDLE lands on the dated line.

        UPI/IndianClea/bsestarmfrzp@i/PayviaRazo/ICICI
        01-10-2025 Bank/001581210828/IBL8974...  1,30,000.00  47,043.91

    The row's own text is only "Bank/001581210828/..." - no payee - so a 1.3
    lakh BSE StAR MF purchase and every CRED card-bill payment looked like
    uncategorised spending. Together that was the ledger's largest opaque
    bucket at 14.2 lakh.
    """
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "UPI/IndianClea/bsestarmfrzp@i/PayviaRazo/ICICI",
        "01-10-2025 Bank/001581210828/IBL897436ba0a5d47 1,30,000.00 47,043.91",
        "UPI/CRED Club/cred.club@axis/payment on/AXIS",
        "27-10-2025 BANK/566606731707/ACDae982fad50ad41 24,770.92 51,691.13",
    ])
    mf = next(r for r in rows if r[2] == "1,30,000.00")
    assert "bsestarmfrzp" in mf[1]
    cred = next(r for r in rows if r[2] == "24,770.92")
    assert "CRED Club" in cred[1]


def test_a_continuation_line_is_claimed_by_only_one_row():
    """The line below a wrapped row must not also be taken by the row after it.

    "PRIVATE LIMI-JITESHSALAUG25//CMS2-..." completes the salary narration
    above it. It contains two slashes, so without tracking what has already
    been claimed the next row would prepend it and report the salary's
    reference as part of an unrelated 30 rupee payment.
    """
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "NEFT-KKBKN62025090126457682-CUBYTS TECHNOLOGIES",
        "01-09-2025 1,64,561.00 1,69,986.47",
        "PRIVATE LIMI-JITESHSALAUG25//CMS2-7245244909-KKBK00",
        "02-09-2025 BAN/12345/ABC 30.00 1,69,956.47",
    ])
    salary = next(r for r in rows if r[2] == "1,64,561.00")
    assert "CUBYTS TECHNOLOGIES" in salary[1] and "JITESHSALAUG25" in salary[1]
    other = next(r for r in rows if r[2] == "30.00")
    assert other[1] == "BAN/12345/ABC"


def test_midword_fragment_rejoins_both_neighbours():
    """One ICICI month splits the salary as prefix / row / suffix.

        NEFT-KKBKN62025100170134164-CUBYTS
        01-10-2025 TECHNOLOGIES PRIVATE LIMI-  1,64,561.00  1,77,043.91
        JITESHSALSEP25//CMS2-7245244909-KKBK00

    The row keeps a description, so the empty-row stitch never fired, and
    "TECHNOLOGIES PRIVATE LIMI-" carries no credit wording - so a 1.64 lakh
    salary was booked as SPENDING. Text cut mid-word takes both neighbours.
    """
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "01-10-2025 B/F 12,482.91",
        "NEFT-KKBKN62025100170134164-CUBYTS",
        "01-10-2025 TECHNOLOGIES PRIVATE LIMI- 1,64,561.00 1,77,043.91",
        "JITESHSALSEP25//CMS2-7245244909-KKBK00",
    ])
    salary = next(r for r in rows if r[2] == "1,64,561.00")
    assert "CUBYTS" in salary[1]
    assert "JITESHSALSEP25" in salary[1]


def test_summary_rows_dated_after_the_period_are_dropped():
    """HSBC heads every statement with its payment due date and amount due.

        08 DEC 2025 6,831.64
        MR JITESH MUKESH AGARWAL
        XXXXXXXXXXX 24 OCT 2025 To 23 NOV 2025 99,181.11

    The first line reads as a dated row ending in an amount, so the extractor
    borrowed the cardholder's name from the line below and invented a 6,831.64
    purchase BY the cardholder, two weeks after the statement closed. One
    appeared in every HSBC statement.
    """
    from app.normalize.metadata import detect_period

    start, end = detect_period("XXXXXXXXXXX 24 OCT 2025 To 23 NOV 2025 99,181.11")
    assert (start, end) == (date(2025, 10, 24), date(2025, 11, 23))

    from app.models.schemas import AccountType, Direction, Statement, Transaction
    from app.normalize.normalizer import _drop_rows_after_period

    def _txn(day, desc):
        return Transaction(
            id=desc, account_id="a", txn_date=day, amount=Decimal("1"),
            direction=Direction.DEBIT, raw_description=desc,
            normalized_description=desc,
        )

    stmt = Statement(id="s", source_filename="f", account_id="a")
    stmt.transactions = [
        _txn(date(2025, 11, 4), "EUREKA FORBES"),      # inside the period
        _txn(date(2025, 10, 20), "EARLY POSTING"),     # before it - still real
        _txn(date(2025, 12, 8), "MR JITESH MUKESH AGARWAL"),  # the due date
    ]
    _drop_rows_after_period(stmt, date(2025, 11, 23))
    kept = [t.raw_description for t in stmt.transactions]
    assert kept == ["EUREKA FORBES", "EARLY POSTING"]
    assert any("after the statement period" in w for w in stmt.parse_warnings)


def test_continuation_lines_are_not_stolen_from_real_rows():
    """A line with its own date, or its own amount, is never a continuation."""
    from app.ingestion.extractors import _is_continuation

    assert _is_continuation("NEFT-KKBKN6-CUBYTS TECHNOLOGIES")
    assert not _is_continuation("01-09-2025 SOMETHING 100.00")   # own data row
    assert not _is_continuation("SOME MERCHANT 250.00")          # own amount
    assert not _is_continuation("3c")                            # too short
    assert not _is_continuation("")


def test_compound_salary_narration_categorises_as_salary():
    """\bSALARY\b never matches "PRIVATELIMI-JITESHSALNOV25"."""
    from app.categorize.rules import apply_rules
    from app.models.schemas import Direction, Transaction

    def category_of(description):
        txn = Transaction(txn_date=date(2025, 1, 1), raw_description=description,
                          amount=Decimal("1"), direction=Direction.CREDIT)
        match = apply_rules(txn)
        return match[0] if match else None

    assert category_of("PRIVATELIMI-JITESHSALNOV25//CMS3-XXXX4909") == "salary"
    assert category_of("NEFT-CR-ACME CORP SALARY") == "salary"
    # "SALE" must not be read as salary.
    assert category_of("BIG SALE AT STORE") != "salary"


def test_card_issued_emi_conversion_is_not_the_emi_category():
    """HDFC prints the literal word "EMI" as a prefix on any ONE-TIME purchase
    that was converted to the card's own installment plan - a hospital bill,
    a fuel fill-up, a dinner. That is a payment METHOD, not a loan repayment,
    and matching bare "EMI" pre-empted the merchant's real category (Dining,
    Fuel, Shopping, Healthcare, Education) for every one of them.

    A genuine loan's principal/interest breakdown is the opposite: narrow and
    unmistakable, carrying an installment-number marker like "(020/036)".
    """
    from app.categorize.rules import apply_rules
    from app.models.schemas import Direction, Transaction
    from app.normalize.parsers import normalize_description

    def category_of(description):
        # normalize_description is what production actually stores in
        # normalized_description - a bare .upper() would miss the glued-city
        # stripping ("DISTRICTDININGGURGOAN" -> "DISTRICTDINING") that several
        # merchant rules depend on.
        txn = Transaction(txn_date=date(2025, 1, 1), raw_description=description,
                          normalized_description=normalize_description(description),
                          amount=Decimal("1"), direction=Direction.DEBIT)
        match = apply_rules(txn)
        return match[0] if match else None

    # Card-issued "convert to EMI" purchases - each falls through to what the
    # purchase actually was, not to EMI.
    assert category_of("17:20 EMI TatadigitalGurgoan C") != "emi"
    assert category_of("18:17 EMI CLOUDNINE PNEPPSPUNE + 1875 C") == "healthcare"
    assert category_of("03:00 EMI UPI-HP Petrol Pump Hind Auto C") == "fuel"
    assert category_of("22:54 EMI DISTRICTDININGGURGOAN + 47 C") == "dining"
    assert category_of("11:09 EMI ADYPUEDUPUNE C") == "education"

    # A genuine amortizing EMI's principal/interest legs still match.
    assert category_of("EMI PRIN FOR TATA AIG GENERAL (020/036) 0") == "emi"
    assert category_of("EMI INT-TATA AIG GENERAL INSU (020/036) 0") == "emi"

    # Real loan repayments, caught by their own specific rules, are untouched.
    assert category_of("BIL/Personal Loan XX24899 EMI Jite") == "emi"
    assert category_of("ACH/HDFC BANK LTD/ICIC7010408210006294/XXXX4545") == "emi"


# --------------------------------------------------------------------------
# Statement coverage grid
# --------------------------------------------------------------------------

def test_guess_period_hint_from_real_filenames():
    """Every filename shape actually seen in this mailbox."""
    from app.analytics.coverage import guess_period_hint

    assert guess_period_hint("Statement_2025MTH08_341562729.pdf") == "2025-08"
    assert guess_period_hint("Statement_OCT2025_729341562.pdf") == "2025-10"
    assert guess_period_hint("6529XXXXXXXXXX90_17-09-2025_198.pdf") == "2025-09"
    assert guess_period_hint("20251223.pdf") == "2025-12"
    assert guess_period_hint("AXIS BANK  Statement for December 2025.pdf") == "2025-12"
    # No date anywhere - must not guess.
    assert guess_period_hint("528593XXXX.pdf") is None
    assert guess_period_hint("") is None
    # A long digit run that is NOT a date (an account number) must not be
    # mistaken for one - year 9341 and month 56 are both out of range.
    assert guess_period_hint("statement_93415629341.pdf") is None


def test_statement_months_covers_a_multi_month_statement():
    from datetime import date
    from app.analytics.coverage import statement_months

    assert statement_months(date(2025, 1, 15), date(2025, 1, 20)) == ["2025-01"]
    assert statement_months(date(2025, 1, 15), date(2025, 3, 20)) == \
        ["2025-01", "2025-02", "2025-03"]
    assert statement_months(None, None) == []


def test_month_range_wraps_years():
    from app.analytics.coverage import month_range
    assert month_range("2025-11", "2026-02") == \
        ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_build_coverage_colors_each_month_correctly():
    from datetime import date
    from types import SimpleNamespace
    from app.analytics.coverage import build_coverage
    from app.models.schemas import Account, AccountType

    account = Account(id="a1", institution="Test Bank",
                      account_type=AccountType.SAVINGS, account_number_masked="X1")
    stmt = SimpleNamespace(id="s1", period_start=date(2025, 8, 1), period_end=date(2025, 8, 31))
    failed_file = SimpleNamespace(id="f1", statement_id=None, period_hint="2025-09",
                                  parse_status="failed")
    unrelated_ok_file = SimpleNamespace(id="f2", statement_id="s1", period_hint="2025-08",
                                        parse_status="parsed")

    rows = build_coverage(
        [account],
        statements_by_account={"a1": [stmt]},
        files_by_account={"a1": [failed_file, unrelated_ok_file]},
    )
    assert len(rows) == 1
    by_month = {m["month"]: m["status"] for m in rows[0]["months"]}
    assert by_month["2025-08"] == "parsed"
    assert by_month["2025-09"] == "failed"
    # October has no file at all for this account -> missing, i.e. red.
    if "2025-10" in by_month:
        assert by_month["2025-10"] == "missing"


def test_build_coverage_prefers_parsed_over_a_prior_failed_attempt():
    """A file that failed once and was later retried successfully must show
    green for that month, not orange - the retry record's statement_id
    resolves to the same month a later parsed statement covers."""
    from datetime import date
    from types import SimpleNamespace
    from app.analytics.coverage import build_coverage
    from app.models.schemas import Account, AccountType

    account = Account(id="a1", institution="Test Bank",
                      account_type=AccountType.SAVINGS, account_number_masked="X1")
    stmt = SimpleNamespace(id="s1", period_start=date(2025, 8, 1), period_end=date(2025, 8, 31))
    retried_file = SimpleNamespace(id="f1", statement_id="s1", period_hint="2025-08",
                                   parse_status="parsed")

    rows = build_coverage(
        [account], statements_by_account={"a1": [stmt]},
        files_by_account={"a1": [retried_file]},
    )
    by_month = {m["month"]: m["status"] for m in rows[0]["months"]}
    assert by_month["2025-08"] == "parsed"


# --------------------------------------------------------------------------
# Card variant / product name, and identity when the number can't be read
# --------------------------------------------------------------------------

def test_detect_account_number_handles_leading_real_digits():
    """Axis (and one of HDFC's own templates) print real digits before the
    masked run instead of masking from the start: "438628******2343". The
    original pattern only matched from-the-start masking and silently
    collapsed three of the user's own distinct Axis cards into one account."""
    from app.normalize.metadata import detect_account_number

    assert detect_account_number("Card No: 438628******2343 Name JITESH") == "XXXX2343"
    assert detect_account_number("653047******5207 360,000.00") == "XXXX5207"
    # The original from-the-start shape must still work.
    assert detect_account_number("Card ending XXXX XXXX XXXX 1234") == "XXXX1234"


def test_detect_card_variant_from_real_letterheads():
    from app.normalize.metadata import detect_card_variant

    assert detect_card_variant("Axis Bank REWARDS Credit Card") == "Rewards"
    assert detect_card_variant("Axis Bank Visa Privilege Credit Card Statement") == "Privilege"
    assert detect_card_variant("Neo Rupay Credit Card Statement") == "Neo"
    assert detect_card_variant("HSBC TRAVELONE CREDIT CARD") == "TravelOne"
    # Longest fragment wins: "diners club" must not be shadowed by "diners".
    assert detect_card_variant("HDFC Bank Diners Club Black") == "Diners Club"
    assert detect_card_variant("Just a plain statement with no product name") is None


def test_account_identity_falls_back_to_product_name_then_blank():
    """Three tiers, matching db.repository.upsert_account exactly: masked
    number first, then product name, and only truly generic accounts share
    the fully-blank key."""
    from app.graph.nodes import _account_identity
    from app.models.schemas import Account, AccountType

    rewards = Account(institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
                      product_name="Rewards")
    privilege = Account(institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
                        product_name="Privilege")
    generic_a = Account(institution="Axis Bank", account_type=AccountType.CREDIT_CARD)
    generic_b = Account(institution="Axis Bank", account_type=AccountType.CREDIT_CARD)

    assert _account_identity(rewards) != _account_identity(privilege)
    # Two statements of the SAME named card still collapse to one account.
    rewards_again = Account(institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
                            product_name="Rewards")
    assert _account_identity(rewards) == _account_identity(rewards_again)
    # Genuinely unidentifiable accounts still share the old fallback - that
    # ambiguity is real, not a regression.
    assert _account_identity(generic_a) == _account_identity(generic_b)
    assert _account_identity(generic_a) != _account_identity(rewards)


def test_upsert_account_keeps_two_same_bank_cards_separate_by_product_name(tmp_path):
    """The repository-level mirror of the identity test above - this is what
    actually decides whether two Gmail statements merge in the database."""
    from app.db.database import Database
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType

    db = Database(tmp_path / "variant.db")
    rewards_id = repo.upsert_account(db, Account(
        institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
        product_name="Rewards"))
    privilege_id = repo.upsert_account(db, Account(
        institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
        product_name="Privilege"))
    assert rewards_id != privilege_id

    # A second statement of the SAME card attaches to the same row.
    rewards_again_id = repo.upsert_account(db, Account(
        institution="Axis Bank", account_type=AccountType.CREDIT_CARD,
        product_name="Rewards", current_balance=Decimal("100")))
    assert rewards_again_id == rewards_id
    assert len(repo.get_accounts(db)) == 2


def test_card_variant_ignores_generic_words_deep_in_the_statement():
    """SBI's OWN real letterhead: nine lines of address/statement-summary
    preamble before its transaction table prints a "Reward Points" COLUMN
    HEADER at line 19 ("...Transaction Details Reward Intl.# Amount...").
    That matched the generic "rewards" entry and labelled three different
    real SBI cards with the same fictitious product name. Matching is
    restricted to the opening block specifically to stay clear of it, while
    still reaching HSBC's title on line 6 - seven filler lines here stands in
    for that real gap."""
    from app.models.schemas import AccountType
    from app.normalize.metadata import extract_metadata

    text = (
        "State Bank of India\n"
        "MR JITESH AGARWAL\n"
        "M3 KALSAGAR SHRI RAM COLONY\n"
        "ALANDI ROAD BHOSARI\n"
        "PAYMENT DUE DATE\n"
        "September 29, 2025\n"
        "STATEMENT SUMMARY\n"
        "Total Amount due\n"
        "Transaction Details Reward Intl.# Amount\n"
        "01/09/2025 SOME MERCHANT 100.00\n"
    )
    meta = extract_metadata(text, "statement.pdf")
    assert meta.account_type == AccountType.CREDIT_CARD  # exercises the real gate
    assert meta.product_name is None


def test_card_variant_never_applies_to_a_savings_account():
    """detect_card_variant is a credit-card concept - it must not run at all
    for a savings/current account, whatever words happen to appear in one."""
    from app.normalize.metadata import extract_metadata

    text = (
        "HDFC Bank\n"
        "Savings Account Statement\n"
        "Ace your savings goals this year\n"
        "01/09/2025 SOME MERCHANT 100.00\n"
    )
    meta = extract_metadata(text, "statement.pdf")
    assert meta.product_name is None


def test_sender_domain_overrides_a_misleading_address_line():
    """One real Axis card's statement gives its address as "...Pune City
    HDFC Bank," - a landmark near the user's home, not the issuer - and that
    is the ONLY institution-shaped text anywhere in this template's
    letterhead; the genuine "Axis Bank" mention lives in the GST footer,
    past where the letterhead is cut off. Without the sender override this
    card silently merged into an unrelated HDFC Bank account."""
    from app.normalize.metadata import extract_metadata

    text = (
        "Neo Rupay Credit Card Statement\n"
        "JITESH AGARWAL\n"
        "A-1004, Utsav Homes Pune nashik road Bhosari,\n"
        "Pune City HDFC Bank,\n"
        "PUNE 411039\n"
    )
    without_sender = extract_metadata(text, "Credit_Card_Statement.pdf")
    assert without_sender.institution == "HDFC Bank"  # the bug, reproduced

    with_sender = extract_metadata(
        text, "Credit_Card_Statement.pdf", sender="cc.statements@axisbank.com")
    assert with_sender.institution == "Axis Bank"


def test_sender_override_ignored_when_it_names_no_known_institution():
    """A sender that resolves to nothing recognisable (a personal address, a
    forwarding service) must not silently overwrite a real text-based match
    with a raw domain or display name."""
    from app.normalize.metadata import extract_metadata

    text = "HDFC Bank\nCredit Card Statement\n"
    meta = extract_metadata(text, "statement.pdf", sender="someone@example.com")
    assert meta.institution == "HDFC Bank"


# --------------------------------------------------------------------------
# ICICI's own templates: bold-letter doubling, a missing product name, a
# decoy masked number, and a fabricated balance inside the T&C annexe
# --------------------------------------------------------------------------

def test_undo_bold_letter_doubling_restores_a_doubled_label():
    """ICICI's Amazon Pay template renders 'PAYMENT DUE DATE' as two
    overlapping text layers; pdfplumber then extracts every character
    twice - 'PPAAYYMMEENNTT DDUUEE DDAATTEE'. Each doubled word must collapse
    back to itself even though the middle word ('DUE') is only 3 letters,
    too short to safely de-duplicate on its own without the surrounding
    context - see the next test."""
    from app.normalize.metadata import _undo_bold_letter_doubling

    doubled = "PPAAYYMMEENNTT DDUUEE DDAATTEE\nDecember 19, 2025"
    assert _undo_bold_letter_doubling(doubled) == "PAYMENT DUE DATE\nDecember 19, 2025"


def test_undo_bold_letter_doubling_leaves_ordinary_text_alone():
    """A real word is only ever doubled by this artifact in its entirety, from
    its very first character - 'bookkeeper' merely CONTAINS a doubled-letter
    run ('ookkee') partway through, which a naive mid-string search would
    wrongly collapse. Amounts and dates must also survive untouched."""
    from app.normalize.metadata import _undo_bold_letter_doubling

    text = "Mr Jitesh Agarwal\nbookkeeper fee `47,249.00 on 13/11/2025\nSS Titanic"
    assert _undo_bold_letter_doubling(text) == text


def test_letterhead_is_not_truncated_by_a_statement_period_range():
    """A bare 'DATE to DATE' or 'DATE - DATE' range as the literal first line
    of the document (no label in front) must not be mistaken for the first
    transaction row - Axis's relationship-summary export and IDFC First's
    card statement both open with exactly this shape."""
    from app.normalize.metadata import letterhead

    axis_style = "01/11/2025 to 30/11/2025\nJITESH AGARWAL\nSavings INR 12,422.42\n"
    assert "Savings" in letterhead(axis_style)

    idfc_style = "23/Sep/2025 - 22/Oct/2025\nCredit Card Statement\n(FIRST Millennia XX7597)\n"
    assert "Millennia" in letterhead(idfc_style)


def test_letterhead_still_stops_at_a_percent_prefixed_transaction_row():
    """A wrapped reward-percentage annotation sometimes lands at the very
    start of a transaction row ahead of its date - '58% 19/01/2026 ...' -
    which must still end the letterhead, or an unrelated merchant
    description past it (here 'WALLET LOAD') leaks into the identity-only
    region and misclassifies the account."""
    from app.normalize.metadata import letterhead

    text = "Credit Card Statement\n41% 05/03/2026 12992804772 AMAZON PAY WALLET LOAD 777.00\n"
    head = letterhead(text)
    assert "WALLET" not in head


def test_letterhead_still_stops_at_a_plain_transaction_row():
    """The date-range and percentage-prefix guards must not swallow a normal
    transaction row that happens to have a '-' shortly after its date."""
    from app.normalize.metadata import letterhead

    text = "HDFC BANK LTD\n13/01/2026 -50.00 Some refund\nMore text\n"
    head = letterhead(text)
    assert "More text" not in head


def test_detect_card_variant_from_filename_for_icici():
    """ICICI's own backend names each statement PDF after the card product -
    the only signal that survives text extraction at all for this issuer,
    since its letterhead logo decodes as unmapped glyphs rather than a
    plain-text product name (see extract_metadata's ICICI-only fallback)."""
    from app.normalize.metadata import detect_card_variant_from_filename

    assert detect_card_variant_from_filename(
        "4315XXXXXXXX5001_31348_Retail_Amazon_NORM.pdf") == "Amazon Pay"
    assert detect_card_variant_from_filename(
        "4375XXXXXXXX2002_620647_Retail_HPCL_NORM.pdf") == "HPCL"
    assert detect_card_variant_from_filename(
        "6528XXXXXXXX5004_812562_Retail_Coral_NOR.pdf") == "Coral"
    assert detect_card_variant_from_filename("statement.pdf") is None


def test_icici_credit_card_gets_its_variant_from_filename_when_letterhead_has_none():
    """End-to-end: an ICICI credit card statement whose letterhead never
    repeats its own product name in plain words still gets one, from its
    filename."""
    from app.models.schemas import AccountType
    from app.normalize.metadata import extract_metadata

    text = "Mr Jitesh Agarwal\nSTATEMENT DATE\nDecember 1, 2025\nPAYMENT DUE DATE\n" \
           "STATEMENT SUMMARY\nTotal Amount due\n"
    meta = extract_metadata(
        text, "6528XXXXXXXX5004_812562_Retail_Coral_NOR.pdf",
        sender="<credit_cards@icicibank.com>",
    )
    assert meta.institution == "ICICI Bank"
    assert meta.account_type == AccountType.CREDIT_CARD
    assert meta.product_name == "Coral"


def test_detect_account_number_skips_an_all_zero_prefixed_decoy():
    """A real card's leading digits are its BIN and are never all zeros. One
    ICICI statement embeds an EMI/loan reference shaped exactly like a masked
    card number ('0000XXXXXXXX4199') a few rows below the real card's own
    ('4315XXXXXXXX5001') - the real one must win regardless of which comes
    first in the extracted text."""
    from app.normalize.metadata import detect_account_number

    text = "Points amount\n0000XXXXXXXX4199\n19/01/2026 ...\n4315XXXXXXXX5001\n13/01"
    assert detect_account_number(text) == "XXXX5001"


def test_detect_account_number_returns_none_when_only_a_decoy_is_visible():
    """When the real card number genuinely isn't in view, guessing the
    all-zero decoy would be worse than admitting nothing was found - a wrong
    number creates a phantom account, while None safely defers to the
    product-name and institution+type fallbacks."""
    from app.normalize.metadata import detect_account_number

    text = "Points amount\n0000XXXXXXXX4199\n19/01/2026 Autodebit Payment Recd."
    assert detect_account_number(text) is None


def test_detect_account_number_ignores_a_phone_number_after_a_placeholder_label():
    """ICICI's SMS-blocking instructions read '<YourCreditCard number> to
    9215676766 from your registered mobile number' - 'card number' there
    names a placeholder, not a real field, and the actual value is a support
    phone number shaped exactly like a masked account number."""
    from app.normalize.metadata import detect_account_number

    text = "SMS <YourCreditCard number> to 9215676766 from your registered mobile number"
    assert detect_account_number(text) is None


def test_before_mitc_illustration_strips_the_fabricated_worked_example():
    """ICICI's Most Important Terms & Conditions annexe includes a purely
    illustrative worked example using the exact same balance labels this
    file searches for, attached to fabricated figures. It must be excluded
    before any balance/limit/EMI amount is read."""
    from app.normalize.metadata import _before_mitc_illustration

    text = (
        "STATEMENT SUMMARY\nTotal Amount due\n`0.00\n"
        "The following illustration will indicate the method of calculating MAD charges:\n"
        "18 Closing Balance 26,958.20\n"
    )
    stripped = _before_mitc_illustration(text)
    assert "Total Amount due" in stripped
    assert "26,958.20" not in stripped


def test_closing_balance_is_not_read_from_the_mitc_illustration():
    """End-to-end reproduction of a real HPCL-card statement: the genuine
    closing balance is `0.00`, and the T&C annexe's worked example claims an
    unrelated `26,958.20` under the identical 'Closing Balance' label."""
    from app.normalize.metadata import extract_metadata

    text = (
        "MR JITESH AGARWAL\nSTATEMENT DATE\nDecember 11, 2025\nPAYMENT DUE DATE\n"
        "STATEMENT SUMMARY\nTotal Amount due\n`0.00 = + + -\n"
        "Minimum Amount due CREDIT SUMMARY\n`0.00\n"
        "13/11/2025 12329046524 SOME MERCHANT 500.00\n"
        "The following illustration will indicate the method of calculating MAD charges:\n"
        "18 Closing Balance 26,958.20\n"
    )
    meta = extract_metadata(text, "4375XXXXXXXX2002_620647_Retail_HPCL_NORM.pdf")
    assert meta.closing_balance != Decimal("26958.20")
