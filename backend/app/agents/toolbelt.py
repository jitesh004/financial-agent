"""The read-only tools an agent may call against the ledger.

Every tool is a whitelisted Python function over the user's own rows. There is
no tool that writes, no tool that takes SQL, and no tool that reaches outside
the tenant - `ledger_query` runs through `analytics.query`, which is a closed
registry of dimensions and measures precisely so that a query description
coming from somewhere untrusted can never become a query.

The other rule is that a tool returns FIGURES, computed in Decimal, never
prose. The model decides what to look at and what it means; it is not the
thing doing the arithmetic. That is what makes an agent's answer checkable:
the run keeps every call and every result, so any number in the answer can be
traced back to the tool that produced it.

Results are capped. A tool that can return thousands of rows returns the top
slice and says how many it left out, because an agent that fills its context
with the ledger has no room left to think about it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable

from ..analytics import periods
from ..analytics import query as q
from ..db import repository as repo
from ..models.schemas import (LOAN_TYPES, AccountType, Category, Direction,
                              FlowRole, Transaction)

log = logging.getLogger(__name__)

#: The most rows any single tool will hand back. Past this an agent is reading
#: the ledger rather than reasoning about it.
MAX_ROWS = 60

#: And the most transactions a text search will return.
MAX_SEARCH_ROWS = 40


@dataclass(frozen=True)
class Tool:
    name: str
    #: One sentence the model reads to decide whether it wants this.
    summary: str
    #: What the arguments are, in the same shape a JSON schema uses. Kept as a
    #: plain dict rather than a real schema object because it is only ever
    #: rendered into a prompt - the arguments themselves are validated by the
    #: function, which has to be defensive anyway.
    args: dict[str, str]
    run: Callable[..., Any]
    #: An example call, which is worth more than any amount of description
    #: for a tool with a structured argument.
    example: dict[str, Any] | None = None


def _money(value: Any) -> float | None:
    if value is None:
        return None
    return float(round(Decimal(str(value)), 2))


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _as_date(value: Any) -> date | None:
    """A YYYY-MM-DD string as a date, or None. A bad one is simply ignored:
    an agent that mistypes a bound should get a wider answer and a chance to
    narrow it, not a dead run."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# The ledger, through the query engine
# ---------------------------------------------------------------------------

def ledger_schema(db, **_: Any) -> dict[str, Any]:
    """Every dimension, measure, filter and preset `ledger_query` accepts."""
    raw = q.schema(db)
    # Trimmed hard. The full schema carries every account label, every month
    # and every category as picker options, which is a page of JSON the model
    # does not need to choose a dimension.
    return {
        "dimensions": [
            {"key": f["key"], "label": f["label"], "type": f["type"]}
            for f in raw["fields"] if f.get("groupable")
        ],
        "filterable": [f["key"] for f in raw["fields"] if f.get("filterable")],
        "measures": [
            {"key": m["key"], "label": m["label"], "aggs": m["aggs"]}
            for m in raw["measures"]
        ],
        "operators": list(q.OPERATORS),
        "date_presets": [p[0] for p in q.DATE_PRESETS],
        "categories": raw["options"].get("category", []),
        "accounts": raw["options"].get("account_id", []),
        "months": raw["options"].get("months", [])[:36],
    }


def ledger_query(db, spec: dict[str, Any] | None = None,
                 **kwargs: Any) -> dict[str, Any]:
    """Group and aggregate the ledger any way the registry allows."""
    spec = dict(spec or kwargs or {})
    spec.pop("_period_override", None)
    spec["limit"] = min(int(spec.get("limit") or MAX_ROWS), MAX_ROWS)
    try:
        result = q.run_query(db, spec)
    except q.QueryError as exc:
        # Handed back as a result rather than raised: a query naming a
        # dimension that does not exist is something the agent can fix on its
        # next turn, and killing the run over it wastes everything before it.
        return {"error": str(exc),
                "hint": "Call ledger_schema for the keys that do exist."}
    return {
        "columns": [{"key": c["key"], "label": c["label"], "role": c["role"]}
                    for c in result["columns"]],
        "rows": result["rows"],
        "row_count": result["row_count"],
        "truncated": result["truncated"],
        "range": result["range"],
    }


