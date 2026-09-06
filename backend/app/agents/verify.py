"""Check that every figure an agent reports came out of a tool.

The rest of this app enforces "no language model ever produces a figure" by
construction: the model is handed computed numbers and asked to interpret
them. An agent breaks that arrangement, because it CHOOSES what to compute and
then writes prose around the results - and a small model writing prose around
numbers will, sooner or later, write a number that was not in the results.

Not usually a wild invention. The failure mode is quieter and worse: a figure
transposed from the wrong row, a total the model added up itself and got
slightly wrong, a balance from the tool call two steps earlier. Every one of
those looks exactly like a correct answer.

So the check is mechanical. Every number that appeared anywhere in any tool
result this run is collected; every number in the finished answer is
extracted; and any money-scale figure with no match is reported as unverified.

Three decisions worth stating, because each is a limit on what this proves:

  Only MONEY is checked. A figure at or above ONE_RUPEE_SCALE has to be
  quoted; anything smaller is left alone. Counts, month numbers, percentages
  and ratios are things a model is allowed to work out from two figures it was
  given, and checking them would flag "43% of take-home" - which is correct,
  derived, and unmatched by construction. Money is different: it is never
  derived, it is read, and the arithmetic that produces it belongs in Python.

  A match is approximate. "41.2 lakh" and 4,124,761.64 are the same figure
  written two ways, and a check that insisted on the paise would flag every
  correctly rounded number in the answer. TOLERANCE is wide enough for
  rounding to two significant figures at lakh scale and narrow enough that a
  genuinely different number does not slip through.

  Nothing is deleted. An unverified figure is REPORTED, not stripped: silently
  editing a model's prose would leave a sentence that reads as if it were
  checked, and the whole point here is to be able to tell the difference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

#: Below this, a number is a count, a month, a day, a percentage or a rate,
#: and a model is entitled to have worked it out. At or above it, the number
#: is money and must have been read from a tool.
#:
#: One thousand rather than something smaller because the noise below it is
#: relentless - "12 months", "3 loans", "43%", "8.45" - and every one of those
#: would be a false alarm. It does mean a genuinely invented small fee is not
#: caught, which is a real gap and the honest cost of a threshold anywhere.
ONE_RUPEE_SCALE = Decimal("1000")

#: How far apart two figures may be and still be the same figure. Covers
#: rounding to the rupee, to the thousand, and to two significant figures at
#: lakh scale - "41.2 lakh" against 41,24,761.64 is 0.11% out.
TOLERANCE = Decimal("0.01")

#: Indian shorthand, as a person writes it in prose.
_SCALES = {
    "k": Decimal("1000"),
    "thousand": Decimal("1000"),
    "lakh": Decimal("100000"),
    "lakhs": Decimal("100000"),
    "lac": Decimal("100000"),
    "lacs": Decimal("100000"),
    "crore": Decimal("10000000"),
    "crores": Decimal("10000000"),
    "cr": Decimal("10000000"),
}

#: A number as it appears in written text, with or without Indian digit
#: grouping, optionally followed by a scale word. The scale is captured so
#: "41.2 lakh" resolves to 4120000 rather than to 41.2.
_NUMBER = re.compile(
    r"(?<![\w.])"
    r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(k\b|thousand\b|lakhs?\b|lacs?\b|crores?\b|cr\b)?",
    re.IGNORECASE,
)

#: Text that is an identifier rather than a quantity. A run id, a masked card
#: number and an ISO date all contain digits and none of them is money.
_NOT_A_QUANTITY = re.compile(
    r"\b\d{4}-\d{2}(?:-\d{2})?\b"          # 2026-04 or 2026-04-10
    r"|\bXXXX\d+\b"                        # a masked account number
    r"|\b[0-9a-f]{8,}\b"                   # an id
    r"|\(\s*\d+\s*/\s*\d+\s*\)",           # an instalment counter
    re.IGNORECASE,
)


@dataclass
class Report:
    """What the check found."""

    #: Money-scale figures in the answer with no matching tool result.
    unverified: list[str] = field(default_factory=list)
    #: How many money-scale figures were checked at all.
    checked: int = 0
    #: How many distinct numbers the tools produced to check against.
    available: int = 0

    @property
    def clean(self) -> bool:
        return not self.unverified

    def as_json(self) -> dict[str, Any]:
        return {"unverified": list(self.unverified), "checked": self.checked,
                "available": self.available, "clean": self.clean}


# ---------------------------------------------------------------------------
# What the tools produced
# ---------------------------------------------------------------------------

def collect_figures(value: Any, into: set[Decimal] | None = None
                    ) -> set[Decimal]:
    """Every number anywhere in a tool result, however deeply nested.

    Walked as DATA rather than scraped from the serialised JSON, so a number
    is collected as the value it is and not as whatever text happened to
    surround it. A string that holds a number counts - money crosses this
    boundary as a decimal string in several places - but a string that merely
    contains digits does not.
    """
    figures = set() if into is None else into
    if isinstance(value, bool):
        return figures
    if isinstance(value, (int, float, Decimal)):
        _add(figures, value)
    elif isinstance(value, str):
        stripped = value.replace(",", "").strip()
        if stripped and re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
            _add(figures, stripped)
    elif isinstance(value, dict):
        for item in value.values():
            collect_figures(item, figures)
    elif isinstance(value, (list, tuple)):
        for item in value:
            collect_figures(item, figures)
    return figures


def _add(figures: set[Decimal], value: Any) -> None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return
    figures.add(abs(number))
    # A tool reports 4124761.64; the answer will say 41,24,762 or 41.25 lakh.
    # The rounded forms are added so the comparison below does not have to
    # guess how the model chose to present it.
    figures.add(abs(number).quantize(Decimal("1")))


# ---------------------------------------------------------------------------
# What the answer claims
# ---------------------------------------------------------------------------

def figures_in(text: str) -> list[tuple[str, Decimal]]:
    """Every quantity written in a piece of prose, as (as written, value)."""
    if not text:
        return []
    # Blanked rather than removed, so the offsets of everything after an id
    # or a date do not shift and a number is not accidentally fused to its
    # neighbour.
    cleaned = _NOT_A_QUANTITY.sub(lambda m: " " * len(m.group(0)), text)

    found: list[tuple[str, Decimal]] = []
    for match in _NUMBER.finditer(cleaned):
        digits, scale = match.group(1), (match.group(2) or "").lower().strip()
        try:
            value = Decimal(digits.replace(",", ""))
        except InvalidOperation:
            continue
        if scale:
            value *= _SCALES.get(scale, Decimal("1"))
        found.append((match.group(0).strip(), value))
    return found


def _matches(value: Decimal, figures: set[Decimal]) -> bool:
    if value in figures:
        return True
    for known in figures:
        largest = max(abs(value), abs(known))
        if largest == 0:
            continue
        if abs(value - known) <= largest * TOLERANCE:
            return True
    return False


def check(answer: dict[str, Any], figures: set[Decimal]) -> Report:
    """Every money figure in this answer, against everything the tools said."""
    report = Report(available=len(figures))
    if not answer:
        return report

    seen: set[str] = set()
    for text in _texts(answer):
        for written, value in figures_in(text):
            if value < ONE_RUPEE_SCALE:
                continue
            report.checked += 1
            if _matches(value, figures):
                continue
            if written in seen:
                continue  # the same wrong figure repeated is one problem
            seen.add(written)
            report.unverified.append(written)
    return report


def _texts(answer: dict[str, Any]) -> list[str]:
    """Every string in an answer that a reader would take as a claim."""
    out: list[str] = [str(answer.get("headline") or ""),
                      str(answer.get("summary") or "")]
    for metric in answer.get("metrics") or []:
        if isinstance(metric, dict):
            out += [str(metric.get("value") or ""), str(metric.get("note") or "")]
    for finding in answer.get("findings") or []:
        if isinstance(finding, dict):
            out += [str(finding.get("title") or ""),
                    str(finding.get("detail") or "")]
            out += [str(e) for e in (finding.get("evidence") or [])]
    for action in answer.get("actions") or []:
        if isinstance(action, dict):
            out += [str(action.get("title") or ""),
                    str(action.get("detail") or ""),
                    str(action.get("mechanism") or "")]
    out += [str(c) for c in (answer.get("caveats") or [])]
    return [t for t in out if t]


#: What the reader is told when a figure could not be traced. Written as a
#: caveat rather than a banner because it is a qualification on the answer,
#: and it belongs where the answer's other qualifications are.
def caveat_for(report: Report) -> str | None:
    if report.clean:
        return None
    listed = ", ".join(report.unverified[:6])
    more = (f" and {len(report.unverified) - 6} more"
            if len(report.unverified) > 6 else "")
    return (
        f"{len(report.unverified)} figure(s) here did not come from any tool "
        f"this run returned ({listed}{more}). They may be arithmetic the "
        f"model did itself, or they may be wrong - check them against the "
        f"working before relying on them."
    )
