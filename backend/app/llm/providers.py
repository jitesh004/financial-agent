from typing import Any
from ..config import config
from . import keyring
from . import telemetry
from . import settings as llm_settings
import httpx
import json
import logging
import os
import re
import time

#: Seconds to wait for a completion. Generous because these are batch calls,
#: not interactive ones: the categoriser sends forty merchants at a time, and a
#: structured-output reply for that regularly runs past thirty seconds - which
#: is how a correctly configured provider still reported "0 from the model" on
#: every run, with only a warning in the log to say why. Override with
#: LLM_TIMEOUT_SECONDS where a slow proxy needs longer still.
REQUEST_TIMEOUT = float(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))

#: How many times to wait out a 429 before giving up on a call. Free
#: OpenRouter models allow 20 requests a minute, so a categorisation run
#: with enough unknown merchants will hit the ceiling mid-run; the limit
#: clears within the minute, and the batch is worth waiting for.
RATE_LIMIT_RETRIES = max(
    0, int(os.environ.get("LLM_RATE_LIMIT_RETRIES", "3")))

#: Ceiling on a single wait, however long the provider asks for. A daily
#: quota resets hours away, and blocking an import job until then is
#: worse than failing the batch and saying so.
MAX_RATE_LIMIT_WAIT = float(os.environ.get("LLM_RATE_LIMIT_MAX_WAIT", "60"))


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

    # Cut off mid-answer, or genuinely malformed? The two look identical
    # from here and they are not the same problem: one is fixed by raising
    # `max_tokens`, the other by fixing the prompt or the schema. Reported
    # as one message, every failure with a budget too small read as "the
    # model cannot follow instructions", which is the wrong thing to go and
    # fix - and on the tier this app targets, tokens are the cheap resource
    # (250,000 a minute against 500 requests a DAY), so a budget that
    # truncates costs the only thing that actually runs out.
    if _looks_truncated(text):
        raise ValueError(
            "The model's reply was cut off before it finished - the JSON "
            "ends mid-structure. Raise max_tokens for this call; a "
            "reasoning model spends its thinking against the same "
            f"allowance. Got {len(text)} characters ending {text[-60:]!r}")

    raise ValueError(f"Model did not return parseable JSON: {raw[:200]!r}")


def _looks_truncated(text: str) -> bool:
    """Does this read as an answer that stopped, rather than a bad answer?

    An unterminated string, or more openers than closers, means the reply
    was still being written when the budget ran out. A model that simply
    answered in prose has neither.
    """
    if not text:
        return False
    depth = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == chr(92):
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return in_string or depth > 0

class Provider:
    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", temperature: float = 0.0) -> str:
        raise NotImplementedError
    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", schema: dict | None = None) -> Any:
        raise NotImplementedError
    @property
    def available(self) -> bool:
        return False

def _clamp_wait(seconds: float) -> float:
    return max(0.0, min(seconds, MAX_RATE_LIMIT_WAIT))