def search_transactions(db, text: str = "", category: str | None = None,
                        account_id: str | None = None,
                        direction: str | None = None,
                        min_amount: float | None = None,
                        start: str | None = None, end: str | None = None,
                        limit: int = MAX_SEARCH_ROWS, **_: Any) -> dict[str, Any]:
    """Individual rows, for when the aggregate is not the answer."""
    limit = min(int(limit or MAX_SEARCH_ROWS), MAX_SEARCH_ROWS)
    # The repository filters on structured fields; text and direction are
    # applied here, over rows already narrowed by whatever else was given.
    rows = repo.get_transactions(
        db, category=category or None, account_id=account_id or None,
        start=_as_date(start), end=_as_date(end),
        sort_by="amount", sort_dir="desc")

    needle = (text or "").strip().upper()
    floor = Decimal(str(min_amount)) if min_amount else None
    kept: list[Transaction] = []
    for txn in rows:
        if needle and needle not in " ".join(filter(None, (
                txn.raw_description, txn.normalized_description,
                txn.merchant))).upper():
            continue
        if direction and txn.direction.value != str(direction).lower():
            continue
        if floor is not None and txn.amount < floor:
            continue
        kept.append(txn)
    matched = len(kept)
    kept = kept[:limit]
    return {
        "transactions": [
            {"date": _iso(t.txn_date), "description": (t.raw_description or "")[:90],
             "merchant": t.merchant, "amount": _money(t.amount),
             "direction": t.direction.value, "category": t.category,
             "account_id": t.account_id, "excluded": t.excluded,
             "needs_review": t.needs_review}
            for t in kept
        ],
        "returned": len(kept),
        "matched": matched,
        "note": ("Sorted largest first, and this is only the top "
                 f"{len(kept)} of {matched} matches - use ledger_query for "
                 f"totals." if matched > len(kept) else ""),
    }


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def accounts(db, **_: Any) -> dict[str, Any]:
    """Every account, with what it holds or what is owed on it."""
    out = []
    for account in repo.get_accounts(db):
        out.append({
            "id": account.id,
            "name": account.display_name(),
            "type": account.account_type.value,
            "institution": account.institution,
            "balance": _money(account.current_balance),
            "credit_limit": _money(account.credit_limit),
            "outstanding": _money(account.principal_outstanding),
            "interest_rate_pct": _money(account.interest_rate),
            "emi": _money(account.emi_amount),
            "is_liability": account.account_type.value in {
                t.value for t in (AccountType.CREDIT_CARD, *LOAN_TYPES)},
        })
    return {"accounts": out, "count": len(out)}


def _ledger(db) -> tuple[list[Transaction], dict[str, Any]]:
    rows = repo.get_transactions(db)
    accounts_by_id = {a.id: a for a in repo.get_accounts(db) if a.id}
    return rows, accounts_by_id


#: Accounts whose balance is money the user can actually spend this week.
#: Investments are deliberately not here: selling one is a decision, usually
#: made at a bad moment, and counting it as available cash is what turns a
#: liquidity problem into an invisible one.
LIQUID_TYPES = {AccountType.SAVINGS, AccountType.CURRENT, AccountType.WALLET}


def _liquid_balance(rows: list[Transaction],
                    accounts_by_id: dict[str, Any]) -> dict[str, Any]:
    """What is available to spend, and which accounts that figure knows about.

    "Unknown" and "zero" have to be told apart here, and summing with a zero
    default does not. A statement that prints no running balance leaves
    `current_balance` empty, so a person with two lakh in the bank came out
    at a liquid balance of 0 - which made the runway 0 months and put the
    cashflow projection underwater on day one. An agent reading that would
    tell somebody solvent that they are broke.

    So a missing balance is recovered the way analytics.engine recovers one -
    from the last running balance the statement printed - and if it still is
    not knowable the account is NAMED rather than counted as empty.
    """
    total = Decimal("0")
    known: list[str] = []
    unknown: list[str] = []
    from_statement: list[str] = []

    for account_id, account in accounts_by_id.items():
        if account.account_type not in LIQUID_TYPES:
            continue
        balance = account.current_balance
        if balance is None:
            with_balance = [t for t in rows
                            if t.account_id == account_id
                            and t.balance_after is not None and not t.excluded]
            if with_balance:
                balance = max(with_balance,
                              key=lambda t: t.txn_date).balance_after
                from_statement.append(account.display_name())
        if balance is None:
            unknown.append(account.display_name())
            continue
        total += balance
        known.append(account.display_name())

    return {
        "balance": total if known else None,
        "known": known,
        "unknown": unknown,
        "basis": ("declared balances" if not from_statement
                  else "declared balances, plus the last running balance "
                       "printed on the statement for "
                       + ", ".join(from_statement)),
    }


