"""The questions, and how to know whether the answer was right.

Every expectation is computed from the ledger when the eval runs, not
written down here. A case that says "fuel in August is 5,500" is wrong the
moment a statement is imported; a case that says "fuel in August is
whatever SQL says it is" stays true.

Ground truth is deliberately raw SQL rather than the agent's own tools.
An eval built on `ledger_query` cannot catch a bug in `ledger_query`, and
there was one - a date range with no `preset` was discarded, so every
dated question returned all-time figures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable


@dataclass(frozen=True)
class Case:
    """One question, and what a correct answer to it looks like."""

    key: str
    question: str
    #: Why this question is in the set. Not decoration: a failing case is
    #: read by someone deciding whether to change the prompt or the tool,
    #: and "it should get this right" is not enough to decide with.
    why: str

    #: Computes the expected figure from the ledger. None for cases where
    #: correctness is structural rather than numeric.
    truth: Callable[[Any], Decimal | None] | None = None

    #: Tools the answer cannot be right without. Checked as a subset, so
    #: an agent taking a longer route still passes.
    needs_tools: tuple[str, ...] = ()

    #: Tools that answering this correctly should NOT require. Catches an
    #: agent reaching for the whole ledger when a precise tool exists.
    avoids_tools: tuple[str, ...] = ()

    #: A follow-up asked in the same conversation, to test that the thread
    #: is carried. Scored as its own case.
    follow_up: str = ""
    follow_up_truth: Callable[[Any], Decimal | None] | None = None

    #: Cases that should produce an empty/absent answer rather than a
    #: figure - "nothing matched" is a correct answer and inventing one is
    #: the failure being tested for.
    expect_zero: bool = False

    #: Words the answer has to contain to count as having addressed the
    #: question. For a question with no single right figure, this is what
    #: stops "I could not work it out" from scoring as a pass.
    must_mention: tuple[str, ...] = ()

    tags: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Ground truth, straight from the tables
# ---------------------------------------------------------------------------

def _scalar(db, sql: str, params: tuple = ()) -> Decimal | None:
    with db.connection() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    value = list(dict(row).values())[0]
    return None if value is None else Decimal(str(value))


def _spend_in(month: str, category: str = "") -> Callable[[Any], Decimal | None]:
    """Money out in one accounting month, optionally in one category.

    Refunds are netted off, because that is what "spending" means everywhere
    else in this app - the Overview, the budget, every report. Ground truth
    has to be independent of the query engine, which is why this is raw SQL;
    it does NOT get to invent its own definition of the word. It did, at
    first, counting expense debits and nothing else, and July came out 398
    higher than the app's figure. An agent answering correctly would have
    failed the case, which is the most expensive kind of wrong an eval can
    be: it sends someone to fix code that was right.
    """
    def truth(db):
        sql = ("SELECT COALESCE(SUM(CASE"
               "   WHEN direction = 'debit' AND flow_role = 'expense'"
               "        THEN CAST(amount AS NUMERIC)"
               "   WHEN direction = 'credit' AND flow_role = 'refund'"
               "        THEN -CAST(amount AS NUMERIC)"
               "   ELSE 0 END), 0) FROM transactions"
               " WHERE accounting_month = ?")
        params: tuple = (month,)
        if category:
            sql += " AND category = ?"
            params = (month, category)
        return _scalar(db, sql, params)
    return truth


def _income_in(month: str) -> Callable[[Any], Decimal | None]:
    """Earnings in one accounting month - credits the app judged to be
    income, which is not the same as every credit."""
    def truth(db):
        return _scalar(
            db,
            "SELECT COALESCE(SUM(CAST(amount AS NUMERIC)), 0) FROM transactions"
            " WHERE flow_role = 'income' AND direction = 'credit'"
            "   AND accounting_month = ?", (month,))
    return truth


def _count_accounts(kind: str) -> Callable[[Any], Decimal | None]:
    def truth(db):
        return _scalar(db, "SELECT COUNT(*) FROM accounts WHERE account_type = ?",
                       (kind,))
    return truth


def _largest_expense() -> Callable[[Any], Decimal | None]:
    def truth(db):
        return _scalar(
            db,
            "SELECT COALESCE(MAX(CAST(amount AS NUMERIC)), 0) FROM transactions"
            " WHERE direction = 'debit' AND flow_role = 'expense'")
    return truth


def _total_owed() -> Callable[[Any], Decimal | None]:
    def truth(db):
        return _scalar(
            db,
            "SELECT COALESCE(SUM(CAST(principal_outstanding AS NUMERIC)), 0)"
            "  FROM accounts WHERE account_type IN"
            "       ('home_loan', 'personal_loan', 'auto_loan')"
            "   AND COALESCE(principal_outstanding, '') <> ''")
    return truth


# ---------------------------------------------------------------------------
# The set
# ---------------------------------------------------------------------------

CASES: tuple[Case, ...] = (
    # ---- the basic shape: a figure, for a period, in a category ----------
    Case(
        key="fuel-one-month",
        question="How much did I spend on fuel in August 2026?",
        why="The simplest possible question, and the one that exposed two "
            "real bugs: arguments arriving empty, and a date range being "
            "silently discarded. If this regresses, everything dated is "
            "wrong.",
        truth=_spend_in("2026-08", "fuel"),
        needs_tools=("ledger_query",),
        follow_up="And in July?",
        follow_up_truth=_spend_in("2026-07", "fuel"),
        tags=("figure", "period", "follow-up"),
    ),
    Case(
        key="total-spend-month",
        question="What was my total spending in July 2026?",
        why="No category filter, so the answer depends entirely on the "
            "period being applied and on spend meaning `flow_role = "
            "expense` rather than every debit. Transfers and card "
            "settlements must not be counted.",
        truth=_spend_in("2026-07"),
        needs_tools=("ledger_query",),
        tags=("figure", "period"),
    ),
    Case(
        key="income-month",
        question="How much did I earn in August 2026?",
        why="The other side of the ledger. An agent that reports gross "
            "credits here is counting refunds and transfers as earnings.",
        truth=_income_in("2026-08"),
        tags=("figure", "period"),
    ),

    # ---- a question with no numeric answer --------------------------------
    Case(
        key="how-many-cards",
        question="How many credit cards do I have?",
        why="A count, not a sum, and it should come from `accounts` rather "
            "than an aggregation over transactions.",
        truth=_count_accounts("credit_card"),
        needs_tools=("accounts",),
        tags=("count",),
    ),

    # ---- the honest empty answer -----------------------------------------
    Case(
        key="nothing-matches",
        question="How much did I spend on scuba diving lessons in 2026?",
        why="There is no such category and no such merchant. The correct "
            "answer is that nothing matched - inventing a figure, or "
            "silently reporting the all-time total because a filter was "
            "dropped, is the failure this catches.",
        expect_zero=True,
        tags=("empty", "honesty"),
    ),

    # ---- reaching for the right tool --------------------------------------
    Case(
        key="largest-expense",
        question="What is the single largest thing I have ever spent money on?",
        why="One row, not a total. An agent that answers this with "
            "`ledger_query` has given a sum where a transaction was asked "
            "for.",
        truth=_largest_expense(),
        needs_tools=("search_transactions",),
        tags=("rows",),
    ),
    Case(
        key="what-do-i-owe",
        question="What do I owe in total across my loans?",
        why="`position` and `loans` exist precisely for this. Aggregating "
            "it out of transactions gives a different and wrong number, "
            "because a loan balance is not the sum of its payments. "
            "Scored on TOOL CHOICE, not on the figure, and deliberately: "
            "what the app considers owed is the Position total - attested "
            "balances rolled forward, plus bureau-reported debt nothing "
            "has adopted - and raw SQL over `accounts` is a different, "
            "smaller number. Asserting the SQL figure would fail the agent "
            "for being right; asserting the Position figure would test "
            "Position against itself. So this checks that it reached for "
            "the right tool and produced an answer, and leaves the "
            "arithmetic to test_position.py where it can be checked "
            "properly.",
        needs_tools=("position",),
        tags=("balances", "tool-choice"),
    ),

    # ---- ambiguity ---------------------------------------------------------
    Case(
        key="ambiguous-food",
        question="How much do I spend on food?",
        why="'Food' is not a category. Dining and groceries both are. The "
            "right behaviour is to answer both and say which is which, "
            "not to silently pick one - a single figure here is a wrong "
            "answer however close it is.",
        needs_tools=("ledger_query",),
        # Both by name, or the ambiguity was not handled - it was picked a
        # side of. Without this the case passed on "food spending cannot be
        # determined", which is neither of the two behaviours being judged.
        must_mention=("dining", "groceries"),
        tags=("ambiguity",),
    ),

    # ---- the thread --------------------------------------------------------
    Case(
        key="follow-up-pronoun",
        question="What was my biggest spending category last month?",
        why="The follow-up carries no noun at all. If the thread is not "
            "reaching the model, 'why is it so high' is unanswerable and "
            "the agent will either guess or ask what is meant.",
        follow_up="Why is it so high?",
        tags=("follow-up",),
    ),
)
