"""A whitelisted query engine over the ledger.

The Explore tab lets a user assemble arbitrary questions - any dimension, any
measure, any filter, any combination. The obvious way to build that is to let
something generate SQL. This module deliberately does not.

Every dimension, measure, aggregation and operator is declared here as a
constant. A request names them by key; nothing a caller sends ever reaches the
database as SQL. A key that is not in the registry is rejected, so the widest
possible query surface is still a closed set - which is what makes it safe to
expose an endpoint that takes a query description as JSON.

The second rule is the project's own: no figure is produced by anything other
than exact arithmetic. `amount` is stored as TEXT holding a decimal, and
summing that as a float accumulates error across a few thousand rows. Every
money measure is therefore converted to INTEGER PAISE inside SQL and summed as
an integer, then divided by 100 exactly once at the end. See `_paise`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from ..models.schemas import CATEGORY_GROUPS, Category
from . import periods

# --------------------------------------------------------------------------
# Money, exactly
# --------------------------------------------------------------------------


def _paise(column: str) -> str:
    """A TEXT decimal column as an integer number of paise.

    The float multiply is an intermediate only: a float64 represents every
    rupee-and-paise value in this domain far more precisely than half a paisa,
    so ROUND lands on the exact integer. From there the aggregation is integer
    arithmetic and cannot drift, however many rows it spans.
    """
    return f"CAST(ROUND(CAST({column} AS REAL) * 100) AS INTEGER)"


#: Signed: money in is positive, money out is negative.
_SIGNED = (
    f"CASE WHEN t.direction = 'credit' THEN {_paise('t.amount')} "
    f"ELSE -{_paise('t.amount')} END"
)


def _category_group_case() -> str:
    """CASE mapping each category to the dashboard's own grouping.

    Built from CATEGORY_GROUPS rather than restated, so a category added to a
    group in schemas.py appears here without a second edit.
    """
    whens = []
    for group, categories in CATEGORY_GROUPS.items():
        names = ", ".join(f"'{c}'" for c in categories)
        whens.append(f"WHEN t.category IN ({names}) THEN '{group}'")
    return "CASE " + " ".join(whens) + " ELSE 'Other' END"


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

FieldType = Literal["text", "month", "date", "money", "number", "bool"]


@dataclass(frozen=True)
class Field:
    """Something you can group by, filter on, or both."""

    key: str
    label: str
    group: str
    sql: str
    type: FieldType = "text"
    groupable: bool = True
    filterable: bool = True
    #: Key into the options map returned by /api/query/schema. None means the
    #: UI should offer a free-text match rather than a picker - a merchant
    #: list runs to thousands of entries and is useless as a dropdown.
    options: str | None = None
    hint: str = ""


@dataclass(frozen=True)
class Measure:
    """Something you can compute. `sql` is the per-row value to aggregate."""

    key: str
    label: str
    group: str
    sql: str
    type: FieldType = "money"
    aggs: tuple[str, ...] = ("sum", "avg", "min", "max")
    hint: str = ""


_ACCOUNT_LABEL = (
    "COALESCE(a.institution, 'Unknown')"
    " || CASE WHEN COALESCE(a.product_name, '') != ''"
    "         THEN ' ' || a.product_name ELSE '' END"
    " || CASE WHEN COALESCE(a.account_number_masked, '') != ''"
    "         THEN ' (' || a.account_number_masked || ')' ELSE '' END"
)

_UPI = "(t.raw_description LIKE 'UPI%' OR t.raw_description LIKE 'upi%')"

FIELDS: dict[str, Field] = {f.key: f for f in [
    # ---- time ----
    Field("month", "Month", "Time", periods.effective_month_sql("t."),
          type="month", options="months",
          hint="The accounting month, which is not always the calendar month "
               "of the date - see analytics.periods."),
    Field("calendar_month", "Calendar month", "Time",
          "substr(t.txn_date, 1, 7)", type="month"),
    Field("year", "Year", "Time", "substr(t.txn_date, 1, 4)"),
    Field("quarter", "Quarter", "Time",
          "substr(t.txn_date, 1, 4) || '-Q' ||"
          " CAST((CAST(substr(t.txn_date, 6, 2) AS INTEGER) + 2) / 3 AS INTEGER)"),
    Field("date", "Date", "Time", "t.txn_date", type="date"),
    # EXTRACT(DOW) numbers the days the same way SQLite's strftime('%w')
    # did - 0 is Sunday - so the CASE below did not have to be renumbered
    # when this moved to PostgreSQL.
    Field("day_of_week", "Day of week", "Time",
          "CASE CAST(EXTRACT(DOW FROM CAST(t.txn_date AS DATE)) AS INTEGER)"
          " WHEN 0 THEN 'Sunday' WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday'"
          " WHEN 3 THEN 'Wednesday' WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday'"
          " ELSE 'Saturday' END",
          options="days_of_week"),
    Field("day_of_month", "Day of month", "Time", "substr(t.txn_date, 9, 2)"),

    # ---- account ----
    Field("account", "Account", "Account", _ACCOUNT_LABEL,
          options="accounts_by_label"),
    Field("account_id", "Account (pick one)", "Account", "t.account_id",
          groupable=False, options="accounts",
          hint="Filter by a specific account. Group by 'Account' for a "
               "readable label."),
    Field("institution", "Institution", "Account",
          "COALESCE(a.institution, 'Unknown')", options="institutions"),
    Field("account_type", "Account type", "Account",
          "COALESCE(a.account_type, 'unknown')", options="account_types"),
    Field("currency", "Currency", "Account", "t.currency"),

    # ---- classification ----
    Field("category", "Category", "Classification", "t.category",
          options="categories"),
    Field("category_group", "Category group", "Classification",
          _category_group_case(), options="category_groups"),
    Field("flow_role", "Flow role", "Classification",
          "CASE WHEN t.flow_role != '' THEN t.flow_role ELSE 'unassigned' END",
          options="flow_roles",
          hint="Which side of the books the row lands on. Exactly one per row, "
               "so a sum across roles never double-counts."),
    Field("direction", "Direction", "Classification", "t.direction",
          options="directions"),
    Field("merchant", "Merchant", "Classification",
          "COALESCE(NULLIF(t.merchant, ''), '(none)')",
          hint="Free-text match: there are far too many merchants for a picker."),
    Field("description", "Description", "Classification",
          "t.normalized_description", groupable=False,
          hint="Free-text match against the cleaned narration."),
    Field("raw_description", "Raw description", "Classification",
          "t.raw_description", groupable=False),
    Field("rail", "Payment rail", "Classification",
          f"CASE WHEN {_UPI} THEN 'upi' ELSE 'other' END", options="rails"),
    Field("category_source", "Categorised by", "Classification",
          "t.category_source", options="category_sources",
          hint="Tells a hard rule apart from a model guess."),
    Field("recurrence", "Recurring?", "Classification",
          "CASE WHEN COALESCE(t.recurring_series_id, '') != ''"
          " THEN 'Recurring' ELSE 'One-off' END", options="recurrence"),

    # ---- flags & numbers ----
    Field("needs_review", "Needs review", "Quality", "t.needs_review", type="bool"),
    Field("excluded", "Excluded by user", "Quality", "t.excluded", type="bool"),
    Field("is_internal_transfer", "Internal transfer", "Quality",
          "t.is_internal_transfer", type="bool"),
    Field("is_mirror_leg", "Mirror leg", "Quality", "t.is_mirror_leg", type="bool",
          hint="The duplicate leg of a move between your own accounts. Counting "
               "both legs double-counts the money."),
    Field("amount", "Amount", "Amounts", _paise("t.amount"),
          type="money", groupable=False,
          hint="Filter on size, e.g. everything above 10,000."),
    Field("category_confidence", "Category confidence", "Quality",
          "t.category_confidence", type="number", groupable=False),
    Field("note", "Note", "Quality", "t.note", groupable=False),
]}


MEASURES: dict[str, Measure] = {m.key: m for m in [
    Measure("net_amount", "Net amount", "Money", _SIGNED,
            hint="Credits positive, debits negative. Sums to the actual change "
                 "in the account."),
    Measure("outflow", "Money out", "Money",
            f"CASE WHEN t.direction = 'debit' THEN {_paise('t.amount')} ELSE 0 END"),
    Measure("inflow", "Money in", "Money",
            f"CASE WHEN t.direction = 'credit' THEN {_paise('t.amount')} ELSE 0 END"),
    Measure("gross_amount", "Amount (unsigned)", "Money", _paise("t.amount"),
            hint="Size of the transaction regardless of direction."),
    Measure("txn_count", "Transactions", "Counts", "*",
            type="number", aggs=("count",)),
    Measure("merchant_count", "Distinct merchants", "Counts",
            "NULLIF(t.merchant, '')", type="number", aggs=("count_distinct",)),
    Measure("account_count", "Distinct accounts", "Counts", "t.account_id",
            type="number", aggs=("count_distinct",)),
    Measure("category_count", "Distinct categories", "Counts", "t.category",
            type="number", aggs=("count_distinct",)),
    Measure("confidence", "Category confidence", "Quality",
            "t.category_confidence", type="number", aggs=("avg", "min", "max")),
]}

AGG_LABELS = {
    "sum": "Sum", "avg": "Average", "min": "Smallest", "max": "Largest",
    "count": "Count", "count_distinct": "Distinct count",
}

#: op -> (SQL template, how many values it consumes)
OPERATORS: dict[str, tuple[str, str]] = {
    "in":           ("{col} IN ({ph})", "list"),
    "not_in":       ("{col} NOT IN ({ph})", "list"),
    "eq":           ("{col} = ?", "one"),
    "ne":           ("{col} != ?", "one"),
    "contains":     ("{col} LIKE ? ESCAPE '\\'", "like"),
    "not_contains": ("{col} NOT LIKE ? ESCAPE '\\'", "like"),
    "starts_with":  ("{col} LIKE ? ESCAPE '\\'", "prefix"),
    "gt":           ("{col} > ?", "one"),
    "gte":          ("{col} >= ?", "one"),
    "lt":           ("{col} < ?", "one"),
    "lte":          ("{col} <= ?", "one"),
    "between":      ("{col} BETWEEN ? AND ?", "two"),
    "is_true":      ("{col} = 1", "none"),
    "is_false":     ("{col} = 0", "none"),
    "is_empty":     ("COALESCE({col}, '') = ''", "none"),
    "is_not_empty": ("COALESCE({col}, '') != ''", "none"),
}

OP_LABELS = {
    "in": "is any of", "not_in": "is none of", "eq": "is", "ne": "is not",
    "contains": "contains", "not_contains": "does not contain",
    "starts_with": "starts with", "gt": "more than", "gte": "at least",
    "lt": "less than", "lte": "at most", "between": "between",
    "is_true": "is yes", "is_false": "is no",
    "is_empty": "is blank", "is_not_empty": "is not blank",
}

#: Which operators make sense for which field type.
OPS_FOR_TYPE: dict[str, tuple[str, ...]] = {
    "text":   ("in", "not_in", "eq", "ne", "contains", "not_contains",
               "starts_with", "is_empty", "is_not_empty"),
    "month":  ("in", "not_in", "eq", "ne", "gte", "lte", "between"),
    "date":   ("eq", "ne", "gte", "lte", "between"),
    "money":  ("gt", "gte", "lt", "lte", "between", "eq"),
    "number": ("gt", "gte", "lt", "lte", "between", "eq"),
    "bool":   ("is_true", "is_false"),
}

#: Read from the period engine so the Explore tab offers exactly the periods
#: every other screen does - one catalogue, one resolution, one answer to
#: "last 3 months". `inherit` is Explore's own: a widget that takes whatever
#: window the board is set to.
DATE_PRESETS: list[tuple[str, str]] = [
    (p["value"], p["label"]) for p in periods.PERIOD_PRESETS]

MAX_ROWS = 5000


class QueryError(ValueError):
    """A query naming something the registry does not contain."""


# --------------------------------------------------------------------------
# Date presets
# --------------------------------------------------------------------------


def resolve_period(spec: dict[str, Any] | None,
                   today: date | None = None) -> periods.Period:
    """A date range spec to a resolved window, in the engine's error type.

    The resolution itself is analytics.periods' job - this only translates a
    refusal, so a widget naming a preset that does not exist reports a bad
    query rather than a server fault.
    """
    try:
        return periods.resolve_period(spec, today)
    except periods.PeriodError as exc:
        raise QueryError(str(exc)) from exc


def resolve_range(spec: dict[str, Any] | None,
                  today: date | None = None) -> tuple[str | None, str | None]:
    """A date range spec to concrete ISO date bounds (inclusive).

    The CALENDAR reading of a period, which is what a date axis wants and
    what the comparison window is measured in. It is not what rows are
    filtered by: that is the accounting month - see `compile_query` and
    analytics.periods.
    """
    start, end = resolve_period(spec, today).bounds()
    return (start.isoformat() if start else None, end.isoformat() if end else None)


def shift_range(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    """The equally long window immediately before [start, end].

    Used for period-over-period comparison. Returns (None, None) when the
    window is open-ended, because "the period before all of time" is not a
    thing and a comparison against it would be misleading rather than empty.
    """
    if not start or not end:
        return None, None
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    span = (last - first).days + 1
    return (date.fromordinal(first.toordinal() - span).isoformat(),
            date.fromordinal(first.toordinal() - 1).isoformat())


_LIKE_SPECIALS = re.compile(r"([\\%_])")


def _like(value: Any, mode: str) -> str:
    escaped = _LIKE_SPECIALS.sub(r"\\\1", str(value))
    return f"{escaped}%" if mode == "prefix" else f"%{escaped}%"


# --------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------


@dataclass
class Compiled:
    sql: str
    params: list[Any]
    columns: list[dict[str, Any]]
    #: Parallel to `columns`: how to decode each SELECT position.
    decoders: list[str] = dc_field(default_factory=list)


def _coerce(field: Field, value: Any) -> Any:
    """Match a filter value to how the column stores it.

    Money filters are typed in rupees and compared against paise, so the
    conversion has to happen here or "over 10,000" silently means over 100.
    """
    if field.type == "money":
        return int(round(float(value) * 100))
    if field.type == "number":
        return float(value)
    if field.type == "bool":
        return 1 if value in (True, 1, "1", "true", "yes") else 0
    return value


def _filter_clause(spec: dict[str, Any]) -> tuple[str, list[Any]]:
    key = spec.get("field")
    field = FIELDS.get(key)
    if field is None:
        raise QueryError(f"Unknown filter field '{key}'")
    if not field.filterable:
        raise QueryError(f"'{field.label}' cannot be filtered on")

    op = spec.get("op") or "in"
    if op not in OPERATORS:
        raise QueryError(f"Unknown operator '{op}'")
    if op not in OPS_FOR_TYPE.get(field.type, OPS_FOR_TYPE["text"]):
        raise QueryError(f"'{OP_LABELS.get(op, op)}' does not apply to {field.label}")

    template, arity = OPERATORS[op]
    raw = spec.get("value")
    col = field.sql

    if arity == "none":
        return template.format(col=col), []

    if arity == "list":
        values = raw if isinstance(raw, list) else [raw]
        values = [v for v in values if v is not None and v != ""]
        if not values:
            # An empty "is any of" is a half-finished filter, not a request to
            # match nothing. Dropping the clause keeps the widget readable
            # instead of rendering an empty chart with no explanation.
            return "", []
        placeholders = ", ".join("?" * len(values))
        return (template.format(col=col, ph=placeholders),
                [_coerce(field, v) for v in values])

    if arity == "two":
        values = raw if isinstance(raw, list) else [raw, raw]
        if len(values) != 2 or any(v in (None, "") for v in values):
            return "", []
        return template.format(col=col), [_coerce(field, v) for v in values]

    if arity in {"like", "prefix"}:
        if raw in (None, ""):
            return "", []
        return template.format(col=col), [_like(raw, arity)]

    if raw in (None, ""):
        return "", []
    return template.format(col=col), [_coerce(field, raw)]


def compile_query(spec: dict[str, Any], today: date | None = None) -> Compiled:
    """Turn a query description into parameterised SQL.

    Nothing from `spec` is interpolated into the statement except keys that
    were found in the registry; every user-supplied value is bound.
    """
    dimensions = [d for d in (spec.get("dimensions") or []) if d]
    measures = spec.get("measures") or [{"field": "net_amount", "agg": "sum"}]

    select: list[str] = []
    columns: list[dict[str, Any]] = []
    decoders: list[str] = []
    group_by: list[str] = []

    for i, key in enumerate(dimensions):
        field = FIELDS.get(key)
        if field is None:
            raise QueryError(f"Unknown dimension '{key}'")
        if not field.groupable:
            raise QueryError(f"'{field.label}' cannot be grouped by")
        select.append(f"{field.sql} AS d{i}")
        # Grouping by ordinal rather than repeating the expression: several
        # of these are long CASE statements, and the ordinal is both shorter
        # to read in the SQL the Explore tab shows the user and immune to the
        # expression being matched textually.
        group_by.append(str(i + 1))
        columns.append({"key": key, "label": field.label, "role": "dimension",
                        "type": field.type})
        decoders.append("raw")

    for i, requested in enumerate(measures):
        if isinstance(requested, str):
            requested = {"field": requested}
        measure_key = requested.get("field")
        measure = MEASURES.get(measure_key)
        if measure is None:
            raise QueryError(f"Unknown measure '{measure_key}'")
        agg = requested.get("agg") or measure.aggs[0]
        if agg not in measure.aggs:
            raise QueryError(
                f"'{AGG_LABELS.get(agg, agg)}' does not apply to {measure.label}")

        if agg == "count":
            expr = "COUNT(*)"
        elif agg == "count_distinct":
            expr = f"COUNT(DISTINCT {measure.sql})"
        elif agg == "avg" and measure.type == "money":
            # Averaged in paise and rounded back to a whole paisa, so the
            # result is still exact currency rather than a float tail.
            expr = f"CAST(ROUND(AVG({measure.sql})) AS INTEGER)"
        else:
            expr = f"{agg.upper()}({measure.sql})"

        select.append(f"{expr} AS m{i}")
        columns.append({
            "key": f"m{i}", "role": "measure", "type": measure.type, "agg": agg,
            "field": measure_key,
            "label": requested.get("label") or (
                measure.label if agg in {"sum", "count", "count_distinct"}
                else f"{AGG_LABELS[agg]} {measure.label.lower()}"),
        })
        decoders.append("money" if measure.type == "money" else "number")

    # ---- WHERE ----
    clauses: list[str] = []
    params: list[Any] = []

    # The period, and which column it applies to.
    #
    # A month window filters on the ACCOUNTING month, not on the date: that is
    # the whole difference between "August" meaning the rows dated in August
    # and "August" meaning the rows the ledger counts in August - the salary
    # paid on 1 September among them. Every other screen in the app draws that
    # line the same way, and a widget that drew it differently would disagree
    # with the Overview beside it.
    #
    # A custom day range filters on the date, because that is what a day range
    # is for.
    period = spec.get("_period_override") or resolve_period(
        spec.get("date_range"), today)
    if period.mode == "months":
        month = periods.effective_month_sql("t.")
        if period.start_month:
            clauses.append(f"{month} >= ?")
            params.append(period.start_month)
        if period.end_month:
            clauses.append(f"{month} <= ?")
            params.append(period.end_month)
    elif period.mode == "dates":
        if period.start:
            clauses.append("t.txn_date >= ?")
            params.append(period.start.isoformat())
        if period.end:
            clauses.append("t.txn_date <= ?")
            params.append(period.end.isoformat())

    for one in (spec.get("filters") or []):
        clause, values = _filter_clause(one)
        if clause:
            clauses.append(clause)
            params.extend(values)

    # Two defaults that are on unless explicitly turned off. Both appear as
    # checkboxes in the widget editor rather than being applied invisibly: a
    # total that quietly drops rows is exactly the kind of number this project
    # exists not to produce.
    if spec.get("exclude_mirror_legs", True):
        clauses.append("t.is_mirror_leg = 0")
    if spec.get("exclude_excluded", True):
        clauses.append("t.excluded = 0")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    group = f"GROUP BY {', '.join(group_by)}" if group_by else ""

    # ---- ORDER BY ----
    aliases = {f"m{i}" for i in range(len(measures))}
    aliases |= {f"d{i}" for i in range(len(dimensions))}

    sorts = spec.get("sort") or []
    if not sorts and dimensions:
        # A time dimension reads forwards; anything else is most useful ranked
        # by its first measure.
        if FIELDS[dimensions[0]].type in {"month", "date"}:
            sorts = [{"key": dimensions[0], "dir": "asc"}]
        else:
            sorts = [{"key": "m0", "dir": "desc"}]

    order_parts = []
    for one in sorts:
        key = one.get("key")
        alias = f"d{dimensions.index(key)}" if key in dimensions else key
        if alias not in aliases:
            continue
        direction = ("DESC" if str(one.get("dir", "asc")).lower().startswith("desc")
                     else "ASC")
        order_parts.append(f"{alias} {direction}")
    order = f"ORDER BY {', '.join(order_parts)}" if order_parts else ""

    limit = max(1, min(int(spec.get("limit") or 200), MAX_ROWS))

    sql = " ".join(part for part in (
        f"SELECT {', '.join(select)}",
        "FROM transactions t",
        "LEFT JOIN accounts a ON a.id = t.account_id",
        where, group, order,
        # One row past the limit, purely to detect truncation. A widget that
        # silently shows the top 200 of 4,000 is how a partial answer gets
        # read as a complete one.
        f"LIMIT {limit + 1}",
    ) if part)

    return Compiled(sql=sql, params=params, columns=columns, decoders=decoders)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def _decode(value: Any, how: str) -> Any:
    if value is None:
        return None
    if how == "money":
        # Exactly one division, at the very end. Decimal so the rupee figure
        # is the true quotient rather than a binary approximation of it.
        return float(Decimal(int(value)) / 100)
    if how == "number":
        return float(value)
    return value


def run_query(db, spec: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Execute a query spec and return rows plus the SQL that produced them.

    The SQL comes back with the result on purpose. Every other figure in this
    app can be traced to the statement it came from; a number the user
    assembled themselves should be no different, and being able to read the
    query is how they check it means what they think it means.
    """
    compiled = compile_query(spec, today)
    limit = max(1, min(int(spec.get("limit") or 200), MAX_ROWS))

    with db.connection() as conn:
        raw_rows = conn.execute(compiled.sql, compiled.params).fetchall()

    truncated = len(raw_rows) > limit
    raw_rows = raw_rows[:limit]

    keys = [c["key"] for c in compiled.columns]
    rows = [
        {keys[i]: _decode(row[i], compiled.decoders[i]) for i in range(len(keys))}
        for row in raw_rows
    ]

    period = resolve_period(spec.get("date_range"), today)
    out: dict[str, Any] = {
        "columns": compiled.columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "sql": compiled.sql,
        "params": [str(p) for p in compiled.params],
        # `start`/`end` are the calendar bounds of the window; `mode` and
        # `basis` say whether the rows were selected by accounting month or by
        # date, which is the difference between two figures that would
        # otherwise look like a bug.
        "range": period.as_json(),
    }

    if spec.get("compare"):
        out.update(_compare(db, spec, compiled, rows, raw_rows, today))
    return out