def _retry_after_seconds(response: Any, attempt: int) -> float:
    """How long to wait before retrying a rate-limited call.

    OpenRouter sends `Retry-After` (seconds) or `X-RateLimit-Reset` (a
    millisecond epoch) on a 429. Prefer whichever it gave, because guessing
    shorter just spends another request against the same ceiling; fall back
    to doubling waits when it gave neither. Capped so a misread header cannot
    park an import job for an hour.

    Gemini sends NEITHER. It answers 429 with the delay in the response BODY
    - a `google.rpc.RetryInfo` block carrying `retryDelay: "16s"`, and the
    same figure in prose at the end of the message. Reading only the two
    headers above meant every Gemini 429 fell through to the exponential
    fallback and waited 1s, 2s, then 4s against a limit that had told us,
    precisely, to wait 15.9 - so all three retries were spent inside the
    window and the call failed anyway. An agent making seventeen tool calls
    never finished one.
    """
    headers = getattr(response, "headers", {}) or {}

    body = None
    try:
        body = response.json()
    except Exception:  # not JSON, or no body - the headers below still apply
        body = None

    if isinstance(body, list):
        body = body[0] if body else None
    if isinstance(body, dict):
        error = body.get("error") or {}
        for detail in (error.get("details") or []):
            if not isinstance(detail, dict):
                continue
            delay = detail.get("retryDelay")
            if not delay:
                continue
            try:
                return _clamp_wait(float(str(delay).rstrip("s")))
            except (TypeError, ValueError):
                pass
        # The prose carries it too, and has outlived more than one change to
        # the structured shape: "Please retry in 15.914573931s."
        prose = re.search(r"retry in ([\d.]+)\s*s",
                          str(error.get("message") or ""), re.IGNORECASE)
        if prose:
            try:
                return _clamp_wait(float(prose.group(1)))
            except (TypeError, ValueError):
                pass

    raw = headers.get("Retry-After")
    if raw:
        try:
            # Clamped at both ends: an HTTP-date rather than a count of
            # seconds falls through to the header below, and a stale or
            # negative value must not reach time.sleep(), which raises on one.
            return _clamp_wait(float(raw))
        except (TypeError, ValueError):
            pass

    raw = headers.get("X-RateLimit-Reset")
    if raw:
        try:
            wait = float(raw) / 1000.0 - time.time()
            if wait > 0:
                return _clamp_wait(wait)
        except (TypeError, ValueError):
            pass

    return _clamp_wait(2.0 ** attempt)


#: A 429 that names a per-minute INPUT TOKEN ceiling rather than a request
#: count. Worth saying out loud when the retries run out, because the number
#: explains the failure in a way "429" does not.
_PER_MINUTE_INPUT_LIMIT = re.compile(
    r"limit:\s*(\d+).{0,80}?input_token_count|input_token_count.{0,80}?limit:\s*(\d+)",
    re.IGNORECASE | re.DOTALL)


def _token_budget_note(response: Any) -> str:
    """Why a 429 kept happening, in the terms the provider used, or "".

    Gemini's free tier meters INPUT TOKENS PER MINUTE - 16,000 on the model
    this app defaults to. That is a budget shared across every call made in
    the same minute, so an agent sending 12,000-token prompts can make one
    call a minute and no more, however small each request is on its own.
    Retrying is still the right response; what was missing was any way for
    the person reading the failure to know that the ceiling was tokens per
    minute rather than a dead key or a broken request.
    """
    try:
        body = response.json()
    except Exception:
        return ""
    if isinstance(body, list):
        body = body[0] if body else {}
    if not isinstance(body, dict):
        return ""
    message = str((body.get("error") or {}).get("message") or "")
    match = _PER_MINUTE_INPUT_LIMIT.search(message)
    if not match:
        return ""
    limit = match.group(1) or match.group(2)
    return (
        f" This tier meters input tokens per minute ({limit}), shared across "
        f"every call in the same minute - not a per-request size limit. A "
        f"run making several large calls in quick succession will keep "
        f"hitting it."
    )


#: A refusal of the CREDENTIAL, however the provider chose to spell it.
#:
#: Google does not use 401 for this. A mistyped or revoked key comes back
#: `400 INVALID_ARGUMENT` with "API key not valid" in the body - the same
#: status a malformed request gets, which is why the status alone cannot
#: decide. Checked against the body so a genuine payload error still reads
#: as a payload error and does not stand a working key down.
#:
#: This is the commonest real failure there is - somebody pastes a key with
#: a character missing - and until it was recognised the whole multi-key
#: ring did nothing for it: the bad key was never retired, never rotated
#: past, and failed every call for the life of the process.
_BAD_CREDENTIAL = re.compile(
    r"api[\s_-]?key not valid"
    r"|api[\s_-]?key[\s_-]?invalid"
    r"|invalid[\s_-]?api[\s_-]?key"
    r"|invalid authentication"
    r"|incorrect api key"
    r"|unauthenticated",
    re.IGNORECASE,
)


def _rejects_the_key(response: Any) -> bool:
    """Is this response the provider refusing the credential?"""
    status = getattr(response, "status_code", 0)
    if status in (401, 403):
        return True
    if status != 400:
        return False
    return bool(_BAD_CREDENTIAL.search(_body_text(response)))


def _body_text(response: Any) -> str:
    """Whatever the provider said, as text, without raising."""
    try:
        return (getattr(response, "text", "") or "")[:600]
    except Exception:                   # pragma: no cover - defensive
        return ""


