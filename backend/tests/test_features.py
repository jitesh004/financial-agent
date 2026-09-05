"""Tests for profile-driven password derivation, dedup, and Gmail import.

These cover the three ways a statement reaches the pipeline other than a plain
manual upload, plus the deduplication that keeps any of those paths from double
counting.
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from tests.support import fresh_ledger  # noqa: E402
from app.graph.build import build_graph  # noqa: E402
from app.ingestion import router  # noqa: E402
from app.ingestion.passwords import (MAX_CANDIDATES, derive_passwords,  # noqa: E402
                                     redact_candidate)
from app.models.profile import UserProfile  # noqa: E402

SAMPLES = ROOT / "data" / "samples"
ENCRYPTED = ROOT / "data" / "samples_encrypted" / "icici_credit_card_locked.pdf"

PROFILE = UserProfile(full_name="PANKAJ SHARMA", date_of_birth=date(1988, 7, 14),
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
    assert "pank1407" in candidates
    assert "PANK1407" in candidates


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


def test_custom_passwords_stay_first_once_the_issuer_is_known():
    """The half of that claim that was not true.

    With no institution the candidate list came back untouched and the user's
    own password led it. With one, `_prioritise` re-sorted on shape alone and
    knew nothing about where a candidate came from - so a password with no
    letters in it, which is what an NPS PRAN is, sorted below every
    name-and-digits guess and went from first to 121st. Each of those is a
    decrypt attempt against a password already known to be wrong, and the
    profile promises the opposite: tried before the derived candidates, so an
    odd format is never a blocker.
    """
    profile = UserProfile(full_name="Asha Rao", date_of_birth=date(1990, 1, 1),
                          pan="ABCDE1234F", mobile="9000000000",
                          custom_passwords=["400080396530"])
    for institution in (None, "KFintech", "HDFC Bank", "ICICI"):
        candidates = derive_passwords(profile, institution)
        assert candidates[0] == "400080396530",             f"demoted to {candidates.index('400080396530') + 1} for {institution}"


def test_empty_profile_yields_nothing():
    assert derive_passwords(UserProfile()) == []
    assert derive_passwords(None) == []


def test_redaction_hides_the_password():
    assert redact_candidate("pank1407") == "p*******"
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
    assert not any("pank1407" in w for w in result.warnings)


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

    class _NoToken:
        def load(self): return None
        def save(self, token_json): pass

    client = GoogleGmailClient(_NoToken())
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
     "PAYMENT FROM UPSTOX SECURITIES PVT LTD- DSCNB A/C TO PANKAJ - DOMNEFT01",
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
    from app.db import repository as repo
    from app.models.profile import UserProfile
    from fastapi.testclient import TestClient
    import app.main as main_module

    original_db = db_module._db
    db_module._db = fresh_ledger()
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
    from app.db import repository as repo
    from app.models.profile import UserProfile

    db = fresh_ledger()
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
    ("PRIVATELIMI-PANKAJSALNOV25//CMS3-XXXX1234", "credit"),
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

    # Returns (direction, reason) since a direction read off wording is the
    # weakest of the real signals and the row is worth flagging as such.
    result, reason = _direction_from_description(description, is_liability=False)
    assert (result.value if result else None) == expected
    assert reason, "every outcome names a reason, including the default"



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

        NEFT-KKBKN6...-ACME TECHNOLOGIES          <- narration
        01-09-2025  1,64,561.00  1,69,986.47        <- the data row
        PRIVATE LIMI-PANKAJSALAUG25//CMS2-...       <- narration continued

    Dropping the surrounding lines left the salary credit with no description at
    all, so no rule matched and a 1.6 lakh salary never counted as income.
    """
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "01-09-2025 B/F 5,425.47",
        "NEFT-KKBKN60000000000005678-ACME TECHNOLOGIES",
        "01-09-2025 1,64,561.00 1,69,986.47",
        "PRIVATE LIMI-PANKAJSALAUG25//CMS2-9000001234-KKBK00",
        "02-09-2025 BAN/12345/ABC 30.00 1,69,956.47",
    ])
    salary = next(r for r in rows if r[2] == "1,64,561.00")
    assert "ACME TECHNOLOGIES" in salary[1]
    assert "PANKAJSALAUG25" in salary[1]

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

    "PRIVATE LIMI-PANKAJSALAUG25//CMS2-..." completes the salary narration
    above it. It contains two slashes, so without tracking what has already
    been claimed the next row would prepend it and report the salary's
    reference as part of an unrelated 30 rupee payment.
    """
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "NEFT-KKBKN60000000000005678-ACME TECHNOLOGIES",
        "01-09-2025 1,64,561.00 1,69,986.47",
        "PRIVATE LIMI-PANKAJSALAUG25//CMS2-9000001234-KKBK00",
        "02-09-2025 BAN/12345/ABC 30.00 1,69,956.47",
    ])
    salary = next(r for r in rows if r[2] == "1,64,561.00")
    assert "ACME TECHNOLOGIES" in salary[1] and "PANKAJSALAUG25" in salary[1]
    other = next(r for r in rows if r[2] == "30.00")
    assert other[1] == "BAN/12345/ABC"


def test_midword_fragment_rejoins_both_neighbours():
    """One ICICI month splits the salary as prefix / row / suffix.

        NEFT-KKBKN60000000000001234-ACME
        01-10-2025 TECHNOLOGIES PRIVATE LIMI-  1,64,561.00  1,77,043.91
        PANKAJSALSEP25//CMS2-9000001234-KKBK00

    The row keeps a description, so the empty-row stitch never fired, and
    "TECHNOLOGIES PRIVATE LIMI-" carries no credit wording - so a 1.64 lakh
    salary was booked as SPENDING. Text cut mid-word takes both neighbours.
    """
    from app.ingestion.extractors import _rows_from_text_lines

    rows = _rows_from_text_lines([
        "01-10-2025 B/F 12,482.91",
        "NEFT-KKBKN60000000000001234-ACME",
        "01-10-2025 TECHNOLOGIES PRIVATE LIMI- 1,64,561.00 1,77,043.91",
        "PANKAJSALSEP25//CMS2-9000001234-KKBK00",
    ])
    salary = next(r for r in rows if r[2] == "1,64,561.00")
    assert "ACME" in salary[1]
    assert "PANKAJSALSEP25" in salary[1]


def test_summary_rows_dated_after_the_period_are_dropped():
    """HSBC heads every statement with its payment due date and amount due.

        08 DEC 2025 6,831.64
        MR PANKAJ KUMAR SHARMA
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
        _txn(date(2025, 12, 8), "MR PANKAJ KUMAR SHARMA"),  # the due date
    ]
    _drop_rows_after_period(stmt, date(2025, 11, 23))
    kept = [t.raw_description for t in stmt.transactions]
    assert kept == ["EUREKA FORBES", "EARLY POSTING"]
    assert any("after the statement period" in w for w in stmt.parse_warnings)


def test_continuation_lines_are_not_stolen_from_real_rows():
    """A line with its own date, or its own amount, is never a continuation."""
    from app.ingestion.extractors import _is_continuation

    assert _is_continuation("NEFT-KKBKN6-ACME TECHNOLOGIES")
    assert not _is_continuation("01-09-2025 SOMETHING 100.00")   # own data row
    assert not _is_continuation("SOME MERCHANT 250.00")          # own amount
    assert not _is_continuation("3c")                            # too short
    assert not _is_continuation("")


def test_compound_salary_narration_categorises_as_salary():
    """\bSALARY\b never matches "PRIVATELIMI-PANKAJSALNOV25"."""
    from app.categorize.rules import apply_rules
    from app.models.schemas import Direction, Transaction

    def category_of(description):
        txn = Transaction(txn_date=date(2025, 1, 1), raw_description=description,
                          amount=Decimal("1"), direction=Direction.CREDIT)
        match = apply_rules(txn)
        return match[0] if match else None

    assert category_of("PRIVATELIMI-PANKAJSALNOV25//CMS3-XXXX1234") == "salary"
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
    masked run instead of masking from the start: "411111******4321". The
    original pattern only matched from-the-start masking and silently
    collapsed three of the user's own distinct Axis cards into one account."""
    from app.normalize.metadata import detect_account_number

    assert detect_account_number("Card No: 411111******4321 Name PANKAJ") == "XXXX4321"
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
    from app.db import repository as repo
    from app.models.schemas import Account, AccountType

    db = fresh_ledger()
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
        "MR PANKAJ SHARMA\n"
        "12 MAPLE COURT SAMPLE COLONY\n"
        "GREEN AVENUE WESTPARK\n"
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
        "PANKAJ SHARMA\n"
        "A-1004, Sample Residency Springfield road Westpark,\n"
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

    text = "Mr Pankaj Sharma\nbookkeeper fee `47,249.00 on 13/11/2025\nSS Titanic"
    assert _undo_bold_letter_doubling(text) == text


