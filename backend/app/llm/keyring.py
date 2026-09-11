"""Several API keys for one provider, tried in turn.

A free tier does not run out of tokens; it runs out of REQUESTS. The tier
this app targets meters 250,000 tokens a minute against 15 requests a minute
and 500 a day - so the token budget is effectively free and the request
budget is the whole constraint. An import of 336 documents can exhaust a
day's allowance before it finishes, and no amount of waiting inside one
request clears a DAILY ceiling.

More keys is the only thing that actually raises that ceiling. AI Studio
issues one key per project and a person may hold several, so this holds the
list and decides which to use next.

Three rules, and they exist because the failures differ:

  - A 429 retires the key for a cooldown and moves to the next one. Waiting
    is right for a per-minute burst and useless for a per-day cap, and from
    inside one response the two are frequently indistinguishable - so the
    cheap, always-correct move is to ask a different key first and only
    sleep once every key is cooling.
  - A 401 or 403 retires the key for the whole process. A revoked or
    mistyped key will not start working in sixty seconds, and retrying it
    on every subsequent call turns one bad paste into a stall on every
    request.
  - Anything else is not the key's fault and does not retire it.

Cooldowns are per process and deliberately not persisted. They are a hint
for the next few minutes, not a fact about the account, and a restart should
not inherit a guess made before it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: How long a key rests after a 429 before it is offered again. One minute,
#: because the per-minute window is the one that actually clears; a daily
#: ceiling simply fails again and rests again, which costs one request per
#: key per minute rather than a stall.
COOLDOWN_SECONDS = 60.0

#: A key that answered with 401/403 is not coming back this process.
_FOREVER = float("inf")

_lock = threading.Lock()
#: secret -> unix time it becomes usable again.
_resting: dict[str, float] = {}


@dataclass(frozen=True)
class ApiKey:
    """One credential, with the label its owner gave it."""

    label: str
    secret: str

    def masked(self) -> str:
        if len(self.secret) <= 8:
            return "..." if self.secret else ""
        return f"{self.secret[:4]}...{self.secret[-4:]}"


def parse(raw: Any) -> list[ApiKey]:
    """Read the stored key list, in any of the shapes it can arrive in.

    Accepts the JSON array the Settings screen writes, and tolerates a bare
    string so a workspace that only ever set the single `llm_api_key` keeps
    working untouched.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except ValueError:
                return [ApiKey("Default key", text)]
        else:
            return [ApiKey("Default key", text)]

    out: list[ApiKey] = []
    for i, entry in enumerate(raw or []):
        if isinstance(entry, str):
            secret = entry.strip()
            label = ""
        elif isinstance(entry, dict):
            secret = str(entry.get("key") or entry.get("secret") or "").strip()
            label = str(entry.get("label") or entry.get("name") or "").strip()
        else:
            continue
        if secret:
            out.append(ApiKey(label or f"Key {i + 1}", secret))
    return out


def dedupe(keys: list[ApiKey]) -> list[ApiKey]:
    """First occurrence of each secret wins, order preserved.

    The same key pasted twice is one key, and rotating onto it after it has
    just been rate limited wastes the one request that rotation was for.
    """
    seen: set[str] = set()
    out: list[ApiKey] = []
    for key in keys:
        if key.secret in seen:
            continue
        seen.add(key.secret)
        out.append(key)
    return out


def available(keys: list[ApiKey]) -> list[ApiKey]:
    """Those not currently resting, in order; falls back to all of them.

    Never returns empty while any key exists. When every key is cooling, the
    caller still has to try something - and one 429 is a better outcome than
    refusing to make a call the user asked for.
    """
    now = time.monotonic()
    with _lock:
        ready = [k for k in keys if _resting.get(k.secret, 0.0) <= now]
        forever = {k.secret for k in keys
                   if _resting.get(k.secret, 0.0) == _FOREVER}
    if ready:
        return ready
    # Everything is resting. Offer the ones that can recover, not the ones
    # that were rejected outright.
    recoverable = [k for k in keys if k.secret not in forever]
    return recoverable or keys


def rest(key: ApiKey, seconds: float = COOLDOWN_SECONDS) -> None:
    """Stand this key down for a while."""
    with _lock:
        _resting[key.secret] = time.monotonic() + seconds
    log.warning("API key %r rate limited; resting it for %.0fs",
                key.label or key.masked(), seconds)


def retire(key: ApiKey, reason: str) -> None:
    """Stand this key down for the rest of the process."""
    with _lock:
        _resting[key.secret] = _FOREVER
    log.error("API key %r rejected (%s); it will not be tried again "
              "until restart", key.label or key.masked(), reason)


def revive(key: ApiKey) -> None:
    """Clear any cooldown - the key just worked."""
    with _lock:
        _resting.pop(key.secret, None)


def status(keys: list[ApiKey]) -> list[dict[str, Any]]:
    """What the Settings screen shows: which keys are usable right now."""
    now = time.monotonic()
    out = []
    with _lock:
        for key in keys:
            until = _resting.get(key.secret, 0.0)
            if until == _FOREVER:
                state = "rejected"
                resting_for = None
            elif until > now:
                state = "resting"
                resting_for = int(until - now)
            else:
                state = "ready"
                resting_for = None
            out.append({"label": key.label, "hint": key.masked(),
                        "state": state, "resting_for": resting_for})
    return out


def forget_all() -> None:
    """Drop every cooldown. Called when the key list itself changes."""
    with _lock:
        _resting.clear()