def _post_with_retries(client: Any, url: str, *, json: Any,
                       headers: dict | None = None, provider: str,
                       keys: list | None = None,
                       headers_for: Any = None) -> Any:
    """POST, rotating API keys and then backing off, until something works.

    A 429 is the provider saying "not yet"; a timeout or a dropped
    connection is the network saying "ask again". Neither means the request
    was wrong, and both were fatal here - the 429 because only its status
    was checked, the timeout because `client.post` raised straight out of
    the loop.

    That cost whole agent runs. One of these agents makes twenty-three tool
    calls over ten steps; a single slow reply on call twenty-three threw
    away the twenty-two before it and reported "ReadTimeout" as the answer
    to a question about the holder's debt.

    ROTATION COMES BEFORE BACKOFF, and the order is the substance. A free
    tier meters requests per minute AND per day, and a 429 does not say
    which ceiling was hit. Sleeping clears the first and never clears the
    second, so an import that trips the daily cap used to stall for the
    full backoff and then fail anyway. Asking a different key costs nothing
    and is correct for both - it is only when every key is resting that
    waiting is the right move.

    `keys` and `headers_for` are how a caller offers more than one
    credential: `headers_for(secret)` builds the request headers for a
    given key, because each provider carries it differently (a bearer
    token, an `x-goog-api-key`, an `api-key`). Callers with a single
    credential pass `headers` and behave exactly as before.

    Raises the last error if every attempt fails, so a genuine outage still
    surfaces rather than being swallowed.
    """
    ring = list(keys or [])
    if ring and headers_for is None:        # pragma: no cover - programmer error
        raise ValueError("keys= requires headers_for=")

    last_error: Exception | None = None
    last_resp: Any = None
    attempt = 0
    exhausted_keys: set[str] = set()

    while attempt <= RATE_LIMIT_RETRIES:
        key = None
        if ring:
            usable = [k for k in keyring.available(ring)
                      if k.secret not in exhausted_keys]
            # Every key has failed this call: fall back to the full ring and
            # let the backoff below do the waiting.
            key = (usable or keyring.available(ring))[0]
            request_headers = headers_for(key.secret)
        else:
            request_headers = headers or {}

        # Every attempt is recorded, whatever becomes of it. A request that
        # was rate limited on one key and succeeded on the next is two
        # facts, and folding them into one is how a quota problem stays
        # invisible: the summary reads "1 request, ok" and the reason the
        # import took four minutes is nowhere.
        call = telemetry.CURRENT.get()
        started = time.monotonic()

        def _record(status: str, http_status: int = 0, error: str = "") -> None:
            if call is None:
                return
            call.attempt(
                key_label=key.label if key is not None else "",
                key_hint=key.masked() if key is not None else "",
                status=status, http_status=http_status, error=error[:500],
                latency_ms=int((time.monotonic() - started) * 1000))

        try:
            resp = client.post(url, json=json, headers=request_headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            _record("failed", error=f"{type(exc).__name__}: {exc}")
            attempt += 1
            if attempt > RATE_LIMIT_RETRIES:
                raise
            delay = _clamp_wait(2.0 ** attempt)
            logging.warning(
                "%s call failed (%s); retrying in %.0fs (attempt %d of %d)",
                provider, type(exc).__name__, delay, attempt,
                RATE_LIMIT_RETRIES)
            time.sleep(delay)
            continue

        last_resp = resp

        # A credential the provider refuses will not start working, so it is
        # retired for the process rather than rested. One mistyped key
        # otherwise costs a failed attempt on every request from here on.
        if key is not None and _rejects_the_key(resp):
            _record("rejected", resp.status_code,
                    f"the provider refused this key: {_body_text(resp)}")
            keyring.retire(key, f"HTTP {resp.status_code}")
            exhausted_keys.add(key.secret)
            if len(exhausted_keys) < len(ring):
                continue            # another key, without spending a retry
            return resp

        if resp.status_code != 429:
            # The body, not just the status. "HTTP 400" on the statistics
            # page says nothing a reader can act on; "API key not valid"
            # or "Unknown name 'type'" says exactly what to go and fix.
            _record("ok" if resp.status_code < 400 else "failed",
                    resp.status_code,
                    "" if resp.status_code < 400
                    else f"HTTP {resp.status_code}: {_body_text(resp)}")
            if key is not None:
                keyring.revive(key)
            return resp

        _record("rate_limited", 429, "rate limited by the provider")

        # Rate limited. Stand this key down and try the next one FIRST;
        # only sleep when there is nothing else to ask.
        if key is not None:
            keyring.rest(key, _retry_after_seconds(resp, attempt))
            exhausted_keys.add(key.secret)
            if len(exhausted_keys) < len(ring):
                logging.info(
                    "%s rate limited; switching to another API key "
                    "(%d of %d tried)", provider, len(exhausted_keys),
                    len(ring))
                continue            # a different key is not a retry

        attempt += 1
        if attempt > RATE_LIMIT_RETRIES:
            return resp

        delay = _retry_after_seconds(resp, attempt)
        logging.warning(
            "%s rate limited (429) on every key; waiting %.0fs "
            "(attempt %d of %d)", provider, delay, attempt,
            RATE_LIMIT_RETRIES)
        time.sleep(delay)
        # A wait may have cleared the per-minute window for keys already
        # tried, so they are candidates again.
        exhausted_keys.clear()

    if last_resp is not None:
        return last_resp
    if last_error is not None:  # pragma: no cover - loop returns first
        raise last_error
    return last_resp


def _message_text(data: Any) -> str:
    """The model's answer, skipping its thinking.

    Reasoning models on OpenRouter put the chain of thought in a sibling
    field, not in `content`:

        message -> {"content": "[{\"i\": 0, ...}]",
                    "reasoning": "The user wants me to classify..."}

    So `content` is the answer and is read first. But a reasoning model that
    runs out of `max_tokens` mid-thought returns `content: ""` with the whole
    budget spent in `reasoning`, and an empty string reads downstream as a
    silent failure - categorisation reporting "0 from the model" over a
    provider that was answering. Falling back to the reasoning at least gives
    `_parse_json_loose` something to find the answer in.
    """
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        logging.error("Unexpected OpenRouter response: %s", data)
        return ""
    if not isinstance(message, dict):
        logging.error("Unexpected OpenRouter message: %s", message)
        return ""

    content = message.get("content")
    # Some providers return content as a list of parts rather than a string.
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    if isinstance(content, str) and content.strip():
        return content

    for key in ("reasoning", "reasoning_content"):
        thinking = message.get(key)
        if isinstance(thinking, str) and thinking.strip():
            logging.warning(
                "OpenRouter returned only reasoning (finish_reason=%s); "
                "falling back to it. Raise LLM max_tokens or lower "
                "OPENROUTER_REASONING_EFFORT if this recurs.",
                data.get("choices", [{}])[0].get("finish_reason"),
            )
            return thinking
    return ""


class OpenRouterProvider(Provider):
    """One key, one OpenAI-shaped endpoint, a catalogue of free models.

    `:free` models cost nothing per token but are rate limited per *request*
    - 20 a minute, and 50 a day until the account has bought $10 of credit,
    then 1000 a day. A categorisation run walks its merchants forty to a
    call, so the per-minute ceiling is the one that bites, and a 429 there
    would otherwise lose a whole batch of merchants to a limit that clears in
    seconds. Hence the wait-and-retry below rather than a bare raise.
    """

    @property
    def available(self) -> bool:
        return bool(llm_settings.for_provider("openrouter")["api_key"])

    def _model(self, tier: str) -> str:
        return llm_settings.model_for(tier, "openrouter")

    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096,
                 tier: str = "fast", temperature: float = 0.0,
                 json_mode: bool = False, schema: dict | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model(tier),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Asking for JSON in the system prompt is a request; this is a
        # constraint. A model left to answer freely prefaces the array with a
        # sentence of explanation, which parses as nothing - which is how a
        # correctly configured provider reports "0 from the model" on every
        # run with only a warning in the log to say why.
        #
        # Which constraint matters. `json_object` only promises *an* object,
        # and an object is precisely what the categoriser cannot use: it asks
        # for one answer per merchant and gets back a single
        # {"i": 0, ...} with the other thirty-nine dropped, which arrives
        # downstream as "expected a JSON array, got dict" and loses the whole
        # batch. A schema states the shape instead of hoping for it - and,
        # because the category list is an enum inside it, a hallucinated
        # bucket stops being possible rather than being caught and discarded
        # afterwards. Callers wanting a bare object still pass json_mode.
        if schema is not None and config.OPENROUTER_JSON_MODE:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True,
                                "schema": schema},
            }
        elif json_mode and config.OPENROUTER_JSON_MODE:
            payload["response_format"] = {"type": "json_object"}
        # `reasoning_effort`, not OpenRouter's own `reasoning: {effort}`.
        # Both endpoints accept this spelling - it is OpenAI's, which is the
        # shape they are each compatible with - but Google's accepts only
        # this one, rejecting the nested form outright with a 400 `Unknown
        # name "reasoning"` rather than ignoring a field it does not know.
        #
        # It is worth sending rather than omitting. Gemini 3.x flash thinks
        # by default, and thinking is charged against `max_tokens`: the
        # letterhead lookup asks for 100 tokens, which the model spends
        # entirely on reasoning, returning empty content and no institution.
        # At 'low' the same call answers in fifteen.
        if config.OPENROUTER_REASONING_EFFORT:
            payload["reasoning_effort"] = config.OPENROUTER_REASONING_EFFORT

        live = llm_settings.for_provider("openrouter")

        def headers_for(secret: str) -> dict:
            built = {"Authorization": f"Bearer {secret}"}
            # Attribution, so a shared key's traffic is identifiable on
            # openrouter.ai. Neither header carries anything about the user.
            if config.OPENROUTER_APP_URL:
                built["HTTP-Referer"] = config.OPENROUTER_APP_URL
            if config.OPENROUTER_APP_TITLE:
                built["X-Title"] = config.OPENROUTER_APP_TITLE
            return built

        base_url = (live["base_url"] or "https://openrouter.ai/api/v1").rstrip("/")
        url = f"{base_url}/chat/completions"
        with telemetry.record(
                "openrouter", self._model(tier), tier=tier,
                reasoning=config.OPENROUTER_REASONING_EFFORT or "off",
                prompt=f"{system}\n\n{prompt}" if system else prompt), \
                httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = _post_with_retries(
                client, url, json=payload, provider="OpenRouter",
                keys=live.get("api_keys"), headers_for=headers_for)
            resp.raise_for_status()
            data = resp.json()
            telemetry.note_usage(data, _message_text(data))

        # OpenRouter reports upstream failures in the body, with a 200 on the
        # envelope that carried them. Left unread, the error surfaces as an
        # empty answer with no explanation anywhere.
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = (error.get("message") if isinstance(error, dict)
                       else str(error))
            raise RuntimeError(f"OpenRouter returned an error: {message}")

        return _message_text(data)

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", schema: dict | None = None) -> Any:
        system = system or "You return only valid JSON. No prose, no code fences."
        raw = self.complete(prompt, system=system, max_tokens=max_tokens,
                            tier=tier, temperature=0.0, json_mode=True,
                            schema=schema)
        return _parse_json_loose(raw)


