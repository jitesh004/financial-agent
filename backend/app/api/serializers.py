"""Convert domain objects to JSON-safe dicts for the API.

Money crosses the wire as a NUMBER, not a string, because the frontend needs to
chart it. That is safe in one direction only: the browser must never send a
computed amount back. Every figure the UI shows is read-only and re-derived
server-side, so a float round-trip in JavaScript can never corrupt the ledger.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..models.schemas import Account, Statement, Transaction


def num(value: Decimal | None) -> float | None:
    """Decimal -> float for JSON, rounded to paise."""
    if value is None:
        return None
    return float(round(Decimal(value), 2))


def jsonable(obj: Any) -> Any:
    """Recursively make any analytics object JSON-serializable."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Decimal):
        return num(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "value") and hasattr(obj, "name"):  # Enum
        return obj.value
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: jsonable(v) for k, v in asdict(obj).items()}
    if hasattr(obj, "model_dump"):
        return jsonable(obj.model_dump())
    return str(obj)


def account_json(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "institution": account.institution,
        "product_name": account.product_name,
        "account_type": account.account_type.value,
        "display_name": account.display_name(),
        "masked_number": account.account_number_masked,
        "holder_name": account.holder_name,
        "currency": account.currency,
        "is_liability": account.is_liability,
        "balance": num(account.balance),
        "current_balance": num(account.current_balance),
        "principal_outstanding": num(account.principal_outstanding),
        "interest_rate": num(account.interest_rate),
        "emi_amount": num(account.emi_amount),
        "credit_limit": num(account.credit_limit),
    }


def transaction_json(txn: Transaction) -> dict[str, Any]:
    return {
        "id": txn.id,
        "account_id": txn.account_id,
        "date": txn.txn_date.isoformat(),
        "description": txn.raw_description,
        "merchant": txn.merchant,
        "amount": num(txn.amount),
        "direction": txn.direction.value,
        "signed_amount": num(txn.signed_amount),
        "balance_after": num(txn.balance_after),
        "category": txn.category.value,
        "category_source": txn.category_source.value,
        "category_confidence": round(txn.category_confidence, 2),
        "is_internal_transfer": txn.is_internal_transfer,
        "is_mirror_leg": txn.is_mirror_leg,
        "is_spend": txn.is_spend,
        "recurring_series_id": txn.recurring_series_id,
        "reference": txn.reference,
    }


def statement_json(statement: Statement) -> dict[str, Any]:
    recon = statement.reconciliation
    return {
        "id": statement.id,
        "account_id": statement.account_id,
        "filename": statement.source_filename,
        "format": statement.source_format.value,
        "extractor": statement.extractor_used,
        "period_start": statement.period_start.isoformat() if statement.period_start else None,
        "period_end": statement.period_end.isoformat() if statement.period_end else None,
        "opening_balance": num(statement.opening_balance),
        "closing_balance": num(statement.closing_balance),
        "transaction_count": len(statement.transactions),
        "reconciliation": {
            "status": recon.status.value,
            "discrepancy": num(recon.discrepancy),
            "total_credits": num(recon.total_credits),
            "total_debits": num(recon.total_debits),
            "message": recon.message,
            "suspect_rows": recon.suspect_rows,
        } if recon else None,
        "warnings": statement.parse_warnings,
    }


def analysis_json(analysis: Any) -> dict[str, Any]:
    """Serialize an AnalysisResult, flattening the awkward tuple fields."""
    if analysis is None:
        return {}

    return {
        "period": {
            "start": analysis.period_start.isoformat() if analysis.period_start else None,
            "end": analysis.period_end.isoformat() if analysis.period_end else None,
            "months_covered": analysis.months_covered,
        },
        "totals": {
            "income": num(analysis.total_income),
            "spend": num(analysis.total_spend),
            "invested": num(analysis.total_invested),
            "net_savings": num(analysis.net_savings),
            "savings_rate": analysis.savings_rate,
            "average_monthly_income": num(analysis.average_monthly_income),
            "average_monthly_spend": num(analysis.average_monthly_spend),
            "transaction_count": analysis.transaction_count,
            "internal_transfer_total": num(analysis.internal_transfer_total),
        },
        "monthly": [
            {"month": m.month, "income": num(m.income), "spend": num(m.spend),
             "invested": num(m.invested), "total_outflow": num(m.total_outflow),
             "net": num(m.net), "savings_rate": m.savings_rate,
             "transaction_count": m.transaction_count}
            for m in analysis.monthly
        ],
        "monthly_by_category": {
            month: {c: num(v) for c, v in cats.items()}
            for month, cats in analysis.monthly_by_category.items()
        },
        "by_category": [
            {"category": c.category, "group": c.group, "total": num(c.total),
             "share_pct": c.share_pct, "count": c.transaction_count,
             "monthly_average": num(c.monthly_average),
             "largest_single": num(c.largest_single),
             "largest_description": c.largest_description}
            for c in analysis.by_category
        ],
        "by_group": {k: num(v) for k, v in analysis.by_group.items()},
        "top_merchants": [
            {"merchant": m.merchant, "total": num(m.total), "count": m.count,
             "average": num(m.average), "category": m.category,
             "first_seen": m.first_seen.isoformat(),
             "last_seen": m.last_seen.isoformat()}
            for m in analysis.top_merchants
        ],
        "income_sources": [
            {"source": s, "total": num(t), "count": n}
            for s, t, n in analysis.income_sources
        ],
        "salary_flows": [
            {"month": f.month, "salary_date": f.salary_date.isoformat(),
             "salary_amount": num(f.salary_amount),
             "left_over": num(f.left_over),
             "days_to_next_salary": f.days_to_next_salary,
             "days_to_half_spent": f.days_to_half_spent,
             "allocations": [
                 {"category": c, "amount": num(a), "pct_of_salary": p}
                 for c, a, p in f.allocations
             ]}
            for f in analysis.salary_flows
        ],
        "net_worth": {k: num(v) for k, v in analysis.net_worth.items()},
        "largest_expenses": [transaction_json(t) for t in analysis.largest_expenses],
        "unusual": [
            {**transaction_json(t), "reason": why} for t, why in analysis.unusual
        ],
        "uncategorized": {
            "count": analysis.uncategorized_count,
            "total": num(analysis.uncategorized_total),
        },
        "notes": analysis.notes,
    }


