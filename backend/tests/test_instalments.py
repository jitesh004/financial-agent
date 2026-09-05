"""The word "EMI" is not evidence of a loan.

Every case below is a real narration shape. The offer-marker rows are what an
issuer prints against an ordinary purchase to advertise that the charge could
be split up if the cardholder asked; the loan rows are a lender collecting
money. Before `rules.instalments` existed, both families landed in the debt
figures and the merchant cache learned the wrong answer permanently.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.categorize.llm_categorizer import (COUNTERPARTY_CATEGORIES,  # noqa: E402
                                            _merchant_key,
                                            categorize_with_llm, last_run)
from app.categorize.rules import apply_rules  # noqa: E402
from app.models.schemas import (Category, ConfidenceSource,  # noqa: E402
                                Direction, Transaction)
from app.normalize.parsers import normalize_description  # noqa: E402
from app.rules import instalments  # noqa: E402


def txn(raw: str, direction: Direction = Direction.DEBIT,
        amount: str = "5000") -> Transaction:
    return Transaction(
        txn_date=date(2026, 3, 4),
        raw_description=raw,
        normalized_description=normalize_description(raw),
        amount=Decimal(amount),
        direction=direction,
    )


# --------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------

OFFER_MARKERS = [
    "22:01 EMI INFINITIRETAILLIMITEDMumbai",
    "17:20 EMI TatadigitalGurgoan",
    "EMI CLOUDNINE PNEPPSPUNE",
    "EMI Riders Choice",
    "EMI-EDUSPARK INTERNATIONAL",
    "EMI PANCHJANYA AUTOMOBILE",
    "POS 4728 EMI ZEPTO MARKETPLACE",
]

REAL_INSTALMENTS = [
    "EMI PRIN FOR TATA AIG GENERAL (020/036)",
    "EMI INT-HDFC BANK (007/060)",
    "MERIDIAN HOME LOAN EMI PRIN (013/240)",
    "ACH-D- BAJAJ FINANCE LTD",
    "NACH DR HDB FINANCIAL SERVICES",
    "HL12345678 EMI DEBIT",
    "PERSONAL LOAN REPAYMENT",
    "INSTALMENT 24 OF 60",
]

#: The word "instalment" belongs to three worlds and only one of them is debt.
NOT_DEBT_AT_ALL = [
    ("RD INSTALMENT 15000", Category.INVESTMENT),
    ("SIP INSTALMENT PARAG PARIKH FLEXI CAP", Category.INVESTMENT),
    ("INSTALLMENT PROCESSING FEE", Category.FEES_CHARGES),
]


@pytest.mark.parametrize("raw", OFFER_MARKERS)
def test_an_offer_marker_is_not_a_loan(raw):
    assert not instalments.looks_like_loan_instalment(raw)
    assert instalments.carries_offer_marker(raw)
    # The merchant survives; only the marker goes.
    assert "EMI" not in instalments.strip_offer_marker(raw).upper().split()


@pytest.mark.parametrize("raw", REAL_INSTALMENTS)
def test_a_lender_collecting_money_is_a_loan(raw):
    assert instalments.looks_like_loan_instalment(raw)
    assert not instalments.carries_offer_marker(raw)
    # Nothing is stripped out of a real instalment - the wording is what the
    # row means, and the EMI rule needs to see it.
    assert instalments.strip_offer_marker(raw) == raw


@pytest.mark.parametrize("raw", REAL_INSTALMENTS)
def test_a_real_instalment_is_categorised_as_debt(raw):
    match = apply_rules(txn(raw))
    assert match is not None, raw
    assert match[0] == Category.EMI, f"{raw} -> {match}"


@pytest.mark.parametrize("raw,expected", NOT_DEBT_AT_ALL)
def test_the_word_instalment_alone_does_not_make_it_debt(raw, expected):
    """A recurring deposit is saving, and a conversion fee is a charge.

    Both used to be filed as EMI: the debt rule carried a bare
    \\bINSTAL?MENT\\b and sits above the investment and fee rules, so it
    claimed all three before either of them ran.
    """
    match = apply_rules(txn(raw))
    assert match is not None, raw
    assert match[0] == expected, f"{raw} -> {match}"


def test_a_marked_purchase_is_categorised_by_its_merchant():
    """The marker hid the one thing the row does say."""
    match = apply_rules(txn("22:01 EMI CLOUDNINE PNEPPSPUNE"))
    assert match is not None
    assert match[0] == Category.HEALTHCARE

    match = apply_rules(txn("EMI ZEPTO MARKETPLACE"))
    assert match is not None
    assert match[0] == Category.GROCERIES


def test_emirates_is_not_an_emi():
    """The token has to stand alone. "EMIRATES" and "SEMI" are words."""
    assert not instalments.carries_offer_marker("EMIRATES AIRLINE TICKET")
    assert instalments.strip_offer_marker("EMIRATES AIRLINE TICKET") \
        == "EMIRATES AIRLINE TICKET"
    assert instalments.strip_offer_marker("SEMICONDUCTOR LABS") \
        == "SEMICONDUCTOR LABS"


def test_a_row_that_is_only_a_marker_keeps_its_text():
    """Stripping everything would turn a poor description into none at all."""
    assert instalments.strip_offer_marker("EMI") == "EMI"
    assert instalments.strip_offer_marker("22:01 EMI") == "22:01 EMI"


# --------------------------------------------------------------------------
# The model path
# --------------------------------------------------------------------------

def test_the_merchant_key_drops_the_marker():
    """Two things ride on this key, and both were wrong with EMI in it.

    It is what the model sees - the single token most likely to drag it to
    the wrong answer - and it is what the cache is keyed on, so the same
    hospital was learned twice, once correctly and once as debt.
    """
    marked = txn("22:01 EMI CLOUDNINE PNEPPSPUNE")
    marked.merchant = "EMI CLOUDNINE PNEPPS"
    plain = txn("CLOUDNINE PNEPPSPUNE")
    plain.merchant = "CLOUDNINE PNEPPS"
    assert _merchant_key(marked) == _merchant_key(plain)
    assert "EMI" not in _merchant_key(marked).split()


def test_a_real_instalment_keeps_its_own_merchant_key():
    row = txn("EMI PRIN FOR TATA AIG GENERAL (020/036)")
    row.merchant = None
    assert "EMI" in _merchant_key(row)


class _Model:
    """A model that answers a fixed category for everything it is asked."""

    available = True

    def __init__(self, category: str):
        self.category = category
        self.calls = 0

    def complete_json(self, prompt, system="", **kwargs):
        self.calls += 1
        count = prompt.count('\n1. [') + prompt.count('\n0. [') or 1
        return {"results": [{"i": i, "category": self.category,
                             "confidence": 0.9}
                            for i in range(count)]}


def test_the_model_may_not_call_a_merchant_a_loan():
    """The backstop for when a model says "emi" anyway.

    The marker is stripped before it ever sees the string, but a small model
    handed "CLOUDNINE" will still occasionally reach for debt. An answer that
    names a counterparty relationship needs a lender in the string; without
    one it is refused and the row stays uncategorized, which the UI shows as
    "needs review". A wrong debt figure is worse than a missing one.
    """
    rows = [txn("CLOUDNINE PNEPPSPUNE")]
    rows[0].merchant = "CLOUDNINE PNEPPS"
    model = _Model(Category.EMI)

    from_cache, from_model = categorize_with_llm(rows, db=None, client=model)

    assert model.calls == 1, "the model was not consulted at all"
    assert (from_cache, from_model) == (0, 0)
    assert rows[0].category == Category.UNCATEGORIZED
    assert last_run["unevidenced"] == 1


def test_the_model_may_still_call_a_lender_a_loan():
    rows = [txn("ACH-D- BAJAJ FINANCE LTD")]
    rows[0].merchant = "ACH D BAJAJ FINANCE LTD"
    model = _Model(Category.EMI)

    _, from_model = categorize_with_llm(rows, db=None, client=model)

    assert from_model == 1
    assert rows[0].category == Category.EMI
    assert last_run["unevidenced"] == 0


def test_an_ordinary_answer_is_untouched():
    rows = [txn("SOME UNKNOWN SHOP")]
    rows[0].merchant = "SOME UNKNOWN SHOP"
    model = _Model(Category.SHOPPING)

    _, from_model = categorize_with_llm(rows, db=None, client=model)

    assert from_model == 1
    assert rows[0].category == Category.SHOPPING


def test_every_counterparty_category_is_guarded():
    """The guard covers the whole family, not just the one that misfired."""
    assert COUNTERPARTY_CATEGORIES == {
        Category.EMI, Category.LOAN_INTEREST, Category.CC_PAYMENT}


# --------------------------------------------------------------------------
# The cache heals itself
# --------------------------------------------------------------------------

def test_a_poisoned_merchant_category_is_forgotten(tenant):
    """A run that merely stopped making the mistake would keep applying it.

    The merchant cache is permanent by design - an answer given once is
    reused forever - so every hospital already filed as debt would have
    stayed there until somebody re-categorised it by hand. Cleared on read
    instead, per account, so it heals on the next run each user does.
    """
    from app.db import repository as repo
    from app.db.database import get_db
    from tests.support import fresh_ledger

    db = fresh_ledger()
    repo.save_merchant_categories(db, {
        "CLOUDNINE PNEPPS": (Category.EMI, 0.9, "llm"),
        "BAJAJ FINANCE LTD ACH D": (Category.EMI, 0.9, "llm"),
    })

    rows = [txn("CLOUDNINE PNEPPSPUNE"), txn("ACH-D- BAJAJ FINANCE LTD")]
    rows[0].merchant = "CLOUDNINE PNEPPS"
    rows[1].merchant = "BAJAJ FINANCE LTD ACH D"

    from_cache, _ = categorize_with_llm(rows, db=db, client=None)

    # The lender's row keeps its answer; the hospital's is dropped.
    assert from_cache == 1
    assert rows[1].category == Category.EMI
    assert rows[0].category == Category.UNCATEGORIZED
    assert "CLOUDNINE PNEPPS" not in repo.lookup_merchants(
        get_db(), ["CLOUDNINE PNEPPS"])


def test_a_users_own_correction_is_never_second_guessed(tenant):
    """They are allowed to call a merchant whatever they need it to be."""
    from app.db import repository as repo
    from tests.support import fresh_ledger

    db = fresh_ledger()
    repo.save_merchant_categories(
        db, {"CLOUDNINE PNEPPS": (Category.EMI, 1.0, "user")})

    rows = [txn("CLOUDNINE PNEPPSPUNE")]
    rows[0].merchant = "CLOUDNINE PNEPPS"

    from_cache, _ = categorize_with_llm(rows, db=db, client=None)

    assert from_cache == 1
    assert rows[0].category == Category.EMI
    assert rows[0].category_source == ConfidenceSource.USER


# --------------------------------------------------------------------------
# The app explaining itself
# --------------------------------------------------------------------------

def test_the_explain_box_agrees_with_the_pipeline():
    """This box IS the app explaining itself, so it has to search the same
    strings. It did not: the ledger filed "EMI CLOUDNINE" as healthcare and
    the box reported that nothing matched it at all."""
    from app.api.rules_routes import _explain_description

    result = _explain_description("22:01 EMI CLOUDNINE PNEPPSPUNE", "debit")
    assert result["winner"]["category"] == Category.HEALTHCARE
    assert any("CLOUDNINE" in s and "EMI" not in s.split()
               for s in result["searched"])


def test_the_explain_box_names_the_rule_that_stood_down():
    from app.api.rules_routes import _explain_description

    result = _explain_description("RD INSTALMENT 15000", "debit")
    assert result["winner"]["category"] == Category.INVESTMENT
    vetoed = result["vetoed"]
    assert vetoed and vetoed[0]["category"] == Category.EMI
    assert vetoed[0]["vetoed_by"].upper() == "RD"