def _compare(db, spec: dict[str, Any], compiled: Compiled, rows: list[dict[str, Any]],
             raw_rows: list[Any], today: date | None) -> dict[str, Any]:
    """Attach the same measures over the preceding window of equal length.

    Measured in whatever unit the window itself is in: three accounting
    months compare against the three before them, and a 28-day custom range
    against the 28 days before it. Comparing a month window against a day
    window would put a partial month beside a whole one.
    """
    period = resolve_period(spec.get("date_range"), today)
    previous_window = periods.previous_period(period)
    if previous_window is None:
        return {"compare_note": "Comparison needs a bounded date range - there "
                                "is no period before 'all time'."}

    previous = compile_query(
        {**spec, "compare": False, "_period_override": previous_window}, today)
    with db.connection() as conn:
        prev_rows = conn.execute(previous.sql, previous.params).fetchall()

    dimension_count = sum(1 for c in compiled.columns if c["role"] == "dimension")
    index = {tuple(row[i] for i in range(dimension_count)): row for row in prev_rows}
    measure_positions = [i for i, c in enumerate(compiled.columns)
                         if c["role"] == "measure"]

    for row, raw in zip(rows, raw_rows):
        match = index.get(tuple(raw[i] for i in range(dimension_count)))
        for i in measure_positions:
            alias = compiled.columns[i]["key"]
            before = _decode(match[i], compiled.decoders[i]) if match else None
            now = row.get(alias)
            row[f"{alias}__prev"] = before
            row[f"{alias}__delta"] = (
                None if before is None or now is None else round(now - before, 2))

    prev_start, prev_end = previous_window.bounds()
    return {"compared_to": {
        "start": prev_start.isoformat() if prev_start else None,
        "end": prev_end.isoformat() if prev_end else None,
    }}


