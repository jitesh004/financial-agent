"""Reads and writes for the staging area.

Kept out of `repository` on purpose. Everything in that module is about the
ledger - the rows that count - and the one rule staging has to enforce is that
its contents do not count until someone says so. Two modules make that rule
visible in the import list of any file that breaks it.

Identity here is the file's content hash, never its name or its Gmail message
id. The same statement re-downloaded under a different message, or an upload
of a file already fetched from the mailbox, is one entry with one selection.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from typing import Any, Iterable, Sequence

from .database import Database

log = logging.getLogger(__name__)

#: Columns carried straight through from a caller's dict.
_WRITABLE = (
    "filename", "origin", "kind", "scan_intent", "path", "message_id",
    "sender", "subject",
    "parse_status", "parse_message", "parsed_at", "account_label",
    "account_key", "account_type", "period_start", "period_end", "row_count",
    "debits", "credits", "recon_status",
)


#: How particular a source is about what it looks for. "statement" is the
#: catch-all - its sender list contains every broker and bureau - so anything
#: else that also found a document knows more about it.
_SOURCE_SPECIFICITY = {"": 0, "statement": 1, "upload": 2,
                       "bureau": 3, "investment": 3, "transactional": 3}


def _more_specific(incoming: str, stored: str | None) -> bool:
    return (_SOURCE_SPECIFICITY.get(incoming or "", 2)
            > _SOURCE_SPECIFICITY.get(stored or "", 0))


def _entry_dict(row: Any) -> dict[str, Any]:
    """A staged row as a plain dict, without the tenancy column.

    `user_id` is how the row-level security policy finds the row; it is not
    something the Review screen has any use for, and it would otherwise travel
    all the way into the JSON the browser gets.
    """
    return {k: v for k, v in zip(row.keys(), row) if k != "user_id"}


def _row_to_entry(row: Any) -> dict[str, Any]:
    entry = _entry_dict(row)
    entry["selected"] = bool(entry.get("selected"))
    for field in ("warnings", "payload"):
        raw = entry.get(field)
        try:
            entry[field] = json.loads(raw) if raw else ([] if field == "warnings" else {})
        except (TypeError, ValueError):
            entry[field] = [] if field == "warnings" else {}
    return entry


def known_hashes(db: Database) -> dict[str, str]:
    """Every staged file's hash, mapped to its id.

    What makes a re-scan cheap: a mailbox scan turns up the same 147 files
    every time, and this is how the parse step knows it has already read 145
    of them.
    """
    with db.connection() as conn:
        rows = conn.execute("SELECT id, file_hash FROM staged_files").fetchall()
    return {r["file_hash"]: r["id"] for r in rows}


def add(db: Database, file_hash: str, **fields: Any) -> str:
    """Stage a file, or return the id of the one already staged for it.

    An existing entry keeps its selection and its parse result. Re-scanning
    must never silently re-tick something the user turned off.
    """
    if not file_hash:
        raise ValueError("a staged file needs a content hash")

    with db.connection() as conn:
        existing = conn.execute(
            "SELECT id, scan_intent FROM staged_files WHERE file_hash = ?",
            (file_hash,)).fetchone()
        if existing:
            # The entry stands - its selection and its parse result are not
            # this call's to overwrite - but its SOURCE can be corrected.
            #
            # The scans overlap: the statement scan's sender list contains
            # every broker, so a Zerodha holdings PDF is found first as a
            # statement and later as an investment. Leaving the first answer
            # in place filed 108 investment documents under Account
            # statements, and the Investments section reported nothing staged
            # while Choose went on offering them.
            incoming = fields.get("scan_intent")
            if incoming and _more_specific(incoming, existing["scan_intent"]):
                conn.execute(
                    "UPDATE staged_files SET scan_intent = ? WHERE id = ?",
                    (incoming, existing["id"]))
            return existing["id"]

        entry_id = str(uuid.uuid4())
        values = {k: fields.get(k) for k in _WRITABLE}
        values["filename"] = values.get("filename") or "(unnamed)"
        values["origin"] = values.get("origin") or "gmail"
        values["kind"] = values.get("kind") or "statement"
        values["parse_status"] = values.get("parse_status") or "pending"
        # A column declared NOT NULL DEFAULT '' still rejects an explicit
        # NULL, and every unsupplied field arrives here as one - so a caller
        # that simply did not know an email's subject failed the insert.
        for column in ("sender", "subject", "scan_intent", "parse_message",
                       "account_label",
                       "account_key", "account_type", "recon_status"):
            if values.get(column) is None:
                values[column] = ""
        for column in ("row_count",):
            if values.get(column) is None:
                values[column] = 0
        for column in ("debits", "credits"):
            if values.get(column) is None:
                values[column] = "0"
        columns = ", ".join(["id", "file_hash", "selected", "payload", *_WRITABLE])
        holders = ", ".join(["?"] * (len(_WRITABLE) + 4))
        conn.execute(
            f"INSERT INTO staged_files ({columns}) VALUES ({holders})",
            [entry_id, file_hash, 1 if fields.get("selected", True) else 0,
             json.dumps(fields.get("payload") or {}),
             *[values[k] for k in _WRITABLE]],
        )
        return entry_id


def record_parse(db: Database, entry_id: str, *, status: str, message: str = "",
                 payload: Any = None, warnings: Sequence[str] = (),
                 **summary: Any) -> None:
    """Write what parsing a staged file produced.

    The summary columns exist so the Review screen can describe a file - which
    account, which period, how many rows, how much - without reading `payload`
    back. With 150 staged statements that is the difference between a screen
    that opens and one that thinks about it.
    """
    sets = ["parse_status = ?", "parse_message = ?", "parsed_at = datetime('now')",
            "payload = ?", "warnings = ?"]
    args: list[Any] = [status, message[:500],
                       json.dumps(payload if payload is not None else {}, default=str),
                       json.dumps(list(warnings))]
    # `kind` is included because what a file IS only becomes known by reading
    # it. Everything downloaded is staged as a statement; the parse is what
    # discovers that this one is a CRIF report and that one is a broker's
    # holdings, and without writing that back the Review screen went on
    # calling them statements.
    for column in ("kind", "account_label", "account_key", "account_type",
                   "period_start", "period_end", "row_count", "debits",
                   "credits", "recon_status"):
        if column in summary:
            value = summary[column]
            sets.append(f"{column} = ?")
            args.append(value.isoformat() if isinstance(value, date) else value)
    args.append(entry_id)
    with db.connection() as conn:
        conn.execute(f"UPDATE staged_files SET {', '.join(sets)} WHERE id = ?", args)


def unparsed(db: Database, kinds: Sequence[str] | None = None,
             scan_intent: str | None = None) -> list[dict[str, Any]]:
    """Staged files that have never been parsed successfully.

    "Never parsed successfully" rather than "never parsed": a file that failed
    because a password was missing should be retried once the profile can open
    it, and one that failed on a bug should be retried once the bug is fixed.
    """
    sql = "SELECT * FROM staged_files WHERE parse_status NOT IN ('ok', 'empty')"
    args: list[Any] = []
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    if scan_intent:
        sql += " AND scan_intent = ?"
        args.append(scan_intent)
    with db.connection() as conn:
        return [_row_to_entry(r) for r in conn.execute(sql, args).fetchall()]


def all_entries(db: Database, *, selected_only: bool = False,
                kinds: Sequence[str] | None = None,
                with_payload: bool = False) -> list[dict[str, Any]]:
    columns = "*" if with_payload else (
        ", ".join(c for c in
                  ["id", "file_hash", "filename", "origin", "kind", "scan_intent", "path",
                   "message_id", "sender", "subject", "selected", "superseded_by",
                   "parse_status", "parse_message", "parsed_at", "account_label",
                   "account_key", "account_type", "period_start", "period_end",
                   "row_count", "debits", "credits", "recon_status", "warnings",
                   "added_at"]))
    sql = f"SELECT {columns} FROM staged_files WHERE 1=1"
    args: list[Any] = []
    if selected_only:
        # A superseded row is never processed even while it is ticked: the
        # statement that replaced it is the better record of the same money.
        sql += " AND selected = 1 AND superseded_by IS NULL"
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    sql += " ORDER BY account_label, period_start, filename"
    with db.connection() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for row in rows:
        entry = _entry_dict(row)
        entry["selected"] = bool(entry.get("selected"))
        try:
            entry["warnings"] = json.loads(entry.get("warnings") or "[]")
        except (TypeError, ValueError):
            entry["warnings"] = []
        if with_payload:
            try:
                entry["payload"] = json.loads(entry.get("payload") or "{}")
            except (TypeError, ValueError):
                entry["payload"] = {}
        out.append(entry)
    return out


def payload_of(db: Database, entry_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute("SELECT payload FROM staged_files WHERE id = ?",
                           (entry_id,)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["payload"] or "{}")
    except (TypeError, ValueError):
        return {}


def set_selected(db: Database, decisions: Iterable[tuple[str, bool]]) -> int:
    pairs = [(1 if want else 0, entry_id) for entry_id, want in decisions]
    if not pairs:
        return 0
    with db.connection() as conn:
        conn.executemany(
            "UPDATE staged_files SET selected = ? WHERE id = ?", pairs)
    return len(pairs)


def remove(db: Database, entry_ids: Sequence[str]) -> int:
    if not entry_ids:
        return 0
    with db.connection() as conn:
        holders = ",".join("?" * len(entry_ids))
        return conn.execute(
            f"DELETE FROM staged_files WHERE id IN ({holders})",
            list(entry_ids)).rowcount


def apply_supersession(db: Database) -> int:
    """Mark alerts that a staged statement now covers.

    An alert is a one-line SMS about a payment; the statement covering that
    date is the reconciled record of the same money. Counting both is counting
    it twice, and this is where that is prevented - in staging, before either
    reaches the ledger, rather than after both are already in it.

    Recomputed from scratch every time rather than accumulated, so unticking
    the statement that superseded an alert brings the alert back.
    """
    entries = all_entries(db)
    statements = [e for e in entries
                  if e["kind"] == "statement" and e["selected"]
                  and e["parse_status"] == "ok" and e["account_key"]]
    alerts = [e for e in entries if e["kind"] == "alert"]
    if not alerts:
        return 0

    updates: list[tuple[str | None, str]] = []
    for alert in alerts:
        covering = None
        for statement in statements:
            if statement["account_key"] != alert["account_key"]:
                continue
            start, end = statement["period_start"], statement["period_end"]
            when = alert["period_start"] or alert["period_end"]
            if not (start and end and when):
                continue
            if start <= when <= end:
                covering = statement["id"]
                break
        if covering != alert["superseded_by"]:
            updates.append((covering, alert["id"]))

    if updates:
        with db.connection() as conn:
            conn.executemany(
                "UPDATE staged_files SET superseded_by = ? WHERE id = ?", updates)
    return len(updates)


def counts(db: Database) -> dict[str, int]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total,"
            "       SUM(selected) AS selected,"
            "       SUM(CASE WHEN parse_status = 'ok' THEN 1 ELSE 0 END) AS parsed,"
            "       SUM(CASE WHEN parse_status NOT IN ('ok','empty') THEN 1 ELSE 0 END) AS pending,"
            "       SUM(CASE WHEN superseded_by IS NOT NULL THEN 1 ELSE 0 END) AS superseded"
            " FROM staged_files").fetchone()
    return {k: int(row[k] or 0) for k in
            ("total", "selected", "parsed", "pending", "superseded")}
