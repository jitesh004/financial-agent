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

def redact(text: str) -> str:
    """Strip identifiers that have no analytical value but real leak cost."""
    if not text:
        return text
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
        # simple heuristic for "Name: John Doe" or "Mr. John Doe"
        line = re.sub(r"\b(?:Mr\.|Mrs\.|Ms\.|Name:)\s+[A-Za-z\s]+", "[NAME]", line, flags=re.IGNORECASE)
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
