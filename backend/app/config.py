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

    LLM_PROVIDER = (
        _env('LLM_PROVIDER', default='openrouter') or 'openrouter').lower()

    # ---- Agents ----------------------------------------------------------
    #
    # How much prompt one agent run may spend. "auto" reads the configured
    # model's name and picks: a small one gets the compact budget, which is
    # sized to fit a whole run inside one minute of Gemini's free-tier
    # input-token ceiling. "compact" and "full" force it either way.
    #
    # Worth forcing to compact on any metered tier even with a large model:
    # the ceiling that bites is tokens per MINUTE, shared across every call,
    # and a run that spends 90,000 of them is rate limited into uselessness
    # however capable the model at the other end is.
    AGENT_PROFILE = (_env('FA_AGENT_PROFILE', default='auto') or 'auto').lower()

    # ---- Gemini (Google's own API) ---------------------------------------
    #
    # Distinct from pointing the OpenRouter provider at Google's
    # OpenAI-compatible endpoint, which also works. Native buys the things
    # the compatibility shim flattens away: `responseSchema` can have an
    # ARRAY at its root, so the categoriser's answers need no object
    # wrapper, and `systemInstruction` is a field of its own rather than a
    # message with a role - which for the Gemma models is the difference
    # between an answer and an empty list.

    GEMINI_API_KEY = _env('GEMINI_API_KEY')
    GEMINI_BASE_URL = (
        _env('GEMINI_BASE_URL',
             default='https://generativelanguage.googleapis.com/v1beta')
        or '').rstrip('/')

    #: Categorisation and letterhead lookups.
    GEMINI_MODEL_FAST = _env('GEMINI_MODEL_FAST',
                             default='gemma-4-26b-a4b-it')
    #: The written narrative.
    GEMINI_MODEL_STRONG = _env('GEMINI_MODEL_STRONG',
                               default='gemma-4-26b-a4b-it')

    # ---- OpenRouter ------------------------------------------------------
    #
    # One key, one OpenAI-shaped endpoint, and a catalogue that includes
    # models billed at zero. "Free" there means no charge per token; it does
    # NOT mean unlimited. OpenRouter caps every `:free` model at 20 requests
    # a minute and 50 a day, raised to 1000 a day once the account has ever
    # bought $10 of credit. The scarce resource is therefore *requests*, not
    # tokens, which is why the categoriser's forty-merchants-per-call batching
    # matters more here than it did on a metered provider.

    OPENROUTER_API_KEY = _env('OPENROUTER_API_KEY')
    OPENROUTER_BASE_URL = (
        _env('OPENROUTER_BASE_URL', default='https://openrouter.ai/api/v1')
        or '').rstrip('/')

    #: The high-volume tier: merchant categorisation in batches of forty, and
    #: the one-line issuer lookup on an unrecognised letterhead. Gemma 4 26B
    #: A4B is a mixture-of-experts that activates under 4B parameters per
    #: token, so it answers quickly, and it accepts `response_format:
    #: json_object` - which this app needs rather than merely prefers, see
    #: the note in providers.OpenRouterProvider.complete.
    OPENROUTER_MODEL_FAST = _env(
        'OPENROUTER_MODEL_FAST', default='google/gemma-4-26b-a4b-it:free')

    #: The narrative tier: one call, six thousand tokens, and the only place
    #: the model writes prose a person reads. GLM 5.2 is a reasoning model
    #: with native structured outputs and a context window long enough for
    #: the whole computed brief.
    OPENROUTER_MODEL_STRONG = _env(
        'OPENROUTER_MODEL_STRONG', default='z-ai/glm-5.2:free')

    #: How hard the model should think before answering, for the models that
    #: expose the control. Low by default: the fast tier is classification
    #: against a fixed list of categories, where thinking mostly spends the
    #: token budget that the answer needs. Set to 'medium' or 'high' if the
    #: narrative reads thin. Empty sends no reasoning directive at all.
    #
    #: Read straight from the environment rather than through `_env`, which
    #: folds an empty value into the default. Here that distinction is the
    #: whole point: `OPENROUTER_REASONING_EFFORT=` is documented, in this
    #: file and in .env.example, as the way to send no directive at all, and
    #: through `_env` it came back as 'low' instead - so the one escape
    #: hatch from the directive did nothing, silently. It exists for a model
    #: or endpoint that rejects the field rather than ignoring it, which is
    #: a failure of the whole request rather than of the directive.
    _reasoning_effort = os.environ.get('OPENROUTER_REASONING_EFFORT')
    OPENROUTER_REASONING_EFFORT = (
        'low' if _reasoning_effort is None
        else _reasoning_effort.strip().lower())

    #: Send `response_format: {"type": "json_object"}` on JSON calls. On by
    #: default because it is the difference between a parsed answer and a
    #: silent zero, but not every free model accepts it - turn it off if you
    #: point OPENROUTER_MODEL_* at one that does not.
    OPENROUTER_JSON_MODE = _flag('OPENROUTER_JSON_MODE', default=True)

    #: Optional attribution, shown on openrouter.ai's activity page so a
    #: shared key's traffic can be told apart. Sent as HTTP-Referer/X-Title.
    OPENROUTER_APP_URL = _env('OPENROUTER_APP_URL', default=APP_BASE_URL)
    OPENROUTER_APP_TITLE = _env('OPENROUTER_APP_TITLE',
                                default='Financial Agent')

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
