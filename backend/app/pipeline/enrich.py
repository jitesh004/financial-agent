"""The one enrichment pipeline.

Turning a pile of parsed rows into an analysed ledger involves nine steps that
have to happen in a specific order. That sequence used to be written out three
separate times - once in the LangGraph nodes, once in the Gmail route, once in
the incremental file-merge route - and they had already drifted apart:

  - The Gmail path accepted a `use_llm` flag and never read it, so the model
    was silently never consulted there no matter what the caller asked for.
  - The file-merge path had no `use_llm` parameter at all.
  - Neither of them consulted the learned merchant cache, so a category the
    user had corrected was re-guessed from scratch every time a statement
    arrived by that route.

Three copies of an ordering this subtle is three chances to get it wrong, and
each new accounting rule would have had to be added to all of them. There is
now one implementation and the three entry points are thin wrappers over it.

Order matters at two points in particular:

  - Transfer detection runs BEFORE categorization. Cross-account evidence that
    a debit is a credit-card payment is far stronger than any text pattern,
    and the rules layer then leaves those rows alone.
  - User overrides run LAST, after every automatic classifier. See
    pipeline.overrides for why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models.schemas import AccountType, Category, ConfidenceSource, Direction
from .fingerprint import stamp_fingerprints
from .overrides import OverrideReport, apply_overrides

log = logging.getLogger(__name__)


@dataclass
class EnrichmentResult:
    transactions: list = field(default_factory=list)
    accounts: dict = field(default_factory=dict)
    duplicate_count: int = 0
    transfer_report: Any = None
    settlement_report: Any = None
    recurring: list = field(default_factory=list)
    analysis: Any = None
    loan_projections: list = field(default_factory=list)
    forecast: Any = None
    override_report: OverrideReport = field(default_factory=OverrideReport)
    rules_settled: int = 0
    llm_settled: int = 0
    cache_settled: int = 0
    fell_back: int = 0
    warnings: list = field(default_factory=list)


def enrich_ledger(
    db,
    transactions: list,
    accounts: dict,
    *,
    use_llm: bool = False,
    holder_names: list[str] | None = None,
    horizon_months: int = 6,
    progress: Callable[[str], None] | None = None,
    run_analysis: bool = True,
    statements_by_account: dict | None = None,
    statement_periods: dict | None = None,
) -> EnrichmentResult:
    """Deduplicate, classify, apply user decisions, and analyse.

    `progress` is an optional callback taking a phase label, so the Gmail job
    can report where it is without this module knowing anything about jobs.
    """
    from ..analytics.engine import analyze
    from ..analytics.periods import assign_accounting_months
    from ..analytics.recurring import detect_recurring
    from ..analytics import forecast as forecast_mod
    from ..analytics import loans as loans_mod
    from ..categorize.rules import categorize_by_rules, fallback_category
    from ..reconcile.settlement import match_settlements
    from ..reconcile.transfers import (detect_reversals, detect_transfers,
                                       find_duplicate_transactions)

    def phase(label: str) -> None:
        if progress:
            progress(label)

    result = EnrichmentResult(accounts=accounts)
    holder_names = holder_names or []

    # 1. Duplicates, from statements whose periods overlap.
    phase("Removing duplicates")
    duplicates = find_duplicate_transactions(transactions)
    if duplicates:
        dupe_ids = {id(d) for d in duplicates}
        transactions = [t for t in transactions if id(t) not in dupe_ids]
        result.warnings.append(
            f"Removed {len(duplicates)} duplicate transaction(s) caused by "
            f"overlapping statement periods."
        )
    result.duplicate_count = len(duplicates)

    transactions.sort(key=lambda t: (t.txn_date, t.account_id or ""))
    result.transactions = transactions

    if not transactions:
        result.warnings.append("No usable transactions were produced by any file.")
        return result

    # 1b. Drop parser artifacts (header rows mistaken as transactions).
    #
    # These are not real transactions that the user might want to exclude
    # from their totals - they never happened at all. A credit card
    # statement's own column-header line ("PaymentDueDate Min.AmountDue
    # ChequeNo Date Bank Amount") gets misread as a data row often enough to
    # need this. Marking one `excluded = True` and keeping it in the ledger
    # was the wrong shape: it still occupied a real database row with a
    # garbage date and amount, still showed up in the Transactions table
    # (just with a badge), and anything downstream that forgot to check
    # `excluded` - `detect_recurring` did, until this same session - still
    # saw it as real activity. Three of these landing roughly a month apart
    # with a near-identical amount is exactly the shape recurring detection
    # looks for, and it showed up as its own bogus "recurring series"
    # sitting next to the user's actual subscriptions and EMIs. Dropped from
    # the ledger the same way an exact duplicate is, above - never
    # persisted, never displayed, never counted by anything.
    phase("Filtering parser artifacts")
    is_artifact = lambda t: (  # noqa: E731
        "Min.AmountDue" in (t.raw_description or "")
        or "PaymentDueDate" in (t.raw_description or "")
    )
    artifact_count = sum(1 for t in transactions if is_artifact(t))
    if artifact_count:
        transactions = [t for t in transactions if not is_artifact(t)]
        result.transactions = transactions
        result.warnings.append(f"Dropped {artifact_count} parser artifact(s).")

    # 2. Content identity, before anything wants to look a decision up by it.
    stamp_fingerprints(transactions, accounts)

    # 2b. Expand any split transaction into its parts, keyed by the
    # fingerprint just stamped above. Done this early so every later step -
    # transfers, categorization, analysis - sees ordinary rows and needs no
    # special case for "this one used to be several things".
    from .overrides import apply_splits
    transactions = apply_splits(db, transactions)
    result.transactions = transactions

    # 2c. Cancel a failed charge against its own same-account refund, before
    # transfer matching or categorization ever sees either leg - a gateway
    # that fails a charge and reverses it before a successful retry is not
    # three real transactions, and neither the failed debit nor its refund
    # should count as spending, income, or a candidate leg for anything else.
    phase("Cancelling reversed charges")
    reversed_count = detect_reversals(transactions)
    if reversed_count:
        result.warnings.append(
            f"{reversed_count} failed charge(s) were matched against their own "
            f"refund and excluded from every total.")

    # 3. Transfers first - see the module docstring.
    phase("Matching transfers between accounts")
    result.transfer_report = detect_transfers(transactions, accounts)
    if result.transfer_report is not None:
        result.warnings.extend(result.transfer_report.notes)

    # 4. Settlement matching — card bill payments, multi-leg CRED, etc.
    #    Runs after transfer detection so self-transfers are already claimed
    #    and won't be double-matched as settlements.
    phase("Matching card settlements")
    if statements_by_account is None:
        # Loaded here rather than left to the caller. Every one of the three
        # entry points omitted it, so it defaulted to None, so the coverage
        # check inside always answered "no coverage" and the distinction it
        # exists to draw - somebody else funded this, versus the funding
        # statement simply is not loaded - never actually happened.
        try:
            from ..db import repository as _repo
            statements_by_account = _repo.get_statement_periods_by_account(db)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("statement periods unavailable for coverage gate: %s", exc)
            statements_by_account = {}

    result.settlement_report = match_settlements(
        transactions, accounts, db,
        statements_by_account=statements_by_account,
    )
    if result.settlement_report is not None:
        result.warnings.extend(result.settlement_report.notes)

    # 5. Deterministic rules. Skips anything already decided.
    phase("Categorizing")
    result.rules_settled = categorize_by_rules(transactions)

    # 6. The learned merchant cache, and the model for whatever is left. This
    #    is also the only path that reads back a category the user corrected
    #    on a merchant, which is why it must run on EVERY route rather than
    #    just the upload one.
    remaining = [t for t in transactions if t.category == Category.UNCATEGORIZED]
    if remaining:
        phase("Consulting learned categories")
        try:
            from ..categorize.llm_categorizer import categorize_with_llm

            cached, modelled = categorize_with_llm(
                transactions, db=db, client=None if use_llm else _OfflineClient()
            )
            result.cache_settled, result.llm_settled = cached, modelled
        except Exception as exc:  # never let classification break ingestion
            log.warning("merchant categorization skipped: %s", exc)
            result.warnings.append(
                "Learned-category lookup was unavailable for this run.")

    # 7. Last-resort bucket. Without this an unrecognised CREDIT stays
    #    uncategorized, and since only income categories count towards
    #    money-in, an entire salary history can silently vanish from the
    #    dashboard.
    fell_back = 0
    for txn in transactions:
        if txn.category != Category.UNCATEGORIZED:
            continue
        guess = fallback_category(txn, holder_names)
        if guess != Category.UNCATEGORIZED:
            txn.category = guess
            txn.category_source = ConfidenceSource.DEFAULT
            txn.category_confidence = 0.3
            fell_back += 1
    result.fell_back = fell_back
    if fell_back:
        result.warnings.append(
            f"{fell_back} transaction(s) had no matching rule and were placed "
            f"in a default category.")

    # 8. The user's own decisions, over the top of everything inferred above.
    phase("Applying your saved decisions")
    result.override_report = apply_overrides(db, transactions, accounts)
    result.warnings.extend(result.override_report.notes)

    # 9. Stamp flow_role explicitly on every transaction. This runs AFTER
    #    overrides so the stored role includes user decisions. Derived roles
    #    still work for rows that predate this step, but having an explicit
    #    value in the DB makes queries and debugging far simpler.
    from ..models.schemas import FlowRole, derive_flow_role
    from ..categorize.rules import looks_like_person_payment

    for txn in transactions:
        if not txn.flow_role:
            txn.flow_role = derive_flow_role(txn).value

        # A credit carrying a spending category nets against that spending -
        # a returned purchase, a reversed fee. That is unambiguous when the
        # counterparty is a merchant ("AMAZON PAY ... -549" against an Amazon
        # charge), but not when it is a person: "UPI/MEERA NAIR/.../school
        # fee/" could be someone reimbursing a fee the user paid, or simply
        # money given to them. Netting is the more likely reading and is what
        # gets applied, but it is never applied silently.
        #
        # Note this only moves the income/expense SPLIT - net savings is
        # identical either way - so a wrong guess here is visible and cheap,
        # which is why a default plus review beats blocking the figure.
        if (txn.flow_role == FlowRole.REFUND.value
                and not txn.needs_review
                and looks_like_person_payment(txn)):
            txn.needs_review = True
            txn.review_reason = (
                "Credit from a person against a spending category - counted "
                "as money back rather than income. Confirm or flip."
            )

        # A card-bill row that never found its far leg is a silent default,
        # not a confirmed fact - detect_transfers only found candidates
        # within its own day-gap and account set, and a missing statement,
        # a payment made from an account not yet uploaded, or a partial
        # payment all look identical from here. Surfacing it is what turns
        # "assumed" into "confirmed or corrected" instead of a number the
        # user never gets a chance to check.
        if (txn.category == Category.CC_PAYMENT and not txn.is_internal_transfer
                and not txn.needs_review):
            txn.needs_review = True
            if txn.direction == Direction.CREDIT:
                txn.review_reason = (
                    "This card's payment-received entry has no matching bank "
                    "debit - either that statement is missing, or someone "
                    "else paid this bill. Counted as settling the card, not "
                    "as income."
                )
            else:
                txn.review_reason = (
                    "This card-bill payment has no matching entry on the "
                    "card's own statement, so it is counted as spending - "
                    "the only record available without that statement. "
                    "Connecting it would let this be excluded as a transfer "
                    "instead."
                )

    if not run_analysis:
        return result

    # 10. Period attribution — accounting_month, salary drift.
    phase("Assigning accounting periods")
    result.recurring = detect_recurring(transactions)

    # Periods come from the caller when it has them in hand, and from the
    # database only as a fallback. During an import the statements are not
    # written yet - they are saved after this runs - so a lookup here found
    # nothing for exactly the rows being imported, which are the only ones
    # whose attribution had not already been settled on a previous pass.
    periods = dict(statement_periods or {})
    if not periods:
        try:
            from ..db.database import get_db as _get_db
            from ..db import repository as _repo
            periods = _repo.get_statement_period_by_id(_get_db())
        except Exception:  # pragma: no cover - attribution is a refinement
            log.warning("could not load statement periods for attribution")
    assign_accounting_months(transactions, result.recurring, periods)

    # 11. The analysis proper.
    phase("Computing analysis")
    result.analysis = analyze(transactions, accounts)

    for account_id, account in accounts.items():
        account_txns = [t for t in transactions if t.account_id == account_id]
        projection = loans_mod.project_loan(account, account_txns)
        if projection:
            result.loan_projections.append(projection)

    opening = sum(
        (a.current_balance or 0) for a in accounts.values()
        if a.account_type in {AccountType.SAVINGS, AccountType.CURRENT,
                              AccountType.WALLET}
    )
    result.forecast = forecast_mod.forecast(
        monthly=result.analysis.monthly,
        series=result.recurring,
        opening_balance=opening,
        horizon_months=horizon_months,
        as_of=result.analysis.period_end,
    )
    return result


class _OfflineClient:
    """Stand-in that reports itself unavailable, so the cache is still read.

    `categorize_with_llm` looks up the learned merchant cache before it
    considers calling a model, and returns early if the cache settles
    everything. Passing this instead of skipping the call entirely means a
    run with the model disabled still benefits from every category the user
    has already corrected - which the two non-graph routes previously missed
    altogether.
    """

    available = False

    def complete_json(self, *args, **kwargs):  # pragma: no cover - never called
        raise RuntimeError("model disabled for this run")