#: JSON Schema words that Google's schema dialect has no place for. It takes
#: an OpenAPI 3.0 subset, which rejects an unknown key outright rather than
#: ignoring it, so a schema written for the OpenAI providers has to be
#: translated rather than forwarded.
_SCHEMA_DROP = {"additionalProperties", "$schema", "strict", "definitions",
                "$defs", "default", "examples", "title"}


def _gemini_schema(schema: Any) -> Any:
    """A JSON Schema rewritten in Google's OpenAPI dialect.

    Two differences and one addition. Types are upper-case there ("OBJECT",
    not "object"); `additionalProperties` and friends are not part of the
    subset and are refused rather than ignored; and `propertyOrdering` is
    added so the model emits fields in the order the schema lists them,
    which keeps a truncated reply parseable up to the point it stops.

    Callers keep writing ordinary JSON Schema, so one schema at one call
    site serves every provider.
    """
    if isinstance(schema, list):
        return [_gemini_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _SCHEMA_DROP:
            continue
        if key == "type" and isinstance(value, str):
            out[key] = value.upper()
        elif key == "type" and isinstance(value, list):
            # `{"type": ["integer", "null"]}` is how JSON Schema spells an
            # optional field, and Google's dialect has no union type at all
            # - it rejects the list outright with `Proto field is not
            # repeating, cannot start list` and the whole request 400s.
            # Nullability lives in its own key there, so the two halves are
            # separated rather than the schema being rewritten at each call
            # site: a caller writes ordinary JSON Schema and every provider
            # gets something it accepts, which is this function's whole job.
            concrete = [v for v in value if str(v).lower() != "null"]
            out[key] = str(concrete[0]).upper() if concrete else "STRING"
            if len(concrete) < len(value):
                out["nullable"] = True
        elif key == "properties" and isinstance(value, dict):
            out[key] = {k: _gemini_schema(v) for k, v in value.items()}
            out["propertyOrdering"] = list(value)
        elif key in ("items", "not"):
            out[key] = _gemini_schema(value)
        elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
            out[key] = [_gemini_schema(v) for v in value]
        else:
            out[key] = value
    return out


def _gemini_error(response: Any) -> str:
    """What Google actually said, which is never in the status line.

    A 429 here is "Too Many Requests" on the envelope and, in the body, the
    quota's name and its limit - the difference between a burst worth
    retrying and a daily ceiling that is not.
    """
    try:
        body = response.json()
        if isinstance(body, list):
            body = body[0] if body else {}
        error = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error)
    except Exception:
        return (getattr(response, "text", "") or "")[:300]