def loan_json(projection: Any) -> dict[str, Any]:
    return {
        "account_id": projection.account_id,
        "label": projection.label,
        "outstanding": num(projection.outstanding),
        "annual_rate": num(projection.annual_rate),
        "emi": num(projection.emi),
        "months_remaining": projection.months_remaining,
        "years_remaining": round(projection.months_remaining / 12, 1),
        "payoff_date": projection.payoff_date.isoformat() if projection.payoff_date else None,
        "total_interest_remaining": num(projection.total_interest_remaining),
        "total_payable_remaining": num(projection.total_payable_remaining),
        "next_interest_share_pct": round(projection.next_interest_share * 100, 1),
        "warnings": projection.warnings,
        # Yearly sampling: a 240-row schedule is unreadable in a chart and
        # bloats the payload for no benefit.
        "schedule": [
            {"month": r.month, "date": r.when.isoformat(),
             "opening": num(r.opening), "emi": num(r.emi),
             "interest": num(r.interest), "principal": num(r.principal),
             "closing": num(r.closing)}
            for r in projection.schedule[::12]
        ],
    }


def forecast_json(forecast: Any) -> dict[str, Any]:
    if forecast is None:
        return {}
    return {
        "opening_balance": num(forecast.opening_balance),
        "commitment_ratio_pct": round(forecast.commitment_ratio * 100, 1),
        "runway_months": forecast.runway_months,
        "first_shortfall_month": forecast.first_shortfall_month,
        "confidence": forecast.confidence,
        "assumptions": forecast.assumptions,
        "warnings": forecast.warnings,
        "months": [
            {"month": m.month,
             "committed_income": num(m.committed_income),
             "committed_outflow": num(m.committed_outflow),
             "discretionary_expected": num(m.discretionary_expected),
             "discretionary_low": num(m.discretionary_low),
             "discretionary_high": num(m.discretionary_high),
             "net_expected": num(m.net_expected),
             "closing_expected": num(m.closing_balance_expected),
             "closing_low": num(m.closing_balance_low),
             "closing_high": num(m.closing_balance_high)}
            for m in forecast.months
        ],
    }


def recurring_json(series: Any) -> dict[str, Any]:
    return {
        "id": series.id,
        "account_id": series.account_id,
        "label": series.label,
        "category": series.category.value,
        "direction": series.direction.value,
        "amount": num(series.median_amount),
        "monthly_equivalent": num(series.monthly_equivalent),
        "cadence": series.cadence_name,
        "cadence_days": series.cadence_days,
        "occurrences": series.occurrences,
        "first_seen": series.first_seen.isoformat() if series.first_seen else None,
        "last_seen": series.last_seen.isoformat() if series.last_seen else None,
        "next_expected": series.next_expected.isoformat() if series.next_expected else None,
        "is_active": series.is_active,
        "confidence": series.confidence,
    }


def source_file_json(record: Any) -> dict[str, Any]:
    """One row of the file/password registry.

    The working password is deliberately excluded from this shape - it exists
    in the local database only, and every place that reports whether a file is
    open or locked to the UI uses `password_status` and a redacted hint
    instead. See ingestion.passwords.redact_candidate.
    """
    from ..ingestion.passwords import redact_candidate

    return {
        "id": record.id,
        "filename": record.filename,
        "source": record.source,
        "sender": record.sender,
        "size_bytes": record.size_bytes,
        "password_status": record.password_status,
        "password_redacted": redact_candidate(record.password) if record.password else None,
        "parse_status": record.parse_status,
        "institution_guess": record.institution_guess,
        "account_type_guess": record.account_type_guess,
        "account_id": record.account_id,
        "statement_id": record.statement_id,
        "transaction_count": record.transaction_count,
        "error_message": record.error_message,
        "first_seen_at": record.first_seen_at,
        "last_attempted_at": record.last_attempted_at,
    }


def transfer_json(report: Any) -> dict[str, Any]:
    if report is None:
        return {"pairs": [], "total": 0, "double_count_avoided": 0, "notes": []}
    return {
        "total": num(report.total_amount),
        "double_count_avoided": num(report.double_count_avoided),
        "notes": report.notes,
        "pairs": [
            {"pair_id": p.pair_id, "amount": num(p.amount), "kind": p.kind,
             "from": p.from_account, "to": p.to_account,
             "day_gap": p.day_gap, "confidence": p.confidence}
            for p in report.pairs
        ],
    }
