"""What the one non-deterministic reader in `normalize` is allowed to say.

`card_summary` reads a credit card's summary box with a model, and its own
docstring calls that a deliberate exception. Most of what it returns - the
limit, the minimum due, the dates - is unchecked by anything and otherwise
simply absent, so a careful reading beats nothing.

One field is different. `total_due` becomes `statement.closing_balance`,
which becomes the card's `principal_outstanding`, which reaches Net Worth.
These are the guards on that path.
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.normalize.card_summary import _amount                # noqa: E402
from app.normalize.metadata import _implausible_due           # noqa: E402


# ---------------------------------------------------------------- the sign

def test_a_card_in_credit_does_not_become_a_card_in_debt():
    """Stripping to digits lost the sign.

    An Indian card statement says the issuer owes the holder either as
    "-500.00" or as "500.00 CR", and both came back as a positive 500 -
    read as a total due, that is 500 of debt on the wrong side of the
    balance sheet, on the one figure here that reaches Net Worth.
    """
    assert _amount("-500.00", set()) == Decimal("-500.00")
    assert _amount("500.00 CR", set()) == Decimal("-500.00")
    assert _amount("500.00 Cr", set()) == Decimal("-500.00")


def test_an_ordinary_debit_balance_keeps_its_sign():
    assert _amount("1,234.56", set()) == Decimal("1234.56")
    assert _amount("12,345.67 Dr", set()) == Decimal("12345.67")


def test_cr_inside_a_word_is_not_a_credit_marker():
    """The marker is a whole word at the end, not a substring - or every
    amount labelled "CREDIT LIMIT" would flip sign."""
    assert _amount("2,00,000 CREDIT LIMIT", set()) == Decimal("200000")


def test_an_issuer_prefix_is_still_refused():
    """The rule that matters is enforced, not merely requested of the
    model: a card number sits exactly where a limit belongs."""
    assert _amount("653047", {Decimal("653047")}) is None


# ------------------------------------------------------------- the due

def test_an_ordinary_bill_is_believed():
    assert _implausible_due(Decimal("405.00"), Decimal("200000")) == ""


def test_a_floor_would_have_been_the_wrong_shape():
    """`credit_limit` has a 1000 floor and a due must not.

    A real card bill is legitimately zero, or fifty rupees. The check has
    to be against something else - and the card's own limit sits in the
    same summary box.
    """
    assert _implausible_due(Decimal("0"), Decimal("200000")) == ""
    assert _implausible_due(Decimal("50"), Decimal("200000")) == ""


def test_genuinely_over_the_limit_is_still_allowed():
    """A standing instruction landing on a nearly-full card is real, and
    runs to a few percent."""
    assert _implausible_due(Decimal("205000"), Decimal("200000")) == ""
    assert _implausible_due(Decimal("200000"), Decimal("200000")) == ""


def test_a_figure_far_past_the_limit_is_refused():
    """The misread this module exists to catch: the limit itself, the cash
    limit, or a lakh figure lifted from the interest worked example."""
    refusal = _implausible_due(Decimal("650470"), Decimal("200000"))
    assert refusal
    assert "credit limit" in refusal


def test_with_no_limit_known_there_is_nothing_to_check_against():
    """Refusing on no evidence would throw away the reading this module
    exists to provide."""
    assert _implausible_due(Decimal("8813.65"), None) == ""
    assert _implausible_due(Decimal("8813.65"), Decimal("0")) == ""


def test_a_credit_balance_is_not_refused_as_implausible():
    """A card in credit is a real state, and the sign now survives
    `_amount`, so this check must not treat it as a misread."""
    assert _implausible_due(Decimal("-500"), Decimal("200000")) == ""


# -------------------------------------------------- what the holder is told

def _card_text():
    """Enough of a card statement for `extract_metadata` to type it."""
    return (
        "HDFC Bank Credit Card Statement\n"
        "Card Number XXXX6885\n"
        "Statement Period 01/08/2026 to 31/08/2026\n"
        "01/08/2026 SWIGGY BANGALORE 450.00\n"
    )


def _read_returning(**fields):
    from app.normalize.card_summary import CardSummary

    base = dict(credit_limit=None, total_due=None, min_due=None,
                statement_date=None, payment_due_date=None,
                cycle_start=None, cycle_end=None)
    base.update(fields)
    return lambda text, bins=None: CardSummary(**base)


def test_the_holder_is_told_when_a_balance_came_from_the_model(monkeypatch):
    """The commonest case used to read "not None found elsewhere".

    This is the one figure in the box that becomes an account balance and
    reaches Net Worth, so that it arrived from a model has to be legible
    without the holder inferring it from some other number changing.
    """
    from app.normalize import card_summary, metadata

    monkeypatch.setattr(card_summary, "read",
                        _read_returning(total_due=Decimal("405.00")))
    meta = metadata.extract_metadata(_card_text(), filename="hdfc.pdf",
                                     full_text=_card_text())

    notes = " ".join(meta.notes)
    assert "405.00" in notes
    assert "summary box" in notes
    assert "None" not in notes, notes


def test_an_implausible_balance_is_refused_and_said_so(monkeypatch):
    """Refusing quietly would leave the holder with a card that has no
    balance and no reason given."""
    from app.normalize import card_summary, metadata

    monkeypatch.setattr(card_summary, "read",
                        _read_returning(total_due=Decimal("650470"),
                                        credit_limit=Decimal("200000")))
    text = _card_text() + "Credit Limit 200000\n"
    meta = metadata.extract_metadata(text, filename="hdfc.pdf", full_text=text)

    notes = " ".join(meta.notes)
    if "650470" in notes:
        assert "Ignored" in notes
        assert meta.closing_balance != Decimal("650470")
