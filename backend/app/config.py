"""One place that reads the environment.

Everything configurable arrives through `config`, so no call site has to
remember a variable name or repeat a default.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

_root_dir = Path(__file__).resolve().parents[2]
load_dotenv(_root_dir / '.env')


def _env(*names: str, default: str | None = None) -> str | None:
    """First of `names` that is set, else `default`.

    Several settings have two accepted spellings. Azure's own portal and
    docs call it the "chat deployment", which is the name a user copies into
    their .env; an earlier version of this file invented
    AZURE_OPENAI_DEPLOYMENT_FAST instead. Reading both means a correctly
    written .env is not silently ignored in favour of a default deployment
    that does not exist on the user's resource - which fails at request time
    with an unhelpful 404 rather than at startup.
    """
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


class Config:
    LLM_PROVIDER = (_env('LLM_PROVIDER', default='gemini') or 'gemini').lower()

    GEMINI_API_KEY = _env('GEMINI_API_KEY')
    GEMINI_MODEL_FAST = _env('GEMINI_MODEL_FAST', default='gemini-2.5-flash')
    GEMINI_MODEL_STRONG = _env('GEMINI_MODEL_STRONG', default='gemini-2.5-pro')

    AZURE_OPENAI_ENDPOINT = _env('AZURE_OPENAI_ENDPOINT')
    AZURE_OPENAI_API_KEY = _env('AZURE_OPENAI_API_KEY')
    AZURE_OPENAI_API_VERSION = _env(
        'AZURE_OPENAI_API_VERSION', default='2024-02-15-preview')

    #: The deployment used for ordinary work.
    AZURE_OPENAI_DEPLOYMENT_FAST = _env(
        'AZURE_OPENAI_CHAT_DEPLOYMENT',      # the name Azure itself uses
        'AZURE_OPENAI_DEPLOYMENT_FAST',      # legacy spelling
        default='gpt-4o-mini',
    )
    #: Optional. Unset means the strong tier reuses the chat deployment,
    #: rather than falling back to some other model the user may not have
    #: deployed at all.
    AZURE_OPENAI_DEPLOYMENT_STRONG = _env(
        'AZURE_OPENAI_STRONG_DEPLOYMENT',
        'AZURE_OPENAI_DEPLOYMENT_STRONG',
        default=AZURE_OPENAI_DEPLOYMENT_FAST,
    )

    #: Azure now serves an OpenAI-compatible surface at /openai/v1/. Set this
    #: true to use the older ?api-version=... form instead.
    AZURE_OPENAI_USE_CLASSIC = (
        (_env('AZURE_OPENAI_USE_CLASSIC', default='false') or 'false')
        .strip().lower() in {'1', 'true', 'yes', 'on'}
    )

    AZURE_OPENAI_EMBEDDING_DEPLOYMENT = _env(
        'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', default='text-embedding-3-small')


config = Config()
