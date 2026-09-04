"""Every tunable number in one list, with the reason it is that number.

The values are IMPORTED, never retyped. A page that shows the user "transfers
pair within 4 days" while the matcher actually uses 7 is worse than showing
nothing - it is a confident wrong answer about the app's own behaviour, and
that is exactly the failure this whole rules package exists to prevent.

`why` is the sentence from the constant's own comment. Where the two ever
disagree the comment is right and this is stale, so keep them together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Threshold:
    #: Which part of the pipeline it governs.
    group: str
    #: Human name, as it should read on screen.
    name: str
    value: Any
    #: How to read the number: money, days, a ratio, a plain count.
    unit: str
    why: str
    #: Where it lives, so a developer can find it in one step.
    source: str


def all_thresholds() -> list[Threshold]:
    """Built on call rather than at import, so a circular import cannot make
    this module the reason the app fails to start."""
    from ..analytics import recurring
    from ..ingestion import extractors, passwords, txn_email
    from ..normalize import normalizer
    from ..reconcile import balance_check, bureau_match, settlement, transfers

    return [
        Threshold(
            "Reading", "Minimum columns for a table", extractors.MIN_TABLE_COLUMNS,
            "columns",
            "Rows narrower than this are page furniture, not data.",
            "ingestion/extractors.py"),
        Threshold(
            "Reading", "Minimum rows for a table", extractors.MIN_TABLE_ROWS,
            "rows", "", "ingestion/extractors.py"),
        Threshold(
            "Reading", "Dated rows before we stop looking", extractors.MIN_DATED_ROWS,
            "rows",
            "Below this, the slower stream extraction is worth running as well "
            "in case it finds a better table.",
            "ingestion/extractors.py"),
        Threshold(
            "Reading", "Rows that make a transaction table",
            normalizer.MIN_PARSEABLE_ROWS, "rows",
            "One is permitted: a genuinely quiet month has a single "
            "transaction, and a candidate must already map to date, "
            "description and amount to get this far.",
            "normalize/normalizer.py"),
        Threshold(
            "Reading", "Letterhead window", normalizer._HEAD_LINES, "lines",
            "Identity is read from the top of the document only. A savings "
            "statement's body says HOME LOAN EMI on every EMI row, and "
            "matching that relabels the whole account.",
            "normalize/normalizer.py"),
        Threshold(
            "Reading", "Footer window kept", normalizer._TAIL_LINES, "lines",
            "Totals sometimes sit in a footer, so the tail is kept and "
            "everything between is dropped.",
            "normalize/normalizer.py"),
        Threshold(
            "Reading", "Password candidates tried", passwords.MAX_CANDIDATES,
            "candidates",
            "A hard cap. These are published formats, not guesses - a "
            "brute-force space for an eight-character password is about "
            "10^14 - and the cap guarantees a large profile cannot turn this "
            "into a brute-forcer.",
            "ingestion/passwords.py"),

        Threshold(
            "Reconciliation", "Balance tolerance", balance_check.TOLERANCE,
            "money",
            "Statements round to paise and a few institutions publish totals "
            "to the rupee, so a sub-rupee gap is not evidence of a bug.",
            "reconcile/balance_check.py"),
        Threshold(
            "Reconciliation", "Material discrepancy",
            balance_check.MATERIAL_DISCREPANCY, "money",
            "Above this we stop calling it a rounding artefact and call it a "
            "broken parse.",
            "reconcile/balance_check.py"),

        Threshold(
            "Transfers", "Days apart", transfers.MAX_DAY_GAP, "days",
            "NEFT and IMPS settle same-day, but a weekend or a card issuer's "
            "posting lag can stretch it.",
            "reconcile/transfers.py"),
        Threshold(
            "Transfers", "Amount tolerance", transfers.AMOUNT_TOLERANCE, "money",
            "Transfers move an exact figure. Anything looser starts pairing "
            "unrelated transactions of similar size.",
            "reconcile/transfers.py"),
        Threshold(
            "Transfers", "Reversal window", transfers.REVERSAL_MAX_DAY_GAP,
            "days", "", "reconcile/transfers.py"),

        Threshold(
            "Card settlement", "Days apart", settlement.MAX_DAY_GAP, "days",
            "Wider than a plain transfer's window - card issuers post with "
            "more lag.",
            "reconcile/settlement.py"),
        Threshold(
            "Card settlement", "Residual allowed (absolute)",
            settlement.RESIDUAL_ABS_MAX, "money",
            "A multi-leg group need not tally to the paise; the remainder is "
            "usually a wallet top-up or rounding.",
            "reconcile/settlement.py"),
        Threshold(
            "Card settlement", "Residual allowed (share)",
            settlement.RESIDUAL_PCT_MAX, "ratio", "",
            "reconcile/settlement.py"),
        Threshold(
            "Card settlement", "Candidates considered", settlement.MAX_CANDIDATES,
            "count",
            "Subset-sum over a whole ledger will find coincidences, so the "
            "search is bounded to the nearest few by date.",
            "reconcile/settlement.py"),
        Threshold(
            "Card settlement", "Legs per side", settlement.MAX_LEGS_PER_SIDE,
            "count", "", "reconcile/settlement.py"),
        Threshold(
            "Card settlement", "Confidence floor", settlement.CONFIDENCE_FLOOR,
            "ratio",
            "Below this a group goes to review rather than applying itself. "
            "One leg matching one leg on an exact amount is strong evidence; "
            "five legs summing to a sixth is what a large ledger produces on "
            "its own.",
            "reconcile/settlement.py"),

        Threshold(
            "Credit bureau", "Auto-link confidence",
            bureau_match.AUTO_LINK_CONFIDENCE, "ratio",
            "Nothing auto-links on a lender's name alone.",
            "reconcile/bureau_match.py"),
        Threshold(
            "Credit bureau", "Suggest confidence",
            bureau_match.SUGGEST_CONFIDENCE, "ratio", "",
            "reconcile/bureau_match.py"),

        Threshold(
            "Recurring", "Occurrences needed", recurring.MIN_OCCURRENCES,
            "count", "", "analytics/recurring.py"),
        Threshold(
            "Recurring", "Confidence floor", recurring.MIN_CONFIDENCE, "ratio",
            "", "analytics/recurring.py"),
        Threshold(
            "Recurring", "Amount may vary by",
            recurring.AMOUNT_VARIANCE_TOLERANCE, "ratio",
            "The default. Utilities drift a lot month to month and a "
            "subscription barely moves, so the tolerance is set per category "
            "- an EMI is the same to the paisa, an electricity bill triples "
            "between March and June and is no less a fixed obligation.",
            "analytics/recurring.py"),
        Threshold(
            "Recurring", "Cadence fit floor", recurring.MIN_CADENCE_FIT,
            "ratio",
            "Every cadence is fitted to the dates and the best one wins. "
            "Below this, nothing fits: the charges keep no rhythm, whatever "
            "their amounts do.",
            "analytics/recurring.py"),
        Threshold(
            "Recurring", "Price change to notice", recurring.MIN_LEVEL_SHIFT,
            "ratio",
            "A jump smaller than this is noise. A bigger one - with both "
            "levels tight and every charge cleanly on one side of it - is a "
            "price rise, and the series survives it with the new level as "
            "the going-forward figure.",
            "analytics/recurring.py"),

        Threshold(
            "Alerts", "Supersede window", txn_email.SUPERSEDE_DAY_WINDOW, "days",
            "How close a statement row must be to an alert to be considered "
            "the same payment. The statement always wins: it is checked, the "
            "alert is not.",
            "ingestion/txn_email.py"),
    ]
