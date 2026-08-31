"""Parsing into staging, and building the ledger back out of it.

Two halves of one rule: reading a document and counting it are separate acts.

`parse_entry` turns a staged file into a parse result stored beside it, and
touches nothing else. `materialise` takes the staged files that are ticked and
builds the entire ledger from them - wiping what was there first, because the
selection IS the ledger, and a rebuild that added to the old one could never
remove anything.

Nothing here decides what is selected. That is the Review screen's job.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from ..db import repository as repo
from ..db import staging
from ..db.database import Database
from ..models.schemas import (Account, AccountType, Statement, Transaction)

log = logging.getLogger(__name__)

#: What a parse can conclude. "empty" is a success: the file was read and had
#: nothing in it, which is a different thing from failing to read it, and
#: retrying it forever would be pointless.
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_LOCKED = "needs_password"


def account_key(account: Account | None) -> str:
    """A stable identity for grouping, across re-parses and re-imports.

    Institution plus the masked number, because that pair is what a reader
    recognises and what survives the account row being deleted and rebuilt
    with a new uuid on every Process data.
    """
    if account is None:
        return ""
    return f"{account.institution}|{account.account_number_masked}".strip("|")


# ---------------------------------------------------------------- parsing --


def parse_entry(db: Database, entry: dict[str, Any],
                password_candidates: list[str] | None = None) -> str:
    """Read one staged file and record what it contained.

    Returns the resulting parse status. Never raises: a file that cannot be
    read is a fact about that file, and letting it stop the batch would mean
    one bad PDF costs the other hundred and forty-six.
    """
    path = Path(entry.get("path") or "")
    entry_id = entry["id"]
    name = entry.get("filename") or path.name

    if not path.exists():
        staging.record_parse(db, entry_id, status=STATUS_FAILED,
                             message="The file is no longer on disk.")
        return STATUS_FAILED

    try:
        from ..ingestion.router import extract
        extraction = extract(path, password_candidates=password_candidates or [])
    except Exception as exc:
        staging.record_parse(db, entry_id, status=STATUS_FAILED,
                             message=f"{type(exc).__name__}: {exc}")
        return STATUS_FAILED

    if getattr(extraction, "needs_password", False):
        staging.record_parse(
            db, entry_id, status=STATUS_LOCKED,
            message="Protected, and no password derived from your profile "
                    "opened it.")
        return STATUS_LOCKED

    from ..ingestion import bureau as bureau_reader
    from ..ingestion import portfolio as portfolio_reader

    text = bureau_reader._text_of(extraction)

    if bureau_reader.looks_like_bureau_report(text, name):
        return _stage_bureau(db, entry_id, text, name, extraction)

    # Checked BEFORE the statement reader, not just before the portfolio one.
    #
    # Teaching `looks_like_portfolio` to refuse a contract note stopped it
    # inventing holdings, but the file then fell through to the statement
    # reader instead - which is worse. Fifteen Zerodha contract notes each
    # produced one transaction whose "amount" was its SETTLEMENT NUMBER:
    # "Settlement No: 2026151" became a 20,26,151 debit, and money out for
    # the year read three crore.
    #
    # A record of trades is neither a ledger nor a portfolio. It is read,
    # understood, and counted for nothing.
    if portfolio_reader.looks_like_trades(text):
        return _stage_trades(db, entry_id, text, name, extraction,
                             entry.get("sender", ""))

    if portfolio_reader.looks_like_portfolio(text, name):
        return _stage_portfolio(db, entry_id, text, name, extraction,
                                entry.get("sender", ""))

    return _stage_statement(db, entry_id, name, extraction, entry.get("sender", ""))


def _stage_trades(db: Database, entry_id: str, text: str, name: str,
                  extraction: Any, sender: str) -> str:
    """Record a contract note or transaction statement as read, and empty.

    Deliberately produces no transactions. The money a trade moves reaches
    the ledger through the bank statement that funded it; counting the
    contract note as well would count it twice, and its columns - traded
    quantity, strike rate, settlement number - are not the columns a ledger
    has.
    """
    from ..ingestion.gmail_source import institution_for_sender
    from ..ingestion import portfolio as portfolio_reader

    provider = (portfolio_reader.detect_layout(text, name)[1]
                or institution_for_sender(sender) or "Broker")
    staging.record_parse(
        db, entry_id, status=STATUS_EMPTY,
        message="A record of trades, not of money held or moved. Read and "
                "understood; nothing in it counts toward your ledger.",
        payload={"kind": "trades", "provider": provider},
        kind="trades",
        warnings=list(getattr(extraction, "warnings", []) or []),
        account_label=f"{provider} contract notes",
        account_key=f"trades|{provider}",
        account_type="investment",
        row_count=0, debits="0", credits="0",
        recon_status="not_applicable",
    )
    return STATUS_EMPTY


def _stage_statement(db: Database, entry_id: str, name: str, extraction: Any,
                     sender: str) -> str:
    from ..normalize.normalizer import normalize

    try:
        statement, account = normalize(extraction, filename=name, sender=sender)
    except Exception as exc:
        staging.record_parse(db, entry_id, status=STATUS_FAILED,
                             message=f"{type(exc).__name__}: {exc}")
        return STATUS_FAILED

    from ..reconcile.balance_check import reconcile
    recon = reconcile(statement, account.account_type)
    debits = sum((t.amount for t in statement.transactions
                  if t.direction.value == "debit"), Decimal("0"))
    credits = sum((t.amount for t in statement.transactions
                   if t.direction.value == "credit"), Decimal("0"))

    payload = {
        "kind": "statement",
        "statement": statement.model_dump(mode="json"),
        "account": account.model_dump(mode="json"),
        "reconciliation": {
            "status": recon.status.value,
            "message": recon.message,
            "discrepancy": str(recon.discrepancy)
            if recon.discrepancy is not None else None,
        },
    }
    status = STATUS_OK if statement.transactions else STATUS_EMPTY
    staging.record_parse(
        db, entry_id, status=status,
        message=recon.message[:400],
        payload=payload, kind="statement",
        warnings=[*extraction.warnings, *statement.parse_warnings],
        account_label=account.display_name(),
        account_key=account_key(account),
        account_type=account.account_type.value,
        period_start=statement.period_start,
        period_end=statement.period_end,
        row_count=len(statement.transactions),
        debits=str(debits), credits=str(credits),
        recon_status=recon.status.value,
    )
    return status


def _stage_bureau(db: Database, entry_id: str, text: str, name: str,
                  extraction: Any) -> str:
    from ..ingestion import bureau as bureau_reader

    try:
        report = bureau_reader.parse_report(text, name)
    except Exception as exc:
        staging.record_parse(db, entry_id, status=STATUS_FAILED,
                             message=f"{type(exc).__name__}: {exc}")
        return STATUS_FAILED

    payload = {"kind": "bureau", "report": dataclasses.asdict(report)}
    staging.record_parse(
        db, entry_id, status=STATUS_OK,
        message=f"{report.bureau.upper()} report, score {report.score or '—'}",
        payload=payload, kind="bureau",
        warnings=[*extraction.warnings, *report.warnings],
        account_label=f"{report.bureau.upper()} credit report",
        account_key=f"bureau|{report.bureau}",
        account_type="credit_report",
        period_start=report.pulled_on, period_end=report.pulled_on,
        row_count=len(report.accounts or []),
        recon_status="not_applicable",
    )
    return STATUS_OK


def _stage_portfolio(db: Database, entry_id: str, text: str, name: str,
                     extraction: Any, sender: str) -> str:
    from ..ingestion import portfolio as portfolio_reader
    from ..ingestion.gmail_source import institution_for_sender

    try:
        statement = portfolio_reader.parse_statement(
            text, extraction.tables, name)
    except Exception as exc:
        staging.record_parse(db, entry_id, status=STATUS_FAILED,
                             message=f"{type(exc).__name__}: {exc}")
        return STATUS_FAILED

    provider = (statement.provider or institution_for_sender(sender)
                or "Investments")
    recon_status, _, recon_message = statement.reconcile()
    payload = {"kind": "portfolio", "provider": provider,
               "statement": dataclasses.asdict(statement)}
    staging.record_parse(
        db, entry_id, status=STATUS_OK,
        message=f"{len(statement.holdings)} holding(s). {recon_message}"[:400],
        payload=payload, kind="portfolio",
        warnings=[*extraction.warnings, *statement.warnings],
        account_label=f"{provider} portfolio",
        account_key=f"portfolio|{provider}",
        account_type="investment",
        period_start=statement.as_of, period_end=statement.as_of,
        row_count=len(statement.holdings),
        recon_status=recon_status,
    )
    return STATUS_OK


def stage_alert(db: Database, alert: dict[str, Any]) -> str | None:
    """Stage one transaction alert.

    Alerts are not files, so their identity is built from the message they
    came from plus the figure they report - enough that re-scanning the same
    mailbox recognises the same alert, and two genuinely different alerts in
    one email stay separate.
    """
    import hashlib

    message_id = alert.get("message_id") or ""
    fingerprint = "|".join(str(alert.get(k) or "") for k in
                           ("message_id", "amount", "date_iso", "account_suffix",
                            "merchant"))
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    institution = alert.get("institution") or alert.get("sender_name") or "Unknown"
    suffix = alert.get("account_suffix") or ""
    entry_id = staging.add(
        db, digest,
        filename=(alert.get("merchant") or alert.get("subject")
                  or "Transaction alert")[:120],
        origin="gmail", kind="alert",
        # Without this the column defaults to empty, and an empty scan_intent
        # is read as "statement" - so 113 alerts were staged perfectly well
        # and then counted under Account statements. The alerts section
        # reported nothing staged while its documents sat in the next section
        # along, and Parse offered no way to reach them.
        scan_intent="transactional",
        message_id=message_id,
        sender=alert.get("sender", ""), subject=alert.get("subject", ""),
        payload={"kind": "alert", "alert": alert},
        parse_status=STATUS_OK,
    )
    amount = str(alert.get("amount") or "0")
    direction = alert.get("direction") or "debit"
    staging.record_parse(
        db, entry_id, status=STATUS_OK,
        message=alert.get("summary", "") or "",
        payload={"kind": "alert", "alert": alert},
        account_label=f"{institution}{f' (…{suffix})' if suffix else ''}",
        account_key=f"{institution}|{f'XXXX{suffix}' if suffix else ''}",
        account_type="credit_card",
        period_start=alert.get("date_iso"), period_end=alert.get("date_iso"),
        row_count=1,
        debits=amount if direction == "debit" else "0",
        credits=amount if direction == "credit" else "0",
        recon_status="unreconciled",
    )
    return entry_id


# ----------------------------------------------------------- materialising --


def materialise(db: Database, progress: Callable[[str], None] | None = None
                ) -> dict[str, Any]:
    """Build the whole ledger from the staged files that are ticked.

    Destructive by design. The staged selection is the definition of the
    ledger, so this clears what was there and rebuilds - anything else and
    unticking a file could never remove its rows.

    What a human decided is carried across separately, by fingerprint, because
    it is the one thing here that no amount of re-parsing can reproduce.
    """
    def say(message: str) -> None:
        if progress:
            progress(message)

    # Nothing is snapshotted here. What a human decided lives in
    # `user_overrides`, which the "parsed_data" scope deliberately does not
    # touch, and `apply_overrides` re-attaches it by content fingerprint once
    # the rows exist again - including re-pointing a decision whose row was
    # re-parsed into a slightly different shape. Copying it out and back would
    # be a second, worse implementation of a mechanism that already works.
    say("Clearing the old ledger")
    from ..db.database import CLEAR_SCOPES
    with db.connection() as conn:
        for table in CLEAR_SCOPES["rebuild"]:
            conn.execute(f"DELETE FROM {table}")

    say("Rebuilding from your selection")
    entries = staging.all_entries(db, selected_only=True, with_payload=True)

    accounts: dict[str, str] = {}      # account_key -> db account id
    transactions: list[Transaction] = []
    account_objects: dict[str, Account] = {}
    statements_written = 0
    bureau_written = 0
    portfolio_written = 0
    # Per-file, so one unreadable document cannot cost the other 144 - but
    # collected and reported, because a count of zero with no explanation is
    # how a broken rebuild looks exactly like an empty one.
    failures: list[str] = []

    def resolve_account(payload_account: dict[str, Any], key: str) -> str:
        if key in accounts:
            return accounts[key]
        account = Account(**payload_account)
        account.id = None
        account_id = repo.upsert_account(db, account)
        account.id = account_id
        accounts[key] = account_id
        account_objects[account_id] = account
        return account_id

    unread = 0
    # Alerts last, always. An alert carries four digits and an issuer name and
    # nothing else, so it can only be attached to an account some statement
    # has already described - and `entries` arrives sorted by account label,
    # under which "HDFC Bank (…6885)" (an alert) sorts before "HDFC Bank
    # Marriott Bonvoy Credit Card (XXXX6885)" (its statement). Every alert was
    # therefore looked up against an account that did not exist yet, and all
    # 211 of them were silently dropped from the rebuild.
    entries.sort(key=lambda e: e.get("kind") == "alert")
    for entry in entries:
        payload = entry.get("payload") or {}
        if entry.get("parse_status") not in ("ok", "empty") or not payload:
            # Ticked but never successfully read - a password-protected PDF
            # with no working password, most often. Not a rebuild failure:
            # there is simply nothing to rebuild from, and calling it a
            # failure buries the ones that are.
            #
            # "empty" is NOT one of these. Those files were read perfectly
            # well and had no transactions in them, which is a normal thing
            # for a statement in a month with no activity - and they still
            # carry an account and a period worth keeping. Counting them as
            # unreadable reported 29 failures over 13 actual ones.
            unread += 1
            continue
        kind = payload.get("kind") or entry.get("kind")

        if kind == "statement":
            try:
                statement = Statement(**payload["statement"])
                account_id = resolve_account(payload["account"],
                                             entry["account_key"] or entry["id"])
                statement.id = entry["id"]
                statement.account_id = account_id
                recon = payload.get("reconciliation") or {}
                repo.save_statement(db, statement, account_id, _Recon(recon))
                statements_written += 1
                for txn in statement.transactions:
                    txn.account_id = account_id
                    txn.statement_id = statement.id
                    txn.source = "statement"
                    transactions.append(txn)
            except Exception as exc:
                log.exception("could not rebuild statement %s", entry["filename"])
                failures.append(f"{entry['filename']}: {type(exc).__name__}: {exc}")

        elif kind == "alert":
            try:
                account_id = _account_for_alert(db, entry, accounts, account_objects)
                txn = _alert_transaction(payload["alert"], account_id)
                if txn:
                    transactions.append(txn)
            except Exception as exc:
                log.exception("could not rebuild alert %s", entry["filename"])
                failures.append(f"{entry['filename']}: {type(exc).__name__}: {exc}")

        elif kind == "bureau":
            try:
                from ..ingestion.bureau import BureauReport
                report = _rebuild_dataclass(BureauReport, payload["report"])
                repo.save_bureau_report(db, report, file_hash=entry["file_hash"],
                                        filename=entry["filename"])
                bureau_written += 1
            except Exception as exc:
                log.exception("could not rebuild bureau report %s", entry["filename"])
                failures.append(f"{entry['filename']}: {type(exc).__name__}: {exc}")

        elif kind == "portfolio":
            try:
                from ..ingestion.portfolio import PortfolioStatement
                statement = _rebuild_dataclass(PortfolioStatement,
                                               payload["statement"])
                provider = payload.get("provider") or "Investments"
                holdings_account = Account(
                    institution=provider,
                    account_type=AccountType.INVESTMENT,
                    account_number_masked="")
                account_id = repo.upsert_account(db, holdings_account)
                repo.save_portfolio_statement(
                    db, statement, account_id=account_id,
                    file_hash=entry["file_hash"], filename=entry["filename"])
                portfolio_written += 1
            except Exception as exc:
                log.exception("could not rebuild portfolio %s", entry["filename"])
                failures.append(f"{entry['filename']}: {type(exc).__name__}: {exc}")

    if transactions:
        repo.save_transactions(db, transactions)

    return {
        "statements": statements_written,
        "transactions": len(transactions),
        "accounts": len(accounts),
        "bureau_reports": bureau_written,
        "portfolios": portfolio_written,
        "failed": len(failures),
        "failures": failures[:10],
        "unread": unread,
        # The account objects are handed back so the caller can enrich without
        # reloading them, and the transactions with them: enrichment is where
        # user decisions are re-attached, and doing it here would mean running
        # it twice.
        "_transactions": transactions,
        "_accounts": {aid: acct for aid, acct in account_objects.items()},
    }


class _Recon:
    """The shape `save_statement` expects from a reconciliation result."""

    def __init__(self, data: dict[str, Any]) -> None:
        from ..models.schemas import ReconciliationStatus
        raw = data.get("status") or "not_applicable"
        try:
            self.status = ReconciliationStatus(raw)
        except ValueError:
            self.status = ReconciliationStatus.NOT_APPLICABLE
        self.message = data.get("message") or ""
        discrepancy = data.get("discrepancy")
        self.discrepancy = Decimal(discrepancy) if discrepancy else None


def _rebuild_dataclass(cls: Any, data: dict[str, Any]) -> Any:
    """Rehydrate a dataclass, including the dataclasses nested inside it.

    `asdict` flattens the whole tree to plain dicts, so rebuilding only the
    outer object left `report.accounts` as a list of dicts. Everything
    downstream reads those by attribute, so saving a credit report raised
    AttributeError - and the rebuild reported "0 credit reports" rather than
    an error, because the exception was caught per file so one bad document
    could not cost the other hundred and forty-four.
    """
    element_types = _ELEMENT_TYPES.get(cls.__name__, {})
    fields = {f.name: f for f in dataclasses.fields(cls)}
    kwargs: dict[str, Any] = {}
    for name, value in (data or {}).items():
        if name not in fields:
            continue
        element = element_types.get(name)
        if element is not None and isinstance(value, list):
            kwargs[name] = [
                _rebuild_dataclass(element, item) if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            kwargs[name] = _coerce(value, fields[name].type)
    return cls(**kwargs)


def _coerce(value: Any, annotation: Any) -> Any:
    """Put a JSON value back into the type its field declares.

    JSON has no Decimal and no date, so `asdict` writes both as strings and
    reading them back gives strings. Nothing complains at rebuild time - the
    object is constructed perfectly happily - and the failure lands later, in
    whatever first does arithmetic or calls a date method on it:

        units * nav        -> TypeError: can't multiply sequence by non-int
        as_of.isoformat()  -> AttributeError: 'str' object has no attribute

    That cost 81 broker statements out of one rebuild. Money must never come
    back from storage as text, because a Decimal that is secretly a string
    still adds up - by concatenation.
    """
    if value is None:
        return None
    hint = str(annotation)
    if "datetime" in hint:
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if "date" in hint:
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    if "Decimal" in hint:
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    if "int" in hint and "print" not in hint:
        if isinstance(value, bool) or isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except ValueError:
            return None
    return value


def _element_types() -> dict[str, dict[str, Any]]:
    """Which list fields hold dataclasses, and of what type.

    Read lazily and by name so importing this module does not drag in every
    reader, and so a reader that changes shape fails loudly here rather than
    silently producing dicts again.
    """
    from ..ingestion.bureau import BureauAccount, BureauReport
    from ..ingestion.portfolio import Holding, PortfolioStatement
    return {
        BureauReport.__name__: {"accounts": BureauAccount},
        PortfolioStatement.__name__: {"holdings": Holding},
    }


class _LazyElementTypes(dict):
    def get(self, key, default=None):  # type: ignore[override]
        if not self:
            self.update(_element_types())
        return super().get(key, default)


_ELEMENT_TYPES = _LazyElementTypes()


def _account_for_alert(db: Database, entry: dict[str, Any],
                       accounts: dict[str, str],
                       account_objects: dict[str, Account]) -> str | None:
    """The account an alert belongs to, if a staged statement identifies one.

    An alert carries four digits and an issuer name and nothing else, so it can
    only be attached to an account some statement has already described. With
    no such statement the alert has no home, and inventing one from marketing
    text is how a mailbox turns into accounts that do not exist.
    """
    key = entry.get("account_key") or ""
    if key in accounts:
        return accounts[key]

    suffix = key.split("|")[-1].replace("XXXX", "")[-4:] if "|" in key else ""
    if not suffix:
        return None
    for existing_key, account_id in accounts.items():
        if existing_key.endswith(suffix):
            return account_id
    return None


def _alert_transaction(alert: dict[str, Any], account_id: str | None
                       ) -> Transaction | None:
    if not account_id:
        return None
    from datetime import date as _date
    from ..models.schemas import Direction

    try:
        when = _date.fromisoformat(str(alert.get("date_iso"))[:10])
    except (TypeError, ValueError):
        return None
    try:
        amount = Decimal(str(alert.get("amount")))
    except Exception:
        return None

    return Transaction(
        account_id=account_id,
        txn_date=when,
        raw_description=alert.get("merchant") or alert.get("summary") or "",
        merchant=alert.get("merchant") or "",
        amount=amount,
        direction=(Direction.CREDIT if alert.get("direction") == "credit"
                   else Direction.DEBIT),
        source="email_alert",
        needs_review=True,
        review_reason="Read from an alert email; nothing has reconciled it.",
    )