def analysis(db, period: dict[str, Any] | None = None,
             **_: Any) -> dict[str, Any]:
    """Income, spending and what was left, for a window."""
    from ..analytics.engine import analyze

    rows, accounts_by_id = _ledger(db)
    window = periods.resolve_period(period or {"preset": "last_12m"})
    scoped = periods.filter_transactions(rows, window)
    result = analyze(scoped, accounts_by_id)
    return {
        "period": window.as_json(),
        "totals": {
            "income": _money(result.total_income),
            "spend": _money(result.total_spend),
            "gross_spend": _money(result.gross_spend),
            "invested": _money(result.total_invested),
            "net_savings": _money(result.net_savings),
            "savings_rate_pct": result.savings_rate,
            "average_monthly_income": _money(result.average_monthly_income),
            "average_monthly_spend": _money(result.average_monthly_spend),
            "transactions": result.transaction_count,
        },
        "monthly": [
            {"month": m.month, "income": _money(m.income),
             "spend": _money(m.spend), "net": _money(m.net),
             "savings_rate_pct": m.savings_rate}
            for m in result.monthly
        ],
        "by_category": [
            {"category": c.category, "group": c.group, "total": _money(c.total),
             "share_pct": c.share_pct, "count": c.transaction_count,
             "monthly_avg": _money(c.monthly_average)}
            for c in result.by_category[:24]
        ],
        "top_merchants": [
            {"merchant": m.merchant, "total": _money(m.total),
             "count": m.count, "avg": _money(m.average), "category": m.category}
            for m in result.top_merchants[:15]
        ],
        "net_worth": {k: _money(v) for k, v in result.net_worth.items()},
        "net_worth_as_of": _iso(result.net_worth_as_of),
        "uncategorized_total": _money(result.uncategorized_total),
        "notes": result.notes,
    }


def budget(db, period: dict[str, Any] | None = None,
           **_: Any) -> dict[str, Any]:
    """What a month costs: what is committed, and what actually varies."""
    from ..analytics.budget import analyse_budget
    from ..analytics.loans import project_loan
    from ..analytics.recurring import detect_recurring

    rows, accounts_by_id = _ledger(db)
    series = detect_recurring(rows)
    projections = [p for p in (
        project_loan(a, [t for t in rows if t.account_id == aid])
        for aid, a in accounts_by_id.items()) if p]
    window = periods.resolve_period(period or {"preset": "last_6m"})
    result = analyse_budget(rows, series, period=window, loans=projections,
                            accounts=accounts_by_id)
    return {
        "period": window.as_json(),
        "months": result.months,
        "income_typical": _money(result.income_typical),
        "committed_debt": _money(result.committed_debt),
        "committed_spending": _money(result.committed_spending),
        "committed_saving": _money(result.committed_saving),
        "variable_typical": _money(result.variable_typical),
        "monthly_cost": _money(result.monthly_cost),
        "headroom": _money(result.headroom),
        "committed_ratio_pct": result.committed_ratio,
        "commitments": [
            {"label": c.label, "category": c.category, "kind": c.kind,
             "monthly": _money(c.monthly), "cadence": c.cadence,
             "months_seen": c.months_seen, "ends_on": _iso(c.ends_on),
             "months_left": c.months_left, "next_expected": _iso(c.next_expected),
             "confidence": c.confidence}
            for c in result.commitments[:MAX_ROWS]
        ],
        "variable": [
            {"category": v.category, "group": v.group,
             "typical_monthly": _money(v.typical_monthly),
             "low": _money(v.low_monthly), "high": _money(v.high_monthly),
             "total": _money(v.total), "months_seen": v.months_seen,
             "every_month": v.every_month, "count": v.transaction_count}
            for v in result.variable[:MAX_ROWS]
        ],
        "notes": result.notes,
    }