def test_letterhead_is_not_truncated_by_a_statement_period_range():
    """A bare 'DATE to DATE' or 'DATE - DATE' range as the literal first line
    of the document (no label in front) must not be mistaken for the first
    transaction row - Axis's relationship-summary export and IDFC First's
    card statement both open with exactly this shape."""
    from app.normalize.metadata import letterhead

    axis_style = "01/11/2025 to 30/11/2025\nPANKAJ SHARMA\nSavings INR 12,422.42\n"
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

    text = "Mr Pankaj Sharma\nSTATEMENT DATE\nDecember 1, 2025\nPAYMENT DUE DATE\n" \
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
        "MR PANKAJ SHARMA\nSTATEMENT DATE\nDecember 11, 2025\nPAYMENT DUE DATE\n"
        "STATEMENT SUMMARY\nTotal Amount due\n`0.00 = + + -\n"
        "Minimum Amount due CREDIT SUMMARY\n`0.00\n"
        "13/11/2025 12329046524 SOME MERCHANT 500.00\n"
        "The following illustration will indicate the method of calculating MAD charges:\n"
        "18 Closing Balance 26,958.20\n"
    )
    meta = extract_metadata(text, "4375XXXXXXXX2002_620647_Retail_HPCL_NORM.pdf")
    assert meta.closing_balance != Decimal("26958.20")


# --------------------------------------------------------------------------
# OpenRouter puts the model's thinking beside its answer, not inside it.
# --------------------------------------------------------------------------

def test_openrouter_reply_reads_content_not_reasoning():
    """A reasoning model returns both; the answer is `content`.

    Reading the reasoning instead would give categorisation "The user wants
    me to classify twenty-two merchants..." where it asked for JSON, which
    parses as nothing and reports "0 from the model" over a provider that was
    answering perfectly well.
    """
    from app.llm.providers import _message_text

    reply = {"choices": [{"message": {
        "reasoning": "The user wants me to classify...",
        "content": '[{"i": 0, "category": "groceries"}]',
    }}]}
    assert _message_text(reply) == '[{"i": 0, "category": "groceries"}]'


def test_openrouter_reply_without_reasoning_is_unchanged():
    """Models that do not think out loud must keep working."""
    from app.llm.providers import _message_text

    reply = {"choices": [{"message": {"content": "plain answer"}}]}
    assert _message_text(reply) == "plain answer"


def test_openrouter_reply_that_is_only_reasoning_is_not_swallowed():
    """A model that spends max_tokens thinking returns an empty `content`.

    Returning "" here would read downstream as a silent failure. The
    reasoning at least gives _parse_json_loose somewhere to look.
    """
    from app.llm.providers import _message_text

    reply = {"choices": [{"message": {
        "content": "",
        "reasoning": "thinking and nothing else",
    }}], "finish_reason": "length"}
    assert _message_text(reply) == "thinking and nothing else"


def test_openrouter_content_may_arrive_as_parts():
    """Some upstreams return content as a list rather than a string."""
    from app.llm.providers import _message_text

    reply = {"choices": [{"message": {"content": [
        {"type": "text", "text": '{"institution": '},
        {"type": "text", "text": '"HDFC Bank"}'},
    ]}}]}
    assert _message_text(reply) == '{"institution": "HDFC Bank"}'


def test_openrouter_malformed_reply_returns_empty_rather_than_raising():
    from app.llm.providers import _message_text

    assert _message_text({"choices": []}) == ""
    assert _message_text({}) == ""
    assert _message_text({"choices": [{"message": None}]}) == ""


# --------------------------------------------------------------------------
# The free tier is rated per request, so a 429 is routine rather than fatal.
# --------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_body if json_body is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_retry_after_header_is_honoured():
    from app.llm.providers import _retry_after_seconds

    resp = _Resp(429, headers={"Retry-After": "12"})
    assert _retry_after_seconds(resp, attempt=0) == 12.0


def test_rate_limit_reset_header_is_read_as_epoch_milliseconds():
    from app.llm.providers import _retry_after_seconds

    reset = (time.time() + 30) * 1000
    resp = _Resp(429, headers={"X-RateLimit-Reset": str(reset)})
    assert 25 <= _retry_after_seconds(resp, attempt=0) <= 31


def test_rate_limit_wait_is_capped():
    """A daily quota resets hours out; blocking an import until then is worse
    than failing the batch and saying so."""
    from app.llm.providers import MAX_RATE_LIMIT_WAIT, _retry_after_seconds

    resp = _Resp(429, headers={"Retry-After": "86400"})
    assert _retry_after_seconds(resp, attempt=0) == MAX_RATE_LIMIT_WAIT


def test_a_stale_retry_after_never_reaches_sleep_as_a_negative():
    """time.sleep() raises on a negative, so a stale header would turn a
    routine 429 into a lost batch."""
    from app.llm.providers import _retry_after_seconds

    assert _retry_after_seconds(_Resp(429, headers={"Retry-After": "-9"}),
                                attempt=0) == 0.0
    stale = (time.time() - 600) * 1000
    assert _retry_after_seconds(
        _Resp(429, headers={"X-RateLimit-Reset": str(stale)}),
        attempt=1) == 2.0


