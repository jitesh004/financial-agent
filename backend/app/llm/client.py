"""LLM client wrapper.

Two responsibilities beyond calling the API:

  1. Degrade gracefully. If no API key is configured the app must still work -
     rules categorize, analytics compute, the dashboard renders. Only the
     narrative and the unknown-merchant tail are lost. A financial tool that
     hard-fails without an internet connection is the wrong shape.

  2. Redact before sending. Account numbers are stripped at ingestion, but this
     is the last gate before text leaves the machine, so it re-checks rather
     than trusting an upstream invariant.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..config import config
from .providers import Provider, GeminiProvider, AzureOpenAIProvider

log = logging.getLogger(__name__)

DEFAULT_MODEL = "fast"
NARRATIVE_MODEL = "strong"

_LONG_DIGITS = re.compile(r"\b\d(?:[ -]?\d){8,17}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE = re.compile(r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b")

# Simple address patterns to strip
_ADDRESS_PATTERNS = re.compile(
    r"\b(?:plot|flat|door|block|sector|phase|nagar|road|street|colony|apartment|floor|bhavan|marg)\b.*", 
    re.IGNORECASE
)
_PINCODE = re.compile(r"\b\d{6}\b")

#: An honorific or a "Name:" label followed by the name itself. The trailing
#: period is optional because real statements print "Mr Jitesh Agarwal" as
#: often as "Mr. Jitesh Agarwal", and requiring it let every one of them
#: through. Bounded to at most four capitalised words so it consumes a name
#: rather than the remainder of the line.
_HONORIFIC_NAME = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Name)\b\.?:?\s+"
    r"(?:[A-Za-z]+(?:\s+|$)){1,4}",
    re.IGNORECASE,
)

def known_holder_names() -> list[str]:
    """Names this workspace knows belong to its owner.

    A generic "does this line look like a person's name?" heuristic is not
    reliable enough to protect a real name - a statement can print it bare,
    in caps, with no honorific and nothing else on the line, which is
    indistinguishable from a merchant. But the app already extracts the
    holder's name deterministically, from the profile and from the statement
    letterheads themselves, so it does not have to guess: it can strike out
    the specific strings it knows.

    Failures here are swallowed on purpose. Redaction runs on the path to an
    outbound model call, and a database hiccup must degrade it to the regex
    rules rather than take the whole call down.
    """
    names: set[str] = set()
    try:
        from ..db.database import get_db
        from ..db import repository as repo

        db = get_db()
        profile = repo.get_profile(db)
        if profile.full_name:
            names.add(profile.full_name)
        for account in repo.get_accounts(db):
            if account.holder_name:
                names.add(account.holder_name)
    except Exception:  # pragma: no cover - defensive
        log.debug("could not load holder names for redaction", exc_info=True)
    return sorted(n for n in names if _looks_like_a_person_name(n))


def _looks_like_a_person_name(value: str) -> bool:
    """Reject the debris that heuristic extraction leaves in `holder_name`.

    Real values seen in this workspace's own accounts table include
    "S.", "Willnotbeheldliableforanytransaction..." and
    ". (Monday To Friday Between 9:30 A.M. And 6:00 P.M.)". Feeding those in
    is not a privacy problem but a quality one: "Monday" and "Friday" would
    be struck out of every statement, degrading exactly the text the model is
    being asked to read.
    """
    value = (value or "").strip()
    if not 4 <= len(value) <= 60:
        return False
    if any(ch.isdigit() for ch in value):
        return False
    words = [w.strip(".") for w in value.split()]
    if not 2 <= len(words) <= 5:
        return False
    return all(w.isalpha() and len(w) >= 2 for w in words)


def _name_patterns(names: list[str]) -> list[re.Pattern[str]]:
    """Match a known name, and also each of its parts.

    Statements are inconsistent about which parts they print - "Jitesh
    Agarwal" on one, "JITESH MUKESH AGARWAL" on another - so matching only
    the full string would miss most of them. Parts shorter than three
    characters are skipped: a two-letter initial would match inside ordinary
    words and shred the surrounding text.
    """
    out = []
    for name in names:
        parts = [p for p in re.split(r"\s+", name.strip()) if len(p) >= 3]
        if not parts:
            continue
        # Longest first, so "JITESH AGARWAL" is replaced as one unit before
        # either half can be replaced on its own.
        joined = r"\s+".join(re.escape(p) for p in parts)
        out.append(re.compile(rf"\b{joined}\b", re.IGNORECASE))
        for part in sorted(parts, key=len, reverse=True):
            out.append(re.compile(rf"\b{re.escape(part)}\b", re.IGNORECASE))
    return out


def redact(text: str, names: list[str] | None = None) -> str:
    """Strip identifiers that have no analytical value but real leak cost.

    `names` defaults to the workspace's known holder names. Pass an explicit
    list (including an empty one) to override.
    """
    if not text:
        return text

    for pattern in _name_patterns(
        known_holder_names() if names is None else names
    ):
        text = pattern.sub("[NAME]", text)
    text = _LONG_DIGITS.sub(
        lambda m: f"XXXX{re.sub(r'[^0-9]', '', m.group(0))[-4:]}", text
    )
    text = _PAN.sub("[PAN]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[PHONE]", text)
    
    # Aggressively mask common name and address prefixes
    lines = []
    for line in text.split("\n"):
        if _ADDRESS_PATTERNS.search(line):
            continue
        if _PINCODE.search(line):
            continue
        # "Name: John Doe", "Mr. John Doe", and - the shape real statements
        # actually use - "Mr Jitesh Agarwal" with no period at all. The
        # period was previously required, which is why every one of this
        # workspace's four statement formats sailed straight through.
        line = _HONORIFIC_NAME.sub("[NAME]", line)
        lines.append(line)
        
    return "\n".join(lines)


class LLMUnavailable(RuntimeError):
    """Raised when a caller demands an LLM and none is configured."""


class LLMClient:
    """Minimal wrapper over LLM Providers."""

    def __init__(self, provider: Provider | None = None, tier: str = DEFAULT_MODEL):
        self._provider = provider
        self.tier = tier

    @property
    def available(self) -> bool:
        return self._provider is not None and self._provider.available

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        if not self.available:
            raise LLMUnavailable("No LLM Provider is configured or available.")
        return self._provider.complete(
            prompt=redact(prompt),
            system=system,
            max_tokens=max_tokens,
            tier=model or self.tier,
            temperature=temperature
        )

    def complete_json(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> Any:
        if not self.available:
            raise LLMUnavailable("No LLM Provider is configured or available.")
        return self._provider.complete_json(
            prompt=redact(prompt),
            system=system,
            max_tokens=max_tokens,
            tier=model or self.tier
        )


_clients: dict[str, LLMClient] = {}


def get_client(model: str = DEFAULT_MODEL) -> LLMClient:
    global _clients
    
    if model not in _clients:
        provider = None
        if config.LLM_PROVIDER == "gemini":
            provider = GeminiProvider()
        elif config.LLM_PROVIDER == "azure":
            provider = AzureOpenAIProvider()
        
        # fallback if requested provider isn't available, but it's set in config
        _clients[model] = LLMClient(provider=provider, tier=model)
        
    return _clients[model]