def _gemini_text(data: Any) -> str:
    """The answer, with the model's thinking left out.

    Gemini returns a candidate as a list of parts, and a thinking model
    marks its chain of thought with `"thought": true` on the part carrying
    it. Concatenating every part would prepend the reasoning to the JSON and
    parse as nothing; taking parts[0] blindly returns the reasoning instead
    of the answer whenever the model thought first. So: the unmarked parts,
    joined, and the marked ones only if that leaves nothing at all.
    """
    try:
        candidate = data["candidates"][0]
    except (KeyError, IndexError, TypeError):
        logging.error("Unexpected Gemini response: %s", data)
        return ""

    parts = (candidate.get("content") or {}).get("parts") or []
    answer = "".join(
        str(part.get("text", "")) for part in parts
        if isinstance(part, dict) and not part.get("thought")
    )
    if answer.strip():
        return answer

    # Nothing but thinking, or nothing at all. Said plainly here rather than
    # surfacing as unparseable JSON three frames up.
    reason = candidate.get("finishReason")
    if reason and reason != "STOP":
        logging.warning(
            "Gemini returned no answer (finishReason=%s). Raise max_tokens "
            "if that is MAX_TOKENS; check the prompt if it is SAFETY.",
            reason)
    return "".join(str(p.get("text", "")) for p in parts
                   if isinstance(p, dict))


