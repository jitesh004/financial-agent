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

from ..models.schemas import AccountType, Category, ConfidenceSource
from .fingerprint import stamp_fingerprints
from .overrides import OverrideReport, apply_overrides

log = logging.getLogger(__name__)


@dataclass
class EnrichmentResult:
    transactions: list = field(default_factory=list)
    accounts: dict = field(default_factory=dict)
    duplicate_count: int = 0
    transfer_report: Any = None
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
) -> EnrichmentResult:
    """Deduplicate, classify, apply user decisions, and analyse.

    `progress` is an optional callback taking a phase label, so the Gmail job
    can report where it is without this module knowing anything about jobs.
    """
    from ..analytics.engine import analyze
    from ..analytics.recurring import detect_recurring
    from ..analytics import forecast as forecast_mod
    from ..analytics import loans as loans_mod
    from ..categorize.rules import categorize_by_rules, fallback_category
    from ..reconcile.transfers import detect_transfers, find_duplicate_transactions

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

    # 2. Content identity, before anything wants to look a decision up by it.
    stamp_fingerprints(transactions, accounts)

    # 3. Transfers first - see the module docstring.
    phase("Matching transfers between accounts")
    result.transfer_report = detect_transfers(transactions, accounts)
    if result.transfer_report is not None:
        result.warnings.extend(result.transfer_report.notes)

    # 4. Deterministic rules. Skips anything already decided.
    phase("Categorizing")
    result.rules_settled = categorize_by_rules(transactions)

    # 5. The learned merchant cache, and the model for whatever is left. This
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

    # 6. Last-resort bucket. Without this an unrecognised CREDIT stays
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

    # 7. The user's own decisions, over the top of everything inferred above.
    phase("Applying your saved decisions")
    result.override_report = apply_overrides(db, transactions, accounts)
    result.warnings.extend(result.override_report.notes)

    if not run_analysis:
        return result

    # 8. Recurring series and 9. the analysis proper.
    phase("Detecting recurring commitments")
    result.recurring = detect_recurring(transactions)

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
