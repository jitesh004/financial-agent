"""Turn an ExtractionResult into a Statement of canonical Transactions.

This is where format-agnostic rows become finance. The work is:

  1. pick the transaction table(s) out of everything the extractor found
  2. merge tables that are continuations of each other (multi-page PDFs)
  3. recover a header, from the table, a sibling block, or the data itself
  4. parse each row, applying the account's sign convention
  5. hand back warnings rather than silently dropping rows

The design rule throughout: a row we cannot parse confidently is reported, not
guessed at. A dropped row shows up in the reconciliation gate as a discrepancy,
which is exactly the signal we want.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal

from ..models.schemas import (Account, AccountType, Direction, ExtractedTable,
                              ExtractionResult, LIABILITY_TYPES, SourceFormat,
                              Statement, Transaction)
from .column_map import (ColumnMapping, find_header_row, infer_roles_from_data,
                         looks_like_header, map_columns)
from .metadata import StatementMetadata, extract_metadata
from .parsers import (infer_date_order, normalize_description, parse_amount,
                      parse_date, parse_signed_amount, redact_account_numbers)

log = logging.getLogger(__name__)

#: A table needs at least this many parseable rows to be the transaction table.
#: One is permitted because a genuinely quiet month has a single transaction,
#: and a candidate must ALSO map to date + description + amount before it gets
#: this far, which excludes summary blocks.
MIN_PARSEABLE_ROWS = 1


class NormalizationError(Exception):
    """Raised only when no transaction table could be identified at all."""


def _apply_balance_to_account(account: Account, account_type: AccountType,
                              statement: Statement) -> None:
    """Copy a statement's closing balance onto its account, dated.

    The date is what makes this safe to merge later: statements do not
    always arrive in chronological order (Gmail search, a batch upload, a
    single-file retry can all process an old month after a newer one), and
    `graph.nodes._merge_account_facts` needs to know which of two candidate
    balances is actually the more recent one rather than assuming whichever
    file happened to be seen first.
    """
    if statement.closing_balance is None:
        return
    if account_type in LIABILITY_TYPES:
        account.principal_outstanding = statement.closing_balance
    else:
        account.current_balance = statement.closing_balance
    account.balance_as_of = statement.period_end


def normalize(
    extraction: ExtractionResult,
    filename: str,
    account_type_hint: AccountType | None = None,
    sender: str = "",
) -> tuple[Statement, Account]:
    """Convert extracted tables into a Statement plus the Account it belongs to.

    `sender` (the email address a Gmail-sourced file arrived from) is passed
    straight through to extract_metadata, where it overrides body-text
    institution detection when the two disagree - see the comment there.
    """

    meta = extract_metadata(
        _metadata_text(extraction), filename=filename, sender=sender
    )
    account_type = account_type_hint or meta.account_type or AccountType.UNKNOWN

    statement = Statement(
        source_filename=filename,
        source_format=extraction.source_format,
        extractor_used=extraction.extractor_used,
        parse_warnings=list(extraction.warnings),
        period_start=meta.period_start,
        period_end=meta.period_end,
        opening_balance=meta.opening_balance,
        closing_balance=meta.closing_balance,
    )
    statement.parse_warnings.extend(meta.notes)

    account = Account(
        institution=meta.institution or "Unknown",
        account_type=account_type,
        account_number_masked=meta.account_number_masked or "",
        product_name=meta.product_name,
        holder_name=meta.holder_name,
        currency=meta.currency,
        interest_rate=meta.interest_rate,
        emi_amount=meta.emi_amount,
        credit_limit=meta.credit_limit,
    )
    # A statement can legitimately contain nothing. slice sends a monthly
    # statement for a dormant account that says "No transactions found" with
    # every total at zero. Reporting that as a parse FAILURE is wrong: nothing
    # failed, and it sends the user hunting for a bug that isn't there.
    if _looks_empty(extraction.full_text):
        statement.extra["empty_statement"] = True
        statement.parse_warnings.append(
            "This statement reports no transactions for the period."
        )
        _apply_balance_to_account(account, account_type, statement)
        return statement, account

    # Resolves a bare "June 18" - American Express prints every transaction
    # date with no year, trusting the statement period (printed once,
    # elsewhere - see metadata.detect_period) to supply it. Needed starting
    # here, not just for the final row conversion below: a column of
    # otherwise-unparseable dates is one `_rank_tables` cannot recognise as
    # the date column at all, which fails the table before a single row is
    # ever converted. Preferring period_end over period_start is a
    # simplification - exactly right for a statement whose period does not
    # cross a calendar year boundary, which is every case actually seen.
    default_year = None
    if meta.period_end:
        default_year = meta.period_end.year
    elif meta.period_start:
        default_year = meta.period_start.year

    examples = sum(1 for t in extraction.tables if _is_worked_example(t))
    candidates = _rank_tables(extraction.tables, default_year=default_year)
    if examples:
        statement.parse_warnings.append(
            f"Ignored {examples} table(s) that the document itself labels as "
            f"an illustration - the worked example in the terms and "
            f"conditions, not your transactions."
        )
    if not candidates:
        statement.parse_warnings.append(
            "No table in this file looked like a transaction list."
        )
        _apply_balance_to_account(account, account_type, statement)
        return statement, account

    best_mapping, chosen, chosen_body = candidates[0]
    merged_rows = _merge_continuations(chosen, chosen_body, candidates, best_mapping)

    transactions, warnings = _rows_to_transactions(
        merged_rows, best_mapping, account_type, meta.currency,
        opening_balance=meta.opening_balance, default_year=default_year,
    )
    statement.transactions = transactions
    statement.parse_warnings.extend(warnings)
    _drop_rows_after_period(statement, meta.period_end)
    _drop_outlier_dates(statement)

    if best_mapping.inferred_from_data:
        statement.parse_warnings.append(
            "Column headers were missing, so column roles were inferred from the "
            "data. Verify debit/credit assignment before trusting totals."
        )

    _infer_period_from_rows(statement)
    _infer_balances_from_rows(statement, account_type)
    # After inference, not before it: a statement whose letterhead omits the
    # closing balance still has one derived from the last row's running
    # balance, and that derived figure must reach the account too.
    _apply_balance_to_account(account, account_type, statement)
    return statement, account


#: Phrases an issuer prints when an account had no activity at all.
_EMPTY_STATEMENT = re.compile(
    r"no\s+transactions?\s+(found|for\s+this\s+period|during)"
    r"|nil\s+transactions?"
    r"|there\s+are\s+no\s+transactions"
    r"|no\s+activity\s+(in|for)",
    re.IGNORECASE,
)


def _looks_empty(text: str) -> bool:
    """Whether the document itself declares that it holds no transactions."""
    return bool(text) and bool(_EMPTY_STATEMENT.search(text))


#: How much of the document counts as "letterhead" for metadata purposes.
_HEAD_LINES = 45
_TAIL_LINES = 15


def _metadata_text(extraction: ExtractionResult) -> str:
    """The slice of the document that plausibly contains statement metadata.

    Deliberately NOT the whole document. Transaction narrations are full of
    phrases that look like metadata - a savings statement contains "HOME LOAN
    EMI" on every EMI row, and matching that would relabel the entire account.
    Metadata lives in the letterhead at the top; totals sometimes appear in a
    footer, so we keep the tail too and drop everything in between.
    """
    text = extraction.full_text or "\n".join(
        t.surrounding_text for t in extraction.tables[:2]
    )
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) <= _HEAD_LINES + _TAIL_LINES:
        return "\n".join(lines)
    return "\n".join(lines[:_HEAD_LINES] + lines[-_TAIL_LINES:])


# --------------------------------------------------------------------------
# Table selection
# --------------------------------------------------------------------------

#: Phrases that mark a table as a WORKED EXAMPLE rather than a ledger.
#:
#: Indian card issuers are required to print a "Most Important Terms and
#: Conditions" section, and it demonstrates how interest and minimum payment
#: are calculated using a fictional statement - dates, merchants, amounts and
#: all. ICICI's HPCL card prints two of them, and they extract exactly like a
#: transaction table because that is what they are drawn as.
#:
#: Judged on shape alone they beat the real one: on that statement the ledger
#: held two rows and the illustration held twenty-four, so the file parsed to
#: a 1-rupee "urchase on" and a 5-rupee "ayment on" dated 2023 - three years
#: outside its own period - while the two genuine transactions were never seen.
#:
#: Matched against text with the whitespace removed, because PDF extraction
#: splits words across cells ("e i llustration"). "illustration" is specific
#: enough that no accidental run of characters forms it.
_EXAMPLE_MARKERS = (
    "illustration",
    "illustrative",
    "mostimportanttermsandconditions",
    "samplestatement",
    "hypothetical",
)


def _squeezed(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def _is_worked_example(table: ExtractedTable) -> bool:
    """Does this table announce itself as an example rather than a record?

    Reads the text around the table and its own opening rows - the two places
    the caption lives. Not the whole table: a merchant legitimately called
    "Illustration Studio" three hundred rows down must not disqualify a real
    ledger.
    """
    context = _squeezed(getattr(table, "surrounding_text", "") or "")
    rows = list(getattr(table, "rows", None) or [])
    heading = _squeezed(" ".join(
        str(cell or "") for row in rows[:3] for cell in row))
    return any(marker in context or marker in heading
               for marker in _EXAMPLE_MARKERS)


def _rank_tables(
    tables: list[ExtractedTable], default_year: int | None = None,
) -> list[tuple[ColumnMapping, ExtractedTable, list[list[str]]]]:
    """Score every extracted table and return the usable ones, best first.

    A statement file typically contains several tables: a metadata block, the
    transactions, and sometimes a summary. We want the one that maps cleanly to
    transaction roles and has the most rows.

    Each entry carries its own resolved body alongside the original
    `ExtractedTable` rather than writing the body back onto `table.rows` -
    `extract()` builds a fresh `ExtractionResult` per call, but a caller can
    still hand the SAME result to `normalize()` more than once (files_routes's
    fetch-one-month flow normalizes once to check account identity, then again
    inside the merge). Overwriting `table.rows` here made the second call
    resolve against the first call's already-stripped, already-merged output
    instead of the original extraction - harmless when that happened to
    resolve the same way again, silently wrong the moment it didn't.
    """
    from collections import defaultdict

    entries: list[tuple[ColumnMapping, ExtractedTable, list[list[str]], int]] = []
    for table in tables:
        if _is_worked_example(table):
            # Dropped outright rather than scored down. A worked example is
            # not a weaker record of the same money - it is a record of money
            # that never moved, and preferring nothing to it is the whole
            # point of the reconciliation gate.
            log.debug("skipping a worked example table")
            continue
        mapping, body = _resolve_mapping(table, default_year=default_year)
        if mapping is None or not mapping.is_usable():
            continue
        parseable = _count_parseable(body, mapping, default_year=default_year)
        if parseable < MIN_PARSEABLE_ROWS:
            continue
        entries.append((mapping, table, body, parseable))

    # Group pages of the same logical table before scoring.
    #
    # Scoring individual tables is unfair across extraction strategies: a ruled
    # statement yields one table PER PAGE (45 rows each), while text-line
    # recovery yields a single table for the whole document (392 rows). Judged
    # row-by-row the text version always wins, even when the ruled version is
    # cleaner - and a lower-quality parse that still "works" is exactly the kind
    # of silent wrongness this pipeline exists to prevent.
    groups: dict[tuple, list[tuple[ColumnMapping, ExtractedTable, list[list[str]], int]]] = defaultdict(list)
    for mapping, table, body, parseable in entries:
        key = (tuple(sorted(mapping.roles.items())), table.source_sheet)
        groups[key].append((mapping, table, body, parseable))

    scored: list[tuple[float, list]] = []
    for members in groups.values():
        total_rows = sum(p for _, _, _, p in members)
        mapping = members[0][0]
        best_confidence = max(t.confidence for _, t, _, _ in members)
        # Extraction confidence is weighted heavily enough to break a row-count
        # tie: when a ruled table and a text-recovered table both cover the same
        # rows, the structured one is materially more trustworthy.
        #
        # The description term is worth about two and a half rows, and it is
        # there because a table with dates and amounts but NO description is
        # usually not a ledger at all - it is a summary block, and the mapping
        # over it is a guess. IDFC's statement offered both:
        #
        #   t1  date | description                    | amount
        #       29 Jul 26 | BillDesk BBPS CC Payment… | 277.76 CR
        #
        #   t3  date | ...six empty columns... | amount
        #       29 Jul 26 |                    | 277.7
        #
        # t3 held one more parseable row, won on row count alone, and turned
        # the month's only transaction into an unnamed 277.7 DEBIT - wrong
        # description, wrong amount, wrong direction, from a statement whose
        # real row was sitting right there fully intact.
        #
        # Deliberately a constant, not a multiplier: it decides near-ties and
        # nothing else. A forty-row ledger whose description column was missed
        # still comfortably outscores a one-row table that happens to have one.
        has_description = "description" in mapping.roles

        # What fraction of the table is actually transactions.
        #
        # A ledger is mostly ledger. When a PDF's ruled table cannot be found,
        # the extractor falls back to recovering the page as text, and that
        # produces a table of the WHOLE PAGE - headings, marketing, footers -
        # in which a couple of rows happen to parse. IDFC's July statement
        # offered exactly that:
        #
        #   t1   28 Jun 26 | DISTRICT MOVIE TICKE, NEW DELHI | 277.76 DR
        #
        #   t3   YOUR CA | RD INFO | RMATI | ON
        #        Enjoy t | he Conve | nience of | flexible pay | ments!
        #        28 Jun 26 | | DISTRICT | MOVIE TICKE, N | EW DELHI | | | 277.7
        #        …thirteen more rows of page furniture
        #
        # t3 is the page cut into columns that fall in the middle of words -
        # and, fatally, in the middle of the amount: 277.76 became 277.7,
        # sixteen rows of noise carrying one silently truncated figure. It won
        # because it had two parseable rows against t1's one.
        #
        # One row in one is worth more than two rows in sixteen, and that is
        # what this term says.
        total_body = sum(len(b) for _, _, b, _ in members) or 1
        density = min(total_rows / total_body, 1.0)
        score = (total_rows * 10
                 + mapping.confidence * 20
                 + best_confidence * 40
                 + (25 if has_description else 0)
                 + density * 30)
        scored.append((score, members))

    scored.sort(key=lambda s: -s[0])
    return [(m, t, b) for _, members in scored for m, t, b, _ in members]


def _resolve_mapping(
    table: ExtractedTable, default_year: int | None = None,
) -> tuple[ColumnMapping | None, list[list[str]]]:
    """Find this table's column mapping and return its data rows (header removed).

    Tries, in order: an explicit header field, a header row inside the table,
    then inference from cell content.

    `default_year` resolves a bare "June 18" the same way it does for the
    final row conversion (see parsers.parse_date) - needed here too, because
    a column of otherwise-unparseable dates is a column `infer_roles_from_data`
    cannot recognise as the date column at all, and a table with no
    recognised date column never passes `is_usable()` in the first place.
    """
    rows = table.rows
    if not rows:
        return None, []

    if table.header:
        mapping = map_columns(table.header)
        if mapping.is_usable():
            return mapping, rows

    header_idx = find_header_row(rows, default_year=default_year)
    if header_idx is not None:
        mapping = map_columns(rows[header_idx])
        if mapping.is_usable():
            return mapping, rows[header_idx + 1:]

    inferred = infer_roles_from_data(rows, default_year=default_year)
    if inferred.is_usable() and not _has_truncated_amounts(rows, inferred):
        # Drop any leading header-looking row so it isn't parsed as data.
        body = rows[1:] if looks_like_header(rows[0], default_year=default_year) else rows
        return inferred, body

    # Tried whenever the plain inference either failed outright or produced a
    # mapping whose own money columns look truncated: a `stream`-strategy
    # extraction (see ingestion.extractors) can place a page-wide column
    # boundary in the middle of a right-aligned number's digits rather than
    # before or after it. The split point then differs row to row, because
    # each row's amount is a different width - "5,399.00" becomes "5,3" /
    # "99.00" in one row, "648,912.00" becomes "64" / "8,912.00" in another.
    # Gating this on `_has_truncated_amounts` rather than on `is_usable()`
    # alone matters because a split-amount table can still satisfy
    # `is_usable()` - the leading half of a split number is still numeric and
    # still fills the column densely, so it reads as a perfectly good money
    # role right up until you look at what actually landed in it. Requiring
    # the plain reading to ALSO look intact before trusting it is what keeps
    # this from ever touching a genuinely split debit/credit layout - the
    # common, currently-working case for every other bank - since that always
    # reads as intact without any repair.
    repaired_rows = _repair_split_amounts(rows)
    if repaired_rows is not rows:
        repaired_inferred = infer_roles_from_data(repaired_rows, default_year=default_year)
        if repaired_inferred.is_usable() and not _has_truncated_amounts(repaired_rows, repaired_inferred):
            body = (repaired_rows[1:]
                    if looks_like_header(repaired_rows[0], default_year=default_year)
                    else repaired_rows)
            return repaired_inferred, body

    # Repair either found nothing to fix or didn't produce anything cleaner -
    # fall back to the plain reading if it was at least usable, rather than
    # discarding the whole table over a money column that merely looks
    # suspicious.
    if inferred.is_usable():
        body = rows[1:] if looks_like_header(rows[0], default_year=default_year) else rows
        return inferred, body

    return None, rows


#: A cell with digits (and thousands commas) but NO decimal point. A real,
#: complete Indian-currency amount is essentially always printed with two
#: decimals even for a whole-rupee figure - a cell missing that decimal is
#: the tell that it is the LEADING half of a number a column boundary cut
#: through, not a complete amount in its own column.
_BARE_DIGITS = re.compile(r"^[\d,]+$")
#: The trailing half of a split amount: digits ending in exactly two
#: decimals, short enough to plausibly be the tail of a larger number.
_DECIMAL_TAIL = re.compile(r"^\d{1,3}\.\d{2}$")


def _repair_split_amounts(rows: list[list[str]]) -> list[list[str]]:
    """Rejoin an amount a `stream` extraction split across two adjacent cells.

    The merged value is written into the RIGHT-hand cell of the pair, not the
    left, and the left is cleared instead. A row whose amount was never split
    in the first place already has it sitting in that same trailing cell -
    writing the repaired value there too, rather than into the leading cell,
    is what lets both kinds of row end up with their one true amount in the
    same column, so a single money role can pick up both. Merging into the
    left cell instead would still repair each split row's own value
    correctly, but scatter the results across two different columns
    depending on whether a given row happened to need repair, leaving no one
    column dense enough for `infer_roles_from_data` to recognise as the
    money column at all.

    Returns `rows` itself, unchanged, when nothing needed repair - callers
    rely on `is` to tell whether anything actually happened.
    """
    changed = False
    repaired: list[list[str]] = []
    for row in rows:
        new_row = list(row)
        i = 0
        while i < len(new_row) - 1:
            left = str(new_row[i] or "").strip()
            right = str(new_row[i + 1] or "").strip()
            if left and _BARE_DIGITS.match(left) and _DECIMAL_TAIL.match(right):
                new_row[i + 1] = left + right
                new_row[i] = ""
                changed = True
                i += 2
            else:
                i += 1
        repaired.append(new_row)
    return repaired if changed else rows


def _has_truncated_amounts(rows: list[list[str]], mapping: ColumnMapping) -> bool:
    """True when a mapping's own money columns look like split-amount halves.

    A mapping can satisfy `is_usable()` while still being wrong: the "clear
    winner" date-column carve-out in `infer_roles_from_data` only checks the
    date column's own hit rate, so a table whose amounts were split across
    two adjacent cells by a `stream` extraction (see `_repair_split_amounts`)
    can still resolve to a mapping that assigns debit/credit to the LEADING
    halves of those splits - present, dense and numeric, so indistinguishable
    from a real money column by every check upstream of this one. The tell is
    what actually landed in the column: a genuine amount is essentially
    always printed with two decimals, even for a whole-rupee figure, so a
    money column mostly full of bare digits with no decimal point at all is
    the same signal `_repair_split_amounts` looks for, just checked after the
    fact instead of before.
    """
    money_cols = [c for c in (mapping.get("debit"), mapping.get("credit"),
                              mapping.get("amount")) if c is not None]
    if not money_cols:
        return False
    total = bare = 0
    for row in rows:
        for c in money_cols:
            if c >= len(row):
                continue
            cell = str(row[c] or "").strip()
            if not cell:
                continue
            total += 1
            if _BARE_DIGITS.match(cell):
                bare += 1
    return total > 0 and bare >= total * 0.3


def _count_parseable(rows: list[list[str]], mapping: ColumnMapping,
                     default_year: int | None = None) -> int:
    date_col = mapping.get("txn_date")
    if date_col is None:
        return 0
    count = 0
    for row in rows:
        if date_col < len(row) and parse_date(row[date_col], default_year=default_year) is not None:
            count += 1
    return count


def _project_row(row: list[str], from_mapping: ColumnMapping,
                 to_mapping: ColumnMapping) -> list[str]:
    """Rebuild `row` so each role's value sits at `to_mapping`'s column index.

    A continuation page can resolve to the same set of roles at DIFFERENT
    column positions than the page chosen to represent the whole statement -
    the split-amount repair (see _repair_split_amounts) is one concrete way
    this happens: it only engages on a page that actually needed it, so that
    page's "amount" can land at a different index than an unaffected page's.
    Concatenating raw rows across such pages and reading them all through one
    fixed set of column indices would silently read some pages' amounts out
    of the wrong column - empty, or someone else's data - rather than
    failing loudly. Projecting each continuation page's rows into the chosen
    page's layout first is what keeps the merge honest.
    """
    width = max([*to_mapping.roles.values(), len(row) - 1], default=-1) + 1
    new_row = [""] * width
    for role, to_idx in to_mapping.roles.items():
        from_idx = from_mapping.get(role)
        if from_idx is not None and from_idx < len(row):
            new_row[to_idx] = row[from_idx]
    return new_row


def _merge_continuations(
    chosen: ExtractedTable,
    chosen_body: list[list[str]],
    candidates: list[tuple[ColumnMapping, ExtractedTable, list[list[str]]]],
    mapping: ColumnMapping,
) -> list[list[str]]:
    """Stitch together tables that are continuations of the chosen one.

    A 40-page PDF statement yields ~40 tables with identical structure. Treating
    only the first as "the" transaction table would silently discard 97% of the
    user's data - a failure mode that produces a plausible-looking but
    completely wrong analysis, which is worse than an obvious crash.

    An exact match on role INDICES (not just names) is trusted from anywhere
    in the document - the common multi-page case, where every page was
    extracted the same way and lines up column for column. A same-NAME,
    different-INDEX match is trusted only when it directly extends a run of
    pages already merged (see _project_row): a later page can resolve to the
    same roles at a different position - the split-amount repair only
    engages on a page that actually needed it, so its "amount" can land
    somewhere another page's does not - without being a different table at
    all. Anywhere else in the document, a same-shaped-but-differently-indexed
    table is far more likely to be an unrelated block - a rewards summary, a
    credit-limit recap - that happens to have a date-like, text-like and
    number-like column without being a continuation of anything. A real
    statement (data/samples/icici_credit_card_2025_2026.pdf, a full year,
    392 real transactions) had exactly such a block: requiring page adjacency
    is what keeps it from being pulled in as 37 extra rows.
    """
    merged: list[list[str]] = list(chosen_body)
    merged_pages: set[int] = {chosen.source_page} if chosen.source_page is not None else set()

    for other_mapping, table, body in candidates[1:]:
        if table is chosen:
            continue
        if set(other_mapping.roles.keys()) != set(mapping.roles.keys()):
            continue
        if chosen.source_page is not None and table.source_page is None:
            continue
        if chosen.source_sheet != table.source_sheet:
            continue
        if other_mapping.roles == mapping.roles:
            merged.extend(body)
            if table.source_page is not None:
                merged_pages.add(table.source_page)
        else:
            if table.source_page is None or not merged_pages:
                continue
            if not any(abs(table.source_page - p) == 1 for p in merged_pages):
                continue
            merged.extend(_project_row(r, other_mapping, mapping) for r in body)
            merged_pages.add(table.source_page)

    return merged


# --------------------------------------------------------------------------
# Row parsing
# --------------------------------------------------------------------------

def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


#: A row that says nothing but "CR" (or "Cr."/"CREDIT"). American Express
#: prints the credit marker on its OWN line, directly under the amount it
#: belongs to:
#:
#:     June 21   Paytm*UBERINDIASYSTEMSP  Noida        2.00
#:                                                       CR
#:     June 24   AMAZON Mumbai                        599.00
#:                                                       CR
#:
#: Nothing looks at the next row, so all three of June's credits were read as
#: charges. They came to exactly the 984.00 the statement declared as credits,
#: and booking them as spending was a two-for-one error: it inflated the month
#: and erased the refunds. The reconciliation gate caught it only once the
#: opening and closing balances could be read.
_LONE_CREDIT_MARKER = re.compile(r"^\s*(?:cr|cr\.|credit)\s*$", re.IGNORECASE)


def _fold_credit_markers(rows: list[list[str]]) -> list[list[str]]:
    """Attach a standalone CR row to the amount above it.

    The marker is appended to the amount cell rather than tracked separately,
    because `parse_amount` already understands a trailing CR - so one line here
    reuses the same convention every other issuer states inline.
    """
    if not rows:
        return rows

    folded: list[list[str]] = []
    for row in rows:
        cells = [str(c or "") for c in row]
        joined = " ".join(c for c in cells if c.strip()).strip()
        if folded and joined and _LONE_CREDIT_MARKER.match(joined):
            previous = folded[-1]
            # The amount is the last cell carrying digits.
            for index in range(len(previous) - 1, -1, -1):
                if any(ch.isdigit() for ch in str(previous[index] or "")):
                    previous[index] = f"{previous[index]} CR"
                    break
            continue
        folded.append(list(cells))
    return folded


#: A description cell holding nothing but a clock time and/or a date. Used to
#: reject a "narration" like "17:10 11 Oct 25" - see the call site.
_TIMESTAMP_ONLY = re.compile(
    r"""^\s*
        (?:\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:am|pm)?)?      # 17:10
        \s*
        (?:\d{1,2}[\s/-]*[A-Za-z]{3,9}[\s/-]*\d{2,4}        # 11 Oct 25
          |\d{1,2}[/-]\d{1,2}[/-]\d{2,4}                    # 11/10/25
          |\d{4}-\d{2}-\d{2})?                              # 2025-10-11
        \s*$""",
    re.IGNORECASE | re.VERBOSE,
)


#: A clock time at the very start of a description. HDFC prints the time of
#: each transaction, and it arrives glued to the merchant:
#:
#:     17:47 UPI-Dominos
#:     11:21 ZEPTO MARKETPLACE PRIVABANGALORE
#:
#: `extract_merchant` finds no merchant in a string that opens with digits and
#: a colon, so all 187 such rows carried an EMPTY merchant and 107 of them
#: went uncategorised - a fifth of the ledger, unreadable because of five
#: characters at the front.
_LEADING_TIME = re.compile(r"^\s*\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:am|pm)?\s+",
                           re.IGNORECASE)


def _strip_leading_time(text: str) -> str:
    """Drop a clock time from the front of a description.

    Only when something is left afterwards: a description that is nothing but
    a time is handled by `_is_only_a_timestamp`, and blanking it here would
    hide that case from the branch that knows what to do with it.
    """
    stripped = _LEADING_TIME.sub("", str(text or ""), count=1)
    return stripped if stripped.strip() else str(text or "")


def _is_only_a_timestamp(text: str) -> bool:
    """Is this "description" just a time, a date, or both?

    An empty cell is not a timestamp - the caller already has a branch for
    that - so a blank string returns False and takes the existing path.
    """
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if not any(ch.isdigit() for ch in stripped):
        return False
    return bool(_TIMESTAMP_ONLY.match(stripped))


def _rows_to_transactions(
    rows: list[list[str]],
    mapping: ColumnMapping,
    account_type: AccountType,
    currency: str,
    opening_balance: Decimal | None = None,
    default_year: int | None = None,
) -> tuple[list[Transaction], list[str]]:
    warnings: list[str] = []
    transactions: list[Transaction] = []

    rows = _fold_credit_markers(rows)

    date_col = mapping.get("txn_date")
    if date_col is None:
        return [], ["No date column identified; cannot build transactions."]

    # Settle the date convention once for the whole statement.
    day_first = infer_date_order([_cell(r, date_col) for r in rows])

    is_liability = account_type in LIABILITY_TYPES
    skipped_no_date = 0
    skipped_no_amount = 0

    skipped_balance_marker = 0

    for i, row in enumerate(rows):
        txn_date = parse_date(_cell(row, date_col), day_first=day_first,
                              default_year=default_year)
        if txn_date is None:
            skipped_no_date += 1
            continue

        raw_desc_probe = _cell(row, mapping.get("description"))
        # Brought-forward / carried-forward rows restate the opening or closing
        # BALANCE. Counting one as a transaction adds the entire account balance
        # to the period's spending - the largest single distortion possible.
        if BALANCE_MARKER_ROW.match(raw_desc_probe.strip()):
            skipped_balance_marker += 1
            continue

        amount, direction = _resolve_amount(row, mapping, is_liability)
        if amount is None:
            skipped_no_amount += 1
            continue
        if amount == 0:
            continue  # zero-value rows are statement furniture, not transactions

        raw_desc = raw_desc_probe
        if _is_only_a_timestamp(raw_desc):
            # A time and a value date are not a narration. IDFC's savings
            # statement wraps the narration around the amount line:
            #
            #     AddMoney/20252846025956/528
            #     11 Oct 25   17:10 11 Oct 25   1,000.00   1,000.00 CR
            #     478636439/UPI
            #
            # so the column beside the date holds "17:10 11 Oct 25" and the
            # words are on the lines above and below, out of reach of this
            # table. Kept as-is it becomes a merchant: a payee named
            # "17:10 11 Oct 25" appears in the ledger, gets a category, and is
            # learned into the merchant cache. Saying nothing is the honest
            # answer, and it routes the row to review instead.
            raw_desc = ""
        else:
            raw_desc = _strip_leading_time(raw_desc)
        if not raw_desc:
            raw_desc = "(no description)"
        raw_desc = redact_account_numbers(raw_desc)

        # A single-amount-column layout carries no debit/credit position, so
        # `_resolve_amount` has to default to DEBIT. Left uncorrected, every
        # salary credit is booked as spending: measured against real statements
        # this produced money-in of 1,009 against money-out of 1.01 crore.
        if not mapping.split_amount_columns:
            hinted = _direction_from_description(raw_desc, is_liability)
            if hinted is not None:
                direction = hinted

        # Signed: an overdraft or a cumulative outflow is genuinely negative.
        balance = parse_signed_amount(_cell(row, mapping.get("balance")))

        transactions.append(Transaction(
            txn_date=txn_date,
            value_date=parse_date(_cell(row, mapping.get("value_date")), day_first=day_first,
                                  default_year=default_year),
            raw_description=raw_desc,
            normalized_description=normalize_description(raw_desc),
            amount=amount,
            direction=direction,
            balance_after=balance,
            currency=currency,
            reference=_cell(row, mapping.get("reference")) or None,
            source_row=i,
        ))

    # The running balance overrides every heuristic above where it is available.
    corrected = _apply_balance_deltas(transactions, is_liability, opening_balance)
    if corrected:
        warnings.append(
            f"{corrected} transaction direction(s) corrected against the "
            f"statement's own running balance."
        )
    if skipped_balance_marker:
        warnings.append(
            f"{skipped_balance_marker} brought/carried-forward row(s) skipped - "
            f"they restate a balance rather than record a transaction."
        )

    if skipped_no_date:
        warnings.append(
            f"{skipped_no_date} row(s) had no parseable date and were skipped "
            f"(usually page headers, subtotals or footers)."
        )
    if skipped_no_amount:
        warnings.append(
            f"{skipped_no_amount} row(s) had a date but no parseable amount and "
            f"were skipped."
        )
    if not transactions:
        warnings.append("No transactions could be parsed from the selected table.")

    return transactions, warnings


#: Rows that restate a balance rather than record a movement.
BALANCE_MARKER_ROW = re.compile(
    # The abbreviations MUST carry their slash. Making it optional turned every
    # merchant starting "CF" or "BF" into a balance marker - "CF FOODS
    # BANGALORE" was silently dropped as if it were a carried-forward row.
    r"^(?:bal(?:ance)?\s*)?[bc]\s*/\s*f\b"
    r"|^(?:balance\s+)?(?:brought|carried)\s+forward\b"
    r"|^(?:opening|closing|previous)\s+balance\b"
    r"|^balance\s+as\s+on\b"
    # Card statements print their summary figures inside the transaction list.
    # HSBC's "NET OUTSTANDING BALANCE" alone added 4.28 lakh of phantom
    # spending across 11 statements - it is the balance, not a purchase.
    r"|^net\s+outstanding(\s+balance)?\b"
    r"|^(?:total|minimum)\s+amount\s+due\b"
    r"|^total\s+dues?\b|^statement\s+balance\b"
    r"|^(?:sub\s*)?total\b\s*:?\s*$",
    re.IGNORECASE,
)

#: Descriptions that mean money came IN, for layouts with a single amount column.
_CREDIT_WORDS = re.compile(
    r"\bpayment\s+received\b|\breceived\b|\brefund\b|\breversal\b|\bcashback\b"
    r"|\bcredited\b|\bcredit\b|\bsalary\b|\bsal\b|\bdividend\b|\binterest\s+cr"
    r"|\bneft[-\s]*cr\b|\bimps[-\s]*cr\b|\bby\s+transfer\b|\bdeposit\b"
    r"|\bthank\s*you\b|\brepayment\b|\bmat(urity)?\s+proceeds\b"
    # Indian payroll narrations run the tokens together, so \bsal\b never
    # matches: "PRIVATELIMI-JITESHSALNOV25//CMS3". Anchoring on SAL followed by
    # a month abbreviation and a year is specific enough not to catch "SALE".
    r"|SAL(?=(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2})"
    r"|\bSAL[-/]|[-/]SAL\b|\bSALARY\b",
    re.IGNORECASE,
)
#: A credit-card bill being settled. This is the one narration whose meaning
#: flips with the account: on the CARD it is money arriving (the balance owed
#: falls), while the very same wording on the BANK account funding it is money
#: leaving. Read as a debit on both sides, every bill payment was counted twice
#: - once as card "spend" and again as bank "spend".
#:
#: HDFC prints "BPPY CC PAYMENT DP0153...", and the user pays through CRED,
#: which lands on ICICI as "UPI/CRED Club/cred.club@axis/payment on/...".
#: '\bcred\b' cannot match inside "credit" - the word boundary requires a
#: non-word character after the d.
_CARD_BILL_PAYMENT = re.compile(
    r"\bbppy\b|\bcc\s*payment\b|\bcredit\s*card\s*payment\b|\bcard\s*payment\b"
    r"|\bcred\b|\bcred\.club\b|\bbillpay\b|\bpayment\s*[-,]?\s*thank\s*you\b",
    re.IGNORECASE,
)
#: Explicit outgoing markers, which beat a coincidental credit word.
_DEBIT_WORDS = re.compile(
    r"\bpayment\s+(made|to|of)\b|\bpurchase\b|\bwithdrawal\b|\batm\b|\bpos\b"
    r"|\bemi\b|\bcharge[sd]?\b|\bfee\b|\bdebited\b|\bdr\b",
    re.IGNORECASE,
)


def _direction_from_description(description: str, is_liability: bool) -> Direction | None:
    """Infer direction from wording when column position cannot say.

    Only consulted for single-amount-column layouts. Debit wording is checked
    first so "EMI PAYMENT RECEIVED BY BANK" is not read as money coming in.
    Returns None when the wording is genuinely ambiguous, leaving the caller's
    default in place rather than inventing a direction.
    """
    if not description:
        return None
    # Checked first, and the only place is_liability changes the answer: a bill
    # payment moves the balance in opposite directions on the card and on the
    # account paying it.
    if _CARD_BILL_PAYMENT.search(description):
        return Direction.CREDIT if is_liability else Direction.DEBIT
    if _DEBIT_WORDS.search(description) and not _CREDIT_WORDS.search(description):
        return Direction.DEBIT
    if _CREDIT_WORDS.search(description):
        return Direction.CREDIT
    return None


def _apply_balance_deltas(
    transactions: list[Transaction],
    is_liability: bool,
    opening_balance: Decimal | None = None,
) -> int:
    """Correct directions using the running-balance column.

    This is the strongest signal available: the balance moved up or it moved
    down, and the statement itself says by how much. Where a row's amount
    matches the delta, the delta decides the direction outright - no keyword
    heuristic can be as reliable. Rows whose amount does not match the delta are
    left alone, since a mismatch means something else is going on.

    Returns how many directions it corrected.
    """
    corrected = 0
    # Seed with the statement's declared opening balance. Without it the FIRST
    # transaction can never be checked - and on a quiet month with a single
    # transaction that means no check at all. IDFC statements with one ~2.00 row
    # were failing reconciliation by exactly twice that amount (the direction
    # flip signature) purely because there was no previous balance to compare to.
    previous: Decimal | None = opening_balance

    for txn in transactions:
        current = txn.balance_after
        if current is None:
            previous = None
            continue
        if previous is not None:
            delta = current - previous
            if abs(abs(delta) - txn.amount) <= Decimal("0.01") and delta != 0:
                # On a liability, a rising balance means money was spent.
                rising = delta > 0
                wanted = (Direction.DEBIT if rising else Direction.CREDIT) \
                    if is_liability else \
                    (Direction.CREDIT if rising else Direction.DEBIT)
                if txn.direction != wanted:
                    txn.direction = wanted
                    corrected += 1
        previous = current

    return corrected


def _resolve_amount(
    row: list[str],
    mapping: ColumnMapping,
    is_liability: bool,
) -> tuple[Decimal | None, Direction]:
    """Determine a row's amount and direction.

    Precedence, strongest signal first:
      1. separate debit/credit columns - unambiguous
      2. an explicit Cr/Dr annotation inside the amount cell
      3. a type/indicator column
      4. sign of a single amount column

    For liability accounts the columns mean the opposite thing: a "debit" on a
    credit card statement is a purchase, which is money leaving the user. We
    normalize to the user's point of view, so a card purchase is a DEBIT and a
    card payment received is a CREDIT.
    """
    if mapping.split_amount_columns:
        debit = parse_amount(_cell(row, mapping.get("debit"))).value
        credit = parse_amount(_cell(row, mapping.get("credit"))).value

        if debit and credit:
            # Both filled is contradictory; the larger is the real movement.
            if debit >= credit:
                credit = None
            else:
                debit = None

        if debit:
            return debit, Direction.DEBIT
        if credit:
            # On a liability statement, a credit-column entry is a bill payment
            # or refund flowing back to the user's benefit.
            return credit, Direction.CREDIT
        return None, Direction.DEBIT

    amount_col = mapping.get("amount")
    parsed = parse_amount(_cell(row, amount_col))
    if parsed.value is None:
        return None, Direction.DEBIT

    direction = Direction.DEBIT
    if parsed.explicit_direction == "credit":
        direction = Direction.CREDIT
    elif parsed.explicit_direction == "debit":
        direction = Direction.DEBIT
    else:
        type_text = _cell(row, mapping.get("type")).lower()
        if type_text:
            if any(k in type_text for k in ("cr", "credit", "deposit", "in")):
                direction = Direction.CREDIT
            elif any(k in type_text for k in ("dr", "debit", "withdraw", "out")):
                direction = Direction.DEBIT

    if is_liability:
        # A positive amount on a card statement is a charge against the user.
        direction = Direction.DEBIT if direction == Direction.DEBIT else Direction.CREDIT

    return parsed.value, direction


# --------------------------------------------------------------------------
# Post-parse inference
# --------------------------------------------------------------------------

def _drop_rows_after_period(statement: Statement, period_end: date | None) -> None:
    """Remove "transactions" dated after the statement's own closing date.

    A statement cannot contain activity that has not happened yet, so a later
    date means the row came from the summary block rather than the ledger.
    HSBC heads every statement with its payment due date and total amount due:

        08 DEC 2025 6,831.64
        MR JITESH MUKESH AGARWAL

    The extractor reads line one as a dated row with a trailing amount and,
    finding no description, borrows the cardholder's name from the line below -
    inventing a 6,831.64 purchase from the cardholder, two weeks after the
    statement closed. One appeared in every HSBC statement.

    The lower bound is far looser than the upper one, and deliberately so. A
    card's TRANSACTION date legitimately precedes the period start when a
    purchase posts late, so a row a few months early is real data. A row YEARS
    early is not: it is a mis-read date, and one of them poisons every figure
    derived from the ledger's span.

    A real example, from an IDFC statement: the line ": 01-MAY-2026 to 3..."
    is the printed statement period, and reading it as a row produced a 6-rupee
    transaction dated in the year 0202. That single row stretched the
    dashboard's range to 21,892 months and turned "average per month" from
    about eighty-six thousand rupees into four.
    """
    if period_end is None or not statement.transactions:
        return

    # Generous: a late-posting purchase, or a card that bills a quarter in
    # arrears, is still real.
    earliest = date(period_end.year - 2, period_end.month, 1)

    kept, late, ancient = [], 0, 0
    for txn in statement.transactions:
        if txn.txn_date > period_end:
            late += 1
        elif txn.txn_date < earliest:
            ancient += 1
        else:
            kept.append(txn)

    if not (late or ancient):
        return
    statement.transactions = kept
    if late:
        statement.parse_warnings.append(
            f"Ignored {late} row(s) dated after the statement period ended "
            f"({period_end}) - these are summary figures such as the payment "
            f"due date, not transactions."
        )
    if ancient:
        statement.parse_warnings.append(
            f"Ignored {ancient} row(s) dated before {earliest} - more than two "
            f"years before this statement closed, so the date was misread "
            f"rather than early."
        )


#: An 8-digit DDMMYYYY run, delimited so it cannot start mid-number. The
#: account number in "20000002170971_22082026_115345421.pdf" is 14 digits and
#: would otherwise offer several plausible-looking dates.
_DDMMYYYY = re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)")


def _ddmmyyyy_in(filename: str) -> str | None:
    """"YYYY-MM" from a DDMMYYYY run in a filename, if one reads as a date."""
    for day, month, year in _DDMMYYYY.findall(filename):
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return f"{year}-{month}"
    return None


def _anchor_from_filename(filename: str | None) -> date | None:
    """The month this file is named for, as a date, or None.

    Reuses the coverage grid's own filename reader so the two never disagree
    about what "20000002170971_22082026_115345421.pdf" is a statement for.
    """
    if not filename:
        return None
    try:
        from ..analytics.coverage import guess_period_hint
        hint = guess_period_hint(filename)
    except Exception:  # pragma: no cover - a hint is never load-bearing
        return None
    if not hint:
        # The coverage grid only knows YYYYMM-style names. IDFC names its
        # files with the statement date the other way round -
        # "20000002170971_22082026_115345421.pdf" is 22 Aug 2026 - and that
        # was the one file whose junk row had no other anchor to catch it.
        hint = _ddmmyyyy_in(filename)
    if not hint:
        return None
    try:
        year, month = (int(part) for part in hint.split("-")[:2])
        return date(year, month, 1)
    except (TypeError, ValueError):
        return None


def _drop_outlier_dates(statement: Statement) -> None:
    """Drop rows dated years away from the rest of the same statement.

    The period guard above needs a declared period to anchor on, and plenty of
    statements print none. Those fall back to inferring the period FROM the
    rows - so a single misread date silently becomes the period, and the whole
    ledger's span with it.

    The anchor here is the median row date, which a handful of bad rows cannot
    move. Two real examples from one import: a 1-rupee row dated 2023 whose
    description was "urchase on" (the tail of "purchase on", read out of a
    footnote), and a 2,622-rupee row dated 2020 with no description at all,
    both on statements covering a single month of 2026.
    """
    rows = statement.transactions
    if not rows:
        return

    # Anchors in order of how much they can be trusted. The first two do not
    # come from the rows at all, which is the whole point: a statement with
    # one transaction on it has no middle to take a median of, and the row
    # count stops mattering as soon as the anchor comes from somewhere else.
    #
    #   1. the period the statement itself prints
    #   2. the date in the filename ("..._22082026_..." is August 2026)
    #   3. the median row date, which needs three rows to mean anything
    anchor = statement.period_end or statement.period_start
    if anchor is None:
        anchor = _anchor_from_filename(statement.source_filename)

    if anchor is None:
        if len(rows) < 3:
            # Nothing outside the rows to check them against, and no
            # trustworthy middle: discarding real data on a coin flip is
            # worse than keeping one bad date.
            return
        ordered = sorted(t.txn_date for t in rows)
        anchor = ordered[len(ordered) // 2]

    median = anchor
    floor = date(median.year - 2, 1, 1)
    ceiling = date(median.year + 2, 12, 31)

    kept = [t for t in rows if floor <= t.txn_date <= ceiling]
    dropped = len(rows) - len(kept)
    if not dropped:
        return
    statement.transactions = kept
    statement.parse_warnings.append(
        f"Ignored {dropped} row(s) dated years away from the rest of this "
        f"statement (most rows sit around {median}) - a misread date, not a "
        f"transaction."
    )


def _infer_period_from_rows(statement: Statement) -> None:
    """Fill a missing statement period from the transactions themselves."""
    if not statement.transactions:
        return
    dates = [t.txn_date for t in statement.transactions]
    if statement.period_start is None:
        statement.period_start = min(dates)
    if statement.period_end is None:
        statement.period_end = max(dates)


def _infer_balances_from_rows(statement: Statement, account_type: AccountType) -> None:
    """Derive opening/closing balances from the running-balance column.

    Only used when the letterhead didn't declare them. Deriving the opening
    balance from the first row's running balance is safe: opening = balance
    after row 1, minus row 1's own effect.
    """
    txns = statement.transactions
    if not txns:
        return

    if account_type == AccountType.INVESTMENT:
        # An investment statement's trailing numeric column is units or
        # cumulative cost, not a cash balance. Deriving a "balance" from it
        # produces a number that looks authoritative and means nothing.
        statement.parse_warnings.append(
            "Investment statement: no cash balance to reconcile against."
        )
        return

    with_balance = [t for t in txns if t.balance_after is not None]
    if not with_balance:
        return

    is_liability = account_type in LIABILITY_TYPES

    if statement.opening_balance is None:
        first = with_balance[0]
        effect = first.signed_amount
        if is_liability:
            effect = -effect
        statement.opening_balance = first.balance_after - effect
        statement.parse_warnings.append(
            "Opening balance was not stated; derived from the first row's "
            "running balance."
        )

    if statement.closing_balance is None:
        statement.closing_balance = with_balance[-1].balance_after
        if not is_liability:
            statement.extra["derived_current_balance"] = str(statement.closing_balance)
        statement.parse_warnings.append(
            "Closing balance was not stated; taken from the last row's running "
            "balance."
        )
