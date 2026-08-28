"""Anthropic client wrapper.

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
import os
import re
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
#: Narrative work benefits from the stronger model; categorization does not.
NARRATIVE_MODEL = "claude-opus-5"

_LONG_DIGITS = re.compile(r"\b\d(?:[ -]?\d){8,17}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE = re.compile(r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b")


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
    return text


class LLMUnavailable(RuntimeError):
    """Raised when a caller demands an LLM and none is configured."""


class LLMClient:
    """Minimal wrapper over the Anthropic Messages API."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self._client: Any = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self.api_key)
        return self._client

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Single-turn completion. Temperature defaults to 0 for reproducibility."""
        client = self._get_client()
        response = client.messages.create(
            model=model or self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "You are a precise financial data assistant.",
            messages=[{"role": "user", "content": redact(prompt)}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )

    def complete_json(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> Any:
        """Completion that must return JSON.

        Models occasionally wrap JSON in prose or a code fence even when told
        not to, so the fence is stripped and the first balanced object/array is
        recovered rather than failing the whole batch on a formatting slip.
        """
        raw = self.complete(
            prompt,
            system=system or "You return only valid JSON. No prose, no code fences.",
            max_tokens=max_tokens,
            model=model,
        )
        return _parse_json_loose(raw)


def _parse_json_loose(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"Model did not return parseable JSON: {raw[:200]!r}")


_client: LLMClient | None = None


def get_client(model: str = DEFAULT_MODEL) -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient(model=model)
    return _client