def recurring(db, include_ended: bool = False, **_: Any) -> dict[str, Any]:
    """Every detected commitment, with why the detector believes it."""
    from ..analytics.recurring import detect_recurring

    rows, _accounts = _ledger(db)
    series = detect_recurring(rows)
    if not include_ended:
        series = [s for s in series if s.is_active]
    return {
        "series": [
            {"id": s.id, "label": s.label, "category": s.category,
             "direction": s.direction.value, "amount": _money(s.median_amount),
             "monthly_equivalent": _money(s.monthly_equivalent),
             "cadence": s.cadence_name, "occurrences": s.occurrences,
             "first_seen": _iso(s.first_seen), "last_seen": _iso(s.last_seen),
             "next_expected": _iso(s.next_expected), "status": s.status,
             "confidence": s.confidence, "coverage": s.coverage,
             "missed": s.missed, "amount_trend": s.amount_trend,
             "was": _money(s.lifetime_median), "changed_on": _iso(s.changed_on),
             "why": s.evidence}
            for s in series[:MAX_ROWS]
        ],
        "count": len(series),
    }


# ---------------------------------------------------------------------------
# Debt
# ---------------------------------------------------------------------------

def _projections(db):
    from ..analytics.loans import project_loan

    rows, accounts_by_id = _ledger(db)
    out = []
    for account_id, account in accounts_by_id.items():
        projection = project_loan(
            account, [t for t in rows if t.account_id == account_id])
        if projection:
            out.append(projection)
    return out


def loans(db, **_: Any) -> dict[str, Any]:
    """Every loan, priced out to the last instalment."""
    return {
        "loans": [
            {"account_id": p.account_id, "label": p.label,
             "outstanding": _money(p.outstanding),
             "annual_rate_pct": _money(p.annual_rate), "emi": _money(p.emi),
             "months_remaining": p.months_remaining,
             "payoff_date": _iso(p.payoff_date),
             "total_interest_remaining": _money(p.total_interest_remaining),
             "total_payable_remaining": _money(p.total_payable_remaining),
             "next_emi_interest_share_pct": round(p.next_interest_share * 100, 1),
             "warnings": p.warnings}
            for p in _projections(db)
        ],
    }


def simulate_prepayment(db, account_id: str | None = None,
                        lump_sum: float = 0, extra_monthly: float = 0,
                        **_: Any) -> dict[str, Any]:
    """What paying more would do to a loan - in months and rupees.

    Closed-form, not modelled: a loan's future is fully determined by
    principal, rate and instalment, so there is nothing here to predict.
    Reported as the mechanical consequence and never as a recommendation -
    what else that money could do is not something this app can see.
    """
    from ..analytics.loans import build_schedule, months_to_payoff

    found = [p for p in _projections(db)
             if not account_id or p.account_id == account_id]
    if not found:
        return {"error": "No loan matches that account_id.",
                "hint": "Call loans for the account ids that exist."}

    lump = Decimal(str(lump_sum or 0))
    extra = Decimal(str(extra_monthly or 0))
    if lump <= 0 and extra <= 0:
        return {"error": "Pass lump_sum, extra_monthly, or both."}

    out = []
    for projection in found:
        balance = projection.outstanding - lump
        emi = projection.emi + extra
        if balance <= 0:
            out.append({
                "account_id": projection.account_id, "label": projection.label,
                "closes_the_loan": True,
                "months_saved": projection.months_remaining,
                "interest_saved": _money(projection.total_interest_remaining),
            })
            continue
        months = months_to_payoff(balance, projection.annual_rate, emi)
        schedule = build_schedule(balance, projection.annual_rate, emi,
                                  date.today())
        interest = sum((r.interest for r in schedule), Decimal("0"))
        out.append({
            "account_id": projection.account_id,
            "label": projection.label,
            "lump_sum": _money(lump), "extra_monthly": _money(extra),
            "closes_the_loan": False,
            "months_remaining_now": projection.months_remaining,
            "months_remaining_after": months,
            "months_saved": (projection.months_remaining - months
                             if months is not None else None),
            "interest_now": _money(projection.total_interest_remaining),
            "interest_after": _money(interest),
            "interest_saved": _money(
                projection.total_interest_remaining - interest),
            "new_payoff_date": _iso(schedule[-1].when) if schedule else None,
        })
    return {"scenarios": out}