# --------------------------------------------------------------------------
# Schema, for a UI that builds itself
# --------------------------------------------------------------------------


def schema(db) -> dict[str, Any]:
    """Everything the query builder needs to render, including live options.

    The frontend holds no hardcoded list of fields: adding one to FIELDS above
    makes it appear in the UI with no change on that side.
    """
    with db.connection() as conn:
        accounts = [
            {"value": r["id"],
             "label": _account_label(r),
             "type": r["account_type"]}
            for r in conn.execute(
                "SELECT id, institution, product_name, account_number_masked,"
                " account_type FROM accounts ORDER BY institution, account_type"
            ).fetchall()
        ]
        custom = [r["name"] for r in conn.execute(
            "SELECT name FROM custom_categories ORDER BY name").fetchall()]
        # The alias is filtered in an outer query rather than in the same
        # WHERE: standard SQL resolves WHERE before the select list exists, so
        # `WHERE m != ''` is only legal where the alias has already been
        # computed - which is one level out.
        months = [r["m"] for r in conn.execute(
            "SELECT m FROM ("
            f"  SELECT DISTINCT {periods.effective_month_sql()} AS m"
            "   FROM transactions"
            ") months WHERE m != '' ORDER BY m DESC"
        ).fetchall()]
        institutions = [r["institution"] for r in conn.execute(
            "SELECT DISTINCT institution FROM accounts ORDER BY institution"
        ).fetchall()]
        account_types = [r["account_type"] for r in conn.execute(
            "SELECT DISTINCT account_type FROM accounts ORDER BY account_type"
        ).fetchall()]

    def opts(values):
        return [{"value": v, "label": v.replace("_", " ").capitalize()}
                for v in values]

    return {
        "fields": [
            {"key": f.key, "label": f.label, "group": f.group, "type": f.type,
             "groupable": f.groupable, "filterable": f.filterable,
             "options": f.options, "hint": f.hint,
             "ops": list(OPS_FOR_TYPE.get(f.type, OPS_FOR_TYPE["text"]))}
            for f in FIELDS.values()
        ],
        "measures": [
            {"key": m.key, "label": m.label, "group": m.group, "type": m.type,
             "aggs": list(m.aggs), "hint": m.hint}
            for m in MEASURES.values()
        ],
        "agg_labels": AGG_LABELS,
        "op_labels": OP_LABELS,
        "date_presets": [{"value": v, "label": label} for v, label in DATE_PRESETS],
        "options": {
            "accounts": accounts,
            "accounts_by_label": [{"value": a["label"], "label": a["label"]}
                                  for a in accounts],
            "institutions": opts(institutions),
            "account_types": opts(account_types),
            "categories": opts(sorted(set(Category.all_builtins()) | set(custom))),
            "category_groups": [{"value": g, "label": g} for g in CATEGORY_GROUPS],
            "flow_roles": opts(["income", "expense", "transfer_out", "transfer_in",
                                "card_settlement", "claim_settlement", "investment",
                                "refund", "excluded", "unassigned"]),
            "directions": opts(["credit", "debit"]),
            "rails": [{"value": "upi", "label": "UPI"},
                      {"value": "other", "label": "Other"}],
            "category_sources": opts(["rule", "merchant_cache", "llm", "user",
                                      "default"]),
            "recurrence": [{"value": "Recurring", "label": "Recurring"},
                           {"value": "One-off", "label": "One-off"}],
            "months": [{"value": m, "label": m} for m in months],
            "days_of_week": [{"value": d, "label": d} for d in
                             ["Monday", "Tuesday", "Wednesday", "Thursday",
                              "Friday", "Saturday", "Sunday"]],
        },
        "max_rows": MAX_ROWS,
    }


def _account_label(row) -> str:
    """Mirrors Account.display_name()'s shape, minus the type suffix.

    Kept in step with _ACCOUNT_LABEL above: the picker's labels and the
    grouped values have to read the same or a filter and a chart legend
    disagree about what one account is called.
    """
    product = f" {row['product_name']}" if row["product_name"] else ""
    masked = (f" ({row['account_number_masked']})"
              if row["account_number_masked"] else "")
    return f"{row['institution']}{product}{masked}"
