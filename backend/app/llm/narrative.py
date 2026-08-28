"""Turn computed analysis into prose.

The model receives a compact, already-computed brief and is asked to interpret
it. It is explicitly forbidden from producing new figures, because every number
in the brief has been derived from a reconciled ledger and a re-stated number
would silently lose that guarantee.

There is also a hard boundary on advice. This app can say "your EMIs are 43% of
take-home, which is above the 40% lenders typically look for" - that is
arithmetic against a published norm. It must not say "you should prepay the
loan instead of investing", which is personalized investment advice and depends
on facts the app cannot see. The system prompt enforces that line, and the
fallback narrative respects it too.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ..analytics.engine import AnalysisResult
from .client import LLMClient, NARRATIVE_MODEL, get_client

log = logging.getLogger(__name__)

SYSTEM = """You are a financial analyst writing a plain-language summary of one
person's finances. You are given figures that have already been computed from
their reconciled bank statements.

Absolute rules:
1. NEVER compute, restate, estimate or round a number that is not in the brief.
   Quote figures exactly as given. If something isn't in the brief, say it isn't
   available rather than inferring it.
2. Do NOT give personalized investment advice. You may state factual
   comparisons against widely published norms (e.g. "EMIs above 40% of take-home
   is what lenders generally treat as stretched"). You may lay out the mechanical
   trade-offs of an option. You must not tell them what to buy, sell, or prioritise
   with their money, and you must not predict market returns.
3. Be specific and concrete. "You spent 47,200 on dining across 96 orders,
   which is 14% of your spending" beats "your dining spending is high".
4. Lead with what is most consequential for this person, not with a template.
5. If the data has caveats (unreconciled files, missing accounts, short history),
   say so plainly in the caveats field rather than burying it.
6. Write for an intelligent adult who is not a finance professional. No jargon
   without explanation, no lecturing, no moralising about their choices.

Return ONLY a JSON object with these keys:
{
  "headline": "one sentence, the single most important fact",
  "summary": "2-4 sentences of overall position",
  "key_findings": [{"title": "...", "detail": "...", "severity": "info|watch|urgent"}],
  "where_money_went": "2-3 sentences tracing income through to what remained",
  "observations": [{"title": "...", "detail": "...", "mechanism": "what would change if they acted on this"}],
  "forecast_note": "2-3 sentences on the projection and its limits",
  "caveats": ["..."]
}
Aim for 4-7 key_findings and 3-5 observations."""


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        # Two dp: the brief is for reading, and full precision adds noise.
        return float(round(obj, 2))
    if isinstance(obj, date):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "value"):
        return obj.value
    return str(obj)


def build_brief(
    analysis: AnalysisResult,
    loan_projections: list[Any],
    forecast: Any,
    recurring: list[Any],
    accounts: dict[str, Any],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the compact fact sheet handed to the model.

    Deliberately summarized rather than raw: sending 20,000 transactions would
    cost a fortune, blow the context window, and give the model room to
    hallucinate a total. Sending the computed aggregates gives it exactly the
    facts it needs to interpret and nothing to recompute.
    """
    brief: dict[str, Any] = {
        "period": {
            "start": analysis.period_start,
            "end": analysis.period_end,
            "months_covered": analysis.months_covered,
        },
        "totals": {
            "income": analysis.total_income,
            "spend": analysis.total_spend,
            "invested": analysis.total_invested,
            "net_savings": analysis.net_savings,
            "savings_rate_pct": analysis.savings_rate,
            "average_monthly_income": analysis.average_monthly_income,
            "average_monthly_spend": analysis.average_monthly_spend,
            "transaction_count": analysis.transaction_count,
        },
        "monthly": [
            {"month": m.month, "income": m.income, "spend": m.spend,
             "net": m.net, "savings_rate_pct": m.savings_rate}
            for m in analysis.monthly
        ],
        "spending_by_category": [
            {"category": c.category, "group": c.group, "total": c.total,
             "share_pct": c.share_pct, "count": c.transaction_count,
             "monthly_avg": c.monthly_average}
            for c in analysis.by_category[:18]
        ],
        "spending_by_group": analysis.by_group,
        "top_merchants": [
            {"merchant": m.merchant, "total": m.total, "count": m.count,
             "avg": m.average, "category": m.category}
            for m in analysis.top_merchants[:12]
        ],
        "income_sources": [
            {"source": s, "total": t, "count": n}
            for s, t, n in analysis.income_sources[:8]
        ],
        "salary_flow": [
            {"month": f.month, "salary": f.salary_amount,
             "left_over": f.left_over,
             "days_to_half_spent": f.days_to_half_spent,
             "top_allocations": f.allocations[:6]}
            for f in analysis.salary_flows[-6:]
        ],
        "net_worth": analysis.net_worth,
        "accounts": [
            {"name": a.display_name(), "type": a.account_type,
             "balance": a.principal_outstanding,
             "rate": a.interest_rate, "emi": a.emi_amount}
            for a in accounts.values()
        ],
        "loans": [
            {"label": p.label, "outstanding": p.outstanding,
             "rate_pct": p.annual_rate, "emi": p.emi,
             "months_remaining": p.months_remaining,
             "payoff_date": p.payoff_date,
             "total_interest_remaining": p.total_interest_remaining,
             "next_emi_interest_share_pct": round(p.next_interest_share * 100, 1)}
            for p in loan_projections
        ],
        "recurring_commitments": [
            {"label": s.label, "category": s.category, "cadence": s.cadence_name,
             "amount": s.median_amount, "monthly_equivalent": s.monthly_equivalent,
             "direction": s.direction, "active": s.is_active}
            for s in recurring[:20]
        ],
        "largest_expenses": [
            {"date": t.txn_date, "description": t.raw_description[:70],
             "amount": t.amount, "category": t.category}
            for t in analysis.largest_expenses[:10]
        ],
        "unusual_transactions": [
            {"date": t.txn_date, "description": t.raw_description[:70],
             "amount": t.amount, "why": why}
            for t, why in analysis.unusual[:8]
        ],
        "data_quality": data_quality,
    }

    if forecast is not None:
        brief["forecast"] = {
            "confidence": forecast.confidence,
            "commitment_ratio_pct": round(forecast.commitment_ratio * 100, 1),
            "runway_months": forecast.runway_months,
            "first_shortfall_month": forecast.first_shortfall_month,
            "assumptions": forecast.assumptions,
            "months": [
                {"month": m.month,
                 "committed_income": m.committed_income,
                 "committed_outflow": m.committed_outflow,
                 "discretionary_expected": m.discretionary_expected,
                 "net_expected": m.net_expected,
                 "closing_expected": m.closing_balance_expected,
                 "closing_low": m.closing_balance_low,
                 "closing_high": m.closing_balance_high}
                for m in forecast.months
            ],
        }

    return brief


def generate_narrative(
    brief: dict[str, Any],
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Ask the model to interpret the brief. Falls back to a computed summary."""
    client = client or get_client()

    if not client.available:
        return _fallback(brief, "No ANTHROPIC_API_KEY configured, so this "
                                "summary was generated directly from the "
                                "computed figures rather than written up.")

    payload = json.dumps(brief, default=_json_default, indent=1)
    try:
        narrative = client.complete_json(
            f"Here is the computed financial brief:\n\n{payload}\n\n"
            f"Write the analysis as specified.",
            system=SYSTEM,
            max_tokens=6000,
            model=NARRATIVE_MODEL,
        )
    except Exception as exc:
        log.warning("narrative generation failed: %s", exc)
        return _fallback(brief, f"The written summary could not be generated "
                                f"({type(exc).__name__}). All figures below are "
                                f"computed and unaffected.")

    if not isinstance(narrative, dict):
        return _fallback(brief, "The model returned an unexpected shape.")

    narrative.setdefault("caveats", [])
    narrative["caveats"] = list(narrative["caveats"]) + brief.get(
        "data_quality", {}
    ).get("notes", [])
    narrative["generated_by"] = "llm"
    return narrative


def _fallback(brief: dict[str, Any], reason: str) -> dict[str, Any]:
    """A useful summary with no model involved.

    Everything here is assembled from figures already computed, so the app
    remains fully functional offline - only the prose quality drops.
    """
    totals = brief.get("totals", {})
    categories = brief.get("spending_by_category", [])
    loans = brief.get("loans", [])
    period = brief.get("period", {})

    income = totals.get("income", 0)
    spend = totals.get("spend", 0)
    rate = totals.get("savings_rate_pct", 0)

    findings = []
    if categories:
        top = categories[0]
        findings.append({
            "title": f"Largest spending category: {top['category'].replace('_', ' ')}",
            "detail": f"{top['total']:,.0f} across {top['count']} transactions, "
                      f"{top['share_pct']}% of all spending "
                      f"(about {top['monthly_avg']:,.0f} a month).",
            "severity": "info",
        })
    if loans:
        total_interest = sum(l.get("total_interest_remaining", 0) for l in loans)
        findings.append({
            "title": f"{len(loans)} active loan(s)",
            "detail": f"{total_interest:,.0f} of interest remains payable over the "
                      f"remaining terms at the current EMIs.",
            "severity": "watch" if total_interest > income else "info",
        })
    if rate < 10:
        findings.append({
            "title": f"Savings rate is {rate}%",
            "detail": f"Of {income:,.0f} received, {spend:,.0f} was spent, "
                      f"leaving {totals.get('net_savings', 0):,.0f}.",
            "severity": "watch",
        })

    return {
        "headline": (
            f"Across {period.get('months_covered', 0)} months you received "
            f"{income:,.0f} and spent {spend:,.0f}, a savings rate of {rate}%."
        ),
        "summary": (
            f"This covers {totals.get('transaction_count', 0)} transactions from "
            f"{period.get('start')} to {period.get('end')}. "
            f"Average monthly income was {totals.get('average_monthly_income', 0):,.0f} "
            f"against average monthly spending of "
            f"{totals.get('average_monthly_spend', 0):,.0f}."
        ),
        "key_findings": findings,
        "where_money_went": _describe_flow(categories),
        "observations": [],
        "forecast_note": _describe_forecast(brief.get("forecast")),
        "caveats": [reason] + brief.get("data_quality", {}).get("notes", []),
        "generated_by": "computed",
    }


def _describe_flow(categories: list[dict[str, Any]]) -> str:
    if not categories:
        return "No categorized spending was available."
    parts = [
        f"{c['category'].replace('_', ' ')} {c['total']:,.0f} ({c['share_pct']}%)"
        for c in categories[:5]
    ]
    return "The largest destinations were: " + ", ".join(parts) + "."


def _describe_forecast(forecast: dict[str, Any] | None) -> str:
    if not forecast or not forecast.get("months"):
        return "Not enough history to project forward."
    first = forecast["months"][0]
    last = forecast["months"][-1]
    shortfall = forecast.get("first_shortfall_month")
    text = (
        f"Projecting {len(forecast['months'])} months forward at "
        f"{forecast.get('confidence')} confidence, the balance moves from "
        f"{first['closing_expected']:,.0f} to {last['closing_expected']:,.0f} "
        f"in the expected case."
    )
    if shortfall:
        text += f" The projection goes negative in {shortfall}."
    return text