# ---------------------------------------------------------------------------
# Looking forward
# ---------------------------------------------------------------------------

def cashflow_forecast(db, horizon_days: int = 90, **_: Any) -> dict[str, Any]:
    """A day-by-day balance projection from the commitments already known.

    The dated half of a forecast, which the monthly one cannot give: a month
    that ends comfortably can still be short on the 4th, because the rent and
    the EMI both leave before the salary arrives. That is the shortfall
    somebody actually experiences, and it is invisible in a monthly total.
    """
    from ..analytics.recurring import detect_recurring, upcoming

    horizon_days = max(7, min(int(horizon_days or 90), 365))
    rows, accounts_by_id = _ledger(db)
    series = detect_recurring(rows)
    liquid = _liquid_balance(rows, accounts_by_id)

    as_of = max((t.txn_date for t in rows if not t.excluded),
                default=date.today())
    events = upcoming(series, horizon_days=horizon_days, as_of=as_of)

    opening = liquid["balance"]
    # With no known balance the SHAPE of the window is still real - which
    # charges land when, and how far down the month goes before pay arrives -
    # so the projection runs from zero and every figure is reported as a
    # movement rather than as a balance. Running it from a zero BALANCE
    # instead would have the first EMI of the month put the account
    # underwater and report a shortfall that is an artifact of a statement
    # that printed no running balance.
    balance = opening if opening is not None else Decimal("0")
    key = "balance_after" if opening is not None else "net_change_by_then"

    timeline: list[dict[str, Any]] = []
    lowest: dict[str, Any] | None = None
    for when, one in events:
        delta = (one.median_amount if one.direction == Direction.CREDIT
                 else -one.median_amount)
        balance += delta
        entry = {"date": _iso(when), "label": one.label,
                 "category": one.category, "amount": _money(delta),
                 key: _money(balance), "confidence": one.confidence}
        timeline.append(entry)
        if lowest is None or balance < Decimal(str(lowest[key])):
            lowest = entry

    shortfall = (next((e for e in timeline if (e[key] or 0) < 0), None)
                 if opening is not None else None)
    return {
        "as_of": _iso(as_of),
        "horizon_days": horizon_days,
        "opening_liquid_balance": _money(opening),
        "balance_known": opening is not None,
        "balance_basis": liquid["basis"],
        "accounts_without_a_balance": liquid["unknown"],
        "committed_events": timeline[:MAX_ROWS],
        "event_count": len(timeline),
        "lowest_point": lowest,
        "first_shortfall": shortfall,
        "note": (
            "Only the commitments the detector is sure of are dated here. "
            "Day-to-day spending is not in this projection - see budget() "
            "for what a month's variable side typically costs."
            + ("" if opening is not None else
               " No account reports a balance, so each row is the CUMULATIVE "
               "MOVEMENT from the start of the window, not a balance. The "
               "timing is real; the level is unknown, and you must not "
               "report a shortfall from it.")),
    }