class GeminiProvider(Provider):
    """Google's own API, rather than its OpenAI-compatible endpoint.

    Both routes reach the same models, and the compatible one needs no code
    at all - point OPENROUTER_BASE_URL at it. This exists for the two things
    that route flattens away:

      - `responseSchema` takes an ARRAY at its root, so a caller wanting a
        list of answers gets one directly. Through `response_format` it
        cannot: JSON mode there promises an object, so the same request has
        to ask for {"results": [...]} and unwrap it.
      - `systemInstruction` is a field rather than a message with a role.
        The Gemma models honour the field and ignore the message; with the
        system text folded into the prompt, the categoriser's batch comes
        back as an empty array.

    Free-tier quota is per model per day and small - 20 requests a day on
    gemini-3.6-flash - so a 429 here is more often a daily ceiling than a
    burst, and no amount of waiting inside one request will clear it.
    """

    @property
    def available(self) -> bool:
        return bool(llm_settings.for_provider("gemini")["api_key"])

    def _model(self, tier: str) -> str:
        return llm_settings.model_for(tier, "gemini")

    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096,
                 tier: str = "fast", temperature: float = 0.0,
                 json_mode: bool = False, schema: dict | None = None) -> str:
        generation: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = _gemini_schema(schema)
        elif json_mode:
            generation["responseMimeType"] = "application/json"

        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        live = llm_settings.for_provider("gemini")
        gemini_base = (live["base_url"]
                       or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        url = (f"{gemini_base}/models/"
               f"{self._model(tier)}:generateContent")
        def headers_for(secret: str) -> dict:
            return {"x-goog-api-key": secret,
                    "Content-Type": "application/json"}

        with telemetry.record(
                "gemini", self._model(tier), tier=tier,
                # Google's own API has no reasoning switch on this path;
                # the model thinks or it does not, by its own nature. Said
                # as "model default" rather than left blank, because blank
                # reads as "nobody knows" and this is a known state.
                reasoning="model default",
                prompt=f"{system}\n\n{prompt}" if system else prompt), \
                httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = _post_with_retries(
                client, url, json=body, provider="Gemini",
                keys=live.get("api_keys"), headers_for=headers_for)
            if resp.status_code >= 400:
                note = (_token_budget_note(resp)
                        if resp.status_code == 429 else "")
                raise RuntimeError(f"Gemini returned {resp.status_code}: "
                                   f"{_gemini_error(resp)}{note}")
            data = resp.json()
            telemetry.note_usage(
                data[0] if isinstance(data, list) and data else data,
                _gemini_text(data[0] if isinstance(data, list) and data
                             else data) if isinstance(data, (dict, list)) else "")

        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Gemini returned an error: {data['error']}")

        return _gemini_text(data)

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", schema: dict | None = None) -> Any:
        system = system or "You return only valid JSON. No prose, no code fences."
        raw = self.complete(prompt, system=system, max_tokens=max_tokens,
                            tier=tier, temperature=0.0, json_mode=True,
                            schema=schema)
        return _parse_json_loose(raw)


class AzureOpenAIProvider(Provider):
    @property
    def available(self) -> bool:
        live = llm_settings.for_provider("azure")
        return bool(live["base_url"] and live["api_key"])

    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", temperature: float = 0.0, schema: dict | None = None) -> str:
        live = llm_settings.for_provider("azure")
        deployment = llm_settings.model_for(tier, "azure")
        base_url = (live["base_url"] or "").rstrip('/')
        if config.AZURE_OPENAI_USE_CLASSIC:
            # The original per-deployment surface.
            url = (f"{base_url}/openai/deployments/{deployment}/chat/completions"
                   f"?api-version={config.AZURE_OPENAI_API_VERSION}")
        else:
            # The OpenAI-compatible surface, where the deployment is named in
            # the body as `model` rather than in the path.
            url = f"{base_url}/openai/v1/chat/completions"

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        # Same reasoning as the OpenRouter provider: a caller that states the
        # shape it needs gets it enforced rather than requested.
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True,
                                "schema": schema},
            }
        if not config.AZURE_OPENAI_USE_CLASSIC:
            payload["model"] = deployment

        def headers_for(secret: str) -> dict:
            return {"api-key": secret, "Authorization": f"Bearer {secret}"}

        with telemetry.record(
                "azure", deployment, tier=tier, reasoning="off",
                prompt=f"{system}\n\n{prompt}" if system else prompt), \
                httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            # Through the shared loop like the other two. This was a bare
            # `client.post`, so Azure alone had no retry on a 429 and no
            # retry on a dropped connection - the two failures the loop
            # exists for, and the ones that cost whole agent runs.
            resp = _post_with_retries(
                client, url, json=payload, provider="Azure OpenAI",
                keys=live.get("api_keys"), headers_for=headers_for)
            resp.raise_for_status()
            data = resp.json()
            telemetry.note_usage(data, _message_text(data))
            try:
                return data["choices"][0]["message"].get("content", "")
            except (KeyError, IndexError):
                logging.error(f"Unexpected Azure response: {data}")
                return ""

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 4096, tier: str = "fast", schema: dict | None = None) -> Any:
        system = system or "You return only valid JSON. No prose, no code fences."
        raw = self.complete(prompt, system=system, max_tokens=max_tokens, tier=tier, temperature=0.0, schema=schema)
        return _parse_json_loose(raw)