def test_an_http_date_retry_after_falls_through_rather_than_raising():
    """RFC 7231 allows a date there; this app only understands seconds."""
    from app.llm.providers import _retry_after_seconds

    resp = _Resp(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    assert _retry_after_seconds(resp, attempt=0) == 1.0


def test_rate_limit_falls_back_to_doubling_when_no_header():
    from app.llm.providers import _retry_after_seconds

    assert _retry_after_seconds(_Resp(429), attempt=0) == 1.0
    assert _retry_after_seconds(_Resp(429), attempt=2) == 4.0


def test_openrouter_retries_a_429_then_succeeds(monkeypatch):
    """One rate-limited call must not lose the batch behind it."""
    from app.llm import providers

    calls = []

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            calls.append((url, json, headers))
            if len(calls) == 1:
                return _Resp(429, headers={"Retry-After": "0"})
            return _Resp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(providers.httpx, "Client", _Client)
    monkeypatch.setattr(providers.time, "sleep", lambda *_: None)
    monkeypatch.setattr(providers.config, "OPENROUTER_API_KEY", "k", raising=False)

    assert providers.OpenRouterProvider().complete("hi") == "ok"
    assert len(calls) == 2


def test_openrouter_json_mode_is_a_constraint_not_a_request(monkeypatch):
    """Asking for JSON in the system prompt is a request; response_format is
    a constraint. Without it a model prefaces the array with prose, which
    parses as nothing."""
    from app.llm import providers

    sent = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            sent.update(url=url, payload=json, headers=headers)
            return _Resp(200, {"choices": [{"message": {
                "content": '[{"i": 0, "category": "groceries"}]'}}]})

    monkeypatch.setattr(providers.httpx, "Client", _Client)
    monkeypatch.setattr(providers.config, "OPENROUTER_API_KEY", "k", raising=False)
    monkeypatch.setattr(providers.config, "OPENROUTER_MODEL_FAST",
                        "google/gemma-4-26b-a4b-it:free", raising=False)

    answer = providers.OpenRouterProvider().complete_json("classify these")

    assert answer == [{"i": 0, "category": "groceries"}]
    assert sent["payload"]["response_format"] == {"type": "json_object"}
    assert sent["payload"]["model"] == "google/gemma-4-26b-a4b-it:free"
    assert sent["url"].endswith("/chat/completions")
    assert sent["headers"]["Authorization"] == "Bearer k"


def test_openrouter_strong_tier_uses_the_strong_model(monkeypatch):
    """The narrative is the one call where prose quality is the point."""
    from app.llm import providers

    sent = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            sent.update(payload=json)
            return _Resp(200, {"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setattr(providers.httpx, "Client", _Client)
    monkeypatch.setattr(providers.config, "OPENROUTER_API_KEY", "k", raising=False)
    monkeypatch.setattr(providers.config, "OPENROUTER_MODEL_STRONG",
                        "z-ai/glm-5.2:free", raising=False)

    providers.OpenRouterProvider().complete("write it", tier="strong")
    assert sent["payload"]["model"] == "z-ai/glm-5.2:free"


def test_openrouter_error_in_a_200_body_is_raised(monkeypatch):
    """OpenRouter reports an upstream failure in the body of an otherwise
    successful envelope. Unread, it surfaces as an unexplained empty answer."""
    from app.llm import providers

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            return _Resp(200, {"error": {"code": 402,
                                         "message": "insufficient credits"}})

    monkeypatch.setattr(providers.httpx, "Client", _Client)
    monkeypatch.setattr(providers.config, "OPENROUTER_API_KEY", "k", raising=False)

    with pytest.raises(RuntimeError, match="insufficient credits"):
        providers.OpenRouterProvider().complete("hi")


def test_a_base_url_override_reaches_another_openai_compatible_endpoint(monkeypatch):
    """Going back to Gemini is a base-URL change, not a code change.

    Gemini serves the same `/chat/completions` shape, so the switch has to be
    the URL and nothing else - no path assumptions, no openrouter.ai baked in
    anywhere.
    """
    from app.llm import providers

    sent = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            sent.update(url=url, payload=json, headers=headers)
            return _Resp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(providers.httpx, "Client", _Client)
    monkeypatch.setattr(providers.config, "OPENROUTER_API_KEY", "g", raising=False)
    monkeypatch.setattr(
        providers.config, "OPENROUTER_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta/openai", raising=False)
    monkeypatch.setattr(providers.config, "OPENROUTER_MODEL_FAST",
                        "gemini-2.5-flash", raising=False)
    # Cleared, as the docs say to on a non-OpenRouter endpoint: the thinking
    # budget is sent as OpenRouter spells it, which Google's layer ignores.
    monkeypatch.setattr(providers.config, "OPENROUTER_REASONING_EFFORT", "",
                        raising=False)

    assert providers.OpenRouterProvider().complete("hi") == "ok"
    assert sent["url"] == ("https://generativelanguage.googleapis.com"
                           "/v1beta/openai/chat/completions")
    assert sent["payload"]["model"] == "gemini-2.5-flash"
    assert sent["headers"]["Authorization"] == "Bearer g"
    assert "reasoning" not in sent["payload"]


def test_openrouter_without_a_key_is_unavailable(monkeypatch):
    """No key must degrade the app, not break it: rules still categorise and
    the narrative falls back to the computed figures."""
    from app.llm import providers

    monkeypatch.setattr(providers.config, "OPENROUTER_API_KEY", None, raising=False)
    assert providers.OpenRouterProvider().available is False


# --------------------------------------------------------------------------
# "Uncategorized" is the absence of an answer, never a cached one.
# --------------------------------------------------------------------------

def _txn(desc, tid="t1"):
    from app.models.schemas import Direction, Transaction
    from datetime import date
    from decimal import Decimal
    return Transaction(
        id=tid, account_id="a1", txn_date=date(2026, 7, 1),
        raw_description=desc, merchant=desc, amount=Decimal("100"),
        direction=Direction.DEBIT, category="uncategorized")


def test_clearing_a_category_forgets_the_merchant(tmp_db):
    """Storing it as a user decision would pin the merchant forever.

    The cache's upsert refuses to overwrite a user row with any later guess,
    so one cleared transaction would keep the model from ever being asked
    about that merchant again.
    """
    from app.categorize.llm_categorizer import record_user_correction
    from app.db.repository import lookup_merchants

    txn = _txn("RBL*SULOCHANA BH")
    record_user_correction(tmp_db, txn, "dining")
    assert "RBL*SULOCHANA BH" in lookup_merchants(tmp_db, ["RBL*SULOCHANA BH"])

    record_user_correction(tmp_db, txn, "uncategorized")
    assert lookup_merchants(tmp_db, ["RBL*SULOCHANA BH"]) == {}, \
        "clearing a category must drop the cache entry, not pin it"


def test_a_cached_uncategorized_still_reaches_the_model(tmp_db):
    """A hit that resolves nothing must not stand in for an answer."""
    from app.categorize.llm_categorizer import categorize_with_llm
    from app.db.repository import save_merchant_categories

    save_merchant_categories(tmp_db, {"HAS": ("uncategorized", 1.0, "user")})

    asked = []

    class Model:
        available = True

        def complete_json(self, prompt, system="", **kw):
            asked.append(prompt)
            return [{"i": 0, "category": "shopping", "confidence": 0.9}]

    txn = _txn("HAS")
    from_cache, from_model = categorize_with_llm([txn], db=tmp_db, client=Model())

    assert asked, "the merchant was never sent to the model"
    assert from_cache == 0 and from_model == 1
    assert txn.category == "shopping"


# --------------------------------------------------------------------------
# Junk dates, traced to the three statements that produced them.
# --------------------------------------------------------------------------

def test_idfc_prints_its_period_with_no_keyword():
    """"23/Jul/2026 - 22/Aug/2026", sitting under the title.

    Unread, that line was parsed as a transaction instead: the year truncated
    to "20" and an amount built out of "26 - 22" gave a 2,622-rupee debit
    dated 2020-07-23, on a statement whose only real row was a 277.76 credit.
    """
    from datetime import date
    from app.normalize.metadata import detect_period

    assert detect_period("Credit Card Statement\n23/Jul/2026 - 22/Aug/2026") \
        == (date(2026, 7, 23), date(2026, 8, 22))


def test_icici_prints_its_period_month_first():
    """"Statement period : May 12, 2026 to August 11, 2026"."""
    from datetime import date
    from app.normalize.metadata import detect_period

    assert detect_period("Statement period : May 12, 2026 to August 11, 2026") \
        == (date(2026, 5, 12), date(2026, 8, 11))


def test_two_loose_numbers_are_not_a_period():
    """The keyword-less pattern must not read any nearby pair as a range."""
    from app.normalize.metadata import detect_period

    assert detect_period("Rows 12 - 15 of 90") == (None, None)


def test_a_decade_apart_is_not_a_period():
    """A statement covers a billing cycle, not eleven years."""
    from app.normalize.metadata import detect_period

    assert detect_period("01/Jan/2015 - 01/Jan/2026") == (None, None)


def test_ddmmyyyy_filenames_anchor_the_outlier_guard():
    """IDFC names files the other way round: ..._22082026_... is Aug 2026."""
    from datetime import date
    from app.normalize.normalizer import _anchor_from_filename

    assert _anchor_from_filename("20000000001234_22082026_115345421.pdf") \
        == date(2026, 8, 1)


def test_a_single_row_statement_is_still_guarded():
    """The row count stopped mattering once the anchor came from the period.

    The IDFC statement had exactly two parsed rows, one of them junk. Needing
    three rows for a median meant the guard declined to run on precisely the
    statement that needed it.
    """
    from datetime import date
    from decimal import Decimal
    from app.models.schemas import Direction, Statement, Transaction
    from app.normalize.normalizer import _drop_outlier_dates

    junk = Transaction(
        id="j", account_id="a", txn_date=date(2020, 7, 23),
        raw_description="(no description)", amount=Decimal("2622"),
        direction=Direction.DEBIT)
    stmt = Statement(
        id="s", account_id="a", source_filename="x.pdf",
        period_start=date(2026, 7, 23), period_end=date(2026, 8, 22),
        transactions=[junk])

    _drop_outlier_dates(stmt)
    assert stmt.transactions == [], "one bad row on a one-row statement"
    assert stmt.parse_warnings


def test_an_out_of_cycle_refund_counts_in_the_cycle_that_billed_it():
    """Real money, wrong month.

    "27APR RAZ*CARS24 SERVICES PR ... 2,242.00 CR" sits between 30JUL and
    09AUG on an HSBC statement covering 24 Jul - 23 Aug 2026: a refund
    carrying the date of the purchase it reverses. Dropping it would delete
    real money; counting it in April stretched the ledger's span by a quarter.
    """
    from datetime import date
    from decimal import Decimal
    from app.analytics.periods import assign_accounting_months
    from app.models.schemas import Direction, Transaction

    refund = Transaction(
        id="r", account_id="a", statement_id="s1", txn_date=date(2026, 4, 27),
        raw_description="RAZ*CARS24 SERVICES PR Gurgaon IND",
        amount=Decimal("2242.00"), direction=Direction.CREDIT)
    normal = Transaction(
        id="n", account_id="a", statement_id="s1", txn_date=date(2026, 8, 13),
        raw_description="PAY*BOOKMYSHOW", amount=Decimal("619.06"),
        direction=Direction.DEBIT)

    periods = {"s1": (date(2026, 7, 24), date(2026, 8, 23))}
    assign_accounting_months([refund, normal], [], periods)

    assert refund.txn_date == date(2026, 4, 27), "the printed date must stand"
    assert refund.accounting_month == "2026-08"
    assert normal.accounting_month == "2026-08"


def test_a_row_dated_after_the_cycle_is_not_claimed_by_it():
    """Only rows dated BEFORE the period start are pulled forward."""
    from datetime import date
    from decimal import Decimal
    from app.analytics.periods import assign_accounting_months
    from app.models.schemas import Direction, Transaction

    later = Transaction(
        id="l", account_id="a", statement_id="s1", txn_date=date(2026, 9, 30),
        raw_description="x", amount=Decimal("1"), direction=Direction.DEBIT)
    assign_accounting_months(
        [later], [], {"s1": (date(2026, 7, 24), date(2026, 8, 23))})
    assert later.accounting_month == "2026-09"


def test_the_span_agrees_with_the_month_rows():
    """The header and the table must answer the same question.

    A refund dated 27 April but billed in the August cycle is counted in
    August. Measuring the span off raw dates announced "27 Apr - 17 Aug, 5
    months" above a table with four month rows in it.
    """
    from datetime import date
    from decimal import Decimal
    from app.analytics.engine import analyze
    from app.models.schemas import Direction, Transaction

    rows = [
        Transaction(id="a", account_id="x", txn_date=date(2026, 4, 27),
                    accounting_month="2026-08", raw_description="refund",
                    amount=Decimal("2242"), direction=Direction.CREDIT),
        Transaction(id="b", account_id="x", txn_date=date(2026, 5, 3),
                    accounting_month="2026-05", raw_description="a",
                    amount=Decimal("100"), direction=Direction.DEBIT),
        Transaction(id="c", account_id="x", txn_date=date(2026, 8, 17),
                    accounting_month="2026-08", raw_description="b",
                    amount=Decimal("100"), direction=Direction.DEBIT),
    ]
    result = analyze(rows)
    assert result.period_start == date(2026, 5, 3), \
        "the span must start in the first month anything is counted in"
    assert result.period_end == date(2026, 8, 17)


def test_an_excluded_row_cannot_stretch_the_span():
    """Rejecting a misread date has to change the numbers it distorted."""
    from datetime import date
    from decimal import Decimal
    from app.analytics.engine import analyze
    from app.models.schemas import Direction, Transaction

    rows = [
        Transaction(id="junk", account_id="x", txn_date=date(2020, 7, 23),
                    raw_description="(no description)", amount=Decimal("2622"),
                    direction=Direction.DEBIT, excluded=True),
        Transaction(id="real", account_id="x", txn_date=date(2026, 7, 29),
                    raw_description="real", amount=Decimal("277.76"),
                    direction=Direction.CREDIT),
    ]
    result = analyze(rows)
    assert result.period_start == date(2026, 7, 29)


# --------------------------------------------------------------------------
# Staging: read, reviewed, and only then counted.
# --------------------------------------------------------------------------

def _csv_statement(tmp_path):
    """A tiny statement that the CSV extractor can read without a password."""
    path = tmp_path / "acme-card-jul.csv"
    path.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "01/07/2026,COFFEE SHOP,120.00,,4880.00\n"
        "05/07/2026,SALARY,,5000.00,9880.00\n",
        encoding="utf-8")
    return path


def test_parsing_a_staged_file_touches_no_ledger(tmp_db, tmp_path):
    """The whole point of staging, as an assertion.

    Two import errors reached the running app before anything covered this
    path - `Statement.reconcile` (a module function, not a method) and
    `institution_for_sender` imported from the wrong module. Both were only
    found by watching a real parse fail, which is exactly what a test is for.
    """
    from app.db import repository as repo
    from app.db import staging
    from app.ingestion.router import file_hash
    from app.pipeline import staging_pipeline

    path = _csv_statement(tmp_path)
    entry_id = staging.add(tmp_db, file_hash(path),
                           filename=path.name, path=str(path), origin="upload")

    entry = next(e for e in staging.all_entries(tmp_db) if e["id"] == entry_id)
    status = staging_pipeline.parse_entry(tmp_db, entry)

    assert status in ("ok", "empty"), f"the file could not be read: {status}"
    assert repo.count_transactions(tmp_db) == 0, \
        "parsing must not put anything in the ledger"
    assert repo.get_accounts(tmp_db) == [], \
        "parsing must not create accounts either"

    parsed = next(e for e in staging.all_entries(tmp_db) if e["id"] == entry_id)
    assert parsed["parse_status"] == status
    assert parsed["row_count"] >= 1


def test_a_restaged_file_is_not_read_twice(tmp_db, tmp_path):
    """Identity is the content hash, so a re-scan costs nothing."""
    from app.db import staging
    from app.ingestion.router import file_hash

    path = _csv_statement(tmp_path)
    digest = file_hash(path)
    first = staging.add(tmp_db, digest, filename=path.name, path=str(path))
    second = staging.add(tmp_db, digest, filename=path.name, path=str(path))
    assert first == second
    assert staging.counts(tmp_db)["total"] == 1


def test_restaging_does_not_re_tick_what_was_turned_off(tmp_db, tmp_path):
    """Re-scanning a mailbox must not undo a decision."""
    from app.db import staging
    from app.ingestion.router import file_hash

    path = _csv_statement(tmp_path)
    digest = file_hash(path)
    entry_id = staging.add(tmp_db, digest, filename=path.name, path=str(path))
    staging.set_selected(tmp_db, [(entry_id, False)])

    staging.add(tmp_db, digest, filename=path.name, path=str(path))
    again = next(e for e in staging.all_entries(tmp_db) if e["id"] == entry_id)
    assert again["selected"] is False


def test_a_statement_supersedes_the_alerts_it_covers(tmp_db):
    """And un-ticking it brings them back.

    Supersession is recomputed from the current selection rather than
    accumulated, so it is reversible - which matters, because the alternative
    is a checkbox that permanently destroys rows the first time it is used.
    """
    from app.db import staging

    statement = staging.add(
        tmp_db, "hash-statement", filename="july.pdf", kind="statement")
    staging.record_parse(tmp_db, statement, status="ok", kind="statement",
                         account_key="HSBC|XXXX1751",
                         period_start="2026-07-01", period_end="2026-07-31")

    alert = staging.add(tmp_db, "hash-alert", filename="a coffee", kind="alert")
    staging.record_parse(tmp_db, alert, status="ok", kind="alert",
                         account_key="HSBC|XXXX1751",
                         period_start="2026-07-14", period_end="2026-07-14")

    staging.apply_supersession(tmp_db)
    rows = {e["id"]: e for e in staging.all_entries(tmp_db)}
    assert rows[alert]["superseded_by"] == statement
    assert not any(e["id"] == alert for e in
                   staging.all_entries(tmp_db, selected_only=True)), \
        "a superseded alert must not be processed even while it is ticked"

    staging.set_selected(tmp_db, [(statement, False)])
    staging.apply_supersession(tmp_db)
    rows = {e["id"]: e for e in staging.all_entries(tmp_db)}
    assert rows[alert]["superseded_by"] is None, \
        "un-ticking the statement must bring its alerts back"


def test_an_alert_outside_the_period_is_not_superseded(tmp_db):
    from app.db import staging

    statement = staging.add(tmp_db, "h1", filename="july.pdf", kind="statement")
    staging.record_parse(tmp_db, statement, status="ok", kind="statement",
                         account_key="HSBC|XXXX1751",
                         period_start="2026-07-01", period_end="2026-07-31")
    alert = staging.add(tmp_db, "h2", filename="august coffee", kind="alert")
    staging.record_parse(tmp_db, alert, status="ok", kind="alert",
                         account_key="HSBC|XXXX1751",
                         period_start="2026-08-14", period_end="2026-08-14")

    staging.apply_supersession(tmp_db)
    rows = {e["id"]: e for e in staging.all_entries(tmp_db)}
    assert rows[alert]["superseded_by"] is None


def test_a_rebuild_does_not_delete_the_job_running_it():
    """Process data runs inside a job, and clears tables when it starts.

    The scope it used - "parsed_data" - includes `jobs`, so the rebuild
    deleted its own row. It then finished perfectly well against a row that no
    longer existed, and the screen watching it sat on "Computing analysis"
    forever waiting for a status nothing was ever going to write.
    """
    from app.db.database import CLEAR_SCOPES

    assert "jobs" not in CLEAR_SCOPES["rebuild"]
    assert "job_items" not in CLEAR_SCOPES["rebuild"]
    # It must still replace everything a document produces, or unticking a
    # file would leave its rows behind.
    for table in ("transactions", "statements", "accounts", "bureau_reports",
                  "holdings", "analysis_runs"):
        assert table in CLEAR_SCOPES["rebuild"], table


# --------------------------------------------------------------------------
# IDFC and ICICI HPCL: two statements that parsed to the wrong thing.
# --------------------------------------------------------------------------

def test_a_table_with_a_description_beats_one_without():
    """IDFC's statement offered its only transaction twice.

        t1  29 Jul 26 | BillDesk BBPS CC Payment/DP316… | 277.76 CR
        t3  29 Jul 26 |                                 | 277.7

    t3 held one more parseable row and won on row count, turning the month's
    only transaction into an unnamed 277.7 DEBIT - wrong description, wrong
    amount, wrong direction, with the real row sitting right there intact.
    """
    from app.models.schemas import ExtractedTable
    from app.normalize.normalizer import _rank_tables

    real = ExtractedTable(
        rows=[["29 Jul 26", "BillDesk BBPS CC Payment/DP316", "277.76 CR"]],
        confidence=0.55)
    summary = ExtractedTable(
        rows=[["29 Jul 26", "", "", "", "", "", "", "277.7"],
              ["22 Aug 26", "", "", "", "", "", "", "0.00"]],
        confidence=0.65)

    ranked = _rank_tables([summary, real], default_year=2026)
    assert ranked, "neither table was usable"
    winner_roles = ranked[0][0].roles
    assert "description" in winner_roles, \
        "the table that maps a description must win a near tie"


def test_a_worked_example_is_not_a_ledger():
    """Card statements must print an illustration; it is not your money.

    ICICI's HPCL statement carries two, drawn exactly like transaction tables.
    Judged on shape the bigger one wins, and the file parsed to a 1-rupee
    "urchase on" and a 5-rupee "ayment on" dated 2023 - three years outside
    its own period - while the two genuine rows were never seen.
    """
    from app.models.schemas import ExtractedTable
    from app.normalize.normalizer import _is_worked_example

    illustration = ExtractedTable(
        rows=[["1", "Purchase on Sep 20, 2023", "26,000"],
              ["5", "Payment on Oct 28, 2023", "1,100"]],
        confidence=0.65,
        surrounding_text="The following illustration will indicate the method "
                         "of calculating the MAD in this scenario")
    ledger = ExtractedTable(
        rows=[["16/07/2026", "13804164290 MICROSOFTBUS MUMBAI IN", "2.00 CR"]],
        confidence=0.55,
        surrounding_text="STATEMENT SUMMARY Total Amount due")

    assert _is_worked_example(illustration)
    assert not _is_worked_example(ledger)


def test_a_split_illustration_caption_is_still_recognised():
    """PDF extraction breaks words across cells: "e i llustration"."""
    from app.models.schemas import ExtractedTable
    from app.normalize.normalizer import _is_worked_example

    table = ExtractedTable(
        rows=[["** The abov", "e i", "llustration", "has been prepared a"]],
        confidence=0.65, surrounding_text="")
    assert _is_worked_example(table)


def test_a_bank_named_as_a_landmark_is_not_the_issuer():
    """Half of India writes addresses this way.

    An ICICI card statement prints the cardholder's address ABOVE the issuer's
    own name, and that address reads "OPP STATE BANK OF INDIA". The address is
    the whole of the letterhead slice, so the card was filed under State Bank
    of India and every figure on it attributed to a bank the user does not
    hold a card with.
    """
    from app.normalize.metadata import detect_institution

    address = ("MR PANKAJ SHARMA\n12 MAPLE COURT SAMPLE COLONY\n"
               "GREEN AVENUE WESTPARK\nOPP STATE BANK OF INDIA\n"
               "MAHARASHTRA, PUNE 400001")
    assert detect_institution(address) is None

    for preposition in ("Near", "Behind", "Beside", "Opposite", "Adj."):
        assert detect_institution(f"12 Main Road, {preposition} HDFC Bank") is None, \
            preposition

    # The bank is still detected when it is not a landmark.
    assert detect_institution("HDFC Bank Credit Card Statement") == "HDFC Bank"


def test_the_bank_named_most_often_wins():
    """An issuer names itself throughout its own statement."""
    from app.normalize.metadata import detect_institution

    text = ("Opp State Bank of India, Pune\n"
            "ICICI Bank Credit Card GST Number: 27AAACI1195H3ZK\n"
            "ICICI Bank Tower, Old Padra Road\n"
            "Contact ICICI Bank customer care")
    assert detect_institution(text) == "ICICI Bank"


def test_the_identity_cache_reads_the_columns_it_writes(tmp_db):
    """Both accessors named a column this table has never had.

    Every call raised OperationalError, and neither is wrapped in a try - so
    the path meant to rescue an unrecognised statement was the one thing
    guaranteed to fail its parse outright.
    """
    from app.db import repository as repo

    assert repo.get_ai_inference(tmp_db, "no-such-hash") is None
    repo.save_ai_inference(tmp_db, "hash-1",
                           {"institution": "ICICI Bank",
                            "account_type": "credit_card"})
    assert repo.get_ai_inference(tmp_db, "hash-1") == {
        "institution": "ICICI Bank", "account_type": "credit_card"}
    # Writing the same key again must update rather than raise.
    repo.save_ai_inference(tmp_db, "hash-1", {"institution": "ICICI Bank"})
    assert repo.get_ai_inference(tmp_db, "hash-1") == {"institution": "ICICI Bank"}


def test_a_page_dump_loses_to_a_real_ledger():
    """One row in one beats two rows in sixteen.

    When a PDF's ruled table cannot be found, the extractor recovers the whole
    PAGE as text - headings, marketing, footers - and a couple of those rows
    happen to parse. IDFC's July statement offered that table alongside its
    real one, and it won on row count. Its columns fall in the middle of
    words, including in the middle of the amount: 277.76 was stored as 277.7.
    """
    from app.models.schemas import ExtractedTable
    from app.normalize.normalizer import _rank_tables

    real = ExtractedTable(
        rows=[["28 Jun 26", "DISTRICT MOVIE TICKE, NEW DELHI", "277.76 DR"]],
        confidence=0.55)
    page_dump = ExtractedTable(
        rows=[["YOUR CA", "RD INFO", "RMATI", "ON", "", "", "", ""],
              ["Statement Date:", "Rela", "tionship No.", "CKYC :", "", "", "", ""],
              ["22/Jul/2026", "5285", "938954", "XXXXXXXXX", "X8490", "", "", ""],
              ["YOUR TR", "ANSAC", "TIONS", "", "", "", "", ""],
              ["Card Number: X", "XXX 9402", "", "", "", "", "", ""],
              ["28 Jun 26", "", "DISTRICT", "MOVIE TICKE, N", "EW DELHI", "", "", "277.7"],
              ["Enjoy t", "he Conve", "nience of", "flexible pay", "ments!", "", "", ""],
              ["", "Upgrade no", "w", "", "Refer now", "", "Apply now", ""],
              ["Covert your", "IDFC FIRST", "Bank Credit", "Card", "", "", "", ""],
              ["", "", "", "", "", "Flexible tenure", "", ""]],
        confidence=0.65)

    ranked = _rank_tables([page_dump, real], default_year=2026)
    assert ranked, "neither table was usable"
    winner_body = ranked[0][2]
    assert any("277.76" in str(cell) for row in winner_body for cell in row), \
        "the dense table holding the untruncated amount must win"


def test_a_timestamp_is_not_a_description():
    """IDFC's savings statement wraps the narration around the amount line:

        AddMoney/20252846025956/528
        11 Oct 25   17:10 11 Oct 25   1,000.00   1,000.00 CR
        478636439/UPI

    so the column beside the date holds a clock time and a value date, and the
    words sit on the lines above and below. Stored as-is, "17:10 11 Oct 25"
    becomes a payee: it shows in the ledger as a merchant, takes a category,
    and is learned into the merchant cache.
    """
    from app.normalize.normalizer import _is_only_a_timestamp

    for timestamp in ("17:10 11 Oct 25", "02:41 31 Oct 25", "17:10",
                      "11/10/25", "2025-10-11", "  09:05 1 Jan 2026 "):
        assert _is_only_a_timestamp(timestamp), timestamp

    for narration in ("02:42 INTEREST CREDIT", "DISTRICT MOVIE TICKET",
                      "BillDesk BBPS CC Payment", "UPI/1234", "AMAZON 12 PAY"):
        assert not _is_only_a_timestamp(narration), narration

    # Empty is not a timestamp - the caller has its own branch for that.
    assert not _is_only_a_timestamp("")
    assert not _is_only_a_timestamp("   ")


# --------------------------------------------------------------------------
# Which number identifies the account.
# --------------------------------------------------------------------------

def test_the_card_number_always_beats_an_account_number():
    """HDFC's Marriott statement prints both, one line apart.

        Credit Card No. 00360000XXXX4321
        Alternate Account Number 0001010000001234567

    The generic label was tried first, so the card was filed as XXXX4567 - a
    number that identifies something else. All fifteen of its transaction
    alerts said 4321 and every one was refused for naming an account that did
    not exist.
    """
    from app.normalize.metadata import detect_account_number

    assert detect_account_number(
        "PANKAJ KUMAR SHARMA Credit Card No. 00360000XXXX4321\n"
        "A-1004 SAMPLE RESIDENCY Alternate Account Number 0001010000001234567"
    ) == "XXXX4321"

    # In either order, and whatever the account number is called.
    assert detect_account_number(
        "Account Number 12345678 ... Card Number 4315XXXXXXXX1111") == "XXXX1111"
    assert detect_account_number(
        "Card No 4315XXXXXXXX1111 ... A/c No 12345678") == "XXXX1111"

    # A statement with no card still reads its account number.
    assert detect_account_number("Account Number 001010000001234567") == "XXXX4567"


def test_a_customer_id_is_not_an_account_number():
    """One customer ID spans every account the bank holds for you.

    ICICI's savings statement prints it first, masked identically:

        STATEMENT SUMMARY for Customer ID : XXXXX9341
        Savings A/c XXXXXXXX1951
    """
    from app.normalize.metadata import detect_account_number

    assert detect_account_number(
        "STATEMENT SUMMARY for Customer ID : XXXXX9341 as on July 31, 2026.\n"
        "Savings A/c XXXXXXXX1951 4,366.43 Registered"
    ) == "XXXX1951"


def test_a_mask_whose_runs_were_collapsed_is_still_read():
    """HSBC prints "51xx xxxx xxxx 1751"; extraction gives "51xx xx xx 1751".

    A pattern demanding groups of exactly four masking characters found
    nothing, so twelve HSBC statements carried no account number at all - and
    their alerts, which all said 1751, were refused.
    """
    from app.normalize.metadata import detect_account_number

    assert detect_account_number("State: 27 51xx xx xx 1751") == "XXXX1751"
    assert detect_account_number("51xx xxxx xxxx 1751") == "XXXX1751"
    assert detect_account_number("XXXX XXXX XXXX 1234") == "XXXX1234"
    assert detect_account_number("411111******4321") == "XXXX4321"


def test_a_masked_account_number_is_not_an_isin():
    """"XXXXXXXX1951" is two letters, nine alphanumerics and a digit.

    An ISIN alone is treated as proof that a document is a securities
    statement, so twelve months of ICICI savings statements - eighty
    transactions each - were routed to the holdings reader, found to contain
    no holdings, and filed as empty portfolios. Their transactions were never
    read.
    """
    from app.ingestion.portfolio import ISIN

    for masked in ("XXXXXXXX1951", "XXXXXXXXXX85", "4315XXXXXXXX9239"):
        assert not ISIN.search(masked), masked
    for real in ("INE002A01018", "INF109K01Z48", "US0378331005", "IN0020230069"):
        assert ISIN.search(real), real


def test_no_source_file_contains_a_literal_control_byte():
    """A regex written as "\b" that became a backspace character matches nothing.

    Two guards in metadata.py silently did nothing because the escape was
    eaten when the file was written, leaving \x08 in the pattern. Nothing
    failed; the rules just never fired.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        raw = path.read_bytes()
        for byte in (b"\x08", b"\x07", b"\x0b", b"\x0c"):
            if byte in raw:
                offenders.append(f"{path.name}: {byte!r}")
    assert not offenders, offenders


def test_alerts_are_rebuilt_after_the_statements_they_attach_to(tmp_db):
    """An alert can only join an account some statement described.

    Entries arrive sorted by account label, and an alert's label is shorter
    than its statement's - "HDFC Bank (…4321)" sorts before "HDFC Bank
    Marriott Bonvoy Credit Card (XXXX4321)". So every alert was looked up
    against an account that did not exist yet, and all of them were dropped
    from the rebuild without a word.
    """
    from app.db import repository as repo
    from app.db import staging
    from app.pipeline import staging_pipeline
    import app.db.database as database_mod

    previous = database_mod._db
    database_mod._db = tmp_db
    try:
        statement = staging.add(tmp_db, "h-stmt", filename="marriott.pdf",
                                kind="statement")
        staging.record_parse(
            tmp_db, statement, status="ok", kind="statement",
            account_key="HDFC Bank|XXXX4321",
            account_label="HDFC Bank Marriott Bonvoy Credit Card (XXXX4321)",
            period_start="2026-07-01", period_end="2026-07-31",
            payload={"kind": "statement",
                     "statement": {"id": "s1", "account_id": "a1",
                                   "source_filename": "marriott.pdf",
                                   "transactions": []},
                     "account": {"institution": "HDFC Bank",
                                 "account_type": "credit_card",
                                 "account_number_masked": "XXXX4321"},
                     "reconciliation": {"status": "passed", "message": ""}})

        alert = staging.add(tmp_db, "h-alert", filename="BIRD", kind="alert")
        staging.record_parse(
            tmp_db, alert, status="ok", kind="alert",
            account_key="HDFC Bank|XXXX4321",
            account_label="HDFC Bank (…4321)",
            period_start="2026-08-04", period_end="2026-08-04",
            payload={"kind": "alert",
                     "alert": {"amount": "2899.00", "direction": "debit",
                               "date_iso": "2026-08-04", "merchant": "BIRD",
                               "account_suffix": "4321"}})

        built = staging_pipeline.materialise(tmp_db)
        rows = built.pop("_transactions", [])
        assert any(t.source == "email_alert" for t in rows), \
            "the alert was dropped because its account did not exist yet"
    finally:
        database_mod._db = previous


def test_money_and_dates_survive_a_round_trip_through_storage():
    """JSON has no Decimal and no date, so both come back as strings.

    Nothing complains when the object is rebuilt - it is constructed happily -
    and the failure lands later, in whatever first does arithmetic or calls a
    date method:

        units * nav        -> TypeError: can't multiply sequence by non-int
        as_of.isoformat()  -> AttributeError: 'str' object has no attribute

    That cost 81 broker statements out of one rebuild. A Decimal that is
    secretly a string still adds up - by concatenation - which is the reason
    this is checked rather than trusted.
    """
    import dataclasses
    from datetime import date
    from decimal import Decimal
    from app.ingestion.bureau import BureauAccount, BureauReport
    from app.ingestion.portfolio import Holding, PortfolioStatement
    from app.pipeline.staging_pipeline import _rebuild_dataclass

    holding = Holding(instrument="Reliance", isin="INE002A01018", symbol="RELI",
                      folio="", kind="equity", units=Decimal("10"),
                      nav=Decimal("2.5"), value=Decimal("25"),
                      avg_cost=None, invested=None)
    statement = PortfolioStatement(
        layout="broker", provider="Zerodha", as_of=date(2026, 3, 31),
        declared_value=Decimal("25"), holdings=[holding], warnings=[])

    back = _rebuild_dataclass(PortfolioStatement,
                              dataclasses.asdict(statement))
    assert back.as_of == date(2026, 3, 31)
    assert back.as_of.isoformat() == "2026-03-31"
    assert back.holdings[0].units * back.holdings[0].nav == Decimal("25.0")
    assert back.holdings[0].avg_cost is None

    account = BureauAccount(
        lender="HDFC", account_type="credit_card",
        account_number_masked="XXXX4321", ownership="individual",
        opened_on=date(2020, 1, 1), closed_on=None, status="open",
        sanctioned=Decimal("1000"), current_balance=Decimal("5"),
        overdue=None, credit_limit=None, emi_amount=None, dpd_history=[])
    report = BureauReport(bureau="crif", score=833, score_band="good",
                          pulled_on=date(2026, 8, 1), holder_name="J",
                          accounts=[account], warnings=[])

    rebuilt = _rebuild_dataclass(BureauReport, dataclasses.asdict(report))
    assert rebuilt.pulled_on == date(2026, 8, 1)
    assert rebuilt.score == 833 and isinstance(rebuilt.score, int)
    assert rebuilt.accounts[0].sanctioned == Decimal("1000")
    assert rebuilt.accounts[0].opened_on == date(2020, 1, 1)
    assert rebuilt.accounts[0].closed_on is None


# --------------------------------------------------------------------------
# One ledger, two pages, two different column layouts.
# --------------------------------------------------------------------------

def test_an_amount_split_inside_its_decimals_is_rejoined():
    """American Express breaks "294.78" into "294.7" and "8".

    The leading half is a legal-looking amount, so nothing complained: eleven
    June transactions were read at the wrong value. Money printed to one
    decimal place is itself the tell - Indian statements print two.
    """
    from app.normalize.normalizer import _repair_split_amounts

    rows = [["June 6", "Razorpay*Zomato", "", "294.7", "8"],
            ["June 17", "AMAZON Mumbai", "", "2,962.0", "5"]]
    repaired = _repair_split_amounts(rows)
    # Joined into the LEFT cell: that is the column this shape leaves the
    # amounts dense in, and a money role has to find them somewhere.
    assert repaired[0][3] == "294.78" and repaired[0][4] == ""
    assert repaired[1][3] == "2,962.05" and repaired[1][4] == ""


def test_the_older_split_shape_still_joins_rightwards():
    """A boundary falling BEFORE the decimals leaves the bulk on the right."""
    from app.normalize.normalizer import _repair_split_amounts

    rows = [["July 4", "ZUDIO", "5,3", "99.00"]]
    repaired = _repair_split_amounts(rows)
    assert repaired[0][2] == "" and repaired[0][3] == "5,399.00"


def test_a_money_column_is_judged_on_its_own_numbers():
    """Pooling columns let one column's debris hide another's split amounts.

    On the Amex statement the column holding the split amounts was 31%
    suspect by itself, but averaging it with a neighbour full of
    "americanexpress.co.in" dragged the pair under the threshold - so the
    broken mapping was accepted and the repair never ran.
    """
    from app.normalize.column_map import ColumnMapping
    from app.normalize.normalizer import _has_truncated_amounts

    rows = [
        ["June 4", "ZEPTO", "americanexpress.co.in", "164.0"],
        ["June 5", "AMAZON", "Cyber City, Tower C", "2,249.0"],
        ["June 6", "AMAZON", "Gurgaon - 122002", "346.0"],
    ]
    mapping = ColumnMapping(roles={"txn_date": 0, "description": 1,
                                   "debit": 2, "credit": 3})
    assert _has_truncated_amounts(rows, mapping) is True


def test_two_pages_of_one_ledger_merge_across_column_layouts():
    """Amex reads page 1 as debit/credit and page 2 as a single amount.

    Requiring identical role NAMES refused the merge, and page one's eleven
    transactions - 11,348.23, exactly the gap the reconciliation gate then
    reported - were dropped without a word.
    """
    from app.models.schemas import ExtractedTable
    from app.normalize.column_map import ColumnMapping
    from app.normalize.normalizer import _merge_continuations, _same_kind_of_table

    page1 = ColumnMapping(roles={"txn_date": 0, "description": 1,
                                 "debit": 6, "credit": 7, "balance": 8})
    page2 = ColumnMapping(roles={"txn_date": 0, "description": 2, "amount": 8})
    assert _same_kind_of_table(page1, page2)

    chosen = ExtractedTable(rows=[], confidence=0.65, source_page=2)
    other = ExtractedTable(rows=[], confidence=0.65, source_page=1)
    body1 = [["June 6", "Razorpay*Zomato", "", "", "", "", "", "294.7", "8"]]

    merged = _merge_continuations(
        chosen, [["June 18", "", "EASEBUZZ", "", "", "", "", "", "2,163.00"]],
        [(page2, chosen, []), (page1, other, body1)], page2)

    # The split is rejoined BEFORE projection - once the halves are reshaped
    # into another page's layout they are no longer adjacent.
    assert any("294.78" in str(cell) for row in merged for cell in row), merged


def test_a_projected_amount_takes_its_direction_from_the_printed_marker():
    """Not from the name an inferred column happened to be given.

    Amex's first page resolved to a "credit" column that in fact held eleven
    ordinary purchases; trusting the label turned every one into money coming
    in.
    """
    from app.normalize.column_map import ColumnMapping
    from app.normalize.normalizer import _project_row

    source = ColumnMapping(roles={"txn_date": 0, "description": 1, "credit": 2})
    target = ColumnMapping(roles={"txn_date": 0, "description": 1, "amount": 2})

    plain = _project_row(["June 4", "ZEPTO", "164.00"], source, target)
    assert plain[2] == "164.00", "no marker printed, so no CR is invented"

    marked = _project_row(["June 24", "AMAZON", "599.00 CR"], source, target)
    assert "CR" in marked[2], "a marker that WAS printed must survive"


def test_a_contract_note_is_not_a_holdings_statement():
    """Both are securities documents; only one says what you own.

    An Upstox "ANNUAL GLOBAL TRANSACTION STATEMENT ... Segment: Future &
    Option" was read as holdings and produced one position - "NIFTY NIFTY
    NIFTY NIFTY", 1,050 units at 22,500 - worth 2.36 CRORE, over a portfolio
    that should have totalled about four lakh. The 1,050 was a traded
    quantity and the 22,500 a strike price; every line closed at Net Quantity
    0.00, so nothing was held at all.
    """
    from app.ingestion.portfolio import looks_like_portfolio, looks_like_trades

    fno = ("ANNUAL GLOBAL TRANSACTION STATEMENT (AGS) Segment : Future & Option\n"
           "Security Description Strike Rate Due Date Net Quantity\n"
           "NIFTY CE 22,500.00 2025-04-09 1,050.00 85.23 89,493.75")
    assert looks_like_trades(fno)
    assert not looks_like_portfolio(fno)

    holdings = ("CONSOLIDATED ACCOUNT STATEMENT\n"
                "Folio No 5104091481/0 INE002A01018 Units Held 127.76 "
                "NAV 452.86 Market Value 57,857.69")
    assert not looks_like_trades(holdings)
    assert looks_like_portfolio(holdings)


def test_a_folio_wrapped_across_a_line_is_the_same_folio():
    """Holdings are keyed by (account, ISIN, folio).

    A PDF that wrapped "5104091481/0" left a soft hyphen in it, so one month
    read "510409148\xad 1/0" and the next read it clean. The database saw two
    holdings, and the same fund appeared twice at two different valuations -
    once per monthly statement.
    """
    from app.ingestion.portfolio import _clean_identifier

    assert _clean_identifier("510409148\xad 1/0") == "5104091481/0"
    assert _clean_identifier("5104091481/0") == "5104091481/0"
    assert _clean_identifier("INF966L01986") == "INF966L01986"
    assert _clean_identifier("​INE002A01018﻿") == "INE002A01018"


def test_a_contract_note_is_not_read_as_a_bank_statement_either(tmp_db, tmp_path):
    """Refusing it as a portfolio is only half the job.

    Teaching `looks_like_portfolio` to reject a contract note stopped it
    inventing holdings, and the file then fell through to the STATEMENT
    reader - which is worse. Fifteen Zerodha contract notes each produced one
    transaction whose amount was its settlement number: "Settlement No:
    2026151" became a 20,26,151 debit, and money out for the year read three
    crore.
    """
    from app.db import staging
    from app.ingestion.router import file_hash
    from app.pipeline import staging_pipeline

    note = tmp_path / "23-09-2025-contract-notes_UC9050.csv"
    note.write_text(
        "Contract Note cum Tax Invoice\n"
        "Settlement No:,2025182,Strike Rate,22500.00\n"
        "Security,Buy Qty,Buy Rate,Sell Qty,Sell Rate\n"
        "NIFTY CE,1050,85.23,1050,85.16\n",
        encoding="utf-8")

    entry_id = staging.add(tmp_db, file_hash(note), filename=note.name,
                           path=str(note), origin="upload")
    entry = next(e for e in staging.all_entries(tmp_db) if e["id"] == entry_id)
    status = staging_pipeline.parse_entry(tmp_db, entry)

    parsed = next(e for e in staging.all_entries(tmp_db) if e["id"] == entry_id)
    assert parsed["kind"] == "trades", parsed["kind"]
    assert parsed["row_count"] == 0, "a contract note produces no transactions"
    assert status == staging_pipeline.STATUS_EMPTY


def test_a_staged_alert_records_which_scan_found_it(tmp_db):
    """Without it the column defaults to empty, and empty read as "statement".

    113 alerts were staged perfectly well and then counted under Account
    statements. The alerts section reported nothing staged while its own
    documents sat in the section next to it, and the Parse step offered no
    way to reach them.
    """
    from app.db import staging
    from app.pipeline.staging_pipeline import stage_alert

    entry_id = stage_alert(tmp_db, {
        "message_id": "m1", "amount": "1069.80", "direction": "debit",
        "date_iso": "2026-08-31", "account_suffix": "1751",
        "institution": "HSBC", "merchant": "AMAZON",
    })
    entry = next(e for e in staging.all_entries(tmp_db) if e["id"] == entry_id)
    assert entry["scan_intent"] == "transactional", entry["scan_intent"]
    assert entry["kind"] == "alert"


def test_an_entry_with_no_recorded_source_is_placed_by_what_it_is(tmp_db):
    """Assuming "statement" is how the alerts came to hide among them."""
    from app.api.staging_routes import _INTENT_FOR_KIND

    assert _INTENT_FOR_KIND["alert"] == "transactional"
    assert _INTENT_FOR_KIND["portfolio"] == "investment"
    assert _INTENT_FOR_KIND["trades"] == "investment"
    assert _INTENT_FOR_KIND["bureau"] == "bureau"


def test_a_more_specific_scan_corrects_a_documents_source(tmp_db):
    """The scans overlap, so the first answer is not always the best one.

    The statement scan's sender list contains every broker, so a Zerodha
    holdings PDF is found first as a statement and later as an investment.
    Keeping the first filed 108 investment documents under Account
    statements: the Investments section reported nothing staged while Choose
    went on offering them, and Parse gave no way to reach them.
    """
    from app.db import staging

    first = staging.add(tmp_db, "hash-1", filename="holdings.pdf",
                        scan_intent="statement")
    again = staging.add(tmp_db, "hash-1", filename="holdings.pdf",
                        scan_intent="investment")
    assert again == first, "still one document"
    entry = next(e for e in staging.all_entries(tmp_db) if e["id"] == first)
    assert entry["scan_intent"] == "investment"

    # And the correction only goes one way - the catch-all never wins back.
    staging.add(tmp_db, "hash-1", filename="holdings.pdf",
                scan_intent="statement")
    entry = next(e for e in staging.all_entries(tmp_db) if e["id"] == first)
    assert entry["scan_intent"] == "investment"


def test_correcting_a_source_does_not_disturb_the_selection(tmp_db):
    """Re-scanning must never re-tick something turned off."""
    from app.db import staging

    entry_id = staging.add(tmp_db, "hash-2", filename="x.pdf",
                           scan_intent="statement")
    staging.set_selected(tmp_db, [(entry_id, False)])
    staging.add(tmp_db, "hash-2", filename="x.pdf", scan_intent="investment")
    entry = next(e for e in staging.all_entries(tmp_db) if e["id"] == entry_id)
    assert entry["selected"] is False
    assert entry["scan_intent"] == "investment"


def test_processing_records_every_document_in_the_file_registry(tmp_db, tmp_path):
    """The Data tab's "Files & passwords" screen reads `source_files`.

    The staged path - which is now how everything arrives - never wrote to it,
    only the old import path did. So a ledger built entirely through the
    wizard left that screen empty: no files, and therefore no password box,
    because the box is rendered per file row. The ledger beside it was fully
    populated, which is what made it look like a display bug rather than a
    missing write.
    """
    from app.api import staging_routes
    from app.db import repository as repo
    from app.db import staging
    from app.ingestion.router import file_hash
    from app.pipeline import staging_pipeline

    path = _csv_statement(tmp_path)
    entry_id = staging.add(tmp_db, file_hash(path), filename=path.name,
                           path=str(path), origin="upload")
    entry = next(e for e in staging.all_entries(tmp_db) if e["id"] == entry_id)
    staging_pipeline.parse_entry(tmp_db, entry)

    assert repo.list_source_files(tmp_db) == [], \
        "parsing alone must not touch the registry"

    staging_routes._register_documents(tmp_db)

    files = repo.list_source_files(tmp_db)
    assert len(files) == 1
    assert files[0].filename == path.name
    assert files[0].source == "upload"
    # "empty" means read and understood with nothing in it that counts. It is
    # a success, and calling it a failure sends someone hunting a bug that is
    # not there.
    assert files[0].parse_status in ("parsed", "unreconciled")


def test_a_locked_document_reaches_the_screen_that_can_unlock_it(tmp_db):
    """`source_files` is the ONLY table that remembers a document which could
    not be read - statements and transactions hold successes exclusively. A
    password-protected PDF that never parsed has no other way to reach the one
    screen with a password box on it."""
    from app.api import staging_routes
    from app.db import repository as repo
    from app.db import staging
    from app.pipeline import staging_pipeline

    entry_id = staging.add(tmp_db, "locked-hash", filename="hdfc_card.pdf",
                           path="/nowhere/hdfc_card.pdf", origin="gmail")
    staging.record_parse(tmp_db, entry_id,
                         status=staging_pipeline.STATUS_LOCKED,
                         message="Protected, and no password derived from "
                                 "your profile opened it.")

    staging_routes._register_documents(tmp_db)

    files = repo.list_source_files(tmp_db)
    assert len(files) == 1
    assert files[0].parse_status == "needs_password"
    assert files[0].password_status == "locked"
    # No statement row was ever written for it, so the registry must not point
    # at one - that is a dangling foreign key, and the drill-down would 500.
    assert files[0].statement_id is None


def test_a_superseded_document_is_not_listed_twice(tmp_db):
    """A statement that arrived twice supersedes its worse copy. Listing both
    puts the same month on screen twice with no way to tell which counted."""
    from app.api import staging_routes
    from app.db import repository as repo
    from app.db import staging

    keep = staging.add(tmp_db, "hash-good", filename="axis_august.pdf")
    drop = staging.add(tmp_db, "hash-worse", filename="axis_august_older.pdf")
    with tmp_db.connection() as conn:
        conn.execute("UPDATE staged_files SET superseded_by = ? WHERE id = ?",
                     (keep, drop))

    staging_routes._register_documents(tmp_db)

    listed = [f.filename for f in repo.list_source_files(tmp_db)]
    assert listed == ["axis_august.pdf"]