def runway(db, **_: Any) -> dict[str, Any]:
    """How long the liquid balance lasts with no income at all.

    Two burn rates, because they answer different questions. The full one is
    what life costs today; the essential one strips out what a person would
    stop paying in the month they lost their income, which is the number that
    decides how long they actually have.
    """
    from ..analytics.budget import analyse_budget
    from ..analytics.recurring import detect_recurring

    rows, accounts_by_id = _ledger(db)
    series = detect_recurring(rows)
    window = periods.resolve_period({"preset": "last_6m"})
    result = analyse_budget(rows, series, period=window,
                            accounts=accounts_by_id)

    liquid = _liquid_balance(rows, accounts_by_id)
    balance = liquid["balance"]
    #: What cannot simply be stopped: debt service, and the categories a
    #: person still has to pay for. Discretionary spending is excluded on
    #: purpose - it is the part that would actually be cut.
    essential_categories = {
        Category.RENT, Category.UTILITIES, Category.GROCERIES,
        Category.HEALTHCARE, Category.INSURANCE, Category.EDUCATION,
        Category.TRANSPORT, Category.FUEL,
    }
    essential_variable = sum(
        (v.typical_monthly for v in result.variable
         if v.category in essential_categories), Decimal("0"))
    essential = result.committed_debt + essential_variable + sum(
        (c.monthly for c in result.commitments
         if c.kind == "spending" and c.category in essential_categories),
        Decimal("0"))

    full = result.monthly_cost

    def months(cost: Decimal) -> float | None:
        # None rather than 0 when the balance is unknown. "You have no
        # runway" and "no account reports a balance" are opposite claims,
        # and a zero default turns the second into the first.
        if balance is None or cost <= 0:
            return None
        return round(float(balance / cost), 1)

    return {
        "liquid_balance": _money(balance),
        "balance_known": balance is not None,
        "balance_basis": liquid["basis"],
        "accounts_counted": liquid["known"],
        "accounts_without_a_balance": liquid["unknown"],
        "full_monthly_cost": _money(full),
        "essential_monthly_cost": _money(essential),
        "months_at_full_cost": months(full),
        "months_at_essential_cost": months(essential),
        "committed_debt_monthly": _money(result.committed_debt),
        "income_typical": _money(result.income_typical),
        "essential_categories": sorted(essential_categories),
        "note": (
            "Liquid means savings, current and wallet balances only - "
            "investments are not counted, because selling them is a decision "
            "rather than a balance."
            + ("" if balance is not None else
               " No liquid account reports a balance, so the runway cannot be "
               "computed at all - the monthly costs below are real, the "
               "number of months is not available. Say so rather than "
               "reporting zero.")),
    }


# ---------------------------------------------------------------------------
# Outside the bank statements
# ---------------------------------------------------------------------------

def position(db, **_: Any) -> dict[str, Any]:
    """What the user has confirmed is true, aged to today.

    The most authoritative thing an agent can read, and the only source that
    can carry a debt no statement mentions. Every figure here was reviewed by
    the person whose money it is, on a date that travels with it, and rolled
    forward from that date by the same amortization the Debt tab uses - so an
    outstanding balance reviewed three months ago comes back three
    instalments lighter rather than three months stale.

    `unaccounted.bureau` is the one to read first. Those are credit accounts a
    lender has reported and the position does not cover, which means every
    total an agent computes is short by whatever they hold. An answer about
    somebody's debt that ignores a loan sitting in that list is a confidently
    wrong answer.
    """
    from ..analytics import position as position_mod

    reports = repo.get_bureau_reports(db)
    bureau_accounts = (repo.get_bureau_accounts(db, reports[0]["id"])
                       if reports else [])
    built = position_mod.build(
        repo.get_position_items(db), repo.get_accounts(db), bureau_accounts)
    items = built["items"]
    return {
        "as_of": built["as_of"],
        "reviewed": bool(items),
        "totals": built["totals"],
        "items": [
            {k: v for k, v in item.items()
             # Trimmed to what an agent reasons about. The full row carries
             # the attested baseline and the observed figure side by side,
             # which is a screen's job rather than a prompt's.
             if k in {"id", "kind", "label", "institution", "outstanding",
                      "emi", "interest_rate", "months_remaining",
                      "payoff_date", "total_interest_remaining",
                      "credit_limit", "utilisation_pct", "next_due_on",
                      "days_to_due", "min_due", "reviewed_on", "stale",
                      "basis", "drift"}}
            for item in items[:MAX_ROWS]
        ],
        "unaccounted": built["unaccounted"],
        "needs_attention": built["needs_attention"],
        "note": (
            "Nothing has been reviewed yet, so there is no attested position "
            "- fall back to accounts() and loans(), and say in your caveats "
            "that the picture is only what the imported statements cover."
            if not items else
            "Reviewed figures, rolled forward from the date each was "
            "confirmed. Where `drift` is set, the statements disagree with "
            "the roll-forward and you should say so rather than picking one."),
    }


