"""Which model this workspace calls, and who decided.

`.env` sets the deployment default. A signed-in user may override any part of
it from the Settings screen, and that override is stored per user in
`app_settings` so it survives a restart and does not leak between tenants.

Everything downstream — the three providers, the client factory, the agent
budget — reads `effective()` rather than `config` directly, so there is one
answer to "which model, on whose key" and adding an override cannot leave a
call site behind still reading the environment.

The API key is write-only over HTTP: `public()` returns a masked hint and a
boolean, never the secret. It is stored in the same row-level-secured table as
the rest of a user's settings, so one tenant cannot read another's.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import config
from . import keyring

log = logging.getLogger(__name__)

#: Stored keys, and the empty value that means "inherit from .env".
OVERRIDE_KEYS = (
    "llm_provider",
    "llm_api_key",
    #: A JSON array of {"label", "key"}. `llm_api_key` remains the single-key
    #: form and is still honoured: a workspace that set one key keeps working
    #: without touching anything, and the list simply extends it.
    "llm_api_keys",
    "llm_base_url",
    "llm_model_fast",
    "llm_model_strong",
    "agent_profile",
    #: Which price list applies to this account: 'free' or 'paid'.
    "llm_pricing_tier",
)

#: What each provider needs, and a few models known to work with this app's
#: JSON-mode requirements. `suggested` is a convenience, never a whitelist —
#: the model fields accept any string the provider will honour.
PROVIDERS: list[dict[str, Any]] = [
    {
        "key": "openrouter",
        "label": "OpenRouter",
        "blurb": "One key, an OpenAI-shaped endpoint, and a catalogue that "
                 "includes models billed at zero.",
        "needs_key": True,
        "key_label": "OpenRouter API key",
        "key_hint": "Starts with sk-or-. Created at openrouter.ai/keys.",
        "default_base_url": "https://openrouter.ai/api/v1",
        "suggested_fast": [
            "google/gemma-4-26b-a4b-it:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "mistralai/mistral-small-3.2-24b-instruct:free",
        ],
        "suggested_strong": [
            "z-ai/glm-5.2:free",
            "deepseek/deepseek-r1:free",
            "anthropic/claude-sonnet-4",
        ],
    },
    {
        "key": "gemini",
        "label": "Google Gemini (native)",
        "blurb": "Google's own API. Native structured outputs, which the "
                 "categoriser needs for list-shaped answers.",
        "needs_key": True,
        "key_label": "Gemini API key",
        "key_hint": "Created at aistudio.google.com/apikey.",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "suggested_fast": ["gemini-3.5-flash-lite", "gemma-4-26b-a4b-it",
                           "gemini-2.5-flash-lite", "gemini-2.5-flash"],
        "suggested_strong": ["gemini-3.5-flash-lite", "gemini-2.5-pro",
                             "gemini-2.5-flash", "gemma-4-26b-a4b-it"],
    },
    {
        "key": "azure",
        "label": "Azure OpenAI",
        "blurb": "Your own Azure deployment. The model fields name "
                 "deployments rather than models.",
        "needs_key": True,
        "key_label": "Azure OpenAI API key",
        "key_hint": "Base URL is the resource endpoint, e.g. "
                    "https://my-resource.openai.azure.com",
        "default_base_url": "",
        "suggested_fast": ["gpt-4o-mini", "gpt-4.1-mini"],
        "suggested_strong": ["gpt-4o", "gpt-4.1"],
    },
]

BY_KEY = {p["key"]: p for p in PROVIDERS}

AGENT_PROFILES = [
    {"key": "auto", "label": "Automatic",
     "blurb": "Reads the model's name: a small model gets the compact budget, "
              "a large one the full budget."},
    {"key": "compact", "label": "Minimum steps",
     "blurb": "5 reasoning steps, 6 tools, short brief. Fits inside a free "
              "tier's per-minute token ceiling."},
    {"key": "full", "label": "Full steps",
     "blurb": "10 reasoning steps, 12 tools, the complete brief. Needs a "
              "capable model and headroom on the rate limit."},
]

AGENT_PROFILE_KEYS = {p["key"] for p in AGENT_PROFILES}


def _env_for(provider: str) -> dict[str, str]:
    """What `.env` configures for one named provider.

    Answered per provider rather than only for the selected one: a caller that
    instantiates `OpenRouterProvider` directly must get OpenRouter's key and
    models even while `LLM_PROVIDER` names something else.
    """
    if provider == "gemini":
        return {
            "api_key": config.GEMINI_API_KEY or "",
            "base_url": config.GEMINI_BASE_URL or "",
            "model_fast": config.GEMINI_MODEL_FAST or "",
            "model_strong": config.GEMINI_MODEL_STRONG or "",
        }
    if provider == "azure":
        return {
            "api_key": config.AZURE_OPENAI_API_KEY or "",
            "base_url": config.AZURE_OPENAI_ENDPOINT or "",
            "model_fast": config.AZURE_OPENAI_DEPLOYMENT_FAST or "",
            "model_strong": config.AZURE_OPENAI_DEPLOYMENT_STRONG or "",
        }
    if provider == "openrouter":
        return {
            "api_key": config.OPENROUTER_API_KEY or "",
            "base_url": config.OPENROUTER_BASE_URL or "",
            "model_fast": config.OPENROUTER_MODEL_FAST or "",
            "model_strong": config.OPENROUTER_MODEL_STRONG or "",
        }
    # A name nothing implements, or none at all. Reporting empty is what makes
    # "no model is configured" distinguishable from "a model with no name".
    return {"api_key": "", "base_url": "", "model_fast": "", "model_strong": ""}


def _stored() -> dict[str, str]:
    """This user's overrides, or nothing if they have none.

    Never raises: a model call must not fail because the settings table was
    briefly unreachable. Losing the override there degrades to the `.env`
    default, which is the state the override was made from.
    """
    try:
        from ..db.database import get_db
        from ..db import repository as repo

        stored = repo.get_settings(get_db())
    except Exception:
        log.debug("could not read LLM overrides; falling back to .env", exc_info=True)
        return {}
    return {k: str(stored.get(k) or "").strip() for k in OVERRIDE_KEYS}


def selected_provider() -> str:
    """Which provider this workspace calls: the override, else `.env`."""
    over = _stored()
    return (over.get("llm_provider") or config.LLM_PROVIDER or "").lower()


def for_provider(name: str) -> dict[str, str]:
    """The key, base URL and models one provider should call with.

    Overrides apply only while that same provider is the selected one. A key
    pasted for OpenRouter is not a key for Gemini, so handing it to whichever
    provider happens to be constructed would send the wrong credential to the
    wrong endpoint.
    """
    env = _env_for(name)
    if selected_provider() != name:
        return {**env, "api_keys": keyring.parse(env["api_key"])}

    over = _stored()
    spec = BY_KEY.get(name, {})

    # The ring, in the order it will be tried: the list the user built,
    # then the single-key field, then `.env`. Deduplicated, because the same
    # secret appearing twice would have the rotation land back on a key that
    # just failed and spend the request that rotating was meant to save.
    ring = keyring.dedupe(
        keyring.parse(over.get("llm_api_keys"))
        + keyring.parse(over.get("llm_api_key"))
        + keyring.parse(env["api_key"])
    )
    return {
        # The first usable key. Kept so every existing caller reading
        # `api_key` still gets a working credential; only the retry loop
        # needs to know there are others.
        "api_key": ring[0].secret if ring else "",
        "api_keys": ring,
        "base_url": (over.get("llm_base_url") or env["base_url"]
                     or spec.get("default_base_url", "")),
        "model_fast": over.get("llm_model_fast") or env["model_fast"],
        "model_strong": over.get("llm_model_strong") or env["model_strong"],
    }


def effective() -> dict[str, str]:
    """The settings a call will actually use: `.env`, overlaid with the UI."""
    provider = selected_provider()
    live = for_provider(provider)
    over = _stored()
    return {
        "llm_provider": provider,
        "llm_api_key": live["api_key"],
        "llm_base_url": live["base_url"],
        "llm_model_fast": live["model_fast"],
        "llm_model_strong": live["model_strong"],
        "agent_profile": (over.get("agent_profile")
                          or config.AGENT_PROFILE or "auto").lower(),
        # Free unless the holder says otherwise. Over-reporting a bill is
        # the less useful error: a zero that should be a number prompts
        # the question, a number that should be zero is simply believed.
        "llm_pricing_tier": (over.get("llm_pricing_tier") or "free").lower(),
    }


def model_for(tier: str, provider: str | None = None) -> str:
    """The model name for the fast or strong tier."""
    live = for_provider(provider or selected_provider())
    return live["model_strong"] if tier == "strong" else live["model_fast"]


def mask(secret: str) -> str:
    """A hint that identifies a key without disclosing it."""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "•" * len(secret)
    return f"{secret[:4]}{'•' * 6}{secret[-4:]}"


def public() -> dict[str, Any]:
    """Everything the Settings screen needs, minus the secret itself."""
    env_provider = (config.LLM_PROVIDER or "").lower()
    env_live = _env_for(env_provider)
    env = {
        "llm_provider": env_provider,
        "llm_api_key": env_live["api_key"],
        "llm_base_url": env_live["base_url"],
        "llm_model_fast": env_live["model_fast"],
        "llm_model_strong": env_live["model_strong"],
        "agent_profile": (config.AGENT_PROFILE or "auto").lower(),
    }
    over = _stored()
    live = effective()

    overridden = sorted(k for k in OVERRIDE_KEYS if over.get(k))

    return {
        "providers": PROVIDERS,
        "agent_profiles": AGENT_PROFILES,
        "provider": live["llm_provider"],
        "base_url": live["llm_base_url"],
        "model_fast": live["llm_model_fast"],
        "model_strong": live["llm_model_strong"],
        "agent_profile": live["agent_profile"],
        "pricing_tier": live.get("llm_pricing_tier") or "free",
        "has_api_key": bool(live["llm_api_key"]),
        "api_key_hint": mask(live["llm_api_key"]),
        #: Every key on the ring, masked, with whether it is usable right
        #: now. A key resting after a 429 or rejected outright is the thing
        #: a user needs told - otherwise "I added three keys and it still
        #: rate limits" has no visible explanation.
        "api_keys": keyring.status(for_provider(live["llm_provider"])["api_keys"]),
        "api_key_from_env": bool(env["llm_api_key"]) and not over.get("llm_api_key"),
        #: Which fields the UI is currently deciding, so the screen can say
        #: "from .env" against the rest instead of implying the user set them.
        "overridden": overridden,
        "customised": bool(overridden),
        "env_defaults": {
            "provider": env["llm_provider"],
            "base_url": env["llm_base_url"],
            "model_fast": env["llm_model_fast"],
            "model_strong": env["llm_model_strong"],
            "agent_profile": env["agent_profile"],
            "has_api_key": bool(env["llm_api_key"]),
        },
    }
