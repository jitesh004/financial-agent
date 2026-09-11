"""What the language model has been asked, and what it cost.

Two questions, two endpoints. `/summary` answers "how much, on what, over
this window"; `/calls` answers "show me the actual requests". They share a
window parser so a figure on the summary and the list you get by clicking
it describe the same set - a statistics page whose total disagrees with its
own drill-down is worse than no page.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from ..db import repository as repo
from ..db.database import get_db
from ..llm import telemetry

router = APIRouter(prefix="/api/llm", tags=["llm"])

#: A window expressed the way a person says it: "30m", "6h", "7d", "3mo",
#: or "all". Minutes and hours matter here because a rate limit is a
#: per-minute window and "did I just burn my quota" is a question about
#: the last few minutes, not the last few days.
_PERIOD = re.compile(r"^(\d+)\s*(m|min|mins|h|hr|hrs|d|day|days|w|mo|mon|y)$",
                     re.IGNORECASE)

_UNITS = {
    "m": "minutes", "min": "minutes", "mins": "minutes",
    "h": "hours", "hr": "hours", "hrs": "hours",
    "d": "days", "day": "days", "days": "days",
    "w": "weeks",
    "mo": "days", "mon": "days",        # months as 30 days - see below
    "y": "days",
}

#: Calendar months and years are not fixed lengths, and pretending they are
#: would make "last 3 months" mean something slightly different every time
#: it was asked. Approximated openly here and the resolved boundary is
#: returned in the response, so the screen can print the window it actually
#: got rather than the one it asked for.
_MULTIPLIER = {"mo": 30, "mon": 30, "y": 365}


def _normalise(value: str) -> str:
    """Accept an ISO bound from a caller and store-format it."""
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"{value!r} is not a date this understands.")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _stamp(when: datetime) -> str:
    """A boundary in the format the column actually stores.

    `created_at` is TEXT written by `fa_now()`, which is
    `to_char(now(), 'YYYY-MM-DD HH24:MI:SS')` - a space, no timezone, no
    microseconds. Comparing it against an ISO string is a TEXT comparison
    where the separator decides the answer: " " sorts below "T", so
    `'2026-09-11 06:08:09' >= '2026-09-11T06:08:38+00:00'` is false for
    every row ever written, and every windowed query came back empty
    without erroring. The one failure mode worse than a wrong number is a
    silently empty list.
    """
    return when.strftime("%Y-%m-%d %H:%M:%S")


def _window(period: str, since: str = "", until: str = "") -> tuple[str, str, str]:
    """Resolve a period to ISO bounds. Returns (since, until, label)."""
    if since or until:
        return _normalise(since), _normalise(until), "custom range"

    text = (period or "all").strip().lower()
    if text in {"", "all", "everything"}:
        return "", "", "all time"

    match = _PERIOD.match(text)
    if not match:
        raise HTTPException(
            400, f"{period!r} is not a window this understands. Use "
                 f"something like 30m, 6h, 7d, 3mo, or 'all'.")

    count, unit = int(match.group(1)), match.group(2).lower()
    amount = count * _MULTIPLIER.get(unit, 1)
    delta = timedelta(**{_UNITS[unit]: amount})
    start = datetime.now(timezone.utc) - delta
    return _stamp(start), "", f"last {count}{unit}"


@router.get("/usage")
def usage(period: str = "all", since: str = "", until: str = "",
          key: str = "") -> dict[str, Any]:
    """Totals for the window: overall, then split by what asked for them."""
    start, end, label = _window(period, since, until)
    data = repo.llm_usage_summary(get_db(), since=start, until=end,
                                  key_label=key)

    known = dict(telemetry.PURPOSE_LABEL)
    for row in data["by_purpose"]:
        row["label"] = known.get(row["purpose"], row["purpose"])

    return {
        **data,
        "window": {"period": period, "label": label,
                   "since": start, "until": end, "key": key},
        # Every purpose this app has, so the screen shows a tab for one
        # that has not run yet rather than hiding it - "categorisation has
        # made no calls" is an answer, and an absent tab is not.
        "purposes": [{"key": k, "label": v} for k, v in telemetry.PURPOSES],
    }


@router.get("/calls")
def calls(period: str = "all", since: str = "", until: str = "",
          key: str = "", purpose: str = "", status: str = "",
          limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """The requests themselves, newest first, retries attached to their
    parent rather than floating free."""
    start, end, label = _window(period, since, until)
    data = repo.llm_calls(get_db(), since=start, until=end, key_label=key,
                          purpose=purpose, status=status,
                          limit=min(max(limit, 1), 500), offset=max(offset, 0))
    return {**data,
            "window": {"period": period, "label": label,
                       "since": start, "until": end, "key": key,
                       "purpose": purpose, "status": status}}
