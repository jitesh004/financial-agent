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


def _flag(*names: str, default: bool = False) -> bool:
    raw = _env(*names, default='true' if default else 'false')
    return (raw or '').strip().lower() in {'1', 'true', 'yes', 'on'}


class Config:
    # ---- storage ---------------------------------------------------------

    #: Where statement files, caches and per-user snapshots live. The ledger
    #: itself is in PostgreSQL now; this is the durable file store beside it.
    DATA_DIR = _env('FA_DATA_DIR', default=str(_root_dir / 'data'))

    #: The ledger. Point this at a copy to try a migration, reproduce a bug or
    #: demo with sample statements without going near the real database.
    #:
    #: The role in this URL must NOT be a superuser and must not hold
    #: BYPASSRLS: PostgreSQL exempts both from row-level security, which is
    #: what keeps one signed-in user out of another's statements. The app
    #: refuses to start otherwise - see db/engine.assert_isolation_enforced.
    DATABASE_URL = _env(
        'FA_DATABASE_URL', 'DATABASE_URL',
        default='postgresql://financial_agent:financial_agent'
                '@localhost:5432/financial_agent',
    )
    DB_POOL_SIZE = int(_env('FA_DB_POOL_SIZE', default='10') or 10)

    #: How many statements are fetched and parsed at once. Also the number
    #: held in memory simultaneously, which is what makes it worth turning
    #: down on a small host: parsing is CPU-bound, so on a fraction of one
    #: core a wide pool multiplies footprint without adding throughput.
    PARSE_WORKERS = max(1, int(_env('FA_PARSE_WORKERS', default='8') or 8))

    #: Last resort for a genuinely single-user deployment where nobody wants
    #: to create a second database role. Logs a warning on every boot rather
    #: than being quietly settable and forgotten.
    ALLOW_UNENFORCED_ISOLATION = _flag('FA_ALLOW_UNENFORCED_ISOLATION')

    # ---- sign-in ---------------------------------------------------------

    #: Where the browser reaches this app. The Google redirect URI is built
    #: from it, and it has to match a URI registered on the OAuth client.
    APP_BASE_URL = (_env('FA_APP_BASE_URL',
                         default='http://localhost:5173') or '').rstrip('/')

    GOOGLE_CLIENT_ID = _env('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = _env('GOOGLE_CLIENT_SECRET')

    #: Cookie lifetime. A financial ledger is not a thing to stay signed into
    #: on a shared machine for a month.
    SESSION_TTL_HOURS = int(_env('FA_SESSION_TTL_HOURS', default='72') or 72)
    SESSION_COOKIE = _env('FA_SESSION_COOKIE', default='fa_session')
    #: Off by default so http://localhost works; must be on behind TLS, and
    #: docker-compose.prod.yml sets it.
    SESSION_COOKIE_SECURE = _flag('FA_SESSION_COOKIE_SECURE')

    #: Optional allowlist. Empty means anyone with a Google account may sign
    #: up; otherwise a comma-separated list of email addresses or @domains.
    ALLOWED_SIGNINS = tuple(
        part.strip().lower()
        for part in (_env('FA_ALLOWED_SIGNINS', default='') or '').split(',')
        if part.strip()
    )

    #: Who may see the operator's view - the accounts on this deployment, how
    #: often each comes back, how much each has imported.
    #:
    #: Comma-separated addresses, and deliberately NOT domains: this is not an
    #: access tier, it is a named person or two who run the deployment, and a
    #: whole domain is far too easy to grant by accident.
    #:
    #: Empty means there is no admin. That is the right default for a grant
    #: nobody can give themselves - an address hardcoded in the source would
    #: be the operator's own address published in every clone of this
    #: repository, and a UI toggle would let anyone signed in award it to
    #: themselves.
    ADMIN_EMAILS = tuple(
        part.strip().lower()
        for part in (_env('FA_ADMIN_EMAILS', default='') or '').split(',')
        if part.strip() and '@' in part and not part.strip().startswith('@')
    )

    @property
    def google_configured(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    def is_admin(self, email: str | None) -> bool:
        """Whether this address runs the deployment.

        Compared case-insensitively on the whole address. No domain matching
        and no prefix matching: "@example.com" in the list would hand the
        operator's view to every colleague, and a substring check would hand
        it to anyone who could register a lookalike address.
        """
        if not email or not self.ADMIN_EMAILS:
            return False
        return email.strip().lower() in self.ADMIN_EMAILS

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.APP_BASE_URL}/api/auth/google/callback"

    # ---- models ----------------------------------------------------------

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
