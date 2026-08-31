"""Starter dashboards.

A blank canvas is the wrong first experience for a query builder: the fields
exist but nothing tells you which combinations are worth asking for. Each
template below is a complete, working board that answers a real question, and
every widget on it is fully editable once created - the template is a starting
point, not a fixed report.

Kept on the server rather than in the frontend so the shipped boards and the
query registry they reference stay in one place and cannot drift apart.
"""

from __future__ import annotations

from typing import Any

#: Widget sizes are columns of a 12-wide grid; height is in 120px row units.
_FULL, _HALF, _THIRD, _QUARTER = 12, 6, 4, 3


def _stat(title: str, measure: str, agg: str = "sum", **query: Any) -> dict[str, Any]:
    return {
        "title": title, "type": "stat", "width": _QUARTER, "height": 1,
        "query": {"dimensions": [], "measures": [{"field": measure, "agg": agg}],
                  **query},
        "viz": {},
    }


TEMPLATES: dict[str, dict[str, Any]] = {
    "cashflow": {
        "name": "Cashflow",
        "description": "Money in against money out, month by month.",
        "filters": {"date_range": {"preset": "last_12m"}, "filters": []},
        "widgets": [
            _stat("Money in", "inflow",
                  filters=[{"field": "flow_role", "op": "in", "value": ["income"]}]),
            _stat("Money out", "outflow",
                  filters=[{"field": "flow_role", "op": "in", "value": ["expense"]}]),
            _stat("Net", "net_amount",
                  filters=[{"field": "flow_role", "op": "in",
                            "value": ["income", "expense"]}]),
            _stat("Transactions", "txn_count", agg="count"),
            {
                "title": "In and out by month", "type": "bar",
                "width": _FULL, "height": 3,
                "query": {
                    "dimensions": ["month"],
                    "measures": [{"field": "inflow", "agg": "sum"},
                                 {"field": "outflow", "agg": "sum"}],
                    "filters": [{"field": "flow_role", "op": "in",
                                 "value": ["income", "expense"]}],
                    "limit": 24,
                },
                "viz": {"stacked": False},
            },
            {
                "title": "Net position by month", "type": "line",
                "width": _FULL, "height": 3,
                "query": {
                    "dimensions": ["month"],
                    "measures": [{"field": "net_amount", "agg": "sum"}],
                    "filters": [{"field": "flow_role", "op": "in",
                                 "value": ["income", "expense"]}],
                    "limit": 24,
                },
                "viz": {},
            },
        ],
    },

    "spending": {
        "name": "Spending deep-dive",
        "description": "Where it goes, who it goes to, and how that is moving.",
        "filters": {"date_range": {"preset": "last_6m"}, "filters": []},
        "widgets": [
            {
                "title": "By category", "type": "hbar", "width": _HALF, "height": 3,
                "query": {
                    "dimensions": ["category"],
                    "measures": [{"field": "outflow", "agg": "sum"}],
                    "filters": [{"field": "flow_role", "op": "in", "value": ["expense"]}],
                    "limit": 15,
                },
                "viz": {"show_share": True},
            },
            {
                "title": "By category group", "type": "donut",
                "width": _HALF, "height": 3,
                "query": {
                    "dimensions": ["category_group"],
                    "measures": [{"field": "outflow", "agg": "sum"}],
                    "filters": [{"field": "flow_role", "op": "in", "value": ["expense"]}],
                    "limit": 10,
                },
                "viz": {},
            },
            {
                "title": "Top merchants", "type": "table",
                "width": _HALF, "height": 3,
                "query": {
                    "dimensions": ["merchant"],
                    "measures": [{"field": "outflow", "agg": "sum"},
                                 {"field": "txn_count", "agg": "count"},
                                 {"field": "gross_amount", "agg": "avg"}],
                    "filters": [{"field": "flow_role", "op": "in", "value": ["expense"]}],
                    "limit": 25,
                },
                "viz": {},
            },
            {
                "title": "Category by month", "type": "pivot",
                "width": _HALF, "height": 3,
                "query": {
                    "dimensions": ["category", "month"],
                    "measures": [{"field": "outflow", "agg": "sum"}],
                    "filters": [{"field": "flow_role", "op": "in", "value": ["expense"]}],
                    "limit": 400,
                },
                "viz": {"pivot_on": "month", "show_totals": True},
            },
            {
                "title": "Spend by day of week", "type": "bar",
                "width": _HALF, "height": 2,
                "query": {
                    "dimensions": ["day_of_week"],
                    "measures": [{"field": "outflow", "agg": "sum"}],
                    "filters": [{"field": "flow_role", "op": "in", "value": ["expense"]}],
                    "sort": [{"key": "m0", "dir": "desc"}],
                },
                "viz": {},
            },
            {
                "title": "Largest single spends", "type": "table",
                "width": _HALF, "height": 2,
                "query": {
                    "dimensions": ["merchant", "month"],
                    "measures": [{"field": "gross_amount", "agg": "max"}],
                    "filters": [{"field": "flow_role", "op": "in", "value": ["expense"]}],
                    "sort": [{"key": "m0", "dir": "desc"}],
                    "limit": 20,
                },
                "viz": {},
            },
        ],
    },

    "accounts": {
        "name": "Accounts & rails",
        "description": "How activity splits across banks, cards and UPI.",
        "filters": {"date_range": {"preset": "last_6m"}, "filters": []},
        "widgets": [
            {
                "title": "Activity by account", "type": "table",
                "width": _FULL, "height": 3,
                "query": {
                    "dimensions": ["account"],
                    "measures": [{"field": "inflow", "agg": "sum"},
                                 {"field": "outflow", "agg": "sum"},
                                 {"field": "net_amount", "agg": "sum"},
                                 {"field": "txn_count", "agg": "count"}],
                    "limit": 50,
                },
                "viz": {},
            },
            {
                "title": "Spend by account type", "type": "donut",
                "width": _THIRD, "height": 3,
                "query": {
                    "dimensions": ["account_type"],
                    "measures": [{"field": "outflow", "agg": "sum"}],
                    "filters": [{"field": "flow_role", "op": "in", "value": ["expense"]}],
                },
                "viz": {},
            },
            {
                "title": "UPI against everything else", "type": "bar",
                "width": _THIRD, "height": 3,
                "query": {
                    "dimensions": ["month", "rail"],
                    "measures": [{"field": "outflow", "agg": "sum"}],
                    "filters": [{"field": "flow_role", "op": "in", "value": ["expense"]}],
                    "limit": 60,
                },
                "viz": {"series_from": "rail", "stacked": True},
            },
            {
                "title": "Institutions", "type": "hbar",
                "width": _THIRD, "height": 3,
                "query": {
                    "dimensions": ["institution"],
                    "measures": [{"field": "outflow", "agg": "sum"}],
                    "filters": [{"field": "flow_role", "op": "in", "value": ["expense"]}],
                },
                "viz": {"show_share": True},
            },
        ],
    },

    "quality": {
        "name": "Data quality",
        "description": "What is still unclassified, unreviewed or guessed at.",
        "filters": {"date_range": {"preset": "all"}, "filters": []},
        "widgets": [
            _stat("Needs review", "txn_count", agg="count",
                  filters=[{"field": "needs_review", "op": "is_true"}]),
            _stat("Uncategorised", "txn_count", agg="count",
                  filters=[{"field": "category", "op": "in",
                            "value": ["uncategorized"]}]),
            _stat("Unassigned flow role", "txn_count", agg="count",
                  filters=[{"field": "flow_role", "op": "in", "value": ["unassigned"]}]),
            _stat("Excluded by you", "txn_count", agg="count",
                  filters=[{"field": "excluded", "op": "is_true"}],
                  exclude_excluded=False),
            {
                "title": "How each row was categorised", "type": "hbar",
                "width": _HALF, "height": 3,
                "query": {
                    "dimensions": ["category_source"],
                    "measures": [{"field": "txn_count", "agg": "count"}],
                },
                "viz": {"show_share": True},
            },
            {
                "title": "Uncategorised value by month", "type": "area",
                "width": _HALF, "height": 3,
                "query": {
                    "dimensions": ["month"],
                    "measures": [{"field": "gross_amount", "agg": "sum"}],
                    "filters": [{"field": "category", "op": "in",
                                 "value": ["uncategorized"]}],
                    "limit": 24,
                },
                "viz": {},
            },
        ],
    },

    "blank": {
        "name": "Blank dashboard",
        "description": "Start from nothing and add your own widgets.",
        "filters": {"date_range": {"preset": "last_12m"}, "filters": []},
        "widgets": [],
    },
}


def catalogue() -> list[dict[str, Any]]:
    """The template list, without the widget payloads."""
    return [
        {"key": key, "name": t["name"], "description": t["description"],
         "widget_count": len(t["widgets"])}
        for key, t in TEMPLATES.items()
    ]


def get(key: str) -> dict[str, Any] | None:
    return TEMPLATES.get(key)
