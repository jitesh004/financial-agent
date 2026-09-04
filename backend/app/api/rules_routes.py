"""Show the user the rules the app runs on them.

Everything this app decides about a document is decided by a rule, and until
now every one of those rules was invisible: a row said "Dining · rule" and
there was no way, anywhere, to learn which rule or why. That is a bad position
for software that tells someone what they spent.

Two endpoints:

  GET  /api/rules       the whole catalogue, read-only
  POST /api/rules/test  run one description or one sender through the stack
                        and report exactly what fires

The second is the one that answers a real question. A catalogue you have to
read is a reference; a box you can paste a narration into is an explanation.

Read-only on purpose. These rules are code, reviewed and tested, and a UI that
let them be edited would create a second source of truth for every one of
them - which is the exact fault this package was built to remove.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..categorize import rules as category_rules
from ..ingestion import bureau, gmail_source, passwords, portfolio, txn_email
from ..models.schemas import CATEGORY_GROUPS, Direction, Transaction
from ..normalize import column_map, metadata
from ..rules import formats, institutions
from ..rules import passwords as password_formats
from ..rules import thresholds as threshold_rules

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["rules"])


def _pattern(compiled) -> str:
    return getattr(compiled, "pattern", str(compiled))


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

@router.get("")
def catalogue() -> dict[str, Any]:
    """Every rule the app applies, grouped by the stage that applies it."""
    return {
        "find": _find_rules(),
        "open": _open_rules(),
        "read": _read_rules(),
        "check": _check_rules(),
        "ledger": _ledger_rules(),
        "privacy": _privacy_rules(),
        "pipeline": _pipeline_rules(),
        "money": _money_rules(),
        "model": _model_rules(),
        "storage": _storage_rules(),
        "vocabulary": _vocabulary(),
        "thresholds": [
            {
                "group": t.group, "name": t.name, "value": str(t.value),
                "unit": t.unit, "why": t.why, "source": t.source,
            }
            for t in threshold_rules.all_thresholds()
        ],
    }


def _find_rules() -> dict[str, Any]:
    return {
        "institutions": [
            {
                "name": i.name,
                "kind": i.kind,
                "match": list(i.match),
                "sends": list(i.sends),
                "password": i.password,
                "password_note": i.password_note,
                "bureau_key": i.bureau_key,
                "portfolio_layout": i.portfolio_layout,
            }
            for i in institutions.REGISTRY
        ],
        "scans": [
            {
                "key": key,
                "label": spec["label"],
                "description": spec["description"],
                "needs_attachment": spec["needs_attachment"],
                "max_months": spec["max_months"],
                "subjects": list(spec["subjects"]),
                "senders": list(spec["senders"]),
            }
            for key, spec in gmail_source.SCAN_INTENTS.items()
        ],
        "rejections": [
            {"reason": reason, "pattern": _pattern(pat)}
            for pat, reason in gmail_source.REJECTION_RULES
        ],
        "statement_subjects": _pattern(gmail_source.STATEMENT_SUBJECTS),
        "skipped_filenames": _pattern(gmail_source.NON_STATEMENT_FILENAMES),
        "generic_senders": {
            k: list(v) for k, v in institutions.GENERIC_SENDERS.items()},
    }


def _open_rules() -> dict[str, Any]:
    return {
        "password_formats": [
            {"label": f.label, "explanation": f.explanation, "needs": list(f.needs)}
            for f in password_formats.FORMATS
        ],
        "max_candidates": passwords.MAX_CANDIDATES,
    }


def _readable_label(pattern: str) -> str:
    """A label regex as the words it matches.

    The screen shows what a statement would have to SAY, not the pattern that
    reads it - "Card Number" rather than `card\\s*(?:number|no\\.?|#)`.
    """
    import re as _re

    text = _re.sub(r"\\s\*|\\s\+", " ", pattern)
    text = _re.sub(r"\(\?:([^)]*)\)", lambda m: m.group(1).split("|")[0], text)
    text = text.replace("\\.?", "").replace("a/?c", "A/C").replace("?", "")
    return " ".join(w.capitalize() if w.islower() else w
                    for w in text.split()).strip()


def _read_rules() -> dict[str, Any]:
    return {
        "account_types": [
            {"pattern": pattern, "type": account_type.value}
            for pattern, account_type in metadata.ACCOUNT_TYPE_PATTERNS
        ],
        "card_variants": dict(sorted(metadata.CARD_VARIANTS.items())),
        "columns": {role: list(aliases)
                    for role, aliases in column_map.COLUMN_ALIASES.items()},
        "required_columns": sorted(column_map.REQUIRED_ROLES),
        "bureau_labels": {field: list(aliases)
                          for field, aliases in bureau._LABELS.items()},
        "bureau_score_range": list(bureau.SCORE_RANGE),
        "portfolio_layouts": [
            {"layout": layout, "provider": provider, "match": list(fragments)}
            for layout, provider, fragments in portfolio.LAYOUT_SIGNATURES
        ],
        "portfolio_columns": [
            {"field": field, "headers": list(hints)}
            for hints, field in portfolio._COLUMN_HINTS
        ],
        "trade_markers": list(portfolio._TRADE_DOCUMENT_MARKERS),
        "alert_templates": [
            {"name": t.name, "direction": t.direction, "kind": t.kind,
             "pattern": _pattern(t.pattern)}
            for t in txn_email.TEMPLATES
        ],
        "not_a_transaction": _pattern(txn_email.NOT_A_TRANSACTION),
        "account_number": {
            "note": "Which number on a statement identifies the account. The "
                    "labels are tried in this order and the first that matches "
                    "wins, so the ORDER is the rule.",
            "shapes_first": "Amex prints XXXX-XXXXXX-31004, which no label "
                            "search would find, so that shape is matched "
                            "before any label is tried.",
            "labels": [
                {"label": _readable_label(pattern), "means": meaning}
                for pattern, meaning in metadata.ACCOUNT_NUMBER_LABELS
            ],
            "never": [
                {"label": label, "why": why}
                for label, why in metadata.NEVER_AN_ACCOUNT_NUMBER
            ],
            "fallback": "If no label matches, a masked number printed on its "
                        "own is used - and failing that, the institution and "
                        "account type together.",
            "stored_as": "Only the last four digits are ever kept. Enough to "
                         "tell two accounts apart, useless to anyone who reads "
                         "the database.",
        },
        "account_identity": {
            "note": "Whether two statements describe the SAME account. The "
                    "masked number plus the account type settles it on its "
                    "own - the institution is deliberately left out of the "
                    "key, because the same account exported twice can "
                    "disagree about it when one file's letterhead names the "
                    "bank and another's does not.",
            "why_it_matters": "Treating that disagreement as two accounts "
                              "silently doubles income, spending and "
                              "investments - the worst failure this app can "
                              "have, because every figure still looks "
                              "plausible.",
            "fallbacks": [
                "The masked account or card number, with the account type.",
                "The card's own product name, for an issuer that masks its "
                "number so completely no digit survives extraction.",
                "Institution and account type together, as a last resort.",
            ],
        },
        "person_vs_business": {
            "note": "Money sent to a named individual is the bulk of Indian "
                    "UPI and is not shopping. It is categorised separately so "
                    "spending totals mean what they say.",
            "signals": [
                "A company token in the name - Ltd, Pvt, Enterprises, Store, "
                "Services and the like - makes it a business.",
                "An honorific (Mr, Mrs, Shri, Smt, Dr) makes it a person.",
                "Otherwise, a payee that is two to four capitalised words and "
                "nothing else reads as a person's name.",
            ],
        },
    }


def _check_rules() -> dict[str, Any]:
    groups = {c: name for name, members in CATEGORY_GROUPS.items()
              for c in members}
    return {
        "categories": [
            {
                "order": i + 1,
                "category": rule.category,
                "group": groups.get(rule.category, "Other"),
                "confidence": rule.confidence,
                "direction": rule.direction.value if rule.direction else None,
                "pattern": _pattern(rule.pattern),
                # The rule's veto, when it has one - a second pattern that
                # stands the first one down. Shown because a rule that says
                # "instalment means debt" without also saying "unless it is
                # an RD instalment" is not the rule the app runs.
                "excludes": _pattern(rule.exclude) if rule.exclude else None,
            }
            for i, rule in enumerate(category_rules.RULES)
        ],
        "category_groups": {name: list(members)
                            for name, members in CATEGORY_GROUPS.items()},
    }


def _ledger_rules() -> dict[str, Any]:
    """What the app decides AFTER the rows exist.

    This half was missing entirely. The catalogue described how a document is
    found, opened, classified and read - and then stopped at the ledger, which
    is exactly where the decisions a user is most likely to question begin.
    Which month a salary counts in, why two rows were paired, and what counts
    as spending at all were none of them visible.
    """
    from ..analytics import periods, recurring
    from ..ingestion import txn_email
    from ..models.schemas import (CONTRA_EXPENSE_ROLES, FlowRole,
                                  NEUTRAL_ROLES)
    from ..reconcile import bureau_match, settlement, transfers
    from ..rules import directions as direction_rules

    role_note = {
        FlowRole.INCOME: "Money that genuinely entered your net worth.",
        FlowRole.EXPENSE: "Money that genuinely left it.",
        FlowRole.TRANSFER_OUT: "The funding leg of a move between your own accounts.",
        FlowRole.TRANSFER_IN: "The receiving account's record of that same money - "
                              "real, but already counted on the way out.",
        FlowRole.CARD_SETTLEMENT: "A payment received on a card. Settles a "
                                  "liability, and is never income no matter who "
                                  "funded it.",
        FlowRole.CLAIM_SETTLEMENT: "Someone repaying a purchase made on your card. "
                                   "Subtracted from spending rather than added to "
                                   "income - the purchase is already counted.",
        FlowRole.INVESTMENT: "Moved into an investment. Still your money, so not "
                             "spending.",
        FlowRole.REFUND: "A merchant giving money back. Also subtracted from "
                         "spending: counting it as income would inflate both sides "
                         "for a cancelled purchase.",
        FlowRole.EXCLUDED: "You took this row out of every total.",
    }

    def counts_as(role):
        if role in NEUTRAL_ROLES:
            return "neither - it is not a flow of your own money"
        if role in CONTRA_EXPENSE_ROLES:
            return "reduces spending"
        return "income" if role is FlowRole.INCOME else "spending"

    return {
        "directions": [direction_rules.describe(r.code)
                       for r in direction_rules.REASONS],
        "flow_roles": [
            {"role": role.value, "note": role_note.get(role, ""),
             "counts_as": counts_as(role)}
            for role in FlowRole
        ],
        "attribution": _attribution_rules(periods),
        "cadences": [
            {"name": name, "days": days, "tolerance_days": tolerance}
            for name, days, tolerance in recurring.CADENCES
        ],
        "recurring": {
            "min_occurrences": recurring.MIN_OCCURRENCES,
            "amount_variance": recurring.AMOUNT_VARIANCE_TOLERANCE,
            "note": "Grouped by a merchant signature with numbers, month names and "
                    "rail codes stripped, then tested for a regular cadence and a "
                    "stable amount. Same payee, wildly different amounts is not a "
                    "fixed charge.",
        },
        "pairing": [
            {
                "name": "A transfer between your own accounts",
                "note": "Same amount to within {tol}, at most {gap} days apart, and "
                        "it must cross accounts. Neither leg counts as income or "
                        "spending.".format(tol=transfers.AMOUNT_TOLERANCE,
                                           gap=transfers.MAX_DAY_GAP),
            },
            {
                "name": "A failed charge and its own reversal",
                "note": "One debit, a same-amount credit and often a second debit, "
                        "on ONE account within {d} days. That is one failed attempt "
                        "and one that went through, not three "
                        "events.".format(d=transfers.REVERSAL_MAX_DAY_GAP),
            },
            {
                "name": "The same row arriving on two statements",
                "note": "Matched on account, date, amount and direction, with the "
                        "running balance required to agree - two identical payments "
                        "in one afternoon are possible. A narration that is a strict "
                        "prefix of another is one row two extractions cut at "
                        "different lengths; the fuller one survives.",
            },
            {
                "name": "A card bill settled across several legs",
                "note": "Only attempted when the bank leg independently names a "
                        "payment rail. Bounded to {c} candidates and {l} legs a "
                        "side, and below {f} confidence it goes to review rather "
                        "than applying itself.".format(
                            c=settlement.MAX_CANDIDATES,
                            l=settlement.MAX_LEGS_PER_SIDE,
                            f=settlement.CONFIDENCE_FLOOR),
            },
        ],
        "alerts": {
            "supersede_days": txn_email.SUPERSEDE_DAY_WINDOW,
            "note": "An alert is replaced the moment the statement covering it "
                    "arrives. The statement always wins: it is checked, the alert "
                    "is not. The alert is flagged rather than deleted - it really "
                    "did arrive, and seeing what replaced it is worth a column.",
        },
        "bureau_matching": {
            "auto_link": bureau_match.AUTO_LINK_CONFIDENCE,
            "suggest": bureau_match.SUGGEST_CONFIDENCE,
            "never_expected": sorted(bureau_match.NOT_REPORTED_BY_BUREAUS),
            "note": "Bureaus report credit, not deposits. Saying a savings account "
                    "is missing from your credit report would be noise dressed as a "
                    "finding.",
        },
    }


def _attribution_rules(periods) -> dict[str, Any]:
    """Which month a transaction is counted in - the salary-drift rules."""
    return {
        "default": "Its own calendar month. A one-off is NEVER moved.",
        "min_occurrences": periods.MIN_OCCURRENCES_TO_SHIFT,
        "month_end_anchor": periods.MONTH_END_ANCHOR,
        "month_start_anchor": periods.MONTH_START_ANCHOR,
        "arrived_early_from": periods.ARRIVED_EARLY_FROM,
        "arrived_late_until": periods.ARRIVED_LATE_UNTIL,
        "steps": [
            {
                "name": "Only a monthly series can move at all",
                "detail": "It has to be monthly and seen at least {n} times. A "
                          "one-off payment stays in the month it happened, "
                          "always.".format(n=periods.MIN_OCCURRENCES_TO_SHIFT),
            },
            {
                "name": "Its payday is the circular median day",
                "detail": "Pay landing on the 31st, 1st, 30th and 2nd is one payday "
                          "either side of a month boundary. A plain median of those "
                          "days gives the 16th, which is wrong about every one of "
                          "them; the circular median gives the month end.",
            },
            {
                "name": "Late arrivals go back, early ones go forward",
                "detail": "Payday on or after the {end}th and it arrives on or "
                          "before the {late}th: that is the PREVIOUS month's pay. "
                          "Payday on or before the {start}th and it arrives on or "
                          "after the {early}th: that is next month's, early.".format(
                              end=periods.MONTH_END_ANCHOR,
                              late=periods.ARRIVED_LATE_UNTIL,
                              start=periods.MONTH_START_ANCHOR,
                              early=periods.ARRIVED_EARLY_FROM),
            },
            {
                "name": "A series never contributes twice to one month",
                "detail": "Shifting can CREATE the double count it exists to "
                          "prevent: pay on 31 August and again on 1 September, and "
                          "moving September back lands both in August and empties "
                          "September. The occurrence nearest payday keeps the month "
                          "and the other is put back in its own calendar month - "
                          "moved back, not merely annotated.",
            },
            {
                "name": "Two genuine payments in one month are flagged, not moved",
                "detail": "When no shift can separate them the row is marked for "
                          "review with the reason, rather than being silently moved "
                          "to a month it may not belong to.",
            },
        ],
    }


def _privacy_rules() -> dict[str, Any]:
    """What is removed before anything reaches a language model.

    Worth showing whether or not the model is switched on. "What leaves this
    machine" is a question a user is entitled to a precise answer to, and the
    precise answer is a list.
    """
    from ..llm import client as llm_client

    return {
        "note": "Applied to every narration before a model sees it. The model is "
                "off unless you turn it on in Settings; this is what it would "
                "receive if you did.",
        "removed": [
            {"what": "Long digit runs", "why": "Account, card and reference numbers.",
             "pattern": _pattern(llm_client._LONG_DIGITS)},
            {"what": "PAN", "why": "A permanent identity number.",
             "pattern": _pattern(llm_client._PAN)},
            {"what": "Email addresses",
             "why": "Yours and the payee's - a UPI handle is an email address.",
             "pattern": _pattern(llm_client._EMAIL)},
            {"what": "Phone numbers",
             "why": "Indian mobile numbers appear inside UPI narrations.",
             "pattern": _pattern(llm_client._PHONE)},
            {"what": "PIN codes and addresses",
             "why": "Card statements print the merchant's locality, which can "
                    "identify where you live.",
             "pattern": _pattern(llm_client._PINCODE)},
            {"what": "Names following an honorific",
             "why": "A person you paid is not a merchant.",
             "pattern": _pattern(llm_client._HONORIFIC_NAME)},
        ],
    }


def _pipeline_rules() -> dict[str, Any]:
    """The order things happen in, and the limits on the machinery itself.

    Order is not an implementation detail here - it is a rule. Duplicates are
    removed before transfers are matched, because a duplicated row would
    otherwise be paired with the original and both hidden from the totals; and
    categorisation runs before your saved decisions are applied, so a decision
    always wins over a rule rather than racing it.
    """
    from ..ingestion import router
    from ..jobs import TERMINAL_STATUSES

    return {
        "stages": [
            {"name": "Remove duplicates",
             "why": "Before anything pairs rows together. A duplicate matched "
                    "against its own original would hide both."},
            {"name": "Filter parser artifacts",
             "why": "A misread row is dropped here rather than being "
                    "categorised, paired and forecast on."},
            {"name": "Cancel reversed charges",
             "why": "A failed charge and its refund are one non-event."},
            {"name": "Match transfers between accounts",
             "why": "So money you moved is not counted as income and spending."},
            {"name": "Match card settlements",
             "why": "The multi-leg cases 1:1 pairing cannot reach."},
            {"name": "Categorise",
             "why": "Rules first; the model only for what rules left over, and "
                    "only if you turned it on."},
            {"name": "Consult learned categories",
             "why": "A merchant you have already corrected keeps your answer."},
            {"name": "Apply your saved decisions",
             "why": "Last, so a decision you made always beats a rule."},
            {"name": "Assign accounting periods",
             "why": "Which month each row counts in - see Your ledger."},
            {"name": "Compute the analysis",
             "why": "Every total is a sum over rows that have already been "
                    "settled."},
        ],
        "formats": {
            "magic_bytes": [
                {"bytes": sig.decode("latin-1").replace("\x00", "\\x00"),
                 "kind": kind}
                for sig, kind in router._MAGIC
            ],
            "extensions": sorted(router.SUPPORTED_EXTENSIONS),
            "note": "The file's first bytes decide, not its extension. People "
                    "rename files, and mail clients hand out .xls files that "
                    "are really HTML - trusting the extension is how you get "
                    "\"no tables found\" on a perfectly good statement.",
        },
        "classification_order": [
            {"reader": "Credit bureau report",
             "test": "A bureau is named AND at least two of: credit report, "
                     "credit information, credit score, account information, "
                     "enquiry, CIR."},
            {"reader": "Holdings statement",
             "test": "Not a record of trades, then an ISIN alone is proof - "
                     "otherwise two softer markers must agree."},
            {"reader": "Bank or card statement",
             "test": "Everything else. Last on purpose: it is the only reader "
                     "with a reconciliation gate to catch its own mistakes."},
        ],
        "jobs": {
            "terminal_states": sorted(TERMINAL_STATUSES),
            "note": "A job that reaches one of these never changes again, so "
                    "the screen stops asking. Work survives a restart: "
                    "progress is written through as it happens rather than "
                    "held in memory until the end.",
        },
    }


def _money_rules() -> dict[str, Any]:
    """Forecasting and loan maths - both arithmetic, neither a model."""
    from ..analytics import forecast

    return {
        "forecast": {
            "note": "Built from two parts with very different certainty. "
                    "Committed money is recurring series already detected - "
                    "the EMI will leave on the 5th. Everything else is "
                    "modelled from your own observed month-to-month variance. "
                    "No model is asked to guess a figure.",
            "min_band_share": float(forecast.MIN_BAND_SHARE),
            "band_note": "Every month carries a low, expected and high figure "
                         "rather than one number. The band is never narrower "
                         "than this share of the median month: four "
                         "near-identical months would otherwise produce a "
                         "band of almost nothing, which reads as a promise "
                         "next month cannot keep.",
            "committed_confidence": forecast.COMMITTED_SERIES_CONFIDENCE,
            "confidence": [
                {"level": "high",
                 "needs": f"{forecast.HIGH_CONFIDENCE_MONTHS}+ months of "
                          f"history, {forecast.HIGH_CONFIDENCE_SERIES}+ active "
                          f"recurring series, and spending varying by less "
                          f"than {int(forecast.HIGH_CONFIDENCE_VOLATILITY * 100)}%"},
                {"level": "medium",
                 "needs": f"{forecast.MEDIUM_CONFIDENCE_MONTHS}+ months and "
                          f"variation under "
                          f"{int(forecast.MEDIUM_CONFIDENCE_VOLATILITY * 100)}%"},
                {"level": "low", "needs": "anything less"},
            ],
            "limits": "It projects your EXISTING patterns forward. It knows "
                      "nothing about a job change, a bonus, a medical event "
                      "or inflation, and says so on the screen.",
        },
        "loans": {
            "note": "Every number is a closed-form formula, never a model. A "
                    "loan's future is fully determined by principal, rate and "
                    "EMI - there is nothing to predict, only to calculate. "
                    "Asking a model for a payoff date would be inventing "
                    "uncertainty where none exists.",
            "rate_recovery": "Where a statement does not print the interest "
                             "rate, it is recovered from the interest actually "
                             "charged rather than guessed.",
        },
    }


def _model_rules() -> dict[str, Any]:
    """What a language model is used for, and what it is never used for."""
    from ..categorize import llm_categorizer
    from ..db.database import get_db
    from ..db import repository as repo

    try:
        enabled = bool(repo.get_settings(get_db()).get("use_llm"))
    except Exception:  # the screen must never 500 on a settings read
        enabled = False

    return {
        "enabled": enabled,
        "used_for": [
            "Naming a merchant that no rule recognises.",
            "Writing the plain-English summary on the dashboard.",
            "Filling identity fields a statement's letterhead did not yield.",
        ],
        "never_used_for": [
            "Any figure. Every total, balance, payoff date and forecast is "
            "arithmetic over your own rows.",
            "Deciding direction, pairing transfers, or reconciling a "
            "statement.",
            "Anything at all unless you switch it on - it is off by default, "
            "and the switch lives on the server rather than in the browser so "
            "that the thing deciding whether to spend money is the thing that "
            "would spend it.",
        ],
        "batch_size": llm_categorizer.BATCH_SIZE,
        "instructions": llm_categorizer.SYSTEM.strip(),
    }


def _storage_rules() -> dict[str, Any]:
    """Where things live and what each clearing action keeps."""
    from ..db.database import CLEAR_SCOPES, MAX_SNAPSHOTS

    tiers = {
        "derived": "Totals, forecasts and recurring series. Reproducible from "
                   "the parsed ledger in seconds.",
        "parsed_data": "The ledger itself - transactions, accounts, "
                       "statements. Rebuildable from the statement files.",
        "rebuild": "What a rebuild replaces, which deliberately excludes the "
                   "job telling you it is running.",
        "staged_imports": "Documents read but not yet processed. Clearing "
                          "these loses no ledger.",
        "files": "The statement files themselves. Anything from Gmail could "
                 "be downloaded again; anything uploaded by hand exists "
                 "nowhere else.",
        "ai_inferences": "What a model suggested. Always regenerable, and "
                         "never the same twice.",
        "decisions": "What YOU decided. Cannot be regenerated by anything.",
        "everything": "All of it.",
    }
    return {
        "max_snapshots": MAX_SNAPSHOTS,
        "snapshot_note": "Every clearing action takes a snapshot first, so "
                         "none of them is final.",
        "scopes": [
            {"scope": name, "tables": len(tables), "note": tiers.get(name, "")}
            for name, tables in CLEAR_SCOPES.items()
        ],
    }


def _vocabulary() -> dict[str, Any]:
    return {
        "months": sorted(formats.MONTHS, key=lambda m: (formats.MONTHS[m], m)),
        "rails": list(formats.RAIL_NAMES),
        "prefix_rails": list(formats.PREFIX_RAILS),
        "signature_rails": list(formats.SIGNATURE_RAILS),
        "bill_payment": {
            "shared": list(formats.BILL_PAYMENT_MARKERS),
            "direction_only": list(formats.DIRECTION_ONLY_BILL_MARKERS),
            "category_only": list(formats.CATEGORY_ONLY_BILL_MARKERS),
            "settlement_only": list(formats.SETTLEMENT_ONLY_BILL_MARKERS),
        },
        "no_figure": sorted(f for f in formats.NO_FIGURE if f),
    }


# --------------------------------------------------------------------------
# Try it
# --------------------------------------------------------------------------

class RuleTest(BaseModel):
    #: A transaction narration, as it appears on a statement.
    description: str = ""
    #: An email sender, to explain what a scan would do with it.
    sender: str = ""
    #: A subject line, which is what most rejections actually read.
    subject: str = ""
    #: An attachment or upload filename.
    filename: str = ""
    #: Which way the money went, since some rules only apply one way.
    direction: str = "debit"


@router.post("/test")
def test(payload: RuleTest) -> dict[str, Any]:
    """Run one example through the rules and report what fires.

    Nothing is stored and no ledger is touched - this reads the same functions
    the pipeline reads, so what it reports is what would actually happen.
    """
    out: dict[str, Any] = {}
    if payload.description.strip():
        out["description"] = _explain_description(
            payload.description.strip(), payload.direction)
    if payload.sender.strip() or payload.subject.strip() or payload.filename.strip():
        out["email"] = _explain_email(
            payload.sender.strip(), payload.subject.strip(),
            payload.filename.strip())
    return out


def _explain_description(text: str, direction: str) -> dict[str, Any]:
    from ..normalize.parsers import normalize_description

    try:
        way = Direction(direction)
    except ValueError:
        way = Direction.DEBIT

    normalized = normalize_description(text)

    # The same strings `apply_rules` looks at, including the variant with the
    # issuer's EMI offer marker removed. Assembled through the same helper
    # rather than restated: this box is the app explaining itself, and an
    # explanation that searches a different set of strings from the pipeline
    # is a second implementation that will drift. It did - "EMI CLOUDNINE"
    # was categorised as healthcare by the ledger and reported here as
    # matching nothing at all.
    haystacks = category_rules.haystacks_for(
        Transaction(txn_date=date.today(), raw_description=text,
                    normalized_description=normalized,
                    amount=Decimal("0"), direction=way))

    # Every rule that COULD match, not just the winner. "Which rule fired" is
    # the answer; "what else nearly fired" is what explains a surprise.
    matches = []
    vetoed = []
    for i, rule in enumerate(category_rules.RULES):
        if rule.direction is not None and rule.direction != way:
            continue
        if not any(rule.pattern.search(h) for h in haystacks):
            continue
        entry = {
            "order": i + 1,
            "category": rule.category,
            "confidence": rule.confidence,
            "direction": rule.direction.value if rule.direction else None,
            "pattern": _pattern(rule.pattern),
        }
        veto = next((rule.exclude.search(h) for h in haystacks
                     if rule.exclude is not None
                     and rule.exclude.search(h)), None)
        if veto is not None:
            # It matched and stood down anyway. Reported separately, because
            # "the loan rule matched but the word RD vetoed it" is the whole
            # answer to why an RD instalment is filed as investment.
            entry["vetoed_by"] = veto.group(0)
            entry["excludes"] = _pattern(rule.exclude)
            vetoed.append(entry)
            continue
        matches.append(entry)

    alert = txn_email.parse_alert(text)

    return {
        "normalized": normalized,
        # What the rules were actually run against, marker-stripped variant
        # included, so a surprising answer can be traced to the string that
        # produced it rather than to the one that was typed.
        "searched": haystacks,
        "winner": matches[0] if matches else None,
        "also_matched": matches[1:],
        "vetoed": vetoed,
        "bill_payment": bool(formats.BILL_PAYMENT.search(text)),
        "rails_stripped": text != normalized,
        "alert": None if alert is None else {
            "template": alert.template,
            "direction": alert.direction,
            "amount": str(alert.amount),
            "counterparty": alert.counterparty,
            "account_suffix": alert.account_suffix,
            "txn_date": alert.txn_date.isoformat() if alert.txn_date else None,
        },
    }


def _explain_email(sender: str, subject: str, filename: str) -> dict[str, Any]:
    haystack = f"{sender} {filename}"
    label, explanation = passwords.password_hint(sender, filename)
    return {
        "institution": gmail_source.institution_for_sender(sender)
        if sender else None,
        "matched_fragments": sorted(institutions.unshadowed(haystack)),
        "category": gmail_source.classify_sender(sender) if sender else None,
        "scans": {
            key: gmail_source.statement_rejection_reason(sender, subject, key)
            for key in gmail_source.SCAN_INTENTS
        },
        "attachment_kept": gmail_source.is_probable_statement_file(filename)
        if filename else None,
        "password": {"format": label, "explanation": explanation},
        "bureau": bureau.detect_bureau("", filename) if filename else None,
        "portfolio_layout": list(portfolio.detect_layout("", filename))
        if filename else None,
    }


# --------------------------------------------------------------------------
# Why is this row the way it is
# --------------------------------------------------------------------------

@router.get("/explain/{txn_id}")
def explain_transaction(txn_id: str) -> dict[str, Any]:
    """Everything the app decided about one row, and on what evidence.

    Three separate questions, and they used to have between them one visible
    answer:

      what is it       - the category, and now which of the 51 rules said so
      which way        - money in or out, and which of five signals decided
      what is it part of - the transfer or settlement group it belongs to,
                           the other legs in it, and how confident the match is

    Read from what was stored at import time. Nothing is recomputed, because a
    recomputed answer could differ from the one that actually produced the
    numbers on screen - and then this page would be explaining a ledger that
    does not exist.
    """
    from ..db.database import get_db
    from ..db import repository as repo
    from ..rules import directions as direction_rules

    db = get_db()
    txn = repo.get_transaction(db, txn_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="No such transaction")

    return {
        "id": txn_id,
        "category": _explain_category(txn),
        "direction": {
            "value": txn.direction.value,
            "reason": direction_rules.describe(txn.direction_reason),
            # An older row has the answer but not the reason: it was
            # categorised before the reason was recorded. Saying so is the
            # honest reading - claiming a default would be inventing one.
            "recorded": bool(txn.direction_reason),
        },
        "transfer": _explain_transfer(db, txn),
    }


def _explain_category(txn) -> dict[str, Any]:
    source = txn.category_source.value
    matched = next((r for r in category_rules.RULES
                    if r.label == txn.category_rule), None)
    return {
        "value": txn.category,
        "source": source,
        "rule": txn.category_rule or None,
        "pattern": _pattern(matched.pattern) if matched else None,
        "confidence": txn.category_confidence,
        "recorded": bool(txn.category_rule) or source != "rule",
    }


#: What each pairing kind means, in one sentence. The kinds are written by
#: reconcile.transfers and reconcile.settlement; keeping the wording here means
#: a new kind shows up as itself rather than as a blank.
_PAIR_KINDS = {
    "self_transfer": "Money you moved between two of your own accounts. "
                     "Counted on neither side - it is not income and it is "
                     "not spending.",
    "cc_payment": "A credit card bill being settled from a bank account. "
                  "Both legs are real, and counting either as spending would "
                  "count the same purchases twice.",
    "investment": "Money moved into an investment account. It left the bank "
                  "but it did not leave you.",
    "card_settlement": "A card bill matched across several legs at once - one "
                       "payment covering several cards, or several part "
                       "payments covering one.",
}


def _explain_transfer(db, txn) -> dict[str, Any] | None:
    """The group this row belongs to, and the other legs in it."""
    if not txn.transfer_pair_id:
        return None

    from ..db import repository as repo

    legs = [
        {
            "id": other.id,
            "date": other.txn_date.isoformat(),
            "description": other.raw_description,
            "amount": str(other.amount),
            "direction": other.direction.value,
            "account_id": other.account_id,
            "is_this_row": other.id == txn.id,
            "is_mirror_leg": other.is_mirror_leg,
        }
        for other in repo.transactions_in_pair(db, txn.transfer_pair_id)
    ]
    pair = repo.get_transfer_pair(db, txn.transfer_pair_id)

    return {
        "pair_id": txn.transfer_pair_id,
        "kind": pair["kind"] if pair else None,
        "what_it_means": _PAIR_KINDS.get(
            (pair or {}).get("kind"),
            "Matched as one movement across accounts."),
        "confidence": pair["confidence"] if pair else None,
        "day_gap": pair["day_gap"] if pair else None,
        "flow_role": txn.flow_role or None,
        # The mirror leg is the side kept out of totals, so that one row is not
        # counted twice under two different names.
        "counted": not txn.is_mirror_leg,
        "legs": legs,
    }