def credit_report(db, **_: Any) -> dict[str, Any]:
    """The latest bureau report, if one has been imported."""
    reports = repo.get_bureau_reports(db)
    if not reports:
        return {"available": False,
                "note": "No credit bureau report has been imported."}
    latest = reports[0]
    accounts_rows = repo.get_bureau_accounts(db, latest.get("id"))
    return {
        "available": True,
        "bureau": latest.get("bureau"),
        "score": latest.get("score"),
        "score_band": latest.get("score_band"),
        "as_of": latest.get("pulled_on"),
        "accounts": [
            {"lender": a.get("lender"), "type": a.get("account_type"),
             "status": a.get("status"), "sanctioned": a.get("sanctioned"),
             "balance": a.get("current_balance"), "emi": a.get("emi_amount"),
             "overdue": a.get("overdue"), "credit_limit": a.get("credit_limit"),
             "opened": a.get("opened_on"), "worst_dpd": a.get("worst_dpd"),
             # Whether this bureau line has been tied to a statement. An
             # agent that cannot see this will total up the loans it can
             # read and state the figure as if it were complete - which is
             # exactly wrong when a fourth loan sits here unmatched.
             "matched_account_id": a.get("account_id"),
             "match_status": a.get("match_status")}
            for a in accounts_rows[:MAX_ROWS]
        ],
        "unmatched_open_accounts": [
            {"lender": a.get("lender"), "type": a.get("account_type"),
             "balance": a.get("current_balance"), "emi": a.get("emi_amount")}
            for a in accounts_rows
            if not a.get("account_id") and (a.get("status") or "open") == "open"
        ],
        "note": "An entry under `unmatched_open_accounts` is a live credit "
                "account no imported statement covers. Any total that leaves "
                "it out is short, and you must say so.",
    }


def holdings(db, **_: Any) -> dict[str, Any]:
    """What is invested, from the imported portfolio statements."""
    try:
        from ..api import wealth_routes
        payload = wealth_routes.portfolio()
    except Exception as exc:  # pragma: no cover - a missing import is not fatal
        log.debug("portfolio unavailable: %s", exc)
        return {"available": False, "note": str(exc)}
    holdings_rows = payload.get("holdings") or []
    return {
        "available": bool(holdings_rows),
        "totals": payload.get("totals"),
        "holdings": holdings_rows[:MAX_ROWS],
        "count": len(holdings_rows),
    }


def data_quality(db, **_: Any) -> dict[str, Any]:
    """How much of the picture is actually there.

    Every agent needs this to caveat honestly. An answer about a person's
    subscriptions is worth very little if half their card statements are
    missing, and the difference between "you spend nothing on dining" and "no
    statement covering your dining card has been imported" is one this tool
    exists to make visible.
    """
    rows, accounts_by_id = _ledger(db)
    statements = repo.get_statements(db)
    dated = [t.txn_date for t in rows if not t.excluded]
    uncategorized = [t for t in rows if t.category == Category.UNCATEGORIZED]
    review = [t for t in rows if t.needs_review]

    per_account: list[dict[str, Any]] = []
    for account_id, account in accounts_by_id.items():
        account_rows = [t for t in rows if t.account_id == account_id]
        account_dates = [t.txn_date for t in account_rows if not t.excluded]
        per_account.append({
            "account": account.display_name(),
            "type": account.account_type.value,
            "transactions": len(account_rows),
            "first": _iso(min(account_dates)) if account_dates else None,
            "last": _iso(max(account_dates)) if account_dates else None,
        })

    return {
        "transactions": len(rows),
        "statements": len(statements),
        "accounts": len(accounts_by_id),
        "first_transaction": _iso(min(dated)) if dated else None,
        "last_transaction": _iso(max(dated)) if dated else None,
        "months_covered": len({t.strftime("%Y-%m") for t in dated}),
        "uncategorized_count": len(uncategorized),
        "uncategorized_total": _money(
            sum((t.amount for t in uncategorized), Decimal("0"))),
        "awaiting_review": len(review),
        "per_account": per_account,
    }


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------

TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("ledger_schema",
         "Every dimension, measure, filter and date preset ledger_query "
         "accepts. Call this before your first ledger_query.",
         {}, ledger_schema),
    Tool("ledger_query",
         "Group and aggregate the whole ledger. This is the general tool - "
         "any breakdown the other tools do not already give you.",
         {"spec": "{dimensions: [key], measures: [{field, agg}], "
                  "filters: [{field, op, value}], "
                  "date_range: {preset} or {start, end}, limit}"},
         ledger_query,
         example={"spec": {
             "dimensions": ["month", "category"],
             "measures": [{"field": "outflow", "agg": "sum"}],
             "filters": [{"field": "category", "op": "in",
                          "value": ["subscriptions", "utilities"]}],
             "date_range": {"preset": "last_12m"}}}),
    Tool("search_transactions",
         "Individual transactions matching a text or filter, largest first.",
         {"text": "substring of the description or merchant",
          "category": "optional category key",
          "account_id": "optional account",
          "direction": "'debit' or 'credit'",
          "min_amount": "number", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
         search_transactions,
         example={"text": "insurance", "direction": "debit"}),
    Tool("accounts", "Every account with its balance, limit, rate and EMI.",
         {}, accounts),
    Tool("analysis",
         "Income, spending, savings rate, category breakdown and top "
         "merchants for a window.",
         {"period": "{preset: all|this_month|last_month|last_3m|last_6m|"
                    "last_12m} or {start, end}"},
         analysis, example={"period": {"preset": "last_12m"}}),
    Tool("budget",
         "What a month costs: fixed commitments, what varies, and the "
         "headroom between them and income.",
         {"period": "same shape as analysis"}, budget,
         example={"period": {"preset": "last_6m"}}),
    Tool("recurring",
         "Every detected recurring charge, with its cadence, whether the "
         "price has changed, and why the detector believes it.",
         {"include_ended": "true to also list series that have stopped"},
         recurring),
    Tool("loans", "Every loan priced out: outstanding, rate, payoff date, "
                  "and the interest still to pay.",
         {}, loans),
    Tool("simulate_prepayment",
         "Exactly what a lump sum or a higher instalment does to a loan, in "
         "months and rupees saved.",
         {"account_id": "which loan, from loans()",
          "lump_sum": "one-off amount", "extra_monthly": "added to every EMI"},
         simulate_prepayment,
         example={"account_id": "loan-1", "lump_sum": 200000}),
    Tool("cashflow_forecast",
         "A dated, day-by-day projection of the known commitments against "
         "the current balance - where the low point falls and when.",
         {"horizon_days": "7 to 365, default 90"}, cashflow_forecast,
         example={"horizon_days": 90}),
    Tool("runway",
         "How many months the liquid balance covers with no income, at full "
         "cost and at essential cost.",
         {}, runway),
    Tool("position",
         "What the user has REVIEWED and confirmed is true - loans, cards, "
         "balances, EMIs, due dates - aged to today, plus the credit "
         "accounts nothing accounts for. The most authoritative source you "
         "have, and the only one that can see a debt no statement mentions.",
         {}, position),
    Tool("credit_report", "The imported bureau report: score and accounts.",
         {}, credit_report),
    Tool("holdings", "What is invested, from imported portfolio statements.",
         {}, holdings),
    Tool("data_quality",
         "How complete the ledger is - date coverage per account, "
         "uncategorised rows, rows awaiting review. Use it to caveat.",
         {}, data_quality),
]}


def describe(names: list[str]) -> list[dict[str, Any]]:
    """The tool catalogue as the prompt renders it."""
    out = []
    for name in names:
        tool = TOOLS.get(name)
        if tool is None:
            continue
        entry: dict[str, Any] = {"name": tool.name, "does": tool.summary}
        if tool.args:
            entry["args"] = tool.args
        if tool.example:
            entry["example"] = tool.example
        out.append(entry)
    return out


class ToolError(RuntimeError):
    """A tool call that could not even be attempted."""


def call(db, name: str, args: dict[str, Any] | None) -> Any:
    """Run one tool. Never raises for a bad argument - see `ledger_query`."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"There is no tool called {name!r}.",
                "available": sorted(TOOLS)}
    if not isinstance(args, dict):
        args = {}
    try:
        return tool.run(db, **args)
    except TypeError as exc:
        # A wrong argument name is the agent's mistake to fix, not a crash.
        return {"error": f"{name} does not take those arguments: {exc}",
                "args": tool.args}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("tool %s failed: %s", name, exc, exc_info=True)
        return {"error": f"{type(exc).__name__}: {exc}"}
